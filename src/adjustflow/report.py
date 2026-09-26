"""Justification report: a written narrative backing the adjustment.

Every run emits Markdown (PDF-ready) that justifies the least-squares
adjustment in plain professional language, with every claim tied to a
number: datum held and why, the stochastic model with the user's chosen
weights and the rationale per observation class, redundancy, the
chi-square global test with interpretation, residual and blunder analysis,
adjusted-coordinate uncertainties, and a validity conclusion against
stated professional criteria.

It is meant to read like the adjustment memo that backs a plat -- no
hand-waving, no uncited numbers.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional


def _mm(x: Optional[float]) -> str:
    if x is None or x != x:  # None or NaN
        return "--"
    return f"{x * 1000.0:.1f} mm"


def _m(x: Optional[float], nd: int = 4) -> str:
    if x is None or x != x:
        return "--"
    return f"{x:.{nd}f} m"


@dataclass
class ReportData:
    project_name: str
    crs: str
    job_path: str
    job_sha256: str
    weights_path: Optional[str]
    weights_sha256: Optional[str]
    weights_cfg: Dict[str, Any]
    package_versions: Dict[str, str]
    paths_run: List[str]
    # Path results, each optional:
    rtk: Any = None          # rtk.RTKResult
    levelnet: Any = None     # levelnet.LevelNetResult
    traverse: Any = None     # traverse.TraverseResult
    validity: Dict[str, Any] = dc_field(default_factory=dict)
    notes: List[str] = dc_field(default_factory=list)


def _stochastic_narrative(cfg: Dict[str, Any]) -> str:
    r = cfg.get("rtk", {})
    l = cfg.get("levels", {})
    a = cfg.get("angles", {})
    d = cfg.get("distances", {})
    ov = cfg.get("overrides", [])
    lines = [
        "## Stochastic model",
        "",
        "Weights follow the single rule *weight = 1 / sigma^2* applied to",
        "a-priori standard deviations. The sigmas below are the surveyor's",
        "choice, loaded from the weights file -- the engine applies them, it",
        "does not invent them.",
        "",
        "### RTK positions and RTK-derived elevation differences",
        "",
        f"* Model: sigma = {r.get('rms_scale')} x receiver-reported RMS per "
        f"component (E, N, U), floored at {_mm(r.get('min_sigma_m'))}.",
        f"* Rationale: the receiver's real-time RMS reflects the actual "
        f"satellite geometry, baseline length, and multipath of each "
        f"occupation; scaling it by {r.get('rms_scale')} keeps the weighting "
        f"proportional to observed quality instead of assigning one blanket "
        f"value. The {_mm(r.get('min_sigma_m'))} floor prevents a single "
        f"over-optimistic RMS from dominating the solution.",
        f"* Missing RMS: where the export carried no RMS the model substitutes "
        f"{_mm(r.get('default_sigma_m'))} and flags the occupation, so no "
        f"observation is ever weighted on an invented precision silently.",
        f"* Non-fixed solutions: policy is `{r.get('float_policy')}` "
        f"(float/single solutions are "
        f"{'excluded from the adjustment' if r.get('float_policy') == 'exclude' else 'kept with sigma multiplied by ' + str(r.get('downweight_factor')) if r.get('float_policy') == 'downweight' else 'kept at face value'}).",
        "",
        "### Differential levels",
        "",
        f"* Model: sigma = {l.get('mm_per_sqrt_km')} mm x sqrt(distance_km), "
        f"floored at {_mm(l.get('min_sigma_m'))}.",
        "* Rationale: random error in differential leveling accumulates with "
        "the square root of the number of setups, hence of distance -- the "
        "classic manufacturer's specification form. The kilometre rate is "
        "taken from the instrument's spec sheet (or the surveyor's field "
        "experience with it), not from the data being adjusted.",
        "",
        "### Total-station angles",
        "",
        f"* Model: sigma = {a.get('arcseconds')} arcseconds (each angle).",
        "* Rationale: the instrument's DIN 18723 / manufacturer angular "
        "accuracy, applied uniformly. Angles observed in more sets than the "
        "spec assumes would justify a smaller value; single-face single-set "
        "work would justify a larger one.",
        "",
        "### EDM distances",
        "",
        f"* Model: sigma = {d.get('mm')} mm + {d.get('ppm')} ppm of the "
        "measured distance.",
        "* Rationale: the standard EDM error model -- a constant centering / "
        "instrument term plus a distance-proportional scale term. Short lines "
        "are dominated by the millimetre term, long lines by the ppm term, "
        "which is exactly how field accuracy behaves.",
    ]
    if ov:
        lines += ["", "### User overrides applied", ""]
        for o in ov:
            scope = o.get("scope", "?")
            match = f" on {o['match']!r}" if o.get("match") else ""
            lines.append(
                f"* {scope}{match} ({o.get('class')}): "
                f"`{o.get('params')}`"
                + (f" -- {o['reason']}" if o.get("reason") else ""))
        lines.append(
            "Overrides take precedence observation > session > class > default, "
            "so a flagged occupation never inherits a class-wide value the "
            "surveyor meant to replace.")
    else:
        lines += ["", "No per-observation overrides were applied; class "
                       "defaults governed every weight."]
    return "\n".join(lines)


def _datum_section(data: ReportData) -> str:
    lines = ["## Datum", ""]
    bases = []
    if data.rtk is not None:
        bases = data.rtk.datum_bases
    held = []
    if data.levelnet is not None:
        held = sorted(data.levelnet.fixed.items())
    if held:
        lines.append("Held fixed (level net):")
        lines.append("")
        for name, elev in held:
            lines.append(f"* **{name}** -- elevation {_m(elev)} held")
        lines.append("")
    if bases:
        lines.append("Base stations defining the RTK datum:")
        lines.append("")
        for b in bases:
            lines.append(
                f"* **{b['id']}** -- E {b['easting']}, N {b['northing']}, "
                f"elev {_m(b['elevation'])} ({b['n_points']} rover points)")
        lines.append("")
    lines += [
        "Why this datum: the Emlid receiver fixes each rover position against "
        "its base station in real time, so every RTK coordinate already "
        "embodies the base-station datum -- the antenna phase-centre "
        "coordinates logged at setup. Holding the base coordinates fixed "
        "propagates that datum through the adjustment instead of "
        "re-estimating it, which would add unknowns without adding "
        "information. If the base was itself positioned by NTRIP/PPP rather "
        "than a known monument, the absolute accuracy of this whole job is "
        "limited by that base position, and the sigmas above describe "
        "*relative* precision only.",
    ]
    rename = (data.weights_cfg.get("datum_rename") or {})
    if rename:
        lines.append("")
        lines.append("Control renaming applied: " + ", ".join(
            f"{k!r} held as {v!r}" for k, v in rename.items()) + ".")
    return "\n".join(lines)


def _observations_section(data: ReportData) -> str:
    lines = ["## Observations", ""]
    if data.rtk is not None:
        n_occ = sum(p.n_occupations for p in data.rtk.points)
        n_exc = data.rtk.n_excluded_total
        lines.append(
            f"* RTK: {len(data.rtk.points)} distinct points from {n_occ} "
            f"occupations ({n_exc} excluded by solution policy).")
    if data.levelnet is not None:
        ln = data.levelnet
        n_rtk = sum(1 for o in ln.observations if o.source == "rtk")
        n_lvl = sum(1 for o in ln.observations if o.source == "levels")
        lines.append(
            f"* Level net: {len(ln.observations)} elevation differences kept "
            f"({n_rtk} RTK-derived, {n_lvl} differential-level), "
            f"{len(ln.rejected)} rejected by data snooping.")
    if data.traverse is not None:
        t = data.traverse
        n_d = sum(1 for o in t.observations if o.kind == "distance")
        n_a = sum(1 for o in t.observations if o.kind == "angle")
        lines.append(
            f"* Traverse: {len(t.observations)} observations kept "
            f"({n_d} distances, {n_a} angles), {len(t.rejected)} rejected "
            f"by data snooping, converged in {t.iterations} iterations.")
    return "\n".join(lines)


def _global_test_text(name: str, gt: Dict[str, Any]) -> List[str]:
    lines = [f"### {name}", ""]
    if gt.get("statistic") != gt.get("statistic"):  # NaN
        return lines + ["Global test not run (no redundancy).", ""]
    lines += [
        f"* Test statistic T = {gt['statistic']:.2f}; critical value "
        f"(alpha = {gt['alpha']}) = {gt['critical']:.2f}.",
        f"* Result: **{'PASS' if gt['passed'] else 'FAIL'}**.",
        "",
    ]
    if gt["passed"]:
        lines.append(
            "Interpretation: the a-posteriori variance factor is consistent "
            "with the a-priori stochastic model at the "
            f"{100 * (1 - gt['alpha']):.0f}% confidence level. The weights the "
            "surveyor chose describe the data's actual scatter -- the model "
            "is validated, not just assumed.")
    else:
        lines.append(
            "Interpretation: the variance factor differs significantly from "
            "the stochastic model. Either the a-priori sigmas were optimistic "
            "(tighten them -- see docs/WEIGHTING.md), an undetected blunder "
            "remains, or the functional model is wrong (e.g. unmodeled "
            "systematic error). Do not treat the adjusted coordinates as "
            "final until this is resolved.")
    return lines + [""]


def _results_section(data: ReportData) -> str:
    lines = ["## Adjustment results", ""]
    if data.levelnet is not None:
        ln = data.levelnet
        lines.append(f"### Level net -- {ln.dof} degrees of freedom")
        lines.append("")
        lines.append(
            f"* A-posteriori sigma0 = {_mm(ln.sigma0)} "
            f"({len(ln.observations)} observations, "
            f"{len(ln.observations) - ln.dof} unknowns).")
        lines += _global_test_text("Global chi-square test (level net)",
                                   ln.global_test)
    if data.traverse is not None:
        t = data.traverse
        lines.append(f"### Traverse -- {t.dof} degrees of freedom")
        lines.append("")
        lines.append(
            f"* A-posteriori sigma0 = {_mm(t.sigma0)} "
            f"({len(t.observations)} observations, "
            f"{len(t.observations) - t.dof} unknowns).")
        lines += _global_test_text("Global chi-square test (traverse)",
                                   t.global_test)
    if data.rtk is not None:
        lines.append("### RTK weighted means")
        lines.append("")
        lines.append(
            "Each repeated point is the variance-weighted mean of its "
            "occupations; the quoted sigma is the propagated "
            "1/sqrt(sum(w)) per component -- the precision of the *mean*, "
            "tighter than any single occupation, exactly as redundancy "
            "should behave.")
        lines.append("")
    return "\n".join(lines)


def _blunder_section(data: ReportData) -> str:
    lines = ["## Residual and blunder analysis", "",
             "Data snooping (Baarda): standardized residuals w = |v|/sqrt(qvv) "
             "against the a-priori model; the worst offender above 3.29 "
             "(~0.1% false-alarm rate per observation) is removed, the net "
             "re-adjusted, and the cycle repeats. Rejections are listed -- "
             "nothing is dropped silently.", ""]
    any_rej = False
    for tag, res in (("Level net", data.levelnet), ("Traverse", data.traverse)):
        if res is None:
            continue
        if res.rejected:
            any_rej = True
            lines.append(f"### {tag} -- {len(res.rejected)} rejected")
            lines.append("")
            for r in res.rejected:
                lines.append(
                    f"* **{r.label}** -- standardized residual "
                    f"{r.standardized_residual:.2f} (residual "
                    f"{r.residual * 1000:.1f} mm against sigma "
                    f"{_mm(r.sigma)}). {r.reason}")
            lines.append("")
        else:
            lines.append(
                f"### {tag} -- clean: no observation exceeded the snooping "
                f"threshold; largest standardized residual "
                f"{res.snoop.max_standardized_residual:.2f}.")
            lines.append("")
    if data.rtk is not None:
        suspect = [(p.name, o) for p in data.rtk.points
                   for o in p.occupations if o.suspect]
        if suspect:
            any_rej = True
            lines.append("### RTK suspect occupations")
            lines.append("")
            for name, o in suspect:
                lines.append(f"* Point **{name}**, session {o.session_id}: "
                             f"{o.suspect_reason}.")
            lines.append("")
    if not any_rej:
        lines.append("No blunders detected on any path.")
        lines.append("")
    return "\n".join(lines)


def _coords_section(data: ReportData) -> str:
    lines = ["## Adjusted coordinates and uncertainties", ""]
    if data.rtk is not None:
        lines += ["### RTK points (weighted means)", "",
                  "| Point | Easting | Northing | Elev | sE | sN | sU | Occ |",
                  "|---|---|---|---|---|---|---|---|"]
        for p in data.rtk.points:
            lines.append(
                f"| {p.name} | {p.easting:.3f} | {p.northing:.3f} | "
                f"{p.elevation:.3f} | {_mm(p.sigma_e)} | {_mm(p.sigma_n)} | "
                f"{_mm(p.sigma_u)} | {p.n_occupations} |")
        lines.append("")
    if data.levelnet is not None:
        lines += ["### Level net stations", "",
                  "| Station | Elevation | sElev |",
                  "|---|---|---|"]
        for s in data.levelnet.points:
            lines.append(f"| {s['station']} | {s['elevation']:.4f} | "
                         f"{_mm(s['std_dev'])} |")
        lines.append("")
    if data.traverse is not None:
        lines += ["### Traverse stations", "",
                  "| Station | Easting | Northing | sE | sN | held |",
                  "|---|---|---|---|---|---|"]
        for s in data.traverse.stations:
            lines.append(
                f"| {s['id']} | {s['easting']:.4f} | {s['northing']:.4f} | "
                f"{_mm(s['sigma_e'])} | {_mm(s['sigma_n'])} | "
                f"{'yes' if s['fixed'] else 'no'} |")
        lines.append("")
    lines.append("Uncertainties are one-sigma, propagated from the a-posteriori "
                 "variance factor through the cofactor matrix -- they describe "
                 "the adjusted position, not the raw field precision.")
    return "\n".join(lines)


def _validity_section(data: ReportData) -> str:
    v = data.validity
    checks = v.get("checks", [])
    lines = ["## Validity conclusion", "",
             "Judged against four professional criteria:", ""]
    for name, passed, detail in checks:
        lines.append(f"* **{'PASS' if passed else 'FAIL'}** -- {name}: {detail}")
    lines.append("")
    if v.get("valid"):
        lines.append(
            "**Conclusion: this is a valid survey adjustment.** The datum is "
            "defined and held, redundancy exists on every path, the "
            "surveyor's stochastic model passed the chi-square global test, "
            "and data snooping found no unresolved blunders. The adjusted "
            "coordinates and their uncertainties above are fit to support "
            "plat, plan, and map deliverables.")
    else:
        lines.append(
            "**Conclusion: this adjustment is NOT valid as it stands.** "
            "Resolve every FAIL above -- re-examine the datum, add "
            "redundancy, revisit the weight choices (docs/WEIGHTING.md), or "
            "hunt the remaining blunder -- and re-run before the results "
            "back any deliverable.")
    return "\n".join(lines)


def build_report(data: ReportData) -> str:
    """Render the full justification narrative as Markdown."""
    today = datetime.date.today().isoformat()
    head = [
        "# Least-Squares Adjustment Report",
        "",
        f"Project: **{data.project_name}** -- {data.crs}",
        f"Date: {today}",
        "",
        "## Provenance",
        "",
        f"* Job file: `{data.job_path}` (SHA-256 `{data.job_sha256[:16]}...`)",
    ]
    if data.weights_path:
        head.append(
            f"* Weights: `{data.weights_path}` "
            f"(SHA-256 `{data.weights_sha256[:16]}...`) -- "
            "the surveyor's stochastic-model choices, applied verbatim")
    else:
        head.append("* Weights: built-in defaults (no weights file supplied) "
                    "-- tune per docs/WEIGHTING.md before production use")
    head.append("* Engines: " + ", ".join(
        f"{k} {vv}" for k, vv in data.package_versions.items()))
    head.append("")
    if data.notes:
        head += ["## Run notes", ""] + [f"* {n}" for n in data.notes] + [""]
    verdict = ("**VALID**" if data.validity.get("valid") else "**NOT VALID**")
    head += [f"## Verdict: {verdict}", "",
             ("All four validity criteria pass -- see the conclusion for the "
              "reasoning." if data.validity.get("valid") else
              "One or more validity criteria fail -- see the conclusion. "
              "Do not use these results for deliverables yet."), ""]
    parts = ["\n".join(head),
             _datum_section(data),
             _observations_section(data),
             _stochastic_narrative(data.weights_cfg),
             _results_section(data),
             _blunder_section(data),
             _coords_section(data),
             _validity_section(data)]
    return "\n\n".join(parts) + "\n"
