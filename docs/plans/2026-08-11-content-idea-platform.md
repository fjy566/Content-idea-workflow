# Content Idea Platform Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a real, single-user local platform that collects public RSS/HTML sources, clusters current topics, calculates recommendation scores, calls user-configured AI APIs for analysis/article/image generation, and trains a small recommendation model from user feedback.

**Architecture:** A modular FastAPI monolith serves Jinja2/HTMX/HTML/CSS pages and owns a SQLite database. An APScheduler background scheduler runs real source crawls and topic analysis. The local recommender starts with deterministic features and a scikit-learn model trained only from recorded user actions; no local LLM or demo content is loaded.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Jinja2, HTMX, SQLite, SQLAlchemy, APScheduler 3.x, httpx, feedparser, BeautifulSoup, optional Playwright, scikit-learn, joblib, pytest.

---

### Task 1: Project foundation

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `README.md`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/main.py`
- Create: `app/templates/base.html`
- Create: `app/static/app.css`

**Steps:**
1. Add pinned-compatible runtime dependencies and test dependencies.
2. Add settings for database path, crawl interval, request timeouts, AI endpoints, and local storage.
3. Add a FastAPI application factory with startup/shutdown hooks.
4. Add a clean empty-state UI that does not insert demo records.
5. Verify imports with `python -m compileall app`.

### Task 2: Persistence and source management

**Files:**
- Create: `app/db.py`
- Create: `app/models.py`
- Create: `app/repositories.py`
- Create: `app/routes/sources.py`
- Create: `app/templates/sources.html`
- Test: `tests/test_persistence.py`

**Steps:**
1. Create tables for sources, raw items, topics, source links, AI tasks, articles, images, settings, and feedback.
2. Enable SQLite WAL mode and foreign keys.
3. Implement CRUD for RSS and HTML sources, including CSS selector configuration.
4. Add source enable/disable and delete actions.
5. Add persistence tests using a temporary SQLite database.

### Task 3: Real crawl and topic pipeline

**Files:**
- Create: `app/crawler.py`
- Create: `app/topic_pipeline.py`
- Create: `app/routes/dashboard.py`
- Create: `app/templates/dashboard.html`
- Create: `app/templates/partials/topic_card.html`
- Test: `tests/test_crawler.py`
- Test: `tests/test_topic_pipeline.py`

**Steps:**
1. Implement RSS parsing with feedparser and HTML parsing with BeautifulSoup.
2. Add request timeout, retry, user-agent, response-size, and content-hash safeguards.
3. Normalize URLs and deduplicate source items.
4. Cluster recent items using Chinese-aware tokenization and title similarity.
5. Calculate a transparent baseline recommendation score from freshness, source count, growth, content quality, conflict/risk signals, and user keywords.
6. Display real stored topics with source links and empty states.

### Task 4: User API integrations

**Files:**
- Create: `app/ai_provider.py`
- Create: `app/routes/settings.py`
- Create: `app/routes/topics.py`
- Create: `app/templates/settings.html`
- Create: `app/templates/topic.html`
- Create: `app/templates/article.html`
- Create: `app/templates/partials/analysis.html`
- Test: `tests/test_ai_provider.py`

**Steps:**
1. Store endpoint, model, and API key settings locally without logging secrets.
2. Implement generic OpenAI-compatible chat requests with JSON parsing and retries.
3. Implement structured topic analysis, article generation, and image generation response parsing.
4. Save AI task status, errors, model, and usage metadata.
5. Add manual actions for analyze, write article, generate image, and edit/save article.
6. Keep the platform usable without configured AI by showing a clear configuration state rather than fake output.

### Task 5: Feedback and recommender training

**Files:**
- Create: `app/recommender.py`
- Create: `app/routes/recommender.py`
- Create: `app/templates/recommender.html`
- Test: `tests/test_recommender.py`

**Steps:**
1. Record view, save, dismiss, generate, edit, and publish feedback.
2. Build numeric features from topic metadata and user preference keywords.
3. Train a small GradientBoostingRegressor when real feedback is sufficient.
4. Save the model artifact under `data/models/` and record model metadata.
5. Use the model only when trained; otherwise use the transparent baseline score.
6. Add a retrain page with sample count, last trained time, and validation status.

### Task 6: Scheduler, operational controls, and tests

**Files:**
- Create: `app/scheduler.py`
- Create: `app/routes/admin.py`
- Create: `app/templates/admin.html`
- Create: `run.ps1`
- Create: `pytest.ini`
- Modify: `README.md`

**Steps:**
1. Schedule enabled-source crawling and pending AI analysis.
2. Add manual crawl, task history, and failure visibility.
3. Add safe local filesystem handling for downloaded images.
4. Run unit tests, compile checks, and a clean application startup check.
5. Fix failures and document exact setup and run commands.

