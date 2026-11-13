"""The settle verb, graded as a service, and the freshest figure a settle books."""

from app.utils.dates import display_today
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.services import status_seam, transaction_service
from app.services.transaction_service import _settle
from app.services.row_valuation import settled_contribution, settled_figure
from app.services.cash_ledger import amounts_by_key, derived_amount_basis
from tests._test_helpers import NO_TAX_LAW
from tests._test_helpers import (
    amount_basis_for,
    generate_row_of,
    repriced_by_the_owner,
    start_test_pay_list,
    typed,
)

from ._helpers import _make_entry, _make_template, _plan_of


class TestSettleTransactionTheVerb:
    """``settle_transaction`` -- ruling **R-FA**'s verb, graded as a SERVICE.

    **These exist because an adversarial review found the verb had no
    service-tier control at all.**  Every assertion about it reached it through
    ``POST /transactions/<id>/mark-done``, so its contract was graded only in
    the shape one HTTP door happens to call it in -- and the whole reason plan
    step X-f2-c2 extracted it is that a SECOND, non-HTTP caller arrives next
    leaf (the reconcile panel's tick) whose shape nothing pinned.
    """

    def test_a_soft_deleted_row_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """A settle cannot resurrect a deleted row -- on EITHER branch.

        **Finding N-233.**  The envelope branch refused this from the
        beginning, with its own docstring giving the reason, and the MANUAL
        branch never did -- so the refusal was a property of which branch a row
        happened to take rather than of the verb.  It was REACHABLE:
        ``get_accessible_transaction`` did not filter ``is_deleted`` until plan
        step ``credit_card:CC-5-4a-4`` (ruling **R-CC89**), so
        ``POST /transactions/<id>/mark-done`` on a soft-deleted non-envelope row
        flipped it into the settled band and stamped a settle day, while
        ``effective_amount`` valued it at ``Decimal("0")`` -- a row that reads
        Paid on every surface and is worth nothing on all of them.  Production
        carries 102 soft-deleted rows, every one of them Projected.

        Graded on the MANUAL branch specifically, because that is the half that
        had no guard.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            txn.is_deleted = True
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transaction_service.settle_transaction(txn)

            # The seam's own sentence, naming the row (ruling R-CC98).
            assert "was deleted: a payment cannot be recorded under it" in (
                str(exc.value)
            )
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert txn.settled_on is None

    def test_settle_amount_refuses_a_soft_deleted_row(
        self, app, seed_user, seed_periods,
    ):
        """The read refuses what the write refuses, for the same reason.

        A deleted row values at ``Decimal("0")`` through ``effective_amount``,
        so answering here would publish a figure ``settle_transaction`` will not
        book -- the argument ``reject_unsettleable``'s transfer-shadow half
        made while transfers had twin rows, applied to the rule that was
        missing beside it.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            txn.is_deleted = True
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transaction_service.settle_amount(txn, amount_basis_for(txn))

            # The seam's own sentence, naming the row (ruling R-CC98).
            assert "was deleted: a payment cannot be recorded under it" in (
                str(exc.value)
            )

    def test_an_envelope_with_entries_ignores_a_supplied_actual(
        self, app, seed_user, seed_periods,
    ):
        """sum(entries) wins over a caller-supplied actual, and posts.

        The precedence half of act 1: an envelope's entries ARE the record of
        what it cost, so a caller offering a different figure is offering an
        opinion about a derived value.  $40 + $50 against a $500 estimate and a
        supplied $999.99 settles at $90.00.
        """
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()
            _make_entry(txn.id, seed_user["user"].id,
                        Decimal("40.00"), "Walmart")
            _make_entry(txn.id, seed_user["user"].id,
                        Decimal("50.00"), "Sam's")
            db.session.flush()

            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("999.99")),
            )

            assert settled_figure(txn) == Decimal("90.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on == display_today()

    def test_a_plain_row_honours_a_supplied_actual(
        self, app, seed_user, seed_periods,
    ):
        """A row with no entries books what the caller supplied.

        The other half of act 1, and the channel ruling **R-FB** gives a first
        real caller: a bill's tick may correct its amount.  $250.00 supplied
        against a $500.00 estimate books $250.00.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()

            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("250.00")),
            )

            assert settled_figure(txn) == Decimal("250.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_a_plain_row_with_no_correction_records_what_it_booked(
        self, app, seed_user, seed_periods,
    ):
        """Nothing typed still RECORDS what moved -- the one-click path.

        **This asserted the opposite until plan step X-au-c3**: the figure
        column was left NULL, because it doubled as the "a human typed this"
        signal and writing the estimate into it would have manufactured a
        correction nobody made.  The two questions have two columns now, so a
        one-click settle records the figure it booked and says the basis is
        ``derived``.  The row's PLAN is untouched -- no settle writes a plan
        column -- and the figure it books is the same ``$500.00`` the old
        fall-back answered, so no balance moves.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("500.00")
            assert txn.estimated_amount is None
            assert _plan_of(txn) == Decimal("500.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_an_illegal_transition_raises_and_leaves_the_row_alone(
        self, app, seed_user, seed_periods,
    ):
        """Settling an already-Cancelled row is refused by the state machine.

        The verb's ``ValidationError`` surface, which the route renders as a
        designed 400.  Graded here rather than only through HTTP so the next
        caller knows what it must catch.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
            db.session.flush()

            with pytest.raises(ValidationError):
                transaction_service.settle_transaction(txn)

            assert txn.settled_on is None
            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)

    def test_income_takes_received_and_expense_takes_paid(
        self, app, seed_user, seed_periods,
    ):
        """``settled_status_id`` is the ONE spelling of the income/expense pick.

        It was written twice before plan step X-f2-c2 -- once in the mark-done
        route and once in ``settle_from_entries``, whose comment said it
        "mirrors" the route.  Both ends are graded here so a future third
        spelling has something to fail against.
        """
        with app.app_context():
            expense_tpl = _make_template(seed_user, is_envelope=False)
            expense = generate_row_of(expense_tpl, seed_periods[0])
            income_tpl = _make_template(
                seed_user, income=True, is_envelope=False,
            )
            income = generate_row_of(income_tpl, seed_periods[0])
            db.session.flush()

            assert transaction_service.settled_status_id(expense) == (
                ref_cache.status_id(StatusEnum.DONE)
            )
            assert transaction_service.settled_status_id(income) == (
                ref_cache.status_id(StatusEnum.RECEIVED)
            )

            transaction_service.settle_transaction(expense)
            transaction_service.settle_transaction(income)
            assert expense.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert income.status_id == ref_cache.status_id(StatusEnum.RECEIVED)


class TestASettleBooksTheFreshestFigure:
    """Plan step **X-aq**, ruling **R-FE**, finding **N-224**.

    **The defect these grade is two answers to one question.**
    ``transactions.estimated_amount`` was a CACHE of a derivation that a
    read-time repair recomputed without writing back -- so every balance
    surface showed the live figure while every settle door booked the stored
    one, and settling moved the projected end balance by the difference.  That
    is the exact invariant ruling R-DH (c) states and plan step X-f3 is
    ship-gated on.

    **Plan step X-au-d closed it at the source rather than at the settle**, and
    these cases are what that looks like from here.  A salary row DECLARES the
    definition that prices it and stores no figure, so there is no cache to
    book instead of and none to reconcile: what the settle books is what the
    amount model answers, and its RECORD (the covering movement, on the
    ``resolved`` source) is the only figure the row ends up holding.  A test that passed by
    reading the plan column would now have to report ``None``.

    The setup is the sibling suite's, so the expected net is not invented here:
    a ``$104,000`` profile over 26 periods with no tax configs seeded nets
    ``$4,000.00`` a period, which
    ``test_income_service.test_the_salary_net.TestSalaryNetFor``'s
    ``test_recomputes_live_ignoring_stored_amount`` pins independently.
    """

    @staticmethod
    def _salary_row(seed_user, period, *, repriced_to=None):
        """Return a Projected income row whose template IS a salary profile.

        The engine's own row (:func:`generate_row_of`, plan step
        balance:X-cf-3b): a paycheck nobody has re-priced DECLARES its
        definition and stores nothing, which is the shape the app produces
        after plan step X-au-d.  **The row's OWNERSHIP follows its override
        flag**: pass *repriced_to* for one a human re-priced, and it takes
        ownership the way the edit door does (:func:`repriced_by_the_owner`).
        Building the two acts independently would let a test assert against
        a state ``ck_transactions_amount_ownership`` and the write doors
        between them cannot produce.

        The definition is linked to the profile AFTER the shared builder
        states its price, so it holds a one-version series nothing reads:
        once linked, ``owns_its_amount`` is False and the row is priced by
        rule 2 whatever that series says (production's salary template holds
        no series at all; the difference is unread either way).
        """
        # Pylint: ``import-outside-toplevel`` -- the salary models are not
        # part of this module's subject and importing them at the top would put
        # the paycheck stack on every transaction-service test's load path.
        # pylint: disable=import-outside-toplevel
        from app.models.ref import FilingStatus
        from app.models.salary_profile import SalaryProfile

        filing = db.session.query(FilingStatus).first()
        profile = SalaryProfile(
            period_end_days_before_payday=6,
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="X-aq Salary",
            state_code="NC",
            is_active=True,
        )
        db.session.add(profile)
        start_test_pay_list(profile, Decimal("4000.00"))  # $104,000.00 a year / 26
        db.session.flush()

        template = _make_template(
            seed_user, income=True, is_envelope=False,
        )
        # The RELATIONSHIP rather than the key, so ``template.salary_profiles``
        # -- which the shared builder's ``set_amount`` already loaded as empty
        # -- is current in this session without waiting for a commit.
        profile.template = template
        db.session.flush()

        txn = generate_row_of(template, period)
        if repriced_to is not None:
            repriced_by_the_owner(txn, repriced_to)
        return txn

    def test_a_declared_paycheck_settles_at_what_its_PROFILE_pays(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """The row books ``$4,000.00`` and its plan column stays empty.

        The headline of X-aq, restated at X-au-d.  Before X-aq this settle
        booked a stale ``$1.00`` while the grid cell beside it read
        ``$4,000.00`` -- ``$3,999.00`` of income deleted from the projection by
        pressing Mark Paid.  X-aq made the settle ask the same producer the
        grid asked; X-au-d deleted the stale figure, so there is no second
        answer left for either of them to prefer.

        **The settle writes NO plan column**, which is what the deleted
        reconciler used to do here.  A derived row has no cache to reconcile,
        so the only figure this act writes is the RECORD -- and the record says
        WHO wrote it (the movement's ``figure_source_id``), which is what keeps
        a machine's resolution distinguishable from a human's correction.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            txn = self._salary_row(seed_user, seed_periods[0])
            db.session.commit()
            assert txn.estimated_amount is None

            transaction_service.settle_transaction(txn)

            assert txn.estimated_amount is None, (
                "the settle must not hand a derived row back to its owner: "
                "that is finding N-437, and the plan stays the definition's"
            )
            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("4000.00")
            assert settled_contribution(txn) == Decimal("4000.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.RECEIVED)

    def test_a_supplied_actual_still_wins_over_the_live_figure(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A figure a human typed beats every derivation.

        The precedence half of act 1 and the reason X-f2-c2's correctable box
        (ruling **R-FB**) is safe: the panel's prefilled amount is what the
        statement says, and a live recompute must not overwrite it.

        **The two facts stay SEPARABLE**, which is what the record's own basis
        column buys: the human's ``$3,912.44`` is what the row RECORDS, and the
        machine's ``$4,000.00`` is what its PLAN still resolves to -- from the
        definition, not from a column, so the plan is not something this settle
        wrote.  Under the shipped-then-withdrawn single-column version the
        resolution was invisible here, so nothing could tell a stale projection
        from an accurate one after the fact.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            txn = self._salary_row(seed_user, seed_periods[0])
            db.session.commit()

            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("3912.44")),
            )

            assert settled_figure(txn) == Decimal("3912.44")
            assert txn.estimated_amount is None
            assert settled_contribution(txn) == Decimal("3912.44")
            # The PLAN, asserted rather than described: it still resolves from
            # the definition, and it is a different number from the record.
            # An adversarial review of this step found the docstring claiming
            # this and nothing grading it -- ``estimated_amount is None`` says
            # the column is empty, not that the derivation still answers.
            assert amounts_by_key(
                [txn], derived_amount_basis(txn.account.user_id, txn.scenario_id),
            )[txn.id] == Decimal("4000.00")

    def test_an_overridden_row_is_not_re_derived(
        self, app, db, seed_user, seed_periods,
    ):
        """A paycheck a human re-priced settles at the figure they typed.

        **The REASON changed at plan step X-au-d** and the outcome did not.  It
        used to hold because the read-time repair dropped an ``is_override``
        row; it holds now because such a row OWNS its figure, so amount rule 1
        answers it and no salary producer is consulted at all (finding
        **N-262**).  The flag is set here because that is what the edit door
        does beside taking ownership, not because anything reads it to price.
        Owned ``$1,234.56`` settles at ``$1,234.56``.
        """
        with app.app_context():
            txn = self._salary_row(
                seed_user, seed_periods[0], repriced_to="1234.56",
            )
            db.session.commit()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("1234.56")
            assert settled_contribution(txn) == Decimal("1234.56")

    # ``test_an_agreeing_live_figure_leaves_the_column_null`` lived here until
    # plan step X-au-d, and its SUBJECT is deleted rather than the case being
    # weakened.  It graded the cache reconciler's own suppression rule -- that
    # a refresh writes nothing when the live figure already equals the stored
    # one -- so that ``actual_amount``'s NULL-ness stayed readable as "a human
    # typed this".  There is no reconciler and no cache: a derived row's plan
    # column is empty before and after every settle, which
    # ``test_a_declared_paycheck_settles_at_what_its_PROFILE_pays`` asserts
    # directly.  The signal that survived is the movement's
    # ``figure_source_id``, and the ``resolved``-versus-``typed`` pair above
    # is its control.

    def test_a_row_with_no_live_seam_is_untouched(
        self, app, db, seed_user, seed_periods,
    ):
        """An ordinary bill is not a candidate, so X-aq writes nothing.

        The blast-radius control.  ``live_projected_net`` wants an income row
        linked to an active profile and ``live_loan_transfer_amounts`` wants a
        transfer shadow, so an expense on a plain template matches neither and
        the settle is byte-identical to its pre-X-aq behaviour.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()

            transaction_service.settle_transaction(txn)

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("500.00")
            assert settled_contribution(txn) == Decimal("500.00")

    def test_an_envelope_with_entries_still_settles_at_its_entries(
        self, app, db, seed_user, seed_periods,
    ):
        """The envelope branch is untouched: entries beat every derivation.

        Act 1's ordering, re-graded from the X-aq side.  A row that is BOTH
        envelope-tracked with entries AND income keeps ``sum(entries)`` -- the
        freshest answer for an envelope is its own record of what it cost, not
        a recompute of what it was expected to be.
        """
        with app.app_context():
            template = _make_template(seed_user, income=True)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()
            _make_entry(txn.id, seed_user["user"].id,
                        Decimal("12.34"), "Refund")
            db.session.commit()

            transaction_service.settle_transaction(txn)

            assert settled_figure(txn) == Decimal("12.34")

    # ``test_a_hand_typed_actual_is_NEVER_overwritten`` lived here until plan
    # step X-au-c3, and its state is unconstructible now rather than merely
    # untested.  It built a PROJECTED salary row carrying
    # ``actual_amount = 3880.15`` and proved a fourth guard on the settle's
    # cache refresh kept it from booking ``$4,000.00`` over it.  A figure
    # RECORDS a settle now (the covering movement the seam writes, which
    # ``reject_settlement_without_settled_status`` keeps off a row whose money
    # has not moved) -- and the door that
    # produced the state is gone with it: the full-edit form's Actual box was
    # deleted, so the only way a human's pre-settle figure reaches a row is
    # ``estimated_amount``, and stating one there is what TAKES OWNERSHIP of
    # the row (``amount_ownership.state_own_amount``, plan step X-au-d).  That
    # ownership is why no derived rule prices such a row, and
    # ``test_an_overridden_row_is_not_re_derived`` above is the control on it.
    # The refresh and its guards are all deleted with the cache they kept
    # true.

    def test_a_supplied_figure_equal_to_the_booked_one_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The echo rule applies to a CALLER's figure too, not just a derived one.

        It was two rules before an adversarial review: the verb suppressed an
        echo of the LIVE figure and the reconcile writer separately suppressed
        an echo of the SUBMITTED one, so the same column meant different things
        depending on which door wrote it.  Plan step X-ap routes a form here
        that submits ``actual_amount`` on EVERY save, so the caller-supplied
        half is the half that matters next.

        `$500.00` supplied against a `$500.00` estimate leaves the column NULL.
        """
        with app.app_context():
            template = _make_template(seed_user, is_envelope=False)
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()

            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("500.00")),
            )

            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("500.00")
            assert settled_contribution(txn) == Decimal("500.00")

    def test_the_figure_is_resolved_BEFORE_the_status_flip(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """Order is load-bearing, and this is the control that says so.

        A row's valuation answers from its SETTLEMENT RECORD once it has
        settled (``row_valuation.fixed_contribution``), so a verb that resolved
        AFTER ``apply_status_change`` would be asking about the very record
        that call had just written -- circular, and it would book whatever the
        seam happened to store.  Grading it as an ORDER rather than as an
        outcome: the row is Projected at the moment the resolution is made.

        **The reason survived plan step X-au-d and its mechanism did not.**  It
        used to be that the read-time repair filtered to Projected rows, so
        asking late always answered "nothing fresher" and the settle silently
        booked the cache.  There is no repair and no cache; what makes the
        order load-bearing now is the settlement record itself.

        **The LIST is the control on the one-resolution rule too** (developer
        ruling, 2026-08-17).  It is asserted as a whole rather than by
        membership, so a settle that resolved the amount twice -- which is what
        the booked figure and the echo comparison each doing their own would
        produce -- fails here with ``[2, 2] != [2]``.  That is the measurement,
        not a claim about it.

        **The spy is installed on the LEAF that owns the name**, not on the
        package that re-exports the verb, and the difference is not cosmetic:
        ``settle_transaction`` resolves ``settle_amount`` as a global of its
        own module, so patching a package attribute would intercept nothing and
        this control would pass while grading nothing.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            txn = self._salary_row(seed_user, seed_periods[0])
            db.session.commit()

            seen_status = []
            real = _settle.settle_amount

            def _spy(row, basis):
                seen_status.append(row.status_id)
                return real(row, basis)

            _settle.settle_amount = _spy
            try:
                transaction_service.settle_transaction(txn)
            finally:
                _settle.settle_amount = real

            assert seen_status == [
                ref_cache.status_id(StatusEnum.PROJECTED),
            ]
            assert txn.estimated_amount is None
            # **The settle RECORDS what it booked, and says HOW that figure is
            # known** (plan step X-au-c3).  This asserted ``actual_amount is
            # None`` until that step, because a NULL there was the only signal
            # that no human had typed a figure -- so a settle with nothing to
            # correct recorded nothing at all, and every reader fell back to
            # the row's PLAN.  The signal is the movement's ``figure_source_id``
            # now (a column of the row's own from X-au-c3 to X-bi-4b-2), so the
            # record can state the figure AND stay distinguishable from a
            # correction.
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(txn) == Decimal("4000.00")
