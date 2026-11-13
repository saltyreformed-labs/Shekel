"""The delete door on a rule-less definition's row: hard, and the dialog says so; the definition
goes with its last row, and the delete is refused while a standing merchant rule names it.
"""

from app.extensions import db
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from tests._test_helpers import generate_row_of, make_expense_template

from ._helpers import _one_off, _recurring


def _rule_naming(seed_user, template):
    """State a standing merchant rule filing a merchant's spending into *template*."""
    merchant = Merchant(account_id=template.account_id, name="Amazon")
    db.session.add(merchant)
    db.session.flush()
    db.session.add(MerchantRule(
        user_id=seed_user["user"].id,
        account_id=template.account_id,
        merchant_id=merchant.id,
        template_id=template.id,
        never_a_purchase=False,
    ))
    db.session.commit()


class TestDelete:
    """``_delete._leaves_the_books`` keys soft-versus-hard on ``recurs``."""

    def test_a_rule_less_definitions_last_row_takes_the_definition_with_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The row goes, and so do the definition and its series (R-BAL23, R-BAL27).

        A one-off is a rule-less definition plus its placed row; with the row
        gone the definition defines nothing, so ``delete_transaction`` disposes
        of it through ``definition_delete.permanently_delete_definition`` --
        the one act the definition's own door and the account door call.  The
        ``template_amount_versions`` row goes with it (delete-orphan), which
        is what "the definition goes" has to mean for a series to have one
        home.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            row_id = one_off.id
            template_id = one_off.template_id
            assert db.session.query(TemplateAmountVersion).filter_by(
                transaction_template_id=template_id,
            ).count() == 1

            resp = auth_client.delete(f"/transactions/{row_id}")

            assert resp.status_code == 200
            assert db.session.get(Transaction, row_id) is None
            assert db.session.get(TransactionTemplate, template_id) is None
            assert db.session.query(TemplateAmountVersion).filter_by(
                transaction_template_id=template_id,
            ).count() == 0

    def test_a_row_of_a_definition_holding_another_row_leaves_it_standing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Not the last row: the row goes hard, the definition and its sibling stay.

        The shape a bank-born envelope takes across paychecks (R-BAL24), and
        a cleared cadence's survivors today.  **The sibling counted here is
        SOFT-DELETED**, on purpose: a soft-deleted one-off is still its
        definition's (10.8), and a predicate that skipped tombstones would
        dispose of the definition under one -- a TEMPLATE-priced row priced
        by nothing, ruling R-JE's state.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="20.00", name="Two Rows",
                category_key="Groceries",
            )
            first = generate_row_of(template, seed_periods_today[0])
            second = generate_row_of(template, seed_periods_today[1])
            template.recurrence_rule = None
            second.is_deleted = True
            db.session.commit()
            first_id, second_id, template_id = first.id, second.id, template.id
            assert first.recurs is False

            resp = auth_client.delete(f"/transactions/{first_id}")

            assert resp.status_code == 200
            assert db.session.get(Transaction, first_id) is None
            assert db.session.get(Transaction, second_id) is not None
            assert db.session.get(TransactionTemplate, template_id) is not None

    def test_the_last_row_is_refused_while_a_merchant_rule_names_the_definition(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL23: the delete would cascade an owner's stated answer away.

        ``fk_merchant_rules_template_account`` is ON DELETE CASCADE, so the
        row's delete -- which disposes of the definition -- is refused with
        the sentence, on the card (the control is withheld and the sentence
        printed) and at the door (a designed 400); row, definition and rule
        all stand.  A rule is never un-stated (R-GS), so restating it is what
        frees the row.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            row_id, template_id = one_off.id, one_off.template_id
            _rule_naming(seed_user, one_off.template)
            rule_count = db.session.query(MerchantRule).count()

            card = auth_client.get(f"/transactions/{row_id}/full-edit")
            assert card.status_code == 200
            html = card.data.decode()
            assert "where a merchant&#39;s bank spending goes" in html
            assert "Delete this row" not in html

            resp = auth_client.delete(f"/transactions/{row_id}")

            assert resp.status_code == 400, resp.data
            assert b"bank spending goes" in resp.data
            assert db.session.get(Transaction, row_id) is not None
            assert db.session.get(TransactionTemplate, template_id) is not None
            assert db.session.query(MerchantRule).count() == rule_count

    def test_a_merchant_rule_does_not_refuse_a_row_that_is_not_the_last(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The firing control for the arm above: nothing is disposed of, so nothing cascades."""
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="20.00", name="Two Rows",
                category_key="Groceries",
            )
            first = generate_row_of(template, seed_periods_today[0])
            generate_row_of(template, seed_periods_today[1])
            template.recurrence_rule = None
            db.session.commit()
            _rule_naming(seed_user, template)
            first_id = first.id

            resp = auth_client.delete(f"/transactions/{first_id}")

            assert resp.status_code == 200, resp.data
            assert db.session.get(Transaction, first_id) is None
            assert db.session.query(MerchantRule).count() == 1

    def test_a_recurring_definitions_row_is_still_soft_deleted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the tombstone the engine reads is kept where a rule stands."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            resp = auth_client.delete(f"/transactions/{recurring.id}")
            assert resp.status_code == 200
            db.session.refresh(recurring)
            assert recurring.is_deleted is True

    def test_the_dialog_promises_what_the_door_will_do(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One sentence per arm, and it is the SERVICE's arm (``preview.soft``).

        The dialog read the row's link until this step; keyed there it would
        have told the owner of a one-off that *the recurring transaction
        behind it keeps generating the others* over a row about to be gone
        for good.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            recurring = _recurring(seed_user, seed_periods_today)
            html = auth_client.get(
                f"/transactions/{one_off.id}/full-edit",
            ).data.decode()
            assert "This cannot be undone." in html
            assert "This occurrence stays deleted" not in html
            # A one-off's ONLY row takes the item with it (R-BAL27), and the
            # dialog says so off the same answer the door acts on.
            assert "the item itself goes with it" in html

            html = auth_client.get(
                f"/transactions/{recurring.id}/full-edit",
            ).data.decode()
            assert "This occurrence stays deleted" in html
            # Ruling R-CC83: the un-archive that brings it back, empty (R-CC75),
            # in the words ruling R-CC112 gave it (developer 2026-09-24): the
            # exception un-archive makes for a row the books have moved over.
            assert (
                "Archiving and then un-archiving that item would bring it "
                "back, empty, unless the books have moved over it by then; "
                "the un-archive names any row it keeps deleted." in html
            )
            assert "This cannot be undone." not in html
            assert "the item itself goes with it" not in html

    def test_the_dialog_does_not_promise_the_item_while_another_row_stands(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL for the pair sentence: a two-row definition keeps the item."""
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="162.25", name="Kayla's Kindle",
                category_key="Groceries",
            )
            first = generate_row_of(template, seed_periods_today[0])
            generate_row_of(template, seed_periods_today[1])
            template.recurrence_rule = None
            db.session.commit()
            assert first.is_placed is True
            html = auth_client.get(
                f"/transactions/{first.id}/full-edit",
            ).data.decode()
            assert "This cannot be undone." in html
            assert "the item itself goes with it" not in html
