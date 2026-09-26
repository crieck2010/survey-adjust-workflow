"""Tests for the user-dictated stochastic model framework."""
import math

import pytest

from adjustflow import stochastic


def test_defaults_validate():
    stochastic.validate_weights(stochastic.default_weights())


def test_load_weights_merges_and_validates(tmp_path):
    p = tmp_path / "w.json"
    p.write_text('{"rtk": {"rms_scale": 2.0}, "overrides": []}')
    cfg = stochastic.load_weights(str(p))
    assert cfg["rtk"]["rms_scale"] == 2.0
    assert cfg["rtk"]["min_sigma_m"] == 0.005  # default preserved


@pytest.mark.parametrize("bad", [
    {"rtk": {"rms_scale": -1.0}},
    {"rtk": {"float_policy": "sometimes"}},
    {"rtk": {"nope": 1.0}},
    {"angles": {"arcseconds": 0}},
    {"distances": {"mm": "two"}},
    {"overrides": [{"scope": "orbit", "class": "rtk", "params": {"rms_scale": 2}}]},
    {"overrides": [{"scope": "class", "class": "lidar", "params": {}}]},
    {"overrides": [{"scope": "observation", "class": "rtk",
                    "params": {"rms_scale": 2}}]},          # missing match
    {"overrides": [{"scope": "class", "class": "rtk", "params": {}}]},
    {"overrides": [{"scope": "class", "class": "rtk",
                    "params": {"arcseconds": 5}}]},          # wrong class key
    {"datum_hold": "BASE-1"},
])
def test_invalid_configs_fail_loud(bad):
    with pytest.raises(ValueError):
        stochastic.validate_weights(bad)


def test_override_precedence():
    cfg = stochastic.default_weights()
    cfg["overrides"] = [
        {"scope": "class", "class": "rtk", "params": {"rms_scale": 9.0},
         "reason": "class-wide"},
        {"scope": "session", "class": "rtk", "match": "S1",
         "params": {"rms_scale": 2.0}, "reason": "bad day"},
        {"scope": "observation", "class": "rtk", "match": "101",
         "params": {"rms_scale": 3.0}, "reason": "canopy"},
    ]
    p, reasons = stochastic.resolve_params(cfg, "rtk", session_id="S1",
                                            obs_id="101")
    assert p["rms_scale"] == 3.0          # observation wins
    assert len(reasons) == 3
    p2, _ = stochastic.resolve_params(cfg, "rtk", session_id="OTHER",
                                      obs_id="999")
    assert p2["rms_scale"] == 9.0         # only class override applies


def test_sigma_rtk_scale_floor_fallback(cfg):
    s, _, fb = stochastic.sigma_rtk(0.010, cfg)
    assert s == pytest.approx(0.010) and not fb
    s, _, _ = stochastic.sigma_rtk(0.001, cfg)
    assert s == pytest.approx(0.005)      # min_sigma_m floor
    s, _, fb = stochastic.sigma_rtk(None, cfg)
    assert s == pytest.approx(0.030) and fb  # default_sigma_m fallback


def test_sigma_level(cfg):
    # 3 mm/sqrt(km) over 4 km -> 6 mm
    s, _, used_default = stochastic.sigma_level(4.0, cfg)
    assert s == pytest.approx(0.006) and not used_default
    s, _, used_default = stochastic.sigma_level(None, cfg)
    assert s == pytest.approx(0.003) and used_default  # 1 km default


def test_sigma_angle(cfg):
    s, _ = stochastic.sigma_angle(cfg)
    assert s == pytest.approx(5.0 * math.pi / (180 * 3600))


def test_sigma_distance(cfg):
    # 2 mm + 2 ppm over 1000 m -> 4 mm
    s, _ = stochastic.sigma_distance(1000.0, cfg)
    assert s == pytest.approx(0.004)
