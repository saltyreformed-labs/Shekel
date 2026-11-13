"""A typed figure is restated on the definition, in place (rulings R-BAL21, R-BAL29, R-BAL37), and
the PATCH handler's act order: a figure after the propagation (R-BAL43) and unlock, edit, lock
(R-BAL58).
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services.amount_ownership import state_own_amount
from app.services.one_off import place_row_of
from app.services.template_amount_service import amount_versions
from app.services.row_valuation import settled_figure
from tests._test_helpers import derived_span, resolved_amount
from tests._definition_price import newest_price

from ._helpers import _placed, _submit, _recurring, _rendered_companion


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


class TestUnlockEditLock:
    """The PATCH handler's act order across the LOCK (plan step X-bi-7c).

    A finalised row's item fields are locked and the definition's propagation
    rewrites Projected rows only, so a request that LIFTS the lock and edits
    in one PATCH applies the transition first, one that SETTLES and edits
    applies the edit first (ruling **R-BAL58**: *unlock, edit, lock*).  Found
    by moving the suite's one-off fixtures onto the producer: the
    posting-lifecycle cases post revert + re-category in one request, and on a
    placed row the definition took Rent while the row kept Groceries, so the
    next settle posted to Groceries.  The cancelled and credit arms are 7c-1's
    adversarial review's: a first build unlocked on the settled band alone.
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

    @pytest.mark.parametrize("locked_by", ["cancel", "mark-credit"])
    def test_un_cancelling_or_un_crediting_and_a_re_category_reach_the_row(
        self, app, auth_client, seed_user, seed_periods_today, locked_by,
    ):
        """The lock is ``is_immutable``, not the settled band: both lifts unlock."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
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
