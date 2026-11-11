"""Prove plan step recurrence:R25 moves nothing: every date and every loan figure.

The regression harness for **R25** (rulings **R-R105**, **R-R106**, **R-R122**;
findings **REC-545**, **REC-546**, **REC-547**).  The step deletes the loan
code's last copies of its calendar and terms -- the rate engine's month step,
the payoff and refinance calculators' month step and clamp, pricing's second
bundle of a loan's terms -- and folds the monthly-grid arithmetic of the loan
calendar, the card statement, the pay grid and the recurrence walk onto one set
beside the clamp in :mod:`app.utils.dates`.  It is a ``$0.00`` step, so every
line this prints must be byte-identical on the base tree and the branch.

**It compiles and runs on BOTH sides** (``docs/plans/verification.md``,
standard 3): it calls only names present on both trees, and the one producer
whose signature the step changes -- the installment price, which took a
``_LoanCashBasis`` and escrow lines and takes the loan's ``LoanCalendar`` --
is reached through :func:`_price`, which dispatches on what the tree HAS.  The
card statement's three functions, which take the card's dated terms SERIES
since plan#2331 (ruling plan#2333), are reached the same way, through
:func:`_card_due_date`, :func:`_card_cycle` and :func:`_card_statements`: on a
one-version series every answer is the bare day's, so the digests stand.  So is
the month walk, which takes a coordinate's day since plan step recurrence:R8-c
and a bare day before it (:func:`_walk_months`).  The first line names the side;
diff from line 2.

**Two halves, because each is blind where the other looks.**

* **PURE** (no database): every function the step rewrote, over EVERY civil
  day the application's calendar holds (2000-01-01 .. 2100-12-31) times EVERY
  nominal day 1..31, wherever the inputs are a day and a nominal day; the
  functions of more arguments over a deterministic sweep stated beside each.
  A SHA-256 digest of every answer is printed per function, with the count of
  answers, so a single moved date anywhere moves a line.  The base tree's
  answers are the independent oracle: they are the deleted copies' own
  arithmetic, not the shared set's.
* **PRODUCTION COPY** (``DATABASE_URL``): what production data runs through --
  every loan's rate periods and installment calendar, both calculators, and
  every figure the forward plan and the walk publish with each loan payment
  planted in DERIVE mode (rolled back), which is the only mode that reaches
  the pricing bundle the step deleted (production's payments are
  stated-price).  ``tests/manual/verify_loan_plan_sum.py`` covers the plan's
  doors; ``verify_generation_pass.py`` the recurrence walk's writes.

Lines starting ``METRIC`` are EXPECTED to differ: they count, at the engine,
the statements one read pass issues against a loan's terms tables, and in a
derive-mode pass the step removes the pricer's load (the walk and the pricer
share one memo); the loan resolver's bundle still loads the terms on its own
(finding REC-559).

Usage::

    PYTHONPATH=. DATABASE_URL=postgresql://.../<copy> DATABASE_URL_APP= \\
        .venv/bin/python tests/manual/verify_r25_calendar_fold.py
"""

import hashlib
import inspect
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import create_app
from app.enums import InterestAccrualConventionEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transfer_template import TransferTemplate
from app.routes.loan.calculators import _project_refinance
from app.services import (
    amortization_engine,
    balance_at,
    card_statement,
    installment_calendar,
    loan_loaders,
    loan_resolver,
    rate_period_engine,
)
from app.services.balance_at import BalanceContext
from app.services.balance_at._plan import loan_plan
from app.services.cash_ledger import _loan_installment
from app.services.loan_ledger import walk_loan_ledger
from app.services.pay_calendar import _grid
from app.services.pay_rhythm import Monthly, SemiMonthly
from app.services import recurrence
from app.services.recurrence import _months
from app.utils.dates import (
    CALENDAR_DATE_MAX,
    CALENDAR_DATE_MIN,
    add_months,
    clamped_day,
    month_ordinal,
)

USER_ID = 1
#: Every civil day the application's calendar holds.
DAYS = [
    CALENDAR_DATE_MIN + timedelta(days=offset)
    for offset in range((CALENDAR_DATE_MAX - CALENDAR_DATE_MIN).days + 1)
]
NOMINAL_DAYS = range(1, 32)
#: One calendar month ordinal per month the calendar holds.
MONTHS = range(month_ordinal(CALENDAR_DATE_MIN), month_ordinal(CALENDAR_DATE_MAX) + 1)


class _Digest:
    """A running SHA-256 over every answer one function gave, and their count."""

    def __init__(self, name):
        self.name = name
        self.hash = hashlib.sha256()
        self.count = 0

    def add(self, value):
        """Fold one answer's ``repr`` into the digest."""
        self.hash.update(repr(value).encode())
        self.hash.update(b"\n")
        self.count += 1

    def line(self):
        """The printed line: name, answer count, digest."""
        return f"PURE {self.name:<34} n={self.count:>10} {self.hash.hexdigest()}"


def _price(origination, payment_day, periods, lines, due, period_start, extra):
    """The derive-mode installment price, on whichever signature this tree has."""
    params = list(inspect.signature(_loan_installment._installment_cash).parameters)
    if params[0] == "calendar":
        from app.services.loan_ledger import LoanCalendar  # pylint: disable=import-outside-toplevel
        calendar = LoanCalendar(
            origination_date=origination, payment_day=payment_day,
            periods=periods, escrow_lines=lines,
        )
        return _loan_installment._installment_cash(
            calendar, due, period_start, extra,
        )
    basis = _loan_installment._LoanCashBasis(  # pylint: disable=no-member
        periods=periods, payment_day=payment_day, origination_date=origination,
    )
    return _loan_installment._installment_cash(  # pylint: disable=too-many-function-args
        basis, lines, due, period_start, extra,
    )


#: Whether this tree's card statement takes the card's dated terms series
#: (plan#2331) rather than a bare close or due day.
_CARD_TAKES_SERIES = (
    list(inspect.signature(card_statement.cycle_containing).parameters)[0]
    == "versions"
)


def _card_series(close_day, due_day):
    """A one-version terms series, which every day before and after reads."""
    return [SimpleNamespace(
        effective_date=CALENDAR_DATE_MIN, statement_close_day=close_day,
        payment_due_day=due_day,
    )]


def _walk_months(start, nominal, step):
    """``_months.walk_months`` on whichever signature this tree has."""
    if "nominal_day" in inspect.signature(_months.walk_months).parameters:
        return _months.walk_months(start, nominal, step)
    day_of_month = getattr(recurrence, "DayOfMonth")
    return _months.walk_months(start, day_of_month(nominal).date_in, step)


def _card_due_date(closes, due_day):
    """``card_statement.due_date_for`` on whichever signature this tree has."""
    if _CARD_TAKES_SERIES:
        return card_statement.due_date_for(_card_series(due_day, due_day), closes)
    return card_statement.due_date_for(closes, due_day)


def _card_cycle(close_day, day):
    """``card_statement.cycle_containing`` on whichever signature this tree has."""
    if _CARD_TAKES_SERIES:
        return card_statement.cycle_containing(_card_series(close_day, close_day), day)
    return card_statement.cycle_containing(close_day, day)


def _card_statements(close_day, first, last):
    """``card_statement.statement_sequence`` on whichever signature this tree has."""
    if _CARD_TAKES_SERIES:
        return card_statement.statement_sequence(
            _card_series(close_day, close_day), first, last,
        )
    return card_statement.statement_sequence(close_day, first, last)


def _escrow_line(*versions):
    """One escrow line with its versions, as ``(effective_date, annual)`` pairs."""
    return SimpleNamespace(
        id=1, name="Escrow",
        versions=[
            SimpleNamespace(
                id=index, effective_date=effective, annual_amount=Decimal(annual),
                is_removed=False, inflation_rate=None, created_at=None,
            )
            for index, (effective, annual) in enumerate(versions)
        ],
    )


def pure_day_by_nominal_day():
    """Every day x every nominal day: the one-day-and-one-day-of-month answers."""
    digests = {name: _Digest(name) for name in (
        "monthly_due_date", "due_in_following_month", "card.due_date_for",
        "card.cycle_containing", "grid.monthly_steps_to", "grid.monthly_payday",
        "installment_of", "installment_paid_by",
    )}
    for nominal in NOMINAL_DAYS:
        anchor = clamped_day(month_ordinal(date(2026, 3, 1)), nominal)
        cadence = Monthly(day=nominal)
        for day in DAYS:
            digests["monthly_due_date"].add(
                installment_calendar.monthly_due_date(day, nominal))
            digests["due_in_following_month"].add(
                installment_calendar.due_in_following_month(day, nominal))
            digests["card.due_date_for"].add(_card_due_date(day, nominal))
            window = _card_cycle(nominal, day)
            digests["card.cycle_containing"].add((window.opens, window.closes))
            steps = _grid.cadence_steps_to(anchor, cadence, day)
            digests["grid.monthly_steps_to"].add(steps)
            digests["grid.monthly_payday"].add((
                _grid.nominal_payday(anchor, cadence, steps),
                _grid.nominal_payday(anchor, cadence, steps + 1),
            ))
            # The installment a day falls in, for originations whose first
            # installment lands before, in and after the day's own month --
            # every relation the threshold can take -- each on a day of its
            # month that varies with the inputs.
            for offset in (-14, -2, -1, 0, 1):
                origination = clamped_day(
                    month_ordinal(day) + offset, (day.day * 7 + nominal) % 31 + 1,
                )
                digests["installment_of"].add(
                    installment_calendar.installment_of(origination, nominal, day))
                digests["installment_paid_by"].add(
                    installment_calendar.installment_paid_by(origination, nominal, day))
    for digest in digests.values():
        print(digest.line())


def pure_sequences():
    """The enumerations: the installment calendar, the statements, the walk."""
    installments = _Digest("installment_dates")
    statements = _Digest("card.statement_sequence")
    for ordinal in MONTHS[:-62]:
        for nominal in NOMINAL_DAYS:
            origination = clamped_day(ordinal, (ordinal * 5 + nominal) % 31 + 1)
            for through in (
                origination + timedelta(days=20),
                add_months(origination, 1),
                add_months(origination, 61),
            ):
                installments.add(installment_calendar.installment_dates(
                    origination, nominal, through))
    for day in DAYS[::7]:
        for nominal in NOMINAL_DAYS:
            for span in (-1, 0, 29, 31, 400):
                statements.add([
                    (window.opens, window.closes)
                    for window in _card_statements(
                        nominal, day, day + timedelta(days=span))
                ])
    walk = _Digest("recurrence.walk_months")
    for start in MONTHS[::7]:
        for nominal in NOMINAL_DAYS:
            for step in (1, 2, 3, 6, 12, 24, 120):
                dates = list(_walk_months(start, nominal, step))
                walk.add((len(dates), dates[:30], dates[-3:]))
    for digest in (installments, statements, walk):
        print(digest.line())


def pure_semi_monthly():
    """Every legal twice-a-month pair x two anchors x every 7th day."""
    digest = _Digest("grid.semi_monthly_steps_to")
    payday = _Digest("grid.semi_monthly_payday")
    for lower in range(1, 28):
        for upper in range(lower + 1, 32):
            cadence = SemiMonthly(days=(lower, upper))
            for member_day in (lower, upper):
                anchor = clamped_day(month_ordinal(date(2026, 3, 1)), member_day)
                for day in DAYS[::7]:
                    steps = _grid.cadence_steps_to(anchor, cadence, day)
                    digest.add(steps)
                    payday.add((
                        _grid.nominal_payday(anchor, cadence, steps),
                        _grid.nominal_payday(anchor, cadence, steps + 1),
                    ))
    print(digest.line())
    print(payday.line())


def pure_loan_engines():
    """The rate engine's boundaries, the projection, the slots and both calculators."""
    periods_digest = _Digest("build_rate_periods")
    for ordinal in MONTHS[:612]:
        for day in (1, 15, 28, 29, 30, 31):
            origination = clamped_day(ordinal, day)
            for first, interval in ((6, 6), (12, 12), (60, 12), (84, 6)):
                terms = rate_period_engine.LoanTerms(
                    origination_date=origination,
                    original_principal=Decimal("250000.00"),
                    base_rate=Decimal("0.06500"),
                    term_months=360,
                    is_arm=True,
                    arm_first_adjustment_months=first,
                    arm_adjustment_interval_months=interval,
                    convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                )
                periods_digest.add([
                    (p.index, p.start_date, p.annual_rate, p.period_pi,
                     p.start_month_index, p.term_months_at_start)
                    for p in rate_period_engine.build_rate_periods(
                        terms=terms, rate_changes=None, recorded_period_pi=None,
                    )
                ])
    print(periods_digest.line())

    projection = _Digest("project_forward")
    for ordinal in MONTHS[:612:3]:
        for nominal in NOMINAL_DAYS:
            starting = clamped_day(ordinal, (ordinal + nominal) % 31 + 1)
            for remaining in (1, 13, 61):
                rows = amortization_engine.project_forward(
                    amortization_engine.ProjectionInputs(
                        starting_balance=Decimal("20000.00"),
                        starting_date=starting,
                        remaining_months=remaining,
                        payment_day=nominal,
                        terms_schedule=[
                            amortization_engine.PeriodTerms(
                                start_date=starting, annual_rate=Decimal("0.06"),
                                monthly_pi=Decimal("386.66"),
                                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                            ),
                            amortization_engine.PeriodTerms(
                                start_date=add_months(starting, 7),
                                annual_rate=Decimal("0.08"),
                                monthly_pi=Decimal("402.10"),
                                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                            ),
                        ],
                    ),
                    extra_monthly=Decimal("25.00"),
                )
                projection.add([
                    (row.month, row.payment_date, row.payment, row.principal,
                     row.interest, row.extra_payment, row.remaining_balance)
                    for row in rows
                ])
    print(projection.line())

    slots = _Digest("schedule_dates")
    for day in DAYS[::5]:
        for nominal in NOMINAL_DAYS:
            slots.add(amortization_engine.schedule_dates([day, day], nominal))
            slots.add(amortization_engine.schedule_dates([day] * 4, nominal))
    print(slots.line())

    payoff = _Digest("calculate_payoff_by_date")
    for ordinal in MONTHS[200:560:4]:
        for nominal in (1, 15, 28, 29, 30, 31):
            origination = clamped_day(ordinal, nominal)
            for years in (3, 9):
                payoff.add(amortization_engine.calculate_payoff_by_date(
                    amortization_engine.PayoffRequest(
                        current_principal=Decimal("20000.00"),
                        remaining_months=120,
                        target_date=add_months(origination, 12 * years),
                        origination_date=origination,
                        payment_day=nominal,
                        terms_schedule=[amortization_engine.PeriodTerms(
                            start_date=origination,
                            annual_rate=Decimal("0.06"),
                            monthly_pi=Decimal("222.04"),
                            convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                        )],
                    )
                ))
    print(payoff.line())

    refinance = _Digest("routes.loan._project_refinance")
    for nominal in NOMINAL_DAYS:
        for term in (12, 60, 360):
            refinance.add(_project_refinance(
                Decimal("200000.00"), Decimal("0.05500"), term, nominal,
                InterestAccrualConventionEnum.MONTHLY_1_12,
            ))
    print(refinance.line())


def pure_installment_price():
    """The derive-mode price over originations, due days, payment days and two terms."""
    digest = _Digest("installment_cash")
    lines = [_escrow_line(
        (date(2019, 1, 1), "1200.00"), (date(2024, 3, 1), "3600.00"),
    )]
    for ordinal in range(month_ordinal(date(2020, 1, 1)), month_ordinal(date(2030, 1, 1))):
        for nominal in NOMINAL_DAYS:
            origination = clamped_day(ordinal, (ordinal * 3 + nominal) % 31 + 1)
            periods = [
                rate_period_engine.RatePeriod(
                    index=0, start_date=origination, annual_rate=Decimal("0.06"),
                    period_pi=Decimal("200.00"), start_month_index=0,
                    term_months_at_start=360,
                    convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                ),
                rate_period_engine.RatePeriod(
                    index=1, start_date=add_months(origination, 25),
                    annual_rate=Decimal("0.07"), period_pi=Decimal("250.00"),
                    start_month_index=25, term_months_at_start=335,
                    convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                ),
            ]
            for offset_days in (-10, 0, 9, 27, 33, 61, 760, 790):
                due = origination + timedelta(days=offset_days)
                for stored in (due, None):
                    digest.add(_price(
                        origination, nominal, periods, lines, stored,
                        due - timedelta(days=6), Decimal("15.00"),
                    ))
    print(digest.line())


def _plant_derive_mode():
    """Put every active loan payment definition in DERIVE mode (rolled back by the caller)."""
    loan_ids = [row.account_id for row in db.session.query(LoanParams).all()]
    for template in (
        db.session.query(TransferTemplate)
        .filter(TransferTemplate.to_account_id.in_(loan_ids))
        .all()
    ):
        settings = (
            db.session.query(LoanPaymentSettings)
            .filter_by(transfer_template_id=template.id)
            .one_or_none()
        )
        if settings is None:
            db.session.add(LoanPaymentSettings(
                transfer_template_id=template.id, derive_from_loan=True,
                extra_principal=Decimal("0.00"),
            ))
        else:
            settings.derive_from_loan = True
    db.session.flush()


#: The tables a loan's CONTRACT TERMS load from.
_TERMS_TABLES = (
    "budget.loan_params", "budget.rate_history",
    "budget.escrow_lines", "budget.escrow_component_versions",
)


def _count_term_loads():
    """Count every statement the ENGINE issues against a terms table; return the counter.

    Engine-level (``before_cursor_execute``), so it sees every load path --
    a loader bound by name in the importing module included -- where wrapping
    module attributes would not.
    """
    counts = Counter()

    def _record(conn, cursor, statement, params, context, executemany):  # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
        for table in _TERMS_TABLES:
            if table in statement:
                counts[table] += 1

    event.listen(Engine, "before_cursor_execute", _record)
    return counts, _record


def production_copy():
    """Every loan figure the step's code reaches, on the copy's own data."""
    as_of = date(2026, 10, 8)
    loans = db.session.query(LoanParams).order_by(LoanParams.account_id).all()
    for params in loans:
        account = db.session.get(Account, params.account_id)
        periods = loan_resolver.resolve_periods(
            params, loan_loaders.load_rate_changes(params.account_id),
        )
        print(f"LOAN {account.name} periods={[
            (p.start_date, p.annual_rate, p.period_pi) for p in periods]}")
        print(f"LOAN {account.name} installments="
              f"{installment_calendar.installment_dates(
                  params.origination_date, params.payment_day, date(2060, 12, 31))}")
        for years in (1, 5, 10):
            print(f"LOAN {account.name} payoff_by_date+{years}y="
                  f"{amortization_engine.calculate_payoff_by_date(
                      amortization_engine.PayoffRequest(
                          current_principal=Decimal('100000.00'),
                          remaining_months=params.term_months,
                          target_date=add_months(as_of, 12 * years),
                          origination_date=date(2026, 10, 1),
                          payment_day=params.payment_day,
                          terms_schedule=loan_resolver.engine_terms(
                              params, loan_loaders.load_rate_changes(params.account_id)),
                      ))}")
        print(f"LOAN {account.name} refinance="
              f"{_project_refinance(Decimal('100000.00'), Decimal('0.05000'), 180, params.payment_day, loan_resolver.accrual_convention(params))}")

    for label, derive in (("STATED", False), ("DERIVE", True)):
        db.session.begin_nested()
        if derive:
            _plant_derive_mode()
        ctx = BalanceContext.build(USER_ID, as_of)
        for params in loans:
            account = db.session.get(Account, params.account_id)
            plan = loan_plan(account, ctx)
            for payment in plan.payments:
                print(f"{label} {account.name} PLAN {payment!r}")
                # The pass's pricer asked directly, so the line sees the
                # pricing bundle the step deleted even where the derived
                # price equals the stated one (it does on both live loans).
                print(f"{label} {account.name} PRICE {payment.due_date} "
                      f"{ctx.amounts().loans.derive_cash(
                          payment.due_date, payment.due_date - timedelta(days=9),
                          account.id, Decimal('7.00'))}")
            for month in range(0, 48):
                day = add_months(date(2026, 10, 1), month)
                print(f"{label} {account.name} BAL {day} "
                      f"{balance_at.balance_at(account, ctx, day)}")
            for outcome in walk_loan_ledger(account.id, ctx.scenario_id).settled_splits:
                print(f"{label} {account.name} SPLIT {outcome.due_date} "
                      f"{outcome.cash} {outcome.interest} {outcome.escrow} "
                      f"{outcome.principal} {outcome.excess} {outcome.balance_after}")
        fresh = BalanceContext.build(USER_ID, as_of)
        accounts = [db.session.get(Account, params.account_id) for params in loans]
        counts, listener = _count_term_loads()
        for account in accounts:
            balance_at.balance_at(account, fresh, date(2027, 6, 1))
        event.remove(Engine, "before_cursor_execute", listener)
        print(f"METRIC {label} statements against each terms table in one "
              f"pass's balance read of both loans: {dict(sorted(counts.items()))}")
        db.session.rollback()


def main():
    """Print the side, the pure digests, then the production copy's figures."""
    side = "BRANCH" if hasattr(__import__(
        "app.utils.dates", fromlist=["grid_month_on_or_before"]),
        "grid_month_on_or_before") else "BASE"
    print(f"# side={side}")
    app = create_app()
    with app.app_context():
        pure_day_by_nominal_day()
        pure_sequences()
        pure_semi_monthly()
        pure_loan_engines()
        pure_installment_price()
        production_copy()


if __name__ == "__main__":
    main()
