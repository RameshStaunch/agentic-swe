"""Find the language of a repo, get a toolchain to test it with, and run its tests in a sandbox.

Toolchain order: the repo's own pixi.toml (the user chose pixi for this repo) -> binaries already installed on the machine
-> otherwise the user's choice of an isolated pixi env (under work/.toolchains/, nothing global) or a global install."""

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Install = Literal["ask", "isolated", "global", "none"]
TOOLCHAINS_DIR = Path("work/.toolchains").resolve()


@dataclass(frozen=True)
class Toolchain:
    language: str
    markers: tuple[str, ...]  # files at the repo root that identify the language
    binaries: tuple[str, ...]  # all must be on PATH to use a native install
    packages: tuple[str, ...]  # conda-forge packages that provide them
    test: tuple[str, ...]
    prepare: tuple[str, ...] = ()  # dependency fetch; runs outside the sandbox because it needs the network
    caches: dict[str, str] = field(default_factory=dict)  # env var -> subdir of a per-language cache the sandbox may write


TOOLCHAINS = {
    "python": Toolchain("python", ("pyproject.toml", "setup.py", "requirements.txt", "conftest.py"), ("python",), ("python", "pytest"),
                        ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider")),
    "node": Toolchain("node", ("package.json",), ("node", "npm"), ("nodejs",), ("npm", "test", "--silent"),
                      prepare=("npm", "install", "--no-audit", "--no-fund"),
                      caches={"npm_config_cache": "npm", "npm_config_update_notifier": ""}),
    "go": Toolchain("go", ("go.mod",), ("go",), ("go",), ("go", "test", "./..."), prepare=("go", "mod", "download"),
                    caches={"GOPATH": "gopath", "GOMODCACHE": "gopath/pkg/mod", "GOCACHE": "gocache", "GOTELEMETRY": ""}),
    # ponytail: Rust/Java are one row each (cargo test / mvn test) once there is an example to prove them
}
EXTENSIONS = {".py": "python", ".js": "node", ".ts": "node", ".go": "go"}


def detect(repo: Path) -> str | None:
    """Language of an existing codebase, from root marker files, else the most common source extension."""
    for tc in TOOLCHAINS.values():
        if any((repo / m).exists() for m in tc.markers):
            return tc.language
    counts: dict[str, int] = {}
    for p in repo.rglob("*"):
        if p.suffix in EXTENSIONS and not any(part.startswith(".") or part == "node_modules" for part in p.parts):
            counts[EXTENSIONS[p.suffix]] = counts.get(EXTENSIONS[p.suffix], 0) + 1
    return max(counts, key=counts.get) if counts else None


@dataclass
class Resolved:
    tc: Toolchain
    how: str  # human-readable: where the binaries come from
    path_prefix: list[str] = field(default_factory=list)
    python: str | None = None  # orchestrator interpreter when python runs natively

    def env(self, scratch: str) -> dict[str, str]:
        cache = TOOLCHAINS_DIR / "cache" / self.tc.language
        env = {**os.environ, "TMPDIR": scratch, "PYTHONPYCACHEPREFIX": scratch}
        for var, sub in self.tc.caches.items():
            env[var] = str(cache / sub) if sub else ("off" if var == "GOTELEMETRY" else "false")
        env["PATH"] = os.pathsep.join([*self.path_prefix, env.get("PATH", "")])
        return env

    def command(self, cmd: tuple[str, ...]) -> list[str]:
        return [self.python if (c == "python" and self.python) else c for c in cmd]


def _pixi_env_bin(manifest: Path) -> Path | None:
    r = subprocess.run(["pixi", "install", "--manifest-path", str(manifest)], capture_output=True, text=True, timeout=1800)
    bin_dir = manifest.parent / ".pixi" / "envs" / "default" / "bin"
    return bin_dir if r.returncode == 0 and bin_dir.exists() else None


def resolve(repo: Path, language: str, install: Install, choose: Callable[[Toolchain], Install]) -> Resolved | str:
    """A toolchain for `language`, or a string saying why there is none."""
    tc = TOOLCHAINS.get(language)
    if tc is None:
        return f"no toolchain known for {language}"
    has_pixi = bool(shutil.which("pixi"))
    if (repo / "pixi.toml").exists() and has_pixi:
        if bin_dir := _pixi_env_bin(repo / "pixi.toml"):
            return Resolved(tc, "repo's own pixi env", [str(bin_dir)])
    if language == "python":
        return Resolved(tc, f"native ({sys.executable})", python=sys.executable)
    if all(shutil.which(b) for b in tc.binaries):
        return Resolved(tc, "native (" + ", ".join(shutil.which(b) or b for b in tc.binaries) + ")")
    if not has_pixi:
        return f"{', '.join(tc.binaries)} not installed and pixi is unavailable to install it"
    if install == "ask":
        install = choose(tc)
    if install == "isolated":
        env_dir = TOOLCHAINS_DIR / language
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "pixi.toml").write_text(
            f'[workspace]\nname = "toolchain-{language}"\nchannels = ["conda-forge"]\nplatforms = ["osx-arm64", "osx-64", "linux-64"]\n\n'
            "[dependencies]\n" + "".join(f'{p} = "*"\n' for p in tc.packages))
        if bin_dir := _pixi_env_bin(env_dir / "pixi.toml"):
            return Resolved(tc, f"isolated pixi env ({env_dir})", [str(bin_dir)])
        return f"could not create an isolated pixi env for {language}"
    if install == "global":
        subprocess.run(["pixi", "global", "install", *tc.packages], capture_output=True, text=True, timeout=1800)
        if all(shutil.which(b) for b in tc.binaries):
            return Resolved(tc, "global (pixi global install)")
        return f"pixi global install {' '.join(tc.packages)} did not put {', '.join(tc.binaries)} on PATH"
    return f"{', '.join(tc.binaries)} not installed; install skipped"


# macOS seatbelt profile: generated code may only write inside its repo, a scratch dir and the toolchain cache, and only
# reach localhost (test databases).
SANDBOX_PROFILE = """(version 1)
(allow default)
(deny network*)
(allow network* (remote ip "localhost:*") (remote unix-socket))
(allow network-bind network-inbound (local ip "localhost:*"))
(deny file-write*)
(allow file-write* (subpath "{repo}") (subpath "{scratch}") (subpath "{cache}") (literal "/dev/null") (literal "/dev/tty") (subpath "/dev/fd"))
"""


def sandboxed(cmd: list[str], repo: Path, scratch: str) -> tuple[list[str], str]:
    if os.environ.get("AGENTIC_SWE_SANDBOX", "1") == "0":
        return cmd, "none (AGENTIC_SWE_SANDBOX=0)"
    if shutil.which("sandbox-exec"):
        profile = SANDBOX_PROFILE.format(repo=repo.resolve(), scratch=Path(scratch).resolve(), cache=TOOLCHAINS_DIR / "cache")
        return ["sandbox-exec", "-p", profile, *cmd], "sandbox-exec: writes limited to repo + scratch + toolchain cache, network localhost only"
    # ponytail: no sandbox off macOS yet; bubblewrap (Linux) or a container is the upgrade path
    return cmd, "none (sandbox-exec unavailable on this OS)"


def run_tests(repo: Path, rt: Resolved | str) -> tuple[bool, str]:
    if isinstance(rt, str):
        return False, f"tests not run: {rt}"
    if rt.tc.language == "python":
        errors = []
        for p in repo.rglob("*.py"):
            try:
                compile(p.read_text(), str(p), "exec")
            except SyntaxError as e:
                errors.append(f"{p.relative_to(repo)}:{e.lineno}: {e.msg}")
        if errors:
            return False, "syntax errors:\n" + "\n".join(errors)
    # Fresh scratch dir per run: TMPDIR and Python bytecode (a same-size edit within the same second would otherwise reuse
    # stale .pyc files), so the sandbox only needs to allow writes to the repo, this dir and the toolchain cache.
    with tempfile.TemporaryDirectory() as scratch:
        env = rt.env(scratch)
        if rt.tc.prepare:
            p = subprocess.run(rt.command(rt.tc.prepare), cwd=repo, capture_output=True, text=True, timeout=900, env=env)
            if p.returncode:
                return False, f"dependency install failed ({' '.join(rt.tc.prepare)}):\n{(p.stdout + p.stderr)[-4000:]}"
        cmd, sandbox_desc = sandboxed(rt.command(rt.tc.test), repo, scratch)
        try:
            r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=600, env=env)
        except subprocess.TimeoutExpired:
            return False, "tests timed out after 600s"
    header = f"[{rt.tc.language}: {' '.join(rt.tc.test)}] [toolchain: {rt.how}] [sandbox: {sandbox_desc}]\n"
    out = header + (r.stdout + r.stderr)[-8000:]
    if rt.tc.language == "python" and r.returncode == 5:
        return False, "no tests collected\n" + out
    return r.returncode == 0, out
