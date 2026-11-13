"""What the accept door refuses, each reachable from a stale page and writing nothing: a row that
moved since the review, and every other refusal.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import statement_match, status_seam, transaction_service
from app.services.row_valuation import settled_figure

from tests._test_helpers import an_entered_day

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)

from ._helpers import _submit


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
