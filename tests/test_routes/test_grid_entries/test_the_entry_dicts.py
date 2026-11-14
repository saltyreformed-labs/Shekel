"""The two builders the grid's entry display reads: build_entry_sums_dict's sums and
build_entry_lists_dict's rendering inputs.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.routes._render_helpers import fragment_amounts
from app.services.movement_account import owner_accounts
from app.services.entry_service import build_entry_lists_dict

from tests._test_helpers import generate_row_of, make_expense_template

from ._helpers import _sums, _lists, _create_tracked_txn, _create_plain_txn, _add_entry


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
                    [txn], budgets, {}, owner_accounts(txn.user_id),
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
