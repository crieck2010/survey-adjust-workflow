"""Job I/O: read .sfield.json, write the adjusted-products contract.

Outputs of a run:

* ``<name>.sadj.json`` -- the machine-readable contract build #3
  (drafting/deliverables) consumes: adjusted coordinates + uncertainties +
  report path + provenance. Schema ``survey-adjust-workflow/adjusted`` v1.
* ``<name>.adjusted.sfield.json`` -- the input job with RTK weighted-mean
  coordinates written back over the raw occupations (same
  ``survey-field/job`` schema v1 -- coordinate *values* change, the schema
  does not). Only written for the RTK path.

Provenance: every output carries SHA-256 of the source job file, SHA-256
of the weights config, and the versions of all three packages.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from ._peers import require_field


FORMAT = "survey-adjust-workflow/adjusted"
SCHEMA_VERSION = 1


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def read_job_file(path: str):
    """Read a .sfield.json job via the survey-field peer (fail loud)."""
    field = require_field()
    return field.jobfile.read_job(path)


def package_versions() -> Dict[str, str]:
    versions = {"survey-adjust-workflow": "0.1.0"}
    try:
        versions["survey-adjust"] = require_adjust_version()
    except ImportError:
        versions["survey-adjust"] = "missing"
    try:
        versions["survey-field"] = require_field_version()
    except ImportError:
        versions["survey-field"] = "missing"
    return versions


def require_adjust_version() -> str:
    from ._peers import require_adjust
    return require_adjust().__version__


def require_field_version() -> str:
    from ._peers import require_field
    return require_field().__version__


def write_sadj(path: str, *, job_path: str, weights_path: Optional[str],
               weights_cfg: Dict[str, Any], paths_run: List[str],
               points: List[Dict[str, Any]],
               validity: Dict[str, Any], report_path: str,
               notes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Write the adjusted-products contract for build #3."""
    doc = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "generator": "survey-adjust-workflow 0.1.0",
        "source_job": {"path": job_path,
                       "sha256": sha256_file(job_path)},
        "weights": {"path": weights_path,
                    "sha256": sha256_file(weights_path)
                    if weights_path else None,
                    "config_sha256": sha256_json(weights_cfg)},
        "package_versions": package_versions(),
        "paths_run": paths_run,
        "points": points,
        "validity": validity,
        "report": report_path,
        "notes": notes or [],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    return doc


def write_adjusted_job_revision(project: Any, rtk_points: Dict[str, Any],
                                path: str) -> None:
    """Write a new .sfield.json revision with RTK means written back.

    Every occupation of an adjusted point name gets the weighted-mean
    coordinates (easting/northing/elevation); the point's ``meta`` records
    the adjustment provenance. The job schema is unchanged -- only
    coordinate values move -- so no schema bump is needed.
    """
    field = require_field()
    data = project.to_dict()
    for session in data.get("sessions", []):
        for pt in session.get("points", []):
            adj = rtk_points.get(pt.get("name"))
            if adj is None:
                continue
            pt["easting"] = adj["easting"]
            pt["northing"] = adj["northing"]
            if adj.get("elevation") is not None:
                pt["elevation"] = adj["elevation"]
            meta = pt.setdefault("meta", {})
            meta["adjusted_by"] = "survey-adjust-workflow 0.1.0 (rtk weighted mean)"
            meta["adjusted_sigma_e_m"] = adj["sigma_e"]
            meta["adjusted_sigma_n_m"] = adj["sigma_n"]
            if adj.get("sigma_u") is not None:
                meta["adjusted_sigma_u_m"] = adj["sigma_u"]
    # NOTE: Project has no free-form meta field, so revision provenance is
    # recorded per point (above) and in the .sadj.json contract -- the job
    # schema itself is untouched.
    rebuilt = field.models.Project.from_dict(data)
    field.jobfile.write_job(rebuilt, path)
