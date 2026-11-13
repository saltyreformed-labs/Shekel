"""The hero reports a day both records can speak for, and a caption counts what its tab draws."""

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import StatementBalanceEvidenceEnum
from app.services import bank_agreement
from app.services.balance_at import BalanceContext
from app.services.statement_match import Tab, reconcile_page
from app.services.statement_match._accepted_view import accepted_counts, accepted_register
from app.services.bank_agreement import AgreementDay, BankAgreement, ComparedSpan
from app.services.statement_import import RecordedRun
from app.services.statement_match._release import acts_of
from tests.test_services.test_outstanding_difference import _opened_on, _settle
from tests.test_services.test_statement_import.test_anchor import _seed_import

from tests.test_services.test_statement_match.test_reads_lineless import _planted_lineless

from tests.test_services.test_statement_match._builders import (
    a_scope,
    an_envelope,
    an_unexplained_outflow,
    filed_by,
)

from ._helpers import _page, _counts


class TestTheHeroReportsADayBOTHRecordsCanSpeakFor:
    """Plan step ``bank_import:X-gj-1a``'s reason for a new derivation.

    :attr:`~app.services.bank_agreement.BankAgreement.standing_gap` reads the
    span's LAST day whether or not an anchor reaches it, so a hero built on it
    prints a dash for an account whose statements simply stop before the span
    does -- which is :attr:`unpriced_days`, a state the report already
    measures and expects.
    """

    def _agreement(self, days, account_id=1, opened_on=None, last_record_on=None):
        """Return an agreement over hand-built days.

        Args:
            days: The :class:`~app.services.bank_agreement.AgreementDay`
                values, ascending.
            account_id: Which account it is about.  Stated rather than left at
                the seeded account's id by luck, because
                :func:`~app.services.statement_match.reconcile_page` refuses a
                pass and an agreement that name different accounts.
            opened_on: The day the books open, or ``None`` for the day before
                the first hand-built day -- which makes every day one the
                books hold movement on, as :meth:`_day` builds it.  A case
                building a day before the books open, or the opening day
                itself, passes the day it built them around.
            last_record_on: The books' last record, or ``None`` -- which every
                hand-built day being quiet makes the default.  A case about
                records running past the last statement (finding
                **BAL-651**) passes the day they run to.

        Returns:
            The :class:`~app.services.bank_agreement.BankAgreement`.

        **``runs`` is ONE run covering the whole hand-built span** (plan step
        ``balance:X-f3c-3`` added the coverage; ``balance:X-bj-1b`` made the
        run the one home of it).  It is deliberately not empty: an empty list
        means "no import covers any of these days", which is a real state and
        not the one this class is about -- every case here is about the HERO,
        and a hand-built agreement claiming its own days are unimported would
        be describing a different account than the one its ``days`` describe.
        The run anchors on nothing; the hand-built days carry their own bank
        balances, which is what the hero reads.
        """
        return BankAgreement(
            account_id=account_id,
            span=ComparedSpan(
                first_day=days[0].day, last_day=days[-1].day,
                recorded_from=days[0].day, recorded_through=days[-1].day,
            ),
            books_opened_on=(
                days[0].day - timedelta(days=1) if opened_on is None
                else opened_on
            ),
            last_record_on=last_record_on,
            runs=[RecordedRun(days[0].day, days[-1].day, None, ())],
            days=list(days),
        )

    def _day(self, day, bank, books):
        """Return one day AFTER the books open, quiet on both sides.

        Args:
            day: The civil day.
            bank: What the bank's record prices it at, or ``None``.
            books: What the app says the account held.

        Returns:
            The :class:`~app.services.bank_agreement.AgreementDay`, with both
            movements known and ``0.00``.
        """
        return AgreementDay(
            day=day, bank_lines=Decimal("0.00"), recorded=Decimal("0.00"),
            asserted=Decimal("0.00"), app_balance=Decimal(books),
            bank_balance=None if bank is None else Decimal(bank),
        )

    def _before_the_books(self, day, bank):
        """Return one day BEFORE the books open.

        The books state neither a movement nor a balance there (rulings
        **R-BAL286**, **R-BAL291**), so both are ``None``; the bank's side is
        whatever its record says.

        Args:
            day: The civil day.
            bank: What the bank's record prices it at, or ``None``.

        Returns:
            The :class:`~app.services.bank_agreement.AgreementDay`.
        """
        return AgreementDay(
            day=day, bank_lines=Decimal("0.00"), recorded=None,
            asserted=None, app_balance=None,
            bank_balance=None if bank is None else Decimal(bank),
        )

    def _opening(self, day, bank, books):
        """Return the day the books OPEN: a balance stated, no movement held.

        Args:
            day: The civil day.
            bank: What the bank's record prices it at, or ``None``.
            books: What the books state at the close of that day.

        Returns:
            The :class:`~app.services.bank_agreement.AgreementDay`.
        """
        return AgreementDay(
            day=day, bank_lines=Decimal("0.00"), recorded=None,
            asserted=None, app_balance=Decimal(books),
            bank_balance=None if bank is None else Decimal(bank),
        )

    def test_it_walks_BACK_past_a_day_the_bank_cannot_price(self):
        """The whole reason it is not ``span.last_day``."""
        agreement = self._agreement([
            self._day(date(2026, 8, 20), "2459.60", "2501.31"),
            self._day(date(2026, 8, 21), None, "2501.31"),
        ])

        headline = agreement.headline

        assert agreement.standing_gap is None
        assert headline.day == date(2026, 8, 20)
        assert headline.bank_balance == Decimal("2459.60")
        assert headline.gap == Decimal("41.71")

    def test_a_day_the_books_state_NO_balance_for_is_not_a_comparison(self):
        """Finding **N-314**, ruling **R-BAL286**: a bank figure alone is no hero.

        Built in the order every real comparison has, the books' figures
        appearing at their opening and never vanishing after it: Aug 18 lies
        before the books open on Aug 19, so the bank's ``2459.60`` there has no
        books balance to stand beside; Aug 19 and 20 state the books' level
        and the bank prices neither.  No day carries both figures, so there is
        no headline -- a hero on Aug 18 would set the bank's balance against a
        figure the books never stated.  *This case hand-built a day with no
        records AFTER a day with them until plan step balance:X-f3c-2b-2c, a
        state no account can be in.*
        """
        agreement = self._agreement([
            self._before_the_books(date(2026, 8, 18), "2459.60"),
            self._opening(date(2026, 8, 19), None, "2501.31"),
            self._day(date(2026, 8, 20), None, "2501.31"),
        ], opened_on=date(2026, 8, 19))

        assert agreement.headline is None
        assert agreement.standing_gap is None

    def test_the_page_refuses_DONE_over_a_comparison_that_stopped_early(
        self, app, db, seed_user,
    ):
        """A balanced comparison three weeks old is not *nothing left to do*.

        The headline walks BACK to the last priceable day, so an account whose
        statements stop before its records do can report ``off by $0.00`` about
        a week that is not this one.
        """
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), None, "2459.60"),
        ], account_id=seed_user["account"].id)

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.off_by == Decimal("0.00")
        assert page.hero.to_explain == 0
        assert page.hero.unpriced_after == 1
        assert page.is_done is False

    def test_a_current_and_balanced_account_IS_done(
        self, app, db, seed_user,
    ):
        """The mirror, so the refusal above is not refusing everything."""
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), "2459.60", "2459.60"),
        ], account_id=seed_user["account"].id)

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.unpriced_after == 0
        assert page.is_done is True

    def test_records_past_the_last_statement_REFUSE_done_and_are_named(
        self, app, db, seed_user,
    ):
        """Finding **BAL-651**, ruling plan#2346: not Done until a statement prices them.

        The statement prices Aug 19, where the books agree to the cent, and the
        books' last record is Aug 21, so the comparison draws Aug 20 and Aug 21
        unpriced.  Before the fix it ended on Aug 19 and read Done.  The page
        says how far it is priced and how far the records run.
        """
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), None, "2459.60"),
            self._day(date(2026, 8, 21), None, "2459.60"),
        ], account_id=seed_user["account"].id,
            last_record_on=date(2026, 8, 21))

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.off_by == Decimal("0.00")
        assert page.hero.to_explain == 0
        assert page.hero.day == date(2026, 8, 19)
        assert page.hero.unpriced_after == 2
        assert page.hero.records_through == date(2026, 8, 21)
        assert page.is_done is False

    def test_records_ending_ON_the_priced_day_are_not_named(
        self, app, db, seed_user,
    ):
        """The mirror: a statement reaching the last record is Done, and names none.

        The books' last record is the priced day itself, so no record waits
        on a statement and the hero's ``records_through`` is ``None``.
        """
        an_envelope(seed_user)
        db.session.commit()
        agreement = self._agreement([
            self._day(date(2026, 8, 19), "2459.60", "2459.60"),
            self._day(date(2026, 8, 20), "2459.60", "2459.60"),
        ], account_id=seed_user["account"].id,
            last_record_on=date(2026, 8, 20))

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=agreement)

        assert page.hero.records_through is None
        assert page.is_done is True

    def test_a_pass_and_an_agreement_for_two_accounts_are_REFUSED(
        self, app, db, seed_user,
    ):
        """Two arguments a caller pairs by hand are two chances to mispair.

        The consequence is one account's hero over another's lines, with no
        figure on the page wrong enough to look wrong.
        """
        an_envelope(seed_user)
        db.session.commit()
        other = self._agreement([
            self._day(date(2026, 8, 20), "1.00", "1.00"),
        ], account_id=seed_user["account"].id + 999)

        with pytest.raises(ValueError) as refused:
            _page(seed_user, Tab.TO_EXPLAIN, agreement=other)

        assert "cannot show one account's lines beside another's" in str(
            refused.value,
        )

    @pytest.mark.parametrize(("typed", "off_by", "done"), [
        ("500.00", Decimal("0.00"), True),
        ("510.00", Decimal("10.00"), False),
    ])
    def test_the_statement_the_opening_was_TYPED_FROM_can_be_done(
        self, app, db, seed_user, seed_periods, typed, off_by, done,
    ):
        """Ruling **R-BAL286**: the opening day's balance IS compared.

        An account created on Feb 2 at *typed*, from a statement ending Feb 2
        whose bank states ``$500.00`` that day; its lines (Jan 30 ``-25.00``,
        Feb 2 ``-10.00``) are all inside the opening, and none is waiting to
        be explained.  Typed right, the hero is Feb 2 at ``$500.00`` against
        ``$500.00`` and the page is DONE; typed ``$510.00``, it is off by
        ``510.00 - 500.00 = +10.00`` and not done.  *The app before plan step
        balance:X-f3c-2b-2c gave this same hero and Done: the origination
        assertion made Feb 2 its first cash fact, so the day was compared.*
        """
        account = _opened_on(seed_user, date(2026, 2, 2), typed)
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 2, 2),
            evidence=StatementBalanceEvidenceEnum.FILE_CHAIN,
            lines=[(date(2026, 1, 30), "-25.00"),
                   (date(2026, 2, 2), "-10.00")],
        )
        db.session.commit()
        agreement = bank_agreement.bank_agreement(
            account, BalanceContext.build(seed_user["user"].id),
        )

        page = reconcile_page(
            a_scope(seed_user, account), agreement, Tab.TO_EXPLAIN,
        )

        assert page.hero.day == date(2026, 2, 2)
        assert page.hero.bank == Decimal("500.00")
        assert page.hero.books == Decimal(typed)
        assert page.hero.off_by == off_by
        assert page.hero.unpriced_after == 0
        assert page.hero.to_explain == 0
        assert page.is_done is done

    def test_a_GAP_day_after_the_hero_counts_as_UNPRICED(
        self, app, db, seed_user, seed_periods,
    ):
        """A day between two imports is drawn, not compared, and unpriced.

        Books open Feb 2 at ``$500.00``.  The first statement's one line is
        Feb 3's ``-40.00``, entered, and it states ``$460.00`` there; the
        second states no balance and its lines are Feb 6 and Feb 7's
        ``-5.00``, entered.  Nothing is imported for Feb 4 - Feb 5.  The hero
        is Feb 3, ``460.00`` against ``460.00``.  After it come four drawn
        days the books state a balance for and the bank prices none of: the
        two gap days, whose bank movement is unknown so they are not
        compared, and the second statement's two days.  So
        ``unpriced_after`` is ``2 + 2 = 4``; counting only the compared
        days after the hero would say ``2``, and a "priced through" note two
        days short.
        """
        account = _opened_on(seed_user, date(2026, 2, 2))
        _seed_import(
            db, account, stated="460.00", effective_on=date(2026, 2, 3),
            evidence=StatementBalanceEvidenceEnum.FILE_CHAIN,
            lines=[(date(2026, 2, 3), "-40.00")], file_name="first.csv",
        )
        _seed_import(
            db, account, stated=None,
            lines=[(date(2026, 2, 6), "-5.00"), (date(2026, 2, 7), "-5.00")],
            file_name="unpriced.csv",
        )
        for amount, day in (("40.00", date(2026, 2, 3)),
                            ("5.00", date(2026, 2, 6)),
                            ("5.00", date(2026, 2, 7))):
            _settle(
                seed_user, seed_periods[2], amount, day, account=account,
            )
        db.session.commit()
        agreement = bank_agreement.bank_agreement(
            account, BalanceContext.build(seed_user["user"].id),
        )

        page = reconcile_page(
            a_scope(seed_user, account), agreement, Tab.TO_EXPLAIN,
        )

        assert [day.day for day in agreement.days if not day.compared] == [
            date(2026, 2, 4), date(2026, 2, 5),
        ]
        assert page.hero.day == date(2026, 2, 3)
        assert page.hero.off_by == Decimal("0.00")
        assert page.hero.unpriced_after == 4

    def test_an_account_no_import_prices_has_no_headline_at_all(self):
        """Reporting an unknown as agreement is what this arc exists to stop."""
        agreement = self._agreement([
            self._day(date(2026, 8, 20), None, "2501.31"),
        ])

        assert agreement.headline is None

    def test_the_page_states_all_three_figures_or_none(
        self, app, db, seed_user,
    ):
        """A hero printing two of three invites a subtraction it cannot do."""
        an_envelope(seed_user)
        db.session.commit()

        hero = _page(seed_user, Tab.TO_EXPLAIN, agreement=None).hero

        assert (hero.day, hero.bank, hero.books, hero.off_by) == (
            None, None, None, None,
        )
        assert hero.to_explain == 0

    def test_done_is_never_claimed_for_an_account_nothing_prices(
        self, app, db, seed_user,
    ):
        """An empty inbox is half of *done*, and the other half is unknown."""
        an_envelope(seed_user)
        db.session.commit()

        page = _page(seed_user, Tab.TO_EXPLAIN, agreement=None)

        assert page.hero.to_explain == 0
        assert page.is_done is False


class TestACaptionCountsOnlyWhatItsTabCanDraw:
    """Finding **bank_import:N-389**, plan step ``bank_import:X-gj-1c``.

    An act that names no bank line has no day, no amount and no wording, so it
    is not a card and never was.  The two readers disagreed about it:
    :func:`~app.services.statement_match._accepted_view.accepted_counts`
    counted the table, and the fold that builds the cards skipped it in Python
    -- so one such act made the Explained caption one higher than the tab could
    deliver.  Measured on a planted act 2026-08-31 before the fix: caption
    ``1``, rendered ``0``, withheld ``0``.

    :data:`~app.services.statement_match._acts.NAMES_A_BANK_LINE` is that
    invariant stated once, in SQL, and both readers narrow on it.

    **The state needs a code defect to reach**, which is why it is planted
    through raw SQL: ``record_match`` refuses an empty side at the one writer,
    ``fk_statement_match_members_line_account`` refuses to remove a line a
    match names, and migration ``e4a7c0f13b92`` deleted the acts that already
    held none.  A case over an account with no such act grades nothing here --
    both numbers are already equal -- so every assertion below stands beside a
    REAL act, which is what makes the equality mean something.
    """

    def _one_real_and_one_lineless(self, seed_user, db):
        """Stage one act a tab can draw, and one it cannot.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
        """
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-12.34",
        )
        db.session.commit()
        filed_by(seed_user, line, envelope, by_rule=False)
        db.session.commit()
        _planted_lineless(db, seed_user)

    def test_the_caption_equals_what_the_tab_delivers(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: this read ``1`` against ``0 + 0`` before the fix."""
        self._one_real_and_one_lineless(seed_user, db)

        page = _page(seed_user, Tab.EXPLAINED)
        rendered = sum(section.count for section in page.sections)
        withheld = sum(section.withheld for section in page.sections)

        assert rendered == 1, "the REAL act must be drawn, or nothing is graded"
        assert _counts(page)[Tab.EXPLAINED] == rendered + withheld

    def test_the_counts_exclude_it_and_the_loader_never_yields_it(
        self, app, db, seed_user,
    ):
        """One clause, asked of both readers, over a set holding both kinds."""
        self._one_real_and_one_lineless(seed_user, db)
        owner, account = seed_user["user"].id, seed_user["account"].id

        counts = accepted_counts(owner, account)

        assert (counts.total, counts.by_rule, counts.by_hand) == (1, 0, 1)
        assert len(acts_of(owner, account)) == 1
        assert len(accepted_register(owner, account).shown) == 1

    def test_reaching_one_is_an_ALARM_and_not_a_silence(
        self, app, db, seed_user, caplog,
    ):
        """Skipping SILENTLY was the original defect (reviews, 2026-08-20).

        Such an act goes on claiming its transactions in ``matched_subjects``,
        so those rows can never be matched again and no release control exists
        to free them.  The alarm moved from the fold to the counts read with
        the clause; what may not happen is that it stops being raised.
        """
        self._one_real_and_one_lineless(seed_user, db)

        with caplog.at_level(logging.ERROR):
            _page(seed_user, Tab.EXPLAINED)

        assert any(
            record.__dict__.get("event") == "statement_match_lineless"
            for record in caplog.records
        )
