"""
Shekel Budget App -- The outstanding set: reader, writer, and their ONE scope

Tests for :mod:`app.services.reconcile_service` -- the reconcile step's offer
set (ruling R-DH (d) / the R-M re-ruling, shipped at plan step S1-c) and the
writer that records a tick against it.

**Moved here from ``test_entry_service`` at plan step X-f2-c1** with the code
it grades, and not one assertion changed on the way: the reader's name and
return SHAPE did (``outstanding_purchases`` -> ``outstanding_set``, grouped by
envelope per ruling R-EW), so ``_listed`` flattens the groups back to the id
list every case below was written against.  Grading the same ids through a new
shape is what makes the move provably behaviour-preserving.
"""

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal


from app import ref_cache
from app.enums import (
    MovementFigureSourceEnum,
    SettledDayBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import (
    account_service,
    cash_ledger,
    reconcile_service,
    status_seam,
    transaction_service,
    transfer_service,
)
from app.services.cash_ledger import derived_amount_basis
from app.services.pay_calendar import (
    PayCalendar,
    calendar_for,
)
from app.services.reconcile_service import _transactions
from app.utils.log_events import (
    EVT_TRANSACTIONS_RECONCILED,
    EVT_TRANSFERS_RECONCILED,
)
from tests._test_helpers import (
    an_entered_day,
    count_amount_bases,
    create_account_of_type,
    figure_source_columns,
    generate_row_of,
    last_covered_day,
    make_cadence_rule,
    make_expense_template,
    make_income_template,
    one_off_row_of,
    open_books_before_the_first_assertion,
    record_paydays_across_a_hole,
    resolved_amount,
    rhythm_of,
    settle_day_columns,
    settlement_if_settling,
    state_template_price,
)
from tests._test_helpers import create_transfer
from tests.oracles.recurrence_baseline import MONTHLY
from app.services.row_valuation import settled_contribution, settled_figure
from app.services.settle_day import record_settle_day


def _make_entry(transaction, user, amount="50.00", description="Kroger",
                purchased_on=None, is_credit=False, account_id=None):
    """Create an entry directly via ORM (bypasses service validation).

    The twin of ``test_entry_service``'s helper, carried with the tests that
    use it rather than imported across test modules: it exists to build a row
    WITHOUT the service under test, so a shared version would couple two
    modules' fixtures to one shape for no gain.  *account_id* is the account
    the purchase's money moved through (plan step ``credit_card:CC-5-2``);
    the row's own when omitted, which is every case but the card swipe.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=transaction.id,
        account_id=transaction.account_id if account_id is None else account_id,
        owner_id=transaction.user_id,
        user_id=user.id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


_OBSERVED_ON = date(2026, 1, 10)


def _statement(account_id, observed_on=_OBSERVED_ON):
    """Return the STATEMENT these tests reconcile against, for *account_id*.

    Plan step **X-f3a-1** (ruling **R-FL**) made every door here take the
    governing ASSERTION rather than a bare civil day, because a tick now records
    WHICH statement showed the money and production carries three days on which
    one account holds more than one assertion.

    **The id is the account's REAL assertion and the day is the test's.**  The
    id has to be real: ``fk_transactions_reconciled_by`` and
    ``fk_transaction_entries_reconciled_by`` refuse a link to a statement that
    does not exist, which is exactly what those composite keys are for.  The day
    does not, and these tests are why it is separable -- they grade the SET
    OPERATION (which rows a bound admits), so the day is an input to the filter
    rather than a fact about the seeded account, as this module's own scope note
    says.

    Args:
        account_id: The account whose governing assertion to borrow.
        observed_on: The civil day to present it for.

    Returns:
        The :class:`~app.services.cash_ledger.AnchorPoint` to reconcile against.
    """
    return replace(
        cash_ledger.governing_anchor(account_id), observed_on=observed_on,
    )


def _reconciled(seed_user, account_id=None, observed_on=_OBSERVED_ON,
                owner_id=None):
    """Return the STATEMENT these tests reconcile against.

    The value both halves of the package take since pay-calendar plan step
    **C4-a-2**: the owner's pay CALENDAR (whose rows may be offered, and the
    span each is dated against), the account, and the assertion.  Built here so
    a case that wants a MISMATCHED pair -- another owner's calendar against this
    owner's account -- says so by naming the odd one out rather than by
    assembling three values by hand.

    Args:
        seed_user: The seeded owner bundle.
        account_id: The account to reconcile; the seed user's own when omitted.
        observed_on: The civil day to present the assertion for.
        owner_id: Whose CALENDAR to date the rows against; the seed user's when
            omitted.  Named separately from *account_id* precisely so the
            ownership cases can cross the two.

    Returns:
        The :class:`~app.services.reconcile_service.Statement`.
    """
    account = seed_user["account"].id if account_id is None else account_id
    owner = seed_user["user"].id if owner_id is None else owner_id
    return reconcile_service.Statement(
        calendar_for(owner), account, _statement(account, observed_on),
    )
_BEFORE_THE_STATEMENT = date(2026, 1, 5)
_AFTER_THE_STATEMENT = date(2026, 1, 12)


def _outstanding_debit(txn, seed_user, amount="50.00",
                       purchased_on=_BEFORE_THE_STATEMENT):
    """Attach one debit purchase with NO recorded posting day."""
    return _make_entry(
        txn, seed_user["user"], amount=amount,
        description="Kroger", purchased_on=purchased_on,
    )


class TestTheOutstandingSet:
    """``outstanding_set`` / ``record_settled_days`` -- ONE definition.

    The reconcile step's reader and its writer share
    ``reconcile_service._purchases._outstanding_scope``, and that sharing is the security
    property, not a tidiness one: a purchase the panel does not OFFER can never
    be stamped by a forged id, because the writer re-applies the same five
    clauses to whatever ids arrive from the form.  So each clause is graded
    from BOTH doors -- listed-or-not, and stamped-or-not -- and a clause that
    held on one side only would fail here.

    An out-of-scope id is silently skipped rather than raising: this is a set
    operation, and the project's "404 for both not-found and not-yours" posture
    expressed as a filter.  ``record_settled_days`` returns what actually
    CHANGED, never what was asked for, which is what makes the skip observable.

    **Scope: this grades the SET OPERATION, not its consequence.**
    ``_OBSERVED_ON`` is passed in rather than resolved from the account, so a
    row stamped here may still read as outstanding to the projection -- which
    is correct for a unit test of the filter and is why the figures a tick
    MOVES are graded end to end in
    ``test_anchor_settle_partition.py::TestRecordingAPurchaseDoesNotMoveTheProjection``
    and the route's day-resolution in ``test_routes/test_accounts.py``.
    """

    @staticmethod
    def _reconcile(seed_user, entry_ids):
        """Run the writer against the seed user's own checking account."""
        return reconcile_service.record_settled_days(
            _reconciled(seed_user), set(entry_ids),
        )

    @staticmethod
    def _listed(seed_user):
        """Return the ids the reader offers for the seed user's account."""
        return [
            purchase.entry_id
            for group in reconcile_service.outstanding_set(_reconciled(seed_user)).groups
            for purchase in group.purchases
        ]

    def test_a_tick_records_WHICH_statement_showed_the_purchase(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The purchase arm writes the clearing fact, not just the day.

        Ruling **R-FL**: ``settled_on`` is an upper bound on the true posting
        day, and ``reconciled_by_id`` is the observation itself.  The day alone
        cannot carry it, because production holds three days on which Checking
        has more than one assertion -- so a rule over ``settled_on`` could not
        say WHICH statement a tick was made against.

        Graded here because this arm's writer is a bulk ``UPDATE``: a column
        omitted from it fails silently, leaving the panel reporting a
        reconciliation it did not record.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.commit()
            statement = _statement(seed_user["account"].id)

            assert self._reconcile(seed_user, [entry.id]) == 1
            db.session.commit()
            db.session.expire_all()

            reloaded = db.session.get(TransactionEntry, entry.id)
            assert reloaded.reconciled_by_id == statement.anchor_id
            assert reloaded.settled_on == statement.observed_on

    def test_an_outstanding_debit_is_listed_and_stamped(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The happy path, so every refusal below is a real discrimination.

        A debit purchase made before the statement day with no recorded posting
        day is offered, and ticking it stamps ``settled_on`` with the
        assertion's own ``observed_on`` -- an upper bound on the true posting
        day, and the only bound the reconciliation predicate consumes.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.commit()

            assert self._listed(seed_user) == [entry.id]
            assert self._reconcile(seed_user, [entry.id]) == 1

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == _OBSERVED_ON

    def test_an_already_recorded_purchase_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """``settled_on IS NULL`` -- the definition of outstanding itself.

        A purchase whose posting day is already recorded is not outstanding
        whatever that day is, so re-submitting its id must not overwrite the
        user's own sharper date with the assertion's coarser upper bound.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            record_settle_day(entry, an_entered_day(_BEFORE_THE_STATEMENT))
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == _BEFORE_THE_STATEMENT

    def test_a_credit_purchase_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A credit-card purchase never touches checking, so it is not on the
        statement being reconciled.

        It leaves through its own CC Payback sibling transaction, so the
        reservation ignores its dates entirely -- recording one would be
        recording a fact nothing reads, against a statement it never appeared
        on.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="50.00", description="Amazon",
                purchased_on=_BEFORE_THE_STATEMENT, is_credit=True,
            )
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_a_purchase_made_after_the_statement_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """``purchased_on <= observed_on`` -- and the DB backstop it fronts.

        A purchase made after the day the balance was read cannot be inside it.
        Stamping it would write a ``settled_on`` earlier than its own
        ``purchased_on``, which
        ``ck_transaction_entries_settled_not_before_purchase`` refuses at the
        database; filtering here makes that constraint a backstop rather than a
        reachable 500 on a form submission.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(
                txn, seed_user, purchased_on=_AFTER_THE_STATEMENT,
            )
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_a_purchase_on_a_settled_parent_IS_offered(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A CLOSED envelope's purchase still owes the bank's day.

        **This asserted the exact opposite until the developer's 2026-08-17
        ruling**, on the premise that "the entry reservation prices only
        Projected rows, so a purchase on a settled parent is inert".  Ruling
        **R-FM** had already falsified that one step earlier: the row's own
        leg of the time (``cash_ledger.settled_cash_leg``, deleted at ruling
        **R-BAL81**) subtracted every POSTED purchase from a settled row's
        close, so recording the day moved that purchase's cash out of the
        close's day and onto the bank's; since ``balance:X-bi-4a`` every
        purchase is a movement of its own and the row books nothing (ruling
        **R-BAL80**), so the day is the whole of what recording it changes.
        The total never changes, and the DAY is what a paper statement
        reconciles against, which is this panel's whole subject.

        Measured on the 2026-08-17 production dump: 28 closed envelopes hold 61
        debit purchases carrying no posting day, ``$4,360.07`` between them,
        none of which this panel would ever have offered.
        ``entry_service._doors._reject_settled_parent`` is the write side of the
        same ruling -- it admits ``settled_on`` on a settled parent and refuses
        every cost-bearing field.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            # Re-fetch against the live session: the fixture object is
            # loaded upstream and an assignment on a stale instance is not
            # reliably marked dirty for the next flush (the same reason
            # ``test_anchor_service`` re-gets its account).
            status_seam.apply_status_change(
                db.session.get(Transaction, txn.id),
                ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(db.session.get(Transaction, txn.id), ref_cache.status_id(StatusEnum.DONE)),
            )
            db.session.commit()

            assert self._listed(seed_user) == [entry.id]
            assert self._reconcile(seed_user, [entry.id]) == 1

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on == _OBSERVED_ON

    def test_a_purchase_on_a_cancelled_parent_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A Cancelled row is not in the plan, so its purchases are not either.

        Its purchase survives on the row (cancelling takes nothing off the
        books), so without the contributing filter it would be offered for an
        envelope the user has already cancelled.  **This graded a SOFT-DELETED
        parent until plan step** ``credit_card:CC-5-4a-4``: the database now
        refuses a row hidden while it holds a purchase (ruling **R-CC92**),
        and a row's delete takes its purchases off first (ruling **R-CC75**),
        so no deleted parent holds one to offer.  The filter's other half is
        the one a parent can still reach.  Re-expressed under rule 5,
        developer-confirmed 2026-09-23.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            transaction_service.apply_requested_status(
                db.session.get(Transaction, txn.id),
                ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.commit()
            assert db.session.get(TransactionEntry, entry.id) is not None

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_another_accounts_purchase_matches_nothing(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A balance assertion declares the real balance of ONE account.

        A user may hold more than one checking account (there is no per-type
        uniqueness), and reconciling across them would drop the other account's
        reservation without ever raising its anchor -- inflating its projected
        balance by the purchase.  The forged id is submitted to account A's
        reconcile and must change nothing on B.
        """
        with app.app_context():
            account_b = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=seed_user["account"].account_type_id,
                    name="Checking 2",
                    anchor_balance=Decimal("2000.00"),
                ),
            )
            db.session.flush()
            # A ONE-OFF envelope (its own rule-less definition) rather than a
            # second row off the shared template: the engine's row already
            # answers this paycheck's occurrence, and
            # ``idx_transactions_template_scenario_occurrence`` holds one row
            # per occurrence.
            txn_b = one_off_row_of(
                seed_periods[0],
                name="Groceries B",
                amount=Decimal("500.00"),
                user_id=seed_periods[0].user_id,
                account_id=account_b.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=seed_entry_template["transaction"].transaction_type_id,
                category_id=seed_user["categories"]["Groceries"].id,
                is_envelope=True,
            )
            db.session.flush()
            entry_b = _outstanding_debit(txn_b, seed_user)
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [entry_b.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry_b.id,
            ).settled_on is None

    def test_a_card_swipe_in_a_checking_envelope_is_offered_on_the_CARDs_statement(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The scope asks the PURCHASE's account, not its row's (plan step CC-5-2).

        Rulings **R-CC15** / **R-BAL75**: a swipe recorded in a checking
        envelope with the card as its account is a movement ON the card.  The
        card's statement is what shows it, so the card's reconcile offers it
        and stamps it; checking's does neither, though the envelope row is
        checking's.  Graded from both doors, as every clause here is.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            txn = seed_entry_template["transaction"]
            assert txn.account_id == seed_user["account"].id
            swipe = _make_entry(
                txn, seed_user["user"], amount="60.00",
                purchased_on=_BEFORE_THE_STATEMENT, account_id=card.id,
            )
            db.session.commit()

            # Checking: not offered, not stamped -- the money never moved
            # through it.
            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [swipe.id]) == 0
            db.session.expire_all()
            assert db.session.get(TransactionEntry, swipe.id).settled_on is None

            # The card: offered under its envelope's heading, and stamped.
            on_the_card = _reconciled(seed_user, account_id=card.id)
            offered = [
                (group.key, purchase.entry_id)
                for group in reconcile_service.outstanding_set(on_the_card).groups
                for purchase in group.purchases
            ]
            assert offered == [(txn.id, swipe.id)]
            assert reconcile_service.record_settled_days(
                on_the_card, {swipe.id},
            ) == 1
            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, swipe.id,
            ).settled_on == _OBSERVED_ON

    def test_another_users_purchase_matches_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods,
        seed_entry_template,
    ):
        """A forged id from another user's data changes nothing.

        The IDOR case at its natural shape: the other user's purchase sits on
        the other user's account, in the other user's pay period.

        **It is OVER-DETERMINED, and that is stated rather than left to be
        discovered** (finding N-69's lesson).  Two clauses reject this row --
        ``Transaction.account_id == account_id`` and
        ``PayPeriod.user_id == owner_id`` -- so deleting either one alone still
        leaves this test green.  The account clause has its own firing control
        (``test_another_accounts_purchase_matches_nothing``); the user clause is
        isolated by the test below, which is the only shape that can.
        """
        with app.app_context():
            other_period = record_paydays_across_a_hole(
                user_id=seed_second_user["user"].id,
                first_payday=date(2026, 1, 2), num_periods=1, rhythm=rhythm_of(14),
            )[0]
            other_txn = one_off_row_of(
                other_period,
                name="Their groceries",
                amount=Decimal("500.00"),
                user_id=other_period.user_id,
                account_id=seed_second_user["account"].id,
                scenario_id=seed_second_user["scenario"].id,
                transaction_type_id=seed_entry_template["transaction"].transaction_type_id,
                is_envelope=True,
            )
            db.session.flush()
            other_entry = _make_entry(
                other_txn, seed_second_user["user"], amount="50.00",
                description="Theirs", purchased_on=_BEFORE_THE_STATEMENT,
            )
            db.session.commit()

            assert self._listed(seed_user) == []
            assert self._reconcile(seed_user, [other_entry.id]) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, other_entry.id,
            ).settled_on is None

    # **``test_the_OWNER_clause_is_load_bearing_on_its_own`` was DELETED at
    # plan step ``pay_calendar:C13-a``**, and this note is what a reader
    # looking for it needs.  That case built a transaction on THIS owner's
    # account under ANOTHER owner's pay period -- a row the schema then
    # permitted -- to reach the ownership half of this package's scope.
    # ``fk_transactions_owner_period`` refuses that INSERT now, so the case
    # could only assert the refusal, which is
    # ``test_c13a_transaction_owner_key.test_a_stranger_s_account_is_refused``
    # verbatim and belongs there.
    #
    # **What it graded is NOT lost, and a first version of its replacement
    # said it was.**  The scope it reached is
    # ``Transaction.pay_period_id.in_(statement.owned_period_ids)`` -- a
    # PERIOD SET since pay-calendar plan step C4-a-2, not the
    # ``pay_periods.user_id`` comparison that replacement named -- and
    # ``TestTheScopeIsTheCALENDARsNotTheTables`` below grades both arms of it
    # through a short calendar, an input the composite keys still permit.
    # Measured 2026-09-02: deleting ``_purchases.py``'s clause fails
    # ``test_the_PURCHASE_arm_is_scoped_the_same_way``.  So the clause is
    # live, graded, and load-bearing for ``_block_headings``' totality, and
    # ``C13-b`` must not treat it as one of finding **P75**'s nineteen: those
    # are reads that REFUSE, and this one SCOPES.

    def test_a_mixed_submission_stamps_only_what_is_in_scope(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A partly-forged submission is neither rejected nor obeyed in full.

        One real outstanding purchase and one out-of-scope id in the same
        request: the count returned is what CHANGED (1), not what was asked for
        (2), so the log records the gap instead of the whole request being
        thrown away or waved through.  A submission that raised on the bad id
        would lose the user's good ticks too.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            good = _outstanding_debit(txn, seed_user, amount="20.00")
            forged = _outstanding_debit(
                txn, seed_user, amount="30.00",
                purchased_on=_AFTER_THE_STATEMENT,
            )
            db.session.commit()

            assert self._reconcile(seed_user, [good.id, forged.id]) == 1

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, good.id,
            ).settled_on == _OBSERVED_ON
            assert db.session.get(
                TransactionEntry, forged.id,
            ).settled_on is None

    def test_an_empty_submission_is_a_no_op(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ticking nothing changes nothing and issues no UPDATE.

        The state a user is in when they open the panel and dismiss it, so it
        must not be an error -- and it must not fall through to an unfiltered
        bulk update, which is precisely what the retired
        ``clear_entries_for_anchor_true_up`` was.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user)
            db.session.commit()

            assert self._reconcile(seed_user, []) == 0

            db.session.expire_all()
            assert db.session.get(
                TransactionEntry, entry.id,
            ).settled_on is None

    def test_the_list_is_ordered_oldest_purchase_first(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Deterministic order, with ``id`` breaking a same-day tie.

        The panel is a checklist read against a paper statement, which is
        chronological; an unordered list would shuffle between renders of the
        same data and make the two impossible to walk together.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            second = _outstanding_debit(
                txn, seed_user, amount="20.00",
                purchased_on=date(2026, 1, 7),
            )
            first = _outstanding_debit(
                txn, seed_user, amount="30.00",
                purchased_on=date(2026, 1, 3),
            )
            same_day_as_second = _outstanding_debit(
                txn, seed_user, amount="40.00",
                purchased_on=date(2026, 1, 7),
            )
            db.session.commit()

            assert self._listed(seed_user) == [
                first.id, second.id, same_day_as_second.id,
            ]


class TestARefundIsAnOutstandingPurchaseAndTheTallyNETS:
    """A merchant credit the bank has not been seen to pay back.

    Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b-3``.  A refund
    IS a negative purchase, so one with no recorded posting day satisfies every
    clause of ``_outstanding_scope`` -- debit, unsettled, made on or before the
    statement day, under a projected parent on this account -- and the panel
    offers it.  ``OutstandingPurchase.amount`` claimed to be POSITIVE and was
    not.

    **The bank-import door's own refunds are NOT this set.**
    ``statement_match._create._born_purchase`` gives a purchase the bank's
    posting day at birth, so a refund it files is settled from the moment it
    exists.  What reaches here is a negative the owner typed on the edit door,
    or one whose posting day they cleared -- both of which
    ``EntryUpdateSchema`` allows by design.

    **The tally NETS, and that is the decision rather than an accident.**  The
    panel's sentence is about what the envelope is *still holding back*, and a
    refund it holds is money it expects to ARRIVE.  A magnitude sum would make
    the figure disagree with the reservation the sentence names -- the defect
    that sentence has already been corrected for once, by `$488.16`.
    """

    def test_a_negative_purchase_is_OFFERED_and_the_total_nets(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """`$120.00` out and `-$45.00` back: two rows, `$75.00` net."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            charge = _outstanding_debit(txn, seed_user, amount="120.00")
            refund = _outstanding_debit(txn, seed_user, amount="-45.00")
            db.session.commit()

            offered = reconcile_service.outstanding_set(_reconciled(seed_user))

            assert sorted(self._listed(seed_user)) == sorted(
                [charge.id, refund.id],
            ), "a refund with no posting day is outstanding like any purchase"
            assert offered.purchase_count == 2, (
                "the COUNT is a count of ticks the owner has to make"
            )
            assert offered.purchase_total == Decimal("75.00"), (
                "120.00 out and 45.00 back is 75.00 of net movement still "
                "held back -- a magnitude sum would read 165.00"
            )

    def test_a_refund_the_BANK_DOOR_filed_is_not_offered(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The control: a recorded posting day is what takes a row OUT.

        Without it the case above passes on a scope that offers every purchase
        whatever its posting day -- which would put the whole import's work
        back on the panel.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _outstanding_debit(txn, seed_user, amount="-45.00")
            # **BOTH columns, because they are ONE fact.**
            # ``ck_transaction_entries_settle_day_basis_pairing`` is a
            # biconditional, so naming only the day is an ``IntegrityError``
            # rather than a row -- and ``observed`` is the basis the bank
            # import writes (plan step **X-az**), which is the state this case
            # is about.
            for column, value in settle_day_columns(
                _BEFORE_THE_STATEMENT, SettledDayBasisEnum.OBSERVED,
            ).items():
                setattr(entry, column, value)
            db.session.commit()

            offered = reconcile_service.outstanding_set(_reconciled(seed_user))

            assert self._listed(seed_user) == []
            assert offered.purchase_count == 0
            assert offered.purchase_total == Decimal("0.00")

    @staticmethod
    def _listed(seed_user):
        """Return the ids the reader offers -- the same read as the class above."""
        return [
            purchase.entry_id
            for group in reconcile_service.outstanding_set(
                _reconciled(seed_user),
            ).groups
            for purchase in group.purchases
        ]


class TestTheSetIsGroupedByItsParent:
    """Ruling **R-EW**: a purchase nests under the thing it belongs to.

    The reader returned a FLAT list of ORM rows until plan step X-f2-c1, and the
    panel named each purchase's parent in a trailing fragment on its own line.
    R-EW rules the other shape -- a grocery purchase and the grocery envelope
    read as one block -- and rejects grouping by act-type, which is exactly what
    separates those two.

    **These grade the GROUPING, which the class above cannot see**: every case
    there flattens the blocks back to an id list on purpose, so that the move
    from ``entry_service`` could be proved to change no answer.  A grouping
    defect is invisible to a flattened assertion by construction.
    """

    @staticmethod
    def _second_envelope(seed_user, seed_periods, name="Gas"):
        """Build a SECOND envelope transaction on the same account and period.

        Two blocks are the minimum that can be grouped wrongly: with one, every
        grouping rule agrees.

        The envelope is the engine's own row of a priced, every-paycheck
        definition (:func:`generate_row_of`, plan step balance:X-cf).
        """
        template = make_expense_template(
            db.session, seed_user, amount="80.00",
            name=name, category_key="Groceries", is_envelope=True,
        )
        return generate_row_of(template, seed_periods[0])

    @staticmethod
    def _resolve(seed_user, observed_on=_OBSERVED_ON):
        return reconcile_service.outstanding_set(
            _reconciled(seed_user, observed_on=observed_on),
        )

    def test_each_envelope_is_one_block_carrying_its_own_purchases(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Two envelopes, three purchases, two blocks -- and no crossover.

        Hand arithmetic: Groceries 40.00 + 60.00 = 100.00, Gas 25.00, and the
        set's own total 125.00.  Every figure is asserted, not just the shape:
        a block total that summed the WRONG block's purchases would still give
        two blocks with the right ids.
        """
        with app.app_context():
            groceries = seed_entry_template["transaction"]
            gas = self._second_envelope(seed_user, seed_periods)
            _outstanding_debit(groceries, seed_user, amount="40.00")
            _outstanding_debit(groceries, seed_user, amount="60.00")
            _outstanding_debit(gas, seed_user, amount="25.00")
            db.session.commit()

            result = self._resolve(seed_user)

            # The value objects THEMSELVES, field for field.  Grading only
            # ids and totals leaves `purchased_on` and `description` unpinned,
            # and mutating `purchased_on` to the statement day survives the
            # whole suite -- every line in the panel captioned with the wrong
            # date, on the one screen whose job is to be walked against a
            # paper statement.  Measured; this is the case that catches it.
            assert result.groups[0].purchases == (
                reconcile_service.OutstandingPurchase(
                    entry_id=result.groups[0].purchases[0].entry_id,
                    purchased_on=_BEFORE_THE_STATEMENT,
                    description="Kroger",
                    amount=Decimal("40.00"),
                ),
                reconcile_service.OutstandingPurchase(
                    entry_id=result.groups[0].purchases[1].entry_id,
                    purchased_on=_BEFORE_THE_STATEMENT,
                    description="Kroger",
                    amount=Decimal("60.00"),
                ),
            )
            assert [group.name for group in result.groups] == [
                "Weekly Groceries", "Gas",
            ]
            assert [g.period.start_date for g in result.groups] == [
                seed_periods[0].start_date, seed_periods[0].start_date,
            ]
            assert [len(g.purchases) for g in result.groups] == [2, 1]
            assert [g.total for g in result.groups] == [
                Decimal("100.00"), Decimal("25.00"),
            ]
            assert result.purchase_count == 3
            assert result.purchase_total == Decimal("125.00")
            assert result.is_empty is False
            # The block names its PARENT, which is the key the grouping is
            # built on and the id plan step X-f2-c2's close tick will post.
            assert [g.key for g in result.groups] == [
                groceries.id, gas.id,
            ]

    def test_one_envelope_in_two_periods_is_two_blocks_the_heading_can_tell_apart(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The recurrence engine gives one template one row PER PERIOD.

        So one envelope in two periods is two parents carrying ONE name, and
        both can hold outstanding purchases at a single assertion -- the scope
        has no period clause, deliberately (a purchase is outstanding or it is
        not, whichever paycheck funded it).  The flat list was equally ambiguous
        per line; GROUPING promotes that ambiguity to the block heading, so the
        leaf that creates the heading is the leaf that has to resolve it.

        Without the period on the group the panel renders two identical
        headings with different subtotals and no way to tell which paycheck
        each belongs to -- and plan step X-f2-c2 hangs a close tick, which
        SETTLES the row, off each of them.
        """
        with app.app_context():
            first = seed_entry_template["transaction"]
            # The same definition's row in the NEXT paycheck, from the engine.
            second = generate_row_of(
                seed_entry_template["template"], seed_periods[1],
            )
            _outstanding_debit(first, seed_user, amount="40.00")
            _outstanding_debit(second, seed_user, amount="60.00")
            db.session.commit()

            result = self._resolve(seed_user)

            assert [g.name for g in result.groups] == [
                "Weekly Groceries", "Weekly Groceries",
            ]
            # ONE name, TWO periods -- which is what the heading renders.
            # The span is the one the owner's calendar DERIVES (pay-calendar
            # plan step C4-a-2); on this fixture's contiguous biweekly schedule
            # it equals the stored pair, which is what the second assertion
            # measures rather than assumes.
            assert [g.period.start_date for g in result.groups] == [
                seed_periods[0].start_date, seed_periods[1].start_date,
            ]
            assert [g.period.end_date for g in result.groups] == [
                last_covered_day(seed_periods[0]), last_covered_day(seed_periods[1]),
            ]
            assert [g.period.period_id for g in result.groups] == [
                seed_periods[0].id, seed_periods[1].id,
            ]
            assert [g.total for g in result.groups] == [
                Decimal("40.00"), Decimal("60.00"),
            ]

    def test_blocks_are_ordered_by_their_oldest_outstanding_purchase(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The block a user is most likely looking for is first.

        **The order is the ROW sort's, not a second sort**, so this is the case
        that would catch a reader that grouped with a plain ``dict`` built from
        an unordered query, or that sorted the blocks by name or by id.  Here
        the older purchase belongs to the SECOND-created envelope, so id order
        and date order disagree and only one of them is right.
        """
        with app.app_context():
            groceries = seed_entry_template["transaction"]
            gas = self._second_envelope(seed_user, seed_periods)
            _outstanding_debit(
                groceries, seed_user, purchased_on=date(2026, 1, 8),
            )
            _outstanding_debit(
                gas, seed_user, purchased_on=date(2026, 1, 2),
            )
            db.session.commit()
            assert gas.id > groceries.id

            result = self._resolve(seed_user)

            assert [g.name for g in result.groups] == [
                "Gas", "Weekly Groceries",
            ]

    def test_purchases_within_a_block_are_oldest_first(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Within a block the purchase order is the account's own timeline.

        The id breaks a same-day tie, so the sort is total: a nondeterministic
        order in a list the user ticks off against a paper statement is a
        usability defect, and in a financial replay it is a reproducibility one.
        """
        with app.app_context():
            groceries = seed_entry_template["transaction"]
            second = _outstanding_debit(
                groceries, seed_user, amount="20.00",
                purchased_on=date(2026, 1, 7),
            )
            first = _outstanding_debit(
                groceries, seed_user, amount="30.00",
                purchased_on=date(2026, 1, 3),
            )
            same_day = _outstanding_debit(
                groceries, seed_user, amount="40.00",
                purchased_on=date(2026, 1, 7),
            )
            db.session.commit()

            result = self._resolve(seed_user)

            assert [p.entry_id for p in result.groups[0].purchases] == [
                first.id, second.id, same_day.id,
            ]

    def test_an_account_with_nothing_outstanding_reports_itself_empty(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """No block, no count, no money -- and ``is_empty`` says so.

        The steady state for a user who reconciles as they go, and the state
        the panel answers with prose rather than an empty form.  Every total is
        asserted to be the quantised zero rather than merely falsy, because
        each is rendered by the money macro.

        **The day moved to before the period at plan step X-f2-c2, and the
        reason is the widening rather than a fixture convenience.**  The seed
        envelope is Projected in a period starting 2026-01-02, so against
        ``_OBSERVED_ON`` (the 10th) its own close is now OVERDUE and the panel
        offers it -- correctly, under ruling R-EW.  "Nothing outstanding" is
        therefore no longer a property of an account with no purchases; it
        needs a day nothing has landed on yet, which is what the 1st is.
        """
        with app.app_context():
            result = self._resolve(seed_user, observed_on=date(2026, 1, 1))

            assert result.is_empty is True
            assert result.groups == ()
            assert result.purchase_count == 0
            assert result.purchase_total == Decimal("0.00")
            assert result.payment_count == 0
            assert result.payment_total == Decimal("0.00")
            assert result.deposit_count == 0
            assert result.deposit_total == Decimal("0.00")

    def test_empty_is_read_off_the_COUNTS_not_off_the_purchase_count(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A panel offering only a BILL is not empty.

        ``is_empty`` was ``purchase_count == 0`` until plan step X-f2-c2, and
        its own docstring predicted this case: a kind arrives that is not a
        purchase, and the old definition answers "empty" for a panel with
        things to offer, suppressing them behind the "nothing is being held
        back twice" copy.

        **It used to be graded by CONSTRUCTING an unreachable set** -- groups
        empty, count non-zero -- because the producer could not build a
        discriminating state.  It can now: the seed envelope carries no
        purchases and its own close is overdue, so ``purchase_count`` is 0
        while ``payment_count`` is 1.  Grading the rule on a state the producer
        really produces is strictly stronger than grading it on a hand-built
        one, and the hand-built one is no longer even reachable under ruling
        R-FC (every offer arrives inside a group).

        Shown to FIRE: reverting ``is_empty`` to ``purchase_count == 0``
        fails here.
        """
        with app.app_context():
            result = self._resolve(seed_user)

            assert result.purchase_count == 0
            assert result.payment_count == 1
            assert result.is_empty is False

    def test_the_empty_constructor_matches_a_genuinely_empty_read(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """``OutstandingSet.empty()`` is the route's shape for "no assertion".

        A route reaching for it must get exactly what the producer would have
        returned, or the panel would render one thing for an account with no
        assertion and another for an account with nothing outstanding -- two
        spellings of one empty state, which is the shape this arc removes.

        The day is the 1st for the reason given two cases up.
        """
        with app.app_context():
            assert reconcile_service.OutstandingSet.empty() == self._resolve(
                seed_user, observed_on=date(2026, 1, 1),
            )


class TestTheTransactionArm:
    """Plan step **X-f2-c2**: the panel offers the source ROWS too.

    Ruling **R-EW** widens the offer set past purchases; this arm is the
    envelope's own close and bills, income included (**R-FD**), settled through
    the verb the grid's Mark Paid calls (**R-FA**) on the STATEMENT's day.

    **The bound is the OVERDUE set and it is the whole security story of this
    arm**, so every clause is graded from BOTH doors -- offered-or-not, and
    settled-or-not -- exactly as the purchase arm's is.  A clause that held on
    one side only would let a forged id settle a row the panel never showed.

    The seed fixture's envelope lives in period 0 (2026-01-02 .. 2026-01-15)
    and is Projected at `$500.00`, so ``_OBSERVED_ON`` (the 10th) is after its
    attribution day and it is offered; 2026-01-01 is before it and it is not.
    """

    @staticmethod
    def _bill(seed_user, period, *, name="Electricity", amount="180.00",
              fires_on_day=None, income=False):
        """Create a projected NON-envelope row -- a bill, or a deposit.

        The engine's own row of a priced definition (:func:`generate_row_of`,
        plan step balance:X-cf).  A bill that names no day of the month
        repeats every paycheck and is dated on the paycheck's own start;
        one that names *fires_on_day* is a MONTHLY bill on that day, which
        is how a row comes to be due on the 8th or the 14th -- the engine
        dates it there (``compute_due_date``), and it lands in *period*
        because that paycheck covers the day.

        Args:
            seed_user: The seeded owner bundle.
            period: The paycheck the row is funded in.  For a monthly bill
                it must be the one covering the day it fires on.
            name: The definition's name.
            amount: Its stated price.
            fires_on_day: The day of the month a monthly bill falls due, or
                ``None`` for an every-paycheck one.
            income: Build a deposit rather than a bill.
        """
        builder = make_income_template if income else make_expense_template
        if fires_on_day is None:
            template = builder(
                db.session, seed_user, amount=amount,
                name=name, category_key="Groceries",
            )
        else:
            type_id = ref_cache.txn_type_id(
                TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE,
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=type_id,
                name=name,
                default_amount=Decimal(amount),
                is_envelope=False,
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            make_cadence_rule(template, MONTHLY, fires_on_day=fires_on_day)
        return generate_row_of(template, period)

    @staticmethod
    def _offered(seed_user, observed_on=_OBSERVED_ON):
        """Return ``{offer key: offer}`` for the seed user's account (a row's id, a leg's pair)."""
        return {
            group.settle.key: group.settle
            for group in reconcile_service.outstanding_set(
                _reconciled(seed_user, observed_on=observed_on),
            ).groups
            if group.settle is not None
        }

    @staticmethod
    def _settle(seed_user, ids, corrections=None,
                observed_on=_OBSERVED_ON, transfer_ids=(),
                transfer_corrections=None):
        """Run the WRITE UNION against the seed user's own account.

        It was ``record_settled_transactions`` until plan step X-f2-c3, which
        made the two source-row arms one writer parameterised by an ``Arm``
        (finding **N-225**) -- so there is no per-arm entry point left to call
        and the union is the door.  *ids* are the rows' ticks and
        *transfer_ids* the transfers' -- each arm's own form field since leaf
        ``balance:X-bi-6-4c-2`` (ruling **R-BAL145**), re-scoped by that arm.
        """
        return reconcile_service.record_reconciliation(
            reconcile_service.ReconcileSubmission(
                statement=_reconciled(seed_user, observed_on=observed_on),
                entry_ids=set(),
                transaction_ids=set(ids),
                corrections=corrections or {},
                transfer_ids=set(transfer_ids),
                transfer_corrections=transfer_corrections or {},
            ),
        )

    def test_an_overdue_row_is_offered_and_settles_on_the_statement_day(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The happy path, so every refusal below is a real discrimination.

        The whole point of the leaf: the settle day is the STATEMENT's, not the
        user's today.  The seam stamps ``display_today()`` when no day is
        supplied, so a test asserting only "it settled" would pass with the
        wrong date on every row -- which is the one screen a user reads against
        a paper statement.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()

            assert txn.id in self._offered(seed_user)
            assert self._settle(seed_user, [txn.id]) == 1

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn.id)
            assert reloaded.settled_on == _OBSERVED_ON
            assert reloaded.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_a_tick_records_WHICH_statement_showed_the_row(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The transaction arm writes the clearing fact beside the settle.

        Ruling **R-FL**.  It is written by the SHARED writer rather than by
        either arm's settle verb, and that placement is the rule: both verbs are
        also the grid's Mark Paid, which settles a row without any statement
        having shown it, so a link written there would record an observation
        nobody made.  Ticking on this panel IS the observation.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()
            statement = _statement(seed_user["account"].id)

            assert self._settle(seed_user, [txn.id]) == 1
            db.session.commit()
            db.session.expire_all()

            reloaded = db.session.get(Transaction, txn.id)
            assert reloaded.reconciled_by_id == statement.anchor_id
            assert reloaded.settled_on == statement.observed_on

    def test_a_row_that_is_not_yet_overdue_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The date bound, from both doors.

        ``attribution_day <= observed_on`` IS the overdue set (ruling R-G
        clamps a projected row's landing day up to ``as_of + 1``), so a row the
        projection has not reached yet is not something a statement can show.
        A forged id for it settles nothing.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()
            early = date(2026, 1, 1)

            assert self._offered(seed_user, observed_on=early) == {}
            assert self._settle(seed_user, [txn.id], observed_on=early) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).settled_on is None

    def test_the_bound_is_the_DUE_DATE_when_the_row_carries_one(
        self, app, db, seed_user, seed_periods,
    ):
        """The landing day is ``attribution_day``, not the period's start.

        A bill due on the 20th of a period that STARTS on the 2nd is not
        overdue on the 10th, even though its period is.  The SQL half of the
        bound admits it (``period.start_date <= observed_on``) and the Python
        half refuses it -- so this is the case that proves the superset is
        actually narrowed rather than merely described.

        Shown to FIRE: dropping ``_lands_on_or_before`` offers this row.
        """
        with app.app_context():
            later = self._bill(
                seed_user, seed_periods[0], name="Rent", fires_on_day=14,
            )
            earlier = self._bill(
                seed_user, seed_periods[0], name="Water", fires_on_day=8,
            )
            db.session.commit()

            offered = self._offered(seed_user)
            assert later.id not in offered
            assert earlier.id in offered
            assert offered[earlier.id].attributed_on == date(2026, 1, 8)

    def test_a_transfer_shadow_belongs_to_the_OTHER_arm(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A shadow is offered, and it is tagged as the TRANSFER arm's.

        **This case inverted at plan step X-f2-c3 and the inversion is the
        step.**  It read "neither offered nor settled": the panel could not
        settle a transfer at all, because ``settle_transaction`` REFUSES a
        shadow (transfer invariant 3 -- the parent and both legs move
        together), so this arm's scope excluded one and nothing else offered
        it.  The transfer arm settles through ``update_transfer`` instead, so
        the row IS offered now -- and what is graded here is that it belongs to
        that arm and not to this one, which is the clause the two arms
        partition the table on.

        **Built through the real transfer service**, not by writing a
        ``transfer_id`` onto an ordinary row: the expense shadow that lands on
        this account is a genuine Projected row in the same period, so it is
        inside every other clause of the scope and ONLY the membership clause
        can tell the two arms' rows apart.
        """
        with app.app_context():
            other = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Savings",
                    account_type_id=seed_user["account"].account_type_id,
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.flush()
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], other,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()

            shadow = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id == transfer.id,
                    Transaction.account_id == seed_user["account"].id,
                )
                .one()
            )

            offer = self._offered(seed_user)[(transfer.id, seed_user["account"].id)]
            assert offer.kind is reconcile_service.OfferKind.TRANSFER
            assert offer.amount == Decimal("75.00")
            # The transaction arm's own scope still refuses it: asked directly,
            # with the transfer arm out of the picture.  Without this the case
            # above would pass for a panel that offered the shadow through the
            # WRONG arm and settled one leg.
            owner_id = seed_user["user"].id
            assert shadow.id not in _transactions.outstanding_transactions(
                # The PANEL's own STATEMENT, which ``outstanding_set`` builds
                # once and threads (pay-calendar plan step C4-a-2): the arm
                # takes the value rather than three arguments it would
                # reassemble.
                _reconciled(seed_user),
                # The PANEL's own basis, which ``outstanding_set`` builds once
                # (plan step X-au-j).  Built here rather than defaulted inside
                # the arm: the parameter is required precisely so a producer
                # cannot quietly rebuild its caller's derivations.
                derived_amount_basis(owner_id, seed_user["scenario"].id),
            )

    def test_a_settled_row_is_neither_offered_nor_re_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Only a PROJECTED row is waiting on the bank.

        And the writer's half is what stops a double-submit re-dating a row
        that already settled: the second POST finds nothing in scope.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()

            assert self._settle(seed_user, [txn.id]) == 1
            db.session.commit()

            assert self._offered(seed_user) == {}
            assert self._settle(seed_user, [txn.id]) == 0

    def test_another_users_row_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
        seed_second_user,
    ):
        """Ownership, from both doors -- a REAL second budget.

        The scope reaches the owner through the row's PAY PERIOD, which is the
        only user_id a Transaction has, so this grades the join as well as the
        clause.

        **The other owner is a seeded user rather than an id nobody holds**
        (plan step X-au-j).  It was ``seed_user.id + 1000`` until this step,
        which no ``users`` row answers to -- so every join dropped it for the
        trivial reason and the ownership clause was never the thing under test.
        A second budget that really exists, with its own baseline scenario and
        its own periods, is the threat this control is written for, and it is
        the shape the panel's amount basis now requires: that basis is built
        for the OWNER being asked about (``require_baseline_scenario``), which
        an unowned id cannot answer at all.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()
            other_id = seed_second_user["user"].id

            assert reconcile_service.outstanding_set(
                _reconciled(seed_user, owner_id=other_id),
            ).groups == ()
            assert reconcile_service.record_reconciliation(
                reconcile_service.ReconcileSubmission(
                    statement=_reconciled(seed_user, owner_id=other_id),
                    entry_ids=set(),
                    transaction_ids={txn.id},
                    corrections={},
                    transfer_ids=set(), transfer_corrections={},
                ),
            ) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).settled_on is None

    def test_another_accounts_row_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A statement declares ONE account's balance.

        Reconciling across accounts would settle a row the bank never showed on
        this statement, on a day that account was never asserted for.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            other = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Second Checking",
                    account_type_id=seed_user["account"].account_type_id,
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.commit()

            assert reconcile_service.outstanding_set(
                _reconciled(seed_user, account_id=other.id),
            ).groups == ()
            assert reconcile_service.record_reconciliation(
                reconcile_service.ReconcileSubmission(
                    # The other ACCOUNT, this owner's calendar and THIS
                    # account's assertion -- the cross-account pairing the
                    # case is named for, so it is spelled out rather than
                    # taken from the helper.
                    statement=reconcile_service.Statement(
                        calendar_for(seed_user["user"].id), other.id,
                        _statement(seed_user["account"].id),
                    ),
                    entry_ids=set(),
                    transaction_ids={txn.id},
                    corrections={},
                    transfer_ids=set(), transfer_corrections={},
                ),
            ) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, txn.id).settled_on is None

    def test_a_soft_deleted_row_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A deleted row is not money this account owes."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            # Re-fetched into THIS session, exactly as the purchase arm's own
            # soft-delete case does: the fixture's object is attached to the
            # session the fixture ran in, so assigning to it directly writes
            # nothing and the control passes for no reason.
            db.session.get(Transaction, txn.id).is_deleted = True
            db.session.commit()

            assert self._offered(seed_user) == {}
            assert self._settle(seed_user, [txn.id]) == 0

    def test_an_envelope_still_being_spent_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The VALUE half of the bound, from both doors.

        An envelope settles at ``sum(entries)`` over EVERY entry it holds, so
        one still carrying a purchase made after the statement day would book
        that purchase too -- dated on the statement's day, and with no
        correction box, because an entries-derived row is not correctable
        (**R-FF**).  The purchase arm has always refused such an entry; this is
        the same rule applied to the parent, and without it the two arms
        disagree about the same dollars.

        **Shown to FIRE**: removing the ``_wholly_spent_by`` term from
        ``_outstanding_rows`` offers this row at `$100.00` and settles it.

        Measured on a clone of production before the fix: one `$137.45`
        purchase three days after Checking's 2026-08-06 assertion made the
        panel offer *Close Groceries* at `$622.55` rather than `$485.10`, and
        ticking it raised the projected balance by exactly `$137.45` at +30d,
        +90d and +365d -- already-spent money handed back to the projection.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(
                txn, seed_user["user"], amount="40.00",
                purchased_on=_BEFORE_THE_STATEMENT,
            )
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                purchased_on=_AFTER_THE_STATEMENT,
            )
            db.session.commit()

            assert txn.id not in self._offered(seed_user)
            assert self._settle(seed_user, [txn.id]) == 0

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn.id)
            assert reloaded.settled_on is None
            assert status_seam.recorded_settlement(reloaded) is None

    def test_an_envelope_spent_only_BEFORE_the_statement_is_still_offered(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The negative control for the refusal above.

        Without it that test would pass just as well if the arm had stopped
        offering envelopes carrying entries at all, which is a different -- and
        wrong -- rule.  The discriminating fact is the entry's DAY, so the same
        two purchases dated on or before the statement are still offered, at
        their sum.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(
                txn, seed_user["user"], amount="40.00",
                purchased_on=_BEFORE_THE_STATEMENT,
            )
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                purchased_on=_OBSERVED_ON,
            )
            db.session.commit()

            assert self._offered(seed_user)[txn.id].amount == Decimal("100.00")
            assert self._settle(seed_user, [txn.id]) == 1

    def test_a_row_with_no_entries_is_unaffected_by_the_value_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """A bill answers the value bound over an empty set.

        Its own control because the bound is written ``all(...)`` and an
        ``all()`` over nothing is True -- the behaviour a bill and a deposit
        need, and exactly the kind of vacuous truth that deserves a test rather
        than a comment.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0])
            db.session.commit()

            assert bill.id in self._offered(seed_user)
            assert self._settle(seed_user, [bill.id]) == 1

    def test_an_empty_submission_is_a_no_op(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ticking nothing settles nothing, and issues no query."""
        with app.app_context():
            assert self._settle(seed_user, []) == 0


# **``TestThePanelIsDatedByTheDERIVEDSpan`` was deleted at plan step
# ``pay_calendar:C4-c``, and it is worth saying what it did rather than
# leaving a gap.**  It was plan step C4-a-2's grade: four cases planted a
# ``budget.pay_periods.end_date`` of 2026-01-08 on a period the paydays
# end on 01-15, with a statement day of 01-10 between the two, and
# asserted that the panel's caption, its block heading and its offer set
# all answered from the DERIVATION -- one of them a control showing the
# stored column really would have moved the landing day.  They were the
# only FIRING controls this module could have (ledger row **P53**):
# every fixture builds periods through ``pay_period_write``, so a stored
# end and a derived end agree by construction and a case written on one
# passes against the other.
#
# The column is dropped.  The plant is not expressible, the two answers
# are one answer, and P53 closes with the class rather than being left
# open under a test that can no longer be written.  The panel's dating is
# still graded -- ``TestTheScopeIsTheCALENDARsNotTheTables`` below drives
# the same three surfaces on ordinary schedules.


class TestTheScopeIsTheCALENDARsNotTheTables:
    """A row the calendar never saw is NOT OFFERED, rather than a 500.

    Pay-calendar plan step **C4-a-2**, second design.  The panel dates every row
    it offers off the owner's calendar
    (:meth:`~app.services.pay_calendar.PayCalendar.require_period`), and the
    first cut reached that lookup from a scope written on
    ``pay_periods.user_id``.  Two reads of two different things then have to
    agree, and under ``READ COMMITTED`` they need not: ``/grid`` and
    ``/dashboard`` append paydays AND populate rows into them inside one
    ``write_transaction`` (``routes/_period_population.py``, ruling **R-R38**),
    so a concurrent render on a lapsed schedule creates rows this panel's query
    admits and its span lookup cannot date.  Both of this package's COMMAND
    doors render after committing -- the reconcile POST and the true-up PATCH
    through ``prompt_fragment`` -- which made it a 500 on a money screen
    (balance finding **N-358**).

    **The scope is the calendar's own saved ids now, so the state is
    inexpressible rather than merely unlikely** -- the shape
    ``statement_match._candidates`` already had, and the rule
    ``require_period``'s own docstring states: *where the precondition is
    carried by the QUERY, the total form is honest.*

    These cases hold the property directly, by handing the panel a calendar
    that legitimately lacks a period the ACCOUNT's rows are filed in.  That is
    the same shape the concurrency produces and needs no concurrency to build.
    """

    @staticmethod
    def _bill(seed_user, period, name="Electricity"):
        """Create one projected bill in *period*, due on its payday.

        The engine's row of an every-paycheck definition, which
        ``compute_due_date`` dates on the paycheck's own start.
        """
        template = make_expense_template(
            db.session, seed_user, amount="180.00",
            name=name, category_key="Groceries",
        )
        return generate_row_of(template, period)

    @staticmethod
    def _short_calendar(seed_user, drop_period_id):
        """Return the owner's calendar with one payday REMOVED.

        Stands in for the calendar a request read a moment before a concurrent
        writer created that payday.  Built from the owner's real paydays minus
        one rather than from literals, so the periods it does hold are the ones
        the database holds.

        Args:
            seed_user: The seeded owner bundle.
            drop_period_id: The ``budget.pay_periods.id`` to leave out.

        Returns:
            The shortened :class:`~app.services.pay_calendar.PayCalendar`.
        """
        whole = calendar_for(seed_user["user"].id)
        return PayCalendar.from_paydays(
            [
                (period.period_id, period.start_date)
                for period in whole.periods
                if period.period_id != drop_period_id
            ],
            whole.eras,
            seed_user["user"].id,
            history_opens_on=None,
        )

    def test_a_row_in_a_period_the_calendar_lacks_is_not_offered(
        self, app, db, seed_user, seed_periods,
    ):
        """The whole property, and the case that used to be a 500.

        Two bills, one in each of the first two periods, both overdue against
        the statement.  Asked with a calendar that holds only the first, the
        panel offers the first and simply does not ask about the second.
        """
        with app.app_context():
            inside = self._bill(seed_user, seed_periods[0], "Electricity")
            outside = self._bill(seed_user, seed_periods[1], "Internet")
            db.session.commit()

            offered = {
                group.settle.key
                for group in reconcile_service.outstanding_set(
                    reconcile_service.Statement(
                        self._short_calendar(seed_user, seed_periods[1].id),
                        seed_user["account"].id,
                        _statement(
                            seed_user["account"].id, date(2026, 1, 20),
                        ),
                    ),
                ).groups
                if group.settle is not None
            }

            assert inside.id in offered
            assert outside.id not in offered

    def test_the_WHOLE_calendar_offers_the_row_the_short_one_dropped(
        self, app, db, seed_user, seed_periods,
    ):
        """The firing control for the case above.

        Without it that test passes for a panel that offers nothing at all, or
        for a bill the fixture never made overdue.  Same rows, same day, the
        owner's REAL calendar: both are offered.
        """
        with app.app_context():
            inside = self._bill(seed_user, seed_periods[0], "Electricity")
            outside = self._bill(seed_user, seed_periods[1], "Internet")
            db.session.commit()

            offered = {
                group.settle.key
                for group in reconcile_service.outstanding_set(
                    _reconciled(seed_user, observed_on=date(2026, 1, 20)),
                ).groups
                if group.settle is not None
            }

            assert inside.id in offered
            assert outside.id in offered

    def test_the_PURCHASE_arm_is_scoped_the_same_way(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The third arm, which is what keeps the block HEADING total.

        ``_block_headings`` labels the parents every arm produced, so a purchase
        arm still scoped on ``pay_periods.user_id`` would have handed it a
        parent the calendar cannot date -- the same 500 through a different
        door.  A purchase against a parent outside the short calendar is not
        offered either.
        """
        with app.app_context():
            later = self._bill(seed_user, seed_periods[1], "Internet")
            _make_entry(later, seed_user["user"], amount="30.00")
            db.session.commit()

            groups = reconcile_service.outstanding_set(
                reconcile_service.Statement(
                    self._short_calendar(seed_user, seed_periods[1].id),
                    seed_user["account"].id,
                    _statement(seed_user["account"].id, date(2026, 1, 20)),
                ),
            ).groups

            assert later.id not in {group.key for group in groups}


class TestTheTransferArm:
    """Plan step **X-f2-c3**: the panel offers a TRANSFER's leg too.

    Money moving between two of the owner's own accounts still leaves one of
    them, so a checking statement shows it exactly as it shows a bill.
    Replayed over production's 57 Checking assertion days, 8 would have carried
    a transfer offer, `$5,442.89` -- six `$500.00` savings sweeps, one
    `$1,910.95` Mortgage payment and one `$531.94` Van Loan payment.

    **What makes it a separate arm is the SETTLE.**  A transfer is three rows
    and ``CLAUDE.md`` invariants 3 and 4 say they move together, so
    ``settle_transaction`` refuses a shadow and ``update_transfer`` is the
    verb.  These grade that a tick moves all THREE and stamps the statement's
    day on both legs -- which is the whole reason the panel prints a note
    saying a second account moves.
    """

    @staticmethod
    def _savings(seed_user, name="Savings"):
        """Create a second cash account for the transfer's other leg.

        Its BOOKS open before anything this class dates (plan step X-f3c-2b,
        ruling **R-HG**): ``create_account`` opens them on the assertion's own
        day, which is today, and every transfer here settles on a statement day
        earlier than that.  It moves no figure -- the assertion still clears
        whatever settled on its own day.
        """
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                name=name,
                account_type_id=seed_user["account"].account_type_id,
                anchor_balance=Decimal("100.00"),
            ),
        )
        db.session.flush()
        open_books_before_the_first_assertion(db.session, account)
        return account

    @classmethod
    def _transfer_out(cls, seed_user, seed_periods, amount="75.00",
                      due_date=None):
        """Return ``(transfer, expense shadow on the seed account)``."""
        transfer = create_transfer(
            seed_user, db.session, seed_user["account"],
            cls._savings(seed_user), seed_periods[0],
            amount=Decimal(amount), due_date=due_date,
        )
        db.session.commit()
        shadow = (
            db.session.query(Transaction)
            .filter(
                Transaction.transfer_id == transfer.id,
                Transaction.account_id == seed_user["account"].id,
            )
            .one()
        )
        return transfer, shadow

    _offered = staticmethod(TestTheTransactionArm._offered)
    _settle = staticmethod(TestTheTransactionArm._settle)

    def test_a_tick_records_the_STATEMENT_on_this_accounts_leg_alone(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The clearing fact is NOT mirrored, where the settle day is.

        Ruling **R-FL**, and the asymmetry is the point.  A transfer leaves one
        bank and arrives at another: the asserted account's statement showed its
        own leg, and the other account's statement is a document nobody has read
        in this act.  ``settled_on`` IS mirrored onto both legs (transfer
        invariant 3, graded by the test below); ``reconciled_by_id`` must not
        be, or ticking a savings sweep off a checking statement would record
        that the savings statement showed the money too.

        This is also what makes the posted walk's per-account shadow read
        meaningful -- see
        ``test_account_posting_service.TestWalkAccountLedger``'s own case, which
        measures what reading the wrong leg's link costs.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)
            statement = _statement(seed_user["account"].id)

            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 1
            db.session.commit()
            db.session.expire_all()

            legs = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id)
                .all()
            )
            assert len(legs) == 2
            recorded = {
                leg.account_id: leg.reconciled_by_id for leg in legs
            }
            assert recorded[seed_user["account"].id] == statement.anchor_id
            other = next(
                account_id for account_id in recorded
                if account_id != seed_user["account"].id
            )
            assert recorded[other] is None, (
                "The other account's leg named a statement nobody read."
            )

    def test_a_tick_settles_the_parent_and_BOTH_legs_on_the_statement_day(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The arm's whole point: three rows move, dated by the statement.

        The leg on the OTHER account settles too -- which is what the panel's
        section note tells the user -- and both carry the asserted day rather
        than the seam's default of the user's today.  A settle that moved one
        leg would break transfer invariants 3 and 4 silently, because
        ``sync_transaction_postings`` returns nothing for a shadow and the
        ledger would stay flat while the grid showed one side settled.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)

            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 1
            db.session.commit()

            db.session.expire_all()
            legs = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id)
                .all()
            )
            assert len(legs) == 2
            settled = ref_cache.status_id(StatusEnum.DONE)
            assert {leg.status_id for leg in legs} == {settled}
            assert {leg.settled_on for leg in legs} == {_OBSERVED_ON}
            assert db.session.get(
                type(transfer), transfer.id,
            ).status_id == settled

    def test_it_is_offered_under_its_OWN_section_with_the_both_sides_note(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """TRANSFER sorts last and carries the sentence about the other account.

        The section is the ACT and the tally is the LEG (below), so this grades
        the half that decides what the screen says.  A transfer landing in the
        Bills section would read as an observation about one account, which is
        exactly what it is not.
        """
        with app.app_context():
            transfer, _shadow = self._transfer_out(seed_user, seed_periods)

            groups = reconcile_service.outstanding_set(_reconciled(seed_user)).groups
            block = next(
                group for group in groups
                if group.key == (transfer.id, seed_user["account"].id)
            )
            assert block is groups[-1]
            assert block.section.label == "Transfers"
            assert "both sides" in block.section.note
            assert block.settle_closes_an_envelope is False

    def test_the_expense_leg_counts_as_a_PAYMENT_and_the_income_leg_a_DEPOSIT(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """One act, one section -- but the statement shows a direction.

        Ruling **R-FD** counts deposits apart from payments because they do not
        sum to anything a reader wants, and a transfer is no exception: money
        leaving Checking is a payment on Checking's statement, and the same
        transfer's other leg is a deposit on the savings account's.  Both are
        graded, from the two panels they appear on.
        """
        with app.app_context():
            savings = self._savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()

            outgoing = reconcile_service.outstanding_set(_reconciled(seed_user))
            assert outgoing.payment_total >= Decimal("75.00")
            assert outgoing.deposit_count == 0

            incoming = reconcile_service.outstanding_set(
                _reconciled(seed_user, account_id=savings.id),
            )
            assert incoming.deposit_count == 1
            assert incoming.deposit_total == Decimal("75.00")
            assert incoming.payment_count == 0
            assert transfer.id  # the same transfer, offered on both panels

    def test_a_row_that_is_not_yet_overdue_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The shared bound applies to this arm too, from BOTH doors.

        The transfer's due date is the day after the statement, so nothing the
        bank showed can have included it.  Graded from the write side as well:
        a clause held on the read side only would let a forged id settle a
        transfer the panel never offered -- and settling one moves a SECOND
        account.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(
                seed_user, seed_periods, due_date=date(2026, 1, 11),
            )

            assert (transfer.id, seed_user["account"].id) not in self._offered(seed_user)
            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, shadow.id).settled_on is None

    def test_another_accounts_transfer_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A balance assertion declares ONE account's balance.

        The transfer runs between two accounts that are neither of them the one
        being reconciled, so its shadows are not on this statement.  Settling
        across accounts would record money against a statement that never
        showed it -- and here it would move two accounts at once.
        """
        with app.app_context():
            source = self._savings(seed_user, name="Second Checking")
            target = self._savings(seed_user, name="Third Checking")
            transfer = create_transfer(
                seed_user, db.session, source, target, seed_periods[0],
                amount=Decimal("75.00"),
            )
            db.session.commit()

            shadow = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id == transfer.id,
                    Transaction.account_id == source.id,
                )
                .one()
            )

            assert (transfer.id, seed_user["account"].id) not in self._offered(seed_user)
            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 0

            db.session.expire_all()
            assert db.session.get(Transaction, shadow.id).settled_on is None

    def test_a_soft_deleted_transfer_is_neither_offered_nor_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A deleted transfer is not money this account owes.

        Graded because the WRITER acts on the PARENT rather than on the shadow
        it was handed: ``update_transfer`` treats a soft-deleted transfer as
        absent and raises ``NotFoundError``, which this route has no handler
        for -- so a scope that admitted one would be a 500 on a money door
        rather than a silent skip.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)
            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()

            assert (transfer.id, seed_user["account"].id) not in self._offered(seed_user)
            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 0

    def test_a_settled_transfer_is_neither_offered_nor_re_settled(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Already recorded is not outstanding, and a replay must not re-date.

        ``done -> done`` is a legal identity transition, so nothing downstream
        would refuse a second submission -- the SCOPE is what stops it, and a
        re-settle would rewrite ``settled_on`` on both legs to a later
        statement's day.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)
            assert self._settle(seed_user, [], transfer_ids=[transfer.id]) == 1
            db.session.commit()

            assert (transfer.id, seed_user["account"].id) not in self._offered(seed_user)
            assert self._settle(
                seed_user, [], observed_on=date(2026, 1, 14), transfer_ids=[transfer.id],
            ) == 0

            db.session.expire_all()
            assert {
                leg.settled_on
                for leg in db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id).all()
            } == {_OBSERVED_ON}

    def test_a_correction_books_on_both_legs_and_counts_as_one(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """A transfer's tick is correctable, and the figure lands on the pair.

        Ruling **R-FF**: correctable exactly when the settle takes its MANUAL
        branch, and a transfer has no other -- a shadow is never
        purchase-tracked (production: 342 shadows, 0 entries).  The corrected
        figure must reach BOTH legs or the two accounts disagree about how much
        money moved between them.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)
            assert self._offered(seed_user)[(transfer.id, seed_user["account"].id)].is_correctable is True

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [], transfer_ids=[transfer.id],
                    transfer_corrections={transfer.id: Decimal("74.11")},
                ) == 1
            db.session.commit()

            db.session.expire_all()
            assert {
                settled_figure(leg)
                for leg in db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id).all()
            } == {Decimal("74.11")}

            events = [
                record for record in caplog.records
                if getattr(record, "event", None) == EVT_TRANSFERS_RECONCILED
            ]
            assert len(events) == 1
            assert events[0].corrected_count == 1

    def test_an_ECHOED_prefill_leaves_both_legs_NULL_and_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """An untouched box is not a correction, on this arm either.

        The panel prefills every correctable row, so a five-row submit posts
        five figures.  Writing an echo would populate a column that is NULL on
        all 17 settled transfer shadows in production -- the only signal that
        says a human read one off a statement.
        """
        with app.app_context():
            transfer, shadow = self._transfer_out(seed_user, seed_periods)

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [], transfer_ids=[transfer.id],
                    transfer_corrections={transfer.id: Decimal("75.00")},
                ) == 1
            db.session.commit()

            db.session.expire_all()
            # Both legs RECORD what the settle booked, on the ``resolved``
            # source -- an echoed prefill is not a correction, and since plan
            # step X-au-c3 "not a correction" is the record's SOURCE rather
            # than a NULL figure.
            legs = (
                db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id).all()
            )
            assert {
                status_seam.recorded_settlement(leg).source for leg in legs
            } == {MovementFigureSourceEnum.RESOLVED}
            assert {settled_figure(leg) for leg in legs} == {Decimal("75.00")}

            events = [
                record for record in caplog.records
                if getattr(record, "event", None) == EVT_TRANSFERS_RECONCILED
            ]
            assert events[0].corrected_count == 0


class TestWhatATickBooks:
    """Ruling **R-FA** / **R-FB** / **R-FF**: the amount, and who may change it.

    The panel must show the figure a tick will BOOK and offer a box exactly
    where the verb would read one.  Both come from ``transaction_service``
    rather than from a column here, and these grade that they agree.
    """

    _bill = staticmethod(TestTheTransactionArm._bill)
    _offered = staticmethod(TestTheTransactionArm._offered)
    _settle = staticmethod(TestTheTransactionArm._settle)

    def test_an_envelope_with_entries_offers_sum_entries_and_no_box(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """`$40` + `$60` against a `$500` estimate offers `$100.00`, read-only.

        Ruling R-EW refuses an editable close: an envelope's ``actual_amount``
        is DERIVED from its entries, so a box would be a second writer of it.
        The offered figure is what the verb books, not the estimate.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(txn, seed_user["user"], amount="60.00")
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("100.00")
            assert offer.is_correctable is False

            assert self._settle(seed_user, [txn.id]) == 1
            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn.id),
            ) == Decimal("100.00")

    def test_an_envelope_with_NO_entries_offers_its_estimate_WITH_a_box(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ruling **R-FF**: correctable exactly when the verb goes manual.

        Production's `Kayla's Spending Money` is envelope-tracked, budgeted
        `$100.00` and carries ZERO entries, so the verb already treats it as
        manual -- there is no derived value to protect and no reason to make
        the user leave the panel to correct it.  Offered at its estimate, with
        the box.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("500.00")
            assert offer.is_correctable is True

    def test_a_bill_ticked_untouched_leaves_actual_amount_NULL(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The prefill echoed back is not a correction.

        The panel renders the amount box PREFILLED, so an untouched tick
        submits the same figure the row would have booked anyway.  Writing it
        into ``actual_amount`` would populate a column that is NULL on every
        uncorrected row and destroy the only signal that says a human typed
        one -- the signal ruling R-FB's own production measurement is made of.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="180.00")
            db.session.commit()

            assert self._settle(
                seed_user, [bill.id], {bill.id: Decimal("180.00")},
            ) == 1

            db.session.expire_all()
            reloaded = db.session.get(Transaction, bill.id)
            # The tick RECORDS what it booked and says the source is
            # ``resolved`` -- nobody typed a different figure (plan step
            # X-au-c3).  It asserted ``actual_amount is None`` until that
            # step, because a NULL there was the only signal that no human
            # had corrected the row.
            assert status_seam.recorded_settlement(reloaded).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert settled_figure(reloaded) == Decimal("180.00")
            assert settled_contribution(reloaded) == Decimal("180.00")

    def test_a_bill_ticked_with_a_different_figure_books_it(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Ruling **R-FB**: a bill's tick MAY correct its amount.

        Production's shape, to the cent: Electricity estimated `$300.00`,
        statement says `$245.32`.  Settling at the estimate would leave
        `$54.68` for plan step X-f3's residual to book as Uncategorized Income
        -- at the one moment the user is holding the paper that says the true
        figure.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="300.00")
            db.session.commit()

            assert self._settle(
                seed_user, [bill.id], {bill.id: Decimal("245.32")},
            ) == 1

            db.session.expire_all()
            reloaded = db.session.get(Transaction, bill.id)
            assert settled_figure(reloaded) == Decimal("245.32")
            # The correction is recorded BESIDE the plan, never into it: the
            # bill is the engine's derived row, so its plan is what the amount
            # model answers, and that is still the definition's $300.00.
            assert resolved_amount(reloaded) == Decimal("300.00")

    def test_a_correction_on_a_DERIVED_row_is_ignored_not_applied(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A forged box for an envelope with entries changes nothing.

        The panel renders no input there, so the only way to submit one is by
        hand.  **What refuses it is the VERB, and a review proved that is the
        only thing that ever did**: the writer carried its own "read the box
        only where the panel offered one" guard, and deleting that guard left
        every test green, because ``settle_transaction`` routes an
        entries-derived row to a branch that ignores ``actual_amount`` outright.
        The guard is gone; this grades the rule that was doing the work.
        `$999.99` against `$40.00` of entries books `$40.00`.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            assert self._settle(
                seed_user, [txn.id], {txn.id: Decimal("999.99")},
            ) == 1

            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn.id),
            ) == Decimal("40.00")

    def test_income_is_offered_as_a_DEPOSIT_and_settles_to_Received(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling **R-FD**: a deposit you are waiting on is what a statement settles.

        Production's largest offered deposit was an FSA reimbursement of
        `$1,958.87`; it settles to Received, not Paid, and the set counts it
        apart from the payments because a deposit and a bill do not sum to
        anything a reader wants.
        """
        with app.app_context():
            deposit = self._bill(
                seed_user, seed_periods[0], name="FSA Reimbursement",
                amount="1958.87", income=True,
            )
            db.session.commit()

            result = reconcile_service.outstanding_set(_reconciled(seed_user))
            assert result.deposit_count == 1
            assert result.deposit_total == Decimal("1958.87")
            assert result.payment_count == 0
            assert self._offered(seed_user)[deposit.id].is_income is True

            assert self._settle(seed_user, [deposit.id]) == 1
            db.session.expire_all()
            assert db.session.get(Transaction, deposit.id).status_id == (
                ref_cache.status_id(StatusEnum.RECEIVED)
            )

    def test_the_two_totals_are_never_summed_into_one(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The double-count ruling R-FA's own text warns about.

        An envelope with `$40` + `$60` of purchases and its own `$100` close
        offers BOTH -- and a single "total" would report `$200` against `$100`
        of money.  The set publishes the purchase total and the payment total
        separately and nothing adds them.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(txn, seed_user["user"], amount="60.00")
            db.session.commit()

            result = reconcile_service.outstanding_set(_reconciled(seed_user))
            assert result.purchase_count == 2
            assert result.purchase_total == Decimal("100.00")
            assert result.payment_count == 1
            assert result.payment_total == Decimal("100.00")
            # Asserted by NAMING the fields, not by ``hasattr``: a probe on a
            # dataclass passes for any typo (``status_seam``'s own X-aa
            # lesson), so it would report "there is no combined total" about a
            # field spelled anything at all.
            assert {field.name for field in fields(result)} == {
                "groups",
                "purchase_count", "purchase_total",
                "payment_count", "payment_total",
                "deposit_count", "deposit_total",
                "damaged",
            }


class TestTheCashFigureBesideTheBookedOne:
    """Finding **N-226**: what a tick BOOKS is not always what a statement shows.

    An envelope settles at ``sum(entries)`` over EVERY entry it holds, and a
    card purchase is one of those -- but a card purchase never touches
    checking, which is exactly why the purchase arm refuses to OFFER one.  So a
    `$40` debit plus a `$60` card purchase is offered at `$100.00` on a screen
    captioned "tick everything your statement shows", against a statement
    showing `$40`.

    **The LEDGER was right either way** -- a card purchase moves nothing
    through checking (``cash_ledger.movement_cash_leg``), as the row's own leg
    of the time subtracted the credit sum -- so the fix prints both figures
    rather than changing what a tick books: ``actual_amount`` legitimately IS
    total spend, and moving it would make the panel disagree with the grid
    and the analytics.

    Production carries 18 card entries in history and ZERO on a Projected
    envelope today, so this is latent rather than live.
    """

    _bill = staticmethod(TestTheTransactionArm._bill)
    _offered = staticmethod(TestTheTransactionArm._offered)

    def test_an_envelope_holding_a_CARD_purchase_publishes_both_figures(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """`$40` debit + `$60` card: booked `$100.00`, statement `$40.00`."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Amazon", is_credit=True,
            )
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("100.00")
            assert offer.cash_amount == Decimal("40.00")

    def test_a_swipe_on_the_CARD_is_off_the_checking_statement_and_counted_ONCE(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Plan step ``credit_card:CC-5-2``: the third term, and the partition.

        `$40` debit on checking + `$60` swipe ON the card (no flag) + `$25`
        swipe on the card ALREADY POSTED: booked `$125.00`; checking's
        statement shows `$40.00`.  The posted card swipe sits in exactly one
        term -- the account's, not the posted-purchase one -- or the cash
        figure would read `$15.00`, a purchase taken out twice.  Graded on the
        panel's own figure and on the three terms directly, so a term that
        stopped partitioning would fail here and not only downstream.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Target", account_id=card.id,
            )
            posted_on_the_card = _make_entry(
                txn, seed_user["user"], amount="25.00",
                description="Shell", account_id=card.id,
            )
            record_settle_day(posted_on_the_card, an_entered_day(_OBSERVED_ON))
            db.session.commit()

            offer = self._offered(seed_user)[txn.id]
            assert offer.amount == Decimal("125.00")
            assert offer.cash_amount == Decimal("40.00")

            assert cash_ledger.credit_entry_sum(txn) == Decimal("0")
            assert cash_ledger.elsewhere_purchase_sum(txn) == Decimal("85.00")
            assert cash_ledger.posted_purchase_sum(txn) == Decimal("0")
            assert cash_ledger.off_statement_sum(txn) == Decimal("85.00")

    def test_a_flagged_line_on_another_account_is_counted_ONCE_around_the_doors(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The partition holds for a row NO door writes: flag AND another account.

        Both purchase doors refuse the combination
        (``entry_service._refusals._reject_flag_beside_another_account``), so
        this plants it on the model directly -- the state the doors exist to
        keep out, graded here so the sum's partition rests on the terms
        themselves and not on the doors: `$60` flagged on the card is the
        FLAG's term alone, and `off_statement_sum` reads `$60.00`, never
        `$120.00`.  Deleting ``not entry.is_credit`` from
        ``elsewhere_purchase_sum`` doubles it.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Target", is_credit=True, account_id=card.id,
            )
            db.session.commit()

            assert cash_ledger.credit_entry_sum(txn) == Decimal("60.00")
            assert cash_ledger.elsewhere_purchase_sum(txn) == Decimal("0")
            assert cash_ledger.posted_purchase_sum(txn) == Decimal("0")
            assert cash_ledger.off_statement_sum(txn) == Decimal("60.00")
            assert self._offered(seed_user)[txn.id].cash_amount == Decimal("40.00")

    def test_an_envelope_of_DEBITS_publishes_no_second_figure(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The negative control, and it is what stops the caption being noise.

        Nothing on the card means the booked figure IS what the statement
        shows, so there is no second number to print.  Without this the case
        above passes for a panel that captions every envelope.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            assert self._offered(seed_user)[txn.id].cash_amount is None

    def test_a_bill_and_a_transfer_publish_no_second_figure(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """A row that carries no entries has no card half to disagree with.

        Graded across BOTH source-row arms because the field is on the shared
        offer type: a bill can hold no entries, and a transfer shadow
        structurally cannot (production: 342 shadows, 0 entries).
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="180.00")
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    name="Savings",
                    account_type_id=seed_user["account"].account_type_id,
                    anchor_balance=Decimal("100.00"),
                ),
            )
            db.session.flush()
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()

            shadow = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id == transfer.id,
                    Transaction.account_id == seed_user["account"].id,
                )
                .one()
            )
            offered = self._offered(seed_user)
            assert offered[bill.id].cash_amount is None
            assert offered[(transfer.id, seed_user["account"].id)].cash_amount is None

    def test_the_cash_figure_matches_what_the_LEDGER_will_post(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The panel's second figure IS the posted one, not a lookalike.

        Both come from ``cash_ledger.credit_entry_sum``, and this grades that
        by settling the row and comparing against what its family's
        movements move through this account -- ``movement_cash_leg``, the one
        valuation the ledger writer and the cash walk post through (ruling
        **R-BAL80**): the debit purchase its own `$40.00`, the card purchase
        nothing, and the row itself nothing (a ``purchases`` close has no
        covering movement, ruling **R-BAL81**).  Two numbers that agree by
        construction rather than by coincidence is the whole reason the term
        was published instead of re-summed.  (Through plan step
        ``balance:X-bi-4a``'s first cut this compared against the row's own
        leg, ``settled_cash_leg``, which R-BAL81 deleted.)
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            _make_entry(
                txn, seed_user["user"], amount="60.00",
                description="Amazon", is_credit=True,
            )
            db.session.commit()

            offered_cash = self._offered(seed_user)[txn.id].cash_amount
            assert reconcile_service.record_reconciliation(
                reconcile_service.ReconcileSubmission(
                    statement=_reconciled(seed_user),
                    entry_ids=set(),
                    transaction_ids={txn.id},
                    corrections={},
                    transfer_ids=set(), transfer_corrections={},
                ),
            ) == 1
            db.session.commit()

            db.session.expire_all()
            settled = db.session.get(Transaction, txn.id)
            assert settled.covering_movements == []
            assert status_seam.covered_cash_leg(settled, settled.account_id) == Decimal("0.00")
            assert sum(
                (
                    cash_ledger.movement_cash_leg(settled, entry)
                    for entry in settled.entries
                ),
                Decimal("0.00"),
            ) == -offered_cash


class TestTheCorrectionCountIsWhatAHumanTyped:
    """Finding **N-231**: the count says how many figures a HUMAN supplied.

    ``transactions_reconciled`` describes its rows as "some carrying a
    corrected amount", and ruling **R-FB**'s production measurement ("11 of 93
    settled bills carry a hand-typed correction") is made of this same signal.
    It was read off the COLUMN -- rows whose ``actual_amount`` changed -- and
    an envelope's close ALWAYS writes that column, so every envelope tick
    incremented it.  It now asks the verb's own published predicate
    (``transaction_service.is_correction``) before the settle.

    Four shapes, and the first two are the ones the column reading got wrong.
    """

    _bill = staticmethod(TestTheTransactionArm._bill)
    _settle = staticmethod(TestTheTransactionArm._settle)

    @staticmethod
    def _corrected_count(caplog):
        """Return ``corrected_count`` off the one reconcile event emitted."""
        events = [
            record for record in caplog.records
            if getattr(record, "event", None) == EVT_TRANSACTIONS_RECONCILED
        ]
        assert len(events) == 1, "expected exactly one reconcile event"
        return events[0].corrected_count

    def test_an_envelope_close_with_no_correction_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """The defect, reproduced from the failing direction.

        An envelope carrying `$40.00` of purchases settles at `$40.00` and its
        ``actual_amount`` moves from NULL to `$40.00` -- a MACHINE write, in the
        same statement as the status.  Nobody typed anything, so the count is
        zero.  Measured before the fix on a probe of this exact shape: it
        logged ``corrected_count: 1``.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(seed_user, [txn.id]) == 1

            assert self._corrected_count(caplog) == 0
            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn.id),
            ) == Decimal("40.00")

    def test_a_forged_box_on_a_DERIVED_row_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """A figure the verb IGNORES was not a correction.

        The panel renders no box for an entries-derived row, so the only way to
        submit one is by hand -- and ``settle_transaction`` drops it.  A count
        that read the submission rather than the outcome would report a
        correction the ledger never made.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            _make_entry(txn, seed_user["user"], amount="40.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [txn.id], {txn.id: Decimal("999.99")},
                ) == 1

            assert self._corrected_count(caplog) == 0

    def test_a_bill_ticked_at_its_prefill_counts_ZERO(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """The echo: an untouched box is not a correction.

        The panel PREFILLS the box, so every correctable row on the form posts
        a figure whether the user touched it or not.  Counting submissions
        would measure how many boxes the panel drew.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="180.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [bill.id], {bill.id: Decimal("180.00")},
                ) == 1

            assert self._corrected_count(caplog) == 0

    def test_a_bill_ticked_at_a_DIFFERENT_figure_counts_ONE(
        self, app, db, seed_user, seed_periods, seed_entry_template, caplog,
    ):
        """The one shape that IS a correction, so the count is not inert.

        Production's Electricity, to the cent: estimated `$300.00`, statement
        `$245.32`.  Without this case the three zeros above are satisfied by a
        count that is always zero.
        """
        with app.app_context():
            bill = self._bill(seed_user, seed_periods[0], amount="300.00")
            db.session.commit()

            with caplog.at_level("INFO"):
                assert self._settle(
                    seed_user, [bill.id], {bill.id: Decimal("245.32")},
                ) == 1

            assert self._corrected_count(caplog) == 1


class TestTheSectionsAndTheOrder:
    """Ruling **R-FC**'s three presentational rules, graded.

    **They shipped with ZERO tests and an adversarial review said so.**  Nothing
    referenced ``OfferKind``, ``section_label``, ``kind``, ``_block_order`` or
    ``rank`` -- the entire content of the ruling, and the whole reason the
    assembler took ownership of the block order, was ungraded.  That is the
    balance arc's live lesson of the time (a producer's new fields are the fields
    nobody was asserting) repeating one leaf later.

    The rules: the blocks arrive ordered by kind then by each block's OLDEST
    offer; a section label is emitted where the kind CHANGES and nowhere else;
    and a childless block is one the template prints inline.
    """

    _bill = staticmethod(TestTheTransactionArm._bill)

    @staticmethod
    def _resolved(seed_user):
        return reconcile_service.outstanding_set(_reconciled(seed_user))

    def test_each_arm_TAGS_its_kind_and_income_is_a_DEPOSIT(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """An income row is a DEPOSIT, not a bill.

        **The defect this grades was live.**  The kind was DERIVED from the
        block's shape -- purchases meant envelope, correctable meant bill -- and
        an income row is never purchase-tracked, so production's `$1,958.87`
        FSA reimbursement rendered under a heading reading "Bills" three lines
        below a summary counting it as a deposit.  A figure and its caption
        disagreeing, on the one screen read beside a paper statement.

        Shown to FIRE: classifying by ``is_correctable`` puts the deposit in
        ``Bills``.
        """
        with app.app_context():
            envelope = seed_entry_template["transaction"]
            bill = self._bill(seed_user, seed_periods[0], name="Electricity")
            deposit = self._bill(
                seed_user, seed_periods[0], name="FSA Reimbursement",
                amount="1958.87", income=True,
            )
            db.session.commit()

            by_id = {
                group.key: group.kind
                for group in self._resolved(seed_user).groups
            }
            assert by_id[envelope.id] is reconcile_service.OfferKind.ENVELOPE
            assert by_id[bill.id] is reconcile_service.OfferKind.BILL
            assert by_id[deposit.id] is reconcile_service.OfferKind.DEPOSIT

    def test_the_blocks_are_ordered_by_KIND_then_by_their_oldest_offer(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """Like sits with like, and within a section the oldest is first.

        The bills are created NEWEST-first and given due dates that reverse
        that, so a result matching insertion order would be indistinguishable
        from one matching the rule if they agreed.
        """
        with app.app_context():
            late_bill = self._bill(
                seed_user, seed_periods[0], name="Water", fires_on_day=9,
            )
            early_bill = self._bill(
                seed_user, seed_periods[0], name="Electricity",
                fires_on_day=3,
            )
            deposit = self._bill(
                seed_user, seed_periods[0], name="Refund",
                amount="20.00", income=True,
            )
            envelope = seed_entry_template["transaction"]
            db.session.commit()

            order = [
                group.key
                for group in self._resolved(seed_user).groups
            ]
            assert order == [
                envelope.id, early_bill.id, late_bill.id, deposit.id,
            ]

    def test_a_section_label_is_emitted_ONLY_where_the_kind_changes(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """One heading per run, and it names the kind that follows it.

        Two bills between an envelope and a deposit: the second bill continues
        its section and carries no label.  A per-block label would print
        "Bills" twice; a missing one would leave the deposit under the bills'
        heading, which is the mis-captioning this ruling's sections exist to
        prevent.
        """
        with app.app_context():
            self._bill(seed_user, seed_periods[0], name="Electricity",
                       fires_on_day=3)
            self._bill(seed_user, seed_periods[0], name="Water",
                       fires_on_day=9)
            self._bill(seed_user, seed_periods[0], name="Refund",
                       amount="20.00", income=True)
            db.session.commit()

            labels = [
                group.section.label if group.section else None
                for group in self._resolved(seed_user).groups
            ]
            assert labels == ["Envelopes", "Bills", None, "Deposits"]

    def test_a_block_with_purchases_is_NOT_childless_and_one_without_is(
        self, app, db, seed_user, seed_periods, seed_entry_template,
    ):
        """The childless rule's own input, which the template branches on.

        R-FC's first presentational rule is the template's -- a block with no
        children prints inline -- so what the producer owes it is an honest
        ``purchases``.  Graded here rather than by scraping markup: the
        rendering is one ``{% if %}`` over this tuple.
        """
        with app.app_context():
            envelope = seed_entry_template["transaction"]
            _make_entry(envelope, seed_user["user"], amount="40.00")
            bill = self._bill(seed_user, seed_periods[0], name="Electricity")
            db.session.commit()

            by_id = {
                group.key: group
                for group in self._resolved(seed_user).groups
            }
            assert len(by_id[envelope.id].purchases) == 1
            assert by_id[bill.id].purchases == ()

    def test_every_kind_has_a_rank_and_a_label(self):
        """The section vocabulary is TOTAL over its own members.

        ``rank`` resolves through a map built from the class, so a member added
        without one is impossible rather than silently sorted to the wrong end
        -- which is the failure mode a hand-written rank map has, and the reason
        plan step X-f2-c3 can add ``TRANSFER`` by writing one line.
        """
        kinds = list(reconcile_service.OfferKind)
        assert sorted(kind.rank for kind in kinds) == list(range(len(kinds)))
        assert all(kind.section_label for kind in kinds)
        # And exactly these two carry a NOTE: Transfers, because a tick there
        # settles a second account too, and Paid from this account (ruling
        # R-CC111, plan step credit_card:CC-5-4b), because its rows were
        # recorded here and reopened and a tick records one here on the
        # statement's day.  Asserted as a set rather than as a count so a note
        # appearing on the wrong section fails here rather than on the screen:
        # each sentence is about what a TICK does, and printing "settles both
        # sides" over the Bills section would be a false promise about
        # somebody's money.
        assert {
            kind for kind in kinds if kind.section_note
        } == {
            reconcile_service.OfferKind.TRANSFER,
            reconcile_service.OfferKind.SETTLEMENT,
        }


class TestThePanelHoldsONEAmountBasis:
    """Plan step **X-au-j**, finding **N-295**: the derivations are the PANEL's.

    An :class:`~app.services.cash_ledger.AmountBasis` holds the owner's live
    derivations -- the paycheck engine run over the whole pay-period set, and
    each loan's P&I, payment day and escrow history.  ``amount_basis``'s own
    docstring says calling those per row is finding **N-228**, and N-295
    recorded the reconcile panel doing exactly that: both source-row arms
    priced every offered row through their own ``settle_amount``, and each of
    those built its own.

    It is ``test_statement_match``'s ``TestThePassHoldsONEAmountBasis`` one
    package over, and it fails the same way: a later change that let a producer
    build its own would restore the cost in SILENCE, because every figure would
    still be right.  Only the wall clock moves, which is why the count is
    asserted and the timing is not.
    """

    def test_one_basis_serves_every_row_the_panel_offers(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The firing control: ONE construction, however many rows are offered.

        **The rows are PROJECTED deliberately, and that is what makes this
        control sharp.**  A settled row is valued from its own record
        (``row_valuation.fixed_contribution``) and never reaches the resolver,
        so a panel of settled rows builds one basis whether or not this step
        shipped -- the assertion would hold over a broken tree.
        """
        with app.app_context():
            # Six offerable bills, plus the seed fixture's own envelope close.
            for index in range(6):
                TestTheTransactionArm._bill(
                    seed_user, seed_periods[0],
                    name=f"Bill {index}", amount=f"{100 + index}.00",
                )
            db.session.commit()

            built = count_amount_bases(monkeypatch)

            offers = {
                group.settle.key: group.settle
                for group in reconcile_service.outstanding_set(_reconciled(seed_user)).groups
                if group.settle is not None
            }

            assert len(offers) >= 6, (
                "the panel must offer several rows -- otherwise one basis and "
                "one per row are the same number and this grades nothing"
            )
            assert len(built) == 1, (
                f"the panel built {len(built)} amount bases for "
                f"{len(offers)} offered rows; X-au-j makes it one"
            )
            # And it is the OWNER's own, not some other budget's: a basis
            # prices from a scenario's salary profiles and a scenario's loans,
            # so a foreign one answers a different figure with nothing to say
            # so (``resolve_transaction_amount`` refuses it).
            assert built == [
                (seed_user["user"].id, seed_user["scenario"].id),
            ]
