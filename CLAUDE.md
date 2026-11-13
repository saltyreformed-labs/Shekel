# CLAUDE.md

Shekel is a personal budget app organized around pay periods, one per paycheck, rather than calendar
months: every transaction maps to a paycheck, with about two years projected forward.

There is no QA team and no human code reviewer, and the developer works alone. CI
(`.github/workflows/ci.yml`: pylint and the full suite) must pass on the exact merge commit before
the merge queue lands anything in `dev` or `main`, but it is only as good as the tests. A defect you
miss ships to production, and in a budgeting app that means real money is mismanaged.

## Rules

Requirements, not suggestions. Hooks, custom pylint checkers (`tools/pylint/shekel_checkers/`) and
CI enforce many of them, and each says what it refused: fix what a gate flags at the root, never
silence it with a bare disable, and add a checker when a rule is an AST pattern rather than hoping a
reviewer remembers it. A gate is a floor: rules 2, 3, 6, 8, 10 and 13 are judgment no gate checks.

1. **No shortcuts, workarounds or band-aids.** No stubbed `pass` or `TODO`, no hardcoded values, no
   broad `except Exception`. Fix the root cause: an override parameter or a wrapper that routes
   around a wrong computation is a refusal to fix it. If the root cause is out of scope, say so and
   stop; never ship the workaround silently.
2. **Read before you write.** Read the whole file before changing it; never rely on memory or on a
   planning document's line numbers.
3. **No guessing.** If unsure what a function returns, what columns a table has or what state an
   object is in, read the code or ask. Ask before proceeding on ambiguous financial logic.
4. **Never ignore a problem.** A failing test, bug, lint error or logic flaw you find, whether you
   caused it or not, is either fixed or stopped on and reported in full. Never dismiss one as
   pre-existing or unrelated without investigating it.
5. **Never modify a test to make it pass.** If a test fails after your change, your code is wrong:
   its financial assertions were computed by hand. The one exception is the developer confirming the
   expected behavior has changed.
6. **Stay in scope.** Change only code the task needs. Report out-of-scope issues, as cards, but do
   not fix them without approval: unscoped changes are unreviewed changes.
7. **Trace impact before changing an interface.** Before changing a function signature, return
   value, model property or column, grep the whole codebase for its callers, consumers and template
   references, and update every one.
8. **Ask before a design decision.** Where several approaches are valid, map the full option space
   and recommend the most-correct from-scratch design firmly; a financial fork gets a worked numeric
   example per option before the question. Correctness outranks time and effort: never propose an
   option because it is quicker. A card's fix is a proposal, and the root cause decides. An
   unanswered design question is a stop, never a fork to take alone.
9. **Show your work.** Show the actual terminal output of tests and lint, not a summary, so the
   developer can verify every claim.
10. **Understand before you change.** Before changing a function over 20 lines, be able to say what
    it does and why; if you cannot, ask. Never rewrite a function from scratch unless asked.
11. **Debug, do not abandon.** Read the full traceback and fix the specific bug; never throw away a
    correct approach because its first implementation had a bug.
12. **Write complete code.** No placeholder comments ("repeat for the remaining cases"): every line,
    every branch, every mapping entry.
13. **No gold-plating.** Build exactly what was asked: no speculative abstraction, no flexibility
    nobody asked for, no handling of impossible cases. Propose a simpler approach before building
    the complex one.
14. **One source of truth for any value.** A value is derived or stored, and each kind has one home.
    Derived: one walk, one producer, and every caller reaches it. Two spellings that agree today are
    still two spellings (a fifth spelling of one interest expression agreed over 200,000 random
    draws and parted at 500,000, `$565.37` against `$565.36`). Where a layer puts the shared leaf
    out of reach, move the leaf: the placement is the root cause. Stored: the same fact in two
    columns is two sources, so delete one; a value both derived and stored leaves the stored copy a
    stale cache. The tell is an invariant: where a rule says two places must always agree, they are
    one value with two homes and a maintenance contract, and the remedy is to delete a home rather
    than keep them in step. An invariant that cannot be violated because there is nothing to violate
    is worth more than one a reconciler enforces. So the goal of every arc is a defect made
    unrepresentable and its fence (a checker, an allowlist, a reconciler) deleted; the fences ruled
    kept are in `docs/audits/fence_census_2026-10-11.md`, and deleting one is the developer's call.

## Multi-session operation

Several sessions work this repo at once, each in its own worktree (`~/projects/shekel-*`), and none
coordinates the others. Begin implementation work with `/charter`, present a design decision with
`/design-forks`, and stop at a boundary with `/handoff`.

- **The tracker says what to do next.** All live work is cards in the private tracker
  `saltyreformed-labs/shekel-plan`, read and written only through quill (Commands), and the next
  step is `quill next`'s answer. A card's body is its spec. Whether a card shipped is git's answer,
  never the card's state: its done file `plan/ships/N` in the merge of `dev` and `main`.
- **A card is built on its branch `card/N`**, which `quill claim` makes from `dev` with the done
  file as its first commit, so merging the branch ships the card and reverting the merge reopens it.
  The card's tasks, in order, are its plan, and each commit belongs to the task in progress.
- **Each session lands its own work.** It opens its card's pull request into `dev` (`quill pr`) and
  queues it (`gh pr merge N --merge`); the queue lands it once CI passes on the exact merge commit.
  `dev` and `main` take a change no other way: a direct push, or `gh pr merge --admin`, is refused
  for everyone. CI runs on pull requests, queue groups and pushes to `main`, not on pushes to `dev`.
  Pushing your own branch to back up work is always fine, and is not a request to merge.
- **A release is a session of its own**, `/release` in the worktree `~/projects/shekel-release`, on
  the developer's word. It merges a cut of `dev` into `main` by a pull request, then resyncs `dev`
  by a pull request from `main`.
- **A stop leaves its note on the task the next session starts** (`/handoff`), and that session's
  whole prompt is `Resume plan#T`.
- **A session that stops to wait on the developer's answer alerts first**: a PushNotification naming
  the session and the question, in a call of its own, then the question in a later call (calls in
  one block run in no set order). An enqueue the auto-mode classifier refuses is such a question,
  never something to route around.
- **Never `pkill` shared tooling**: kill by PID after reading `/proc/<pid>/cwd` to confirm whose
  process it is.
- **Data**: production data at `/opt/docker/shekel` may be read and copied, but production is never
  written outside the deploy pipeline. Dev and throwaway databases take full CRUD.
- **A security audit**, anything recording a security finding or production's security setup, is
  written in the private repository `saltyreformed-labs/shekel-audits` (`~/projects/shekel-audits`),
  never in this tree, so an open finding is never public.
- **Planning documents lag the code**: the code as committed and recent git history say what
  shipped. Anything under an `archive/` or `historical/` directory governs nothing. Each arc's
  reasoning is its own document (`docs/audits/balance_architecture/README.md`,
  `docs/plans/implementation_plan_*.md`); `docs/plans/verification.md` is what done means for a
  step, and `docs/plans/lessons.md` is what this project has already paid to learn. An old id met in
  a comment, such as `balance:R-BAL38`, names a card `quill show` finds, unless it is a shipped
  step's or a closed finding's: their record is the commit that shipped or closed it.

## Commands

```bash
# Dev server, containerized (the primary workflow; live reload through the bind mount)
docker compose -f docker-compose.dev.yml up -d && docker logs -f shekel-dev-app
flask run                                  # fallback: a host process, outside the container

# Tests: never bare pytest; the full suite takes minutes
./scripts/test.sh                          # full suite
./scripts/test.sh tests/path/test_file.py::test_name -v

# Lint: the gate CI runs
pylint app/ --fail-under=10 --fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale,shekel-transaction-status-bypass,shekel-ledger-model-bypass,shekel-unclassified-fenced-export,shekel-private-module-import

# Migrations
flask db migrate -m "description"
flask db upgrade

# The tracker, through quill (--help lists every command, and each command's --help its flags)
python -m tools.quill.quill next                 # the next step to build
python -m tools.quill.quill claim plan#N         # claim card N: branch card/N, checked out
python -m tools.quill.quill show plan#N          # a card, its comments and its git history
python -m tools.quill.quill file step|finding|ruling|question|task ...
python -m tools.quill.quill start plan#T         # begin task T
python -m tools.quill.quill task-file plan#T     # task T's file, committed with its last piece
python -m tools.quill.quill pr plan#N --title T --body-file B   # N's pull request into dev

# A new lane, from a checkout's root in a new terminal: a worktree ~/projects/shekel-NAME at
# origin/dev with its own venv, and a session in it whose first act claims plan#N
.venv/bin/python -m tools.quill.lane NAME [plan#N]
```

## Architecture

```text
Routes (Blueprints) → Services (no Flask imports) → Models (SQLAlchemy) / Schemas (Marshmallow)
```

**Services are isolated from Flask** -- they take plain data, return plain data, never import
`request`/`session`. Do not violate this boundary.

**Transfers are one row each, and the database holds their rules**, each constraint's comment saying
why (`app/models/transfer.py`, `app/models/transaction_entry.py`,
`app/side_band_infrastructure.py`): a settled transfer's amount is stored once, on the transfer. One
rule is not structural yet, and `balance:X-eu` deletes it:
**every write of a transfer goes through `transfer_service`**. Older comments cite these as transfer
invariants 4 and 5.

**Reference tables: IDs for logic, strings for display only.** Never compare against string `name`
columns in Python or Jinja; the rule and its gates are in `.claude/rules/coding.md`.

**No new package without approval.** Dependencies are declared in `pyproject.toml` and locked in
`uv.lock`, and the edit hook stops on a change to either until you confirm it was approved.

## Tests

Each `./scripts/test.sh` run gets a postgres cluster of its own. It deselects the container-spawning
`tests/test_deploy` tests by default (`-m "not docker"`): they leave no line in the report, so a
green local run says nothing about them, and CI runs them all. The rest of what a run needs loads
from `.claude/rules/testing.md` with any file under `tests/`.

## Standards

A fact lives in one tier. This file states a rule and points; `.claude/rules/*` hold the must-knows
for the files their `paths:` cover and load with them; `docs/coding-standards.md` and
`docs/testing-standards.md` hold the rationale and every dated measurement. The `code-reviewer`
agent holds no copy of them: it loads this file at its start and each rules file as it reads a file
the rule covers (measured 2026-11-12).

## Definition of Done

A task is not complete until all of these hold:

1. The code is implemented in full: no TODOs, no placeholders.
2. Docstrings and comments follow the coding rules.
3. The lint command above passes, with no new messages.
4. Targeted tests pass for the changed files.
5. The full suite passes.
6. The pass and fail counts are shown to the developer.
7. A migration is tested in both directions, upgrade and downgrade.
8. A neutral adversarial review of the design and the diff ran (the card's "Adversarial review"
   task): a fresh subagent grading the change's claims about itself hardest, since its tests cannot
   grade them. Each finding is fixed or filed.
9. Each commit message reads `<type>(<scope>): <what changed>`.

## Style

No Unicode dashes: use periods, commas, semicolons or colons between clauses, and `-` for ranges.
