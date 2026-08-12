from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Callable
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
from .topic_pipeline import refresh_all_topic_scores, refresh_topic_score_mix
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

ProgressCallback = Callable[[dict[str, Any]], None]


def _feature_vector_for_settings(topic: Topic, preferred: list[str], blocked: list[str], now) -> list[float]:
    age_hours = max(0.0, (now - as_utc(topic.last_seen_at)).total_seconds() / 3600)
    freshness = max(0.0, 1.0 - age_hours / 72.0)
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


def feature_vector(db: Session, topic: Topic) -> list[float]:
    return _feature_vector_for_settings(
        topic,
        parse_csv_keywords(get_setting(db, "preferred_keywords")),
        parse_csv_keywords(get_setting(db, "blocked_keywords")),
        utcnow(),
    )


def _feature_matrix(db: Session, topics: list[Topic]) -> list[list[float]]:
    preferred = parse_csv_keywords(get_setting(db, "preferred_keywords"))
    blocked = parse_csv_keywords(get_setting(db, "blocked_keywords"))
    now = utcnow()
    return [_feature_vector_for_settings(topic, preferred, blocked, now) for topic in topics]


def gpu_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "available": False,
        "name": "未检测到",
        "xgboost_cuda": False,
        "cuda_ready": False,
        "cuda_reason": "未检测到可用于训练的 NVIDIA GPU。",
    }
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
                status["cuda_reason"] = "已检测到 NVIDIA GPU，正在检查 XGBoost CUDA 支持。"
        except (OSError, subprocess.SubprocessError, IndexError):
            status["cuda_reason"] = "无法运行 nvidia-smi，CUDA 训练不可用。"
    try:
        import xgboost as xgb

        status["xgboost_cuda"] = bool(xgb.build_info().get("USE_CUDA"))
        status["xgboost_version"] = xgb.__version__
    except (ImportError, AttributeError):
        status["xgboost_version"] = None
        status["cuda_reason"] = "未安装支持 CUDA 检测的 XGBoost，当前只能使用 CPU。"
    if status["available"] and not status["xgboost_cuda"]:
        status["cuda_reason"] = "当前 XGBoost 未启用 CUDA，安装 GPU 依赖后才能选择 CUDA。"
    status["cuda_ready"] = bool(status["available"] and status["xgboost_cuda"])
    if status["cuda_ready"]:
        status["cuda_reason"] = "CUDA 训练可用。"
    return status


def _new_model(requested_device: str = "auto", hardware: dict[str, Any] | None = None):
    requested_device = requested_device.strip().lower()
    if requested_device not in {"auto", "cpu", "cuda"}:
        raise ValueError("训练设备只能选择自动、CPU 或 CUDA。")
    hardware = hardware or gpu_status()
    if requested_device == "cuda" and not hardware["cuda_ready"]:
        raise ValueError(f"当前环境无法使用 CUDA：{hardware['cuda_reason']}")
    if requested_device in {"auto", "cuda"} and hardware["cuda_ready"]:
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


def _notify_progress(
    callback: ProgressCallback | None,
    phase: str,
    progress: float,
    message: str,
    **values: Any,
) -> None:
    if callback is None:
        return
    update = {"phase": phase, "progress": progress, "message": message}
    update.update(values)
    callback(update)


def _fit_with_fallback(
    model,
    algorithm: str,
    device: str,
    requested_device: str,
    hardware: dict[str, Any],
    train_x: list[list[float]],
    train_y: list[float],
    metrics: dict[str, Any],
    callback: ProgressCallback | None,
):
    try:
        model.fit(train_x, train_y)
    except Exception as exc:
        if device != "CUDA" or requested_device == "cuda":
            raise RuntimeError(f"{device} 训练失败：{exc}") from exc
        logger.exception("CUDA training failed; falling back to CPU")
        _notify_progress(callback, "fallback", 35, "CUDA 训练失败，自动切换 CPU 继续训练。", error=str(exc))
        model, algorithm, device = _new_model("cpu", hardware)
        metrics.update({"algorithm": algorithm, "device": device, "gpu_fallback": True, "gpu_error": str(exc)})
        model.fit(train_x, train_y)
    return model, algorithm, device


def _record_prediction_device(model, training_device: str, metrics: dict[str, Any]) -> None:
    """Keep a CUDA-trained booster on CUDA for validation and later scoring."""
    metrics["prediction_device"] = training_device if hasattr(model, "get_booster") else "CPU"


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
    sample_topics: list[Topic] = []
    y: list[float] = []
    for topic_id, events in feedback_by_topic.items():
        topic = topics.get(topic_id)
        if topic is None:
            continue
        positive = sum(event.value for event in events if event.value >= 0)
        negative = sum(abs(event.value) for event in events if event.value < 0)
        target = clamp(positive - negative)
        sample_topics.append(topic)
        y.append(target)
    return _feature_matrix(db, sample_topics), y


def train_model(
    db: Session,
    requested_device: str = "auto",
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    requested_device = requested_device.strip().lower()
    _notify_progress(progress_callback, "preparing", 5, "正在读取真实反馈并整理训练样本。")
    x, y = _training_rows(db)
    if len(x) < 8:
        raise ValueError(f"至少需要 8 个有反馈的不同选题，当前只有 {len(x)} 个")
    hardware = gpu_status()
    model, algorithm, device = _new_model(requested_device, hardware)
    metrics: dict[str, Any] = {
        "samples": len(x),
        "requested_device": requested_device,
        "algorithm": algorithm,
        "device": device,
        "feature_count": len(FEATURE_NAMES),
        "target_min": round(min(y), 4),
        "target_max": round(max(y), 4),
        "target_mean": round(sum(y) / len(y), 4),
    }
    _notify_progress(
        progress_callback,
        "data_ready",
        15,
        f"已整理 {len(x)} 个有反馈的不同选题，开始准备训练。",
        samples=len(x),
        algorithm=algorithm,
        device=device,
    )
    if len(x) >= 12:
        _notify_progress(progress_callback, "split", 22, "正在划分训练集和验证集。", samples=len(x))
        train_x, test_x, train_y, test_y = train_test_split(x, y, test_size=0.25, random_state=42)
        _notify_progress(progress_callback, "training", 30, f"正在使用 {device} 训练模型。", device=device)
        model, algorithm, device = _fit_with_fallback(
            model,
            algorithm,
            device,
            requested_device,
            hardware,
            train_x,
            train_y,
            metrics,
            progress_callback,
        )
        _record_prediction_device(model, device, metrics)
        _notify_progress(progress_callback, "evaluating", 72, "模型训练完成，正在计算验证指标。", device=device)
        predictions = _predict_model_matrix(model, test_x, device)
        baseline_prediction = sum(train_y) / len(train_y)
        baseline_predictions = [baseline_prediction] * len(test_y)
        metrics["train_samples"] = len(train_x)
        metrics["validation_samples"] = len(test_x)
        metrics["validation_mae"] = round(float(mean_absolute_error(test_y, predictions)), 4)
        metrics["validation_rmse"] = round(math.sqrt(float(mean_squared_error(test_y, predictions))), 4)
        metrics["validation_r2"] = round(float(r2_score(test_y, predictions)), 4)
        metrics["baseline_mae"] = round(float(mean_absolute_error(test_y, baseline_predictions)), 4)
    else:
        _notify_progress(progress_callback, "training", 30, f"正在使用 {device} 训练模型。", device=device)
        model, algorithm, device = _fit_with_fallback(
            model,
            algorithm,
            device,
            requested_device,
            hardware,
            x,
            y,
            metrics,
            progress_callback,
        )
        _record_prediction_device(model, device, metrics)
        _notify_progress(progress_callback, "evaluating", 72, "样本不足以划分独立验证集，正在保存训练结果。", device=device)
        metrics["validation_mae"] = None
        metrics["validation_rmse"] = None
        metrics["validation_r2"] = None
        metrics["baseline_mae"] = None
        metrics["train_samples"] = len(x)
        metrics["validation_samples"] = 0
    _notify_progress(
        progress_callback,
        "evaluation_done",
        80,
        "训练指标已计算，正在保存模型并准备更新热点排序。",
        metrics=metrics,
        device=device,
        samples=len(x),
    )
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
    _notify_progress(progress_callback, "scoring", 86, "正在把模型分应用到全部热点。", metrics=metrics, device=device)
    path = settings.model_dir / "topic_recommender.joblib"
    joblib.dump({"model": model, "features": FEATURE_NAMES}, path)
    score_metrics = _apply_model_scores(db, model, hardware=hardware)
    metrics.update(
        {
            "score_device": score_metrics["device"],
            "score_seconds": score_metrics["seconds"],
            "score_count": score_metrics["scored"],
            "score_gpu_fallback": score_metrics["gpu_fallback"],
        }
    )
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
    _notify_progress(progress_callback, "refreshing", 97, "模型已保存，正在刷新热点排序。", metrics=metrics, device=device)
    refresh_all_topic_scores(db)
    _notify_progress(progress_callback, "completed", 100, "训练完成，热点排序已更新。", metrics=metrics, device=device)
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


def _set_model_device(model, device: str) -> None:
    if not hasattr(model, "get_booster"):
        return
    model.get_booster().set_param({"device": "cuda" if device == "CUDA" else "cpu"})


def _predict_model_matrix(model, matrix: list[list[float]], device: str):
    """Predict once for a matrix and use a device-aware XGBoost path when possible."""
    if device == "CUDA" and hasattr(model, "get_booster"):
        booster = model.get_booster()
        if callable(getattr(booster, "predict", None)):
            try:
                import xgboost as xgb

                return booster.predict(xgb.DMatrix(matrix))
            except ImportError:
                pass
    return model.predict(matrix)


def _apply_model_scores(
    db: Session,
    model,
    topic_ids: set[int] | None = None,
    hardware: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if topic_ids is not None and not topic_ids:
        return {"scored": 0, "device": "NONE", "seconds": 0.0, "gpu_fallback": False}
    if topic_ids is None:
        topics = list(db.scalars(select(Topic)))
    else:
        topics = []
        topic_id_list = list(topic_ids)
        for start in range(0, len(topic_id_list), 500):
            batch_ids = topic_id_list[start : start + 500]
            topics.extend(db.scalars(select(Topic).where(Topic.id.in_(batch_ids))))
    if not topics:
        return {"scored": 0, "device": "NONE", "seconds": 0.0, "gpu_fallback": False}

    hardware = hardware or gpu_status()
    use_cuda = bool(hardware.get("cuda_ready") and hasattr(model, "get_booster"))
    device = "CUDA" if use_cuda else "CPU"
    matrix = _feature_matrix(db, topics)
    started = time.perf_counter()
    fallback = False
    try:
        _set_model_device(model, device)
        predictions = _predict_model_matrix(model, matrix, device)
    except Exception as exc:
        if device != "CUDA":
            raise
        logger.exception("CUDA recommendation scoring failed; falling back to CPU")
        _set_model_device(model, "CPU")
        predictions = _predict_model_matrix(model, matrix, "CPU")
        device = "CPU"
        fallback = True
        logger.warning("CUDA recommendation scoring fallback: %s", exc)
    for topic, prediction in zip(topics, predictions, strict=True):
        topic.model_score = clamp(float(prediction))
    return {
        "scored": len(topics),
        "device": device,
        "seconds": round(time.perf_counter() - started, 4),
        "gpu_fallback": fallback,
    }


def apply_saved_model_scores(
    db: Session,
    topic_ids: set[int] | None = None,
    hardware: dict[str, Any] | None = None,
) -> dict[str, Any]:
    model = _load_model(db)
    if model is None:
        return {"available": False, "scored": 0, "device": "NONE", "seconds": 0.0, "gpu_fallback": False}
    result = _apply_model_scores(db, model, topic_ids=topic_ids, hardware=hardware)
    db.commit()
    result["available"] = True
    return result


def apply_saved_model(db: Session, topic_ids: set[int] | None = None) -> bool:
    result = apply_saved_model_scores(db, topic_ids=topic_ids)
    if not result["available"]:
        return False
    refresh_topic_score_mix(db, topic_ids)
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
    latest_metrics = artifact.metrics_json or {} if artifact else {}
    hardware = gpu_status()
    model_score_device = latest_metrics.get("score_device")
    if not model_score_device and artifact and latest_metrics.get("device") == "CUDA" and hardware.get("cuda_ready"):
        model_score_device = "CUDA（可用）"
    model_score_device = model_score_device or "CPU（规则评分）"
    return {
        "trained": artifact is not None and Path(artifact.path).exists(),
        "sample_count": sample_count,
        "artifact": artifact,
        "feature_names": FEATURE_NAMES,
        "feature_definitions": [{"name": name, "label": FEATURE_LABELS.get(name, name)} for name in FEATURE_NAMES],
        "hardware": hardware,
        "model_score_device": model_score_device,
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
