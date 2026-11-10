"""
Shekel Budget App -- the grid's doors on a RULE-LESS definition's row

Plan step ``balance:X-bi-7a`` (ruling **R-BAL20**, every plan item has exactly
one definition).  Five "no cadence" readings of ``template_id IS NULL`` on
the transaction doors were re-keyed onto ``Transaction.recurs``, so a row
whose definition has NO rule -- template-linked, no cadence: a cleared cadence
today, every one-off once the family's cutover mints it a definition --
behaves as the one-off it is.  No production row changes behaviour (no
rule-less transaction definition exists on the 2026-09-12 restore), so each
case here MAKES one, the way the edit door does: the engine's row under a
cadence, then the cadence cleared (dis-associate; delete-orphan).

Pinned per door, each beside its recurring control so the two arms are
graded against each other and not against a shape the app never writes:

* DELETE -- hard, and the dialog says so (``preview.soft``, the service's
  own arm); **and since plan step ``balance:X-bi-7b`` the definition goes
  with its LAST row** (rulings R-BAL23 / R-BAL27), stays while it holds
  another, and the delete is REFUSED while a standing merchant rule names
  the definition -- the sentence on the card and the door's 400 are one
  rule (``deletion_refusal``);
* due date -- the popover offers the input, a MOVE lands, a CLEAR is refused
  with a sentence that names the rule rather than "invalid reference";
* period move -- ``is_override`` is NOT flipped (developer 2026-09-13: the
  flip's MOVE half keys on ``recurs``), **and since leaf 7b-2 the move
  RE-PLACES the row** (ruling **R-BAL33**): the target paycheck's start
  unless the owner had stated a day, ``occurs_on`` following (R-BAL25);
* typed figure -- **restated on the DEFINITION, in place** (rulings
  **R-BAL21** / **R-BAL29**, leaf 7b-2), the row never detached; a row the
  interim between the two leaves DID detach is re-attached by the same act
  (**R-BAL37**).  Until 7b-2 the flag flipped and the figure landed OWN.
* the ITEM -- name, category and both flags render on the card for a
  placed row and land on its definition (rulings **R-BAL23** / **R-BAL36**);
  a recurring row's crafted flag is dropped by the schema (BAL-484's writer
  gone); *make this repeat* opens the definition's edit form; the dialog
  says the item goes with its only row.

**Two shapes of rule-less row are graded**, and which each case uses is
deliberate: ``_one_off`` is the pre-7b-1 shape (a cleared cadence, whose
series can hold several versions -- what the restate's walk is graded on),
``_placed`` is the shape the app writes today, through the one producer.
"""
import re
from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.category import Category
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import posting_service
from app.services.amount_ownership import state_own_amount
from app.services.one_off import OneOffToPlace, place_one_off, place_row_of
from app.services.template_amount_service import amount_versions
from app.utils.dates import display_today
from app.services.row_valuation import settled_figure
from werkzeug.datastructures import MultiDict
from tests._test_helpers import (
    add_entry,
    derived_span,
    generate_row_of,
    make_expense_template,
    payback_row_of,
    resolved_amount,
    state_template_price,
)
from tests.test_routes._statement_forms import form_fields
from tests._definition_price import newest_price
from tests._legacy_credit import plant_old_credit


def _one_off(seed_user, seed_periods_today):
    """The engine's row of a definition whose cadence was then cleared.

    A plain helper rather than a fixture: the row has to be born inside the
    test's own ``app_context`` to be persistent in its session.
    """
    template = make_expense_template(
        db.session, seed_user, amount="162.25", name="Kayla's Kindle",
        category_key="Groceries",
    )
    row = generate_row_of(template, seed_periods_today[0])
    template.recurrence_rule = None
    db.session.commit()
    assert row.template_id == template.id
    assert row.recurs is False
    return row


def _placed(seed_user, period, *, due_date=None, name="Kayla's Kindle",
            amount="162.25", category_key="Groceries", **flags):
    """Place a one-off in *period* through the one producer (leaf 7b-1)."""
    row = place_one_off(
        OneOffToPlace(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            name=name,
            amount=Decimal(amount),
            category_id=seed_user["categories"][category_key].id,
            **flags,
        ),
        derived_span(period),
        scenario_id=seed_user["scenario"].id,
        due_date=due_date,
    )
    db.session.commit()
    assert row.is_placed is True
    return row


def _card(auth_client, row):
    """Return the full-edit card for *row*, rendered."""
    resp = auth_client.get(f"/transactions/{row.id}/full-edit")
    assert resp.status_code == 200
    return resp.data.decode()


def _submit(auth_client, row, **changes):
    """PATCH *row* with what its card renders, *changes* typed over it.

    A browser submits every control the card renders at the value it
    renders, so the payload is READ off the card rather than written by
    hand; a changed field replaces every pair of that name (a checkbox and
    its hidden ``false`` twin are one control).
    """
    rendered = form_fields(
        _card(auth_client, row), f"/transactions/{row.id}",
        attribute="hx-patch",
    )
    payload = [pair for pair in rendered if pair[0] not in changes]
    payload.extend(changes.items())
    return auth_client.patch(
        f"/transactions/{row.id}", data=MultiDict(payload),
    )


def _category_select(html):
    """Return the card's category ``<select>`` markup alone."""
    match = re.search(
        r'<select name="category_id".*?</select>', html, flags=re.S,
    )
    assert match is not None
    return match.group(0)


def _recurring(seed_user, seed_periods_today):
    """The control: the engine's row of a definition that still repeats."""
    template = make_expense_template(
        db.session, seed_user, amount="100.00", name="Streaming",
        category_key="Groceries",
    )
    row = generate_row_of(template, seed_periods_today[0])
    db.session.commit()
    assert row.recurs is True
    return row


def _rendered_companion(auth_client, row):
    """Read the popover's rendered-figure companion, as a browser posts it."""
    edit = auth_client.get(f"/transactions/{row.id}/full-edit")
    assert edit.status_code == 200
    companion = re.search(
        r'name="estimated_amount_as_rendered"[^>]*value="([^"]*)"',
        edit.data.decode(),
    )
    assert companion is not None
    return companion.group(1)


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


class TestDueDate:
    """``_gates._reject_generated_due_date_edit`` and the popover's input."""

    def test_the_popover_offers_the_input_on_a_rule_less_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An INPUT where no rule derives the day; TEXT where one does."""
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            recurring = _recurring(seed_user, seed_periods_today)
            html = auth_client.get(
                f"/transactions/{one_off.id}/full-edit",
            ).data.decode()
            assert re.search(r'<input type="date" name="due_date"', html)
            assert "Set by this recurring transaction" not in html

            html = auth_client.get(
                f"/transactions/{recurring.id}/full-edit",
            ).data.decode()
            assert not re.search(r'<input type="date" name="due_date"', html)
            assert "Set by this recurring transaction" in html

    def test_moving_the_date_lands_and_the_price_follows_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The date is the owner's to state (R-BAL22), and rule 3 reads it.

        **Measured rather than claimed** (an adversarial review of this step
        found the gate's first docstring saying the move "prices nothing
        differently", true only of the one-version series R-BAL21 gives a
        one-off at the doors leaf): a CLEARED cadence keeps every version its
        series held, so a row moved across a version boundary re-prices
        through amount rule 3 -- `$162.25` before the boundary, `$170.00`
        after it -- with no figure of its own and no flag, exactly as any
        derived row is priced on its own day.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            # The series: `$162.25` from today (the create door's version),
            # `$170.00` from a week on; the row sits before the boundary.
            today = display_today()
            state_template_price(
                one_off.template, "170.00", effective_on=today + timedelta(days=7),
            )
            db.session.commit()
            assert one_off.due_date < today + timedelta(days=7)
            assert resolved_amount(one_off) == Decimal("162.25")
            moved_to = today + timedelta(days=10)

            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "due_date": moved_to.isoformat(),
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(one_off)
            assert one_off.due_date == moved_to
            assert one_off.estimated_amount is None
            assert one_off.is_override is False
            assert resolved_amount(one_off) == Decimal("170.00")
            assert auth_client.get("/grid").status_code == 200

    def test_a_moved_date_carries_the_occurrence_with_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL25: a placed row answers its own due date, so ``occurs_on`` moves too.

        Leaf 7b-1 wrote both at birth and the popover's date move rewrote
        one: two homes for one fact, and a rule added later would have
        looked for the row at the day it no longer read.  ``one_off.
        state_due_date`` is the one writer now.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            assert row.occurs_on == row.due_date
            moved_to = row.due_date + timedelta(days=5)
            resp = _submit(auth_client, row, due_date=moved_to.isoformat())
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.due_date == moved_to
            assert row.occurs_on == moved_to

    def test_the_input_is_required_on_a_placed_row_and_not_on_a_payback(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The card refuses the clear first; the gate stays the backstop.

        The other half is a row NO definition prices: a CC payback's date
        is its owner's optional note, priced through its parent, so its box
        stays clearable.  (A legacy link-less row was that half until the
        family's cutover, ``balance:X-bi-7d-2``, minted every one a
        definition.)
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            html = _card(auth_client, row)
            box = re.search(r'<input type="date" name="due_date"[^>]*>', html)
            assert box is not None and "required" in box.group(0)

            envelope = _placed(
                seed_user, seed_periods_today[0], name="Card envelope",
                is_envelope=True,
            )
            payback = payback_row_of(
                db.session, seed_user, envelope, Decimal("5.00"),
                seed_periods_today[0].start_date,
            )
            db.session.commit()
            html = _card(auth_client, payback)
            box = re.search(r'<input type="date" name="due_date"[^>]*>', html)
            assert box is not None and "required" not in box.group(0)

    def test_clearing_the_date_is_refused_with_the_reason(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A CLEAR meets the rule's own sentence, not a constraint's.

        ``ck_transactions_template_row_needs_due_date`` would refuse the
        write anyway, rendered as *Invalid reference*; the gate says why.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            was = one_off.due_date
            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "due_date": "",
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 400
            assert b"can be moved but not cleared" in resp.data
            assert b"Invalid reference" not in resp.data
            db.session.refresh(one_off)
            assert one_off.due_date == was

    def test_a_recurring_definitions_row_still_refuses_the_field(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: a generated row's date is its definition's, move or clear."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            was = recurring.due_date
            for submitted in ("2026-02-20", ""):
                resp = auth_client.patch(f"/transactions/{recurring.id}", data={
                    "due_date": submitted,
                    "version_id": recurring.version_id,
                })
                assert resp.status_code == 400
                assert b"recurring transaction" in resp.data
                db.session.refresh(recurring)
                assert recurring.due_date == was


class TestTheItemIsEditedOnTheDefinition:
    """Rulings **R-BAL23** / **R-BAL36**: the card is a one-off's whole lifecycle."""

    def test_the_card_offers_name_category_and_flags_on_a_placed_row_only(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The controls render for a placed row, not for a recurring one."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            html = _card(auth_client, row)
            assert re.search(r'<input type="text" name="name"', html)
            assert re.search(r'<select name="category_id"', html)
            assert 'name="is_envelope"' in html
            assert 'name="companion_visible"' in html
            assert "Make this repeat" in html
            assert f"/templates/{row.template_id}/edit" in html

            recurring = _recurring(seed_user, seed_periods_today)
            html = _card(auth_client, recurring)
            assert not re.search(r'<input type="text" name="name"', html)
            assert not re.search(r'<select name="category_id"', html)
            assert 'name="is_envelope"' not in html
            assert 'name="companion_visible"' not in html
            assert "Make this repeat" not in html

    def test_the_category_select_offers_active_categories_and_the_rows_own(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An archived category is not offered, unless it is the row's own.

        The list is the definition's own edit form's
        (``list_categories_for_edit``); a first draft copied the transfer
        branch's raw query and offered archived ones (adversarial review).
        The row's own archived category stays so an untouched save posts
        the category the row has rather than a browser's first option.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            # Re-read in THIS session: the fixture's instances are detached,
            # so a flag set on one of them would never reach the database.
            archived = db.session.get(Category, next(
                cat.id for key, cat in seed_user["categories"].items()
                if key != "Groceries"
            ))
            archived.is_active = False
            db.session.commit()
            select = _category_select(_card(auth_client, row))
            assert f'<option value="{archived.id}"' not in select
            assert f'<option value="{row.category_id}"' in select

            row.template.category_id = archived.id
            row.category_id = archived.id
            db.session.commit()
            select = _category_select(_card(auth_client, row))
            assert f'<option value="{archived.id}" selected' in select
            resp = _submit(auth_client, row, notes="untouched category")
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.category_id == archived.id
            assert row.template.category_id == archived.id

    def test_a_category_less_placed_row_offers_a_placeholder_that_posts_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The bank door's row for money it could not categorise (R-FN).

        The select shows a placeholder posting the empty value, which the
        schema drops as "leave alone", so an untouched save keeps the row
        uncategorised and picking a category categorises it.
        """
        with app.app_context():
            row = place_one_off(
                OneOffToPlace(
                    user_id=seed_user["user"].id,
                    account_id=seed_user["account"].id,
                    transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                    name="Uncategorised", amount=Decimal("9.99"),
                    category_id=None,
                ),
                derived_span(seed_periods_today[0]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            html = _card(auth_client, row)
            assert "(no category yet)" in html

            resp = _submit(auth_client, row, notes="still uncategorised")
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.template.category_id is None
            assert row.category_id is None

            groceries = seed_user["categories"]["Groceries"]
            resp = _submit(auth_client, row, category_id=str(groceries.id))
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.template.category_id == groceries.id
            assert row.category_id == groceries.id

    def test_a_rename_lands_on_the_definition_and_re_files_the_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The definition is renamed, its row follows, the grid row moves (R-BAL34)."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            resp = _submit(auth_client, row, name="Kayla's Paperwhite")
            assert resp.status_code == 200, resp.data
            assert resp.headers["HX-Trigger"] == "gridRefresh"
            db.session.refresh(row)
            assert row.template.name == "Kayla's Paperwhite"
            assert row.name == "Kayla's Paperwhite"

    def test_an_untouched_save_re_files_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the card posts the name it rendered; nothing moves."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            resp = _submit(auth_client, row, notes="a note")
            assert resp.status_code == 200, resp.data
            assert resp.headers["HX-Trigger"] == "balanceChanged"
            db.session.refresh(row)
            assert row.notes == "a note"
            assert row.template.name == "Kayla's Kindle"

    def test_a_re_category_lands_on_the_definition_and_reaches_the_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The definition's category moves, and the propagation carries it to the row."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            other = next(
                cat for key, cat in seed_user["categories"].items()
                if key != "Groceries"
            )
            resp = _submit(auth_client, row, category_id=str(other.id))
            assert resp.status_code == 200, resp.data
            assert resp.headers["HX-Trigger"] == "gridRefresh"
            db.session.refresh(row)
            assert row.template.category_id == other.id
            assert row.category_id == other.id
            assert row.is_override is False

    def test_the_flags_land_on_the_definition_and_the_row_reads_them(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both flags on, then off; the row's answer is the definition's."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            assert row.tracks_purchases is False
            resp = _submit(
                auth_client, row, is_envelope="true", companion_visible="true",
            )
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.template.is_envelope is True
            assert row.template.companion_visible is True
            assert row.tracks_purchases is True
            assert row.visible_to_companion is True

            resp = _submit(
                auth_client, row, is_envelope="false", companion_visible="false",
            )
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.template.is_envelope is False
            assert row.template.companion_visible is False
            assert row.tracks_purchases is False
            assert row.visible_to_companion is False

    def test_a_recurring_rows_crafted_flag_is_dropped_by_the_schema(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """BAL-484's writer is gone: the row schema declares no flag.

        The write used to LAND on the row's sealed cell (inert to every
        reader in the application, readable by raw SQL) until the cutover
        dropped the cell.  It is unrepresentable through this door: the
        payload's flag is excluded before any code runs, so the DEFINITION
        -- the only place a flag lives -- holds what it held and the row
        reads that.
        """
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            resp = auth_client.patch(f"/transactions/{recurring.id}", data={
                "companion_visible": "true",
                "is_envelope": "true",
                "version_id": recurring.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, recurring.id)
            assert (row.template.companion_visible, row.template.is_envelope) == (
                False, False,
            )
            assert (row.visible_to_companion, row.tracks_purchases) == (False, False)

    def test_a_paybacks_crafted_flag_is_dropped_by_the_schema_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling R-BAL73: a row with no definition loads the row schema.

        A CC payback rendered both checkboxes and landed them on its own
        cells until the family's cutover (``balance:X-bi-7d-2``); it has no
        definition and no cell now, so its card renders no flag control and
        a crafted flag is excluded before any code runs, exactly as a
        recurring row's is.  The payback's own fields still save.
        """
        with app.app_context():
            envelope = _placed(
                seed_user, seed_periods_today[0], name="Card envelope",
                is_envelope=True,
            )
            payback = payback_row_of(
                db.session, seed_user, envelope, Decimal("5.00"),
                seed_periods_today[0].start_date,
            )
            db.session.commit()
            html = _card(auth_client, payback)
            assert 'name="is_envelope"' not in html
            assert 'name="companion_visible"' not in html
            resp = auth_client.patch(f"/transactions/{payback.id}", data={
                "companion_visible": "true",
                "is_envelope": "true",
                "notes": "crafted",
                "version_id": payback.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, payback.id)
            assert row.notes == "crafted"
            assert (row.visible_to_companion, row.tracks_purchases) == (False, False)


class TestTheOverrideFlip:
    """``mutations._apply_field_updates``: the MOVE half keys on ``recurs``."""

    def test_a_period_move_does_not_flip_a_rule_less_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A moved one-off keeps hearing its definition.

        The flag is what keeps the maintain and generate passes off a row the
        owner placed elsewhere; a rule-less definition runs no pass, and the
        flag would only hide the row from ``propagate_to_unruled_definition``
        (the twin's defect **BAL-493** on this table).
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            target = seed_periods_today[1]
            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "pay_period_id": str(target.id),
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(one_off)
            assert one_off.pay_period_id == target.id
            assert one_off.is_override is False

    def test_a_period_move_still_flips_a_recurring_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the flip the regeneration relies on is untouched."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            target = seed_periods_today[1]
            resp = auth_client.patch(f"/transactions/{recurring.id}", data={
                "pay_period_id": str(target.id),
                "version_id": recurring.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(recurring)
            assert recurring.pay_period_id == target.id
            assert recurring.is_override is True

    def test_a_typed_figure_restates_the_definition_and_does_not_flip(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The interim's flip ENDS here (R-BAL28's stated end; R-BAL29).

        This case asserted the opposite until leaf 7b-2 -- OWN on the row
        with the flag beside it, the figure kept safe from the definition's
        next edit by that flag.  Kayla's Kindle, `$162.25` on the
        definition, `$170.00` typed: the definition's ONE version now says
        `$170.00`, the row still reads it, and there is nothing for a flag
        to protect.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            companion = _rendered_companion(auth_client, row)
            assert companion == "162.25"
            resp = _submit(
                auth_client, row, estimated_amount="170.00",
            )
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.estimated_amount is None
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("170.00")
            versions = amount_versions(row.template)
            assert [(v.effective_date, v.amount) for v in versions] == [
                (row.due_date, Decimal("170.00")),
            ]
            assert newest_price(row.template) == Decimal("170.00")


class TestTheRestate:
    """``one_off.restate_price`` at the door: R-BAL29's walk and R-BAL37's re-attach."""

    def test_the_version_the_moved_date_reads_is_corrected_in_place(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL29's own walk: date moved 05-07 -> 05-10, then `$170.00` typed.

        ``set_amount(effective_on=row.due_date)`` would APPEND a 05-10
        version and leave the 05-07 one at `$162.25`, so moving the date
        back would silently re-read the old price.  The restate corrects the
        version the row's date READS, so the series stays one version --
        dated where it was born, saying the new figure.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            born_on = row.due_date
            moved_to = born_on + timedelta(days=3)
            resp = _submit(auth_client, row, due_date=moved_to.isoformat())
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.due_date == moved_to

            resp = _submit(auth_client, row, estimated_amount="170.00")
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            versions = amount_versions(row.template)
            assert [(v.effective_date, v.amount) for v in versions] == [
                (born_on, Decimal("170.00")),
            ]
            assert resolved_amount(row) == Decimal("170.00")

            # And moving the date BACK reads the corrected figure, not an
            # older one: the failure mode the ruling names.
            resp = _submit(auth_client, row, due_date=born_on.isoformat())
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert resolved_amount(row) == Decimal("170.00")

    def test_a_row_the_interim_detached_is_re_attached_by_its_next_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL37 (developer 2026-09-15): definition `$162.25`, row OWN `$170.00`, `$180.00` typed.

        Between leaves 7b-1 and 7b-2 a typed figure landed OWN on the row
        with ``is_override`` beside it, the definition left at the old
        price.  The next figure typed lands on the definition AND declares
        the row priced by it, flag cleared: one home.  Restating alone would
        have left the grid on `$170.00` with the typed `$180.00` invisible;
        keeping the row detached would have left the Recurring page at
        `$162.25`.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            # The interim's shape, as the door wrote it then.
            state_own_amount(row, Decimal("170.00"))
            row.is_override = True
            db.session.commit()
            assert row.amount_source_id is None
            assert resolved_amount(row) == Decimal("170.00")
            assert resolved_amount(row) != newest_price(row.template)

            resp = _submit(auth_client, row, estimated_amount="180.00")
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.estimated_amount is None
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("180.00")
            assert newest_price(row.template) == Decimal("180.00")
            assert len(amount_versions(row.template)) == 1

    def test_a_residue_row_re_priced_and_re_categorised_in_one_save_heals_whole(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The figure act runs BEFORE the definition's fields (adversarial review).

        Re-attached first, the row is one the propagation selects, so its
        category follows in the same request; the other order left it on the
        old category until the next edit.  And the definition's lock counter
        bumps ONCE: the restate and the fields flush together.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            state_own_amount(row, Decimal("170.00"))
            row.is_override = True
            db.session.commit()
            other = next(
                cat for key, cat in seed_user["categories"].items()
                if key != "Groceries"
            )
            definition_version = row.template.version_id

            resp = _submit(
                auth_client, row,
                estimated_amount="180.00", category_id=str(other.id),
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.is_override is False
            assert row.category_id == other.id
            assert row.template.category_id == other.id
            assert resolved_amount(row) == Decimal("180.00")
            assert row.template.version_id == definition_version + 1

    def test_a_row_of_a_MANY_row_definition_takes_the_figure_as_its_own(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL43 (developer 2026-09-16, leaf 7b-3): one occurrence among many.

        A bank-born envelope holds one row per paycheck; a budget typed on
        one paycheck's card is that occurrence's, so it lands OWN with the
        flag -- as a recurring definition's row does -- and the definition's
        one version stays: the sibling and every future paycheck's row read
        `$0.00` still.  Leaf 7b-2 restated the definition here, so `$100.00`
        typed on one paycheck re-budgeted every paycheck's (BAL-499).
        """
        with app.app_context():
            row = _placed(
                seed_user, seed_periods_today[0], amount="0.00", name="Amazon",
            )
            sibling = place_row_of(
                row.template, derived_span(seed_periods_today[1]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            assert sibling.template_id == row.template_id

            resp = _submit(auth_client, sibling, estimated_amount="100.00")
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            sibling = db.session.get(Transaction, sibling.id)
            assert str(sibling.estimated_amount) == "100.00"
            assert sibling.amount_source_id is None
            assert sibling.is_override is True
            assert resolved_amount(sibling) == Decimal("100.00")
            assert resolved_amount(row) == Decimal("0.00")
            assert newest_price(row.template) == Decimal("0.00")
            assert len(amount_versions(row.template)) == 1

    def test_a_recurring_rows_typed_figure_still_detaches_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: one occurrence among many owns its figure (R-BAL21)."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            companion = _rendered_companion(auth_client, recurring)
            assert companion == "100.00"
            resp = _submit(auth_client, recurring, estimated_amount="120.00")
            assert resp.status_code == 200, resp.data
            db.session.refresh(recurring)
            assert str(recurring.estimated_amount) == "120.00"
            assert recurring.amount_source_id is None
            assert recurring.is_override is True
            assert newest_price(recurring.template) == Decimal("100.00")


class TestTheDefinitionsCounterGuardsTheCard:
    """A placed row's card pins the DEFINITION's version too (adversarial review).

    A one-off's price lives on its definition since R-BAL29, so a figure-only
    save bumps the definition's counter and not the row's -- and two cards
    rendered before either saved would both have answered 200, the second
    silently overwriting the first's price, where the row's own counter
    caught that while the price lived on the row (commit C-18).
    """

    def test_the_second_of_two_stale_cards_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two cards, two prices: the first lands, the second meets a 409."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            first = form_fields(
                _card(auth_client, row), f"/transactions/{row.id}",
                attribute="hx-patch",
            )
            second = list(first)
            assert ("template_version_id", str(row.template.version_id)) in first

            def post(fields, figure):
                payload = [pair for pair in fields if pair[0] != "estimated_amount"]
                payload.append(("estimated_amount", figure))
                return auth_client.patch(
                    f"/transactions/{row.id}", data=MultiDict(payload),
                )

            assert post(first, "170.00").status_code == 200
            resp = post(second, "180.00")
            assert resp.status_code == 409
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert resolved_amount(row) == Decimal("170.00")

    def test_a_recurring_rows_card_pins_the_row_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the definition's pin renders for a placed row only."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            html = _card(auth_client, recurring)
            assert 'name="template_version_id"' not in html
            assert 'name="version_id"' in html


class TestAPeriodMoveRePlaces:
    """Ruling **R-BAL33** at the popover's period move (leaf 7b-2)."""

    def test_a_default_dated_one_off_takes_the_target_paychecks_start(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Born on its paycheck's start, moved: due on the new paycheck's start.

        ``occurs_on`` follows (R-BAL25), the flag stays off (R-BAL28's move
        half), and the price is unchanged (a flat series).
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            target = seed_periods_today[1]
            target_start = derived_span(target).start_date
            assert row.due_date == derived_span(seed_periods_today[0]).start_date
            resp = _submit(auth_client, row, pay_period_id=str(target.id))
            assert resp.status_code == 200, resp.data
            assert resp.headers["HX-Trigger"] == "gridRefresh"
            db.session.refresh(row)
            assert row.pay_period_id == target.id
            assert row.due_date == target_start
            assert row.occurs_on == target_start
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("162.25")

    def test_an_owner_dated_one_off_keeps_its_day(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: a date other than the source's start is the owner's."""
        with app.app_context():
            source_start = derived_span(seed_periods_today[0]).start_date
            stated = source_start + timedelta(days=6)
            row = _placed(seed_user, seed_periods_today[0], due_date=stated)
            target = seed_periods_today[1]
            resp = _submit(auth_client, row, pay_period_id=str(target.id))
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.pay_period_id == target.id
            assert row.due_date == stated
            assert row.occurs_on == stated

    def test_a_date_typed_beside_the_move_is_the_owners(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One save, both controls touched: the typed day wins over the default."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            target = seed_periods_today[1]
            typed = derived_span(target).start_date + timedelta(days=4)
            resp = _submit(
                auth_client, row,
                pay_period_id=str(target.id), due_date=typed.isoformat(),
            )
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.pay_period_id == target.id
            assert row.due_date == typed
            assert row.occurs_on == typed


class TestAMoveOntoASiblingsPaycheckOrDayIsRefused:
    """Leaf 7b-3's two guards at the popover (found by its adversarial review).

    A bank-born envelope holds one row per paycheck (R-BAL24), and every
    row of a definition answers its own day (R-BAL25) under the occurrence
    index.  A move onto the sibling's paycheck, or a date onto the
    sibling's day, met that index as a bare *Invalid reference*; each is
    a designed 400 now, and the row is left where it was.
    """

    def test_a_period_move_onto_the_paycheck_holding_a_sibling_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The target already holds this item's row: record into that one instead."""
        with app.app_context():
            row = _placed(
                seed_user, seed_periods_today[0], amount="0.00", name="Amazon",
            )
            sibling = place_row_of(
                row.template, derived_span(seed_periods_today[1]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            resp = _submit(
                auth_client, row, pay_period_id=str(seed_periods_today[1].id),
            )
            assert resp.status_code == 400
            assert b"already holds this item" in resp.data
            assert b"Invalid reference" not in resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            sibling = db.session.get(Transaction, sibling.id)
            assert row.pay_period_id == seed_periods_today[0].id
            assert sibling.pay_period_id == seed_periods_today[1].id
            assert row.due_date == derived_span(seed_periods_today[0]).start_date

    def test_a_date_onto_the_day_a_sibling_answers_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An owner-stated day equal to the sibling's: pick a different day."""
        with app.app_context():
            row = _placed(
                seed_user, seed_periods_today[0], amount="0.00", name="Amazon",
            )
            sibling = place_row_of(
                row.template, derived_span(seed_periods_today[1]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            was = row.due_date
            resp = _submit(
                auth_client, row, due_date=sibling.due_date.isoformat(),
            )
            assert resp.status_code == 400
            assert b"already due that day" in resp.data
            assert b"Invalid reference" not in resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.due_date == was
            assert row.occurs_on == was

    def test_a_move_onto_an_empty_paycheck_beside_a_sibling_still_lands(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the guards refuse the collision, not the sibling."""
        with app.app_context():
            row = _placed(
                seed_user, seed_periods_today[0], amount="0.00", name="Amazon",
            )
            place_row_of(
                row.template, derived_span(seed_periods_today[1]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            target = seed_periods_today[2]
            resp = _submit(auth_client, row, pay_period_id=str(target.id))
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.pay_period_id == target.id
            assert row.due_date == derived_span(target).start_date


class TestAFigureAndACategoryOnASiblingsCard:
    """R-BAL43's act order (found by 7b-3's adversarial review).

    A sibling's typed figure makes the row its OWN (act 5), and an OWN row
    is one the definition's propagation skips (act 4) -- so with the figure
    stated first, a category typed beside it reached the definition and
    every OTHER row but not the row it was typed on.  The figure is stated
    AFTER the propagation now.
    """

    def test_the_category_reaches_the_row_the_figure_was_typed_on(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One save: the figure is the row's own AND the category lands on it."""
        with app.app_context():
            row = _placed(
                seed_user, seed_periods_today[0], amount="0.00", name="Amazon",
            )
            sibling = place_row_of(
                row.template, derived_span(seed_periods_today[1]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            other = next(
                cat for key, cat in seed_user["categories"].items()
                if key != "Groceries"
            )
            resp = _submit(
                auth_client, sibling,
                estimated_amount="100.00", category_id=str(other.id),
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            sibling = db.session.get(Transaction, sibling.id)
            assert sibling.category_id == other.id
            assert sibling.is_override is True
            assert resolved_amount(sibling) == Decimal("100.00")
            assert row.template.category_id == other.id
            assert row.category_id == other.id
            assert resolved_amount(row) == Decimal("0.00")

    def test_a_sibling_already_its_own_is_skipped_by_a_later_category_edit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Reported at the leaf: an OWN sibling keeps its category until its own next edit."""
        with app.app_context():
            row = _placed(
                seed_user, seed_periods_today[0], amount="0.00", name="Amazon",
            )
            sibling = place_row_of(
                row.template, derived_span(seed_periods_today[1]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            resp = _submit(auth_client, sibling, estimated_amount="100.00")
            assert resp.status_code == 200, resp.data
            other = next(
                cat for key, cat in seed_user["categories"].items()
                if key != "Groceries"
            )
            groceries = seed_user["categories"]["Groceries"].id
            resp = _submit(auth_client, row, category_id=str(other.id))
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            sibling = db.session.get(Transaction, sibling.id)
            assert row.category_id == other.id
            assert row.template.category_id == other.id
            assert sibling.category_id == groceries


def _purchase_legs(entry_id):
    """Return ``(pay_period_id, category_id, net)`` per ledger account for one purchase's legs."""
    rows = db.session.execute(sa.text(
        "SELECT je.pay_period_id, la.category_id, SUM(p.amount) AS net "
        "FROM budget.journal_entries je "
        "JOIN budget.account_postings p ON p.journal_entry_id = je.id "
        "JOIN budget.ledger_accounts la ON la.id = p.ledger_account_id "
        "WHERE je.transaction_entry_id = :e "
        "GROUP BY je.pay_period_id, la.id, la.category_id "
        "HAVING SUM(p.amount) <> 0 "
        "ORDER BY je.pay_period_id, la.id"
    ), {"e": entry_id}).fetchall()
    return [(r.pay_period_id, r.category_id, Decimal(r.net)) for r in rows]


def _settled_envelope_with_a_posted_purchase(auth_client, seed_user, period, *, row=None):
    """Return a Paid envelope in *period* holding one posted `$40.00` purchase."""
    row = row if row is not None else _placed(seed_user, period, is_envelope=True)
    add_entry(
        db.session, seed_user, row, Decimal("40.00"),
        period.start_date, settled_on=period.start_date,
    )
    posting_service.sync_transaction_postings(row)
    db.session.commit()
    resp = auth_client.post(f"/transactions/{row.id}/mark-done")
    assert resp.status_code == 200, resp.data
    db.session.expire_all()
    row = db.session.get(Transaction, row.id)
    assert row.status.is_settled is True
    return row


def _submit_only(auth_client, row, keep, **changes):
    """PATCH with only the *keep* controls off the card plus *changes* -- any HTTP client's payload."""
    rendered = form_fields(
        _card(auth_client, row), f"/transactions/{row.id}", attribute="hx-patch",
    )
    payload = [pair for pair in rendered if pair[0] in keep and pair[0] not in changes]
    payload.extend(changes.items())
    return auth_client.patch(f"/transactions/{row.id}", data=MultiDict(payload))


class TestUnlockEditLock:
    """The PATCH handler's act order across the LOCK (plan step X-bi-7c).

    A finalised row's item fields are locked and the definition's propagation
    rewrites Projected rows only, so a request that LIFTS the lock and edits
    in one PATCH applies the transition first, one that SETTLES and edits
    applies the edit first, and the ledger is reconciled after the edits in
    both orders (ruling **R-BAL58**: *unlock, edit, lock*).  Found by moving
    the suite's one-off fixtures onto the producer: the posting-lifecycle
    cases post revert + re-category in one request, and on a placed row the
    definition took Rent while the row kept Groceries, so the next settle
    posted to Groceries.  The cancelled and credit arms, and the purchase-leg
    cases, are 7c-1's adversarial review's: a first build unlocked on the
    settled band alone and reconciled the ledger BEFORE the edits, so a
    revert + period move stranded an envelope's purchase legs in the old
    paycheck.
    """

    def test_a_revert_and_a_re_category_in_one_request_reach_the_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The crafted pair (the card disables the select on a locked row)."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            resp = auth_client.post(f"/transactions/{row.id}/mark-done")
            assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.status.is_settled is True
            other = next(
                cat for key, cat in seed_user["categories"].items()
                if key != "Groceries"
            )
            resp = _submit(
                auth_client, row,
                status_id=str(ref_cache.status_id(StatusEnum.PROJECTED)),
                category_id=str(other.id),
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.status.is_settled is False
            assert row.template.category_id == other.id
            assert row.category_id == other.id, (
                "the definition took the category and the row did not: the "
                "next settle would post to the OLD category"
            )
            assert row.is_override is False

    @pytest.mark.parametrize("locked_by", ["cancel", "old-credit"])
    def test_un_cancelling_or_un_crediting_and_a_re_category_reach_the_row(
        self, app, auth_client, seed_user, seed_periods_today, locked_by,
    ):
        """The lock is ``is_immutable``, not the settled band: both lifts unlock.

        The Credit status is an old Credit row's, planted
        (``tests._legacy_credit``): the press puts a row on the card as Paid
        since plan task plan#2929, so it no longer reaches that status.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            if locked_by == "old-credit":
                plant_old_credit(row)
                db.session.commit()
            else:
                resp = auth_client.post(f"/transactions/{row.id}/{locked_by}")
                assert resp.status_code == 200, resp.data
            db.session.refresh(row)
            assert row.status.is_immutable is True
            assert row.status.is_settled is False
            other = next(
                cat for key, cat in seed_user["categories"].items()
                if key != "Groceries"
            )
            resp = _submit(
                auth_client, row,
                status_id=str(ref_cache.status_id(StatusEnum.PROJECTED)),
                category_id=str(other.id),
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert row.template.category_id == other.id
            assert row.category_id == other.id

    def test_a_minimal_revert_and_move_carries_the_purchase_legs_to_the_new_paycheck(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A purchase's legs are keyed on the PARENT's paycheck (R-FM): the reconcile runs LAST.

        The payload is the smallest any client can send -- no item fields, so
        no propagation happens to reconcile the row -- and the legs move
        because the handler reconciles after the edits in the unlock order.
        """
        with app.app_context():
            row = _settled_envelope_with_a_posted_purchase(
                auth_client, seed_user, seed_periods_today[0],
            )
            entry_id = row.entries[0].id
            assert {p for p, _c, _n in _purchase_legs(entry_id)} == {
                seed_periods_today[0].id,
            }
            resp = _submit_only(
                auth_client, row, {"version_id", "template_version_id"},
                status_id=str(ref_cache.status_id(StatusEnum.PROJECTED)),
                pay_period_id=str(seed_periods_today[1].id),
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.pay_period_id == seed_periods_today[1].id
            assert row.status.is_settled is False
            after = _purchase_legs(entry_id)
            assert {p for p, _c, _n in after} == {seed_periods_today[1].id}, (
                f"the purchase legs stayed in the OLD paycheck: {after}"
            )

    @pytest.mark.parametrize("edit", ["pay_period_id", "category_id"])
    def test_a_recurring_envelopes_revert_and_edit_carry_its_purchase_legs(
        self, app, auth_client, seed_user, seed_periods_today, edit,
    ):
        """THE CONTROL on a recurring definition's row, which no propagation touches."""
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Groceries",
                category_key="Groceries", is_envelope=True,
            )
            generated = generate_row_of(template, seed_periods_today[0])
            db.session.commit()
            assert generated.recurs is True
            row = _settled_envelope_with_a_posted_purchase(
                auth_client, seed_user, seed_periods_today[0], row=generated,
            )
            entry_id = row.entries[0].id
            other = next(
                cat for key, cat in seed_user["categories"].items()
                if key != "Groceries"
            )
            target = (
                str(seed_periods_today[1].id) if edit == "pay_period_id"
                else str(other.id)
            )
            resp = _submit(
                auth_client, row,
                status_id=str(ref_cache.status_id(StatusEnum.PROJECTED)),
                **{edit: target},
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            after = _purchase_legs(entry_id)
            if edit == "pay_period_id":
                assert {p for p, _c, _n in after} == {seed_periods_today[1].id}, after
            else:
                assert {c for _p, c, _n in after if c is not None} == {other.id}, after

    def test_a_settle_and_an_untick_in_one_request_settle_on_the_rows_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL on the other order: the edit precedes the lock.

        Unticking *Track individual purchases* beside Status = Paid needs the
        definition's flag written BEFORE the settle, else the settle asks the
        envelope's purchases (none) and books `$0.00`.
        """
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0], is_envelope=True)
            assert row.tracks_purchases is True
            resp = _submit(
                auth_client, row,
                status_id=str(ref_cache.status_id(StatusEnum.DONE)),
                is_envelope="false",
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.status.is_settled is True
            assert row.template.is_envelope is False
            assert settled_figure(row) == Decimal("162.25")
