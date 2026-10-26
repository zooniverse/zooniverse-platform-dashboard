# Anomaly detection and episode inspection

The Anomalies page provides **statistical inspection hints** for unusual daily
platform activity. It is **not** an intrusion detector, outage monitor, causal
analysis, or assertion that an individual user did something suspicious. Its
purpose is to replace the older weekday-median/MAD chart markers with a smaller,
explainable set of **episodes** that a dashboard reader can inspect quickly.

This page documents the *implemented* detector in
`src/zooniverse_dashboard/anomalies/`, not every method investigated on the
`research/anomaly-models` branch. The production implementation does **not**
run STL, change-point detection, active learning, or the original weekday-MAD
algorithm.

## Inputs, outputs, and execution boundary

The detector consumes only three validated daily-count snapshots:

| Source key | Count being modeled | Upstream aggregate source |
|---|---|---|
| `users` | Registrations per completed UTC date | Panoptes `public.users.created_at` |
| `classifications` | Classifications per completed UTC date | ERAS classifications summary |
| `talks` | Talk comments per completed UTC date | ERAS comments summary |

The values are **counts of actions**, not distinct active users, and neither
individual records nor provider credentials are inputs to the detector.

```text
Panoptes / ERAS
     |
     |  refresh-data, or the dashboard Refresh data button
     v
Validated daily aggregate JSON snapshots (users, classifications, talks)
     |
     |  offline NB2 modeling: build_catalog / rebuild-anomalies
     v
Atomic anomalies.json (on the same persistent data volume)
     |
     +--> /anomalies: episodes, scores, source/date links
     +--> source history charts: clickable episode diamonds
```

The web server **reads** `anomalies.json` but never fits NB2 when rendering a
page, zooming a chart, or clicking an episode. The numerical dependencies are
kept in the separate analysis package and imported only when rebuilding the
catalog. The one-click/CLI refresh pipeline performs the rebuild **after**
aggregate ingestion finishes; `rebuild-anomalies` can also be run independently,
without a database or API request.

## How the daily model works

### 1. Analysis and training windows

All dates are completed UTC dates. Source snapshots must contain a continuous
daily calendar series with nonnegative counts; the collector fills omitted
dates with zeros, an important uncertainty discussed below.

| Parameter | Production setting | Meaning |
|---|---:|---|
| Historical training | **730 days** | Previous daily counts for each fit |
| Model refit interval | **28 days** | One fitted model forecasts the next block |
| Maximum evaluated history | **730 days** | Recent scored dates eligible for the catalog |
| Empirical reference | **365 prior scores** | Rolling history used to rank surprises |
| Minimum empirical reference | **180 prior scores** | No historical-rarity result before this warm-up |

Fitting blocks are anchored to the **beginning of the saved snapshot**, not to
the last date in the snapshot. At the start of a block, the model fits only the
preceding 730 counts and then predicts that block's dates without refitting on
observations from that block. Appending new dates to an otherwise unchanged
snapshot does not retroactively change its earlier individual forecasts.

A snapshot with fewer than **911 daily buckets** cannot produce an evaluated
series (730 training dates, at least 180 calibration warm-up dates, and one
evaluation date). Even above this minimum, fewer than 730 dates may be scored.
Collecting less history with `set-date-limit` can therefore make anomaly
analysis unavailable even while all seven dashboard KPIs remain available.

Changing the **beginning** of the collected range, fitting configuration, or
model implementation can change historical scores; the append-only property
is not a promise that catalog versions are immutable.

### 2. Features and the two count models

Both variants use a negative-binomial regression (NB2) with a log link:

$$
\log(\mu_t) = \beta_0 + \beta_1\frac{t-t_0}{365.25}
  + \sum_{j=1}^{6}\gamma_j I(\operatorname{weekday}(t)=j)
  + \delta_C I_C(t) + \delta_N I_N(t).
$$

Here $\mu_t$ is the expected count on date $t$, $t_0$ is the beginning of that
fit's training window, and Monday is the reference weekday (the six indicators
represent Tuesday through Sunday). The trend is **locally linear on the log
scale**, not a long-term forecast of the platform's growth.

- **User registrations:** includes both holiday terms: Christmas **December
  23–26** and New Year **December 30–January 2**. Their coefficients are
  *estimated from the preceding training data*, not hard-coded activity drops.
- **Classifications and Talk:** use the intercept, local trend, and weekday
  indicators only. The holiday coefficients are omitted entirely.

These fixed holiday windows were chosen as *candidate calendar effects*, not
as proof that Zooniverse activity follows a particular country's holidays.
They only adjust the registration model, and there are relatively few annual
repetitions in any two-year training window. Other holidays, project launches,
campaigns, seasonality, and service changes are **not explicitly modeled**.

With dispersion $\alpha>0$, NB2 assumes

$$
\operatorname{E}[Y_t]=\mu_t,\qquad
\operatorname{Var}(Y_t)=\mu_t+\alpha\mu_t^2.
$$

This accommodates variation beyond a Poisson model, but it still assumes a
particular shape for the count distribution. It does **not** guarantee that
real-world activity has well-calibrated extreme tails.

### 3. Numerical fitting and failure behavior

The implementation uses a **profile-likelihood** fit. It searches over
$\log(\alpha)$, fits a negative-binomial generalized linear model conditional
on each proposed dispersion, and maximizes the NB2 count log likelihood.
The dispersion search is bounded to $10^{-6}\le\alpha\le8$.

It rejects non-converged GLM fits, invalid fitted means or likelihoods, and
solutions too close to the dispersion-search boundary. It does **not**
silently substitute a Poisson forecast or trust a failed optimizer. A failed
source analysis is recorded as unavailable in the catalog; successfully
published aggregate snapshots are not discarded.

### 4. Two different measures of unusualness

For an observed count $y_t$, the **raw NB2 score** uses a two-sided predictive
tail:

$$
p_{\mathrm{NB2},t}=\min\left(1,\;2\min\left[
P(Y_t\le y_t),\;P(Y_t\ge y_t)
\right]\right).
$$

The distribution is the fitted NB2 forecast for that date. The score is small
when the count is far into either tail. The direction is a **spike** when the
observed count is at or above the expected value, otherwise a **drop**.
Because the counts are discrete and the distribution can be misspecified,
this number must not be read as a perfectly calibrated false-alarm probability.

The **rolling historical-rarity score** asks a different question: *How often
was a previous out-of-sample NB2 score at least as extreme as today's?*
Using up to 365 preceding scores (never today's or future scores), it is
computed as

$$
p_{\mathrm{hist},t}=\frac{1+
\#\{s<t:\ p_{\mathrm{NB2},s}\le p_{\mathrm{NB2},t}\}}
{1+n_t},
$$

where the comparison includes only the last $n_t\le365$ recorded forecasts.
If fewer than 180 earlier scores exist, historical rarity is **unavailable**,
not zero. With a full 365-day reference its smallest possible value is
$1/366\approx0.00273$, so this method cannot resolve a nominal 0.1% tail.

This is a **rolling empirical rank**, not a formally guaranteed conformal
p-value. Serial dependence, drifting activity levels, and clusters of extreme
days can invalidate nominal coverage. In particular, a remarkable raw NB2
score can be relatively ordinary compared with a reference window that already
contains many extreme scores. **We retain both measures** instead of letting
one erase the other.

## From daily scores to episodes

The catalog groups statistically unusual dates into episodes; these are the
objects shown on the Anomalies page and marked on source charts.

| Role | Raw NB2 cutoff | Historical-rarity cutoff | Combination |
|---|---:|---:|---|
| **Episode seed** | `<= 0.001` | `<= 0.01` | Either is sufficient |
| **Neighboring candidate** | `<= 0.01` | `<= 0.05` | Either is sufficient |

A candidate can be a seed or a less extreme neighboring day. Candidates are
grouped if they point in the **same direction** and the gap between consecutive
candidate dates is at most **two calendar days** (up to one unflagged day
between them). A group without any seed is discarded.

An episode records:

- **Start / end:** earliest and latest candidate dates of the group. The totals
  and duration include any unflagged day *inside* that span.
- **Detected:** the first date when a *seed* criterion was met; it can be
  later than the episode's displayed start.
- **Observed / expected:** sums across the episode's date span, using each
  date's past-only NB2 expectation. Their ratio describes aggregate magnitude.
- **Evidence / algorithms:** `Calendar-aware NB2` (Users) or `NB2`
  (Classifications and Talk) when a raw seed was present, and `Rolling
  historical rarity (365d)` when an empirical seed was present. **Both** can
  be shown. These are complementary score rules, not independent detectors
  or independent confirmations.
- **Episode scores:** the smallest relevant seed-day scores. They are
  descriptive summaries, **not** p-values for the combined multi-day event.

The underlying **daily model predictions** use only past data. But grouping
and episode boundaries are computed over the currently available evaluation
window: after subsequent dates arrive, the displayed start/end or contextual
annotations can expand. Do not confuse a retrospective episode span with
information actually available on its earliest date.

### Cross-source context: decreases versus increases

Only **drops** receive a cross-source note. A drop episode is annotated when
a *different* source has a drop whose **first seed date** is within **one
calendar day** of the first episode's seed date. It is presented as a
*possible shared service or reporting issue (unverified)*.

This is **contextual matching**, not a joint statistical test. The three
metrics are related platform behaviors, and their errors need not be
independent. Furthermore, because matching is performed on the final set of
episodes, a note can reference a second source's detection on the *next day*;
it is not necessarily known on the first source's detection date.

Coincident **spikes** are deliberately **not** treated as corroboration: a
registration burst, classification campaign, or increased Talk activity can
have very different explanations.

## Catalog, freshness, and the web interface

`rebuild-anomalies` writes `anomalies.json` atomically to `ANALYTICS_DATA_DIR`
(the mounted `/data` volume in Docker). The file contains a format identifier,
generation time, per-source analysis status/coverage/model metadata, and the
episode objects. Each source records the `last_modified_utc` revision of the
aggregate snapshot it used.

When `/anomalies` or a history chart reads the catalog, it checks that each
source's saved revision still matches the source's **current snapshot**.
Results from a changed snapshot are **hidden as stale** rather than treated
as current; unaffected sources may still display their episodes. An older
valid catalog remains on disk if atomic publication fails.

- **Anomalies page:** lists available episodes and their methods/scores;
  `?event=<episode-id>` selects and highlights one episode.
- **History charts:** a clickable diamond marks each episode's **detected**
  date. Clicking opens that episode on `/anomalies`.
- **Show on graph:** returns to `/<source>?episode=<episode-id>` and zooms
  around the episode with seven days of context on each side (subject to the
  saved date coverage).

The chart still shows historical counts and the trailing average. Neither
chart navigation nor catalog reading reruns NB2. **No extra database table,
individual-level telemetry, or continuous alerting service is created.**

### Refresh and rebuild workflow

The usual command is now:

```bash
refresh-data
```

Or click **Refresh data** in the dashboard. In both cases, the pipeline:

1. Acquires the established refresh lock and enforces the one-hour cooldown.
2. Refetches, validates, and atomically publishes each configured daily-count
   source. A failed source retains its previous valid snapshot.
3. Runs the offline anomaly builder against whichever validated snapshots are
   available and atomically publishes a new catalog.
4. Reports progress. The Dash version uses a Diskcache background callback and
   reloads the current page when processing finishes.

For an **offline-only** catalog rebuild, without another collection:

```bash
rebuild-anomalies
# equivalent:
python -m zooniverse_dashboard.anomalies
```

An explicit offline rebuild is also useful after changing analysis code. A
source with insufficient history or a failed fit gets a non-ready analysis
status, not invented anomaly results. The pipeline can therefore finish with
**partial failure** even when some metrics and episodes are available.
See [Operations](operations.md) for data-collection safeguards and logs.

## Interpretation, assumptions, and known limitations

The following are intentional limitations of this **quick-inspection** tool:

1. **No ground truth.** The algorithm detects unusual *aggregate behavior*,
   not attacks, fraud, service outages, or their causes. There is no labeled
   incident dataset on which to estimate true precision or recall.
2. **No proven false-positive rate.** NB2 forecasts are model-dependent, and
   the rolling ranks are not guaranteed calibrated under serial dependence or
   nonstationarity. A 1% threshold is a *ranking/selection rule*, not a
   demonstrated 1% incident rate.
3. **Zero counts may be missing reporting.** The upstream aggregation pipeline
   calendar-fills missing days, but the saved JSON does not preserve which
   individual ERAS buckets were present in the provider response. A zero can
   mean a genuine zero, an omitted bucket, or upstream incompleteness.
   Simultaneous drops across all three sources therefore do not prove an outage.
4. **Counts are not unique people.** Classifications and comments measure
   actions; a small number of very active participants can affect counts.
   Multiple sources need not behave alike during legitimate activity spikes.
5. **Simple time structure.** The model has weekday indicators, a local trend,
   and limited holiday features for Users only; it does not explicitly fit
   project launches, school schedules, campaigns, changing annual seasonality,
   or multiple persistent operational regimes.
6. **Extreme tails and reference contamination.** NB2's tail shape can be
   wrong during clustered bursts or disruptions; historically extreme episodes
   can influence empirical ranks for up to 365 later days.
7. **Limited historical inspection.** Scoring is limited to the newest 730
   eligible dates; older charts may contain counts without anomaly markers.
   A restricted collection window may not provide enough observations to fit.
8. **Past-only daily forecasts, retrospective episode summaries.** Individual
   forecasts and reference ranks do not use future counts; episode grouping,
   cross-source notes, and catalog publication reflect the dates available
   *at the time of rebuilding*.
9. **Operational rather than real-time.** Refreshes are manual; scores can
   become stale or unavailable after a failed analysis, and they are not
   continuous alerts. They depend on validated, complete-enough aggregate
   snapshots on one persistent volume.

Interpret any episode as **"worth inspecting"**, not **"confirmed abnormal
user behavior"**. The count ratio and raw/historical scores convey useful
context, but they are not a diagnosis.

## Implementation reference

| Component | Responsibility |
|---|---|
| `src/zooniverse_dashboard/anomalies/compute.py` | NB2 fit, past-only scoring, threshold rules, episodes, shared-drop notes |
| `src/zooniverse_dashboard/anomalies/catalog.py` | Offline rebuild, atomic catalog, revision validation, visible episodes |
| `src/zooniverse_dashboard/anomalies/__main__.py` | Standalone rebuild command |
| `src/zooniverse_dashboard/pipeline.py` | Refresh-then-analysis orchestration |
| `src/zooniverse_dashboard/pages/anomalies.py` | Episode listing, details and selected-episode route |
| `src/zooniverse_dashboard/ui.py` | Episode diamonds, chart click-through, zoom-to-episode |
| `src/zooniverse_dashboard/assets/episode_pointer.js` | Mouse-pointer affordance over clickable diamonds |

The earlier model comparisons and extended calibration studies are retained on
the research branch but are not prerequisites for dashboard rendering.
