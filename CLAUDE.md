# CLAUDE.md

Shekel is a personal budget app organized around **pay periods** (biweekly paychecks) rather than
calendar months. Every transaction maps to a specific paycheck with ~2-year forward projections.

**Stack:** Python 3.12+ · Flask 3.1 · SQLAlchemy 2.0 · PostgreSQL (multi-schema) · Jinja2 · HTMX ·
Bootstrap 5

**YOU ARE THE ONLY SAFEGUARD.** This project has no QA team and no human code reviewer. CI
(`.github/workflows/ci.yml`: pylint + the full pytest suite) runs on every pull request and on
pushes to `main`, and the merge queue on `dev` and on `main` lands nothing until that
`lint-and-test` check is green on the exact merge commit it lands. CI is therefore an enforced
pre-merge gate -- but it is only as good as the tests, and no human will catch a bad assertion or a
missing case for you. The developer is a solo operator. If you miss a bug, skip an edge case, or
take a shortcut, that defect ships to production. In a budgeting app, that means real money is
mismanaged. Treat every line of code as if someone's rent payment depends on it being correct.

## Rules

Requirements, not suggestions. Several are now backed by deterministic gates (see Automated
enforcement); where a gate enforces a rule, fix what it flags at the root rather than silencing it.

1. **No shortcuts, workarounds, or band-aids.** No stubbed `pass` or `TODO`, no hardcoded values, no
   broad `except Exception` (gate: pylint `broad-exception-caught`). Fix root causes, not symptoms;
   the correct solution is the only acceptable one.

2. **Read before you write.** Read the ENTIRE file before changing it. Do not rely on memory or line
   numbers from planning documents.

3. **No guessing.** If uncertain, read the code or ask the developer. Do not assume what a function
   returns, what columns a table has, or what state an object is in. For ambiguous financial logic,
   ask before proceeding.

4. **NEVER ignore a problem.** If you find a failing test, bug, linter error, or logic flaw --
   whether you caused it or not -- you MUST either (a) fix it, or (b) stop and report it with full
   details. There is no third option. Do not dismiss failures as "pre-existing." Do not say
   "unrelated to my changes" without investigating and reporting. The developer has no one else to
   catch these.

5. **NEVER modify a test to make it pass.** If a test fails after your change, your code is wrong,
   not the test. Financial assertions were computed by hand. Fix your code. The only exception is
   when the developer explicitly confirms the expected behavior has changed.

6. **Stay in scope.** Only modify code related to the current task. Report out-of-scope issues but
   do not fix them without approval. Unscoped changes are unreviewed changes.

7. **Trace impact before changing interfaces.** Before modifying any function signature, return
   value, model property, or column definition, grep the entire codebase for all callers, consumers,
   and template references. Update every one.

8. **Ask before making design decisions.** If multiple valid approaches exist (adding a column vs. a
   table, denormalizing vs. not), present options with tradeoffs. Do not make architectural
   decisions unilaterally.

9. **Show your work.** Show actual terminal output from tests and linting, not summaries. The
   developer must be able to verify your claims from the output you provide.

10. **Understand before you change.** Before modifying any function over 20 lines, explain to
    yourself what it does and why. If you cannot explain it, you do not understand it. Ask the
    developer. NEVER rewrite a function from scratch unless explicitly asked.

11. **Debug, do not abandon.** When code errors, read the full traceback. Fix the specific bug. Do
    not throw away a correct approach because your first implementation had a bug.

12. **Write complete code.** Never use placeholder comments like "repeat for remaining cases" or
    "similar for other types." Write every line, every branch, every mapping entry.

13. **No gold-plating.** Implement exactly what was requested. No speculative abstractions, no
    "flexibility" or "configurability" that was not asked for, no error handling for impossible
    scenarios. If a simpler approach exists, propose it before building the complex one. If 200
    lines could be 50, rewrite it.

14. **ONE SOURCE OF TRUTH FOR ANY VALUE.** A value is either DERIVED or STORED, and each kind has
    exactly one home. **Derived: ONE WALK** -- one producer, and every caller reaches it. Two
    spellings that agree today are still two spellings, so agreement is not the test: a fifth
    spelling of one interest expression agreed over 200,000 randomised draws and parted at 500,000,
    `$565.37` against `$565.36`. Where a layer puts the shared leaf out of reach, MOVE THE LEAF --
    the placement is the root cause and the duplication is its symptom. **Stored: ONE PLACE** -- the
    same fact in two columns is two sources, so delete one. A value that is derived AND stored has
    both problems, and the stored copy is a stale cache with no reconciler: that is what the
    cash-balance arc exists to delete. **The tell is an INVARIANT** -- where a rule says two places
    must always agree, they are one value with two homes and a maintenance contract, and the remedy
    is to delete a home rather than keep them in step. An invariant that cannot be violated because
    there is nothing to violate is worth more than one a reconciler enforces. Rulings `balance:R-JA`
    (the rule), `R-IZ` (the derived half) and `R-IY` (a derivable column is deleted, not
    maintained), developer 2026-09-02.

## Design doctrine

The standing answers to the questions every session eventually asks. These are rules, not mood, and
the developer should never have to re-request them.

- **Root cause only.** A band-aid, an override parameter, or a wrapper that routes around a wrong
  computation is a refusal to fix it. If the root cause is out of scope, say so and stop; never ship
  the workaround silently.
- **The goal state of every arc is that its fences are structurally unnecessary.** A checker, an
  allowlist, or an invariant a reconciler enforces is scaffolding around a defect. The work is to
  make the defect unrepresentable, then delete the fence. The fences ruled KEEP are
  `docs/audits/fence_census_2026-10-11.md`, and a step that would delete one asks the developer
  first (R-BAL268).
- **On a design fork, map the FULL option space, then give a firm recommendation.** Lead with the
  most-correct from-scratch design; sequencing manages risk, the design does not compromise for it.
  A financial fork gets a worked numeric example per option BEFORE the question is asked.
- **Correctness outranks time and effort.** Never propose the quicker option because it is quicker.
  An unanswered design question is a STOP, not a fork to take unilaterally.
- **A neutral adversarial review precedes every commit** -- a fresh subagent over the design and the
  diff, grading the fix's own claims about itself hardest (they are the one thing its tests cannot
  grade).

Two skills re-arm this per task: `/charter` (executing work) and `/design-forks` (presenting a
decision).

## Multi-session operation

Several Claude sessions regularly work this repo at once, each in its own worktree
(`~/projects/shekel-*`), with one session acting as COORDINATOR.

- **PRs and merges are coordinated.** When multiple sessions are active, the coordinator session
  opens, orders and merges every PR. **Any session may `git push` its own branch to back up work**
  (developer ruling 2026-09-02); a pushed branch is not a handoff and not a request to merge.
- **GitHub numbers the cards, so there is no id to reserve.** A step, finding, ruling or question is
  filed as a card (`quill file`, Common Commands) and its number, `plan#N`, is GitHub's.
- **Suite runs no longer need coordinating, and there is no slot to take.** `./scripts/test.sh`
  gives every run a postgres cluster of its own, so no run can restart, corrupt or collide with
  another -- in one worktree or many. What concurrent runs still share is this host's cores, which
  is a resource fact rather than a defect: the wrapper PRINTS any other live pytest and its
  worktree, then proceeds. The measurement and the argument: `.claude/rules/testing.md`.
- **A question to the developer sends an alert first** (**R-BAL282**: "an alert whenever a session
  stops to wait on your answer to a question"). Before each AskUserQuestion the session sends a
  PushNotification naming itself and the question in plain words, in a call of its own; the question
  follows in a later call, because it blocks the turn and two calls in one block run in no set
  order. The tool skips the push while the developer's terminal is active.
- **Never `pkill` shared tooling.** Kill by PID after reading `/proc/<pid>/cwd` to confirm whose
  process it is.
- **Data grants** (developer grant, restated 2026-09-02): production data at `/opt/docker/shekel`
  may be read and copied; dev and throwaway databases take full CRUD. Production itself is never
  written outside the deploy pipeline.

## Automated enforcement

Many rules above are backed by deterministic gates, not just prose. Fix what a gate flags at the
root; never silence it with a bare disable.

- **Per-edit hooks (`scripts/hooks/`)** lint each `app/`/`scripts/` Python edit and hard-block on
  errors and the custom checkers `shekel-decimal-from-float` / `shekel-refname-compare`; templates
  and `requirements.txt` have their own guards.
- **SessionStart hook** (`scripts/hooks/session-start.sh`) prints the session's checkout and branch
  into context at session start, a warning when the shared main checkout is pinned to a release
  branch, a pointer to the Multi-session doctrine, and one to `python -m tools.quill.quill next` for
  the next step (it reads no network, so a start never waits on the tracker).
- **PR-guard hook** (`scripts/hooks/guard-pr-actions.sh`) turns `gh pr create` / `gh pr merge` into
  a permission prompt outside the coordinator session (which exports `SHEKEL_PR_COORDINATOR=1`);
  `git push` is never gated. Advisory by design: an ask decision, never a deny.
- **Stop hook** runs full `pylint app/` -- the only place cross-file `duplicate-code` is caught --
  and hard-blocks once `scripts/hooks/ENFORCE_PYLINT_FLOOR` exists (the 10.00/10 lock-in).
- **Custom checkers:** `tools/pylint/shekel_checkers/` (+ tests), loaded via `.pylintrc`. Add one
  when a rule is an AST pattern rather than hoping a reviewer remembers it.
- **CI + pre-commit** run `pylint app/` (checkers as hard `--fail-on`) and the full suite on every
  PR, and CI's `tools` job runs every tools package's own tests; `useless-suppression` is on, so a
  disable that suppresses nothing is itself a finding.
- **The landing check** (`tools/quill/landing.py`, CI's `landing` job) holds the ONE write rule for
  the plan's records in git, judged on the merge that lands: a card's done file `plan/ships/N` is
  added or changed only from its own branch `card/N`, a finding's also from its owner step's branch
  (plan#1777), and a task's file `plan/tasks/T` only from its card's branch (R-BAL386), but for a
  line carried from the other integration branch (a release or a resync) and, while it lives, the
  records-only door into `main` for a card never claimed (R-BAL363); a change that writes no line of
  a done file may not change what `dev` and `main` together say of that card (R-BAL385, R-BAL390); a
  pull request from `card/N` is refused while a task under N that was not dropped lacks its file, or
  while N or a step above it owns an open finding neither marked fixed nor handed on (plan#1777,
  plan#1784); and a change to what production runs must add, delete or change a card's done file,
  from any branch, a release or a resync passing only with the done files it carries (R-BAL362): the
  files the image holds, as Docker reads its ignore file (`.dockerignore`, R-BAL364's one list of
  them), and the files outside the image that build or run production, which
  `tools/quill/_production.py` lists, their one list (R-BAL400, R-BAL401). Its module docstring
  states every rule.
- **Archived documents** (`tools/ci/archive.py`): every document under an `archive/` or
  `historical/` directory opens with the `> **ARCHIVED.` banner, and CI refuses one that does not.
- **Judgment the linters cannot mechanize** (float-on-money boundaries, IDOR, transfer invariants,
  DRY/SOLID, test quality) is the `code-reviewer` subagent and the `/standards` command.

A gate is a floor, not a ceiling: the judgment rules (2, 3, 6, 8, 10, 13) still apply.

## Common Commands

```bash
# Dev server (containerized -- the primary workflow since 2026-06-12;
# full prod parity: entrypoint pipeline, shekel_app role, redis
# rate limiting, hardened rootfs; live reload via the bind mount)
docker compose -f docker-compose.dev.yml up -d && docker logs -f shekel-dev-app

# Dev server fallback (host process; owner-role DB, no entrypoint gates)
flask run

# Tests -- the full suite takes MINUTES; see the Tests section and docs/testing-standards.md
./scripts/test.sh                             # full suite (in a throwaway cluster of its own)
./scripts/test.sh tests/path/test_file.py::test_name -v  # single test (fast feedback)
python scripts/build_test_db_image.py         # rebuild the baked test-db image by hand (the wrapper does it)

# Lint (custom checkers load via .pylintrc; same gate CI enforces)
pylint app/ --fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale,shekel-transaction-status-bypass,shekel-ledger-model-bypass,shekel-unclassified-fenced-export,shekel-private-module-import

# Database migrations
flask db migrate -m "description"
flask db upgrade

# The plan: cards in the private tracker saltyreformed-labs/shekel-plan, read and written only
# through quill (plan#N or N names a card; each command's --help gives its every flag)
python -m tools.quill.quill next [--arc ARC]          # the next step to build
python -m tools.quill.quill claim plan#N              # claim card N: branch card/N, checked out
python -m tools.quill.quill show plan#N               # a card, its comments and its git history
python -m tools.quill.quill file step|finding|ruling|question --arc ARC --title NAME ...
python -m tools.quill.quill file task --parent plan#N ...    # a task: a piece inside N's one PR
python -m tools.quill.quill task-file plan#T          # write task T's file on its card's branch
python -m tools.quill.quill sync                      # write every card's state from git
python -m tools.quill.quill release plan#N            # free a claim, keeping its work
```

## Architecture

```text
Routes (Blueprints) → Services (no Flask imports) → Models (SQLAlchemy) / Schemas (Marshmallow)
```

**Services are isolated from Flask** -- they take plain data, return plain data, never import
`request`/`session`. Do not violate this boundary.

**PostgreSQL schemas:** ref (lookup tables), auth (users/sessions), budget
(transactions/accounts/templates), salary (pay/tax/deductions), system (audit metadata).

**Key domain concepts:** Anchor Balance (real checking balance, projections flow forward from it).
Balance Calculator (period-by-period from anchor). Recurrence Engine (8 patterns from templates).
Paycheck Calculator (salary + raises - taxes - deductions). Status workflow:
`projected -> done|received|credit|cancelled`, and every one of those back to `projected` (revert).
No status is terminal.

**Established patterns -- use these, do not reinvent:** Authentication is the login gate
(`app/login_gate.py`, ruling `bank_import:R-BI4`): every route is gated by existing, NO view carries
`@login_required` (gate: `tests/test_arch/test_the_login_gate_has_one_home.py`), and a route that
must be public is named in `PUBLIC_ENDPOINTS`. Ownership helpers in `app/utils/auth_helpers.py`.
Security response rule: 404 for both "not found" and "not yours." Structured logging via
`log_event()`. Dependencies pinned in `requirements.txt` -- no new packages without approval.

**Reference tables: IDs for logic, strings for display only.** Enums in `app/enums.py`, cached in
`app/ref_cache/`. NEVER compare against string `name` columns in Python or Jinja (gate:
`shekel-refname-compare` for Python; the template hook for Jinja).

## Definition of Done

A task is NOT complete until ALL of these are true:

1. Code is implemented in full -- no TODOs, no placeholders.
2. Docstrings and comments per coding standards.
3. `pylint app/` is clean: no new messages, and
   `--fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale,shekel-transaction-status-bypass,shekel-ledger-model-bypass,shekel-unclassified-fenced-export,shekel-private-module-import`
   passes (the per-edit and Stop hooks enforce this in-loop).
4. Targeted tests pass for changed files.
5. Full suite passes.
6. Test output (pass/fail counts) shown to developer.
7. Migrations tested in both upgrade and downgrade directions.
8. A neutral adversarial review of the design and diff ran, and its findings are fixed or reported
   (see Design doctrine).
9. Commit message format: `<type>(<scope>): <what changed>`
10. Developer asked if they want to commit and push.

## Transfer Invariants

**CRITICAL INVARIANTS (violating any one is a critical bug):**

1. Every transfer has exactly two linked shadow transactions (one expense, one income).
2. Shadow transactions are never orphaned and never created without their sibling.
3. Shadow amounts, statuses, and periods always equal the parent transfer's.
4. No code path directly mutates a shadow. All mutations go through the transfer service.
5. A transfer's PLAN half is derived from budget.transfers and its RECORD half is read from
   budget.transactions (**R-BAL13**, ruled **R-BAL38** at `balance:X-bi-6a`). Every still-PROJECTED
   leg a balance folds is one side of the parent transfer -- the from-side an expense, the to-side
   an income, each worth `resolve_transfer_amount` on the parent -- and no balance reader reads a
   projected shadow row. A SETTLED leg is its shadow's DATED covering movement since
   `balance:X-bi-4a` (**R-BAL80**: the cash fold reads movements and no row; the posted ledger books
   each side's movement as its own entry against the owner's Transfers-in-transit account since
   `balance:X-bi-6-3`, **R-BAL45** / **R-BAL101**); no amount is read off budget.transfers for a row
   that has settled. Since `recurrence:R16-b-2` (**R-R66**) the forward loan plan also reads
   budget.transfers for OCCURRENCE IDENTITY -- which occurrences a definition's rows already answer,
   in any state. A PROJECTED shadow cannot be counted beside its leg (the plan excludes it by the
   same `transfer_id` test that identifies it); a plan leg is emitted only for a side whose dated
   movement does not exist (**R-BAL79**), so a SETTLED shadow under a still-Projected parent -- a
   status drift, which invariants 3 and 4 forbid and no door writes -- is counted ONCE, by whichever
   half holds it. The reverse drift, a settled parent over a still-Projected shadow that holds no
   movement, is counted by neither half of the cash fold and, since `balance:X-bi-6-4b`, once by the
   loan walk, as a `$0.00` payment (**R-BAL140**); a settled parent over its loan-side (income)
   shadow reverted alone, whose kept movement is un-dated, makes the loan walk refuse
   (`UndatedSettleError`, naming the transfer: its name, its two accounts and due date;
   **R-BAL147**, **R-BAL167**). Pinned in `tests/test_services/test_transfer_legs.py` and
   `test_loan_settled_legs.py`; the structural end is `X-bi-6`, status in ONE row.

**Invariant 3 is rule 14's known instance**: one value kept in two homes by a maintenance contract,
and invariant 5's record half is why the mirror still exists at all. Which clauses are already
structural and which steps delete the rest is the balance arc's plan of record -- the X-bi-6 cards
in the tracker (their argument: `docs/audits/balance_architecture/README.md`). Until that work
ships, the invariants are enforced exactly as written above, and the step that deletes a home owes
this section its rewrite.

## Standards

Full standards: `docs/coding-standards.md` and `docs/testing-standards.md`. They are not
force-loaded; the path-scoped rules in `.claude/rules/` (`coding`, `database`, `testing`, `deploy`,
`recurrence-form`) load the essentials automatically when you touch matching files and point you to
the full doc.

**The tier rule: a fact lives in ONE tier.** This file states a rule in one line and points;
`.claude/rules/*` carry the path-scoped must-knows; `docs/*-standards.md` hold the rationale and
every dated measurement. The `code-reviewer` agent deliberately mirrors the coding, database and
testing rules plus the Transfer Invariants (a subagent loads no path-scoped rules); a change to one
updates the mirror in the same commit -- a discipline no gate enforces yet.

## Tests

Run via `./scripts/test.sh`, never bare `pytest`.
**It gives every run a POSTGRES CLUSTER OF ITS OWN** -- a container started from a baked image on a
rootless docker daemon, reached over a unix socket, removed on exit -- so two runs never meet, in
one worktree or many. The full suite is MINUTES, not seconds; the current count and dated timing
live in `docs/testing-standards.md` (Test Run Guidelines). **The wrapper defaults to
`-m "not docker"`, which DESELECTS the container-spawning `tests/test_deploy` tests** -- they vanish
from the report rather than appearing as skips, so a green local run is not a claim about them; CI
runs the wrapper in six shards with `-m ""` and executes them all. Single test:
`./scripts/test.sh tests/path/test_file.py::test_name -v`.
**A migration needs no manual template rebuild**: the template is baked into the image and the
wrapper re-verifies it on EVERY invocation, rebuilding when the key moved
(`scripts/build_test_db_image.py`).

## Deployment

Docker (Gunicorn + Nginx + Cloudflare Tunnel) on bare-metal Arch Linux: no Ubuntu packages, no
exposed ports, no systemd. `.env`: `DATABASE_URL`, `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`. The compose,
hardening, and prod-override-sync conventions auto-load via `.claude/rules/deploy.md` when you touch
`deploy/` or compose files.

## Development Status

**All live work is cards in the private tracker `saltyreformed-labs/shekel-plan`, and the tracker is
the single source of truth for what to do next** (R-BAL170). Every step, finding, ruling and
question is one card, `plan#N`, and every read and write goes through quill (Common Commands). The
board holds the steps and questions in the order the developer drags them (R-BAL177);
**the next step is `python -m tools.quill.quill next`'s answer.** A card's spec is its body, and a
review reads its edits since the work started (`/charter`, R-BAL174). A step split into leaves holds
them as sub-issues, and a piece of work inside a card's one pull request is a task under it (Git
Workflow). Whether a card SHIPPED is git's answer, never the card's state: its done file
`plan/ships/N` in the merge of `dev` and `main` (R-BAL359), from which `quill sync` writes each
card's state.

Every card but a task carries its arc (a task's is its card's). Each arc's argument, the reasoning
behind its steps, stays in its own document: `docs/audits/balance_architecture/README.md` (balance),
and `implementation_plan_*.md` in `docs/plans/` for recurrence, pay_calendar, credit_card,
bank_import and salary. A ruling made before the tracker is a closed card titled by its old id, and
every step, finding and question open when the tracker took over is a card titled by its id, so
`python -m tools.quill.quill show balance:R-BAL38` finds one. A shipped step's or a closed finding's
id names no card: its record is the commit that shipped or closed it, and the archives. Beside the
arc documents, `docs/plans/verification.md` is what "done" means for a step in any arc, and
`docs/plans/lessons.md` is what this project has already paid to learn.

**Anything under an `archive/` or `historical/` directory is a HISTORICAL RECORD and governs
nothing** -- every such file says so on its first line. Cite one for how a decision came to be,
never for what is true now, and never as a plan of record. The code as committed is the source of
truth for what the app does; planning documents lag it.

`docs/project_roadmap_v5.md` holds the older product direction and lags the code. Planning docs lag
the code generally: treat the codebase and recent git history as the source of truth for what is
actually shipped, not any doc's stated status.

## Style

No Unicode dashes. Use periods, commas, semicolons, or colons for sentence breaks. Use - for ranges.

## Git Workflow

Develop on a short-lived feature branch off `dev`; pushing it is always allowed. `dev` and `main`
each take a change only through a pull request and the merge queue (rulesets `dev-queue` and
`main-queue`, **R-BAL322**). Their bypass lists are empty, so a direct push to either is rejected
for everyone, and so is `gh pr merge --admin`. A merge is `gh pr merge N --merge`, which puts the PR
in the queue; the queue lands it only once `lint-and-test` and `polyglot-lint` pass on the exact
merge commit it lands. CI runs on pull requests, on each queue group and on pushes to `main` -- NOT
on pushes to `dev`, so `dev` work is validated when you open its PR. To ship `dev` to `main`: open a
PR `dev` -> `main`, wait for the green check, then queue it. Do NOT
`git checkout main && git merge dev && git push origin main` -- the `main-queue` ruleset rejects it.
After a PR merges into `main`, resync `dev` so it holds every commit `main` does: a PR `main` ->
`dev`, queued through `dev`'s queue (`git push origin dev` is rejected). When several sessions are
active, PR and merge timing is the coordinator session's call -- see Multi-session operation.

**Work for card N is on its branch `card/N`**, which `quill claim N` makes from `dev` with the
card's done file `plan/ships/N` as its first commit: one branch records one card, merging it ships
the card, and a revert of that merge removes the file and reopens the card.
**A `credit_card` card that moves money owns its pull request**: nothing else rides in it. A
`recurrence` or `pay_calendar` step is a leaf boundary: one card and its one pull request, its own
tests green, independently revertible by reverting its merge; a `recurrence` step states its
behaviour change by measurement, not a hedge.

**A piece of work inside a card's one pull request is a TASK** (R-BAL386), a sub-issue of the card:
filed under it when the piece is planned (`quill file task`), its file `plan/tasks/T` written in the
commit that finishes it (`quill task-file plan#T`). The task closes once the pushed `card/N` holds
the file and reopens if a revert removes it; the card's pull request is refused while a task under
it that was not dropped lacks its file. A piece that lands by a pull request of its own is a leaf
step instead, with its own claim.

**A finding fixed inside a step's pull request ships in it** (plan#1777): the step owns it, its one
blocked-by link (R-BAL395), and writes its done file `plan/ships/F` in the commit that fixes it
(`quill finding-file plan#F`), or hands it to the step that fixes it
(`quill hand plan#F --to plan#S`); the step's pull request is refused while it owns an open finding
it does neither for, and a revert of its merge reopens each finding it marked. A finding's owner is
always a step a branch builds, never one split into smaller steps (plan#1784).

**The resync rule for the files the tracker switch deleted** (the planning registries in
`docs/plans/` and the tools that read or migrated them): `main` keeps them until the first release
after the switch carries their delete, so a resync that meets a hotfix's change to one takes `dev`'s
delete. A hotfix card's record reaches the tracker as its done file, from its own `card/N` (or
through the records-only door while that lives, R-BAL363), never as a registry edit.
