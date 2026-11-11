"""The ledger follows a restatement, and the door refuses a matched line it would swallow."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AccountOpeningSourceEnum, LedgerAccountKindEnum, PostingSourceEnum
from app.exceptions import ValidationError
from app.models.account_opening import AccountOpening
from app.services.cash_ledger import account_opening_fact, earliest_recorded_movement_day
from app.services.opening_service import (
    BooksOpening,
    OpeningRestatementOutcome,
    apply_opening_restatement,
)
from app.services.ledger_lines import account_counter_key, linked_key
from tests._ledger_reads import scenario_lines
from tests._test_helpers import account_never_asserted, match_two_lines

from ._helpers import _ONE_DAY, _cash_account, _opening_count, _linked_ledger_total


class TestThePostedLedgerFollows:
    """The half a reader would assume, measured instead.

    ``budget.account_openings`` is read by BOTH the balance fold and the
    ledger (ruling **R-GX**), and the ``account_opening`` line is DATED on
    ``opened_on``.  Moving either fact therefore moves that line, and a ledger
    that kept it on the old day would describe an opening the app no longer
    holds.
    """

    def test_the_opening_entry_moves_to_the_new_day_and_the_old_one_nets_to_zero(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The opening books on its new day and holds nothing on its old one.

        The account's OWN line sums to the new equity at the new day and to
        nothing at the old one, rather than to both.
        """
        with app.app_context():
            account = _cash_account(
                seed_user, "LedgerFollows", anchor_balance=Decimal("400.00"),
            )
            db.session.commit()
            old_day = account_opening_fact(account.id).opened_on
            new_day = old_day - _ONE_DAY

            apply_opening_restatement(
                account=account,
                opening=BooksOpening(new_day, Decimal("650.00")),
            )

            # The account's OWN leg, day by day.  Summing over ``account_id``
            # instead would fold the balanced counter leg in beside it and
            # read 0.00 on every state -- broken tree and fixed one alike.
            scenario_id = seed_user["scenario"].id
            opening = PostingSourceEnum.ACCOUNT_OPENING
            assert _linked_ledger_total(
                scenario_id, account, opening, new_day,
            ) == Decimal("650.00")
            assert _linked_ledger_total(
                scenario_id, account, opening, old_day,
            ) == Decimal("0.00")
            # And ACROSS every day: the opening books exactly once.
            assert _linked_ledger_total(
                scenario_id, account, opening,
            ) == Decimal("650.00")

    def test_the_opening_books_its_counter_to_ANCHOR_EQUITY(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The counter leg names what the difference WAS (ruling **R-FO**).

        An opening is contributed capital, so its counter is ``anchor_equity``
        -- never ``interest_income`` or ``unrealized_change``, which are what a
        TRUE-UP books on an interest-bearing or modelled account.  A
        restatement that re-keyed the entry but let the dispatch fall through
        to the true-up branch would leave the balance right and put the whole
        opening on the income statement as a day-one return, which is the
        defect R-FO exists to prevent.

        **This case replaced an app-wide trial-balance assertion that could not
        fail** (adversarial review, 2026-08-31): while the door wrote the store,
        ``ck_account_postings_balanced``, a deferred per-entry constraint, aborted
        its COMMIT on a single-legged emission, so the sum was identically zero
        whenever a commit succeeded.  It graded "the restatement commits", which
        the case above already grades with figures.
        """
        with app.app_context():
            account = _cash_account(
                seed_user, "CounterLeg", anchor_balance=Decimal("400.00"),
            )
            db.session.commit()
            new_day = account_opening_fact(account.id).opened_on - _ONE_DAY

            apply_opening_restatement(
                account=account,
                opening=BooksOpening(new_day, Decimal("650.00")),
            )

            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            counters = {
                line.key: line.amount
                for line in scenario_lines(db.session, seed_user["scenario"].id)
                if line.source_kind_id == opening_source
                and line.key.record_id == account.id
                and line.key != linked_key(account)
            }

            # The linked line is +650.00 (asserted above), so its counter is
            # -650.00 and it lands on the equity key and nowhere else.
            assert counters == {
                account_counter_key(
                    LedgerAccountKindEnum.ANCHOR_EQUITY, account.id,
                ): Decimal("-650.00"),
            }

    def test_a_restatement_re_bases_the_LATER_assertions_correction(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Moving the level moves every correction stacked on it.

        This is what the door's own message warns the owner about, measured:
        the account's assertions still say what they said, so raising the
        opening by ``$100.00`` drops the first assertion's correction by the
        same ``$100.00`` rather than being absorbed.  An owner told nothing
        would read the unchanged balance as the door having failed.
        """
        with app.app_context():
            account = _cash_account(
                seed_user, "Rebased", anchor_balance=Decimal("400.00"),
            )
            db.session.commit()
            standing = account_opening_fact(account.id)

            def _trueup_total():
                return _linked_ledger_total(
                    seed_user["scenario"].id, account, PostingSourceEnum.ACCOUNT_TRUEUP,
                )

            # The ABSOLUTES, hand-computed, because a delta whose two sides
            # come from one producer read twice passes on a build carrying a
            # constant offset on both.  The account opens at $400.00 and its
            # origination assertion says $400.00, so the true-up's correction
            # is 400.00 - 400.00 = 0.00.
            assert _trueup_total() == Decimal("0.00")

            apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    standing.opened_on,
                    standing.opening_equity + Decimal("100.00"),
                ),
            )

            # Raising the opening to $500.00 leaves the assertion saying
            # $400.00, so the correction becomes 400.00 - 500.00 = -100.00.
            # That is the sentence the door's own message warns about.
            assert _trueup_total() == Decimal("-100.00")


class TestTheDoorRefusesAMatchedLineItWouldSwallow:
    """The DOOR asks the matched-line bound, not just the bound existing.

    Plan step **balance:X-f3c-2b-2b**.  **This class exists because a planted
    defect survived without it** (2026-08-31): deleting the door's call to
    ``cash_ledger.reject_books_open_on_or_after_matched_lines`` left the whole
    suite green, because the service suite graded the FUNCTION and the
    database suite graded the TRIGGER and nothing joined them.

    What that costs is precisely what the pairing exists to prevent: the
    constraint would still refuse the restatement, at COMMIT, as the
    driver's ``RaiseException`` -- so an owner typing a date would meet a
    500 instead of the sentence ``app.opening_infrastructure`` says an
    ordinary date box gets.
    """

    def test_the_door_gives_a_SENTENCE_not_a_commit_abort(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL for the door's own call.

        The account records NO movement, so the movement bound returns without
        counting anything and the matched-line bound is the only thing between
        the owner and a double count.  The refusal must be this module's
        ``ValidationError``, and the assertion on the type is the half that
        fails when the door stops asking: the trigger's abort is a database
        rule's refusal (SQLSTATE ``P0001``), not a designed one.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Door Matched",
            )
            db.session.flush()
            opened = seed_periods[0].start_date
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=opened,
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()
            early = opened + timedelta(days=10)
            match_two_lines(
                db.session, account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )
            assert earliest_recorded_movement_day(account.id) is None, (
                "this case isolates the MATCHED-LINE bound; a movement would "
                "make the bound beside it refuse first"
            )
            before = _opening_count(account)

            with pytest.raises(
                ValidationError, match="matched a bank line",
            ):
                apply_opening_restatement(
                    account=account,
                    opening=BooksOpening(
                        early + timedelta(days=5), Decimal("10.00"),
                    ),
                )

            db.session.rollback()
            assert _opening_count(account) == before

    def test_a_day_BELOW_the_matched_line_still_restates(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The bound is a bound: the door still accepts a legal day.

        Without this the case above passes against a door that refuses every
        restatement on an account that has ever matched a line, which is the
        mutation a single refusal case cannot see.
        """
        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Door Matched Below",
            )
            db.session.flush()
            opened = seed_periods[0].start_date
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=opened,
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()
            early = opened + timedelta(days=10)
            match_two_lines(
                db.session, account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )

            outcome = apply_opening_restatement(
                account=account,
                opening=BooksOpening(
                    early - timedelta(days=1), Decimal("12.00"),
                ),
            )

            assert outcome is OpeningRestatementOutcome.COMMITTED
            assert account_opening_fact(
                account.id,
            ).opened_on == early - timedelta(days=1)
