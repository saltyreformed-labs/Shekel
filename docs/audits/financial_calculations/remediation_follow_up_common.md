> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Follow-up Remediation -- Common Rules and Work Summary Format

Shared by every commit prompt in `remediation_follow_up_commit_prompts.md`. Each prompt requires
this file to be read in full before any code is edited. Putting the rules here keeps the per-commit
prompts focused on commit-specific objectives, files, and verification gates.

---

## Apply these rules (every commit)

These are the floor, not the ceiling. The plan's commit specification
(`remediation_follow_up_plan.md` Section 7) may extend them with commit-specific constraints; the
union holds.

1. **The plan's specification for this commit is the floor, not the
   ceiling.** If verification surfaces extra in-scope refinements,
   fold them in and explain in the work summary.
2. **Trust-but-verify.** Re-grep every cited symbol; read every file
   you will change in full; confirm the plan claim still holds
   against current code before editing. If reality has drifted,
   stop and report in the work summary before continuing.
3. **No shortcuts, no band-aid fixes.** Fix root causes. Decimal money
   from strings, IDs and semantic booleans for business logic (never
   name strings), DRY/SOLID, fully normalized schema, pythonic
   type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use `--` or `-`).
4. **Never modify a test to make it pass** except for the documented
   exception in the main remediation plan Section 1 rule 2: tests
   pinning a shipping wrong number this finding proves wrong. Every
   re-pinned assertion gets a comment naming the finding ID and the
   hand-computed arithmetic. List each in the work summary.
5. **Targeted pytest during edits; pylint clean; full pytest green as
   the per-commit final gate.**
   - Run targeted tests via `./scripts/test.sh tests/path/test_file.py -v`
     (the wrapper starts a throwaway postgres cluster for the run and
     removes it afterwards; see `docs/testing-standards.md`).
   - `pylint app/ --fail-on=E,F` clean -- no new warnings vs baseline.
   - Full pytest via `./scripts/test.sh` (`-n 12 default`); ends in
     `N passed`, zero failed/errors/xfailed.
   - Migrations (if any) round-trip
     `flask db upgrade -> flask db downgrade -> flask db upgrade`
     cleanly. Rebuild the test template with
     `python scripts/build_test_template.py` after any schema change.
   - Destructive migrations get a `Review:` docstring line and explicit
     developer approval before authoring (the follow-up plan has
     pre-approval at plan time for any item it includes; do not assume
     approval for new destructive work surfaced during execution).
6. **Stay in scope.** Out-of-scope issues, gold-plating opportunities,
   or refactors you noticed but did not perform MUST be flagged in the
   work summary with `file:line` and a one-sentence reason for not
   acting. Do not silently fold them in. Items that are not directly
   handled by a future commit need to be added to
   `docs/mobile_follow_up.md` as a
   new `F-N` entry. Any trivial in-scope items, offer to address as a
   separate commit.
7. **Do not push.** After the work is green, present the work summary
   and ASK whether to commit and push to `dev` (this triggers CI;
   PR-to-`main` is required for promotion per
   `CLAUDE.md` Git Workflow).

---

## Work summary format (use these labels verbatim)

End every session with a structured work summary using these exact labels. Do not invent new labels
or merge sections.

```text
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic
   comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from
   pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade -> downgrade -> upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which
   new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item +
   F-# entry in remediation_follow_up.md (or note "none").
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the
   Co-Authored-By trailer per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"
```

---

## Test-run conventions

- **Always invoke `./scripts/test.sh`** rather than bare `pytest`.
  The wrapper starts a postgres cluster of the run's own, exports the
  test DSNs at it, and defaults the marker expression.  A bare `pytest`
  has no local database to reach.
- **Single file or test:**
  - `./scripts/test.sh tests/path/test_file.py -v`
  - `./scripts/test.sh tests/path/test_file.py::test_name -v`
- **Full suite (per-commit final gate):** `./scripts/test.sh` (uses
  `-n 12` from `pytest.ini` `addopts`). The dated wall-clock figure
  lives in `docs/testing-standards.md` (Test Run Guidelines); the
  ~65 s once quoted here was measured against a far smaller suite and
  is not a current number.
- **Test template rebuild after migrations or after edits to
  `app/ref_seeds.py` / `app/audit_infrastructure.py`:**
  `python scripts/build_test_template.py`.

## Pylint conventions

- `pylint app/ --fail-on=E,F` must finish clean (no E or F messages)
  and show no new W messages vs baseline.
- A new `# pylint: disable=` is acceptable only for genuine false
  positives. When truly necessary it must be scoped to one line,
  name the specific rule, and carry a comment explaining why.

## Git workflow (per CLAUDE.md)

- All development on the `dev` branch.
- `main` is branch-protected: direct pushes are rejected; merge
  requires a PR with a green `lint-and-test` check.
- To ship: push `dev` (CI runs), open PR `dev` -> `main`, wait for
  green, merge via the PR.
- After PR merge, resync `dev`:
  `git fetch origin && git checkout dev && git merge origin/main && git push origin dev`.

## Manual browser verification (mobile / UI commits)

Pytest verifies that the server emits the right HTML; it does NOT
verify what the browser actually renders, how Bootstrap's JS reacts
to taps, or whether a layout collapses at a small viewport.  For any
commit that touches a Jinja partial, CSS, or JS in the grid /
mobile / form layer, run the Playwright harness in
`tests/manual/` as part of the per-commit final gate.

### Setup (one-time per environment)

```bash
pip install -r requirements-dev.txt          # pulls playwright
.venv/bin/playwright install chromium        # downloads ~150 MiB
.venv/bin/python tests/manual/save_dev_session.py
# Prompts for email + password via getpass.  Password is read from
# the TTY, never echoed, never enters Claude's context, never lands
# in shell history.  Stored cookie goes to
# tests/manual/.dev_session_state.json (gitignored).
```

Re-run `save_dev_session.py` whenever the saved session expires or
after any logout.

### Per-commit invocation

If the commit ships a verification script in `tests/manual/`
(named `verify_*.py`), run it:

```bash
.venv/bin/python tests/manual/verify_<topic>.py
```

Each script prints a per-check pass/fail summary and writes
screenshots to `tests/manual/screenshots/`.  Exit code 0 = all
checks passed.

If the commit touches a UI surface that has no existing verification
script, ASK whether to ship one as part of the commit (it doubles
as a regression lock for later commits).  The harness pattern is
documented in `tests/manual/verify_mobile_grid_commit6.py`:
storage_state for auth, headless Chromium at 375x812, per-check
function with a `CheckResult` dataclass, full-page screenshots at
each interaction step.

### Why this is in the per-commit gate, not "nice to have"

The pytest suite asserts every server-rendered HTML invariant.  But
the JavaScript layer -- Bootstrap tab JS, HTMX swap targets, the
mobile bottom-sheet drag handlers, the hash-routing handler that
keeps the active tab pinned after a full GET -- is not covered.
A Jinja change that leaves the server-rendered HTML byte-equivalent
can still break the browser-visible UX (a renamed selector that
JS still queries by old id, a removed class that CSS still styles
on, a moved element that Bootstrap's tab JS can no longer find).
Running the Playwright harness catches these before the commit
lands.

The dev Flask server must be running (`flask run --host 172.32.0.1`
on the bare host, or the containerised path from F-2 in
`docs/mobile_follow_up.md` once that lands).  The harness targets
`http://172.32.0.1:5000` directly -- bypasses nginx so the
LAN-allowlist on `shekel-dev.saltyreformed.com` does not need to
permit Claude's host-loopback origin.

### Reporting

Treat the verification output the same way as the pytest output:
include the final summary line in section E of the work summary
(or a sibling label, e.g. "E2. Manual browser verification:
22/22 passed, screenshots in tests/manual/screenshots/").  If any
check fails, treat it as a failing test per CLAUDE.md rule 4 --
fix the underlying issue, do not edit the harness to hide the
failure.

---

## Common verification grep commands

- **No Flask in services:**
  `grep -nE '^(from|import)\s+flask\b|\b(request|session|current_app|render_template)\b' app/services/<file>.py`
  -- must return empty (B6-01 import-linter is enforced by Commit 36 of the main remediation;
  per-file greps remain useful during authoring).
- **No `Status.name` comparisons:**
  `grep -n "\.name ==\|Status.name" <file>` -- must return empty in
  any business-logic context (use IDs from `ref_cache` instead).
- **No truthiness on financial values:**
  `grep -nE 'if not [a-z_]+:|if [a-z_]+ and ' <file>` -- in
  money/SQLAlchemy-object contexts must be `is None` / `is not None`.
- **No bare `Decimal("0.01")` quantize:**
  `grep -nF '.quantize(Decimal("0.01"))' <file>` should appear only
  in tests/legacy code; production rounds via
  `app.utils.money.round_money`.
