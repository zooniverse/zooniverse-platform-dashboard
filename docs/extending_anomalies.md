# Extending anomaly identification

The dashboard currently uses one offline **negative-binomial (NB2)** scoring
pipeline with two seed criteria: a raw predictive-tail probability and a
rolling historical rarity rank. These produce **one catalog of episodes**, not
two independent detectors. Users get an additional calendar-holiday design;
Classifications and Talk use the simpler design. The Anomalies page reports
statistical inspection candidates, not incidents or causal diagnoses.

For the existing mathematical model, numerical assumptions and limitations,
read [Anomaly detection](anomaly_detection.md) first. This page is a practical
extension guide for Python developers, not a replacement for model validation.

## Current data flow

```text
validated dense activity snapshots (users / classifications / talks)
    → anomalies.compute.score_days(..., source=...)
    → anomalies.compute.episodes(..., source=...)
    → anomalies.compute.add_shared_drop_context(...)
    → anomalies.catalog.build_catalog(...)
    → anomalies.catalog.write_catalog(...) / rebuild(...)
    → anomalies.catalog.visible_events(...) (checks snapshot revisions)
    → pages/anomalies.py and daily chart diamonds in ui.py
```

`anomalies/compute.py` holds the numerical logic; `anomalies/catalog.py`
loads snapshots, isolates failures and publishes the derived catalog;
`pages/anomalies.py` renders episode cards. Importantly, **`build_catalog()`
imports numerical dependencies only when analysis runs**. Do not make Dash
startup or page rendering import model-fitting work unnecessarily.

## Three different extension requests

**1. Change or add an episode-identification rule for the existing NB2 scores.**
For example, apply another threshold or contextual condition to scored days.
Modify `episodes()` and define how it interacts with seed, neighbor and
direction grouping. Decide whether the rule adds evidence to an existing
episode or creates a distinct type of episode. Test overlaps and ensure an
episode is not counted twice.

**2. Change the predictive baseline.** For a new seasonal feature or alternative
regression model, modify `design()`, `_fit_nb2()` or `score_days()` as needed.
This requires a methodological evaluation: preceding-only training, calendar
coverage, convergence failures, calibration, run time and stability. Even if a
model has better fit, don't present its tail score as a verified false-alarm
probability.

**3. Add a genuinely independent anomaly detector.** There is **no general
detector plug-in interface** today. Build a well-tested pure scoring function,
then explicitly integrate it in the offline catalog builder and in display
rules. Define how overlapping detections are deduplicated, attributed, and
linked. Do not claim it is enough to add a string to the `algorithms` list.

## Stable episode contract

`episodes()` currently returns dictionaries with fields such as `id`,
`source`, `direction`, `start`, `end`, `detected`, `days`, `observed`,
`expected`, `raw_p`, `empirical_p`, `seed_days`, `algorithms`, and `context`.
Study the actual creation code and `pages/anomalies.py` before extending these
records. The UI assumes that direction is `spike` or `drop`; episode links
and `catalog.ID_PATTERN` assume IDs of the form
`<source>-YYYY-MM-DD-<direction>` (source is one of the registered activity
sources). `event_by_id()` and `visible_events()` validate those IDs and reject
catalogs whose snapshot revision no longer matches.

If introducing a new kind of identifier, determine whether it fits the
existing **episode** schema or warrants a deliberately different schema and
page. A second detector emitting the same ID for a distinct event must not
silently overwrite it. Any change to event format, accepted IDs or algorithm
labels requires updating `catalog.py`, `pages/anomalies.py`, chart click/deep
links in `ui.py`, and the tests that protect navigation and stale results.

## A safe implementation workflow

1. **State the statistical question.** Specify what counts as an outlier,
   whether spikes/drops are asymmetric, target time scale, and which sources
   the method supports. Be explicit about the unit (daily event counts).
2. **Keep detector input offline and aggregate.** Read validated `Snapshot`
   series only. Project launches are sparse and not currently eligible for NB2;
   adding them is not a simple source-registry toggle.
3. **Use past-only training and calibration.** The current model uses 730-day
   training windows, 28-day refit blocks, up to 365 preceding score ranks,
   at least 180 rank history days, and at least 911 daily buckets before
   evaluation. Changing those values affects comparability and operational
   CPU cost; justify and test the change.
4. **Choose the integration point.** Existing-score selection belongs in
   `episodes()`. New models belong in a separate pure function, called from
   `catalog.build_catalog()` outside HTTP requests. Maintain per-source
   failure isolation and sanitized progress. If you change how versions are
   represented, document when saved anomaly catalogs must be rebuilt.
5. **Define episode representation and links.** Preserve current catalog and
   UI contracts unless a deliberate new schema is needed. Update source model
   labels, ID validation, episode cards and chart markers together.
6. **Compare against a fixed synthetic fixture.** Include normal weekday
   patterns, known spikes/drops, holiday effects, missing/zero counts,
   overdispersion, near-boundary episodes, and overlapping candidates.
7. **Rebuild offline** with `rebuild-anomalies`. Check that the new catalog
   matches current snapshot revisions and renders on `/anomalies` and source
   pages. No live source access is required.
8. **Update the numerical guide**, docstrings, acceptance tests and
   interpretation disclaimers. A threshold change is a scientific/model
   decision, not only a display tweak.

## Required regression guarantees

- Appending future observations never changes the predictions of an earlier
  evaluation window when its training origin and model settings are fixed.
- Training and empirical ranks use only earlier observations; test for
  look-ahead leakage.
- Unsupported sources or nonconsecutive/negative counts fail explicitly.
- A model failure for one source leaves other source catalog sections usable;
  a failed catalog write preserves the preceding file.
- The catalog is considered **stale** when its recorded source snapshot
  revision differs; no stale anomaly markers appear as current.
- Model evidence is labeled carefully. Cross-source drops provide context,
  not corroboration; a corresponding spike is not automatically evidence of
  a shared outage or attack.

Tests to inspect first: `test_anomaly_integration.py`,
`test_refresh_pipeline.py`, `test_page_characterization.py` and
`test_app.py`. Continue to use synthetic aggregate data, not live
volunteer records. `rebuild-anomalies` is the proper local execution path.
