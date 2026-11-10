"""Account route tests: what a hard delete refuses over definitions and movements."""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.extensions import db
from app.models.account import Account
from app.utils import archive_helpers
from app.utils.dates import display_today
from tests._test_helpers import (
    create_account_of_type,
    generate_row_of,
    make_expense_template,
    typed,
)
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service


class TestHardDeleteAndTheAccountsDefinitions:
    """Guard 3 counts RECURRING definitions; a rule-less one goes with the account.

    Plan step ``balance:X-bi-7a`` (ruling **R-BAL23**: the account-delete
    refusal counts definitions with a rule, so an account holding only a
    one-off archives as it does today; the disposal of a row-less rule-less
    definition ruled by the developer 2026-09-13).  Until this step guard 3
    refused on ANY definition, so the refusal named, for a one-off, a
    "recurring transaction" the Recurring list will not show once the doors
    leaf lands -- and, re-keyed alone, a rule-less definition with no row
    would have passed every guard and hit ``transaction_templates``'
    RESTRICT key as a 500.  Each case makes the definition rule-less the way
    the edit door does: the engine's row under a cadence, then the cadence
    cleared.
    """

    def _fresh_account(self, seed_user, name):
        """A checking account opened at `$0.00`, so it posts no anchor leg.

        Guard 5 archives an account holding ANY posting, and a non-zero
        opening posts one at creation -- so an account that is to reach the
        definition guards and the permanent delete has to open empty.
        """
        account = create_account_of_type(
            seed_user, db.session, "Checking", name,
            anchor_balance=Decimal("0.00"),
        )
        db.session.commit()
        return account

    def test_a_recurring_definition_still_refuses(
        self, app, auth_client, seed_user,
    ):
        """THE CONTROL: a definition with a rule blocks the delete, as before."""
        with app.app_context():
            account = self._fresh_account(seed_user, "Has A Rule")
            make_expense_template(
                db.session, seed_user, name="Gym", account=account,
            )
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{account.id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Delete those recurring transactions first" in response.data
            kept = db.session.get(Account, account.id)
            assert kept is not None and kept.is_active is True

    def test_a_rule_less_definition_holding_a_row_archives_instead(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An account whose only history is a one-off archives, as an ad-hoc row does."""
        with app.app_context():
            from app.models.transaction_template import (  # pylint: disable=import-outside-toplevel
                TransactionTemplate,
            )
            account = self._fresh_account(seed_user, "One-Off Only")
            template = make_expense_template(
                db.session, seed_user, name="Kindle", account=account,
            )
            row = generate_row_of(template, seed_periods_today[0])
            template.recurrence_rule = None
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{account.id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"has transaction history" in response.data
            archived = db.session.get(Account, account.id)
            assert archived is not None and archived.is_active is False
            assert db.session.get(Transaction, row.id) is not None
            assert db.session.get(TransactionTemplate, template.id) is not None

    def test_a_rule_less_definitions_row_on_another_account_archives_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The history predicate sees a row UNDER a definition, wherever it sits.

        A row can sit on another account than its definition's -- an
        overridden, a settled or a soft-deleted row stays put when the
        definition's account moves (the maintain pass rewrites only a
        Projected, un-edited row; through ``credit_card:CC-5-1`` it also
        retained a row holding records, an arm ruling **R-CC36** retired) --
        so the definition's account can hold no row of its own.  Deleting
        that definition would ``SET NULL`` the row's link and leave a
        TEMPLATE-priced row with nothing to price it; guard 4 sees the row
        through the definition and archives instead.  The state is planted
        directly: which door produces it is not this case's subject.
        """
        with app.app_context():
            account = self._fresh_account(seed_user, "Definition Here")
            template = make_expense_template(
                db.session, seed_user, name="Moved Away",
            )
            row = generate_row_of(template, seed_periods_today[0])
            template.recurrence_rule = None
            # The definition moves to the fresh account; its row stays on the
            # seed account, as a retained row does.
            template.account_id = account.id
            db.session.commit()
            assert row.account_id != account.id

            response = auth_client.post(
                f"/accounts/{account.id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"has transaction history" in response.data
            archived = db.session.get(Account, account.id)
            assert archived is not None and archived.is_active is False
            db.session.expire_all()
            assert db.session.get(Transaction, row.id).template_id == template.id

    def test_a_settled_ghost_under_a_rule_less_definition_archives_instead(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The definition door's refusal (R-JE, N-440), made at this door too.

        **Found by the adversarial review of this step, reproduced before it
        was fixed**: a settled row soft-deleted while its definition still
        recurred is a tombstone the definition still names; guard 4 read
        live rows only and guard 5 this account's ledger only, so the
        disposal deleted the definition and ``transactions.template_id``'s
        SET NULL left a settled, TEMPLATE-priced row with nothing to price
        it -- unpriceable on any revert, unrestorable by the cutover's
        downgrade.  ``template_has_paid_history`` counts that ghost, and the
        account archives instead, exactly as the definition's own hard-delete
        refuses.
        """
        with app.app_context():
            from app.enums import AmountSourceEnum  # pylint: disable=import-outside-toplevel
            from app.models.transaction_template import (  # pylint: disable=import-outside-toplevel
                TransactionTemplate,
            )
            account = self._fresh_account(seed_user, "Definition Lands Here")
            template = make_expense_template(
                db.session, seed_user, name="Moved Away", amount="55.00",
            )
            row = generate_row_of(template, seed_periods_today[0])
            db.session.commit()
            row_id, template_id = row.id, template.id
            # Settled on the SEED account, then deleted while the definition
            # still recurs: the soft arm, a tombstone.
            assert auth_client.post(
                f"/transactions/{row_id}/mark-done",
            ).status_code == 200
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            db.session.expire_all()
            row = db.session.get(Transaction, row_id)
            assert row.is_deleted is True and row.status.is_settled
            # The definition moves to the fresh account and loses its cadence.
            template = db.session.get(TransactionTemplate, template_id)
            template.account_id = account.id
            template.recurrence_rule = None
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{account.id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"has transaction history" in response.data
            db.session.expire_all()
            archived = db.session.get(Account, account.id)
            assert archived is not None and archived.is_active is False
            assert db.session.get(TransactionTemplate, template_id) is not None
            row = db.session.get(Transaction, row_id)
            assert row.template_id == template_id
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )

    def test_a_projected_ghost_under_a_rule_less_definition_is_deleted_not_unlinked(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A non-settled ghost on ANOTHER account goes with its definition.

        The other half of the same review finding: a Projected row soft-deleted
        while the definition recurred, on the seed account, under a definition
        that then moved to the fresh account and lost its cadence.  Nothing
        refuses (no live row, no settled row), and the disposal is the
        definition door's own act -- the ghost is DELETED, not left as a
        TEMPLATE-priced row with a NULL link.
        """
        with app.app_context():
            from app.models.transaction_template import (  # pylint: disable=import-outside-toplevel
                TransactionTemplate,
            )
            account = self._fresh_account(seed_user, "Definition Lands Here")
            template = make_expense_template(
                db.session, seed_user, name="Moved Away",
            )
            row = generate_row_of(template, seed_periods_today[0])
            db.session.commit()
            row_id, template_id = row.id, template.id
            assert auth_client.delete(f"/transactions/{row_id}").status_code == 200
            db.session.expire_all()
            assert db.session.get(Transaction, row_id).is_deleted is True
            template = db.session.get(TransactionTemplate, template_id)
            template.account_id = account.id
            template.recurrence_rule = None
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{account.id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"permanently deleted" in response.data
            db.session.expire_all()
            assert db.session.get(Account, account.id) is None
            assert db.session.get(TransactionTemplate, template_id) is None
            assert db.session.get(Transaction, row_id) is None

    def test_a_rule_less_definition_with_no_row_goes_with_the_account(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A definition with no rule and no row defines nothing: disposed.

        The state a hard-deleted one-off leaves until the doors leaf deletes
        the pair together, and the one the template form's *Does not repeat*
        makes today.  Without the disposal the delete reaches
        ``transaction_templates.account_id``'s RESTRICT key.
        """
        with app.app_context():
            from app.models.template_amount_version import (  # pylint: disable=import-outside-toplevel
                TemplateAmountVersion,
            )
            from app.models.transaction_template import (  # pylint: disable=import-outside-toplevel
                TransactionTemplate,
            )
            account = self._fresh_account(seed_user, "Row-less One-Off")
            template = make_expense_template(
                db.session, seed_user, name="Kindle", account=account,
            )
            row = generate_row_of(template, seed_periods_today[0])
            template.recurrence_rule = None
            db.session.commit()
            template_id = template.id
            # The one-off's row is hard-deleted at the grid (this step's
            # delete arm), leaving the definition standing alone.
            assert auth_client.delete(f"/transactions/{row.id}").status_code == 200
            assert db.session.get(Transaction, row.id) is None

            response = auth_client.post(
                f"/accounts/{account.id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"permanently deleted" in response.data
            db.session.expire_all()
            assert db.session.get(Account, account.id) is None
            assert db.session.get(TransactionTemplate, template_id) is None
            assert db.session.query(TemplateAmountVersion).filter_by(
                transaction_template_id=template_id,
            ).count() == 0


class TestHardDeleteAndTheMovementsOnAnAccount:
    """Guard 4's MOVEMENT arm (plan step ``credit_card:CC-5-2``).

    A purchase names the account its money moved through (ruling **R-CC15**),
    so since ``CC-5-1`` dropped the parent-account key a card can hold swipes
    recorded in checking envelopes.  The row arm sees none of them -- the rows
    are checking's -- and the cleanup disposes of none, so the RESTRICT key
    ``fk_transaction_entries_account_id`` (ruling **R-CC32**) met the door's
    DELETE as a 500.  **A stated behaviour change**: such an account archives,
    as every other kind of history does.  The arm counts exactly the movements
    the cleanup cannot reach -- under a row neither on the account nor under
    one of its definitions -- and a movement under the account's OWN row, or
    under a row of its own definition, is not one, which the two controls
    below grade directly.  Since plan step ``credit_card:CC-5-4a-4`` no hidden
    row outside a transfer holds a movement (ruling **R-CC92**), so the rows
    this arm can meet are live ones and transfers' legs; the controls are
    live.
    """

    @staticmethod
    def _card(seed_user, name="Rewards Card"):
        """A card opened at `$0.00`, so it posts no anchor leg (guard 5)."""
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", name,
            anchor_balance=Decimal("0.00"),
        )
        db.session.commit()
        return card

    @staticmethod
    def _checking_envelope(seed_user, period):
        """A recurring Groceries envelope of the seed user's on CHECKING."""
        template = make_expense_template(
            db.session, seed_user, amount="500.00",
            name="Groceries", category_key="Groceries", is_envelope=True,
        )
        return generate_row_of(template, period)

    @staticmethod
    def _swipe(row, seed_user, card):
        """Record a `$60.00` purchase against *row* ON *card*, through the door.

        Under a row on *card* it is filed on the row's own account; under any
        other row the CC tick puts it on *card*, as the forms post.
        """
        on_another_account = row.account_id != card.id
        return entry_service.create_entry(
            row.id, seed_user["user"].id,
            entry_service.EntryDetails(
                figure=typed(Decimal("60.00")),
                description="Kroger",
                purchased_on=display_today() - timedelta(days=1),
                on_card=on_another_account,
                card_account_id=card.id if on_another_account else None,
            ),
        )

    def test_a_card_holding_a_checking_envelopes_swipe_archives_instead(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The case that was a 500: real history on the card, under checking's row."""
        with app.app_context():
            card = self._card(seed_user)
            row = self._checking_envelope(seed_user, seed_periods_today[0])
            swipe = self._swipe(row, seed_user, card)
            db.session.commit()
            assert row.account_id == seed_user["account"].id

            response = auth_client.post(
                f"/accounts/{card.id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"purchases recorded on it from other accounts" in response.data
            archived = db.session.get(Account, card.id)
            assert archived is not None and archived.is_active is False
            db.session.expire_all()
            assert db.session.get(TransactionEntry, swipe.id).account_id == card.id

    def test_a_row_ON_the_card_holding_its_own_swipe_is_not_another_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """CONTROL for the arm's first exclusion: a movement under the card's OWN row.

        A recurring checking definition's row moved onto the card, a swipe on
        the card under it.  The other-rows arm answers No -- deleting
        ``Transaction.account_id != account_id`` from that arm makes it count
        this swipe, which is the mutation this exists to catch -- and the card
        archives under the row arm's sentence, the row being its own.
        **The row is LIVE since plan step** ``credit_card:CC-5-4a-4``: it was
        hidden with the swipe inside, which the database now refuses (ruling
        **R-CC92**), and the arm's exclusion is the same question of a live
        row.  Re-expressed under rule 5 twice, developer-confirmed 2026-09-23.
        """
        with app.app_context():
            card = self._card(seed_user)
            row = self._checking_envelope(seed_user, seed_periods_today[0])
            row.account_id = card.id
            db.session.commit()
            swipe = self._swipe(row, seed_user, card)
            db.session.commit()
            card_id, swipe_id = card.id, swipe.id
            assert archive_helpers.account_holds_other_rows_movements(card_id) is False

            response = auth_client.post(
                f"/accounts/{card_id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"has transaction history" in response.data
            assert b"purchases recorded on it from other accounts" not in response.data
            db.session.expire_all()
            archived = db.session.get(Account, card_id)
            assert archived is not None and archived.is_active is False
            assert db.session.get(TransactionEntry, swipe_id) is not None

    def test_a_row_of_the_cards_rule_less_definition_is_not_another_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """CONTROL for the arm's second exclusion: a movement under a row of the card's OWN definition.

        A checking row holding a swipe on the card; then the definition's rule
        is cleared and the definition itself moves onto the card.  The
        other-rows arm answers No -- deleting the ``TransactionTemplate``
        clause from that arm makes it count this swipe -- and the card
        archives under the row arm's sentence, the row being its definition's.
        **The row is LIVE since plan step** ``credit_card:CC-5-4a-4``: it was
        hidden with the swipe inside, which the database now refuses (ruling
        **R-CC92**), and the arm's exclusion is the same question of a live
        row.  Re-expressed under rule 5 twice, developer-confirmed 2026-09-23.
        """
        with app.app_context():
            card = self._card(seed_user)
            row = self._checking_envelope(seed_user, seed_periods_today[0])
            db.session.commit()
            swipe = self._swipe(row, seed_user, card)
            db.session.commit()
            template = row.template
            template.recurrence_rule = None
            template.account_id = card.id
            db.session.commit()
            db.session.expire_all()
            live = db.session.get(Transaction, row.id)
            assert live.is_deleted is False
            assert live.account_id == seed_user["account"].id
            assert live.template.account_id == card.id
            card_id, swipe_id, row_id = card.id, swipe.id, row.id
            assert archive_helpers.account_holds_other_rows_movements(card_id) is False

            response = auth_client.post(
                f"/accounts/{card_id}/hard-delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"has transaction history" in response.data
            assert b"purchases recorded on it from other accounts" not in response.data
            db.session.expire_all()
            archived = db.session.get(Account, card_id)
            assert archived is not None and archived.is_active is False
            assert db.session.get(TransactionEntry, swipe_id) is not None
            assert db.session.get(Transaction, row_id) is not None
