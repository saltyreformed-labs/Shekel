"""What a statement's reconcile panel offers as outstanding: the set itself, a refund that nets in
the tally, and a scope that is the calendar's rather than the tables'.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import account_service, reconcile_service, status_seam, transaction_service
from app.services.pay_calendar import PayCalendar, calendar_for
from tests._test_helpers import (
    an_entered_day,
    create_account_of_type,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    settle_day_columns,
    settlement_if_settling,
)
from app.services.settle_day import record_settle_day

from ._helpers import (
    _make_entry,
    _OBSERVED_ON,
    _statement,
    _reconciled,
    _BEFORE_THE_STATEMENT,
    _AFTER_THE_STATEMENT,
    _outstanding_debit,
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
    and the route's day-resolution in
    ``test_routes/test_accounts/test_reconcile_panel.py``.
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
