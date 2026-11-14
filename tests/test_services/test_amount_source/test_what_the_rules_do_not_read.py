"""What the pricing rules do not read: the column a derived figure replaces, a row's status, and
the clock.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum
from app.services import template_amount_service
from app.services.amount_ownership import state_own_amount
from app.services.cash_ledger import (
    amounts_by_key,
    contribution_of,
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from tests._test_helpers import write_past_the_amount_seam, add_txn
from app.services.transfer_legs import parent_of

from ._helpers import (
    _OLD_PRICE,
    _PRICE_ROSE_ON,
    _PAYCHECK_UNDER_OLD_PRICE,
    _NOT_AN_ANSWER,
    _basis_for,
    _priced_template,
    _salary_template,
    _template_row,
    _legs_of,
    _priced_transfer_template,
    _template_transfer,
    _loan_payment,
)


class TestTheRulesDoNotReadTheColumnTheyReplace:
    """The invariance property, which agreement alone cannot show.

    The unit twin of the exhaustive oracle's second pass: a derived row's answer
    must not move when its own stored amount does.  An adversarial review
    deleted the entire resolver -- ``return txn.estimated_amount`` -- and the
    agreement pass still reported 997 of 997 rows correct, so this is the shape
    of assertion that can tell the two apart.
    """

    @pytest.mark.parametrize("nudge", [Decimal("1000.00")])
    def test_a_template_rows_answer_ignores_its_own_column(
        self, app, db, seed_user, seed_periods, nudge,
    ):
        """Re-pricing the row's column moves nothing: the definition decides.

        The nudge is written in memory and never flushed, because since plan
        step X-au-c2 it CANNOT be flushed:
        ``ck_transactions_amount_ownership`` refuses a figure beside a
        declaration, which is what turns this property from a measurement into a
        construction.  The in-memory write is still what grades the rule.

        **It is written to the private COLUMN since plan step X-au-k, and the
        reason is the property itself.**  The pair is one attribute now, so
        ``state_own_amount`` would ALSO release the declaration -- the row
        would legitimately own the nudge, the resolver would rightly answer
        it, and this test would be grading an OWN row while claiming to grade
        a derived one.  Reaching past the mapping is the only way left to hold
        a derived row beside a rival figure, and that it is the only way left
        is what X-au-k bought.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        basis = _basis_for(seed_user)
        before = resolve_transaction_amount(txn, basis)
        with db.session.no_autoflush:
            write_past_the_amount_seam(txn, nudge)
            assert resolve_transaction_amount(txn, basis) == before
            write_past_the_amount_seam(txn, None)

    def test_a_declared_row_cannot_carry_a_rival_figure_at_all(
        self, app, db, seed_user, seed_periods,
    ):
        """The invariance above, made structural rather than asserted.

        A derived row whose column holds a figure is the state the whole arc
        exists to delete, and it is the state the CHECK forbids -- so the
        rival figure the test above writes in memory cannot survive a flush.

        It reaches past the mapping for the same reason that test does: since
        plan step X-au-k the seam cannot express this state at all, so a
        control routed through it would grade the type and never reach the
        database.  Both tiers are graded, one per test.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        write_past_the_amount_seam(txn, Decimal("1000.00"))
        with pytest.raises(IntegrityError, match="ck_transactions_amount_ownership"):
            db.session.flush()
        db.session.rollback()

    def test_a_legs_answer_ignores_its_parents_column(
        self, app, db, seed_user, seed_periods,
    ):
        """Only the definition moves a generated transfer's leg.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_a_shadows_answer_ignores_both_its_own_column_and_its_parents``;
        ruling **R-BAL167** class 1).  The expense side is its LEG, priced by
        ``amounts_by_key``; the parent's rival ``$2,424.24`` and the
        re-price to ``$199.00`` stand, and the shadow's own rival
        ``$4,242.42`` went with the shadow, which has no column to hold it.
        """
        template = _priced_transfer_template(seed_user)
        xfer = _template_transfer(seed_periods[_PAYCHECK_UNDER_OLD_PRICE], template)
        expense_leg, _income_leg = _legs_of(xfer)
        basis = _basis_for(seed_user)
        with db.session.no_autoflush:
            # The rival figure is written past the mapping, for the reason
            # ``test_a_template_rows_answer_ignores_its_own_column`` states:
            # the act would release the declaration and the test would grade
            # an OWN row.
            write_past_the_amount_seam(xfer, Decimal("2424.24"))
            assert amounts_by_key([expense_leg], basis) == {
                expense_leg.cell_key: _OLD_PRICE,
            }
            write_past_the_amount_seam(xfer, None)
        template_amount_service.set_amount(
            template, Decimal("199.00"), effective_on=_PRICE_ROSE_ON,
        )
        db.session.flush()
        assert amounts_by_key([expense_leg], basis) == {
            expense_leg.cell_key: Decimal("199.00"),
        }

    def test_an_own_rows_answer_follows_its_own_column(
        self, app, db, seed_user, seed_periods,
    ):
        """The other half of the property: an OWN row's column IS its answer."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        basis = _basis_for(seed_user)
        with db.session.no_autoflush:
            state_own_amount(txn, Decimal("36.00"))
            assert resolve_transaction_amount(txn, basis) == Decimal("36.00")
            state_own_amount(txn, Decimal("35.00"))


class TestPricingReadsNoSTATUS:
    """Rules 2 and 4 answer whatever a row's status is, and that is the point.

    Plan step X-au-c2b's headline behaviour change, and an adversarial review of
    this package found it graded by NO case: every salary and loan fixture here is
    Projected, so planting ``if not is_projected(txn): return None`` at the top
    of either rule failed nothing.  The maps those rules used to index were
    built by the read-time REPAIR, which filters to Projected non-overridden
    rows -- so a Cancelled paycheck was refused for a reason that has nothing to
    do with what a paycheck is worth, and a Cancelled loan payment was refused
    as though its LOAN would not resolve, which is a different and alarming
    statement.

    Finding **N-262**'s rule one tier down: status says whether a row COUNTS,
    never what prices it.  ``routes/grid/page.py`` prices every loaded row with
    no status predicate, and ``test_which_rule_prices_a_row.py`` records that
    production carries 7
    Cancelled and 2 Credit template-linked rows, so the arm is reachable the day
    a cutover declares that bucket derived.
    """

    @pytest.mark.parametrize(
        "status_enum",
        [StatusEnum.CANCELLED, StatusEnum.CREDIT, StatusEnum.RECEIVED],
    )
    def test_a_paycheck_prices_the_same_whatever_its_status(
        self, app, db, seed_user, seed_periods, status_enum,
    ):
        """Rule 2 answers the live net for a row no repair would touch."""
        template, _profile = _salary_template(seed_user)
        projected = _template_row(
            seed_periods[0], template,
        )
        moved = _template_row(
            seed_periods[1], template,
        )
        moved.status_id = ref_cache.status_id(status_enum)
        db.session.flush()
        basis = _basis_for(seed_user)

        # Same template, two periods, so the figures are each period's own --
        # what is asserted is that BOTH answer rather than that they are equal.
        assert resolve_transaction_amount(projected, basis) != Decimal(
            _NOT_AN_ANSWER,
        )
        assert resolve_transaction_amount(moved, basis) != Decimal(
            _NOT_AN_ANSWER,
        )

    @pytest.mark.parametrize(
        "status_enum", [StatusEnum.CANCELLED, StatusEnum.CREDIT],
    )
    def test_a_loan_payment_prices_the_same_whatever_its_status(
        self, app, db, seed_user, seed_periods, status_enum,
    ):
        """Rule 4 resolves the loan for a transfer no balance is counting.

        The repair (``LoanPricing.live_cash``) answered ``None`` for a
        Cancelled or Credit shadow, which was correct -- there is no stored
        figure to supersede on a row nobody is counting -- while the RULE still
        priced it.  Those being different questions is the whole split, and it
        outlived the repair: plan step X-au-g-2c-2 deleted the producer, and
        what remains is that the row's BUDGET is its budget whatever its status
        (ruling E-21), while its CONTRIBUTION is ``$0.00``.  Both are asserted,
        because either alone would pass on a rule that had started reading
        status.

        **Asked of the TRANSFER and its checking-side LEG since plan step
        balance:X-bi-6-4d-2** (ruling **R-BAL167** class 1): the status is the
        transfer's, its price is ``resolve_transfer_amount``'s and the leg's
        contribution is ``contribution_of``'s, where all three were the
        checking-side shadow's.  No door moves a transfer to Credit -- the
        state machine's transfer workflow has no Credit edge -- so, as on the
        shadow, the status is written directly; the database holds it, since
        no CHECK constrains a transfer's status.
        """
        xfer = _loan_payment(seed_user, derive=True)
        xfer.status_id = ref_cache.status_id(status_enum)
        db.session.flush()
        # The status RELATIONSHIP, not just the column: the leg's contribution
        # reads the transfer's ``status`` and the assignment above moves only
        # the id, so an unexpired row would answer the contribution of the
        # status it had.
        db.session.refresh(xfer)
        basis = _basis_for(seed_user)
        expense_leg, _income_leg = _legs_of(xfer)

        assert resolve_transfer_amount(xfer, basis) == Decimal("1499.10")
        assert contribution_of(parent_of(expense_leg), basis) == Decimal("0")


#: Every producer that answers "what day is it" without being told.  Matched by
#: the CALLED name, so ``date.today()``, ``datetime.now(...)``,
#: ``dt.datetime.utcnow()`` and a bare ``display_today()`` all count however the
#: module spelled its import.
#:
#: **A CONVERTER is not a clock**, and an early draft of this set said otherwise:
#: ``utc_instant(row.created_at)`` normalises a STORED instant the caller
#: already has, so listing it made ``cash_ledger._events`` read as a clock
#: reader twice over.  The distinction is whether the call takes the moment as
#: an argument or invents it.
#:
#: **What this does NOT catch, enumerated rather than claimed away.**  It
#: matches the CALLED NAME in this package's own source, so four shapes pass:
#: an alias (``from app.utils.dates import display_today as d`` then ``d()``);
#: a bound reference (``_CLOCK = date.today`` then ``_CLOCK()``); a SQL clock
#: (``func.current_date()``, ``text("CURRENT_DATE")``); and -- the widest hole
#: -- a TRANSITIVE read, where a module here calls a helper that reads the
#: clock somewhere else.  An adversarial review demonstrated the last one:
#: adding a ``status_seam.day_is_in_the_future(due)`` branch to
#: ``_loan_installment`` left this green while pricing every future installment
#: with no escrow.
#:
#: Closing it would need a call graph, and a name-keyed call graph
#: OVER-connects -- it can establish "something reaches X", never "nothing
#: does", which is the direction this claim needs.  So the scope is stated
#: instead: **this grades the shapes this package writes today**, and it is the
#: reason the module docstrings say the package makes no clock CALL rather than
#: that it cannot read a clock.
_CLOCK_PRODUCERS = frozenset({
    "today", "now", "utcnow", "display_today",
})


def _clock_calls_in(root):
    """Return ``["<path>:<line>: <expression>", ...]`` for every clock call under it.

    An AST walk rather than a grep: a grep for ``date.today`` matches the word
    inside a docstring, and this package's docstrings discuss the clock read it
    no longer makes at length.  Only a ``Call`` node whose called NAME is a
    clock producer counts.

    Args:
        root: A :class:`pathlib.Path`, a module read alone or a directory walked.

    Returns:
        One human-readable location string per clock call found, in path order.
    """
    # Pylint: import-outside-toplevel -- the file-wide test convention.
    import ast  # pylint: disable=import-outside-toplevel

    found = []
    for path in [root] if root.is_file() else sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            else:
                continue
            if name in _CLOCK_PRODUCERS:
                found.append(f"{path}:{node.lineno}: {ast.unparse(node)}")
    return found


class TestTheAmountModelReadsNoClock:
    """No module of ``cash_ledger`` asks what day it is.

    **Ruling R-IJ's structural consequence** (plan step X-au-g-2b).  The amount
    model's whole job is *what is this row's amount*, and that is a question
    about the ROW: its rules read the row's own dates -- a definition's series
    ``as of`` the row's due date, a loan installment's P&I and escrow on the
    installment's own day.  A clock read inside it means some row is priced by
    when the page was opened rather than by what it is.

    ``_amount_basis.amount_basis`` held the last one, building the loan
    derivation as ``loan_pricing(scenario_id, date.today())`` -- finding
    **N-40**.  The salary half lost its own at plan step X-as.  With both gone
    the package makes NO clock call at all, and that is worth pinning as a
    property rather than leaving as a paragraph: it is the difference between a
    fact and a habit, and the next derivation added here is the one that would
    reintroduce it.

    Stated as an ABSENCE, so it needs TWO controls that the answer is not
    empty for the wrong reason -- a broken walker and a clock-free package
    return the same thing.  ``test_the_walker_can_see_a_clock_call`` shows the
    walker firing; ``test_the_scan_reaches_this_package`` shows it reading THIS
    directory, which the first cannot, since it reads one module outside it.

    The shapes this does not catch are enumerated on :data:`_CLOCK_PRODUCERS`,
    including the transitive one an adversarial review used to slip a wall-clock
    branch past it.
    """

    @staticmethod
    def _cash_ledger_dir():
        """Return the ``app/services/cash_ledger`` package directory."""
        # Pylint: import-outside-toplevel -- the file-wide test convention.
        import pathlib  # pylint: disable=import-outside-toplevel

        # Pylint: import-outside-toplevel -- the file-wide test convention.
        import app.services.cash_ledger as package  # pylint: disable=import-outside-toplevel

        return pathlib.Path(package.__file__).parent

    def test_the_walker_can_see_a_clock_call(self):
        """The walker finds a clock call where one is known to be.

        Without this the assertion below is satisfied by a walker that parses
        nothing: an empty list is what "no clock" and "measured nothing" both
        look like.  ``loan_payment_service.load_loan_context`` resolves the
        loan CARD's escrow at ``date.today()`` -- legitimately: the card answers
        what the owner pays THIS month -- a stable positive, read as the module
        file alone, since its parent ``app/services`` holds other clock calls.
        """
        # Pylint: import-outside-toplevel -- the file-wide test convention.
        import pathlib  # pylint: disable=import-outside-toplevel

        # Pylint: import-outside-toplevel -- the file-wide test convention.
        import app.services.loan_payment_service as module  # pylint: disable=import-outside-toplevel

        found = _clock_calls_in(pathlib.Path(module.__file__))
        assert found, (
            "no clock call found in loan_payment_service.py, where the card's "
            "escrow is resolved at date.today(): the walker measures nothing"
        )

    def test_the_scan_reaches_this_package(self):
        """The cash_ledger scan reads real files, and enough of them.

        The positive control above reads ``loan_payment_service.py`` -- one
        module OUTSIDE this package -- so it cannot tell whether the scan of
        THIS one read anything.  Without this, "no clock in the amount model" and
        "resolved a path with no ``.py`` in it" are the same empty list.
        """
        scanned = sorted(
            path.name for path in self._cash_ledger_dir().rglob("*.py")
        )
        assert "_amount_basis.py" in scanned and "_loan_installment.py" in scanned
        assert len(scanned) >= 12, (
            f"the scan found only {len(scanned)} module(s) in the amount "
            f"model, so its clock census covers almost nothing: {scanned}"
        )

    def test_no_module_of_the_amount_model_reads_a_clock(self):
        """``app/services/cash_ledger`` makes no clock call."""
        found = _clock_calls_in(self._cash_ledger_dir())
        assert found == [], (
            "the amount model read the clock; a row's amount is a question "
            "about the ROW, and its own dates are what price it (ruling "
            f"R-IJ): {found}"
        )
