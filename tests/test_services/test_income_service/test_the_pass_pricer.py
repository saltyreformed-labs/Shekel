"""The amount model reads the pass's pricer, and a pricer is keyed on its raise set."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import RaiseTypeEnum
from app.extensions import db
from app.models.ref import RaiseType
from app.models.salary_raise import SalaryRaise
from app.services.pay_calendar import calendar_for, days_paid_for
from app.services.salary_raises import RaiseTerms, terms_of
from app.services import income_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import NO_TAX_LAW, all_periods, counting_calls, pricing_over

from ._helpers import _create_profile, _make_salary_template, _make_txn


# ── salary:C12: the amount model reads the pass's pricer ────────────


class TestTheAmountModelReadsThePassPricer:
    """``SalaryPricing`` takes a pricer and reads the owner off it (ledger P63).

    **Plan step salary:C12.**  The amount basis was built from an owner and a
    scenario and its salary derivation derived a pricer of its own over a
    calendar of its own; it is built OVER a pricer now.  Three properties are
    graded here, one per case: the pass's basis prices a row through the
    pass's own memo, so the same payday is priced ONCE across the two paths;
    the owner is the pricer's, so a scenario handed a pricer of another
    owner prices nothing rather than that owner's rows; and the DERIVED
    interim a pass-less producer builds derives NOTHING until a paycheck is
    asked for -- which is what keeps a settle of a non-salary row free and
    an owner with no schedule row un-refused, the two properties the old
    ``SalaryPricing._pricing`` had and this step keeps.
    """

    def test_the_basis_prices_a_row_through_the_pass_pricer(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A payday the pass's pricer already priced is NOT priced again.

        The pass prices the profile's paycheck for one period (as a payroll
        feed would), then the basis built over that pricer answers a salary
        row in the SAME period: zero further engine runs, because both read
        one memo.  On the tree before C12 the basis's own pricer ran the
        engine once more here.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            period = all_periods(user_id)[5]
            txn = _make_txn(seed_user, period, template=template)
            db.session.commit()

            ctx = BalanceContext.build(user_id)
            derived = next(
                p for p in ctx.calendar().saved() if p.period_id == period.id
            )
            priced_by_pass = ctx.paychecks().for_profile(profile).at(derived)

            with counting_calls(
                ("app.services.paycheck_calculator", "calculate_paycheck"),
            ) as counts:
                net = income_service.salary_net_for(txn, ctx.amounts().salary)

            assert net == priced_by_pass.earnings.net_pay == Decimal("4000.00")
            assert counts["calculate_paycheck"] == 0, (
                "the basis priced a payday the pass's pricer had already "
                "priced; it is reading a second pricer"
            )

    def test_the_owner_is_the_pricers(
        self, app, db, seed_user, seed_second_user, seed_periods, tax_law,
    ):
        """A basis over another owner's pricer prices THIS owner's rows never.

        The owner used to arrive as a second argument beside the scenario,
        with nothing checking the two agreed; it is read off the pricer now,
        so the only way to price owner A's rows is to hold owner A's pricer.
        Handed owner B's, the profile lookup is scoped to B and finds no
        profile naming A's template, so the row is refused (``None``) rather
        than priced off B's calendar.  **B's calendar is asserted NOT
        derived**, because the refusal alone cannot tell the two apart: a
        lookup that ignored the owner would find A's profile (the scenario is
        A's), derive B's calendar, fail to place A's period in it and answer
        ``None`` too -- the same answer by the wrong door, one calendar
        derivation later.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            txn = _make_txn(
                seed_user, all_periods(user_id)[5], template=template,
            )
            db.session.commit()

            own = income_service.salary_pricing(
                scenario_id, income_service.derived_paycheck_pricing(user_id),
            )
            foreign = income_service.salary_pricing(
                scenario_id,
                income_service.derived_paycheck_pricing(
                    seed_second_user["user"].id,
                ),
            )
            assert own.net_for(template.id, txn.pay_period_id) == Decimal("4000.00")
            with counting_calls(
                ("app.services.pay_calendar._derive", "derive_periods"),
            ) as counts:
                assert foreign.net_for(template.id, txn.pay_period_id) is None
            assert counts["derive_periods"] == 0, (
                "the foreign pricer's calendar was derived: the profile "
                "lookup is not scoped to the pricer's owner"
            )

    def test_the_pass_pricer_and_its_basis_derive_nothing_until_a_paycheck_is_asked(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """Building ``ctx.paychecks()`` and ``ctx.amounts()`` derives NO calendar.

        The pass hands its calendar MEMO as the pricer's source, so a pass
        that folds no cash and prices no paycheck -- the loan arm, the
        bank-agreement fragment, both of which reach ``amounts()`` and read no
        calendar anywhere else -- pays for none and cannot be refused one.  An
        adversarial review of this step measured the first build deriving it
        at ``amounts()``, which put a ``PayCalendarError`` under two ``Raises``
        contracts that promise none; this is the case that keeps it out.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            txn = _make_txn(
                seed_user, all_periods(user_id)[5], template=template,
            )
            db.session.commit()

            with counting_calls(
                ("app.services.pay_calendar._derive", "derive_periods"),
            ) as counts:
                ctx = BalanceContext.build(user_id)
                paychecks = ctx.paychecks()
                basis = ctx.amounts()
                assert paychecks.user_id == user_id
                assert counts["derive_periods"] == 0, (
                    "building the pass's pricer or its basis derived the "
                    "calendar before any paycheck was asked for"
                )
                assert income_service.salary_net_for(txn, basis.salary) == (
                    Decimal("4000.00")
                )
                assert counts["derive_periods"] == 1
                assert paychecks.calendar is ctx.calendar(), (
                    "the pricer's calendar is not the pass's memo"
                )
                assert counts["derive_periods"] == 1

    def test_the_derived_pricer_derives_nothing_until_a_paycheck_is_asked(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """A pass-less basis over a non-salary row derives NO calendar.

        The interim's whole reason: the twelve pass-less producers price
        arbitrary rows, most of them not paychecks, and the calendar costs
        two queries and REFUSES an owner with no schedule row.  So the
        derived pricer answers ``user_id`` without deriving, the profile
        lookup runs against it, and a template no profile names stops there.
        Then the first real paycheck derives it, exactly once.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            period = all_periods(user_id)[5]
            txn = _make_txn(seed_user, period, template=template)
            db.session.commit()

            with counting_calls(
                ("app.services.pay_calendar._derive", "derive_periods"),
            ) as counts:
                paychecks = income_service.derived_paycheck_pricing(user_id)
                pricing = income_service.salary_pricing(scenario_id, paychecks)
                assert paychecks.user_id == user_id
                assert pricing.net_for(template.id + 1000, period.id) is None
                assert counts["derive_periods"] == 0, (
                    "a template no profile names derived the calendar"
                )
                assert pricing.net_for(template.id, txn.pay_period_id) == (
                    Decimal("4000.00")
                )
                assert counts["derive_periods"] == 1
                pricing.net_for(template.id, txn.pay_period_id)
                assert counts["derive_periods"] == 1, (
                    "the derived pricer derived its calendar twice"
                )


# ── salary:S3-f-1: the pricer is keyed on the raise set ─────────────


def _believed_through(row: SalaryRaise, terminal_year) -> RaiseTerms:
    """The row's terms, believed through *terminal_year* instead.

    The shape a what-if hands the engine (plan step salary:S3-f): the
    production value with ONE term changed.
    """
    return replace(RaiseTerms.of(row), terminal_year=terminal_year)


class TestThePricerIsKeyedOnTheRaiseSet:
    """One pricer per profile PER RAISE SET, and the rows are one set.

    Plan step **salary:S3-f-1** (ruling **R-SAL20**).
    :meth:`~app.services.income_service.PaycheckPricing.for_profile` keyed on
    the profile's id alone, and :class:`ProfilePaychecks` memoizes by payday
    alone -- so a second raise set for one profile had nowhere to go but the
    stored set's memo.  The key carries the terms now; ``None`` spells the
    rows, which keeps every stored-plan render on the one pricer it always
    built.
    """

    @staticmethod
    def _profile_with_a_forever_raise(seed_user):
        """``$104,000`` with a recurring 5% March raise from 2026, no end."""
        profile = _create_profile(
            seed_user["user"].id, seed_user["scenario"].id,
        )
        merit = db.session.query(RaiseType).filter_by(name="merit").one()
        row = SalaryRaise(
            salary_profile_id=profile.id, raise_type_id=merit.id,
            effective_date=date(2026, 3, 1),
            percentage=Decimal("0.0500"), is_recurring=True,
            terminal_year=None,
        )
        db.session.add(row)
        db.session.commit()
        db.session.refresh(profile)
        return profile, row

    @staticmethod
    def _payday_in(calendar, year):
        """A projected payday in *year*, off the calendar's own rhythm."""
        axis = calendar.axis(calendar.opening_bound(), date(year, 12, 31))
        return next(p for p in axis if p.start_date.year == year
                    and p.start_date.month >= 6)

    def test_every_spelling_of_the_stored_set_is_ONE_pricer(
        self, app, db, seed_user, seed_periods,
    ):
        """``None``, the rows, and values carrying the rows' terms: one key.

        The finding an adversarial review of this step made: keyed on the
        caller's objects, the stored set had three spellings and each built a
        pricer of its own -- the two-derivations-of-one-figure shape
        ``PlanPoint`` refuses one tier up, and one the pricer-count gate
        cannot see on a probe request, where a second pricer is also the
        legitimate outcome.  The fourth spelling is the one S3-f-2b's rail
        actually sends on every refresh: a probe carrying the stored end year
        unchanged.
        """
        with app.app_context():
            profile, row = self._profile_with_a_forever_raise(seed_user)
            paychecks = pricing_over(
                calendar_for(profile.user_id),
            )
            stored = paychecks.for_profile(profile)

            assert paychecks.for_profile(profile, tuple(profile.raises)) is stored
            assert paychecks.for_profile(profile, terms_of(profile.raises)) is stored
            assert paychecks.for_profile(
                profile, (_believed_through(row, row.terminal_year),),
            ) is stored, (
                "a probe carrying the STORED end year built a second pricer "
                "for the stored set"
            )

    def test_the_rows_are_one_key_and_a_raise_set_is_another(
        self, app, db, seed_user, seed_periods,
    ):
        """Same terms, same pricer; the rows' pricer is untouched by them."""
        with app.app_context():
            profile, row = self._profile_with_a_forever_raise(seed_user)
            paychecks = pricing_over(
                calendar_for(profile.user_id),
            )
            stored = paychecks.for_profile(profile)
            believed = paychecks.for_profile(
                profile, (_believed_through(row, 2026),),
            )

            assert paychecks.for_profile(profile) is stored
            assert believed is not stored, (
                "a raise set other than the rows was served the rows' pricer"
            )
            # An EQUAL tuple built again is the same key: a probe repeated
            # at one raise set must not build a second pricer.
            assert paychecks.for_profile(
                profile, (_believed_through(row, 2026),),
            ) is believed
            assert paychecks.for_profile(profile) is stored

    def test_a_pricer_under_terms_prices_off_them(
        self, app, db, seed_user, seed_periods,
    ):
        """The paychecks a keyed pricer answers are the terms', end to end.

        A June 2028 payday: three applications of 5% on the ``$4,000.00``
        entry under the rows (``4,000.00 x 1.05 = 4,200.00``, ``x 1.05 =
        4,410.00``, ``x 1.05 = $4,630.50``), one under terms believed through
        2026 (``4,000.00 x 1.05 = $4,200.00``).
        """
        with app.app_context():
            profile, row = self._profile_with_a_forever_raise(seed_user)
            calendar = calendar_for(profile.user_id)
            paychecks = pricing_over(calendar)
            june_2028 = self._payday_in(calendar, 2028)

            assert paychecks.for_profile(profile).at(
                june_2028,
            ).earnings.gross_biweekly == Decimal("4630.50")
            assert paychecks.for_profile(
                profile, (_believed_through(row, 2026),),
            ).at(june_2028).earnings.gross_biweekly == Decimal("4200.00"), (
                "the pricer keyed on the terms priced the profile's rows"
            )

    def test_a_never_flushed_raise_is_priced_from_its_FK(
        self, app, db, seed_user, seed_periods,
    ):
        """The X-bl invariance control's row: appended, never flushed, priced.

        ``tests/manual/verify_amount_resolver.py`` appends a ``SalaryRaise``
        with only its ``raise_type_id`` set to a loaded profile under
        ``no_autoflush`` and prices every salary row through the pricer.
        SQLAlchemy does not lazy-load a relationship on a pending instance,
        so ``row.raise_type`` is ``None`` there; the type's name comes off the
        FK through the ref cache instead, and the paycheck moves by the
        raise.  A second adversarial review of S3-f-1 found the relationship
        read raising ``AttributeError`` on exactly this row.

        **The raise is effective February and the paycheck priced is the
        first one paid wholly in February** since task plan#3100.  The pay
        entry is recorded on the first payday, 2026-01-02, and holds every
        raise dated by the day its paycheck opens, 2025-12-14 (ruling
        plan#3091), so the February raise is after it.  The case priced
        February's first paycheck, 02-13, until a paycheck paid each weekday
        at its own pay (ruling plan#3089): 02-13 pays 01-25..02-07, half
        before the raise, so it moves by $500.00.  **Its banner is read on
        02-13** since task plan#3101: that is the first paycheck the raise
        reaches (ruling plan#3092), and 02-27 badges nothing.
        """
        with app.app_context():
            profile = _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()
            db.session.refresh(profile)
            calendar = calendar_for(profile.user_id)
            first = calendar.saved()[0]
            february = next(
                period for period in calendar.saved()
                if days_paid_for(
                    calendar, period.start_date,
                    profile.period_end_days_before_payday,
                ).first >= date(first.start_date.year, 2, 1)
            )
            assert february.start_date == date(first.start_date.year, 2, 27)
            reached = next(
                period for period in calendar.saved()
                if days_paid_for(
                    calendar, period.start_date,
                    profile.period_end_days_before_payday,
                ).last >= date(first.start_date.year, 2, 1)
            )
            assert reached.start_date == date(first.start_date.year, 2, 13)
            before = pricing_over(calendar).for_profile(
                profile,
            ).at(february).earnings.gross_biweekly

            with db.session.no_autoflush:
                pending = SalaryRaise(
                    salary_profile_id=profile.id,
                    raise_type_id=ref_cache.raise_type_id(
                        RaiseTypeEnum.CUSTOM,
                    ),
                    effective_date=date(first.start_date.year, 2, 1),
                    flat_amount=Decimal("26000.00"), percentage=None,
                    is_recurring=False, terminal_year=None,
                )
                profile.raises.append(pending)
                # The relationship is unloaded on a pending instance, which
                # is the state under test; ``no_autoflush`` is what keeps it
                # pending through the pricing below.
                assert pending.raise_type is None
                paychecks = pricing_over(calendar).for_profile(profile)
                after = paychecks.at(february)
                first_reached = paychecks.at(reached)
                db.session.rollback()

            # $26,000 a year is exactly $1,000.00 a paycheck over 26.
            assert after.earnings.gross_biweekly == before + Decimal("1000.00")
            assert first_reached.period.banner.text == "Custom +$26,000.00"
            assert after.period.banner.text == ""
