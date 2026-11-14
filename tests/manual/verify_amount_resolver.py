"""Grade the amount resolver against every row, and prove the grading can FAIL.

Plan step **X-au-b**'s oracle, rebuilt at **X-bl-1** under ruling **R-JF**: for
EVERY row in a database -- not a sample, not the contributing ones, not the
projected ones -- :func:`app.services.cash_ledger.resolve_transaction_amount`
answers from exactly the SOURCE the row's declaration names, or the run fails
naming the rows where it does not.

**AN AGREEMENT ORACLE CANNOT SEE THIS RESOLVER, and an adversarial review proved
it by deleting the resolver.**  With the whole body replaced by
``return txn.estimated_amount`` the agreement pass reported *997 rows, 0
mismatches, OK* (2026-08-12, pre-cutover).  It has to: the app's published
answer IS this resolver (``cash_ledger.amounts_by_key`` is
``{row.id: resolve_transaction_amount(row, basis)}``), so comparing the two
compares a value with itself.  That is the harness question
``docs/plans/verification.md`` standard 3 asks -- *can it SEE the code under
test?* -- answered no, and a free pass that reads as proof is what standard 4's
firing control exists to stop.

**SO THE AGREEMENT PASS IS DELETED RATHER THAN LABELLED** (plan step X-bl-1).
It had two arms and both were identities.  The mismatch arm compared
``resolved`` against ``today``, two keys assigned the SAME expression one line
apart.  The drift arm compared ``resolved`` against the row's STORED column --
and that one is not a slip and not a consequence of the cutovers:
``ck_transactions_amount_ownership`` is the biconditional
``(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)``, so a row
carrying a figure IS an OWN row, and rule 1 answers by RETURNING that figure.
**The only state that comparison could measure -- a row storing a figure while
something else prices it -- is exactly the state the CHECK forbids.**  It was
representable before plan step X-au-c1 added the pair; it has not been since.

**A REPLACEMENT WAS BUILT, AND ITS OWN NEGATIVE CONTROL REFUTED IT.**  The
obvious fix is to assert the CHECK's claim over the data instead of comparing
two producers -- a predicate that can fail where the comparison cannot.  It was
written, and then fired at it: with the constraint DROPPED on a throwaway clone
and one derived row given a figure, the run does not report a violation.  It
DIES, in :func:`~app.models.amount_ownership.AmountOwnership.from_columns`,
before a single record is built::

    ValueError: a row states its OWN figure or the relation that prices it,
    never both and never neither: got figure Decimal('42.00') beside source 1

The pair is guarded at THREE tiers -- the schema CHECK, the write seam, and the
composite type's HYDRATION -- and the third means a reader cannot observe the
violation to report it.  So the replacement predicate is unreachable and was
deleted with the comparison it was meant to replace.  What a broken database
gets is that ValueError, naming the row and both halves: louder than a count,
and one this file cannot improve on.  *Two shipped as-built records and an
earlier draft of this docstring quoted "1,028 rows, 1,028 agreeing, 0 drift" as
a measurement.  The numbers are struck, not tensed.*

**PASS 2 WAS REBUILT BECAUSE IT COULD NOT FAIL FOR THE ROWS IT EXISTED FOR**
(finding **N-445**).  It perturbed each row's OWN stored column and asserted a
DERIVED row did not move -- but its loop opened
``if txn.estimated_amount is None: continue``, and the ownership CHECK is the
biconditional ``(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)``,
which makes that column NULL on every derived row.  So the nudge skipped 100% of
them BY CONSTRUCTION and the second resolve re-priced an unperturbed row: an
unchanged answer from an unchanged input, counted as a pass.  The giveaway is
that the two counts were the SAME NUMBER -- *934 derived rows held* against *934
rows storing no figure at all*.  Two shipped as-built records quote that count;
both are structurally true and neither is evidence.

**The seven perturbations.**  A derived row has no figure of its own to nudge, so
the control moves the SOURCE the row is declared to and asserts the row follows
it -- and asserts, in the same breath, that every row declared to a DIFFERENT
source does not move.

=========================== ============================================ ======================
perturbation                what moves                                   which rows follow
=========================== ============================================ ======================
``own_column``              every row's own column, by +/- a nudge       OWN, to the figure
``transaction_series``      every version a TRANSACTION template states  TEMPLATE
``newest_transaction_version`` each transaction template's LATEST version   TEMPLATE not superseded
``transfer_series``         every version a TRANSFER template states     shadows of a DERIVED
                                                                         parent
``parent_figure``           every transfer that owns its amount          shadows of an OWN parent
``salary_deduction``        a POST-TAX line on every salary profile      SALARY, by exactly -it
``salary_raise``            a FLAT raise partway through the calendar    SALARY funded at or
                                                                         after it, upward
=========================== ============================================ ======================

**EVERY PERTURBATION IS INSTANCE-DISTINCT, and an adversarial review is why.**
A first draft moved every instance of a source KIND by the SAME amount, which
grades WHICH KIND of source a row reads and never WHICH INSTANCE.  Three
mutations passed that draft and exited 0: rule 3 pricing every row from ONE
fixed template's series, rule 5 answering ONE fixed transfer, and rule 2
resolving ONE fixed pay period -- because that instance was nudged too, so each
row moved by exactly the expected amount.  Every one is *priced from the wrong
definition*, the money-moving class this arc exists to prevent.  For five arms
the fix is the SCALE: it is the SOURCE ROW's own id, and the expected magnitude
is read off a stored column already in the pass-1 record.

**Rule 2 has no such scale, and ``salary_raise`` is what gives it one.**  A
salary profile prices EVERY paycheck, so a per-instance magnitude has nothing to
key on and ``salary_deduction`` moves all of them by the same figure -- under
which a rule 2 resolving one FIXED pay period still passed, and a SECOND
adversarial review caught exactly that after the first five arms were built.  A
RAISE is instance-distinct in the only dimension rule 2 has: it lands at a
``(year, month)`` and applies from it, so it splits the rows on their own pay
period and no single fixed period can answer both sides.  The landing month is
the MEDIAN salary row's, computed from the population rather than hardcoded, so
both halves are non-empty on any database with more than one paycheck.

**The own-column perturbation is the one that used to skip, and it no longer
can.**  An OWN row is re-priced through the sanctioned act
(``amount_ownership.state_own_amount``); a DERIVED row is given a rival figure
through ``tests._test_helpers.write_past_the_amount_seam``, the only supported
way to construct the state the mapping refuses -- ``amount_ownership`` is ONE
attribute over a type with no member for *a figure beside a declaration*, so
``state_own_amount`` on a derived row would RELEASE its declaration and the
control would grade an OWN row while claiming to grade a derived one (plan step
X-au-k).  **The write is verified to have landed on every row**, because that
half is graded only NEGATIVELY: a helper that stopped reaching the column would
report all 934 as held and exit 0, which is N-445's outcome through a new door.

**The own-column arm's weight is in its HOLDING half, and that is worth
stating.**  Rule 1's answer IS the column, so asserting that 94 OWN rows follow
a figure just written into it verifies little beyond the read; what the arm is
FOR is the 934 derived rows that must not move, which is the population N-445
skipped entirely.  The rival is scaled and signed per row so the moving half
still catches a rule 1 reading some OTHER row's column, but the number to weigh
is the 934.

**Two properties the run ASSERTS rather than argues.**  After each rollback
every row is re-resolved and must equal its pass-1 answer
(:func:`_assert_restored`) -- because a leak in the derived direction would hand
the ``m7`` mutation below a free pass, ``own_column`` running first and writing
exactly the figure a column-reading rule 3 would then answer.  And every scored
row must be DECLARED a mover by some perturbation except a DERIVE-mode loan
payment (:func:`_assert_every_row_is_reachable`), so a row graded in the holding
direction alone -- N-445's shape -- cannot hide.  ``db.session.dirty`` is
deliberately not used: this project has measured that it cannot tell a rollback
from a flush.

**A perturbation whose DECLARED-mover set is EMPTY grades nothing in the moving
direction, and the report says so in those words** -- read off the expectation,
never off what the resolver did, since inferring it from ``moved == 0`` would
confuse *no row is priced by this source* with *the resolver moved nothing*,
which is N-445 one layer up.  On the 2026-09-09 clone ``transfer_series`` is
such an arm (every one of the 175 transfers still owns its amount; plan step
**X-au-f** is what changes that) and so is the LOAN_PAYMENT rule
(``budget.loan_payment_settings`` is empty).  They are graded by the seeded
fixtures in the ``tests/test_services/test_amount_source/`` package and by
nothing here.

**WHICH ROWS MUST MOVE IS READ OFF THE DECLARATION AND STORED COLUMNS.**  The
expectation is built in pass 1 from ``amount_source_id``, ``template_id`` and
the version dates -- no amount, and no call into the resolver.  It read the
parent transfer's own ``amount_source_id``, the loan payment MODE and
``transfer_id`` too, for the shadows, until plan step balance:X-bi-6-5.

**NO TRANSACTION ROW READS A TRANSFER SINCE PLAN STEP balance:X-bi-6-5**
(2026-10-27).  Plan step X-bi-6-4d-2 deleted the shadows and that step amount
rule 5 and ``transactions.transfer_id``, so ``transfer_series`` and
``parent_figure`` declare no movers and grade every row in the holding
direction, and the record lost its parent fields.  The measurement and the
mutation table below are of the tree before; their rule-5 rows describe a rule
that no longer exists.

**IT DOES SHARE THE CLASSIFIER WITH THE RESOLVER, AND THAT IS A BLIND SPOT
rather than a clean separation.**  A first draft claimed the two sides "do not
share a producer" and that is FALSE:
:func:`~app.services.cash_ledger.amount_rule` is called by the expectation here
AND by the resolver to dispatch, so a broken CLASSIFIER moves both sides
together.  Traced, the four misclassifications split three to one:

* SALARY read as TEMPLATE refuses (``owns_its_amount`` is False on a salary
  template), TEMPLATE read as SALARY refuses (``salary_net_for`` answers
  nothing), and a DERIVE-mode loan payment read as a plain shadow refuses
  (``resolve_transfer_amount`` will not price a derive-mode template) -- all
  three surface in pass 1's refusal arm, which is a different control than this
  one;
* **a MANUAL loan payment read as a plain shadow is SILENT**: the resolver
  answers the parent's figure where the truth is the parent plus the standing
  extra, and the expectation adopts the same wrong classification, so both agree
  and the row scores as having followed ``parent_figure`` exactly.  That is a
  real money shape -- an adversarial review has already caught this resolver
  dropping ``$150.00`` of standing extra -- and it is DORMANT here only because
  ``budget.loan_payment_settings`` is empty.  ``test_amount_source`` grades it.

**What pass 2 still cannot see, stated rather than left to be rediscovered.**

* the price series was MINED OUT of the column it is graded against, so a
  TEMPLATE agreement re-attests X-au-a's backfill.  Migration ``a9d3c15e7f42``
  run-length-encodes ``estimated_amount`` over ``(template, amount)`` and stamps
  each run at its first ``due_date``; the perturbations are what make the
  comparison say something the backfill did not already guarantee;
* the series' TIME dimension is observable on the few rows pricing from a
  SUPERSEDED version -- **7 of the 525 TEMPLATE rows** on this clone -- and
  ``newest_transaction_version`` is the arm that grades them.  Before that arm
  existed, *"answer the newest version, ignore supersession"* passed every
  perturbation;
* the LOAN_PAYMENT rule prices **0 rows** here, so it is graded by
  ``test_amount_source`` and by nothing in this harness;
* ``budget.transfers.amount`` -- the second column ruling R-FI's CHECK covers --
  is never priced or graded here at all: this harness walks transaction rows,
  and it reached a transfer only THROUGH its shadows, which plan step
  balance:X-bi-6-4d-2 deleted.

**Measured 2026-09-09** on a clone of production at ``a1c7e5d20f43``: 1,028
rows and 0 refusals.  The rule census was 525
TEMPLATE, 350 TRANSFER, 94 OWN, 59 SALARY, 0 LOAN_PAYMENT, and 934 of those
1,028 store no figure at all; 7 of the 525 TEMPLATE rows price from a version a
later one supersedes.  Pass 2:
``own_column`` moved 94 and held 934 against a rival VERIFIED to have landed;
``transaction_series`` moved 525 and held 503; ``newest_transaction_version``
moved 518 and held 510, the 7 superseded rows among them; ``transfer_series``
declared 0 and said so; ``parent_figure`` moved 350 and held 678;
``salary_deduction`` moved all 59 by exactly ``-$1,000.00`` and held 969;
``salary_raise`` raised the 30 paychecks funded at or after its landing month
and held the other 998.

**THE NEGATIVE CONTROL, SHOWN TO FIRE** (``docs/plans/verification.md``
standard 4; measured 2026-09-09 on the same clone, on the tree as committed).
Eight mutations of ``cash_ledger._amount_source``, each applied to disk and
printed before the run so a mutation that never landed cannot read as a green
control:

========================== ============================================== =========================
mutation                   what it does                                   what fires
========================== ============================================== =========================
``m7_column_fallback``     rule 3 prefers the row's stored column          ``own_column``, 525
``m2_scalar_not_series``   rule 3 reads ``template.default_amount``        both template arms
``m1_fixed_template``      rule 3 prices EVERY row from ONE template       both template arms, 518
``m3_newest_version``      rule 3 ignores supersession                     ``newest_...``, 7
``m4_constant_shadow``     rule 5 returns a constant                       ``parent_figure``, 350
``m5_fixed_transfer``      rule 5 prices EVERY shadow from ONE parent      ``parent_figure``, 348
``m9_constant_salary``     rule 2 returns a constant paycheck              both salary arms
``m6_fixed_period``        rule 2 prices EVERY paycheck from ONE period    ``salary_raise``, 30
========================== ============================================== =========================

``m2`` cannot be applied again: plan step balance:X-bp dropped the column it
reads, so its row is the 2026-09-09 measurement and nothing more.

All eight exit **1** and all eight report *refusals: 0*, so pass 1 sees none of
them.  Three of them -- ``m1``, ``m5`` and ``m6`` -- are the wrong-INSTANCE
class, and **each passed an earlier draft of this very file**; ``m6`` fires on
``salary_raise`` and on NOTHING else, which is that arm's whole reason for
existing.  ``m7`` is the defect N-445 named, which the pass this step replaced
scored *934 rows held, OK*.

**No arm is unfalsifiable, and a first draft of this paragraph said one was.**
It claimed "no mutation can make ``transfer_series`` fail here", conflating *it
declares no MOVERS* with *it cannot fail*.  That arm grades all 1,028 rows in
the HOLDING direction: a rule 5 reading its parent's definition series instead
of the parent's own figure moves 350 shadows where the expectation says hold,
and fails there.  What is true is narrower -- ``transfer_series`` has no MOVING
population until plan step **X-au-f** gives it one, so the arm this control
cannot yet exercise in that direction is precisely the arm that will grade the
cutover it unblocks.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel_xaub \\
        .venv/bin/python tests/manual/verify_amount_resolver.py out.json

Exit status is 1 when any row refuses or fails a perturbation, so it is usable
as a gate.  It opens a transaction and ROLLS IT BACK after pass 1 and after each
of the seven perturbations; it writes nothing, and it never flushes a
perturbation.  The JSON blob holds one record per row for a before/after
``diff``; use ``git worktree`` for the HEAD side, never ``git checkout``.  *The
record gained a ``perturbed`` map at X-bl-1 and kept ``nudged`` as the
own-column answer under its old name, so a diff against a pre-X-bl-1 run still
lines that column up.*

Like its ``verify_*`` siblings it is deliberately outside pytest's collection
(``pytest.ini`` sets ``python_files = test_*.py``): it needs a populated
database chosen by the operator, not the seeded test template.

**Its perturbations live in a sibling module** since plan#3835 split this
script at 1,000 lines: ``_amount_resolver_perturbations.py`` holds the seven,
what each applies and what each expects of a row, with the constants they are
built from.  This file builds the pass-1 records, resolves every row, scores
and reports each perturbation, asserts that each rollback restored the tree
and that every row is reachable, and runs ``main``.
"""

import json
import pathlib
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` is not importable when this is run as
# ``.venv/bin/python tests/manual/verify_amount_resolver.py`` -- the same
# bootstrap every sibling here carries.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.services import template_amount_service
from app.services.cash_ledger import (
    AmountRule,
    derived_amount_basis,
    amount_rule,
    resolve_transaction_amount,
)
from tests.manual._amount_resolver_perturbations import (
    _OWN_COLUMN,
    _PERTURBATIONS,
    _RISES,
    _control_raise_month,
)


def _money(value):
    """JSON-stable string for a Decimal (or None)."""
    return None if value is None else f"{Decimal(value):.2f}"


def _resolved_or_refusal(txn, basis):
    """Return ``(amount, refusal)`` for one row -- exactly one of them is None.

    Args:
        txn: The Transaction to price.
        basis: Its account's AmountBasis.

    Returns:
        The resolved Decimal and ``None``, or ``None`` and the refusal message.
    """
    try:
        return resolve_transaction_amount(txn, basis), None
    except AmountUnresolvable as exc:
        return None, str(exc)


def _superseded(txn):
    """Whether *txn*'s template prices it from a version a later one supersedes.

    **A property of the TEMPLATE, not of the row's rule**, and the census no
    longer prints it unguarded: 25 of the 32 rows it answers ``True`` for on the
    2026-09-09 clone are OWN rows, priced by rule 1 from their own column and
    reading no version at all.  What GRADES the time dimension is
    :func:`_resolves_to_newest_version`, which is not this predicate's negation;
    this one survives as the record field a pre-X-bl-1 ``diff`` aligns on.

    Args:
        txn: The Transaction to test.

    Returns:
        ``True`` when the template states a later price than the one this row
        resolves to.
    """
    if txn.template_id is None or txn.due_date is None:
        return False
    versions = template_amount_service.amount_versions(txn.template)
    if not versions:
        return False
    return any(version.effective_date > txn.due_date for version in versions)


def _resolves_to_newest_version(txn):
    """Whether *txn*'s answer comes from the LATEST version its template states.

    The partition ``_amount_resolver_perturbations.py``'s
    :func:`_expect_newest_transaction_version` grades, and it is
    **NOT** ``not _superseded(txn)``.  A first draft used that and it is wrong
    for a row whose due date PRECEDES every version its template holds:
    ``template_amount_service.amount_as_of`` back-projects and holds FLAT at the
    earliest price (ruling **R-I**), so on a template stating a SINGLE version
    such a row resolves to the newest one while :func:`_superseded` reports
    ``True``.  **22 rows on the 2026-09-09 clone are in exactly that shape**, and
    the arm passed only because every one of them is an OWN row -- a
    TEMPLATE-rule row there would have been reported as a failure of a CORRECT
    resolver, which is a control crying wolf rather than one that cannot fail,
    but is still a control that is wrong.

    Two date comparisons over stored columns rather than a second
    implementation of that resolver's supersession walk, which is the
    hand-rolled-replay shape this project refuses.

    Args:
        txn: The Transaction to test.

    Returns:
        ``True`` when the template's latest version is the one pricing this row.
    """
    if txn.template_id is None or txn.due_date is None:
        return False
    versions = template_amount_service.amount_versions(txn.template)
    if not versions:
        return False
    if versions[-1].effective_date <= txn.due_date:
        return True
    # No version is in effect on this row's due date, so the series back-
    # projects to its EARLIEST -- which IS the latest only when there is one.
    return len(versions) == 1


@dataclass(frozen=True)
class _Score:
    """One perturbation's result over every scored row.

    A value object rather than four returned values, because the four ARE one
    thing -- how one perturbation graded -- and passing them separately put
    :func:`_report_perturbation` over the argument ceiling.

    Attributes:
        failures: ``(record, why)`` for every row that contradicted its
            expectation.
        moved: Rows the expectation declared movers, which moved correctly.
        held: Rows the expectation could not reach, which did not move.
        declared: The ids the EXPECTATION says this perturbation must move --
            read off the declaration alone, so an empty set is a statement
            about the DATA rather than about what the resolver did.
    """

    failures: list
    moved: int
    held: int
    declared: set


def _load_groups():
    """Return ``{(account_id, scenario_id): [Transaction, ...]}`` for the whole DB.

    Every row, including soft-deleted, Cancelled and Credit ones: the resolver
    is TOTAL over rows, so an oracle that pre-filtered would grade only the
    shapes it already believed in.

    Returns:
        The grouped rows.
    """
    groups = defaultdict(list)
    for txn in db.session.query(Transaction).order_by(Transaction.id).all():
        groups[(txn.account_id, txn.scenario_id)].append(txn)
    return groups


def _resolve_everything():
    """Return ``{transaction id: (amount, refusal)}`` over the whole database.

    Builds a FRESH :class:`~app.services.cash_ledger.AmountBasis` per group on
    every call, which is load-bearing rather than wasteful: the salary and loan
    derivations behind a basis are memoized on it, so re-using one across a
    perturbation would answer from the derivation computed BEFORE it and the
    salary control would grade nothing.

    Returns:
        One entry per row; exactly one of the pair is ``None``.
    """
    answers = {}
    for (account_id, scenario_id), rows in _load_groups().items():
        account = db.session.get(Account, account_id)
        basis = derived_amount_basis(account.user_id, scenario_id)
        for txn in rows:
            answers[txn.id] = _resolved_or_refusal(txn, basis)
    return answers


def _baseline_records():
    """Grade every row's pass-1 answer and record what pass 2 will expect of it.

    The expectation fields -- ``rule``, ``template_id`` and
    ``prices_from_a_superseded_version`` -- are STORED facts read here while the
    ORM rows are live, so the scorer needs no session and pass 2's expected
    MAGNITUDES come from columns rather than from the producer under test.
    The record keeps a ``today`` field so a before/after ``diff`` against a
    pre-cutover run still lines up column for column; it is a COPY of
    ``resolved``, which is what makes pass 1's agreement arm structurally empty
    -- see the module docstring.

    Returns:
        A list of per-row record dicts.
    """
    records = []
    for (account_id, scenario_id), rows in _load_groups().items():
        account = db.session.get(Account, account_id)
        basis = derived_amount_basis(account.user_id, scenario_id)
        for txn in rows:
            rule = amount_rule(txn)
            resolved, refusal = _resolved_or_refusal(txn, basis)
            records.append({
                "id": txn.id,
                "name": txn.name,
                "account_id": txn.account_id,
                "scenario_id": txn.scenario_id,
                "status_id": txn.status_id,
                "is_override": txn.is_override,
                "is_deleted": txn.is_deleted,
                "template_id": txn.template_id,
                "credit_payback_for_id": txn.credit_payback_for_id,
                "due_date": (
                    None if txn.due_date is None else txn.due_date.isoformat()
                ),
                "period_start": (
                    None if txn.pay_period is None
                    else txn.pay_period.start_date.isoformat()
                ),
                "rule": rule.value,
                "prices_from_a_superseded_version": _superseded(txn),
                "resolves_to_newest_version": _resolves_to_newest_version(txn),
                "resolved": _money(resolved),
                "stored": _money(txn.estimated_amount),
                "refusal": refusal,
                "takes_the_control_raise": False,
                "nudged": None,
                "perturbed": {},
            })
    return records


def _score(perturbation, records, after):
    """Grade one perturbation's re-resolution against every row's expectation.

    Args:
        perturbation: The ``_amount_resolver_perturbations.py``'s :class:`_Perturbation`
            that was applied.
        records: Every pass-1 record; each gains this perturbation's answer.
        after: ``{transaction id: (amount, refusal)}`` from the re-resolution.

    Returns:
        The :class:`_Score` for this perturbation.
    """
    failures, moved, held, declared = [], 0, 0, set()
    for rec in records:
        if rec["refusal"] is not None:
            continue
        before = Decimal(rec["resolved"])
        amount, refusal = after[rec["id"]]
        rec["perturbed"][perturbation.key] = _money(amount)
        if perturbation.key == _OWN_COLUMN:
            rec["nudged"] = _money(amount)
        expected = perturbation.expect(rec, before)
        if expected != before:
            declared.add(rec["id"])
        verdict, why = _verdict(perturbation.key, before, expected, amount, refusal)
        if verdict is None:
            failures.append((rec, why))
        elif verdict:
            moved += 1
        else:
            held += 1
    return _Score(failures, moved, held, declared)


def _verdict(key, before, expected, amount, refusal):
    """Return how one row answered a perturbation: moved, held, or failed.

    Split out of :func:`_score` so the per-row decision reads as the four cases
    it is -- a refusal, a required RISE, a required figure, a required hold --
    rather than as one branch chain.

    Args:
        key: The perturbation's key, for the failure message.
        before: The row's pass-1 answer.
        expected: What the expectation says it must now answer, or
            :data:`_RISES` when only the direction is stated.
        amount: What it actually answered.
        refusal: The refusal message, or ``None``.

    Returns:
        ``(True, None)`` when it correctly moved, ``(False, None)`` when it
        correctly held, and ``(None, why)`` when it contradicted its
        expectation.
    """
    if refusal is not None:
        return None, f"a resolvable row REFUSED under {key}"
    if expected is _RISES:
        if amount > before:
            return True, None
        return None, f"a row did not RISE under {key}"
    if amount == expected:
        return expected != before, None
    if expected == before:
        return None, f"a row moved that {key} cannot reach"
    return None, f"a row did not follow {key} exactly"


def _report_pass_one(records):
    """Print the census and the refusals, which is all pass 1 can measure.

    **This pass no longer compares the resolver against anything, and plan step
    X-bl-1 is where that stopped being pretended.**  It had two comparison arms
    and both were identities; the module docstring carries the argument and the
    measurement that settled it.  What is left is the one arm that can be
    non-zero: a REFUSAL.  ``rows CUT OVER`` beside it is a CENSUS, not a
    control -- it counts empty columns and compares nothing.

    Args:
        records: Every per-row record from :func:`_baseline_records`.

    Returns:
        ``True`` when no row refused.
    """
    refused = [rec for rec in records if rec["refusal"] is not None]
    emptied = [
        rec for rec in records
        if rec["refusal"] is None and rec["stored"] is None
    ]

    print(f"rows graded: {len(records)}")
    print("rule census:")
    for rule, count in sorted(Counter(rec["rule"] for rec in records).items()):
        # How many rows of this rule store no figure at all: which buckets have
        # been CUT OVER, since an empty column is what a cutover leaves behind.
        derived = sum(
            1 for rec in records
            if rec["rule"] == rule and rec["stored"] is None
        )
        print(f"  {rule:<13} {count:>5}   (storing no figure: {derived})")
    # RULE-GUARDED: the bare superseded count is 32 on this clone and 25 of
    # those are OWN rows, which rule 1 prices from their own column and which
    # touch no version at all.
    observable = sum(
        1 for rec in records
        if rec["rule"] == AmountRule.TEMPLATE.value
        and not rec["resolves_to_newest_version"]
    )
    print(
        "TEMPLATE rows priced from a version a later one supersedes (the only "
        f"rows on which the series' time dimension is observable): {observable}"
    )

    print("\npass 1 -- what the resolver REFUSES")
    print(f"  refusals: {len(refused)}")
    for rec in refused[:20]:
        print(f"    REFUSED id={rec['id']} rule={rec['rule']}: {rec['refusal']}")
    print(f"  rows CUT OVER (storing no figure at all): {len(emptied)} (a census)")
    return not refused


def _report_perturbation(perturbation, sources, score):
    """Print one perturbation's result, naming an arm that grades NOTHING.

    The emptiness is read off ``declared`` -- what the EXPECTATION says must
    move -- rather than off what the resolver did, so "no row in this database
    is priced by that source" cannot be confused with "the resolver moved
    nothing", which would be finding N-445 one layer up.

    Args:
        perturbation: The ``_amount_resolver_perturbations.py``'s :class:`_Perturbation`
            that was applied.
        sources: How many source rows it mutated.
        score: The :class:`_Score` :func:`_score` returned.
    """
    print(f"\n  {perturbation.key} -- {perturbation.headline}")
    print(f"    source rows mutated: {sources}")
    if not score.declared:
        print(
            "    rows DECLARED to it: 0 -- THIS PERTURBATION GRADES NOTHING in "
            "the moving direction. No row in this database is priced by that "
            "source, so that arm is graded by the "
            "tests/test_services/test_amount_source/ package and by nothing here. "
            f"It still grades {score.held} rows in the HOLDING direction."
        )
    else:
        print(
            f"    rows declared to it: {len(score.declared)}, "
            f"of which moved: {score.moved}"
        )
    print(f"    rows that held: {score.held}")
    print(f"    failures: {len(score.failures)}")
    for rec, why in score.failures[:20]:
        print(
            f"      {why}: id={rec['id']} {rec['name']!r} rule={rec['rule']} "
            f"{rec['resolved']} -> {rec['perturbed'][perturbation.key]}"
        )


def _assert_restored(baseline, where):
    """Assert every row is back at its pass-1 answer after a rollback.

    **What it catches, stated narrowly because a first draft overstated it.**
    That draft said a leaked own-column rival would hand ``m7`` a free pass, by
    making a column-reading rule 3 answer exactly the movement
    ``transaction_series`` expects.  That was true while the series nudge was a
    flat ``_NUDGE`` and an adversarial review showed it is not true now: the
    leaked rival is ``answer +/- 1000 x row id`` while the expected movement is
    ``+1000 x template id``, so the coincidence needs two unrelated ids to
    match.  And under a CORRECT resolver a rival left in a derived row's column
    changes no answer at all, because no rule reads that column -- which is
    reassuring rather than alarming, and is why this arm is not the one
    guarding N-445.

    What it does catch is every leak whose source a rule DOES read: a
    version amount, a transfer's own figure, an OWN row's column, a salary
    line.  Any of those surviving a rollback would make every later arm grade a
    moved tree, and each is silent without this check.

    ``db.session.dirty`` is deliberately NOT the test: this project has
    measured that it cannot tell a rollback from a flush.  The rows are
    RE-RESOLVED and compared to the answers pass 1 recorded.

    Args:
        baseline: ``{transaction id: pass-1 answer}``.
        where: The perturbation key just rolled back, for the message.

    Raises:
        AssertionError: When any row does not resolve to its pass-1 answer.
    """
    restored = _resolve_everything()
    drifted = [
        (rid, answer, restored[rid][0])
        for rid, answer in baseline.items()
        if restored[rid][0] != answer
    ]
    if drifted:
        raise AssertionError(
            f"{len(drifted)} rows did not return to their pass-1 answer after "
            f"{where} was rolled back, so a perturbation LEAKED into the next "
            f"one and every arm after it graded a moved tree: {drifted[:5]}"
        )


def _assert_every_row_is_reachable(records, reached):
    """Assert every scored row is declared a mover by SOME perturbation.

    **The structural half of "this arm grades nothing".**  Naming an empty arm
    is only honest while the rows it does not reach are reached by another; a
    row no perturbation declares is a row pass 2 grades in the holding
    direction alone, which is the shape N-445 was.  There is no exception: a
    DERIVE-mode loan payment was one, its source the loan's own terms, while
    a loan payment's shadow took rule 4 -- which ended at plan step X-au-f-2
    (ruling R-BAL10), and the shadows themselves at X-bi-6-4d-2.

    Args:
        records: Every pass-1 record.
        reached: The union of every perturbation's declared-mover ids.

    Raises:
        AssertionError: When any scored row is unreached.
    """
    scored = {rec["id"] for rec in records if rec["refusal"] is None}
    unreachable = scored - reached
    if unreachable:
        raise AssertionError(
            "pass 2 declares no perturbation able to move "
            f"{len(unreachable)} rows, which are graded in the holding "
            "direction alone -- the state finding N-445 named: "
            f"{sorted(unreachable)[:10]}"
        )


def _stamp_control_raise(records):
    """Stamp which rows the ``salary_raise`` perturbation must reach.

    Read from :func:`_control_raise_month` -- the SAME producer
    ``_amount_resolver_perturbations.py``'s :func:`_apply_salary_raise` lands
    the raise with, called twice rather than spelled twice, so the
    perturbation and its expectation cannot come to
    disagree about which month it landed on.

    Args:
        records: The pass-1 records, stamped in place.
    """
    landing = _control_raise_month(records)
    for rec in records:
        start = rec["period_start"]
        rec["takes_the_control_raise"] = (
            landing is not None
            and rec["rule"] == AmountRule.SALARY.value
            and start is not None
            and (
                date.fromisoformat(start).year,
                date.fromisoformat(start).month,
            ) >= landing
        )


def _run_perturbations(records, baseline):
    """Apply every perturbation in turn, score it and report it.

    Args:
        records: The pass-1 records; each gains this pass's answers.
        baseline: ``{transaction id: pass-1 answer}``.

    Returns:
        ``(clean, graded_nothing)`` -- whether every arm passed, and the keys of
        the arms whose EXPECTATION declared no mover at all.

    Raises:
        AssertionError: From :func:`_assert_restored`, from the bucket-sum
            fence, or from :func:`_assert_every_row_is_reachable`.
    """
    print(
        f"\npass 2 -- invariance: {len(_PERTURBATIONS)} perturbations of "
        "the SOURCES a row's rule names"
    )
    clean = True
    reached: set = set()
    graded_nothing: list = []
    for perturbation in _PERTURBATIONS:
        try:
            with db.session.no_autoflush:
                mutated = perturbation.apply(records)
                sources = len(mutated)
                after = _resolve_everything()
        finally:
            # The perturbation lives only in the session; this is what
            # guarantees it never reaches the database even on an exception,
            # and it also restores the tree for the next one.
            db.session.rollback()
        _assert_restored(baseline, perturbation.key)
        score = _score(perturbation, records, after)
        # A fence against a future edit, NOT a measurement: on this control
        # flow every scored row increments exactly one counter, so the sum
        # cannot currently differ.  It exists so that a later ``continue``
        # cannot drop rows out of the control silently.
        graded = score.moved + score.held + len(score.failures)
        if graded != len(baseline):
            raise AssertionError(
                f"{perturbation.key} scored {graded} of {len(baseline)} "
                "resolvable rows -- the buckets do not sum, so rows fell out "
                "of the control without being graded"
            )
        reached |= score.declared
        if not score.declared:
            graded_nothing.append(perturbation.key)
        _report_perturbation(perturbation, sources, score)
        clean = clean and not score.failures
    _assert_every_row_is_reachable(records, reached)
    return clean, graded_nothing


def main(out_path=None):
    """Grade every row in the database and report.

    Args:
        out_path: Optional path to write the per-row JSON blob to.

    Returns:
        The process exit status.
    """
    app = create_app()
    with app.app_context():
        try:
            records = _baseline_records()
        finally:
            db.session.rollback()
        pass_one_clean = _report_pass_one(records)
        # The figure each row's own-column rival is derived from.  Rows that
        # REFUSED have no answer and are not scored.
        baseline = {
            rec["id"]: Decimal(rec["resolved"])
            for rec in records if rec["refusal"] is None
        }
        _stamp_control_raise(records)
        pass_two_clean, graded_nothing = _run_perturbations(records, baseline)

        if out_path is not None:
            pathlib.Path(out_path).write_text(
                json.dumps(records, indent=1), encoding="utf-8",
            )
            print(f"\nwrote {len(records)} records to {out_path}")

        if not (pass_one_clean and pass_two_clean):
            return 1
        # The banner names the arms that declared no movers, because a summary
        # reading as a clean bill for a population nobody moved is finding
        # N-445 restated in prose rather than in a loop.
        print(
            f"\nOK: no row refused, and every one of the {len(records)} rows "
            "followed exactly the source its declaration names -- and no "
            "other. Every row is reachable by some perturbation except the "
            "DERIVE-mode loan payments, which the loan prices."
        )
        if graded_nothing:
            print(
                "     BUT no row in this database is priced by "
                f"{', '.join(graded_nothing)}, so THAT MUCH IS UNGRADED HERE "
                "in the moving direction. Do not quote this run as evidence "
                "about it."
            )
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
