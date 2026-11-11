"""Companion route tests: a companion's entries and mark-done through the owner's routes, and the
add-purchase form's clock.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.row_valuation import settled_figure
from tests._test_helpers import figure_source_columns, generate_row_of
from tests._two_factor_sign_in import sign_in_seed_companion

from ._helpers import _make_template


# ── Entry Integration (via entries blueprint) ────────────────────────


class TestEntryIntegration:
    """Verify companion can use entry CRUD routes on visible transactions."""

    def test_companion_can_add_entry(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.5: Companion adds entry to visible tracked transaction.

        Entry is created with user_id == companion's ID.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/entries", data={
            "amount": "42.50",
            "description": "Kroger",
            "purchased_on": "2026-01-05",
        })
        assert resp.status_code == 200

        # Verify entry was created.
        entry = db.session.query(TransactionEntry).filter_by(
            transaction_id=txn.id,
        ).first()
        assert entry is not None
        assert entry.amount == Decimal("42.50")
        assert entry.description == "Kroger"

    def test_companion_entry_user_id_is_companion(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.6: Entry.user_id is the companion's ID, not the owner's.

        Verifies attribution: the companion's user_id is recorded
        on the entry for audit purposes.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        companion = seed_companion["user"]
        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/entries", data={
            "amount": "25.00",
            "description": "Target",
            "purchased_on": "2026-01-05",
        })
        assert resp.status_code == 200

        entry = db.session.query(TransactionEntry).filter_by(
            transaction_id=txn.id,
        ).first()
        assert entry.user_id == companion.id

    def test_companion_cannot_access_non_visible_entries(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.7: Companion gets 404 on entry route for non-visible transaction."""
        template = _make_template(
            seed_user, companion_visible=False, track=True, name="Hidden",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/entries", data={
            "amount": "10.00",
            "description": "Test",
            "purchased_on": "2026-01-05",
        })
        assert resp.status_code == 404

    def test_a_crafted_patch_on_a_generated_rows_cell_shows_nothing(
        self, app, db, auth_client, seed_user, seed_periods_today,
        seed_companion,
    ):
        """The owner PATCHes a generated row's ``companion_visible``; nothing changes.

        The popover renders no visibility control for a template-generated
        row, so only a crafted PATCH reaches for its cell -- **and since plan
        step ``balance:X-bi-7b`` the write does NOT land**: the schema the
        door loads for a recurring definition's row declares no flag, so the
        field is dropped before any code could reach a writer (finding
        **BAL-484**'s writer, deleted under ruling R-BAL20; this case
        asserted ``cell is True`` while the inert write plan step X-bi-1
        left unrefused still landed, and the cell itself went at the
        family's cutover, ``balance:X-bi-7d-2``).  The row's template is
        hidden, so the
        row stays off the companion page and its entries door stays 404:
        both surfaces decide by the one accessor, ``visible_to_companion``
        (plan step ``X-bi-1b``, finding **BAL-482**).  Through the
        application's own doors, end to end, which is what the model-level
        seal tests cannot see.
        """
        template = _make_template(
            seed_user, companion_visible=False, track=True, name="Hidden",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        # The positive control: a row the companion MAY see, in the same
        # period, so an empty page cannot pass the absence check below.
        shown = _make_template(
            seed_user, companion_visible=True, name="Shown",
        )
        generate_row_of(shown, seed_periods_today[0])
        db.session.commit()
        txn_id = txn.id

        resp = auth_client.patch(f"/transactions/{txn_id}", data={
            "companion_visible": "true",
            "version_id": txn.version_id,
        })
        assert resp.status_code == 200
        db.session.expire_all()
        # Nothing took the write: the definition -- the only home a flag has
        # -- holds what it held, and the row reads that.
        row = db.session.get(Transaction, txn_id)
        assert row.template.companion_visible is False
        assert row.visible_to_companion is False

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        assert b"Shown" in resp.data
        assert b"Hidden" not in resp.data
        resp = comp.get(f"/transactions/{txn_id}/entries")
        assert resp.status_code == 404

    def test_companion_cannot_guess_txn_id(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.8: Random/non-existent txn_id returns 404."""
        comp = sign_in_seed_companion(app)
        resp = comp.post("/transactions/999999/entries", data={
            "amount": "10.00",
            "description": "Test",
            "purchased_on": "2026-01-05",
        })
        assert resp.status_code == 404

    def test_companion_can_delete_entry(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion can delete an entry on a visible tracked transaction."""
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        entry = TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=seed_companion["user"].id,
            amount=Decimal("30.00"),
            description="Kroger",
            purchased_on=date(2026, 1, 5),
        )
        db.session.add(entry)
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.delete(
            f"/transactions/{txn.id}/entries/{entry.id}",
        )
        assert resp.status_code == 200

        deleted = db.session.get(TransactionEntry, entry.id)
        assert deleted is None

    def test_companion_can_edit_entry(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion can edit an entry on a visible tracked transaction."""
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        entry = TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=seed_companion["user"].id,
            amount=Decimal("30.00"),
            description="Kroger",
            purchased_on=date(2026, 1, 5),
        )
        db.session.add(entry)
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.patch(
            f"/transactions/{txn.id}/entries/{entry.id}",
            data={
                "amount": "45.00",
                # The edit form emits the direction beside the amount since
                # plan step ``bank_import:X-gj-2b-3``, and the route refuses
                # one without the other rather than guessing a sign.
                "direction": "charge",
                "description": "Kroger Updated",
            },
        )
        assert resp.status_code == 200

        db.session.refresh(entry)
        assert entry.amount == Decimal("45.00")
        assert entry.description == "Kroger Updated"


# ── Mark Done Integration ────────────────────────────────────────────


class TestMarkDoneIntegration:
    """Verify companion mark-done through the transactions route."""

    def test_companion_marks_visible_projected_as_paid(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.9: Companion marks visible PROJECTED transaction as Paid.

        Status changes from Projected to Done (Paid).
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        done_id = ref_cache.status_id(StatusEnum.DONE)
        assert txn.status_id == done_id

    def test_companion_mark_done_non_visible_rejected(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.10: Companion gets 404 for mark-done on non-visible transaction."""
        template = _make_template(
            seed_user, companion_visible=False, name="Mortgage",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 404

    def test_companion_mark_paid_returns_card_with_trigger(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion mobile Mark Paid swaps the card in place, no reload.

        The companion action bar posts ``render=mobile_card`` with
        ``can_edit=0``, so mark_done returns the re-rendered companion
        card (settled badge, no Mark Paid, and -- because can_edit is
        false -- no owner-only Edit Amount / Open Full) plus
        ``HX-Trigger: mobileCardSettled`` rather than the full-reload
        ``gridRefresh``.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()
        txn_id = txn.id

        comp = sign_in_seed_companion(app)
        resp = comp.post(
            f"/transactions/{txn_id}/mark-done",
            data={"render": "mobile_card", "card_prefix": "tp", "can_edit": "0"},
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert 'class="mobile-card-wrapper"' in body
        assert f'id="card-tp-{txn_id}"' in body
        assert "badge-done" in body
        assert "Mark Paid" not in body
        # can_edit=0 keeps the owner-only affordances out of the swap.
        assert "Edit Amount" not in body
        assert "Open Full" not in body
        assert resp.headers.get("HX-Trigger") == "mobileCardSettled"

    def test_companion_view_shows_paid_indicator_after_mark_done(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """After marking as Paid, companion view shows the Paid indicator.

        Mark the transaction done, then load the companion view
        and verify the "Paid" text appears.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        # Now load the companion view for this period.
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Paid" in html

    def test_companion_mark_done_records_the_entry_sum(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Mark-done on a tracked transaction settles it at its purchases.

        Arithmetic: entries = $100 + $50 = $150.

        **What it reads changed at plan step X-au-c3 and the FIGURE did not.**
        The close used to COPY the entry sum into ``actual_amount``; a
        ``purchases`` settlement stores no figure at all, because the row's own
        entries state it and a stored copy would need a reconciler to keep the
        two in step.  So the assertion asks the accessor rather than the
        column, and still asks for $150.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("100.00"), description="Kroger",
            purchased_on=date(2026, 1, 5),
        ))
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("50.00"), description="Walmart",
            purchased_on=date(2026, 1, 6),
        ))
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        assert settled_figure(txn) == Decimal("150.00")


class TestTheCompanionAddPurchaseFormReadsTheUsersClock:
    """The companion page's ``today`` is ``display_today()``, never the process's.

    **The owner's mobile card was corrected for this at plan step S1-c and the
    companion page was left behind**, on the surface that SHARES its macro:
    ``routes/companion._build_partial_context`` passed ``today=date.today()``
    into ``grid/_transaction_entries.html``, whose ``purchased_on`` input takes
    it as both the default ``value`` and the ``max``.  ``entry_service``
    judges that field against ``display_today()`` (ruling R-M,
    ``_reject_future_purchase_date``), so on any process not pinned to
    ``America/New_York`` the companion's own form defaulted to -- and capped at
    -- a date its own server refuses.  Found by an adversarial design review of
    plan step **C2-f2b** and fixed there, because that step was already editing
    the two lines above it; the review's other correct point was that a fix
    without a test is not a fix, which is what this class is.

    Latent in production, which pins ``TZ: America/New_York`` in both compose
    files.  NOT latent in CI, which runs ``TZ=Pacific/Kiritimati`` (UTC+14)
    precisely to catch this shape.

    **It pins WHICH CLOCK the route reads rather than arranging for the two to
    disagree**, for the reason
    ``test_grid.test_create.TestTheAddPurchaseFormReadsTheUsersClock`` states at
    length: the C library's zone is process-global and survives
    ``monkeypatch``'s env restore, so a ``TZ`` mutation leaks into every later
    test in the same xdist worker.  Substituting ``display_today`` for a
    sentinel is exact and side-effect free -- the rendered form carries it if
    and only if the route reads that function, which IS the defect.
    """

    #: A date no clock can produce on its own, so its appearance in the
    #: rendered form can only have come from ``display_today``.
    _SENTINEL = date(2019, 7, 4)

    def test_the_companion_card_form_defaults_to_the_users_civil_day(
        self, app, db, seed_user, seed_periods_today, seed_companion,
        monkeypatch,
    ):
        """The companion's add-purchase input comes from ``display_today``.

        Both halves are asserted.  The positive alone would pass a build that
        rendered the sentinel somewhere unrelated; the negative alone would
        pass a page that rendered no date at all.

        Negative control (standard 4): restoring ``today=date.today()`` in
        ``_build_partial_context`` fails this on
        ``assert 'value="2019-07-04"' in html``.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes import companion as companion_routes

        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        # The premise: the sentinel is not what any real clock answers, so a
        # route reading ``date.today()`` cannot produce it by coincidence.
        assert date.today() != self._SENTINEL
        monkeypatch.setattr(
            companion_routes, "display_today", lambda: self._SENTINEL,
        )

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'name="purchased_on"' in html, (
            "the add-purchase form must actually have rendered"
        )
        assert f'value="{self._SENTINEL.isoformat()}"' in html
        assert f'max="{self._SENTINEL.isoformat()}"' in html
        assert date.today().isoformat() not in html
