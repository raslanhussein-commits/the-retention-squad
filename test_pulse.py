# Automatic tests for Customer Pulse AI (no API calls, free, ~1 second)
# Run with:  python test_pulse.py

import sys

import pulse_reader as pr

EXPECTED = {
    "Nova Retail Co.":        (54.3, "AT_RISK"),
    "BrightPath Logistics":   (92.9, "HEALTHY"),
    "Helix Health":           (39.1, "CRITICAL"),
    "Orbit Learning":         (75.1, "AT_RISK"),   # raw WATCH, escalated by guardrail
    "Pinecrest Bank":         (53.6, "AT_RISK"),
    "Zenith Manufacturing":   (63.0, "AT_RISK"),
    "Stellar Fintech":        (84.7, "HEALTHY"),
    "Redwood Telecom":        (66.4, "WATCH"),
    "Crimson Energy":         (77.8, "WATCH"),
}


def by_name(name):
    return next(a for a in pr.accounts if a.name == name)


def variant(base, **changes):
    """Copy an account with some fields changed."""
    return pr.AccountSnapshot(**{**base.model_dump(), **changes})


def test_expected_scores_and_statuses():
    for a in pr.accounts:
        h = pr.calculate_health(a)
        score, status = EXPECTED[a.name]
        assert h.total_score == score, f"{a.name}: score {h.total_score} != {score}"
        assert h.status == status, f"{a.name}: status {h.status} != {status}"


def test_scores_stay_between_0_and_100():
    for a in pr.accounts:
        h = pr.calculate_health(a)
        for field in ("adoption", "engagement", "support", "sentiment",
                      "commercial", "relationship", "implementation", "total_score"):
            value = getattr(h, field)
            assert 0 <= value <= 100, f"{a.name}: {field} = {value}"


def test_guardrails_never_make_status_better():
    for a in pr.accounts:
        h = pr.calculate_health(a)
        assert pr.STATUS_ORDER.index(h.status) >= pr.STATUS_ORDER.index(h.score_status), a.name


def test_status_thresholds():
    assert pr.status_from_score(80) == "HEALTHY"
    assert pr.status_from_score(79.9) == "WATCH"
    assert pr.status_from_score(65) == "WATCH"
    assert pr.status_from_score(64.9) == "AT_RISK"
    assert pr.status_from_score(45) == "AT_RISK"
    assert pr.status_from_score(44.9) == "CRITICAL"


def test_more_urgent_tickets_never_raises_score():
    base = by_name("Nova Retail Co.")
    scores = [pr.calculate_health(variant(base, urgent_support_tickets=n)).total_score
              for n in range(0, 6)]
    assert scores == sorted(scores, reverse=True), scores


def test_falling_usage_never_raises_score():
    base = by_name("BrightPath Logistics")
    scores = [pr.calculate_health(variant(base, usage_change_pct=u)).total_score
              for u in (40, 20, 0, -20, -40)]
    assert scores == sorted(scores, reverse=True), scores


def test_missing_data_does_not_crash():
    empty = pr.AccountSnapshot(
        name="Empty Co.", usage_change_pct=0, active_user_ratio_pct=50,
        open_support_tickets=0, urgent_support_tickets=0, days_to_renewal=200,
    )
    h = pr.calculate_health(empty)
    assert 0 <= h.total_score <= 100


def test_billing_dispute_plus_competitor_forces_critical():
    base = by_name("BrightPath Logistics")
    h = pr.calculate_health(variant(base, payment_dispute=True, competitor_evaluation=True))
    assert h.status == "CRITICAL", h.status


def test_unhappy_nps_blocks_healthy_status():
    base = by_name("BrightPath Logistics")
    h = pr.calculate_health(variant(base, nps=3))
    assert h.status != "HEALTHY", h.status


def test_lost_sponsor_near_renewal_blocks_healthy_status():
    base = by_name("BrightPath Logistics")
    h = pr.calculate_health(variant(base, executive_sponsor_active=False, days_to_renewal=60))
    assert h.status in ("AT_RISK", "CRITICAL"), h.status


def test_report_file_is_written(tmp_path="test_report_tmp.md"):
    import os
    results = [{"account": a, "health": pr.calculate_health(a),
                "health_analysis": "x", "risk_analysis": "y", "action_plan": "z"}
               for a in pr.accounts]
    pr.write_report(results, tmp_path)
    text = open(tmp_path, encoding="utf-8").read()
    os.remove(tmp_path)
    assert "Portfolio Summary" in text
    for a in pr.accounts:
        assert a.name in text


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as error:
            failed += 1
            print(f"FAIL  {name}  ->  {error}")
        except Exception as error:
            failed += 1
            print(f"ERROR {name}  ->  {type(error).__name__}: {error}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)