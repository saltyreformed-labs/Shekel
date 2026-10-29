"""What counts as a category's use: the archive helpers' history checks, a standing merchant rule
filed under it, and a transfer answering for its own category and accounts.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.ref import TransactionType
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transaction_service, transfer_service
from app.utils.archive_helpers import (
    account_has_history,
    category_has_usage,
    template_has_paid_history,
    transfer_template_has_paid_history,
)
from tests._test_helpers import (
    create_account_of_type,
    create_savings_account,
    create_transfer,
    generate_row_of,
    generate_transfer_of,
    make_expense_template,
    make_transfer_template,
    one_off_row_of,
    typed,
)


# ── Archive Helpers Tests (5A.5-1) ──────────────────────────────────


class TestArchiveHelpers:
    """Tests for archive history-detection utility functions.

    Verifies that template_has_paid_history, transfer_template_has_paid_history,
    account_has_history, and category_has_usage return correct boolean results
    for various data configurations.
    """

    def test_existing_categories_default_active(self, app, db, seed_user):
        """C-5A.5-1: Seed categories all have is_active=True after migration."""
        with app.app_context():
            categories = (
                db.session.query(Category)
                .filter_by(user_id=seed_user["user"].id)
                .all()
            )
            assert len(categories) > 0, "seed_user should have created categories"
            for cat in categories:
                assert cat.is_active is True, (
                    f"Category '{cat.display_name}' should default to is_active=True"
                )

    def test_template_has_paid_history_true(self, app, db, seed_user, seed_periods_today):
        """C-5A.5-2: template_has_paid_history returns True when a Paid transaction exists.

        The definition's own row, settled the way Mark Paid settles it (plan
        step balance:X-cf-4): the engine only ever writes Projected.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="500.00",
                name="Paid History Template",
            )
            transaction_service.settle_transaction(
                generate_row_of(template, seed_periods_today[0]),
            )
            db.session.commit()

            result = template_has_paid_history(template.id)
            assert result is True

    def test_template_has_paid_history_false(self, app, db, seed_user, seed_periods_today):
        """C-5A.5-3: template_has_paid_history returns False when only Projected txns exist."""
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="500.00",
                name="Projected Only Template",
            )
            generate_row_of(template, seed_periods_today[0])
            db.session.commit()

            result = template_has_paid_history(template.id)
            assert result is False

    def test_transfer_template_has_paid_history_true(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C-5A.5-4: transfer_template_has_paid_history returns True when Paid transfer exists.

        The transfer is the engine's (:func:`generate_transfer_of`, plan step
        balance:X-ch) and is SETTLED through ``transfer_service`` -- the
        transfer's own door, which records each side's payment as it settles
        -- rather than born Paid by hand.
        """
        with app.app_context():
            savings_account = create_savings_account(
                seed_user, db.session, "Savings for Transfer Test",
                Decimal("0.00"),
            )
            xfer_template = make_transfer_template(
                db.session, seed_user, savings_account, amount="200.00",
            )
            xfer = generate_transfer_of(xfer_template, seed_periods_today[0])
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            result = transfer_template_has_paid_history(xfer_template.id)
            assert result is True

    def test_transfer_template_has_paid_history_false(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C-5A.5-5: transfer_template_has_paid_history returns False when only Projected."""
        with app.app_context():
            savings_account = create_savings_account(
                seed_user, db.session, "Savings for Projected Transfer",
                Decimal("0.00"),
            )
            xfer_template = make_transfer_template(
                db.session, seed_user, savings_account, amount="150.00",
            )
            generate_transfer_of(xfer_template, seed_periods_today[0])
            db.session.commit()

            result = transfer_template_has_paid_history(xfer_template.id)
            assert result is False

    def test_account_has_history_true(self, app, db, seed_user, seed_periods_today):
        """C-5A.5-6: account_has_history returns True when account has any non-deleted txn."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            one_off_row_of(
                seed_periods_today[0],
                name="Account History Txn",
                amount=Decimal("100.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            result = account_has_history(seed_user["account"].id)
            assert result is True

    def test_account_has_history_false(self, app, db, seed_user):
        """C-5A.5-7: account_has_history returns False when account has zero transactions."""
        with app.app_context():
            # seed_user["account"] has no transactions (each test gets a freshly
            # cloned database).
            result = account_has_history(seed_user["account"].id)
            assert result is False

    def test_account_has_history_through_a_definition_on_it(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A live row UNDER one of the account's definitions is history too.

        Plan step balance:X-bi-7a: the hard-delete door disposes of a
        rule-less definition with the account, so a row that makes it define
        something has to be seen here even when the row sits on ANOTHER
        account (a retained row stays where it is when its definition's
        account moves).  The row here is on the seed account; the
        definition has moved to a fresh one that holds no row of its own.
        """
        with app.app_context():
            other = create_account_of_type(
                seed_user, db.session, "Checking", "Definition Moved Here",
                anchor_balance=Decimal("0.00"),
            )
            template = make_expense_template(db.session, seed_user)
            row = generate_row_of(template, seed_periods_today[0])
            template.account_id = other.id
            db.session.commit()
            assert row.account_id == seed_user["account"].id

            assert account_has_history(other.id) is True

            row.is_deleted = True
            db.session.commit()
            assert account_has_history(other.id) is False

    def test_category_has_usage_true(self, app, db, seed_user):
        """C-5A.5-8: category_has_usage returns True when a template references the category."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            category = seed_user["categories"]["Rent"]

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=expense_type.id,
                name="Category Usage Template",
                default_amount=Decimal("1200.00"),
            )
            db.session.add(template)
            db.session.commit()

            result = category_has_usage(category.id, seed_user["user"].id)
            assert result is True

    def test_category_has_usage_false(self, app, db, seed_user):
        """C-5A.5-9: category_has_usage returns False when nothing references the category."""
        with app.app_context():
            # Create a fresh category with no templates or transactions.
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Unused",
                item_name="Orphan",
            )
            db.session.add(cat)
            db.session.commit()

            result = category_has_usage(cat.id, seed_user["user"].id)
            assert result is False

    def test_category_has_usage_scoped_to_user(
        self, app, db, seed_user, seed_second_user,
    ):
        """C-5A.5-10: category_has_usage returns False when only another user's template uses it.

        User B creates a template referencing User B's category.  When
        we check category_has_usage for User A's category (same group/item
        names but different user_id), it must return False because the
        check is scoped to User A.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # User B creates a template referencing User B's own Rent category.
            user_b_cat = seed_second_user["categories"]["Rent"]
            template_b = TransactionTemplate(
                user_id=seed_second_user["user"].id,
                account_id=seed_second_user["account"].id,
                category_id=user_b_cat.id,
                transaction_type_id=expense_type.id,
                name="User B Rent Template",
                default_amount=Decimal("900.00"),
            )
            db.session.add(template_b)
            db.session.commit()

            # User A's Rent category should show no usage (User B's template
            # does not count).
            user_a_cat = seed_user["categories"]["Rent"]
            result = category_has_usage(user_a_cat.id, seed_user["user"].id)
            assert result is False


class TestACategoryAStandingMERCHANTRULEFilesUnderIsInUse:
    """Plan step ``bank_import:X-gd-2``, on a gap that predates it.

    ``category_has_usage`` counted templates and transactions only, so a
    category referenced ONLY by a merchant rule's *new envelope* answer read as
    unused -- and a "no" here is what permits a PERMANENT delete.
    ``fk_merchant_rules_category_owner`` cascades, so the rule went with it,
    under a flash reading "permanently deleted" that said nothing about the
    answer the owner lost.

    **The cascade itself is right** and is not what changed: an answer naming a
    category that no longer exists is not an answer, and ``RESTRICT`` was
    refused as finding **N-302**'s dead end.  What was wrong is a door calling
    the category unused while a stored decision used it -- and ruling **R-GS**
    sharpens that, because a rule row is never un-stated by the owner, so a
    silent cascade would be the only way one could vanish.

    Measured on the developer's dev database 2026-08-26: 12 new-envelope rules
    naming 6 distinct categories, every one of them ALSO used by a template or
    a transaction -- so the path is reachable and has not yet fired.
    """

    def _a_rule_filing_under(self, seed_user, category):
        """Stage a *new envelope* rule naming *category*, and return it.

        Args:
            seed_user: The seeded user bundle.
            category: The category the answer files under.

        Returns:
            The staged :class:`~app.models.merchant_rule.MerchantRule`.
        """
        merchant = Merchant(
            account_id=seed_user["account"].id, name="Public Library",
        )
        db.session.add(merchant)
        db.session.flush()
        rule = MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=merchant.id,
            envelope_name="Library Fines",
            category_id=category.id,
            never_a_purchase=False,
        )
        db.session.add(rule)
        db.session.commit()
        return rule

    def test_the_helper_counts_it(self, app, db, seed_user):
        """The predicate itself, on a category nothing ELSE references."""
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Only A Rule Uses This",
            )
            db.session.add(category)
            db.session.flush()
            self._a_rule_filing_under(seed_user, category)

            assert category_has_usage(
                category.id, seed_user["user"].id,
            ) is True

    def test_the_delete_door_ARCHIVES_it_and_keeps_the_rule(
        self, app, auth_client, db, seed_user,
    ):
        """The consequence, at the door where the money-shaped loss happened.

        The category survives as archived and the owner's answer survives with
        it -- which is the state ``_new_envelope_placement`` already designs
        for, reporting *the category you filed it under is archived* rather
        than filing money somewhere nobody named.
        """
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Only A Rule Uses This",
            )
            db.session.add(category)
            db.session.flush()
            rule = self._a_rule_filing_under(seed_user, category)
            rule_id, category_id = rule.id, category.id

            response = auth_client.post(
                f"/categories/{category_id}/delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"archived" in response.data
            surviving = db.session.get(Category, category_id)
            assert surviving is not None
            assert surviving.is_active is False
            assert db.session.get(MerchantRule, rule_id) is not None

    def test_a_category_NO_rule_names_is_still_permanently_deleted(
        self, app, auth_client, db, seed_user,
    ):
        """The firing control: the new clause narrows, it does not block.

        Without it the two cases above would be satisfied by a helper that
        answered True for everything, which would make permanent deletion
        unreachable for every category on the account.
        """
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Nothing Uses This",
            )
            db.session.add(category)
            db.session.commit()
            category_id = category.id

            response = auth_client.post(
                f"/categories/{category_id}/delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert db.session.get(Category, category_id) is None

    def test_a_STRANGERS_rule_cannot_name_this_owners_category_at_all(
        self, app, db, seed_user, seed_second_user,
    ):
        """Why the rule clause needs no ``user_id`` term, shown structurally.

        **This case replaced one that graded nothing.** The original staged a
        stranger's rule naming the STRANGER'S OWN category and then asserted
        this owner's category was unused -- true whatever the clause said,
        because no rule anywhere named it. Deleting the clause's ``user_id``
        filter left it green. Found by adversarial review 2026-08-26.

        What is actually true is stronger than a filter:
        ``fk_merchant_rules_category_owner`` is composite over
        ``(category_id, user_id)`` against ``categories(id, user_id)``, and
        ``categories.id`` is a primary key -- so a rule naming a category
        DETERMINES its owner, and a stranger's rule naming this owner's
        category is UNWRITABLE rather than filtered out. That is what the
        clause rests on, so that is what is graded.
        """
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Mine",
            )
            db.session.add(category)
            db.session.flush()
            merchant = Merchant(
                account_id=seed_second_user["account"].id, name="Their Shop",
            )
            db.session.add(merchant)
            db.session.flush()
            db.session.add(MerchantRule(
                user_id=seed_second_user["user"].id,
                account_id=seed_second_user["account"].id,
                merchant_id=merchant.id,
                envelope_name="Theirs",
                category_id=category.id,
                never_a_purchase=False,
            ))

            with pytest.raises(Exception) as caught:
                db.session.flush()

            assert "fk_merchant_rules_category_owner" in str(caught.value)

    def test_a_rule_on_ANOTHER_ACCOUNT_of_this_owner_counts(
        self, app, db, seed_user):
        """The clause is scoped by neither owner nor ACCOUNT, and the second
        absence is the load-bearing one.

        A category is owner-scoped and a rule is account-scoped, so one owner's
        rules on two accounts may file under one category. An account term
        would answer "unused" for a category the owner's OTHER account's rule
        is using, and the permanent delete would cascade that rule away -- the
        exact defect this clause was added to close, reintroduced by narrowing
        it.
        """
        with app.app_context():
            second = Account(
                user_id=seed_user["user"].id,
                name="Second Checking",
                account_type_id=seed_user["account"].account_type_id,
            )
            db.session.add(second)
            db.session.flush()
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Only The Other Account Uses This",
            )
            db.session.add(category)
            db.session.flush()
            merchant = Merchant(account_id=second.id, name="Public Library")
            db.session.add(merchant)
            db.session.flush()
            db.session.add(MerchantRule(
                user_id=seed_user["user"].id,
                account_id=second.id,
                merchant_id=merchant.id,
                envelope_name="Library Fines",
                category_id=category.id,
                never_a_purchase=False,
            ))
            db.session.commit()

            assert category_has_usage(
                category.id, seed_user["user"].id,
            ) is True


class TestATransferAnswersForItsOwnCategoryAndAccounts:
    """Plan step ``balance:X-bi-6-4c-4``: a transfer is asked, not its shadows.

    ``category_has_usage`` and ``account_has_history`` saw a transfer only
    through its shadow rows in ``budget.transactions`` -- the category a shadow
    carries, the account a shadow sits on -- and a recurring transfer
    DEFINITION not at all.  ``X-bi-6-4d`` deleted every shadow (ruling
    **R-BAL166**) and no row can name a transfer since
    (``ck_transactions_names_no_transfer`` refused one until plan step
    balance:X-bi-6-5 dropped the column), so each predicate asks
    ``budget.transfers`` (and ``budget.transfer_templates``) itself.  The
    shadows-away cases below were written to PLANT the state 6-4d produces --
    a live transfer no shadow row speaks for -- by moving or hiding the
    shadows around the service; since 6-4d the app produces it and no shadow
    exists to move, and each was red against the predicate as it stood.  The
    definition
    case is finding **BAL-545**'s declared change.
    """

    def _a_category(self, owner, name):
        """Stage a fresh category of *owner*'s that nothing references yet.

        Args:
            owner: The seeded user bundle that owns it.
            name: Its item name.

        Returns:
            The flushed :class:`~app.models.category.Category`.
        """
        category = Category(
            user_id=owner["user"].id, group_name="Temp", item_name=name,
        )
        db.session.add(category)
        db.session.flush()
        return category

    def _a_projected_transfer(self, owner, name):
        """Create a one-time Projected transfer between two fresh accounts.

        Args:
            owner: The seeded user bundle that owns it.
            name: A prefix for the two accounts' names.

        Returns:
            ``(transfer, source, destination)``, committed.
        """
        source = create_account_of_type(
            owner, db.session, "Checking", f"{name} Source",
        )
        destination = create_account_of_type(
            owner, db.session, "Savings", f"{name} Destination",
        )
        db.session.commit()
        transfer = create_transfer(
            owner, db.session, source, destination,
            owner["bootstrap_period"],
        )
        db.session.commit()
        return transfer, source, destination

    def test_a_category_only_a_transfer_DEFINITION_names_is_in_use(
        self, app, auth_client, db, seed_user,
    ):
        """BAL-545: the definition alone archives the category at the door.

        No transfer is generated from it, so no shadow names the category and
        the predicate as it stood answered "unused" -- the door then deleted
        the category and ``transfer_templates.category_id``'s ``SET NULL``
        took the definition's category with it, under a flash saying only
        that the category was deleted.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Definition Savings", Decimal("0.00"),
            )
            category = self._a_category(seed_user, "Only A Transfer Rule")
            template = make_transfer_template(db.session, seed_user, savings)
            template.category_id = category.id
            db.session.commit()
            category_id, template_id = category.id, template.id

            assert category_has_usage(
                category_id, seed_user["user"].id,
            ) is True

            response = auth_client.post(
                f"/categories/{category_id}/delete", follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"archived instead" in response.data
            surviving = db.session.get(Category, category_id)
            assert surviving is not None and surviving.is_active is False
            assert db.session.get(
                TransferTemplate, template_id,
            ).category_id == category_id

    def test_a_category_only_a_transfer_carries_is_in_use_even_once_hidden(
        self, app, db, seed_user,
    ):
        """The Transfer arm answers for a category only a transfer carries.

        The category goes on through the transfer service and no row carries
        it, so the transfer is the only thing that can answer.  Soft-deleting
        the transfer keeps the answer, as a soft-deleted ROW's category always
        has.  Until plan step balance:X-bi-6-4d-2 made a transfer one row, the
        service mirrored the category to both shadows and the case
        re-categorised them around it, to plant the state a shadowless
        transfer presents.
        """
        with app.app_context():
            category = self._a_category(seed_user, "Only A Transfer")
            transfer, _source, _destination = self._a_projected_transfer(
                seed_user, "Categorised",
            )
            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id, category_id=category.id,
            )
            db.session.commit()

            assert category_has_usage(
                category.id, seed_user["user"].id,
            ) is True

            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()

            assert category_has_usage(
                category.id, seed_user["user"].id,
            ) is True

    def test_a_STRANGERS_transfer_or_definition_does_not_count(
        self, app, db, seed_user, seed_second_user,
    ):
        """Both transfer clauses are scoped by the OWNER's ``user_id``.

        ``transfers.category_id`` and ``transfer_templates.category_id`` are
        single-column keys, so a stranger's rows CAN name this owner's
        category (planted here around the doors, which refuse it); the reader
        is what stops the stranger's use from counting as the owner's.  Each
        ``user_id`` term dropped alone turns this red.
        """
        with app.app_context():
            category = self._a_category(seed_user, "Mine Alone")
            savings = create_savings_account(
                seed_second_user, db.session, "Their Savings", Decimal("0.00"),
            )
            template = make_transfer_template(
                db.session, seed_second_user, savings,
            )
            template.category_id = category.id
            db.session.commit()
            transfer = create_transfer(
                seed_second_user, db.session, seed_second_user["account"],
                savings, seed_second_user["bootstrap_period"],
            )
            transfer.category_id = category.id
            db.session.commit()

            assert category_has_usage(
                category.id, seed_user["user"].id,
            ) is False

    @pytest.mark.parametrize(
        "status", [StatusEnum.PROJECTED, StatusEnum.CANCELLED, StatusEnum.DONE],
    )
    def test_a_live_transfer_is_its_accounts_history_until_hidden_whatever_its_status(
        self, app, seed_user, status,
    ):
        """BAL-539: both endpoints hold history while the transfer is live, whatever its status.

        The transfer itself is the history, on both of its accounts; its own
        soft delete is what ends it, as a row's does.  The status and the
        delete each go through the transfer's own door and are asserted
        before the predicate is asked, so a door that left the transfer
        Projected, or live, cannot pass here.  It ran over a Projected
        transfer only, hidden by hand, until an adversarial review of plan
        step balance:X-bi-6-4d-2 found an arm narrowed to Projected passing
        the whole suite; a Paid transfer holding its payment records cannot be
        hidden by hand (``ck_hidden_transfer_holds_nothing``).  Its [DONE]
        transfer holds them, so the delete takes them off and sets it back to
        Projected (ruling R-BAL246): the hidden Paid state is the case below.
        Until
        4d-2 made a transfer one row, the case hid its shadows around the
        service to plant the state a shadowless transfer presents.
        """
        with app.app_context():
            transfer, source, destination = self._a_projected_transfer(
                seed_user, "History",
            )
            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(status),
            )
            db.session.commit()
            assert db.session.get(Transfer, transfer.id).status_id == (
                ref_cache.status_id(status)
            )

            assert account_has_history(source.id) is True
            assert account_has_history(destination.id) is True

            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            assert db.session.get(Transfer, transfer.id).is_deleted is True

            assert account_has_history(source.id) is False
            assert account_has_history(destination.id) is False

    def test_a_transfer_closed_at_zero_is_history_until_hidden_though_still_paid(
        self, app, seed_user,
    ):
        """A ``$0.00`` close is its accounts' history while live, and none once hidden.

        A ``$0.00`` close holds no payment record, so its own soft delete
        keeps it Paid (ruling R-BAL246): a hidden transfer that is settled.
        The status and the hide are asserted before the predicate is asked.
        An arm that counted a hidden SETTLED transfer passed every module
        that asks the predicate until an adversarial review of plan step
        balance:X-bi-6-4d-2 found it.
        """
        with app.app_context():
            transfer, source, destination = self._a_projected_transfer(
                seed_user, "Zero Close",
            )
            transfer_service.settle_transfer(
                transfer.id, seed_user["user"].id,
                submitted=typed(Decimal("0.00")),
            )
            db.session.commit()
            assert db.session.get(Transfer, transfer.id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            )

            assert account_has_history(source.id) is True
            assert account_has_history(destination.id) is True

            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            stored = db.session.get(Transfer, transfer.id)
            assert (stored.is_deleted, stored.status_id) == (
                True, ref_cache.status_id(StatusEnum.DONE),
            )

            assert account_has_history(source.id) is False
            assert account_has_history(destination.id) is False

    def test_the_account_door_archives_an_account_a_live_transfer_names(
        self, app, auth_client, db, seed_user,
    ):
        """BAL-539's consequence, at the door: archived, the transfer kept.

        Before the predicate asked the transfer itself (plan step
        balance:X-bi-6-4c-4), a live transfer whose shadows were hidden left
        the door finding no history: it deleted the transfer in its cleanup
        and the account with it.
        """
        with app.app_context():
            transfer, _source, destination = self._a_projected_transfer(
                seed_user, "Door",
            )
            transfer_id, destination_id = transfer.id, destination.id

            response = auth_client.post(
                f"/accounts/{destination_id}/hard-delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"has transaction history" in response.data
            archived = db.session.get(Account, destination_id)
            assert archived is not None and archived.is_active is False
            assert db.session.get(Transfer, transfer_id) is not None
