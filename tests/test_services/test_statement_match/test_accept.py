"""The ACCEPT door: what it refuses, and what it moves when it does not.

Plan step **bank_import:X-f6a-2**, rulings **R-FS**, **R-FP** and **R-FV**.
This is the only door in the arc that moves money, so every one of its
refusals is a FIRING CONTROL here -- written to fail if the refusal were
deleted, which is the standard ``docs/plans/verification.md`` sets and which
this project has twice measured the absence of.

**What the door promises, and what each class below pins:**

* a group's two sides SUM, or nothing is written and the difference is named;
* every member row takes the bank's day -- settling a Projected row and
  correcting a settled one;
* it does NOT write ``reconciled_by_id`` (ruling **R-FV**) and it RELEASES any
  link it moves a day out from under;
* an id outside the owner's own candidate set is refused, not skipped;
* a release restores the QUESTION and leaves the days alone.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    MovementFigureSourceEnum,
    SettledDayBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.exceptions import ValidationError
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import (
    balance_at,
    entry_service,
    statement_match,
    status_seam,
    transaction_service,
)
from app.services.balance_at import BalanceContext
from app.services.row_valuation import settled_figure
from app.services.statement_match import MatchSubmission

from tests._test_helpers import (
    an_observed_day,
    observed,
    on_both_sides,
    typed,
    family_journal_filter,
    an_entered_day,
    create_settled_cash_transaction,
)

from ._builders import (
    accepted_acts,
    a_bank_line,
    a_purchase,
    a_scope,
    a_submission,
    a_transaction,
    an_assertion,
    an_import,
)
from app.services.settle_day import (
    SettleDay,
    recorded_settle_day,
)
from app.models.amount_ownership import AmountOwnership


def _balance_on(seed_user, day):
    """Return the checking account's balance as of *day*.

    Args:
        seed_user: The seeded user bundle.
        day: The day to value the account on.

    Returns:
        The balance.
    """
    return balance_at.balance_at(
        seed_user["account"],
        BalanceContext(
            user_id=seed_user["user"].id,
            scenario=seed_user["scenario"], as_of=day,
        ),
        day,
    )


def _posted_cash_by_day(db, txn, account):
    """Return what *txn* posts to *account*'s cash leg, netted per civil day.

    **NETTED, not listed, because this ledger is APPEND-ONLY.**  A correction
    does not move a journal entry: measured 2024-01-06 -> 2024-01-10 on a real
    settle, it writes a REVERSAL at the old day and a fresh posting at the new
    one, so three entries stand over two days.  A test asserting *which days
    carry an entry* would call that a leftover posting and be wrong; what says
    the money moved is that the old day's postings SUM to zero.

    Args:
        db: The session fixture.
        txn: The transaction whose postings to read.
        account: The account whose cash leg to read.

    Returns:
        ``{entry_date: net}`` over the days this row posts on, days netting to
        zero omitted -- so the answer is the days the row actually MOVES cash.
    """
    rows = (
        db.session.query(
            JournalEntry.entry_date, db.func.sum(Posting.amount),
        )
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .join(
            LedgerAccount,
            LedgerAccount.id == Posting.ledger_account_id,
        )
        .filter(
            family_journal_filter(txn),
            LedgerAccount.account_id == account.id,
        )
        .group_by(JournalEntry.entry_date)
        .all()
    )
    return {row[0]: row[1] for row in rows if row[1] != 0}


def _submit(
    seed_user, lines=(), transactions=(), entries=(), residual=None,
    transfers=(),
):
    """Accept a match naming exactly these subjects.

    Args:
        seed_user: The seeded user bundle.
        lines: Bank line rows.
        transactions: Transaction rows.
        entries: Purchase rows.
        residual: The difference the screen showed and the owner agreed to
            record, or ``None`` (plan step ``bank_import:X-f6d-4``).
        transfers: Transfer rows, each ticked as its leg on this account
            (leaf ``balance:X-bi-6-4c-1``).

    Returns:
        The :class:`~app.services.statement_match.AcceptedMatch`.
    """
    # DERIVED HERE, so every call sees the rows this test has staged.  A
    # scope built once per test would be a snapshot older than the fixture.
    # **The SAME scope builds the submission and applies it**, which is the
    # two-moment flow the screen has: the reviewed state a tick carries is read
    # off the pass that rendered it (finding **N-336**).
    scope = a_scope(seed_user)
    return statement_match.accept_match(
        a_submission(
            scope, lines=lines, transactions=transactions, entries=entries,
            residual=residual, transfers=transfers,
        ),
        scope,
    )


class TestABalancedMatchIsRecorded:
    """The ordinary one-line-one-row case."""

    def test_the_row_takes_the_banks_day(self, app, db, seed_user):
        """The correction this whole arc exists to make.

        Measured on the developer's own statement: of 58 lines an exact-amount
        predicate pairs uniquely with a row, only 23 carry the day the app had
        recorded.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=3),
        )

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on == bank_day
        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0
        assert accepted.posts_on == bank_day

    def test_a_projected_row_is_settled(self, app, db, seed_user):
        """The bank is evidence the money moved, so the row says so.

        11 rows inside the developer's own statement span had never been
        marked as having happened.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on == bank_day
        assert txn.status.is_settled
        assert accepted.settled_count == 1
        assert accepted.corrected_count == 0

    def test_a_row_already_on_the_banks_day_counts_as_neither(
        self, app, db, seed_user,
    ):
        """Confirming is not correcting, and the report must not claim it is."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert (accepted.settled_count, accepted.corrected_count) == (0, 0)

    def test_the_match_is_recorded_with_one_member_per_subject(
        self, app, db, seed_user,
    ):
        """The relation is what makes a re-import stop re-proposing it.

        **The app-side member names the row's PAYMENT, not the row** (plan
        step ``credit_card:CC-5-4a-1``, ruling **R-CC43**, developer
        2026-09-21: *the MOVEMENT is the subject of every settled match on
        every screen; a row is a candidate only while Projected*).  The
        owner ticked a Projected bill; the accept settled it through its own
        door, which wrote its covering movement, and that movement -- the
        money the bank showed -- is what the act records.  Through
        ``CC-5-3`` this asserted ``transaction_id == txn.id``.
        Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions
        under R-CC43 -- the member names the payment; a day moves through the seam that mirrors it;
        a settled row is ticked as its payment."
        Since ``CC-5-4a-2`` the member table has no row column, so "no member
        names the row" is the schema's; the assertion that said so went with it.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        members = (
            db.session.query(StatementMatchMember)
            .filter(StatementMatchMember.match_id == accepted.match_id)
            .all()
        )
        assert len(members) == 2
        assert {m.bank_statement_line_id for m in members} == {line.id, None}
        (movement,) = txn.covering_movements
        assert {m.transaction_entry_id for m in members} == {movement.id, None}
        assert movement.settled_on == line.posted_on

    def test_a_reviewed_act_is_recorded_as_a_TICK_and_not_as_a_rule(
        self, app, db, seed_user,
    ):
        """Ruling **R-GT**, at the door rather than at the column.

        This is the reviewed-pass door: a row here exists because a person read
        a proposal and pressed Apply, which is the whole of ruling **R-FP**'s
        surviving half.  ``applied_by_rule`` is NOT NULL with no default, so
        the fact is stated at the call site or nothing is written at all -- and
        what it must state HERE is ``False``.  Plan step ``bank_import:X-ge``
        builds the door that states ``True``; measured on the developer's dev
        database 2026-08-26, all 221 recorded acts are ticks.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert db.session.get(
            StatementMatch, accepted.match_id,
        ).applied_by_rule is False


class TestTheGroupMustSum:
    """The developer's ruling of 2026-08-17, and finding **N-239**'s own data.

    **Amended by plan step ``bank_import:X-f6d-4``**: the sides must still
    meet, but a difference the owner ACCEPTS is closed by a member this door
    mints rather than by sending them away (ruling **R-FN**).  What is graded
    here is the refusal that stands when nobody accepts one;
    :mod:`.test_residual` grades the other half.
    """

    def test_a_five_cent_shortfall_is_refused(self, app, db, seed_user):
        """The payroll shape: the bank paid more than the app's rows say.

        7 payroll deposits on a production clone of the developer's own data
        sit `$0.04`-`$0.06` apart from what the app holds.  A tolerance would
        absorb exactly the defect the matcher is the first instrument able to
        see -- and this refusal is what stands when the owner has NOT agreed
        to record the difference.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="2573.43")
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
            )

        assert "0.05" in str(caught.value)
        # It says what to do about it, which is the half X-f6d-4 added: before
        # that step the only advice was to go and edit a row.  *It said "tick
        # the box" until plan step bank_import:X-gp*: a group's consent is one
        # control whose OPTIONS are the acts now, and a refusal is the
        # service's own words about the control it offers (R-FZ(a)), so the
        # sentence names the choice and both acts.
        assert "choose what to do with the difference" in str(caught.value)
        assert "row with no category" in str(caught.value)
        assert salary.settled_on is None
        assert allowance.settled_on is None

    def test_a_group_that_sums_is_accepted(self, app, db, seed_user):
        """The control: the same shape, correct to the cent."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.38", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
        )

        assert accepted.settled_count == 2
        assert salary.settled_on == bank_day
        assert allowance.settled_on == bank_day

    def test_every_member_takes_the_LATEST_bank_day(self, app, db, seed_user):
        """N lines, one row: the row is not wholly moved until the last posts.

        Taking the earliest would let a balance asserted between the two absorb
        money that had not all left the account.
        """
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        last = first + timedelta(days=2)
        line_a = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
        )
        line_b = a_bank_line(
            seed_user, statement, amount="-80.00", posted_on=last,
        )
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(
            seed_user, lines=[line_a, line_b], transactions=[txn],
        )

        assert accepted.posts_on == last
        assert txn.settled_on == last


class TestARowThatMOVEDSinceTheReviewIsRefused:
    """Finding **N-336**, at the door: what commits is what was reviewed.

    Ruling **R-FP** says a match is a PROPOSAL, and the screen states the
    correction accepting one would write.  Until plan step
    ``bank_import:X-f6d-3`` nothing compared the two: ``resolve_rows``
    re-priced the row per act -- correct in itself, finding **N-309** -- and
    ``corrected_figure`` then wrote the bank's figure whatever that price was.
    Reproduced on the developer's own data: the screen offered *from
    ``-178.32`` to ``-178.29``*, the row was edited to ``500.00`` in another
    tab, and Apply wrote a **``$321.71``** correction under that caption.

    **The exact tier never had this**, which is why it is a regression rather
    than an old hole: an equal match whose price moved became UNEQUAL and was
    refused, so staleness failed CLOSED by accident until ``X-f6d-2`` made an
    unequal one-to-one recordable.

    ``test_submission`` grades the two coordinates as values, one writer at a
    time.  These grade that the door REACHES them and writes nothing.
    """

    def test_a_row_whose_FIGURE_moved_is_refused(self, app, db, seed_user):
        """The reproduced case, through the real door.

        **What moves is the covering MOVEMENT's figure**, because that is
        what a settled row is worth to this door (ruling **R-BAL81**,
        ``covered_cash_leg``), and the movement's row carries no version of
        the parent's: the FIGURE coordinate alone moves.  Through plan step
        ``balance:X-bi-4a``'s first cut this moved the row's own
        ``settled_amount`` (deleted at ``X-bi-4b-2``) and
        the ownership, which the price no longer reads and which bump the
        row's ``version_id`` -- so the case passed on the REVISION arm and
        the figure coordinate of a settled row was unpinned (adversarial
        review 2026-09-18).
        """
        line = a_bank_line(seed_user, an_import(seed_user), amount="-178.29")
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=line.posted_on,
        )
        scope = a_scope(seed_user)
        submission = a_submission(scope, lines=[line], transactions=[txn])
        version_reviewed = txn.version_id

        # ...and what the row is worth moves after the screen was rendered,
        # with the row's own revision untouched.
        [movement] = txn.covering_movements
        movement.amount = Decimal("500.00")
        db.session.flush()
        assert txn.version_id == version_reviewed

        with pytest.raises(ValidationError, match="reviewed against different"):
            statement_match.accept_match(submission, a_scope(seed_user))

        assert db.session.query(StatementMatch).count() == 0
        assert movement.amount == Decimal("500.00"), (
            "the refused act wrote the bank's figure over the edit"
        )

    def test_a_row_whose_REVISION_moved_is_refused(self, app, db, seed_user):
        """The coordinate the figure cannot see.

        The row is worth exactly what the screen showed and has still been
        edited -- here its settle day, which is what decides whether a match
        re-dates anything.  A guard on the figure alone applies this silently.
        """
        line = a_bank_line(seed_user, an_import(seed_user), amount="-180.00")
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=line.posted_on,
        )
        scope = a_scope(seed_user)
        submission = a_submission(scope, lines=[line], transactions=[txn])

        # A raw write around the seam, on the ROW alone: the subject the
        # screen offered is the row's PAYMENT (plan step
        # ``credit_card:CC-5-4a-1``, ruling **R-CC43**), and this edit never
        # touches the movement -- so it is the ROW's counter, carried in the
        # payment's token beside the movement's, that has to catch it.
        txn.settled_on = line.posted_on - timedelta(days=2)
        db.session.flush()

        with pytest.raises(ValidationError, match="reviewed against different"):
            statement_match.accept_match(submission, a_scope(seed_user))

        assert db.session.query(StatementMatch).count() == 0

    def test_a_PURCHASE_whose_revision_moved_is_refused(
        self, app, db, seed_user,
    ):
        """The other candidate kind, and it was uncovered.

        The two rows a match can name are built by DIFFERENT constructors, so a
        control over transactions alone grades one of them: found by a mutation
        sweep 2026-08-23, where hardcoding ``purchase_candidate``'s revision
        left every test passing.  It matters because a purchase is the row
        ruling **R-GE** newly lets a match re-price -- 2 of the developer's own
        10 near misses are purchases under a settled envelope.

        **What moves here is neither the figure nor a day**, so this isolates
        the revision coordinate: only the description changes, which no other
        guard in this door reads.
        """
        line = a_bank_line(seed_user, an_import(seed_user), amount="-25.00")
        # Closed FROM the purchase through the verb (ruling R-BAL78), as the
        # developer's near misses are.
        envelope = a_transaction(
            seed_user, name="Groceries", amount="200.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=line.posted_on, description="Walmart",
        )
        transaction_service.settle_transaction(
            envelope, settle_day=an_entered_day(line.posted_on),
        )
        db.session.flush()
        scope = a_scope(seed_user)
        submission = a_submission(scope, lines=[line], entries=[purchase])

        purchase.description = "Walmart, corrected"
        db.session.flush()

        with pytest.raises(ValidationError, match="reviewed against different"):
            statement_match.accept_match(submission, a_scope(seed_user))

        assert db.session.query(StatementMatch).count() == 0

    def test_ONE_row_named_TWICE_at_two_figures_is_refused(
        self, app, db, seed_user,
    ):
        """A crafted body may not choose which state the guard checks.

        The screen renders exactly one input per row, so this cannot arrive
        from it -- but ``MatchSubmission.rows`` is a SET of values rather than
        of ids, so two entries naming one subject at different figures are two
        distinct members that collapse to one key in ``subjects``.  Whichever
        the set iterated last would have become the state the staleness guard
        compared against, on the door that re-prices rows.

        **A first draft left this to the count below and a docstring claimed
        it was caught there.**  It was not: that count is taken over the
        COLLAPSED mapping, so two rows over one subject compares 1 against 1
        and passes.  Found by re-auditing this step's own diff, 2026-08-23.
        """
        line = a_bank_line(seed_user, an_import(seed_user), amount="-180.00")
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=line.posted_on,
        )
        scope = a_scope(seed_user)
        honest = a_submission(scope, lines=[line], transactions=[txn])
        truthful_row = next(iter(honest.rows))

        crafted = replace(honest, rows=frozenset({
            truthful_row,
            replace(truthful_row, cash_amount=Decimal("-999.00")),
        }))

        with pytest.raises(ValidationError, match="same row more than once"):
            statement_match.accept_match(crafted, scope)

        assert db.session.query(StatementMatch).count() == 0

    def test_an_UNMOVED_row_still_applies(self, app, db, seed_user):
        """The control that keeps the two above from grading a broken door.

        Both cases would pass against a door that refused every match, and
        137 of the developer's own proposals take this arm.
        """
        line = a_bank_line(seed_user, an_import(seed_user), amount="-178.29")
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=line.posted_on,
        )

        # Stating 0.03 is what -178.29 bank against a -178.32 row comes to, and every
        # match carries the difference it was reviewed against since
        # plan step bank_import:X-gj-1b -- the near tier's own card
        # renders it as a hidden field (the stated_difference filter).
        accepted = _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )

        assert accepted.repriced_count == 1
        assert settled_figure(txn) == Decimal("178.29")


class TestEveryOtherRefusalFires:
    """Each of these is reachable from a stale page, and each writes nothing."""

    def test_a_match_with_no_row_is_refused(self, app, db, seed_user):
        """One thing is not a match."""
        line = a_bank_line(seed_user, an_import(seed_user))

        with pytest.raises(ValidationError, match="at least one"):
            _submit(seed_user, lines=[line])

    def test_a_match_with_no_line_is_refused(self, app, db, seed_user):
        """The other half of the same rule."""
        txn = a_transaction(seed_user, amount="180.00")

        with pytest.raises(ValidationError, match="at least one"):
            _submit(seed_user, transactions=[txn])

    def test_an_envelope_and_its_own_purchase_is_refused(
        self, app, db, seed_user,
    ):
        """It cannot count the same money twice: the ROW is not offered.

        Through plan step ``balance:X-bi-4a``'s first cut an envelope's cash
        leg INCLUDED its outstanding purchases, so naming both summed that
        purchase in two terms and ``_reject_parent_and_its_own_purchase``
        stood between.  Under ruling **R-BAL81** a row that settles from its
        purchases is worth ``0`` to the offer and is not a candidate at all
        -- its purchases are -- so the row is refused as unavailable, and the
        double count is unrepresentable rather than refused.  That left this
        arm of the guard unreachable, and plan step ``credit_card:CC-5-4a-5``,
        leaf 5c-2c-1, deleted the guard (finding **CC-386**, ruling
        **R-CC144**).
        """
        statement = an_import(seed_user)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(seed_user, envelope, amount="25.00")
        line = a_bank_line(seed_user, statement, amount="-125.00")

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(
                seed_user, lines=[line],
                transactions=[envelope], entries=[purchase],
            )

        assert purchase.settled_on is None
        assert envelope.settled_on is None

    def test_an_envelope_matched_SEPARATELY_from_its_purchase_is_refused(
        self, app, db, seed_user,
    ):
        """The cross-match half, and the one that actually moves money.

        **Within one match the two sides are priced together and refuse
        together; across two, each balances on its own and the second
        FALSIFIES the first.**  Measured on a production clone by adversarial
        financial review 2026-08-17: envelope 2280 prices at `-265.69` with its
        four unposted purchases included, its purchase 78 at `-18.64`; matching
        the envelope first and the purchase second stamps the purchase's
        posting day, dropping the envelope's leg to `-247.05` -- so two matched
        line-sets worth `-284.33` end up backed by `-265.69` of ledger and the
        projected balance reads `$18.64` HIGH.  The hand-build form lists an
        envelope and its purchases side by side, so it is two clicks.

        **Under ruling R-BAL81 the first click cannot land**: envelope 2280
        is worth ``0`` to the offer while it holds purchases and is refused
        as unavailable, so there is no first match for the second to
        falsify; the purchase matches on its own, at its own figure.  The
        cross-match arm of ``_reject_parent_and_its_own_purchase`` was not
        reached from THIS shape after that ruling; the one shape it still
        reached -- an envelope matched EMPTY, reverted, and then given a
        purchase -- was a false refusal, and matches since finding **CC-385**
        (the case below).  The guard itself is deleted since plan step
        ``credit_card:CC-5-4a-5``, leaf 5c-2c-1 (finding **CC-386**, ruling
        **R-CC144**).
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        # Two purchases, so the envelope's figure at its purchases (`-55.00`)
        # is distinguishable from either of them.
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        a_purchase(
            seed_user, envelope, amount="30.00", description="Aldi",
            purchased_on=bank_day,
        )
        envelope_line = a_bank_line(
            seed_user, statement, amount="-55.00", posted_on=bank_day,
        )
        purchase_line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            sequence_in_group=1,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[envelope_line], transactions=[envelope])
        assert envelope.settled_on is None

        _submit(seed_user, lines=[purchase_line], entries=[purchase])

        assert purchase.settled_on == bank_day
        assert envelope.settled_on is None

    def test_a_purchase_under_a_REVERTED_matched_envelope_matches(
        self, app, db, seed_user,
    ):
        """The cross-match guard's last shape was a false refusal, and it matches.

        An EMPTY envelope is worth its plan and is matched WHOLE.  The owner
        then reverts it: the match still names its covering movement (nothing
        in the status seam touches ``statement_match_members``), the movement
        is kept UN-DATED and counts nothing, and the row is Projected again,
        so a purchase may be recorded against it.  This case pinned that
        purchase's match as refused, "would count the same money twice"
        (adversarial review 2026-09-18), until finding **CC-385** (plan step
        ``credit_card:CC-5-4a-5``, leaf 5c-2b) measured the sentence false:
        the purchase's line is the only line explaining the purchase's money.
        Rewritten under the developer's rule-5 answer of 2026-10-05, "Allow
        it, change test": *"The match saves: the $30 purchase is dated 9/26
        and Checking goes down $30.00. The old $120 match stays flagged with
        its Undo."* -- here a `$25.00` purchase under a `$100.00` envelope.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        whole_line = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=bank_day,
        )
        _submit(seed_user, lines=[whole_line], transactions=[envelope])
        assert envelope.status.is_settled

        status_seam.apply_status_change(
            envelope, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.flush()
        assert envelope.status.is_settled is False
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        purchase_line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            sequence_in_group=1,
        )

        kept = envelope.covering_movements[0]
        _submit(seed_user, lines=[purchase_line], entries=[purchase])

        assert purchase.settled_on == bank_day
        assert kept.settled_on is None
        assert db.session.query(StatementMatchMember).filter_by(
            transaction_entry_id=kept.id,
        ).count() == 1, "the old match still names the kept payment"

    def test_a_purchase_matched_SEPARATELY_from_its_envelope_is_refused(
        self, app, db, seed_user,
    ):
        """The same clash approached from the other side.

        Asked in both orders because the guard read two relations -- a
        submitted purchase against already-matched parents, and a submitted
        envelope against already-matched purchases -- and one of them being
        right proves nothing about the other.  Under ruling **R-BAL81** the
        envelope is worth ``0`` to the offer whether or not one of its
        purchases has matched, so this order too refuses the ROW as
        unavailable rather than as a clash; the `-30.00` line is the other
        purchase's, and it matches.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        aldi = a_purchase(
            seed_user, envelope, amount="30.00", description="Aldi",
            purchased_on=bank_day,
        )
        purchase_line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )
        # Through X-bi-4a's first cut the envelope booked only what its
        # purchases did NOT, so once the 25.00 purchase posted its close was
        # worth 30.00 and this line was offered the ROW; it is Aldi's line.
        envelope_line = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=bank_day,
            sequence_in_group=1,
        )
        _submit(seed_user, lines=[purchase_line], entries=[purchase])

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[envelope_line], transactions=[envelope])
        assert envelope.settled_on is None

        _submit(seed_user, lines=[envelope_line], entries=[aldi])
        assert aldi.settled_on == bank_day

    def test_a_cancelled_row_is_refused(self, app, db, seed_user):
        """Not money this account moved, so not a candidate and not matchable."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(
            seed_user, amount="180.00", status=StatusEnum.CANCELLED,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[line], transactions=[txn])

    def test_a_card_purchase_is_refused(self, app, db, seed_user):
        """A card purchase never touches checking, so no statement shows it."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-25.00")
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", is_credit=True,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[line], entries=[purchase])

    def test_an_unknown_line_is_refused(self, app, db, seed_user):
        """A refusal rather than a silent skip: this door names rows on purpose."""
        txn = a_transaction(seed_user, amount="180.00")

        scope = a_scope(seed_user)
        with pytest.raises(ValidationError, match="no longer on this account"):
            statement_match.accept_match(
                replace(
                    a_submission(scope, transactions=[txn]),
                    line_ids=frozenset({999999}),
                ),
                scope,
            )

    def test_a_second_match_on_one_LINE_is_refused(self, app, db, seed_user):
        """The line twin of the row guard below, and it was missing.

        ``uq_statement_match_members_line`` refuses the second act either way,
        so nothing could be corrupted -- but it arrives as an
        ``IntegrityError`` AFTER ``_apply_day`` has already moved a settle day,
        which reaches the user as "Something went wrong" and logs a traceback
        at ERROR for an ordinary stale page.  The hand-build form renders a
        checkbox per unmatched line, so one tab submitting a line another tab
        just matched is two clicks.  Found by adversarial security review
        2026-08-17.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        first = a_transaction(seed_user, name="Electric", amount="180.00")
        second = a_transaction(seed_user, name="Water", amount="180.00")
        _submit(seed_user, lines=[line], transactions=[first])

        with pytest.raises(ValidationError, match="already matched"):
            _submit(seed_user, lines=[line], transactions=[second])

        assert second.settled_on is None

    def test_a_second_match_on_one_row_is_refused(self, app, db, seed_user):
        """A matched row leaves the candidate set, so it cannot be claimed twice."""
        statement = an_import(seed_user)
        first = a_bank_line(seed_user, statement, sequence_in_group=0)
        second = a_bank_line(seed_user, statement, sequence_in_group=1)
        txn = a_transaction(seed_user, amount="180.00")
        _submit(seed_user, lines=[first], transactions=[txn])

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[second], transactions=[txn])


class TestTheClearingLinkIsNotWritten:
    """Ruling **R-FV**: identity is stored, absorption is derived."""

    def test_no_member_gains_a_clearing_link(self, app, db, seed_user):
        """A bank line names no ``account_anchor_history`` row, so nor does this."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")

        _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.reconciled_by_id is None

    def test_moving_a_day_RELEASES_an_existing_link(self, app, db, seed_user):
        """The bank has just contradicted the day that link was recorded on.

        The release is the settle doors' own rule (``status_seam``); this pins
        that the accept door goes THROUGH them rather than writing the column,
        which is what makes the two facts unable to disagree.
        """
        from app.models.account import AccountAnchorHistory  # local: this test

        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        anchor = (
            db.session.query(AccountAnchorHistory)
            .filter_by(account_id=seed_user["account"].id)
            .first()
        )
        assert anchor is not None, "create_account writes the opening assertion"
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=2),
        )
        txn.reconciled_by_id = anchor.id
        db.session.flush()
        line = a_bank_line(seed_user, statement, posted_on=bank_day)

        _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on == bank_day
        assert txn.reconciled_by_id is None


class TestReleasingAMatch:
    """The repair door finding **N-302** says a refusal owes."""

    def test_it_deletes_the_act_and_its_members(self, app, db, seed_user):
        """The bank lines become unexplained again.

        **The receipt is a value rather than a count since plan step
        ``bank_import:X-f6f``** (ruling **R-GG**), because the door can now
        destroy rows and a receipt that said only "2" could not tell an undo
        that moved nothing from one that removed a `$213.49` purchase.  This
        act created nothing, so all three removal facts are zero -- which is
        the control for the ones below that are not.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")
        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        released = statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )

        assert released.released_count == 2
        assert released.removed_rows == 0
        assert released.removed_cash == Decimal("0.00")
        assert released.kept_containers == 0
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(StatementMatchMember).count() == 0
        # A match between rows that already existed leaves the row standing:
        # the release removes what the act CREATED, never what it named.
        assert db.session.get(Transaction, txn.id) is not None

    def test_it_does_NOT_put_the_day_back(self, app, db, seed_user):
        """The bank is still the best record of when that money moved.

        Reverting the correction to tidy a relation would throw away the fact
        and keep the bookkeeping.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=4),
        )
        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )

        assert txn.settled_on == bank_day

    def test_the_row_is_matchable_again(self, app, db, seed_user):
        """What a release restores is the QUESTION."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")
        accepted = _submit(seed_user, lines=[line], transactions=[txn])
        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )

        again = _submit(seed_user, lines=[line], transactions=[txn])

        assert again.match_id != accepted.match_id

    def test_another_owners_match_is_refused(self, app, db, seed_user):
        """404-for-both, as a refusal because this door names one act."""
        with pytest.raises(ValidationError, match="no longer there"):
            statement_match.release_match(
                999999, seed_user["user"].id, seed_user["account"].id,
            )


class TestAPurchaseIsMatchableToo:
    """A purchase is a cash movement of its own (plan step ``balance:X-f3b``)."""

    def test_a_purchase_takes_the_banks_posting_day(self, app, db, seed_user):
        """The 267 card-swipe lines on the developer's statement are these."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.settled_on == bank_day
        assert accepted.settled_count == 1


class TestATransferShadowIsMatchedThroughItsService:
    """The kind the bank shows most, and the one that must not be touched directly.

    **13 of 22 non-swipe one-to-one matches on the developer's own statement are
    transfer shadows** -- the mortgage transfer, the Van Loan payment, the money
    market moves -- and 9 of those carry a day the bank corrects.  So this is the
    ordinary case rather than an exotic one, and it is the case that must go
    through ``transfer_service``: ``CLAUDE.md``'s transfer invariant 4 admits no
    direct mutation of a shadow, and ``transaction_service.settle_transaction``
    REFUSES one outright.
    """

    @staticmethod
    def _a_transfer(db, seed_user, *, amount="75.00", settled=False):
        """Return a transfer off the seeded checking account into a new Savings.

        Built through ``transfer_service.create_transfer``, the sole creation
        chokepoint, so the pair and its invariants are the real ones.  It
        returned the transfer's Checking SHADOW until plan step
        ``balance:X-bi-6-4d-2``, when each side's record moved off its shadow
        onto the transfer (ruling **R-BAL88**): the class reads a side as its
        record (:meth:`_record`) and ticks it as the transfer (ruling
        **R-BAL167** classes 1 and 4).

        Args:
            db: The session fixture.
            seed_user: The seeded user bundle.
            amount: The transfer amount.
            settled: Whether to settle the pair first, so the match CORRECTS a
                day rather than settling one.

        Returns:
            The :class:`~app.models.transfer.Transfer`.
        """
        from app.models.account import Account  # local: this class only
        from app.services import account_service, transfer_service
        from tests._test_helpers import (
            create_transfer,
            open_books_before_the_first_assertion,
        )

        destination = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=seed_user["account"].account_type_id,
                name="Savings",
                anchor_balance=Decimal("100.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
        )
        db.session.flush()
        # **Its BOOKS open before the bootstrap period** (plan step X-f3c-2b,
        # ruling **R-HG**).  ``observed_on`` above puts the origination
        # ASSERTION on the period's first day, which is right -- and it puts
        # the books there too, while the shadows this class matches settle on
        # that same day.  An opening equity is its own day's CLOSING balance,
        # so those settles are inside it.
        open_books_before_the_first_assertion(db.session, destination)
        transfer = create_transfer(
            seed_user, db.session, seed_user["account"], destination,
            seed_user["bootstrap_period"], amount=Decimal(amount),
        )
        if settled:
            transfer_service.settle_transfer(
                transfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    transfer.from_account_id, transfer.to_account_id,
                    an_entered_day(
                        seed_user["bootstrap_period"].start_date + timedelta(days=5),
                    ),
                ),
            )
        db.session.flush()
        assert isinstance(destination, Account)
        return transfer

    @staticmethod
    def _record(db, transfer, account_id):
        """Return *transfer*'s side record on *account_id*, or ``None``.

        Read by this class's own SQL over the side links (ruling **R-BAL167**
        class 4: never through the producer under test).
        """
        from app.models.transaction_entry import TransactionEntry  # local

        return (
            db.session.query(TransactionEntry)
            .filter(
                (TransactionEntry.expense_transfer_id == transfer.id)
                | (TransactionEntry.income_transfer_id == transfer.id),
                TransactionEntry.account_id == account_id,
            )
            .one_or_none()
        )

    def test_a_projected_shadow_settles_through_the_transfer_service(
        self, app, db, seed_user,
    ):
        """Both sides and the parent move in one call, which is invariant 3.

        Each side is its RECORD since plan step ``balance:X-bi-6-4d-2`` (ruling
        **R-BAL167** class 1): the transfer says Paid and each side's record
        is dated, where each shadow said Paid until then.
        """
        transfer = self._a_transfer(db, seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transfers=[transfer])

        assert accepted.settled_count == 1
        assert self._record(
            db, transfer, transfer.from_account_id,
        ).settled_on == bank_day
        assert transfer.status.is_settled
        assert self._record(
            db, transfer, transfer.to_account_id,
        ).settled_on is not None

    def test_a_settled_shadows_day_is_CORRECTED(self, app, db, seed_user):
        """The majority case on real data, and the one a settle verb cannot do.

        ``transfer_service.settle_transfer`` is an idempotent no-op on a
        transfer already in the settled band -- it writes nothing and reports
        False -- so a door that only knew that verb would silently fail to
        correct 9 of the developer's own 13 shadow matches.  The dispatch takes
        ``update_transfer`` here instead, and this is the control over it.
        """
        transfer = self._a_transfer(db, seed_user, settled=True)
        record = self._record(db, transfer, transfer.from_account_id)
        bank_day = seed_user["bootstrap_period"].start_date
        assert record.settled_on != bank_day
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transfers=[transfer])

        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0
        assert record.settled_on == bank_day

    def test_a_match_states_ITS_side_and_the_far_side_borrows(
        self, app, db, seed_user,
    ):
        """The bank showed Checking; Savings' side borrows that day (ruling R-BAL89).

        Plan step ``balance:X-bi-6-4c-3`` (design D5): the matcher states
        ``observed`` for the side on the statement's account only.  It stated it
        for BOTH until then, which is how every far side of a matched transfer
        came to claim a bank line it never had.
        """
        transfer = self._a_transfer(db, seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        _submit(seed_user, lines=[line], transfers=[transfer])

        assert recorded_settle_day(
            self._record(db, transfer, transfer.from_account_id),
        ) == an_observed_day(bank_day)
        assert recorded_settle_day(
            self._record(db, transfer, transfer.to_account_id),
        ) == SettleDay(day=bank_day, basis=SettledDayBasisEnum.BORROWED)

    def test_a_correction_moves_a_side_that_only_borrowed_its_day(
        self, app, db, seed_user,
    ):
        """Paid pressed, then the statement: BOTH sides land on the bank's day.

        Ruling **R-BAL163**'s worked example: a Paid press leaves both sides
        borrowing the press day, so a Checking statement showing another day
        moves Checking (its own now) and Savings (still borrowing) together.
        """
        from app.services import transfer_service  # local: this class only

        transfer = self._a_transfer(db, seed_user)
        transfer_service.settle_transfer(transfer.id, seed_user["user"].id)
        db.session.flush()
        record = self._record(db, transfer, transfer.from_account_id)
        bank_day = seed_user["bootstrap_period"].start_date
        assert record.settled_on != bank_day
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transfers=[transfer])

        assert accepted.corrected_count == 1
        assert recorded_settle_day(record) == an_observed_day(bank_day)
        assert recorded_settle_day(
            self._record(db, transfer, transfer.to_account_id),
        ) == SettleDay(day=bank_day, basis=SettledDayBasisEnum.BORROWED)

    def test_a_correction_leaves_a_far_side_with_its_own_day(
        self, app, db, seed_user,
    ):
        """A far side the owner TYPED keeps its day when Checking's statement moves Checking.

        Declared change 2 of plan step ``balance:X-bi-6-4c-3``: a statement
        re-dates only its own side, and a side with evidence of its own does not
        follow (ruling **R-BAL142**).  The ``settled`` fixture states one day
        typed on both sides, the one-box popover's meaning (ruling R-BAL165).
        """
        transfer = self._a_transfer(db, seed_user, settled=True)
        far_side = self._record(db, transfer, transfer.to_account_id)
        typed_day = recorded_settle_day(far_side)
        assert typed_day.basis is SettledDayBasisEnum.ENTERED
        bank_day = seed_user["bootstrap_period"].start_date
        assert typed_day.day != bank_day
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=bank_day,
        )

        _submit(seed_user, lines=[line], transfers=[transfer])

        assert recorded_settle_day(
            self._record(db, transfer, transfer.from_account_id),
        ) == an_observed_day(bank_day)
        assert recorded_settle_day(far_side) == typed_day

    def test_a_shadow_whose_PARENT_is_gone_is_not_matchable(
        self, app, db, seed_user,
    ):
        """Not money this account owes, and pricing one asks about a gone row.

        Unreachable through today's doors -- ``delete_transfer(soft=True)``
        marks the transfer AND both shadows, and production carries 0 of these
        -- so this plants the state directly.  A scope that is correct only
        because every writer keeps a convention is a contract nobody can see.
        """
        from app.models.transfer import Transfer  # local: this test only

        transfer = self._a_transfer(db, seed_user)
        parent = db.session.get(Transfer, transfer.id)
        parent.is_deleted = True
        db.session.flush()
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-75.00",
            posted_on=seed_user["bootstrap_period"].start_date,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[line], transfers=[parent])


class TestAnAcceptedMatchStopsAgreeingWhenItStopsHolding:
    """``AcceptedGroup.agrees`` asks three questions, and a first draft asked one.

    **A CASCADE can falsify a match without touching a single day**, which is
    what an adversarial design review measured on 2026-08-17: deleting a
    purchase or destroying a pay period removes that member silently, and a
    day-only test then reports a group explaining less than it claims -- or,
    when every row goes, nothing at all -- as still agreeing with the bank.
    A SOFT delete was worse, because it does not cascade at all: the row kept
    its ``settled_on`` and contributed zero to every balance, so only the SUM
    could see it had gone.  No item can be hidden holding its payment since
    plan step ``balance:X-bi-6-4d-2`` (ruling **credit_card:R-CC75**, "Same
    as a one-off", for transfers too: the delete takes the payments off and
    withdraws the match it empties), so that case's test was retired
    (developer, rule 5, 2026-10-09); the SUM still answers for a purchase
    moved to a card (``TestAMatchStopsHoldingWhenAPURCHASELEAVESTHEACCOUNT``).
    """

    @staticmethod
    def _accepted_pair(db, seed_user):
        """Accept a two-row group and return its rows and the review set."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.38", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )
        _submit(seed_user, lines=[line], transactions=[salary, allowance])
        return salary, allowance

    def _groups(self, seed_user):
        """Return the accepted groups the screen would render."""
        return accepted_acts(seed_user)

    def test_it_agrees_while_it_holds(self, app, db, seed_user):
        """The control, without which every arm below could pass vacuously."""
        self._accepted_pair(db, seed_user)

        groups = self._groups(seed_user)

        assert len(groups) == 1
        assert groups[0].agrees is True

    def test_an_EXPENSE_row_agrees_while_it_holds(self, app, db, seed_user):
        """The same control on the kind plan step X-bi-3a covers.

        A settled bill's covering movement carries the money since that leaf,
        and the register read the row's own leg alone (``settled_cash_leg``,
        zero for a covered bill), so every accepted bill match reported
        itself as no longer holding, while the income control above stayed
        green (adversarial review, 2026-09-16).  The register prices the row
        at its covering movement now (``covered_cash_leg``, ruling
        **R-BAL81**), as the offer and the post-apply check do.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-148.32", posted_on=bank_day,
        )
        bill = a_transaction(seed_user, name="Electric", amount="148.32")
        _submit(seed_user, lines=[line], transactions=[bill])

        groups = self._groups(seed_user)

        assert len(groups) == 1
        assert groups[0].agrees is True

    def test_a_hand_moved_day_stops_it_agreeing(self, app, db, seed_user):
        """The owner contradicted the bank, and the screen says so.

        Moved through the row's own door, which mirrors the day onto the
        covering movement the act names (plan step ``credit_card:CC-5-4a-1``,
        ruling **R-CC43**); this wrote the row's day pair directly through
        ``CC-5-3``, when the act named the row.
        Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions
        under R-CC43 -- the member names the payment; a day moves through the seam that mirrors it;
        a settled row is ticked as its payment."
        """
        salary, _ = self._accepted_pair(db, seed_user)
        transaction_service.apply_requested_status(
            salary, salary.status_id,
            settle_day=an_entered_day(salary.settled_on + timedelta(days=1)),
        )
        db.session.flush()

        assert self._groups(seed_user)[0].agrees is False


class TestAMatchStopsHoldingWhenAPURCHASELEAVESTHEACCOUNT:
    """A purchase flipped to CARD moves no cash through this account at all."""

    def test_flipping_a_matched_purchase_to_card_breaks_agreement(
        self, app, db, seed_user,
    ):
        """It keeps its day, so only the SUM can see it has gone.

        ``update_entry`` supports the flip and releases the clearing link for
        it; the purchase keeps ``settled_on`` throughout.  A valuation reading
        the magnitude alone therefore reported the match as still explaining
        the bank's figure while the money had left through the card instead.
        Found by adversarial financial review 2026-08-17.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )
        _submit(seed_user, lines=[line], entries=[purchase])
        before = accepted_acts(seed_user)
        assert before[0].agrees is True

        purchase.is_credit = True
        db.session.flush()

        after = accepted_acts(seed_user)
        assert after[0].agrees is False
        assert after[0].rows[0].settled_on == bank_day, (
            "the day is untouched -- which is why a day-only test was blind"
        )



class TestItCorrectsThePurchaseDayTheBankContradicts:
    """Ruling **R-FW**: the bank owns BOTH of a purchase's days.

    A purchase carries the day it was MADE (``purchased_on``, the budget clock)
    beside the day the bank TOOK the money (``settled_on``, the cash clock).
    Accepting a match asserts that this bank line IS this purchase -- which
    asserts the purchase was made on or before the day the line posted.  A
    recorded day after that is refuted by the owner's own act, so it moves.

    **Measured, and it is why the step exists.**  On the developer's own
    2026-08-16 statement against a production clone, 14 unexplained bank lines
    worth `$1,028.66` were an exact amount at the same merchant as an unmatched
    purchase, and were blocked from being proposed only because the purchase
    had been recorded 1 to 5 days after the bank posted it -- six of them typed
    in one bookkeeping session on 2026-04-29 for swipes posted 04-24 and 04-27.
    The create-a-purchase door X-f6a-3b builds would have offered to record
    every one of them a SECOND time.

    **The narrowness is measured too.**  Taking the bank's day unconditionally
    would move 27 of the 44 purchases in today's proposals, 18 of them onto a
    CLEARING day, because the source states no transaction day on 179 of 361
    lines.  Correcting only what the bank contradicts moves 3.
    """

    @staticmethod
    def _envelope_and_purchase(seed_user, purchased_on):
        """Return an envelope and one unposted `$25.00` purchase under it."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        return envelope, a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=purchased_on,
        )

    def test_a_purchase_recorded_AFTER_the_posting_day_is_re_dated(
        self, app, db, seed_user,
    ):
        """The refuted day moves back onto the day the bank posted.

        This is the six-in-one-session shape: the money left on the bank's day
        and the purchase was typed days later, so the recorded purchase day is
        an impossibility rather than a disagreement.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        _, purchase = self._envelope_and_purchase(
            seed_user, bank_day + timedelta(days=5),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == bank_day
        assert purchase.settled_on == bank_day

    def test_it_takes_the_day_the_bank_STATED_over_the_day_it_cleared(
        self, app, db, seed_user,
    ):
        """Two different facts, and the swipe day is the one a purchase wants.

        SECU states the swipe day inside a card line's description on 182 of
        361 lines: 157 of them 1 to 4 days before it posts, and 25 on the
        posting day itself.  Writing the posting day instead would record the
        purchase as made on the day it cleared.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        swipe_day = bank_day - timedelta(days=2)
        _, purchase = self._envelope_and_purchase(
            seed_user, bank_day + timedelta(days=5),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=swipe_day,
        )

        _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == swipe_day
        assert purchase.settled_on == bank_day

    def test_a_purchase_the_bank_does_NOT_contradict_keeps_its_day(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL for the narrowness, and it is the whole ruling.

        A purchase recorded BEFORE the bank posted it is not refuted by
        anything, so the day the owner typed stands -- even though the bank
        states a day of its own.  Without this arm the door would overwrite 27
        of 44 correct dates on the developer's own statement in order to fix 3
        wrong ones.  Delete the ``expected_on <= posts_on`` test in
        ``corrected_purchase_day`` and this fails.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        made_on = bank_day - timedelta(days=4)
        _, purchase = self._envelope_and_purchase(seed_user, made_on)
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=1),
        )

        _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == made_on
        assert purchase.settled_on == bank_day

    def test_a_TRANSACTION_has_no_purchase_day_to_correct(
        self, app, db, seed_user,
    ):
        """Only a purchase carries the second clock.

        A transaction's ``settled_on`` is its only date column -- what says
        when it was budgeted is its pay period -- so the correction must not
        leak onto the other kind.  ``update_entry`` is the only door that could
        write it, and a transaction never reaches one.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        row = a_transaction(seed_user, name="Electricity", amount="180.00")
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=3),
        )

        accepted = _submit(seed_user, lines=[line], transactions=[row])

        assert row.settled_on == bank_day
        assert accepted.settled_count == 1

    def test_a_GROUP_re_dates_to_its_EARLIEST_stated_day(
        self, app, db, seed_user,
    ):
        """Earliest for the purchase day, latest for the posting day.

        A purchase the bank split across several lines was made no later than
        the first of them, while the row is not wholly moved until the last one
        posts -- so the two ends of :class:`MatchDays` are opposite ends on
        purpose, and a single ``max`` for both would date the purchase after
        the swipe that started it.
        """
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        last_day = first_day + timedelta(days=3)
        _, purchase = self._envelope_and_purchase(
            seed_user, last_day + timedelta(days=5),
        )
        early = a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=first_day,
            transaction_on=first_day - timedelta(days=1),
        )
        late = a_bank_line(
            seed_user, statement, amount="-15.00", posted_on=last_day,
            transaction_on=last_day - timedelta(days=1),
        )

        accepted = _submit(seed_user, lines=[early, late], entries=[purchase])

        assert purchase.purchased_on == first_day - timedelta(days=1)
        assert purchase.settled_on == last_day
        assert accepted.posts_on == last_day

    def test_a_re_dated_purchase_already_settled_counts_as_CORRECTED(
        self, app, db, seed_user,
    ):
        """A day moved is work done, whichever of the two columns moved.

        A settled purchase already carrying the bank's posting day but a
        refuted purchase day is not "unchanged": the door writes to it, so
        reporting it as untouched would claim the opposite of what happened.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=9)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=2),
            settled_on=bank_day + timedelta(days=2),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == bank_day
        assert purchase.settled_on == bank_day
        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0


class TestCorrectingAPurchaseDayMovesNoMoney:
    """The financial property ruling **R-FW** rests on, isolated by DIFFERENCE.

    A purchase carries two clocks and only ONE is cash.  ``settled_on`` is what
    :func:`~app.services.cash_ledger.posted_purchase_sum` buckets on and what
    the walk emits a dated leg for; ``purchased_on`` is the BUDGET clock, read
    outside the write doors only by ``reconcile_service``'s offer set and by
    the grid's out-of-period warning.

    **A purchase-day-ONLY correction cannot be built, which is why this is a
    differential.**  ``corrected_purchase_day`` fires only when the recorded
    day is after the bank's, and ``ck_transaction_entries_settled_not_before_
    purchase`` then forces the settle day to move too -- so every correction
    is accompanied by a cash write, and a first draft of this class asserted a
    hand-computed allow-set around it that passed vacuously.  Found by
    adversarial test-quality review 2026-08-18, which proposed this shape.

    **The shape:** two fixtures identical in every way that touches cash --
    same amount, same envelope, same bank line, so the same ``posts_on`` --
    differing only in whether the purchase day is REFUTED.  One is re-dated and
    one is not.  If ``purchased_on`` were a cash input the two daily balance
    series would diverge; the assertion is that they are identical.
    """

    @staticmethod
    def _series(seed_user, lo, hi):
        """Return the checking account's daily balance over ``[lo, hi]``."""
        out, day = {}, lo
        while day <= hi:
            ctx = BalanceContext(
                user_id=seed_user["user"].id,
                scenario=seed_user["scenario"], as_of=day,
            )
            out[day] = balance_at.balance_at(seed_user["account"], ctx, day)
            day += timedelta(days=1)
        return out

    @staticmethod
    def _accept_one(seed_user, made_on, bank_day, name):
        """Accept a match for one `$25.00` purchase made on *made_on*."""
        statement = an_import(seed_user)
        envelope = a_transaction(
            seed_user, name=name, amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=made_on,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )
        return purchase, _submit(seed_user, lines=[line], entries=[purchase])

    def test_a_re_dated_purchase_books_what_an_un_re_dated_one_books(
        self, app, db, seed_user,
    ):
        """The difference between the two runs is the purchase day and nothing.

        Delete the ``expected_on <= posted_first`` test so that BOTH are
        re-dated and this still passes -- correctly, because the property is
        that the budget clock is not cash, not that the correction is rare.
        Make ``purchased_on`` a cash input and it fails.
        """
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=9)
        lo, hi = bank_day - timedelta(days=6), bank_day + timedelta(days=12)

        refuted, accepted_a = self._accept_one(
            seed_user, bank_day + timedelta(days=4), bank_day, "Groceries A",
        )
        db.session.flush()
        with_correction = self._series(seed_user, lo, hi)

        assert refuted.purchased_on == bank_day, "the budget clock did not move"
        assert accepted_a.redated_count == 1

        db.session.rollback()

        unrefuted, accepted_b = self._accept_one(
            seed_user, bank_day - timedelta(days=4), bank_day, "Groceries B",
        )
        db.session.flush()
        without_correction = self._series(seed_user, lo, hi)

        assert unrefuted.purchased_on == bank_day - timedelta(days=4)
        assert accepted_b.redated_count == 0
        assert with_correction == without_correction, (
            "the purchase day moved a balance: "
            f"{ {d: (with_correction[d], without_correction[d])
                 for d in with_correction
                 if with_correction[d] != without_correction[d]} }"
        )

    def test_the_window_can_SEE_a_money_move(self, app, db, seed_user):
        """THE POSITIVE CONTROL, without which the test above proves nothing.

        A null result over a window is worth what the window's sensitivity is
        worth.  This moves the CASH clock on the same helper over the same
        window and asserts the series changes by the purchase's own amount, so
        a harness that had gone blind fails here rather than reporting
        agreement forever.
        """
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=9)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day, settled_on=bank_day,
        )
        lo, hi = bank_day - timedelta(days=5), bank_day + timedelta(days=12)
        before = self._series(seed_user, lo, hi)

        entry_service.update_entry(
            purchase.id, seed_user["user"].id,
            settle_day=an_entered_day(bank_day + timedelta(days=4)),
        )
        db.session.flush()

        after = self._series(seed_user, lo, hi)
        moved = {d for d in before if before[d] != after[d]}
        assert moved, "the window cannot see a cash move, so it grades nothing"
        assert {after[d] - before[d] for d in moved} == {Decimal("25.00")}


class TestWhatRefutesAPurchaseDayIsTheEARLIESTLine:
    """A group's two ends are opposite, and so are the days they answer.

    ``posts_on`` is the LATEST bank day, because a row is not wholly moved
    until its last line posts.  But what REFUTES a recorded purchase day is the
    EARLIEST: money cannot leave before it is spent, so a purchase explained by
    lines posted 06-01 and 06-10 was made on or before 06-01.

    Testing the purchase day against ``posts_on`` let an impossibility stand --
    a purchase recorded 06-05 is impossible against the 06-01 line, and
    ``update_entry``'s own check compares it against the LATEST day, so nothing
    else would catch it either.  Found by adversarial design review 2026-08-18.
    """

    def test_a_purchase_after_the_EARLIEST_line_is_corrected(
        self, app, db, seed_user,
    ):
        """It is refuted by the first line even though the last one covers it."""
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        last_day = first_day + timedelta(days=9)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=first_day + timedelta(days=4),
        )
        early = a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=first_day,
        )
        late = a_bank_line(
            seed_user, statement, amount="-15.00", posted_on=last_day,
        )

        accepted = _submit(seed_user, lines=[early, late], entries=[purchase])

        assert purchase.purchased_on == first_day
        assert purchase.settled_on == last_day
        assert accepted.redated_count == 1


class TestTheReceiptNamesAPurchaseDayCorrection:
    """A re-dated purchase that was still Projected reported only "settled".

    ``AcceptedMatch`` partitions the rows it changed into settled and
    corrected, and an unsettled purchase lands in the first -- so a
    purchase-day correction on one appeared in NEITHER count, and the flash
    said only "marked 1 row(s) as having happened".  That is the step's own
    motivating case: the six purchases typed in one bookkeeping session carry
    no settle day at all.  Found by two independent adversarial reviews
    2026-08-18.
    """

    def test_an_unsettled_purchase_that_is_re_dated_is_counted(
        self, app, db, seed_user,
    ):
        """It counts as settled AND as re-dated, because it was both."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=5),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert accepted.settled_count == 1
        assert accepted.redated_count == 1, (
            "a purchase day moved and the receipt does not say so"
        )

    def test_a_match_that_moves_no_purchase_day_reports_none(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL: the count is not simply the purchase count."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day - timedelta(days=2),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert accepted.settled_count == 1
        assert accepted.redated_count == 0


class TestTheStoredTransactionDayReachesTheScreen:
    """The DB -> screen wiring, which nothing graded.

    Every proposer test hand-builds a :class:`BankLine`, so the path
    ``bank_statement_lines.transaction_on`` -> ``as_bank_line`` ->
    :attr:`MatchProposal.made_on` was exercised by nothing: deleting the copy
    in the reader left the whole suite green.  The consequence is the exact
    divergence :attr:`MatchProposal.days` says it exists to prevent -- the
    screen promising the CLEARING day while the door writes the STATED one.
    Found by adversarial test-quality review 2026-08-18.
    """

    def test_the_screen_offers_the_day_the_bank_STATED(
        self, app, db, seed_user,
    ):
        """Through ``review_set``, not through a hand-built value."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        swipe_day = bank_day - timedelta(days=2)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=3),
        )
        # A second purchase, so the ENVELOPE does not price at the same figure
        # as the one being matched -- otherwise the proposer offers the parent
        # and the purchase never reaches the assertion.
        a_purchase(
            seed_user, envelope, amount="31.00", description="Aldi",
            purchased_on=bank_day,
        )
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=swipe_day,
        )

        review = statement_match.review_set(a_scope(seed_user))

        offered = [p for p in review.proposals if p.redated_purchases]
        assert len(offered) == 1
        assert offered[0].made_on == swipe_day, (
            "the screen offers a day the reader did not carry through"
        )
        assert offered[0].redate_gap == 5

    def test_a_proposal_from_the_screen_is_ACCEPTABLE(
        self, app, db, seed_user,
    ):
        """The end-to-end claim "0 proposals whose Accept can never succeed".

        Nothing took a proposal from :func:`propose` and fed it to the accept
        door, so that claim was graded by no test at all.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=3),
        )
        a_purchase(
            seed_user, envelope, amount="31.00", description="Aldi",
            purchased_on=bank_day,
        )
        a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=1),
        )
        review = statement_match.review_set(a_scope(seed_user))
        assert review.proposals

        for proposal in review.proposals:
            statement_match.accept_match(
                MatchSubmission(
                    line_ids=frozenset(l.line_id for l in proposal.lines),
                    rows=frozenset(
                        statement_match.as_reviewed(r) for r in proposal.rows
                    ),
                ),
                a_scope(seed_user),
            )


class TestTheNarrownessBoundary:
    """`expected_on <= posted_first` is where the 27-of-44 over-correction begins.

    A purchase recorded EXACTLY on the day the bank posted it is not refuted by
    anything, and turning that ``<=`` into ``<`` would drag it backwards onto
    the stated swipe day -- which is the behaviour the ruling measured and
    rejected.  The nearest existing test sat seven days off the boundary.
    Found by adversarial test-quality review 2026-08-18.
    """

    def test_a_purchase_made_ON_the_posting_day_is_left_alone(
        self, app, db, seed_user,
    ):
        """Equal is not contradicted."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=2),
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == bank_day
        assert accepted.redated_count == 0

    def test_a_purchase_made_ONE_DAY_after_it_is_corrected(
        self, app, db, seed_user,
    ):
        """The first refuted value, so the boundary is pinned from both sides."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=bank_day + timedelta(days=1),
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
            transaction_on=bank_day - timedelta(days=2),
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.purchased_on == bank_day - timedelta(days=2)
        assert accepted.redated_count == 1

    def test_a_GROUP_whose_lines_MIX_stated_and_unstated_days(
        self, app, db, seed_user,
    ):
        """``happened_on`` falls back per LINE, not per match.

        Both lines in the existing group test state a day, so nothing graded
        the mix -- which is the ordinary case, since the source states one on
        182 of 361 lines.
        """
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        last_day = first_day + timedelta(days=6)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=last_day + timedelta(days=2),
        )
        a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=first_day,
        )
        stated = a_bank_line(
            seed_user, statement, amount="-15.00", posted_on=last_day,
            transaction_on=last_day - timedelta(days=1),
        )
        early = db.session.query(type(stated)).filter_by(
            amount=Decimal("-10.00"),
        ).one()

        _submit(seed_user, lines=[early, stated], entries=[purchase])

        # The earlier line states nothing, so it falls back to its posting day
        # -- and that is earlier than the later line's stated day.
        assert purchase.purchased_on == first_day


class TestARowCarryingTheBanksDayIsStillWrittenIfItsPurchaseDayMoves:
    """"Unchanged" asks about BOTH clocks, and only a group can prove it.

    :func:`_apply_day` returns early on ``unchanged`` without writing, so a row
    already settled on the bank's day and needing a purchase-day correction
    must not take that arm.

    **The conjunct that says so was DEAD when it was written, and the fix to
    another finding made it live.**  For a single line, ``settled_on ==
    posts_on`` plus ``ck_transaction_entries_settled_not_before_purchase``
    forces ``purchased_on <= posts_on``, so no correction was possible and the
    test could not be built.  Once what REFUTES a purchase day became the
    EARLIEST posted day rather than the latest, a group separates them: settled
    on the LAST line's day, made after the FIRST line's.  Found by adversarial
    test-quality review 2026-08-18, which measured the conjunct as unreachable
    and predicted this case.
    """

    def test_it_is_written_and_reported_as_corrected(
        self, app, db, seed_user,
    ):
        """Delete ``and purchase_day is None`` and the purchase day stops moving."""
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        last_day = first_day + timedelta(days=9)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00",
            purchased_on=first_day + timedelta(days=4),
            settled_on=last_day,
        )
        early = a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=first_day,
        )
        late = a_bank_line(
            seed_user, statement, amount="-15.00", posted_on=last_day,
        )

        accepted = _submit(seed_user, lines=[early, late], entries=[purchase])

        assert purchase.settled_on == last_day, "the cash clock must not move"
        assert purchase.purchased_on == first_day, "the budget clock did not move"
        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0
        assert accepted.redated_count == 1


class TestARedatedSettleLeavesNoPostingBehind:
    """Finding **N-324**: every day assertion here had no posting to MOVE.

    ``_builders.a_transaction`` writes ``status_id`` and ``settled_on``
    straight through the ORM and the covering movement through the seam's
    own writer, and that route is DELIBERATE -- a broken settle verb must not
    also break the
    fixture that would have caught it (the builders' own module docstring).
    **What nobody stated was the consequence.**  Those rows carry no ledger
    postings, so ``_apply_day`` reaches ``apply_requested_status``, which
    reconciles and re-posts at the new day -- and a defect that left the OLD
    day's posting behind, the classic double-count on a re-dated settle, was
    invisible to every case in this file.  X-f6a-3c-2's batch-versus-per-act
    control compares postings between the two paths and so could not see an
    error common to both.

    **The remedy is a fixture built through the REAL settle door**
    (``tests._test_helpers.create_settled_cash_transaction``, which drives
    ``status_seam.apply_status_change`` and
    ``posting_service.sync_transaction_postings`` in the order the mark-done
    route does), and an assertion about the OLD day rather than only the new
    one.  It sits beside the ORM-built cases rather than replacing them,
    because the two grade different things.

    **ONLY THE LEDGER CASE BELOW CATCHES A LEFTOVER POSTING, and that is worth
    stating because it is the opposite of what one would assume.**  Planting
    the defect ruling **R-FA** exists to prevent -- a matcher that stamps
    ``settled_on`` itself instead of going through the status door, so the old
    day's posting stands and no new one is written -- fails
    ``test_the_rows_cash_NETS_to_the_banks_day_alone`` and leaves
    ``test_the_OLD_days_balance_stops_carrying_the_row`` GREEN.  The balance
    producer reads a row's ``settled_on``, not the journal, so a stale posting
    moves no balance and no balance assertion can see one.  A class that
    grades this defect with a balance alone grades nothing.
    """

    def test_the_OLD_days_balance_stops_carrying_the_row(
        self, app, db, seed_user,
    ):
        """MONEY: the figure the app PUBLISHES moves to the bank's day.

        The row settles on the day the app guessed.  The bank says it moved
        four days later, so the earlier day must stop carrying it and the later
        day must carry it exactly once -- the account would otherwise be short
        by this amount on every day between the two.

        **This is the PRODUCER's half and it cannot see a stale posting** (see
        the class docstring): ``balance_at`` reads ``settled_on``, so the
        sibling case below is what grades the ledger.  Both are here because a
        defect can move one without the other.
        """
        period = seed_user["bootstrap_period"]
        guessed = period.start_date + timedelta(days=1)
        bank_day = guessed + timedelta(days=4)
        txn = create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("180.00"),
            settled_on=guessed, name="Electricity",
        )
        db.session.flush()
        before_at_guessed = _balance_on(seed_user, guessed)
        before_at_bank_day = _balance_on(seed_user, bank_day)

        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
        )
        _submit(seed_user, lines=[line], transactions=[txn])
        db.session.flush()

        db.session.refresh(txn)
        assert txn.settled_on == bank_day
        # The old day RELEASES it...
        assert _balance_on(seed_user, guessed) == (
            before_at_guessed + Decimal("180.00")
        )
        # ...and the bank's day still carries it, exactly once.
        assert _balance_on(seed_user, bank_day) == before_at_bank_day

    def test_the_rows_cash_NETS_to_the_banks_day_alone(
        self, app, db, seed_user,
    ):
        """The same fact read from the LEDGER rather than from a balance.

        A balance is one sum over everything, so a leftover posting here and a
        missing one there can cancel inside it; the journal, read per day and
        per account, cannot hide either.  **The ledger is append-only**, so
        what says the money moved is that the old day nets to zero -- measured,
        a correction writes a reversal at the old day and a posting at the new,
        three entries over two days.
        """
        period = seed_user["bootstrap_period"]
        guessed = period.start_date + timedelta(days=1)
        bank_day = guessed + timedelta(days=4)
        txn = create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("180.00"),
            settled_on=guessed, name="Electricity",
        )
        db.session.flush()
        account = seed_user["account"]
        assert _posted_cash_by_day(db, txn, account) == {
            guessed: Decimal("-180.00"),
        }

        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
        )
        _submit(seed_user, lines=[line], transactions=[txn])
        db.session.flush()

        assert _posted_cash_by_day(db, txn, account) == {
            bank_day: Decimal("-180.00"),
        }


class TestTheBankCanCONFIRMADayThePanelOnlyBOUNDED:
    """Plan step **X-az**: a confirmation writes the basis, not the day.

    **The reverse-direction half of finding N-332.**  The reconcile panel stamps
    the day the owner asserted a BALANCE for -- the money moved on or BEFORE it.
    When a bank line then posts on exactly that day the app has learned
    something: the bound is the true posting day.  Nothing could record it,
    because ``_apply_day``'s ``"unchanged"`` arm returned early and no settle
    door fires when the day does not move, so such a row went on reporting
    itself a bound forever.

    **The DAY is unchanged, so the receipt is unchanged**: neither
    ``settled_count`` nor ``corrected_count`` moves, because nothing settled and
    nothing was corrected.  What moves is the stored answer to *how is this day
    known*, and with it the window the matcher would bound a future line by.
    """

    @staticmethod
    def _reconciled_purchase(seed_user, made_on, asserted_for):
        """Return a purchase ticked on the panel: an ASSERTED day and a link."""
        assertion = an_assertion(seed_user, observed_on=asserted_for)
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        return a_purchase(
            seed_user, envelope, amount="18.64", purchased_on=made_on,
            settled_on=asserted_for, reconciled_by=assertion,
            settle_day_basis=SettledDayBasisEnum.ASSERTED,
        )

    def test_a_line_on_the_bound_itself_raises_the_basis_to_observed(
        self, app, db, seed_user,
    ):
        """The confirmation: same day, stronger evidence, recorded."""
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            asserted_for = made_on + timedelta(days=20)
            purchase = self._reconciled_purchase(
                seed_user, made_on, asserted_for,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-18.64", posted_on=asserted_for,
            )
            db.session.flush()

            accepted = _submit(seed_user, lines=[line], entries=[purchase])
            db.session.flush()

            assert recorded_settle_day(purchase) == SettleDay(
                day=asserted_for, basis=SettledDayBasisEnum.OBSERVED,
            )
            # The DAY did not move, so neither tally counts it.
            assert accepted.settled_count == 0
            assert accepted.corrected_count == 0

    def test_the_confirmation_KEEPS_the_clearing_link(
        self, app, db, seed_user,
    ):
        """A statement that AGREES does not withdraw the one already seen.

        Every settle door releases ``reconciled_by_id`` when the day MOVES, and
        the predicate is about the day rather than the pair for exactly this
        case: an observation the bank confirms strengthens the record the link
        holds instead of contradicting it.
        """
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            asserted_for = made_on + timedelta(days=20)
            purchase = self._reconciled_purchase(
                seed_user, made_on, asserted_for,
            )
            linked_to = purchase.reconciled_by_id
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-18.64", posted_on=asserted_for,
            )
            db.session.flush()

            _submit(seed_user, lines=[line], entries=[purchase])
            db.session.flush()

            assert purchase.reconciled_by_id == linked_to

    def test_a_row_already_OBSERVED_is_left_entirely_alone(
        self, app, db, seed_user,
    ):
        """The early return survives: only a WEAKER basis is worth writing.

        Without this arm the confirmation runs a full settle door on every
        already-correct row of every accept -- a posting reconcile, a payback
        sync and an optimistic-lock bump apiece, for a column that is already
        right.
        """
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            bank_day = made_on + timedelta(days=2)
            envelope = a_transaction(
                seed_user, name="Groceries", amount="100.00", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="18.64", purchased_on=made_on,
                settled_on=bank_day,
                settle_day_basis=SettledDayBasisEnum.OBSERVED,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-18.64", posted_on=bank_day,
            )
            db.session.flush()
            before = purchase.version_id

            _submit(seed_user, lines=[line], entries=[purchase])
            db.session.flush()

            assert purchase.version_id == before, (
                "an already-observed row was written for nothing"
            )
            assert recorded_settle_day(purchase).basis is (
                SettledDayBasisEnum.OBSERVED
            )


class TestAOneToOneMatchTakesTheBanksFigure:
    """Ruling **R-GD(a)** and finding **N-335**: the bank's figure IS the record.

    **The defect these exist for, measured on the developer's own dev database
    2026-08-22.**  Bank line 285 (`ACH DEBIT GEICO PREM COLL`, `-178.29`,
    posted 07-02) sat THREE CENTS from transaction 2461 (`178.32`, settled
    07-06).  The exact-amount predicate offered nothing, the accept door would
    have refused it anyway, and the cheapest act the review screen had left was
    to record the line as a NEW purchase -- so the ledger booked `-178.29` on
    07-02 AND `-178.32` on 07-06, `$356.61` for one `$178.29` movement.

    A refusal is not neutral when the screen beside it offers to duplicate.

    **A case left this class at plan step ``balance:X-bi-4a``**:
    ``test_a_row_carrying_a_CARD_purchase_corrects_its_GROSS`` built a
    ``derived`` bill holding a card purchase and graded that the bank's
    figure corrected the row's GROSS with the card purchase still subtracted.
    Ruling **R-BAL78** makes a stated figure beside purchases unrepresentable
    (a row holding purchases records its money AS them; the settle verb and
    the seam refuse a figure over them, and the entry doors refuse a purchase
    on a stored-figure row -- ``test_entry_service`` grades both), so the
    row that case corrected cannot exist and the case had no subject left.
    What a card purchase does to a row's cash leg is the card arc's
    (``credit_card:CC-5``), on the card's own account.
    """

    @staticmethod
    def _geico(seed_user, statement, bank_day, app_day):
        """Stage the developer's own case: a bill settled three cents off."""
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=app_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-178.29", posted_on=bank_day,
        )
        return txn, line

    def test_the_three_cents_is_a_CORRECTION_not_a_refusal(
        self, app, db, seed_user,
    ):
        """The whole finding, as one accepted match."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        txn, line = self._geico(
            seed_user, statement, bank_day, bank_day + timedelta(days=4),
        )

        # Stating 0.03 is what -178.29 bank against a -178.32 row comes to, and every
        # match carries the difference it was reviewed against since
        # plan step bank_import:X-gj-1b -- the near tier's own card
        # renders it as a hidden field (the stated_difference filter).
        accepted = _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )

        assert accepted.match_id is not None, "the match must be RECORDED"
        assert settled_figure(txn) == Decimal("178.29"), (
            "the row must book what the BANK took, not what the app guessed"
        )
        assert txn.settled_on == bank_day

    def test_the_correction_says_it_is_one(self, app, db, seed_user):
        """A corrected figure is stored as STATED, not as resolved.

        The movement's source is what makes *did the bank disagree with this
        row* a stored answer instead of one re-derived by comparing against a
        recomputation that may since have moved.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        txn, line = self._geico(
            seed_user, statement, bank_day, bank_day + timedelta(days=4),
        )

        # Stating 0.03 is what -178.29 bank against a -178.32 row comes to, and every
        # match carries the difference it was reviewed against since
        # plan step bank_import:X-gj-1b -- the near tier's own card
        # renders it as a hidden field (the stated_difference filter).
        _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )

        assert status_seam.recorded_settlement(txn).stated
        # And WHO wrote it: the matcher's transaction arm is the one door
        # that states a figure as the BANK's (plan step X-bi-3e-1, ruling
        # R-BAL61), graded end to end on the row's covering movement --
        # ``typed`` would also read as stated, so the reading alone cannot
        # tell the two writers apart.
        (movement,) = txn.covering_movements
        assert movement.amount == Decimal("178.29")
        assert movement.figure_source_id == ref_cache.movement_figure_source_id(
            MovementFigureSourceEnum.OBSERVED,
        )

    def test_a_settled_PAYCHECK_five_cents_off_is_corrected_the_same_way(
        self, app, db, seed_user,
    ):
        """The income arm of finding BAL-523, on the developer's own payroll line.

        A settled paycheck is worth its covering movement in its own direction
        (``+figure``, ruling **R-BAL81**), and the bank's figure corrects it
        exactly as it corrects a bill: the record and the movement both read
        the bank's `$2,473.43`.  Through plan step ``balance:X-bi-4a``'s
        first cut ``_landing.corrected_figure`` added ``off_statement_sum``,
        whose posted-purchase term walked the FAMILY and summed the paycheck's
        own dated covering movement as a purchase that had posted, so the
        correction booked `2,473.43 + 2,473.38 = 4,946.81` onto the movement
        and the post-apply check refused the act -- production's payroll
        line 259 against rows 1625 + 787, 2026-09-18 23:15 UTC, in the
        observer's record.  The bill cases above cover the expense arm; this
        is the control for the income one, and it is RED on the walk over
        ``entries``.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        paycheck = a_transaction(
            seed_user, name="Payroll", amount="2473.38", income=True,
            status=StatusEnum.RECEIVED, settled_on=bank_day,
        )
        [movement] = paycheck.covering_movements
        assert movement.amount == Decimal("2473.38")
        line = a_bank_line(
            seed_user, statement, amount="2473.43", posted_on=bank_day,
        )

        accepted = _submit(
            seed_user, lines=[line], transactions=[paycheck], residual="0.05",
        )

        assert accepted.match_id is not None
        assert status_seam.recorded_settlement(paycheck) == status_seam.Settlement(
            Decimal("2473.43"), MovementFigureSourceEnum.OBSERVED,
        )
        assert [m.amount for m in paycheck.covering_movements] == [
            Decimal("2473.43"),
        ]
        assert status_seam.covered_cash_leg(paycheck, paycheck.account_id) == Decimal("2473.43")

    def test_an_AGREEING_match_writes_no_correction(self, app, db, seed_user):
        """The control, and it is what makes the test above mean anything.

        A row the bank agrees with must keep its DERIVED basis: if every match
        wrote ``corrected``, the basis would say nothing and the assertion
        above would pass for the wrong reason.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-178.32", posted_on=bank_day,
        )

        _submit(seed_user, lines=[line], transactions=[txn])

        assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
            Decimal("178.32"), MovementFigureSourceEnum.RESOLVED,
        )

    def test_the_ACCOUNT_reads_the_banks_figure_and_not_both(
        self, app, db, seed_user,
    ):
        """The money assertion: one movement, booked once, at the bank's figure.

        This is the property finding **N-335** measures the loss of -- the app
        held `$356.61` against a `$178.29` payment because the line became a
        second row instead of correcting the first.

        **It asserts the POSTED LEG rather than the balance, and that is not
        laziness.**  The bank day here is the anchor's own day, and until the
        cutover (`balance:X-f3c`) an assertion RESETS the ledger to the figure
        it names -- so `balance_at` reads `1000.00` either side of this match
        whatever the row books, and a balance assertion would grade the anchor
        instead of the correction.  What the ledger posted for the row is the
        fact this test is about, and it is exact.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        txn, line = self._geico(
            seed_user, statement, bank_day, bank_day + timedelta(days=4),
        )
        # Stating 0.03 is what -178.29 bank against a -178.32 row comes to, and every
        # match carries the difference it was reviewed against since
        # plan step bank_import:X-gj-1b -- the near tier's own card
        # renders it as a hidden field (the stated_difference filter).
        _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )
        db.session.flush()

        posted = _posted_cash_by_day(db, txn, seed_user["account"])
        assert sum(posted.values()) == Decimal("-178.29"), (
            "the row must book what the BANK took, exactly once -- the whole "
            "of what N-335 measures the loss of"
        )


class TestWhatAOneToOneMatchSTILLRefuses:
    """The four indeterminacies R-GD(a) did NOT dissolve.

    Each is a FIRING CONTROL: written to fail if its clause were deleted, which
    is what `docs/plans/verification.md` asks of a refusal on a money door.
    """

    def test_a_SIGN_disagreement_is_refused(self, app, db, seed_user):
        """Money leaving is not money arriving, whatever the magnitudes do.

        The old sum test caught this by accident; with the sum test gone it
        needs its own clause, or a `+180.00` deposit would silently "correct" a
        `-180.00` bill.
        """
        statement = an_import(seed_user)
        txn = a_transaction(seed_user, name="Electricity", amount="180.00")
        line = a_bank_line(seed_user, statement, amount="180.00")

        with pytest.raises(ValidationError, match="not the same movement"):
            _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on is None

    def test_an_ENVELOPE_is_refused(self, app, db, seed_user):
        """Its figure IS its purchases, so it is not offered at all.

        Through plan step ``balance:X-bi-4a``'s first cut the row reached
        ``_reject_uncorrectable_row``'s "no figure of its own" clause; under
        ruling **R-BAL81** a row that settles from its purchases is worth
        ``0`` and is not a candidate, so it is refused as unavailable one
        refusal earlier.  That clause's remaining subject is the CC payback,
        the next case.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        db.session.flush()
        line = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=bank_day,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[line], transactions=[envelope])

        assert envelope.settled_on is None

    def test_a_CC_PAYBACK_is_refused(self, app, db, seed_user):
        """Finding **N-252**'s class, and the one a first draft MISSED.

        A payback's figure is a fact about the row it repays, and
        ``entry_credit_workflow.sync_entry_payback`` re-states it on every entry
        mutation -- so a ``corrected`` record written here is silently reverted
        by the next sibling write.  The transaction door's own backstop refuses
        only the ENVELOPE half of this class (the payback is refused at the
        PATCH route instead), so without this clause the correction reaches the
        column.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        payback = Transaction(
            account_id=seed_user["account"].id,
            user_id=seed_user['bootstrap_period'].user_id,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name="CC Payback: Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            amount_ownership=AmountOwnership.own(Decimal("60.00")),
            credit_payback_for_id=envelope.id,
        )
        db.session.add(payback)
        db.session.flush()
        line = a_bank_line(
            seed_user, statement, amount="-55.00", posted_on=bank_day,
        )

        with pytest.raises(ValidationError, match="no figure of its own"):
            _submit(seed_user, lines=[line], transactions=[payback])

        assert payback.settled_on is None
        assert status_seam.recorded_settlement(payback) is None


class TestASettledPurchaseTakesTheBanksFigure:
    """Ruling **R-GE** (2026-08-22): the same evidence R-FX already accepted.

    ``entry_service._reject_settled_parent`` refuses a purchase's ``amount``
    under a settled parent -- finding **N-229**, widened to the settled BAND at
    `balance:X-au-c3` -- because re-pricing a row whose money has moved would
    move it again.  That reason holds for the act it was written about: a human
    typing a different number on their own second thoughts.

    **A bank line is not that act**, which is the argument R-FX accepted one
    door over when it ruled that the same evidence justifies ADDING a purchase
    to a settled row.  2 of the developer's own 10 near misses are exactly this
    -- `Groceries: Walmart`, `$121.12` against the bank's `$121.16`.

    **The BOUND is the door, not the row**, and the second test is its firing
    control: the permission rides on the figure's own stated SOURCE -- the
    matcher states ``observed`` with the figure (plan step X-bi-3e-1, ruling
    **R-BAL69**; it rode on the settle day's basis until then) -- so a caller
    without a statement cannot reach it.
    """

    @staticmethod
    def _settled_envelope_with_a_purchase(seed_user, day):
        """Stage an envelope CLOSED FROM one purchase the bank will correct.

        Through the verb, so the close is a ``purchases`` record with no
        covering movement (ruling **R-BAL78**: a stated figure beside
        purchases is unrepresentable) -- the state the developer's two
        near misses are in.  Laid on bare with ``settled_on`` this was a
        ``derived`` close holding a purchase, and once ``a_transaction``
        covered its settled rows, a mirror beside the purchase as well.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="121.12", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="121.12", description="Walmart",
            purchased_on=day,
        )
        transaction_service.settle_transaction(
            envelope, settle_day=an_entered_day(day),
        )
        db.session.flush()
        assert envelope.covering_movements == []
        return envelope, purchase

    def test_the_purchase_is_RECOSTED_from_a_match(self, app, db, seed_user):
        """The developer's own Walmart case, four cents out."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)
        line = a_bank_line(
            seed_user, statement, amount="-121.16", posted_on=day,
        )

        # Stating -0.04 is what -121.16 bank against a -121.12 purchase comes to, and every
        # match carries the difference it was reviewed against since
        # plan step bank_import:X-gj-1b -- the near tier's own card
        # renders it as a hidden field (the stated_difference filter).
        _submit(
            seed_user, lines=[line], entries=[purchase], residual="-0.04",
        )

        assert purchase.amount == Decimal("121.16"), (
            "the purchase must take the figure the BANK showed"
        )
        assert purchase.settled_on == day

    def test_the_HAND_EDIT_door_still_refuses(self, app, db, seed_user):
        """R-GE's bound, as a firing control.

        Delete the source test in ``entry_service._refusals.cost_fields_changing``
        and this passes -- which is what makes it worth writing.  The
        permission must be reachable ONLY with a figure the bank's line
        stated; an owner typing into the popover still meets N-229's refusal,
        unchanged.
        """
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)

        with pytest.raises(ValidationError):
            entry_service.update_entry(
                purchase.id, seed_user["user"].id, figure=typed(Decimal("121.16")),
            )

        assert purchase.amount == Decimal("121.12")

    def test_an_ENTERED_day_does_not_buy_the_permission(
        self, app, db, seed_user,
    ):
        """The narrower control: it is the figure's SOURCE that permits.

        A caller submitting a settle day beside the figure must not inherit the
        permission just for having submitted one -- only a figure the bank's
        line stated carries the evidence, and ``typed`` is what every hand
        door states.
        """
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)

        with pytest.raises(ValidationError):
            entry_service.update_entry(
                purchase.id, seed_user["user"].id,
                figure=typed(Decimal("121.16")), settle_day=an_entered_day(day),
            )

        assert purchase.amount == Decimal("121.12")

    def test_an_OBSERVED_day_beside_a_typed_figure_does_not_buy_it_either(
        self, app, db, seed_user,
    ):
        """The direction the re-spelling changed (plan step X-bi-3e-1).

        Until this step the permission was read off the DAY: an ``observed``
        settle day in the call released ``amount`` whoever wrote the figure.
        The figure states its own writer now (ruling **R-BAL69**), and a
        person's figure beside the bank's day is still a person's -- the
        BAL-508 shape, and the one a day-based predicate lets through.  Put
        the predicate back on the day and this passes.
        """
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)

        with pytest.raises(ValidationError):
            entry_service.update_entry(
                purchase.id, seed_user["user"].id,
                figure=typed(Decimal("121.16")),
                settle_day=SettleDay(
                    day=day, basis=SettledDayBasisEnum.OBSERVED,
                ),
            )

        assert purchase.amount == Decimal("121.12")

    def test_the_banks_figure_alone_buys_it(self, app, db, seed_user):
        """The accepting arm, at the door the matcher reaches.

        Without it the two refusals above prove nothing about the release:
        a figure the bank's line stated re-costs the settled purchase, which
        is exactly R-GE's rule with the evidence read off the figure.
        """
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)

        # The figure ALONE: no settle day in the call, so the old day-based
        # predicate would refuse this and the source-based one admits it.
        entry_service.update_entry(
            purchase.id, seed_user["user"].id,
            figure=observed(Decimal("121.16")),
        )

        assert purchase.amount == Decimal("121.16")
        assert purchase.figure_source_id == ref_cache.movement_figure_source_id(
            MovementFigureSourceEnum.OBSERVED,
        )
