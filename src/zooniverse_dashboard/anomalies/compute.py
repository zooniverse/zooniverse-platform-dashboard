"""Fit past-only negative-binomial models and group daily anomaly episodes.

This offline, numerical analysis module consumes validated daily aggregate
counts from :class:`zooniverse_dashboard.snapshots.Snapshot` via the catalog
builder. It does not connect to databases, call ERAS, import Dash, or process
individual user records. Web requests use
:mod:`zooniverse_dashboard.anomalies.catalog` instead of fitting models.

A log-link NB2 regression uses a local linear trend and six weekday indicators
(with Monday as the reference); registrations additionally include fixed
Christmas and New Year windows. A 730-day moving training window is refitted
in 28-day blocks, with the evaluation restricted to the latest 730 eligible
dates. Forecasts and 365-day rolling rarity ranks use only earlier data.

Raw predictive-tail scores and empirical rarity ranks select candidate dates;
same-direction candidates close in time form episodes. These scores and
cross-source drop annotations are **inspection hints**, not verified incidents,
calibrated false-alarm probabilities, or causal explanations. See the handbook
page ``docs/anomaly_detection.md`` for equations and interpretation limits.

See Also
--------
:func:`zooniverse_dashboard.anomalies.compute.score_days`
    Compute past-only daily NB2 forecasts and scores.
:func:`zooniverse_dashboard.anomalies.compute.episodes`
    Group qualifying days into descriptive episode records.
:func:`zooniverse_dashboard.anomalies.catalog.build_catalog`
    Read validated snapshots and assemble the publishable catalog.
"""

from __future__ import annotations

import math
import warnings
from datetime import date, timedelta
from typing import Any

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import nbinom
from statsmodels.genmod.families import NegativeBinomial as NegativeBinomialFamily
from statsmodels.genmod.generalized_linear_model import GLM

TRAIN_DAYS: int = 730
EVALUATION_DAYS: int = 730
CALIBRATION_DAYS: int = 365
MIN_CALIBRATION_DAYS: int = 180
REFIT_DAYS: int = 28
RAW_SEED: float = 0.001
RAW_NEIGHBOR: float = 0.01
EMPIRICAL_SEED: float = 0.01
EMPIRICAL_NEIGHBOR: float = 0.05


def _holiday(day: date) -> tuple[float, float]:
    """Calculate fixed calendar indicators for the registration model.

    The two features flag December 23–26 and December 30–January 2,
    respectively. These are deterministic candidate holiday effects; they do not
    establish that observed changes are caused by holidays.

    Parameters
    ----------
    day : datetime.date
        UTC calendar day for which to evaluate holiday membership.

    Returns
    -------
    tuple[float, float]
        ``(christmas, new_year)`` indicators, each exactly ``0.0`` or ``1.0``.
    """
    christmas: bool = day.month == 12 and 23 <= day.day <= 26
    new_year: bool = (day.month == 12 and day.day >= 30) or (day.month == 1 and day.day <= 2)
    return float(christmas), float(new_year)


def design(days: tuple[date, ...], origin: date, *, calendar: bool) -> np.ndarray:
    """Construct the NB2 log-mean design matrix for a forecasting block.

    Columns are an intercept, elapsed years since ``origin`` (using 365.25 days
    per year), and Tuesday-through-Sunday binary indicators; Monday is implicit
    in the intercept. When ``calendar`` is true, two further columns encode the
    Christmas and New Year windows from ``_holiday``. The same origin must be
    used for a training block and the dates being forecast from it.

    Parameters
    ----------
    days : tuple[datetime.date, ...]
        Ordered calendar dates to represent, usually the training dates or the
        forecast block dates.
    origin : datetime.date
        First training date for the current fitted model; sets trend zero.
    calendar : bool
        If true, append two fixed holiday indicators for registrations; false for
        classifications or Talk activity.

    Returns
    -------
    numpy.ndarray
        Floating-point design matrix of shape ``(len(days), 8)`` without holidays
        or ``(len(days), 10)`` with holidays.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.compute.score_days`
        Construct and reuse this matrix for past-only prediction.
    """
    t: np.ndarray = np.asarray([(day - origin).days / 365.25 for day in days], dtype=float)
    features: list[np.ndarray] = [np.ones(len(days)), t]
    features.extend(
        np.asarray([d.weekday() == weekday for d in days], dtype=float) for weekday in range(1, 7)
    )
    if calendar:
        holidays: np.ndarray = np.asarray([_holiday(d) for d in days], dtype=float)
        features.extend((holidays[:, 0], holidays[:, 1]))
    return np.column_stack(features)


def _fit_nb2(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, float]:
    """Estimate NB2 coefficients and dispersion by profile likelihood.

    For each trial dispersion ``alpha``, fit a log-link negative-binomial GLM,
    then compute the full NB2 count log likelihood. Search over log-dispersion
    within ``[log(1e-6), log(8)]``. Reject nonconvergence, nonpositive or
    nonfinite fitted means, nonfinite likelihood, and optimizer solutions too
    close to the permitted dispersion boundaries. The routine intentionally has
    no silent Poisson fallback.

    Parameters
    ----------
    y : numpy.ndarray
        One-dimensional array of nonnegative training counts.
    x : numpy.ndarray
        Two-dimensional design matrix with one row per count and a column for
        every predictor, produced by :func:`zooniverse_dashboard.anomalies.compute.design`.

    Returns
    -------
    tuple[numpy.ndarray, float]
        Fitted regression coefficient vector and positive NB2 dispersion
        parameter ``alpha``. NB2 variance is ``mu + alpha * mu**2``.

    Raises
    ------
    ValueError
        If the number of observations is below the number of predictors plus 20.
    RuntimeError
        If optimization, GLM convergence, likelihood validation, or the final fit
        fails. Numerical libraries may also raise errors for invalid inputs.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.compute.design`
        Feature construction for the fit.
    """
    if len(y) < x.shape[1] + 20:
        raise ValueError("Too few training observations")

    def fit_for(log_alpha: float) -> tuple[Any, float]:
        """Fit and evaluate the NB2 regression at one log-dispersion value.

        Parameters
        ----------
        log_alpha : float
            Logarithm of the trial dispersion parameter.

        Returns
        -------
        tuple[typing.Any, float]
            Fitted statsmodels GLM results object and NB2 log likelihood.

        Raises
        ------
        RuntimeError
            If the GLM fails to converge or computed means/likelihood are invalid.
        """
        alpha: float = math.exp(log_alpha)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            result: Any = GLM(y, x, family=NegativeBinomialFamily(alpha=alpha)).fit(
                maxiter=120, tol=1e-9
            )
        if not result.converged:
            raise RuntimeError("NB2 GLM did not converge")
        mu: np.ndarray = np.exp(x @ np.asarray(result.params))
        if not np.all(np.isfinite(mu)) or np.any(mu <= 0):
            raise RuntimeError("Invalid NB2 fitted means")
        size: float = 1 / alpha
        ll: float = float(np.sum(nbinom.logpmf(y, size, size / (size + mu))))
        if not np.isfinite(ll):
            raise RuntimeError("Invalid NB2 likelihood")
        return result, ll

    def objective(log_alpha: float) -> float:
        """Convert a trial fit to a bounded scalar minimization objective.

        Parameters
        ----------
        log_alpha : float
            Candidate log-dispersion parameter.

        Returns
        -------
        float
            Negative log likelihood, or ``1e100`` for the anticipated numerical or
            convergence errors; other unexpected errors are not suppressed.
        """
        try:
            return -fit_for(log_alpha)[1]
        except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError):
            return 1e100

    bounds: tuple[float, float] = (math.log(1e-6), math.log(8.0))
    optimum: Any = minimize_scalar(
        objective,
        bounds=bounds,
        method="bounded",
        options={"xatol": 1e-4, "maxiter": 90},
    )
    if (
        not optimum.success
        or optimum.fun >= 1e99
        or min(optimum.x - bounds[0], bounds[1] - optimum.x) <= 1e-3
    ):
        raise RuntimeError("NB2 dispersion did not converge to an interior solution")
    fit, _ = fit_for(float(optimum.x))
    return np.asarray(fit.params, dtype=float), math.exp(float(optimum.x))


def score_days(rows: tuple[tuple[date, int], ...], *, source: str) -> list[dict[str, Any]]:
    """Compute past-only daily NB2 forecasts and surprise scores.

    Fit the model on preceding observations in 28-day blocks anchored to the
    *start* of the snapshot. Each fit uses up to 730 preceding calendar days.
    Only the newest 730 eligible dates are returned; up to 365 preceding forecast
    scores are used to form rolling empirical ranks, with at least 180 required.
    The first evaluated date requires at least 911 daily observations. Appending
    later dates does not change earlier individual forecasts when the start of
    the input series and model settings are unchanged.

    Raw scores are two-sided NB2 predictive-tail probabilities, capped at one.
    The empirical score ranks the current raw score against recent *earlier* raw
    scores with add-one smoothing. Serial dependence and model misspecification
    mean neither score is a guaranteed incident false-positive probability.

    Parameters
    ----------
    rows : tuple[tuple[datetime.date, int], ...]
        Chronologically ordered, continuous UTC daily-count pairs from a validated
        :class:`zooniverse_dashboard.snapshots.Snapshot`. Negative values, invalid
        numbers, and missing dates are rejected; zero counts are retained.
    source : str
        One of ``"users"``, ``"classifications"``, or ``"talks"``. The users
        model includes the holiday indicators; the other models do not.

    Returns
    -------
    list[dict[str, typing.Any]]
        Daily dictionaries with ``date`` (ISO UTC calendar date), ``observed``
        (int), ``expected`` (positive float), ``raw_p`` (float), ``empirical_p``
        (float or None), and ``direction`` (``spike`` or ``drop``). Returns an empty
        list if there is insufficient history or the input is empty.

    Raises
    ------
    ValueError
        If the source is unsupported or counts/dates fail validation.
    RuntimeError
        If any required NB2 fit or predicted mean fails; the exception reports
        the source and training end date without leaking underlying data.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.compute.episodes`
        Convert scored days into nearby same-direction episodes.
    :func:`zooniverse_dashboard.anomalies.catalog.build_catalog`
        Handle per-source analysis failures in the offline catalog.
    """
    if source not in ("users", "classifications", "talks"):
        raise ValueError("Invalid source")
    if not rows:
        return []
    days: tuple[date, ...] = tuple(day for day, _ in rows)
    values: np.ndarray = np.asarray([count for _, count in rows], dtype=float)
    if (
        np.any(~np.isfinite(values))
        or np.any(values < 0)
        or any(b - a != timedelta(days=1) for a, b in zip(days, days[1:]))
    ):
        raise ValueError("Invalid or discontinuous daily counts")
    n: int = len(rows)
    evaluation: int = min(EVALUATION_DAYS, n - TRAIN_DAYS - MIN_CALIBRATION_DAYS)
    if evaluation <= 0:
        return []
    evaluated_from: int = n - evaluation
    warmup_from: int = max(TRAIN_DAYS, evaluated_from - CALIBRATION_DAYS)
    first_block: int = TRAIN_DAYS + (warmup_from - TRAIN_DAYS) // REFIT_DAYS * REFIT_DAYS
    prior_p: list[float] = []
    scored: list[dict[str, Any]] = []
    calendar: bool = source == "users"
    for block in range(first_block, n, REFIT_DAYS):
        left: int = max(warmup_from, block)
        right: int = min(n, block + REFIT_DAYS)
        if left >= right:
            continue
        train_start: int = max(0, block - TRAIN_DAYS)
        training: tuple[date, ...] = days[train_start:block]
        origin: date = training[0]
        x: np.ndarray = design(training, origin, calendar=calendar)
        vx: np.ndarray = design(days[left:right], origin, calendar=calendar)
        try:
            beta, alpha = _fit_nb2(values[train_start:block], x)
        except Exception as exc:
            raise RuntimeError(
                f"NB2 fit failed for {source} training through {days[block - 1]} "
                f"({type(exc).__name__})"
            ) from None
        means: np.ndarray = np.exp(vx @ beta)
        if np.any(~np.isfinite(means)) or np.any(means <= 0):
            raise RuntimeError(f"Invalid {source} expected counts")
        size: float = 1.0 / alpha
        observed: np.ndarray = values[left:right].astype(int)
        prob: np.ndarray = size / (size + means)
        tail: np.ndarray = np.minimum(
            1.0,
            2
            * np.minimum(
                nbinom.cdf(observed, size, prob),
                nbinom.sf(observed - 1, size, prob),
            ),
        )
        for day, count, expected, raw_p in zip(days[left:right], observed, means, tail):
            historical: list[float] = prior_p[-CALIBRATION_DAYS:]
            empirical: float | None = (
                (1 + sum(p <= float(raw_p) for p in historical)) / (len(historical) + 1)
                if len(historical) >= MIN_CALIBRATION_DAYS
                else None
            )
            prior_p.append(float(raw_p))
            if day >= days[evaluated_from]:
                scored.append(
                    {
                        "date": day.isoformat(),
                        "observed": int(count),
                        "expected": float(expected),
                        "raw_p": float(raw_p),
                        "empirical_p": empirical,
                        "direction": "spike" if count >= expected else "drop",
                    }
                )
    return scored


def episodes(scored: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
    """Group daily statistical candidates into same-direction episodes.

    A day is a *seed* if its raw score is at most ``RAW_SEED`` or its available
    empirical rank is at most ``EMPIRICAL_SEED``. A *neighbor* meets the less
    stringent ``RAW_NEIGHBOR`` or ``EMPIRICAL_NEIGHBOR`` cutoff. Consecutive
    candidates are merged when their directions match and their dates are at most
    two days apart; groups without any seed are discarded. The observed and
    expected episode totals cover every scored date in the final inclusive span,
    including any intervening unflagged day.

    ``detected`` is the first seed date, not necessarily the episode start.
    The ``algorithms`` list identifies which of the two selection rules supplied
    seeds; these are complementary criteria on the same underlying series, *not*
    independent detectors. Episode scores summarize seed-day evidence; they are
    not multi-day event p-values. Episode grouping is retrospective and may grow
    as new observations enter the evaluated range.

    Parameters
    ----------
    scored : list[dict[str, typing.Any]]
        Chronologically ordered daily dictionaries from :func:`zooniverse_dashboard.anomalies.compute.score_days`, with
        date, observed, expected, direction, raw_p and empirical_p fields.
    source : str
        Source key used in the episode ID and evidence labels; callers provide a
        key accepted by :func:`zooniverse_dashboard.anomalies.compute.score_days`.

    Returns
    -------
    list[dict[str, typing.Any]]
        Episode dictionaries containing ID, source, start/end/detected dates,
        direction, span day count, observed and expected totals, raw/empirical
        seed scores, number of seed days, evidence labels, and mutable context.
        Returns an empty list if there are no seeded candidate groups.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.compute.add_shared_drop_context`
        Append possible cross-source decrease context after grouping.
    """
    if not scored:
        return []
    candidates: list[dict[str, Any]] = []
    for score in scored:
        empirical = score["empirical_p"]
        raw = score["raw_p"]
        seed = raw <= RAW_SEED or (empirical is not None and empirical <= EMPIRICAL_SEED)
        neighboring = raw <= RAW_NEIGHBOR or (
            empirical is not None and empirical <= EMPIRICAL_NEIGHBOR
        )
        if seed or neighboring:
            candidates.append({**score, "seed": seed})
    groups: list[list[dict[str, Any]]] = []
    for score in candidates:
        if groups and (
            groups[-1][-1]["direction"] == score["direction"]
            and (
                date.fromisoformat(score["date"]) - date.fromisoformat(groups[-1][-1]["date"])
            ).days
            <= 2
        ):
            groups[-1].append(score)
        else:
            groups.append([score])
    by_day: dict[str, dict[str, Any]] = {row["date"]: row for row in scored}
    results: list[dict[str, Any]] = []
    for group in groups:
        seeds: list[dict[str, Any]] = [row for row in group if row["seed"]]
        if not seeds:
            continue
        start: str = group[0]["date"]
        end: str = group[-1]["date"]
        cur: date = date.fromisoformat(start)
        final: date = date.fromisoformat(end)
        span: list[dict[str, Any]] = []
        while cur <= final:
            if cur.isoformat() in by_day:
                span.append(by_day[cur.isoformat()])
            cur += timedelta(days=1)
        observed: int = sum(row["observed"] for row in span)
        expected: float = sum(row["expected"] for row in span)
        model_seeds: list[dict[str, Any]] = [r for r in seeds if r["raw_p"] <= RAW_SEED]
        empirical_seeds: list[dict[str, Any]] = [
            r for r in seeds if r["empirical_p"] is not None and r["empirical_p"] <= EMPIRICAL_SEED
        ]
        algorithms: list[str] = []
        if model_seeds:
            algorithms.append("Calendar-aware NB2" if source == "users" else "NB2")
        if empirical_seeds:
            algorithms.append("Rolling historical rarity (365d)")
        direction: str = group[0]["direction"]
        results.append(
            {
                "id": f"{source}-{start}-{direction}",
                "source": source,
                "start": start,
                "end": end,
                "detected": min(row["date"] for row in seeds),
                "direction": direction,
                "days": len(span),
                "observed": int(observed),
                "expected": round(float(expected), 2),
                "raw_p": min(row["raw_p"] for row in seeds),
                "empirical_p": min(
                    (row["empirical_p"] for row in seeds if row["empirical_p"] is not None),
                    default=None,
                ),
                "seed_days": len(seeds),
                "algorithms": algorithms,
                "context": [],
            }
        )
    return results


def add_shared_drop_context(events: list[dict[str, Any]]) -> None:
    """Annotate near-simultaneous decreases across different sources in place.

    For each drop episode, inspect other sources' drop episodes for first seed
    dates within one UTC calendar day. Add a single contextual note listing
    those sources, without deducing an outage or correlating spikes. Since matching
    is based on the final retrospective episode catalog, a related detection may
    occur on the following day and need not have been known at the first
    source's detection date.

    Parameters
    ----------
    events : list[dict[str, typing.Any]]
        Mutable episode dictionaries from :func:`zooniverse_dashboard.anomalies.compute.episodes`, potentially spanning
        all three activity sources. May be modified in place by adding a
        ``context`` list; the function does not return a new collection.

    Returns
    -------
    None
        Mutates matching drop episodes only. Spike episodes are unchanged.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.compute.episodes`
        Build the episode records that receive annotations.
    """
    for event in events:
        if event["direction"] != "drop":
            continue
        related: list[str] = []
        first: date = date.fromisoformat(event["detected"])
        for other in events:
            if other["source"] == event["source"] or other["direction"] != "drop":
                continue
            second: date = date.fromisoformat(other["detected"])
            if abs((first - second).days) <= 1:
                related.append(other["source"])
        if related:
            event["context"] = [
                "Similar decrease in "
                + ", ".join(sorted(set(related)))
                + "; possible shared service or reporting issue (unverified)"
            ]
