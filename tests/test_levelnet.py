"""Tests for the level-net workflow (drives the survey-adjust engine)."""
import pytest

from adjustflow import levelnet, stochastic


def _level_obs_file(tmp_path, rows):
    p = tmp_path / "levels.json"
    import json
    p.write_text(json.dumps(rows))
    return str(p)


def test_level_net_recovers_truth(tmp_path, cfg):
    rows = [
        {"from_station": "BM", "to_station": "TP1",
         "delta_h_m": 1.001, "distance_km": 0.5, "id": "L1"},
        {"from_station": "TP1", "to_station": "TP2",
         "delta_h_m": 2.002, "distance_km": 0.5, "id": "L2"},
        {"from_station": "BM", "to_station": "TP2",
         "delta_h_m": 3.000, "distance_km": 0.8, "id": "L3"},
    ]
    obs = levelnet.load_level_observations(_level_obs_file(tmp_path, rows))
    # minimal job: base BM elev 100 only (no rover points needed here)
    from field.models import BaseStation, Project
    project = Project(name="lvl", base_stations=[
        BaseStation(id="BM", elevation=100.0)], sessions=[])
    res = levelnet.adjust_level_network(project, cfg, level_obs=obs)
    assert res.dof == 1
    by_station = {s["station"]: s for s in res.points}
    assert by_station["TP1"]["elevation"] == pytest.approx(101.0, abs=0.01)
    assert by_station["TP2"]["elevation"] == pytest.approx(103.0, abs=0.01)
    assert res.global_test["passed"]
    assert res.rejected == []
    assert res.snoop.converged_clean


def test_level_net_weights_come_from_user_config(tmp_path, cfg):
    # Loosen the levels spec 10x: the a-priori sigmas must be 30 mm, and
    # the a-posteriori std_dev must follow the data scatter (~2 mm here),
    # not the weight scale -- the scale cancels in a-posteriori propagation.
    cfg["levels"]["mm_per_sqrt_km"] = 30.0
    rows = [
        {"from_station": "BM", "to_station": "TP1",
         "delta_h_m": 1.000, "distance_km": 1.0, "id": "L1"},
        {"from_station": "BM", "to_station": "TP1",
         "delta_h_m": 1.004, "distance_km": 1.0, "id": "L2"},
    ]
    obs = levelnet.load_level_observations(_level_obs_file(tmp_path, rows))
    from field.models import BaseStation, Project
    project = Project(name="lvl", base_stations=[
        BaseStation(id="BM", elevation=100.0)], sessions=[])
    res = levelnet.adjust_level_network(project, cfg, level_obs=obs)
    assert res.observations[0].sigma == pytest.approx(0.030)
    tp1 = next(s for s in res.points if s["station"] == "TP1")
    assert tp1["std_dev"] == pytest.approx(0.002, rel=0.05)


def test_blunder_rejected_by_snooping(tmp_path, cfg):
    rows = [
        {"from_station": "BM", "to_station": "TP1",
         "delta_h_m": 1.000, "distance_km": 0.5, "id": "L1"},
        {"from_station": "TP1", "to_station": "TP2",
         "delta_h_m": 2.000, "distance_km": 0.5, "id": "L2"},
        {"from_station": "BM", "to_station": "TP2",
         "delta_h_m": 3.500, "distance_km": 0.8, "id": "L3"},  # 0.5 m blunder
    ]
    obs = levelnet.load_level_observations(_level_obs_file(tmp_path, rows))
    from field.models import BaseStation, Project
    project = Project(name="lvl", base_stations=[
        BaseStation(id="BM", elevation=100.0)], sessions=[])
    res = levelnet.adjust_level_network(project, cfg, level_obs=obs)
    assert len(res.rejected) == 1
    assert "L3" in res.rejected[0].label
    assert res.rejected[0].standardized_residual > 3.29
    assert "data snooping" in res.rejected[0].reason
    assert res.snoop.converged_clean
    by_station = {s["station"]: s for s in res.points}
    assert by_station["TP2"]["elevation"] == pytest.approx(103.0, abs=0.01)


def test_rtk_diffs_from_job_drive_net(project, cfg):
    res = levelnet.adjust_level_network(project, cfg)
    # 4 FIX point *records* (101 x3 occupations, 102 x1) -> 4 diffs;
    # 2 free stations -> dof = 2. Repeated occupations are legitimate
    # parallel observations, not duplicates.
    assert res.dof == 2
    assert len(res.observations) == 4  # FLOAT point 103 excluded
    assert any("103" in w for w in res.warnings)
    by_station = {s["station"]: s for s in res.points}
    assert by_station["101"]["elevation"] == pytest.approx(105.0, abs=0.05)


def test_no_datum_fails_loud(cfg):
    from field.models import Project
    project = Project(name="nodatum", sessions=[])  # no base stations
    with pytest.raises(ValueError, match="no held datum"):
        levelnet.adjust_level_network(project, cfg)


def test_datum_rename_and_hold(project, cfg):
    cfg["datum_rename"] = {"BASE-1": "BM-A"}
    res = levelnet.adjust_level_network(project, cfg)
    assert "BM-A" in res.fixed
    cfg["datum_hold"] = ["NOPE"]
    with pytest.raises(ValueError, match="no held datum"):
        levelnet.adjust_level_network(project, cfg)
