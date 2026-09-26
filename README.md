# survey-adjust-workflow

Least-squares adjustment workflow for the land-surveying suite — build #2
of the SurveySuite desktop product. It sits between
[survey-field](https://github.com/crieck2010/survey-field) (Emlid import →
`.sfield.json`) and the future drafting/deliverables stage.

Pure Python, zero third-party dependencies. The sibling engines are
**reused, not reimplemented**: `survey-adjust` (least-squares engine,
matrix toolkit, chi-square test) and `survey-field` (job files).

## What it does

Three adjustment paths under one **user-dictated stochastic model**:

1. **RTK point adjustment** — repeated occupations of the same point name
   → variance-weighted mean with propagated uncertainty. FLOAT/SINGLE
   policy is yours (`exclude` / `downweight` / `include`).
2. **Level-net workflow** — drives `survey-adjust.adjust_level_net` end to
   end from job base→rover diffs plus your differential-level
   observations, with your weights.
3. **Parametric traverse adjustment** — angles + distances, unknown
   station E/N, iterated weighted least squares (pure Python, using
   survey-adjust's matrix toolkit).

Plus, on every path:

- **You dictate the weights.** A human-editable JSON stochastic model:
  RTK from receiver RMS × your scale factor, levels in mm/√km, angles in
  arcseconds, distances in mm + ppm — overridable at class, session, or
  single-observation level. Invalid configs fail loud with the reason.
- **Blunder detection.** Standardized residuals + iterative Baarda data
  snooping. Rejections are listed with reasons, never silently dropped.
- **Justification report.** Every run writes a plain-language narrative —
  datum held and why, your weight choices with rationale, redundancy,
  chi-square test with interpretation, residual/blunder analysis,
  uncertainties, and a validity verdict against four professional
  criteria. It reads like the adjustment memo behind a plat.

## Quick start

```bash
pip install "survey-adjust-workflow @ git+https://github.com/crieck2010/survey-adjust-workflow@v0.1.0"

# 1. Start from the template and make the weights yours:
adjustflow weights-template > weights.json   # then edit

# 2. Run (job from survey-field's `field import`):
adjustflow run --job job.sfield.json --weights weights.json \
    --report report.md --out adjusted.sadj.json \
    --adjusted-job job.adjusted.sfield.json

# 3. With a traverse and differential levels:
adjustflow run --job job.sfield.json --weights weights.json \
    --report report.md --traverse traverse.json --level-obs levels.json
```

`adjustflow run` exits 0 when the adjustment is **VALID**, 1 when any
validity criterion fails (datum / redundancy / stochastic model /
blunders) — wire that into scripts and the desktop app.

## The weights file

```jsonc
{
  "rtk":       {"rms_scale": 1.0, "min_sigma_m": 0.005,
                "float_policy": "exclude", "downweight_factor": 3.0},
  "levels":    {"mm_per_sqrt_km": 3.0, "min_sigma_m": 0.002},
  "angles":    {"arcseconds": 5.0},
  "distances": {"mm": 2.0, "ppm": 2.0},
  "datum_rename": {"BASE-1": "BM-A"},
  "overrides": [
    {"scope": "session", "class": "rtk", "match": "2026-09-25_BASE-1",
     "params": {"rms_scale": 1.5}, "reason": "afternoon canopy"}
  ]
}
```

Read [`docs/WEIGHTING.md`](docs/WEIGHTING.md) before tuning — it's a
genuinely educational guide (FS-exam useful), not just a reference.

## Outputs for build #3

- `report.md` — the justification narrative (headline artifact).
- `adjusted.sadj.json` — machine-readable contract: adjusted coordinates
  + uncertainties + validity verdict + provenance (`format:
  survey-adjust-workflow/adjusted` v1).
- `job.adjusted.sfield.json` — job revision with RTK means written back.

Full contracts: [`docs/INTEROP.md`](docs/INTEROP.md).

## Python API

```python
from adjustflow import stochastic, rtk, levelnet, traverse, jobio

cfg = stochastic.load_weights("weights.json")
project = jobio.read_job_file("job.sfield.json")
res = rtk.adjust_rtk_points(project, cfg)
for p in res.points:
    print(p.name, round(p.easting, 3), f"±{p.sigma_e*1000:.1f}mm")
```

## Layout

```
src/adjustflow/
  stochastic.py   user-dictated weight models + validation
  rtk.py          repeated-occupation weighted means
  levelnet.py     level-net workflow (drives survey-adjust)
  traverse.py     parametric traverse LS (angles + distances)
  snooping.py     standardized residuals + iterative data snooping
  report.py       justification narrative generator
  jobio.py        .sfield.json in / .sadj.json + revision out
  cli.py          adjustflow run / weights-template
docs/
  WEIGHTING.md    how to choose weights (educational)
  INTEROP.md      handoff contracts for builds #3/#5
examples/         weights, traverse, and level-obs examples
```

## Design notes

- Engine-only: no UI imports anywhere; the desktop app (build #5) will
  call the Python API.
- survey-adjust's engine is reused for level nets; the traverse solver is
  new (parametric angles/distances didn't exist there) but uses its
  matrix toolkit and chi-square test.
- Traverse is planimetric (E, N); heights come from the level net.
- Angle values accept any degree convention; residuals wrap to ±180°.
- Minimally redundant traverse nets can go singular when snooping
  removes an observation — the engine stops loudly; the fix is more
  field observations, not settings.

## License

MIT — Charles Rieck.
