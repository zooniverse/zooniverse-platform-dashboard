"""Past-only NB2 anomaly episode extraction over validated daily aggregates.

Anomaly candidates are statistical inspection hints, not incidents. The
calibration rank is descriptive under serial dependence and changing activity.
No database, API, Dash, or user-level records are accessed here.
"""

from __future__ import annotations

import math
import warnings
from datetime import date, timedelta

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import nbinom
from statsmodels.genmod.families import NegativeBinomial as NegativeBinomialFamily
from statsmodels.genmod.generalized_linear_model import GLM

TRAIN_DAYS = 730
EVALUATION_DAYS = 730
CALIBRATION_DAYS = 365
MIN_CALIBRATION_DAYS = 180
REFIT_DAYS = 28
RAW_SEED = 0.001
RAW_NEIGHBOR = 0.01
EMPIRICAL_SEED = 0.01
EMPIRICAL_NEIGHBOR = 0.05


def _holiday(day: date) -> tuple[float, float]:
    christmas = day.month == 12 and 23 <= day.day <= 26
    new_year = (day.month == 12 and day.day >= 30) or (day.month == 1 and day.day <= 2)
    return float(christmas), float(new_year)


def design(days: tuple[date, ...], origin: date, *, calendar: bool) -> np.ndarray:
    """Weekday and local linear trend, plus Christmas/New Year for registrations."""
    t = np.asarray([(day - origin).days / 365.25 for day in days], dtype=float)
    features = [np.ones(len(days)), t]
    features.extend(
        np.asarray([d.weekday() == weekday for d in days], dtype=float) for weekday in range(1, 7)
    )
    if calendar:
        holidays = np.asarray([_holiday(d) for d in days], dtype=float)
        features.extend((holidays[:, 0], holidays[:, 1]))
    return np.column_stack(features)


def _fit_nb2(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, float]:
    """Profile-likelihood NB2 fit; reject invalid or boundary solutions."""
    if len(y) < x.shape[1] + 20:
        raise ValueError("Too few training observations")

    def fit_for(log_alpha: float):
        alpha = math.exp(log_alpha)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            result = GLM(y, x, family=NegativeBinomialFamily(alpha=alpha)).fit(
                maxiter=120, tol=1e-9
            )
        if not result.converged:
            raise RuntimeError("NB2 GLM did not converge")
        mu = np.exp(x @ np.asarray(result.params))
        if not np.all(np.isfinite(mu)) or np.any(mu <= 0):
            raise RuntimeError("Invalid NB2 fitted means")
        size = 1 / alpha
        ll = float(np.sum(nbinom.logpmf(y, size, size / (size + mu))))
        if not np.isfinite(ll):
            raise RuntimeError("Invalid NB2 likelihood")
        return result, ll

    def objective(log_alpha: float) -> float:
        try:
            return -fit_for(log_alpha)[1]
        except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError):
            return 1e100

    bounds = (math.log(1e-6), math.log(8.0))
    optimum = minimize_scalar(
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


def score_days(rows: tuple[tuple[date, int], ...], *, source: str) -> list[dict]:
    """Return past-only forecast scores over at most the last 730 days.

    Forecast blocks are anchored to the start of the snapshot, not its end, so
    appending future observations cannot revise existing historical forecasts.
    """
    if source not in ("users", "classifications", "talks"):
        raise ValueError("Invalid source")
    if not rows:
        return []
    days = tuple(day for day, _ in rows)
    values = np.asarray([count for _, count in rows], dtype=float)
    if (
        np.any(~np.isfinite(values))
        or np.any(values < 0)
        or any(b - a != timedelta(days=1) for a, b in zip(days, days[1:]))
    ):
        raise ValueError("Invalid or discontinuous daily counts")
    n = len(rows)
    evaluation = min(EVALUATION_DAYS, n - TRAIN_DAYS - MIN_CALIBRATION_DAYS)
    if evaluation <= 0:
        return []
    evaluated_from = n - evaluation
    warmup_from = max(TRAIN_DAYS, evaluated_from - CALIBRATION_DAYS)
    first_block = TRAIN_DAYS + (warmup_from - TRAIN_DAYS) // REFIT_DAYS * REFIT_DAYS
    prior_p: list[float] = []
    scored: list[dict] = []
    calendar = source == "users"
    for block in range(first_block, n, REFIT_DAYS):
        left = max(warmup_from, block)
        right = min(n, block + REFIT_DAYS)
        if left >= right:
            continue
        train_start = max(0, block - TRAIN_DAYS)
        training = days[train_start:block]
        origin = training[0]
        x = design(training, origin, calendar=calendar)
        vx = design(days[left:right], origin, calendar=calendar)
        try:
            beta, alpha = _fit_nb2(values[train_start:block], x)
        except Exception as exc:
            raise RuntimeError(
                f"NB2 fit failed for {source} training through {days[block - 1]} "
                f"({type(exc).__name__})"
            ) from None
        means = np.exp(vx @ beta)
        if np.any(~np.isfinite(means)) or np.any(means <= 0):
            raise RuntimeError(f"Invalid {source} expected counts")
        size = 1.0 / alpha
        observed = values[left:right].astype(int)
        prob = size / (size + means)
        tail = np.minimum(
            1.0,
            2
            * np.minimum(
                nbinom.cdf(observed, size, prob),
                nbinom.sf(observed - 1, size, prob),
            ),
        )
        for day, count, expected, raw_p in zip(days[left:right], observed, means, tail):
            historical = prior_p[-CALIBRATION_DAYS:]
            empirical = (
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


def episodes(scored: list[dict], *, source: str) -> list[dict]:
    """Group same-direction NB2 candidates; expose independent algorithm evidence."""
    if not scored:
        return []
    candidates = []
    for score in scored:
        empirical = score["empirical_p"]
        raw = score["raw_p"]
        seed = raw <= RAW_SEED or (empirical is not None and empirical <= EMPIRICAL_SEED)
        neighboring = raw <= RAW_NEIGHBOR or (
            empirical is not None and empirical <= EMPIRICAL_NEIGHBOR
        )
        if seed or neighboring:
            candidates.append({**score, "seed": seed})
    groups: list[list[dict]] = []
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
    by_day = {row["date"]: row for row in scored}
    results = []
    for group in groups:
        seeds = [row for row in group if row["seed"]]
        if not seeds:
            continue
        start = group[0]["date"]
        end = group[-1]["date"]
        cur = date.fromisoformat(start)
        final = date.fromisoformat(end)
        span = []
        while cur <= final:
            if cur.isoformat() in by_day:
                span.append(by_day[cur.isoformat()])
            cur += timedelta(days=1)
        observed = sum(row["observed"] for row in span)
        expected = sum(row["expected"] for row in span)
        model_seeds = [r for r in seeds if r["raw_p"] <= RAW_SEED]
        empirical_seeds = [
            r for r in seeds if r["empirical_p"] is not None and r["empirical_p"] <= EMPIRICAL_SEED
        ]
        algorithms = []
        if model_seeds:
            algorithms.append("Calendar-aware NB2" if source == "users" else "NB2")
        if empirical_seeds:
            algorithms.append("Rolling historical rarity (365d)")
        direction = group[0]["direction"]
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


def add_shared_drop_context(events: list[dict]) -> None:
    """Mark shared drops as *context*, never infer outages or correlate spikes."""
    for event in events:
        if event["direction"] != "drop":
            continue
        related = []
        first = date.fromisoformat(event["detected"])
        for other in events:
            if other["source"] == event["source"] or other["direction"] != "drop":
                continue
            second = date.fromisoformat(other["detected"])
            if abs((first - second).days) <= 1:
                related.append(other["source"])
        if related:
            event["context"] = [
                "Similar decrease in "
                + ", ".join(sorted(set(related)))
                + "; possible shared service or reporting issue (unverified)"
            ]
