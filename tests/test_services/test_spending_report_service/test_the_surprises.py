"""Estimate surprises: which rows surface, the cap and the net, and a row whose plan the amount
model derives, settled or not.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.services import spending_analysis, status_seam, template_amount_service
from app.utils.dates import display_today
from app.services.cash_ledger import derived_amount_basis
from app.services.transfer_legs import leg_of
from app.services.spending_report_service import compute_spending_report
from app.services.spending_report_service._surprises import _MAX_SURPRISES, _build_surprises
from tests._test_helpers import (
    cover_bare_settled_row,
    create_savings_account,
    create_settled_transfer,
    generate_row_of,
    make_expense_template,
    settle_day_columns,
    state_template_price,
)
from tests._definition_price import newest_price

from ._helpers import _txn, _pp_window


# ── Surprises ────────────────────────────────────────────────────────


class TestSurprises:
    """Estimate surprises: differs vs equals vs no-actual, cap + net."""

    def test_surprises_signed_delta_and_net(self, app, seed_user, seed_periods, db):
        """Only rows whose actual differs surface; net sums their deltas.

        Rent est 1200 actual 1250 (+50); Car est 300 actual 280 (-20);
        Groceries est 400 actual 400 (0, not a surprise); Utility est 100
        actual None (resolves to estimate, delta 0, not a surprise).
        Sorted by |delta| desc: Rent(+50) then Car(-20).  Net = +30.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "1200.00",
                 actual="1250.00")
            _txn(db, seed_user, seed_periods[0], "Car", "Car Payment", "300.00",
                 actual="280.00")
            _txn(db, seed_user, seed_periods[0], "Food", "Groceries", "400.00",
                 actual="400.00")
            _txn(db, seed_user, seed_periods[0], "Util", "Rent", "100.00",
                 actual=None)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            rows = report.surprises.rows
            assert [r.name for r in rows] == ["Rent", "Car"]
            assert rows[0].delta == Decimal("50.00")
            assert rows[0].estimated == Decimal("1200.00")
            assert rows[0].actual == Decimal("1250.00")
            assert rows[1].delta == Decimal("-20.00")
            # Net = +50 + (-20) = +30.
            assert report.surprises.net == Decimal("30.00")

    def test_surprises_capped_but_net_is_full(self, app, seed_user, seed_periods, db):
        """The list caps at _MAX_SURPRISES; the net sums ALL surprises.

        Six surprises of +10..+60; the list keeps the 5 largest (+60..+20)
        but the net is +10+20+30+40+50+60 = +210.
        """
        with app.app_context():
            for i in range(1, 7):
                est = Decimal("100.00")
                _txn(db, seed_user, seed_periods[0], f"S{i}", "Rent",
                     est, actual=est + Decimal(str(i * 10)))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert len(report.surprises.rows) == _MAX_SURPRISES
            assert report.surprises.rows[0].delta == Decimal("60.00")
            # Net includes the capped-off +10 surprise too.
            assert report.surprises.net == Decimal("210.00")


class TestTheActualHalfAsksTheAmountModel:
    """`resolved_actual_amount`'s fall-through, which had NO test at all.

    **The kernel was public, documented as the shared "what did this row
    actually cost" rule, and reached by exactly one settled-only caller** -- so
    every case in this package drove it through the arm that returns a settlement,
    and the arm below ran nowhere in the suite.  Two neutral adversarial reviews
    of plan step X-bu found what was hiding there: it read the row's own
    ``estimated_amount`` column and REFUSED a row carrying none, which is
    finding **BAL-462**'s failure mode on the UNSETTLED population instead of
    the settled one -- the same exception, the same page, the same class of row,
    held off only by a caller convention.  X-bu re-pointed it at the amount
    model (developer ruling 2026-09-05, superseding BAL-462's "is correct and
    stays" clause) and this class is the arm's first coverage.

    **Its fixture is an engine-generated row since plan step
    balance:X-bi-6-4d-2**, which made a transfer one row (ruling R-BAL258).
    It was a projected transfer's expense shadow until then: the twin was the
    class's FIXTURE for an unsettled derived row, replaced by another member
    of that population (an engine-generated row), assertions unchanged (the
    coordinator's ruling, 10-15; not an R-BAL167 class).  A transfer LEG
    takes the fall-through's other arm --
    :func:`~app.services.cash_ledger.resolve_amount` prices it off its
    parent -- and ``test_the_scope_and_the_cash_flow_set.py``'s
    :meth:`TestATransferIsALegOfItsParent.test_a_leg_reads_its_record_when_settled_else_its_plan`
    grades that arm (an unsettled leg's actual is its plan, ``$75.00``).
    """

    @staticmethod
    def _unsettled_derived_row(db, seed_user, period):
        """Generate a definition's row in *period*: unsettled, priced $321.00 on its own date.

        The definition states TWO prices, so the cases grade which one the
        fall-through reads: ``$321.00`` effective thirty days before the row,
        the version the row's DATE selects, and ``$200.00`` on the owner's
        today (:func:`make_expense_template`'s own statement), the NEWEST
        price and so the one ``default_amount`` held.  A fall-through that
        answered the definition's newest price rather than its price on the
        row's own due date would read ``$200.00``.  Both prices were
        ``$321.00`` when plan step ``balance:X-bi-6-4d-2`` first built this
        helper, and a fall-through reading ``default_amount`` passed both
        cases (measured by that step's delta review).

        The two facts hold on every clock later than the row's due date -- a
        seeded January 2026 day, so every day this suite runs from now on --
        and the guards below assert each, so a clock that broke one fails
        its guard by name rather than the figure (the guard shape of
        ``test_a_template_priced_row_reads_the_version_for_its_own_due_date``,
        which names the clock trap).
        """
        template = make_expense_template(
            db.session, seed_user, amount="200.00", name="Derived Bill",
        )
        db.session.flush()
        state_template_price(
            template, Decimal("321.00"),
            effective_on=period.start_date - timedelta(days=30),
        )
        db.session.flush()
        row = generate_row_of(template, period)
        db.session.commit()
        assert newest_price(template) == Decimal("200.00"), (
            "the newest price must differ from the one the row's date "
            "selects, or a fall-through reading the newest price passes"
        )
        assert template_amount_service.amount_as_of(
            template, row.due_date,
        ) == Decimal("321.00"), (
            "the row's due date must select the $321.00 stated before it, "
            "which a today-version dated between the two would supersede"
        )
        return row

    def test_an_unsettled_derived_row_is_PRICED_not_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """An unsettled row of a definition: no figure of its own, and a plan anyway.

        The fixture is a production shape rather than a contrivance: a row
        the recurrence engine generates from a recurring definition is
        DERIVED -- it stores no plan, and its definition's price series
        answers for it on its own due date -- which is exactly what the old
        column read could not answer.

        It asserts the FIGURE, not the absence of an exception: a test that only
        proved this returns would pass against a fix that priced the row from
        its settlement or from zero, and `$0.00` is precisely the answer that
        never looks wrong on a page.
        """
        with app.app_context():
            row = self._unsettled_derived_row(db, seed_user, seed_periods[0])
            assert row.estimated_amount is None, (
                "the precondition: a generated row stores no plan of its "
                "own, so the column read this arm used to make cannot answer it"
            )
            assert status_seam.recorded_settlement(row) is None, (
                "and it has recorded nothing, so this row reaches the "
                "fall-through rather than the settlement arm"
            )

            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert spending_analysis.resolved_actual_amount(
                row, basis,
            ) == Decimal("321.00"), (
                "an unsettled row's actual is what the amount model says its "
                "plan is -- its definition's price on its own due date"
            )

    def test_an_unsettled_rows_variance_is_zero_BY_CONSTRUCTION(
        self, app, db, seed_user, seed_periods,
    ):
        """Both halves are the same call, so there is nothing to disagree.

        The property the re-point buys, and the reason it is worth more than a
        second producer that happens to agree: the surprises list's two terms
        for an unsettled row are now ``resolved_actual_amount`` and
        ``resolve_transaction_amount`` over the SAME row and the SAME basis, so
        the delta cannot be non-zero.  Before X-bu the two were different
        producers over different sources -- the column and the amount model --
        and on a derived row they did not merely differ, one of them RAISED.

        The same two preconditions as the case above, so this cannot pass
        through the settlement arm: a row that had recorded its plan would
        also read a zero variance.
        """
        with app.app_context():
            row = self._unsettled_derived_row(db, seed_user, seed_periods[0])
            assert row.estimated_amount is None, (
                "the precondition: a generated row stores no plan of its own"
            )
            assert status_seam.recorded_settlement(row) is None, (
                "and it has recorded nothing, so both halves reach the "
                "amount model rather than the settlement arm"
            )
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            surprises = _build_surprises([row], basis)

            assert surprises.rows == [], (
                "an unsettled row is not a surprise: its actual and its "
                "estimate are one call on one row against one basis"
            )
            assert surprises.net == Decimal("0")


class TestASettledRowWhosePlanIsDerivedIsPriced:
    """A settled row carrying no figure of its OWN still has a plan."""

    def test_a_settled_transfer_leg_is_priced_from_its_parent_not_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The estimate is the PARENT's figure, and the delta is real.

        Regression for the release blocker measured on a production clone
        2026-09-05.  The amount-source cutovers declare a row DERIVED and empty
        its ``estimated_amount``; 116 of the rows they declared on production
        are SETTLED, and a settled EXPENSE is exactly what this window loads.
        This list read ``owned_amount``, a cheap accessor that answered a row's
        own column and REFUSED one carrying none -- so the first derived row it
        met raised ``AmountUnresolvable`` and ``/analytics/spending`` returned
        500 for every window.  Measured: 200 on the shipped image, 500 on the
        candidate, across 323 routes the only regression.  *That accessor no
        longer exists: plan step X-bu deleted it and folded its body into the
        resolver's own OWN arm, so the one-token answer this list reached for is
        gone.  X-bu also took the ACTUAL half's fall-through onto the same
        resolver, which is the sibling case ``TestTheActualHalfAsksTheAmountModel``
        above pins.*

        **It asserts the FIGURE, not the absence of an exception.**  A test that
        only proved this returns at all would pass against a fix that priced the
        row wrongly, and the production symptom the sibling defect carries is
        ``+$9.81`` on a paycheck -- exactly the size that never looks wrong on a
        page.  So the estimate is pinned to the parent's ``$321.00`` and the
        delta to the ``$79.00`` the settlement actually recorded.

        The reducer is handed the transfer's expense LEG -- what
        ``query_settled_expenses`` loads for a transfer -- where it was handed
        the expense twin row, which records nothing since plan step
        ``balance:X-bi-6-4d-2`` (the side's record hangs off the transfer and
        the transfer's status is the one status; ruling R-BAL167 class 1).
        Its precondition read that twin -- ``estimated_amount is None``, the
        shadow storing no plan of its own -- and went with it at plan step
        ``balance:X-bi-6-4d-2`` (ruling R-BAL258, class 1 with nothing
        re-expressed): no such row can be stored now
        (``test_a_transfer_is_one_row/test_the_upgrade.py::TestTheUpgrade::
        test_the_database_refuses_a_twin``),
        and a leg has no figure field at all, its plan being the parent's
        through :func:`~app.services.cash_ledger.resolve_amount`.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Surprises Savings",
                Decimal("1000.00"),
            )
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
                amount=Decimal("321.00"), settled_amount=Decimal("400.00"),
            )
            db.session.commit()

            expense_leg = leg_of(xfer, seed_user["account"].id)

            surprises = _build_surprises(
                [expense_leg],
                derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id),
            )

            assert len(surprises.rows) == 1, (
                "a settled row whose actual differs from its plan is a surprise "
                "whether or not the plan is stored"
            )
            row = surprises.rows[0]
            assert row.estimated == Decimal("321.00"), (
                "the plan is the parent transfer's figure, resolved live -- not "
                "the settled amount (which would zero every delta) and not a "
                "refusal"
            )
            assert row.actual == Decimal("400.00")
            assert row.delta == Decimal("79.00")
            assert surprises.net == Decimal("79.00")

    def test_the_whole_report_renders_it_rather_than_500ing(
        self, app, db, seed_user, seed_periods,
    ):
        """Through ``compute_spending_report``, which is the door that 500'd.

        The sibling above pins the reducer; this pins the WIRING.  The
        production symptom was a 500 from ``/analytics/spending``, and a test
        that only calls ``_build_surprises`` directly proves the reducer can
        price a derived row while proving nothing about the basis being built
        and threaded in ``compute_spending_report`` -- which is the line the
        fix actually added.  Raised by an adversarial review of the fix.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "E2E Savings", Decimal("1000.00"),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
                amount=Decimal("321.00"), settled_amount=Decimal("400.00"),
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )

            assert report is not None
            leg_rows = [
                row for row in report.surprises.rows
                if row.delta == Decimal("79.00")
            ]
            assert len(leg_rows) == 1, (
                "the settled derived transfer's leg reaches the rendered "
                "report, and before the fix this call raised AmountUnresolvable"
            )
            assert leg_rows[0].estimated == Decimal("321.00")

    def test_a_template_priced_row_reads_the_version_for_its_own_due_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The rule that prices 59 of production's 78 affected rows.

        The leg above is priced by its parent (rule 5 priced its shadow, which
        went at balance:X-bi-6-4d-2).  Rule 3 -- a row of a recurring
        definition, priced by its dated series -- is a different path
        (``_rule_within_definition`` -> ``_stated_amount`` -> ``amount_as_of``)
        with refusal arms the transfer path never reaches, and it is the
        DOMINANT shape among the settled rows the cutovers declared.  An
        adversarial review raised it: the first test covered the minority.

        It asserts the series is read on the ROW'S OWN DUE DATE and not at its
        newest version: the definition is re-priced to ``$200.00`` effective
        after this row's due date, so a resolver reading the latest version
        would answer ``$200.00`` and make the delta ``-$50.00`` instead of
        ``+$25.00``.

        **The ``$200.00`` version is dated TOMORROW, not sixty days after the
        row, and plan step balance:X-cf-3 measured why.**  ``make_expense_template``
        states the ``$100.00`` price effective TODAY, so a version sixty days
        after a January 2026 row stopped being the newest the day the suite
        ran past March 2026, and a resolver mutated to read the newest
        version -- ``default_amount``, which ``set_amount`` re-synced onto it
        -- answered ``$100.00`` and passed.  Tomorrow is newest on every
        clock.  Red with that mutation, green without.
        """
        with app.app_context():
            period = seed_periods[0]
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Series Priced",
            )
            db.session.flush()
            state_template_price(
                template, Decimal("100.00"),
                effective_on=period.start_date - timedelta(days=30),
            )
            state_template_price(
                template, Decimal("200.00"),
                effective_on=display_today() + timedelta(days=1),
            )
            db.session.flush()
            assert newest_price(template) == Decimal("200.00"), (
                "the newest version must be the one the row's date does NOT "
                "select, or the mutation this case guards against passes"
            )

            # The engine's row, due on the paycheck's own start (what
            # ``compute_due_date`` derives for an every-paycheck definition),
            # then SETTLED on that day at $125.00 -- the record laid on bare,
            # through the one door a bare-built settlement goes through.
            txn = generate_row_of(template, period)
            due = txn.due_date
            assert due == period.start_date
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            for column, value in settle_day_columns(due).items():
                setattr(txn, column, value)
            db.session.flush()
            cover_bare_settled_row(db.session, txn, "125.00")
            db.session.commit()

            surprises = _build_surprises(
                [txn],
                derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id),
            )

            assert len(surprises.rows) == 1
            row = surprises.rows[0]
            assert row.estimated == Decimal("100.00"), (
                "the plan is the series version in effect on the row's OWN due "
                "date; reading the newest version would answer $200.00"
            )
            assert row.actual == Decimal("125.00")
            assert row.delta == Decimal("25.00")
