from app.article_format import (
    article_image_queries,
    image_markdown,
    insert_image_markdown,
    insert_images_by_plan,
    insert_images_evenly,
    render_markdown,
    resolve_image_count,
)
from app.models import GeneratedImage
from app.routes.topics import _target_length


def _image(image_id: int, name: str, source_url: str | None = None) -> GeneratedImage:
    return GeneratedImage(
        id=image_id,
        article_id=1,
        file_path=f"images/{name}.jpg",
        prompt=f"配图 {name}",
        provider="test",
        source_url=source_url,
        attribution="作者：测试；许可：CC BY" if source_url else None,
    )


def test_custom_article_length_accepts_full_supported_range():
    assert _target_length("300") == 300
    assert _target_length("2350") == 2350
    assert _target_length("10000") == 10000


def test_custom_article_length_rejects_invalid_values():
    for value in ("299", "10001", "abc"):
        try:
            _target_length(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{value} should be rejected")


def test_auto_image_count_scales_with_length_and_manual_count_is_capped():
    assert resolve_image_count("auto", 600) == 1
    assert resolve_image_count("auto", 1800) == 3
    assert resolve_image_count("auto", 5000) == 4
    assert resolve_image_count("99", 1200) == 6


def test_image_queries_use_headings_and_fill_requested_count():
    queries = article_image_queries("总标题", "# 总标题\n\n## 芯片产业\n\n正文\n\n## 人工智能\n\n正文", "", 4)
    assert queries == ["芯片产业", "人工智能", "总标题", "芯片产业"]


def test_multiple_images_are_inserted_into_markdown_without_duplicates():
    first = _image(1, "first", "https://commons.wikimedia.org/wiki/File:First.jpg")
    second = _image(2, "second")
    content = "# 标题\n\n第一段。\n\n## 小标题\n\n第二段。\n\n第三段。"

    inserted = insert_images_evenly(content, [first, second])
    inserted_again = insert_images_evenly(inserted, [first, second])

    assert inserted.count("/media/images/first.jpg") == 1
    assert inserted.count("/media/images/second.jpg") == 1
    assert "Wikimedia Commons" in inserted
    assert inserted_again == inserted


def test_user_can_insert_an_existing_image_at_a_chosen_position():
    image = _image(3, "chosen")
    content = "# 标题\n\n第一段。\n\n第二段。"
    result = insert_image_markdown(content, image, "after_first")
    assert result.index(image_markdown(image)) > result.index("第一段。")
    assert result.index(image_markdown(image)) < result.index("第二段。")


def test_model_image_plan_resolves_heading_and_falls_back_without_duplicates():
    first = _image(4, "first")
    second = _image(5, "second")
    content = "# 标题\n\n导语。\n\n## 背景\n\n背景段落。\n\n## 影响\n\n影响段落。"
    result = insert_images_by_plan(
        content,
        [first, second],
        [
            {"image_index": 1, "after_heading": "背景", "paragraph_hint": "背景段落", "reason": "说明背景"},
            {"image_index": 2, "after_heading": "不存在的章节", "paragraph_hint": "", "reason": "无匹配"},
        ],
    )

    assert result.count("/media/images/first.jpg") == 1
    assert result.count("/media/images/second.jpg") == 1
    assert result.index("/media/images/first.jpg") > result.index("背景段落")


def test_markdown_preview_renders_images_but_not_raw_html_or_javascript_links():
    rendered = str(render_markdown("# 标题\n\n![图](/media/images/a.jpg)\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))"))
    assert "<h1>标题</h1>" in rendered
    assert '<img src="/media/images/a.jpg"' in rendered
    assert "<script>" not in rendered
    assert 'href="javascript:' not in rendered
