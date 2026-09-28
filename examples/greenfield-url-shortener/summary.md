# Engineering summary

**Requirement:** Build a scalable URL shortener service with APIs, persistence, and analytics.
**Status:** ready_for_review (tests passing, validator says **approve**)

## Plan

Six tasks in three waves: schema and code generation in parallel, then the HTTP API and unit tests, then integration tests and docs. The service is a stateless FastAPI app over Postgres with create, redirect and stats endpoints.

**Why this plan:** Splitting persistence from code generation let independent work run concurrently and kept each task's file scope small, so the approval gate could enforce it. Tests were separate tasks so they were written against the finished interfaces rather than alongside them.

## Understanding

Build an HTTP service that turns long URLs into short codes, redirects short codes to their targets, persists links durably, and reports click analytics per link.

**Acceptance criteria**

- POST /links returns a short code for any valid http(s) URL and rejects other schemes.
- GET /{code} redirects to the target URL; unknown codes return 404 and expired links 410.
- Codes are unique, non-sequential and collision-safe under concurrent creation.
- Every redirect is recorded as a click with timestamp and referrer.
- GET /links/{code}/stats returns total clicks, clicks per day and top referrers.
- Links and clicks persist in Postgres; unit and integration tests pass.

**Ambiguities found**

- none

**Clarifications used**

- none

## Architecture

**Components**

- FastAPI app (stateless): link creation, redirect, stats endpoints
- Code generator: 7-char random base62 codes from `secrets`, alias validation
- Postgres: `links` and `clicks` tables, accessed via SQLAlchemy async + asyncpg
- Click recorder: FastAPI background task that inserts after the redirect is sent

**API contract**

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/links` | {"url": str, "alias"?: str, "expires_at"?: datetime} | 201 {code, short_url, target_url, expires_at} | 409 alias taken | 422 invalid |
| `GET` | `/{code}` | - | 307 Location: target | 404 | 410 expired |
| `GET` | `/links/{code}/stats` | - | 200 {code, target_url, total_clicks, clicks_by_day[{day, clicks}], top_referrers[[referrer, n]]} | 404 |
| `GET` | `/healthz` | - | 200 {"ok": true} |

**Data model**

```sql
CREATE TABLE links (
  id BIGSERIAL PRIMARY KEY,
  code VARCHAR(32) NOT NULL UNIQUE,
  target_url TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ
);
CREATE TABLE clicks (
  id BIGSERIAL PRIMARY KEY,
  link_id BIGINT NOT NULL REFERENCES links(id) ON DELETE CASCADE,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  referrer TEXT,
  user_agent TEXT
);
CREATE INDEX ix_clicks_link_at ON clicks (link_id, at);
```

**Key decisions**

- Random base62 codes instead of encoding the row id: not enumerable, no coordination between app instances; costs a unique-index check and a rare retry on collision.
- 307 instead of 301 redirects: every click reaches the server so analytics are complete; costs an extra round trip for repeat visitors.
- Clicks inserted in a background task after the response: redirect latency is one indexed read; a crash can lose an in-flight click. A queue is the upgrade path at high volume.
- Raw click rows rather than pre-aggregated counters: flexible queries and no hot-row contention; stats queries get slower as history grows, so partition `clicks` by time later.
- Postgres uniqueness is the source of truth for codes, so correctness holds with any number of app instances.

## Tasks

Persistence and code generation have no dependency on each other, so they are built in parallel; the API composes both. Unit tests only need the code generator, so they start as soon as it lands; integration tests and docs wait for the API.

| Task | Kind | Depends on | Result |
|---|---|---|---|
| persistence | codegen | - | done |
| codes | codegen | - | done |
| api | codegen | persistence, codes | done |
| unit-tests | test | codes | done |
| integration-tests | test | api | done |
| docs | docs | api | done |

## Artifacts

- `shortener/__init__.py`
- `shortener/db.py`
- `shortener/codes.py`
- `tests/test_codes.py`
- `shortener/app.py`
- `conftest.py`
- `tests/test_api.py`
- `README.md`

Full diff: `changes.patch`. Test output: `tests.txt`. Every agent output: `steps/`.

## Risks

- Stats cost grows with click history for very popular links.
- In-flight clicks can be lost on crash.
- No rate limiting or URL reputation checks yet.
- Stats queries scan all clicks for a link; a viral link with millions of clicks will make /stats slow.
- Background click writes are lost if the process dies between response and insert.
- No rate limiting on POST /links, so the service can be used to mass-create spam links.
- Open redirect: any http(s) target is accepted, which phishing campaigns can abuse.
- Schema is created with create_all at startup; there is no migration path yet.

## Trade-offs

- 307 redirects for complete analytics.
- Random codes for unguessability.
- Background click writes for fast redirects.
- 307 over 301: complete analytics at the cost of repeat-visit latency.
- Random codes over id-encoding: unguessable and coordination-free, at the cost of a unique-index write check.
- Raw click rows over counters: flexible reporting now, aggregation/partitioning needed later.

## Failure scenarios and guardrails

- Postgres unavailable: create and redirect both fail with 500; /healthz still answers, so it is a liveness check, not readiness.
- Code space pressure: collision retries exhaust after 5 attempts and return 503 (probability negligible below ~10^10 links).
- Clock skew between app instances slightly shifts expiry enforcement.

- Only http(s) URLs up to 2048 characters are accepted; javascript: and data: URLs are rejected with 422.
- Aliases are length- and charset-limited and cannot shadow API routes.
- Unique constraint on links.code makes duplicate codes impossible even under concurrent writers.
- 11 unit and integration tests cover the happy path and every error status.

## Assumptions

- Single Postgres, horizontally scaled app tier.
- Anonymous links, no auth.
- Analytics = counts per day and top referrers.

## Limitations

- No cache in front of redirects.
- No migrations (create_all).
- No abuse protection (rate limits, blocklists).
