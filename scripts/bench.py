#!/usr/bin/env python3
"""justn-bench: personal harness x model benchmark runner.

Stdlib only. Runs agents through brgr, grades deterministically, appends one
JSON line per run to results.jsonl, and pivots the file for comparison.
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = Path(os.environ.get("JUSTN_BENCH_TASKS", ROOT / "tasks"))
HOME = Path(os.environ.get("JUSTN_BENCH_HOME", Path.home() / ".agents" / "bench"))
RESULTS = HOME / "results.jsonl"
WORK = HOME / "work"

KINDS = ("bugfix", "feature", "review", "explore", "ui")
VARIANTS = ("loose", "strict")
TEST_PATTERNS = (
    "*_test.go", "*_test.rs", "tests/*", "*/tests/*", "*.test.ts", "*.test.tsx",
    "*.spec.ts", "*.spec.tsx", "*__tests__*", "test_*.py", "*_test.py",
)


# ---------- small helpers ----------

def sh(cmd, cwd=None, check=True, timeout=None, capture=True):
    r = subprocess.run(
        cmd, cwd=cwd, shell=isinstance(cmd, str), text=True, timeout=timeout,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if check and r.returncode != 0:
        raise SystemExit(f"command failed ({r.returncode}): {cmd}\n{r.stdout or ''}")
    return r


def git(args, cwd, check=True):
    return sh(["git", *args], cwd=cwd, check=check).stdout.strip()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_task(task_id):
    p = TASKS_DIR / task_id / "task.json"
    if not p.exists():
        raise SystemExit(f"unknown task: {task_id}")
    return json.loads(p.read_text())


def all_tasks():
    return [json.loads(p.read_text()) for p in sorted(TASKS_DIR.glob("*/task.json"))]


def is_test_path(path):
    return any(fnmatch.fnmatch(path, pat) for pat in TEST_PATTERNS)


def clone_at(repo, commit, dest):
    """Fresh clone of a local repo checked out at commit (detached)."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sh(["git", "clone", "-q", "--no-hardlinks", str(repo), str(dest)])
    git(["checkout", "-q", "--detach", commit], dest)
    git(["config", "user.email", "bench@localhost"], dest)
    git(["config", "user.name", "justn-bench"], dest)
    return dest


def overlay_paths(dest, commit, paths):
    if paths:
        git(["checkout", commit, "--", *paths], dest)


def commit_all(dest, msg):
    git(["add", "-A"], dest)
    git(["commit", "-q", "--allow-empty", "-m", msg], dest)


def run_tests(dest, cmd, timeout):
    t = time.time()
    try:
        r = sh(cmd, cwd=dest, check=False, timeout=timeout)
        ok, out = r.returncode == 0, r.stdout or ""
    except subprocess.TimeoutExpired:
        ok, out = False, "TIMEOUT"
    return ok, out[-4000:], int((time.time() - t) * 1000)


def prepare_state(task, dest, with_hidden_tests=False):
    """Build the task's starting state in dest and return it.

    bugfix: base + visible tests. feature: base only (tests hidden until grading).
    review/explore/ui: base as-is.
    """
    clone_at(task["repo"], task["base_commit"], dest)
    if task["kind"] == "bugfix" or (with_hidden_tests and task["kind"] == "feature"):
        overlay_paths(dest, task["solution_commit"], task.get("test_paths", []))
    commit_all(dest, f"bench task state: {task['id']}")
    if task.get("setup_cmd"):
        # untracked by design: deps for grading only (brgr worktrees get tracked files)
        sh(task["setup_cmd"], cwd=dest, timeout=900)
        with open(dest / ".git/info/exclude", "a") as f:
            f.write("node_modules\ntarget\n")
    return dest


# ---------- task add ----------

def cmd_task_add(a):
    repo = Path(a.repo).expanduser().resolve()
    sol = git(["rev-parse", a.commit], repo)
    base = git(["rev-parse", f"{sol}^"], repo)
    changed = git(["diff", "--name-only", base, sol], repo).splitlines()
    test_paths = a.test_path or [p for p in changed if is_test_path(p)]
    task = {
        "id": a.id, "kind": a.kind, "lang": a.lang, "repo": str(repo),
        "base_commit": base, "solution_commit": sol,
        "subject": git(["log", "-1", "--format=%s", sol], repo),
        "test_cmd": a.test_cmd, "test_paths": test_paths, "setup_cmd": a.setup_cmd,
        "timeout_s": a.timeout, "prompts": {"loose": a.loose, "strict": a.strict},
        "answer": {"must_mention": a.must_mention or [], "threshold": a.threshold},
        "added": now_iso(),
    }
    if a.kind in ("bugfix", "feature"):
        if not a.test_cmd or not test_paths:
            raise SystemExit("bugfix/feature need --test-cmd and test paths (auto-detected or --test-path)")
        gate = WORK / "_gate" / a.id
        prepare_state(task, gate, with_hidden_tests=True)
        fail_ok, fail_out, _ = run_tests(gate, a.test_cmd, a.timeout)
        clone_at(repo, sol, gate)
        if a.setup_cmd:
            sh(a.setup_cmd, cwd=gate, timeout=900)
        pass_ok, pass_out, _ = run_tests(gate, a.test_cmd, a.timeout)
        shutil.rmtree(gate, ignore_errors=True)
        if fail_ok or not pass_ok:
            print(f"gate FAILED: tests at task state passed={fail_ok} (want False), "
                  f"at solution passed={pass_ok} (want True)")
            print("--- task-state output ---\n" + fail_out[-1500:])
            print("--- solution output ---\n" + pass_out[-1500:])
            raise SystemExit(1)
        print("gate ok: fails at task state, passes at solution")
    elif a.kind in ("review", "explore") and not a.must_mention:
        raise SystemExit("review/explore need --must-mention (answer key)")
    d = TASKS_DIR / a.id
    if d.exists() and not a.force:
        raise SystemExit(f"task exists: {a.id} (use --force)")
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n")
    print(f"added {a.id} ({a.kind}, {len(test_paths)} test paths)")


def cmd_task_list(a):
    for t in all_tasks():
        print(f"{t['id']:32} {t['kind']:8} {t['lang']:5} {Path(t['repo']).name:18} {t['subject'][:60]}")


# ---------- token lookup (best effort, keyed by worktree path) ----------

def tokens_for(harness, workspace):
    try:
        h = harness.removeprefix("local.")
        if h == "opencode":
            return _tok_opencode(workspace)
        if h == "claude-code":
            return _tok_claude(workspace)
        if h == "codex":
            return _tok_codex(workspace)
        if h == "command-code":
            return _tok_commandcode(workspace)
    except Exception as e:  # noqa: BLE001 - token lookup must never fail a run
        return {"source": f"error: {e}"}
    return {"source": "unknown"}


def _tok_opencode(ws):
    db = Path.home() / ".local/share/opencode/opencode.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = []
    for table in ("session_v2", "session"):
        try:
            rows += con.execute(
                f"select tokens_input, tokens_output, tokens_reasoning, tokens_cache_read from {table} where directory = ?",
                (ws,)).fetchall()
        except sqlite3.OperationalError:
            pass
    if not rows:
        return {"source": "opencode:none"}
    i, o, r, c = (sum(x[k] for x in rows) for k in range(4))
    return {"source": "opencode", "input": i, "output": o + r, "cache_read": c}


def _tok_claude(ws):
    enc = re.sub(r"[^A-Za-z0-9]", "-", ws)
    seen, i, o, c = set(), 0, 0, 0
    files = glob.glob(str(Path.home() / ".claude/projects" / enc / "*.jsonl"))
    for f in files:
        for line in open(f, encoding="utf-8", errors="ignore"):
            if '"usage"' not in line:
                continue
            try:
                m = json.loads(line).get("message") or {}
            except json.JSONDecodeError:
                continue
            u, mid = m.get("usage"), m.get("id")
            if not u or mid in seen:
                continue
            seen.add(mid)
            i += u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            o += u.get("output_tokens", 0)
            c += u.get("cache_read_input_tokens", 0)
    if not files:
        return {"source": "claude:none"}
    return {"source": "claude", "input": i, "output": o, "cache_read": c}


def _tok_codex(ws):
    best = None
    for f in sorted(glob.glob(str(Path.home() / ".codex/sessions/*/*/*/rollout-*.jsonl")))[-400:]:
        txt = open(f, encoding="utf-8", errors="ignore").read()
        if f'"cwd":"{ws}"' not in txt:
            continue
        for line in txt.splitlines():
            if '"token_count"' in line:
                try:
                    info = json.loads(line)["payload"]["info"]
                    if info:
                        best = info["total_token_usage"]
                except (KeyError, json.JSONDecodeError, TypeError):
                    pass
    if not best:
        return {"source": "codex:none"}
    return {"source": "codex", "input": best["input_tokens"] - best.get("cached_input_tokens", 0),
            "output": best["output_tokens"], "cache_read": best.get("cached_input_tokens", 0)}


def _tok_commandcode(ws):
    slug = re.sub(r"[^a-z0-9]+", "-", ws.lower()).strip("-")
    i = o = c = 0
    found = False
    for d in glob.glob(str(Path.home() / ".commandcode/projects/*")):
        if not Path(d).name.endswith(slug[-40:]):
            continue
        for f in glob.glob(d + "/*.jsonl"):
            if f.endswith(".checkpoints.jsonl"):
                continue
            for line in open(f, encoding="utf-8", errors="ignore"):
                for k, v in re.findall(r'"(inputTokens|outputTokens|cacheReadTokens)":(\d+)', line):
                    found = True
                    v = int(v)
                    if k == "inputTokens":
                        i += v
                    elif k == "outputTokens":
                        o += v
                    else:
                        c += v
    if not found:
        return {"source": "command-code:none"}
    return {"source": "command-code", "input": i, "output": o, "cache_read": c}


# ---------- run ----------

def brgr(args, timeout=None):
    r = sh(["brgr", "--json", *args], check=False, timeout=timeout)
    out = (r.stdout or "").strip()
    for cand in (out, out.splitlines()[-1] if out else ""):
        try:
            return r.returncode, json.loads(cand)
        except json.JSONDecodeError:
            pass
    return r.returncode, {"raw": out[-2000:]}


def grade(task, diff, answer, run_dir):
    kind = task["kind"]
    if kind in ("bugfix", "feature"):
        g = prepare_state(task, run_dir / "grade", with_hidden_tests=False)
        if diff.strip():
            p = run_dir / "candidate.diff"
            p.write_text(diff)
            r = sh(["git", "apply", "--whitespace=nowarn", str(p)], cwd=g, check=False)
            if r.returncode != 0:
                return {"pass": False, "score": 0.0, "note": "diff did not apply: " + r.stdout[-300:]}
        # restore graders' tests so an agent can't pass by editing them
        overlay_paths(g, task["solution_commit"], task.get("test_paths", []))
        ok, out, ms = run_tests(g, task["test_cmd"], task["timeout_s"])
        (run_dir / "test-output.txt").write_text(out)
        return {"pass": ok, "score": 1.0 if ok else 0.0, "test_ms": ms}
    if kind in ("review", "explore"):
        keys = task["answer"]["must_mention"]
        low = answer.lower()
        hit = [k for k in keys if k.lower() in low]
        score = len(hit) / len(keys) if keys else 0.0
        return {"pass": score >= task["answer"].get("threshold", 1.0), "score": round(score, 3),
                "missed": [k for k in keys if k not in hit]}
    return {"pass": None, "score": None, "note": "ui: rate manually with `bench rate`"}


def diff_lines(diff):
    return sum(1 for l in diff.splitlines() if (l.startswith("+") or l.startswith("-"))
               and not l.startswith(("+++", "---")))


def select_tasks(pattern):
    pats = pattern.split(",")
    return [t for t in all_tasks() if any(fnmatch.fnmatch(t["id"], p) or t["kind"] == p for p in pats)]


def cmd_run(a):
    tasks = select_tasks(a.task)
    variants = a.variant.split(",")
    plan = [(t, v, n) for t in tasks for v in variants for n in range(a.repeat)]
    print(f"plan: {len(plan)} runs · harness={a.harness} model={a.model or '(harness default)'} "
          f"effort={a.effort or '-'}")
    for t, v, n in plan:
        print(f"  {t['id']} [{v}] #{n + 1}")
    if not plan:
        raise SystemExit("no tasks matched")
    if not a.yes:
        print("dry run only. re-run with --yes to execute (paid model calls).")
        return
    for t, v, n in plan:
        run_one(a, t, v)


def run_one(a, task, variant):
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    run_dir = WORK / run_id
    src = prepare_state(task, run_dir / "src")
    prompt = task["prompts"][variant]
    if task["kind"] in ("review", "explore"):
        prompt += "\n\nDo not modify files. Answer in your final message."
    args = ["run", "--headless", "--harness", a.harness, "--capture-diff", "--capture-logs",
            "--workspace", str(src), "--deadline-seconds", str(a.deadline)]
    if a.model:
        args += ["--model", a.model]
    if a.effort:
        args += ["--effort", a.effort]
    t0 = time.time()
    rc, rec = brgr([*args, prompt])
    task_id, workspace = rec.get("task_id"), rec.get("workspace", "")
    row = {"run_id": run_id, "ts": now_iso(), "task": task["id"], "kind": task["kind"],
           "lang": task["lang"], "variant": variant, "harness": a.harness,
           "model": a.model or "default", "effort": a.effort or None, "brgr_task": task_id}
    if not task_id:
        row.update({"status": "launch_failed", "pass": False, "error": str(rec)[:500]})
        return record(row, run_dir)
    brgr(["wait", task_id, "--timeout-seconds", str(a.deadline + 60)], timeout=a.deadline + 120)
    row["wall_ms"] = int((time.time() - t0) * 1000)
    _, res = brgr(["result", task_id])
    outcome = (res.get("result") or {}).get("outcome", "unknown")
    diff = answer = ""
    for art in res.get("artifacts", []):
        mt = art.get("reference", {}).get("media_type", "")
        if mt == "text/x-diff":
            diff = art.get("text") or ""
        elif mt == "text/x-brgr-stdout":
            answer = art.get("text") or ""
    (run_dir / "answer.md").write_text(answer)
    row["status"] = outcome
    row["diff_lines"] = diff_lines(diff)
    row.update(grade(task, diff, answer, run_dir))
    row["tokens"] = tokens_for(a.harness, workspace)
    row["ux"] = None
    brgr(["reject", "--reason", f"justn-bench {run_id}", "--include-ignored", task_id])
    if not a.keep:
        for sub in ("src", "grade"):
            shutil.rmtree(run_dir / sub, ignore_errors=True)
    return record(row, run_dir)


def record(row, run_dir):
    HOME.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "row.json").write_text(json.dumps(row, indent=2, ensure_ascii=False))
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tok = row.get("tokens") or {}
    print(f"  -> {row['task']} [{row['variant']}] pass={row.get('pass')} score={row.get('score')} "
          f"out_tok={tok.get('output', '?')} wall={row.get('wall_ms', 0) // 1000}s  ({row['run_id']})")
    return row


# ---------- rate / compare ----------

def read_rows():
    if not RESULTS.exists():
        return []
    return [json.loads(l) for l in RESULTS.read_text().splitlines() if l.strip()]


def cmd_rate(a):
    rows = read_rows()
    hit = False
    for r in rows:
        if r["run_id"] == a.run_id:
            r["ux"] = a.score
            if a.note:
                r["ux_note"] = a.note
            hit = True
    if not hit:
        raise SystemExit(f"no run {a.run_id}")
    tmp = RESULTS.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(RESULTS)
    print(f"rated {a.run_id}: {a.score}")


def cmd_compare(a):
    rows = read_rows()
    if a.task:
        pats = a.task.split(",")
        rows = [r for r in rows if any(fnmatch.fnmatch(r["task"], p) or r["kind"] == p for p in pats)]
    if a.since:
        rows = [r for r in rows if r["ts"] >= a.since]
    keys = a.by.split(",")
    groups = {}
    for r in rows:
        groups.setdefault(tuple(str(r.get(k)) for k in keys), []).append(r)
    head = [*keys, "runs", "pass", "score", "out_tok", "in_tok", "wall", "diff", "ux"]
    table = []
    for g, rs in sorted(groups.items()):
        graded = [r for r in rs if r.get("pass") is not None]
        passed = sum(1 for r in graded if r["pass"])
        scores = [r["score"] for r in rs if isinstance(r.get("score"), (int, float))]
        outs = [r["tokens"]["output"] for r in rs if isinstance((r.get("tokens") or {}).get("output"), int)]
        ins = [r["tokens"]["input"] for r in rs if isinstance((r.get("tokens") or {}).get("input"), int)]
        walls = [r["wall_ms"] for r in rs if r.get("wall_ms")]
        diffs = [r["diff_lines"] for r in rs if r.get("diff_lines") is not None]
        uxs = [r["ux"] for r in rs if isinstance(r.get("ux"), (int, float))]
        avg = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
        fmt_k = lambda x: "-" if x is None else (f"{x / 1000:.1f}k" if x >= 1000 else f"{x:.0f}")  # noqa: E731
        table.append([*g, str(len(rs)), f"{passed}/{len(graded)}" if graded else "-",
                      "-" if avg(scores) is None else f"{avg(scores):.2f}",
                      fmt_k(avg(outs)), fmt_k(avg(ins)),
                      "-" if avg(walls) is None else f"{avg(walls) / 1000:.0f}s",
                      "-" if avg(diffs) is None else f"{avg(diffs):.0f}",
                      "-" if avg(uxs) is None else f"{avg(uxs):.1f}"])
    if a.json:
        print(json.dumps([dict(zip(head, row)) for row in table], ensure_ascii=False, indent=2))
        return
    w = [max(len(str(x)) for x in col) for col in zip(head, *table)] if table else [len(h) for h in head]
    print("  ".join(h.ljust(n) for h, n in zip(head, w)))
    for row in table:
        print("  ".join(str(x).ljust(n) for x, n in zip(row, w)))
    if not table:
        print("(no results yet)")


# ---------- cli ----------

def main():
    p = argparse.ArgumentParser(prog="bench", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("task").add_subparsers(dest="tcmd", required=True)
    ta = t.add_parser("add", help="create a task from a commit (bugfix/feature are gated fail->pass)")
    ta.add_argument("--id", required=True)
    ta.add_argument("--repo", required=True)
    ta.add_argument("--commit", required=True, help="solution commit (fix/feature); for review: the bug-introducing commit")
    ta.add_argument("--kind", required=True, choices=KINDS)
    ta.add_argument("--lang", required=True, choices=("rust", "ts", "go", "other"))
    ta.add_argument("--loose", required=True, help="vague one-liner prompt")
    ta.add_argument("--strict", required=True, help="precise prompt with files, constraints, done criteria")
    ta.add_argument("--test-cmd")
    ta.add_argument("--setup-cmd", help="run in fresh checkouts before tests, e.g. 'ln -s ~/dev/x/node_modules .'")
    ta.add_argument("--test-path", action="append", help="override auto-detected test paths (repeatable)")
    ta.add_argument("--must-mention", action="append", help="answer key token for review/explore (repeatable)")
    ta.add_argument("--threshold", type=float, default=1.0)
    ta.add_argument("--timeout", type=int, default=600, help="test timeout seconds")
    ta.add_argument("--force", action="store_true")
    ta.set_defaults(fn=cmd_task_add)
    tl = t.add_parser("list")
    tl.set_defaults(fn=cmd_task_list)

    r = sub.add_parser("run", help="run tasks on one harness/model (dry run unless --yes)")
    r.add_argument("--harness", required=True, help="brgr harness id, e.g. local.opencode")
    r.add_argument("--model")
    r.add_argument("--effort")
    r.add_argument("--task", default="*", help="id glob(s) or kind(s), comma separated")
    r.add_argument("--variant", default="loose,strict")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--deadline", type=int, default=1200, help="per-run seconds")
    r.add_argument("--keep", action="store_true", help="keep run checkouts")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(fn=cmd_run)

    ra = sub.add_parser("rate", help="record a manual UX score for a run")
    ra.add_argument("run_id")
    ra.add_argument("score", type=float)
    ra.add_argument("--note")
    ra.set_defaults(fn=cmd_rate)

    c = sub.add_parser("compare", help="pivot results.jsonl")
    c.add_argument("--by", default="harness,model,variant")
    c.add_argument("--task", help="filter: id glob(s) or kind(s)")
    c.add_argument("--since", help="ISO date prefix, e.g. 2026-10-01")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_compare)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
