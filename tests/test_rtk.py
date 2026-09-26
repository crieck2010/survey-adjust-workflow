"""Tests for the RTK repeated-occupation path."""
import pytest

from adjustflow import rtk


def test_weighted_mean_matches_hand_calc(project, cfg):
    res = rtk.adjust_rtk_points(project, cfg)
    p101 = next(p for p in res.points if p.name == "101")
    assert p101.n_occupations == 3
    # equal weights: plain mean
    assert p101.easting == pytest.approx((500100.00 + 500100.02 + 500099.99) / 3)
    assert p101.sigma_e == pytest.approx(0.010 / (3 ** 0.5))
    assert p101.sigma_u == pytest.approx(0.015 / (3 ** 0.5))
    assert p101.elevation == pytest.approx((105.00 + 105.02 + 104.99) / 3)


def test_float_excluded_by_default_with_warning(project, cfg):
    res = rtk.adjust_rtk_points(project, cfg)
    names = [p.name for p in res.points]
    assert "103" not in names  # FLOAT excluded
    assert res.n_excluded_total == 1  # counted even though the point is gone
    assert any("103" in w and "exclude" in w for w in res.warnings)


def test_float_downweight_policy(project, cfg):
    cfg["rtk"]["float_policy"] = "downweight"
    res = rtk.adjust_rtk_points(project, cfg)
    p103 = next(p for p in res.points if p.name == "103")
    assert p103.sigma_e == pytest.approx(0.010 * 3.0)  # downweight_factor
    assert p103.n_excluded == 0


def test_datum_bases_reported(project, cfg):
    res = rtk.adjust_rtk_points(project, cfg)
    assert res.datum_bases[0]["id"] == "BASE-1"
    assert res.datum_bases[0]["elevation"] == pytest.approx(100.0)


def test_suspect_occupation_flagged(cfg):
    from conftest import make_project, make_point
    from field.models import RoverSession
    proj = make_project()
    # Inject a 10 cm blunder into one occupation of 101 (sigma 10 mm).
    bad = make_point("101", 500100.10, 4500100.00, 105.00)
    proj.sessions.append(RoverSession(id="2026-09-27_BASE-1",
                                      base_station_id="BASE-1",
                                      date="2026-09-27", points=[bad]))
    res = rtk.adjust_rtk_points(proj, cfg)
    p101 = next(p for p in res.points if p.name == "101")
    assert any(o.suspect for o in p101.occupations)
    assert any("suspect" in w for w in res.warnings)
    # default: kept but flagged (4 occupations still in the mean)
    assert p101.n_occupations == 4


def test_suspect_occupation_rejected_when_configured(project, cfg):
    from conftest import make_point
    from field.models import RoverSession
    cfg["rtk"]["reject_suspect_occupations"] = True
    bad = make_point("101", 500100.10, 4500100.00, 105.00)
    project.sessions.append(RoverSession(id="2026-09-27_BASE-1",
                                         base_station_id="BASE-1",
                                         date="2026-09-27", points=[bad]))
    res = rtk.adjust_rtk_points(project, cfg)
    p101 = next(p for p in res.points if p.name == "101")
    # blundered occupation excluded from the mean -> mean near truth
    assert abs(p101.easting - 500100.003) < 0.01
