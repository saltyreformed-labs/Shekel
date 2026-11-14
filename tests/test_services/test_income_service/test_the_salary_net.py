"""What a salary row nets: salary_net_for, and the balance surfaces that reach its answer."""

from decimal import Decimal

from app.services.cash_flow_set import CashFlowSet
from app.models.ref import TransactionType
from app.models.transaction_template import TransactionTemplate
from app.services import balance_at, income_service, paycheck_calculator
from tests._tax_configs import tax_configs_for
from app.services.balance_at import BalanceContext
from tests._test_helpers import NO_TAX_LAW, all_periods, make_every_period_rule, payroll_basis

from ._helpers import _create_profile, _make_salary_template, _make_txn, _net_map, _derived


class TestSalaryNetFor:
    """Unit tests for ``income_service.salary_net_for`` -- amount rule 2's body.

    Locks the two properties the amount model relies on: the producer
    (a) answers what the salary PROFILE pays for the row's own period, and
    (b) answers for a row it can PRICE, which is a question about the
    definition and never about whether the row still counts.

    **Half (b) is the inversion plan step X-au-d completed.**  This class
    graded a read-time repair, whose candidate set was "Projected,
    non-overridden, salary-linked income" -- so a Cancelled or hand-priced
    paycheck was refused for reasons that have nothing to do with what a
    paycheck is worth.  Pricing asks the definition; whether a row still counts
    is finding **N-262**'s separate question, answered above this rule by
    ``row_valuation.fixed_contribution``, and WHO owns the figure is answered
    by the row's declaration rather than by ``is_override``.
    """

    def test_recomputes_live_ignoring_stored_amount(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A salary-linked income row maps to what its PROFILE pays.

        The row is the engine's, RE-PRICED by its owner to a deliberately
        wrong ``$1.00`` (the re-price door's two acts) so the two answers
        stay distinguishable at this tier: a producer reading the column
        would answer ``$1.00`` and the profile answers ``$4,000.00``.  The
        DECLARED shape -- where the column is empty and the distinction is
        structural rather than measured -- is graded one tier up, by
        ``test_amount_source`` over the rule this producer is the body of.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            period = all_periods(user_id)[5]
            txn = _make_txn(
                seed_user, period, template=template, owned_amount="1.00",
            )
            db.session.commit()

            overrides = _net_map(user_id, scenario_id, [txn])

            # $104,000 profile, no raise, no tax configs seeded -> net =
            # gross = 104000 / 26 = $4,000.00 (hand-computed; the sibling
            # balance-resolver test pins the same value for this setup).
            # The producer must return this LIVE net, never the stale $1.00.
            expected_net = Decimal("4000.00")
            assert overrides == {txn.id: expected_net}
            assert overrides[txn.id] != Decimal("1.00")

    def test_a_period_this_owners_calendar_does_NOT_hold_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A ``pay_period_id`` off this owner's calendar answers ``None``.

        **Amount rule 2's SECOND refusal, and plan step salary:S3-d rewrote
        how it is reached.**  It was a miss in a ``{period_id: breakdown}``
        map built from this owner's saved window; it is
        :meth:`~app.services.pay_calendar.PayCalendar.period_by_id` returning
        ``None`` now.  Both refuse, and the second is scoped because the
        calendar is built from the owner's id -- but "the map was the owner's"
        and "the lookup is scoped" are different guarantees, and swapping the
        second for an unscoped lookup would leave every other case in this
        file passing.

        That is the shape where a moved door disarms the control in front of
        it, so the refusal is asserted directly rather than inferred from the
        producer that happens to sit behind it today.  An adversarial review
        of S3-d found this branch untested.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            _make_salary_template(seed_user, profile)
            db.session.commit()

            foreign_period_id = all_periods(seed_second_user["user"].id)[0].id
            own_period_ids = {p.period_id for p in _derived(user_id)}
            assert foreign_period_id not in own_period_ids, (
                "the two fixtures share a pay-period id, so this case would "
                "grade nothing"
            )

            pricing = income_service.salary_pricing(
                scenario_id, income_service.derived_paycheck_pricing(user_id),
            )
            assert pricing.net_for(
                profile.template_id, foreign_period_id,
            ) is None

    def test_prices_by_the_DEFINITION_and_never_by_the_rows_status(
        self, app, db, seed_user, seed_periods,
    ):
        """What a paycheck is WORTH does not depend on whether it still counts.

        **This case asserted the opposite until plan step X-au-d**, and the
        inversion is the step.  It graded a read-time repair whose candidate
        set was "Projected, non-overridden, salary-linked income", so it
        asserted that a RECEIVED paycheck and an OVERRIDDEN one were omitted.
        Those two omissions were the repair's status gate, not a pricing rule
        (finding **N-262**): a Received paycheck is worth exactly what the
        profile paid for its period, and whether the app should USE that figure
        is a different question answered above this producer.

        Five rows, and the split is now by DEFINITION rather than by status:
        the three on the salary template are all priced -- Projected, Received
        and overridden alike -- while non-salary income (a template no profile
        names) and an expense are not, because no salary profile states what
        they are worth.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            category = next(iter(seed_user["categories"].values()))
            other_template = TransactionTemplate(
                user_id=user_id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Non-salary income",
            )
            db.session.add(other_template)
            db.session.flush()
            # The cadence the engine needs to write its row; no price, because
            # nothing here prices this row and an empty series is what refuses
            # a producer that tried.
            make_every_period_rule(db.session, other_template)
            db.session.commit()

            periods = all_periods(user_id)
            # Distinct periods: an every-paycheck definition names ONE
            # occurrence per paycheck, and the engine writes each once.
            wanted = _make_txn(seed_user, periods[5], template=template)
            received = _make_txn(
                seed_user, periods[6], template=template,
                status_name="Received",
            )
            overridden = _make_txn(
                seed_user, periods[7], template=template, owned_amount="1.00",
            )
            non_salary = _make_txn(
                seed_user, periods[5], template=other_template,
            )
            expense = _make_txn(
                seed_user, periods[5], template=None, type_name="Expense",
                owned_amount="1.00",
            )
            db.session.commit()

            priced = _net_map(
                user_id, scenario_id,
                [wanted, received, overridden, non_salary, expense],
            )

            assert set(priced) == {wanted.id, received.id, overridden.id}, (
                "every row on the salary template is PRICED by the profile "
                "whatever its status or override flag, and no other row is; "
                f"got ids {sorted(priced)}"
            )
            assert priced[received.id] == priced[wanted.id], (
                "a Received paycheck is worth what the profile paid for its "
                "period; refusing it here would be the repair's status gate "
                "re-added inside a pricing rule (finding N-262)"
            )

    def test_empty_when_no_salary_definition_prices_the_row(
        self, app, db, seed_user, seed_periods,
    ):
        """A row no salary profile's template names has no answer here."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            # Empty transaction list.
            assert _net_map(user_id, scenario_id, []) == {}

            # An income row whose template has no SalaryProfile -> omitted.
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            category = next(iter(seed_user["categories"].values()))
            unlinked = TransactionTemplate(
                user_id=user_id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Unlinked income",
            )
            db.session.add(unlinked)
            db.session.flush()
            make_every_period_rule(db.session, unlinked)
            db.session.commit()
            txn = _make_txn(
                seed_user, all_periods(user_id)[3],
                template=unlinked,
            )
            db.session.commit()
            assert _net_map(user_id, scenario_id, [txn]) == {}


class TestLiveIncomeThroughBalanceResolver:
    """Balance surfaces price a projected salary row from its PROFILE.

    The drift-without-regeneration lock -- the exact failure mode (a profile,
    calibration or code change staling the grid) that motivated the income
    resolver.  **Plan step X-au-d changed what makes it hold**: the row no
    longer stores a figure a repair has to supersede, it DECLARES the
    definition that prices it, so there is one answer rather than a preferred
    one.  The class stays because the surfaces still have to reach that answer.
    """

    def test_a_declared_salary_row_reaches_the_grid_and_the_BALANCE(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A declared salary income row contributes its profile's net to both.

        $104,000 profile, no deductions, no tax configs seeded -> net = gross =
        104000/26 = $4,000.00.  The row stores NOTHING, so a surface that read
        the column would contribute ``None`` rather than a stale figure -- the
        substitution this cutover makes unrepresentable instead of unlikely.

        The income row was read through ``cash_ledger.period_subtotal`` until
        plan step X-c2b3 deleted it; it is now the shipped
        ``GridColumn.income``, which is what the grid footer renders.  The
        $4,000.00 is unchanged across both that step and this one, because the
        rule is the same on both bases -- which is the property this test
        exists to pin.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            periods = all_periods(user_id)
            period = periods[5]
            row = _make_txn(seed_user, period, template=template)
            db.session.commit()
            assert row.estimated_amount is None

            tax_configs = tax_configs_for(
                profile, period.start_date.year,
            )
            breakdowns = paycheck_calculator.project_salary(
                payroll_basis(profile, _derived(user_id)), _derived(user_id),
                tax_configs,
            )
            expected_net = {
                bd.period.period_id: bd.earnings.net_pay for bd in breakdowns
            }[period.id]
            # Sanity: the profile's answer is the hand-computed figure, and
            # it is not something the row could have been read off.
            assert expected_net == Decimal("4000.00")

            # The grid's income row reflects the live net.
            column = balance_at.grid_balance_view(
                CashFlowSet.single(account), bctx,
            ).columns[period.id]
            assert column.income == expected_net, (
                f"GridColumn.income should be the profile's {expected_net}, "
                f"got {column.income} (the row stores no figure at all)"
            )

            # The BALANCE moves by that net too, not just the rendered income
            # row -- the property that makes the derivation a basis rather than
            # a display value.  Re-pointed off the deleted anchor-forward walk
            # onto the cash view at plan step X-g4b; the delta is unchanged
            # because both fold one amount model.
            result = balance_at.cash_balance_map(
                account, bctx,
            )
            idx = next(i for i, p in enumerate(periods) if p.id == period.id)
            prior = result[periods[idx - 1].id]
            assert result[period.id] - prior == expected_net, (
                "the income period's balance delta should be the live net "
                f"{expected_net}, got {result[period.id] - prior}"
            )

    def test_overridden_income_row_keeps_user_value(
        self, app, db, seed_user, seed_periods,
    ):
        """A salary income row that OWNS its figure is not recomputed.

        The non-vacuity partner for the case above, and the one whose REASON
        changed at plan step X-au-d.  It used to hold because a read-time
        repair excluded ``is_override`` rows; it holds now because the row
        DECLARES no definition -- ``amount_ownership`` is what decides, so the
        figure a human typed is answered by amount rule 1 and no salary
        producer is consulted at all (finding **N-262**).  The flag is set here
        because that is what the edit doors do beside taking ownership, not
        because pricing reads it.  Read through ``GridColumn.income`` since
        plan step X-c2b3 deleted ``cash_ledger.period_subtotal``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            period = all_periods(user_id)[5]
            _make_txn(
                seed_user, period, template=template, owned_amount="1234.56",
            )
            db.session.commit()

            column = balance_at.grid_balance_view(
                CashFlowSet.single(account), bctx,
            ).columns[period.id]
            assert column.income == Decimal("1234.56"), (
                "a row that OWNS its figure must keep the user's amount, "
                f"got {column.income}"
            )
