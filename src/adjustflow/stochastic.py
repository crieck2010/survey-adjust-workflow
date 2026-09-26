"""User-dictated stochastic models for the adjustment workflow.

Every observation class gets an a-priori sigma model whose parameters the
*user* chooses, in a human-editable JSON file. The engine never invents
weights: it applies the user's model, resolves overrides (observation >
session > class > default), and fails loud on anything invalid.

Weighting rule everywhere: weight = 1 / sigma^2.

Classes:
    rtk       -- Emlid RTK positions / RTK-derived elevation differences.
                 sigma = rms_scale * receiver RMS per component, floored at
                 min_sigma_m. Falls back to default_sigma_m when the export
                 carries no RMS (flagged, never silent).
    levels    -- differential-level elevation differences.
                 sigma = mm_per_sqrt_km * sqrt(distance_km) / 1000, floored.
    angles    -- total-station angles. sigma = arcseconds -> radians.
    distances -- EDM distances. sigma = (mm + ppm * dist_m / 1000) / 1000.

A weights file looks like::

    {
      "rtk": {"rms_scale": 1.0, "min_sigma_m": 0.005,
              "default_sigma_m": 0.030, "float_policy": "exclude",
              "downweight_factor": 3.0},
      "levels": {"mm_per_sqrt_km": 3.0, "min_sigma_m": 0.002,
                 "default_distance_km": 1.0},
      "angles": {"arcseconds": 5.0},
      "distances": {"mm": 2.0, "ppm": 2.0},
      "datum_rename": {"BASE-1": "BM-A"},
      "datum_hold": null,
      "overrides": [
        {"scope": "session", "class": "rtk",
         "match": "2026-09-25_BASE-1",
         "params": {"rms_scale": 1.5},
         "reason": "afternoon canopy, multipath suspected"}
      ]
    }

See docs/WEIGHTING.md for how to choose these numbers.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

CLASSES = ("rtk", "levels", "angles", "distances")

# Which parameter keys are legal per class; anything else is a loud error.
_CLASS_PARAMS = {
    "rtk": {"rms_scale", "min_sigma_m", "default_sigma_m",
            "float_policy", "downweight_factor",
            "reject_suspect_occupations", "suspect_threshold"},
    "levels": {"mm_per_sqrt_km", "min_sigma_m", "default_distance_km"},
    "angles": {"arcseconds"},
    "distances": {"mm", "ppm"},
}

# Numeric parameters that must be strictly positive when present.
_POSITIVE = {
    "rtk": {"rms_scale", "min_sigma_m", "default_sigma_m",
            "downweight_factor", "suspect_threshold"},
    "levels": {"mm_per_sqrt_km", "min_sigma_m", "default_distance_km"},
    "angles": {"arcseconds"},
    "distances": {"mm", "ppm"},
}

ARCSEC_TO_RAD = math.pi / (180.0 * 3600.0)


def default_weights() -> Dict[str, Any]:
    """Sane starting stochastic model. The user is expected to tune it."""
    return {
        "rtk": {
            "rms_scale": 1.0,
            "min_sigma_m": 0.005,
            "default_sigma_m": 0.030,
            "float_policy": "exclude",      # exclude | downweight | include
            "downweight_factor": 3.0,
            "reject_suspect_occupations": False,
            "suspect_threshold": 4.0,
        },
        "levels": {
            "mm_per_sqrt_km": 3.0,
            "min_sigma_m": 0.002,
            "default_distance_km": 1.0,
        },
        "angles": {"arcseconds": 5.0},
        "distances": {"mm": 2.0, "ppm": 2.0},
        "datum_rename": {},
        "datum_hold": None,
        "overrides": [],
    }


def _check_positive(cls: str, params: Dict[str, Any], where: str) -> None:
    for key in _POSITIVE[cls]:
        if key in params:
            val = params[key]
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise ValueError(
                    f"{where}: {cls}.{key} must be a number, got {val!r}")
            if val <= 0:
                raise ValueError(
                    f"{where}: {cls}.{key} must be positive, got {val}")


def validate_weights(cfg: Dict[str, Any]) -> None:
    """Validate a weights config; raise ValueError naming the problem."""
    if not isinstance(cfg, dict):
        raise ValueError("weights config must be a JSON object")
    for cls in CLASSES:
        if cls in cfg:
            section = cfg[cls]
            if not isinstance(section, dict):
                raise ValueError(f"'{cls}' section must be an object")
            unknown = set(section) - _CLASS_PARAMS[cls]
            if unknown:
                raise ValueError(
                    f"'{cls}' has unknown parameters: {sorted(unknown)}; "
                    f"allowed: {sorted(_CLASS_PARAMS[cls])}")
            _check_positive(cls, section, f"'{cls}'")
    rtk = cfg.get("rtk", {})
    if "float_policy" in rtk and rtk["float_policy"] not in (
            "exclude", "downweight", "include"):
        raise ValueError(
            "rtk.float_policy must be 'exclude', 'downweight' or 'include', "
            f"got {rtk['float_policy']!r}")
    for key in ("datum_rename",):
        if key in cfg and not isinstance(cfg[key], dict):
            raise ValueError(f"'{key}' must be an object")
    if "datum_hold" in cfg and cfg["datum_hold"] is not None:
        if not isinstance(cfg["datum_hold"], list) or not all(
                isinstance(x, str) for x in cfg["datum_hold"]):
            raise ValueError("'datum_hold' must be a list of station names or null")
    overrides = cfg.get("overrides", [])
    if not isinstance(overrides, list):
        raise ValueError("'overrides' must be a list")
    for i, ov in enumerate(overrides):
        where = f"overrides[{i}]"
        if not isinstance(ov, dict):
            raise ValueError(f"{where} must be an object")
        scope = ov.get("scope")
        if scope not in ("observation", "session", "class"):
            raise ValueError(
                f"{where}: scope must be 'observation', 'session' or "
                f"'class', got {scope!r}")
        cls = ov.get("class")
        if cls not in CLASSES:
            raise ValueError(
                f"{where}: class must be one of {list(CLASSES)}, got {cls!r}")
        if scope in ("observation", "session") and not ov.get("match"):
            raise ValueError(f"{where}: scope '{scope}' needs a 'match' value")
        params = ov.get("params", {})
        if not isinstance(params, dict) or not params:
            raise ValueError(f"{where}: 'params' must be a non-empty object")
        unknown = set(params) - _CLASS_PARAMS[cls]
        if unknown:
            raise ValueError(
                f"{where}: unknown parameters for class '{cls}': "
                f"{sorted(unknown)}")
        _check_positive(cls, params, where)


def load_weights(path: str) -> Dict[str, Any]:
    """Load and validate a weights JSON file, merged over the defaults."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    validate_weights(raw)
    cfg = default_weights()
    for cls in CLASSES:
        if cls in raw:
            cfg[cls].update(raw[cls])
    for key in ("datum_rename", "datum_hold", "overrides"):
        if key in raw:
            cfg[key] = raw[key]
    return cfg


def _matching_overrides(cfg: Dict[str, Any], cls: str,
                        session_id: Optional[str],
                        obs_id: Optional[str]) -> List[Dict[str, Any]]:
    """Overrides for this class, weakest scope first (class < session <
    observation) so later entries win on merge."""
    rank = {"class": 0, "session": 1, "observation": 2}
    hits = []
    for ov in cfg.get("overrides", []):
        if ov.get("class") != cls:
            continue
        scope = ov["scope"]
        if scope == "class":
            hits.append(ov)
        elif scope == "session" and session_id == ov.get("match"):
            hits.append(ov)
        elif scope == "observation" and obs_id == ov.get("match"):
            hits.append(ov)
    hits.sort(key=lambda o: rank[o["scope"]])
    return hits


def resolve_params(cfg: Dict[str, Any], cls: str,
                   session_id: Optional[str] = None,
                   obs_id: Optional[str] = None) -> Dict[str, Any]:
    """Effective parameters for one class after override resolution.

    Returns (params, applied_override_reasons).
    """
    params = dict(cfg.get(cls, {}))
    reasons = []
    for ov in _matching_overrides(cfg, cls, session_id, obs_id):
        params.update(ov.get("params", {}))
        reason = ov.get("reason", "")
        reasons.append(
            f"{ov['scope']} override"
            + (f" on {ov['match']!r}" if ov.get("match") else "")
            + (f": {reason}" if reason else ""))
    return params, reasons


def _floor(value: float, floor: float) -> float:
    return value if value >= floor else floor


def sigma_rtk(rms: Optional[float], cfg: Dict[str, Any],
              session_id: Optional[str] = None,
              obs_id: Optional[str] = None) -> tuple[float, List[str], bool]:
    """A-priori sigma (m) for one RTK component from the receiver RMS.

    Returns (sigma, override_reasons, used_fallback). ``rms`` is the
    receiver-reported RMS for that component (rms_e / rms_n / rms_u).
    """
    params, reasons = resolve_params(cfg, "rtk", session_id, obs_id)
    scale = params["rms_scale"]
    floor = params["min_sigma_m"]
    if rms is None or rms <= 0:
        return params["default_sigma_m"], reasons, True
    return _floor(scale * rms, floor), reasons, False


def sigma_level(distance_km: Optional[float],
                cfg: Dict[str, Any],
                session_id: Optional[str] = None,
                obs_id: Optional[str] = None) -> tuple[float, List[str], bool]:
    """A-priori sigma (m) for a differential-level difference."""
    params, reasons = resolve_params(cfg, "levels", session_id, obs_id)
    d = distance_km if (distance_km and distance_km > 0) \
        else params["default_distance_km"]
    used_default = not (distance_km and distance_km > 0)
    sigma = params["mm_per_sqrt_km"] * math.sqrt(d) / 1000.0
    return _floor(sigma, params["min_sigma_m"]), reasons, used_default


def sigma_angle(cfg: Dict[str, Any],
                session_id: Optional[str] = None,
                obs_id: Optional[str] = None) -> tuple[float, List[str]]:
    """A-priori sigma (radians) for a total-station angle."""
    params, reasons = resolve_params(cfg, "angles", session_id, obs_id)
    return params["arcseconds"] * ARCSEC_TO_RAD, reasons


def sigma_distance(dist_m: float, cfg: Dict[str, Any],
                   session_id: Optional[str] = None,
                   obs_id: Optional[str] = None) -> tuple[float, List[str]]:
    """A-priori sigma (m) for an EDM distance."""
    params, reasons = resolve_params(cfg, "distances", session_id, obs_id)
    return (params["mm"] + params["ppm"] * dist_m / 1000.0) / 1000.0, reasons
