"""Tests for the shared data-snooping machinery."""
import math

import pytest

from adjustflow.snooping import (
    LinResult,
    iterative_snooping,
    standardized_residuals,
)


def _tiny_net(blunder=0.0):
    # Fixed F=0; free X, Y. Obs: F->X=1.0, X->Y=1.0, F->Y=2.0+blunder.
    # Design rows: [1,0], [-1,1], [0,1]; P = I.
    obs = [
        {"from_station": "F", "to_station": "X", "delta_h": 1.0, "sigma": 1.0},
        {"from_station": "X", "to_station": "Y", "delta_h": 1.0, "sigma": 1.0},
        {"from_station": "F", "to_station": "Y", "delta_h": 2.0 + blunder,
         "sigma": 1.0},
    ]
    labels = ["F->X", "X->Y", "F->Y"]
    return obs, labels


def _run(obs):
    from adjustflow.snooping import level_linresult
    from adjustflow import stochastic  # noqa (import order sanity)
    import adjust
    das = [adjust.DiffObservation(o["from_station"], o["to_station"],
                                  o["delta_h"], o["sigma"]) for o in obs]
    r = adjust.adjust_level_net(das, fixed={"F": 0.0})
    return level_linresult(obs, {"F": 0.0}, r.residuals, r.dof, r.sigma0)


def test_standardized_residuals_match_hand_calc():
    obs, _ = _tiny_net(blunder=0.0)
    lin = _run(obs)
    w = standardized_residuals(lin)
    # Hand calc: v = [1/30, 1/30, -1/30]... verify numerically instead:
    # with zero misclosure residuals must be ~0 and |w| tiny.
    assert all(abs(x) < 1e-9 for x in lin.residuals)
    assert all(x < 1e-6 for x in w)


def test_standardized_residual_of_blunder():
    obs, _ = _tiny_net(blunder=0.3)
    lin = _run(obs)
    w = standardized_residuals(lin)
    # qvv_33 = 1/3 -> w_3 = 0.1/sqrt(1/3) = 0.1732
    assert w[2] == pytest.approx(0.1 / math.sqrt(1 / 3), rel=1e-6)
    assert w[2] == max(w)


def test_iterative_snooping_removes_blunder():
    # w_3 = blunder/sqrt(3) for this geometry; need blunder > ~5.7
    obs, labels = _tiny_net(blunder=10.0)
    res = iterative_snooping(
        obs, labels, lambda idxs: _run([obs[i] for i in idxs]),
        threshold=3.29)
    assert len(res.rejected) == 1
    assert res.rejected[0].index == 2
    assert "data snooping" in res.rejected[0].reason
    assert res.kept_indices == [0, 1]
    assert res.converged_clean


def test_iterative_snooping_clean_set_untouched():
    obs, labels = _tiny_net(blunder=0.0)
    res = iterative_snooping(
        obs, labels, lambda idxs: _run([obs[i] for i in idxs]))
    assert res.rejected == []
    assert res.kept_indices == [0, 1, 2]
    assert res.converged_clean


def test_threshold_must_be_positive():
    obs, labels = _tiny_net()
    with pytest.raises(ValueError, match="positive"):
        iterative_snooping(obs, labels, lambda idxs: None, threshold=0)
