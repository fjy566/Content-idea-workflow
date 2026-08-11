from app.utils import content_hash, normalize_url, parse_csv_keywords, tokenize


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

