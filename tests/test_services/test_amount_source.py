"""
Shekel Budget App -- Where one row's amount comes from (plan step X-au-b)

The total dispatch of :mod:`app.services.cash_ledger._amount_source`: ruling
**R-FI**'s amount rules (five, four since plan step balance:X-bi-6-5), the
order that makes two of them subsets of two
others rather than rivals, and the refusal each one raises where its producer
cannot answer.

**A DERIVED fixture here carries no stored figure at all, and an OWN fixture
carries one no rule may answer.**  Neither is decoration.  An adversarial review
mutated the resolver to ``return txn.estimated_amount`` -- deleting the dispatch,
all five rules and every refusal -- and the exhaustive production oracle beside
this file reported *997 rows, 0 mismatches, OK*, because for 946 of those rows
the app's own answer IS that column.  A fixture whose stored figure equals its
derived one cannot tell the two implementations apart, so none here does.  Since
plan step X-au-c2 a derived row cannot even hold a rival figure:
``declare_derived`` empties the column as it stamps the source, because
``ck_transactions_amount_ownership`` pairs the two, so the same mutation now
returns ``None`` where a ``Decimal`` is asserted.  The OWN fixtures still carry
``_NOT_AN_ANSWER`` -- for them the column IS the answer, and the figure names
which of three candidates a test means.

**Which rule prices a row is now a question to the COLUMN** (finding **N-262**,
closed here): ``amount_rule`` reads ``amount_source_id`` and no longer infers
ownership from ``is_override`` or from having left Projected.  So every derived
fixture DECLARES its relation, and the three arms that used to be inferences --
overridden, non-Projected, soft-deleted -- are graded in
:class:`TestTheDeclarationDecides` as the inversions they became: the flag and
the status no longer move the rule in either direction.

**The refusals are why most of this file exists.**  Zero rows on the 2026-08-12
production clone take any refusal arm, so production evidence cannot show that a
single one of them fires.  Every guard therefore carries its own control, built
to reach the arm and shown to raise, per ``docs/plans/verification.md``
standard 4.

The LOAN_PAYMENT rule is graded ONLY here: ``budget.loan_payment_settings`` is
empty on production, so the oracle prices ``$0.00`` through it and a seeded loan
is the only place it runs.

**No transfer shadow is born since plan step balance:X-bi-6-4d-2**, and
none can be stored: ``ck_transactions_names_no_transfer`` refused one (ruling
**R-BAL258**) until plan step balance:X-bi-6-5 dropped the column a shadow
named its transfer by.  So a case that priced a shadow asks the TRANSFER, or
prices its legs through ``amounts_by_key`` -- each leg worth
``resolve_transfer_amount`` on its parent (**R-BAL13**), which is what rule 5
answered for the shadow -- with its figures unchanged (ruling **R-BAL167**
class 1).  Rule 5 itself -- a twin's dispatch to its parent and its refusal of
an orphan -- was graded on a twin built UNSAVED (class 4), and three cases
asserted the database refused a planted twin (class 2), until plan step
balance:X-bi-6-5 deleted the rule, its relation and the column: those six
cases went with them (ruling plan#1850), and that step's commit names each.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import AcctTypeEnum, AmountSourceEnum, StatusEnum, TxnTypeEnum
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import (
    income_service, pay_list_service, status_seam, template_amount_service,
    transaction_service, transfer_legs,
)
from app.services.amount_ownership import declare_derived, state_own_amount
from app.services.cash_ledger import (
    AmountRule,
    DefinitionRow,
    derived_amount_basis,
    amount_rule,
    amounts_by_key,
    contribution_of,
    contributions_by_key,
    definition_cash,
    is_loan_payment_definition,
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from app.services.cash_ledger import transfer_amount_rule
from app.services.cash_ledger._amount_rule import (
    _RELATION_RULES,
    _TRANSFER_RELATION_RULES,
)
from app.services.cash_ledger._amount_source import (
    _RULE_ANSWERS,
    _TRANSFER_RULE_ANSWERS,
)
from tests._test_helpers import NO_TAX_LAW
from tests._test_helpers import (
    write_past_the_amount_seam,
    add_escrow_line,
    add_txn,
    capture_sql_statements,
    create_loan_account,
    create_savings_account,
    create_transfer,
    current_pay_period,
    one_off_row_of,
    payback_row_of,
    generate_row_of,
    generate_transfer_of,
    loan_params_for,
    make_cadence_rule,
    make_every_period_rule,
    make_loan_payment_template,
    make_salary_profile,
    rebuild_calendar,
    repriced_by_the_owner,
    settlement_if_settling,
    transfer_repriced_by_the_owner,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY
from app.services.balance_at import BalanceContext
from app.routes.grid._items import build_amount_maps
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import parent_of


# The definition's price history, and the row dates that select from it.  TWO
# versions rather than one, deliberately: a single-version series answers the
# same figure on every day, so a test built on one cannot tell a resolver that
# reads DATES from one that ignores them -- nor the series from the
# ``default_amount`` scalar it replaced, which ``set_amount`` keeps equal to the
# NEWEST version.  Production has exactly one multi-version template of each
# kind, which is why this discrimination lives here rather than in the oracle.
_OLD_PRICE = Decimal("178.00")
_NEW_PRICE = Decimal("165.30")
_PRICE_ROSE_ON = date(2026, 1, 1)
_PRICE_FELL_ON = date(2026, 3, 1)
_DUE_UNDER_OLD_PRICE = date(2026, 2, 14)

# A TRANSACTION row's date is the ENGINE's (plan step balance:X-cf-3b): an
# every-paycheck definition dates its row on the paycheck's own start
# (``compute_due_date``), so WHICH paycheck a row is generated in is what
# selects its price.  The seeded calendar runs biweekly from 2026-01-02, so
# its first paycheck opens under the old price and its sixth (2026-03-13)
# under the new one; the date tests assert the window each row lands in.  A
# row older than the series needs a paycheck before 2026-01-01, which that
# calendar does not hold -- its one case rebuilds the calendar to open in
# 2025.  A TRANSFER's date is the engine's too since plan step balance:X-ch
# (the twin builder :func:`_generated_transfer`), so the same two paychecks
# select a transfer's price; ``_DUE_UNDER_OLD_PRICE`` is what one case asks
# the DEFINITION's series directly with.
#
# **Under the every-paycheck cadence a row's due date IS its paycheck's
# start**, so a case whose subject is "the due date, not the period start"
# cannot be built on it: the mutation it exists to catch reads the same day.
# Such a case takes a MONTHLY cadence on the 1st and the paycheck that covers
# the 1st but does not start on it (``_PAYCHECK_STRADDLING_THE_PRICE_FALL``,
# 2026-02-27..03-12, whose row the engine dates 2026-03-01).
_PAYCHECK_UNDER_OLD_PRICE = 0
_PAYCHECK_UNDER_NEW_PRICE = 5
_PAYCHECK_STRADDLING_THE_PRICE_FALL = 4
#: The first contractual installment of :func:`_mortgage` (originated
#: 2026-01-01, paid on the 1st), which is the occurrence its MONTHLY payment
#: rule first names; :func:`_loan_payment` generates in the paycheck covering it
#: (:func:`current_pay_period`, the calendar's own placement).
_FIRST_INSTALLMENT = date(2026, 2, 1)
_CALENDAR_OPENING_BEFORE_THE_SERIES = date(2025, 12, 5)

# The figure every OWN fixture stores on the row itself, and every settled one
# records as what moved.  No rule may answer it, so any test that returns it
# has caught a resolver reading a column.
_NOT_AN_ANSWER = "999.99"


def _basis_for(seed_user):
    """The AmountBasis for this owner and scenario -- what a caller builds.

    A basis is pinned to an owner and a scenario since plan step X-au-c2b, not
    to a row set, so there is one per test whatever it resolves.  Its two
    derivations are lazy, so building it here costs nothing until a rule asks.
    """
    return derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)


def _declare_transfer_derived(xfer):
    """Declare *xfer* priced by its definition, which EMPTIES its amount column.

    The application's own writer (``declare_derived``), flushed: a row states
    either a figure it owns or the relation that prices it, never both
    (``ck_transfers_amount_ownership``).  It had a transaction twin,
    ``_declare_derived``, which declared shadow rows and went with its last
    caller at plan step balance:X-bi-6-4d-2, where no shadow is born (ruling
    **R-BAL167** class 4), with ``_state_own_amount``, its other direction.

    Only ``template`` can price a transfer -- a transfer has no parent transfer
    -- so the relation is not a parameter here.  ``ck_transfers_adhoc_owns_amount``
    additionally refuses the declaration outright on an ad-hoc transfer, so this
    is only ever called on a generated one.
    """
    declare_derived(xfer, AmountSourceEnum.TEMPLATE)
    db.session.flush()
    return xfer


def _resolve(seed_user, txn):
    """Resolve one row against a basis built over exactly that row."""
    return resolve_transaction_amount(txn, _basis_for(seed_user))


def _priced_template(
    seed_user, name="Geico", txn_type=TxnTypeEnum.EXPENSE, *,
    cadence=EVERY_PERIOD, fires_on_day=None,
):
    """A transaction template whose series states two prices, two months apart.

    Every-paycheck by default; a case whose subject is the row's OWN date
    against its period's start states a MONTHLY *cadence* and the day.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(txn_type),
        name=name,
        default_amount=_OLD_PRICE,
    )
    db.session.add(template)
    db.session.flush()
    template_amount_service.set_amount(
        template, _OLD_PRICE, effective_on=_PRICE_ROSE_ON,
    )
    template_amount_service.set_amount(
        template, _NEW_PRICE, effective_on=_PRICE_FELL_ON,
    )
    # The definition first, then the cadence onto it (plan step R-F6): a
    # definition with rows is one that repeats, and the engine writes them.
    make_cadence_rule(template, cadence, fires_on_day=fires_on_day)
    db.session.flush()
    return template


def _seriesless_template(seed_user, name="Never Stated"):
    """A transaction template created around the write door, so its series is empty.

    It repeats every paycheck all the same: the engine writes rows from a
    cadence, not from a price, which is exactly what leaves this row with a
    definition that states nothing.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=Decimal("42.00"),
    )
    db.session.add(template)
    db.session.flush()
    make_every_period_rule(db.session, template)
    return template


def _salary_template(seed_user, txn_type=TxnTypeEnum.INCOME):
    """A template an ACTIVE salary profile drives, and the profile."""
    template = _priced_template(
        seed_user, name="Data Manager", txn_type=txn_type,
    )
    profile = make_salary_profile(seed_user, db.session)
    profile.template = template
    db.session.flush()
    return template, profile


def _template_row(period, template, *, owns=False):
    """The ENGINE's row of *template* in *period* (plan step balance:X-cf-3b).

    DERIVED -- declaring its definition and storing no figure -- which is the
    state the template cutover (plan step X-au-e) puts every non-override row
    in, and it is dated by the engine: on the paycheck's start, for the
    every-paycheck cadence every definition here carries.  Pass ``owns=True``
    for the other half of the model -- a row the OWNER re-priced, taken the
    way the edit door takes it (:func:`repriced_by_the_owner`) -- where the
    stored ``_NOT_AN_ANSWER`` IS the answer.

    It built the row by hand and then linked it until X-cf-3b, which is the
    shape the census in ledger row BAL-480 could not see (date first, link
    second); the one axis its callers set on it, the DUE DATE, is now which
    paycheck they generate in.
    """
    txn = generate_row_of(template, period)
    return repriced_by_the_owner(txn, _NOT_AN_ANSWER) if owns else txn


def _legs_of(xfer):
    """Return *xfer*'s two legs, from-side first, as a screen reads them.

    ``transfer_legs.leg_of`` on each endpoint.  It replaces ``_shadow_of``,
    which returned one of the two shadow ROWS and went with its last caller at
    plan step balance:X-bi-6-4d-2, where no shadow is born (ruling
    **R-BAL167** class 4): a leg is read off its transfer, and
    ``amounts_by_key`` prices it.
    """
    return [
        transfer_legs.leg_of(xfer, xfer.from_account_id),
        transfer_legs.leg_of(xfer, xfer.to_account_id),
    ]


def _generated_transfer(
    seed_user, period, to_account, *,
    series=_OLD_PRICE, later=_NEW_PRICE, stored=Decimal("111.11"),
    owns=False, cadence=EVERY_PERIOD, fires_on_day=None,
):
    """The ENGINE's transfer, in *period*, of a template whose series states TWO prices.

    The twin of :func:`_template_row` (plan step balance:X-ch): DERIVED,
    parent and both legs -- the parent names its definition and each leg
    names the parent, the state plan step X-au-f puts every generated
    transfer in -- so their amount columns are EMPTY and a resolver reading
    any of the three answers ``None`` against an asserted ``Decimal``.  It is
    dated by the engine: on the paycheck's start under the every-paycheck
    *cadence* it carries by default, so WHICH paycheck selects the price
    (``_PAYCHECK_UNDER_OLD_PRICE`` / ``_PAYCHECK_UNDER_NEW_PRICE``), or on
    the day a MONTHLY *cadence* names inside the paycheck, for the one case
    whose subject is the date being the row's OWN and not its period's.  It
    built the transfer through the ad-hoc door, dated it by hand and linked
    it afterwards until X-ch -- date first, link second, the shape ledger
    row BAL-488's census could not see.

    Pass ``owns=True`` for the shape where a HUMAN authored the figure --
    parent and legs all holding ``stored``, a figure no derived rule may
    answer -- taken the way the transfer edit door takes it
    (:func:`transfer_repriced_by_the_owner`), which ruling **R-IO** says
    must keep the figure they typed.

    The price is stated through ``template_amount_service.set_amount``, the
    one write door, at the two dates the module's constants name.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name="Money Market Contribution",
        default_amount=series,
    )
    db.session.add(template)
    db.session.flush()
    template_amount_service.set_amount(
        template, series, effective_on=_PRICE_ROSE_ON,
    )
    if later is not None:
        template_amount_service.set_amount(
            template, later, effective_on=_PRICE_FELL_ON,
        )
    make_cadence_rule(template, cadence, fires_on_day=fires_on_day)
    xfer = generate_transfer_of(template, period)
    if owns:
        transfer_repriced_by_the_owner(xfer, stored)
    return xfer, template


def _mortgage(seed_user, escrow_annual=Decimal("3600.00")):
    """A $200k / 6% / 360mo mortgage with an escrow line, paid on the 1st.

    P&I = amortize(200000, 0.06, 360) = 1,199.10; escrow = 3,600 / 12 = 300.00.
    """
    loan = create_loan_account(
        seed_user, db.session, name="Live Mortgage",
        principal=Decimal("200000.00"), rate=Decimal("0.06000"),
        term=360, origination_date=date(2026, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    params = loan_params_for(db.session, loan.id)
    add_escrow_line(
        db.session, loan.id, "Property Tax", escrow_annual,
        effective_date=params.origination_date,
    )
    return loan


def _loan_payment(
    seed_user, *, derive, extra=None, to_account=None,
    series=Decimal("1300.00"), stored=Decimal("1250.00"), owns=False,
):
    """A mortgage payment transfer in one of its two modes.

    The ENGINE's transfer of the loan's own definition
    (:func:`make_loan_payment_template` -- a MONTHLY rule on the loan's
    payment day, bound to its first contractual installment, with the
    ``loan_payment_settings`` row that carries the MODE), generated in the
    paycheck covering ``_FIRST_INSTALLMENT`` and dated on it.  A hand-built
    one sat in whichever paycheck the caller named and carried that date by
    hand (plan step balance:X-ch).

    TWO DISTINCT figures -- the definition states ``$1,300.00`` and, with
    ``owns=True``, the parent transfer's column holds ``$1,250.00`` -- so a
    manual-mode assertion names which of the two it means.  The review that
    built this fixture found an earlier one setting every figure to one
    number, which made the test pass for any implementation.

    **There were THREE until plan step X-au-g-2c-2, and the third is now
    UNREPRESENTABLE rather than merely unused.**  Each shadow held ``$1,200.00``
    so that a manual-mode assertion could prove the rule did not read the
    shadow's own column -- which is exactly what the deleted
    ``_manual_shadow_amount`` did read.  From that step a shadow declared
    ``PARENT_TRANSFER`` from birth and ``ck_transactions_amount_ownership``
    refused it a figure, so the distinction the third number graded was made
    by the schema while shadows were born.  None is born now, and no row can
    name a transfer: ``ck_transactions_names_no_transfer`` refused one until
    plan step balance:X-bi-6-5 dropped the column itself.

    ``owns=True`` takes the transfer for the OWNER at ``stored`` through the
    transfer door (:func:`transfer_repriced_by_the_owner`).  It also took each
    shadow's figure back to ``$1,200.00`` until plan step balance:X-bi-6-4d-2,
    where no shadow is born and the database refuses one (ruling
    **R-BAL258**), so that figure went with its row: the one case that named
    it names the transfer's own ``$1,250.00`` as the rival base instead
    (ruling **R-BAL272**).

    Returns the transfer.  It returned ``(the checking-side shadow, both
    shadows)`` until that step (ruling **R-BAL167** class 4); a case that
    priced a shadow asks the transfer or its legs (:func:`_legs_of`).
    """
    loan = _mortgage(seed_user) if to_account is None else to_account
    template = make_loan_payment_template(
        db.session, seed_user, loan, amount=series,
        derive_from_loan=derive,
        extra_principal="0.00" if extra is None else extra,
    )
    xfer = generate_transfer_of(
        template, current_pay_period(seed_user["user"].id, _FIRST_INSTALLMENT),
    )
    if owns:
        transfer_repriced_by_the_owner(xfer, stored)
    db.session.flush()
    return xfer


def _declare_loan_payment_derived(xfer):
    """Declare a loan payment's transfer derived.

    **It declares the TRANSFER alone since plan step balance:X-bi-6-4d-2**,
    where no shadow is born (ruling **R-BAL167** class 4); it looped the
    transfer's shadows first, and the paragraphs below are the history of
    that loop's scope.

    Separate from :func:`_generated_transfer` for two reasons.  One is ordering:
    a test builds the payment, watches the producer price it off the stored
    column, and only THEN declares it -- the transition the loan cutover (plan
    step X-au-g) performs.

    **It declares BOTH legs since plan step X-au-g-2c-2, and the bound that
    once stopped it is twice gone.**  It read: the loan-side income leg cannot
    be declared at all, because ``loan_payment_service.get_payment_history``
    prices every shadow income row on the loan account through
    ``row_valuation.settled_contribution``, which REFUSES a row whose plan is
    derived.  Plan step **X-au-g-2c-1** routed that reader through
    ``cash_ledger.contributions_by_key``, closing finding **N-266**(a) and making
    the scope a CHOICE; **X-au-g-2c-2** then made it not a choice either, by
    declaring every transfer shadow derived on live rows.  A fixture that
    declared one leg would now be building a pair the application cannot
    produce.

    **This docstring used to call the bound a CYCLE, and it was not one.**  It
    read: the loan resolves through ``load_loan_context`` ->
    ``get_payment_history``, so the rule that prices the row routes back to the
    row.  That path was deleted at plan step X-au-g-1 --
    ``_resolve_loan_basis`` read the loan's terms alone (and plan step
    recurrence:R25 deleted it for the pass's ``LoanCalendars``) -- leaving finding
    N-266(a)'s conclusion standing on ONE UNROUTED READER, which is what
    X-au-g-2c routed.  Three weeks between the diagnosis and its true cause,
    which is why a finding's claim is re-measured before its remedy is built.
    """
    return _declare_transfer_derived(xfer)


class TestTheDispatchIsTotal:
    """Every rule has an answer, and the enum is the list of rules."""

    def test_every_amount_rule_has_an_answer_in_one_of_the_two_tables(self):
        """The two dispatch tables cover the enum exactly -- no rule, no orphan.

        The predicate behind the module's "TOTAL dispatch" claim.  A member
        added to :class:`AmountRule` without an answer would raise a ``KeyError``
        at the lookup rather than silently taking whichever branch happened to
        be last, and this is what says so before a row does.

        **It became a UNION at plan step X-au-f-2**, where ruling **R-BAL10**
        moved rule 4 off the transaction dispatch onto the transfer's.  Written
        as a union rather than as ``set(AmountRule) - {LOAN_PAYMENT}`` on the
        transaction table: a set defined by SUBTRACTION claims members nobody
        censused, and the union states the property that is actually wanted --
        every rule is answerable somewhere.
        """
        assert set(_RULE_ANSWERS) | set(_TRANSFER_RULE_ANSWERS) == set(
            AmountRule,
        )

    def test_each_table_answers_exactly_the_rules_its_rows_can_take(self):
        """And the union does not let a rule land in only ONE table by accident.

        The union above is satisfied by a rule wired into either table, so on
        its own it would pass a LOAN_PAYMENT wired only into the transaction
        dispatch -- which is the state plan step X-au-f-2 exists to leave.  The
        two key sets are therefore named, so a member added to
        :class:`AmountRule` arrives with a decision about BOTH tables rather
        than defaulting into whichever one someone edited.

        A TRANSACTION cannot take rule 4 (its parent transfer answers it) and a
        TRANSFER cannot take rule 2 (a salary profile names a transaction
        template).  A fifth rule, a shadow's *= my parent*, was the
        transaction table's alone until plan step balance:X-bi-6-5 deleted it
        (ruling plan#1850 trimmed it from this set).
        """
        assert set(_RULE_ANSWERS) == {
            AmountRule.OWN,
            AmountRule.SALARY,
            AmountRule.TEMPLATE,
        }
        assert set(_TRANSFER_RULE_ANSWERS) == {
            AmountRule.OWN,
            AmountRule.TEMPLATE,
            AmountRule.LOAN_PAYMENT,
        }

    def test_every_declarable_relation_has_a_rule(self):
        """Every relation a row may DECLARE refines into one of the four rules.

        The second half of the same totality claim, and the one plan step
        X-au-c2 added: :func:`amount_rule` now branches on
        ``amount_source_id``, so a member added to
        :class:`~app.enums.AmountSourceEnum` -- ``credit_card:CC4c``'s finance
        charge is the one already known to need one (finding **N-264**) --
        must arrive with a rule beside it.  Without this it would raise a
        ``KeyError`` on the first row that declared it, in production, instead
        of here.
        """
        assert set(_RELATION_RULES) == set(AmountSourceEnum)

    def test_every_relation_a_TRANSFER_can_declare_has_a_rule(self):
        """The transfer classifier's half, added with its table at X-au-f-2.

        A transfer may declare TEMPLATE, the one member, so its refinement
        table holds one entry.  Asserting the set both ways is what makes a NEW
        member arrive with a decision here too: a finance charge that can
        price a transfer needs an entry, and one that cannot fails the second
        assertion until it is named there with a refusal in
        :func:`transfer_amount_rule` to say so.  PARENT_TRANSFER was named
        there, refused by name, until plan step balance:X-bi-6-5 deleted the
        member and the refusal (ruling plan#1850 trimmed it from this set).
        """
        assert set(_TRANSFER_RELATION_RULES) == {AmountSourceEnum.TEMPLATE}
        assert set(AmountSourceEnum) - set(_TRANSFER_RELATION_RULES) == set()


class TestWhichRulePricesARow:
    """The classification, and the order two of its arms depend on."""

    def test_a_one_off_placed_today_is_priced_by_its_definition(
        self, app, db, seed_user, seed_periods,
    ):
        """An ordinary row since ``balance:X-bi-7b``: a rule-less definition prices it."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        assert txn.recurs is False and txn.is_placed is True
        assert amount_rule(txn) is AmountRule.TEMPLATE

    def test_a_cc_payback_owns_its_amount(self, app, db, seed_user, seed_periods):
        """A payback carries NEITHER link, so today's rules can only call it OWN.

        The kind ruling R-FI names and this step does not move: its figure is
        DERIVED (the sum of the source row's credit entries) and stored, with no
        link for a discriminator to key on -- which is why R-FI's discriminator
        is an explicit column and why finding **N-243** stays open.  21 such rows
        on the production clone, all classified here.
        """
        source = one_off_row_of(
            seed_periods[0], name="Groceries", amount="500.00",
            user_id=seed_user["user"].id, account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=seed_user["categories"]["Groceries"].id,
            is_envelope=True,
        )
        payback = payback_row_of(
            db.session, seed_user, source, Decimal("123.18"),
            seed_periods[0].start_date,
        )
        assert payback.pay_period_id == seed_periods[1].id
        assert amount_rule(payback) is AmountRule.OWN

    def test_a_template_row_is_priced_by_its_definition(
        self, app, db, seed_user, seed_periods,
    ):
        """A generated row's price comes from the template's series."""
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        assert amount_rule(txn) is AmountRule.TEMPLATE

    def test_a_salary_row_beats_the_template_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """SALARY is tested first, because a profile names an ordinary template.

        The precedence control: this row satisfies BOTH arms -- it has a
        template, and that template is salary-linked -- so a dispatch that
        tested TEMPLATE first would price every paycheck off a price series
        nobody ever stated.
        """
        template, _profile = _salary_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        assert amount_rule(txn) is AmountRule.SALARY

    def test_a_loan_payment_TRANSFER_beats_the_series_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """LOAN_PAYMENT is tested first, because a loan payment HAS a series.

        The precedence control R-BAL10 moved here from the shadow, and its
        inverse is finding **N-263**: a loan-payment transfer that placed as a
        plain template row would be priced from its definition's series --
        which in DERIVE mode is refused outright (``owns_its_amount`` is False)
        and in MANUAL mode silently drops the standing extra.

        The parent is DECLARED here, which is what makes the refinement
        reachable: a transfer carrying its own figure takes rule 1 first,
        whatever its template says (**R-IO**).
        """
        xfer = _loan_payment(seed_user, derive=True)
        assert transfer_amount_rule(xfer) is AmountRule.LOAN_PAYMENT

    def test_a_row_that_took_its_figure_back_owns_it_again(
        self, app, db, seed_user, seed_periods,
    ):
        """A template-generated row that carries a figure owns it, definition or not.

        The state a hand re-price leaves and the state the freeze leaves are the
        same state, and it is the one the CHECK describes: a figure and no
        source.  ``is_override`` is not consulted -- see
        :class:`TestTheDeclarationDecides` for the inverse, which is the half
        finding **N-262** was about.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template, owns=True)
        assert amount_rule(txn) is AmountRule.OWN
        assert _resolve(seed_user, txn) == Decimal(_NOT_AN_ANSWER)

    def test_soft_deleting_a_row_does_not_change_which_rule_prices_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Deletion says whether a row counts, never who owns its figure.

        Making it flip the rule would force ``amount_source_id`` to be rewritten
        on every delete and restore -- a stored value beside a second writer,
        which is the shape this arc removes.  102 of production's 330 transfer
        shadows are soft-deleted, so the arm is ordinary rather than theoretical.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        txn.is_deleted = True
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE


class TestTheDeclarationDecides:
    """Finding **N-262**: the COLUMN says who owns a figure, and nothing else does.

    Until plan step X-au-c2 the OWN arm was inferred from ``is_override`` and
    from having left Projected -- neither of which
    ``ck_transactions_amount_ownership`` can see -- so four live doors could
    write a row the schema admits and the resolver refuses.  Each of the four is
    reached here on a DECLARED row and shown to resolve through its relation
    rather than falling into OWN and raising.
    """

    @pytest.mark.parametrize("status", [
        StatusEnum.DONE, StatusEnum.RECEIVED, StatusEnum.CREDIT,
        StatusEnum.CANCELLED,
    ])
    def test_leaving_projected_does_not_take_a_declared_row_back(
        self, app, db, seed_user, seed_periods, status,
    ):
        """A declared row keeps its relation in every status the machine admits.

        Credit and Cancelled are the reachable half and they are why this is a
        parametrize rather than a settled-only case: both leave Projected WITHOUT
        entering the settled band, so no freeze fires and no figure is written
        back.  Under the inference such a row took the OWN arm and
        ``_own_figure`` raised on its empty column -- and production carries 7
        Cancelled and 2 Credit template-linked rows against a grid route that
        loads every row in the window with no status predicate
        (``routes/grid/_items.py``'s ``load_grid_items``), so the first
        bucket to derive would have taken out the whole screen.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        status_id = ref_cache.status_id(status)
        # Through the seam, and the two settling arms record a figure no rule
        # may answer: a resolver that read the settlement record instead of
        # the series would answer 999.99 here rather than the old price.
        status_seam.apply_status_change(
            txn, status_id,
            settlement=settlement_if_settling(
                txn, status_id, submitted=Decimal(_NOT_AN_ANSWER),
            ),
        )
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_the_override_flag_does_not_take_a_declared_row_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The flag carries four facts and pricing is not one of them.

        ``routes/transactions/mutations.py:295`` sets it for a pay-period MOVE
        with no re-price at all, and finding **N-238** (plan step X-au-h) is the
        split of the other three.  A row that was moved and not re-priced still
        has its definition's price, and a rule reading the flag would refuse it.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        txn.is_override = True
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_bulk_flag_update_does_not_take_a_declared_row_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The carry-forward shape: the flag set by SQL, past every ORM hook.

        ``carry_forward_service/_execute.py:157`` sets ``is_override`` in a bulk
        ``query.update``, which no validator and no session event can see.  It is
        written that way for the partial unique index rather than for pricing
        (the fourth fact finding **N-238** names), so a row it touched must price
        exactly as it did before.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        (
            db.session.query(Transaction)
            .filter(Transaction.id == txn.id)
            .update({"is_override": True}, synchronize_session="fetch")
        )
        db.session.flush()
        assert amount_rule(txn) is AmountRule.TEMPLATE
        assert _resolve(seed_user, txn) == _OLD_PRICE


class TestWhatEachRuleAnswers:
    """The figure each rule produces, hand-computed."""

    def test_an_own_row_answers_its_stored_estimate(
        self, app, db, seed_user, seed_periods,
    ):
        """The OWN rule reads the column and nothing else."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        assert _resolve(seed_user, txn) == Decimal("35.00")

    def test_an_own_row_answers_the_ESTIMATE_not_an_entered_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """The resolver answers the estimate half; a typed actual is a separate fact.

        ``effective_amount`` is ``actual ?? estimated``, and returning THAT here
        would make the resolver answer a human's realised figure where plan step
        X-au-c expects the budgeted one -- and would write it into the amount
        column at the freeze.  41 rows on the production clone carry an actual.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Groceries", "500.00",
            status_enum=StatusEnum.DONE, settled_amount="462.34",
        )
        assert settled_contribution(txn) == Decimal("462.34")
        assert _resolve(seed_user, txn) == Decimal("500.00")

    def test_an_excluded_row_answers_its_amount_not_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled row contributes nothing and still HAS an amount.

        ``effective_amount`` answers ``0`` for it; the amount column does not,
        and at X-au-c a resolver returning zero here would write ``0.00`` into
        the column of every excluded row.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Gym", "40.00",
            status_enum=StatusEnum.CANCELLED,
        )
        assert settled_contribution(txn) == Decimal("0")
        assert _resolve(seed_user, txn) == Decimal("40.00")

    def test_a_template_row_answers_the_price_in_effect_on_its_due_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The series is resolved on the ROW's own due date, not on today.

        Due 2026-01-02 -- its paycheck's start -- falls between the 2026-01-01
        version (``$178.00``) and the 2026-03-01 one (``$165.30``), so the
        answer is the older price -- which
        is also the answer the ``default_amount`` scalar CANNOT give, since
        ``set_amount`` keeps that column at the newest stated figure.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[_PAYCHECK_UNDER_OLD_PRICE], template)
        assert _PRICE_ROSE_ON <= txn.due_date < _PRICE_FELL_ON
        assert template.default_amount == _NEW_PRICE
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_later_row_of_the_same_template_answers_the_later_price(
        self, app, db, seed_user, seed_periods,
    ):
        """One definition, two rows, two prices -- which is what a series is for.

        The row is DATED on a day its paycheck does not START on: the
        definition is MONTHLY on the 1st, its price falls on 2026-03-01, and
        the engine's row in the paycheck 2026-02-27..03-12 is due 2026-03-01
        -- so this case fails a resolver that substitutes the period's start
        for the row's due date, the mutation the transfer twin below was
        written against.  **It could not, from plan step X-cf-3b until
        X-ch**: under the every-paycheck cadence the engine dates a row on
        its paycheck's start, so both readings named one day, and the only
        thing in this file that caught the substitution on the transaction
        arm was the no-due-date refusal, which plan step X-bv-2 deletes.  The
        adversarial review of X-ch measured it on the transfer twin and the
        same mutation was run here.
        """
        template = _priced_template(seed_user, cadence=MONTHLY, fires_on_day=1)
        txn = _template_row(
            seed_periods[_PAYCHECK_STRADDLING_THE_PRICE_FALL], template,
        )
        assert txn.pay_period.start_date < _PRICE_FELL_ON <= txn.due_date
        assert _resolve(seed_user, txn) == _NEW_PRICE

    def test_a_row_older_than_the_series_holds_at_the_earliest_price(
        self, app, db, seed_user,
    ):
        """Before the first version the series holds FLAT (ruling R-I's shape).

        The arm that makes the resolver total for a row generated into a
        historical period; 23 rows on the production clone are due before their
        template's earliest version.  The seeded calendar opens the day after
        the series does, so the row's paycheck comes from a calendar rebuilt
        to open in 2025 -- BEFORE the definition is built, because an
        every-paycheck rule's first occurrence is the calendar's opening day.
        """
        periods = rebuild_calendar(
            seed_user["user"].id, _CALENDAR_OPENING_BEFORE_THE_SERIES, 4, 14,
        )
        template = _priced_template(seed_user)
        txn = _template_row(periods[0], template)
        assert txn.due_date < _PRICE_ROSE_ON
        assert _resolve(seed_user, txn) == _OLD_PRICE

    def test_a_salary_row_answers_its_PROFILE_and_stores_no_figure(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """The SALARY rule routes to the profile, and the row holds nothing.

        **There is no second producer of a net paycheck since plan step
        X-au-d**, so an equality against ``income_service.salary_net_for``
        would be one producer graded against itself -- the shape
        ``X-au-g-2c-3c`` measured false after 200,000 agreeing draws.  What is
        gradeable is that the answer TRACKS THE PROFILE and comes from nowhere
        else: the row carries no figure at all (the declaration empties it), so
        a column-reading resolver answers ``None``, and re-pricing the PROFILE
        moves the answer.  The arithmetic is the paycheck engine's and is
        graded by its own suites.

        **The profile is re-priced through its PAY ENTRY since plan step
        salary:X-av-3a**, which dropped ``annual_salary`` for
        ``salary.pay_entries``: the Fix door doubles the one paycheck the
        profile stores, where the test doubled the yearly salary the engine
        divided by 26.
        """
        tax_law(NO_TAX_LAW)
        template, profile = _salary_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        assert txn.estimated_amount is None
        before = _resolve(seed_user, txn)
        # PINNED, not merely positive: this suite seeds no tax configs, so the
        # net IS the gross and the arithmetic is visible -- $75,000 over the
        # 26 paychecks the seeded calendar's cadence gives.  An adversarial
        # review of plan step X-au-d noted that ``> 0`` and ``after > before``
        # are satisfied by any monotone dependence on the salary, so a change
        # to the divisor or the rounding would pass.
        assert before == Decimal("2884.62"), "75000 / 26, no tax configs seeded"

        # Doubled through the Fix door, the route's own call; the payday is
        # unchanged, so the door checks no day.
        entry = profile.pay_entries[0]
        ctx = BalanceContext.build(seed_user["user"].id)
        pay_list_service.fix_entry(
            entry, ctx, entry.amount * 2, entry.payday, ctx.as_of,
        )
        db.session.flush()
        # A FRESH basis: the projection is memoized for the life of one, which
        # is the sharing ``SalaryPricing`` exists for, so re-asking the old one
        # would assert the memo rather than the rule.
        after = _resolve(seed_user, txn)

        # 2 x $2,884.62 = $5,769.24, the doubled paycheck with no tax.  It was
        # $5,769.23 while the engine divided a doubled YEAR: 150,000 / 26 =
        # 5,769.2308 -> 5,769.23 (ruling R-SAL59 stores the paycheck).
        assert after == Decimal("5769.24"), "2 x 2884.62"
        assert after > before, (
            "doubling the salary must move the paycheck this rule answers; a "
            f"rule reading anything else would still answer {before}"
        )

    def test_a_transfer_leg_answers_its_parents_RULE_not_its_column(
        self, app, db, seed_user, seed_periods,
    ):
        """A leg answers its transfer's RULE: one row holds what both sides read.

        Neither column can hold a rival figure any more -- the declaration empties
        both -- so the assertion discriminates between the definition's
        ``$178.00`` and a ``None`` from either. That is a stronger control than
        the three distinct figures this test used to plant, and it is the
        difference plan step X-au-c2 makes: before it, the shadow's own column
        and its parent's were both populated and only the review's three-figure
        fixture told a column-reading resolver from a rule-resolving one.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_a_transfer_shadow_answers_its_parents_RULE_not_either_column``;
        ruling **R-BAL167** class 1).  No shadow is born, so the expense side
        is its LEG, priced by ``amounts_by_key``; the shadow's empty column
        went with the shadow, and the parent's empty column stands.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[_PAYCHECK_UNDER_OLD_PRICE], savings,
        )
        expense_leg, _income_leg = _legs_of(xfer)
        assert xfer.amount is None
        assert amounts_by_key([expense_leg], _basis_for(seed_user)) == {
            expense_leg.cell_key: _OLD_PRICE,
        }

    def test_both_legs_of_one_transfer_answer_the_same_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Both legs of one transfer answer their parent, not themselves.

        The two legs used to be stored at DIFFERENT figures so a resolver reading
        each shadow's own column would answer two numbers where one is right.
        The declaration empties both columns instead, so such a resolver now
        answers ``None`` twice -- the same discrimination, made structural.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_both_shadows_of_one_transfer_answer_the_same_figure``; ruling
        **R-BAL167** class 1).  The two legs are read off one transfer and
        priced by ``amounts_by_key``, one ``resolve_transfer_amount`` on
        that transfer each, so they cannot answer two figures: what is
        asserted is that BOTH sides are priced, each at the parent's rule.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[_PAYCHECK_UNDER_OLD_PRICE], savings,
        )
        assert amounts_by_key(_legs_of(xfer), _basis_for(seed_user)) == {
            (xfer.id, seed_user["account"].id): _OLD_PRICE,
            (xfer.id, savings.id): _OLD_PRICE,
        }

    def test_a_leg_of_an_adhoc_transfer_answers_the_parents_own_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """An ad-hoc parent owns its amount, and the leg follows it there.

        Zero ad-hoc transfers exist on the production clone, so this shape is
        graded here and nowhere else -- and it is the arm that decides whether
        the transfer rule is total for a transfer nobody generated.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_a_shadow_of_an_adhoc_transfer_answers_the_parents_own_figure``;
        ruling **R-BAL167** class 1): the expense side is its leg, priced by
        ``amounts_by_key``, where the expense shadow was declared derived
        and resolved.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("75.00"),
        )
        expense_leg, _income_leg = _legs_of(xfer)
        assert xfer.amount_source_id is None
        assert amounts_by_key([expense_leg], _basis_for(seed_user)) == {
            expense_leg.cell_key: Decimal("75.00"),
        }


class TestTheTransferRule:
    """``resolve_transfer_amount``: the second column ruling R-FI's CHECK covers."""

    def test_an_adhoc_transfer_owns_its_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """Nobody generated it, so no definition states its price."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("75.00"),
        )
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == Decimal("75.00")

    def test_a_generated_transfer_answers_its_definitions_series(
        self, app, db, seed_user, seed_periods,
    ):
        """There is no stored column to bypass: the series is asked on the due date."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[_PAYCHECK_UNDER_OLD_PRICE], savings,
        )
        assert xfer.amount is None
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == _OLD_PRICE

    def test_a_later_transfer_of_the_same_template_answers_the_later_price(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer arm resolves on the TRANSFER's own due date.

        The sibling of the transaction-side date test, and the review's mutation
        proved it was missing: substituting the pay period's START for the due
        date -- exactly what ``_stated_amount``'s docstring argues against, since
        a period begins up to two weeks before the installment it funds -- moved
        no test and no production row.  Here the definition is MONTHLY on the
        1st and the transfer is the engine's in the paycheck that covers
        2026-03-01 without starting on it: the period starts 2026-02-27,
        before the 2026-03-01 version, while the transfer is due 2026-03-01,
        on it -- so the two dates select different prices.  **An
        every-paycheck cadence cannot build this case**, because the engine
        dates such a row on its paycheck's start and the substitution reads
        the same day; the adversarial review of plan step balance:X-ch caught
        the conversion disarming it exactly that way.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[_PAYCHECK_STRADDLING_THE_PRICE_FALL], savings,
            cadence=MONTHLY, fires_on_day=1,
        )
        assert xfer.pay_period.start_date < _PRICE_FELL_ON <= xfer.due_date
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == _NEW_PRICE

    def test_a_generated_transfer_that_carries_a_figure_owns_it(
        self, app, db, seed_user, seed_periods,
    ):
        """A definition behind it does not make a transfer derived: the column does.

        The transfer twin of
        :meth:`TestWhichRulePricesARow.test_a_row_that_took_its_figure_back_owns_it_again`,
        and the shape a re-priced transfer and a frozen one both leave.  Its
        definition still states ``$178.00`` on this due date, and the transfer
        answers its own ``$111.11``.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, template = _generated_transfer(
            seed_user, seed_periods[_PAYCHECK_UNDER_OLD_PRICE], savings,
            owns=True,
        )
        assert template_amount_service.amount_as_of(
            template, _DUE_UNDER_OLD_PRICE,
        ) == _OLD_PRICE
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == Decimal("111.11")

    @pytest.mark.parametrize("status", [
        StatusEnum.DONE, StatusEnum.CANCELLED,
    ])
    def test_a_transfers_status_does_not_decide_which_rule_prices_it(
        self, app, db, seed_user, seed_periods, status,
    ):
        """Leaving Projected does not take a declared transfer's relation back.

        The transfer half of finding **N-262**, and the reason
        ``resolve_transfer_amount`` stopped consulting
        ``balance_predicates.is_projected`` at plan step X-au-c2: a Cancelled
        transfer never enters the settled band, so no freeze writes it a figure,
        and a status-driven OWN arm would refuse the empty column that is left.
        """
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _ = _generated_transfer(
            seed_user, seed_periods[_PAYCHECK_UNDER_OLD_PRICE], savings,
        )
        xfer.status_id = ref_cache.status_id(status)
        db.session.flush()
        assert resolve_transfer_amount(xfer, _basis_for(seed_user)) == _OLD_PRICE


class TestEveryRefusalFires:
    """Each guard, reached and shown to raise.  None of them fires on production."""

    def test_a_template_row_whose_series_is_empty_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """Nobody ever stated a price, so ``default_amount`` is not an answer.

        The scalar carries no time dimension, which is why the refusal is a
        refusal: reading it would price a February row at whatever the template
        says today.
        """
        template = _seriesless_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        with pytest.raises(AmountUnresolvable, match="series is EMPTY"):
            _resolve(seed_user, txn)

    def test_a_row_whose_template_was_deleted_in_session_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A row still naming a definition that is gone gets the arc's refusal.

        The FK is ``ON DELETE SET NULL``, so the database cannot hold this
        pairing -- but a hard template delete leaves identity-mapped rows
        carrying the stale id until the flush, and a review found that state
        raising a bare ``AttributeError`` out of the salary predicate instead.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)
        basis = _basis_for(seed_user)
        txn.template = None
        with pytest.raises(AmountUnresolvable, match="could not be loaded"):
            resolve_transaction_amount(txn, basis)

    def test_an_expense_row_on_a_salary_template_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The classifier and its producer disagree, so no rule answers.

        A REAL data state rather than a hand-emptied basis: the classifier calls
        a template salary-linked whatever its transaction type
        (``is_salary_linked_template`` reads only the profiles), while
        ``income_service.salary_net_for`` takes INCOME rows only.  Such a row is
        claimed by the SALARY rule and answered by nothing -- which is the
        refusal.
        """
        template, _profile = _salary_template(
            seed_user, txn_type=TxnTypeEnum.EXPENSE,
        )
        txn = _template_row(seed_periods[0], template)
        assert amount_rule(txn) is AmountRule.SALARY
        with pytest.raises(AmountUnresolvable, match="live recompute answered nothing"):
            _resolve(seed_user, txn)

    def test_a_generated_transfer_with_no_stated_price_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer-level twin of the empty-series refusal."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Unstated Contribution",
            default_amount=Decimal("250.00"),
        )
        db.session.add(template)
        db.session.flush()
        # A cadence and no price: the engine's transfer is derived and dated,
        # and nothing can then answer for it.
        make_every_period_rule(db.session, template)
        xfer = generate_transfer_of(template, seed_periods[0])
        with pytest.raises(AmountUnresolvable, match="series is EMPTY"):
            resolve_transfer_amount(xfer, _basis_for(seed_user))

    def test_an_own_row_carrying_no_figure_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The resolver's own totality contract: it answers a Decimal or raises.

        Unreachable through the database: ``ck_transactions_amount_ownership``
        is what keeps an OWN row's figure present, so the state below is a row
        written AROUND the CHECK.  ``no_autoflush`` is what makes the RAISE the
        failure and not the constraint: a review found the un-flushed ``None``
        reaching PostgreSQL first and the test passing on an ``IntegrityError``
        instead of on its own assertion.
        """
        txn = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        basis = _basis_for(seed_user)
        with db.session.no_autoflush:
            # The FOURTH shape: ownership stated as neither a figure nor a
            # relation.  Since plan step X-au-k the acts refuse it -- a caller
            # meaning "derived" says so -- so the state is named directly by
            # the bare value object, which is exactly what SQLAlchemy hands a
            # half-built row and what the CHECK refuses to persist.
            txn.amount_ownership = None
            with pytest.raises(
                AmountUnresolvable, match="owns its amount and carries none",
            ):
                resolve_transaction_amount(txn, basis)
            state_own_amount(txn, Decimal("35.00"))

    def test_a_transfer_carrying_no_figure_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The same totality contract on the second column the model covers."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("75.00"),
        )
        with db.session.no_autoflush:
            # The FOURTH shape, on the transfer side: ownership stated as
            # neither a figure nor a relation.  See the transaction twin above
            # for why the bare value object names it since plan step X-au-k.
            xfer.amount_ownership = None
            with pytest.raises(
                AmountUnresolvable, match="owns its amount and carries none",
            ):
                resolve_transfer_amount(xfer, _basis_for(seed_user))
            state_own_amount(xfer, Decimal("75.00"))


class TestTheLoanPaymentRule:
    """The rule production cannot exercise: ``loan_payment_settings`` is empty there."""

    def test_a_derive_mode_payment_answers_the_loans_own_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """P&I + the escrow in effect on the installment's due date.

        Loan $200,000 / 6% / 360mo, escrow $3,600/yr:
            P&I    = amortize(200000, 0.06, 360) = 1,199.10
            escrow = 3,600 / 12                  =   300.00
            PITI                                 = 1,499.10

        The definition says ``$1,300.00`` -- a figure the loan's own answer is
        not.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_a_derive_mode_shadow_answers_the_loans_own_figure``; ruling
        **R-BAL167** class 1): asked of the TRANSFER, whose rule the shadow
        answered.  The sentence above named three figures -- the definition's,
        the parent's ``$1,250.00`` and the shadow's ``$1,200.00`` -- and this
        fixture stores neither of the other two: the payment is not taken for
        an owner, and no shadow is born.
        """
        xfer = _loan_payment(seed_user, derive=True)
        basis = _basis_for(seed_user)
        assert resolve_transfer_amount(xfer, basis) == Decimal("1499.10")

    def test_a_manual_payment_with_no_extra_answers_its_stated_base(
        self, app, db, seed_user, seed_periods,
    ):
        """Manual mode's base is a STATED amount, so its DEFINITION holds it.

        The definition states ``$1,300.00``.  A review found the earlier
        fixture setting every figure equal, which made the test pass for every
        candidate implementation -- and the loan's own P&I of ``$1,199.10`` must
        not appear either.

        Asked of the TRANSFER since plan step balance:X-bi-6-4d-2, whose rule
        the shadow answered (ruling **R-BAL167** class 1).  The sentence above
        also named the parent's ``$1,250.00`` and the shadow's ``$1,200.00``,
        and this fixture stores neither: the payment is not taken for an
        owner, and no shadow is born.
        """
        xfer = _loan_payment(seed_user, derive=False)
        basis = _basis_for(seed_user)
        assert transfer_amount_rule(xfer) is AmountRule.LOAN_PAYMENT
        assert resolve_transfer_amount(xfer, basis) == Decimal("1300.00")

    def test_a_manual_payment_answers_the_same_base_with_and_without_an_extra(
        self, app, db, seed_user, seed_periods,
    ):
        """One mode, one expression: the extra is added, never a different base.

        The defect this test exists for, reproduced by an adversarial review:
        ``live_loan_transfer_amounts`` prices a manual payment from
        ``shadow.estimated_amount + extra``, so a rule that used the live map
        whenever it had an entry answered ``$1,200.00 + $150.00`` with a standing
        extra and ``$1,300.00`` (the series) without one -- the same payment
        priced from two different bases, one of them the stored column ruling
        R-FI deletes.

        **The rival answer is named as a NUMBER now, and plan step X-au-g-2c-2
        is why.**  It used to be produced: the payment was built owning its
        figure, ``LoanPricing.live_cash`` was asked and answered ``$1,350.00``
        from ``shadow.estimated_amount + extra``, and the row was declared
        afterwards so the resolver could be watched ignoring it.  That producer
        is deleted, so what stands is the arithmetic it would have done --
        ``$1,200.00 + $150.00`` -- asserted against the column while the shadow
        still holds one, and then contradicted by the resolver once the shadow
        is derived.  The discrimination is the same and one producer shorter.

        **The rival base is the TRANSFER's stored ``$1,250.00`` since plan
        step balance:X-bi-6-4d-2** -- ruling **R-BAL272**, the developer's
        answer, picked verbatim: "Use $1,250.00 (Recommended)": "The stored
        figure the test names becomes the transfer's own $1,250.00 (a wrong
        answer would be $1,250.00 + $150.00 = $1,400.00). The test then makes
        the payment follow its definition, checks the stored figure is gone,
        and checks the price is still $1,450.00. Same check as before, on the
        one row that can still hold a figure. Listed in the commit."  The
        shadow's ``$1,200.00`` it
        named before went with the shadow, which the database refuses (ruling
        **R-BAL258**).
        """
        xfer = _loan_payment(
            seed_user, derive=False, extra=Decimal("150.00"),
            owns=True,
        )
        basis = _basis_for(seed_user)
        # The stored base a wrong rule would add the extra to: the transfer's
        # own $1,250.00, which would answer $1,250.00 + $150.00 = $1,400.00
        # rather than the definition's $1,300.00 + $150.00.
        assert xfer.amount == Decimal("1250.00")
        _declare_loan_payment_derived(xfer)
        # Declaring it EMPTIES that column, so the rival base is not merely
        # unread -- it is unconstructible.  Asserting the absence says which of
        # the two facts holds; a ``!= 1400`` beside the equality below would be
        # entailed by it and could never fail on its own.
        assert xfer.amount is None
        assert resolve_transfer_amount(xfer, basis) == Decimal("1450.00")

    def test_a_derive_mode_payment_whose_loan_will_not_resolve_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """No loan behind it, so its P&I has no answer and nothing substitutes.

        The destination is an ordinary savings account, so the loan calendar
        (``loan_ledger.LoanCalendars.loan_calendar_of``, ``_resolve_loan_basis``
        until plan step recurrence:R25) answers nothing.  A fallback here would publish
        the stored figure, which on a derive-mode payment is a snapshot of
        exactly the computation that just failed.
        """
        savings = create_savings_account(
            seed_user, db.session, "Not A Loan", Decimal("5000.00"),
        )
        xfer = _loan_payment(
            seed_user, derive=True, to_account=savings,
        )
        basis = _basis_for(seed_user)
        # Asked of the TRANSFER, whose rule the shadow answered, since plan
        # step balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
        with pytest.raises(AmountUnresolvable, match="would not resolve"):
            resolve_transfer_amount(xfer, basis)


def _row_of(xfer) -> DefinitionRow:
    """The four pricing columns a written transfer carries, as the estimate sees them."""
    return DefinitionRow(
        template=xfer.template,
        due_date=xfer.due_date,
        period_start=xfer.pay_period.start_date,
        to_account_id=xfer.to_account_id,
    )


class TestTheDefinitionPriceIsTheRowsOwnRule:
    """:func:`~app.services.cash_ledger.definition_cash`, plan step **R16-b-2** (R-R67).

    The balance seam's ESTIMATED loan tier prices an occurrence no row answers
    yet, and the estimate must be what the row would resolve to -- or the
    loan's payoff moves the moment generation writes the row.  Each test
    prices a WRITTEN transfer twice: through the row's own rule and through
    :func:`definition_cash` over the same four columns, and asserts the two
    equal.  The equality is the property; the arithmetic each case pins is
    the sibling class's, restated so a wrong shared arm cannot pass by
    agreeing with itself.
    """

    def test_a_series_priced_definition_answers_the_rows_rule_3_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Rule 3: the definition's series as of the row's due date, $178.00."""
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, _template = _generated_transfer(
            seed_user, seed_periods[_PAYCHECK_UNDER_OLD_PRICE], savings,
        )
        basis = _basis_for(seed_user)
        row = _row_of(xfer)

        estimate = definition_cash(row, basis, subject="the estimate")

        assert estimate == resolve_transfer_amount(xfer, basis)
        assert estimate == _OLD_PRICE
        assert not is_loan_payment_definition(xfer.template)

    def test_a_derive_mode_loan_payment_answers_the_rows_rule_4_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Rule 4, derive arm: P&I $1,199.10 + escrow $300.00 = $1,499.10."""
        xfer = _loan_payment(seed_user, derive=True)
        basis = _basis_for(seed_user)
        row = _row_of(xfer)

        estimate = definition_cash(row, basis, subject="the estimate")

        assert estimate == resolve_transfer_amount(xfer, basis)
        assert estimate == Decimal("1499.10")
        assert is_loan_payment_definition(xfer.template)

    def test_a_manual_loan_payment_with_an_extra_answers_the_rows_rule_4_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Rule 4, manual arm: the stated $1,300.00 plus the standing $150.00."""
        xfer = _loan_payment(
            seed_user, derive=False, extra=Decimal("150.00"),
        )
        basis = _basis_for(seed_user)
        row = _row_of(xfer)

        estimate = definition_cash(row, basis, subject="the estimate")

        assert estimate == resolve_transfer_amount(xfer, basis)
        assert estimate == Decimal("1450.00")

    def test_an_empty_series_refuses_and_names_the_subject(
        self, app, db, seed_user, seed_periods,
    ):
        """No contract fallback: the estimate refuses exactly where the row would.

        ``standing_installment_cash`` answered the CONTRACT for a definition
        whose series was empty, which this replaces; a figure nobody stated is
        refused, and the message names what was being priced rather than a
        row id it does not have.
        """
        savings = create_savings_account(
            seed_user, db.session, "Never Priced", Decimal("5000.00"),
        )
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Never Stated",
            default_amount=Decimal("42.00"),
        )
        db.session.add(template)
        db.session.flush()
        basis = _basis_for(seed_user)
        row = DefinitionRow(
            template=template, due_date=date(2026, 2, 1),
            period_start=seed_periods[0].start_date,
            to_account_id=savings.id,
        )

        with pytest.raises(AmountUnresolvable, match="price series is EMPTY") as caught:
            definition_cash(row, basis, subject="template 7's occurrence 2026-02-01")
        assert "template 7's occurrence 2026-02-01" in str(caught.value)

    def test_a_derive_mode_definition_with_no_loan_behind_it_refuses(
        self, app, db, seed_user, seed_periods,
    ):
        """The derive arm's refusal fires for an occurrence exactly as for a row."""
        savings = create_savings_account(
            seed_user, db.session, "Not A Loan", Decimal("5000.00"),
        )
        xfer = _loan_payment(
            seed_user, derive=True, to_account=savings,
        )
        basis = _basis_for(seed_user)

        with pytest.raises(AmountUnresolvable, match="would not resolve"):
            definition_cash(_row_of(xfer), basis, subject="the estimate")


class TestTheBatchTier:
    """``amount_basis``, and the merged map that is now derived from it."""

    def test_the_basis_keeps_the_two_derivations_apart(
        self, app, db, seed_user, seed_periods,
    ):
        """Salary answers and loan answers come from their own derivations.

        Which rule prices a row is a fact about the row; merging the two first
        would make it a question about which map the id turned up in, which is
        the discriminator ruling R-FI refuted.

        **The loan payment is asked as its TRANSFER since plan step
        balance:X-bi-6-4d-2** (ruling **R-BAL167** class 1), and one clause
        is gone with no row to ask: ``salary_net_for(shadow, ...) is None``
        asked the salary derivation about the loan payment's shadow, and the
        payment is a transfer now, which that derivation (it takes INCOME
        transaction rows) is never handed.  The claim is narrower by that
        clause.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_periods[0], template,
        )
        xfer = _loan_payment(seed_user, derive=True)
        basis = _basis_for(seed_user)

        # WHICH RULE prices each row is decided by the row, and the two rows
        # reach different rules.  This asked ``basis.loans.live_cash`` on both
        # until plan step X-au-g-2c-2 deleted that method; the dispatch is the
        # thing the assertion was always about, so it is asserted directly.
        # The loan payment's rule is asked of its PARENT since plan step
        # X-au-f-2 (ruling R-BAL10).
        assert amount_rule(paycheck) is AmountRule.SALARY
        assert transfer_amount_rule(xfer) is AmountRule.LOAN_PAYMENT
        # And each derivation answers its own row.  "Apart" also meant that
        # the two do not CROSS -- the salary derivation answered nothing for
        # the loan payment's shadow -- and that clause went with the shadow
        # (the docstring), so this asserts each answers, not that they part.
        assert income_service.salary_net_for(
            paycheck, basis.salary,
        ) != Decimal(_NOT_AN_ANSWER)
        assert resolve_transfer_amount(xfer, basis) == Decimal("1499.10")

    def test_a_basis_answers_for_a_row_it_was_not_built_over(
        self, app, db, seed_user, seed_periods,
    ):
        """A basis is pinned to an OWNER and a SCENARIO, never to a row set.

        This replaces the membership refusal plan step X-au-c2b deleted.  That
        guard existed because a basis stored per-row ANSWERS, so a MISS was
        indistinguishable from a producer's deliberate omission -- an
        adversarial review reproduced a manual loan payment resolved outside its
        own basis answering ``$1,250.00`` where ``$1,400.00`` was right,
        silently dropping a standing ``$150.00`` extra.  A basis holds the
        DERIVATIONS now, so nothing is ever absent: the payment's cash is
        computed from its own config whenever it is asked.  The control is the
        MANUAL-with-extra shape the review used, resolved against a basis built
        before the row existed at all.
        """
        stale = _basis_for(seed_user)
        xfer = _loan_payment(
            seed_user, derive=False, extra=Decimal("150.00"),
        )
        # Built before this payment existed, and it prices it: the definition's
        # $1,300.00 plus the standing $150.00.  The review's failing figure was
        # $1,250.00 -- the parent's stored column with the extra dropped -- and
        # it is named so the assertion cannot pass on a dropped term.  Asked
        # of the TRANSFER, whose rule the shadow answered, since plan step
        # balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
        assert resolve_transfer_amount(xfer, stale) == Decimal("1450.00")
        other = add_txn(
            db.session, seed_user, seed_periods[0], "Fuel", "60.00",
        )
        assert resolve_transaction_amount(other, stale) == Decimal("60.00")

    def test_ONE_map_prices_a_paycheck_a_loan_payment_and_an_owner_together(
        self, app, db, seed_user, seed_periods,
    ):
        """The map a screen reads covers every row, each by its own rule.

        **This case asserted a UNION of two maps until plan step X-au-d.**
        ``live_amounts`` held the read-time repair's answers and
        ``display_amounts_by_id`` laid them over the resolved ones; both
        cutovers deleted the repair (the loan half at X-au-g-2c-2, the salary
        half at X-au-d), so there is one map and the composition has no second
        half left to drop.  What the case still has to show is that the ONE map
        is TOTAL over kinds -- a map that had quietly stopped pricing loan
        payments, or paychecks, would satisfy a per-kind assertion perfectly.

        Three rows, three rules, three distinguishable figures: a declared
        paycheck answered by its profile, a derive-mode loan payment answered
        by the loan, and a paycheck a HUMAN re-priced answered by its own
        column.  The third is what stops the first from passing on a resolver
        that answers rule 2 for every salary-linked row whatever its ownership.

        **The map is the GRID's merged budgets map since plan step
        balance:X-bi-6-4d-2** (ruling **R-BAL167** class 1).  The loan
        payment's two shadow rows sat in ``amounts_by_key`` beside the
        paychecks; no shadow is born, and a screen holds a transfer's LEGS in
        the same map by ``leg.cell_key`` (``amounts_by_key``, since plan
        step ``balance:X-bi-6-1``).  The case asks the GRID's merge,
        ``build_amount_maps``, rather than restating it here.  It is not the
        only spelling: the dashboard pulse builds the same rows-plus-legs map
        inline (``dashboard_service/_pulse.py``), a second home for one
        derived map (CLAUDE.md rule 14), reported at plan step
        balance:X-bi-6-4d-2 rather than moved in this tests-only step.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_periods[0], template,
        )
        repriced = _template_row(
            seed_periods[1], template, owns=True,
        )
        xfer = _loan_payment(
            seed_user, derive=True,
        )
        legs = _legs_of(xfer)
        basis = _basis_for(seed_user)

        shown = build_amount_maps([paycheck, repriced, *legs], basis).budgets

        assert set(shown) == {
            paycheck.id, repriced.id, *(leg.cell_key for leg in legs),
        }
        assert shown[legs[0].cell_key] == Decimal("1499.10")
        assert shown[legs[1].cell_key] == Decimal("1499.10")
        assert shown[repriced.id] == Decimal(_NOT_AN_ANSWER)
        assert shown[paycheck.id] > Decimal("0")
        assert shown[paycheck.id] != Decimal(_NOT_AN_ANSWER), (
            "a DECLARED paycheck is priced by its profile; answering the "
            "figure the row beside it owns would mean the rule read a column"
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
        savings = create_savings_account(
            seed_user, db.session, "Money Market", Decimal("5000.00"),
        )
        xfer, template = _generated_transfer(
            seed_user, seed_periods[_PAYCHECK_UNDER_OLD_PRICE], savings,
        )
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


def _touch(*rows):
    """Refresh *rows* from the database, outside any statement capture.

    ``db.session.commit()`` EXPIRES every instance, so the next attribute read
    emits a PK SELECT.  The query-count controls below are about whether a
    DERIVATION re-runs, and a refresh landing inside their capture would make
    them assertions about SQLAlchemy's expiry policy instead.

    Args:
        rows: The ORM instances to load back.
    """
    for row in rows:
        _ = row.estimated_amount


class TestTheBasisIsOneDerivationPerReadPass:
    """The property plan step X-au-c2b's restructure exists to make structural.

    A basis held per-row ANSWERS for one row SET until that step, so a request
    that loaded two row sets ran the paycheck engine and the loan resolver twice
    -- findings **N-268** and **N-269**, two filings of that one cause.  Holding
    the DERIVATIONS instead makes "one pricing pass per read pass" a property of
    the object rather than a discipline every surface has to remember, and these
    are the controls that say so.

    Each is a QUERY-COUNT assertion, because that is the only thing that can
    tell a memo from a re-derivation: both answer the same figure, which is
    exactly why the duplication went unnoticed long enough to be filed twice.
    The one exception is the no-derivation case below, which reads the two
    memos directly since plan step balance:X-bi-7c (an ordinary row prices
    through its definition now, so "no statement at all" stopped being the
    fact) and bounds the row's own reads beside it.
    """

    def test_no_paycheck_and_no_loan_payment_means_neither_derivation_resolves(
        self, app, db, seed_user, seed_periods,
    ):
        """A row set with neither kind resolves neither derivation.

        The "fast no-op when there are no candidates" property the row-set
        producers had, KEPT rather than traded away for the sharing: both
        derivations are lazy and each half of :func:`live_override` answers
        ``None`` from the row's own columns before it touches one.  Without that
        the salary projection would run on every read pass in the app.

        **The BASIS is built inside the capture, and that is what makes this a
        control.**  Built outside it, an eagerly-loading derivation issues its
        query before the capture opens and this passes anyway -- which a
        mutation check caught it doing: making ``LoanPricing`` load its config
        map in its constructor left the assertion green.
        """
        row = add_txn(db.session, seed_user, seed_periods[0], "Haircut", "35.00")
        db.session.commit()
        # The commit EXPIRED the row, so the first attribute read refreshes it
        # from the database.  Done here rather than inside the capture: that
        # SELECT is SQLAlchemy's, not the derivation's, and counting it would
        # make this assertion about the session rather than about laziness.
        _touch(row)
        # The two pins are read out HERE for the same reason: the commit
        # expired the seeded ``User`` and ``Scenario`` too, and their refresh
        # SELECTs are the session's rather than the amount model's.
        user_id = seed_user["user"].id
        scenario_id = seed_user["scenario"].id

        built = {}

        def _price():
            built["basis"] = derived_amount_basis(user_id, scenario_id)
            return amounts_by_key([row], built["basis"])

        _answer, statements = capture_sql_statements(_price)

        assert _answer == {row.id: Decimal("35.00")}
        # **Graded on the two derivations' own memos since plan step
        # balance:X-bi-7c** (ruling **R-BAL59**, the rule-5 batch).  The three
        # statements are finding **BAL-511** (closed at X-bi-7d-2: the grid read path is flat across the cutover).
        # An ordinary expense row is a ONE-OFF -- a rule-less definition's
        # placed row, TEMPLATE-priced (rule 3) -- so pricing it reads the row's
        # OWN relations: its definition, that definition's price series, and
        # the one salary-profile row rule 2's refinement asks about (measured
        # 2026-09-16: three statements per row loaded bare).  This asserted
        # ``statements == []`` while the row OWNED its figure and rule 1
        # answered from the row alone; what the control has always been about
        # is the two DERIVATIONS of the basis, and neither may have resolved.
        # The memos are read directly because a statement's text cannot tell
        # the refinement's one-profile read from the projection's.
        basis = built["basis"]
        touched = [text for text, _params in statements]
        assert basis.salary._profiles is None, (  # pylint: disable=protected-access -- the derivation's memo IS the fact
            "pricing an ordinary expense row must not resolve the paycheck "
            f"derivation; got {touched}"
        )
        # The loan derivation's memo is the pass's ``LoanCalendars`` since plan
        # step recurrence:R25 (it was ``LoanPricing._loans``); its params memo
        # is the first thing any loan price loads, and the calendar is built
        # over it, so both empty is "no loan resolved".
        terms = basis.loans._terms  # pylint: disable=protected-access -- likewise
        assert not terms._params and not terms._calendars, (  # pylint: disable=protected-access -- likewise
            "pricing an ordinary expense row must not resolve the loan "
            f"derivation; got {touched}"
        )
        # The row's own reads, BOUNDED so growth is seen: the definition, its
        # one salary-profile check, its version series -- finding BAL-511's
        # three, per definition.  A fourth here is a new per-row read.
        assert len(statements) <= 3, touched

    def test_the_salary_projection_runs_ONCE_however_many_row_sets_ask(
        self, app, db, seed_user, seed_periods,
    ):
        """Two row sets, one basis, one paycheck engine run.

        The direct control on **N-268**: the dashboard pulse priced rows the
        cash fold had already priced, and paid a second full projection for it.
        The second ask must issue no statement at all -- not merely fewer.
        """
        template, _profile = _salary_template(seed_user)
        # DERIVED, not owning: an OWN paycheck is answered by rule 1 off its own
        # column and touches no projection at all, so the first ask would issue
        # no statement and this control would measure nothing (plan step
        # X-au-d, where the map became ``amounts_by_key``).
        first = _template_row(
            seed_periods[0], template,
        )
        second = _template_row(
            seed_periods[1], template,
        )
        db.session.commit()
        basis = _basis_for(seed_user)
        _touch(first, second)

        _first, first_statements = capture_sql_statements(
            lambda: amounts_by_key([first], basis),
        )
        _second, second_statements = capture_sql_statements(
            lambda: amounts_by_key([second], basis),
        )

        assert first_statements, "the first ask must resolve the projection"
        assert second_statements == [], (
            "the second row set must answer from the derivation the first "
            f"resolved; got {[text for text, _params in second_statements]}"
        )
        # The ANSWER, not just the query count.  A memo that answers correctly
        # once and empty afterwards satisfies a count assertion perfectly, and
        # an adversarial review of this file caught exactly that omission: the
        # second ask must be the same live net, not merely cheap.
        assert _first[first.id] == _second[second.id] > Decimal("0")

    def test_the_loan_resolve_runs_ONCE_however_many_legs_ask(
        self, app, db, seed_user, seed_periods,
    ):
        """Both legs of one payment, one basis, one loan resolve.

        The direct control on **N-269**: the transfer settle door re-queried the
        transfer and re-resolved the loan for every offered row, because each
        offered row built its own basis.  The two legs of one payment are the
        smallest shape that shows it -- they share the transfer, the config and
        the destination loan, so everything the second ask needs is what the
        first resolved.

        **It asks the RESOLVER since plan step X-au-g-2c-2**, where it asked
        ``basis.loans.live_cash``.  That is a wider claim rather than a
        translation: the memo it grades is still ``LoanPricing._loan``, but the
        path ran through ``amount_rule`` and rule 4, so a second shadow that
        re-read its transfer or re-resolved its template showed up here too.
        Both shadows were ``_touch``-ed first, so the relationship loads were
        not what the count measured.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_the_loan_resolve_runs_ONCE_however_many_shadows_ask``; ruling
        **R-BAL167** class 1).  The two asks are the payment's two LEGS, each
        priced by ``amounts_by_key``, where they were its two shadow rows
        priced by the resolver.  Both legs hold ONE transfer, so the second
        cannot re-read it.  What the empty second count can see is a
        STATEMENT: a loan resolved again, or any explicit query.  The
        definition and its settings are relationships the first ask loaded,
        so reading them again issues none, and this count cannot say whether
        the second ask re-read them.  The legs are built after the commit and
        before either capture, which reads the transfer's columns back where
        ``_touch`` read each row's.
        """
        xfer = _loan_payment(seed_user, derive=True)
        db.session.commit()
        basis = _basis_for(seed_user)
        first, second = _legs_of(xfer)

        _first, first_statements = capture_sql_statements(
            lambda: amounts_by_key([first], basis)[first.cell_key],
        )
        _second, second_statements = capture_sql_statements(
            lambda: amounts_by_key([second], basis)[second.cell_key],
        )

        assert first_statements, "the first ask must resolve the loan"
        assert second_statements == [], (
            "the second leg must answer from the loan already resolved; got "
            f"{[text for text, _params in second_statements]}"
        )
        # The FIGURE: both legs of one payment are priced off its one
        # transfer, so a memo that answered ``None`` the second time would
        # show two figures for one transfer while passing every query-count
        # assertion.
        assert _first == _second == Decimal("1499.10")

    def test_the_settle_freeze_and_the_display_ask_ONE_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """The AMOUNT MODEL is the single rule, where there were two functions.

        ``live_loan_transfer_amounts`` (the display) and
        ``live_loan_payment_amount`` (the settle freeze) were two
        implementations of one rule, the second's docstring stating that it
        "mirrors" the first's candidate filter -- kept in step by hand, which is
        the shape that eventually disagrees.  They became one method
        (``LoanPricing.live_cash``) at plan step X-au-c2b and NO method at plan
        step X-au-g-2c-2: a derived shadow is priced by rule 4, so the settle
        books what the display shows because both ask the resolver.

        The control is unchanged in kind and stronger in reach -- it now spans
        two packages rather than two callers of one method: what the screen
        publishes (``amounts_by_key``) and what a tick would book
        (``transaction_service.settle_amount`` of the leg's transfer, the one
        composition since ruling plan#1983's settle fold deleted
        ``transfer_service.leg_settle_amount``) must be the same figure.
        Both are asked of the checking-side LEG since plan step
        ``balance:X-bi-6-4d-2``, which prices a leg off its transfer and its
        side's record and deleted ``settle_amount(shadow)``; they were asked
        of the checking-side twin row until then (``amounts_by_key``,
        ``settle_amount``), and the figure is unchanged (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        # The payment is the transfer, and the leg is its checking side, where
        # it was the checking-side shadow's transfer and account until plan
        # step balance:X-bi-6-4d-2 (ruling R-BAL167 class 4).
        xfer = _loan_payment(seed_user, derive=True)
        db.session.commit()
        basis = _basis_for(seed_user)
        leg = transfer_legs.leg_of(xfer, xfer.from_account_id)

        displayed = amounts_by_key([leg], basis)[leg.cell_key]
        booked = transaction_service.settle_amount(parent_of(leg), basis)

        assert booked == displayed == Decimal("1499.10")

    def test_one_read_pass_hands_out_the_SAME_basis(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``BalanceContext.amounts()`` memoizes, so a render holds one.

        Identity, not equality: two bases with the same pins ARE equal (the
        derivations are excluded from the comparison, exactly as
        ``BalanceContext``'s own memo caches are), so an equality assertion
        would pass on two objects that each resolve the projection separately.
        """
        ctx = BalanceContext.build(seed_user["user"].id)

        assert ctx.amounts() is ctx.amounts()
        # And that it memoized the RIGHT one.  A defect passing the wrong ids
        # -- ``derived_amount_basis(self.user_id, self.user_id)``, or a hardcoded
        # baseline -- memoizes just as well, and on a single-user single-
        # scenario seed the two ids are often equal, so identity alone cannot
        # see it.  The pins are public fields precisely so this is assertable.
        assert ctx.amounts().user_id == ctx.user_id
        assert ctx.amounts().scenario_id == ctx.scenario_id


class TestABudgetIsNotAContribution:
    """Ruling E-21's base, and the two accessors that answer it.

    A row's BUDGET and what it CONTRIBUTES are different questions, and plan
    step X-au-c2b's reader routing turns on their being different: an
    entry-tracked row's remaining, its over-budget flag and its amount cell all
    answer on the budget, which E-21 fixes on the row's own amount
    unconditionally -- never the entered actual, never status-dependent.  A
    reader handed a contribution instead would answer ``$0.00`` for a Cancelled
    envelope whose budget is still its budget, and would re-base a settled row's
    variance on the very number it is being compared against.
    """

    def test_a_cancelled_envelopes_budget_is_still_its_budget(
        self, app, db, seed_user, seed_periods,
    ):
        """The batch answers the amount; the contribution answers zero.

        Both are correct answers to their own question, which is why the reader
        has to pick.  ``$0.00`` on a Cancelled row is what a balance should
        count it as; it is not what the envelope was budgeted.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Groceries", "400.00",
        )
        txn.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
        db.session.flush()
        basis = _basis_for(seed_user)

        assert amounts_by_key([txn], basis) == {txn.id: Decimal("400.00")}
        assert contributions_by_key([txn], basis) == {txn.id: Decimal("0")}

    def test_an_entered_actual_does_not_move_the_budget(
        self, app, db, seed_user, seed_periods,
    ):
        """What a row RECORDED is what it cost, never what it was budgeted.

        The control on the surprises list: its two terms are the plan and the
        record, so a plan producer that answered the record would make every
        delta zero and the list empty.  This is the row that separates them.

        **The plan half names the RESOLVER since plan step X-bu.**  It read
        ``owned_amount``, the accessor that answered the plan from the row's own
        column -- a second spelling of the question ``resolve_transaction_amount``
        answers, and the pairing this docstring named until that step deleted it
        (finding **BAL-462**).  The contribution half is unchanged, and so are
        both figures: what the pair separates was never the accessor's doing.

        **The row is SETTLED since plan step X-au-c3.**  A figure records a
        settle, so the projected row this used to build -- ``$60.00`` budgeted,
        ``$81.40`` typed -- is a state the CHECKs refuse.  The separation under
        test is unchanged and so are both figures.
        """
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Fuel", "60.00",
            status_enum=StatusEnum.DONE, settled_amount="81.40",
        )
        db.session.flush()
        basis = _basis_for(seed_user)

        assert resolve_transaction_amount(txn, basis) == Decimal("60.00")
        assert settled_contribution(txn) == Decimal("81.40")
        assert amounts_by_key([txn], basis) == {txn.id: Decimal("60.00")}

    def test_a_derived_rows_budget_is_ANSWERED_rather_than_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A row carrying no figure of its own still has a budget.

        **This test asserted the opposite until plan step X-bu, and the
        inversion is the step.**  It read ``owned_amount(txn)`` inside a
        ``pytest.raises`` and was named for the refusal, on the ground that a
        settled-only reader handed a derived row should fail loudly rather than
        subtract a ``None``.  The refusal was real and it FIRED -- on
        ``/analytics/spending``, for every window, on production-shaped data
        (finding **BAL-462**) -- because the accessor was a SECOND answer to a
        question ``resolve_transaction_amount`` already answers, and the two
        part exactly here: on a row an amount-source cutover has declared
        DERIVED.  What the deletion removed is the ACCESSOR -- a public,
        one-token answer to the plan question a reader could take instead of
        asking the amount model -- and NOT the refusal, which survives inside
        the amount model's own arms.  *Plan step X-bx moved that refusal into
        ``cash_ledger._amount_source`` as the private ``_own_figure``, so it is
        no longer reachable by a caller that spells the column read itself:
        ``shekel-private-module-import`` blocks the spelling and the module
        defines no public one.  This paragraph said it WAS reachable, which was
        true when written.*  So the surviving claim is the positive one below.

        **The two assertions guard DIFFERENT things, and an adversarial review
        corrected this paragraph's account of the first.**  It said the second
        alone would pass against a producer that had gone back to reading the
        column; it would not -- ``declare_derived`` EMPTIES the column, so such
        a producer refuses and the second assertion fails, which is exactly what
        a mutation of rule 3 measured.  What ``estimated_amount is None`` guards
        is the FIXTURE: a later change to ``_template_row`` or to the engine's
        row that left a figure on the row would keep the second assertion green
        while it graded nothing at all.
        """
        template = _priced_template(seed_user)
        txn = _template_row(seed_periods[0], template)

        assert txn.estimated_amount is None
        assert amounts_by_key(
            [txn], _basis_for(seed_user),
        ) == {txn.id: _OLD_PRICE}


class TestThePinsAreTheContractNow:
    """The guard that replaced ``priced_ids``, and the axis it protects.

    Deleting the membership set removed a refusal, and an adversarial review of
    this step's own build pointed out that its replacement control proved the
    SAFE direction -- a basis answers for a row it was not built over -- while
    the unsafe one had nothing asserting it.  The unsafe direction is not a
    miss, it is a silently different number: ``LoanPricing`` resolves a loan
    against ITS scenario's payment history, so a foreign basis answers a
    different ``monthly_payment`` and says nothing.

    ``scenario_id`` is a NOT NULL column on every row, so the check is total
    where the membership set was only ever as good as the caller's row list.
    """

    def test_a_row_from_another_scenario_is_REFUSED(
        self, app, db, seed_user, seed_periods,
    ):
        """The pins are checked, and the refusal names both scenarios."""
        txn = add_txn(
            db.session, seed_user, seed_periods[0], "Haircut", "35.00",
        )
        db.session.flush()
        foreign = derived_amount_basis(
            seed_user["user"].id, seed_user["scenario"].id + 1,
        )

        with pytest.raises(AmountUnresolvable, match="prices scenario"):
            resolve_transaction_amount(txn, foreign)

    def test_the_batch_refuses_the_same_way(
        self, app, db, seed_user, seed_periods,
    ):
        """``amounts_by_key`` inherits it: no row is priced past the pins.

        The batch has no status gate above the resolve, so it is the surface a
        cross-scenario row reaches first on a page that loads every status.
        """
        txn = add_txn(db.session, seed_user, seed_periods[0], "Fuel", "60.00")
        db.session.flush()
        foreign = derived_amount_basis(
            seed_user["user"].id, seed_user["scenario"].id + 1,
        )

        with pytest.raises(AmountUnresolvable, match="prices scenario"):
            amounts_by_key([txn], foreign)


class TestOneRowHasOneDisplayedFigure:
    """Every surface shows a row's amount by ONE rule (``amounts_by_key``).

    An adversarial review found that rule written twice and differently: the
    grid merged the seam's live-override map over its resolved one, while every
    HTMX fragment and the companion view published the resolved map ALONE under
    the same context key.  So a projected salary row whose profile had moved
    past its cached column showed the live net on the grid and the stale column
    in the quick-edit box the same click opened -- and that box is what a save
    posts back from, which is how a figure nobody saw gets booked.

    **The composition that defect was in is GONE as of plan step X-au-d**, and
    the class stays because the property does not: a screen and a balance still
    have to show one figure per row.  What makes that structural now is that
    the second answer has no home -- a declared row stores no column for a map
    to disagree with.
    """

    def test_a_declared_paycheck_displays_its_profiles_net(
        self, app, db, seed_user, seed_periods,
    ):
        """A paycheck the definition prices shows what the definition pays.

        The discriminator is the ROW's own column: it is empty, so a resolver
        that read it would answer ``None`` into a money map rather than a
        figure.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_periods[0], template,
        )
        basis = _basis_for(seed_user)

        displayed = amounts_by_key([paycheck], basis)[paycheck.id]

        assert paycheck.estimated_amount is None
        assert displayed > Decimal("0")

    def test_a_paycheck_a_HUMAN_repriced_displays_the_typed_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Ownership decides, not the template's kind (finding **N-262**).

        The partner the case above needs: without it, a resolver answering
        rule 2 for every salary-linked row whatever its ownership would pass --
        and that resolver overwrites the figure a human typed, on the screen
        that same human types it into.
        """
        template, _profile = _salary_template(seed_user)
        paycheck = _template_row(
            seed_periods[0], template, owns=True,
        )
        basis = _basis_for(seed_user)

        assert amounts_by_key([paycheck], basis) == {
            paycheck.id: Decimal(_NOT_AN_ANSWER),
        }

    def test_an_ordinary_row_displays_what_it_resolves_to(
        self, app, db, seed_user, seed_periods,
    ):
        """The non-salary, non-derived case, so the map is total over kinds."""
        txn = add_txn(db.session, seed_user, seed_periods[0], "Rent", "1200.00")
        db.session.flush()
        basis = _basis_for(seed_user)

        assert amounts_by_key([txn], basis) == {txn.id: Decimal("1200.00")}


class TestPricingReadsNoSTATUS:
    """Rules 2 and 4 answer whatever a row's status is, and that is the point.

    Plan step X-au-c2b's headline behaviour change, and an adversarial review of
    this file found it graded by NO case: every salary and loan fixture here is
    Projected, so planting ``if not is_projected(txn): return None`` at the top
    of either rule failed nothing.  The maps those rules used to index were
    built by the read-time REPAIR, which filters to Projected non-overridden
    rows -- so a Cancelled paycheck was refused for a reason that has nothing to
    do with what a paycheck is worth, and a Cancelled loan payment was refused
    as though its LOAN would not resolve, which is a different and alarming
    statement.

    Finding **N-262**'s rule one tier down: status says whether a row COUNTS,
    never what prices it.  ``routes/grid/page.py`` prices every loaded row with
    no status predicate, and this file records that production carries 7
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
        """Rule 4 resolves the loan for a shadow no balance is counting.

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


def _clock_calls_in(package_dir):
    """Return ``["<path>:<line>: <expression>", ...]`` for every clock call under it.

    An AST walk rather than a grep: a grep for ``date.today`` matches the word
    inside a docstring, and this package's docstrings discuss the clock read it
    no longer makes at length.  Only a ``Call`` node whose called NAME is a
    clock producer counts.

    Args:
        package_dir: A :class:`pathlib.Path` to walk (recursively).

    Returns:
        One human-readable location string per clock call found, in path order.
    """
    # Pylint: import-outside-toplevel -- the file-wide test convention.
    import ast  # pylint: disable=import-outside-toplevel

    found = []
    for path in sorted(package_dir.rglob("*.py")):
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
    directory, which the first cannot, since it walks a different one.

    The shapes this does not catch are enumerated on :data:`_CLOCK_PRODUCERS`,
    including the transitive one an adversarial review used to slip a wall-clock
    branch past it.
    """

    @staticmethod
    def _cash_ledger_dir():
        """Return the ``app/services/cash_ledger`` package directory."""
        # Pylint: import-outside-toplevel -- the file-wide test convention.
        import pathlib  # pylint: disable=import-outside-toplevel

        import app.services.cash_ledger as package  # pylint: disable=import-outside-toplevel

        return pathlib.Path(package.__file__).parent

    def test_the_walker_can_see_a_clock_call(self):
        """The walker finds a clock call where one is known to be.

        Without this the assertion below is satisfied by a walker that parses
        nothing: an empty list is what "no clock" and "measured nothing" both
        look like.  ``loan_payment_service._context`` resolves the loan CARD's
        escrow at ``date.today()`` -- legitimately, because the card answers
        what the owner pays THIS month -- so it is a stable positive.
        """
        # Pylint: import-outside-toplevel -- the file-wide test convention.
        import pathlib  # pylint: disable=import-outside-toplevel

        import app.services.loan_payment_service as package  # pylint: disable=import-outside-toplevel

        found = _clock_calls_in(pathlib.Path(package.__file__).parent)
        assert found, (
            "the walker found no clock call in loan_payment_service, where the "
            "loan card's escrow is resolved at date.today() -- so it is "
            "measuring nothing and the absence it reports elsewhere is empty"
        )

    def test_the_scan_reaches_this_package(self):
        """The cash_ledger scan reads real files, and enough of them.

        The positive control above walks ``loan_payment_service`` -- a
        DIFFERENT directory -- so it cannot tell whether the scan of THIS one
        read anything.  Without this, "no clock in the amount model" and
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
