# INTEROP — handoff contracts for survey-adjust-workflow

Build #2 of the SurveySuite desktop product. It owns the **job → adjusted
coordinates** step: `.sfield.json` in, weighted least-squares adjustment
out, with a written justification. Everything downstream reads the
`.sfield.json` revision and/or the `.sadj.json` contract described here.

## Inputs

### 1. Job file — `.sfield.json` (`survey-field/job` schema v1)

Read with `field.jobfile.read_job`. This package:

- takes the datum from base stations via `field.adapters.to_adjust_control`
  (renamed through `weights.datum_rename`, restricted by
  `weights.datum_hold`);
- builds one elevation-difference observation per usable rover point
  (base → rover), weighted with the **rtk** stochastic class from the
  receiver's vertical RMS — honoring `rtk.float_policy`
  (exclude / downweight / include), never silently;
- groups repeated point names into occupations for the weighted-mean path.

Readers reject `schema_version > 1` loudly (handled by survey-field).

### 2. Weights file — user stochastic model (JSON)

`adjustflow weights-template` prints the schema with defaults. Sections
`rtk | levels | angles | distances`, plus `datum_rename`, `datum_hold`,
`overrides[]` (scope `observation > session > class > default`). Invalid
configs raise `ValueError` naming the exact problem. See
`docs/WEIGHTING.md` for how to choose values.

### 3. Traverse interchange — JSON (defined here, stable for v1)

The shape a future total-station importer targets — define against this,
don't invent another:

```jsonc
{
  "stations": [
    {"id": "A", "easting": 1000.0, "northing": 2000.0, "fixed": true},
    {"id": "B", "easting": 1100.5, "northing": 1999.8, "fixed": false}
    // free-station coords are APPROXIMATE (metres, project CRS);
    // the adjustment refines them in place
  ],
  "observations": [
    {"type": "distance", "from": "A", "to": "B", "value_m": 100.002,
     "sigma_m": 0.003, "id": "D1"},          // sigma_m optional
    {"type": "angle", "at": "B", "from": "A", "to": "C",
     "value_deg": 90.0011, "sigma_arcsec": 5.0, "id": "A1"}
    // value_deg: any degree convention (0-360 or signed); residuals are
    // wrapped to +/-180 deg. sigma_arcsec optional.
  ]
}
```

Rules: ≥2 fixed stations; every observation's stations must exist;
angle `at/from/to` must be three distinct stations; explicit `sigma_m` /
`sigma_arcsec` override the stochastic model for that observation only.
The adjustment is planimetric (E, N); heights come from the level net.

### 4. Level-observation interchange — JSON list (defined here, stable)

The shape a future survey-levels importer targets:

```jsonc
[{"from_station": "BM-A", "to_station": "TP1", "delta_h_m": 1.234,
  "distance_km": 0.42, "sigma_m": 0.004, "id": "L1"}]
// distance_km optional (falls back to levels.default_distance_km, flagged);
// sigma_m optional (overrides the mm/sqrt(km) model for that observation)
```

## Outputs

### A. `report.md` — the justification narrative (headline artifact)

Markdown, PDF-ready. Sections: provenance (job/weights SHA-256, engine
versions), verdict, datum (what held and why), observations, stochastic
model (each class's equation, the user's chosen values, the rationale,
applied overrides), adjustment results per path, chi-square global test
with plain-language interpretation, residual/blunder analysis (every
rejection listed with reason), adjusted coordinates with one-sigma
uncertainties, and a validity conclusion against four criteria (datum
defined / redundancy / stochastic model validated / no unresolved
blunders). Written to read like the adjustment memo behind a plat.

### B. `<name>.sadj.json` — adjusted-products contract (build #3 consumes)

Format `survey-adjust-workflow/adjusted`, `schema_version: 1`:

```jsonc
{"format": "survey-adjust-workflow/adjusted", "schema_version": 1,
 "generator": "survey-adjust-workflow 0.1.0",
 "source_job": {"path": "...", "sha256": "..."},
 "weights": {"path": "...", "sha256": "...", "config_sha256": "..."},
 "package_versions": {"survey-adjust-workflow": "0.1.0", ...},
 "paths_run": ["rtk", "levels"],
 "points": [{"name": "101", "easting": ..., "northing": ..., "elevation": ...,
             "sigma_e": ..., "sigma_n": ..., "sigma_u": ...,
             "source": "rtk-weighted-mean | level-net | traverse",
             "n_occupations": 3, "sessions": [...], "held": false}],
 "validity": {"valid": true,
              "checks": [["Datum defined", true, "..."], ...]},
 "report": "report.md", "notes": [...]}
```

Coordinates are `null` where the path doesn't estimate them (level net
gives elevations only; traverse gives E/N only). `validity.valid == false`
means build #3 should refuse to draft deliverables from these numbers.

### C. `<name>.adjusted.sfield.json` — job revision (RTK path only)

The input job with RTK weighted-mean coordinates written back over the
raw occupations (same `survey-field/job` schema v1 — values change, the
schema doesn't). Each touched point's `meta` records
`adjusted_by` + the adjusted sigmas. Build #3 may draft from this file
directly with any survey-field-compatible reader.

## Python API (for the desktop app, build #5)

```python
from adjustflow import stochastic, rtk, levelnet, traverse, report, jobio
from adjustflow.cli import _validity_checks  # (will be promoted to public)

cfg = stochastic.load_weights("weights.json")   # validated, merged
project = jobio.read_job_file("job.sfield.json")

rtk_res = rtk.adjust_rtk_points(project, cfg)
lvl_res = levelnet.adjust_level_network(project, cfg, level_obs=[...])
trv_res = traverse.adjust_traverse(traverse.load_traverse("t.json"), cfg)
```

All result objects are plain dataclasses; all engine I/O is JSON.

## What build #3 (drafting/deliverables) needs from this package

1. Read `.sadj.json`: `points[]` (adjusted coords + sigmas + `source`),
   `validity.valid` (refuse to draft when false), `report` (link/attach
   the justification to the plat set).
2. Or read the `.adjusted.sfield.json` revision with any survey-field
   reader for the RTK-adjusted point set.
3. Sigmas are one-sigma; error-ellipse / confidence-interval rendering
   is build #3's job (95% = x2.4477 for 2D, x1.96 per component).

## Versioning

- `.sadj.json` `schema_version` bumps on any breaking contract change;
  readers should reject newer versions loudly.
- Additive point fields do not bump the version.
- This package pins `survey-adjust` to a commit SHA and `survey-field`
  to `v0.1.0` (see `pyproject.toml`); bump deliberately, rerun the
  suite, and note it in the CHANGELOG.
