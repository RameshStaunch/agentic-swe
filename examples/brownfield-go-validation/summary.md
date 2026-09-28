# Engineering summary

**Requirement:** Reject todos with an empty or overlong title
**Status:** ready_for_review (tests passing, validator says **approve**)

## Plan

Added title validation to POST /todos (trim, 1-200 characters, JSON 400 errors, 64 KiB body cap), then tests and docs in parallel.

**Why this plan:** Impact analysis showed the handler passed input straight to the store with an unbounded body read, so validation and the size cap went in the handler as a pure, testable function.

## Understanding

Make POST /todos reject titles that are empty (after trimming whitespace) or longer than a sensible limit, with a clear 400 error, instead of storing them.

**Acceptance criteria**

- Empty and whitespace-only titles return 400 and store nothing.
- Titles over 200 characters return 400; exactly 200 is accepted, including multi-byte characters.
- Valid titles are stored trimmed and return 201 as before.
- Malformed JSON still returns 400, now with a JSON error body.
- go test ./... passes, including the existing test.

**Ambiguities found**

- none

**Clarifications used**

- none

## Impact analysis

The service is one package: todo.go holds the Store and an http.ServeMux with POST /todos and GET /todos. POST decodes the body and passes the title straight to Store.Add with no checks, and the decoder reads an unbounded body. Validation belongs in the handler, as a pure function that is easy to unit test; the Store and GET /todos are unaffected.

**Impacted files**

- todo.go
- validate_test.go (new)
- README.md

**Impacted APIs**

- POST /todos: new 400 responses with a JSON error body; titles are trimmed before storing

**Data-flow changes**

- Request body is capped with http.MaxBytesReader before decoding.
- Title is trimmed and validated before Store.Add.

**Risk notes**

- The error for malformed JSON changes from plain text to JSON; clients parsing the text body would notice.
- Rune count vs byte length matters for non-ASCII titles; utf8.RuneCountInString keeps the limit in characters.
- Existing todos created before the change may violate the new rule; they are left as they are.

## Tasks

One handler change carries the behaviour; tests and docs both depend on its final contract and can run in parallel after it.

| Task | Kind | Depends on | Result |
|---|---|---|---|
| validate-title | codegen | - | done |
| validation-tests | test | validate-title | done |
| docs | docs | validate-title | done |

## Artifacts

- `todo.go`
- `validate_test.go`
- `README.md`

Full diff: `changes.patch`. Test output: `tests.txt`. Every agent output: `steps/`.

## Risks

- Error body format change for malformed JSON.
- Malformed-JSON errors change from plain text to a JSON body.
- Previously accepted blank titles are now rejected; any client sending them will start seeing 400s.

## Trade-offs

- Trimmed titles are stored.
- Limit is a constant.
- Trim-and-store rather than rejecting padded titles: friendlier, but the stored value differs from what was sent.
- Fixed 200-character limit in code rather than configuration: simpler, but changing it needs a deploy.

## Failure scenarios and guardrails

- A client sends a 1 MB body: MaxBytesReader stops decoding at 64 KiB and returns 400 instead of buffering it.

- ValidateTitle is table-tested at the boundary, including multi-byte characters.
- HTTP tests assert rejected requests store nothing.

## Assumptions

- 200 characters is an acceptable limit.

## Limitations

- Existing invalid todos are not migrated.
