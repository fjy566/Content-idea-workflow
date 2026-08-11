# Structured Creation Flow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert AI topic suggestions into strict structured recommendation cards and let users generate an article from a selected card or a custom idea.

**Architecture:** Keep the existing JSON database field but store normalized recommendation objects instead of display-formatted strings. Add a compatibility normalizer for old string data, centralize analysis persistence, and pass an explicitly resolved writing angle plus preset article options into the generation prompt.

**Tech Stack:** FastAPI, SQLAlchemy JSON, Jinja2, plain HTML/CSS, DeepSeek/OpenAI-compatible chat API, pytest.

---

### Task 1: Strict recommendation schema

**Files:**
- Create: `app/topic_recommendations.py`
- Modify: `app/ai_provider.py`
- Modify: `app/models.py`
- Test: `tests/test_topic_recommendations.py`

1. Define normalization for `title`, `approach`, and `reader_value`.
2. Strip Markdown decoration and numbering from legacy strings.
3. Require exactly usable fields and return at most five recommendations.
4. Update the AI prompt to request five JSON objects with no Markdown.

### Task 2: Centralize analysis persistence

**Files:**
- Modify: `app/routes/topics.py`
- Modify: `app/scheduler.py`

1. Save normalized recommendation objects in both manual and scheduled analysis paths.
2. Normalize legacy records when rendering without requiring a migration.
3. Pass normalized recommendations to the detail template.

### Task 3: Recommendation-driven article generation

**Files:**
- Modify: `app/routes/topics.py`
- Modify: `app/ai_provider.py`
- Modify: `app/templates/topic.html`
- Test: `tests/test_article_choices.py`

1. Render recommendation cards as radio options.
2. Add a custom-topic option and server-side validation.
3. Replace free-form article type, style, and length with safe presets.
4. Include the selected angle explicitly in the article-generation prompt.
5. Return clear errors to the detail page instead of generating the wrong article.

### Task 4: UX and end-to-end verification

**Files:**
- Modify: `app/static/app.css`
- Modify: `tests/test_topic_ui.py`

1. Style readable recommendation cards and writing controls.
2. Cover empty, legacy, validation-error, loading, and configured states.
3. Run the full test and compile suite.
4. Restart the app, regenerate one real topic analysis, choose a recommendation, and verify article creation.
