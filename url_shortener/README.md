# url-shortener

FastAPI + Postgres URL shortener with click analytics.

## API

| Method | Path | Body / query | Response |
|---|---|---|---|
| `POST` | `/links` | `{"url": "https://…", "alias"?: "promo", "expires_at"?: ISO-8601}` | `201 {code, short_url, target_url, expires_at}`; `409` alias taken; `422` invalid url/alias/expiry |
| `GET` | `/{code}` | | `307` redirect to target; `404` unknown; `410` expired |
| `GET` | `/links/{code}/stats` | | `200 {code, target_url, total_clicks, clicks_by_day[], top_referrers[]}` |
| `GET` | `/healthz` | | `200 {ok: true}` |

OpenAPI docs are served at `/docs`.

## Data model

- `links(id, code UNIQUE, target_url, created_at, expires_at)`
- `clicks(id, link_id → links, at, referrer, user_agent)`, indexed on `(link_id, at)`

## Design notes

- **Codes** are 7 random base62 characters from `secrets` (≈3.5 trillion values). Uniqueness is enforced by the database; on the rare collision the insert is retried up to 5 times. Random codes are not enumerable, unlike sequential ids.
- **Redirects are 307**, not 301, so browsers don't cache them and every click reaches the analytics path. The trade-off is one extra round trip for repeat visitors.
- **Clicks are written after the response is sent** (background task), keeping the redirect path to a single indexed read. A crash between response and write loses that click; a queue (Kafka/SQS) would fix this at higher volume.
- **Scaling path**: the app is stateless, so it scales horizontally behind a load balancer. Next steps would be a read-through cache (Redis) for hot codes, batching click inserts, and time-partitioning `clicks`.

## Run

```bash
export SHORTENER_DATABASE_URL=postgresql+asyncpg://localhost:55432/url_shortener  # default
uvicorn shortener.app:app
pytest    # integration tests need the Postgres above
```
