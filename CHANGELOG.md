# Changelog

All notable changes to this project follow [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-26

### Added
- Three adjustment paths under one user-dictated stochastic model:
  RTK repeated-occupation weighted means, level-net workflow driving the
  survey-adjust engine, parametric traverse adjustment (angles +
  distances, iterated weighted LS on survey-adjust's matrix toolkit).
- `stochastic.py`: human-editable JSON weight configs — rtk (RMS x scale,
  floor, fallback), levels (mm/sqrt(km)), angles (arcseconds), distances
  (mm + ppm) — with class/session/observation override precedence and
  fail-loud validation.
- `snooping.py`: Baarda data snooping on standardized residuals
  (a-priori), iterative removal, every rejection listed with reason.
- `report.py`: justification narrative — datum, stochastic rationale
  citing the user's choices, redundancy, chi-square interpretation,
  blunder analysis, uncertainties, four-criterion validity verdict.
- `jobio.py`: `.sadj.json` adjusted-products contract (schema v1) for
  build #3; `.adjusted.sfield.json` job revision with RTK means written
  back (schema unchanged).
- CLI: `adjustflow run` (exit 0 VALID / 1 NOT VALID) and
  `adjustflow weights-template`.
- `docs/WEIGHTING.md`: practical guide to choosing weights (FS-exam
  useful). `docs/INTEROP.md`: exact handoff contracts for builds #3/#5.
- Traverse and level-observation interchange formats defined and frozen
  for v1 (future total-station / survey-levels importers target these).
- 50-test pytest suite; fresh-clone verified.
