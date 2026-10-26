"""The outstanding set grouped by its parent, and the transaction arm of a reconcile."""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import account_service, reconcile_service, status_seam
from app.services.pay_calendar import calendar_for
from tests._test_helpers import generate_row_of, last_covered_day, make_expense_template

from ._helpers import (
    _make_entry,
    _OBSERVED_ON,
    _statement,
    _reconciled,
    _BEFORE_THE_STATEMENT,
    _AFTER_THE_STATEMENT,
    _outstanding_debit,
    _transaction_arm_bill,
    _transaction_arm_offered,
    _transaction_arm_settle,
)


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

    _bill = staticmethod(_transaction_arm_bill)
    _offered = staticmethod(_transaction_arm_offered)
    _settle = staticmethod(_transaction_arm_settle)

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
