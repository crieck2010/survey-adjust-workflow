"""Path 2 -- level-net workflow: job data -> survey-adjust -> report.

Two observation sources feed one weighted least-squares level net solved by
``adjust.adjust_level_net`` from the survey-adjust engine:

1. **RTK-derived elevation differences** from the job file: one diff per
   usable rover point, base -> rover, ``delta_h = rover_elev - base_elev``.
   These are RTK observations, so they take the **rtk** stochastic class:
   sigma = rms_scale * receiver vertical RMS (floored). The base they hang
   from is the held datum.
2. **Differential-level observations** from a user-supplied interchange
   file (the shape a future survey-levels importer will target)::

       [{"from_station": "BM-A", "to_station": "TP1",
         "delta_h_m": 1.234, "distance_km": 0.42, "id": "L1"}, ...]

   These take the **levels** class: sigma = mm_per_sqrt_km*sqrt(dist_km).

The datum comes from the job's base stations via
``field.adapters.to_adjust_control``, renamed through the weights file's
``datum_rename`` map and restricted by ``datum_hold`` when the user wants
fewer held stations. Data snooping runs on the combined set; the final
clean set is solved once more and those numbers go in the report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional, Tuple

from . import stochastic
from .snooping import (
    LinResult,
    RejectedObservation,
    SnoopResult,
    iterative_snooping,
    level_linresult,
)
from ._peers import require_adjust, require_field


@dataclass
class LevelObs:
    from_station: str
    to_station: str
    delta_h: float
    sigma: float
    source: str            # "rtk" | "levels"
    label: str
    weight_reasons: List[str] = dc_field(default_factory=list)


@dataclass
class LevelNetResult:
    points: List[Dict[str, Any]]      # station, elevation, std_dev
    residuals: List[float]
    adjusted_observations: List[float]
    sigma0: Optional[float]
    dof: int
    observations: List[LevelObs]       # the KEPT set, solution order
    rejected: List[RejectedObservation]
    snoop: SnoopResult
    global_test: Dict[str, Any]       # passed, statistic, critical, alpha
    fixed: Dict[str, float]           # held datum as solved
    warnings: List[str]


def _datum_from_job(project: Any, cfg: Dict[str, Any]) -> Tuple[Dict[str, float], List[str]]:
    field = require_field()
    control = field.adapters.to_adjust_control(project)
    rename = cfg.get("datum_rename", {}) or {}
    hold = cfg.get("datum_hold")
    warnings: List[str] = []
    fixed: Dict[str, float] = {}
    for base_id, elev in control.items():
        if hold is not None and base_id not in hold:
            warnings.append(f"datum: base {base_id!r} not in datum_hold; not held")
            continue
        name = rename.get(base_id, base_id)
        if name != base_id:
            warnings.append(f"datum: base {base_id!r} held as control {name!r}")
        fixed[name] = elev
    if not fixed:
        raise ValueError(
            "no held datum: the job has no base station with a known "
            "elevation (or datum_hold excluded them all)")
    return fixed, warnings


def _rtk_diff_observations(project: Any, cfg: Dict[str, Any],
                           fixed: Dict[str, float]) -> Tuple[List[LevelObs], List[str]]:
    """Base->rover elevation diffs, rtk stochastic class, float policy honored.

    The datum-rename map is applied to the from-station so renamed control
    stays linked to its observations.
    """
    field = require_field()
    Solution = field.models.Solution
    rename = cfg.get("datum_rename", {}) or {}
    obs: List[LevelObs] = []
    warnings: List[str] = []
    params = cfg.get("rtk", {})
    policy = params.get("float_policy", "exclude")
    base_elev = {b.id: b.elevation for b in project.base_stations}
    for session in project.sessions:
        be = base_elev.get(session.base_station_id)
        if be is None:
            continue
        from_station = rename.get(session.base_station_id,
                                  session.base_station_id)
        for pt in session.points:
            if pt.elevation is None:
                continue
            if pt.solution != Solution.FIX:
                if policy == "exclude":
                    warnings.append(
                        f"level net: point {pt.name!r} solution {pt.solution} "
                        f"excluded by float_policy='exclude'")
                    continue
                if policy == "downweight":
                    warnings.append(
                        f"level net: point {pt.name!r} solution {pt.solution} "
                        f"down-weighted x{params.get('downweight_factor')}")
            rms_u = pt.rms_u if pt.rms_u else pt.rms_lateral
            sigma, reasons, fallback = stochastic.sigma_rtk(
                rms_u, cfg, session.id, pt.name)
            if policy == "downweight" and pt.solution != Solution.FIX:
                sigma *= params.get("downweight_factor", 3.0)
            if fallback:
                warnings.append(
                    f"level net: point {pt.name!r} has no vertical RMS; "
                    f"used default_sigma_m={params.get('default_sigma_m')}")
            obs.append(LevelObs(
                from_station=from_station, to_station=pt.name,
                delta_h=pt.elevation - be, sigma=sigma, source="rtk",
                label=f"{pt.name} (RTK elev diff, {session.id})",
                weight_reasons=reasons))
    return obs, warnings


def load_level_observations(path: str) -> List[Dict[str, Any]]:
    """Read the differential-level interchange file (list of dicts)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("level-observation file must be a JSON list")
    for i, o in enumerate(data):
        for key in ("from_station", "to_station", "delta_h_m"):
            if key not in o:
                raise ValueError(
                    f"level observation [{i}]: missing {key!r}; each entry "
                    f"needs from_station, to_station, delta_h_m and "
                    f"optionally distance_km, sigma_m, id")
        if not isinstance(o["delta_h_m"], (int, float)):
            raise ValueError(f"level observation [{i}]: delta_h_m must be numeric")
    return data


def _level_diff_observations(raw: List[Dict[str, Any]],
                             cfg: Dict[str, Any]) -> List[LevelObs]:
    obs: List[LevelObs] = []
    for i, o in enumerate(raw):
        label = o.get("id", f"level-obs-{i}")
        if "sigma_m" in o and o["sigma_m"] is not None:
            sigma = float(o["sigma_m"])
            if sigma <= 0:
                raise ValueError(f"level observation {label!r}: sigma_m must be positive")
            reasons = ["user-supplied sigma_m"]
        else:
            sigma, reasons, _ = stochastic.sigma_level(
                o.get("distance_km"), cfg, obs_id=label)
        obs.append(LevelObs(
            from_station=o["from_station"], to_station=o["to_station"],
            delta_h=float(o["delta_h_m"]), sigma=sigma, source="levels",
            label=f"{label} (diff level {o['from_station']}->{o['to_station']})",
            weight_reasons=reasons))
    return obs


def adjust_level_network(project: Any, cfg: Dict[str, Any],
                         level_obs: Optional[List[Dict[str, Any]]] = None,
                         alpha: float = 0.05,
                         snoop_threshold: float = 3.29) -> LevelNetResult:
    """Full level-net workflow: build, snoop, solve, test."""
    adjust = require_adjust()
    warnings: List[str] = []

    fixed, dw = _datum_from_job(project, cfg)
    warnings.extend(dw)

    obs, w1 = _rtk_diff_observations(project, cfg, fixed)
    warnings.extend(w1)
    if level_obs:
        obs.extend(_level_diff_observations(level_obs, cfg))

    if not obs:
        raise ValueError("no usable elevation-difference observations")

    labels = [o.label for o in obs]
    obs_dicts = [{"from_station": o.from_station, "to_station": o.to_station,
                  "delta_h": o.delta_h, "sigma": o.sigma} for o in obs]

    def run_subset(idxs: List[int]) -> LinResult:
        sub = [obs_dicts[i] for i in idxs]
        da = adjust.DiffObservation
        result = adjust.adjust_level_net(
            [da(**d) for d in sub], fixed=dict(fixed))
        return level_linresult(sub, fixed, result.residuals,
                               result.dof, result.sigma0)

    snoop = iterative_snooping(obs, labels, run_subset,
                               threshold=snoop_threshold)

    kept_dicts = [obs_dicts[i] for i in snoop.kept_indices]
    kept_obs = [obs[i] for i in snoop.kept_indices]
    da = adjust.DiffObservation
    final = adjust.adjust_level_net([da(**d) for d in kept_dicts],
                                    fixed=dict(fixed))

    if final.dof > 0 and final.sigma0 is not None:
        passed, statistic, critical = adjust.global_test(
            final.dof, final.sigma0 ** 2, alpha)
    else:
        passed, statistic, critical = False, float("nan"), float("nan")
        warnings.append("global chi-square test needs dof > 0; skipped")

    return LevelNetResult(
        points=[{"station": p.station, "elevation": p.elevation,
                 "std_dev": p.std_dev} for p in final.points],
        residuals=list(final.residuals),
        adjusted_observations=list(final.adjusted_observations),
        sigma0=final.sigma0, dof=final.dof,
        observations=kept_obs, rejected=snoop.rejected, snoop=snoop,
        global_test={"passed": passed, "statistic": statistic,
                     "critical": critical, "alpha": alpha},
        fixed=fixed, warnings=warnings)
