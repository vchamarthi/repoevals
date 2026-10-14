"""Task authoring: find candidate tasks in a repository's history and scaffold them.

Everything produced here is a **draft** (`[metadata].status = "draft"`): a human, or an agent following the
authoring playbook, completes the instruction, refines targets, runs `repoevals check`, and approves the task.

    mine            rank commits that changed both library code and tests (bug fixes with tests)
    scaffold_fix    turn one such commit into a hidden-tests task (base, oracle.patch, hidden tests)
    scaffold_replay turn a reviewed GitHub pull request into a review-replay task (needs the `gh` CLI)
    mutants         generate candidate mutants for a Python file or function
    prune_mutants   keep only the mutants the task's reference tests detect
    approve         mark draft tasks as ready
"""
from __future__ import annotations

import ast
import difflib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import RepoConfig
from .task import load_task
from .util import git, glob_match, run

DRAFT_MARKER = "<!-- repoevals:draft"
FIX_WORDS = re.compile(r"\b(fix(e[sd])?|bug|issue|regression|crash|wrong|incorrect|error|nan|overflow|leak|race|"
                       r"segfault|broken|handle|correct)\b", re.I)
PR_NUM = re.compile(r"(?:\(#(\d+)\)\s*$|Merge pull request #(\d+))")
CODE_EXT = re.compile(r"\.(py|pyx|pxd|pxi|c|cc|cpp|cxx|h|hh|hpp|cu|cuh|f90|f|rs|go|java|kt|ts|js|jl|m|src)$")


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if len(s) > n:
        s = s[:n]
        s = s[: s.rfind("_")] if "_" in s else s
    return s.strip("_") or "task"


def _title(source: Path, sha: str) -> tuple[str, str]:
    """(title, body) of a commit; for GitHub merge commits the title is the pull request title."""
    subject = git(source, "log", "-1", "--format=%s", sha).out.strip()
    body = git(source, "log", "-1", "--format=%b", sha).out.strip()
    if subject.startswith("Merge pull request #") and body:
        first, _, rest = body.partition("\n")
        return first.strip(), rest.strip()
    return subject, body


def _classify(cfg: RepoConfig, path: str) -> str:
    if glob_match(path, cfg.test_globs):
        return "test"
    if cfg.library_globs:
        return "lib" if glob_match(path, cfg.library_globs) else "other"
    return "lib" if CODE_EXT.search(path) else "other"


def _repo_slug(cfg: RepoConfig, source: Path) -> str:
    url = cfg.url or git(source, "remote", "get-url", "origin").out.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/.\s]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else ""


# ------------------------------------------------------------------------------------------------- mining

def mine(cfg: RepoConfig, source: Path, rev: str = "HEAD", limit: int = 500, since: str = "",
         max_lib_lines: int = 300) -> list[dict]:
    """Commits on the first-parent history of `rev` that change library code and tests, best candidates first."""
    args = ["log", "--first-parent", f"-n{limit}", "--format=%H%x1f%cs%x1f%s", rev]
    if since:
        args.insert(1, f"--since={since}")
    out = git(source, *args).out
    slug_ = _repo_slug(cfg, source)
    cands = []
    for line in out.splitlines():
        try:
            sha, date, subject = line.split("\x1f", 2)
        except ValueError:
            continue
        stats = git(source, "diff", "--numstat", "--no-renames", f"{sha}^1", sha).out
        lib, tests, other, lib_lines, test_lines, new_tests = [], [], [], 0, 0, 0
        status = dict(reversed(x.split("\t")[:2]) for x in
                      git(source, "diff", "--name-status", "--no-renames", f"{sha}^1", sha).out.splitlines()
                      if "\t" in x)
        for row in stats.splitlines():
            parts = row.split("\t")
            if len(parts) < 3:
                continue
            a, r, path = parts[0], parts[1], parts[2]
            n = (int(a) if a.isdigit() else 0) + (int(r) if r.isdigit() else 0)
            kind = _classify(cfg, path)
            if kind == "test":
                tests.append(path)
                test_lines += n
                new_tests += status.get(path) == "A"
            elif kind == "lib":
                lib.append(path)
                lib_lines += n
            else:
                other.append(path)
        if not lib or not tests:
            continue
        score = 0.0
        score += 3 if FIX_WORDS.search(subject) else 0
        score += 2 if 3 <= lib_lines <= max_lib_lines else (-3 if lib_lines > max_lib_lines else 0)
        score += 1 if len(lib) <= 3 else -1
        score += 1 if test_lines >= 5 else 0
        score += 0.5 if new_tests == 0 else 0  # modified existing test files are easiest to target
        m = PR_NUM.search(subject)
        pr = int(m.group(1) or m.group(2)) if m else None
        if subject.startswith("Merge pull request #"):
            subject = _title(source, sha)[0] or subject
            score += 3 if FIX_WORDS.search(subject) else 0
        cands.append({"sha": sha, "date": date, "subject": subject, "pr": pr,
                      "url": (f"https://github.com/{slug_}/pull/{pr}" if pr and slug_ else
                              f"https://github.com/{slug_}/commit/{sha}" if slug_ else ""),
                      "lib_files": lib, "test_files": tests, "lib_lines": lib_lines, "test_lines": test_lines,
                      "score": round(score, 1)})
    cands.sort(key=lambda c: (-c["score"], c["lib_lines"]))
    return cands


COMPILED = re.compile(r"\.(c|cc|cpp|cxx|h|hh|hpp|cu|cuh|pyx|pxd|pxi|f|f90|cmake)$|(^|/)CMakeLists\.txt$|"
                      r"(^|/)meson\.build$|(^|/)setup\.py$")


def release_compatible(cfg: RepoConfig, source: Path, cands: list[dict], release: str) -> list[dict]:
    """Candidates after `release` whose library change is not compiled code and applies cleanly at `release`.

    For projects evaluated against prebuilt release binaries (no compiler available): such a fix can become a task
    whose base is the release itself (`author fix --base <release>`).
    """
    from .workspace import _extract
    rel = git(source, "rev-parse", f"{release}^{{commit}}", check=True).out.strip()
    after = set(git(source, "rev-list", f"{rel}..HEAD").out.split())
    out = []
    with tempfile.TemporaryDirectory() as td:
        tree = Path(td) / "t"
        tree.mkdir()
        _extract(source, rel, tree)
        for c in cands:
            if c["sha"] not in after or any(COMPILED.search(f) for f in c["lib_files"]):
                continue
            patch = git(source, "diff", "--binary", f"{c['sha']}^1", c["sha"], "--", *c["lib_files"]).out
            pf = Path(td) / "fix.patch"
            pf.write_text(patch)
            if run(["git", "apply", "--check", str(pf)], cwd=tree).rc == 0:
                out.append({**c, "base": release})
    return out


# ------------------------------------------------------------------------------------------------- scaffolding

def _toml_str(v: str) -> str:
    return json.dumps(v)


def _write_task_toml(d: Path, lines: list[str]) -> None:
    (d / "task.toml").write_text("\n".join(lines).rstrip() + "\n")


def _instruction_template(kind: str, notes: str) -> str:
    return (f"{DRAFT_MARKER} — replace this whole file before approving the task.\n"
            f"Write the {kind} as a user would report it: the symptom, a minimal reproducer, the expected\n"
            "behaviour. Do not describe the fix, name the functions it changes, or link to the fixing change.\n"
            f"{notes}\n-->\n\nTODO: problem statement.\n")


def extract_changed_tests(old: str, new: str) -> tuple[str, list[str]]:
    """A standalone test module with only the test functions/classes that `new` adds or changes relative to `old`,
    plus all of `new`'s module-level setup (imports, constants, helpers). Returns ("", []) if nothing changed."""
    def segments(src: str) -> dict[str, str]:
        out = {}
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return out
        lines = src.splitlines(keepends=True)
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([n.lineno] + [d.lineno for d in n.decorator_list])
                out[n.name] = "".join(lines[start - 1:n.end_lineno])
        return out
    tree = ast.parse(new)
    lines = new.splitlines(keepends=True)
    before, after = segments(old), segments(new)
    is_test = {name for name in after if name.startswith(("test", "Test"))}
    changed = [name for name in after if name in is_test and before.get(name) != after[name]]
    if not changed:
        return "", []
    keep: list[str] = []
    for n in tree.body:
        name = getattr(n, "name", None)
        if name in is_test and name not in changed:
            continue
        start = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])
        keep.append("".join(lines[start - 1:n.end_lineno]))
    return "\n\n".join(k.rstrip("\n") for k in keep) + "\n", changed


def scaffold_fix(cfg: RepoConfig, source: Path, commit: str, task_id: str = "", family: str = "fix",
                 force: bool = False, base: str = "", extract_tests: bool = False) -> Path:
    """Create a draft hidden-tests task from a commit that fixed a bug (or added a feature) together with tests.

    By default the task starts from the commit's parent. With `base`, it starts from another revision that still
    has the bug — typically the last release before the fix, so prebuilt release binaries match the task's
    sources — and the fix must apply there.
    """
    sha = git(source, "rev-parse", f"{commit}^{{commit}}", check=True).out.strip()
    fix_parent = git(source, "rev-parse", f"{sha}^1", check=True).out.strip()
    parent = git(source, "rev-parse", f"{base}^{{commit}}", check=True).out.strip() if base else fix_parent
    raw_subject = git(source, "log", "-1", "--format=%s", sha).out.strip()
    subject, body = _title(source, sha)
    date = git(source, "log", "-1", "--format=%cs", sha).out.strip()
    if not task_id:
        short = re.sub(r"^(fix(es|ed)?|add(s|ed)?)\b[:\s]*", "", subject, flags=re.I)
        task_id = f"{family}_{slug(short, 32)}"
    d = cfg.tasks_dir / task_id
    if d.exists():
        if not force:
            raise FileExistsError(f"{d} exists (use --force to overwrite)")
        shutil.rmtree(d)
    (d / "hidden").mkdir(parents=True)
    status = {}
    for row in git(source, "diff", "--name-status", "--no-renames", fix_parent, sha).out.splitlines():
        parts = row.split("\t")
        if len(parts) >= 2:
            status[parts[-1]] = parts[0][0]
    tests = sorted(p for p, s in status.items() if s != "D" and _classify(cfg, p) == "test")
    non_tests = sorted(p for p in status if _classify(cfg, p) != "test")
    # the reference fix is the library change only: changelogs, docs and CI files often conflict at other bases
    # and are not part of what the agent is asked to do
    lib_changes = [p for p in non_tests if _classify(cfg, p) == "lib"] or non_tests
    if not tests:
        raise ValueError(f"{sha[:12]} changes no test files (by [policy].test_globs); nothing to hide")
    if not non_tests:
        raise ValueError(f"{sha[:12]} changes only tests; there is no fix to reproduce")
    hidden_targets, extracted = [], {}
    for p in tests:
        blob = run(["git", "-C", str(source), "show", f"{sha}:{p}"]).out
        if extract_tests and p.endswith(".py"):
            prev = run(["git", "-C", str(source), "show", f"{fix_parent}:{p}"])
            text, names = extract_changed_tests(prev.out if prev.rc == 0 else "", blob)
            if not names:
                continue
            rel = str(Path(p).parent / f"test_repoevals_{task_id}_{Path(p).stem.removeprefix('test_')}.py")
            target = d / "hidden" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            hidden_targets.append(rel)
            extracted[rel] = names
        else:
            target = d / "hidden" / p
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(blob)
            hidden_targets.append(p)
    if not hidden_targets:
        shutil.rmtree(d)
        raise ValueError(f"{sha[:12]}: no added or changed test functions found")
    patch = git(source, "diff", "--binary", "--no-renames", fix_parent, sha, "--", *lib_changes).out
    if base:
        from .workspace import _extract
        with tempfile.TemporaryDirectory() as td:
            _extract(source, parent, Path(td))
            pf = Path(td) / ".fix.patch"
            pf.write_text(patch)
            chk = run(["git", "apply", "--check", str(pf)], cwd=td)
        if chk.rc != 0:
            shutil.rmtree(d)
            raise ValueError(f"the fix does not apply at {base}: {chk.out.strip()[:400]}")
    (d / "oracle.patch").write_text(patch)
    m = PR_NUM.search(raw_subject)
    slug_ = _repo_slug(cfg, source)
    src_url = (f"https://github.com/{slug_}/pull/{m.group(1) or m.group(2)}" if m and slug_ else
               f"https://github.com/{slug_}/commit/{sha}" if slug_ else sha)
    targets = hidden_targets if cfg.test.adapter in ("pytest", "command") else \
        [f"TODO: test target for {t}" for t in hidden_targets]
    _write_task_toml(d, [
        'version = "1.0"', "",
        "[metadata]",
        f"family = {_toml_str(family)}",
        'status = "draft"',
        f'base = "{parent}"',
        f'created = "{date}"',
        f"source = {_toml_str(src_url)}",
        'difficulty = "medium"',
        "tags = []", "",
        "[agent]", "timeout_sec = 3600", "",
        "[verifier]", "timeout_sec = 1800", "",
        "[grader]",
        'type = "hidden_tests"',
        "# Narrow targets to the tests that fail before the fix and pass after it (node ids for pytest).",
        f"targets = {json.dumps(targets)}",
        "# Existing tests of the same component that must keep passing.",
        "regression_targets = []",
        f"root_cause_files = {json.dumps([p for p in non_tests if _classify(cfg, p) == 'lib'])}",
        'gate = ["strict_pass"]',
    ])
    notes = (f"Source: {src_url}\nCommit subject: {subject}\n" + (f"Commit message:\n{body}\n" if body else ""))
    (d / "instruction.md").write_text(_instruction_template("bug report" if family == "fix" else "feature request",
                                                            notes.replace("-->", "- ->")))
    (d / "AUTHOR_NOTES.md").write_text(
        f"# Author notes: {task_id}\n\nNot shown to the agent under evaluation.\n\n"
        f"- source: {src_url}\n- commit: {sha}\n- base: {parent}" + (f" ({base}, not the fix's parent)" if base
                                                                       else " (the fix's parent)") +
        f"\n- subject: {subject}\n\n"
        f"## Files changed by the fix\n\n" + "\n".join(f"- `{p}` ({status[p]})" for p in non_tests) +
        "\n\n## Hidden tests (from the fix commit)\n\n" + "\n".join(
            f"- `{p}`" + (f": {', '.join(extracted[p])}" if p in extracted else "") for p in hidden_targets) +
        (f"\n\n## Commit message\n\n{body}\n" if body else "\n"))
    return d


def _gh_json(args: list[str]) -> object:
    p = run(["gh", *args])
    if p.rc != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {p.out[:500]}")
    return json.loads(p.out)


def select_review_threads(comments: list[dict], pr_author: str) -> tuple[str, list[dict]]:
    """Pick the reviewed revision with the most top-level human comments; return (commit, threads)."""
    top = [c for c in comments if not c.get("in_reply_to_id") and c.get("path")
           and (c.get("user") or {}).get("type") != "Bot"
           and not str((c.get("user") or {}).get("login", "")).endswith("[bot]")
           and (c.get("user") or {}).get("login") != pr_author
           and (c.get("original_line") or c.get("line"))]
    by_rev: dict[str, list[dict]] = {}
    for c in top:
        by_rev.setdefault(c.get("original_commit_id") or c.get("commit_id"), []).append(c)
    if not by_rev:
        return "", []
    rev = max(by_rev, key=lambda r: len(by_rev[r]))
    threads = [{"file": c["path"], "line": int(c.get("original_line") or c.get("line")),
                "body": str(c.get("body", ""))[:1000], "author": (c.get("user") or {}).get("login", ""),
                "url": c.get("html_url", "")} for c in by_rev[rev]]
    return rev, threads


def scaffold_replay(cfg: RepoConfig, source: Path, pr: int, repo_slug: str = "", task_id: str = "",
                    force: bool = False) -> Path:
    """Create a draft review-replay task from a reviewed GitHub pull request (requires an authenticated `gh`)."""
    repo_slug = repo_slug or _repo_slug(cfg, source)
    if not repo_slug:
        raise ValueError("cannot tell the GitHub repository; pass --repo owner/name or set [repo].url")
    info = _gh_json(["api", f"repos/{repo_slug}/pulls/{pr}"])
    comments = _gh_json(["api", "--paginate", "--slurp", f"repos/{repo_slug}/pulls/{pr}/comments"])
    comments = [c for page in comments for c in (page if isinstance(page, list) else [page])]
    rev, threads = select_review_threads(comments, info["user"]["login"])
    if not threads:
        raise ValueError(f"PR #{pr} has no inline review comments from reviewers")
    git(source, "fetch", "-q", "origin", f"pull/{pr}/head", info["base"]["ref"])
    if git(source, "cat-file", "-e", f"{rev}^{{commit}}").rc != 0:
        raise RuntimeError(f"reviewed commit {rev[:12]} is not available locally; fetch it and retry")
    base = git(source, "merge-base", info["base"]["sha"], rev).out.strip() or info["base"]["sha"]
    task_id = task_id or f"review_replay_pr{pr}"
    d = cfg.tasks_dir / task_id
    if d.exists():
        if not force:
            raise FileExistsError(f"{d} exists (use --force to overwrite)")
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "review.patch").write_text(git(source, "diff", "--binary", "--no-renames", base, rev).out)
    (d / "gold.json").write_text(json.dumps({"pr": pr, "revision": rev, "threads": threads}, indent=1))
    date = str(info.get("merged_at") or info.get("closed_at") or info.get("created_at") or "")[:10]
    _write_task_toml(d, [
        'version = "1.0"', "",
        "[metadata]", 'family = "review"', 'status = "draft"', f'base = "{base}"', f'created = "{date}"',
        f'source = "https://github.com/{repo_slug}/pull/{pr}"', "tags = []", "",
        "[agent]", "timeout_sec = 1800", "",
        "[grader]", 'type = "review_replay"',
        f"review_title = {_toml_str(info.get('title', 'Change under review'))}",
        "# Score-only by default. To make it pass/fail, add e.g.: gate = [\"recall>=0.4\", \"precision>=0.3\"]",
    ])
    (d / "instruction.md").write_text(REVIEW_INSTRUCTION.format(project=cfg.id))
    (d / "AUTHOR_NOTES.md").write_text(
        f"# Author notes: {task_id}\n\nPR: https://github.com/{repo_slug}/pull/{pr} — {info.get('title', '')}\n"
        f"Reviewed revision: {rev}\nBase: {base}\n\n"
        "Edit gold.json: keep threads that ask for a change; remove praise, questions answered by the author, and\n"
        "pure nits. Each remaining thread is a finding a good reviewer should make.\n\n" +
        "\n".join(f"- `{t['file']}:{t['line']}` ({t['author']}): {t['body'][:160]!r}" for t in threads) + "\n")
    return d


REVIEW_INSTRUCTION = """You are reviewing a pull request to {project}. The change under review is the HEAD commit of
this repository (inspect it with `git show HEAD`). Review it as a maintainer would: report every problem that should be
fixed before merge (correctness, numerical robustness, API behaviour, tests, the project's conventions). Do not report
things that are acceptable, and do not modify any files.

End your answer with a fenced ```json block containing an array of findings, each an object
`{{"file": "<repo-relative path>", "line": <line in the new version of the file>, "severity": "blocker|major|minor",
"rule": "<short rule name>", "message": "<one sentence>"}}`.
"""


# ------------------------------------------------------------------------------------------------- mutants

@dataclass
class _Edit:
    start: int
    end: int
    text: str
    op: str
    line: int


def _offsets(src: str) -> list[int]:
    starts, pos = [], 0
    for line in src.splitlines(keepends=True):
        starts.append(pos)
        pos += len(line)
    return starts + [pos]


def _pos(src_lines: list[str], starts: list[int], lineno: int, col_bytes: int) -> int:
    line = src_lines[lineno - 1]
    col = len(line.encode("utf-8")[:col_bytes].decode("utf-8", errors="ignore"))
    return starts[lineno - 1] + col


CMP_SWAP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==", " is not ": " is ",
            " is ": " is not ", " not in ": " in ", " in ": " not in "}
BIN_SWAP = {"+": "-", "-": "+", "*": "/", "/": "*", "//": "/", "%": "*", "**": "*"}


def _candidate_edits(src: str, function: str = "") -> list[_Edit]:
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    starts = _offsets(src)

    def span(node) -> tuple[int, int]:
        return (_pos(lines, starts, node.lineno, node.col_offset), _pos(lines, starts, node.end_lineno,
                                                                         node.end_col_offset))
    roots = [tree]
    if function:
        roots = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == function]
        if not roots:
            raise ValueError(f"function {function!r} not found")
    edits: list[_Edit] = []
    seen = set()
    for root in roots:
        for node in ast.walk(root):
            if id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                a, b = span(node.left)[1], span(node.comparators[0])[0]
                gap = src[a:b]
                for k, v in CMP_SWAP.items():
                    if gap.strip() == k.strip():
                        edits.append(_Edit(a, b, gap.replace(k.strip(), v.strip(), 1), f"cmp_{k.strip()}",
                                           node.lineno))
                        break
            elif isinstance(node, ast.BinOp):
                a, b = span(node.left)[1], span(node.right)[0]
                gap = src[a:b]
                op = gap.strip().strip("()").strip()
                if op in BIN_SWAP:
                    edits.append(_Edit(a, b, gap.replace(op, BIN_SWAP[op], 1), f"binop_{op}", node.lineno))
            elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
                a, b = span(node.values[0])[1], span(node.values[1])[0]
                gap = src[a:b]
                swap = "or" if isinstance(node.op, ast.And) else "and"
                edits.append(_Edit(a, b, re.sub(r"\b(and|or)\b", swap, gap, count=1), f"bool_{swap}", node.lineno))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 \
                    and node.func.id == "abs":
                s, e = span(node)
                a, b = span(node.args[0])
                edits.append(_Edit(s, e, src[a:b], "drop_abs", node.lineno))
            elif isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                swap = {"max": "min", "min": "max", "amax": "amin", "amin": "amax", "argmax": "argmin",
                        "argmin": "argmax", "sum": "mean", "mean": "sum"}.get(name)
                if swap:
                    s, e = span(node.func)
                    text = src[s:e]
                    edits.append(_Edit(s, e, text[: len(text) - len(name)] + swap, f"call_{name}", node.lineno))
            elif isinstance(node, ast.Constant) and type(node.value) in (int, float) and not isinstance(node.value,
                                                                                                   bool):
                s, e = span(node)
                new = node.value + 1 if node.value in (0, 0.0) else (0 if node.value == 1 else node.value + 1)
                edits.append(_Edit(s, e, repr(new), "const", node.lineno))
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                s, e = span(node)
                a, b = span(node.operand)
                edits.append(_Edit(s, e, src[a:b], "drop_neg", node.lineno))
            elif isinstance(node, (ast.If, ast.While)) and not isinstance(node.test, ast.Constant):
                a, b = span(node.test)
                edits.append(_Edit(a, b, f"not ({src[a:b]})", "negate_cond", node.lineno))
            elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
                sl = node.slice
                for part, label in ((sl.lower, "lower"), (sl.upper, "upper")):
                    if part is not None and not isinstance(part, ast.Constant):
                        a, b = span(part)
                        edits.append(_Edit(a, b, f"({src[a:b]}) + 1", f"slice_{label}", node.lineno))
    return edits


def _git_patch(path: str, old: str, new: str) -> str:
    diff = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                f"a/{path}", f"b/{path}", n=3)
    body = "".join(x if x.endswith("\n") else x + "\n\\ No newline at end of file\n" for x in diff)
    return f"diff --git a/{path} b/{path}\n{body}"


def mutants(source_root: Path, path: str, function: str = "", max_n: int = 20,
            base: str = "") -> list[tuple[str, str]]:
    """[(name, patch)] candidate mutants for a Python file, spread across operator kinds. With `base`, the file is
    read at that revision (the task's base), not from the working tree."""
    src = git(source_root, "show", f"{base}:{path}", check=True).out if base else (source_root / path).read_text()
    edits = _candidate_edits(src, function)
    by_op: dict[str, list[_Edit]] = {}
    for e in edits:
        by_op.setdefault(e.op.split("_")[0], []).append(e)
    order: list[_Edit] = []
    while any(by_op.values()) and len(order) < max_n * 3:
        for k in list(by_op):
            if by_op[k]:
                order.append(by_op[k].pop(0))
    out, seen = [], set()
    for e in order:
        new = src[:e.start] + e.text + src[e.end:]
        if new == src or new in seen:
            continue
        try:
            compile(new, path, "exec")
        except SyntaxError:
            continue
        seen.add(new)
        out.append((f"m{len(out) + 1:02d}_{e.op.replace('/', 'div').replace('*', 'mul')}_L{e.line}",
                    _git_patch(path, src, new)))
        if len(out) >= max_n:
            break
    return out


def scaffold_mutation(cfg: RepoConfig, source: Path, task_id: str, path: str, function: str = "",
                      base: str = "") -> Path:
    """Create a draft test-writing task (task.toml, instruction template, oracle_tests/) if it does not exist."""
    import time
    d = cfg.tasks_dir / task_id
    if (d / "task.toml").exists():
        return d
    (d / "oracle_tests").mkdir(parents=True, exist_ok=True)
    head = git(source, "rev-parse", f"{base or 'HEAD'}^{{commit}}", check=True).out.strip()
    what = f"`{function}` in `{path}`" if function else f"`{path}`"
    _write_task_toml(d, [
        'version = "1.0"', "",
        "[metadata]", 'family = "test"', 'status = "draft"', f'base = "{head}"',
        f'created = "{time.strftime("%Y-%m-%d")}"', f"source = {_toml_str('mutants of ' + path)}", "tags = []", "",
        "[agent]", "timeout_sec = 2400", "",
        "[verifier]", "timeout_sec = 1800", "",
        "[grader]", 'type = "mutation"', "min_kill = 0.8", "oracle_min_kill = 1.0",
    ])
    (d / "instruction.md").write_text(_instruction_template(
        "test-writing request",
        f"Target: {what}. Ask for tests of its observable behaviour (edge cases, dtypes, errors), say where tests\n"
        "belong and how they are run, forbid changes to library code, and ask the agent to end its answer with\n"
        '```json {"targets": ["<test file>"]}```.'))
    return d


def write_mutants(task_dir: Path, items: list[tuple[str, str]]) -> list[Path]:
    md = task_dir / "mutants"
    md.mkdir(parents=True, exist_ok=True)
    written = []
    for name, patch in items:
        p = md / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.patch"
        p.write_text(patch)
        written.append(p)
    return written


def prune_mutants(cfg: RepoConfig, source: Path, task_id: str) -> dict:
    """Grade the reference tests against every mutant; move mutants they do not kill to mutants_rejected/."""
    from .grade import apply_oracle, grade_run
    from .workspace import prepare
    t = load_task(cfg.tasks_dir / task_id)
    if t.grader_type != "mutation":
        raise ValueError(f"{task_id} is not a mutation task")
    with tempfile.TemporaryDirectory(prefix="repoevals-prune-") as td:
        rd = Path(td) / "run"
        prepare(cfg, t, rd, source)
        apply_oracle(rd, cfg)
        g = grade_run(rd, cfg, t)
    per = g.get("details", {}).get("mutants") or {}
    if g["status"] == "error" or not per:
        raise RuntimeError(f"could not grade the reference tests: {g.get('errors') or g.get('metrics')}")
    rejected = t.dir / "mutants_rejected"
    moved = []
    for name, status in per.items():
        if not status.startswith("killed"):
            rejected.mkdir(exist_ok=True)
            shutil.move(str(t.dir / "mutants" / f"{name}.patch"), rejected / f"{name}.patch")
            moved.append({"mutant": name, "status": status})
    return {"kept": sum(1 for s in per.values() if s.startswith("killed")), "rejected": moved,
            "baseline_pass": g["metrics"].get("baseline_pass")}


# ------------------------------------------------------------------------------------------------- status

BASE_RE = re.compile(r'^(base\s*=\s*)"([^"]*)"\s*$', re.M)


def pin(cfg: RepoConfig, source: Path, task_ids: list[str] | None = None) -> list[tuple[str, str]]:
    """Rewrite symbolic bases (HEAD, branch names) in task.toml to full commit SHAs."""
    from .task import discover
    done = []
    for t in discover(cfg.tasks_dir, task_ids):
        if re.fullmatch(r"[0-9a-f]{40}", t.base):
            continue
        sha = git(source, "rev-parse", f"{t.base}^{{commit}}", check=True).out.strip()
        f = t.dir / "task.toml"
        text = f.read_text()
        if BASE_RE.search(text):
            f.write_text(BASE_RE.sub(lambda m, sha=sha: f'{m.group(1)}"{sha}"', text, count=1))
            done.append((t.id, sha))
    return done


STATUS_RE = re.compile(r'^(status\s*=\s*)"draft"\s*$', re.M)


def approve(cfg: RepoConfig, task_ids: list[str]) -> list[str]:
    """Set status = "ready" in each task.toml. Refuses tasks whose instruction is still the draft template."""
    done = []
    for tid in task_ids:
        d = cfg.tasks_dir / tid
        t = load_task(d)
        if DRAFT_MARKER in t.instruction:
            raise ValueError(f"{tid}: instruction.md is still the draft template")
        text = (d / "task.toml").read_text()
        if STATUS_RE.search(text):
            (d / "task.toml").write_text(STATUS_RE.sub(r'\1"ready"', text, count=1))
            done.append(tid)
    return done


# ------------------------------------------------------------------------------------------------- playbook

PLAYBOOK_URL = "https://github.com/vchamarthi/repoevals/blob/main/docs/agent-playbook.md"


def playbook_text() -> str:
    """The agent playbook shipped with the package (docs/agent-playbook.md in the source tree)."""
    here = Path(__file__).resolve().parent
    for p in (here / "data" / "agent_playbook.md", here.parent.parent / "docs" / "agent-playbook.md"):
        if p.exists():
            return p.read_text()
    raise FileNotFoundError("agent playbook not found in the installation")


def prompt_text(repo: str, count: int = 12, families: str = "", workdir: str = "", playbook: str = "") -> str:
    fam = families or "fix (most), review (seeded, clean control, replay), test (mutation), numeric if numerical"
    return f"""Create repoevals evaluation tasks for the repository {repo}.

Follow the repoevals task-authoring playbook exactly: {playbook or PLAYBOOK_URL}
(if you cannot open that link, run `repoevals author playbook` and follow its output).

Parameters:
- target repository: {repo}
- number of tasks: {count}
- task mix: {fam}
- working directory: {workdir or "a fresh clone of the repository in the current directory"}

Constraints:
- Every task stays a draft (status = "draft") for human review; do not approve tasks yourself.
- Do not push, open pull requests, or change the project's code outside .agent-evals/ and repoevals.toml unless asked.
- Finish only when `repoevals validate --source .` reports no errors and `repoevals check --repeats 3` reports OK for
  every task you keep, or when you have documented why a task was dropped.
- End with the summary table from .agent-evals/AUTHORING_REPORT.md.
"""


SKILL_HEADER = """---
name: repoevals-task-author
description: Create repoevals evaluation tasks for a repository — configure repoevals, mine bug fixes and reviewed
  pull requests from history, scaffold fix, review, test-writing and numerical tasks, verify them with
  `repoevals check`, and write an authoring report for human review. Use when asked to create, generate or
  author agent evaluation tasks or repoevals tasks.
---

"""

CODEX_HEADER = """---
description: Create repoevals evaluation tasks for a repository (follow the repoevals task-authoring playbook)
argument-hint: REPO=<url-or-path> [COUNT=12] [FAMILIES=...]
---

Arguments: $ARGUMENTS

"""


def install_agent_files(root: Path, claude: bool = True, codex_home: Path | None = None) -> list[Path]:
    """Write the playbook as a Claude Code skill in the repository and/or a Codex custom prompt."""
    text = playbook_text()
    written = []
    if claude:
        p = root / ".claude" / "skills" / "repoevals-task-author" / "SKILL.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(SKILL_HEADER + text)
        written.append(p)
    if codex_home is not None:
        p = codex_home / "prompts" / "repoevals-author.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(CODEX_HEADER + text)
        written.append(p)
    return written
