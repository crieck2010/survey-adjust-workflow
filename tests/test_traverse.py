"""Tests for the parametric traverse adjustment."""
import json
import math

import pytest

from adjustflow import stochastic, traverse
from adjustflow.traverse import _azimuth, _az_partials, _wrap_pi


def square_traverse(blunder_m=0.0):
    return {
        "stations": [
            {"id": "A", "easting": 0.0, "northing": 0.0, "fixed": True},
            {"id": "D", "easting": 0.0, "northing": 100.0, "fixed": True},
            {"id": "B", "easting": 100.0, "northing": 0.0, "fixed": False},
            {"id": "C", "easting": 100.0, "northing": 100.0, "fixed": False},
        ],
        "observations": [
            {"type": "distance", "from": "A", "to": "B",
             "value_m": 100.000, "id": "D1"},
            {"type": "distance", "from": "B", "to": "C",
             "value_m": 100.000 + blunder_m, "id": "D2"},
            {"type": "distance", "from": "C", "to": "D",
             "value_m": 100.000, "id": "D3"},
            {"type": "angle", "at": "B", "from": "A", "to": "C",
             "value_deg": 90.0, "id": "A1"},
            {"type": "angle", "at": "C", "from": "B", "to": "D",
             "value_deg": 90.0, "id": "A2"},
        ],
    }


def test_square_traverse_recovers_truth(cfg):
    res = traverse.adjust_traverse(square_traverse(), cfg)
    by_id = {s["id"]: s for s in res.stations}
    assert by_id["B"]["easting"] == pytest.approx(100.0, abs=1e-6)
    assert by_id["B"]["northing"] == pytest.approx(0.0, abs=1e-6)
    assert by_id["C"]["easting"] == pytest.approx(100.0, abs=1e-6)
    assert by_id["C"]["northing"] == pytest.approx(100.0, abs=1e-6)
    assert res.dof == 1  # 5 obs - 4 unknowns
    assert res.global_test["passed"]
    assert res.snoop.converged_clean


def test_converges_from_poor_approximates(cfg):
    data = square_traverse()
    for s in data["stations"]:
        if not s["fixed"]:
            s["easting"] += 7.5
            s["northing"] -= 4.2
    res = traverse.adjust_traverse(data, cfg)
    by_id = {s["id"]: s for s in res.stations}
    assert by_id["B"]["easting"] == pytest.approx(100.0, abs=1e-4)
    assert by_id["C"]["northing"] == pytest.approx(100.0, abs=1e-4)


def redundant_traverse(angle_blunder_deg=0.0, dist_blunder_m=0.0):
    """Closed square with 8 observations / 4 unknowns (dof=4).

    Extra observations (closing distance A-D, angles at A and D) keep every
    snooping subset geometrically healthy -- a minimally redundant net goes
    singular when any observation is removed, which is a network-design
    fact, not an engine bug.
    """
    return {
        "stations": [
            {"id": "A", "easting": 0.0, "northing": 0.0, "fixed": True},
            {"id": "D", "easting": 0.0, "northing": 100.0, "fixed": True},
            {"id": "B", "easting": 100.0, "northing": 0.0, "fixed": False},
            {"id": "C", "easting": 100.0, "northing": 100.0, "fixed": False},
        ],
        "observations": [
            {"type": "distance", "from": "A", "to": "B",
             "value_m": 100.000, "id": "D1"},
            {"type": "distance", "from": "B", "to": "C",
             "value_m": 100.000 + dist_blunder_m, "id": "D2"},
            {"type": "distance", "from": "C", "to": "D",
             "value_m": 100.000, "id": "D3"},
            {"type": "distance", "from": "A", "to": "D",
             "value_m": 100.000, "id": "D4"},
            {"type": "angle", "at": "B", "from": "A", "to": "C",
             "value_deg": 90.0 + angle_blunder_deg, "id": "A1"},
            {"type": "angle", "at": "C", "from": "B", "to": "D",
             "value_deg": 90.0, "id": "A2"},
            {"type": "angle", "at": "A", "from": "D", "to": "B",
             "value_deg": 90.0, "id": "A3"},
            {"type": "angle", "at": "D", "from": "A", "to": "C",
             "value_deg": 270.0, "id": "A4"},   # signed angle is -90 deg
        ],
    }


def test_redundant_traverse_recovers_truth(cfg):
    res = traverse.adjust_traverse(redundant_traverse(), cfg)
    by_id = {s["id"]: s for s in res.stations}
    assert by_id["B"]["easting"] == pytest.approx(100.0, abs=1e-6)
    assert by_id["C"]["northing"] == pytest.approx(100.0, abs=1e-6)
    assert res.dof == 4
    assert res.global_test["passed"]
    assert res.snoop.converged_clean


def test_distance_blunder_rejected(cfg):
    res = traverse.adjust_traverse(
        redundant_traverse(dist_blunder_m=0.5), cfg)
    assert len(res.rejected) == 1
    assert "D2" in res.rejected[0].label
    assert res.snoop.converged_clean
    by_id = {s["id"]: s for s in res.stations}
    assert by_id["B"]["easting"] == pytest.approx(100.0, abs=1e-3)


def test_angle_blunder_rejected(cfg):
    # 5-degree blunder at B: the full set still converges (it is the
    # snooping subsets of a *minimally* redundant net that go singular,
    # which is why this fixture carries dof=4).
    res = traverse.adjust_traverse(
        redundant_traverse(angle_blunder_deg=5.0), cfg)
    assert len(res.rejected) == 1
    assert "A1" in res.rejected[0].label
    assert res.snoop.converged_clean


def test_azimuth_partials_match_finite_differences():
    ep, np_, eq, nq = 10.0, 20.0, 35.0, 60.0
    h = 1e-7
    analytic = _az_partials(ep, np_, eq, nq)
    f = lambda a, b, c, d: _azimuth(a, b, c, d)
    numeric = (
        (f(ep + h, np_, eq, nq) - f(ep - h, np_, eq, nq)) / (2 * h),
        (f(ep, np_ + h, eq, nq) - f(ep, np_ - h, eq, nq)) / (2 * h),
        (f(ep, np_, eq + h, nq) - f(ep, np_, eq - h, nq)) / (2 * h),
        (f(ep, np_, eq, nq + h) - f(ep, np_, eq, nq - h)) / (2 * h),
    )
    for a, n in zip(analytic, numeric):
        assert a == pytest.approx(n, rel=1e-5)


def test_wrap_pi():
    assert _wrap_pi(3 * math.pi) == pytest.approx(math.pi)      # (-pi, pi]
    assert _wrap_pi(-3 * math.pi) == pytest.approx(math.pi)
    assert _wrap_pi(1.5 * math.pi) == pytest.approx(-0.5 * math.pi)
    assert _wrap_pi(-1.5 * math.pi) == pytest.approx(0.5 * math.pi)


def test_validation_fails_loud(cfg):
    with pytest.raises(ValueError, match="at least two fixed"):
        traverse.adjust_traverse(
            {"stations": [{"id": "A", "easting": 0, "northing": 0,
                            "fixed": True}],
             "observations": []}, cfg)
    bad = square_traverse()
    bad["observations"][0]["from"] = "ZZZ"
    with pytest.raises(ValueError, match="unknown station"):
        traverse.adjust_traverse(bad, cfg)
    bad = square_traverse()
    bad["observations"][0]["type"] = "zenith"
    with pytest.raises(ValueError, match="type must be"):
        traverse.adjust_traverse(bad, cfg)


def test_user_sigma_overrides_applied(cfg):
    data = square_traverse()
    data["observations"][0]["sigma_m"] = 0.001  # 1 mm on D1
    res = traverse.adjust_traverse(data, cfg)
    assert res.snoop.converged_clean
    # tighter distance weight -> smaller distance residuals dominate check:
    by_label = dict(zip(res.residual_labels, res.residuals))
    assert abs(by_label["D1 (dist A->B)"]) < 0.002


def test_load_traverse_rejects_garbage(tmp_path, cfg):
    p = tmp_path / "t.json"
    p.write_text('{"nope": true}')
    with pytest.raises(ValueError, match="stations.*observations"):
        traverse.adjust_traverse(traverse.load_traverse(str(p)), cfg)
