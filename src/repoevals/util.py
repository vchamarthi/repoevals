"""Small shared helpers: subprocess, git, globbing, JSON extraction. Standard library only."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class Proc:
    rc: int
    out: str
    timed_out: bool = False
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out


SECRET_ENV = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIAL|_AUTH$|"
                        r"^AWS_|^AZURE_|^GOOGLE_APPLICATION|^GH_|^GITHUB_TOKEN$|^ACTIONS_ID_TOKEN|^NPM_|^PYPI_|"
                        r"^TWINE_|^SSH_AUTH_SOCK$)", re.I)
MAX_OUTPUT = 20 * 1024 * 1024  # bytes of output kept in memory per command (the tail)


def scrubbed_env(keep: Iterable[str] = ()) -> dict[str, str]:
    """The current environment without credentials (names in `keep` are retained)."""
    keep = set(keep)
    return {k: v for k, v in os.environ.items() if k in keep or not SECRET_ENV.search(k)}


def run(cmd: str | Sequence[str], cwd: Path | str | None = None, timeout: float | None = None,
        env: Mapping[str, str] | None = None, stdin: str | None = None, scrub: bool = False,
        keep_env: Iterable[str] = (), output_file: Path | None = None) -> Proc:
    """Run a command, merging stderr into stdout. A string is split with shlex (no shell).

    The command gets its own process group; on timeout the whole group is killed, so background processes an agent
    or a test suite started cannot keep running (and editing the workspace) after the deadline.
    """
    import signal
    import tempfile
    import time

    argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    base_env = scrubbed_env(keep_env) if scrub else dict(os.environ)
    full_env = {**base_env, **(env or {})}
    t0 = time.monotonic()
    # output goes to a file, not a pipe: memory stays bounded however much a build or an agent prints
    sink = open(output_file, "wb+") if output_file else tempfile.TemporaryFile()
    try:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=full_env, stdin=subprocess.PIPE if stdin is not None
                                    else subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except (FileNotFoundError, PermissionError) as e:
            return Proc(127, str(e), seconds=time.monotonic() - t0)
        timed_out = False
        try:
            proc.communicate(input=stdin.encode() if stdin is not None else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.communicate()
        size = sink.seek(0, os.SEEK_END)
        sink.seek(max(0, size - MAX_OUTPUT))
        out = sink.read().decode("utf-8", errors="replace")
        if size > MAX_OUTPUT:
            out = f"[repoevals: {size - MAX_OUTPUT} earlier bytes of output omitted]\n" + out
        return Proc(-1 if timed_out else proc.returncode, out, timed_out=timed_out, seconds=time.monotonic() - t0)
    finally:
        sink.close()


def run_shell(cmd: str, cwd: Path | str | None = None, timeout: float | None = None,
              env: Mapping[str, str] | None = None) -> Proc:
    """Run a repo-configured command line through bash (config commands may use pipes, &&, env vars).

    Builds, tests and benchmarks execute code the agent wrote, so they never see credentials from the environment.
    """
    return run(["bash", "-c", cmd], cwd=cwd, timeout=timeout, env=env, scrub=True)


def git(repo: Path | str, *args: str, check: bool = False, stdin: str | None = None) -> Proc:
    p = run(["git", "-c", "core.quotepath=off", "-c", "advice.detachedHead=false", *args], cwd=repo, stdin=stdin)
    if check and p.rc != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {p.out.strip()[:2000]}")
    return p


def git_init_snapshot(ws: Path, message: str) -> str:
    """Commit everything in ws as a single root commit; return its sha."""
    git(ws, "init", "-q", "-b", "main", check=True)
    git(ws, "add", "-A", "-f", check=True)
    env_args = ["-c", "user.name=repoevals", "-c", "user.email=repoevals@localhost", "-c", "commit.gpgsign=false"]
    git(ws, *env_args, "commit", "-q", "--no-verify", "--allow-empty", "-m", message, check=True)
    return git(ws, "rev-parse", "HEAD", check=True).out.strip()


def git_commit(ws: Path, message: str) -> str:
    env_args = ["-c", "user.name=repoevals", "-c", "user.email=repoevals@localhost", "-c", "commit.gpgsign=false"]
    git(ws, "add", "-A", "-f", check=True)
    git(ws, *env_args, "commit", "-q", "--no-verify", "--allow-empty", "-m", message, check=True)
    return git(ws, "rev-parse", "HEAD", check=True).out.strip()


def apply_patch(ws: Path, patch: Path, reverse: bool = False, check_only: bool = False) -> Proc:
    args = ["apply", "--whitespace=nowarn"]
    if reverse:
        args.append("-R")
    if check_only:
        args.append("--check")
    return git(ws, *args, str(patch.resolve()))


class WorkTree:
    """Grade-time view of an agent's tree.

    Compares the files on disk with a commit through a private git dir (the copy `prepare` saved outside the
    workspace, when available) and a throwaway index. Nothing the agent did to `.git` — skip-worktree or
    assume-unchanged flags, `info/exclude`, rewritten or deleted history — changes what the grader sees.
    Ignored-but-present files are included when they match `force_globs` (tests, test infrastructure), so a
    `.gitignore` entry cannot hide a new conftest.py either.
    """

    def __init__(self, ws: Path, gitdir: Path | None = None, force_globs: Iterable[str] = ()):
        import tempfile
        self.ws = Path(ws).resolve()
        self.gitdir = Path(gitdir).resolve() if gitdir else self.ws / ".git"
        self.force_globs = list(force_globs)
        self._tmp = tempfile.mkdtemp(prefix="repoevals-index-")
        self.index = str(Path(self._tmp) / "index")

    def __del__(self):
        shutil.rmtree(getattr(self, "_tmp", ""), ignore_errors=True)

    def git(self, *args: str, check: bool = False) -> Proc:
        p = run(["git", "-c", "core.quotepath=off", "-c", "advice.detachedHead=false", "-c", "core.bare=false",
                 f"--git-dir={self.gitdir}", f"--work-tree={self.ws}", *args], cwd=self.ws,
                env={"GIT_INDEX_FILE": self.index})
        if check and p.rc != 0:
            raise RuntimeError(f"git {' '.join(args)} failed in {self.ws}: {p.out.strip()[:2000]}")
        return p

    def stage(self, base: str) -> None:
        self.git("read-tree", base, check=True)
        self.git("add", "-A", "--", ".")
        if self.force_globs:
            ignored = self.git("ls-files", "--others", "--ignored", "--exclude-standard").out.splitlines()
            forced = [p for p in ignored if glob_match(p, self.force_globs)]
            for i in range(0, len(forced), 200):
                self.git("add", "-f", "--", *forced[i:i + 200])

    def changed(self, base: str) -> dict[str, str]:
        """{path: status letter (A/M/D/T)} between base and the files on disk."""
        self.stage(base)
        res: dict[str, str] = {}
        for line in self.git("diff", "--cached", "--name-status", "--no-renames", base).out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                res[parts[-1]] = parts[0][0]
        return res

    def diff_text(self, base: str, paths: Iterable[str] | None = None) -> str:
        self.stage(base)
        args = ["diff", "--cached", "--no-renames", "--no-color", base]
        if paths is not None:
            args += ["--", *paths]
        return self.git(*args).out

    def diff_stats(self, base: str) -> tuple[int, int]:
        """(lines added, lines removed) against base, new files included."""
        self.stage(base)
        added = removed = 0
        for line in self.git("diff", "--cached", "--numstat", "--no-renames", base).out.splitlines():
            a, r, *_ = line.split("\t") + ["", ""]
            added += int(a) if a.isdigit() else 0
            removed += int(r) if r.isdigit() else 0
        return added, removed

    def restore(self, base: str, paths: Iterable[str]) -> list[str]:
        """Reset each path to its content at base (deleting it if it did not exist there)."""
        restored = []
        for p in paths:
            target = self.ws / p
            if target.is_symlink():
                target.unlink()
            if self.git("cat-file", "-e", f"{base}:{p}").rc == 0:
                self.git("checkout", base, "--", p, check=True)
            elif target.is_file():
                target.unlink()
            restored.append(p)
        return restored

    def show(self, *args: str) -> str:
        return self.git("show", *args).out


def changed_files(ws: Path, base: str, gitdir: Path | None = None) -> dict[str, str]:
    return WorkTree(ws, gitdir).changed(base)


def diff_text(ws: Path, base: str, paths: Iterable[str] | None = None, gitdir: Path | None = None) -> str:
    return WorkTree(ws, gitdir).diff_text(base, paths)


def diff_stats(ws: Path, base: str, gitdir: Path | None = None) -> tuple[int, int]:
    return WorkTree(ws, gitdir).diff_stats(base)


def restore_paths(ws: Path, base: str, paths: Iterable[str], gitdir: Path | None = None) -> list[str]:
    return WorkTree(ws, gitdir).restore(base, paths)


@lru_cache(maxsize=512)
def _glob_re(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-like glob (`**`, `*`, `?`) to a regex matched against posix relative paths."""
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(path: str, patterns: Iterable[str]) -> bool:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return any(_glob_re(p).match(path) for p in patterns)


def last_json(text: str):
    """The last fenced ```json block in text, else the last top-level JSON array/object, else None."""
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text or "", re.S)
    for b in reversed(blocks):
        try:
            return json.loads(b)
        except ValueError:
            continue
    for opener, closer in (("[", "]"), ("{", "}")):
        end = (text or "").rfind(closer)
        start = (text or "").rfind(opener, 0, end)
        while start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                start = text.rfind(opener, 0, start)
    return None


def copytree_overlay(src: Path, dst: Path) -> list[str]:
    """Copy every file under src onto dst (overwriting); returns the relative paths written.

    Existing symlinks at a target are replaced, and a target whose parent resolves outside dst is refused, so an
    agent cannot pre-plant links that redirect hidden material out of the workspace.
    """
    written = []
    root = dst.resolve()
    for f in sorted(src.rglob("*")):
        if f.is_file():
            rel = f.relative_to(src).as_posix()
            if rel.endswith(".oracle"):  # stored with a suffix so the repo's own tooling ignores it
                rel = rel[: -len(".oracle")]
            target = dst / rel
            for parent in reversed(target.relative_to(dst).parents):
                d = dst / parent
                if d.is_symlink():
                    d.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            if not within(root, target.parent):
                raise RuntimeError(f"refusing to write {rel}: its directory resolves outside the workspace")
            if target.is_symlink() or target.exists():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.copy2(f, target)
            written.append(rel)
    return written


def read_toml(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def fmt(template: str, **kw) -> str:
    """str.format that leaves unknown {placeholders} untouched."""
    class _D(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    return template.format_map(_D(**{k: v for k, v in kw.items() if v is not None}))


def within(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def package_versions() -> dict[str, str]:
    """Installed distributions and versions of the running Python (results depend on them; record them)."""
    from importlib.metadata import distributions
    out = {}
    for d in distributions():
        name = (d.metadata["Name"] or "").lower()
        if name:
            out[name] = d.version
    return dict(sorted(out.items()))
