"""Transaction entry route tests: settled_on on the entry edit form, and the host parameter's
grid-cell refresh after each mutation.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.utils.dates import display_today

from tests._test_helpers import (
    an_entered_day,
    append_balance_assertion,
    figure_source_columns,
    settle_instant_on,
)
from tests._two_factor_sign_in import sign_in_seed_companion
from app.services.settle_day import record_settle_day

from ._helpers import (
    _PURCHASED_ON,
    _ASSERTED_ON,
    _add_entry,
    _create_visible_tracked_txn,
    _create_other_user_txn,
)


class TestTheSettledOnEditPath:
    """``settled_on`` on the entry edit form -- the fact the toggle stood in for.

    The write door a user has for "when did my bank actually take this",
    replacing the toggle (plan step S1-c).  It is on the EDIT form and
    deliberately not on the ADD form: at the moment a purchase is recorded
    there is nothing to have observed, and a value typed then would be a
    forecast in a fact column.
    """

    def test_flipping_a_TICKED_purchase_to_card_releases_its_link(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A reproducible unhandled 500, found by X-f3b's trace 2026-08-15.

        ``ck_transaction_entries_card_purchase_clears_nowhere`` (plan step
        X-f3a-1) refuses a purchase that is BOTH on a card and linked to a
        statement -- a card purchase never touches this account, so the claim is
        false by construction.  The edit door released the link when the POSTING
        DAY moved and not when the CARD flag was set, so PATCHing ``is_credit``
        on a purchase the reconcile panel had ticked raised an
        ``IntegrityError`` no handler catches.  Reproduced on a production clone
        against entry 87 before the fix.

        The remedy is the one already beside it: the edit is a CORRECTION, so
        the fact that contradicts the observation wins and the observation is
        released.  Asserted on both columns -- a fix that swallowed the flip
        would satisfy a status check alone.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=_PURCHASED_ON,
            )
            record_settle_day(entry, an_entered_day(_PURCHASED_ON))
            statement = append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1000.00"), settle_instant_on(_ASSERTED_ON),
            )
            entry.reconciled_by_id = statement.id
            db.session.commit()
            entry_id = entry.id

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry_id}",
                data={"is_credit": "true"},
            )

            assert resp.status_code == 200
            db.session.expire_all()
            flipped = db.session.get(TransactionEntry, entry_id)
            assert flipped.is_credit is True
            assert flipped.reconciled_by_id is None, (
                "a card purchase clears nowhere on this account, so the "
                "statement it named is released rather than left to violate "
                "the CHECK"
            )

    def test_the_edit_form_records_a_posting_day(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCHing ``settled_on`` stores it and fires ``balanceChanged``.

        The trigger matters as much as the column: recording a posting day can
        move every rendered projection (it is what lets the reservation stop
        holding the purchase back), so a surface showing a balance must
        recompute.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )
            assert entry.settled_on is None

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": "2026-01-07"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == date(2026, 1, 7)

    def test_an_empty_value_CLEARS_the_posting_day(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """An emptied date input puts the purchase back among the outstanding.

        This is a real user action -- "I marked this posted and my statement
        does not actually show it" -- and it is the reason the schema field
        carries ``allow_none``: without it an empty input would be dropped as
        "not provided" and the wrong observation would be unretractable
        through the UI.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )
            record_settle_day(entry, an_entered_day(date(2026, 1, 7)))
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": ""},
            )
            assert resp.status_code == 200

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_a_posting_day_before_the_purchase_is_refused(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """Money cannot leave the account before it was spent.

        The door's own check (``entry_service._reject_settled_before_purchase``)
        so the user gets a message naming both dates rather than a 500 from the
        ``ck_transaction_entries_settled_not_before_purchase`` IntegrityError.

        400, not 422: the date parses and the SCHEMA is satisfied, so this is
        the service refusing a semantically impossible pair rather than the
        form failing validation -- the same split every other entries route
        makes.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": "2026-01-04"},
            )

            assert resp.status_code == 400
            assert b"before you make it" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_a_posting_day_after_today_is_REFUSED(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The upper bound plan step X-f3b added, and why it inverted.

        **This test asserted the OPPOSITE until X-f3b** (ruling **R-FM**), on a
        reason that was true then: a forward posting day was the conservative
        direction, because no assertion closed over it, so the purchase stayed
        outstanding and its whole budget stayed reserved.  A recorded posting
        day is now the moment the money leaves the book, so a forward one
        RELEASES the reservation today and books the cash later -- already-spent
        money back in today's projection, which is exactly what
        ``status_seam.reject_future_settle_day`` refuses on a transaction and
        why the two columns' rationales no longer differ.

        Nothing expressible is lost: "I bought this and my bank has not taken it
        yet" is ``settled_on`` LEFT EMPTY, which is what that state has always
        meant and which holds the whole budget back.  Measured before the bound
        was added: ZERO of 91 production purchases carried a forward day.

        The refusal must also leave the stored value ALONE -- a guard that
        refuses and writes anyway is worse than no guard.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )
            forward = display_today() + timedelta(days=3)

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": forward.isoformat()},
            )

            assert resp.status_code == 400
            assert b"cannot be in the future" in resp.data
            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_another_users_entry_cannot_be_dated(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """PATCHing another user's entry returns 404 and writes nothing.

        Carried over from the retired toggle class: the ownership guard is the
        same one on the same shape of door, and dropping the toggle must not
        drop its IDOR coverage.
        """
        with app.app_context():
            other = _create_other_user_txn()
            other_entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=other["transaction"].id,
                account_id=other["transaction"].account_id,
                owner_id=other["transaction"].user_id,
                user_id=other["user"].id,
                amount=Decimal("50.00"),
                description="Other",
                purchased_on=date(2026, 1, 5),
                is_credit=False,
            )
            db.session.add(other_entry)
            db.session.commit()

            resp = auth_client.patch(
                f"/transactions/{other['transaction'].id}"
                f"/entries/{other_entry.id}",
                data={"settled_on": "2026-01-07"},
            )
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, other_entry.id,
            ).settled_on is None

    def test_a_cross_transaction_entry_id_cannot_be_dated(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The parameter-confusion guard, carried over from the toggle class.

        An entry id that does not belong to the transaction in the URL is 404,
        so a companion cannot reach a non-visible transaction's entries by
        pairing them with a visible transaction's id.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")
            other_txn = _create_visible_tracked_txn(
                seed_user, seed_periods,
            )["transaction"]

            resp = auth_client.patch(
                f"/transactions/{other_txn.id}/entries/{entry.id}",
                data={"settled_on": "2026-01-07"},
            )
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None


# ---- Surface routing: host param + desktop OOB cell refresh -------------

class TestEntryMutationSurfaces:
    """The ``host`` query param and the desktop OOB grid-cell refresh.

    Every entries-CRUD control in ``grid/_transaction_entries.html``
    ships ``?host=<prefix>`` so the route's re-render reconstructs the
    same entry-list root id the request's ``hx-target`` named (bare
    ``entry-list-<id>`` for the desktop popover, ``entry-list-tp-<id>``
    for the inline mobile/companion card list).  Desktop-popover
    mutations additionally carry an out-of-band re-render of the parent
    transaction's grid cell, because an entry mutation changes the
    envelope's "spent / budget" progress display and the primary swap
    only replaces the entry list inside the popover -- without the OOB
    fragment the on-grid amount stays stale until a full page reload
    (the 2026-06-11 grid-bug fix batch).
    """

    def test_create_carries_oob_cell_with_updated_progress(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A popover-surface create returns the OOB cell with new progress.

        The envelope budget is $500 (seed_entry_template) and the new
        entry is $50, so the re-rendered cell's progress display must
        read ``$50 / $500`` (entry total / estimated).

        **It read ``50 / 500`` until plan step ``bank_import:X-gj-2b-3``**
        (developer ruling 2026-09-01).  The cell formatted these two figures
        with a raw ``"{:,.0f}".format`` rather than the ``money`` macro gate
        B7 makes the one currency formatter; the raw spelling could not show a
        sign while every purchase was positive, and ruling
        **bank_import:R-II** made a refund a NEGATIVE purchase.  The macro is
        what the mobile card beside it has always used for the same two
        numbers.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # Primary swap body: the popover's bare entry-list id.
            assert f'id="entry-list-{txn.id}"' in html
            # OOB fragment: the grid cell wrapper rides along...
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html
            # ...carrying the refreshed spent/budget progress display.
            assert "$50 / $500" in html

    def test_update_carries_oob_cell(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A popover-surface amount update returns the OOB cell.

        $50 entry updated to $75 against the $500 budget -> the cell
        progress must read ``$75 / $500`` -- through the ``money`` macro since
        plan step ``bank_import:X-gj-2b-3``, for the reason
        :meth:`test_create_carries_oob_cell_with_updated_progress` states.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"amount": "75.00", "direction": "charge"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html
            assert "$75 / $500" in html

    def test_delete_carries_oob_cell(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A popover-surface delete returns the OOB cell.

        Deleting the only $50 entry leaves zero spent; with no entries
        the cell drops the progress display and shows the estimated
        amount (``500``) again.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(txn, seed_user, "50.00", "Kroger")

            resp = auth_client.delete(
                f"/transactions/{txn.id}/entries/{entry.id}",
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html
            assert "50 / 500" not in html

    def test_recording_a_posting_day_carries_oob_cell(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A popover-surface posting-day edit returns the OOB cell.

        Successor to the cleared-toggle case (plan step S1-c): the toggle
        route is deleted and ``settled_on`` on the edit form is the door that
        records the same fact.  It needs the OOB cell for the same reason the
        toggle did -- recording a posting day changes what the envelope holds
        back, so the on-grid "spent / budget" figure behind the popover goes
        stale without it.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _add_entry(
                txn, seed_user, "50.00", "Kroger",
                purchased_on=date(2026, 1, 5),
            )

            resp = auth_client.patch(
                f"/transactions/{txn.id}/entries/{entry.id}",
                data={"settled_on": "2026-01-07"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html

    def test_host_tp_reconstructs_inline_card_list_id(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """``?host=tp`` re-renders under the inline card's prefixed id.

        The inline mobile/companion card list targets
        ``#entry-list-tp-<id>``; the response must reconstruct that id
        (and thread the host through its own CRUD controls) so the swap
        lands on the element the request named, and it must NOT carry
        the desktop OOB cell -- the companion page has no
        ``#txn-cell-<id>`` element to swap into.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries?host=tp",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'id="entry-list-tp-{txn.id}"' in html
            assert f'id="entry-list-{txn.id}"' not in html
            # CRUD controls inside the list re-carry the host.
            assert "host=tp" in html
            # No desktop OOB cell on the inline-card surface.
            assert f'id="txn-cell-{txn.id}"' not in html

    def test_invalid_host_degrades_to_popover_surface(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """A host outside the short-token shape renders the bare surface.

        Free-form input must not be echoed into a DOM id; the route
        treats it as the popover surface (bare id + OOB cell).
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.post(
                f"/transactions/{txn.id}/entries?host=NOT%20a%20token!",
                data={
                    "amount": "50.00",
                    "description": "Kroger",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'id="entry-list-{txn.id}"' in html
            assert "NOT a token" not in html
            assert f'<div id="txn-cell-{txn.id}" hx-swap-oob="true">' in html

    def test_list_entries_honors_host(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """GET list (edit/cancel buttons) reconstructs the prefixed id too."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(
                f"/transactions/{txn.id}/entries?host=tp",
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f'id="entry-list-tp-{txn.id}"' in html
            assert f'id="entry-list-{txn.id}"' not in html

    def test_full_edit_lazy_entry_list_swaps_outerhtml(
        self, app, auth_client, seed_user, seed_periods,
        seed_entry_template,
    ):
        """The popover's lazy entries container replaces itself outerHTML.

        The list_entries response's ROOT element is another div with
        this same ``entry-list-<id>`` id; the default innerHTML swap
        nested two identically-id'd elements until the first CRUD swap
        collapsed them -- invalid HTML, and any other
        ``#entry-list-<id>`` resolution (CRUD targets, OOB fragments)
        bound ambiguously in the interim.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")
            assert resp.status_code == 200
            html = resp.data.decode()
            start = html.index('hx-trigger="load"')
            tag = html[start:html.index(">", start)]
            assert 'hx-swap="outerHTML"' in tag
            assert f'id="entry-list-{txn.id}"' in tag

    def test_companion_never_receives_oob_cell(
        self, app, auth_client, seed_user, seed_periods,
        seed_companion,
    ):
        """A companion stripping ``host`` must not get the owner's cell.

        The OOB fragment is the OWNER's desktop-grid cell, whose
        aria-label/title markup includes ``txn.notes`` -- a field no
        companion-reachable surface renders.  ``host`` is
        client-controlled, so the gate must be ownership, not the
        param: a companion request without ``host`` gets the entry
        list only.
        """
        with app.app_context():
            data = _create_visible_tracked_txn(seed_user, seed_periods)
            txn = data["transaction"]
            txn.notes = "owner-private-note"
            db.session.commit()

            comp = sign_in_seed_companion(app)
            resp = comp.post(
                f"/transactions/{txn.id}/entries",
                data={
                    "amount": "45.00",
                    "description": "Aldi",
                    "purchased_on": "2026-01-05",
                },
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # Entry list still returned (the mutation itself is allowed)...
            assert f'id="entry-list-{txn.id}"' in html
            # ...but no owner-only OOB cell, and no notes leak.
            assert f'id="txn-cell-{txn.id}"' not in html
            assert "owner-private-note" not in html
