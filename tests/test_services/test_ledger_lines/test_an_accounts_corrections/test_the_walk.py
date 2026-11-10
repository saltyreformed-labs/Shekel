"""An account's corrections off its walk: the civil-day partition, and its reset at each assertion.

Each case frames the account's records the way the worked-out read does
(``account_corrections`` over the account's ``walk_cash_ledger``); the door that loaded the walk
itself, ``walk_account_ledger``, went at plan task plan#3155 with its loan refusal and its
missing-account answer, and their two cases with it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.models.transaction_entry import TransactionEntry
from app.services import status_seam
from app.services.cash_ledger import walk_cash_ledger
# The account walk's framing is package-private in the worked-out ledger, the
# read's own composition over each account's walk (plan task plan#3155 deleted
# its account door); these cases reach it by name, as ``test_the_target_rules.py``
# reaches the account half's rules, rather than asking for a re-export that would
# exist only for a test.
from app.services.ledger_lines._account_walk import account_corrections
from app.utils.dates import display_today, to_display_date
from tests._test_helpers import (
    create_envelope_txn,
    create_settled_transfer,
    figure_source_columns,
    settle_day_columns,
    settle_instant_on,
    transfer_side_record,
)

from ._helpers import (
    _ONE_HOUR,
    _PINNED_OPENING_AT,
    _PINNED_OPENING_DAY,
    _add_assertion,
    _make_account,
    _origin_day,
    _settle_expense,
)


class TestTheAccountWalk:
    """The pure walk partitions sources by CIVIL DAY and resets at assertions.

    The day is the user's (``America/New_York``) and an assertion is the
    closing balance for it -- EVERY assertion, opening and true-up alike --
    ruling R-DH, ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``.
    It was an INSTANT partition until 2026-07-31, which decided the question by
    which button the user pressed first and cost production $4,001.42; the
    OPENING then carried an exception for one day, until finding N-133 / F1
    scored it against four months of that same account and found it the
    second-worst correction in the history.
    """

    def test_opening_only_walk(self, app, db, seed_user):
        """A fresh account walks to its OPENING EQUITY plus an agreeing assertion.

        Savings anchored $500.00 with no settled activity.  TWO corrections
        since plan step X-f3c-2a, and the pair is the point:

        * the account's OPENING EQUITY -- ``budget.account_openings``, written
          by ``create_account`` from the balance the owner typed -- booking
          ``500.00 - 0.00`` onto the linked ledger against ``anchor_equity``;
        * the owner's ASSERTION, whose ``ledger_before`` is already 500.00, so
          it corrects NOTHING and books no line.

        What the books open with is unchanged by the step: $500.00.  What
        changed is where that figure comes FROM -- a stored fact rather than
        the earliest assertion's delta, so a BACK-DATED assertion can no longer
        re-elect it.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            corrections = account_corrections(
                walk_cash_ledger(account.id, seed_user["scenario"].id),
            )
            assert len(corrections) == 2
            assert corrections[0].opens_the_books is True
            assert corrections[0].target_balance == Decimal("500.00")
            assert corrections[0].ledger_before == Decimal("0.00")
            # The assertion agrees with the books, so it books nothing.
            assert corrections[1].opens_the_books is False
            assert corrections[1].target_balance == Decimal("500.00")
            assert corrections[1].ledger_before == Decimal("500.00")

    def test_a_settle_on_an_earlier_day_is_inside_the_opening(
        self, app, db, seed_user,
    ):
        """A settle dated an EARLIER DAY than the opening is in ledger_before.

        Savings anchored $500.00; a $200.00 expense settled the day BEFORE the
        origination assertion: the source is absorbed, so the ASSERTION's
        ledger_before is 300.00 and its delta is ``500 - 300 = +200.00`` -- the
        anchor already reflected that spend.

        *It read ``ledger_before == -200.00`` and a ``+700.00`` delta until plan
        step X-f3c-2a, because the walk seeded at ZERO and the opening's delta
        had to carry the whole of the account's opening equity on top of the
        correction.  The two are separated now: the books open at $500.00 and
        the assertion corrects the $200.00 the records moved.*

        **The boundary is a DAY, inclusive, for every assertion kind** (ruling
        R-DH (a)).  This case is the strictly-earlier one, which no variant of
        the rule has ever disputed; the opening's OWN day is the case
        :meth:`test_a_settle_on_the_openings_own_day_is_absorbed`
        pins.  The fixture moved from "one hour before" to "one day before"
        because an hour is not a unit this partition has any more.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            _settle_expense(
                seed_user, account, "200.00", origin - timedelta(days=1),
            )
            _db.session.commit()

            corrections = account_corrections(
                walk_cash_ledger(account.id, seed_user["scenario"].id),
            )
            assert len(corrections) == 2
            assert corrections[0].ledger_before == Decimal("0.00")
            assert corrections[0].target_balance == Decimal("500.00")
            assert corrections[1].ledger_before == Decimal("300.00")

    def test_a_settle_on_the_openings_own_day_is_absorbed(
        self, app, db, seed_user,
    ):
        """A settle dated the opening's OWN day is ABSORBED.

        Savings anchored $500.00 with its opening pinned to 12:00 EDT; a $200.00
        expense settled an hour after it, and again an hour before it -- both the
        SAME civil day.  The ASSERTION's ``ledger_before`` is 300.00 both times:
        the settle is inside the asserted balance, so the assertion's delta is
        ``500 - 300 = +200.00`` and the walk lands on $500.00, the balance the
        user typed.  (It read -200.00 and a +700.00 delta until plan step
        X-f3c-2a seeded the walk at the account's stored opening equity instead
        of at zero.)

        **This is ruling R-DH (a) with no exception for the opening** (finding
        N-133 / F1, ruled 2026-07-31).  An assertion is the CLOSING balance for
        its civil day, opening and true-up alike.  The opening carried an
        exception for one day -- it rode on top of its own day's sources -- and
        the exception was reverted once scored against production: on the
        developer's real Checking, four settles share the opening's civil day
        and stacking them on top made the walk read $4,804.00 for a day the bank
        showed $2,746.58, then book a -$1,986.16 correction at the next
        assertion where absorbing them books +$71.26.

        **It was parametrised over "an hour after" and "an hour before" until
        plan step X-f1, and the pair no longer describes two inputs.**  Its own
        docstring already recorded that the two fed byte-identical values --
        ``_source_net_days`` aggregated a day's sources to one ``(day, net)``
        pair before the partition ran (the walk reads the records since plan task
        plan#2525) -- and once the settle stores a DAY
        rather than an instant the two arms are literally the same fixture.
        Collapsed rather than left implying a discrimination the code cannot
        make; the regression insurance it was kept for is now carried by the
        column's type, since re-introducing an instant partition would need a
        column that can hold one.

        **This test could not fail before 2026-07-31.**  It passed
        ``origin + timedelta(days=1)`` while claiming "an hour after -- the same
        civil day", so it graded a settle on a LATER day, which rides on top
        under either rule.  The day offset was not careless -- the origination's
        instant is the ambient wall clock, so an hour offset really can cross
        midnight -- which is why the fix is a PINNED opening rather than a
        smaller offset.
        """
        with app.app_context():
            # Opened ON the pinned day, so the origination assertion IS the
            # one this case is about -- appending a second would give the walk
            # a correction the case did not write (plan step X-f3c-2c).
            account = _make_account(
                seed_user, "500.00", observed_on=_PINNED_OPENING_DAY,
            )
            pinned = _PINNED_OPENING_AT
            settle = _settle_expense(
                seed_user, account, "200.00", _PINNED_OPENING_DAY,
            )
            _db.session.commit()

            # The precondition the whole case rests on: ONE civil day for both
            # events.  Without it this grades a different partition entirely,
            # which is the defect being repaired here.
            assert to_display_date(pinned) == _PINNED_OPENING_DAY
            assert settle.settled_on == _PINNED_OPENING_DAY

            corrections = account_corrections(
                walk_cash_ledger(account.id, seed_user["scenario"].id),
            )
            assert len(corrections) == 2
            assert corrections[1].ledger_before == Decimal("300.00")

    def test_a_settle_dated_before_the_origination_is_absorbed(
        self, app, db, seed_user,
    ):
        """A settle dated before the origination assertion is inside it.

        The $200.00 expense carries the 2024 bootstrap period's start day, which
        precedes the origination assertion (test-run time, 2026+), so it is
        absorbed: the assertion's ledger_before is 300.00 -- the account's
        $500.00 opening equity less the $200.00 the records moved (it read
        -200.00 against a zero seed until plan step X-f3c-2a).

        **It reached that day through a FALLBACK until plan step X-f1** -- the
        row carried no ``paid_at`` and every reader substituted its pay period's
        ``start_date`` -- and this test was named for the fallback.  The day is
        a stored fact now and the substitution is gone, so the fixture states
        the day it always meant.  The figure is unchanged, because the day is.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            _settle_expense(
                seed_user, account, "200.00",
                seed_user["bootstrap_period"].start_date,
            )
            _db.session.commit()

            corrections = account_corrections(
                walk_cash_ledger(account.id, seed_user["scenario"].id),
            )
            assert corrections[1].ledger_before == Decimal("300.00")

    def test_a_movement_with_no_day_moves_no_correction(
        self, app, seed_user,
    ):
        """A dayless movement is worth nothing to the walk.

        Plan task **plan#2525**: the walk reads the RECORDS, where it read the
        account's STORED postings.  This is the state that told the two apart
        -- a covering movement (plan step **X-bi-3a**) whose day is nulled
        after it settled, money never seen to move.  The
        postings walk REFUSED it by name (``_purchase_source_days``, deleted
        with the task); the records value a dayless movement at nothing -- it
        is in flight, ruling **R-BAL77** -- so no correction is built on it.
        It was named ``..._whatever_is_posted`` and checked the stored leg
        was still there until plan step plan#2914 deleted the store.

        Hand-computed, Savings opened at $500.00 with a $200.00 expense settled
        on the 2024 bootstrap period's first day, before the origination
        assertion:

            dated     assertion ledger_before = 500.00 - 200.00 = 300.00
            dayless   assertion ledger_before = 500.00          (in flight)
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            scenario_id = seed_user["scenario"].id
            txn = _settle_expense(
                seed_user, account, "200.00",
                seed_user["bootstrap_period"].start_date,
            )
            _db.session.commit()
            movement, = txn.covering_movements
            dated = account_corrections(
                walk_cash_ledger(account.id, scenario_id),
            )
            assert dated[1].ledger_before == Decimal("300.00"), (
                "CONTROL: dated, the movement is inside the assertion"
            )

            _db.session.query(TransactionEntry).filter(
                TransactionEntry.id == movement.id,
            ).update(
                {"settled_on": None, "settled_day_basis_id": None},
                synchronize_session=False,
            )
            _db.session.commit()

            dayless = account_corrections(
                walk_cash_ledger(account.id, scenario_id),
            )
            assert dayless[1].ledger_before == Decimal("500.00")

    def test_a_transfers_clearing_link_is_read_off_THIS_accounts_leg(
        self, app, db, seed_user,
    ):
        """Walking one account reads its OWN side's link, not the other's.

        **The defect this closes was not in ruling R-FL's own amendment**; it
        was found tracing the loader at plan step X-f3a-1.  The walk's transfer
        loader (``_transfer_source_days``, deleted at ``balance:X-bi-6-3``
        when a transfer's legs became its two covering movements' own entries,
        read by the movement loader off the movement on THIS account) resolved
        the transfer's INCOME shadow whatever account was being walked, which
        was harmless for the DAY -- the transfer service mirrored
        ``settled_on`` onto both shadows -- and wrong for the LINK, because
        clearing is deliberately NOT mirrored: a transfer leaves one bank and arrives at
        another, and each statement reports its own leg.  The property is
        structural now (a movement sits on one account and carries its own
        link) and this test pins it as such.  **The link is planted on the
        Savings side's RECORD since plan step ``balance:X-bi-6-4d-2``**: the
        record hangs off the transfer, and the twins that step left kept no
        day, so a link on the Savings twin -- where this planted it, mirrored
        onto the movement by the seam until then -- was refused by the twin's
        ``ck_transactions_cleared_needs_settle_day`` (ruling R-BAL167 class 2).
        No transaction names a transfer since plan step
        ``balance:X-bi-6-5``.

        The fixture makes the two answers differ.  A $200.00 Checking ->
        Savings transfer settles the day AFTER Savings' origination.  The
        SAVINGS (income) leg then names a LATER Savings assertion -- exactly
        what ticking it on the next Savings statement writes.  Walking CHECKING
        must be unmoved by that: the Checking leg names nothing, so Checking's
        own date rule decides and the transfer is inside the Checking assertion
        that closes after it.

        Hand-computed on Checking, whose own opening asserts $1,000.00 long
        before the transfer:

            opening: ledger_before      0.00, resets the walk to 1000.00
            true-up: ledger_before   1000.00 - 200.00 = 800.00

        Read off the income leg instead, Checking's walk would file its own
        outgoing money under an assertion belonging to another ACCOUNT -- an id
        Checking's ``StatementCoverage`` does not contain -- so the transfer
        would be cleared by no assertion of Checking's at all, ride on top of
        every one of them, and the true-up's ``ledger_before`` would read the
        bare ``$1,000.00`` reset.
        """
        with app.app_context():
            savings = _make_account(seed_user, "500.00")
            checking = seed_user["account"]
            origin = _origin_day(savings)
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("200.00"),
                settled_on=origin + timedelta(days=1),
            )
            # A Checking assertion closing AFTER the transfer, so Checking's own
            # date rule has something to put it inside of.
            _add_assertion(
                checking, "1000.00", _PINNED_OPENING_AT + timedelta(days=40),
            )
            income_record = transfer_side_record(
                _db.session, transfer.id, savings.id,
            )
            later_savings = _add_assertion(
                savings, "900.00", _PINNED_OPENING_AT + timedelta(days=50),
            )
            income_record.reconciled_by_id = later_savings.id
            _db.session.commit()

            checking_corrections = (
                account_corrections(
                    walk_cash_ledger(checking.id, seed_user["scenario"].id),
                )
            )
            assert checking_corrections[-1].ledger_before == Decimal("800.00")

    def test_a_purchases_clearing_link_is_read_off_the_PURCHASE(
        self, app, db, seed_user,
    ):
        """The account walk reads a purchase's own link, not its parent's.

        Plan step X-f3b (ruling **R-FM**) gave the posted walk a FOURTH source
        loader, the only one whose rows carried a link the parent cannot
        supply: a still-PROJECTED envelope has no ``reconciled_by_id`` of its
        own, so a loader reading the parent would have answered ``None`` for
        every purchase and the record would have decided nothing here while
        deciding on the read side.  The walk reads the records since plan task
        plan#2525, each purchase as a movement of its own, and this pins that
        it still reads the purchase's link.

        The shape that discriminates it is the one the reconcile panel writes --
        TWO assertions sharing a civil day, where the link picks between them
        (``StatementCoverage._recorded_anchor_id``).  A ``$40.00`` purchase the
        bank took before both; the account's opening asserts ``$1,000.00`` and
        the two same-day assertions declare ``$1,000.00`` then ``$2,000.00``.

        Hand-computed, with the walk resetting to each asserted balance:

            unlinked   first  ledger_before = 1000.00 - 40.00 = 960.00
                       second ledger_before = 1000.00          (nothing left)
            linked to  first  ledger_before = 1000.00          (nothing cleared)
            the SECOND second ledger_before = 1000.00 - 40.00 =  960.00

        Read off the still-Projected PARENT instead, the link would be ``None``
        and the pair would stay on the unlinked answer.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            period = seed_user["bootstrap_period"]
            posted_on = _origin_day(checking) + timedelta(days=1)
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Groceries", Decimal("500.00"),
            )
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("40.00"),
                description="ticked on the second reading",
                purchased_on=posted_on,
                **settle_day_columns(posted_on),
                is_credit=False,
            )
            _db.session.add(entry)
            _db.session.flush()
            day_at = _PINNED_OPENING_AT + timedelta(days=40)
            _add_assertion(checking, "1000.00", day_at)
            governing = _add_assertion(
                checking, "2000.00", day_at + timedelta(hours=8),
            )
            _db.session.commit()

            control = account_corrections(
                walk_cash_ledger(checking.id, scenario_id),
            )
            assert [c.ledger_before for c in control[-2:]] == [
                Decimal("960.00"), Decimal("1000.00"),
            ], (
                "CONTROL: unlinked, the date rule puts the purchase in the "
                "FIRST assertion of that day."
            )

            entry.reconciled_by_id = governing.id
            _db.session.commit()

            corrections = account_corrections(
                walk_cash_ledger(checking.id, scenario_id),
            )
            assert [c.ledger_before for c in corrections[-2:]] == [
                Decimal("1000.00"), Decimal("960.00"),
            ], (
                "the purchase's OWN link moved it to the governing assertion; "
                "its still-Projected parent has no link to read instead"
            )

    def test_transfer_attribution_uses_the_walked_sides_settle_day(
        self, app, db, seed_user,
    ):
        """A transfer is attributed by its walked side's settled civil day.

        Two Checking -> Savings transfers into a $500.00-anchored Savings:
        $50.00 settled the day BEFORE the origination assertion (absorbed) and
        $150.00 the day AFTER (rides on top).  The opening's ledger_before is
        therefore exactly +50.00.

        The offsets are DAYS since ruling R-DH (2026-07-31): the partition
        reads a civil day, so an hour before the assertion is the SAME day and
        would no longer discriminate the two sides this test is about.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], account,
                seed_user["bootstrap_period"], amount=Decimal("50.00"),
                settled_on=origin - timedelta(days=1),
            )
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], account,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
                settled_on=origin + timedelta(days=1), name="Post-anchor",
            )
            _db.session.commit()

            corrections = account_corrections(
                walk_cash_ledger(account.id, seed_user["scenario"].id),
            )
            assert len(corrections) == 2
            # [0] is the account's OPENING EQUITY; the assertion follows it and
            # absorbs the pre-anchor transfer leg (plan step X-f3c-2a).
            assert corrections[0].opens_the_books is True
            assert corrections[0].ledger_before == Decimal("0.00")
            assert corrections[1].ledger_before == Decimal("550.00")

    def test_trueup_day_partition(self, app, db, seed_user):
        """The CRITICAL-1 case: a true-up absorbs only settles up to its own day.

        Savings anchored $500.00 (origination, day T).  A $200.00 expense on
        T+1, a true-up asserting $350.00 on T+2, a $100.00 expense on T+3:

          opening: ledger_before 0.00              (delta +500.00)
          true-up: ledger_before 500 - 200 = 300.00 (delta +50.00 -- the
                   engine's answer was 300, the user asserted 350)

        The T+3 settle is dated after the true-up's day and perturbs nothing.
        A period-granular walk would have absorbed BOTH settles (all four
        events share one 14-day pay period) and mis-stated the true-up, which
        is what this test exists to catch.

        **The fixture spans DAYS where it used to span hours** (ruling R-DH,
        2026-07-31).  The partition is day-granular now, so hour offsets put
        every event on one day and the case would no longer discriminate
        day-granular from period-granular at all -- it would pass under both.
        The expected figures are unchanged; only the units the fixture
        separates its events by had to move to the units the rule reads.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            _add_assertion(
                account, "350.00", settle_instant_on(origin + timedelta(days=2)),
            )
            _settle_expense(
                seed_user, account, "100.00", origin + timedelta(days=3),
            )
            _db.session.commit()

            corrections = account_corrections(
                walk_cash_ledger(account.id, seed_user["scenario"].id),
            )
            assert len(corrections) == 3
            assert corrections[0].opens_the_books is True
            assert corrections[0].ledger_before == Decimal("0.00")
            assert corrections[0].target_balance == Decimal("500.00")
            # The ORIGINATION assertion: the books already read 500.00, so it
            # corrects nothing.
            assert corrections[1].opens_the_books is False
            assert corrections[1].ledger_before == Decimal("500.00")
            # The TRUE-UP: 500.00 opening less the 200.00 settle on its own
            # side of the day boundary, asserted up to 350.00.
            assert corrections[2].opens_the_books is False
            assert corrections[2].ledger_before == Decimal("300.00")

    def test_two_assertions_on_one_day_both_absorb_it_in_recording_order(
        self, app, db, seed_user,
    ):
        """One civil day, both assertion kinds, ONE rule -- and the tie-break.

        Savings anchored $500.00 with its opening pinned to 12:00 EDT; a $75.00
        expense two hours later; a TRUE-UP asserting $425.00 an hour after that.
        All three share one civil day.  Both assertions close that day, so the
        day's sources walk FIRST and each assertion resets over what it finds:

          opening: ledger_before -75.00   (the $75 is inside the $500 typed)
          true-up: ledger_before 500.00   (the opening's reset, nothing after)

        **This is the discriminating control for ruling R-DH (a), and each
        figure is broken by a different mutant.**  Give the OPENING back its
        one-day-old exception and the first reads 0.00; drop the reset that
        makes each assertion supersede the walked total and the second reads
        -75.00.  (Making the TRUE-UP alone ride on top does NOT move the second
        figure -- the opening has already consumed the day's source through the
        monotonic pointer -- so that arm is covered by the sibling above, not
        here.)  It also pins
        the same-day tie-break the rule depends on: two assertions about one day
        apply in RECORDING order and the last is that day's closing balance,
        which is why the true-up sees the opening's $500.00 rather than the
        other way round.

        It was named ``test_same_instant_settle_is_absorbed`` -- the vocabulary
        of the INSTANT partition ruling R-DH deleted on 2026-07-31 -- and then
        ``test_a_trueup_absorbs_a_settle_the_opening_rode_on_top_of``, for the
        amendment reverted at finding N-133 / F1.  Both names described a rule
        the code no longer has.
        """
        with app.app_context():
            # **The shared civil day is TODAY's, and the account is OPENED on
            # it** (plan step X-f3c-2c).  Two things forced that.
            #
            # The account must be opened on the day rather than re-asserted
            # onto it: an assertion is append-only, so appending would leave
            # the ORIGINATION standing on ``create_account_of_type``'s
            # day-before-today -- a fourth correction nobody wrote, of
            # ``+$75.00``, past every event this case names, while the three
            # preconditions below (which do not look at the origination) went
            # on passing.  Found by the adversarial review of this step.
            #
            # And the day must be the FROZEN today, because RECORDING order is
            # what this case grades.  ``create_account`` stamps the
            # origination's ``created_at`` from the clock, which no fixture
            # moves per account; on any earlier day the true-up would be
            # recorded first and the pair would apply in the wrong order.
            day = display_today()
            account = _make_account(seed_user, "500.00", observed_on=day)
            settle = _settle_expense(seed_user, account, "75.00", day)
            trueup_at = settle_instant_on(day) + 3 * _ONE_HOUR
            _add_assertion(account, "425.00", trueup_at)
            _db.session.commit()

            # ONE civil day for all three events -- the precondition that makes
            # the two figures below a statement about the RULE rather than
            # about their dates.  The ORIGINATION is included now: leaving it
            # out is what let the premise go false unnoticed.
            assert _origin_day(account) == day
            assert settle.settled_on == day
            assert to_display_date(trueup_at) == day

            corrections = account_corrections(
                walk_cash_ledger(account.id, seed_user["scenario"].id),
            )
            # THREE, not "at least three": a fourth would be an event no line
            # above wrote, and every index below would still read as it does.
            assert len(corrections) == 3
            assert corrections[0].opens_the_books is True
            assert corrections[0].ledger_before == Decimal("0.00")
            # The two assertions, in recording order.  The first absorbs the
            # day's settle on top of the 500.00 the books opened with (it read
            # -75.00 against a zero seed until plan step X-f3c-2a); the second
            # sees the first's reset.
            assert corrections[1].opens_the_books is False
            assert corrections[1].ledger_before == Decimal("425.00")
            assert corrections[2].opens_the_books is False
            assert corrections[2].ledger_before == Decimal("500.00")

    def test_reverted_source_drops_out(self, app, db, seed_user):
        """A reverted settle leaves the walk.

        The $200.00 expense (paid T+1d) is reverted through the status door; a
        true-up at T+2d asserting $480.00
        (distinct from the $500.00 origination -- asserting the balance that
        already governs writes nothing, ruling R-EQ) then
        sees ledger_before 500.00 -- the reverted source contributes
        nothing, whatever its settle day said.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            txn = _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            # The revert goes through the ONE status door (plan step X-bi-3a):
            # the settle wrote a covering movement that carries the money, and
            # only the seam's revert releases it.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _add_assertion(account, "480.00", settle_instant_on(origin + timedelta(days=2)))
            _db.session.commit()

            corrections = account_corrections(
                walk_cash_ledger(account.id, seed_user["scenario"].id),
            )
            assert corrections[1].ledger_before == Decimal("500.00")
