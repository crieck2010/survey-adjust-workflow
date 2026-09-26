"""Path 3 -- parametric traverse adjustment (angles + distances).

Unknowns are the easting/northing of every free station; at least two
stations must be held fixed (position *and* orientation need constraining).
Observation equations, linearized about approximate coordinates and solved
by iterated weighted least squares (Gauss-Newton):

* distance P->Q:  d + v = hypot(Eq-Ep, Nq-Np)
* angle at B (A-B-C): theta + v = az(B->C) - az(B->A),
  azimuths clockwise from north, residuals wrapped to (-pi, pi].

Weights come from the user's stochastic model: angles from
``angles.arcseconds``, distances from ``distances.mm + ppm``. Data snooping
runs on the final linearization; the surviving set is re-solved and its
numbers go in the report.

Interchange format (the shape a future total-station importer targets)::

    {"stations": [{"id": "A", "easting": 1000.0, "northing": 2000.0,
                   "fixed": true},
                  {"id": "B", "easting": 1100.5, "northing": 1999.8,
                   "fixed": false}],          # free-station coords are
                                               # approximate; refined in place
     "observations": [
        {"type": "distance", "from": "A", "to": "B",
         "value_m": 100.002, "id": "D1"},
        {"type": "angle", "at": "B", "from": "A", "to": "C",
         "value_deg": 90.0011, "id": "A1"}]}

This path is planimetric (E, N) only; heights come from the level net.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional, Tuple

from . import stochastic
from .snooping import (
    LinResult,
    RejectedObservation,
    SnoopResult,
    iterative_snooping,
)
from ._peers import require_adjust, require_adjust_matrices

TWO_PI = 2.0 * math.pi


def _wrap_pi(a: float) -> float:
    """Wrap angle to (-pi, pi]."""
    a = a % TWO_PI
    if a > math.pi:
        a -= TWO_PI
    return a


def _azimuth(ep: float, np_: float, eq: float, nq: float) -> float:
    """Azimuth P->Q, clockwise from north, radians."""
    return math.atan2(eq - ep, nq - np_)


def _az_partials(ep: float, np_: float, eq: float, nq: float
                 ) -> Tuple[float, float, float, float]:
    """d(az)/d(Ep,Np,Eq,Nq)."""
    de, dn = eq - ep, nq - np_
    l2 = de * de + dn * dn
    if l2 <= 0:
        raise ValueError("zero-length line in azimuth partials")
    return -dn / l2, de / l2, dn / l2, -de / l2


@dataclass
class TraverseStation:
    id: str
    easting: float      # approximate for free stations; refined in place
    northing: float
    fixed: bool


@dataclass
class TraverseObs:
    kind: str           # "distance" | "angle"
    label: str
    value: float        # metres, or radians for angles
    sigma: float
    # distance: p_from, p_to. angle: p_at, p_from, p_to
    p_from: Optional[str] = None
    p_to: Optional[str] = None
    p_at: Optional[str] = None
    weight_reasons: List[str] = dc_field(default_factory=list)


@dataclass
class TraverseResult:
    stations: List[Dict[str, Any]]  # id, easting, northing, sigma_e, sigma_n, fixed
    residuals: List[float]          # computed - observed (rad for angles)
    residual_labels: List[str]
    sigma0: Optional[float]
    dof: int
    observations: List[TraverseObs]  # kept set
    rejected: List[RejectedObservation]
    snoop: SnoopResult
    global_test: Dict[str, Any]
    iterations: int
    warnings: List[str]


def load_traverse(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "stations" not in data \
            or "observations" not in data:
        raise ValueError("traverse file must be an object with "
                         "'stations' and 'observations'")
    return data


def _parse(data: Dict[str, Any], cfg: Dict[str, Any]
           ) -> Tuple[List[TraverseStation], List[TraverseObs], List[str]]:
    warnings: List[str] = []
    seen = set()
    stations: List[TraverseStation] = []
    for s in data["stations"]:
        sid = s.get("id")
        if not sid or sid in seen:
            raise ValueError(f"traverse: duplicate or missing station id {sid!r}")
        seen.add(sid)
        for key in ("easting", "northing"):
            if s.get(key) is None:
                raise ValueError(
                    f"traverse: station {sid!r} needs {key!r} (approximate "
                    f"coordinates are fine for free stations)")
        stations.append(TraverseStation(
            id=sid, easting=float(s["easting"]), northing=float(s["northing"]),
            fixed=bool(s.get("fixed", False))))
    n_fixed = sum(1 for s in stations if s.fixed)
    if n_fixed < 2:
        raise ValueError(
            "traverse: at least two fixed stations are required to constrain "
            "position and orientation")
    if not any(not s.fixed for s in stations):
        raise ValueError("traverse: no free stations to adjust")

    obs: List[TraverseObs] = []
    for i, o in enumerate(data["observations"]):
        kind = o.get("type")
        label = o.get("id", f"obs-{i}")
        if kind == "distance":
            for key in ("from", "to", "value_m"):
                if o.get(key) is None:
                    raise ValueError(
                        f"traverse observation {label!r}: missing {key!r}")
            if o["from"] not in seen or o["to"] not in seen:
                raise ValueError(
                    f"traverse observation {label!r}: unknown station")
            if o["from"] == o["to"]:
                raise ValueError(
                    f"traverse observation {label!r}: from == to")
            if "sigma_m" in o and o["sigma_m"] is not None:
                sigma = float(o["sigma_m"])
                if sigma <= 0:
                    raise ValueError(
                        f"traverse observation {label!r}: sigma_m must be positive")
                reasons = ["user-supplied sigma_m"]
            else:
                sigma, reasons = stochastic.sigma_distance(
                    float(o["value_m"]), cfg, obs_id=label)
            obs.append(TraverseObs(
                kind="distance", label=f"{label} (dist {o['from']}->{o['to']})",
                value=float(o["value_m"]), sigma=sigma,
                p_from=o["from"], p_to=o["to"], weight_reasons=reasons))
        elif kind == "angle":
            for key in ("at", "from", "to", "value_deg"):
                if o.get(key) is None:
                    raise ValueError(
                        f"traverse observation {label!r}: missing {key!r}")
            if o["at"] not in seen or o["from"] not in seen or o["to"] not in seen:
                raise ValueError(
                    f"traverse observation {label!r}: unknown station")
            if len({o["at"], o["from"], o["to"]}) < 3:
                raise ValueError(
                    f"traverse observation {label!r}: at/from/to must differ")
            if "sigma_arcsec" in o and o["sigma_arcsec"] is not None:
                sigma = float(o["sigma_arcsec"]) * stochastic.ARCSEC_TO_RAD
                if sigma <= 0:
                    raise ValueError(
                        f"traverse observation {label!r}: sigma_arcsec must be positive")
                reasons = ["user-supplied sigma_arcsec"]
            else:
                sigma, reasons = stochastic.sigma_angle(cfg, obs_id=label)
            obs.append(TraverseObs(
                kind="angle",
                label=f"{label} (angle {o['from']}-{o['at']}-{o['to']})",
                value=math.radians(float(o["value_deg"])), sigma=sigma,
                p_at=o["at"], p_from=o["from"], p_to=o["to"],
                weight_reasons=reasons))
        else:
            raise ValueError(
                f"traverse observation {label!r}: type must be 'distance' "
                f"or 'angle', got {kind!r}")
    return stations, obs, warnings


def _solve_subset(stations: List[TraverseStation], obs: List[TraverseObs],
                  ) -> Tuple[Dict[str, Tuple[float, float]], LinResult, int]:
    """Iterated LS on a subset. Returns (coords, final LinResult, iterations).

    coords maps every station id -> (E, N) at convergence.
    """
    m_ = require_adjust_matrices()
    free = [s for s in stations if not s.fixed]
    uidx = {}
    for s in free:
        uidx[s.id] = (2 * len(uidx), 2 * len(uidx) + 1)
    m = 2 * len(free)
    coords = {s.id: (s.easting, s.northing) for s in stations}

    def calc_distance(o: TraverseObs):
        ep, np_ = coords[o.p_from]
        eq, nq = coords[o.p_to]
        return math.hypot(eq - ep, nq - np_)

    def calc_angle(o: TraverseObs):
        eb, nb = coords[o.p_at]
        ea, na = coords[o.p_from]
        ec, nc = coords[o.p_to]
        return _wrap_pi(_azimuth(eb, nb, ec, nc) - _azimuth(eb, nb, ea, na))

    iterations = 0
    for iterations in range(1, 51):
        k = len(obs)
        a = m_.zeros(k, m)
        ell = [0.0] * k
        for r, o in enumerate(obs):
            if o.kind == "distance":
                ep, np_ = coords[o.p_from]
                eq, nq = coords[o.p_to]
                d = math.hypot(eq - ep, nq - np_)
                if d <= 0:
                    raise ValueError(
                        f"traverse: zero distance on {o.label}")
                ell[r] = o.value - d                      # observed - computed
                de, dn = (eq - ep) / d, (nq - np_) / d     # d(d)/d(Eq,Nq)
                if o.p_from in uidx:
                    i, j = uidx[o.p_from]
                    a[r][i], a[r][j] = -de, -dn
                if o.p_to in uidx:
                    i, j = uidx[o.p_to]
                    a[r][i], a[r][j] = de, dn
            else:
                eb, nb = coords[o.p_at]
                ea, na = coords[o.p_from]
                ec, nc = coords[o.p_to]
                calc = _wrap_pi(_azimuth(eb, nb, ec, nc)
                                - _azimuth(eb, nb, ea, na))
                ell[r] = _wrap_pi(o.value - calc)         # observed - computed
                # theta = az(B->C) - az(B->A).
                # d az(P->Q)/dQ = (dc_e, dc_n); d az(P->Q)/dP = -(d az/dQ).
                # dtheta/dC = d az(B->C)/dC
                # dtheta/dA = -d az(B->A)/dA
                # dtheta/dB = d az(B->C)/dB - d az(B->A)/dB
                #           = -d az(B->C)/dC + d az(B->A)/dA
                _, _, dc_e, dc_n = _az_partials(eb, nb, ec, nc)
                _, _, da_e, da_n = _az_partials(eb, nb, ea, na)
                if o.p_at in uidx:
                    i, j = uidx[o.p_at]
                    a[r][i] = -dc_e + da_e
                    a[r][j] = -dc_n + da_n
                if o.p_to in uidx:
                    i, j = uidx[o.p_to]
                    a[r][i], a[r][j] = dc_e, dc_n
                if o.p_from in uidx:
                    i, j = uidx[o.p_from]
                    a[r][i], a[r][j] = -da_e, -da_n
        p = [1.0 / (o.sigma ** 2) for o in obs]
        pmat = [[p[i] if i == j else 0.0 for j in range(k)] for i in range(k)]
        at = m_.transpose(a)
        n = m_.matmul(at, m_.matmul(pmat, a))
        t = m_.matvec(m_.matmul(at, pmat), ell)
        try:
            qxx = m_.inverse(n)
        except ValueError as exc:
            raise ValueError(
                "traverse normal equations singular: check control and "
                "observation geometry") from exc
        dx = m_.matvec(qxx, t)
        for s in free:
            i, j = uidx[s.id]
            e, n_ = coords[s.id]
            coords[s.id] = (e + dx[i], n_ + dx[j])
        if max(abs(v) for v in dx) < 1e-9:
            break
    else:
        raise ValueError("traverse: failed to converge in 50 iterations")

    # Final linearization at convergence for residuals / covariance.
    k = len(obs)
    a = m_.zeros(k, m)
    v = [0.0] * k
    for r, o in enumerate(obs):
        if o.kind == "distance":
            ep, np_ = coords[o.p_from]
            eq, nq = coords[o.p_to]
            d = math.hypot(eq - ep, nq - np_)
            v[r] = d - o.value                       # computed - observed
            de, dn = (eq - ep) / d, (nq - np_) / d
            if o.p_from in uidx:
                i, j = uidx[o.p_from]
                a[r][i], a[r][j] = -de, -dn
            if o.p_to in uidx:
                i, j = uidx[o.p_to]
                a[r][i], a[r][j] = de, dn
        else:
            eb, nb = coords[o.p_at]
            ea, na = coords[o.p_from]
            ec, nc = coords[o.p_to]
            calc = _wrap_pi(_azimuth(eb, nb, ec, nc) - _azimuth(eb, nb, ea, na))
            v[r] = _wrap_pi(calc - o.value)          # computed - observed
            _, _, dc_e, dc_n = _az_partials(eb, nb, ec, nc)
            _, _, da_e, da_n = _az_partials(eb, nb, ea, na)
            if o.p_at in uidx:
                i, j = uidx[o.p_at]
                a[r][i], a[r][j] = -dc_e + da_e, -dc_n + da_n
            if o.p_to in uidx:
                i, j = uidx[o.p_to]
                a[r][i], a[r][j] = dc_e, dc_n
            if o.p_from in uidx:
                i, j = uidx[o.p_from]
                a[r][i], a[r][j] = -da_e, -da_n
    p = [1.0 / (o.sigma ** 2) for o in obs]
    dof = k - m
    sigma0 = None
    if dof > 0:
        s02 = sum(vi * p[i] * vi for i, vi in enumerate(v)) / dof
        sigma0 = math.sqrt(max(s02, 0.0))
    lin = LinResult(residuals=v, design=a, weight_diag=p, dof=dof,
                    sigma0=sigma0)
    # stash qxx/coords for the caller via attributes on lin
    lin.qxx = qxx  # type: ignore[attr-defined]
    lin.coords = coords  # type: ignore[attr-defined]
    return coords, lin, iterations


def adjust_traverse(data: Dict[str, Any], cfg: Dict[str, Any],
                    alpha: float = 0.05,
                    snoop_threshold: float = 3.29) -> TraverseResult:
    """Full traverse workflow: parse, snoop, solve, test."""
    adjust = require_adjust()
    stations, obs, warnings = _parse(data, cfg)
    labels = [o.label for o in obs]

    def run_subset(idxs: List[int]) -> LinResult:
        sub = [obs[i] for i in idxs]
        _, lin, _ = _solve_subset(stations, sub)
        return lin

    snoop = iterative_snooping(obs, labels, run_subset,
                               threshold=snoop_threshold)
    kept = [obs[i] for i in snoop.kept_indices]
    coords, lin, iterations = _solve_subset(stations, kept)

    qxx = lin.qxx
    s0 = lin.sigma0 if lin.sigma0 is not None else 1.0
    free_ids = [s.id for s in stations if not s.fixed]
    uidx = {sid: (2 * i, 2 * i + 1) for i, sid in enumerate(free_ids)}
    out_stations = []
    for s in stations:
        e, n_ = coords[s.id]
        if s.fixed:
            out_stations.append({"id": s.id, "easting": e, "northing": n_,
                                 "sigma_e": 0.0, "sigma_n": 0.0,
                                 "fixed": True})
        else:
            i, j = uidx[s.id]
            out_stations.append({
                "id": s.id, "easting": e, "northing": n_,
                "sigma_e": s0 * math.sqrt(max(qxx[i][i], 0.0)),
                "sigma_n": s0 * math.sqrt(max(qxx[j][j], 0.0)),
                "fixed": False})

    if lin.dof > 0 and lin.sigma0 is not None:
        passed, statistic, critical = adjust.global_test(
            lin.dof, lin.sigma0 ** 2, alpha)
    else:
        passed, statistic, critical = False, float("nan"), float("nan")
        warnings.append("global chi-square test needs dof > 0; skipped")

    return TraverseResult(
        stations=out_stations,
        residuals=list(lin.residuals),
        residual_labels=[o.label for o in kept],
        sigma0=lin.sigma0, dof=lin.dof,
        observations=kept, rejected=snoop.rejected, snoop=snoop,
        global_test={"passed": passed, "statistic": statistic,
                     "critical": critical, "alpha": alpha},
        iterations=iterations, warnings=warnings)
