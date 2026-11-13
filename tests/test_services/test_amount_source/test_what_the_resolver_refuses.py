"""What the resolver refuses: each guard reached and shown to raise, and a basis's scenario pins,
which replaced the membership set.
"""

from decimal import Decimal

import pytest

from app.enums import TxnTypeEnum
from app.exceptions import AmountUnresolvable
from app.models.transfer_template import TransferTemplate
from app.services.amount_ownership import state_own_amount
from app.services.cash_ledger import (
    AmountRule,
    derived_amount_basis,
    amount_rule,
    amounts_by_key,
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from tests._test_helpers import (
    add_txn,
    create_savings_account,
    create_transfer,
    generate_transfer_of,
    make_every_period_rule,
)

from ._helpers import (
    _basis_for,
    _resolve,
    _priced_template,
    _seriesless_template,
    _salary_template,
    _template_row,
)


class TestEveryRefusalFires:
    """Each guard, reached and shown to raise.  None of them fires on production."""

    def test_a_template_row_whose_series_is_empty_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """Nobody ever stated a price, so no scalar copy is an answer.

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
            seed_user, seed_user["account"], savings,
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
