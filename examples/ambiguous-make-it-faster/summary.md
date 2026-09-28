# Engineering summary

**Requirement:** Make the notes API faster
**Status:** ready_for_review (tests passing, validator says **approve**)

## Plan

Asked three clarifying questions, then fixed the list endpoint's N+1 query pattern, moved tag filtering into SQL, indexed note_tags, and added a query-count regression test.

**Why this plan:** 'Faster' had no endpoint, target or constraints, so the run paused to ask rather than guess. With the answers (list endpoint, constant queries, no API changes) the fix was internal-only, and the test measures query count so it is a deterministic proxy for latency.

## Understanding

Reduce latency of the notes API, target and endpoints to be confirmed.

**Acceptance criteria**

- Listing notes performs a constant number of queries regardless of note count.
- Tag filtering does not load notes that do not match.
- The API contract is unchanged and existing tests pass.
- A test fails if the per-note query pattern is reintroduced.

**Ambiguities found**

- 'Faster' does not say which endpoint: create, list, fetch and delete have very different costs.
- No target is given (latency percentile, data size), so there is no way to know when the work is done.
- It is unclear whether API changes (e.g. pagination, dropping fields) are acceptable, or only internal changes.

**Clarifications used**

- Q: Which endpoint is slow, or is it all of them? A: The list endpoint. It gets slow once we have a few thousand notes.
- Q: What does 'fast enough' mean: a latency target at what data size? A: Listing should not get slower as notes grow; constant number of DB queries per request.
- Q: May the API contract change, or must it stay backward compatible? A: No breaking API changes, existing clients must keep working.

## Impact analysis

GET /notes runs one query for the notes and then one tag query per note (_tags inside a loop): 1+N queries. The tag filter also runs in Python after loading every note. note_tags has no indexes, so each per-note lookup is a full scan of note_tags, making listing O(N x T). Fetch and create are single-row and not the bottleneck.

**Impacted files**

- notes/app.py
- notes/db.py
- README.md
- tests/test_performance.py (new)

**Impacted APIs**

- GET /notes (internal change only; same request and response)

**Data-flow changes**

- Tags for a page of notes are loaded in one IN (...) query and grouped in memory.
- Tag filtering moves into SQL via a subquery on note_tags.
- New indexes on note_tags(note_id) and note_tags(tag).

**Risk notes**

- SQLite limits bound parameters per statement (32766 on modern builds); an IN list over every note could hit it with a very large table. Pagination is the long-term fix, but was ruled out by 'no API changes'.
- Tag order must stay insertion order, which the per-note query implicitly returned.
- Indexes are added with IF NOT EXISTS, so existing databases pick them up on next connect.

## Tasks

Indexes and the query rewrite touch different files and are independent, so they run in parallel. The regression test needs both; docs only need the new behaviour.

| Task | Kind | Depends on | Result |
|---|---|---|---|
| indexes | codegen | - | done |
| batch-tags | codegen | - | done |
| perf-tests | test | indexes, batch-tags | done |
| docs | docs | batch-tags | done |

## Artifacts

- `notes/db.py`
- `notes/app.py`
- `README.md`
- `tests/test_performance.py`

Full diff: `changes.patch`. Test output: `tests.txt`. Every agent output: `steps/`.

## Risks

- IN-list size limit on very large tables.
- Unbounded response size remains.
- Very large unpaginated lists can exceed SQLite's bound-parameter limit in the IN clause.
- Listing still returns every note; memory and payload size grow linearly.

## Trade-offs

- No pagination, per the constraint.
- Write cost of two indexes.
- Kept the response unpaginated to honour 'no API changes', leaving the biggest win (pagination) on the table.
- Two extra indexes slightly slow writes to note_tags in exchange for fast reads.

## Failure scenarios and guardrails

- A note table beyond ~32k rows makes the IN list fail on older SQLite builds; chunking the id list is the fix.

- Regression test fails if per-note queries return.
- Existing behaviour tests (tag filter, order) still pass.

## Assumptions

- The answers given apply; wall-clock latency was not measured, query count is the proxy.

## Limitations

- No benchmark against a realistic data set.
- Recommend pagination (see the brownfield-pagination example) as a follow-up.
