"""A tracked row's cell: its progress display ("X / Y"), its tooltip's entry breakdown, and
entry_sums reaching the grid page.
"""

from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.services import transaction_service

from tests._test_helpers import current_pay_period

from ._helpers import _create_tracked_txn, _create_plain_txn, _add_entry


class TestCellProgressDisplay:
    """Tests for progress display via the GET /transactions/<id>/cell endpoint."""

    def test_tracked_projected_shows_progress(self, app, auth_client,
                                               seed_user, seed_periods_today):
        """Cell shows 'X / Y' format for tracked projected txn with entries.

        Arithmetic: 2 entries @ $150 + $80 = $230 spent on $500 budget.
        Cell should display '230 / 500' (no dollar sign, no cents).
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            assert b"$230 / $500" in resp.data

    def test_over_budget_has_danger_class(self, app, auth_client,
                                          seed_user, seed_periods_today):
        """Over-budget progress cell includes text-danger styling.

        Arithmetic: entries total $530 on $500 budget -> over by $30.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("300.00"))
            _add_entry(txn, seed_user, Decimal("230.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            assert b"$530 / $500" in resp.data
            assert b"text-danger" in resp.data
            assert b"fw-semibold" in resp.data

    def test_under_budget_no_danger_class(self, app, auth_client,
                                           seed_user, seed_periods_today):
        """Under-budget progress cell does NOT have text-danger styling.

        Arithmetic: entry total $100 on $500 budget -> $400 remaining.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("100.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            assert b"$100 / $500" in resp.data
            # The progress span should NOT have text-danger.
            # Check that the progress span uses font-mono without danger.
            assert b'class="font-mono"' in resp.data

    def test_no_entries_shows_standard_estimated(self, app, auth_client,
                                                  seed_user, seed_periods_today):
        """Tracked txn with no entries shows standard estimated amount.

        No progress format -- just '500' in standard font-mono span.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            html = resp.data.decode()
            # Standard display: just the estimated amount.
            assert ">$500</span>" in html
            # Progress format must NOT appear.  **The needle carries the
            # currency mark** (developer ruling 2026-09-01,
            # ``bank_import:R-IM``): the cell renders through ``money`` now, so
            # progress would read ``$X / $500`` -- and the old ``"/ 500"``
            # needle would be absent from BOTH states, passing for the wrong
            # reason.  Re-pointed rather than dropped.
            assert "/ $500" not in html

    def test_done_shows_actual_not_progress(self, app, auth_client,
                                             seed_user, seed_periods_today):
        """Paid (DONE) txn shows what it RECORDED, not progress format.

        Entry total is $330 on a $500 budget.  The close records the
        ``purchases`` basis, whose figure is those entries, so the cell shows
        '330' and not '330 / 500'.

        **Settled through the real verb since plan step X-au-c3.**  The fixture
        assigned ``status_id`` and the figure directly, which the settlement
        record's own CHECKs refuse -- and a fixture that writes a close by hand
        cannot grade what a close produces.  The verb picks the entries branch
        itself, so the basis under test is the one the app writes.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("200.00"))
            _add_entry(txn, seed_user, Decimal("130.00"))
            db.session.flush()

            transaction_service.settle_transaction(txn)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            html = resp.data.decode()
            # Should show actual amount, not progress format.
            assert "/ 500" not in html

    def test_non_tracked_unchanged(self, app, auth_client,
                                    seed_user, seed_periods_today):
        """Non-tracked transaction renders standard amount (regression).

        A plain one-off (its definition no envelope): shows '200' in font-mono span.
        """
        with app.app_context():
            txn = _create_plain_txn(seed_user, seed_periods_today)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            assert resp.status_code == 200
            html = resp.data.decode()
            assert ">$200</span>" in html
            # Re-pointed with the currency mark, for the reason
            # ``test_no_entries_shows_standard_estimated`` states.
            assert "/ $200" not in html


class TestCellProgressTooltip:
    """Tests for the enhanced tooltip on tracked transactions with entries."""

    def test_tooltip_remaining_under_budget(self, app, auth_client,
                                             seed_user, seed_periods_today):
        """Tooltip shows spent/budget and 'remaining' when under budget.

        Arithmetic: $230 spent on $500 budget -> $270 remaining.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "$230.00 / $500.00" in html
            assert "$270.00 remaining" in html
            assert "2 entries" in html

    def test_tooltip_over_budget(self, app, auth_client,
                                  seed_user, seed_periods_today):
        """Tooltip shows 'over' when over budget.

        Arithmetic: $530 spent on $500 budget -> $30 over.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("300.00"))
            _add_entry(txn, seed_user, Decimal("230.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "$530.00 / $500.00" in html
            assert "$30.00 over" in html

    def test_tooltip_singular_entry(self, app, auth_client,
                                     seed_user, seed_periods_today):
        """Tooltip says '1 entry' (singular) for a single entry."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("100.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "1 entry" in html
            # Must NOT say "1 entries".
            assert "1 entries" not in html

    def test_tooltip_plural_entries(self, app, auth_client,
                                     seed_user, seed_periods_today):
        """Tooltip says '3 entries' (plural) for multiple entries."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("50.00"), description="Store A")
            _add_entry(txn, seed_user, Decimal("60.00"), description="Store B")
            _add_entry(txn, seed_user, Decimal("70.00"), description="Store C")
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "3 entries" in html

    def test_tooltip_credit_note(self, app, auth_client,
                                  seed_user, seed_periods_today):
        """Tooltip mentions CC portion when credit entries exist.

        Arithmetic: $150 debit + $100 credit = $250 total.
        Credit note: 'includes $100.00 on CC'.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("100.00"), is_credit=True)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "includes $100.00 on CC" in html

    def test_tooltip_no_credit_note_when_zero(self, app, auth_client,
                                               seed_user, seed_periods_today):
        """Tooltip omits CC note when no credit entries exist."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("200.00"))
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            assert "on CC" not in html

    def test_standard_tooltip_no_entries(self, app, auth_client,
                                          seed_user, seed_periods_today):
        """Non-entry txn gets standard tooltip with status name."""
        with app.app_context():
            txn = _create_plain_txn(seed_user, seed_periods_today)
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/cell")

            html = resp.data.decode()
            # Standard tooltip includes status name.
            assert "Projected" in html
            # Enhanced tooltip markers must be absent.
            assert "remaining" not in html
            assert "entries" not in html


class TestGridPageEntrySums:
    """Integration test: entry_sums flows through the grid page render."""

    def test_grid_page_shows_progress(self, app, auth_client,
                                       seed_user, seed_periods_today):
        """GET /grid renders progress format for tracked txns with entries.

        Creates the transaction in the current period so it appears in
        the default grid view.
        """
        with app.app_context():
            # Find the current period so the txn is visible in the grid.
            current = current_pay_period(
                seed_user["user"].id,
            )
            # Find which seed_periods_today index matches the current period.
            period_idx = next(
                (i for i, p in enumerate(seed_periods_today) if p.id == current.id),
                0,
            )

            txn, _ = _create_tracked_txn(
                seed_user, seed_periods_today, period_index=period_idx,
            )
            payday = seed_periods_today[period_idx].start_date
            _add_entry(txn, seed_user, Decimal("180.00"), purchased_on=payday)
            _add_entry(
                txn, seed_user, Decimal("70.00"),
                purchased_on=payday - timedelta(days=1),
            )
            db.session.commit()

            resp = auth_client.get("/grid")

            assert resp.status_code == 200
            # The desktop grid cell should show progress format.
            # 180 + 70 = 250 spent on 500 budget.
            assert b"$250 / $500" in resp.data
            # **And the out-of-period badge, which puts /grid's OWN span map
            # under measurement for the first time** (adversarial review of
            # plan step C4-a-3).  The two purchases are dated on the payday and
            # the day before it, so exactly ONE is out of period -- and the
            # route resolves its spans by a different construction from the
            # unit tests above (``_build_entry_maps`` over
            # ``ctx.all_periods``), which nothing else exercises.  Before this,
            # the whole route-level evidence for the warning was one assertion
            # on the entries FRAGMENT.
            assert resp.data.count(b"Date outside pay period range") == 1
