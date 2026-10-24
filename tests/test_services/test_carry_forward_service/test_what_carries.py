"""What carry_forward_unpaid moves and what it leaves: a one-off re-placed through the one producer
on the move (R-BAL33), the statuses, soft-deleted rows and other scenarios it leaves behind, and
the bulk UPDATE that re-checks status at the SQL tier (F-049 / C-22).
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.ref import TransactionType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.services import carry_forward_service
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import resolve_transaction_amount
from app.services.one_off import OneOffToPlace, place_one_off
from tests._test_helpers import (
    amount_basis_for,
    derived_span,
    generate_row_of,
    one_off_row_of,
    settled_day_basis_id,
)

from ._helpers import _create_transaction, _create_template


def _place_kindle(seed_user, period, *, due_date=None, amount="162.25"):
    """Place Kayla's Kindle in *period* through the one producer (plan step X-bi-7b).

    A one-off is a rule-less definition plus its placed row (ruling
    **R-BAL20**); the app has exactly one way to make one, so these cases
    take it rather than hand-building the shape.

    Args:
        seed_user: The seed_user fixture dict.
        period: The ORM ``PayPeriod`` to place the row in.
        due_date: The owner's stated day, or ``None`` for the paycheck's
            start (ruling **R-BAL22**).
        amount: The definition's one price.

    Returns:
        The placed, committed :class:`Transaction`.
    """
    row = place_one_off(
        OneOffToPlace(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            name="Kayla's Kindle",
            amount=Decimal(amount),
            category_id=seed_user["categories"]["Groceries"].id,
        ),
        derived_span(period),
        scenario_id=seed_user["scenario"].id,
        due_date=due_date,
    )
    db.session.commit()
    return row


class TestAPeriodMoveRePlacesAOneOff:
    """Ruling **R-BAL33** at carry-forward's move-whole arm (plan step X-bi-7b).

    A one-off is due on its PLACED paycheck's start unless the owner stated a
    day (R-BAL22), so the default follows the placement: a placed row carried
    forward takes the target's start, an owner-stated day -- any date other
    than the source paycheck's start, read by position -- stays, and
    ``occurs_on`` follows the date either way (R-BAL25).  Found by leaf
    7b-1's adversarial review: a one-off rolled to the next paycheck read
    OVERDUE on the dashboard pulse and late in ``payment_timeliness`` where
    the undated row it replaced sat on the *anytime this period* shelf.
    """

    def test_a_default_dated_one_off_takes_the_target_paychecks_start(
        self, app, db, seed_user, seed_periods,
    ):
        """The Kindle walk: born 09-04, carried to 09-18, due 09-18.

        And the balance is IDENTICAL under either date -- a one-off's series
        is flat (R-BAL21) and ``attribution_day`` budgets the row to its
        paycheck -- so the target period's balance moves by the row's
        figure and nothing else, exactly as the date it left behind gave.
        """
        with app.app_context():
            row = _place_kindle(seed_user, seed_periods[0])
            source_start = derived_span(seed_periods[0]).start_date
            target_start = derived_span(seed_periods[1]).start_date
            assert row.due_date == source_start
            assert row.occurs_on == source_start
            account = seed_user["account"]
            before = balance_at.cash_balance_map(
                account,
                BalanceContext.build(
                    seed_user["user"].id, as_of=seed_periods[0].start_date,
                ),
            )

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.expire_all()
            db.session.refresh(row)
            assert count == 1
            assert row.pay_period_id == seed_periods[1].id
            assert row.template_id is not None
            assert row.is_override is False
            assert row.due_date == target_start
            assert row.occurs_on == target_start
            assert resolve_transaction_amount(
                row, amount_basis_for(row),
            ) == Decimal("162.25")
            after = balance_at.cash_balance_map(
                account,
                BalanceContext.build(
                    seed_user["user"].id, as_of=seed_periods[0].start_date,
                ),
            )
            # The row left period 0 and landed in period 1: the running
            # balance at period 0's end recovers the $162.25 and period 1's
            # end is where it was, because the row's whole figure is
            # budgeted to whichever paycheck holds it, on any day inside.
            assert after[seed_periods[0].id] - before[seed_periods[0].id] == Decimal("162.25")
            assert after[seed_periods[1].id] == before[seed_periods[1].id]

    def test_an_owner_dated_one_off_keeps_its_day(
        self, app, db, seed_user, seed_periods,
    ):
        """THE CONTROL: a date other than the source's start is the owner's."""
        with app.app_context():
            source_start = derived_span(seed_periods[0]).start_date
            stated = source_start + timedelta(days=6)
            row = _place_kindle(seed_user, seed_periods[0], due_date=stated)
            assert row.due_date == stated

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.expire_all()
            db.session.refresh(row)
            assert row.pay_period_id == seed_periods[1].id
            assert row.due_date == stated
            assert row.occurs_on == stated
            assert row.is_override is False


class TestCarryForwardUnpaid:
    """Unit tests for carry_forward_unpaid covering gaps in existing tests."""

    def test_settled_status_not_moved(self, app, db, seed_user, seed_periods):
        """Transactions in the RECEIVED status are not carried forward.

        Specimen was ``Settled`` -- the terminal archive added in WU-05 --
        until plan step **balance:X-am** deleted that status.  Carry-forward
        moves PROJECTED rows and nothing else (``is_projected_clause``), so
        every other status is the same specimen; ``Received`` is the one this
        class was otherwise missing.
        """
        with app.app_context():
            txn = _create_transaction(
                seed_user, seed_periods, status_name="Received",
                name="Received Paycheck",
            )
            original_period_id = txn.pay_period_id

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            assert count == 0
            db.session.refresh(txn)
            assert txn.pay_period_id == original_period_id

    # ``test_actual_amount_preserved_after_carry_forward`` lived here until
    # plan step X-au-c3 and has no state left to describe.  It carried a
    # PROJECTED row holding ``actual_amount = 1200.00`` beside a ``1234.56``
    # estimate, and asserted the carry left both alone.  A figure now RECORDS a
    # settle, so an unsettled row cannot carry one -- and carry-forward moves
    # UNPAID rows, so no row it touches has a record to preserve.  What the
    # carry must not disturb is the row's OWN amount, and the envelope suites
    # in ``test_the_envelope_leftover.py`` and ``test_the_envelope_target.py``
    # grade exactly that: ``TestCarryForwardEnvelopePartialSpend`` and
    # its five siblings assert the source's and the target's
    # ``estimated_amount`` on every branch.

    def test_scenario_id_preserved_after_carry_forward(
        self, app, db, seed_user, seed_periods
    ):
        """The transaction's scenario_id is unchanged after carry forward.

        The service only modifies pay_period_id and is_override. This test
        verifies scenario_id is not inadvertently altered.
        """
        with app.app_context():
            txn = _create_transaction(seed_user, seed_periods, name="Scenario Check")
            original_scenario_id = txn.scenario_id

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            db.session.refresh(txn)
            assert txn.scenario_id == original_scenario_id

    def test_all_statuses_comprehensive(self, app, db, seed_user, seed_periods):
        """All 5 statuses plus soft-deleted: only non-deleted projected moves.

        Creates one transaction for each status (projected, paid, received,
        credit, cancelled) plus one projected+deleted. Verifies
        exactly 1 transaction moves and 5 remain in the source period.

        It was 6 and 6 until plan step **balance:X-am** deleted the terminal
        ``Settled`` archive.  The list is written out rather than derived from
        ``StatusEnum`` deliberately: a status added to the enum should make
        somebody decide whether carry-forward moves it, and a self-updating
        list would answer that question silently.
        """
        with app.app_context():
            statuses = ["Projected", "Paid", "Received", "Credit", "Cancelled"]
            original_ids = {}

            for status_name in statuses:
                txn = _create_transaction(
                    seed_user, seed_periods,
                    status_name=status_name,
                    name=f"Status-{status_name}",
                )
                original_ids[status_name] = txn.id

            # Also create a projected+deleted transaction (should NOT move).
            deleted_txn = _create_transaction(
                seed_user, seed_periods,
                status_name="Projected",
                name="Deleted-projected",
            )
            deleted_txn.is_deleted = True
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            # Only the non-deleted projected transaction moved.
            assert count == 1

            # Target period has exactly 1 transaction.
            target_txns = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[1].id)
                .all()
            )
            assert len(target_txns) == 1
            assert target_txns[0].name == "Status-Projected"

            # Source period retains 5 transactions (4 non-projected + 1 deleted).
            source_txns = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(source_txns) == 5

            # Verify each non-moved transaction is still in the source.
            for status_name in ["Paid", "Received", "Credit", "Cancelled"]:
                txn = db.session.get(Transaction, original_ids[status_name])
                assert txn.pay_period_id == seed_periods[0].id, (
                    f"{status_name} transaction should stay in source period"
                )

            # Deleted projected also stays in source.
            db.session.refresh(deleted_txn)
            assert deleted_txn.pay_period_id == seed_periods[0].id

    def test_carry_forward_only_moves_transactions_for_specified_scenario(
        self, app, db, seed_user, seed_periods
    ):
        """Carry forward with scenario_id only moves that scenario's transactions.

        Creates projected transactions in both the baseline and an alternative
        scenario, then carries forward only the baseline.  The alternative
        scenario's transactions must remain untouched.
        """
        with app.app_context():
            baseline_scenario = seed_user["scenario"]

            # Create an alternative scenario for the same user.
            alt_scenario = Scenario(
                user_id=seed_user["user"].id,
                name="What-If",
                is_baseline=False,
            )
            db.session.add(alt_scenario)
            db.session.flush()

            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            # Baseline projected transaction.
            baseline_txn = one_off_row_of(
                seed_periods[0],
                name="Baseline Expense",
                amount=Decimal("50.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=baseline_scenario.id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )

            # Alternative scenario projected transaction.
            alt_txn = one_off_row_of(
                seed_periods[0],
                name="Alt Expense",
                amount=Decimal("75.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=alt_scenario.id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.flush()

            # Carry forward only the baseline scenario.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, baseline_scenario.id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            # Only the baseline transaction should have moved.
            assert count == 1

            db.session.refresh(baseline_txn)
            assert baseline_txn.pay_period_id == seed_periods[1].id

            # Alternative scenario transaction must remain in source.
            db.session.refresh(alt_txn)
            assert alt_txn.pay_period_id == seed_periods[0].id


# ── F-049 / C-22: Conditional UPDATE Race Tests ───────────────────


class TestCarryForwardStatusRecheck:
    """F-049 / C-22: bulk UPDATE re-checks status_id at the SQL tier.

    Closes the race window between
    ``_build_carry_forward_context``'s SELECT and the bulk UPDATE
    that moves discrete rows: a concurrent ``mark_done`` /
    ``mark_credit`` / ``cancel`` request can transition a row out of
    Projected after the SELECT but before our flush, and a per-row
    ``setattr(...)`` would carry a now-settled row into the target
    period -- silently erasing the user's prior status decision.

    The fix replaces the per-row mutation with a conditional bulk
    UPDATE whose WHERE clause includes ``status_id == projected``.
    Race-loser rows are silently left in place and the count
    reflects only the rows that actually moved.
    """

    def test_concurrent_mark_done_excludes_row_from_move(
        self, app, db, seed_user, seed_periods,
    ):
        """A row settled mid-batch must NOT move to the target period.

        Simulates the race by raw-SQL UPDATE'ing a Projected row to
        Paid AFTER the carry-forward context has been built but
        BEFORE the bulk UPDATE applies.  The bulk UPDATE's
        ``status_id == projected`` WHERE clause should reject the
        change for that row, leaving it Paid in the source period
        with a coherent settle-day audit.
        """
        from unittest.mock import patch  # pylint: disable=import-outside-toplevel
        from sqlalchemy import text  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Two projected ad-hoc rows in source.  txn_loser will
            # transition to Paid mid-batch (race-loser); txn_winner
            # remains Projected and must move normally.
            txn_loser = _create_transaction(
                seed_user, seed_periods, name="Race-Loser",
            )
            txn_winner = _create_transaction(
                seed_user, seed_periods, name="Race-Winner",
            )
            db.session.commit()
            loser_id = txn_loser.id
            winner_id = txn_winner.id

            paid_status_id = ref_cache.status_id(StatusEnum.DONE)

            real_build = (
                carry_forward_service._execute._build_carry_forward_context  # pylint: disable=protected-access
            )

            def racing_build(*args, **kwargs):
                """Build the context, then simulate a concurrent mark_done."""
                ctx = real_build(*args, **kwargs)
                # Direct SQL UPDATE bypasses the ORM's session-level
                # bookkeeping so the in-memory ``ctx.discrete_txns``
                # entries remain "Projected" -- exactly the state
                # the race produces in production.  Bumping
                # version_id mirrors the optimistic-lock contract a
                # real concurrent commit would honor.
                #
                # It writes the status and the settle DAY pair in the SAME
                # statement because that is what the seam does (plan steps
                # X-f1 / X-az); the record the seam would also write -- the
                # covering movement -- is not laid, so the loser is a
                # ``$0.00`` close (ruling R-BAL82): this case grades the
                # STATUS precondition, and a figure would change nothing it
                # asserts.  (The row's own figure columns rode in this
                # statement through plan step balance:X-bi-4b-1, because a
                # CHECK paired the day with them.)  ``CURRENT_DATE`` rather
                # than an instant: the column is a civil DAY.
                db.session.execute(
                    text(
                        "UPDATE budget.transactions "
                        "SET status_id = :paid, "
                        "    settled_on = CURRENT_DATE, "
                        "    settled_day_basis_id = :day_basis, "
                        "    version_id = version_id + 1 "
                        "WHERE id = :tid"
                    ),
                    {
                        "paid": paid_status_id,
                        # WHICH KIND of day the race stamped (plan step X-az):
                        # ``entered``, because a concurrent mark-done is a door
                        # supplying the owner's today with no bank document
                        # behind it -- the same basis the seam's own default
                        # arm writes.  It is in the SAME statement for the same
                        # reason the figure is: the pair is welded by
                        # ``ck_transactions_settle_day_basis_pairing``.
                        "day_basis": settled_day_basis_id(
                            SettledDayBasisEnum.ENTERED,
                        ),
                        "tid": loser_id,
                    },
                )
                db.session.commit()
                return ctx

            with patch.object(
                carry_forward_service._execute, "_build_carry_forward_context",
                side_effect=racing_build,
            ):
                count = carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )
                db.session.commit()

            db.session.expire_all()
            loser = db.session.get(Transaction, loser_id)
            winner = db.session.get(Transaction, winner_id)

            # Race-loser stayed at source period AND remained Paid
            # -- the bulk UPDATE skipped it because its status was
            # no longer Projected at SQL execution time.
            assert loser.pay_period_id == seed_periods[0].id, (
                "Race-loser was carried forward despite being Paid; "
                "the F-049 status precondition is not effective."
            )
            assert loser.status.name == "Paid"
            assert loser.settled_on is not None

            # Race-winner moved normally.
            assert winner.pay_period_id == seed_periods[1].id
            assert winner.status.name == "Projected"

            # Count reflects only the moved row.
            assert count == 1, (
                f"Expected 1 row moved (winner only); got {count}"
            )

    def test_concurrent_soft_delete_excludes_row_from_move(
        self, app, db, seed_user, seed_periods,
    ):
        """A row soft-deleted mid-batch must NOT move to the target.

        Mirrors the mark_done test but for soft-delete: the bulk
        UPDATE's ``is_deleted = FALSE`` WHERE clause closes the
        same race for the soft-delete path.
        """
        from unittest.mock import patch  # pylint: disable=import-outside-toplevel
        from sqlalchemy import text  # pylint: disable=import-outside-toplevel

        with app.app_context():
            txn = _create_transaction(seed_user, seed_periods, name="Race-Deleted")
            db.session.commit()
            tid = txn.id

            real_build = (
                carry_forward_service._execute._build_carry_forward_context  # pylint: disable=protected-access
            )

            def racing_build(*args, **kwargs):
                ctx = real_build(*args, **kwargs)
                db.session.execute(
                    text(
                        "UPDATE budget.transactions "
                        "SET is_deleted = TRUE, "
                        "    version_id = version_id + 1 "
                        "WHERE id = :tid"
                    ),
                    {"tid": tid},
                )
                db.session.commit()
                return ctx

            with patch.object(
                carry_forward_service._execute, "_build_carry_forward_context",
                side_effect=racing_build,
            ):
                count = carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )
                db.session.commit()

            db.session.expire_all()
            row = db.session.get(Transaction, tid)
            assert row.is_deleted is True
            assert row.pay_period_id == seed_periods[0].id, (
                "Soft-deleted row was carried forward; F-049 "
                "is_deleted precondition is not effective."
            )
            assert count == 0

    def test_template_linked_row_gets_is_override_in_same_update(
        self, app, db, seed_user, seed_periods,
    ):
        """Template-linked rows flip is_override=True in the same SQL UPDATE.

        Regression for the F-049 fix: collapsing the loop into a
        bulk UPDATE must still set ``is_override = TRUE`` on
        template-linked rows so the partial unique generation indexes do
        not collide with a rule-generated row in the target period.
        """
        with app.app_context():
            template = _create_template(
                seed_user, name="Recurring Tpl", category_key="Groceries",
            )
            txn = generate_row_of(template, seed_periods[0])
            assert txn.is_override is False

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.expire_all()
            db.session.refresh(txn)
            assert count == 1
            assert txn.pay_period_id == seed_periods[1].id
            assert txn.is_override is True

    def test_a_rule_less_definitions_row_moves_without_the_flip(
        self, app, db, seed_user, seed_periods,
    ):
        """A row of a definition with NO rule moves as an ad-hoc row does.

        Plan step balance:X-bi-7a (ruling **R-BAL20**): the bulk UPDATEs
        split on ``Transaction.recurs``, not on the link.  A cleared cadence
        leaves its rows template-linked, and until this step the discrete
        branch flagged them ``is_override`` on the move -- a flag whose only
        effect on a rule-less definition's row is to hide it from
        ``propagate_to_unruled_definition`` for good (the twin's defect
        **BAL-493**).  The recurring sibling above keeps its flip; this row
        keeps its link and ``is_override = False``.

        **Its DATE moves with it since plan step balance:X-bi-7b** (ruling
        **R-BAL33**): the engine dated this row from its paycheck's start (a
        cadence naming no day of the month), which reads as the R-BAL22
        default, so the move re-places it on the target's start.  This case
        asserted ``due_date == generated_due`` until then.
        """
        with app.app_context():
            template = _create_template(
                seed_user, name="Cleared Cadence", category_key="Groceries",
            )
            txn = generate_row_of(template, seed_periods[0])
            template.recurrence_rule = None
            db.session.commit()
            assert txn.due_date == derived_span(seed_periods[0]).start_date
            assert txn.is_override is False
            assert txn.recurs is False

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.expire_all()
            db.session.refresh(txn)
            assert count == 1
            assert txn.pay_period_id == seed_periods[1].id
            assert txn.template_id == template.id
            assert txn.is_override is False
            assert txn.due_date == derived_span(seed_periods[1]).start_date
            assert txn.occurs_on == txn.due_date

    def test_version_id_bumped_by_bulk_update(
        self, app, db, seed_user, seed_periods,
    ):
        """The bulk UPDATE bumps version_id to honor the optimistic-lock contract.

        Regression: replacing per-row ORM mutation with a bulk
        UPDATE must keep the version_id_col contract intact.  An
        UPDATE that does NOT bump version_id would let a concurrent
        ORM-level flush at the old version succeed, silently
        overwriting our batch.
        """
            # noqa: pylint: disable=protected-access
        with app.app_context():
            txn = _create_transaction(seed_user, seed_periods, name="Version Bump")
            db.session.commit()
            db.session.refresh(txn)
            initial_version = txn.version_id

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            db.session.expire_all()
            db.session.refresh(txn)
            assert txn.version_id == initial_version + 1, (
                f"Expected version_id {initial_version + 1}, got "
                f"{txn.version_id}; bulk UPDATE failed to bump."
            )
