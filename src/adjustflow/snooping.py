"""Blunder detection: standardized residuals and iterative data snooping.

Baarda-style data snooping on a linearized parametric adjustment. For a
weight matrix P = diag(1/sigma^2) (a-priori sigma0 = 1):

    Qxx = (A^T P A)^-1            cofactor matrix of the unknowns
    Qvv = P^-1 - A Qxx A^T        cofactor matrix of the residuals
    w_i = v_i / sqrt(qvv_ii)      standardized residual (a-priori)

Under H0 (no blunder) each w_i is approximately standard normal, so
|w_i| > 3.29 is a ~0.1% event per observation. The loop removes the worst
offender, re-adjusts, and repeats until clean or the iteration cap hits.

Rejected observations are returned with their reasons -- nothing is ever
silently dropped. The caller decides what to do with the kept set (usually
a final clean adjustment whose numbers go in the report).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ._peers import require_adjust_matrices


def _mat():
    """Matrix toolkit from the survey-adjust peer engine (lazy)."""
    return require_adjust_matrices()


@dataclass
class LinResult:
    """One linearized adjustment pass, enough for snooping diagnostics."""
    residuals: List[float]            # v = computed - observed
    design: List[List[float]]        # A, k x m
    weight_diag: List[float]         # diag(P), P = diag(1/sigma^2)
    dof: int
    sigma0: Optional[float]         # a-posteriori reference std. dev.


@dataclass
class RejectedObservation:
    index: int                       # index into the ORIGINAL observation list
    label: str                       # human label, e.g. "101 (RTK elev diff)"
    standardized_residual: float
    residual: float
    sigma: float
    iteration: int                   # snooping iteration that removed it
    reason: str


@dataclass
class SnoopResult:
    kept_indices: List[int]
    rejected: List[RejectedObservation]
    iterations: int
    threshold: float
    max_standardized_residual: float  # on the final kept set
    converged_clean: bool


def standardized_residuals(res: LinResult) -> List[float]:
    """|w_i| for each observation from a-priori cofactor propagation."""
    m_ = _mat()
    a = res.design
    p = res.weight_diag
    k = len(p)
    m = len(a[0]) if k else 0
    if k == 0 or m == 0:
        return []
    at = m_.transpose(a)
    pmat = [[p[i] if i == j else 0.0 for j in range(k)] for i in range(k)]
    n = m_.matmul(at, m_.matmul(pmat, a))
    try:
        qxx = m_.inverse(n)
    except ValueError:
        # Singular on a subset -- cannot standardize; caller treats as dirty.
        return [math.inf] * k
    # qvv_ii = 1/p_i - a_i Qxx a_i^T ; guard tiny negatives from roundoff.
    w = []
    for i in range(k):
        row = a[i]
        aq = m_.matvec(qxx, row)          # Qxx a_i^T  (qxx symmetric)
        aqa = sum(ri * aqi for ri, aqi in zip(row, aq))
        qvv = 1.0 / p[i] - aqa
        if qvv <= 1e-18:
            w.append(math.inf if abs(res.residuals[i]) > 0 else 0.0)
        else:
            w.append(abs(res.residuals[i]) / math.sqrt(qvv))
    return w


def _design_level_net(obs: List[Dict[str, Any]],
                      fixed: Dict[str, float]) -> List[List[float]]:
    """Rebuild the level-net design matrix (rows: -1 at from, +1 at to).

    This duplicates only the *geometry* of survey-adjust's level net so the
    snooper can form Qvv; the solution itself always comes from
    ``adjust.adjust_level_net``.
    """
    stations = set(fixed)
    for o in obs:
        stations.update((o["from_station"], o["to_station"]))
    free = sorted(s for s in stations if s not in fixed)
    idx = {s: j for j, s in enumerate(free)}
    a = _mat().zeros(len(obs), len(free))
    for r, o in enumerate(obs):
        if o["from_station"] not in fixed:
            a[r][idx[o["from_station"]]] = -1.0
        if o["to_station"] not in fixed:
            a[r][idx[o["to_station"]]] = 1.0
    return a


def level_linresult(obs: List[Dict[str, Any]],
                    fixed: Dict[str, float],
                    residuals: List[float],
                    dof: int,
                    sigma0: Optional[float]) -> LinResult:
    """Package a survey-adjust level-net result for snooping."""
    return LinResult(
        residuals=list(residuals),
        design=_design_level_net(obs, fixed),
        weight_diag=[1.0 / (o["sigma"] ** 2) for o in obs],
        dof=dof,
        sigma0=sigma0,
    )


def iterative_snooping(
    observations: List[Any],
    labels: List[str],
    run_adjustment: Callable[[List[int]], LinResult],
    threshold: float = 3.29,
    max_iterations: int = 10,
) -> SnoopResult:
    """Remove blunders one at a time until the set is clean.

    Args:
        observations: full original observation list (opaque to the snooper).
        labels: human label per observation, same order.
        run_adjustment: takes a list of *original* indices, returns LinResult
            for that subset. Must raise ValueError if the subset is
            unadjustable (disconnected / singular); the snooper then stops
            and reports the subset as not clean.
        threshold: |w| limit for flagging (default 3.29 ~ 0.1% two-sided).
        max_iterations: cap on removal rounds.

    Returns:
        SnoopResult with kept indices (into the original list), rejections
        with reasons, and whether the final set tested clean.
    """
    if threshold <= 0:
        raise ValueError("snooping threshold must be positive")
    kept = list(range(len(observations)))
    rejected: List[RejectedObservation] = []
    converged_clean = False
    iterations = 0

    for iteration in range(1, max_iterations + 1):
        iterations = iteration
        try:
            res = run_adjustment(kept)
        except ValueError as exc:
            # Subset broke the adjustment; stop, do not silently continue.
            rejected.append(RejectedObservation(
                index=-1, label="(adjustment failed)",
                standardized_residual=math.inf, residual=math.nan,
                sigma=math.nan, iteration=iteration,
                reason=f"snooping stopped: {exc}"))
            break
        w = standardized_residuals(res)
        if not w:
            break
        worst_local = max(range(len(w)), key=lambda i: w[i])
        worst_global = kept[worst_local]
        if w[worst_local] <= threshold:
            converged_clean = True
            break
        sig = math.sqrt(1.0 / res.weight_diag[worst_local]) \
            if res.weight_diag[worst_local] > 0 else math.nan
        rejected.append(RejectedObservation(
            index=worst_global,
            label=labels[worst_global],
            standardized_residual=w[worst_local],
            residual=res.residuals[worst_local],
            sigma=sig,
            iteration=iteration,
            reason=(f"standardized residual {w[worst_local]:.2f} exceeded "
                    f"threshold {threshold:.2f} (data snooping, "
                    f"iteration {iteration})")))
        kept.pop(worst_local)
        if not kept:
            break

    # Final diagnostics on the kept set (best effort).
    max_w = 0.0
    if kept:
        try:
            final = run_adjustment(kept)
            ws = standardized_residuals(final)
            max_w = max(ws) if ws else 0.0
            if max_w <= threshold:
                converged_clean = True
        except ValueError:
            converged_clean = False

    return SnoopResult(
        kept_indices=kept,
        rejected=rejected,
        iterations=iterations,
        threshold=threshold,
        max_standardized_residual=max_w,
        converged_clean=converged_clean,
    )
