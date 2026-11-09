"""
Shekel Budget App -- Action-card status-dropdown pre-hint tests.

Grid audit D2 (ruled 2026-07-11, both halves): besides the designed
error fragments, the action cards' status dropdowns disable options the
state machine would reject from the row's CURRENT status, so an illegal
transition cannot be picked instead of failing after Save.  These tests
pin the disabled-option rendering per card; the legality table itself is
covered by ``tests/test_services/test_state_machine.py``.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.ref import AccountType, TransactionType
from app.services import account_service, status_seam, transfer_service
from tests._test_helpers import settlement_if_settling, one_off_row_of
from app.models.amount_ownership import AmountOwnership


def _create_expense(seed_user, seed_periods_today, *, is_envelope=False):
    """Insert a projected one-off expense for a full-edit GET."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    txn = one_off_row_of(
        seed_periods_today[0],
        name="Prehint Expense",
        amount=Decimal("42.00"),
        user_id=seed_periods_today[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
        is_envelope=is_envelope,
    )
    db.session.commit()
    return txn


def _option_tag(html, status_id):
    """Return the ``<option>`` tag chunk for *status_id* from *html*.

    Splitting on ``<option`` keeps each option's attributes in one
    chunk regardless of attribute order or line wrapping, so the
    assertions below cannot false-positive on a neighbouring option.
    """
    for chunk in html.split("<option")[1:]:
        tag = chunk.split(">", 1)[0]
        if f'value="{status_id}"' in tag:
            return tag
    raise AssertionError(f"no option for status id {status_id} in fragment")


class TestTransactionCardPreHint:
    """The transaction card's dropdown disables illegal transitions."""

    def test_paid_disables_cancelled_enables_projected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """From Paid: Cancelled is disabled, Projected stays selectable.

        The pre-hint's two halves on one card -- a move the state machine
        refuses renders ``disabled`` with the reason in its ``title``, and the
        revert beside it does not.

        It was written from PROJECTED, where ``Settled`` was the one disabled
        option.  Plan step **balance:X-am** deleted that status, and Projected
        now reaches every status the enum has -- so a from-Projected card has
        nothing disabled to assert, and the case moves to the row state that
        does.  ``test_projected_offers_every_status_but_credit`` below is the other half.
        """
        with app.app_context():
            txn = _create_expense(seed_user, seed_periods_today)
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            status_seam.apply_status_change(
                txn, paid_id,
                settlement=settlement_if_settling(txn, paid_id),
            )
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            cancelled_tag = _option_tag(
                html, ref_cache.status_id(StatusEnum.CANCELLED),
            )
            assert "disabled" in cancelled_tag
            assert "Not reachable from Paid" in cancelled_tag

            projected_tag = _option_tag(
                html, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            assert "disabled" not in projected_tag

    def test_projected_offers_every_status_but_credit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """From Projected, no option is disabled but the type mismatch and Credit.

        The other half of the pre-hint, and it is the assertion plan step
        **balance:X-am** makes possible: Projected reaches every status but
        Credit, so what is greyed on an EXPENSE card is ``Received`` -- which
        the state machine allows and ``transaction_service.offerable_status_ids``
        removes, because an expense settles as Paid -- and ``Credit``, which
        no row enters since plan task plan#3057 (finding plan#2133: the
        Credit button puts a row on the card).  Only Received until then.

        Asserting the whole set rather than one option is what catches a status
        added to the enum with nobody deciding whether Projected may reach it.
        """
        with app.app_context():
            txn = _create_expense(seed_user, seed_periods_today)

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            disabled = {
                member.value for member in StatusEnum
                if "disabled" in _option_tag(
                    html, ref_cache.status_id(member),
                )
            }
            assert disabled == {"Received", "Credit"}, (
                f"expense card disables {sorted(disabled)}; expected only the "
                "type-mismatched settled status and Credit"
            )

    def test_envelope_row_disables_credit_option(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A purchase-tracking row's Credit option is disabled.

        Credit is per-entry on tracked rows, and the dropdown says so (plan
        task plan#3057: no row enters Credit, so the option is disabled on
        every row not drawn Credit; a projected expense's title points to the
        Credit button instead, and every other row's is the generic one).
        """
        with app.app_context():
            txn = _create_expense(
                seed_user, seed_periods_today, is_envelope=True,
            )

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            credit_tag = _option_tag(
                html, ref_cache.status_id(StatusEnum.CREDIT),
            )
            assert "disabled" in credit_tag
            assert "per purchase entry" in credit_tag


class TestTransferCardPreHint:
    """The transfer card's dropdown excludes transaction-only statuses."""

    def test_transfer_disables_credit_and_received(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Credit and Received are disabled on a transfer's dropdown.

        The transfer transition map excludes both statuses entirely
        (credit/payback is expense-only; Received is an income display
        convention), so they are never selectable regardless of the
        current status.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Prehint Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)
            db.session.flush()
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods_today[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("75.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                    name="Prehint Transfer",
                ),
            )
            db.session.commit()

            resp = auth_client.get(f"/transfers/{xfer.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            for member in (StatusEnum.CREDIT, StatusEnum.RECEIVED):
                tag = _option_tag(html, ref_cache.status_id(member))
                assert "disabled" in tag

            done_tag = _option_tag(html, ref_cache.status_id(StatusEnum.DONE))
            assert "disabled" not in done_tag
