"""Ruling R-FO end to end: a true-up's counter leg says what the difference WAS.

Balance arc, plan step **X-f3d**.  A non-loan account's balance assertion posts
a balanced two-leg correction, and until this step BOTH legs' destinations were
fixed: the account's linked ledger, and its equity row.  For a Roth IRA or a
Property that second leg is wrong -- the difference is investment return -- and
booking it to equity is why ``$10,653.91`` of return earned over 4.5 months was
invisible on the income statement (measured on a production clone 2026-08-13).

What these cases pin, and each is a thing that could ship wrong on its own:

* the DISPATCH -- an ``INTEREST`` account's true-up books to Interest Income,
  an ``INVESTMENT`` / ``APPRECIATING`` account's to Change in Value, and
  a ``PLAIN`` account's stays on equity (unchanged, so the cutover X-f3c owns
  it);
* the OPENING -- which books to equity for EVERY kind, because capital brought
  onto the books is not something earned;
* BALANCE NEUTRALITY -- the linked leg, and therefore every balance the app
  reports for the account, is byte-identical either side of the ruling;
* the SELF-MIGRATION -- an account whose true-up is already posted against
  equity re-points on the next sync, with no backfill, as one balanced delta;
* the STATEMENTS -- the income statement reports the gain BELOW net income and
  the balance sheet still ties out, which are the two ways this change could
  have gone silently wrong (counted as earnings, or left out of equity).

The oracle is deliberately independent of the writer: every assertion reads
``budget.account_postings`` back through test-authored queries, and the
statement cases read the public report functions rather than the chart.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingSourceEnum,
)
from app.extensions import db as _db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.services import account_posting_service, ledger_report_service
from app.services.ledger_report_service import StatementWindow
from app.utils.dates import display_today
from app.services.pay_calendar import calendar_for
from app.services.ledger_lines import (
    account_counter_key,
    chart_record_type,
    linked_key,
)
from tests._ledger_parity import assert_ledger_parity
from tests._ledger_reads import ledger_net, owner_worked_out_lines
from tests._stored_ledger import ledger_account_of_kind
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
)

# The account types this step's dispatch actually separates, and the counter
# kind each one's TRUE-UP must land in.  Written as the built-in type NAME
# rather than the projection kind so the case exercises the whole chain the
# app really walks -- ``ref.account_types``' boolean flags -> ``classify_account``
# -> the dispatch -> the chart row -- instead of starting halfway down it.
_TRUE_UP_COUNTER_BY_TYPE = [
    ("Checking", LedgerAccountKindEnum.ANCHOR_EQUITY,
     LedgerAccountClassEnum.EQUITY),
    ("Money Market", LedgerAccountKindEnum.INTEREST_INCOME,
     LedgerAccountClassEnum.INCOME),
    ("Roth IRA", LedgerAccountKindEnum.UNREALIZED_CHANGE,
     LedgerAccountClassEnum.UNREALIZED),
    ("Property", LedgerAccountKindEnum.UNREALIZED_CHANGE,
     LedgerAccountClassEnum.UNREALIZED),
]


def _correction_legs(account_id, source_kind):
    """Return ``{(kind name, class name): net}`` of one correction kind on an account.

    Off the worked-out read, over every key whose record is the account: its own
    line and its correction counters, because a re-typed true-up moves only the
    counter side.  A cell netting ``$0.00`` is left out.  Named by the enums' string
    values so a case states the row it means.
    """
    source_kind_id = ref_cache.posting_source_id(source_kind)
    owner_id = _db.session.get(Account, account_id).user_id
    nets = {}
    for _scenario_id, line in owner_worked_out_lines(_db.session, owner_id):
        if (
            line.source_kind_id == source_kind_id
            and chart_record_type(line.key) is Account
            and line.key.record_id == account_id
        ):
            cell = (line.key.kind.value, line.key.ledger_class.value)
            nets[cell] = nets.get(cell, Decimal("0")) + line.amount
    return {cell: net for cell, net in nets.items() if net != 0}


_TRUE_UP_DAYS_AFTER_OPENING = 1


def _true_up_day() -> date:
    """Return the civil day every fixture true-up in this module is observed on."""
    return display_today() - timedelta(days=3 - _TRUE_UP_DAYS_AFTER_OPENING)


def _true_up(account, balance, days_after_opening=None):
    """Assert a later balance on *account* and drive the reconcile.

    Appends the ``AccountAnchorHistory`` row the true-up door would stage,
    observed *days_after_opening* days after the opening's own day (defaulting
    to :data:`_TRUE_UP_DAYS_AFTER_OPENING`), then runs the SAME all-scenarios
    sync the chokepoint calls.  A case asserting TWICE passes an explicit
    offset for the second, so the two land on different civil days and
    therefore in different correction keys.

    **The day comes off the opening's ``observed_on``, not its ``created_at``.**
    An account's opening is RECORDED now and observed three days back, so
    offsetting the recording instant would date the true-up in the FUTURE --
    where a balance sheet as-of today excludes it whole and the case would
    fail for a reason that has nothing to do with the ruling.  Which row is
    the OPENING is decided business-date first
    (``cash_ledger.cash_anchor_facts``), so the later ``observed_on`` is what
    makes this a true-up; ``created_at`` only breaks a same-day tie and is
    nudged forward to keep that tie deterministic.
    """
    # Pylint: ``import-outside-toplevel`` -- the model is loaded lazily here
    # for the same collection-time-safety reason ``tests/_test_helpers``
    # documents; this module is otherwise app-symbol free at import.
    # pylint: disable=import-outside-toplevel
    from app.models.account import AccountAnchorHistory

    opening = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.observed_on, AccountAnchorHistory.id)
        .first()
    )
    _db.session.add(AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        created_at=opening.created_at + timedelta(
            seconds=days_after_opening or _TRUE_UP_DAYS_AFTER_OPENING,
        ),
        observed_on=opening.observed_on + timedelta(
            days=days_after_opening or _TRUE_UP_DAYS_AFTER_OPENING,
        ),
        # The ENTERED day moves with the observed one (**N-299**): the column
        # defaults to the wall clock, which a historical row must not inherit.
        recorded_on=opening.observed_on + timedelta(
            days=days_after_opening or _TRUE_UP_DAYS_AFTER_OPENING,
        ),
    ))
    _db.session.flush()
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )


def account_asset_class_name(account):
    """Return the class name of *account*'s own line.

    So a case asserting on a correction's legs states the class the line actually
    carries instead of assuming Asset -- which the negatively-anchored liability
    shapes elsewhere in this suite would falsify.
    """
    return linked_key(account).ledger_class.value


def _fund_before_the_opening(seed_user, account, amount):
    """Settle a transfer INTO *account*, dated a day before its opening.

    Builds the shape the case above needs: an account whose opening assertion
    the records already explain, so its opening correction books nothing.  The
    transfer is settled on the day BEFORE the opening's ``observed_on``, which
    the walk absorbs into the opening (an assertion is the closing balance for
    its own day), leaving ``ledger_before`` equal to the asserted figure.
    """
    return create_settled_transfer(
        seed_user, _db.session,
        from_account=seed_user["account"],
        to_account=account,
        period=seed_user["bootstrap_period"],
        amount=Decimal(str(amount)),
        settled_on=display_today() - timedelta(days=4),
    )


def _retype(account, type_name):
    """Re-type *account* in place and drive the sync the route drives.

    The state every production modelled account is in the moment this code
    deploys, reproduced without a migration: the true-up is already posted
    against equity, and the dispatch now names a different counter row for it.
    ``routes.accounts.crud.update_account`` does exactly this pair -- assign the
    type, then ``sync_account_anchor_postings_all_scenarios`` -- inside one
    request.
    """
    # Pylint: ``import-outside-toplevel`` -- lazy app import, as above.
    # pylint: disable=import-outside-toplevel
    from app.models.ref import AccountType

    account.account_type_id = (
        _db.session.query(AccountType).filter_by(name=type_name).one().id
    )
    _db.session.flush()
    _db.session.expire(account)
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )



def _opened_account(seed_user, type_name, name, opening):
    """Create an account of *type_name* opened three days ago at *opening*."""
    return create_account_of_type(
        seed_user, _db.session, type_name, name,
        anchor_balance=Decimal(str(opening)),
        observed_on=display_today() - timedelta(days=3),
    )


class TestTheTrueUpCounterLegNamesTheDifference:
    """The dispatch, end to end from the account TYPE to the posted leg."""

    @pytest.mark.parametrize(
        "type_name,expected_kind,expected_class", _TRUE_UP_COUNTER_BY_TYPE,
    )
    def test_a_true_up_books_its_counter_leg_by_account_kind(
        self, app, db, seed_user, type_name, expected_kind, expected_class,
    ):
        """Each account kind's true-up books its counter leg where R-FO says.

        A $1,000.00 opening trued up to $1,150.00 books ``+$150.00`` on the
        linked ledger and ``-$150.00`` on the counter row -- and WHICH counter
        row is the whole ruling.  The class is asserted beside the kind because
        no CHECK ties them: a value change booked into an Equity-class row
        would balance perfectly and still leave the income statement silent.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, type_name, f"Dispatch {type_name}", "1000.00",
            )
            _true_up(account, "1150.00")

            true_up_legs = _correction_legs(
                account.id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert true_up_legs == {
                ("linked", account_asset_class_name(account)):
                    Decimal("150.00"),
                (expected_kind.value, expected_class.value):
                    Decimal("-150.00"),
            }

    @pytest.mark.parametrize(
        "type_name,expected_kind,_expected_class", _TRUE_UP_COUNTER_BY_TYPE,
    )
    def test_the_opening_books_to_equity_whatever_the_account_is(
        self, app, db, seed_user, type_name, expected_kind, _expected_class,
    ):
        """An OPENING always books to the equity row, never to the true-up's.

        Capital brought onto the books is not something earned.  A Property
        opened at $350,000.00 booking that to Change in Value would say the
        house appreciated by its whole value on the day it was recorded -- so
        the opening arm answers before the dispatch is consulted, and this
        pins it on every kind including the two the dispatch sends elsewhere.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, type_name, f"Opening {type_name}", "1000.00",
            )
            _true_up(account, "1150.00")

            opening_legs = _correction_legs(
                account.id, PostingSourceEnum.ACCOUNT_OPENING,
            )
            assert opening_legs == {
                ("linked", account_asset_class_name(account)):
                    Decimal("1000.00"),
                ("anchor_equity", "Equity"): Decimal("-1000.00"),
            }
            # And the true-up's counter row really is a DIFFERENT row whenever
            # the dispatch names one -- otherwise this case would pass on an
            # account that never dispatched at all.
            if expected_kind is not LedgerAccountKindEnum.ANCHOR_EQUITY:
                assert ledger_account_of_kind(
                    _db.session, account.id, expected_kind,
                ) is not None

    def test_an_opening_the_records_explain_still_leaves_a_gain_a_gain(
        self, app, db, seed_user,
    ):
        """An opening that books NOTHING does not make the next correction capital.

        **This case exists because a first fix of a sibling defect failed it.**
        The create form pre-filled Opening Balance with ``0``, so an owner who
        took the default left the account's earliest assertion booking a zero
        delta -- and re-keying "the opening" onto the first correction that
        BOOKS something answered that, at the cost of this: an account opened at
        a figure the RECORDS ALREADY EXPLAIN books nothing at its opening too,
        and its first real market gain would then be treated as capital and
        buried in equity.  That is the defect ruling R-FO exists to close.

        Reproduced exactly: a Roth opened at ``$1,000.00`` with a ``$1,000.00``
        settled transfer already dated before it.  The ``$150.00`` that follows
        is a market move and must read as one.  The ``$0`` pre-fill is fixed on
        the FORM, where it belongs.

        **The delta-keyed rule is now unreachable rather than merely rejected**
        (plan step X-f3c-2a).  Which correction opens the books is read from the
        stored ``budget.account_openings`` row, so no property of the DELTA
        series can re-elect it -- the refinement that failed this case has
        nothing left to key on.  The opening therefore books the account's
        stored equity here, where it booked NOTHING before: the transfer dated
        before the books opened is the pre-opening double count **N-378**
        measures, and X-f3c-2b closes it.  What this case grades is unchanged
        and is the whole point -- the ``$150.00`` after it still reads as a
        market gain and not as capital.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, "Roth IRA", "Explained Opening", "1000.00",
            )
            _fund_before_the_opening(seed_user, account, "1000.00")
            account_posting_service.sync_account_anchor_postings_all_scenarios(
                account.id,
            )
            # The opening books the account's STORED equity, whatever the
            # records happen to explain -- which is the premise that replaced
            # "the opening books nothing" and the reason no delta-keyed rule
            # can re-elect it.
            assert _correction_legs(
                account.id, PostingSourceEnum.ACCOUNT_OPENING,
            ) == {
                ("linked", "Asset"): Decimal("1000.00"),
                ("anchor_equity", "Equity"): Decimal("-1000.00"),
            }

            _true_up(account, "1150.00")

            gain = account_counter_key(
                LedgerAccountKindEnum.UNREALIZED_CHANGE, account.id,
            )
            # +$1,000.00 from the opening-day correction, less the $150.00
            # market gain.  **The $1,000.00 is finding N-378 wearing this
            # account's clothes and is PINNED rather than endorsed**: the
            # transfer is dated before the books opened, so the fold counts it
            # twice, the opening assertion corrects it straight back out, and
            # on a MODELLED account that correction's counter is
            # ``unrealized_change`` (ruling R-FO) -- so the double count reads
            # as a market LOSS that never happened.  Plan step X-f3c-2b closes
            # it at the write door; until then this is what the ledger says and
            # a test that hid it would be worse than one that states it.
            assert ledger_net(
                _db.session, gain, seed_user["scenario"].id,
            ) == Decimal("850.00"), (
                "a market gain after an opening the records explained was "
                "booked as capital, which is the defect R-FO closes"
            )

    def test_a_zero_opening_is_still_the_opening(
        self, app, db, seed_user,
    ):
        """The account's EARLIEST assertion opens the books, even asserting $0.

        The rule is a property of the assertion history, not of the delta
        series, so it answers the same way in every scenario and cannot be
        moved by what happens to be posted.  A ``$0`` opening followed by the
        real figure therefore reads that figure as a change in value rather
        than as capital -- which is why the create form was changed to ASK for
        the opening balance instead of pre-filling ``0``
        (``templates/accounts/form.html``): the defect was a default nobody
        chose, and fixing it in the ledger rule cost the case above.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, "Roth IRA", "Zero Opened", "0.00",
            )
            _true_up(account, "22909.02")

            assert _correction_legs(
                account.id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == {
                ("linked", account_asset_class_name(account)):
                    Decimal("22909.02"),
                ("unrealized_change", "Unrealized"): Decimal("-22909.02"),
            }

    def test_the_linked_leg_is_untouched_by_the_dispatch(
        self, app, db, seed_user,
    ):
        """Balance neutrality: only the COUNTER side moved.

        Two accounts differing ONLY in type -- one Checking (which still books
        to equity), one Roth IRA (which now books a change in value) -- given
        identical openings and identical true-ups must carry identical linked
        ledgers.  That equality is what makes this step safe to ship without
        moving a figure the owner reads: every balance the app reports comes
        off the linked ledger or the cash fold, never off the counter row.
        """
        with app.app_context():
            plain = _opened_account(
                seed_user, "Checking", "Neutral Checking", "1000.00",
            )
            modelled = _opened_account(
                seed_user, "Roth IRA", "Neutral Roth", "1000.00",
            )
            for account in (plain, modelled):
                _true_up(account, "1150.00")

            scenario_id = seed_user["scenario"].id
            plain_linked = linked_key(plain)
            modelled_linked = linked_key(modelled)
            assert ledger_net(_db.session, plain_linked, scenario_id) == Decimal("1150.00")
            assert ledger_net(_db.session, modelled_linked, scenario_id) == (
                ledger_net(_db.session, plain_linked, scenario_id)
            )


class TestThePostedLedgerSelfMigrates:
    """An already-posted equity true-up re-points on the next sync, no backfill."""

    def test_an_equity_trueup_re_points_to_unrealized_change(
        self, app, db, seed_user,
    ):
        """The reconcile emits ONE balanced delta and the linked leg does not move.

        The upgrade path this step ships with: the migration seeds ref rows and
        moves no leg, because the reconcile reads back EVERY leg of a
        correction key and drives it to the new target.  Reproduced by posting
        the true-up while the account still classifies as Checking, re-typing
        it to a Roth IRA, and re-syncing -- which is exactly the state every
        production modelled account is in the moment this code deploys.

        Two things are asserted because either alone would pass a broken
        implementation: the true-up's $150.00 leaves equity for the gain line, and
        the linked ledger is UNCHANGED (the migration is balance-neutral).  The
        read holds no reversal, so the parity line grades that the store reversed
        the old leg rather than leaving it behind to double-count equity.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, "Checking", "Re-pointed", "1000.00",
            )
            _true_up(account, "1150.00")

            scenario_id = seed_user["scenario"].id
            equity = account_counter_key(
                LedgerAccountKindEnum.ANCHOR_EQUITY, account.id,
            )
            linked = linked_key(account)
            assert ledger_net(_db.session, equity, scenario_id) == (
                Decimal("-1150.00")
            )
            linked_before = ledger_net(_db.session, linked, scenario_id)

            # The account is now an INVESTMENT, so the dispatch names a
            # different counter row for its true-up.
            _retype(account, "Roth IRA")

            gain = account_counter_key(
                LedgerAccountKindEnum.UNREALIZED_CHANGE, account.id,
            )
            assert ledger_net(_db.session, gain, scenario_id) == (
                Decimal("-150.00")
            )
            # The opening stays on equity; only the true-up's $150.00 moved.
            assert ledger_net(_db.session, equity, scenario_id) == (
                Decimal("-1000.00")
            )
            assert ledger_net(_db.session, linked, scenario_id) == (
                linked_before
            )
            assert_ledger_parity(db.session, scenario_id)

    def test_a_second_sync_after_the_re_point_writes_nothing(
        self, app, db, seed_user,
    ):
        """The re-point happens ONCE, however many times the sync runs.

        **This case exists because the implementation failed it**, and it
        failed on real data: a second deploy backfill against a production
        clone re-emitted all 14 re-point deltas, and every pass after it would
        have too -- the value-change row growing without bound, equity
        shrinking to match, and the linked ledger and the trial balance both
        perfectly correct the whole way, so nothing else would have said a word.

        The cause was the posted-side reader's SCOPE.  It selected the entries
        carrying a leg on the account's LINKED ledger, which was sound while
        every correction had one; a re-point moves only the counter side, so
        its delta entry has no linked leg and the reader could not see the very
        entry it had just written.
        ``_posting_reconcile.posted_correction_legs`` now scopes to the
        account's own chart rows.

        **The RE-POINT is the whole precondition, and a first version of this
        case did not reach it** (adversarial review, 2026-08-14): it opened the
        account as a Roth, so every correction booked straight to the gain row,
        carried a linked leg, and stayed visible to the narrow reader -- the
        entire suite passed with the fix reverted.  The account must therefore
        be re-typed FIRST, and the assertion is an ENTRY COUNT, so a second
        delta that happened to net to zero still fails.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, "Checking", "Idempotent Re-point", "1000.00",
            )
            _true_up(account, "1150.00")
            _retype(account, "Roth IRA")
            entries_after_the_re_point = _correction_entry_count(account.id)

            for _ in range(2):
                account_posting_service.\
                    sync_account_anchor_postings_all_scenarios(account.id)

            assert _correction_entry_count(account.id) == (
                entries_after_the_re_point
            )

    def test_the_re_point_leaves_every_entry_balanced(
        self, app, db, seed_user,
    ):
        """No entry sums to anything but zero after the move.

        The delta the re-point emits carries a reversal leg and a fresh leg and
        no linked leg at all (its delta is zero and is dropped), which is the
        one entry shape this step introduces.  A whole-ledger sweep is the
        cheapest way to say that shape is legal.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, "Checking", "Balanced Re-point", "1000.00",
            )
            _true_up(account, "1015.01")
            # The re-type is what PRODUCES the counter-only entry this case
            # sweeps for; without it no such entry exists and the sweep is
            # vacuous (adversarial review, 2026-08-14).
            _retype(account, "Money Market")

            unbalanced = _db.session.execute(_db.text("""
                SELECT je.id, SUM(p.amount)
                FROM budget.journal_entries je
                JOIN budget.account_postings p ON p.journal_entry_id = je.id
                GROUP BY je.id
                HAVING SUM(p.amount) <> 0
            """)).all()
            assert unbalanced == []


class TestTheStatementsReportTheDifference:
    """The two ways this change could have gone silently wrong."""

    def test_an_unrealized_change_is_reported_below_net_income(
        self, app, db, seed_user,
    ):
        """The gain appears on the income statement WITHOUT entering net income.

        The defect R-FO closes is that investment return was invisible; the
        defect it must not introduce is that return reads as earnings.  Both
        are asserted at once: the Unrealized section carries the figure, the
        Income section does not, net income excludes it, and comprehensive
        income is the sum.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, "Roth IRA", "Reported Roth", "1000.00",
            )
            _true_up(account, "1150.00")
            _db.session.commit()

            # The window follows the CORRECTION's civil day, not today's: the
            # suite's clock sweep runs at month and year boundaries, where an
            # as-of-today month would not contain a true-up dated two days ago.
            observed = _true_up_day()
            report = ledger_report_service.compute_income_statement(
                seed_user["user"].id, calendar_for(seed_user["user"].id),
                StatementWindow(
                    window_type="month",
                    month=observed.month,
                    year=observed.year,
                ),
            )

            assert report.unrealized.total == Decimal("150.00")
            assert [line.label for line in report.unrealized.lines] == [
                "Reported Roth -- Change in Value",
            ]
            assert all(
                "Unrealized" not in line.label for line in report.income.lines
            )
            assert report.comprehensive_income == (
                report.net_income + Decimal("150.00")
            )

    def test_an_interest_true_up_is_ordinary_income(
        self, app, db, seed_user,
    ):
        """An INTEREST account's true-up lands in Income, not below the line.

        The other half of the dispatch: interest a savings account paid IS
        earnings, so it belongs in net income.  Asserting it lands in the
        Income section is what keeps the two arms from collapsing into one.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, "Money Market", "Reported MM", "1000.00",
            )
            _true_up(account, "1015.01")
            _db.session.commit()

            # The window follows the CORRECTION's civil day, not today's: the
            # suite's clock sweep runs at month and year boundaries, where an
            # as-of-today month would not contain a true-up dated two days ago.
            observed = _true_up_day()
            report = ledger_report_service.compute_income_statement(
                seed_user["user"].id, calendar_for(seed_user["user"].id),
                StatementWindow(
                    window_type="month",
                    month=observed.month,
                    year=observed.year,
                ),
            )

            income_labels = [line.label for line in report.income.lines]
            assert "Reported MM -- Interest Income" in income_labels
            assert report.unrealized.lines == []
            assert report.comprehensive_income == report.net_income

    def test_the_balance_sheet_still_ties_out_with_a_gain_posted(
        self, app, db, seed_user,
    ):
        """Assets == Liabilities + Equity, with the gain folded into equity.

        The trial balance is what a new reporting class can break: every class
        outside Assets and Liabilities has to be folded into equity exactly
        once, so a class added WITHOUT its closing line puts the sheet out by
        that class's whole net.  The accumulated line is asserted by value, not
        just by presence, so a line that renders the wrong figure still fails.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, "Roth IRA", "Tie-out Roth", "1000.00",
            )
            _true_up(account, "1150.00")
            _db.session.commit()

            report = ledger_report_service.compute_balance_sheet(
                seed_user["user"].id, display_today(),
            )

            accumulated = [
                line for line in report.equity.lines
                if line.label == "Accumulated Change in Value"
            ]
            assert [line.amount for line in accumulated] == [Decimal("150.00")]
            assert report.tie_out.in_balance
            assert report.tie_out.assets == (
                report.tie_out.liabilities_plus_equity
            )
            assert report.tie_out.ledger_net == 0

    def test_an_owner_with_no_position_gets_no_unrealized_line(
        self, app, db, seed_user,
    ):
        """A plain-checking owner's statements are untouched by this step.

        The regression guard for everyone the ruling does not concern: with no
        investment or property, the income statement has no Unrealized section
        content, comprehensive income equals net income, and the balance sheet
        carries no accumulated line -- a ``$0.00`` line would tell the owner
        they hold a position they do not.
        """
        with app.app_context():
            account = _opened_account(
                seed_user, "Checking", "Plain Only", "1000.00",
            )
            _true_up(account, "1150.00")
            _db.session.commit()

            observed = _true_up_day()
            income = ledger_report_service.compute_income_statement(
                seed_user["user"].id, calendar_for(seed_user["user"].id),
                StatementWindow(
                    window_type="month",
                    month=observed.month,
                    year=observed.year,
                ),
            )
            sheet = ledger_report_service.compute_balance_sheet(
                seed_user["user"].id, display_today(),
            )

            assert income.unrealized.lines == []
            assert income.unrealized.total == Decimal("0.00")
            assert income.comprehensive_income == income.net_income
            assert [
                line.label for line in sheet.equity.lines
                if line.key is None
            ] == ["Retained Earnings"]
            assert sheet.tie_out.in_balance


class TestTheDispatchIsNotVacuous:
    """Negative controls: each guard is SHOWN to fire."""

    def test_booking_every_kind_to_equity_fails_the_dispatch_case(
        self, app, db, seed_user, monkeypatch,
    ):
        """The pre-R-FO behaviour, planted, is caught.

        A guard whose control does not fire is not a guard.  Forcing the
        dispatch back to its old answer -- everything to ``anchor_equity`` --
        must make the Roth IRA case fail; if it does not, that case is
        asserting something the old code already satisfied.
        """
        # Pylint: ``import-outside-toplevel`` -- lazy app import, as above.
        # pylint: disable=import-outside-toplevel
        # The dispatch table lives in the worked-out chart since plan task
        # plan#2526 (it was ``ledger_account_service._counters``'), so the
        # planted regression is planted there.
        from app.services.ledger_lines import _chart

        monkeypatch.setattr(
            _chart, "_TRUEUP_COUNTER_KINDS",
            dict.fromkeys(
                _chart._TRUEUP_COUNTER_KINDS,
                LedgerAccountKindEnum.ANCHOR_EQUITY,
            ),
        )
        with app.app_context():
            account = _opened_account(
                seed_user, "Roth IRA", "Planted Roth", "1000.00",
            )
            _true_up(account, "1150.00")

            legs = _correction_legs(
                account.id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert ("unrealized_change", "Unrealized") not in legs, (
                "the planted regression did not take effect, so the dispatch "
                "cases above are not testing the dispatch"
            )
            assert legs[("anchor_equity", "Equity")] == Decimal("-150.00")

    def test_the_unrealized_class_is_credit_normal(self, app, db):
        """A gain presents POSITIVE, which only holds if the seed is right.

        ``is_debit_normal`` has no server default and is set by the seed alone;
        seeding it TRUE would invert every unrealized figure on both statements
        while leaving the trial balance closed, so it is pinned where the
        reader actually consults it.
        """
        with app.app_context():
            class_id = ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.UNREALIZED,
            )
            assert ref_cache.ledger_class_is_debit_normal(class_id) is False


def _correction_entry_count(account_id):
    """Return how many anchor-correction journal entries an account carries.

    Counted over the account's OWN chart rows rather than its linked row, for
    the reason ``_posting_reconcile.posted_correction_legs`` is scoped that way:
    a re-point delta touches only counter rows, so a linked-scoped count would
    be blind to exactly the entries the idempotency case is about.
    """
    entry_ids = (
        _db.session.query(Posting.journal_entry_id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(LedgerAccount.account_id == account_id)
    )
    correction_sources = [
        ref_cache.posting_source_id(source)
        for source in (
            PostingSourceEnum.ACCOUNT_OPENING,
            PostingSourceEnum.ACCOUNT_TRUEUP,
        )
    ]
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.source_kind_id.in_(correction_sources),
            JournalEntry.id.in_(entry_ids),
        )
        .count()
    )
