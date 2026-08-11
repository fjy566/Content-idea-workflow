# ADR 0003: Validate External Network Boundaries and Expose Images Only

- Status: accepted
- Date: 2026-08-12

## Context

The application is a single-user local tool, but it fetches user-selected RSS/HTML sources, model APIs, and image URLs. Those responses and redirect targets are untrusted. The previous static mount also covered the whole data directory, which could expose the SQLite database and recommender artifacts.

## Decision

1. All external HTTP clients use a shared `safe_request` helper. Automatic redirects are disabled. GET redirects are followed manually only after every target passes public-URL validation; POST redirects fail closed.
2. Dynamic Playwright pages validate the initial page and abort requests to invalid HTTP(S) targets.
3. The application mounts only `data/images` at `/media/images`; database and model files remain outside the static file surface.
4. Downloaded/generated images must have both an allowed response MIME type and a matching JPG, PNG, or WebP file signature.
5. DNS resolution checks remain opt-in through `STRICT_SSRF_DNS=1` because some local proxy/DNS environments intentionally return synthetic private addresses for public domains. Literal private addresses and blocked hostnames are always rejected.

## Consequences

- A changed or redirected external provider fails visibly instead of silently reaching a private host.
- Image and model artifacts remain local files and are not downloadable through the web app except for images deliberately stored under `data/images`.
- Environments requiring DNS rebinding protection must enable the strict setting and verify their DNS behavior.
- This remains intentionally lightweight for the local single-machine deployment; authentication and multi-user authorization are outside the current product boundary.
