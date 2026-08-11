from app.utils import content_hash, image_extension_from_bytes, normalize_url, parse_csv_keywords, tokenize


def test_normalize_url_removes_tracking_parameters():
    assert normalize_url("https://Example.com/news?id=3&utm_source=x#top") == "https://example.com/news?id=3"


def test_tokenize_handles_chinese_terms():
    tokens = tokenize("消费维权和平台回应")
    assert "消费" in tokens or "消费维权" in tokens
    assert "回应" in tokens


def test_content_hash_is_stable():
    assert content_hash("a", "b") == content_hash("a", "b")
    assert content_hash("a", "b") != content_hash("b", "a")


def test_parse_csv_keywords_supports_chinese_commas():
    assert parse_csv_keywords("职场，消费, 科技") == ["职场", "消费", "科技"]


def test_image_signature_only_accepts_supported_raster_formats():
    assert image_extension_from_bytes(b"\xff\xd8\xffjpeg") == "jpg"
    assert image_extension_from_bytes(b"\x89PNG\r\n\x1a\npng") == "png"
    assert image_extension_from_bytes(b"RIFFxxxxWEBPdata") == "webp"
    assert image_extension_from_bytes(b"<html>not-an-image</html>") is None
