from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import joblib
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .categories import CATEGORY_OPTIONS, categorize_topic
from .models import Feedback, ModelArtifact, Topic, utcnow
from .repositories import delete_old_model_artifacts, get_setting, latest_model_artifact
from .topic_pipeline import refresh_all_topic_scores
from .utils import as_utc, clamp, clean_text, parse_csv_keywords, tokenize


logger = logging.getLogger(__name__)
MODEL_NAME = "topic_recommender"
FEATURE_NAMES = [
    "freshness",
    "source_count",
    "item_count",
    "source_velocity",
    "content_quality",
    "conflict_score",
    "risk_score",
    "preferred_match",
    "blocked_match",
    "title_length",
    "summary_length",
    "source_diversity",
    "chinese_ratio",
    "ai_analyzed",
    "ai_confidence",
    "ai_recommendation_count",
    "ai_audience_count",
    *[f"category_{category}" for category in CATEGORY_OPTIONS],
]
FEATURE_LABELS = {
    "freshness": "时效性",
    "source_count": "来源数量",
    "item_count": "信息条目数",
    "source_velocity": "传播速度",
    "content_quality": "内容完整度",
    "conflict_score": "矛盾争议度",
    "risk_score": "内容风险度",
    "preferred_match": "偏好关键词匹配",
    "blocked_match": "屏蔽关键词匹配",
    "title_length": "标题长度",
    "summary_length": "摘要长度",
    "source_diversity": "来源多样性",
    "chinese_ratio": "中文内容比例",
    "ai_analyzed": "是否完成 AI 分析",
    "ai_confidence": "AI 置信度",
    "ai_recommendation_count": "AI 推荐角度数",
    "ai_audience_count": "目标受众数",
    **{f"category_{category}": f"内容分类：{category}" for category in CATEGORY_OPTIONS},
}

FEEDBACK_VALUES = {
    "view": 5.0,
    "save": 45.0,
    "dismiss": -30.0,
    "generate": 60.0,
    "choose_angle": 55.0,
    "add_image": 35.0,
    "edit": 80.0,
    "publish": 100.0,
}
FEEDBACK_LABELS = {
    "view": "查看热点",
    "save": "收藏",
    "dismiss": "不感兴趣",
    "generate": "生成文章",
    "choose_angle": "采用推荐角度",
    "add_image": "添加配图",
    "edit": "编辑文章",
    "publish": "发布文章",
}


def feature_vector(db: Session, topic: Topic) -> list[float]:
    age_hours = max(0.0, (utcnow() - as_utc(topic.last_seen_at)).total_seconds() / 3600)
    freshness = max(0.0, 1.0 - age_hours / 72.0)
    preferred = parse_csv_keywords(get_setting(db, "preferred_keywords"))
    blocked = parse_csv_keywords(get_setting(db, "blocked_keywords"))
    text = f"{topic.title} {topic.summary}".lower()
    tokens = tokenize(text)
    preferred_match = sum(1 for value in preferred if value in tokens or value in text) / max(1, len(preferred))
    blocked_match = sum(1 for value in blocked if value in tokens or value in text) / max(1, len(blocked))
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    visible_chars = len(re.findall(r"\w", text))
    category = categorize_topic(topic.title, topic.summary, topic.ai_tags)
    base = [
        freshness,
        min(1.0, topic.source_count / 10.0),
        min(1.0, topic.item_count / 20.0),
        min(1.0, topic.source_velocity / 5.0),
        topic.content_quality / 100.0,
        topic.conflict_score / 100.0,
        topic.risk_score / 100.0,
        min(1.0, preferred_match),
        min(1.0, blocked_match),
        min(1.0, len(topic.title) / 80.0),
        min(1.0, len(topic.summary or "") / 1000.0),
        min(1.0, topic.source_count / max(1.0, float(topic.item_count))),
        min(1.0, chinese_chars / max(1, visible_chars)),
        1.0 if topic.analyzed_at is not None else 0.0,
        min(1.0, max(0.0, float(topic.ai_confidence or 0.0))),
        min(1.0, len(topic.ai_angles or []) / 5.0),
        min(1.0, len(topic.ai_audience or []) / 5.0),
    ]
    return base + [1.0 if category == value else 0.0 for value in CATEGORY_OPTIONS]


def gpu_status() -> dict[str, Any]:
    status: dict[str, Any] = {"available": False, "name": "未检测到", "xgboost_cuda": False}
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            completed = subprocess.run(
                [executable, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            name = clean_text(completed.stdout.splitlines()[0] if completed.stdout else "", 200)
            if name:
                status.update({"available": True, "name": name})
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
    try:
        import xgboost as xgb

        status["xgboost_cuda"] = bool(xgb.build_info().get("USE_CUDA"))
        status["xgboost_version"] = xgb.__version__
    except (ImportError, AttributeError):
        status["xgboost_version"] = None
    return status


def _new_model(prefer_gpu: bool = True):
    hardware = gpu_status()
    if prefer_gpu and hardware["available"] and hardware["xgboost_cuda"]:
        from xgboost import XGBRegressor

        return XGBRegressor(
            n_estimators=180,
            learning_rate=0.035,
            max_depth=3,
            min_child_weight=2,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="reg:squarederror",
            tree_method="hist",
            device="cuda",
            random_state=42,
        ), "XGBoost", "CUDA"
    return GradientBoostingRegressor(
        n_estimators=140,
        learning_rate=0.035,
        max_depth=2,
        min_samples_leaf=2,
        loss="huber",
        random_state=42,
    ), "GradientBoosting", "CPU"


def _use_cpu_for_prediction(model, training_device: str, metrics: dict[str, Any]) -> None:
    """Keep CUDA for fitting, then avoid CPU-input/CUDA-booster prediction copies."""
    if training_device == "CUDA" and hasattr(model, "get_booster"):
        model.get_booster().set_param({"device": "cpu"})
        metrics["prediction_device"] = "CPU"


def record_feedback(db: Session, topic_id: int, action: str, article_id: int | None = None) -> Feedback:
    if action not in FEEDBACK_VALUES:
        raise ValueError(f"Unsupported feedback action: {action}")
    feedback = Feedback(topic_id=topic_id, article_id=article_id, action=action, value=FEEDBACK_VALUES[action])
    db.add(feedback)
    db.commit()
    return feedback


def _training_rows(db: Session) -> tuple[list[list[float]], list[float]]:
    topics = {topic.id: topic for topic in db.scalars(select(Topic))}
    feedback_by_topic: dict[int, list[Feedback]] = defaultdict(list)
    for feedback in db.scalars(select(Feedback).order_by(Feedback.created_at.asc())):
        feedback_by_topic[feedback.topic_id].append(feedback)
    x: list[list[float]] = []
    y: list[float] = []
    for topic_id, events in feedback_by_topic.items():
        topic = topics.get(topic_id)
        if topic is None:
            continue
        positive = sum(event.value for event in events if event.value >= 0)
        negative = sum(abs(event.value) for event in events if event.value < 0)
        target = clamp(positive - negative)
        x.append(feature_vector(db, topic))
        y.append(target)
    return x, y


def train_model(db: Session) -> dict[str, Any]:
    started = time.perf_counter()
    x, y = _training_rows(db)
    if len(x) < 8:
        raise ValueError(f"至少需要 8 个有反馈的不同选题，当前只有 {len(x)} 个")
    model, algorithm, device = _new_model()
    metrics: dict[str, Any] = {
        "samples": len(x),
        "algorithm": algorithm,
        "device": device,
        "feature_count": len(FEATURE_NAMES),
        "target_min": round(min(y), 4),
        "target_max": round(max(y), 4),
        "target_mean": round(sum(y) / len(y), 4),
    }
    if len(x) >= 12:
        train_x, test_x, train_y, test_y = train_test_split(x, y, test_size=0.25, random_state=42)
        try:
            model.fit(train_x, train_y)
        except Exception:
            if device != "CUDA":
                raise
            logger.exception("CUDA training failed; falling back to CPU")
            model, algorithm, device = _new_model(prefer_gpu=False)
            metrics.update({"algorithm": algorithm, "device": device, "gpu_fallback": True})
            model.fit(train_x, train_y)
        _use_cpu_for_prediction(model, device, metrics)
        predictions = model.predict(test_x)
        baseline_prediction = sum(train_y) / len(train_y)
        baseline_predictions = [baseline_prediction] * len(test_y)
        metrics["train_samples"] = len(train_x)
        metrics["validation_samples"] = len(test_x)
        metrics["validation_mae"] = round(float(mean_absolute_error(test_y, predictions)), 4)
        metrics["validation_rmse"] = round(math.sqrt(float(mean_squared_error(test_y, predictions))), 4)
        metrics["validation_r2"] = round(float(r2_score(test_y, predictions)), 4)
        metrics["baseline_mae"] = round(float(mean_absolute_error(test_y, baseline_predictions)), 4)
    else:
        try:
            model.fit(x, y)
        except Exception:
            if device != "CUDA":
                raise
            logger.exception("CUDA training failed; falling back to CPU")
            model, algorithm, device = _new_model(prefer_gpu=False)
            metrics.update({"algorithm": algorithm, "device": device, "gpu_fallback": True})
            model.fit(x, y)
        _use_cpu_for_prediction(model, device, metrics)
        metrics["validation_mae"] = None
        metrics["validation_rmse"] = None
        metrics["validation_r2"] = None
        metrics["baseline_mae"] = None
        metrics["train_samples"] = len(x)
        metrics["validation_samples"] = 0
    importances = getattr(model, "feature_importances_", None)
    if importances is not None:
        ranked = sorted(
            ((name, float(value)) for name, value in zip(FEATURE_NAMES, importances, strict=True)),
            key=lambda item: item[1],
            reverse=True,
        )
        metrics["top_features"] = [
            {"name": name, "label": FEATURE_LABELS.get(name, name), "importance": round(value * 100.0, 3)}
            for name, value in ranked[:10]
        ]
    path = settings.model_dir / "topic_recommender.joblib"
    joblib.dump({"model": model, "features": FEATURE_NAMES}, path)
    _apply_model_scores(db, model)
    model_scores = [float(topic.model_score) for topic in db.scalars(select(Topic)) if topic.model_score is not None]
    if model_scores:
        metrics["score_min"] = round(min(model_scores), 4)
        metrics["score_max"] = round(max(model_scores), 4)
        metrics["score_mean"] = round(sum(model_scores) / len(model_scores), 4)
    metrics["training_seconds"] = round(time.perf_counter() - started, 3)
    artifact = ModelArtifact(
        name=MODEL_NAME,
        path=str(path),
        sample_count=len(x),
        metrics_json=metrics,
    )
    db.add(artifact)
    db.flush()
    delete_old_model_artifacts(db, MODEL_NAME, str(path))
    db.commit()
    refresh_all_topic_scores(db)
    return metrics


def _load_model(db: Session):
    artifact = latest_model_artifact(db, MODEL_NAME)
    if artifact is None or not Path(artifact.path).exists():
        return None
    try:
        bundle = joblib.load(artifact.path)
        return bundle.get("model") if isinstance(bundle, dict) else bundle
    except Exception:
        logger.exception("Could not load recommender model")
        return None


def _apply_model_scores(db: Session, model) -> None:
    for topic in db.scalars(select(Topic)):
        prediction = float(model.predict([feature_vector(db, topic)])[0])
        topic.model_score = clamp(prediction)


def apply_saved_model(db: Session) -> bool:
    model = _load_model(db)
    if model is None:
        return False
    _apply_model_scores(db, model)
    db.commit()
    refresh_all_topic_scores(db)
    return True


def recommender_status(db: Session) -> dict[str, Any]:
    artifact = latest_model_artifact(db, MODEL_NAME)
    topics = list(db.scalars(select(Topic)))
    feedback_events = list(db.scalars(select(Feedback).order_by(Feedback.created_at.asc())))
    sample_topic_ids = {feedback.topic_id for feedback in feedback_events}
    sample_count = len(sample_topic_ids)
    action_counts = Counter(feedback.action for feedback in feedback_events)
    feedback_breakdown = [
        {
            "action": action,
            "label": FEEDBACK_LABELS[action],
            "count": action_counts.get(action, 0),
            "weight": weight,
        }
        for action, weight in FEEDBACK_VALUES.items()
    ]
    category_counts = Counter(
        categorize_topic(topic.title, topic.summary, topic.ai_tags)
        for topic in topics
        if topic.id in sample_topic_ids
    )
    model_topics = [topic for topic in topics if topic.model_score is not None]
    score_values = [float(topic.model_score) for topic in model_topics]
    score_buckets = [
        {"label": "0–20", "count": sum(0 <= value < 20 for value in score_values)},
        {"label": "20–40", "count": sum(20 <= value < 40 for value in score_values)},
        {"label": "40–60", "count": sum(40 <= value < 60 for value in score_values)},
        {"label": "60–80", "count": sum(60 <= value < 80 for value in score_values)},
        {"label": "80–100", "count": sum(80 <= value <= 100 for value in score_values)},
    ]
    artifacts = list(
        db.scalars(select(ModelArtifact).where(ModelArtifact.name == MODEL_NAME).order_by(ModelArtifact.trained_at.desc()).limit(10))
    )
    quality_note = "尚未训练，当前推荐分使用透明规则计算。"
    if artifact:
        metrics = artifact.metrics_json or {}
        validation_r2 = metrics.get("validation_r2")
        if validation_r2 is None:
            quality_note = "真实样本仍少，暂时没有独立验证集；当前模型只能作为辅助排序。"
        elif validation_r2 < 0:
            quality_note = "验证 R² 为负，当前模型尚未超过简单基线；需要更多收藏、忽略、编辑和发布反馈。"
        elif validation_r2 < 0.3:
            quality_note = "模型已有学习效果但稳定性偏弱，建议继续积累不同类型的真实反馈。"
        else:
            quality_note = "模型在当前验证集上已有可用区分能力，仍应结合来源质量与风险指标判断。"
    return {
        "trained": artifact is not None and Path(artifact.path).exists(),
        "sample_count": sample_count,
        "artifact": artifact,
        "feature_names": FEATURE_NAMES,
        "feature_definitions": [{"name": name, "label": FEATURE_LABELS.get(name, name)} for name in FEATURE_NAMES],
        "hardware": gpu_status(),
        "samples_needed": max(0, 8 - sample_count),
        "topic_count": len(topics),
        "feedback_event_count": len(feedback_events),
        "coverage_percent": round(sample_count / max(1, len(topics)) * 100.0, 2),
        "feedback_breakdown": feedback_breakdown,
        "category_breakdown": [
            {"name": category, "count": category_counts.get(category, 0)} for category in CATEGORY_OPTIONS
        ],
        "score_buckets": score_buckets,
        "score_count": len(score_values),
        "unscored_count": max(0, len(topics) - len(model_topics)),
        "score_min": round(min(score_values), 2) if score_values else None,
        "score_max": round(max(score_values), 2) if score_values else None,
        "score_mean": round(sum(score_values) / len(score_values), 2) if score_values else None,
        "top_topics": sorted(model_topics, key=lambda topic: float(topic.model_score or 0), reverse=True)[:10],
        "training_history": artifacts,
        "quality_note": quality_note,
    }
