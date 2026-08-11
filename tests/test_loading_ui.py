from pathlib import Path

def test_base_layout_contains_accessible_operation_overlay():
    source = Path("app/templates/base.html").read_text(encoding="utf-8")
    assert 'id="operation-overlay"' in source
    assert 'role="status"' in source
    assert "/static/form-loading.js?v=2" in source


def test_loading_script_preserves_named_submit_button_before_disabling():
    source = Path("app/static/form-loading.js").read_text(encoding="utf-8")
    mirror_index = source.index("mirror.name = submitter.name")
    disable_index = source.index("button.disabled = true")
    assert mirror_index < disable_index
    assert "form.dataset.submitting" in source
    assert "event.preventDefault()" in source
    assert "HTMLFormElement.prototype.submit.call(form)" in source
    assert 'submitter?.getAttribute("formmethod")' in source
    assert "submitter?.formMethod" not in source
    assert "}, 80)" in source


def test_article_form_explains_generation_and_prevents_repeat_clicks():
    source = Path("app/templates/topic.html").read_text(encoding="utf-8")
    assert 'data-loading-title="正在生成文章初稿"' in source
    assert "可能需要 20–120 秒" in source
    assert 'data-loading-text="初稿生成中…"' in source


def test_editor_exposes_human_friendly_formatting_and_draft_recovery():
    source = Path("app/templates/article.html").read_text(encoding="utf-8")
    script = Path("app/static/article-editor.js").read_text(encoding="utf-8")

    assert 'id="article-editor-form"' in source
    assert 'data-editor-command="bold"' in source
    assert 'data-editor-command="link"' in source
    assert 'data-view-mode="preview"' in source
    assert 'id="local-draft-banner"' in source
    assert 'data-autosave-url="/articles/{{ article.id }}/autosave"' in source
    assert 'data-editor-command="table"' in source
    assert 'data-editor-command="undo"' in source
    assert 'data-copy-image=' in source
    assert 'class="image-position-select"' in source
    assert "localStorage" in script
    assert "restore-local-draft" in script
    assert "refreshHeadingOptions" in script
    assert 'const before = editor.value.slice(0, start);' in script
    assert "const beforeGap" in script
    assert "let lastSelection" in script
    assert "requestAutosave" in script
    assert "setInterval(() => requestAutosave(\"定时\"), 30_000)" in script
    assert 'button.addEventListener("mousedown", (event) => event.preventDefault())' in script


def test_settings_exposes_chinese_image_search_provider():
    source = Path("app/templates/settings.html").read_text(encoding="utf-8")
    route = Path("app/routes/settings.py").read_text(encoding="utf-8")

    assert 'name="image_search_provider"' in source
    assert "360 图片（中国搜索）" in source
    assert "image_search_provider" in route


def test_recommender_exposes_device_choice_and_real_progress_polling():
    source = Path("app/templates/recommender.html").read_text(encoding="utf-8")
    script = Path("app/static/recommender.js").read_text(encoding="utf-8")
    route = Path("app/routes/recommender.py").read_text(encoding="utf-8")

    assert 'name="training_device"' in source
    assert 'value="cpu"' in source
    assert 'value="cuda"' in source
    assert 'id="training-progress-bar"' in source
    assert 'data-status-url="/recommender/training/{{ training_run.id }}/status"' in source
    assert 'src="/static/recommender.js?v=1"' in source
    assert "setInterval(poll, 1000)" in script
    assert "fetch(statusUrl" in script
    assert 'training_device: str = Form("auto")' in route
    assert 'router.get("/training/{run_id}/status"' in route
