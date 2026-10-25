---
name: justn-bench
description: Benchmark coding-agent harness x provider/model combinations on tasks built from the user's own Rust/TS/Go commits, record every run, and compare results. Use when the user asks to benchmark, compare, or record how a harness or model performs (bugfix/feature/review/explore/ui tasks, loose vs strict prompts, tokens, time), or to add a benchmark task from a commit.
---

# justn-bench

Personal benchmark. Deterministic work lives in `scripts/bench.py`; you choose
tasks, harness/model, and interpret results. Never grade or record by hand.

`B=<this skill dir>/scripts/bench.py` — run with `python3 $B ...`.

## Run a benchmark

1. The user picks harness and model. Do not substitute. Harness ids are brgr ids
   (`brgr --json doctor` lists them: `local.opencode`, `local.claude-code`,
   `local.codex`, `local.command-code`, ...). Model strings are the harness's own
   (e.g. OpenCode `hive/zai-org/glm-5.3-flash`, Command Code `hive/zai-org/glm-5.3-flash`).
   If a model is not configured in that harness, say so instead of running.
2. `python3 $B task list` and select with `--task` (id globs or kinds, comma separated).
3. Dry run first (no `--yes`): show the user the plan (run count, combos). Execute
   with `--yes` only after the user confirms or already asked to run. These are
   paid model calls.
4. Runs are sequential. Hive is limited to 5 req/s — don't run several bench
   processes against it in parallel.
5. After runs, show `python3 $B compare --by harness,model,variant` (add
   `--task` / `--since` filters as needed). Report pass counts with run counts;
   do not over-read single runs. Suggest `--repeat` when the sample is tiny.

## Add a task

- bugfix: `--commit` = the fix commit (its tests are kept, the fix reverted).
- feature: `--commit` = the feature commit (tests hidden until grading).
- review: `--commit` = the bug-introducing commit; `--must-mention` = answer-key
  tokens (file, function, or behavior words from the later fix).
- explore: `--must-mention` = files/functions the answer must name.
- ui: no auto grading; the user rates with `python3 $B rate <run_id> <score>`.

Always write both prompts: `--loose` (one vague line, like a real hurried request)
and `--strict` (files, constraints, done criteria, "do not edit tests"). Set
`--setup-cmd` when fresh clones need deps (e.g. `npm ci --prefer-offline`) and a
`--test-cmd` that builds what the tests import. bugfix/feature are gated: the
command refuses a task unless tests fail at the task state and pass at the solution.

## Files

- Results: `~/.agents/bench/results.jsonl`; per-run artifacts in
  `~/.agents/bench/work/<run_id>/` (answer.md, candidate.diff, test-output.txt).
- Tasks: `tasks/<id>/task.json` in this repo.
- Token counts are best-effort from local harness logs; `unknown`/`*:none` means
  not found, not zero.

The README warns readers not to trust these numbers much. Keep that tone when
summarizing results.
