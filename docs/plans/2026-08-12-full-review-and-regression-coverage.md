# Full Review and Regression Coverage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Review the complete local content-idea workflow, add regression coverage for every user-facing business flow and important failure path, fix verified defects, and publish a tested new version.

**Architecture:** Keep the current FastAPI + Jinja + SQLite monolith appropriate for single-machine use. Improve behavior at the existing service/repository boundaries instead of introducing distributed infrastructure, and test both pure business functions and the HTTP-visible form/redirect flows. Preserve real-only data behavior and never replace external integrations with demo data.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, SQLite, Jinja2, pytest, httpx/Starlette test clients, JavaScript syntax checks, optional XGBoost CPU/CUDA recommender.

**Status:** Completed on 2026-08-12. The repository now has 89 passing tests, the verified fixes are documented in the code review notes below, and the intended changes are ready for the release commit.

---

### Task 1: Establish the audit baseline and business-flow matrix

**Files:**
- Inspect: `app/main.py`, `app/routes/*.py`, `app/*.py`, `app/templates/*.html`, `app/static/*.js`
- Inspect: `tests/*.py`, `README.md`, `.env.example`, `pyproject.toml`
- Modify: `docs/plans/2026-08-12-full-review-and-regression-coverage.md`

**Step 1: Synchronize and inspect repository state**

Run `git fetch origin`, compare `main` with `origin/main`, and record any pre-existing worktree changes before editing.

**Step 2: Run the existing test suite**

Run `.venv\Scripts\python.exe -m pytest -q` and record failures, skipped tests, warnings, and missing optional dependencies.

**Step 3: Map business flows to tests**

Cover the following flows explicitly:

- application startup, health endpoint, database initialization, default source seeding
- source creation, update, enable/disable, deletion, URL validation, Chinese default catalog preservation
- one-round crawl selection, progress state, crawl logs, log clearing, duplicate handling and source failure isolation
- topic grouping, category filter, keyword search, pagination, topic detail and recommendation metrics
- explicit AI model discovery, API configuration, structured five-angle recommendation parsing and malformed-response fallback
- article angle selection/custom angle, length/style/type controls, article generation success/failure, task records and save status
- China image search, image relevance filtering, AI image generation, quantity handling, context queries, placement planning and fallback
- Markdown editor rendering, toolbar actions, current-cursor insertion, manual placement, autosave, local draft recovery and article deletion
- recommender sample metrics, feedback, model training on auto/CPU/CUDA, progress polling, artifact persistence and model-score refresh
- security boundaries: SSRF/URL validation, path traversal, untrusted feed/Markdown content, secret exposure, unsafe HTTP methods and malformed input

**Step 4: Write the audit findings before implementation**

For every finding, record the observable behavior, root cause, affected files, regression test, and smallest safe fix.

### Task 2: Strengthen test fixtures and HTTP-level coverage

**Files:**
- Inspect/Modify: `tests/conftest.py` or create it if no shared fixtures exist
- Create/Modify: `tests/test_app_routes.py`, `tests/test_business_regressions.py`, or the smallest existing test modules that fit each flow
- Test: all affected route and business modules

**Step 1: Add isolated SQLite and temporary-data fixtures**

Ensure tests use temporary databases and media/model directories, never the user’s live `data/` directory.

**Step 2: Add route-level form/redirect tests**

Assert status codes, redirect targets, preserved filters, user-visible notices, and that POST-only mutations are not accidentally reachable through GET.

**Step 3: Add failure-path tests**

Use mocked HTTP/API responses only at the network boundary to verify timeouts, malformed JSON, unsupported model responses, empty RSS feeds, invalid URLs, partial image failures, and failed training runs.

**Step 4: Add security regression tests**

Assert local/private/metadata URLs are rejected, stored media cleanup cannot escape the data directory, Markdown is sanitized, and secrets do not appear in rendered pages or error messages.

### Task 3: Implement fixes revealed by regression tests

**Files:**
- Modify only the affected `app/` modules and templates
- Update corresponding tests beside each fix

**Step 1: Fix one failing behavior at a time**

Keep route handlers thin, put reusable behavior in services/repositories, validate untrusted input at the boundary, and preserve transaction/error semantics.

**Step 2: Verify each fix immediately**

Run the focused test for the changed flow, then the related module tests before moving on.

**Step 3: Review data safety**

Confirm that failed external calls do not create false success records, partial operations report their actual result, and destructive operations remain explicit and scoped.

### Task 4: Complete verification and documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/plans/2026-08-12-full-review-and-regression-coverage.md`
- Modify: `docs/` audit notes if the review produces durable architecture/security decisions

**Step 1: Run the complete verification set**

Run `.venv\Scripts\python.exe -m pytest -q`, `.venv\Scripts\python.exe -m compileall -q app tests`, `node --check` for every project JavaScript file, and `git diff --check`.

**Step 2: Verify the running service**

Check `/health`, `/openapi.json`, the main dashboard, article library, settings, source management, admin crawl page and recommender page without mutating live data.

**Step 3: Update documentation**

Document the tested business flows, failure behavior, optional GPU dependency behavior, and the exact verification commands. Do not add demo data or claim unsupported coverage.

### Task 5: Commit and publish

**Files:**
- All intentional changes from Tasks 2–4

**Step 1: Review staged changes**

Run `git diff --check`, `git diff --stat`, and inspect the staged diff for secrets, generated files, accidental database changes, or unrelated edits.

**Step 2: Commit**

Use a descriptive commit message such as `test: complete business workflow review and regression coverage`.

**Step 3: Push and verify**

Run `git push origin main`, then verify `main` matches `origin/main` and the final worktree is clean.

## Review findings and completed fixes

- Replaced deprecated FastAPI startup/shutdown event handlers with a lifespan so application lifecycle tests are warning-free at the framework level.
- Added baseline security headers and narrowed the static media mount to `data/images`.
- Added redirect-aware external request handling, image signature validation, query relevance guards, and Playwright request guards.
- Prevented blank model responses and blank manual saves/autosaves from overwriting useful article data.
- Made multi-image search use distinct context queries and preserve requested order for fixed insertion positions.
- Made article deletion reference-aware so shared local images are removed only after their last article reference is deleted.
- Added HTTP-level regression coverage for health, media isolation, articles, sources, crawl selection/logs, settings/model discovery, topics, and recommender training submission/status.
