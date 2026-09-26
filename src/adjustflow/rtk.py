"""Path 1 -- RTK point adjustment: repeated occupations, weighted means.

The Emlid rover often occupies the same point name several times (re-shots
across sessions or days). Each occupation i carries receiver RMS per
component; the user-dictated stochastic model turns those into a-priori
sigmas (``stochastic.sigma_rtk``). The adjusted position is the
variance-weighted mean per component:

    x_hat = sum(w_i x_i) / sum(w_i),   w_i = 1/sigma_i^2
    sigma_x_hat^2 = 1 / sum(w_i)

The datum is *held from the receiver solution*: Emlid already fixed the
rover against the base station, so the adjusted coordinates inherit the
base-station datum. This path does not re-estimate the datum; the report
states which base(s) it came from and why.

FLOAT/SINGLE policy comes from the weights file (rtk.float_policy):
exclude (default, with warning), downweight (sigma x downweight_factor),
or include (with warning). Excluded occupations are listed, never hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import stochastic
from ._peers import require_field


@dataclass
class Occupation:
    session_id: str
    easting: float
    northing: float
    elevation: Optional[float]
    sigma_e: float
    sigma_n: float
    sigma_u: float
    solution: str
    used_fallback_sigma: bool
    weight_reasons: List[str]
    # Blunder-screen fields, filled by screen_occupations().
    suspect: bool = False
    suspect_reason: str = ""


@dataclass
class RTKPoint:
    name: str
    easting: float
    northing: float
    elevation: Optional[float]
    sigma_e: float
    sigma_n: float
    sigma_u: Optional[float]
    n_occupations: int
    n_excluded: int
    sessions: List[str]
    occupations: List[Occupation]
    excluded: List[Dict[str, Any]]   # solution-policy exclusions, with reasons


@dataclass
class RTKResult:
    points: List[RTKPoint]
    datum_bases: List[Dict[str, Any]]  # base id, coords, n_points
    warnings: List[str]
    n_excluded_total: int = 0  # solution-policy exclusions, incl. fully-skipped points


def _apply_float_policy(solution: str, sigmas: Tuple[float, float, float],
                         cfg: Dict[str, Any]) -> Tuple[Tuple[float, float, float], Optional[str]]:
    """Return (sigmas, exclusion_reason). exclusion_reason None => keep."""
    params = cfg.get("rtk", {})
    policy = params.get("float_policy", "exclude")
    if solution == "FIX":
        return sigmas, None
    if policy == "include":
        return sigmas, None
    if policy == "downweight":
        f = params.get("downweight_factor", 3.0)
        return (sigmas[0] * f, sigmas[1] * f, sigmas[2] * f), None
    return sigmas, (f"solution {solution}: excluded by rtk.float_policy='exclude'")


def group_occupations(project: Any) -> Dict[str, List[Tuple[str, Any]]]:
    """Map point name -> [(session_id, SurveyPoint)] for projected points."""
    groups: Dict[str, List[Tuple[str, Any]]] = {}
    for session in project.sessions:
        for pt in session.points:
            if not pt.has_projected():
                continue
            groups.setdefault(pt.name, []).append((session.id, pt))
    return groups


def screen_occupations(occs: List[Occupation], threshold: float,
                       reject: bool) -> Tuple[List[Occupation], List[Occupation]]:
    """Flag statistically suspect occupations within one point's set.

    Standardized residual of each occupation against the leave-one-out
    weighted mean is overkill for field data; instead each occupation is
    tested against the full weighted mean with the combined uncertainty
    sqrt(sigma_occ^2 + sigma_mean^2) per component. |w| > threshold on any
    component marks it suspect. Returns (kept, suspect).
    """
    if len(occs) < 3:
        return occs, []  # not enough redundancy to judge anyone
    for comp in ("e", "n", "u"):
        vals = [(getattr(o, {"e": "easting", "n": "northing", "u": "elevation"}[comp]),
                 getattr(o, f"sigma_{comp}")) for o in occs]
        vals = [(v, s) for v, s in vals if v is not None]
        if len(vals) < 3:
            continue
        wsum = sum(1.0 / (s ** 2) for _, s in vals)
        mean = sum(v / (s ** 2) for v, s in vals) / wsum
        smean = math.sqrt(1.0 / wsum)
        for o in occs:
            v = getattr(o, {"e": "easting", "n": "northing", "u": "elevation"}[comp])
            s = getattr(o, f"sigma_{comp}")
            if v is None:
                continue
            w = abs(v - mean) / math.sqrt(s ** 2 + smean ** 2)
            if w > threshold:
                o.suspect = True
                o.suspect_reason = (
                    f"{comp}-component residual {abs(v - mean) * 1000:.1f} mm "
                    f"is {w:.1f} combined-sigma (> {threshold})")
    kept = [o for o in occs if not (o.suspect and reject)]
    return kept, [o for o in occs if o.suspect]


def adjust_rtk_points(project: Any, cfg: Dict[str, Any]) -> RTKResult:
    """Weighted-mean adjustment of every repeated RTK point in the job."""
    field = require_field()
    Solution = field.models.Solution
    warnings: List[str] = []
    points: List[RTKPoint] = []
    n_excluded_total = 0

    params = cfg.get("rtk", {})
    reject_suspect = bool(params.get("reject_suspect_occupations", False))
    suspect_threshold = float(params.get("suspect_threshold", 4.0))

    for name in sorted(group_occupations(project)):
        occs: List[Occupation] = []
        excluded: List[Dict[str, Any]] = []
        for session_id, pt in group_occupations(project)[name]:
            se, r_e, fb_e = stochastic.sigma_rtk(pt.rms_e, cfg, session_id, name)
            sn, r_n, fb_n = stochastic.sigma_rtk(pt.rms_n, cfg, session_id, name)
            rms_u = pt.rms_u if pt.rms_u else pt.rms_lateral
            su, r_u, fb_u = stochastic.sigma_rtk(rms_u, cfg, session_id, name)
            reasons = sorted(set(r_e + r_n + r_u))
            if fb_e or fb_n or fb_u:
                warnings.append(
                    f"point {name!r} session {session_id!r}: no receiver RMS; "
                    f"used default_sigma_m={params.get('default_sigma_m')}")
            (se, sn, su), excl_reason = _apply_float_policy(
                pt.solution, (se, sn, su), cfg)
            if excl_reason is not None:
                n_excluded_total += 1
                excluded.append({"session": session_id,
                                 "solution": pt.solution,
                                 "reason": excl_reason})
                warnings.append(f"point {name!r} session {session_id!r}: {excl_reason}")
                continue
            if pt.solution != Solution.FIX:
                warnings.append(
                    f"point {name!r} session {session_id!r}: solution "
                    f"{pt.solution} kept per float_policy="
                    f"{params.get('float_policy')!r}")
            occs.append(Occupation(
                session_id=session_id, easting=pt.easting,
                northing=pt.northing, elevation=pt.elevation,
                sigma_e=se, sigma_n=sn, sigma_u=su,
                solution=pt.solution,
                used_fallback_sigma=bool(fb_e or fb_n or fb_u),
                weight_reasons=reasons))

        if not occs:
            warnings.append(f"point {name!r}: no usable occupations after "
                            f"solution policy; skipped")
            continue

        kept, suspect = screen_occupations(occs, suspect_threshold, reject_suspect)
        for o in suspect:
            warnings.append(
                f"point {name!r} session {o.session_id!r}: suspect occupation "
                f"({o.suspect_reason}); "
                f"{'rejected' if reject_suspect else 'kept but flagged'}")
        use = kept if kept else occs  # never let screening empty the point

        def wmean(vals):
            ws = sum(1.0 / (s ** 2) for _, s in vals)
            return (sum(v / (s ** 2) for v, s in vals) / ws,
                    math.sqrt(1.0 / ws))

        e, se_m = wmean([(o.easting, o.sigma_e) for o in use])
        n, sn_m = wmean([(o.northing, o.sigma_n) for o in use])
        elev_vals = [(o.elevation, o.sigma_u) for o in use
                     if o.elevation is not None]
        elev, su_m = wmean(elev_vals) if elev_vals else (None, None)

        points.append(RTKPoint(
            name=name, easting=e, northing=n, elevation=elev,
            sigma_e=se_m, sigma_n=sn_m, sigma_u=su_m,
            n_occupations=len(occs), n_excluded=len(excluded),
            sessions=sorted({o.session_id for o in occs}),
            occupations=occs, excluded=excluded))

    datum_bases = [
        {"id": b.id, "easting": b.easting, "northing": b.northing,
         "elevation": b.elevation, "n_points": b.n_points}
        for b in project.base_stations
    ]
    return RTKResult(points=points, datum_bases=datum_bases,
                    warnings=warnings, n_excluded_total=n_excluded_total)
