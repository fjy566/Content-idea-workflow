# Topic Control and Classification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add understandable topic categories, per-run source selection, and API model discovery with a selectable model list.

**Architecture:** Classify topics at read time with a deterministic local Chinese keyword classifier, avoiding schema migrations and API cost. Extend the crawler scheduler with an optional source-id filter. Add server-rendered model discovery that derives an OpenAI-compatible `/models` endpoint, validates the URL, calls it with the user-authorized key, and renders a select list with manual fallback.

**Tech Stack:** FastAPI, SQLAlchemy, Jinja2, httpx, plain HTML/CSS, pytest, respx.

---

### Task 1: Topic categories

**Files:**
- Create: `app/categories.py`
- Modify: `app/routes/dashboard.py`
- Modify: `app/routes/topics.py`
- Modify: `app/templates/dashboard.html`
- Modify: `app/templates/partials/topic_card.html`
- Modify: `app/templates/topic.html`
- Test: `tests/test_categories.py`

1. Define stable categories and keyword rules, with technology taking priority.
2. Classify current topics without changing the database.
3. Add category counts and filter links to the dashboard.
4. Show a category badge in each compact row and in topic details.
5. Test Chinese and English technology, finance, social, and international examples.

### Task 2: Select sources for each crawl

**Files:**
- Modify: `app/scheduler.py`
- Modify: `app/routes/admin.py`
- Modify: `app/templates/admin.html`
- Test: `tests/test_scheduler_selection.py`

1. Accept an optional set of source IDs in the crawl cycle.
2. Keep disabled sources excluded from both manual and scheduled crawling.
3. Render enabled sources as checked checkboxes and disabled sources as unavailable.
4. Reject an empty manual selection with a clear message.
5. Verify only selected sources are crawled.

### Task 3: Fetch and select API models

**Files:**
- Modify: `app/ai_provider.py`
- Modify: `app/routes/settings.py`
- Modify: `app/templates/settings.html`
- Test: `tests/test_model_discovery.py`

1. Derive a compatible models endpoint from a chat or image endpoint.
2. Validate the models URL and call it with the supplied or stored API key.
3. Parse OpenAI-compatible model-list responses and normalize unique IDs.
4. Render a dropdown after a successful fetch, preserving unsaved settings.
5. Keep a manual model-name fallback for incompatible providers.

### Task 4: UX and verification

**Files:**
- Modify: `app/static/app.css`

1. Style category pills, source selection controls, and model discovery states.
2. Cover keyboard focus, empty states, errors, and responsive layout.
3. Run the full pytest suite and Python compile check.
4. Restart the local server and verify all three workflows in the browser.
