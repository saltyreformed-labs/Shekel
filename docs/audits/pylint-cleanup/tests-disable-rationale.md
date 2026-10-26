# `shekel-disable-rationale` (W9903) in `tests/`

**Status:** OPEN, unscheduled. Needs a developer ruling before any keystroke.
**Measured:** 2026-08-08, against `dev` at `33103305`.
**Reproduce:** `pylint tests/ tools/ | grep W9903`

---

## The short version

I reported this to you as "`tests/_test_helpers.py` carries 48 pre-existing
`shekel-disable-rationale` findings". That number is right, and the framing was
misleading in two ways this document exists to correct.

1. **It is not one file.** There are **858 findings across 109 test files**.
   `_test_helpers.py` is 48 of them -- **5.6%**, ranking 4th.
2. **They are not oversights.** `tests/` is out of pylint scope by
   **ratified decision #1**, locked with you on 2026-06-04
   (`docs/audits/pylint-cleanup/plan.md:685`). The gate was never pointed at
   this tree, so nothing here regressed; the convention simply does not extend
   to it.

So the real question is not "how do I fix 48 findings" but **"do I want the
rationale convention to apply to `tests/` at all, and if so, how do I get there
without a 858-line commit that reviews as noise?"**

---

## 1. What the rule requires

`shekel-disable-rationale` (W9903, `tools/pylint/shekel_checkers/disable_rationale.py`)
requires every `# pylint: disable=` to carry a `Pylint:` why-comment in a fixed
place, naming every rule it disables. The point is a single grep:

```bash
grep -rn "Pylint:" app/     # every suppression, with its justification
```

Two locations, decided by where the directive sits:

| directive is on | rationale goes |
|---|---|
| a `def` / `class` line (the `too-many-*` smells) | a `Pylint:` note in that symbol's **docstring** |
| any other line | a `# Pylint:` comment **immediately above** |

The marker is capitalised deliberately: pylint matches its own `pylint:` pragma
case-sensitively, so `Pylint:` is invisible to pylint's option parser and cannot
collide with it.

**The `(<count>/<limit>)` shape is convention, not machine-checked.** The
checker enforces marker presence, location, and rule-naming only.

---

## 2. The measurement

### Repo-wide

```text
tree      findings   files    gated?
--------  --------   -----    ------
app/             0       0    YES  (CI + pre-commit + per-edit hook, --fail-on)
scripts/         0       0    YES  (CI step 5a + pre-commit)
tools/pylint/    0       0    YES  (pre-commit, 10.00/10 floor)
tools/plan_gate/ 1       1    no
tests/         858     109    no
```

The three gated trees are at zero. That is the gate working, not luck.

### The ten heaviest files

```text
 94  tests/test_services/test_savings_dashboard_service.py
 60  tests/test_routes/test_loan.py
 55  tests/test_routes/test_grid.py
 48  tests/_test_helpers.py          <-- the file I reported
 43  tests/test_services/test_cash_walk.py
 26  tests/test_schemas/test_validation.py
 24  tests/test_services/test_cash_fold.py
 23  tests/test_routes/test_savings.py
 19  tests/test_services/test_mfa_service.py
 19  tests/test_routes/test_accounts.py
```

Long tail: 99 further files hold the remaining ~447.

### Inside `_test_helpers.py`

71 disable directives; **48 flagged, 23 already compliant**. The file has been
drifting toward the convention rather than away from it.

By rule:

| rule disabled | directives | flagged |
|---|---|---|
| `import-outside-toplevel` | 66 | 44 |
| `too-many-arguments, too-many-positional-arguments` | 3 | 2 |
| `global-statement` | 2 | 2 |

**By how much work each one is** -- this is the number that matters:

| shape | count | what it needs |
|---|---|---|
| already carries an inline `-- explanation` on the same line | **33** | RELOCATE the existing prose into a `# Pylint:` line above |
| has an ordinary explanatory comment above, missing the marker | **2** | prepend `Pylint: ``rule`` --` to a comment that already exists |
| carries no prose at all | **13** | AUTHOR a rationale (judgement) |

**Roughly 73% of the work is mechanical relocation, not writing.** The file uses
an older in-file convention -- `# pylint: disable=X  -- explanation` -- that
predates the `Pylint:` marker. `.pre-commit-config.yaml:87-91` even names that
older shape when it explains why the `tests/` decimals scan disables
`unknown-option-value`.

### The 13 that need actual thought

```text
line   398  global-statement
line   675  global-statement
line  2225  import-outside-toplevel
line  2260  import-outside-toplevel
line  2261  import-outside-toplevel
line  2262  import-outside-toplevel
line  2719  too-many-arguments, too-many-positional-arguments
line  2904  too-many-arguments, too-many-positional-arguments
line  3850  import-outside-toplevel
line  3905  import-outside-toplevel
line  4037  import-outside-toplevel
line  4038  import-outside-toplevel
line  4041  import-outside-toplevel
```

All 48 flagged lines:

```text
 398  675  714  922  974 1076 1148 1184 1294 1552 1588 1695 1729 1778 1822 1866
1937 2025 2055 2152 2186 2225 2260 2261 2262 2366 2451 2537 2598 2631 2671 2719
2765 2837 2897 2904 2972 3079 3109 3274 3337 3849 3850 3904 3905 4037 4038 4041
```

---

## 3. Worked examples

### The target, already in the file (line 434)

```python
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
```

### Shape A -- relocate (33 of them). Line 714:

```python
# before
    # pylint: disable=import-outside-toplevel  -- avoid module-load

# after
    # Pylint: ``import-outside-toplevel`` -- avoid module-load ordering
    # problems; this module must import no app symbols at collection time.
    # pylint: disable=import-outside-toplevel
```

### Shape B -- prepend the marker (2 of them). Lines 3849, 3904:

```python
# before
    # by conftest-adjacent code before the app's models are configured in some
    # collection orders, and the ledger models are needed only by this function.
    from app.extensions import db  # pylint: disable=import-outside-toplevel

# after -- same prose, one added marker line
    # Pylint: ``import-outside-toplevel`` -- deferred: the ORM is imported by
    # conftest-adjacent code before the app's models are configured in some
    # collection orders, and the ledger models are needed only by this function.
    from app.extensions import db  # pylint: disable=import-outside-toplevel
```

### Shape C -- author one (13 of them). Line 398:

```python
# before
    def stamp(self):
        """Return the next instant: the frozen one, plus one microsecond."""
        global _DB_CLOCK_ISSUED  # pylint: disable=global-statement

# after
    def stamp(self):
        """Return the next instant: the frozen one, plus one microsecond."""
        # Pylint: ``global-statement`` -- the issued-instant counter is
        # process-wide by construction: it exists so two calls in one test
        # cannot return the same microsecond, which a per-instance counter
        # could not guarantee across the fixtures that share the clock.
        global _DB_CLOCK_ISSUED  # pylint: disable=global-statement
```

### Definition-scoped -- the rationale goes in the DOCSTRING (lines 2719, 2904)

```python
def add_entry(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ...
):
    """<existing summary>

    Pylint: ``too-many-arguments`` / ``too-many-positional-arguments`` (N/5) --
    <why these are irreducible: they are the columns of one row this helper
    builds, and a param object here would make every call site construct a
    value it immediately destructures>.
    """
```

Note ratified decision #3: **never raise a `.pylintrc` threshold to win a
smell.** For a helper whose arguments genuinely are one row's columns, the
documented disable is the right answer; for one that is doing two jobs,
decompose instead.

---

## 4. The decision you actually need to make

This is a ruling, not a task, and it is the reason nothing here should start
with a keystroke.

### Option A -- leave `tests/` out of scope (the status quo)

Ratified decision #1 stands. Cost: zero. What you give up: the `grep "Pylint:"`
guarantee stops at the `app/` boundary, so a suppression in a test file is
unauditable by that grep. Given that `tests/` contains no production logic, that
may be exactly the right trade.

### Option B -- extend the convention to `tests/`, ungated

Fix all 858 over time, no gate. **This is the weakest option** and worth naming
so it is rejected deliberately: without a gate the count regrows, and you would
have spent the effort to arrive back here. The `app/` cleanup's own history is
the evidence -- it needed the `ENFORCE_PYLINT_FLOOR` lock-in to hold.

### Option C -- gate `tests/` for W9903 only, then clean to it

Add a pre-commit hook mirroring the existing `pylint-tests-decimals` one, which
already proves the pattern: `tests/` is out of general scope, but ONE rule is
enforced there because it catches a real defect class.

```yaml
      - id: pylint-tests-disable-rationale
        name: shekel-disable-rationale on tests/
        entry: pylint
        language: system
        types: [python]
        files: ^tests/.*\.py$
        pass_filenames: false
        args:
          - tests/
          - --disable=all
          - --enable=shekel-disable-rationale
          - --disable=unknown-option-value,bad-option-value
```

The `--disable=unknown-option-value,bad-option-value` pair is **not optional**,
and the cost of omitting it is measured: on `_test_helpers.py` alone the scan
emits **99** extra `unknown-option-value` / `bad-option-value` messages, because
pylint reads the older `disable=X -- explanation` comments' trailing prose as
further rule names. `pylint-tests-decimals` hit the same thing and documents it.

Verified working as written:

```console
$ pylint tests/_test_helpers.py --disable=all \
      --enable=shekel-disable-rationale \
      --disable=unknown-option-value,bad-option-value
... 48 W9903 findings, no noise
```

The gate cannot go in until the tree is clean, so this is: clean first, gate
last, in that order.

### Option D -- gate only what new code adds

Same hook, but scoped to changed files (drop `pass_filenames: false`). New and
touched test files must comply; the 858 stay until their file is next edited.
**Cheapest path to "it stops getting worse"**, and it composes with C: adopt D
now, finish the backlog, promote to C.

**My recommendation: D now, C when the count reaches zero.** D costs one hook
and buys the ratchet immediately; C is the lock-in and should follow the
cleanup, not precede it.

---

## 5. If you do clean it, the order that keeps it reviewable

A single 858-finding commit is unreviewable and would bury any judgement call in
it. Suggested sequencing:

1. **`_test_helpers.py` first (48).** It is shared by the whole suite, so its
   conventions propagate; it is already 32% compliant; and 35 of its 48 are
   mechanical. Good calibration for what the rest costs.
2. **The plan gate's one finding (1): nothing to do.** The tracker switch
   (`balance:X-cx`, L8) deleted `tools/plan_gate/` with its tests.
3. **The four heavy files (252).** `test_savings_dashboard_service`, `test_loan`,
   `test_grid`, `test_cash_walk`. One commit each.
4. **The long tail (557 across 104 files).** Batch by directory.

Per commit, the check is:

```bash
pylint tests/<path> --disable=all --enable=shekel-disable-rationale \
       --disable=unknown-option-value,bad-option-value
./scripts/test.sh tests/<path> -q      # comments only; must not move
```

**Every one of these commits changes comments only.** If a test's pass/fail
status moves, something other than a comment was edited -- stop and look.

### Two traps

- **Do not batch-rewrite with a regex.** 13 of the 48 in `_test_helpers.py`
  alone need an authored rationale, and a script cannot tell them from the 33
  that only need relocating. A wrong rationale is worse than none: it tells the
  next reader the suppression was considered when it was not.
- **Some disables should be DELETED, not documented.** Check `useless-suppression`
  (enabled in `.pylintrc`) per file first -- a directive that suppresses nothing
  is itself a finding, and writing a rationale for it entrenches dead code. I
  hit exactly this in `app/` on 2026-08-08: two `too-many-arguments` disables on
  5-argument functions, suppressing nothing.

---

## 6. Provenance

Surfaced 2026-08-08 while hoisting three tax-config seeders out of
`tests/test_routes/test_salary.py` into `tests/_test_helpers.py` for plan step
R4b-1 (`b4538d25`). My edit introduced 3 new W9903 findings, which I fixed in
that commit; the 48 pre-existing ones I left, and reported, under CLAUDE.md rule
6 (report out of scope, do not fix without approval).

This document exists because my report understated the scope by a factor of ~18
and did not name the ratified decision that put `tests/` outside the gate in the
first place.

**Related:** `docs/audits/pylint-cleanup/plan.md` (the `app/` + `scripts/`
cleanup and its ratified decisions), `docs/coding-standards.md` (the rationale
format), `.pre-commit-config.yaml:80-96` (the `pylint-tests-decimals` precedent
for a single-rule `tests/` gate).
