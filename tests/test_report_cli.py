"""Tests for the justification report and the CLI end-to-end run."""
import json

import pytest

from adjustflow import jobio, report as report_mod, rtk, stochastic
from adjustflow.cli import _validity_checks, main as cli_main


def _report_data(project, cfg, tmp_path):
    job = str(tmp_path / "job.sfield.json")
    from field import jobfile
    jobfile.write_job(project, job)
    rtk_res = rtk.adjust_rtk_points(project, cfg)
    data = report_mod.ReportData(
        project_name=project.name, crs=project.crs,
        job_path=job, job_sha256=jobio.sha256_file(job),
        weights_path=None, weights_sha256=None, weights_cfg=cfg,
        package_versions=jobio.package_versions(),
        paths_run=["rtk"], rtk=rtk_res, notes=[])
    data.validity = _validity_checks(data)
    return data


def test_report_reads_like_a_plat_memo(project, cfg, tmp_path):
    data = _report_data(project, cfg, tmp_path)
    md = report_mod.build_report(data)
    for section in ["## Datum", "## Stochastic model", "## Observations",
                    "## Adjustment results", "## Residual and blunder analysis",
                    "## Adjusted coordinates and uncertainties",
                    "## Validity conclusion", "## Provenance",
                    "## Verdict"]:
        assert section in md, section
    # numbers cited, not hand-waving
    assert "BASE-1" in md
    assert "101" in md
    assert "weight = 1 / sigma^2" in md
    # stochastic rationale cites the user's actual choices
    assert "1.0 x receiver-reported RMS" in md
    assert "5 mm + 2 ppm" not in md  # distances not run; angles section exists
    assert "5.0 arcseconds" in md


def test_report_marks_invalid_when_test_fails(project, cfg, tmp_path):
    data = _report_data(project, cfg, tmp_path)
    data.validity = {"valid": False,
                     "checks": [("Datum defined", True, "x"),
                                ("Something", False, "y")]}
    md = report_mod.build_report(data)
    assert "NOT VALID" in md
    assert "Do not use these results for deliverables" in md


def test_cli_run_end_to_end(project, cfg, tmp_path):
    from field import jobfile
    job = tmp_path / "job.sfield.json"
    jobfile.write_job(project, str(job))
    weights = tmp_path / "weights.json"
    weights.write_text(json.dumps(cfg))
    rep = tmp_path / "report.md"
    out = tmp_path / "adj.sadj.json"
    rev = tmp_path / "rev.sfield.json"
    rc = cli_main(["run", "--job", str(job), "--weights", str(weights),
                   "--report", str(rep), "--out", str(out),
                   "--adjusted-job", str(rev)])
    assert rc == 0  # RTK-only run is valid: datum held, redundancy, no blunders
    text = rep.read_text()
    assert "## Verdict: **VALID**" in text
    sadj = json.loads(out.read_text())
    assert sadj["format"] == "survey-adjust-workflow/adjusted"
    assert sadj["schema_version"] == 1
    assert sadj["validity"]["valid"] is True
    assert any(p["name"] == "101" for p in sadj["points"])
    # adjusted job revision reads back and carries the means
    proj2 = jobfile.read_job(str(rev))
    pts101 = [p for s in proj2.sessions for p in s.points
              if p.name == "101"]
    assert len(pts101) == 3
    assert pts101[0].easting == pytest.approx(
        (500100.00 + 500100.02 + 500099.99) / 3)
    assert "adjusted_by" in pts101[0].meta


def test_cli_run_with_traverse_and_levels(tmp_path, cfg):
    import math
    from field.models import BaseStation, Project
    project = Project(name="combo", base_stations=[
        BaseStation(id="BM", elevation=100.0)], sessions=[])
    from field import jobfile
    job = tmp_path / "job.sfield.json"
    jobfile.write_job(project, str(job))
    levels = tmp_path / "levels.json"
    levels.write_text(json.dumps([
        {"from_station": "BM", "to_station": "TP1", "delta_h_m": 1.0,
         "distance_km": 0.5, "id": "L1"},
        {"from_station": "TP1", "to_station": "TP2", "delta_h_m": 2.0,
         "distance_km": 0.5, "id": "L2"},
        {"from_station": "BM", "to_station": "TP2", "delta_h_m": 3.0,
         "distance_km": 0.8, "id": "L3"}]))
    trav = tmp_path / "trav.json"
    trav.write_text(json.dumps({
        "stations": [
            {"id": "A", "easting": 0.0, "northing": 0.0, "fixed": True},
            {"id": "D", "easting": 0.0, "northing": 100.0, "fixed": True},
            {"id": "B", "easting": 100.0, "northing": 0.0, "fixed": False},
            {"id": "C", "easting": 100.0, "northing": 100.0, "fixed": False}],
        "observations": [
            {"type": "distance", "from": "A", "to": "B", "value_m": 100.0},
            {"type": "distance", "from": "B", "to": "C", "value_m": 100.0},
            {"type": "distance", "from": "C", "to": "D", "value_m": 100.0},
            {"type": "angle", "at": "B", "from": "A", "to": "C",
             "value_deg": 90.0},
            {"type": "angle", "at": "C", "from": "B", "to": "D",
             "value_deg": 90.0}]}))
    rep = tmp_path / "report.md"
    rc = cli_main(["run", "--job", str(job), "--report", str(rep),
                   "--level-obs", str(levels), "--traverse", str(trav)])
    assert rc == 0
    text = rep.read_text()
    assert "## Verdict: **VALID**" in text
    assert "TP2" in text and "| B |" in text


def test_cli_weights_template(capsys):
    assert cli_main(["weights-template"]) == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["distances"] == {"mm": 2.0, "ppm": 2.0}
