"""The seven perturbations ``verify_amount_resolver.py``'s pass 2 applies.

Each moves one kind of SOURCE a row's amount rule names and states what every
row's answer must then be: an ``_apply_*`` function mutates the source rows in
the session, which the harness never flushes and rolls back, and an
``_expect_*`` function reads the answer off the row's pass-1 record, never off
the resolver.  ``_PERTURBATIONS`` pairs them.

Split out of ``verify_amount_resolver.py`` when that script passed 1,000 lines
(plan#3835; ruling plan#3544).  Every name kept its leading underscore and its
docstring; the argument for each arm is that script's module docstring.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum, PaycheckLineKindEnum, RaiseTypeEnum
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import template_amount_service
from app.services.cash_ledger import AmountRule
from app.services.amount_ownership import state_own_amount
from tests._test_helpers import write_past_the_amount_seam


# The unit every money perturbation is built from.  It is SCALED by the source
# row's own id at each use, so two instances of one source kind can never move
# a row by the same amount -- see the block above the perturbations.
#
# *This constant used to argue that it was positive so
# ``ck_transactions_estimated_amount`` (``>= 0``) would still hold if a
# perturbation were ever flushed.  That is no longer true and the claim is
# deleted rather than tensed: :func:`_rival_for` writes BELOW a row's answer on
# half the rows deliberately, and the safety property is that nothing here is
# flushed at all, which is the only one this harness has ever relied on.*
_NUDGE = Decimal("1000.00")

# The flat annual raise the period-partitioned salary control lands.  A year's
# figure rather than a paycheck's, and large enough that no paycheck's net can
# round back to where it started.
_CONTROL_RAISE = Decimal("10000.00")

# The expectation for a row whose answer must MOVE UP without a stated
# magnitude -- ``salary_raise``'s, and only its.  A raise changes GROSS, and net
# is not linear in gross under either tax path, so that arm asserts a PARTITION
# and a DIRECTION; the EXACT salary magnitude is ``salary_deduction``'s, one arm
# over.  A sentinel OBJECT rather than ``None`` or a string: ``None`` is already
# what a refusal answers with, and a value that could compare EQUAL to a Decimal
# would be scored as a magnitude.  This one is recognised only by identity.
_RISES = object()


# ── The seven perturbations ─────────────────────────────────────────
#
# Each APPLIES a change to one kind of source and STATES what every row's
# answer must then be.  ``apply`` returns the rows it actually MUTATED, which
# is what the report counts; the returned list is not a lifetime device --
# SQLAlchemy strongly holds a modified attached instance itself
# (``orm/state.py`` sets ``_strong_obj`` on the first attribute change of a
# session-attached row; read in SQLAlchemy 2.0.49, 2026-09-09).  *A first draft
# of this comment claimed the identity map's WEAK references could collect a
# perturbed row and re-load it unperturbed, and gave that as the reason for
# returning the rows.  An adversarial review read the library and refuted it:
# the hazard is real for a row nothing has modified and cannot occur for one
# every ``apply`` here has just written to.*
#
# **EVERY PERTURBATION IS INSTANCE-DISTINCT, and an adversarial review is why.**
# The first draft moved every instance of a source KIND by the SAME ``_NUDGE``,
# which grades WHICH KIND of source a row reads and never WHICH INSTANCE of it.
# Three mutations passed the whole file and exited 0 under that draft: rule 3
# pricing every row from ONE fixed template's series, rule 5 answering ONE fixed
# transfer, and rule 2 resolving ONE fixed pay period -- because that instance
# was nudged too, so the row moved by exactly the expected amount.  Each is
# *every recurring row priced from the wrong definition*, which is the
# money-moving class this arc exists to prevent.  The scale is now the SOURCE
# ROW's own id, so two instances can never move a row by the same amount, and
# the expected magnitude is read off a stored column already in the pass-1
# record (``template_id``; ``transfer_id`` and ``parent_template_id`` until
# plan step balance:X-bi-6-5, when no transaction row read a transfer any more).
#
# Nothing here flushes.  ``main`` wraps each apply and re-resolution in
# ``no_autoflush`` (the series and parent lookups issue SELECTs, and an
# autoflush would push the perturbation at the database) and rolls the session
# back afterwards, which is what guarantees a perturbation never reaches the
# disk even on an exception.


def _rival_for(row_id: int, answer: Decimal) -> Decimal:
    """Return the rival figure the own-column perturbation writes for a row.

    The row's own pass-1 answer displaced by :data:`_NUDGE`, and the SIGN
    alternates on the row id.  Both properties are load-bearing:

    * **per row rather than one absolute figure**, because an absolute one
      COLLIDES.  A first draft wrote ``$1000.00`` into every derived row's
      column; the production clone states exactly that price on one template
      amount version, so on the 2 rows priced by it the "rival" WAS the true
      answer, no movement was possible either way, and both were counted as
      held -- a row graded by coincidence rather than by construction, which is
      finding N-445's own shape reappearing inside its fix;
    * **scaled by the row id**, because a rival that is merely *the answer plus
      a constant* collides between two rows sharing an answer.  An adversarial
      review counted 23 OWN rows in that shape on the clone -- six of them at
      ``$100.00`` -- and a rule 1 answering some OTHER row's column could sit on
      any of them undetected;
    * **alternating in sign**, because a one-sided rival is invisible to a
      one-sided wrong rule.  With every rival ABOVE the truth, a rule answering
      ``min(own column, source)`` holds on every row and scores as correct.
      The negative rival is never flushed, so ``ck_transactions_estimated_amount``
      (``>= 0``) is not in play, and
      :meth:`~app.models.amount_ownership.AmountOwnership.own` refuses only
      ``None``.

    Args:
        row_id: The transaction's id, which decides the direction.
        answer: The row's pass-1 answer.

    Returns:
        A figure that differs from *answer* by exactly one nudge, either way.
    """
    displacement = _NUDGE * row_id
    return answer + displacement if row_id % 2 else answer - displacement


def _apply_own_column(records):
    """Give every row a rival figure in its OWN amount column.

    An OWN row is re-priced through the sanctioned act; a DERIVED row's column
    is EMPTY and its rival is written past the mapping -- the only way to
    construct a state ``amount_ownership`` has no member for.  See
    ``verify_amount_resolver.py``'s module docstring for why
    ``state_own_amount`` cannot be used there.

    **The write is VERIFIED to have landed, and that is what makes the derived
    half self-evidencing.**  The OWN half is graded positively -- those rows
    must MOVE, so a dead write fails loudly -- but the derived half is graded
    only negatively, so a ``write_past_the_amount_seam`` that stopped reaching
    the mapped column would report every derived row as held and exit 0: N-445's
    outcome through a new door.  The suite proves that helper reaches the real
    column (``test_a_declared_row_cannot_carry_a_rival_figure_at_all``, in
    ``test_amount_source/test_what_the_rules_do_not_read.py``, flushes one and
    catches the ``IntegrityError``), but that is out of band from the run whose
    report quotes the number.

    Args:
        records: The pass-1 records.  A row that REFUSED carries no answer and
            is not scored, so it is left alone rather than given an invented
            figure.

    Returns:
        The rows this actually mutated.

    Raises:
        AssertionError: When a rival figure does not read back off the column.
    """
    baseline = {
        rec["id"]: Decimal(rec["resolved"])
        for rec in records if rec["refusal"] is None
    }
    mutated = []
    for txn in db.session.query(Transaction).all():
        answer = baseline.get(txn.id)
        if answer is None:
            continue
        rival = _rival_for(txn.id, answer)
        if txn.amount_source_id is not None:
            write_past_the_amount_seam(txn, rival)
        else:
            state_own_amount(txn, rival)
        if txn.estimated_amount != rival:
            raise AssertionError(
                f"the rival figure did not land on transaction {txn.id}: the "
                f"column reads {txn.estimated_amount} and the perturbation "
                f"wrote {rival}, so every 'held' below would be grading an "
                "unperturbed row"
            )
        mutated.append(txn)
    return mutated


def _transaction_versions():
    """Return every amount version a TRANSACTION template states."""
    return (
        db.session.query(TemplateAmountVersion)
        .filter(TemplateAmountVersion.transaction_template_id.isnot(None))
        .all()
    )


def _apply_transaction_series(_records):
    """Nudge every amount version a TRANSACTION template states.

    Scaled by the OWNING TEMPLATE's id, so a row priced from the wrong
    definition moves by the wrong amount rather than by the right one.  Takes
    the records it does not read, so every perturbation applies through ONE
    signature.

    Returns:
        The versions this mutated.
    """
    versions = _transaction_versions()
    for version in versions:
        version.amount = (
            Decimal(str(version.amount))
            + _NUDGE * version.transaction_template_id
        )
    return versions


def _apply_newest_transaction_version(_records):
    """Nudge ONLY the LATEST version of each TRANSACTION template.

    **The arm that grades the series' TIME dimension, and an adversarial review
    is why it exists.**  Every other perturbation moves a template's whole
    series together, so a resolver that answered the NEWEST version for every
    row -- ignoring supersession, and mispricing every row whose due date
    predates the latest price change -- moved by exactly the expected amount and
    passed.  ``set_amount`` kept ``default_amount`` equal to the newest
    version (until plan step balance:X-bp dropped the column), so that was
    also the defect the ``m2`` negative control was firing on for an
    incidental reason rather than because the harness could see
    supersession.

    Moving the newest version ALONE splits the population on a stored date: a
    row whose own ``due_date`` is on or after that version's ``effective_date``
    resolves to it and must move; a row below it resolves to an earlier version
    and must hold to the cent.  Both sides of that partition are columns
    (``template_amount_versions.effective_date``, ``transactions.due_date``),
    read in pass 1 by ``verify_amount_resolver.py``'s :func:`_superseded` -- so the
    expectation is a single date comparison rather than a second implementation of
    ``template_amount_service.amount_as_of``'s supersession walk, which is the
    hand-rolled-replay shape this project refuses.

    Takes the records it does not read, so every perturbation applies through
    ONE signature.

    Returns:
        The one version per template this mutated.
    """
    # ``amount_versions`` is the ONE spelling of a template's version ORDER,
    # and it is the one ``verify_amount_resolver.py``'s
    # :func:`_resolves_to_newest_version` reads too -- a
    # second max() here would be two orderings held in step by hand, in the
    # file whose whole subject is that shape (CLAUDE.md rule 14).
    latest = list({
        version.transaction_template_id:
            template_amount_service.amount_versions(
                version.transaction_template,
            )[-1]
        for version in _transaction_versions()
    }.values())
    for version in latest:
        version.amount = (
            Decimal(str(version.amount))
            + _NUDGE * version.transaction_template_id
        )
    return latest


def _apply_transfer_series(_records):
    """Nudge every amount version a TRANSFER template states.

    Scaled by the owning template's id, for the reason
    :func:`_apply_transaction_series` states.  Takes the records it does not
    read, so every perturbation applies through ONE signature.

    Returns:
        The versions this mutated.
    """
    versions = (
        db.session.query(TemplateAmountVersion)
        .filter(TemplateAmountVersion.transfer_template_id.isnot(None))
        .all()
    )
    for version in versions:
        version.amount = (
            Decimal(str(version.amount))
            + _NUDGE * version.transfer_template_id
        )
    return versions


def _apply_parent_figure(_records):
    """Nudge the figure of every transfer that OWNS its amount.

    Scaled by the TRANSFER's own id, so a shadow priced from the wrong parent
    moves by the wrong amount.  Takes the records it does not read, so every
    perturbation applies through ONE signature.

    Returns:
        The transfers this mutated.
    """
    mutated = []
    for xfer in (
        db.session.query(Transfer)
        .filter(Transfer.amount_source_id.is_(None))
        .all()
    ):
        # A transfer that owns its amount and carries none is
        # ``ck_transfers_amount_ownership`` broken: there is nothing here to
        # move (``resolve_transfer_amount`` -> ``_own_figure`` refuses it).
        if xfer.amount is None:
            continue
        state_own_amount(xfer, xfer.amount + _NUDGE * xfer.id)
        mutated.append(xfer)
    return mutated


def _apply_salary_deduction(_records):
    """Add a POST-TAX deduction of one nudge to every salary profile.

    **This is the salary SOURCE moved in the one way whose effect on net pay is
    EXACT.**  Ruling R-JF asks that derived rows move by exactly the
    perturbation, and for a paycheck that rules out almost every input: net is
    ``gross - pre-tax - taxes - post-tax`` (``paycheck_calculator``), and gross,
    the pre-tax lines and both tax paths all reach net through a non-linear
    composition.  A POST-TAX FLAT line does not -- it is subtracted last and
    whole -- so ``+$1,000.00`` of it is ``-$1,000.00`` of net on every paycheck
    that takes it, under the bracket path and the calibrated path alike.
    A line with NO recurrence rule (since plan step salary:R15-b; it was
    ``deductions_per_year=26``) is what makes "every paycheck" true: the
    engine skips a payday only where the line's rule names none.

    **Two earlier choices were rejected, and the reasons are the control's own
    argument.**  ``SalaryProfile.extra_withholding`` is exactly linear
    (``tax_calculator`` adds it to the de-annualised withholding) but is read
    ONLY on the bracket path, and the production clone's profile carries an
    ACTIVE calibration -- so it would have moved nothing and reported 59 rows
    correct, which is the defect this step exists to delete, rebuilt.
    ``annual_salary`` moves net under both paths but not by a stated amount, so
    the arm could only assert a DIRECTION -- which a rule answering GROSS
    instead of NET, or twice the net, satisfies.  An adversarial review named
    both.

    Takes the records it does not read, so every perturbation applies through
    ONE signature.  The line is appended to the loaded relationship
    ``profile.lines``, which is what ``_calculate_deductions`` iterates, so
    it is visible to the derivation without ever being flushed.

    Returns:
        The profiles this mutated.
    """
    profiles = db.session.query(SalaryProfile).all()
    for profile in profiles:
        profile.lines.append(PaycheckLine(
            salary_profile_id=profile.id,
            paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                PaycheckLineKindEnum.POST_TAX_DEDUCTION,
            ),
            calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
            name="X-bl-1 invariance control (never flushed)",
            amount=_NUDGE,
            annual_cap=None,
            inflation_enabled=False,
            is_active=True,
            sort_order=0,
        ))
    return profiles


def _control_raise_month(records):
    """Return the ``(year, month)`` the control raise lands on, or ``None``.

    The MEDIAN salary row's pay-period month, computed from the population
    rather than hardcoded, so both sides of the partition are non-empty on any
    database that has more than one salary row -- a constant would silently
    degenerate to *every row* or *no row* on a clone whose calendar sits
    elsewhere, which is an arm that grades nothing wearing a passing report.

    Called by :func:`_apply_salary_raise` and by the stamping in
    ``verify_amount_resolver.py``'s :func:`main`;
    ONE function called twice rather than two derivations of one date.

    Args:
        records: The pass-1 records.

    Returns:
        The ``(year, month)`` pair, or ``None`` when no salary row resolves.
    """
    starts = sorted(
        rec["period_start"] for rec in records
        if rec["refusal"] is None
        and rec["rule"] == AmountRule.SALARY.value
        and rec["period_start"] is not None
    )
    if not starts:
        return None
    middle = date.fromisoformat(starts[len(starts) // 2])
    return middle.year, middle.month


def _apply_salary_raise(records):
    """Land a one-off FLAT raise partway through the owner's salary rows.

    **The arm that gives rule 2 an INSTANCE dimension, and an adversarial review
    is why it exists.**  Every other perturbation is scaled by its source row's
    id, so a rule answering from the wrong instance of the right kind moves by
    the wrong amount.  ``salary_deduction`` cannot be: it shifts EVERY paycheck
    by the same figure, so a rule 2 that resolved one FIXED pay period for every
    row moved all 59 by exactly the expected amount and the run exited 0 -- with
    the profile carrying two recurring raises, that is every paycheck after the
    first mispriced by hundreds of dollars.

    A raise lands at a ``(year, month)`` and applies to every payday from it
    (``salary_raises.apply_raises`` reads only those two fields), so it splits
    the rows on their OWN pay period: rows at or after it must RISE and rows
    before it must hold to the cent.  That partition is a comparison of two
    stored dates, and it is what a fixed-period rule cannot satisfy -- whichever
    period it fixed on, it answers the same side of the split for every row.

    **The magnitude is not asserted, and that is a property of pay.**  A raise
    moves GROSS, and net is not linear in gross under either tax path.  The
    EXACT salary magnitude is ``salary_deduction``'s, one arm over; between them
    the two grade *how much* and *for which period*.

    Non-recurring with no terminal year, so it lands exactly once and
    ``_is_believed_in`` cannot drop it.  Appended to the loaded relationship
    ``profile.raises``, which is what ``apply_raises`` iterates, so it is
    visible to the derivation without ever being flushed.

    Args:
        records: The pass-1 records, read for the month the raise lands on.

    Returns:
        The profiles this mutated, or an empty list when no salary row resolves.
    """
    landing = _control_raise_month(records)
    if landing is None:
        return []
    year, month = landing
    profiles = db.session.query(SalaryProfile).all()
    for profile in profiles:
        profile.raises.append(SalaryRaise(
            salary_profile_id=profile.id,
            raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.CUSTOM),
            effective_date=date(year, month, 1),
            flat_amount=_CONTROL_RAISE,
            percentage=None,
            is_recurring=False,
            terminal_year=None,
            notes="X-bl-1 invariance control (never flushed)",
        ))
    return profiles


def _expect_salary_raise(rec, before):
    """The answer every row must give once the control raise lands.

    A SALARY row funded at or after the raise must RISE; one funded before it
    must hold to the cent.  No magnitude -- see :func:`_apply_salary_raise`.
    """
    if rec["rule"] != AmountRule.SALARY.value:
        return before
    return _RISES if rec["takes_the_control_raise"] else before


def _expect_own_column(rec, before):
    """The answer every row must give once its own column carries a rival."""
    if rec["rule"] == AmountRule.OWN.value:
        return _rival_for(rec["id"], before)
    return before


def _expect_transaction_series(rec, before):
    """The answer every row must give once transaction series move."""
    if rec["rule"] == AmountRule.TEMPLATE.value:
        return before + _NUDGE * rec["template_id"]
    return before


def _expect_newest_transaction_version(rec, before):
    """The answer every row must give once only the LATEST versions move.

    A TEMPLATE row follows it exactly when the latest version is the one in
    effect on the row's own due date; a row pricing from an EARLIER version
    must hold to the cent, which is the half that grades supersession.  The
    partition is ``verify_amount_resolver.py``'s :func:`_resolves_to_newest_version`,
    which is deliberately not the negation of :func:`_superseded` -- see
    there.
    """
    if (
        rec["rule"] == AmountRule.TEMPLATE.value
        and rec["resolves_to_newest_version"]
    ):
        return before + _NUDGE * rec["template_id"]
    return before


def _expect_transfer_series(_rec, before):
    """The answer every row must give once transfer series move: its own.

    No transaction row reads a transfer since plan step balance:X-bi-6-5
    deleted amount rule 5 (its shadows went at X-bi-6-4d-2), so every row
    holds and the arm is a negative control in the holding direction alone.
    """
    return before


def _expect_parent_figure(_rec, before):
    """The answer every row must give once owned transfer figures move: its own.

    For the reason :func:`_expect_transfer_series` gives.
    """
    return before


def _expect_salary_deduction(rec, before):
    """The answer every row must give once a post-tax line is added."""
    if rec["rule"] == AmountRule.SALARY.value:
        return before - _NUDGE
    return before


@dataclass(frozen=True)
class _Perturbation:
    """One source moved, and what every row's answer must then be.

    Attributes:
        key: The name the report and the JSON blob use.
        headline: What the report prints above the counts.
        apply: ``(records) -> [ORM row, ...]`` -- mutates the source rows and
            returns exactly the ones it mutated, which the report counts.
        expect: ``(record, before) -> Decimal`` -- the answer this row must
            give, read off its DECLARATION and stored columns, never off the
            resolver.  Returning *before* IS the statement that this
            perturbation cannot reach the row.
    """

    key: str
    headline: str
    apply: Callable[[dict], list]
    expect: Callable[[dict, Decimal], Decimal]


#: The key whose answer the JSON blob also records under the pre-X-bl-1 name
#: ``nudged``, so a ``diff`` against an older run still lines that column up.
#: Named rather than spelled twice: a rename of the perturbation would
#: otherwise stop populating the field with nothing failing.
_OWN_COLUMN = "own_column"

_PERTURBATIONS = (
    _Perturbation(
        _OWN_COLUMN,
        f"every row's own answer displaced ${_NUDGE} in its OWN column",
        _apply_own_column,
        _expect_own_column,
    ),
    _Perturbation(
        "transaction_series",
        f"+${_NUDGE} x template id on every TRANSACTION template's versions",
        _apply_transaction_series,
        _expect_transaction_series,
    ),
    _Perturbation(
        "newest_transaction_version",
        f"+${_NUDGE} x template id on each TRANSACTION template's LATEST version",
        _apply_newest_transaction_version,
        _expect_newest_transaction_version,
    ),
    _Perturbation(
        "transfer_series",
        f"+${_NUDGE} x template id on every TRANSFER template's versions",
        _apply_transfer_series,
        _expect_transfer_series,
    ),
    _Perturbation(
        "parent_figure",
        f"+${_NUDGE} x transfer id on every transfer that OWNS its amount",
        _apply_parent_figure,
        _expect_parent_figure,
    ),
    _Perturbation(
        "salary_deduction",
        f"a ${_NUDGE} POST-TAX line on every salary profile",
        _apply_salary_deduction,
        _expect_salary_deduction,
    ),
    _Perturbation(
        "salary_raise",
        f"a one-off ${_CONTROL_RAISE} FLAT raise partway through the calendar",
        _apply_salary_raise,
        _expect_salary_raise,
    ),
)
