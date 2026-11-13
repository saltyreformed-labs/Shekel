"""Companion route tests: what each card renders, its entry progress, its Mark Paid button, its
inline entry list and its tap to expand.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.services import status_seam
from app.models.transaction_entry import TransactionEntry
from tests._test_helpers import figure_source_columns, generate_row_of, settlement_if_settling
from tests._two_factor_sign_in import sign_in_seed_companion

from ._helpers import _make_template


# ── Entry Data in Response HTML ──────────────────────────────────────


class TestEntryDataInHTML:
    """Verify companion view renders correct entry progress information."""

    def test_tracked_txn_with_entries_shows_progress(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible tracked transaction with entries shows "$X / $Y" progress.

        Arithmetic: entries=$200, estimated=$500.  HTML contains
        "200 / 500" (the {:,.0f} format without dollar signs in the span).
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("200.00"), description="Kroger",
            purchased_on=date(2026, 1, 5),
        ))
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$200 / $500" in html
        assert "$300 left" in html

    def test_tracked_txn_without_entries_shows_estimated(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible tracked transaction without entries shows estimated amount only.

        No progress bar, just the estimated amount.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$500" in html
        # No progress format -- should not contain " / ".
        assert "/ $500" not in html

    def test_non_tracked_txn_shows_estimated_amount(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible non-tracked transaction shows estimated amount."""
        template = _make_template(
            seed_user, companion_visible=True, track=False, name="Birthday Gift",
            amount="100.00",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Birthday Gift" in html
        assert "$100" in html

    def test_over_budget_shows_over_indicator(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Over-budget transaction shows "over" indicator with danger styling.

        Arithmetic: estimated=$100, entries=$120.  HTML contains
        "$120 / $100" and "$20 over".
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Gas",
            amount="100.00",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("120.00"), description="Shell",
            purchased_on=date(2026, 1, 5),
        ))
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$120 / $100" in html
        assert "$20 over" in html
        assert "text-danger" in html

    def test_progress_bar_pct_rendered_as_decimal_string(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """C9-4 (F-23 / MED-04 / E-16): the rendered ``data-progress-pct``
        attribute carries the Decimal-quantised percentage produced by
        :func:`entry_service.pct_complete`, not the legacy
        ``float(Decimal_expr)`` value the companion route used to compute
        inline.

        Hand arithmetic: total = $55.50, target = $100.00.
        55.50 / 100.00 * 100 = 55.50; quantise(0.01) = Decimal("55.50").
        The attribute appears verbatim because Jinja's
        ``[ed.pct, 100]|min`` filter returns the smaller of the Decimal
        and 100 (still Decimal("55.50") here), and Decimal's __str__
        renders the canonical "55.50".
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
            amount="100.00",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("55.50"), description="Kroger",
            purchased_on=date(2026, 1, 5),
        ))
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The progress-bar width is driven by data-progress-pct; the
        # Decimal-quantised value is rendered as-is.
        assert 'data-progress-pct="55.50"' in html
        # The aria-valuenow companion uses int() on the Decimal -- locks
        # that the Decimal pct survives the template's |int filter
        # without producing scientific notation or other surprise forms.
        assert 'aria-valuenow="55"' in html


# ── Mark as Paid Button Visibility ───────────────────────────────────


class TestMarkPaidButtonVisibility:
    """Verify the Mark Paid affordance appears only for PROJECTED transactions.

    Re-pinned in mobile-first v3 plan Commit 13 / D-B: companion adopted
    the shared ``grid/_mobile_this_period.html`` partial + ``render_row_card``
    macro + ``_mobile_card_actions.html`` action bar, which use the shorter
    "Mark Paid" button label and the ``badge-done`` settled badge.  The
    legacy companion ``_transaction_card.html`` (removed in the mobile
    follow-up F-4 cleanup; preserved in git history) used
    "Mark as Paid" + ``bi-check-circle-fill``; those strings no longer
    render through the companion route's HTML so the assertions move to
    the shared-design vocabulary.  The semantic contract (Projected =
    action affordance present, Settled = badge only) is unchanged.
    """

    def test_projected_transaction_shows_mark_paid_button(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """PROJECTED transaction shows the 'Mark Paid' button.

        The shared per-card action bar at
        ``_mobile_card_actions.html:68`` reads ``<i class="bi bi-check2">
        </i> Mark Paid`` (no "as") for any non-settled status -- the
        text was shortened when the action bar was introduced in
        mobile-first v3 plan Commit 7 and companion picked it up by
        adopting the partial in Commit 13.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Mark Paid" in html

    def test_paid_transaction_shows_paid_indicator(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Paid (Done) transaction shows the settled badge, not a button.

        The shared ``render_row_card`` macro emits
        ``<span class="badge-done">&#10003;</span>`` for any
        ``status.is_settled`` row (see ``_grid_row_macros.html:166-170``)
        instead of the legacy companion ``<i class="bi
        bi-check-circle-fill"></i> Paid`` rendering; the action bar's
        Mark Paid button renders only for Projected rows (the
        ``txn.status_id == STATUS_PROJECTED`` guard in
        ``_mobile_card_actions.html``, tightened by grid audit D2).
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        status_seam.apply_status_change(txn, ref_cache.status_id(StatusEnum.DONE), settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)))
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Settled rows render the shared ``badge-done`` checkmark
        # span (the macro's settled-badge path) instead of the legacy
        # ``bi-check-circle-fill`` Bootstrap icon.
        assert "badge-done" in html
        # The Mark Paid button must not appear -- the action bar guards
        # against rendering it for settled statuses.
        assert "Mark Paid" not in html

    def test_credit_transaction_hides_mark_paid_button(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """A Credit-status card offers no Mark Paid button.

        Credit -> Paid is an illegal transition (Credit only reverts to
        Projected), yet the old not-is_settled guard still rendered the
        button on Credit cards -- a tap then 400ed invisibly.  The
        projected-only guard (grid audit D2, ruled 2026-07-11) removes
        the dead affordance; the CC chip still marks the state.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        txn.status_id = ref_cache.status_id(StatusEnum.CREDIT)
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "badge-credit" in html
        assert "Mark Paid" not in html


# ── Entry List Inline Rendering ──────────────────────────────────────


class TestEntryListInlineRendering:
    """Verify envelope transactions render the entries list inline.

    The per-card HTMX ``hx-trigger="load"`` lazy-loader was replaced
    by a server-side ``{% include %}`` of
    ``grid/_transaction_entries.html`` because the lazy-load shape
    fanned out to ~60 parallel GETs on ``entries.list_entries`` per
    grid page load (6 visible periods x ~10 envelope templates),
    which exceeds the ``RATELIMIT_DEFAULT`` ceiling of "30 per
    minute" and left the over-limit cards stuck on a never-
    resolving loading spinner.  The macro now consumes
    ``entry_lists`` (built by
    ``entry_service.build_entry_lists_dict`` and passed by both
    the grid and companion routes via ``render_template``) plus
    ``today``, and includes the entries template inline.
    """

    def test_envelope_txn_renders_entries_inline(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Envelope transaction card renders the entries list server-side.

        Asserts the inline entries list div is in the response body and
        that the entries route URL appears (used by the add-entry form's
        ``hx-post`` inside the inline include).  The companion view
        renders through the This Period partial (``id_prefix='tp'``), so
        the inline list carries the per-tab-namespaced id
        ``entry-list-tp-<id>`` -- distinct from the full-edit popover's
        bare ``entry-list-<id>`` so the two never collide.  The CRUD
        url_for output carries ``host=tp`` so the namespace survives the
        round-trip.  The old ``hx-trigger="load"`` lazy-load attribute
        MUST NOT be present -- its presence on a recurring envelope card
        would re-introduce the rate-limit storm.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Inline entries section is in the DOM, namespaced to the
        # This Period tab (the add-entry form inside
        # _transaction_entries.html hx-posts to the entries route, so
        # the URL appears and carries the host prefix).
        assert f'id="entry-list-tp-{txn.id}"' in html
        assert f"/transactions/{txn.id}/entries" in html
        assert "host=tp" in html
        # No HTMX load-trigger -- the macro no longer emits the
        # lazy-loader wrapper.
        assert 'hx-trigger="load"' not in html

    def test_non_envelope_txn_has_no_entries_section(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Non-envelope transaction card does NOT render an entries section.

        Only envelope templates (``is_envelope=True``) get the inline
        entries section.  A non-envelope txn must produce neither
        the ``entry-list-<id>`` div nor any ``/entries`` URL in the
        response.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=False, name="Birthday",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f'id="entry-list-{txn.id}"' not in html
        assert f"/transactions/{txn.id}/entries" not in html


# ── Card Tap-to-Expand Reachability ──────────────────────────────────


class TestCardTapToExpand:
    """Verify companion cards are tappable so the action bar is reachable.

    The Mark Paid button, the inline entries list, and the add-purchase
    form all live inside the collapsed ``.mobile-card-expansion`` that
    opens only when the delegated tap handler in
    ``app/static/js/mobile_grid.js`` matches
    ``.mobile-txn-card[data-mobile-txn-id]``.  The companion route
    renders the shared card with ``can_edit=False``; before the fix that
    flag suppressed ``data-mobile-txn-id``, so companion cards emitted
    the action bar into the HTML but the card could never be tapped
    open -- the spouse saw the line items yet could not view existing
    purchases, add a purchase, or mark anything paid.

    These tests assert the tappability precondition (the attribute)
    rather than mere string presence.  The string-presence assertions
    in the other companion tests pass even when the card is sealed shut,
    which is exactly why the interactivity regression slipped through.
    """

    def test_companion_card_has_tap_to_expand_attribute(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion (can_edit=False) card carries data-mobile-txn-id.

        Without the attribute the tap handler in mobile_grid.js skips
        the card and the expansion never opens, sealing the entries
        list, the add-purchase form, and Mark Paid behind a collapse
        the companion cannot trigger.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f'data-mobile-txn-id="{txn.id}"' in html

    def test_companion_card_keeps_owner_only_controls_hidden(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """can_edit=False still drops the owner-only Edit/Open buttons.

        Tap-to-expand is universal, but the owner-only affordances
        (Edit Amount, Open Full) must remain absent for the companion
        even though the card is now reachable.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = sign_in_seed_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Edit Amount" not in html
        assert "Open Full" not in html
