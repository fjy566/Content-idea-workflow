from __future__ import annotations

import re
from collections.abc import Sequence

from markdown_it import MarkdownIt
from markupsafe import Markup

from .models import GeneratedImage
from .utils import clean_text


_MARKDOWN = MarkdownIt("commonmark", {"html": False, "breaks": False})
_VALID_INSERT_POSITIONS = {"start", "after_first", "middle", "end"}


def render_markdown(value: str) -> Markup:
    """Render untrusted Markdown with raw HTML disabled and URL validation enabled."""
    return Markup(_MARKDOWN.render((value or "")[:200_000]))


def auto_image_count(target_length: int) -> int:
    if target_length <= 800:
        return 1
    if target_length <= 1400:
        return 2
    if target_length <= 2200:
        return 3
    return 4


def resolve_image_count(value: str, target_length: int) -> int:
    if value == "auto":
        return auto_image_count(target_length)
    try:
        return max(1, min(6, int(value)))
    except (TypeError, ValueError):
        return auto_image_count(target_length)


def article_image_queries(title: str, content: str, instruction: str, count: int) -> list[str]:
    headings = [
        clean_text(match.group(1), 160)
        for match in re.finditer(r"(?m)^#{2,3}\s+(.+?)\s*$", content or "")
        if clean_text(match.group(1), 160)
    ]
    seeds = [clean_text(instruction, 160), *headings, clean_text(title, 160)]
    unique = list(dict.fromkeys(seed for seed in seeds if len(seed) >= 2))
    if not unique:
        unique = ["中国 科技 新闻"]
    return [unique[index % len(unique)] for index in range(count)]


def _escape_markdown_text(value: str) -> str:
    return re.sub(r"([\\`*_[\]<>])", r"\\\1", clean_text(value, 800))


def image_markdown(image: GeneratedImage) -> str:
    alt = _escape_markdown_text(image.prompt or "文章配图")[:120] or "文章配图"
    block = f"![{alt}](/media/{image.file_path})"
    if image.source_url:
        attribution = _escape_markdown_text(image.attribution or "请在原始页面核对许可")
        source_label = "Wikimedia Commons" if "commons.wikimedia.org" in image.source_url else clean_text(image.provider or "图片来源", 120)
        block += f"\n\n*图片来源：[{_escape_markdown_text(source_label)}]({image.source_url})；{attribution}*"
    return block


def image_is_inserted(content: str, image: GeneratedImage) -> bool:
    return f"](/media/{image.file_path})" in (content or "")


def insert_image_markdown(content: str, image: GeneratedImage, position: str = "end") -> str:
    if image_is_inserted(content, image):
        return content
    if position not in _VALID_INSERT_POSITIONS:
        position = "end"
    blocks = [block.strip() for block in re.split(r"\n{2,}", (content or "").strip()) if block.strip()]
    image_block = image_markdown(image)
    if not blocks:
        return image_block
    if position == "start":
        index = 1 if blocks[0].startswith("#") else 0
    elif position == "after_first":
        index = next((idx + 1 for idx, block in enumerate(blocks) if not block.startswith("#")), 1)
    elif position == "middle":
        index = max(1, len(blocks) // 2)
    else:
        index = len(blocks)
    blocks.insert(min(index, len(blocks)), image_block)
    return "\n\n".join(blocks).strip()


def insert_images_evenly(content: str, images: Sequence[GeneratedImage]) -> str:
    pending = [image for image in images if not image_is_inserted(content, image)]
    if not pending:
        return content
    blocks = [block.strip() for block in re.split(r"\n{2,}", (content or "").strip()) if block.strip()]
    if not blocks:
        return "\n\n".join(image_markdown(image) for image in pending)
    insertions: dict[int, list[str]] = {}
    first_content = 1 if blocks[0].startswith("#") else 0
    available = max(1, len(blocks) - first_content)
    for index, image in enumerate(pending, start=1):
        after = first_content + round(available * index / (len(pending) + 1))
        after = max(first_content + 1, min(len(blocks), after))
        insertions.setdefault(after, []).append(image_markdown(image))
    result: list[str] = []
    for index, block in enumerate(blocks, start=1):
        result.append(block)
        result.extend(insertions.get(index, []))
    result.extend(insertions.get(len(blocks) + 1, []))
    return "\n\n".join(result).strip()


def _plain_heading(value: str) -> str:
    return re.sub(r"^#{1,6}\s+", "", clean_text(value, 200)).strip().lower()


def _planned_insert_index(blocks: list[str], placement: dict[str, object]) -> int | None:
    """Resolve an AI plan to a deterministic Markdown block boundary."""
    paragraph_hint = clean_text(placement.get("paragraph_hint"), 240).strip().lower()
    if paragraph_hint:
        for index, block in enumerate(blocks):
            if paragraph_hint in block.lower():
                return index + 1

    after_heading = _plain_heading(clean_text(placement.get("after_heading"), 200))
    if not after_heading:
        return None
    for index, block in enumerate(blocks):
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", block)
        if not heading_match or _plain_heading(heading_match.group(2)) != after_heading:
            continue
        level = len(heading_match.group(1))
        for next_index in range(index + 1, len(blocks)):
            next_heading = re.match(r"^(#{1,6})\s+", blocks[next_index])
            if next_heading and len(next_heading.group(1)) <= level:
                return next_index
        return len(blocks)
    return None


def insert_images_by_plan(
    content: str,
    images: Sequence[GeneratedImage],
    placements: Sequence[dict[str, object]],
) -> str:
    """Insert images at model-selected section boundaries with safe fallbacks.

    The model only proposes a heading or short paragraph hint.  This function
    performs the actual insertion locally, validates the index, prevents
    duplicates, and evenly places anything the plan cannot resolve.
    """
    blocks = [block.strip() for block in re.split(r"\n{2,}", (content or "").strip()) if block.strip()]
    if not blocks:
        return "\n\n".join(image_markdown(image) for image in images if not image_is_inserted(content, image)).strip()

    plan_by_index: dict[int, dict[str, object]] = {}
    for placement in placements:
        if not isinstance(placement, dict):
            continue
        try:
            image_index = int(placement.get("image_index", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= image_index <= len(images) and image_index not in plan_by_index:
            plan_by_index[image_index] = placement

    fallback: list[GeneratedImage] = []
    for image_index, image in enumerate(images, start=1):
        if image_is_inserted("\n\n".join(blocks), image):
            continue
        insertion_index = _planned_insert_index(blocks, plan_by_index.get(image_index, {}))
        if insertion_index is None:
            fallback.append(image)
            continue
        blocks.insert(min(max(0, insertion_index), len(blocks)), image_markdown(image))

    if fallback:
        return insert_images_evenly("\n\n".join(blocks).strip(), fallback)
    return "\n\n".join(blocks).strip()
