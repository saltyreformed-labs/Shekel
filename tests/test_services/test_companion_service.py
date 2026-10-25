"""
Shekel Budget App -- Companion Service Tests

Data isolation and visibility filtering tests for the companion
service.  Verifies that companion users see exactly the transactions
whose ``Transaction.visible_to_companion`` is True -- a row of a definition
(generated, or a one-off's placed row) by the DEFINITION's
``companion_visible`` flag, a legacy link-less row by its own until the
cutover ``balance:X-bi-7d`` -- scoped to their linked owner's pay periods.  **The service asks that
property of each loaded row since plan step ``balance:X-bi-1b``** (ruling
**R-BAL19**); it restated the rule in SQL before, and
:class:`TestVisibilityFiltering` holds the cases that tell the two apart.

Covers plan test IDs: 10.1, 10.2, 10.4, 10.12, 10.13.
Additional tests beyond the plan baseline cover period isolation,
entry eager-loading, misconfigured companions, and edge cases.
"""

import pytest
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import RoleEnum, StatusEnum, TxnTypeEnum
from app.exceptions import NotFoundError
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import companion_service
from app.services.auth_service import hash_password
from app.services.pay_calendar import PayCalendarError, calendar_for
from tests._test_helpers import (
    capture_sql_statements,
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    moved_by_the_owner,
    one_off_row_of,
    open_owner_calendar,
    payback_row_of,
    resolved_amount,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_template(
    seed_user, *, companion_visible, track=False, name="Item", amount="500.00",
):
    """Create a priced, every-paycheck expense definition for the owner.

    A thin adapter over :func:`make_expense_template` keeping this file's
    vocabulary (``track`` is ``is_envelope``); what it returns is a definition
    the engine can write rows from (plan step balance:X-cf).

    Args:
        seed_user: The seed_user fixture dict.
        companion_visible: Whether the template is companion-visible.
        track: Whether to enable is_envelope.
        name: Template name.
        amount: The definition's stated price, which is what its rows are
            worth.

    Returns:
        The created TransactionTemplate object (flushed, ID available).
    """
    return make_expense_template(
        db.session, seed_user, amount=amount, name=name,
        is_envelope=track, companion_visible=companion_visible,
    )


def _make_txn(period, template, *, name=None):
    """Create the definition's row in a specific period.

    The ENGINE's row (:func:`generate_row_of`, plan step balance:X-cf):
    derived, so its figure is the definition's price and its
    ``estimated_amount`` column is empty -- a test that wants the figure asks
    :func:`resolved_amount`.  A *name* is then laid on bare, the way the
    edit door renames a row (``_apply_field_updates`` writes the column and
    flags nothing), for the cases that tell two rows of one definition apart
    by name.  It took a ``seed_user`` until X-cf-3; the engine resolves the
    owner from the definition, so the argument was unread and went.

    Args:
        period: The PayPeriod to generate into.
        template: The TransactionTemplate.
        name: Override the name (defaults to template.name).

    Returns:
        The created Transaction object (flushed, ID available).
    """
    txn = generate_row_of(template, period)
    if name is not None and name != template.name:
        txn.name = name
        db.session.flush()
    return txn


# ── Visibility Filtering ─────────────────────────────────────────────


class TestVisibilityFiltering:
    """Verify companion only sees transactions from visible templates."""

    def test_companion_sees_visible_transactions(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.1: 3 templates (2 visible, 1 not) -- only 2 returned.

        Arithmetic: 2 visible templates produce 2 transactions in
        period[0].  The non-visible template's transaction is excluded.
        """
        t_vis1 = _make_template(seed_user, companion_visible=True, name="Groceries")
        t_vis2 = _make_template(seed_user, companion_visible=True, name="Gas")
        t_hidden = _make_template(seed_user, companion_visible=False, name="Mortgage")

        _make_txn(seed_periods_today[0], t_vis1, name="Groceries")
        _make_txn(seed_periods_today[0], t_vis2, name="Gas")
        _make_txn(seed_periods_today[0], t_hidden, name="Mortgage")
        db.session.commit()

        companion = seed_companion["user"]
        view = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        txns = view.transactions
        names = [t.name for t in txns]
        assert len(txns) == 2
        assert "Groceries" in names
        assert "Gas" in names
        assert "Mortgage" not in names

    def test_companion_sees_no_non_visible(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.2: All templates non-visible -- empty list, not error.

        The service returns an empty transaction list without raising.
        """
        t1 = _make_template(seed_user, companion_visible=False, name="Rent")
        t2 = _make_template(seed_user, companion_visible=False, name="Electric")
        _make_txn(seed_periods_today[0], t1, name="Rent")
        _make_txn(seed_periods_today[0], t2, name="Electric")
        db.session.commit()

        companion = seed_companion["user"]
        view = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        txns, period = view.transactions, view.period
        assert txns == []
        assert period is not None

    def test_mix_tracked_and_untracked_visible(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Both tracked and non-tracked visible templates are returned.

        Verifies that is_envelope does not affect
        companion visibility filtering.
        """
        t_tracked = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        t_simple = _make_template(
            seed_user, companion_visible=True, track=False, name="Gas",
        )
        _make_txn(seed_periods_today[0], t_tracked, name="Groceries")
        _make_txn(seed_periods_today[0], t_simple, name="Gas")
        db.session.commit()

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        ).transactions
        assert len(txns) == 2

    def test_visible_template_no_transactions_in_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible template exists but no transactions in the requested period.

        The service returns an empty list (not an error) when the template
        has no transactions in the given period.
        """
        _make_template(seed_user, companion_visible=True, name="Christmas")
        # No transactions created for this template in this period.
        db.session.commit()

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        ).transactions
        assert txns == []

    def test_soft_deleted_transactions_excluded(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Soft-deleted transactions are filtered out even if template is visible.

        The is_deleted=True filter prevents deleted transactions from
        appearing in the companion view.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        txn = _make_txn(seed_periods_today[0], template, name="Groceries")
        txn.is_deleted = True
        db.session.commit()

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        ).transactions
        assert len(txns) == 0

    def test_a_row_with_no_definition_is_hidden(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """A card payback is hidden though its envelope is shown: nothing states it may be.

        Ruling **R-BAL73**: a row that names no definition answers ``False``
        to ``visible_to_companion``.  Until the family's cutover (plan step
        ``balance:X-bi-7d-2``) a legacy link-less one-off stated its own
        ``companion_visible`` in a cell of the row's and this service read
        it; the cutover minted every such row a definition (the placed case
        below) and dropped the cell.  The link-less row left is a CC payback
        (the transfer shadow was the other until plan step
        ``balance:X-bi-6-4d-2``), and the card cutover (``credit_card:CC-7``,
        rulings **R-CC17** / **R-CC20**) deletes it, so this case goes with
        the arm.

        The payback lands in the paycheck AFTER its envelope's, and the
        envelope's definition is companion-visible: the envelope's own
        paycheck shows it, which is the control that the service answers
        this owner's rows at all, and the payback's paycheck shows nothing.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        envelope = _make_txn(seed_periods_today[0], template)
        payback = payback_row_of(
            db.session, seed_user, envelope, Decimal("40.00"),
            seed_periods_today[0].start_date,
        )
        db.session.commit()
        assert payback.template_id is None
        assert payback.pay_period_id == seed_periods_today[1].id

        companion = seed_companion["user"]
        shown = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        ).transactions
        assert [t.id for t in shown] == [envelope.id]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=payback.pay_period_id,
        ).transactions
        assert txns == []

    @pytest.mark.parametrize("shown", [True, False])
    def test_a_placed_one_offs_visibility_is_its_definitions(
        self, app, db, seed_user, seed_periods_today, seed_companion, shown,
    ):
        """A one-off placed today (plan step balance:X-bi-7b) reads its DEFINITION's flag.

        The shape every one-off has held since 7b and every row will hold
        after 7d: ``one_off_row_of(companion_visible=...)`` lands on the
        definition (ruling R-BAL36), and the service shows the row by that
        and nothing else.  Both halves, so a service that showed everything
        or nothing fails one of them.
        """
        txn = one_off_row_of(
            seed_periods_today[0], name="Shared Dinner", amount="60.00",
            user_id=seed_periods_today[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            category_id=list(seed_user["categories"].values())[0].id,
            companion_visible=shown,
        )
        db.session.commit()
        assert txn.template.companion_visible is shown

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        ).transactions
        assert [t.id for t in txns] == ([txn.id] if shown else [])

    def test_the_filter_costs_no_query_per_row(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Asking each row ``visible_to_companion`` issues no SELECT per row.

        The property reads ``txn.template`` on every generated row, and the
        query eager-loads the template for exactly that reason; a dropped
        ``selectinload`` would turn the filter into one SELECT per row.
        Measured over the axis the defect lives on -- the service call with
        TWO generated rows against SIX, hidden and shown alike -- and on the
        MECHANISM rather than a total: the statements that read
        ``budget.transaction_templates`` number exactly one either way (the
        eager load's batch), so a capture that fired on nothing at all could
        not pass by reading zero twice.
        """
        companion_id = seed_companion["user"].id
        period = seed_periods_today[0]
        period_id = period.id

        def _template_reads_with(n_templates):
            for i in range(n_templates):
                tpl = _make_template(
                    seed_user, companion_visible=(i % 2 == 0),
                    name=f"Item {n_templates}-{i}",
                )
                _make_txn(period, tpl)
            db.session.commit()
            # Every instance is expired by the commit, so both captures start
            # from the same identity-map state and load the same way.
            _, statements = capture_sql_statements(
                lambda: companion_service.get_visible_transactions(
                    companion_id, period_id=period_id,
                )
            )
            assert statements, "the capture saw no statement at all"
            return sum(
                1 for text, _params in statements
                if "budget.transaction_templates" in text
            )

        assert _template_reads_with(2) == 1
        assert _template_reads_with(4) == 1  # four more: six rows now

    def test_two_rows_of_one_name_are_ordered_by_id(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Within one name, rows come back by id -- the order the cards render.

        Two rows of one definition in one period is the carried-leftover
        shape (see the override-sibling case below), and ``ORDER BY name``
        alone left their order to the query plan: the join removed at plan
        step ``balance:X-bi-1b`` flipped it on one production page.

        Built so that the plan's order and id order DIFFER, or the case
        grades nothing: the carried row is created first (the smaller id)
        and MOVED last, so its live tuple sits after the canonical's in the
        heap and a scan without the tie-break answers descending ids.
        Measured red under exactly that mutation before landing.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        target = seed_periods_today[1]
        # The engine writes one row of a definition per paycheck, so the
        # second row is the previous paycheck's carried forward: moved into
        # the target and flagged ``is_override``, the move door's two acts.
        carried = _make_txn(seed_periods_today[0], template)
        canonical = _make_txn(target, template)
        db.session.commit()
        carried.pay_period_id = target.id
        carried.is_override = True
        db.session.commit()
        assert carried.id < canonical.id

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=target.id,
        ).transactions
        assert [t.id for t in txns] == [carried.id, canonical.id]

    def test_transactions_ordered_by_name(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible transactions are returned in alphabetical order by name."""
        t_z = _make_template(seed_user, companion_visible=True, name="Zucchini Fund")
        t_a = _make_template(seed_user, companion_visible=True, name="Apples Budget")
        _make_txn(seed_periods_today[0], t_z, name="Zucchini Fund")
        _make_txn(seed_periods_today[0], t_a, name="Apples Budget")
        db.session.commit()

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        ).transactions
        assert txns[0].name == "Apples Budget"
        assert txns[1].name == "Zucchini Fund"

    def test_companion_sees_override_sibling_alongside_rule_generated(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Carry-forward override siblings stay visible to the companion.

        When the owner carries an unpaid grocery row from a past period
        into the current period that already has the rule-generated
        next instance, both rows now sit in the same period -- one with
        is_override=False (canonical), one with is_override=True
        (carried).  Both still link to the same companion-visible
        TransactionTemplate, so the companion view must return both.
        Without this, the companion would see only the rule-generated
        row and silently miss the carried envelope -- the exact failure
        mode that ruled out the pure-detach fix.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        target = seed_periods_today[1]
        rule_generated = _make_txn(target, template, name="Groceries")

        # The carried row is the PREVIOUS paycheck's own row brought forward
        # by the move door's two acts: carry-forward moves an unpaid row into
        # the target period and flags it ``is_override``.  Two engine rows,
        # one moved -- the shape itself, not a hand-built copy of it.  (Each
        # answers its own occurrence, which is what lets the pair be stored;
        # the flag is what keeps the maintain pass off the carried row.)
        carried = moved_by_the_owner(
            _make_txn(seed_periods_today[0], template), into=target,
        )
        db.session.commit()

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=target.id,
        ).transactions

        # Both rows are returned -- the override sibling stays visible.
        ids = sorted(t.id for t in txns)
        assert ids == sorted([rule_generated.id, carried.id])

        # And the override flag survives the round-trip so the UI can
        # distinguish carried items from canonical ones if it chooses.
        flags = sorted(t.is_override for t in txns)
        assert flags == [False, True]


# ── Period Isolation ─────────────────────────────────────────────────


class TestPeriodIsolation:
    """Verify transactions from other periods are not included."""

    def test_transactions_from_other_periods_excluded(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Only the requested period's transactions are returned.

        Creates transactions in periods 0 and 1, requests period 0,
        verifies only period 0's transaction appears.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_periods_today[0], template, name="Groceries P0")
        _make_txn(seed_periods_today[1], template, name="Groceries P1")
        db.session.commit()

        companion = seed_companion["user"]
        view = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        txns, period = view.transactions, view.period
        assert len(txns) == 1
        assert txns[0].name == "Groceries P0"
        assert period.period_id == seed_periods_today[0].id

    def test_period_id_belonging_to_different_owner_raises(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.13: Companion with period from a different owner raises NotFoundError.

        **The comparison this used to name is GONE, and the guarantee is
        stronger for it** (plan step C2-f2b).  The service resolved the id with
        ``db.session.get(PayPeriod, ...)`` and then checked
        ``period.user_id != owner_id`` by hand; it asks
        ``calendar.period_by_id`` on the linked OWNER's calendar now, so a
        period belonging to anyone else is ABSENT rather than rejected and
        there is no comparison a later edit can drop.  ``NotFoundError`` still
        carries one message for both "no such period" and "not yours", which is
        the project's security response rule.
        """
        # Create a second owner with their own period.
        second_user = User(
            email="other@test.local",
            password_hash=hash_password("otherpass"),
            display_name="Other",
        )
        db.session.add(second_user)
        db.session.flush()
        settings = UserSettings(user_id=second_user.id)
        db.session.add(settings)

        # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
        other_period = open_owner_calendar(second_user.id, date(2026, 1, 2))[0]
        db.session.commit()

        companion = seed_companion["user"]
        with pytest.raises(NotFoundError):
            companion_service.get_visible_transactions(
                companion.id, period_id=other_period.id,
            )

    def test_period_id_none_returns_current_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """period_id=None returns the current period's transactions.

        ``calendar.period_containing(display_today())`` picks it -- the
        owner's derived calendar and the USER's civil day since plan step
        C2-f2b, where it was ``get_current_period`` on the process clock.
        We create a template+transaction in the first period and call
        with period_id=None.  If today falls in that period, the
        transaction is returned; otherwise we get a different period.
        Either way, a valid ``CompanionPageRead`` is returned.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        companion = seed_companion["user"]
        # This may return a different period than seed_periods_today[0]
        # depending on the current date, but it should not raise.
        view = companion_service.get_visible_transactions(companion.id)
        assert view.period is not None

    def test_nonexistent_period_id_raises(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Requesting a non-existent period_id raises NotFoundError."""
        companion = seed_companion["user"]
        with pytest.raises(NotFoundError):
            companion_service.get_visible_transactions(
                companion.id, period_id=999999,
            )


# ── User Validation ──────────────────────────────────────────────────


class TestUserValidation:
    """Verify companion service rejects invalid user configurations."""

    def test_owner_user_rejected(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Plan 10.12: Owner user passed to get_visible_transactions raises NotFoundError.

        Defense-in-depth: even though the route checks the role,
        the service independently verifies the user is a companion.
        """
        owner = seed_user["user"]
        with pytest.raises(NotFoundError, match="not a companion"):
            companion_service.get_visible_transactions(owner.id)

    def test_nonexistent_user_id_raises(self, app, db):
        """Non-existent user_id raises NotFoundError."""
        with pytest.raises(NotFoundError, match="not found"):
            companion_service.get_visible_transactions(999999)

    def test_companion_with_no_linked_owner_raises(
        self, app, db, seed_user,
    ):
        """Companion with linked_owner_id=None raises NotFoundError.

        This represents a data integrity issue that should not silently
        return empty results.
        """
        orphan = User(
            email="orphan@test.local",
            password_hash=hash_password("orphanpass"),
            display_name="Orphan",
            role_id=ref_cache.role_id(RoleEnum.COMPANION),
            linked_owner_id=None,
        )
        db.session.add(orphan)
        db.session.flush()
        settings = UserSettings(user_id=orphan.id)
        db.session.add(settings)
        db.session.commit()

        with pytest.raises(NotFoundError, match="no linked owner"):
            companion_service.get_visible_transactions(orphan.id)

    def test_valid_companion_succeeds(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Properly configured companion user passes all validation."""
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        _make_txn(seed_periods_today[0], template, name="Groceries")
        db.session.commit()

        companion = seed_companion["user"]
        view = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        )
        assert view.period is not None


# ── Entry Eager Loading ──────────────────────────────────────────────


class TestEntryEagerLoading:
    """Verify entries are eager-loaded on returned transactions."""

    def test_entries_accessible_without_lazy_load(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Entries are eagerly loaded via selectinload.

        After closing the session's identity map (expunging all),
        accessing txn.entries should still work because they were
        loaded during the query, not lazily.  We verify by checking
        that entries are populated on the returned objects.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = _make_txn(seed_periods_today[0], template)
        entry = TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=seed_user["user"].id,
            amount=Decimal("42.50"),
            description="Kroger",
            purchased_on=date(2026, 1, 5),
        )
        db.session.add(entry)
        db.session.commit()

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        ).transactions
        assert len(txns) == 1
        # Access entries -- they should be loaded already.
        assert len(txns[0].entries) == 1
        assert txns[0].entries[0].amount == Decimal("42.50")
        assert txns[0].entries[0].description == "Kroger"


# ── Entry Data Computation ───────────────────────────────────────────


class TestEntryDataComputation:
    """Verify entry_data computed via entry_service functions is correct.

    Plan 10.4: tracked transaction with entries has correct total,
    remaining, and count.
    """

    def test_entry_sums_computed_correctly(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.4: Entry data (total, remaining, count) is correct.

        Arithmetic: estimated=$500, entries=$100+$50=$150.
        remaining = $500 - $150 = $350.  count = 2.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
            amount="500.00",
        )
        txn = _make_txn(seed_periods_today[0], template)
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

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        ).transactions
        assert len(txns) == 1
        assert len(txns[0].entries) == 2

        # Verify the service functions produce correct values.
        from app.services.entry_service import compute_entry_sums, compute_remaining
        sum_debit, sum_credit = compute_entry_sums(txns[0].entries)
        total = sum_debit + sum_credit
        # The row is DERIVED: its budget is what the amount model says, which
        # is what the companion route hands ``build_entry_sums_dict``.
        remaining = compute_remaining(resolved_amount(txns[0]), txns[0].entries)

        assert total == Decimal("150.00")
        assert remaining == Decimal("350.00")

    def test_over_budget_remaining_negative(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Over-budget scenario: remaining is negative.

        Arithmetic: estimated=$100, entries=$70+$50=$120.
        remaining = $100 - $120 = -$20.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Gas",
            amount="100.00",
        )
        txn = _make_txn(seed_periods_today[0], template)
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("70.00"), description="Shell",
            purchased_on=date(2026, 1, 5),
        ))
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("50.00"), description="BP",
            purchased_on=date(2026, 1, 6),
        ))
        db.session.commit()

        companion = seed_companion["user"]
        txns = companion_service.get_visible_transactions(
            companion.id, period_id=seed_periods_today[0].id,
        ).transactions
        from app.services.entry_service import compute_remaining
        remaining = compute_remaining(resolved_amount(txns[0]), txns[0].entries)
        assert remaining == Decimal("-20.00")


# ── period navigation ────────────────────────────────────────────────


class TestPeriodNavigationMovedToTheCalendar:
    """``get_previous_period`` is GONE; its two cases are graded on the READ.

    Plan step **C2-f1**.  That function was ``pay_period_service.get_next_period``
    with ``+ 1`` changed to ``- 1`` -- one rule written twice on the stored
    ordinal -- and the companion view asked both, so a schedule whose
    ``period_index`` disagreed with its payday order could step forward into
    one period and back into another.  Both halves are now searches of the
    owner's ONE derived calendar, and since plan step **C2-f2b** they are
    answered by :func:`companion_service.get_visible_transactions` itself:
    ``previous`` and ``next_period`` ride on the page read beside the period
    they flank.  The assertions below are the deleted tests' own, re-pointed
    twice.

    **They were on ``routes.companion._period_neighbours`` until C2-f2b**, and
    that helper is gone rather than moved: an adversarial design review of that
    step found the service handing its whole ``PayCalendar`` across its own
    security boundary so one route helper could ask it two searches.  Answering
    them where the owner was validated is what removed it.

    **``TestGetCompanionPeriods`` sat above this class until C2-f2b and went
    with the function it graded.**  ``companion_service.get_companion_periods``
    answered "every pay period of the linked owner", built for a jump-to
    ``<select>`` that ``companion/index.html`` now replaces with its own prev /
    next links -- and an AST census that day found it with ZERO callers in
    ``app/``.  Its three tests were the only thing keeping a dead
    ``get_all_periods`` reader alive.  Nothing moved with them because nothing
    the application does was graded by them; the navigation they were written
    for is what this class covers.
    """

    @staticmethod
    def _read(companion, period):
        """Return the companion page read for *period*."""
        return companion_service.get_visible_transactions(
            companion.id, period_id=period.id,
        )

    def test_returns_previous_and_next_together(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Period 1's neighbours are periods 0 and 2, from ONE calendar load."""
        with app.app_context():
            view = self._read(seed_companion["user"], seed_periods_today[1])
            assert view.previous is not None
            assert view.previous.period_id == seed_periods_today[0].id
            assert view.next_period is not None
            assert view.next_period.period_id == seed_periods_today[2].id

    def test_returns_none_for_the_first_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """The first period has no previous, which is what hides the back link."""
        with app.app_context():
            view = self._read(seed_companion["user"], seed_periods_today[0])
            assert view.previous is None
            assert view.next_period is not None
            assert view.next_period.period_id == seed_periods_today[1].id

    def test_returns_none_for_the_last_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """The mirror the deleted pair never had a test for.

        ``get_previous_period`` was tested at the schedule's head and
        ``get_next_period`` at its tail, in two different files, so neither
        end was covered on both sides.  One read answers both, so both ends
        are graded here.
        """
        with app.app_context():
            view = self._read(seed_companion["user"], seed_periods_today[-1])
            assert view.next_period is None
            assert view.previous is not None
            assert view.previous.period_id == seed_periods_today[-2].id

    def test_it_reads_the_LINKED_OWNERS_calendar(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """The schedule the page navigates is the linked OWNER's, not the requester's.

        A companion holds no paydays of their own by design, so a read that
        resolved the calendar from the REQUESTING user would answer ``None`` on
        both sides and silently remove the navigation.  The control is the
        companion's own empty calendar beside the owner's answer.

        **The assertion moved DOWN a layer at plan step C2-f2b, and that is the
        point.**  ``routes.companion._period_neighbours`` used to pick the owner
        itself, off the ``user_id`` of the ORM row it was handed -- a correct
        answer reached from the render's data rather than from the security
        check.  The neighbours are resolved inside the function that has
        already validated the companion and resolved ``linked_owner_id``, so
        the owner is chosen exactly once and a presentation layer never sees a
        calendar at all.
        """
        with app.app_context():
            companion = seed_companion["user"]
            assert companion.linked_owner_id == seed_user["user"].id

            view = self._read(companion, seed_periods_today[1])
            assert view.period.period_id == seed_periods_today[1].id
            assert view.previous is not None and view.next_period is not None

            # The control, STRENGTHENED by plan step ``pay_calendar:C4-d``
            # (ruling R-PC45).  It read ``calendar_for(companion.id).periods
            # == ()``: the requester's own calendar was EMPTY, so a read built
            # from ``current_user`` would have hidden both links.  A companion
            # holds no ``budget.pay_schedule`` row -- production's user 2 is
            # exactly this -- and ``calendar_for`` refuses that owner now, so
            # the same mistake is a 500 rather than a silently empty nav.  The
            # control is the stronger claim, and it is the one the ruling
            # bought: the wrong owner cannot be resolved quietly.
            with pytest.raises(PayCalendarError, match="no pay calendar"):
                calendar_for(companion.id)
