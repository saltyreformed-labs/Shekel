"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_cash_amounts.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.services.cash_ledger import in_flight_movements
from app.services.cash_ledger._amounts import _entry_aware_amount
from tests._test_helpers import add_entry, create_envelope_txn, planted_basis


# The four days these tests turn on, named for the fact each one is.  They are
# DISTINCT on purpose: a purchase made, taken by the bank a day later, and read
# on a statement two days after that is the ordinary debit-card shape, and
# collapsing them onto one date would hide which comparison the rule makes.
_PURCHASED_ON = date(2026, 1, 20)


_POSTED_ON = date(2026, 1, 21)


# The day a statement was read.  It is SCENERY since plan step X-f3b (ruling
# **R-FM**) and kept for exactly that reason: the reservation no longer asks
# which statement showed a purchase, so a day that once decided which bucket a
# debit fell in now decides nothing here, and
# `test_the_accounts_assertion_history_is_not_an_input` is the control that
# says so.  Which statement CLEARED a movement is the walk's question, and a
# PURCHASE's own link is graded there rather than here:
# `test_cash_walk/test_the_clearing_and_order.py`'s
# `TestARecordedClearingFactMayNotMoveALineAcrossAStatement
# ::test_a_PURCHASE_carries_its_OWN_link_not_its_parents` on the read side, and
# `test_account_posting_service.py`'s
# `TestWalkAccountLedger::test_a_purchases_clearing_link_is_read_off_the_PURCHASE`
# on the posted one.  Both were ADDED at this step: the class existed for
# transactions only, and citing it for purchases before that was an invented
# citation an adversarial review caught.
_STATEMENT_DAY = date(2026, 1, 22)


def _basis(*rows, overrides=None):
    """The :class:`AmountBasis` a producer would hand these rows.

    ``overrides`` is planted on the live DERIVATIONS
    (:class:`~tests._test_helpers.PlantedPricing`) rather than into a
    ``{transaction_id: Decimal}`` map the basis carries: plan step X-au-c2b
    made a basis hold the derivations themselves, keyed on an owner and a
    scenario rather than on a row set.  Since plan step **X-au-d** the salary
    derivation is what amount RULE 2 asks directly, rather than what a
    read-time repair indexed, so a planted answer is graded through the
    resolver and not through an override seam laid over it.

    **It was a ``ProjectedBasis`` carrying the account's clearing rule beside
    this until plan step X-f3b** (ruling **R-FM**), because the reservation
    asked which statement had cleared a purchase.  It asks the purchase now, so
    the wrapper and its second field went with the question.
    """
    return planted_basis(*rows, overrides=overrides)


_POSTED_AFTER_THE_STATEMENT = date(2026, 1, 23)


def _envelope(db_session, seed_user, period, estimated, entries=()):
    """Build a Projected envelope expense carrying *entries*, and return it.

    The shared setup for the reservation tests.  Each moved test built ~50
    lines of template + transaction + entry construction inline and then
    re-queried with ``selectinload``; the re-query is not reproduced because
    the rule under test reads ``txn.entries`` through the relationship
    descriptor, which resolves either way -- a property
    :class:`TestTheEntriesRelationshipIsNotASeam` below pins explicitly rather
    than leaving it implied in 18 setups.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        period: The :class:`~app.models.pay_period.PayPeriod` to place it in.
        estimated: The envelope's budgeted amount, as a string.
        entries: An iterable of ``(amount, is_credit, settled_on)`` triples --
            a string amount, a bool, and the day the bank was seen to take the
            money (or ``None`` for a purchase not yet seen on a statement).
            The third element was a BOOL until plan step S1-c, which is the
            whole finding: a flag claimed a purchase was inside the anchor
            without saying which day made it so, while a date can be compared
            against the day the balance was actually read.  Passing the day at
            the call site is what makes each test's bucket readable there
            instead of in this helper.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.
    """
    txn = create_envelope_txn(
        seed_user, db_session, period, "Groceries", Decimal(estimated),
    )
    for amount, is_credit, settled_on in entries:
        add_entry(
            db_session, seed_user, txn, Decimal(amount), _PURCHASED_ON,
            is_credit=is_credit, settled_on=settled_on,
        )
    db_session.commit()
    return txn


def _in_flight(txn):
    """Return what the IN-FLIGHT tier holds for *txn*'s un-dated purchases.

    Ruling **R-BAL77** (plan step ``balance:X-bi-4a``) split the three-bucket
    reservation in two: :func:`_entry_aware_amount` holds the envelope's
    UNSPENT budget and the plan's in-flight tier holds each un-dated non-card
    purchase at its own figure, ``-delta`` under an expense.  The cases below
    assert both halves and their sum, which is the figure each asserted of
    the one function through ``X-bi-3e``.
    """
    return sum(
        (
            -fact.delta
            for fact in in_flight_movements(txn.account_id, txn.scenario_id)
            if fact.transaction_id == txn.id
        ),
        Decimal("0"),
    )


def _held_back(txn):
    """Return ``(unspent budget, in flight, their sum)`` for *txn*."""
    unspent = _entry_aware_amount(txn, _basis(txn))
    in_flight = _in_flight(txn)
    return unspent, in_flight, unspent + in_flight


class _FakeActiveProfile:  # pylint: disable=too-few-public-methods
    """The one attribute ``is_salary_linked_template`` reads off a profile."""

    is_active = True


class _FakeSalaryTemplate:  # pylint: disable=too-few-public-methods
    """A definition an ACTIVE salary profile names, for rule 2's refinement."""

    salary_profiles = (_FakeActiveProfile(),)


class _FakeRow:  # pylint: disable=too-few-public-methods
    """A non-ORM stand-in carrying only what a valuation rule may read.

    Deliberately missing ``entries``, and missing ``status_id`` too when
    *statusless* is set: the ordering of :func:`_entry_aware_amount`'s two
    guards is load-bearing, and that shape is what proves it (see
    :meth:`TestTheLiveOverride.test_no_entries_short_circuits_before_the_status_read`).

    **It grew the pricing columns at plan step X-au-c2b and the settlement
    record's at X-au-c3**, and the reason is the restructure itself: a
    read-time repair indexed a ``{transaction_id: Decimal}`` map the basis
    carried, so it read no column at all; the RULES read the row's own columns
    to decide which of them prices it.  Those reads are the same ones the
    producers made when they BUILT that map over a row set -- moved, not added
    -- so a stand-in for a row a valuation may see carries them.

    **A salary-shaped row is DECLARED derived since plan step X-au-d**, which
    is not a convenience: that step is what made "the salary profile prices
    this row" and "this row stores no figure" the same fact, so a stand-in that
    was salary-shaped AND owned a figure would be a state the amount model
    cannot be in.  It carries the ``template`` stub amount rule 2's refinement
    reads for the same reason.
    """

    def __init__(
        self, txn_id=None, effective_amount="77.00", statusless=False,
        *, salary_shaped=False, pay_period_id=None,
    ):
        self.id = txn_id
        # What the row OWNS, which since plan step X-au-c2 is what the
        # valuation reads: ``amount_source_id IS NULL`` says the figure is the
        # row's own, and the four attributes below are every column the OWN arm
        # and the contribution gate touch.  There is no ``effective_amount``
        # property any more -- the resolved figure arrives as an argument.
        #
        # **Plain attributes, and NOT ``amount_ownership``** (plan step
        # X-au-k).  This is a stand-in rather than a mapped row, so what it
        # owes the rules under test is their READ shape; the composite is how
        # the real model keeps those two reads consistent, and a stub that
        # carried it would have to re-implement the projection to answer them
        # at all.
        # A salary-shaped row DECLARES its definition and stores no figure
        # (plan step X-au-d); every other stand-in OWNS what it holds.  The two
        # halves are one attribute on the real model
        # (``ck_transactions_amount_ownership`` pairs them), so a stand-in that
        # set them independently could express a state no row may be in.
        self.estimated_amount = None if salary_shaped else Decimal(
            effective_amount,
        )
        self.amount_source_id = (
            ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE)
            if salary_shaped else None
        )
        # What amount rule 2's refinement reads to tell a salary-linked
        # definition from an ordinary one
        # (``template_amount_service.is_salary_linked_template``).
        self.template = _FakeSalaryTemplate() if salary_shaped else None
        # The settlement RECORD (plan step X-au-c3) is the row's ENTRIES
        # since ``balance:X-bi-4b-1``: ``settled_figure`` asks the STATUS
        # first -- a retained record on a reverted row is not what that row
        # is worth -- and the entries only for a row the status says has
        # settled, so a stand-in a valuation may see carries both.
        self.entries = []
        self.is_deleted = False
        self.status = None
        # What the live-override seam reads before it answers "nothing
        # supersedes this row": the loan half asks for a transfer, the salary
        # half for an income row on a template, and both for the two status
        # facts.  ``None`` / ``False`` throughout, so neither derivation is
        # reached and the fall-through under test is what runs.
        self.transfer_id = None
        # A row the SALARY half can answer for: income, on a template, in a
        # period.  Off by default, so the fall-through under test is what runs.
        # It became the only way to reach the seam at plan step X-au-g-2c-2 --
        # the loan half is deleted, and it was the half a planted figure used
        # to land on.
        self._salary_shaped = salary_shaped
        # The scenario the basis this row is priced against declares.  Zero on
        # both sides, matching ``planted_basis``'s default: a mismatch is what
        # ``resolve_transaction_amount`` refuses (plan step X-au-c2b).
        self.scenario_id = 0
        self.is_override = False
        self.is_income = salary_shaped
        self.template_id = txn_id if salary_shaped else None
        self.pay_period_id = pay_period_id
        # ``status_id`` is always present, and *statusless* now means the
        # ``status`` RELATIONSHIP is absent rather than the column.  Since plan
        # step X-au-c3 every valuation asks the status whether a row is worth
        # what it RECORDED or what it PLANS, so a stand-in without the column is
        # not a row any valuation could see -- and a test double built around a
        # missing attribute grades an impossible input.
        #
        # A salary-shaped row carries the PROJECTED id, because the salary
        # half's own gate is ``is_projected(txn) and not txn.is_override``.
        self.status_id = (
            ref_cache.status_id(StatusEnum.PROJECTED) if salary_shaped else None
        )
