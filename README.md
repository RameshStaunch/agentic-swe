# agentic-swe

An agentic software engineering system: give it a requirement, and it returns a reviewable engineering outcome (a plan, code, tests, docs, a diff, and a risk review), with a human in control at the points you choose.

```
requirement ─▶ analyse ─▶ design / impact ─▶ plan (DAG) ─▶ build (parallel waves) ─▶ test ⟲ fix ─▶ validate ─▶ summary
                  │                              │                  │                                  │
            ambiguities? ask              approve plan?      approve each edit?                 escalate if still red
```

Seven [Pydantic AI](https://ai.pydantic.dev) agents with typed outputs, driven by one orchestrator, audited in Postgres. See [docs/architecture.md](docs/architecture.md).

## Setup

Requires [pixi](https://pixi.sh). Everything else, including Python and Postgres, comes from conda-forge.

```bash
pixi install
pixi run db-init          # creates a local Postgres cluster in ./pgdata on port 55432 and starts it
```

After a reboot, `pixi run db-start` (and `pixi run db-stop` to stop it).

For live runs, pick any Pydantic AI model with `--model <provider>:<model>` (or set `AGENTIC_SWE_MODEL`). The provider reads its own API key from the environment or `.env` (gitignored), so only the key for the provider you pick is needed:

| `--model` | Key |
|---|---|
| `google:gemini-flash-latest` (default) | `GOOGLE_API_KEY` |
| `anthropic:claude-sonnet-5` | `ANTHROPIC_API_KEY` |
| `openai:gpt-5` | `OPENAI_API_KEY` |

A missing key or unknown model fails before the run starts. Rate-limit and overload errors (429/5xx) are retried with backoff. The Gemini free tier has no quota for Pro models.

## Demos (no API key needed)

Each demo replays a recorded run: the agent outputs come from `examples/<name>/steps/`, but everything else is real. The orchestrator applies the files through the approval gate, runs the test suite, computes the diff, and writes the audit trail.

```bash
pixi run demo-shortener     # greenfield, full-auto:  URL shortener -> ./url_shortener
pixi run demo-brownfield    # brownfield, suggest:    pagination on the seed notes service (approve each edit)
pixi run demo-ambiguous     # ambiguous,  auto-edit:  "make the notes API faster" (asks 3 questions first)
```

| Example | What it shows | Output |
|---|---|---|
| Greenfield | Architecture + API contract, 6 tasks in 3 parallel waves, 11 unit/integration tests on Postgres | [summary](examples/greenfield-url-shortener/summary.md) · [transcript](examples/greenfield-url-shortener/transcript.txt) · [code](url_shortener/) |
| Brownfield | Impact analysis finds that the tag filter runs in Python, so naive paging would be wrong; fixes both | [summary](examples/brownfield-pagination/summary.md) · [transcript](examples/brownfield-pagination/transcript.txt) · [diff](examples/brownfield-pagination/changes.patch) |
| Ambiguous | Detects 3 ambiguities, pauses for answers, then fixes an N+1 with a query-count regression test | [summary](examples/ambiguous-make-it-faster/summary.md) · [transcript](examples/ambiguous-make-it-faster/transcript.txt) · [diff](examples/ambiguous-make-it-faster/changes.patch) |

> **How the recordings were made:** live calls were out of scope while building this, so the agent outputs in `examples/*/steps/` were written by hand in the exact typed schemas the agents return. They are a faithful stand-in for model output, not a captured model run. Run any demo without `--replay` (below) to record a live one; it writes the same files.

## Live runs

```bash
pixi run agentic-swe run "Build a scalable URL shortener service with APIs, persistence, and analytics." \
    --repo url_shortener --scope greenfield --mode suggest

pixi run agentic-swe run "Add rate limiting to note creation" --repo work/notes --scope brownfield --mode auto-edit
```

Every run writes `runs/<timestamp>-<slug>/` with `summary.md`, `changes.patch`, `tests.txt`, `transcript.txt` and `steps/*.json` (every agent output, which is also what `--replay` reads). Pass `--out examples/<name>` to record a new example.

Other commands: `pixi run agentic-swe runs` (recent runs from the audit trail), `pixi run agentic-swe show <id>` (a run's full event log). Use `--no-db` to run without Postgres.

## Controlled autonomy

Like Claude Code and Codex, you choose how much the agents do on their own:

| Mode | Asks about ambiguities | Approves plan | Approves each edit | Out-of-scope writes |
|---|---|---|---|---|
| `suggest` (default) | yes | yes | yes (shows diff; `n` skips, free text = feedback and redo) | ask |
| `auto-edit` | yes | yes | no | ask |
| `full-auto` | no, uses stated assumptions | no | no | rejected |

In every mode: writes outside the repo or to `.git`/`.env` are blocked, each task may only write the files the plan declared for it, failing tests trigger up to 2 automatic fix attempts, and anything still failing escalates to the human (or is flagged `needs_review` in full-auto). Pre-answer questions with `--answer "..."` (repeatable).

## HTTP API

```bash
pixi run api    # http://localhost:8000/docs
```

`POST /runs {requirement, repo, scope, answers?, replay?}` starts a full-auto run (a server has no terminal to approve at) and returns its id; `GET /runs` and `GET /runs/{id}` return status, the event log and the summary. Paths must be inside the workspace.

## CLI or UI?

**CLI.** The autonomy model is borrowed from Claude Code/Codex, which are terminal tools; a terminal shows the orchestration honestly (plan table, parallel waves, diffs, prompts at the gate); and with a few hours available, the effort went into the orchestration rather than UI chrome. The FastAPI service exposes the same orchestrator, so a web UI could sit on top of it without touching the core.

## Tests

```bash
pixi run test
```

See [docs/testing.md](docs/testing.md).

## Layout

```
src/agentic_swe/
  models.py        typed contracts between agents (requirement, task graph, design, impact, code change, validation, summary)
  agents.py        the seven agents and their repo tools (list/read/grep)
  orchestrator.py  pipeline, DAG waves, approval gate, test/fix loop, record/replay, summary rendering
  db.py            Postgres audit trail (runs, events)
  cli.py / api.py  front ends
seed_repo/         pre-existing notes service (brownfield target, kept pristine; demos copy it to work/)
url_shortener/     output of the greenfield demo
examples/          the three recorded runs
```
