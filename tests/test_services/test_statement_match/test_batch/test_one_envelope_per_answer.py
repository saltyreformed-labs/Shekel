"""One envelope per answer per period (finding N-327, plan step X-f6a-4): what one press mints, the
day a converged envelope closes on, and the money converging moves.
"""

from datetime import timedelta
from decimal import Decimal

from app.extensions import db as _db
from app.models.transaction import Transaction
from app.services import balance_at, statement_match, status_seam, transaction_service
from app.services.statement_match import NewEnvelope
from app.services.statement_match import _create

from tests._ledger_reads import linked_key_of, nets_by_key_and_day
from tests._test_helpers import an_entered_day

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_purchase,
    a_scope,
    a_transaction,
    an_answers,
    an_import,
)

from ._helpers import _batch, _creation


def _balance(seed_user, day):
    """Return the checking account's balance as of *day*.

    Args:
        seed_user: The seeded user bundle.
        day: The day to value the account on.

    Returns:
        The balance.
    """
    return balance_at.balance_at(
        seed_user["account"],
        # Built with the day it is asked for, exactly as ``test_create``'s own
        # money cases do.  ``BalanceContext.build`` resolves an ``as_of`` from
        # the CLOCK, so two reads a few statements apart share one -- which
        # answers the same figure whatever the postings did.
        balance_at.BalanceContext(
            user_id=seed_user["user"].id,
            scenario=seed_user["scenario"],
            as_of=day,
        ),
        day,
    )


class TestOnePressMintsOneEnvelopePerAnswerPerPeriod:
    """Finding **N-327**, developer ruling 2026-08-20 (plan step X-f6a-4).

    A ``new envelope called X`` answer minted one PER LINE, so a sweep
    fragmented the budget line the policy names.  Measured on the developer's
    own statement: a ``Lowe's`` answer places 4 lines over 3 pay periods, so
    ONE press made 4 envelopes -- two of them in the SAME period -- and the
    next statement made more beside them.

    **The convergence is scoped to one REQUEST**, which is what
    :class:`~app.services.statement_match.MintedEnvelopes` is: the
    cross-statement half is answered by the SUGGESTION, which degrades to a
    ``RECORD_IN`` against an envelope of that name already in the period and is
    printed where the owner can override it.

    **It moves NO money**, which the batch-vs-one-at-a-time control
    (``test_a_batch_books_what_its_acts_book.py``'s
    ``TestABatchBooksWhatTheSameActsBookOneAtATime``) now measures: each
    purchase carries its own posting day and is its own cash movement, so an
    envelope's own leg is `0.00` whether one envelope holds four purchases or
    four hold one each.
    """

    def test_two_lines_in_ONE_period_share_one_envelope(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: this is the same-press duplication itself."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        first = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=day,
            description="LOWES ONE",
        )
        second = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=day,
            description="LOWES TWO", sequence_in_group=0,
        )
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, first, new_envelope=answer),
            _creation(seed_user, second, new_envelope=answer),
        ])
        db.session.flush()

        assert outcome.applied_count == 2
        assert outcome.recorded_count == 2
        assert outcome.envelopes_created == 1
        envelopes = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).all()
        assert len(envelopes) == 1
        assert sorted(entry.amount for entry in envelopes[0].entries) == [
            Decimal("30.00"), Decimal("45.00"),
        ]

    def test_the_shared_envelope_moves_NO_cash_leg_of_its_own(
        self, app, db, seed_user,
    ):
        """MONEY: the second purchase carries its own posting day.

        An envelope's own cash leg is what its purchases have NOT already
        taken (ruling **R-FM**), and every purchase this door writes carries
        the day the bank took it -- so the envelope contributes nothing itself
        whether it holds one purchase or four.  Asserted directly, because
        "the budget stopped fragmenting" would be worth nothing if the money
        moved with it.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        lines = [
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {amount}",
            )
            for amount in ("-30.00", "-45.00")
        ]
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)
        # Valued AFTER the posting day, never on the anchor's own: an anchor
        # RESETS the ledger for its day, so a balance read there answers the
        # assertion whatever the postings say.
        measured_on = day + timedelta(days=2)
        before = _balance(seed_user, measured_on)

        _batch(seed_user, creations=[
            _creation(seed_user, line, new_envelope=answer) for line in lines
        ])
        db.session.flush()

        assert _balance(seed_user, measured_on) == before - Decimal("75.00")

    def test_lines_in_TWO_periods_get_one_envelope_EACH(
        self, app, db, seed_user,
    ):
        """The key carries the period, because an envelope belongs to one.

        FIRING CONTROL against the over-correction: converging on the NAME
        alone would file a March swipe into an April budget line, which is the
        one thing a pay-period app may never do.
        """
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        later = a_later_period(seed_user).start_date
        one = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=first_day,
            description="LOWES EARLY",
        )
        two = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=later,
            description="LOWES LATE",
        )
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, one, new_envelope=answer),
            _creation(seed_user, two, new_envelope=answer),
        ])
        db.session.flush()

        assert outcome.applied_count == 2
        assert outcome.envelopes_created == 2
        envelopes = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).all()
        assert len({row.pay_period_id for row in envelopes}) == 2

    def test_one_NAME_under_TWO_categories_stays_two_envelopes(
        self, app, db, seed_user,
    ):
        """The key carries the category, and that is not decoration.

        Two answers naming one word under two categories are two budget lines,
        and merging them would file spending under a category the owner did not
        pick -- which is what every spending report groups by.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        one = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=day,
            description="ONE",
        )
        two = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=day,
            description="TWO",
        )
        groceries = seed_user["categories"]["Groceries"]
        other = next(
            category for name, category in seed_user["categories"].items()
            if name != "Groceries"
        )

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, one, new_envelope=NewEnvelope(
                name="Shared Name", category_id=groceries.id,
            )),
            _creation(seed_user, two, new_envelope=NewEnvelope(
                name="Shared Name", category_id=other.id,
            )),
        ])
        db.session.flush()

        assert outcome.envelopes_created == 2
        assert db.session.query(Transaction).filter_by(
            name="Shared Name",
        ).count() == 2

    def test_a_REFUSED_creation_leaves_no_envelope_for_the_next_line(
        self, app, db, seed_user,
    ):
        """Ruling **R-FZ**: a refused item leaves nothing behind.

        A creation rolled back inside its own SAVEPOINT must not leave a later
        line pointing at a row that no longer exists -- so the registry is
        written only once the act has returned.  The second line therefore
        mints the envelope the first one failed to.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        made_later = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=day,
            transaction_on=day + timedelta(days=3), description="REFUSED",
        )
        ordinary = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=day,
            description="LANDS",
        )
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, made_later, new_envelope=answer),
            _creation(seed_user, ordinary, new_envelope=answer),
        ])
        db.session.flush()

        assert outcome.refused_count == 1
        assert outcome.applied_count == 1
        assert outcome.envelopes_created == 1
        envelope = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()
        assert [entry.amount for entry in envelope.entries] == [
            Decimal("45.00"),
        ]


class TestAConvergedEnvelopeClosesOnTheLatestDayItHolds:
    """Finding from adversarial financial review 2026-08-20.

    A press mints ONE envelope per answer per pay period, and the second line
    to reach it used to leave the container recording the FIRST line's posting
    day.  Measured before the fix: two lines in one period submitted 01-05 then
    01-09 closed the envelope on **2024-01-05 while it held `$45.00` the bank
    did not take until 01-09**, and submitting them the other way round
    recorded 01-09 -- so the close day was whichever line happened to be filed
    first.

    **No figure moved either way** (each purchase carries its own posting day,
    so the envelope's own cash leg is `0.00`), which is why no balance
    assertion could see it.  What was wrong is that a row recorded closing
    before money it holds.  The rule applied is this arc's own for a group:
    a match's day is ``max(posted_on)`` over its lines.
    """

    def _sweep(self, seed_user, days, name="Home Improvement"):
        """Record one line per day in *days*, in that order, under one answer."""
        statement = an_import(seed_user)
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name=name, category_id=category.id)
        lines = [
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {amount}",
            )
            for amount, day in days
        ]
        return _batch(seed_user, creations=[
            _creation(seed_user, line, new_envelope=answer) for line in lines
        ])

    def test_it_closes_on_the_LATEST_day_whichever_order_they_arrive(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL, and the order-independence is the point.

        The two submissions differ only in which line is filed first, and the
        recorded close day must not.
        """
        first = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        later = first + timedelta(days=4)

        self._sweep(seed_user, [("-30.00", first), ("-45.00", later)])
        db.session.flush()
        ascending = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()

        assert ascending.settled_on == later

    def test_the_order_does_not_decide_the_close_day(
        self, app, db, seed_user,
    ):
        """The same two lines, filed newest first."""
        first = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        later = first + timedelta(days=4)

        self._sweep(seed_user, [("-45.00", later), ("-30.00", first)])
        db.session.flush()
        descending = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()

        assert descending.settled_on == later

    def test_re_closing_it_moves_NO_cash_leg_of_its_own(
        self, app, db, seed_user,
    ):
        """MONEY: re-stamping the close day must not move a figure.

        The envelope settles from its own entries and every purchase carries
        the day the bank took it, so the container contributes nothing itself
        -- before the re-close and after it: a ``purchases`` close has no
        covering movement and is worth nothing of its own (ruling
        **R-BAL81**), and the balance moves by the purchases alone.
        """
        first = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        later = first + timedelta(days=4)
        before = _balance(seed_user, later + timedelta(days=2))

        self._sweep(seed_user, [("-30.00", first), ("-45.00", later)])
        db.session.flush()

        envelope = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()
        assert envelope.covering_movements == []
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert _balance(seed_user, later + timedelta(days=2)) == (
            before - Decimal("75.00")
        )

    def test_an_EXISTING_envelope_keeps_its_own_close_day(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL against over-correcting.

        A row the owner already closed is THEIR record.  Recording a purchase
        into it is the shipped destination arm and it leaves that day alone; a
        rule that re-dated it here would edit the owner's record rather than
        complete this press's own.
        """
        closed_on = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        # Closed FROM ITS PURCHASES through the verb, on the owner's day: the
        # one settled state a new purchase may join (ruling R-FX), and the one
        # a ``purchases`` record is -- no covering movement, its purchases the
        # record (ruling R-BAL78).  This laid the close on bare and flipped
        # its basis by hand, a row no door writes.
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="30.00", description="Kroger",
            purchased_on=closed_on, settled_on=closed_on,
        )
        transaction_service.settle_transaction(
            envelope, settle_day=an_entered_day(closed_on),
        )
        db.session.flush()
        assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
            None, None,
        )
        assert envelope.covering_movements == []
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-20.00",
            posted_on=closed_on + timedelta(days=5), description="LATER",
        )

        _batch(seed_user, creations=[
            _creation(seed_user, line, transaction_id=envelope.id),
        ])
        db.session.flush()

        db.session.refresh(envelope)
        assert envelope.settled_on == closed_on


class TestConvergingMovesTheSameMoneyAsNotConverging:
    """The money half of finding **N-327**, measured rather than asserted.

    A press mints ONE envelope per answer per pay period.  The claim that the
    convergence is money-neutral was stated in three docstrings and graded by
    nothing until two adversarial reviews said so 2026-08-20 -- the
    batch-versus-one-at-a-time control could not see it (its fixture creates no
    envelope at all), and the direct balance case passes with convergence
    disabled, because two envelopes holding one purchase each and one envelope
    holding two both take the same cash.

    **So this compares the two paths on what convergence must PRESERVE and
    deliberately not on what it must CHANGE.**  Balances, ledger postings and
    the purchases themselves must be identical; the number of budget rows is
    the thing that differs, and comparing it would grade the fixture rather
    than the money.
    """

    def _facts(self, db, seed_user, days):
        """Return the money facts both paths must agree on."""
        account = seed_user["account"]
        ctx = balance_at.BalanceContext.build(seed_user["user"].id)
        entries = _db.session.execute(_db.text(
            "SELECT e.description, e.amount, e.purchased_on, e.settled_on"
            "  FROM budget.transaction_entries e"
            " ORDER BY e.amount, e.settled_on"
        )).all()
        return {
            "balances": {
                day.isoformat(): str(
                    balance_at.cash_balance_at(account, ctx, day),
                )
                for day in days
            },
            "entries": [tuple(str(v) for v in row) for row in entries],
            "ledger": nets_by_key_and_day(_db.session, seed_user["scenario"].id),
        }

    def test_the_two_paths_book_the_same_money(self, app, db, seed_user):
        """FIRING CONTROL for the money claim, on a fixture that converges."""
        statement = an_import(seed_user)
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        later = day + timedelta(days=3)
        lines = [
            a_bank_line(
                seed_user, statement, amount="-30.00", posted_on=day,
                description="LOWES ONE",
            ),
            a_bank_line(
                seed_user, statement, amount="-45.00", posted_on=later,
                description="LOWES TWO",
            ),
        ]
        creations = [
            _creation(seed_user, line, new_envelope=answer) for line in lines
        ]
        days = [day, later, later + timedelta(days=7)]
        _db.session.flush()

        converged_run = _db.session.begin_nested()
        outcome = _batch(seed_user, creations=creations)
        _db.session.flush()
        assert outcome.envelopes_created == 1, "the fixture did not converge"
        converged = self._facts(db, seed_user, days)
        converged_run.rollback()
        _db.session.expire_all()

        separate_run = _db.session.begin_nested()
        for creation in creations:
            statement_match.create_purchase_from_line(
                creation, a_scope(seed_user),
                _create.MintedEnvelopes.none_yet(), an_answers(seed_user),
                applied_by_rule=False,
            )
            _db.session.flush()
        assert _db.session.execute(_db.text(
            "SELECT count(*) FROM budget.transactions"
            " WHERE name = 'Home Improvement'"
        )).scalar() == 2, "the un-converged path did not make two envelopes"
        separate = self._facts(db, seed_user, days)
        separate_run.rollback()
        _db.session.expire_all()

        assert converged["balances"] == separate["balances"]
        assert converged["entries"] == separate["entries"]
        assert converged["ledger"] == separate["ledger"]
        # The figures, or that comparison could hold between two empty ledgers:
        # each purchase books its own line on the day its line posted.
        cash = linked_key_of(_db.session, seed_user["account"].id)
        assert separate["ledger"][(cash, day)] == Decimal("-30.00")
        assert separate["ledger"][(cash, later)] == Decimal("-45.00")
