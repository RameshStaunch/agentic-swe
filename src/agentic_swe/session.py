"""Interactive session: pick a model, then describe work in plain language; the agents classify, ask, plan and build."""

import asyncio
import os
import shutil
from pathlib import Path

from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .agents import DEFAULT_MODEL, load_model
from .cli import run_once
from .models import Mode

MODELS = [  # (pydantic-ai model string, env var its provider reads)
    ("google:gemini-flash-latest", "GOOGLE_API_KEY"),
    ("google:gemini-pro-latest", "GOOGLE_API_KEY"),
    ("anthropic:claude-sonnet-5", "ANTHROPIC_API_KEY"),
    ("anthropic:claude-opus-5-5", "ANTHROPIC_API_KEY"),
    ("openai:gpt-5", "OPENAI_API_KEY"),
]
EXAMPLES = {  # name -> (requirement, seed repo copied into work/ before replay)
    "greenfield-url-shortener": ("Build a scalable URL shortener service with APIs, persistence, and analytics.", None),
    "brownfield-pagination": ("Add pagination to the notes list endpoint", "seed_repo"),
    "ambiguous-make-it-faster": ("Make the notes API faster", "seed_repo"),
}
MODES: dict[str, str] = {
    "suggest": "approve the plan and every edit",
    "auto-edit": "approve the plan; edits apply unless they leave a task's declared files",
    "full-auto": "no prompts; review the summary at the end",
}
HELP = """[bold]Describe what you want built or changed[/], or use a command:
  /model   switch model        /mode   switch autonomy mode
  /repo    switch directory    /runs   recent runs
  /help    this help           /exit   quit"""


class Session:
    def __init__(self, console: Console | None = None):
        self.console = console or Console(record=True)
        self.model_name: str | None = None
        self.llm: Model | None = None
        self.replay = False
        self.mode: Mode | None = None
        self.repo: Path | None = None

    def ask(self, prompt: str, default: str = "") -> str:
        hint = f" [dim]({default})[/]" if default else ""
        return self.console.input(f"{prompt}{hint} ").strip() or default

    # ---------------------------------------------------------------- choices
    def choose_model(self) -> None:
        t = Table("#", "Model", "Key", title="Choose a model", title_justify="left")
        for i, (name, key) in enumerate(MODELS, 1):
            t.add_row(str(i), name, f"[green]{key} set[/]" if os.environ.get(key) else f"[dim]{key} missing[/]")
        t.add_row("c", "custom", "any Pydantic AI '<provider>:<model>'")
        t.add_row("r", "replay", "recorded examples, no API calls")
        self.console.print(t)
        default = next((str(i) for i, (n, k) in enumerate(MODELS, 1) if n == DEFAULT_MODEL and os.environ.get(k)), "r")
        while True:
            pick = self.ask("Model", default).lower()
            if pick == "r":
                self.model_name, self.llm, self.replay = "replay", None, True
                break
            name = self.ask("Model string") if pick == "c" else MODELS[int(pick) - 1][0] if pick.isdigit() and 0 < int(pick) <= len(MODELS) else pick
            try:
                self.llm, self.model_name, self.replay = load_model(name), name, False
                break
            except (UserError, ImportError, IndexError) as e:
                self.console.print(f"[red]{name}: {e}[/]")
        self.console.print(f"Using [bold]{self.model_name}[/]\n")

    def choose_mode(self) -> None:
        for i, (m, d) in enumerate(MODES.items(), 1):
            self.console.print(f"  {i}. [bold]{m}[/]  {d}")
        pick = self.ask("Autonomy", "1")
        self.mode = list(MODES)[int(pick) - 1] if pick in ("1", "2", "3") else pick if pick in MODES else "suggest"  # type: ignore[assignment]

    def choose_repo(self) -> None:
        path = Path(self.ask("Which directory should I work in? New or empty means a new project", str(self.repo or "work/project")))
        files = [p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts] if path.exists() else []
        self.console.print(f"[dim]{path}: {'%d existing files' % len(files) if files else 'empty, will be created'}[/]")
        self.repo = path

    # ---------------------------------------------------------------- work
    def build(self, requirement: str, replay: Path | None = None) -> None:
        if self.repo is None:
            self.choose_repo()
        if self.mode is None:
            self.choose_mode()
        assert self.repo and self.mode and self.model_name
        try:
            result = asyncio.run(run_once(requirement, self.repo, None, self.mode, model_name=self.model_name, llm=self.llm,
                                          replay=replay, answers=[], out=None, no_db=False, console=self.console))
        except KeyboardInterrupt:
            self.console.print("[yellow]interrupted[/]")
            return
        except Exception as e:  # keep the session alive; the run is already marked failed in the audit trail
            self.console.print(f"[red]run failed:[/] {e}")
            return
        if result.run_dir:
            self.console.print(f"\n[bold]{result.status}[/]. Review [cyan]{result.run_dir / 'summary.md'}[/] and [cyan]{result.run_dir / 'changes.patch'}[/]\n")

    def replay_example(self) -> None:
        names = list(EXAMPLES)
        for i, n in enumerate(names, 1):
            self.console.print(f"  {i}. [bold]{n}[/]  {EXAMPLES[n][0]}")
        pick = self.ask("Example", "1")
        name = names[int(pick) - 1] if pick in ("1", "2", "3") else names[0]
        requirement, seed = EXAMPLES[name]
        self.repo = Path("work") / name
        shutil.rmtree(self.repo, ignore_errors=True)
        if seed:
            shutil.copytree(seed, self.repo, ignore=shutil.ignore_patterns("__pycache__"))
        self.console.print(f"[dim]requirement:[/] {requirement}\n[dim]directory:[/] {self.repo}")
        self.build(requirement, replay=Path("examples") / name)

    def loop(self) -> None:
        self.console.print(Panel("[bold]agentic-swe[/]  requirement in, reviewable change out", expand=False))
        self.choose_model()
        self.console.print(HELP)
        if self.replay:
            self.console.print("[dim]Replay mode: press enter to pick a recorded example (/model to switch to a live model).[/]")
        while True:
            try:
                line = self.console.input("\n[bold cyan]›[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line and not self.replay:
                continue
            cmd = line.lower()
            if cmd in ("/exit", "/quit"):
                break
            elif cmd == "/help":
                self.console.print(HELP)
            elif cmd == "/model":
                self.choose_model()
            elif cmd == "/mode":
                self.choose_mode()
            elif cmd == "/repo":
                self.choose_repo()
            elif cmd == "/runs":
                from .cli import runs
                runs()
            elif cmd.startswith("/"):
                self.console.print(f"unknown command {line}; /help lists them")
            elif self.replay:
                self.replay_example()
            else:
                self.build(line)
        self.console.print("bye")
