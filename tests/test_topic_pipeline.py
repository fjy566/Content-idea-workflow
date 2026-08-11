from app.topic_pipeline import heuristic_conflict, heuristic_risk


def test_conflict_heuristic_detects_conflict_terms():
    assert heuristic_conflict("平台回应消费者质疑", "") > 0


def test_risk_heuristic_detects_unverified_terms():
    assert heuristic_risk("网传某事件", "") > 0

