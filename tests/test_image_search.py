from dataclasses import replace

import httpx
import respx

import app.image_search as image_search


def test_chinese_technology_query_has_relevant_english_fallbacks():
    candidates = image_search._query_candidates("人工智能 芯片")
    assert "artificial intelligence" in candidates
    assert "semiconductor chip" in candidates


@respx.mock
def test_360_search_downloads_a_real_raster_result_and_keeps_source(tmp_path, monkeypatch):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    monkeypatch.setattr(image_search, "settings", replace(image_search.settings, data_dir=tmp_path))
    respx.get(image_search.QIHOO_IMAGE_API).mock(return_value=httpx.Response(200, json={
        "total": 1,
        "list": [{
            "title": "国产 AI 芯片",
            "thumb": "https://cdn.example.com/chip.jpg",
            "link": "https://news.example.com/chip",
        }],
    }))
    respx.get("https://cdn.example.com/chip.jpg").mock(
        return_value=httpx.Response(200, content=b"jpeg-data", headers={"content-type": "image/jpeg"})
    )

    result = image_search.search_360_image("国产 AI 芯片")

    assert result.provider == "360 图片（中国搜索）"
    assert result.source_url == "https://news.example.com/chip"
    assert result.file_path.read_bytes() == b"jpeg-data"


def test_china_search_does_not_silently_switch_to_a_foreign_source(monkeypatch):
    monkeypatch.setattr(image_search, "search_360_image", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("中国搜索被限制")))

    try:
        image_search.search_image("科技")
    except ValueError as exc:
        assert "中国搜索被限制" in str(exc)
    else:
        raise AssertionError("Chinese image search failures must remain visible")


@respx.mock
def test_commons_search_skips_an_already_used_source(tmp_path, monkeypatch):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    monkeypatch.setattr(image_search, "settings", replace(image_search.settings, data_dir=tmp_path))
    first_url = "https://commons.wikimedia.org/wiki/File:First.jpg"
    second_url = "https://commons.wikimedia.org/wiki/File:Second.jpg"
    respx.get(image_search.COMMONS_API).mock(return_value=httpx.Response(200, json={"query": {"pages": [
        {"title": "File:First.jpg", "imageinfo": [{"mime": "image/jpeg", "thumburl": "https://upload.wikimedia.org/first.jpg", "descriptionurl": first_url}]},
        {"title": "File:Second.jpg", "imageinfo": [{"mime": "image/jpeg", "thumburl": "https://upload.wikimedia.org/second.jpg", "descriptionurl": second_url}]},
    ]}}))
    respx.get("https://upload.wikimedia.org/second.jpg").mock(return_value=httpx.Response(200, content=b"second", headers={"content-type": "image/jpeg"}))

    result = image_search.search_commons_image("芯片", {first_url})

    assert result.source_url == second_url
    assert result.file_path.read_bytes() == b"second"


@respx.mock
def test_commons_search_downloads_real_response_and_keeps_attribution(tmp_path, monkeypatch):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    monkeypatch.setattr(image_search, "settings", replace(image_search.settings, data_dir=tmp_path))
    respx.get(image_search.COMMONS_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "query": {
                    "pages": [{
                        "title": "File:Chip.jpg",
                        "imageinfo": [{
                            "mime": "image/jpeg",
                            "thumburl": "https://upload.wikimedia.org/chip.jpg",
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Chip.jpg",
                            "extmetadata": {
                                "Artist": {"value": "<b>真实作者</b>"},
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            },
                        }],
                    }]
                }
            },
        )
    )
    respx.get("https://upload.wikimedia.org/chip.jpg").mock(
        return_value=httpx.Response(200, content=b"jpeg-data", headers={"content-type": "image/jpeg"})
    )

    result = image_search.search_commons_image("人工智能芯片")

    assert result.file_path.exists()
    assert result.file_path.read_bytes() == b"jpeg-data"
    assert "真实作者" in result.attribution
    assert "CC BY-SA 4.0" in result.attribution
    assert result.source_url.endswith("File:Chip.jpg")
