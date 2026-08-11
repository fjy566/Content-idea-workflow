from dataclasses import replace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.recommender as recommender
from app.models import Base, Feedback, Topic
from app.recommender import FEEDBACK_VALUES, FEATURE_LABELS, FEATURE_NAMES


def test_recommender_has_explicit_features_and_feedback_values():
    assert "freshness" in FEATURE_NAMES
    assert "ai_confidence" in FEATURE_NAMES
    assert "category_IT科技" in FEATURE_NAMES
    assert len(FEATURE_NAMES) >= 20
    assert FEATURE_LABELS["risk_score"] == "内容风险度"
    assert FEEDBACK_VALUES["publish"] > FEEDBACK_VALUES["view"]
    assert FEEDBACK_VALUES["dismiss"] < 0


def test_recommender_can_train_on_feedback_in_temporary_database(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    monkeypatch.setattr(recommender, "settings", replace(recommender.settings, data_dir=tmp_path))
    with Session(engine) as db:
        for index in range(8):
            topic = Topic(
                title=f"真实测试选题 {index}",
                summary="用于训练反馈排序的测试记录",
                source_count=index + 1,
                item_count=index + 1,
                source_velocity=index / 3,
                content_quality=index * 10,
                conflict_score=index * 5,
                risk_score=5,
                recommendation_score=20,
            )
            db.add(topic)
            db.flush()
            db.add(Feedback(topic_id=topic.id, action="publish", value=float(index + 1)))
        db.commit()
        progress_updates = []
        metrics = recommender.train_model(db, requested_device="cpu", progress_callback=progress_updates.append)
        assert metrics["samples"] == 8
        assert metrics["requested_device"] == "cpu"
        assert metrics["device"] == "CPU"
        assert metrics["feature_count"] == len(FEATURE_NAMES)
        assert metrics["train_samples"] == 8
        assert metrics["target_min"] <= metrics["target_mean"] <= metrics["target_max"]
        assert len(metrics["top_features"]) == 10
        assert all(item["label"] for item in metrics["top_features"])
        assert (model_dir / "topic_recommender.joblib").exists()

        status = recommender.recommender_status(db)
        assert status["topic_count"] == 8
        assert status["feedback_event_count"] == 8
        assert status["coverage_percent"] == 100.0
        assert status["unscored_count"] == 0
        assert sum(item["count"] for item in status["feedback_breakdown"]) == 8
        assert status["training_history"]
        assert progress_updates[0]["phase"] == "preparing"
        assert any(update["phase"] == "evaluation_done" for update in progress_updates)
        assert progress_updates[-1]["phase"] == "completed"
        assert progress_updates[-1]["progress"] == 100


def test_cuda_request_is_rejected_when_cuda_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        recommender,
        "gpu_status",
        lambda: {
            "available": False,
            "name": "未检测到",
            "xgboost_cuda": False,
            "cuda_ready": False,
            "cuda_reason": "测试环境没有 CUDA",
            "xgboost_version": None,
        },
    )
    try:
        recommender._new_model("cuda")
    except ValueError as exc:
        assert "测试环境没有 CUDA" in str(exc)
    else:
        raise AssertionError("CUDA request should fail when CUDA is unavailable")
