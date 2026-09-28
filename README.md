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

### Interactive examples

These are excerpts from real sessions using the offline `mock` model, so you can reproduce them without an API key. Full transcripts are in [`examples/sessions/`](examples/sessions/). Lines after `›`, `?`, `>` or a `(default)` hint are what was typed; an empty answer takes the default.

**An ambiguous request on an existing codebase** ([full transcript](examples/sessions/ambiguous-brownfield.txt)): it works on a copy of `seed_repo`, asks three questions before planning, then asks for plan approval (auto-edit mode).

```
› Make the notes API faster
Which directory should I work in? Enter for a new project (new) seed_repo
seed_repo has 6 files. Work on a timestamped copy or edit in place? (copy)
copied to work/seed_repo-20260928-214133; seed_repo is untouched
  1. suggest  approve the plan and every edit
  2. auto-edit  approve the plan; edits apply unless they leave a task's declared files
  3. full-auto  no prompts; review the summary at the end
Autonomy (1) 2
───────────────────────── 1 · Requirement analysis ─────────────────────────
? Which endpoint is slow, or is it all of them?
  enter = The list endpoint, since it is the only one whose cost grows with data size.
> The list endpoint, it gets slow with a few thousand notes
? What does 'fast enough' mean: a latency target at what data size?
  enter = Constant query count for listing, regardless of how many notes exist.
> Constant number of queries per request
? May the API contract change, or must it stay backward compatible?
  enter = No breaking API changes.
> No breaking API changes
Classified: brownfield · ambiguous, clarified · python
───────────────────────── 2 · Codebase impact analysis ─────────────────────
GET /notes runs one query for the notes and then one tag query per note ... 1+N queries.
───────────────────────── 3 · Task decomposition ───────────────────────────
execution waves: (indexes, batch-tags) → (perf-tests, docs)
Approve this plan?  y
───────────────────────── 4 · Build ────────────────────────────────────────
wave 1: indexes, batch-tags
  ✓ indexes notes/db.py
  ✓ batch-tags notes/app.py
wave 2: perf-tests, docs
  ✓ perf-tests tests/test_performance.py
  ✓ docs README.md
───────────────────────── 5 · Test & recover ───────────────────────────────
tests: passed
ready_for_review. Review runs/20260928-214133-make-the-notes-api-faster/summary.md and .../changes.patch
› /exit
```

**A new project** ([full transcript](examples/sessions/greenfield-setup.txt)): enter creates a fresh directory, the session asks how to set the project up, and in suggest mode every edit is shown as a diff and approved.

```
› Build a URL shortener with click analytics
Which directory should I work in? Enter for a new project (new)
new project in work/project-20260928-214136
Autonomy (1) 1
Classified: greenfield · clear · python
? New project: how should it be set up? Language, package manager, test framework, how tests are
run, any tools you want or want avoided.
  enter = python with its standard package manager and test framework
> Python 3.12, FastAPI, Postgres, pytest
Setup: Python 3.12, FastAPI, Postgres, pytest
...
execution waves: (persistence, codes) → (api, unit-tests) → (integration-tests, docs)
Approve this plan?  y
wave 1: persistence, codes
--- a/shortener/codes.py
+++ b/shortener/codes.py
@@ -0,0 +1,16 @@
+import re
+import secrets
...
Apply 1 file(s) for codes?  y
  ✓ codes shortener/codes.py
...
tests: passed
```

**A Go codebase when Go isn't installed** ([full transcript](examples/sessions/go-missing-toolchain.txt)): the session detects Go and asks how to run the tests instead of installing anything on its own.

```
› Reject todos with an empty or overlong title
Classified: brownfield · clear · go
...
go not installed. How should I run the tests?
  1. install go from conda-forge into an isolated pixi env under work/.toolchains/ (nothing global)
  2. run a command you give me, e.g. docker run --rm -v "$PWD":/w -w /w golang:1.22 go test ./...
  3. skip the tests
> 1
go toolchain: isolated pixi env (~/Documents/experiments/agentic-swe/work/.toolchains/go)
tests: passed
```

In suggest mode you can also answer an edit with free text instead of `y`/`n`. The coder then redoes that task with your text as review feedback. `/model` switches models mid-session; `/mode` changes the autonomy level for the next request.

## Models

Any Pydantic AI model works: pass `--model <provider>:<model>` (or set `AGENTIC_SWE_MODEL`). The provider reads its own API key, so only the key for the provider you pick is needed:

| Model | Key |
|---|---|
| `openrouter:qwen/qwen3.8-27b:free` (default) | `OPENROUTER_API_KEY` |
| `google:gemini-flash-latest` | `GOOGLE_API_KEY` |
| `anthropic:claude-sonnet-5` | `ANTHROPIC_API_KEY` |
| `openai:gpt-5` | `OPENAI_API_KEY` |
| `mock` | none (offline, see below) |

A missing key or unknown model fails before the run starts. Rate-limit and overload errors (429/5xx) are retried with backoff and then reported in one line.

**`mock`** is an offline stand-in for an LLM, for testing. It answers each agent from the recorded examples, but unlike `--replay` it goes through the real Pydantic AI agent loop: output tools, schema validation and the decomposer's graph validator all run. It picks the example from words in your requirement ("faster"/"slow" → the ambiguous example, "pagination" → brownfield, "todo"/"title" → the Go example, anything else → the URL shortener), so it exercises the flow; it does not write code for arbitrary requests.

## Languages, repo instructions and setup

The target codebase does not have to be Python. The language comes from the repository's marker files (`pyproject.toml`/`conftest.py`, `package.json`, `go.mod`) or, for a new project, from the analyst's structured `language` field.

- **`AGENTS.md` is honoured.** If the repository has one, its contents are added to every agent's prompt as repository instructions that override general conventions.
- **The repo says how it is tested.** The codebase reasoner reports the test command the repository itself declares (in `AGENTS.md`, CI config, a `Makefile`/`justfile`, `package.json` scripts or the README) as a structured field, and that command is what runs. Without one, the language default is used (`pytest`, `npm test`, `go test ./...`).
- **New projects ask you how to set them up**: language, package manager, test framework, how tests run, tools to use or avoid. The answer goes to every agent, and the architect returns the resulting test command. Full-auto uses the language's standard tooling.
- **Or state it yourself:** `--test-command "..."` is used as-is, ahead of anything detected (a Docker, nix or asdf command works here).

Where the tools come from, in order:

1. **The repo's own `pixi.toml`**, if it has one.
2. **Binaries already installed** on the machine (`node`/`npm`, `go`, ...), used as they are.
3. **Otherwise you decide** (a prompt in suggest/auto-edit mode): install the toolchain from conda-forge into an isolated pixi env under `work/.toolchains/<language>/`; give a command of your own (for toolchains conda-forge doesn't have, or to use Docker); or skip the tests. Nothing is ever installed globally, and Docker is never assumed. Full-auto uses the isolated env when conda-forge has the toolchain, otherwise skips the tests and flags the run. `--install isolated|none` answers the question up front.

Dependencies (`npm install`, `go mod download`) are fetched before the tests, outside the sandbox; the tests themselves run sandboxed (macOS only for now). Python, Node and Go have built-in defaults; any other language works through a declared or user-given test command.

## Scripted runs

```bash
pixi run agentic-swe run "Add rate limiting to note creation" --from seed_repo --mode auto-edit
pixi run agentic-swe run "Make the notes API faster" --from seed_repo --model mock
pixi run agentic-swe run "Build a scalable URL shortener service with APIs, persistence, and analytics." --mode full-auto
pixi run agentic-swe run "Reject todos with an empty or overlong title" --from seed_go --install isolated
pixi run agentic-swe run "Add a CLI flag" --from ../my-rust-tool --test-command "cargo test"
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
pixi run demo-go            # Go brownfield, suggest: title validation on the Go todo service (installs Go if missing)
```

| Example | What it shows | Output |
|---|---|---|
| Greenfield | Architecture + API contract, 6 tasks in 3 parallel waves, 11 unit/integration tests on Postgres | [summary](examples/greenfield-url-shortener/summary.md) · [transcript](examples/greenfield-url-shortener/transcript.txt) · [code](url_shortener/) |
| Brownfield | Impact analysis finds that the tag filter runs in Python, so naive paging would be wrong; fixes both | [summary](examples/brownfield-pagination/summary.md) · [transcript](examples/brownfield-pagination/transcript.txt) · [diff](examples/brownfield-pagination/changes.patch) |
| Ambiguous | Detects 3 ambiguities, pauses for answers, then fixes an N+1 with a query-count regression test | [summary](examples/ambiguous-make-it-faster/summary.md) · [transcript](examples/ambiguous-make-it-faster/transcript.txt) · [diff](examples/ambiguous-make-it-faster/changes.patch) |
| Go brownfield | Detects Go, gets a toolchain (here: isolated pixi env, since Go isn't installed), adds title validation with table tests | [summary](examples/brownfield-go-validation/summary.md) · [transcript](examples/brownfield-go-validation/transcript.txt) · [diff](examples/brownfield-go-validation/changes.patch) |

> **How the recordings were made:** the agent outputs in `examples/*/steps/` were written by hand in the exact typed schemas the agents return. They are a faithful stand-in for model output, not a captured model run. Run the same requirement with a live `--model` and `--out examples/<name>` to record a live one.

## Controlled autonomy

Like Claude Code and Codex, you choose how much the agents do on their own:

| Mode | Asks about ambiguities | Approves plan | Approves each edit | Out-of-scope writes |
|---|---|---|---|---|
| `suggest` (default) | yes | yes | yes (shows diff; `n` skips, free text = feedback and redo) | ask |
| `auto-edit` | yes | yes | no | ask |
| `full-auto` | no, uses stated assumptions | no | no | rejected |

In every mode: generated tests run sandboxed (on macOS, `sandbox-exec` limits writes to the working directory, a scratch dir and the toolchain cache, and network to localhost) with the toolchain chosen as above; code the agents execute runs in the [Monty](https://github.com/pydantic/monty) sandbox; the agents' read tools and all writes are confined to the working directory, `.git`/`.env`/`.pixi` are never written, each task may only write the files the plan declared for it, failing tests trigger up to 2 automatic fix attempts, and anything still failing escalates to the human (or is flagged `needs_review` in full-auto).

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
  toolchains.py    language detection, toolchain resolution (repo pixi / native / isolated), sandboxed test runs
  db.py            Postgres audit trail (runs, events)
  cli.py           scripted commands (run, runs, show) and working-directory handling
  session.py       interactive session
  api.py           HTTP API
seed_repo/         pre-existing Python notes service (brownfield target; runs work on copies)
seed_go/           pre-existing Go todo service (Go brownfield target)
url_shortener/     output of the greenfield example
examples/          the four recorded runs
work/, runs/       per-run working copies, isolated toolchains (work/.toolchains) and outputs (gitignored)
```

## Repo tooling

Inside pixi, `gh` keeps its config in `.gh/` (gitignored, via `GH_CONFIG_DIR`), so this repo can use a different GitHub account from the system `gh` login. Log in with `pixi run gh auth login` and push with `pixi run git push`.
