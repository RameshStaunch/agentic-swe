# Engineering summary

**Requirement:** Add pagination to the notes list endpoint
**Status:** ready_for_review (tests passing, validator says **approve**)

## Plan

Changed the list handler to keyset pagination with the tag filter in SQL, then added tests and docs in parallel.

**Why this plan:** Impact analysis showed the existing in-Python tag filter would make naive LIMIT paging return short or empty pages, so the filter moved into the query as part of the same change.

## Understanding

Make GET /notes return results in bounded pages that a client can walk through completely, without breaking existing clients.

**Acceptance criteria**

- GET /notes returns at most `limit` notes (default 50, max 200).
- Following X-Next-Cursor visits every note exactly once, in id order.
- The tag filter is applied before paging, so pages are full when enough matching notes exist.
- Invalid limit or cursor values return 422.
- Existing tests still pass.

**Ambiguities found**

- none

**Clarifications used**

- none

## Impact analysis

GET /notes in notes/app.py loads every note, then filters by tag in Python. Paging on top of that would be wrong: a page of 50 could come back with fewer matching notes, or none. The tag filter has to move into SQL before LIMIT is applied. No other endpoint or the schema needs to change.

**Impacted files**

- notes/app.py
- README.md
- tests/test_pagination.py (new)

**Impacted APIs**

- GET /notes: new `limit` and `after` query params, new `X-Next-Cursor` response header

**Data-flow changes**

- Tag filtering moves from Python post-processing into the SQL WHERE clause.
- Rows are read with keyset pagination (id > cursor ORDER BY id LIMIT n+1) instead of a full table read.

**Risk notes**

- Clients that relied on GET /notes returning everything now get the first 50 unless they follow the cursor.
- Keeping the response a bare list avoids breaking existing clients, so the cursor has to travel in a header.
- Per-note tag lookups (N+1) remain; out of scope for this change but noted.

## Tasks

One code change in the list handler; tests and docs both depend on its final interface and can run in parallel after it.

| Task | Kind | Depends on | Result |
|---|---|---|---|
| paginate-list | codegen | - | done |
| pagination-tests | test | paginate-list | done |
| docs | docs | paginate-list | done |

## Artifacts

- `notes/app.py`
- `tests/test_pagination.py`
- `README.md`

Full diff: `changes.patch`. Test output: `tests.txt`. Every agent output: `steps/`.

## Risks

- Unbounded-list callers silently get the first page only.
- Behaviour change: callers expecting every note in one response now get 50.
- Clients that ignore response headers cannot discover further pages.

## Trade-offs

- No random page access.
- Cursor in a header to keep the body shape.
- Keyset over offset: stable under concurrent writes and O(page) per request, but no 'jump to page N'.
- Header cursor over an envelope body: backward compatible, less discoverable.

## Failure scenarios and guardrails

- A client passes a cursor from a deleted note: still works, since the cursor is just an id bound.

- limit is bounded to 1..200 and after must be >= 0 (422 otherwise).
- Queries are parameterised.
- Existing tests plus 6 new ones pass.

## Assumptions

- Ordering by id (creation order) is acceptable.

## Limitations

- The per-note tag lookup (N+1) is unchanged.
