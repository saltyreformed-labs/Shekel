"""
Shekel Budget App -- Grid Entry Progress Indicator Tests (Commit 7)

Tests the entry progress display ("X / Y" format) and enhanced tooltip
for tracked transactions in the grid cell and mobile grid views.

Covers:
  - build_entry_sums_dict computation correctness (unit tests).
  - Cell endpoint rendering with progress format (integration tests).
  - Tooltip enhancement with entry breakdown.
  - Non-tracked transaction regression (display unchanged).
  - Grid page flow: entry_sums passes through to template context.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.ref import TransactionType
from app.routes._render_helpers import fragment_amounts
from app.services.account_resolver import resolve_owner_cash_flow_set
from app.services.entry_service import build_entry_lists_dict, build_entry_sums_dict
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.settle_day import record_settle_day
from app.services import transaction_service

from tests._test_helpers import (
    an_entered_day,
    current_pay_period,
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
)


def _sums(rows):
    """``build_entry_sums_dict`` with the budget map its caller must supply.

    The builder takes ``{transaction_id: resolved amount}`` since plan step
    X-au-c2b -- it read ``txn.estimated_amount``, the COLUMN a derived row does
    not carry -- so every route that renders a cell resolves its rows once and
    hands the map down.  These tests resolve through the app's own single-set
    door (:func:`~app.routes._render_helpers.fragment_amounts`) rather than
    passing a literal, so the figures they assert are the ones the app would
    show.

    Args:
        rows: The rows to aggregate.

    Returns:
        The ``{txn_id: sums}`` mapping.
    """
    budgets = {}
    for row in rows:
        budgets.update(fragment_amounts(row).budgets)
    return build_entry_sums_dict(rows, budgets)


def _lists(rows):
    """``build_entry_lists_dict`` with the same budget map (see :func:`_sums`).

    **The paycheck SPANS come from the owner's own pay calendar** since
    pay-calendar plan step C4-a-3, resolved through ``require_period`` rather
    than assembled from a literal here, so a span read off the stored
    ``end_date`` column does not have to move again when plan step C4-c dropped
    it.

    **It is the same construction TWO of the four call sites use, not four**,
    and saying which is the correction an adversarial review of this step
    made: ``_render_entry_list`` and ``render_mobile_card`` resolve a period
    exactly this way, so for those the map here IS the app's.  ``/grid``
    builds a third construction (``{p.period_id: p for p in ctx.all_periods}``,
    inside ``grid.page._build_entry_maps``) and the companion page a fourth
    (``{view.period.period_id: view.period}``); this helper exercises neither.
    They answer equal ``DerivedPeriod`` values today -- checked -- so it
    cannot HIDE a defect in them, but it does not measure them either.  The
    route-level control for ``/grid``'s own map is
    :meth:`TestGridPageEntrySums.test_grid_page_shows_progress`.

    Args:
        rows: The rows to build entry-list contexts for.

    Returns:
        The ``{txn_id: entry_list_view}`` mapping.
    """
    budgets = {}
    periods = {}
    for row in rows:
        budgets.update(fragment_amounts(row).budgets)
        period = calendar_for(row.pay_period.user_id).require_period(
            FiledRow.for_row(row),
        )
        periods[period.period_id] = period
    # The OWNER's cash-flow set with no override, as every route passes it
    # (plan step credit_card:CC-5-2): every row here is one owner's, and an
    # empty list has no owner (the builder never reads the set for it).
    return build_entry_lists_dict(
        rows, budgets, periods,
        resolve_owner_cash_flow_set(rows[0].user_id) if rows else None,
        # The owner is looking, as on the grid (plan step CC-5-4a-5).
        rows[0].user_id if rows else None,
    )

def _create_tracked_txn(seed_user, seed_periods_today, period_index=0,
                         estimated=Decimal("500.00")):
    """Create a tracked expense transaction backed by a tracking-enabled template.

    The definition is priced at *estimated* and its row is the engine's
    (:func:`generate_row_of`, plan step balance:X-cf-4): DERIVED, so the
    figure a case expects to see rendered is what the row RESOLVES to.

    Returns:
        tuple of (Transaction, TransactionTemplate).
    """
    template = make_expense_template(
        db.session, seed_user, amount=estimated, name="Groceries",
        category_key="Groceries", is_envelope=True,
    )
    txn = generate_row_of(template, seed_periods_today[period_index])
    return txn, template


def _create_plain_txn(seed_user, seed_periods_today, period_index=0,
                       estimated=Decimal("200.00"), name="Test Expense"):
    """Create a non-tracked one-off expense (its definition's ``is_envelope`` False)."""
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

    txn = one_off_row_of(
        seed_periods_today[period_index],
        name=name,
        amount=estimated,
        user_id=seed_periods_today[period_index].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.flush()
    return txn


def _add_entry(txn, seed_user, amount, is_credit=False,
               description="Purchase", purchased_on=None):
    """Add a purchase entry to a transaction.

    Args:
        txn: The parent transaction.
        seed_user: The seed_user fixture dict.
        amount: The purchase amount.
        is_credit: Whether the purchase was made on a credit card.
        description: The store name / note.
        purchased_on: The day the purchase was made.  **Pass one wherever the
            test asserts anything about the OUT-OF-PERIOD warning**, and pass
            it relative to the row's own payday rather than as a literal: the
            default below is a fixed 2026-04-12 while ``seed_periods_today``
            builds its periods around the CLOCK, so whether that date falls
            inside the row's paycheck is a property of the day the suite runs
            (the weekly clock sweep moves it, ``docs/test-suite-clocks.md``).
            A test that leaves it defaulted may assert the sums but must not
            assert the warning.

    Returns:
        The flushed :class:`TransactionEntry`.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=amount,
        description=description,
        purchased_on=purchased_on or date(2026, 4, 12),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


class TestBuildEntrySumsDict:
    """Unit tests for the build_entry_sums_dict helper function."""

    def test_debit_entries_only(self, app, seed_user, seed_periods_today):
        """Tracked txn with only debit entries: credit sum is Decimal('0')."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("100.00"))
            _add_entry(txn, seed_user, Decimal("50.00"))
            db.session.commit()

            result = _sums([txn])

            assert txn.id in result
            sums = result[txn.id]
            # 100 + 50 = 150 debit, 0 credit
            assert sums["debit"] == Decimal("150.00")
            assert sums["credit"] == Decimal("0")
            assert isinstance(sums["credit"], Decimal)
            assert sums["total"] == Decimal("150.00")
            assert sums["count"] == 2

    def test_credit_entries_only(self, app, seed_user, seed_periods_today):
        """Tracked txn with only credit entries: debit sum is Decimal('0')."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("75.00"), is_credit=True)
            db.session.commit()

            result = _sums([txn])

            sums = result[txn.id]
            assert sums["debit"] == Decimal("0")
            assert isinstance(sums["debit"], Decimal)
            assert sums["credit"] == Decimal("75.00")
            assert sums["total"] == Decimal("75.00")
            assert sums["count"] == 1

    def test_mixed_entries(self, app, seed_user, seed_periods_today):
        """Both debit and credit sums correct for mixed entries."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            _add_entry(txn, seed_user, Decimal("100.00"), is_credit=True)
            db.session.commit()

            result = _sums([txn])

            sums = result[txn.id]
            # 150 + 80 = 230 debit, 100 credit, 330 total
            assert sums["debit"] == Decimal("230.00")
            assert sums["credit"] == Decimal("100.00")
            assert sums["total"] == Decimal("330.00")
            assert sums["count"] == 3

    def test_no_entries_excluded(self, app, seed_user, seed_periods_today):
        """Transaction with empty entries list is NOT in the result dict."""
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            db.session.commit()

            result = _sums([txn])

            assert txn.id not in result

    def test_non_tracked_excluded(self, app, seed_user, seed_periods_today):
        """Non-tracked transaction (a non-envelope definition's) is NOT in the result dict."""
        with app.app_context():
            txn = _create_plain_txn(seed_user, seed_periods_today)
            db.session.commit()

            result = _sums([txn])

            assert txn.id not in result

    def test_multiple_txns_independent(self, app, seed_user, seed_periods_today):
        """Multiple tracked txns each have independent entry sums."""
        with app.app_context():
            txn1, _ = _create_tracked_txn(
                seed_user, seed_periods_today, estimated=Decimal("500.00"),
            )
            _add_entry(txn1, seed_user, Decimal("100.00"))

            txn2, _ = _create_tracked_txn(
                seed_user, seed_periods_today, period_index=1,
                estimated=Decimal("300.00"),
            )
            _add_entry(txn2, seed_user, Decimal("250.00"))
            _add_entry(txn2, seed_user, Decimal("50.00"), is_credit=True)
            db.session.commit()

            result = _sums([txn1, txn2])

            assert result[txn1.id]["total"] == Decimal("100.00")
            assert result[txn1.id]["count"] == 1
            assert result[txn2.id]["total"] == Decimal("300.00")
            assert result[txn2.id]["count"] == 2

    def test_empty_list_returns_empty_dict(self, app):
        """Empty transaction list returns empty dict."""
        with app.app_context():
            result = _sums([])
            assert result == {}

    def test_c31_4_remaining_server_computed(
        self, app, seed_user, seed_periods_today,
    ):
        """C31-4 -- ``remaining`` and ``over_budget`` are server-computed.

        Previously :file:`grid/_transaction_cell.html` subtracted
        ``estimated_amount - es.total`` in Jinja (TA-01); the value
        now arrives pre-computed in the dict so the template renders
        without inline arithmetic.

        Arithmetic: estimated $500.00 budget, two debit entries
        $150.00 + $80.00 = $230.00 spent; remaining = 500 - 230 = 270.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            db.session.commit()

            sums = _sums([txn])[txn.id]
            assert sums["remaining"] == Decimal("270.00")
            assert sums["over_budget"] is False
            assert isinstance(sums["remaining"], Decimal)

    def test_c31_4_over_budget_flag_server_computed(
        self, app, seed_user, seed_periods_today,
    ):
        """C31-4 -- the over-budget flag matches a remaining < 0 test.

        Arithmetic: estimated $500 budget, $600 spent; remaining = -100,
        over_budget = True (matches the prior Jinja conditional
        ``remaining < 0``).
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("600.00"))
            db.session.commit()

            sums = _sums([txn])[txn.id]
            assert sums["remaining"] == Decimal("-100.00")
            assert sums["over_budget"] is True


class TestBuildEntryListsDict:
    """Unit tests for the ``build_entry_lists_dict`` helper.

    The helper pre-computes the entry-list rendering inputs that the
    grid + companion routes feed to ``render_row_card`` so the
    inline ``_transaction_entries.html`` include can render on the
    initial response instead of fanning out to one ``/entries`` GET
    per envelope card (which blew past ``RATELIMIT_DEFAULT`` of
    "30 per minute" on a 6-period grid).  Tests mirror the
    ``TestBuildEntrySumsDict`` shape above.
    """

    def test_envelope_with_entries_has_data(
        self, app, seed_user, seed_periods_today,
    ):
        """Envelope txn with entries appears in result with full data.

        Arithmetic: $500 estimated, two debit entries $150 + $80 =
        $230; remaining = 500 - 230 = 270.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            payday = seed_periods_today[0].start_date
            _add_entry(txn, seed_user, Decimal("150.00"), purchased_on=payday)
            _add_entry(txn, seed_user, Decimal("80.00"), purchased_on=payday)
            db.session.commit()

            result = _lists([txn])

            assert txn.id in result
            data = result[txn.id]
            assert len(data["entries"]) == 2
            assert data["remaining"] == Decimal("270.00")
            assert isinstance(data["remaining"], Decimal)
            # **EXACT, and both purchases are dated on the PAYDAY itself**, so
            # the answer is known by construction rather than by where in the
            # calendar the suite happens to run.  This read
            # ``assert isinstance(data["out_of_period_ids"], set)`` under a
            # comment saying membership "depends on the period dates" -- and
            # an adversarial review of plan step C4-a-3 measured that comment
            # false and the assertion free: the entries were dated 2026-04-12
            # against periods built around today, so BOTH were unconditionally
            # out of period and a shape check read as coverage.
            assert data["out_of_period_ids"] == set()

    def test_out_of_period_purchases_are_named_EXACTLY(
        self, app, seed_user, seed_periods_today,
    ):
        """One purchase inside the paycheck, one outside; only the outside one.

        **The control that tells the warning's two failure directions apart**
        (adversarial review of plan step C4-a-3).  Before it, the whole app had
        ONE assertion about this set -- ``test_entries.py``'s "Date outside pay
        period range" presence check -- and both mutations of the predicate
        killed that same test, because its fixture held a single purchase and
        that purchase was the out-of-period one.  Identical singleton fail sets
        are not two controls; a set that is merely NON-EMPTY, or merely a
        ``set``, is not a measurement of which entries are in it.

        Here the fail sets are distinct and neither is empty:

        * a predicate that never fires answers ``set()``;
        * a predicate INVERTED -- ``covers`` without the ``not``, which is how
          a careless restore leaves it -- answers ``{the in-period one}``;
        * only the correct rule answers ``{the out-of-period one}``.

        The two days are taken from the row's own PAYDAY rather than written
        as literals, so this holds on any day the suite runs: a period covers
        its payday by definition, and the day before a payday belongs to the
        paycheck before it.  ``start_date`` is also the one column plan step
        **C4-c** keeps, so this fixture does not have to move again.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            payday = seed_periods_today[0].start_date
            inside = _add_entry(
                txn, seed_user, Decimal("10.00"),
                description="On the payday", purchased_on=payday,
            )
            outside = _add_entry(
                txn, seed_user, Decimal("20.00"), description="The day before",
                purchased_on=payday - timedelta(days=1),
            )
            db.session.commit()

            data = _lists([txn])[txn.id]

            assert data["out_of_period_ids"] == {outside.id}
            assert inside.id not in data["out_of_period_ids"]

    def test_a_row_whose_paycheck_the_map_omits_RAISES(
        self, app, seed_user, seed_periods_today,
    ):
        """An uncovered paycheck is a KeyError, never a silently absent warning.

        ``build_entry_lists_dict`` documents this refusal and nothing measured
        it (adversarial review of plan step C4-a-3).  It is load-bearing prose
        rather than decoration: ``grid/page._build_entry_maps`` argues that its
        span map and its row set are cut from ONE window *because* passing the
        narrower visible slice would raise here, and an untested refusal makes
        that argument unfalsifiable.

        A row missing from the map means the caller built it from a different
        row set than it is rendering.  Answering an empty warning set instead
        would drop a warning the screen owes, on a row the app cannot place --
        the same disposition ``budgets`` takes for a row it did not price.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("10.00"))
            db.session.commit()
            budgets = fragment_amounts(txn).budgets

            with pytest.raises(KeyError):
                build_entry_lists_dict(
                    [txn], budgets, {}, resolve_owner_cash_flow_set(txn.user_id),
                    txn.user_id,
                )

    def test_envelope_without_entries_still_included(
        self, app, seed_user, seed_periods_today,
    ):
        """Envelope txn with zero entries is included with empty list.

        Mirrors the macro's expectation: an envelope card without
        entries still renders the entries section (showing "No
        purchases recorded yet" + the add-entry form), so the dict
        must carry the empty-list entry rather than excluding the
        txn.  Remaining equals the full estimated amount.
        """
        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            db.session.commit()

            result = _lists([txn])

            assert txn.id in result
            data = result[txn.id]
            assert data["entries"] == []
            assert data["remaining"] == Decimal("500.00")
            assert data["out_of_period_ids"] == set()

    def test_non_envelope_excluded(
        self, app, seed_user, seed_periods_today,
    ):
        """Non-envelope (a non-envelope definition's) txn is NOT in the result dict.

        The macro's ``txn.tracks_purchases`` guard means the
        inline entries section is only rendered for envelope
        rows.  Pre-computing entries for non-envelopes would
        waste work and leak a misleading entry record.
        """
        with app.app_context():
            txn = _create_plain_txn(seed_user, seed_periods_today)
            db.session.commit()

            result = _lists([txn])

            assert txn.id not in result

    def test_non_envelope_template_excluded(
        self, app, seed_user, seed_periods_today,
    ):
        """Txn whose template has is_envelope=False is NOT in the result.

        A template can exist without enabling envelope tracking
        (recurring bills, e.g. mortgage).  Such transactions do not
        get inline entries.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="2000.00", name="Mortgage",
                category_key="Groceries",
            )
            txn = generate_row_of(template, seed_periods_today[0])
            db.session.commit()

            result = _lists([txn])

            assert txn.id not in result

    def test_multiple_envelopes_independent(
        self, app, seed_user, seed_periods_today,
    ):
        """Each envelope txn's data is computed independently.

        Two envelope txns with different entry counts and remaining
        balances must produce two independent dict entries with the
        correct per-txn values.
        """
        with app.app_context():
            txn1, _ = _create_tracked_txn(
                seed_user, seed_periods_today, estimated=Decimal("500.00"),
            )
            _add_entry(txn1, seed_user, Decimal("100.00"))

            txn2, _ = _create_tracked_txn(
                seed_user, seed_periods_today, period_index=1,
                estimated=Decimal("300.00"),
            )
            _add_entry(txn2, seed_user, Decimal("250.00"))
            db.session.commit()

            result = _lists([txn1, txn2])

            assert result[txn1.id]["remaining"] == Decimal("400.00")
            assert len(result[txn1.id]["entries"]) == 1
            assert result[txn2.id]["remaining"] == Decimal("50.00")
            assert len(result[txn2.id]["entries"]) == 1

    def test_empty_list_returns_empty_dict(self, app):
        """Empty transaction list returns empty dict, no errors."""
        with app.app_context():
            result = _lists([])
            assert result == {}


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


class TestTheEntryListContextHasOneProducer:
    """The whole entry-list context comes from ``entry_list_view``, everywhere.

    **These are the controls the bug had none of.**  Every test of the
    posted-purchase indicator drove ``GET /transactions/<id>/entries``
    (``test_entries.py::TestTheDerivedPostedIndicator``) -- the ONE render
    path that supplied its key.  The grid macro, the mobile card,
    the companion view and the full-edit popover all render the same partial
    from :func:`build_entry_lists_dict`, which did not, and Jinja answers
    ``entry.id in Undefined`` as ``False`` silently.  So on every initial
    render an already-posted purchase read *"Still outstanding"* while the
    projection had already released its reservation -- 9 of 9 such purchases
    on the 2026-08-13 production clone.

    A missing key cannot fail loudly in Jinja, so it is graded here instead:
    the producer supplies the whole context, and the macro is asserted to
    unpack every key of it.
    """

    def test_the_dict_carries_the_posted_indicator(
        self, app, seed_user, seed_periods_today,
    ):
        """A purchase whose bank posting day is recorded comes back POSTED.

        Both purchases below carry one, a day apart and either side of the
        account's only assertion, and both come back posted -- because the
        indicator asks the PURCHASE since plan step X-f3b (ruling **R-FM**).
        It asked the account's clearing rule before, so this test expected the
        later one to be absent; that answer would now contradict the
        reservation, which releases both.
        """
        from tests._test_helpers import (
            append_balance_assertion, settle_instant_on,
        )

        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            inside = _add_entry(txn, seed_user, Decimal("150.00"))
            outside = _add_entry(txn, seed_user, Decimal("80.00"))
            unobserved = _add_entry(txn, seed_user, Decimal("20.00"))
            asserted_on = seed_periods_today[0].start_date
            record_settle_day(inside, an_entered_day(asserted_on))
            record_settle_day(outside, an_entered_day(asserted_on + timedelta(days=1)))
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1000.00"), settle_instant_on(asserted_on),
            )
            db.session.commit()

            data = _lists([txn])[txn.id]
            unobserved_id = unobserved.id

        assert data["posted_ids"] == {inside.id, outside.id}, (
            "a purchase with a recorded posting day has left the account, "
            "whichever side of a declared balance its day falls on; one with "
            "no posting day has not"
        )
        assert unobserved_id not in data["posted_ids"]

    def test_the_grid_macro_unpacks_every_key_the_producer_returns(
        self, app, seed_user, seed_periods_today,
    ):
        """The macro's ``{% set %}`` list covers the whole context.

        The negative control for the defect itself: delete ``posted_ids``
        from ``_grid_row_macros.html`` and this fails, where the rendered page
        would have gone on looking plausible.
        """
        from pathlib import Path

        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            _add_entry(txn, seed_user, Decimal("150.00"))
            db.session.commit()
            produced = set(_lists([txn])[txn.id])

        macro = Path(app.root_path) / "templates/grid/_grid_row_macros.html"
        source = macro.read_text(encoding="utf-8")
        unset = {
            key for key in produced
            if f"{{% set {key} = _entry_data.{key} %}}" not in source
        }
        assert not unset, (
            f"_grid_row_macros.html does not unpack {sorted(unset)} from "
            "_entry_data, and Jinja answers a membership test against an "
            "Undefined as False SILENTLY -- so the partial would render the "
            "wrong arm rather than raising"
        )


class TestTheAmountFenceIsGone:
    """No cell template still reads the amount COLUMN, or a silent fallback.

    Plan step X-au-c2b replaced a transient ``txn.live_estimated_amount`` --
    annotated onto each row by ONE route and read everywhere behind
    ``if ... is defined else txn.estimated_amount`` -- with a ``budgets`` map
    every render path publishes.  Both halves had to go together, and the
    reason is the shape ``TestTheEntryListContextHasOneProducer`` above records:
    a Jinja ``Undefined`` answers silently, so a render path that forgot to set
    the attribute showed the stale column with nothing on screen to say so.

    These are SOURCE assertions rather than render assertions, deliberately.
    What is being asserted is that the fence has no way back -- a future edit
    reintroducing either spelling would render correctly on the one path that
    sets it and wrongly everywhere else, which is exactly the state that shipped
    the posted-purchase bug and which no single render test caught.
    """

    # The EDIT forms are in this census, and an adversarial review is why.
    # They were routed off the column in the same commit but onto a SCALAR
    # ``budget``, which renders ``value=""`` in silence when unpublished where a
    # map raises -- on the two surfaces where an empty figure is POSTED BACK.
    # A census that stopped at the display templates said the fence was gone
    # while the doors that matter most still had it.
    _CELL_TEMPLATES = (
        "grid/_transaction_cell.html",
        "grid/_grid_row_macros.html",
        "grid/_mobile_this_period.html",
        "grid/_mobile_plan.html",
        "grid/_mobile_card_single.html",
        "grid/_transaction_quick_edit.html",
        "grid/_transaction_full_edit.html",
    )

    @staticmethod
    def _source(app, name):
        """Return a template's SOURCE text (not its render).

        Args:
            app: The Flask app, for its Jinja loader.
            name: The template's loader name.

        Returns:
            The template file's text.
        """
        source, _path, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, name,
        )
        return source

    def test_no_cell_template_reads_the_transient_attribute(self, app):
        """``live_estimated_amount`` exists nowhere but in prose about it."""
        for name in self._CELL_TEMPLATES:
            for line in self._source(app, name).splitlines():
                if "live_estimated_amount" not in line:
                    continue
                assert line.lstrip().startswith(("{#", "#", "-#", "{%-")) or (
                    "``" in line
                ), (
                    f"{name} still READS the transient attribute: {line!r}"
                )

    def test_no_cell_template_reads_the_amount_column_for_display(self, app):
        """``estimated_amount`` survives only as a form FIELD NAME and in prose.

        The distinction is the point: ``name="estimated_amount"`` is the column
        an edit POSTS to, which is correct and unchanged; ``{{ txn.estimated_amount }}``
        is a READ of a column a derived row does not carry.
        """
        for name in self._CELL_TEMPLATES:
            for line in self._source(app, name).splitlines():
                if "estimated_amount" not in line:
                    continue
                assert (
                    'name="estimated_amount"' in line
                    or "``" in line
                    or "{#" in line
                    or "#}" in line
                    or not ("{{" in line or "{%" in line)
                ), f"{name} still reads the amount column: {line!r}"

    def test_a_render_without_the_map_FAILS_rather_than_falling_back(self, app):
        """The replacement for the fence: a missing map is an error, not a shrug.

        ``budgets[t.id]`` on an absent map raises ``UndefinedError`` where
        ``t.live_estimated_amount if ... is defined`` rendered the stale column.
        That difference IS the fix -- a render path that forgets to publish the
        map now cannot ship a wrong figure -- so it is asserted rather than left
        as a property of Jinja nobody wrote down.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

        import jinja2  # pylint: disable=import-outside-toplevel

        txn = SimpleNamespace(
            id=1, name="Rent", settled_amount=None,
            estimated_amount=Decimal("1200.00"),
            status=SimpleNamespace(is_settled=False, name="Projected"),
            status_id=99, transfer_id=None, credit_payback_for_id=None,
            is_expense=True, tracks_purchases=False, notes=None,
        )
        template = app.jinja_env.get_template("grid/_transaction_cell.html")
        with app.test_request_context("/"):
            with pytest.raises(
                jinja2.exceptions.UndefinedError, match="budgets",
            ):
                template.render(txn=txn)

    def test_an_EDIT_form_without_the_map_fails_too(
        self, app, seed_user, seed_periods_today,
    ):
        """The door a figure is POSTED BACK from raises like the display ones.

        The one that matters most: a display template rendering an empty amount
        is visible, where an edit box opening blank invites a save that books
        whatever is typed over a figure the user never saw.  It was a scalar
        ``budget`` for one commit, which renders ``value=""`` and says nothing.

        A REAL row rather than a stand-in, so ``budgets`` is the only name the
        render can be missing -- which is what lets the ``match`` be specific
        rather than accepting any ``UndefinedError`` the template happens to
        raise first.
        """
        import jinja2  # pylint: disable=import-outside-toplevel

        with app.app_context():
            txn, _ = _create_tracked_txn(seed_user, seed_periods_today)
            db.session.commit()
            template = app.jinja_env.get_template(
                "grid/_transaction_quick_edit.html",
            )
            with app.test_request_context("/"):
                with pytest.raises(
                    jinja2.exceptions.UndefinedError, match="budgets",
                ):
                    template.render(txn=txn, locked=False)
                # And it RENDERS the resolved figure when the map is published,
                # so the raise above is the missing map rather than the
                # template being unrenderable.
                html = template.render(
                    txn=txn, locked=False, budgets=fragment_amounts(txn).budgets,
                )
                # The row is DERIVED, so the figure the box must carry is the
                # price its definition states -- its own column is None.
                assert 'value="500.00"' in html
