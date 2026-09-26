"""adjustflow: run the least-squares adjustment workflow.

    adjustflow run --job job.sfield.json --weights weights.json \\
        --report report.md [--path all|rtk|levels|traverse]
        [--traverse traverse.json] [--level-obs levels.json]
        [--out adjusted.sadj.json] [--adjusted-job revised.sfield.json]
        [--alpha 0.05] [--snoop-threshold 3.29]

    adjustflow weights-template   # print an editable weights JSON template
"""

from __future__ import annotations

import argparse
import json
import sys

from . import jobio, levelnet, report as report_mod, rtk, \
    stochastic, traverse


def _validity_checks(data: report_mod.ReportData) -> dict:
    checks = []

    # 1. Datum defined.
    datum_ok, datum_detail = False, "no datum held"
    if data.levelnet is not None and data.levelnet.fixed:
        datum_ok = True
        datum_detail = (f"{len(data.levelnet.fixed)} station(s) held: "
                        + ", ".join(sorted(data.levelnet.fixed)))
    elif data.rtk is not None and data.rtk.datum_bases:
        datum_ok = True
        datum_detail = ("RTK datum inherited from base station(s): "
                        + ", ".join(b["id"] for b in data.rtk.datum_bases))
    checks.append(("Datum defined", datum_ok, datum_detail))

    # 2. Redundancy on every path.
    redund_ok, redund_detail = True, []
    if data.rtk is not None:
        n_occ = sum(p.n_occupations for p in data.rtk.points)
        n_pt = len(data.rtk.points)
        if n_pt == 0:
            redund_detail.append("RTK: no points in job (path vacuous)")
        else:
            ok = n_occ > n_pt
            redund_ok &= ok
            redund_detail.append(
                f"RTK: {n_occ} occupations / {n_pt} points "
                f"({'redundant' if ok else 'no redundancy -- single '
                  'occupations cannot be checked'})")
    for tag, res in (("level net", data.levelnet), ("traverse", data.traverse)):
        if res is not None:
            ok = res.dof > 0
            redund_ok &= ok
            redund_detail.append(f"{tag}: dof={res.dof}")
    checks.append(("Sufficient redundancy", redund_ok,
                   "; ".join(redund_detail) or "no paths run"))

    # 3. Stochastic model validated (chi-square).
    stoch_ok, stoch_detail = True, []
    for tag, res in (("level net", data.levelnet), ("traverse", data.traverse)):
        if res is not None:
            gt = res.global_test
            if gt["statistic"] != gt["statistic"]:  # NaN -> skipped
                stoch_detail.append(f"{tag}: test skipped (no dof)")
            else:
                stoch_ok &= bool(gt["passed"])
                stoch_detail.append(
                    f"{tag}: {'PASS' if gt['passed'] else 'FAIL'} "
                    f"(T={gt['statistic']:.2f} vs {gt['critical']:.2f})")
    if data.rtk is not None and data.levelnet is None and data.traverse is None:
        stoch_detail.append("RTK means: no global test applicable "
                            "(weighted means, not a network)")
    checks.append(("Stochastic model validated", stoch_ok,
                   "; ".join(stoch_detail) or "no testable path"))

    # 4. No unresolved blunders.
    blund_ok, blund_detail = True, []
    for tag, res in (("level net", data.levelnet), ("traverse", data.traverse)):
        if res is not None:
            ok = res.snoop.converged_clean
            blund_ok &= ok
            blund_detail.append(
                f"{tag}: {'clean' if ok else 'NOT clean'} "
                f"({len(res.rejected)} rejected)")
    if data.rtk is not None:
        n_sus = sum(1 for p in data.rtk.points for o in p.occupations
                    if o.suspect)
        if n_sus:
            blund_detail.append(f"RTK: {n_sus} suspect occupation(s) flagged")
    checks.append(("No unresolved blunders", blund_ok,
                   "; ".join(blund_detail) or "no snooping path"))

    valid = all(c[1] for c in checks)
    return {"valid": valid, "checks": checks}


def _sadj_points(data: report_mod.ReportData):
    """Adjusted points, merged by name across paths.

    The rtk, level-net, and traverse paths can all estimate the same
    station (e.g. a rover point appears in the RTK weighted mean *and*
    the level net). Emitting one entry per (path, station) produced
    duplicate names, and downstream readers (survey-drafting) take
    last-wins -- so the planimetric coordinates were lost whenever the
    level-net entry came second. Merge instead: per component, the
    first non-null value wins, in path order rtk -> traverse ->
    level-net; sources are combined.
    """
    raw = []
    if data.rtk is not None:
        for p in data.rtk.points:
            raw.append({
                "name": p.name, "easting": p.easting, "northing": p.northing,
                "elevation": p.elevation, "sigma_e": p.sigma_e,
                "sigma_n": p.sigma_n, "sigma_u": p.sigma_u,
                "source": "rtk-weighted-mean",
                "n_occupations": p.n_occupations,
                "sessions": p.sessions})
    if data.levelnet is not None:
        for s in data.levelnet.points:
            raw.append({
                "name": s["station"], "easting": None, "northing": None,
                "elevation": s["elevation"], "sigma_e": None,
                "sigma_n": None, "sigma_u": s["std_dev"],
                "source": "level-net"})
    if data.traverse is not None:
        for s in data.traverse.stations:
            raw.append({
                "name": s["id"], "easting": s["easting"],
                "northing": s["northing"], "elevation": None,
                "sigma_e": s["sigma_e"], "sigma_n": s["sigma_n"],
                "sigma_u": None, "source": "traverse",
                "held": s["fixed"]})
    merged: dict = {}
    order: list = []
    for pt in raw:
        name = pt["name"]
        if name not in merged:
            merged[name] = dict(pt)
            merged[name]["source"] = [pt["source"]]
            order.append(name)
            continue
        cur = merged[name]
        for key in ("easting", "northing", "elevation",
                    "sigma_e", "sigma_n", "sigma_u"):
            if cur.get(key) is None and pt.get(key) is not None:
                cur[key] = pt[key]
        if pt["source"] not in cur["source"]:
            cur["source"].append(pt["source"])
        for key in ("n_occupations", "sessions", "held"):
            if key not in cur and key in pt:
                cur[key] = pt[key]
            elif key == "held" and pt.get("held"):
                cur[key] = True
    points = []
    for name in order:
        pt = merged[name]
        pt["source"] = "+".join(pt["source"])
        points.append(pt)
    return points


def cmd_run(args) -> int:
    paths = ([args.path] if args.path != "all"
             else ["rtk", "levels", "traverse"])
    notes = []
    if "traverse" in paths and not args.traverse:
        if args.path == "traverse":
            print("error: --path traverse needs --traverse traverse.json",
                  file=sys.stderr)
            return 2
        # --path all without a traverse file: run the job-data paths.
        paths.remove("traverse")
        notes.append("traverse path skipped: no --traverse file supplied")

    cfg = (stochastic.load_weights(args.weights) if args.weights
           else stochastic.default_weights())
    project = jobio.read_job_file(args.job)
    level_obs = (levelnet.load_level_observations(args.level_obs)
                 if args.level_obs else None)

    rtk_res = level_res = trav_res = None
    if "rtk" in paths:
        rtk_res = rtk.adjust_rtk_points(project, cfg)
        notes.extend(rtk_res.warnings[:10])
    if "levels" in paths:
        level_res = levelnet.adjust_level_network(
            project, cfg, level_obs=level_obs, alpha=args.alpha,
            snoop_threshold=args.snoop_threshold)
        notes.extend(level_res.warnings[:10])
    if "traverse" in paths:
        trav_res = traverse.adjust_traverse(
            traverse.load_traverse(args.traverse), cfg, alpha=args.alpha,
            snoop_threshold=args.snoop_threshold)
        notes.extend(trav_res.warnings[:10])

    data = report_mod.ReportData(
        project_name=project.name, crs=project.crs,
        job_path=args.job, job_sha256=jobio.sha256_file(args.job),
        weights_path=args.weights,
        weights_sha256=jobio.sha256_file(args.weights) if args.weights else None,
        weights_cfg=cfg, package_versions=jobio.package_versions(),
        paths_run=paths, rtk=rtk_res, levelnet=level_res, traverse=trav_res,
        notes=notes)
    data.validity = _validity_checks(data)

    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write(report_mod.build_report(data))
    print(f"report written: {args.report}")

    if args.out:
        sadj = jobio.write_sadj(
            args.out, job_path=args.job, weights_path=args.weights,
            weights_cfg=cfg, paths_run=paths,
            points=_sadj_points(data), validity=data.validity,
            report_path=args.report, notes=notes)
        print(f"adjusted-products contract written: {args.out} "
              f"({len(sadj['points'])} points)")

    if args.adjusted_job and rtk_res is not None:
        by_name = {p.name: {"easting": p.easting, "northing": p.northing,
                            "elevation": p.elevation, "sigma_e": p.sigma_e,
                            "sigma_n": p.sigma_n, "sigma_u": p.sigma_u}
                   for p in rtk_res.points}
        jobio.write_adjusted_job_revision(project, by_name, args.adjusted_job)
        print(f"adjusted job revision written: {args.adjusted_job}")

    verdict = "VALID" if data.validity["valid"] else "NOT VALID"
    print(f"verdict: {verdict}")
    return 0 if data.validity["valid"] else 1


def cmd_weights_template(_args) -> int:
    print(json.dumps(stochastic.default_weights(), indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="adjustflow",
        description="Least-squares adjustment workflow for the survey suite")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the adjustment workflow")
    p_run.add_argument("--job", required=True, help=".sfield.json job file")
    p_run.add_argument("--weights", default=None,
                       help="weights JSON (user stochastic model)")
    p_run.add_argument("--report", required=True, help="output Markdown report")
    p_run.add_argument("--path", default="all",
                       choices=["all", "rtk", "levels", "traverse"])
    p_run.add_argument("--traverse", default=None,
                       help="traverse interchange JSON (needs --path traverse)")
    p_run.add_argument("--level-obs", default=None,
                       help="differential-level observations JSON")
    p_run.add_argument("--out", default=None,
                       help="adjusted-products .sadj.json for build #3")
    p_run.add_argument("--adjusted-job", default=None,
                       help="write adjusted .sfield.json revision (RTK path)")
    p_run.add_argument("--alpha", type=float, default=0.05,
                       help="chi-square significance level")
    p_run.add_argument("--snoop-threshold", type=float, default=3.29,
                       help="data-snooping |w| threshold")
    p_run.set_defaults(func=cmd_run)

    p_w = sub.add_parser("weights-template",
                         help="print an editable weights JSON template")
    p_w.set_defaults(func=cmd_weights_template)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
