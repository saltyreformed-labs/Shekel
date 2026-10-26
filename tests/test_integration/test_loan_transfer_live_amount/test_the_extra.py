"""A loan payment's extra: read off its definition's own settings row, included in a derived price,
added to a manual payment's base, and absent where none is set.
"""

from decimal import Decimal

from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.models.transfer_template import TransferTemplate
from app.services import transfer_recurrence
from app.services import escrow_calculator
from app.services.loan_loaders import load_escrow_lines, loan_payment_due_date
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurring_transfer_query import loan_payment_config

from ._helpers import _derived_cash, _state_price, _build_derived_loan_transfer, _loan_payment_legs


def test_a_definitions_standing_extra_is_read_off_its_own_settings_row(
    app, db, seed_user,
):
    """A recurring payment's extra is ITS settings row's, 0.00 without one.

    Read per DEFINITION through ``loan_payment_config`` (plan step R7d-g-3):
    0.00 before an extra is set, the settings value after, and 0.00 for a
    definition with no settings row at all.  The loan-level reader this
    graded, ``loan_standing_extra``, went at that step with the payoff
    composer's ``extra_principal`` it fed (ruling **R-R88**, which re-ruled R-R83's seam clause
    there): the extra rides inside each generated row's cash (amount rule 4),
    and no loan-level figure threads it any more.
    """
    with app.app_context():
        loan, _escrow, _scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        db.session.commit()

        assert loan_payment_config(template) == (True, Decimal("0.00"))

        template.settings.extra_principal = Decimal("250.00")
        db.session.commit()
        assert loan_payment_config(template) == (True, Decimal("250.00"))

        # A definition with no settings row: not a loan payment, no extra.
        plain = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan.id,
            name="No settings row",
            default_amount=Decimal("10.00"),
        )
        db.session.add(plain)
        db.session.flush()
        assert loan_payment_config(plain) == (False, Decimal("0.00"))


# ── Overpayment (step 5): the standing extra rides both modes' cash ──────────


def test_derived_override_includes_standing_extra(
    app, db, seed_user, seed_periods,
):
    """A derive-mode override is P&I + escrow + the standing extra_principal.

    Loan $200,000 / 6% / 360mo, escrow $3,600/yr; extra_principal $100.00:
        P&I    = amortize(200000, 0.06, 360) = 1,199.10
        escrow = 3600 / 12                   = 300.00
        cash   = 1,199.10 + 300.00 + 100.00  = 1,599.10
    The extra is a LIVE parameter added on top; it is NOT baked into the stale
    stored $1.00, which stays untouched.  It prices the payments' legs, where
    it priced the shadow rows until plan step balance:X-bi-6-4d-2 (ruling
    R-BAL167 class 1).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.extra_principal = Decimal("100.00")
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        overrides = _derived_cash(
            seed_user, _loan_payment_legs(loan.id, scenario_id),
        )
        assert overrides
        assert all(v == Decimal("1599.10") for v in overrides.values())


def test_manual_payment_with_extra_gets_base_plus_extra(
    app, db, seed_user, seed_periods,
):
    """A MANUAL payment (not derive) with a standing extra overrides to base + extra.

    Manual base (stored default) $1,499.10 + extra $100.00 = $1,599.10.  The
    base is operator-owned (not re-derived); only the extra is added live.
    It prices the payments' legs, where it priced the shadow rows until plan
    step balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        # Flip to manual mode with a realistic typed base + a standing extra.
        template.settings.derive_from_loan = False
        template.settings.extra_principal = Decimal("100.00")
        # The manual BASE is a STATED amount, so its definition holds it in the
        # SERIES (plan step X-au-a) rather than in the ``default_amount``
        # scalar.  Setting only the scalar priced this payment at the fixture's
        # $1.00 + the extra once the parent started reading its definition --
        # $101.00 against the $1,599.10 below, which is the figure this case
        # has always asserted and which is unchanged.
        template.default_amount = Decimal("1499.10")
        _state_price(template, Decimal("1499.10"))
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        overrides = _derived_cash(
            seed_user, _loan_payment_legs(loan.id, scenario_id),
        )
        assert overrides
        assert all(v == Decimal("1599.10") for v in overrides.values())


# ``test_manual_extra_keys_to_recurring_base_not_a_typed_actual`` lived here
# until plan step X-au-c3, and the DISCRIMINATOR it needed no longer exists.
# It put an operator-typed ``actual_amount`` on a PROJECTED, non-override loan
# shadow -- the one state where ``estimated_amount`` and the row's CONTRIBUTION
# gave different answers -- and proved ``_manual_shadow_amount`` keys the
# standing extra to the recurring base rather than to the per-instance figure.
#
# A figure RECORDS a settle now, so the seam makes that row unconstructible
# (``reject_settlement_without_settled_status``), and ``LoanPricing.live_cash`` gates on
# ``is_projected`` -- so no row this producer can see carries a settled figure at
# all.  The two expressions therefore answer the same number for every
# constructible input, and a test written against the difference cannot fail,
# which is not a test (finding **N-184**'s rule).  What survives is the extra
# riding the recurring base, which
# ``test_manual_payment_with_extra_gets_base_plus_extra`` above grades on the
# row shape that is still reachable.


def test_manual_payment_without_extra_gets_no_override(
    app, db, seed_user, seed_periods,
):
    """A MANUAL payment with no extra is worth its DEFINITION's base.

    Nothing to re-derive and no extra to add, so the base the operator owns IS
    the cash.  ``$1,499.10`` here, and the loan's own contractual installment
    happens to be the same figure -- which is what makes the SECOND assertion
    load-bearing: the escrow is raised afterwards, and a manual payment must NOT
    follow it.  Without that, this case would pass just as well on a rule that
    had quietly started deriving from the loan.

    **It asserted an EMPTY map until plan step X-au-g-2c-2** -- the read-time
    override was dormant for this shape, so the row simply did not appear.
    Absence could not tell "no override" from "not priced at all"; the row is
    priced now, by rule 4's manual arm, and the figure is stated.

    It prices the payments' legs, and the probe keys the escrow by each leg's
    installment date, where both read the shadow rows until plan step
    balance:X-bi-6-4d-2 (ruling R-BAL167 class 1; ``loan_payment_due_date``
    takes a leg).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.derive_from_loan = False
        template.settings.extra_principal = Decimal("0.00")
        template.default_amount = Decimal("1499.10")
        # The manual base is the definition's SERIES (see the sibling case).
        _state_price(template, Decimal("1499.10"))
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        legs = _loan_payment_legs(loan.id, scenario_id)
        assert legs, "expected generated loan payments"
        resolved = _derived_cash(seed_user, legs)
        assert all(v == Decimal("1499.10") for v in resolved.values())

        # Raise the escrow: a DERIVE payment would move to $1,599.10 and a
        # manual one must not, which is what says the base is the operator's.
        # The VERSION carries the figure -- ``EscrowLine`` has no
        # ``annual_amount`` at all, so writing one there sets an unmapped
        # attribute and this case would "pass" without the escrow moving.
        version = (
            db.session.query(EscrowComponentVersion)
            .join(EscrowLine, EscrowLine.id == EscrowComponentVersion.line_id)
            .filter(EscrowLine.account_id == loan.id)
            .one()
        )
        version.annual_amount = Decimal("4800.00")
        db.session.commit()

        # The control on the control: the raise really took effect, so the
        # assertion below is about the RULE and not about a write that never
        # happened.  Measured over the axis the RULE reads -- each payment's own
        # installment date (the derive arm resolves the escrow on
        # ``loan_payment_due_date``) -- and not a date chosen here.  A hard-coded
        # date coincides only while the write mutates the standing version in
        # place; converting it to "append a later version", which is the shape
        # ``add_escrow_line`` itself produces, would leave the probe green while
        # the payments read the old figure and this case went back to proving
        # nothing.
        lines = load_escrow_lines(loan.id)
        assert {
            escrow_calculator.escrow_monthly_as_of(
                lines, loan_payment_due_date(leg, 1),
            )
            for leg in legs
        } == {Decimal("400.00")}

        after = _derived_cash(seed_user, legs)
        assert all(v == Decimal("1499.10") for v in after.values())


def test_a_derive_payment_on_the_same_fixture_does_follow_the_escrow(
    app, db, seed_user, seed_periods,
):
    """The other half of the manual case's discrimination, asserted not argued.

    ``test_manual_payment_without_extra_gets_no_override`` above proves a manual
    payment does not follow an escrow raise, and its probe proves the raise took
    effect -- but "a DERIVE payment WOULD have moved to $1,599.10" was left as an
    inference from ``_shadow_live_amount``'s formula rather than measured.  An
    adversarial review of this step asked for the pair, and it is cheap: the same
    fixture, the same raise, ``derive_from_loan`` left ON.

    P&I 1,199.10 + the raised escrow 400.00 = 1,599.10.

    **It graded NOTHING from the checkpoint of plan step balance:X-bi-6-4d-2
    that deleted the shadows (revision ``82f7b7ed4332``) until it was
    re-expressed** (CLAUDE.md rule 4; ruling R-BAL167 class 1).
    Its row set was every shadow in the scenario, none is born since that
    step, so both ``all()`` reads ran over an empty map and passed --
    measured: zero rows, an empty map.  It prices the payments' LEGS now, and
    each read is guarded non-empty, so a derive arm that stopped following
    the escrow fails here.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.default_amount = Decimal("1499.10")
        # The manual base is the definition's SERIES (see the sibling case).
        _state_price(template, Decimal("1499.10"))
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        legs = _loan_payment_legs(loan.id, scenario_id)
        assert legs, "expected generated loan payments"
        before = _derived_cash(seed_user, legs)
        assert before
        assert all(v == Decimal("1499.10") for v in before.values())

        version = (
            db.session.query(EscrowComponentVersion)
            .join(EscrowLine, EscrowLine.id == EscrowComponentVersion.line_id)
            .filter(EscrowLine.account_id == loan.id)
            .one()
        )
        version.annual_amount = Decimal("4800.00")
        db.session.commit()

        after = _derived_cash(seed_user, legs)
        assert after
        assert all(v == Decimal("1599.10") for v in after.values())
