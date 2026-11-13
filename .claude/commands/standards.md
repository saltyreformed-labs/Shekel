---
description: Run the full Shekel quality gate on the working changes and report results.
allowed-tools: Bash, Read, Grep, Glob, Task
---

Run the project's quality gate on the current changes and report the results
plainly, showing actual command output (not summaries). Do not fix anything yet;
report first.

1. Identify the changed files: `git status --porcelain` and `git diff --stat`.

2. Lint:
   - `pylint app/` (the custom checkers in tools/pylint/ load via .pylintrc).
     Report the score and every message. The standing goal is 10.00/10, zero
     messages;
     `--fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale,shekel-transaction-status-bypass,shekel-ledger-model-bypass,shekel-unclassified-fenced-export,shekel-private-module-import`
     are hard failures regardless of score.
   - If any changed file is under `tests/`, also run
     `pylint tests/ --disable=all --enable=shekel-decimal-from-float` to catch
     float-sourced Decimals in hand-computed assertions.
   - Run the checker's own unit tests: `pytest tools/pylint/tests -c /dev/null -q`.

3. Test the changed code: map each changed `app/` module to its test file under
   `tests/` and run them via `./scripts/test.sh tests/<path> -v`. If the mapping
   is unclear, run the directory. Show the pass/fail summary.

4. Invoke the `code-reviewer` subagent (via the Task tool) on the diff for the
   judgment-level review the linters cannot do (float-on-money boundaries,
   user-scoping/IDOR, CLAUDE.md's transfer rules, DRY/SOLID, test quality).

5. Summarize: what passed, what failed (with output), and the single most
   important thing to address. If everything is green and the review is clean,
   say so explicitly and name what was checked.
