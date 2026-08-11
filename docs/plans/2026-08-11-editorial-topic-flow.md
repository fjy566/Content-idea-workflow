# Editorial Topic Flow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the platform into a simple two-step workflow: scan compact topic rows, open one topic, then use the configured text API to generate actionable content angles.

**Architecture:** Keep the existing FastAPI and Jinja server-rendered application. Simplify the dashboard template and CSS, expose chat-provider readiness to the topic template, and retain the existing explicit POST action for paid API calls so merely browsing never consumes API quota.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Jinja2, plain HTML/CSS, pytest.

---

### Task 1: Define the compact dashboard

**Files:**
- Modify: `app/templates/dashboard.html`
- Modify: `app/templates/partials/topic_card.html`
- Modify: `app/static/app.css`

1. Replace the card grid with one compact vertical list.
2. Render only the title, a sanitized one-line excerpt, minimal metadata, and recommendation score.
3. Add hover and keyboard focus states and keep the entire topic row easy to open.
4. Verify long HTML summaries cannot expand the row.

### Task 2: Make the detail page a guided decision flow

**Files:**
- Modify: `app/routes/topics.py`
- Modify: `app/templates/topic.html`
- Modify: `app/static/app.css`

1. Pass text-API configuration state and model name to the template.
2. Structure the page as source facts, AI topic recommendations, article creation, and feedback.
3. Show a setup call-to-action when the API is missing; otherwise show one clear recommendation button.
4. Keep existing analysis results visible as recommendation cards and preserve source links.

### Task 3: Improve recommendation output

**Files:**
- Modify: `app/ai_provider.py`
- Modify: `app/routes/topics.py`

1. Ask the configured model for five concise, distinct Chinese content angles.
2. Explain expected reader value and avoid unsupported claims.
3. Avoid creating a failed AI task when configuration is absent.
4. Preserve the current JSON compatibility and saved topic fields.

### Task 4: Verify behavior

**Files:**
- Create: `tests/test_topic_ui.py`

1. Test compact list markup and one-line excerpt behavior.
2. Test configured and unconfigured detail states.
3. Run `pytest -q` and `python -m compileall -q app`.
4. Open the running application and visually verify dashboard and detail pages with real stored topics.
