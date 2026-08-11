import pytest

from app.topic_recommendations import normalize_recommendations, resolve_writing_angle


def test_normalizes_structured_recommendations_and_removes_markdown():
    result = normalize_recommendations([
        {"title": "**01 私域骗局**", "approach": "从封闭渠道切入", "reader_value": "帮助家庭防骗"}
    ])

    assert result == [{"title": "私域骗局", "approach": "从封闭渠道切入", "reader_value": "帮助家庭防骗"}]


def test_converts_legacy_pipe_string():
    result = normalize_recommendations(["**01** 私域骗局｜拆解营销话术｜帮助读者保护老人"])

    assert result[0]["title"] == "私域骗局"
    assert result[0]["approach"] == "拆解营销话术"
    assert result[0]["reader_value"] == "帮助读者保护老人"


def test_strict_count_rejects_incomplete_model_output():
    with pytest.raises(ValueError, match="需要 5 条"):
        normalize_recommendations(
            [{"title": "一个选题", "approach": "一个切入点", "reader_value": "一种价值"}],
            required_count=5,
        )


def test_resolves_selected_or_custom_writing_angle():
    recommendations = [{"title": "选题A", "approach": "切入点A", "reader_value": "价值A"}]

    assert "选题A" in resolve_writing_angle(recommendations, "recommendation:0", "")
    assert resolve_writing_angle(recommendations, "custom", "写给年轻人的防骗指南") == "自定义选题：写给年轻人的防骗指南"


def test_custom_choice_requires_content():
    with pytest.raises(ValueError, match="至少 4 个字"):
        resolve_writing_angle([], "custom", "")
