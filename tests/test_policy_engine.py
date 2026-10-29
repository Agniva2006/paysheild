import pytest
from engine.policy_engine import get_policy_engine

def test_policy_engine_initialization():
    engine = get_policy_engine()
    engine.recalibrate(new_budget=0.02, new_block_threshold=0.85)
    state = engine.get_state()
    assert state["friction_budget"] == 0.02
    assert state["stepup_threshold"] > 0.0
    assert state["block_threshold"] == 0.85
    assert state["is_calibrated"] is True

def test_policy_decisions():
    engine = get_policy_engine()
    engine.recalibrate(new_budget=0.02, new_block_threshold=0.85)
    
    t_stepup = engine.stepup_threshold
    t_block = engine.block_threshold

    # Score below t_stepup -> ALLOW
    action, reason = engine.evaluate(t_stepup - 0.05)
    assert action == "ALLOW"
    assert "seamless" in reason.lower() or "tolerance" in reason.lower()

    # Score between t_stepup and t_block -> STEP-UP
    action, reason = engine.evaluate((t_stepup + t_block) / 2.0)
    assert action == "STEP-UP"
    assert "secondary authentication" in reason.lower() or "step-up" in reason.lower()

    # Score at or above t_block -> BLOCK
    action, reason = engine.evaluate(t_block + 0.05)
    assert action == "BLOCK"
    assert "hard denial" in reason.lower() or "block" in reason.lower()

def test_live_recalibration_2_to_1_percent():
    engine = get_policy_engine()
    
    # Baseline: 2% budget
    engine.recalibrate(new_budget=0.02)
    stepup_2pct = engine.stepup_threshold

    # Target: 1% budget
    res = engine.recalibrate(new_budget=0.01)
    stepup_1pct = engine.stepup_threshold

    # At 1% friction budget (tighter friction), step-up threshold must be higher than at 2%
    assert stepup_1pct > stepup_2pct
    assert res["requires_retraining"] is False
    assert res["requires_restart"] is False
    assert res["status"] == "success"
