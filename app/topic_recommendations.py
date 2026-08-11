from __future__ import annotations

import re
from typing import Any

from .utils import clean_text


Recommendation = dict[str, str]

_DECORATION = re.compile(r"(?:\*\*|__|`|#{1,6})")
_LEADING_NUMBER = re.compile(r"^\s*(?:第\s*)?(?:\*\*)?\d{1,2}(?:\*\*)?\s*[.、:：)）-]*\s*")


def _plain(value: object, limit: int = 300) -> str:
    text = clean_text(value, limit)
    text = _DECORATION.sub("", text)
    text = _LEADING_NUMBER.sub("", text)
    return text.strip(" \t\r\n|｜-—:：")


def _from_mapping(value: dict[str, Any]) -> Recommendation | None:
    title = _plain(value.get("title") or value.get("topic") or value.get("headline"), 120)
    approach = _plain(value.get("approach") or value.get("angle") or value.get("entry_point"), 220)
    reader_value = _plain(value.get("reader_value") or value.get("value") or value.get("benefit"), 220)
    if not title or not approach or not reader_value:
        return None
    return {"title": title, "approach": approach, "reader_value": reader_value}


def _from_legacy_string(value: object) -> Recommendation | None:
    text = _plain(value, 800)
    parts = [_plain(part, 300) for part in re.split(r"[｜|]", text) if _plain(part, 300)]
    if len(parts) < 3:
        return None
    return {"title": parts[0][:120], "approach": parts[1][:220], "reader_value": "；".join(parts[2:])[:220]}


def normalize_recommendations(value: object, required_count: int | None = None) -> list[Recommendation]:
    if not isinstance(value, list):
        if required_count:
            raise ValueError("模型没有返回 recommendations 数组")
        return []
    result: list[Recommendation] = []
    for item in value:
        recommendation = _from_mapping(item) if isinstance(item, dict) else _from_legacy_string(item)
        if recommendation and recommendation not in result:
            result.append(recommendation)
        if len(result) == 5:
            break
    if required_count is not None and len(result) != required_count:
        raise ValueError(f"模型推荐格式不完整：需要 {required_count} 条，实际得到 {len(result)} 条")
    return result


def recommendation_text(recommendation: Recommendation) -> str:
    return (
        f"选题：{recommendation['title']}\n"
        f"切入点：{recommendation['approach']}\n"
        f"读者价值：{recommendation['reader_value']}"
    )


def resolve_writing_angle(recommendations: list[Recommendation], choice: str, custom_topic: str) -> str:
    if choice == "custom":
        custom = _plain(custom_topic, 500)
        if len(custom) < 4:
            raise ValueError("选择自定义选题时，请填写至少 4 个字的选题说明")
        return f"自定义选题：{custom}"
    if not choice.startswith("recommendation:"):
        raise ValueError("请选择一个 AI 推荐选题，或者选择自定义选题")
    try:
        index = int(choice.split(":", 1)[1])
        recommendation = recommendations[index]
    except (ValueError, IndexError) as exc:
        raise ValueError("选择的 AI 推荐选题不存在，请重新选择") from exc
    return recommendation_text(recommendation)
