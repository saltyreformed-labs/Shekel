"""What a standing rule files by itself, and what it refuses to.

Plan step ``bank_import:X-ge``, ruling **R-GH**.  **This is the only door in
the app that moves money without a press**, so every case here is paired: a
refusal is asserted beside the otherwise-identical line that IS filed, because
*nothing was filed* is true of a broken door as well as of a working refusal.
A control that only shows the empty side grades the fixture, not the code.

The rows are built through the ORM (``_builders``), never through the services
under test, so a broken create door cannot also build the fixture that would
have caught it.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.category import Category
from app.models.statement_match import StatementMatch
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import balance_at, statement_match, transaction_service
from app.services.balance_at import BalanceContext
from app.services.statement_match import (
    DAY_WINDOW,
    BankLine,
    Consent,
    MatchSubmission,
    ReviewedLine,
    MerchantSection,
    NewEnvelope,
    PurchaseCreation,
    ReviewBounds,
    ReviewedBatch,
    ReviewSet,
    file_new_swipes,
    review_set,
    rule_filed_acts,
)

from ._builders import (
    a_bank_line,
    a_later_period,
    a_purchase,
    a_rule,
    a_scope,
    a_transaction,
    an_import,
    the_merchant_id,
)
from tests._test_helpers import (
    last_covered_day,
    resolved_amount,
)

MERCHANT = "Food Lion"


def _groceries(seed_user, *, name="Groceries", amount="500.00", **kwargs):
    """Stage the recurring envelope a template rule files into."""
    return a_transaction(
        seed_user, name=name, amount=amount, is_envelope=True, **kwargs,
    )


def _swipe(seed_user, statement, *, amount="-10.89", merchant=MERCHANT, **kw):
    """Stage one recorded card swipe under *statement*."""
    return a_bank_line(
        seed_user, statement, amount=amount, merchant=merchant,
        description=f"POINT OF SALE DEBIT L340 ({merchant})", **kw,
    )


def _file(seed_user, statement):
    """Run the filing door for *statement*, exactly as the import route does."""
    return file_new_swipes(a_scope(seed_user), statement.id)


def _balance_on(seed_user, day):
    """Return the checking account's balance as of *day*."""
    return balance_at.balance_at(
        seed_user["account"],
        BalanceContext(
            user_id=seed_user["user"].id,
            scenario=seed_user["scenario"], as_of=day,
        ),
        day,
    )


def _purchases_in(envelope):
    """Return the purchases filed under *envelope*, oldest id first."""
    return (
        db.session.query(TransactionEntry)
        .filter(TransactionEntry.transaction_id == envelope.id)
        .order_by(TransactionEntry.id)
        .all()
    )


def _story(filing):
    """Return the whole outcome, so a failed count says WHY rather than what.

    A bare ``filed_count == 1`` failing prints ``0 != 1`` and names nothing;
    this puts the refusals and the withheld reasons in the same assertion, so
    a case that breaks reports the sentence the door gave.
    """
    return {
        "filed": filing.filed_count,
        "withheld": [item.reason for item in filing.withheld],
        "refused": [item.reason for item in filing.outcome.refused],
    }


class TestARuleFilesANewSwipeByItself:
    """The money property: consent given once files a line with no press."""

    def test_a_template_answer_files_the_swipe_and_moves_the_balance(
        self, app, db, seed_user,
    ):
        """Ruling **R-GH**'s worked example, end to end.

        The `$10.89` Food Lion swipe becomes a Groceries purchase dated the day
        the bank posted it, with nobody ticking anything -- and the BALANCE
        falls by exactly what the bank took, which is the fact that makes this
        a money door rather than a bookkeeping one.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
            _swipe(seed_user, statement, posted_on=day)
            db.session.flush()
            before = _balance_on(seed_user, day + timedelta(days=2))

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            assert filing.filed_total == Decimal("-10.89")
            purchases = _purchases_in(envelope)
            assert [p.amount for p in purchases] == [Decimal("10.89")]
            assert purchases[0].settled_on == day
            assert purchases[0].settled_day_basis_id == (
                ref_cache.settled_day_basis_id(SettledDayBasisEnum.OBSERVED)
            )
            assert _balance_on(seed_user, day + timedelta(days=2)) == (
                before - Decimal("10.89")
            )

    def test_the_act_records_that_a_RULE_performed_it(
        self, app, db, seed_user,
    ):
        """Ruling **R-GT**: which consent this act had is stored, not derived.

        It is the whole reason the column exists -- the receipt and the review
        screen's badge partition on it -- and until this step every act on the
        developer's own database was a tick.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            _swipe(seed_user, statement)
            db.session.flush()

            _file(seed_user, statement)
            db.session.flush()

            assert db.session.query(StatementMatch).one().applied_by_rule is (
                True
            )

    def test_a_new_envelope_answer_MINTS_the_container_and_files_into_it(
        self, app, db, seed_user,
    ):
        """The second of the two container answers (ruling **R-GI**).

        Developer ruling 2026-08-26: it files too, rather than waiting for
        ``X-f6c`` to give the answer a template identity -- Amazon is 26 lines
        and `$1,323.06` of the developer's own year.
        """
        with app.app_context():
            a_rule(
                seed_user, "Amazon", envelope_name="Amazon",
                category_id=seed_user["categories"]["Groceries"].id,
            )
            statement = an_import(seed_user)
            _swipe(seed_user, statement, amount="-31.56", merchant="Amazon")
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            assert filing.outcome.envelopes_created == 1
            minted = (
                db.session.query(Transaction)
                .filter(Transaction.name == "Amazon")
                .one()
            )
            assert minted.tracks_purchases is True and minted.template.is_envelope is True
            # Nothing budgeted it, so it budgets nothing and records only its
            # own purchases (``_container._NO_BUDGET``) -- priced by its
            # rule-less DEFINITION's one version since leaf 7b-3 of
            # balance:X-bi-7b (ruling R-BAL24), where it carried OWN $0.00.
            assert minted.is_placed is True
            assert minted.estimated_amount is None
            assert resolved_amount(minted) == Decimal("0.00")
            assert [p.amount for p in _purchases_in(minted)] == [
                Decimal("31.56"),
            ]
            # And the answer NAMES the definition from now on (N-328): the
            # stored rule's NEW-ENVELOPE columns became the TEMPLATE one.
            stored = db.session.query(MerchantRule).one()
            assert stored.template_id == minted.template_id
            assert stored.envelope_name is None
            assert stored.category_id is None

    def test_three_swipes_on_one_answer_share_ONE_minted_envelope(
        self, app, db, seed_user,
    ):
        """Finding **N-327**, on the door that has no press to bound it.

        The within-request registry is what stops a rule fragmenting its own
        budget line, and an import is one request -- so a pass filing three
        Amazon swipes into one period makes ONE Amazon envelope holding three
        purchases, not three envelopes.
        """
        with app.app_context():
            a_rule(
                seed_user, "Amazon", envelope_name="Amazon",
                category_id=seed_user["categories"]["Groceries"].id,
            )
            statement = an_import(seed_user)
            start = seed_user["bootstrap_period"].start_date
            for index, amount in enumerate(("-31.56", "-10.65", "-35.20")):
                _swipe(
                    seed_user, statement, amount=amount, merchant="Amazon",
                    posted_on=start + timedelta(days=index),
                    sequence_in_group=index,
                )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 3, "withheld": [], "refused": [],
            }
            assert filing.outcome.envelopes_created == 1
            minted = (
                db.session.query(Transaction)
                .filter(Transaction.name == "Amazon")
                .all()
            )
            assert len(minted) == 1
            # 31.56 + 10.65 + 35.20 = 77.41
            assert sum(p.amount for p in _purchases_in(minted[0])) == (
                Decimal("77.41")
            )

    def test_it_files_into_an_envelope_that_has_ALREADY_CLOSED(
        self, app, db, seed_user,
    ):
        """Developer ruling 2026-08-26, on measurement.

        Of the 80 lines a rule would file on the developer's own year, 33 go
        into an envelope he had already marked paid and **ZERO** into one still
        open -- he settles ahead of the bank.  Ruling **R-FX** admits it on
        exactly these terms: the row's recorded figure IS its purchases, so a
        new one raises that cost by exactly what the bank showed.  A door that
        refused this would file nothing for Groceries, Gas or Kayla's.

        **The purchase that closed the envelope is EXPLAINED by the bank**
        (a second line for exactly its figure, which the exact tier pairs),
        since plan step ``bank_import:X-f6b-2``: ruling **bank_import:R-BI19**
        withholds a filing into an envelope holding a purchase no bank line
        explains, so this case now says what it always meant -- a CLOSED
        envelope takes the filing -- with the other safeguard out of the way.
        The withheld shape is the next case's.  *The pairing rests on the
        window*: the closed envelope is itself an exact `$130.11` candidate,
        and it is the PURCHASE the tier pairs only because
        ``settle_from_entries`` dates the envelope TODAY, more than
        ``DAY_WINDOW`` from the line, so ``within_window`` refuses it; the
        undated purchase's window is the period's start, where the line is.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_purchase(
                seed_user, envelope, amount="130.11", description="Sams",
            )
            transaction_service.settle_from_entries(envelope)
            db.session.flush()
            assert envelope.status.is_settled is True
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            _swipe(seed_user, statement, amount="-10.89")
            _swipe(
                seed_user, statement, amount="-130.11", merchant="Sam's Club",
                sequence_in_group=1,
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            # 130.11 + 10.89: a ``purchases`` close IS its entries, so the row
            # now records the swipe the bank showed after it was closed.
            assert sorted(p.amount for p in _purchases_in(envelope)) == [
                Decimal("10.89"), Decimal("130.11"),
            ]

    def test_an_envelope_holding_a_purchase_NO_LINE_EXPLAINS_withholds(
        self, app, db, seed_user,
    ):
        """Ruling **bank_import:R-BI19**, plan step ``bank_import:X-f6b-2``.

        The case above with the bank's `$130.11` line absent: the envelope's
        own ``Sams`` purchase is money the books already hold that no bank
        line explains, and the `$10.89` swipe the rule would file into the
        same envelope may be that purchase logged at another amount -- the
        21-of-150 shape the developer's 2026-09-15 import measured.  Until
        this step the door filed it (the case above was written against that
        door) and the near tier, whose figure bound the `$130.11` fails by
        a mile, reported nothing.  The clause names WHERE to look.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_purchase(
                seed_user, envelope, amount="130.11", description="Sams",
            )
            transaction_service.settle_from_entries(envelope)
            db.session.flush()
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            _swipe(seed_user, statement, amount="-10.89")
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 0,
                "withheld": [
                    "your records already hold $130.11 leaving that no bank "
                    "line explains, inside Groceries, so recording it "
                    "automatically could count the same money twice",
                ],
                "refused": [],
            }
            assert [p.amount for p in _purchases_in(envelope)] == [
                Decimal("130.11"),
            ]


class TestOnlyANewSwipeIsFiled:
    """Ruling **R-GI**: a rule applies to NEW swipe lines and nothing else."""

    def test_a_line_an_EARLIER_import_recorded_is_left_alone(
        self, app, db, seed_user,
    ):
        """The freshness rule, with its positive control beside it.

        Two swipes, one recorded by the import being filed and one by an
        earlier import.  Only the fresh one is filed -- and asserting that
        needs BOTH, because "one purchase exists" is equally true of a door
        that files everything and one that files nothing.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            earlier = an_import(seed_user)
            start = seed_user["bootstrap_period"].start_date
            _swipe(seed_user, earlier, amount="-12.23", posted_on=start)
            statement = an_import(seed_user)
            _swipe(
                seed_user, statement, amount="-10.89",
                posted_on=start + timedelta(days=1),
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            assert [p.amount for p in _purchases_in(envelope)] == [
                Decimal("10.89"),
            ]

    def test_running_the_door_TWICE_files_nothing_the_second_time(
        self, app, db, seed_user,
    ):
        """Idempotency, which is what makes a re-import safe.

        The second pass sees the same import id and the same lines -- but the
        first pass MATCHED them, so they are no longer unexplained and no rule
        can reach them.  A door without that property would double the money
        every time the owner re-uploaded an overlapping export.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            _swipe(seed_user, statement)
            db.session.flush()

            first = _file(seed_user, statement)
            db.session.flush()
            second = _file(seed_user, statement)
            db.session.flush()

            assert first.filed_count == 1
            assert _story(second) == {
                "filed": 0, "withheld": [], "refused": [],
            }
            assert second.says_nothing is True
            assert len(_purchases_in(envelope)) == 1


class TestAnActThatWouldTouchAHandMadeRowKeepsItsTick:
    """Ruling **R-GH**'s protective half, which this door must not cross."""

    def test_a_line_the_matcher_PROPOSES_against_is_never_filed(
        self, app, db, seed_user,
    ):
        """The double-count guard, and the whole reason R-GH splits by class.

        The app already holds a `$130.11` Sams purchase; the bank's `$130.11`
        line is that same movement, so the matcher proposes it and the act that
        would settle it is a MODIFICATION needing a tick.  A rule that filed it
        anyway would record the swipe twice -- which is the `$356.61`-for-one-
        `$178.29`-movement shape finding **N-335** measures.

        The `$10.89` line beside it has no counterpart and IS filed, so this
        case cannot pass because the door did nothing.

        **The hand purchase is SETTLED, and that is what makes the proposal
        name the PURCHASE rather than its envelope.**  It is also the shape the
        developer's own books are in -- measured on his 2026-08-13 Groceries
        row, three hand purchases all carrying a settle day, all three proposed
        against their own lines.  Left unsettled, the envelope is priced at the
        reservation those purchases make and the bank's line matches the
        ENVELOPE whole, which is the collision the case below covers.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            start = seed_user["bootstrap_period"].start_date
            a_purchase(
                seed_user, envelope, amount="130.11", description="Sams",
                purchased_on=start, settled_on=start,
                settle_day_basis=SettledDayBasisEnum.ENTERED,
            )
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            _swipe(seed_user, statement, amount="-130.11", posted_on=start)
            _swipe(
                seed_user, statement, amount="-10.89",
                posted_on=start + timedelta(days=1), sequence_in_group=1,
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            # The `$130.11` swipe stayed ONE purchase, not two.
            assert sorted(p.amount for p in _purchases_in(envelope)) == [
                Decimal("10.89"), Decimal("130.11"),
            ]

    def test_a_CREDIT_and_a_SWIPE_from_ONE_merchant_file_with_opposite_signs(
        self, app, db, seed_user,
    ):
        """One rule, one pass, both directions -- and the signs are mirrors.

        **This case asserted that an inflow NEVER becomes a purchase until plan
        step ``bank_import:X-gj-2b-2``**, which was true only while
        ``ck_transaction_entries_positive_amount`` forbade a negative one.  A
        credit from a merchant whose rule names a SPENDING container is that
        rule's INVERSE -- a refund back into the same container (ruling
        **R-HT(a)**) -- so the same answer now places both directions.

        **The strongest form of the sign property available**, because both
        lines take the identical path: same merchant, same rule, same
        container, same door, same pass.  Nothing but the bank's own sign
        differs, so a sign branch anywhere in that path would show up as an
        asymmetry here.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            # **Both dated AFTER the account's opening assertion**, which is
            # load-bearing for the balance assertion below.  A movement dated
            # on the day a balance was asserted for is already inside it, so
            # the posted self-heal corrects it back out -- for a refund and a
            # swipe alike.  A first draft put the credit on the period's start
            # date and measured the refund's `$42.00` vanishing, which is the
            # ASSERTION doing its job rather than a sign defect.
            start_day = seed_user["bootstrap_period"].start_date
            _swipe(
                seed_user, statement, amount="42.00",
                posted_on=start_day + timedelta(days=1),
            )
            _swipe(
                seed_user, statement, amount="-10.89",
                posted_on=start_day + timedelta(days=2),
            )
            db.session.flush()
            before = _balance_on(seed_user, start_day + timedelta(days=5))

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 2, "withheld": [], "refused": [],
            }
            # The refund is NEGATIVE and the swipe is POSITIVE -- the bank's
            # own signs, inverted once by the one expression that converts a
            # line into a purchase.
            assert sorted(p.amount for p in _purchases_in(envelope)) == [
                Decimal("-42.00"), Decimal("10.89"),
            ]
            # THE MONEY nets the way the statement does: `$42.00` back in,
            # `$10.89` out.
            assert _balance_on(seed_user, start_day + timedelta(days=5)) == (
                before + Decimal("42.00") - Decimal("10.89")
            )

    def test_the_receipt_counts_the_two_directions_SEPARATELY(
        self, app, db, seed_user,
    ):
        """One count said *recorded as a purchase your records did not have*.

        Plan step ``bank_import:X-gj-2b-3``.  ``BatchOutcome.recorded_count``
        carried both directions, and the receipt's caption for it names an act
        the bank did not perform on a refund -- *dated the day your bank took
        it*, over money it gave back.  That is exactly the defect
        ``deposited_count`` was split out of the same field for, one direction
        later.

        The same two lines as the case above, so the pass is identical and only
        the counting is under test.  **Asserts the SUM as well as the split**:
        ``RuleFiling.filed_count`` names its members, so a split it was not
        told about silently under-reports -- which is how the deposit half
        arrived, and this is the second time.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            start_day = seed_user["bootstrap_period"].start_date
            _swipe(
                seed_user, statement, amount="42.00",
                posted_on=start_day + timedelta(days=1),
            )
            _swipe(
                seed_user, statement, amount="-10.89",
                posted_on=start_day + timedelta(days=2),
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            outcome = filing.outcome
            assert (outcome.recorded_count, outcome.refunded_count) == (1, 1), (
                "the swipe is a charge and the credit is a refund; one count "
                "for both is a caption false of half its members"
            )
            assert outcome.deposited_count == 0, (
                "a merchant credit a rule files into a container is a "
                "PURCHASE, not income -- the control that keeps the split "
                "above from being satisfied by routing everything to deposits"
            )
            # The sum every consumer of this receipt reads.
            assert filing.filed_count == 2
            assert outcome.moved_nothing is False

    def test_a_pass_that_files_ONLY_a_refund_still_reports_it(
        self, app, db, seed_user,
    ):
        """The firing control for the sum, and for *"Nothing moved."*

        With ``filed_count`` and ``moved_nothing`` naming their members, a pass
        whose ONLY act is a refund is the arrangement that catches a member
        they were not told about -- and both would be wrong in the direction
        that reports NOTHING over money that moved.  The case above cannot see
        it: its charge keeps both properties true whatever the refund does.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            start_day = seed_user["bootstrap_period"].start_date
            _swipe(
                seed_user, statement, amount="42.00",
                posted_on=start_day + timedelta(days=1),
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert filing.outcome.recorded_count == 0
            assert filing.outcome.refunded_count == 1
            assert filing.filed_count == 1, (
                "a pass that filed only refunds reported filing NOTHING"
            )
            assert filing.outcome.moved_nothing is False, (
                "the receipt said 'Nothing moved.' over a purchase it created"
            )
            assert [p.amount for p in _purchases_in(envelope)] == [
                Decimal("-42.00"),
            ]


class TestAnAnswerlessMerchantIsLeftForTheOwner:
    """The three answers that place nothing, and the bar beside them."""

    @pytest.mark.parametrize("answer", ["none", "always_ask", "never"])
    def test_a_merchant_with_no_container_answer_files_nothing(
        self, app, db, seed_user, answer,
    ):
        """A rule that names no container REACHES nothing (ruling **R-GS**).

        *You have not said*, *ask me every time* and *never a purchase* place
        no money -- the first two because they name no destination and the
        third because it BARS one (ruling **R-GJ**).  All three leave the line
        on the review screen, and none of them is this pass WITHHOLDING
        anything, so none is reported as a withheld line.

        The answered Food Lion swipe files beside it in every arm, so the empty
        assertion cannot pass on a door that filed nothing at all.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            if answer == "always_ask":
                a_rule(seed_user, "Lowe's", always_ask=True)
            elif answer == "never":
                a_rule(seed_user, "Lowe's")
            statement = an_import(seed_user)
            start = seed_user["bootstrap_period"].start_date
            _swipe(
                seed_user, statement, amount="-55.47", merchant="Lowe's",
                posted_on=start,
            )
            _swipe(
                seed_user, statement, amount="-10.89",
                posted_on=start + timedelta(days=1),
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            assert [p.amount for p in _purchases_in(envelope)] == [
                Decimal("10.89"),
            ]

    def test_a_template_answer_that_reaches_no_row_HERE_files_nothing(
        self, app, db, seed_user,
    ):
        """An UNRESOLVED placement is not an act (``_placement``'s rule).

        The rule names a template whose row is in the BOOTSTRAP period, and the
        swipe falls in the next one, which holds none.  Substituting a
        different destination is how a suggestion becomes a guess.  It is not a
        WITHHELD line either: nothing about this pass's search is in doubt, the
        answer simply does not reach here.
        """
        with app.app_context():
            envelope = _groceries(seed_user, name="Gas", amount="120.00")
            period = a_later_period(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            _swipe(
                seed_user, statement, amount="-10.89",
                posted_on=period.start_date + timedelta(days=1),
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 0, "withheld": [], "refused": [],
            }
            assert _purchases_in(envelope) == []


class TestAPassThatCouldNotFinishLookingWITHHOLDS:
    """Developer ruling 2026-08-26: fail closed, and say what was dropped."""

    def test_a_line_the_NEAR_tier_could_not_decide_is_withheld_with_a_reason(
        self, app, db, seed_user,
    ):
        """The `$356.61` shape, refused before an automatic door reaches it.

        Two of the owner's own Food Lion purchases sit within half a percent of
        the bank's line, so the near tier admits both and refuses to choose.
        The screen already prints *check the match form below before recording
        this as new spending* on such a line; this is that advice made
        structural for a door with nobody reading it.

        **The second Food Lion swipe is withheld TOO since plan step
        ``bank_import:X-f6b-2``**, by the other safeguard (ruling
        **bank_import:R-BI19**): the same two hand rows are named for its
        merchant and sit inside the envelope its rule would file it into, so
        it may be one of them logged at another amount.  *It read "the second
        swipe, whose figure no row is near, IS filed" until then*, which was
        the gap that ruling closes.  The gap is still asked FIRST, so the
        contested line keeps the near tier's own words.  A third swipe from a
        merchant whose envelope holds nothing IS filed -- so a door that
        withheld everything still fails this case.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            start = seed_user["bootstrap_period"].start_date
            for amount in ("178.32", "178.30"):
                a_purchase(
                    seed_user, envelope, amount=amount,
                    description="Food Lion", purchased_on=start,
                )
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            gas = _groceries(seed_user, name="Gas", amount="200.00")
            a_rule(seed_user, "BJ's Fuel", template_id=gas.template_id)
            statement = an_import(seed_user)
            _swipe(seed_user, statement, amount="-178.29", posted_on=start)
            _swipe(
                seed_user, statement, amount="-10.89",
                posted_on=start + timedelta(days=1), sequence_in_group=1,
            )
            _swipe(
                seed_user, statement, amount="-47.61", merchant="BJ's Fuel",
                posted_on=start + timedelta(days=1), sequence_in_group=2,
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert filing.filed_count == 1
            withheld = {
                item.line.amount: item.reason for item in filing.withheld
            }
            assert set(withheld) == {Decimal("-178.29"), Decimal("-10.89")}
            assert "close enough" in withheld[Decimal("-178.29")]
            assert withheld[Decimal("-10.89")] == (
                "your records already hold $356.62 leaving that no bank line "
                "explains, named for Food Lion or inside Groceries, so "
                "recording it automatically could count the same money twice"
            )
            # The two hand rows are untouched and nothing joined them; the
            # fuel swipe landed in Gas.
            assert sorted(p.amount for p in _purchases_in(envelope)) == [
                Decimal("178.30"), Decimal("178.32"),
            ]
            assert [p.amount for p in _purchases_in(gas)] == [
                Decimal("47.61"),
            ]

    def test_a_destination_this_statement_explains_WHOLE_is_withheld(
        self, app, db, seed_user,
    ):
        """Ruling **R-FZ(d)**, applied from the other side of the order.

        That ruling settles a collision between two TICKED items in the
        PROPOSAL's favour.  Auto-apply files BEFORE the proposal is ticked, so
        the same answer has to be reached by withholding: an envelope the
        statement explains as a whole may not also take a purchase, or its
        recorded cost exceeds the line it was matched to.

        Measured 0 of 80 on the developer's own year -- because every hand
        purchase in his books carries a settle day, so the matcher names the
        PURCHASE.  **The shape is reachable rather than hypothetical**: an
        envelope holding UNPOSTED purchases is priced at the reservation they
        make, and a line equal to that figure matches the envelope WHOLE.  This
        case builds exactly that.
        """
        with app.app_context():
            envelope = _groceries(seed_user, amount="500.00")
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            start = seed_user["bootstrap_period"].start_date
            # An untouched Projected envelope reserves its whole estimate, so
            # a line at that figure pairs with the ROW itself.
            _swipe(seed_user, statement, amount="-500.00", posted_on=start)
            _swipe(
                seed_user, statement, amount="-10.89",
                posted_on=start + timedelta(days=1), sequence_in_group=1,
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert filing.filed_count == 0
            assert [item.line.amount for item in filing.withheld] == [
                Decimal("-10.89"),
            ]
            assert "makes that match impossible to accept" in filing.withheld[0].reason
            assert _purchases_in(envelope) == []


class TestALineOutsideTheCalendarIsLeftAlone:
    """A swipe with no paycheck to belong to is not filed anywhere."""

    def test_a_swipe_past_the_last_saved_period_files_nothing(
        self, app, db, seed_user,
    ):
        """It is SKIPPED rather than refused, and that is the honest answer.

        A purchase is budgeted in the period holding the day it was made, so a
        swipe past the last saved payday has no budget for a rule to file it
        into -- and there is no destination to resolve, which is what
        ``_leftovers._one_creatable`` answers with no placement at all.  A door
        that guessed the nearest period would file already-spent money against
        the wrong paycheck.

        The in-calendar swipe files beside it, so the empty half cannot pass on
        a door that did nothing.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            start = seed_user["bootstrap_period"].start_date
            _swipe(seed_user, statement, amount="-10.89", posted_on=start)
            beyond = last_covered_day(seed_user["bootstrap_period"]) + timedelta(
                days=400,
            )
            _swipe(seed_user, statement, amount="-12.23", posted_on=beyond)
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            assert [p.amount for p in _purchases_in(envelope)] == [
                Decimal("10.89"),
            ]


class TestARefusedItemCostsOnlyItself:
    """The ruled per-item failure policy, under a rule's consent.

    **The savepoint is** :func:`~._batch.apply_reviewed`'s **and it is graded
    there**; what these hold is that a RULE-consented pass reaches it -- one
    item refused, the rest landed, the refusal reported rather than raised.
    The refusal used is a stale line id, which is what a concurrent removal
    between a pass's derivation and its write looks like and is the case
    ``_batch._run`` names for catching ``NotFoundError`` beside
    ``ValidationError``.
    """

    def test_one_refused_creation_does_not_cost_the_others(
        self, app, db, seed_user,
    ):
        """A refused item leaves nothing behind and the rest still land."""
        with app.app_context():
            envelope = _groceries(seed_user)
            statement = an_import(seed_user)
            line = _swipe(seed_user, statement, amount="-10.89")
            db.session.flush()

            outcome = statement_match.apply_reviewed(
                ReviewedBatch(
                    skips=(),
                    consent=Consent.STANDING_RULE,
                    incomes=(),
                    matches=(),
                    creations=(
                        PurchaseCreation(
                            line_id=line.id, transaction_id=envelope.id,
                        ),
                        PurchaseCreation(
                            line_id=999_999, transaction_id=envelope.id,
                        ),
                    ),
                ),
                a_scope(seed_user),
            )
            db.session.flush()

            assert outcome.recorded_count == 1
            assert outcome.refused_count == 1
            assert [p.amount for p in _purchases_in(envelope)] == [
                Decimal("10.89"),
            ]
            # ...and the one that landed still says a RULE did it.
            assert db.session.query(StatementMatch).one().applied_by_rule is (
                True
            )


class TestTheDoorCannotReachAnotherOwner:
    """The IDOR controls, which no service test had until 2026-08-26.

    `_filing.py` CLAIMS that taking the account from the SCOPE is what stops
    one door's import being filed against another door's account.  A claim in a
    docstring with no control behind it is the shape this arc keeps finding, so
    both halves are asserted here: the write reaches nothing, and the READ that
    feeds the receipt shows nothing.
    """

    def test_a_FOREIGN_import_id_files_nothing(
        self, app, db, seed_user, seed_second_user,
    ):
        """The scope's account is the ONE statement of whose lines are reached.

        The second owner's import carries a swipe whose merchant name the FIRST
        owner has a rule for -- a merchant is per-account, so it is a different
        row, and neither the line nor the rule may cross.  The first owner's
        own swipe files in the same pass, so a door that reached nothing at all
        would fail this.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            mine = an_import(seed_user)
            _swipe(seed_user, mine, amount="-10.89")
            theirs = an_import(seed_second_user)
            a_bank_line(
                seed_second_user, theirs, amount="-99.99",
                description=f"POINT OF SALE DEBIT L340 ({MERCHANT})",
                merchant=MERCHANT,
            )
            db.session.flush()

            # This owner's scope, the OTHER owner's import.
            across = file_new_swipes(a_scope(seed_user), theirs.id)
            db.session.flush()

            assert _story(across) == {
                "filed": 0, "withheld": [], "refused": [],
            }
            assert _purchases_in(envelope) == []

            # ...and the control that proves the door was not simply inert.
            mine_filed = _file(seed_user, mine)
            db.session.flush()

            assert mine_filed.filed_count == 1
            assert [p.amount for p in _purchases_in(envelope)] == [
                Decimal("10.89"),
            ]

    def test_the_receipt_shows_no_other_owners_act(
        self, app, db, seed_user, seed_second_user,
    ):
        """`rule_filed_acts` narrows by OWNER and ACCOUNT, not by the flag.

        Both owners' rules file; each receipt holds its own act and only its
        own.  Asserting one side would pass on a reader that returned
        everything to everyone, which is why both are staged and both are
        checked.
        """
        with app.app_context():
            mine = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=mine.template_id)
            my_import = an_import(seed_user)
            _swipe(seed_user, my_import, amount="-10.89")
            db.session.flush()
            _file(seed_user, my_import)
            db.session.flush()

            receipt = rule_filed_acts(
                seed_user["user"].id, seed_user["account"].id,
            )
            theirs = rule_filed_acts(
                seed_second_user["user"].id,
                seed_second_user["account"].id,
            )

            assert [group.amount for group in receipt] == [Decimal("-10.89")]
            assert theirs == []
            # ...and asking for MY act while naming THEIR account answers
            # nothing rather than answering it.
            assert rule_filed_acts(
                seed_second_user["user"].id, seed_user["account"].id,
            ) == []


class TestAnImportWithNothingToDoDerivesNothing:
    """The two EXACT short-circuits, and that they change no answer.

    Both were added when an adversarial review measured what the pass costs on
    the WRITE door -- 0.59-0.75 s and 202 queries on the developer's own
    account, growing with every act it accumulates.  What matters for
    correctness is that each is exact rather than a heuristic, so each is
    asserted against the state it claims to be equivalent to.
    """

    def test_an_account_with_NO_container_rule_files_nothing(
        self, app, db, seed_user,
    ):
        """Only a template or new-envelope answer can place a line.

        The account here holds a *never a purchase* rule and an *ask me every
        time* one, so the short-circuit fires -- and the answer has to be the
        same one the whole derivation gives, which the class above already
        asserts for the same two answers on an account that DOES have a
        container rule beside them.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, always_ask=True)
            a_rule(seed_user, "Lowe's")
            statement = an_import(seed_user)
            _swipe(seed_user, statement, amount="-10.89")
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert filing.says_nothing is True
            assert _purchases_in(envelope) == []

    def test_an_import_that_recorded_NO_fresh_line_files_nothing(
        self, app, db, seed_user,
    ):
        """A re-import records no line, so no rule can reach one.

        The import row here owns no line at all, which is what an overlapping
        re-export produces.  The rule EXISTS and reaches a line under a
        DIFFERENT import, so this cannot pass for want of an answer.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            earlier = an_import(seed_user)
            _swipe(seed_user, earlier, amount="-10.89")
            re_import = an_import(seed_user)
            db.session.flush()

            filing = _file(seed_user, re_import)
            db.session.flush()

            assert filing.says_nothing is True
            assert _purchases_in(envelope) == []


class TestEveryBoundThePassPublishesWITHHOLDS:
    """The three arms of :meth:`ReviewSet.search_gap_for`, each shown FIRING.

    **Two of the three are unreachable from ordinary data and that is exactly
    why they are graded here.**  A crowded day needs more than
    :data:`~._propose.MAX_GROUP_DAY_ROWS` candidate rows sharing a bucket, and
    an unpriceable row needs an amount model that cannot value it -- both
    measure ZERO on the developer's own 378 lines.  An arm nothing can reach is
    an arm nothing can catch breaking, so these build the published bound
    directly, on the value the pass hands out, rather than waiting for data
    that would make the defect expensive first.
    """

    @staticmethod
    def _a_review(**bounds):
        """Return a ReviewSet publishing *bounds* and nothing else."""
        return ReviewSet(
            # **No default on the dataclass, so every construction states it**
            # (plan step ``bank_import:X-gj-4b``): an empty set means *nothing is
            # barred*, and that is the value that reads as safe.  This helper
            # exercises no SKIP and no card-payment merchant, so empty is honest.
            account_payments=frozenset(),
            proposals=(), unmatched=(), unmatched_rows=(),
            creatable=(), parked=(), answered_never=(),
            recordable_inflows=(),
            merchants=MerchantSection(merchants=(), templates=()),
            bounds=ReviewBounds(
                calendar_opens=None,
                before_calendar_count=0,
                before_calendar_last_day=None,
                crowded_days=bounds.get("crowded_days", ()),
                unpriceable_count=bounds.get("unpriceable_count", 0),
            ),
            declined_lines=bounds.get("declined", {}),
        )

    @staticmethod
    def _a_line(day, line_id=7):
        """Return the bank line the arms are asked about."""
        return BankLine(
            line_id=line_id, posted_on=day, amount=Decimal("-10.89"),
            description="POINT OF SALE DEBIT L340 (Food Lion)",
            transaction_on=None, merchant_id=1, merchant="Food Lion",
        )

    def test_a_clean_pass_reports_no_gap(self):
        """The control that stops the three arms firing on everything."""
        day = date(2026, 8, 17)

        assert self._a_review().search_gap_for(self._a_line(day)) is None

    def test_an_UNDECIDED_near_line_reports_its_own_gap(self):
        """The measured arm: the near tier admitted a row and would not pick."""
        day = date(2026, 8, 17)

        gap = self._a_review(
            declined={7: "one of your own rows is close enough to this"},
        ).search_gap_for(
            self._a_line(day),
        )

        assert gap is not None and "close enough" in gap

    @pytest.mark.parametrize("offset", [0, DAY_WINDOW, -DAY_WINDOW])
    def test_a_CROWDED_day_inside_the_pairing_window_reports_a_gap(
        self, offset,
    ):
        """The group search skipped a day this line could have paired across.

        Measured on the WINDOW rather than on the line's own day, because that
        is the span :func:`~._propose._groups` pairs a line to a bucket over --
        a narrower test would clear a line the search never looked for a group
        for.  All three edges of the window, so a strict-vs-inclusive slip on
        either end is caught.
        """
        day = date(2026, 8, 17)
        crowded = day + timedelta(days=offset)

        gap = self._a_review(crowded_days=(crowded,)).search_gap_for(
            self._a_line(day),
        )

        assert gap is not None and str(crowded) in gap

    def test_a_CROWDED_day_beyond_the_window_reports_nothing(self):
        """...and one the line could never have paired across does not.

        Without this the arm would withhold every line on the account the
        moment one day anywhere got crowded, which is a bound that has stopped
        bounding anything.
        """
        day = date(2026, 8, 17)

        gap = self._a_review(
            crowded_days=(day + timedelta(days=DAY_WINDOW + 1),),
        ).search_gap_for(self._a_line(day))

        assert gap is None

    def test_an_UNPRICEABLE_row_withholds_every_line(self):
        """Account-wide blindness withholds account-wide.

        A row the amount model cannot value is absent from the candidate set
        entirely, so there is no line it can be said not to match -- which
        makes *no proposal claimed this* an answer about a search that could
        not be run.
        """
        day = date(2026, 8, 17)

        gap = self._a_review(unpriceable_count=2).search_gap_for(
            self._a_line(day),
        )

        assert gap is not None and "could not be priced" in gap


class TestARuleMayNotModifyAHandMadeRow:
    """The boundary R-GH draws, made unrepresentable rather than maintained."""

    def test_a_rule_consented_batch_carrying_a_MATCH_is_unconstructible(self):
        """``ReviewedBatch.__post_init__``, at the value rather than the door.

        A standing rule is consent for CREATING a row from a new swipe.  Every
        act that modifies a row the owner made by hand reaches
        ``accept_match``, which hardcodes ``applied_by_rule=False`` and says no
        rule reaches it -- so the batch that would contradict that sentence
        cannot be built at all.
        """
        with pytest.raises(ValueError) as refused:
            ReviewedBatch(
                skips=(),
                consent=Consent.STANDING_RULE,
                incomes=(),
                matches=(MatchSubmission(
                    lines=frozenset({ReviewedLine(
                        line_id=1, happened_on=date(2024, 1, 5),
                    )}),
                    rows=frozenset({"transaction:1"}),
                ),),
                creations=(),
            )

        assert "R-GH" in str(refused.value)

    def test_a_TICKED_batch_may_carry_matches(self):
        """The control that stops the guard above refusing everything.

        Without it, a ``__post_init__`` raising unconditionally would pass the
        case above and break every reviewed pass in the app.
        """
        batch = ReviewedBatch(
            skips=(),
            consent=Consent.TICKED,
            incomes=(),
            matches=(MatchSubmission(
                lines=frozenset({ReviewedLine(
                    line_id=1, happened_on=date(2024, 1, 5),
                )}),
                rows=frozenset({"transaction:1"}),
            ),),
            creations=(),
        )

        assert batch.item_count == 1
        assert batch.consent.applied_by_rule is False

    def test_a_rule_consented_batch_of_CREATIONS_is_legal(self):
        """...and the arm the filing door actually builds is not refused."""
        batch = ReviewedBatch(
            skips=(),
            consent=Consent.STANDING_RULE,
            incomes=(),
            matches=(),
            creations=(PurchaseCreation(line_id=1, transaction_id=2),),
        )

        assert batch.item_count == 1
        assert batch.consent.applied_by_rule is True


class TestTheReceiptCarriesTheUndo:
    """Ruling **R-GH**: every application is receipted, and undoable."""

    def test_the_receipt_lists_only_the_acts_a_RULE_performed(
        self, app, db, seed_user,
    ):
        """The receipt is a READ over ``applied_by_rule``, not a flash.

        A ticked creation sits beside a rule-filed one; only the second is on
        the receipt.  Asserting one act would pass on a reader that returned
        everything, which is why the ticked act is staged too.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            start = seed_user["bootstrap_period"].start_date
            _swipe(seed_user, statement, amount="-10.89", posted_on=start)
            ticked_line = _swipe(
                seed_user, statement, amount="-12.23", merchant="Lowe's",
                posted_on=start + timedelta(days=1),
            )
            db.session.flush()

            statement_match.apply_reviewed(
                ReviewedBatch(
                    skips=(),
                    consent=Consent.TICKED,
                    incomes=(),
                    matches=(),
                    creations=(PurchaseCreation(
                        line_id=ticked_line.id,
                        new_envelope=NewEnvelope(
                            name="Home Improvement",
                            category_id=(
                                seed_user["categories"]["Groceries"].id
                            ),
                        ),
                    ),),
                ),
                a_scope(seed_user),
            )
            db.session.flush()
            _file(seed_user, statement)
            db.session.flush()

            receipt = rule_filed_acts(
                seed_user["user"].id, seed_user["account"].id,
            )

            assert db.session.query(StatementMatch).count() == 2
            assert len(receipt) == 1
            assert receipt[0].amount == Decimal("-10.89")
            assert receipt[0].applied_by_rule is True

    def test_undoing_a_rule_filed_act_removes_the_purchase_it_created(
        self, app, db, seed_user,
    ):
        """R-GG's undo, reached from the receipt this step renders.

        A rule filed the purchase, so the owner never pressed anything to
        create it -- which is exactly why the receipt has to carry the inverse.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            _swipe(seed_user, statement, posted_on=day)
            db.session.flush()
            before = _balance_on(seed_user, day + timedelta(days=2))

            _file(seed_user, statement)
            db.session.flush()
            act = db.session.query(StatementMatch).one()
            released = statement_match.release_match(
                act.id, seed_user["user"].id, seed_user["account"].id,
            )
            db.session.flush()

            assert released.removed_rows == 1
            assert released.removed_cash == Decimal("-10.89")
            assert _purchases_in(envelope) == []
            assert _balance_on(seed_user, day + timedelta(days=2)) == before


class TestTheRuleAndTheSweepAgreeAboutWhereMoneyGoes:
    """One derivation, two readers -- the property X-ge rests on."""

    def test_a_placement_names_a_creation_exactly_when_it_names_a_value(
        self, app, db, seed_user,
    ):
        """:meth:`Placement.creation_for` and :attr:`select_value` agree.

        The sweep control the owner presses and the rule that fires without one
        must aim at the same destination, or the app answers *where does this
        merchant's money go* two ways on the door that moves it.  Graded as a
        round trip over BOTH container kinds rather than case by case, because
        a fifth placement kind added to one side and not the other is the
        failure this pair invites.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            a_rule(
                seed_user, "Amazon", envelope_name="Amazon",
                category_id=seed_user["categories"]["Groceries"].id,
            )
            statement = an_import(seed_user)
            start = seed_user["bootstrap_period"].start_date
            _swipe(seed_user, statement, amount="-10.89", posted_on=start)
            _swipe(
                seed_user, statement, amount="-31.56", merchant="Amazon",
                posted_on=start + timedelta(days=1),
            )
            db.session.flush()

            review = statement_match.review_set(a_scope(seed_user))
            seen = set()
            for item in review.creatable:
                placement = item.placement
                if placement is None:
                    continue
                seen.add(placement.kind)
                creation = placement.creation_for(item.line.line_id)
                assert (creation is None) is (
                    placement.select_value is None
                ), placement.kind
                if creation is None:
                    continue
                assert creation.line_id == item.line.line_id
                if creation.transaction_id is not None:
                    assert placement.select_value == str(
                        creation.transaction_id,
                    )
                else:
                    assert placement.select_value == "new"
                    assert creation.new_envelope == placement.new_envelope

            assert len(seen) == 2, "both container kinds were exercised"

    def test_a_line_naming_NO_merchant_keys_no_rule(
        self, app, db, seed_user,
    ):
        """The rule's key is a merchant ROW held to this account (**R-GR**).

        A line naming no merchant at all keys no rule, which is the direction a
        missing fact has to fail in -- and it is the ordinary shape rather than
        a crafted one: a source that names none records ``NULL``.  The rule
        EXISTS, which is what stops this passing for want of one.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, MERCHANT, template_id=envelope.template_id)
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="-10.89",
                description="POINT OF SALE DEBIT L340 FOOD LION",
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert the_merchant_id(seed_user, MERCHANT) is not None
            assert _story(filing) == {
                "filed": 0, "withheld": [], "refused": [],
            }
            assert _purchases_in(envelope) == []


class TestAStandingRuleFilesADepositByItself:
    """Ruling **R-HT(a)**, plan step ``bank_import:X-gj-2a``.

    **The money property in the other direction**: an INCOME answer is consent
    given once, so a deposit from that signature becomes an income row at
    import with no press -- the same act class **R-GH** consents to, because it
    CREATES a row from a new bank line and modifies nothing the owner made by
    hand.

    Measured on the developer's own account 2026-08-31: five ``Dividend
    Earned`` deposits worth `$0.79` dissolve under this rule, and they are 5 of
    the 16 lines his inbox holds.
    """

    def _deposit(self, seed_user, statement, *, amount="0.15", **kwargs):
        """Stage one recorded deposit from the ruled signature."""
        return a_bank_line(
            seed_user, statement, amount=amount, merchant="Dividend Earned",
            description="DIVIDEND EARNED (Dividend Earned)", **kwargs,
        )

    def test_an_income_answer_files_the_deposit_and_moves_the_balance(
        self, app, db, seed_user,
    ):
        """The whole act: the row, its category, its day, and the money.

        **The balance is asserted rather than the row alone**, which is this
        class's twin's rule: a row written with the wrong sign, the wrong day
        or a status the walk ignores is a row that exists and changes nothing.
        """
        with app.app_context():
            category = seed_user["categories"]["Salary"]
            a_rule(
                seed_user, "Dividend Earned", income_category_id=category.id,
            )
            statement = an_import(seed_user)
            # **PAST the anchor day, exactly as this class's outflow twin
            # stages it.**  The seeded account asserts `$1,000.00` on the
            # bootstrap period's first day, and a pre-cutover assertion RESETS
            # the walk -- so a row settled on that day is absorbed by the
            # assertion and the balance does not move, which would make this
            # case fail for a reason that is not about the rule.
            day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
            self._deposit(seed_user, statement, amount="0.15", posted_on=day)
            db.session.flush()
            before = _balance_on(seed_user, day + timedelta(days=2))

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            # POSITIVE, where the outflow twin's is negative: the receipt's
            # figure is the bank's own direction and this door now nets both.
            assert filing.filed_total == Decimal("0.15")
            recorded = (
                db.session.query(Transaction)
                .filter(
                    Transaction.account_id == seed_user["account"].id,
                    Transaction.category_id == category.id,
                )
                .all()
            )
            assert len(recorded) == 1
            # Priced by the definition the door mints (balance:X-bi-7b, R-BAL21).
            assert resolved_amount(recorded[0]) == Decimal("0.15")
            assert recorded[0].settled_on == day
            assert recorded[0].settled_day_basis_id == (
                ref_cache.settled_day_basis_id(SettledDayBasisEnum.OBSERVED)
            )
            # THE MONEY: the deposit is IN the balance, on the bank's own day.
            assert _balance_on(seed_user, day + timedelta(days=2)) == (
                before + Decimal("0.15")
            )

    def test_the_act_records_that_a_RULE_performed_it(
        self, app, db, seed_user,
    ):
        """**R-GT**: the receipt and the Filed-by-rules tab both read this.

        Without it the act lands on the Explained tab as though the owner had
        ticked it, and ``rule_filed_acts`` -- the receipt's own reader -- finds
        nothing to show an undo for.
        """
        with app.app_context():
            a_rule(
                seed_user, "Dividend Earned",
                income_category_id=seed_user["categories"]["Salary"].id,
            )
            statement = an_import(seed_user)
            self._deposit(
                seed_user, statement,
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.flush()

            _file(seed_user, statement)
            db.session.flush()

            acts = rule_filed_acts(
                seed_user["user"].id, seed_user["account"].id,
            )
            assert len(acts) == 1

    def test_an_UNANSWERED_deposit_is_left_alone_and_is_not_withheld(
        self, app, db, seed_user,
    ):
        """The absence of a rule is not this pass withholding anything.

        A receipt reading *your rules withheld this* about a signature the
        owner has never answered for is false, and it is the sentence that
        makes a real withholding unreadable.
        """
        with app.app_context():
            statement = an_import(seed_user)
            self._deposit(
                seed_user, statement,
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.flush()

            filing = _file(seed_user, statement)

            assert _story(filing) == {
                "filed": 0, "withheld": [], "refused": [],
            }

    def test_an_ACCOUNT_WITH_ONLY_AN_INCOME_RULE_still_files(
        self, app, db, seed_user,
    ):
        """The pre-check's own case, and it would fail SILENTLY if wrong.

        ``_has_a_filing_rule`` short-circuits before the pass is derived at
        all, so an account whose only rules answer DEPOSITS would have filed
        nothing with no refusal, no withholding and nothing on the receipt --
        a door that returned early has nothing to report.  Staging an account
        that holds NO container rule is what makes this case grade the
        short-circuit rather than the arm downstream of it.
        """
        with app.app_context():
            a_rule(
                seed_user, "Dividend Earned",
                income_category_id=seed_user["categories"]["Salary"].id,
            )
            statement = an_import(seed_user)
            self._deposit(
                seed_user, statement,
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert filing.filed_count == 1


class TestARuleWithholdsADepositTheBooksMayAlreadyHold:
    """The narrowing that makes filing a deposit SAFE (**R-GH**, **R-HT(a)**).

    **Recording a deposit the books already hold is the only way this door can
    count money twice**, and the outflow half's narrowing does not cover it:
    that one withholds where the pass did not finish LOOKING, which is a fact
    about a search.  A deposit's hazard is that its own pay period already
    holds unexplained income which could BE it.

    Under a human tick the card renders that warning and the person decides.
    There is no person here, so the rule withholds wherever the card would have
    warned -- and the sentence says which rows, so the owner can go and look.

    **Measured on the developer's own account 2026-08-31, over all 16
    recordable inflows**: quiet for the five dividends (`$0.12`-`$0.22`) and
    the three merchant credits (`$11.73`-`$28.29`), and firing for the seven
    payroll deposits and the `$200.00` member deposit -- whose period holds a
    `$2,473.38` row and a `$100.00` row, so `$200.00` is not provably outside
    them.  So it holds back exactly the line whose books cannot be shown to be
    missing it, and costs this step nothing it was built for.
    """

    def test_a_deposit_its_period_may_already_hold_is_WITHHELD(
        self, app, db, seed_user,
    ):
        """The money property: no row is written, and the receipt says why."""
        with app.app_context():
            a_rule(
                seed_user, "Dividend Earned",
                income_category_id=seed_user["categories"]["Salary"].id,
            )
            day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
            # An unexplained income row of $100.00 in the SAME period, so a
            # $150.00 deposit is not provably outside it.
            a_transaction(
                seed_user, name="Some income", amount="100.00",
                income=True,
            )
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="150.00",
                merchant="Dividend Earned",
                description="DIVIDEND EARNED (Dividend Earned)",
                posted_on=day,
            )
            db.session.flush()
            before = _balance_on(seed_user, day + timedelta(days=2))

            filing = _file(seed_user, statement)
            db.session.flush()

            story = _story(filing)
            assert story["filed"] == 0
            assert story["refused"] == []
            assert len(story["withheld"]) == 1
            assert "count the same money twice" in story["withheld"][0]
            # **The FIGURE as money.**  This sentence is composed in a SERVICE,
            # so no template filter reaches it: it read a bare ``100.00``
            # beside a card reading ``$100.00`` until plan step
            # ``bank_import:X-gj-2b`` gave the fact ONE composer
            # (``ArrivalsAlreadyHeld.why_it_could_double_count``), which the
            # refund half reads too.
            assert "$100.00" in story["withheld"][0]
            assert "holds 100.00" not in story["withheld"][0]
            # THE MONEY: nothing moved.
            assert _balance_on(seed_user, day + timedelta(days=2)) == before

    def test_a_deposit_SMALLER_than_every_such_row_is_still_filed(
        self, app, db, seed_user,
    ):
        """The guard is a PROOF, not a threshold, and this is its other side.

        A deposit smaller than the SMALLEST unexplained income row in its
        period cannot be any subset of them, every one being positive -- so
        there is nothing for the owner to check and withholding it would be the
        warn-on-every-row shape this package measures money going through.
        **This is the case the whole step exists for**: the developer's five
        dividends sit in periods holding payroll rows worth thousands.
        """
        with app.app_context():
            a_rule(
                seed_user, "Dividend Earned",
                income_category_id=seed_user["categories"]["Salary"].id,
            )
            day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
            a_transaction(
                seed_user, name="Payroll", amount="2473.38",
                income=True,
            )
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="0.15",
                merchant="Dividend Earned",
                description="DIVIDEND EARNED (Dividend Earned)",
                posted_on=day,
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }

    def test_the_bound_is_the_SMALLEST_row_and_not_the_largest(
        self, app, db, seed_user,
    ):
        """The `min` this class's docstring rests on, graded at last.

        **Both cases above stage a period holding exactly ONE unexplained
        income row, where ``min`` and ``max`` are the same number** -- so the
        PROOF the guard rests on (*a deposit smaller than the smallest of them
        cannot be any subset of them, every one being positive*) was asserted
        by neither.  A mutation review flipped that ``min`` to ``max`` and this
        class stayed green; a pre-existing case elsewhere caught it, which is
        not the same as this class grading its own claim.

        Two rows and a deposit BETWEEN them is the discriminating shape, and it
        is the developer's own: his `$200.00` member deposit sits in a period
        holding `$2,473.38` and `$100.00`.
        """
        with app.app_context():
            a_rule(
                seed_user, "Dividend Earned",
                income_category_id=seed_user["categories"]["Salary"].id,
            )
            day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
            a_transaction(
                seed_user, name="Big", amount="2473.38", income=True,
            )
            a_transaction(
                seed_user, name="Small", amount="100.00", income=True,
            )
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="200.00",
                merchant="Dividend Earned",
                description="DIVIDEND EARNED (Dividend Earned)",
                posted_on=day,
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            # Under `max` the deposit is BELOW 2473.38 and would be filed;
            # under `min` it is at or above 100.00 and is withheld.
            story = _story(filing)
            assert story["filed"] == 0, story
            assert "count the same money twice" in story["withheld"][0]

    def test_the_DOORS_own_refusal_is_reported_as_a_withholding(
        self, app, db, seed_user,
    ):
        """A day the door refuses is a SENTENCE, not a refusal on the receipt.

        The branch that asks ``inflow.withheld`` before the rule's own
        narrowings was reachable by no case (mutation review 2026-08-31):
        deleting it left the deposit to reach the door and be REFUSED there
        instead, which files nothing either way but reports it as a failure
        rather than as something left for the owner.  The receipt's two
        registers mean different things and this is the one written for a
        person.
        """
        with app.app_context():
            a_rule(
                seed_user, "Dividend Earned",
                income_category_id=seed_user["categories"]["Salary"].id,
            )
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="0.15",
                merchant="Dividend Earned",
                description="DIVIDEND EARNED (Dividend Earned)",
                posted_on=date(2029, 1, 3),
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            story = _story(filing)
            assert story["filed"] == 0
            assert story["refused"] == [], (
                "a day the pass can see is not a door refusal"
            )
            assert len(story["withheld"]) == 1
            assert "has not happened yet" in story["withheld"][0]

    def test_an_ARCHIVED_income_category_is_REPORTED_and_files_nothing(
        self, app, db, seed_user,
    ):
        """A rule naming a retired category is reported, never substituted for.

        Filing into a category the owner has archived would resurrect it
        silently; falling back to NO category would file the money somewhere
        they did not name, under a receipt saying their rule ran.  Both are the
        substitution :mod:`~._placement` refuses, so the answer is a sentence.
        """
        with app.app_context():
            category_id = seed_user["categories"]["Salary"].id
            a_rule(
                seed_user, "Dividend Earned", income_category_id=category_id,
            )
            # **Re-fetched INSIDE this context, never assigned on the bundle's
            # own object.**  ``seed_user`` was built in a different app
            # context, so mutating the instance it holds leaves the change in a
            # session this context never flushes -- and the case would go on
            # reading an ACTIVE category while claiming to test an archived
            # one.  Measured: the first draft did exactly that, and the deposit
            # was filed.  ``test_directory`` records the same trap.
            db.session.get(Category, category_id).is_active = False
            db.session.flush()
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="0.15",
                merchant="Dividend Earned",
                description="DIVIDEND EARNED (Dividend Earned)",
                posted_on=(
                    seed_user["bootstrap_period"].start_date
                    + timedelta(days=3)
                ),
            )
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            story = _story(filing)
            assert story["filed"] == 0
            assert len(story["withheld"]) == 1
            assert "archived" in story["withheld"][0]


class TestARuleWithholdsADepositThePassDidNotFinishLookingAt:
    """Ruling **R-GH**'s FIRST narrowing, in the inflow direction.

    **This class exists because the narrowing was missing and the omission
    moved money** (adversarial code review 2026-08-31).  ``_inflow_filings``
    asked only whether the deposit's own pay period already held unexplained
    income, and its docstring claimed that was *the only way this door can
    count money twice*.  It is not: ``_verdict.ruled`` withholds an outflow
    whenever the pass did not finish LOOKING, and a deposit has the same
    hazard.

    **The two guards have DISJOINT fail sets, which is why one cannot stand in
    for the other.**  ``arrivals_already_held_in`` tests the ROW's own span
    (``expected_on .. expected_through``), so it sees only rows in the
    deposit's pay period; the near tier reaches ACROSS periods by
    ``DAY_WINDOW``.  A candidate row one period over is invisible to the first
    and visible to the second -- which is the state this case stages.
    """

    def test_a_deposit_a_tier_DECLINED_to_conclude_about_is_not_filed(
        self, app, db, seed_user,
    ):
        """The money property: no row is written where the pass hedged.

        Two of the owner's own income rows sit either side of the deposit's
        figure, so the near tier CONTESTS it and declines rather than picking
        one -- the developer's own measured *Apple Music* shape, in the inflow
        direction.  The rows are in the period BEFORE the deposit, so the
        double-count guard is silent about them and only the search gap sees
        the collision.
        """
        with app.app_context():
            a_rule(
                seed_user, "Dividend Earned",
                income_category_id=seed_user["categories"]["Salary"].id,
            )
            # Two candidate rows in the BOOTSTRAP period, either side of the
            # figure, so no tier can choose between them.
            a_transaction(
                seed_user, name="Maybe this one", amount="1496.00",
                income=True,
            )
            a_transaction(
                seed_user, name="Or this one", amount="1504.00", income=True,
            )
            later = a_later_period(seed_user)
            statement = an_import(seed_user)
            a_bank_line(
                seed_user, statement, amount="1500.00",
                merchant="Dividend Earned",
                description="DIVIDEND EARNED (Dividend Earned)",
                posted_on=later.start_date,
            )
            db.session.flush()
            before = _balance_on(seed_user, later.start_date)

            filing = _file(seed_user, statement)
            db.session.flush()

            story = _story(filing)
            assert story["filed"] == 0, story
            assert len(story["withheld"]) == 1
            assert "did not finish looking" in story["withheld"][0]
            # THE MONEY: nothing was written, so nothing can be counted twice.
            assert _balance_on(seed_user, later.start_date) == before


class TestAMerchantCreditIsFILEDWhicheverSpendingAnswerItHas:
    """Ruling **R-HT(a)** and **R-II**: a credit is the merchant rule's INVERSE.

    A merchant credit from a merchant whose SPENDING the owner has placed is a
    REFUND -- a NEGATIVE purchase back into that same container -- rather than
    income.  Plan step ``bank_import:X-gj-2b-2`` is what files it; until then
    this class asserted the same two arms REPORTING and filing nothing.

    **Both container answers, because only one of them was ever graded**
    (mutation review 2026-08-31).  The refund arm fires for
    ``rule.answer in (TEMPLATE, NEW_ENVELOPE)`` and every case staged a
    TEMPLATE -- so a mutation giving NEW_ENVELOPE an income arm survived 959
    tests and auto-filed a credit as income, which is exactly the misfiling
    ruling **R-HX** refused.  **The stakes rose with this step**: that arm now
    MOVES MONEY where it previously only chose a sentence.

    **The untested arm was the developer's own biggest case.**  Measured on a
    clone 2026-08-31: his `Amazon` rule is a NEW ENVELOPE answer and his
    `Walmart` rule is a TEMPLATE one, so of the merchant credits in his inbox
    the largest -- `$28.29`, and `$86.67` across the whole span -- takes the
    arm nothing exercised.
    """

    def _credit(self, seed_user, statement, merchant, *, posted_on=None):
        """Stage one recorded CREDIT from *merchant*."""
        return a_bank_line(
            seed_user, statement, amount="42.00", merchant=merchant,
            description=f"POINT OF SALE CREDIT L340 ({merchant})",
            posted_on=posted_on or (
                seed_user["bootstrap_period"].start_date + timedelta(days=3)
            ),
        )

    def test_a_NEW_ENVELOPE_answer_files_the_refund_into_the_minted_envelope(
        self, app, db, seed_user,
    ):
        """The arm the developer's Amazon rule takes -- and the money moves.

        Asserts the SIGN and the BALANCE, not merely that something was filed:
        a refund booked as a POSITIVE purchase would still report
        ``filed == 1`` while taking money the bank had just given back.
        """
        with app.app_context():
            a_rule(
                seed_user, "Amazon", envelope_name="Amazon",
                category_id=seed_user["categories"]["Groceries"].id,
            )
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
            self._credit(seed_user, statement, "Amazon", posted_on=day)
            db.session.flush()
            before = _balance_on(seed_user, day + timedelta(days=2))

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            minted = (
                db.session.query(Transaction)
                .filter(Transaction.name == "Amazon")
                .one()
            )
            assert [p.amount for p in _purchases_in(minted)] == [
                Decimal("-42.00"),
            ]
            # THE MONEY: the bank gave it back, so the balance RISES by exactly
            # what the line says -- the opposite direction to a swipe, through
            # the same door and with no sign branch anywhere in it.
            assert _balance_on(seed_user, day + timedelta(days=2)) == (
                before + Decimal("42.00")
            )

    def test_a_TEMPLATE_answer_files_it_the_same_way(
        self, app, db, seed_user,
    ):
        """The arm his Walmart rule takes, so the pair covers both containers.

        Stated as its own case rather than a parametrize, because the two
        answers are stored in DIFFERENT columns and a shared fixture that got
        the column wrong would stage one shape twice.
        """
        with app.app_context():
            envelope = _groceries(seed_user)
            a_rule(seed_user, "Walmart", template_id=envelope.template_id)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
            self._credit(seed_user, statement, "Walmart", posted_on=day)
            db.session.flush()
            before = _balance_on(seed_user, day + timedelta(days=2))

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing) == {
                "filed": 1, "withheld": [], "refused": [],
            }
            assert [p.amount for p in _purchases_in(envelope)] == [
                Decimal("-42.00"),
            ]
            assert _balance_on(seed_user, day + timedelta(days=2)) == (
                before + Decimal("42.00")
            )

    def test_an_UNCLAIMED_deposit_is_still_not_a_purchase(
        self, app, db, seed_user,
    ):
        """The other side of the partition, asserted beside it.

        **This is the case that keeps the refund arm honest.**  What makes a
        credit a refund is the owner's own SPENDING answer for that merchant;
        a deposit from a merchant they have said nothing about is not one, and
        filing it against a budget line would be the guess ruling **R-HX**
        refused.

        **It asserts which LIST the screen puts the line in, and a first
        version asserting only that nothing was filed graded NOTHING** --
        adversarial review 2026-09-01 drove a dispatcher mutated to route every
        inflow into the purchase pipeline and all three cases in this class
        survived it.  They had to: an unclaimed deposit resolves to no
        container either way, so the auto-filing door writes nothing under the
        mutant exactly as it does under the rule, and ``filed == 0`` is true of
        both.  What the two states DISAGREE about is where the line is offered
        -- :attr:`~._reads.ReviewSet.recordable_inflows` under the rule and
        :attr:`~._reads.ReviewSet.creatable` under the mutant -- so that is
        what this asks.
        """
        with app.app_context():
            _groceries(seed_user)
            statement = an_import(seed_user)
            line = self._credit(seed_user, statement, "Some Employer")
            db.session.flush()

            filing = _file(seed_user, statement)
            db.session.flush()

            assert _story(filing)["filed"] == 0
            assert db.session.query(TransactionEntry).count() == 0

            offered = review_set(a_scope(seed_user))
            assert [
                item.line.line_id for item in offered.recordable_inflows
            ] == [line.id], (
                "an unclaimed deposit belongs to the INCOME door -- routing "
                "it to the purchase pipeline is what R-HX refused"
            )
            assert line.id not in [
                item.line.line_id for item in offered.creatable
            ]
