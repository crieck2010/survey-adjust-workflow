# Choosing Weights: A Practical Guide to the Stochastic Model

*Read this before you tune `weights.json`. It is written to be genuinely
useful if you are studying for the FS exam -- the concepts here (weighting,
redundancy, the chi-square test, blunder detection) are core exam topics.*

## 1. Why weights exist at all

A least-squares adjustment finds the coordinates that best fit *all* your
observations at once. But not all observations deserve equal say: a 2 mm
EDM distance should pull harder than a float-solution RTK shot. Weights
encode that trust. The rule is simple and universal:

> **weight = 1 / sigma^2**, where sigma is the *a-priori* standard deviation
> you assign to the observation *before* seeing how it fits.

Two consequences worth internalizing:

- **Only the ratios matter for the coordinates.** Double every sigma and
  the adjusted positions don't move -- but the chi-square test (section 5)
  *does* notice, because it checks your sigmas against reality.
- **Weights are a statement about your instruments and field conditions,
  not about the data.** You choose them from specs and experience, then
  the adjustment tells you whether you were honest.

## 2. A-priori vs a-posteriori

- **A-priori** (your input): the sigmas in `weights.json`. Your professional
  judgment, written down.
- **A-posteriori** (the output): `sigma0`, the reference standard deviation
  computed from the actual residuals. If your model was right, sigma0 ≈ 1
  (meaning: residuals are about the size your sigmas predicted).

When sigma0 comes out near 1, your weights described reality. When it is
far from 1, either your sigmas were optimistic/pessimistic or a blunder is
hiding in the data. That comparison *is* the chi-square global test.

## 3. The four observation classes

### RTK (`rms_scale`, `min_sigma_m`, `default_sigma_m`)

Model: `sigma = rms_scale x receiver RMS`, per component, floored at
`min_sigma_m`.

The receiver's RMS already folds in satellite geometry, baseline length,
and multipath *for that occupation* -- it is the best per-shot quality
metric you have. Scaling (default 1.0) lets you be conservative: under
canopy or near structures, experienced surveyors mentally inflate RTK
uncertainty, and `rms_scale: 1.5` on that session's override writes that
judgment down instead of hiding it.

The floor (`min_sigma_m`, default 5 mm) exists because receivers are
optimistic. A reported 2 mm RMS on a 2 km baseline in gusty wind is not
2 mm -- the floor keeps one lucky fix from hijacking the weighted mean.

### Differential levels (`mm_per_sqrt_km`)

Model: `sigma = mm_per_sqrt_km x sqrt(distance_km)`.

Why square root? Random error accumulates per *setup*, and setups scale
with distance -- variances add, so the standard deviation grows with
sqrt(distance). The kilometre rate comes from the level's spec sheet
(typical: 1.5--3 mm/sqrt(km) for a digital level, higher for an
automatic/optical one). This is the number the FS exam expects you to
reach for in a "misclosure allowable" problem, in weight form.

### Angles (`arcseconds`)

One number: the instrument's angular accuracy (e.g. 5" for a 5-second
gun). Straight from the DIN 18723 / manufacturer spec. If you observed
each angle in multiple sets, the *mean* of sets is tighter than the spec
-- but unless you can defend the divisor, keep the spec value. Being
slightly pessimistic here is a professional virtue, not a flaw.

### Distances (`mm + ppm`)

Model: `sigma = mm + ppm x distance`.

The constant term covers centering, instrument noise, and prism offset
error; the ppm term covers scale (frequency/atmospheric) error. On a
50 m shot the mm term dominates; on a 2 km shot the ppm term does. That
crossover is exactly how EDM accuracy behaves in the field, which is why
this two-term model is the industry standard.

## 4. Relative vs absolute -- the subtlety that matters

For the *coordinates*, only relative weights matter. But the chi-square
test and the reported coordinate sigmas depend on the *absolute* scale.
Practical upshot:

- If the global test **fails high** (T >> critical): your sigmas were
  optimistic *as a group*, or a blunder remains. First check the blunders
  (the report lists them); if clean, loosen the class sigmas.
- If it **fails low** (T suspiciously small -- the test here is
  upper-tail only, but sigma0 << 1 is the tell): your sigmas were
  pessimistic. The coordinates are still fine, but you're understating
  your precision -- tighten toward the specs.

Never "tune sigmas until the test passes" blindly. Change a sigma only
when you can state the field reason ("afternoon canopy", "single-face
angles", "rental prism of unknown offset") -- and put that reason in the
override's `reason` field so the report cites it.

## 5. The chi-square global test, plainly

Null hypothesis: *my stochastic model is correct* (sigma0^2 = 1).
Test statistic T = dof x sigma0^2 follows a chi-square distribution with
`dof` degrees of freedom. Reject at significance `alpha` (default 5%) if
T exceeds the critical value.

- **Pass** = your weights are consistent with the observed scatter.
- **Fail** = something is off (see section 4). It does *not* tell you
  *what* -- that is the blunder hunt's job.

Note the asymmetry: the test can only *fail to reject* your model, never
prove it. A pass with dof = 1 is weak evidence; a pass with dof = 20 is
strong. Redundancy is what gives the test teeth -- which is why the
validity checklist demands dof > 0 on every path.

## 6. Blunder detection (data snooping)

After the adjustment, each observation gets a *standardized residual*:

> w = |v| / sqrt(qvv),  qvv from a-priori cofactor propagation

Under "no blunder", w behaves like a standard normal variable, so
|w| > 3.29 happens by chance about 0.1% of the time per observation.
The workflow removes the worst offender above 3.29, re-adjusts, and
repeats. This is Baarda's data snooping -- the same idea behind the
"tau test" in textbooks.

Two honest limitations to know for the exam and the field:

- **Masking**: two blunders can hide each other. Snooping removes one at
  a time and re-tests, which defeats simple masking, but a cluster of
  consistent blunders (e.g. a wrong prism offset on every shot) looks
  like truth to any statistical test. Field procedure beats statistics.
- **Minimal redundancy**: with dof near 0, removing one observation can
  collapse the network geometry (singular normal equations). The engine
  stops loudly rather than guessing -- the fix is fieldwork (more
  observations), not settings.

## 7. Worked starting values

| Situation | Sensible starting weights |
|---|---|
| Open-sky RTK, fixed, short baseline | `rms_scale: 1.0`, floor 5 mm |
| RTK near canopy/structures | session override `rms_scale: 1.5--2.0` |
| Digital level, good conditions | `mm_per_sqrt_km: 2.0--3.0` |
| 5" total station, 1 set | `arcseconds: 5.0` |
| 5" total station, 3 sets (mean) | `arcseconds: 3.0` (defensible divisor) |
| EDM, calibrated prism | `mm: 2.0, ppm: 2.0` |
| EDM, unknown rental prism | `mm: 5.0, ppm: 3.0` + note why |

Then run, read the justification report, and let the chi-square test and
the residuals argue with you. That argument -- your judgment vs the
data's scatter, on the record -- is the entire point of the report.
