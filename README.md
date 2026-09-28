# agentic-swe

An agentic software engineering system: describe a requirement, and it returns a reviewable engineering outcome (a plan, code, tests, docs, a diff, and a risk review), with a human in control at the points you choose.

```
requirement ─▶ analyse ─▶ design / impact ─▶ plan (DAG) ─▶ build (parallel waves) ─▶ test ⟲ fix ─▶ validate ─▶ summary
                  │                              │                  │                                  │
     greenfield/brownfield?               approve plan?      approve each edit?                 escalate if still red
     ambiguities? ask
```

Seven [Pydantic AI](https://ai.pydantic.dev) agents with typed outputs, driven by one orchestrator, audited in Postgres. See [docs/architecture.md](docs/architecture.md).

## Setup

Requires [pixi](https://pixi.sh). Everything else, including Python, Postgres and `gh`, comes from conda-forge.

```bash
pixi install
pixi run db-init          # creates a local Postgres cluster in ./pgdata on port 55432 and starts it
```

After a reboot, `pixi run db-start` (and `pixi run db-stop` to stop it). Use `--no-db` on any run to skip Postgres.

For a live model, put its provider's key in `.env` (gitignored), for example `OPENROUTER_API_KEY=...`. No key is needed for the `mock` model or for replays.

## Quick start: interactive session

```bash
pixi run agentic-swe
```

A Claude Code style session:

1. **Pick a model.** The menu shows which provider keys are set. Option 1 is `mock` (offline), `c` takes any Pydantic AI `<provider>:<model>` string, and `r` replays the recorded examples.
2. **Describe what you want** in plain language.
3. **Pick a directory.** Enter for a new project (`work/project-<time>/`), or give a path. For an existing codebase it asks whether to work on a timestamped **copy** (default) or edit **in place**.
4. **The analyst classifies the request** as greenfield or brownfield from what is in the directory, and flags it as ambiguous if it could reasonably mean different things. For ambiguous requests it asks clarifying questions, and asks again if your answers leave things open (up to two follow-up rounds).
5. **Pick the autonomy mode** (once per session), then it plans, builds, tests, validates and writes a summary.

Commands between requests: `/model`, `/mode`, `/repo`, `/runs`, `/help`, `/exit`.

## Models

Any Pydantic AI model works: pass `--model <provider>:<model>` (or set `AGENTIC_SWE_MODEL`). The provider reads its own API key, so only the key for the provider you pick is needed:

| Model | Key |
|---|---|
| `openrouter:qwen/qwen3.8-27b:free` (default) | `OPENROUTER_API_KEY` |
| `google:gemini-flash-latest` | `GOOGLE_API_KEY` |
| `anthropic:claude-sonnet-5` | `ANTHROPIC_API_KEY` |
| `openai:gpt-5` | `OPENAI_API_KEY` |
| `mock` | none (offline, see below) |

A missing key or unknown model fails before the run starts. Rate-limit and overload errors (429/5xx) are retried with backoff and then reported in one line. Free tiers are often rate-limited: the Gemini free tier has no quota for Pro models, and OpenRouter `:free` models are frequently throttled upstream.

**`mock`** is an offline stand-in for an LLM, for testing. It answers each agent from the recorded examples, but unlike `--replay` it goes through the real Pydantic AI agent loop: output tools, schema validation and the decomposer's graph validator all run. It picks the example from words in your requirement ("faster"/"slow" → the ambiguous example, "pagination" → brownfield, anything else → the URL shortener), so it exercises the flow; it does not write code for arbitrary requests.

## Scripted runs

```bash
pixi run agentic-swe run "Add rate limiting to note creation" --from seed_repo --mode auto-edit
pixi run agentic-swe run "Make the notes API faster" --from seed_repo --model mock
pixi run agentic-swe run "Build a scalable URL shortener service with APIs, persistence, and analytics." --mode full-auto
```

**Where the agents work:**

| Flag | Behaviour |
|---|---|
| `--from <dir>` | Copy the codebase to a new `work/<name>-<time>/` and work on the copy. The original is never touched, and earlier attempts stay side by side. |
| `--repo <dir>` | Edit that directory in place (created if missing). |
| neither | Create a new `work/project-<time>/`. |

`--scope greenfield|brownfield` is optional; by default the analyst decides. Pre-answer clarifying questions with `--answer "..."` (repeatable).

Every run writes `runs/<timestamp>-<slug>/` with `summary.md`, `changes.patch`, `tests.txt`, `transcript.txt` and `steps/*.json` (every agent output, which is also what `--replay` reads). Pass `--out examples/<name>` to record a new example.

Other commands: `pixi run agentic-swe runs` (recent runs from the audit trail), `pixi run agentic-swe show <id>` (a run's full event log).

## Demos (no API key needed)

Each demo replays a recorded run on a fresh timestamped copy: the agent outputs come from `examples/<name>/steps/`, but everything else is real. The orchestrator applies the files through the approval gate, runs the test suite, computes the diff, and writes the audit trail.

```bash
pixi run demo-shortener     # greenfield, full-auto:  URL shortener in a new work/project-<time>/
pixi run demo-brownfield    # brownfield, suggest:    pagination on a copy of the seed notes service (approve each edit)
pixi run demo-ambiguous     # ambiguous,  auto-edit:  "make the notes API faster" (asks 3 questions first)
```

| Example | What it shows | Output |
|---|---|---|
| Greenfield | Architecture + API contract, 6 tasks in 3 parallel waves, 11 unit/integration tests on Postgres | [summary](examples/greenfield-url-shortener/summary.md) · [transcript](examples/greenfield-url-shortener/transcript.txt) · [code](url_shortener/) |
| Brownfield | Impact analysis finds that the tag filter runs in Python, so naive paging would be wrong; fixes both | [summary](examples/brownfield-pagination/summary.md) · [transcript](examples/brownfield-pagination/transcript.txt) · [diff](examples/brownfield-pagination/changes.patch) |
| Ambiguous | Detects 3 ambiguities, pauses for answers, then fixes an N+1 with a query-count regression test | [summary](examples/ambiguous-make-it-faster/summary.md) · [transcript](examples/ambiguous-make-it-faster/transcript.txt) · [diff](examples/ambiguous-make-it-faster/changes.patch) |

> **How the recordings were made:** the agent outputs in `examples/*/steps/` were written by hand in the exact typed schemas the agents return. They are a faithful stand-in for model output, not a captured model run. Run the same requirement with a live `--model` and `--out examples/<name>` to record a live one.

## Controlled autonomy

Like Claude Code and Codex, you choose how much the agents do on their own:

| Mode | Asks about ambiguities | Approves plan | Approves each edit | Out-of-scope writes |
|---|---|---|---|---|
| `suggest` (default) | yes | yes | yes (shows diff; `n` skips, free text = feedback and redo) | ask |
| `auto-edit` | yes | yes | no | ask |
| `full-auto` | no, uses stated assumptions | no | no | rejected |

In every mode: the agents' read tools and all writes are confined to the working directory, `.git`/`.env`/`.pixi` are never written, each task may only write the files the plan declared for it, failing tests trigger up to 2 automatic fix attempts, and anything still failing escalates to the human (or is flagged `needs_review` in full-auto).

## HTTP API

```bash
pixi run api    # http://localhost:8000/docs
```

`POST /runs {requirement, repo, scope?, model?, answers?, replay?}` starts a full-auto run (a server has no terminal to approve at) and returns its id; `GET /runs` and `GET /runs/{id}` return status, the event log and the summary. `repo` and `replay` must be inside the workspace.

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
  agents.py        the seven agents, their repo tools (list/read/grep), and model loading
  mock.py          offline mock model
  orchestrator.py  pipeline, DAG waves, approval gate, test/fix loop, record/replay, summary rendering
  db.py            Postgres audit trail (runs, events)
  cli.py           scripted commands (run, runs, show) and working-directory handling
  session.py       interactive session
  api.py           HTTP API
seed_repo/         pre-existing notes service (brownfield target; runs work on copies)
url_shortener/     output of the greenfield example
examples/          the three recorded runs
work/, runs/       per-run working copies and outputs (gitignored)
```

## Repo tooling

Inside pixi, `gh` keeps its config in `.gh/` (gitignored, via `GH_CONFIG_DIR`), so this repo can use a different GitHub account from the system `gh` login. Log in with `pixi run gh auth login` and push with `pixi run git push`.
