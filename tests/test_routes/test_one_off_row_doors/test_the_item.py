"""The item is edited on the definition: name, category and both flags render on a placed row's
card and land on its definition, and the card pins the definition's version too.
"""

import re
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.category import Category
from app.models.transaction import Transaction
from app.services.one_off import OneOffToPlace, place_one_off
from werkzeug.datastructures import MultiDict
from tests._test_helpers import derived_span, payback_row_of, resolved_amount
from tests.test_routes._statement_forms import form_fields

from ._helpers import _placed, _card, _submit, _recurring


def _category_select(html):
    """Return the card's category ``<select>`` markup alone."""
    match = re.search(
        r'<select name="category_id".*?</select>', html, flags=re.S,
    )
    assert match is not None
    return match.group(0)


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
        (``list_active_categories``); a first draft copied the transfer
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
