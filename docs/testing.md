# Testing approach

There are two things to test: the **system** (does the orchestration behave correctly?) and the **code it produces** (does it work?).

## The system: `pixi run test`

`tests/test_orchestrator.py` drives the real orchestrator with replayed agent outputs, so it is deterministic, free, and needs no API key.

| Area | Test |
|---|---|
| Planning | DAG waves are layered correctly; unknown dependencies, cycles, and parallel tasks sharing a file are rejected |
| Guardrails | full-auto blocks path escapes, `.git`/`.env` writes and out-of-scope writes; auto-edit asks only about out-of-scope writes; suggest turns free-text answers into redo feedback; full-auto falls back to stated assumptions |
| Recovery | a planted bug makes tests fail, the fix step runs, tests go green, and the patch contains the fix |
| Failure propagation | a task whose agent keeps failing is retried once, then marked failed; its dependents are `blocked`; the run ends `needs_review` |
| Golden runs | each of the three committed examples replays against a fresh copy of its repo and must end `ready_for_review` with its generated tests passing |

The recovery test caught a real bug while building this: a fix that changed a file without changing its size, within the same second, was invisible to the test run because Python reused the stale `.pyc`. Test runs now use a fresh bytecode cache.

The greenfield golden run needs Postgres (`pixi run db-start`) and is skipped without it.

## The generated code

Every run executes the target repo's full pytest suite after the build, and again after each fix attempt. The generated suites for the examples:

- **URL shortener:** 3 unit tests (code shape, distinctness over 10k draws, alias rules) and 8 integration tests against real Postgres covering create, redirect, 404/409/410/422 paths and analytics.
- **Pagination:** 6 tests, including a full cursor walk that must visit each note exactly once and the tag-filter-before-paging case the old code would fail.
- **Make it faster:** a query-count regression test (via sqlite3's trace callback) that fails on the original N+1 code and passes after the change. It asserts query counts rather than wall time, so it is deterministic.

## What is not covered

- Live model behaviour. LLM output varies run to run, so live runs are checked structurally (schemas, graph validation, tests) rather than against fixed expectations. The next step is an eval set: a few dozen requirements with acceptance checks, run live on a schedule, tracking pass rate and cost.
- The HTTP API has a manual smoke test only (start `pixi run api`, POST a replay run, poll it).
