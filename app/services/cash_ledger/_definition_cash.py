"""
Shekel Budget App -- Cash ledger: what a DEFINITION's row is worth, written or not.

The tier below :mod:`._amount_source`, split out at plan step **R16-b-2**
(ruling **R-R67**, developer 2026-09-11) when that module passed
``max-module-lines`` -- and the split is by QUESTION, not by size.
``_amount_source`` answers *what is this ROW's amount* and dispatches on the
row's declaration; this module answers *what does this DEFINITION say a row
is worth on a date*, over the four VALUES such a row carries or would carry
(:class:`DefinitionRow`).  The row arms up there delegate down here, and the
balance seam's ESTIMATED loan tier (``balance_at._plan``) prices every
occurrence no row answers yet through :func:`definition_cash` -- so a row and
its estimate are ONE function, and a loan's payoff cannot move the moment
generation writes the row.

**Why one function, stated once.**  ``recurring_transfer_query
.standing_installment_cash`` was a copy of these arms, kept in step by intent
("the arms are deliberately the same cases so the two cannot come to
disagree"), and measured a cent apart on a loan's last contractual
installment: the copy read the contract's residual last row where the row
reads the rate period's level payment, so a `$3,000` loan at 5% over three
months read its payoff `2026-04-15` before the rows existed and `2026-05-15`
after (finding **REC-517**).  Two spellings that agree today are still two
spellings (``CLAUDE.md`` rule 14).  The copy is deleted; the cent that
remains is the amount model's own question, re-owned to ``recurrence:R16-f``.

Two arms, decided the way :func:`._amount_rule.transfer_amount_rule` decides
them for a written row:

* **rule 4** (:func:`_loan_payment_cash_from`) -- a loan payment, one arm per
  MODE: derive from the loan (:class:`._loan_pricing.LoanPricing`), or the
  definition's stated series plus the standing extra;
* **rule 3** (:func:`_stated_amount`) -- any other definition's stated
  series as of the row's own due date.

Rule 1 -- a figure the owner typed onto the row -- has no analogue for a row
that does not exist, which is exactly why a written row's own figure always
wins over the estimate once it is there.

Boundary discipline: no Flask, no session of its own, no clock; every money
value is :class:`~decimal.Decimal`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.exceptions import AmountUnresolvable
from app.services import template_amount_service
from app.services.recurring_transfer_query import loan_payment_config
from app.utils.money import round_money

from ._amount_basis import AmountBasis
from ._amount_rule import is_loan_payment_definition


@dataclass(frozen=True)
class DefinitionRow:
    """The four columns a generated transfer carries, or WOULD carry, that price it.

    What rule 3 and rule 4 are functions of, lifted off the row (plan step
    **R16-b-2**, ruling **R-R67**) so a row that exists and an occurrence no
    row answers yet are priced through one signature: the written transfer
    builds this from its own columns, the balance seam's estimate from the
    occurrence it is pricing and the paycheck the row would live in.  A value
    rather than four positional arguments because they are one thing -- a
    row's pricing inputs -- and the seam builds hundreds per loan.

    Attributes:
        template: The recurring transfer definition (``Transfer.template``).
        due_date: The row's own due date -- its ``due_date`` column, or the
            date the occurrence's row would carry
            (:func:`app.services.recurrence.compute_due_date`); for a loan
            payment, the installment it pays, if any, is the one this date
            falls in (ruling **R-R104**).  Never
            ``None``: a written row here names a definition, and
            ``ck_transfers_template_row_needs_due_date`` (plan step
            **X-bv-2**, ruling **R-BAL17**) refuses such a row without a
            date, so the state this was ``date | None`` for -- an owner
            clearing it -- cannot be stored.
        period_start: The payday of the paycheck funding it
            (``pay_period.start_date``).  Rule 4's derive arm still takes it
            beside the date because :func:`app.services.loan_loaders.installment_for`
            serves an AD-HOC loan payment too, which names no definition and
            may carry no date; on this row the date is always present and
            the fallback is never reached.
        to_account_id: The account the definition pays into.
    """

    template: object
    due_date: date
    period_start: date
    to_account_id: int


def _stated_amount(template, on_date: date, subject: str) -> Decimal:
    """Return what *template* states for ``on_date``, refusing when it states nothing.

    The series arm shared by rule 3 and by :func:`resolve_transfer_amount`, so
    the two cannot come to disagree about what "the definition's price" means.
    Resolution itself is ``template_amount_service.amount_as_of``: the newest
    version at or before the date, holding FLAT before the earliest one, which
    is what makes it total for a row generated into a historical period.

    Two refusals, and both are states the app can reach:

    * **the definition does not own its amount**
      (``template_amount_service.owns_its_amount`` is False -- a salary-linked
      template, a derive-mode loan payment).  Its price is COMPUTED by something
      else, so any versions it holds are dormant rather than authoritative.
      **The test for this is not "is the series empty", and a control found the
      difference**: a template switched from manual to derive-mode KEEPS the
      versions stated while it was manual (X-au-a's stated behaviour -- they are
      the record of what was stated then), so an emptiness test would answer a
      derive-mode payment from a price nobody is stating any more.
    * **an empty series** on a definition that DOES own its amount: its creator
      wrote the scalar without going through
      ``template_amount_service.set_amount``, the one write door.  Nobody ever
      stated a price, and X-au-a's own docstring names this refusal as the
      reason it answers ``None`` instead of guessing.

    **A third refusal -- no due date -- was DELETED at plan step X-bv-2**
    (ruling **R-BAL17**), not re-targeted.  It refused a linked row carrying
    no date, because a pay period's bounds are not a substitute for one (a
    period begins up to two weeks before the installment it funds, ruling
    D5's contract time, so a price change inside that window would answer
    one figure here and another everywhere else).  That argument is now the
    schema's: ``ck_transactions_template_row_needs_due_date`` and
    ``ck_transfers_template_row_needs_due_date`` refuse the row at flush,
    every writer of a linked row dates it (``compute_due_date``'s answer,
    which always exists, or the chosen paycheck's start where the definition
    names no cadence -- the one-time transfer, the cleared-rule carry-forward
    arm -- which is that function's own answer for a rule dated on its
    funding payday),
    and an occurrence no row answers yet is dated by the same function -- so
    ``on_date`` is a ``date`` here by construction, and a refusal over a
    state that cannot be stored is a fence.

    **It names its SUBJECT rather than a row, since plan step R16-b-2.**  A
    row is one subject -- "Transfer 123" -- and an occurrence no row answers
    yet is another -- "template 9's occurrence 2026-04-22" -- and the balance
    seam's ESTIMATED loan tier prices the second through this same arm
    (:func:`definition_cash`, ruling **R-R67**), so the message says what was
    being priced instead of assuming it had an id.

    Args:
        template: The transaction or transfer template that states the price.
        on_date: The row's own due date, or the date the occurrence's row
            would carry.
        subject: What is being priced, for the refusal message -- a row
            (``"Transfer 123"``) or an occurrence.

    Returns:
        The stated amount on ``on_date``.

    Raises:
        AmountUnresolvable: When the definition's amount is derived rather than
            stated, or when the series is empty.
    """
    if template is None:
        raise AmountUnresolvable(
            f"{subject} names a template that could not be "
            "loaded, so the definition that states its price is gone. The "
            "database cannot hold this pairing -- it is a row whose template "
            "was hard-deleted in this same session while the row still "
            "carried the id, a state only the transfer twin can still reach: "
            "budget.transfers.transfer_template_id is ON DELETE SET NULL, "
            "where budget.transactions.template_id has been ON DELETE "
            "RESTRICT since plan step balance:X-bi-7d-2."
        )
    if not template_amount_service.owns_its_amount(template):
        raise AmountUnresolvable(
            f"{subject} is priced by template "
            f"{template.id}, whose own amount is DERIVED rather than stated -- "
            "a salary-linked template, or a loan payment in derive mode. Its "
            "price series is dormant and may still hold versions stated while "
            "it owned its amount, so reading it here would answer a price "
            "nobody is stating any more. The rule that prices this row is the "
            "one that computes the definition's amount, and it had no answer."
        )
    stated = template_amount_service.amount_as_of(template, on_date)
    if stated is None:
        raise AmountUnresolvable(
            f"{subject} is priced by template "
            f"{template.id}, which states no amount for {on_date.isoformat()} "
            "-- its price series is EMPTY. Either its amount is derived rather "
            "than stated (template_amount_service.owns_its_amount is False), "
            "or it was created without going through set_amount, the one write "
            "door. There is deliberately no fallback to default_amount: that "
            "scalar has no time dimension, so reading it here would price a "
            "March row at June's figure."
        )
    return stated


def _loan_payment_cash_from(
    row: DefinitionRow, basis: AmountBasis, *, subject: str,
) -> Decimal:
    """Rule 4 over the four VALUES a loan-payment row carries, or would carry.

    The body :func:`_loan_payment_cash` had until plan step **R16-b-2**, taken
    out of the row so the balance seam's ESTIMATED loan tier can price an
    occurrence NO ROW answers yet through the identical arms
    (:func:`definition_cash`, ruling **R-R67**): the row and its estimate are
    ONE function, so a loan's payoff cannot move when generation writes the
    row.  ``standing_installment_cash`` was a second spelling of these arms
    and is deleted by the same step; finding **REC-517** is the cent the two
    spellings parted by.

    Args:
        row: The loan-payment row's four pricing columns, written or
            would-be (a settings row hangs off its template).
        basis: The read pass's basis; its ``loans`` derivation resolves the
            loan.
        subject: What is being priced, for the refusal messages.

    Returns:
        The payment's live cash.

    Raises:
        AmountUnresolvable: When a DERIVE-mode payment's loan will not resolve,
            or when a MANUAL payment's definition states no price.
    """
    derive, extra = loan_payment_config(row.template)
    if derive:
        live = basis.loans.derive_cash(
            row.due_date, row.period_start, row.to_account_id, extra,
        )
        if live is None:
            raise AmountUnresolvable(
                f"{subject} is a DERIVE-mode loan payment and the "
                "loan would not resolve, so its P&I has no answer. The "
                f"destination account {row.to_account_id} carries no "
                "LoanParams, or its schedule could not be built. The stored "
                "figure is not a fallback: on a derive-mode payment it is a "
                "snapshot of exactly the computation that just failed."
            )
        return live
    return round_money(
        _stated_amount(row.template, row.due_date, subject) + extra
    )


def definition_cash(
    row: DefinitionRow, basis: AmountBasis, *, subject: str,
) -> Decimal:
    """Return what a row *template* has NOT written yet would be worth.

    **The estimate is the row's own rule** (plan step **R16-b-2**, ruling
    **R-R67**, developer 2026-09-11).  The balance seam's forward loan plan
    prices every occurrence a definition names that no row in any state
    answers, and until this function it priced them through
    ``recurring_transfer_query.standing_installment_cash`` -- a copy of the
    arms below, kept in step by intent and measured a cent apart on the last
    contractual installment (finding **REC-517**: the copy read the contract's
    residual last row where the row reads the rate period's level payment, so
    the payoff flipped one installment the moment the rows existed).  A row
    and its estimate now share one function, so the two cannot part, and the
    remaining cent is the amount model's own question (REC-517, re-owned to
    ``recurrence:R16-f``).

    Which rule prices a generated transfer is decided the way
    :func:`._amount_rule.transfer_amount_rule` decides it for a written one:
    a definition with a loan-payment settings row is rule 4
    (:func:`_loan_payment_cash_from`, either mode), and any other definition
    is rule 3 (:func:`_stated_amount`).  Rule 1 -- a figure the owner typed
    onto the row -- cannot apply to a row that does not exist, which is why a
    written row's OWN figure always wins over this estimate once it is there.

    **An EMPTY series REFUSES here as it does on the row**, where the copy
    this replaces answered the contract instead.  A definition owning its
    amount with no version was written around the one write door
    (``template_amount_service.set_amount``), and the honest answer for money
    nobody has stated is the refusal the row would raise, not a figure the
    definition never gave.  Both live loan definitions state a price
    (measured on the 2026-09-11 production clone).

    Args:
        row: The occurrence as the :class:`DefinitionRow` its transfer would
            be -- the definition, the date the row would carry, the payday
            of the paycheck it would live in, and the account it pays into.
        basis: The read pass's basis.
        subject: What is being priced, for the refusal messages -- the
            template and the occurrence, since there is no row to name.

    Returns:
        The occurrence's cash, exactly what the generated row would resolve
        to.

    Raises:
        AmountUnresolvable: See :func:`_loan_payment_cash_from` and
            :func:`_stated_amount`.
    """
    if is_loan_payment_definition(row.template):
        return _loan_payment_cash_from(row, basis, subject=subject)
    return _stated_amount(row.template, row.due_date, subject)


def stated_price(template, on_date: date, *, subject: str) -> Decimal:
    """Rule 3 for a reader above this package: what *template* states for ``on_date``.

    The public door onto :func:`_stated_amount`, the series arm rule 3 and
    rule 4's manual arm share, for a reader pricing a DEFINITION on a day
    rather than a row (plan step **X-bp**, ruling plan#2693): the Recurring
    page and the savings page price each definition as of the read pass's
    day by the rule that prices its rows
    (``obligations_aggregator.todays_price``).  A definition no loan-payment
    settings row names is rule 3 there as it is here, and rule 3 needs no
    :class:`AmountBasis` -- which is why that reader asks this door rather
    than :func:`definition_cash`: building a basis refuses an owner with no
    baseline scenario, and ruling **R-R30** allows that refusal only for a
    definition that pays into a loan.

    Args:
        template: The transaction or transfer template that states the price.
        on_date: The day to price.
        subject: What is being priced, for the refusal messages.

    Returns:
        The stated amount on ``on_date``.

    Raises:
        AmountUnresolvable: See :func:`_stated_amount`: the definition's
            amount is derived rather than stated, or its series is empty.
    """
    return _stated_amount(template, on_date, subject)


__all__ = ["DefinitionRow", "definition_cash", "stated_price"]
