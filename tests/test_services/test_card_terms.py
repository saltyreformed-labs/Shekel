"""
Shekel Budget App -- the card's terms as a dated series (``app.services.card_terms``)

Plan#2331, developer ruling **plan#2333**: a card's terms are versions, each
in force from its ``effective_date`` until the next one's, so a change governs
from its day and every statement and reward before it stands.  What is
pinned:

* the done-when of plan#2331, on plan#2333's own example saved through
  :func:`~app.services.card_terms.set_terms`: the statement a Feb 10 purchase
  is on -- its window, due date and minimum -- and the cash back rate the
  purchase reads are the same before and after the Mar 1 version is saved,
  and the statement after is the changeover cycle (the walk itself,
  :func:`~app.services.card_statement.terms_in_effect`, is graded beside it in
  ``tests/test_services/test_card_statement.py``);
* the read (:func:`~app.services.card_terms.load_card_term_versions`) is the
  account's own versions, earliest first;
* the write (:func:`~app.services.card_terms.set_terms`): a new date adds a
  version and leaves the one before it as it was; the SAME date corrects that
  version in place (one row, its id kept, every term rewritten, a blank
  nullable term cleared, ``updated_at`` moved) -- the second write graded,
  because a producer right on the first write can be wrong on the repeat;
* the remove (:func:`~app.services.card_terms.remove_terms`, developer ruling
  **plan#2587**): the removed version's days re-derive from the version the
  walk then names, graded on the ruling's own example (a cut to 1.0% mistyped
  Jan 1 for Mar 1, then removed and saved again) and on the earliest version
  removed, whose days the next one then governs; the card's only version is
  refused and stays; a row of another card, an unknown id and any id on a
  dormant card are ``False``, writing nothing.  The two-tab race is graded at
  the door (``tests/test_routes/test_card_terms.py``), because the owner's
  lock that serialises it is a request's.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.exceptions import CardTermsRemovalRefused
from app.extensions import db
from app.models.credit_card_params import CreditCardParams
from app.services.card_statement import (
    CycleWindow,
    cycle_containing,
    due_date_for,
    minimum_payment,
    terms_in_effect,
)
from app.services.card_terms import (
    load_card_term_versions,
    remove_terms,
    set_terms,
)
from tests._test_helpers import create_account_of_type

#: The terms through February: plan#2333's worked example.
_FEBRUARY = {
    "statement_close_day": 20,
    "payment_due_day": 15,
    "min_payment_percent": Decimal("0.0200"),
    "min_payment_floor": Decimal("25.00"),
    "cashback_rate": Decimal("0.0150"),
    "auto_redeem_threshold": Decimal("25.00"),
    "credit_limit": Decimal("5000.00"),
}

#: The terms from Mar 1: the close moves to the 5th, the due day to the 30th,
#: cash back drops to 1.0%, and the two nullable terms are blanked.
_MARCH = {
    "statement_close_day": 5,
    "payment_due_day": 30,
    "min_payment_percent": Decimal("0.0200"),
    "min_payment_floor": Decimal("25.00"),
    "cashback_rate": Decimal("0.0100"),
    "auto_redeem_threshold": None,
    "credit_limit": None,
}

#: The examples' purchase (rulings plan#2333 and plan#2587): $600.00 on Feb 10.
_FEB_10 = date(2026, 2, 10)
_PURCHASE = Decimal("600.00")


def _card(seed_user, name="Visa"):
    """A committed Credit Card account of *seed_user*'s, with no terms."""
    account = create_account_of_type(seed_user, db.session, "Credit Card", name)
    db.session.commit()
    return account


def _set(account, effective_date, terms):
    """State *terms* from *effective_date* and commit, as the door does."""
    set_terms(account.id, account.user_id, effective_date, terms)
    db.session.commit()
    db.session.expire_all()


class TestADateChangeLeavesThePastStanding:
    """Plan#2331's done-when, on plan#2333's example, through saved versions."""

    @staticmethod
    def _what_feb_10_reads(account):
        """The Feb 10 purchase's statement, its due date and minimum, and its cash back rate."""
        versions = load_card_term_versions(account.id)
        window = cycle_containing(versions, _FEB_10)
        return (
            window,
            due_date_for(versions, window.closes),
            minimum_payment(versions, window.closes, _PURCHASE),
            terms_in_effect(versions, _FEB_10).cashback_rate,
        )

    def test_saving_march_leaves_februarys_statement_and_cash_back(self, app, seed_user):
        """The Feb 20 statement keeps $600.00 due Mar 15, minimum $25.00, and $9.00 back.

        Close 20, due 15, 2% with a $25.00 floor, 1.5% back: the Feb 10
        purchase is on ``[Jan 20, Feb 20)``, due Mar 15; its minimum is
        max(25.00, 2% x 600.00 = 12.00) = $25.00 and its cash back
        600.00 x 0.0150 = $9.00.  Saving March's terms (close 5, due 30,
        1.0%) from Mar 1 moves none of it; the statement after is the
        changeover cycle ``[Feb 20, Mar 5)``, due Mar 30.
        """
        with app.app_context():
            account = _card(seed_user)
            february = {**_FEBRUARY, "min_payment_percent": Decimal("0.0200")}
            _set(account, date(2026, 1, 1), february)
            before = self._what_feb_10_reads(account)
            assert before == (
                CycleWindow(date(2026, 1, 20), date(2026, 2, 20)),
                date(2026, 3, 15),
                Decimal("25.00"),
                Decimal("0.0150"),
            )
            assert _PURCHASE * before[3] == Decimal("9.00")

            _set(account, date(2026, 3, 1), _MARCH)

            assert self._what_feb_10_reads(account) == before
            versions = load_card_term_versions(account.id)
            after_the_change = cycle_containing(versions, date(2026, 2, 25))
            assert after_the_change == CycleWindow(date(2026, 2, 20), date(2026, 3, 5))
            assert due_date_for(versions, after_the_change.closes) == date(2026, 3, 30)
            # A Mar 10 purchase reads March's 1.0%: 600.00 x 0.0100 = $6.00.
            assert _PURCHASE * terms_in_effect(
                versions, date(2026, 3, 10),
            ).cashback_rate == Decimal("6.00")


class TestTheReadAndTheWrite:
    """``load_card_term_versions`` and ``set_terms`` against the table."""

    def test_a_new_date_adds_a_version_and_the_one_before_stands(
        self, app, seed_user,
    ):
        """February's terms, then March's from Mar 1: two versions, February's unchanged."""
        with app.app_context():
            account = _card(seed_user)
            _set(account, date(2026, 1, 1), _FEBRUARY)
            first_id = load_card_term_versions(account.id)[0].id

            _set(account, date(2026, 3, 1), _MARCH)

            versions = load_card_term_versions(account.id)
            assert [v.effective_date for v in versions] == [
                date(2026, 1, 1), date(2026, 3, 1),
            ]
            assert versions[0].id == first_id
            for column, value in _FEBRUARY.items():
                assert getattr(versions[0], column) == value, column
            for column, value in _MARCH.items():
                assert getattr(versions[1], column) == value, column
            assert {v.user_id for v in versions} == {seed_user["user"].id}

    def test_the_same_date_corrects_that_version_in_place(self, app, seed_user):
        """A second save on Mar 1: one March version, its id kept, every term rewritten.

        Every term changes, the two blank ones gaining values, so a
        correction that skipped any column leaves it as it was and fails here.
        """
        with app.app_context():
            account = _card(seed_user)
            _set(account, date(2026, 1, 1), _FEBRUARY)
            _set(account, date(2026, 3, 1), _MARCH)
            march = load_card_term_versions(account.id)[1]
            march_id, march_updated = march.id, march.updated_at
            corrected = {
                **_MARCH,
                "statement_close_day": 6,
                "payment_due_day": 1,
                "min_payment_percent": Decimal("0.0300"),
                "min_payment_floor": Decimal("35.00"),
                "cashback_rate": Decimal("0.0125"),
                "auto_redeem_threshold": Decimal("50.00"),
                "credit_limit": Decimal("8000.00"),
            }

            _set(account, date(2026, 3, 1), corrected)

            versions = load_card_term_versions(account.id)
            assert len(versions) == 2
            assert versions[1].id == march_id
            for column, value in corrected.items():
                assert getattr(versions[1], column) == value, column
            # MOVED, not "later": this module freezes PostgreSQL's ``now()`` for
            # a query (``set_terms`` stamps with it) while a column DEFAULT reads
            # the real clock (``tests/conftest.py``'s clock note), so the two
            # stamps are on different clocks here.  What is graded is that the
            # ``ON CONFLICT`` arm stamps at all -- it runs no ORM ``onupdate``.
            assert versions[1].updated_at != march_updated
            for column, value in _FEBRUARY.items():
                assert getattr(versions[0], column) == value, column

    def test_a_correction_clears_a_blanked_nullable_term(self, app, seed_user):
        """February stated with a threshold and a limit, re-saved blank: both NULL."""
        with app.app_context():
            account = _card(seed_user)
            _set(account, date(2026, 1, 1), _FEBRUARY)

            _set(account, date(2026, 1, 1), {
                **_FEBRUARY, "auto_redeem_threshold": None, "credit_limit": None,
            })

            (version,) = load_card_term_versions(account.id)
            assert version.auto_redeem_threshold is None
            assert version.credit_limit is None

    def test_the_read_is_the_accounts_own_versions_earliest_first(
        self, app, seed_user,
    ):
        """Written out of order, read earliest first; another card's are not read."""
        with app.app_context():
            account = _card(seed_user)
            other = _card(seed_user, name="Other")
            _set(account, date(2026, 3, 1), _MARCH)
            _set(account, date(2026, 1, 1), _FEBRUARY)
            _set(other, date(2026, 2, 1), _FEBRUARY)

            versions = load_card_term_versions(account.id)

            assert [v.effective_date for v in versions] == [
                date(2026, 1, 1), date(2026, 3, 1),
            ]
            assert {v.account_id for v in versions} == {account.id}
            assert db.session.query(CreditCardParams).count() == 3

    def test_a_dormant_card_reads_no_versions(self, app, seed_user):
        """No terms saved: an empty series, which the gate reads as dormant."""
        with app.app_context():
            account = _card(seed_user)
            assert load_card_term_versions(account.id) == []


def _version_on(account, effective_date):
    """The id of *account*'s version dated *effective_date*."""
    return next(
        v.id for v in load_card_term_versions(account.id)
        if v.effective_date == effective_date
    )


def _remove(account, row_id):
    """Remove *row_id* from *account* and commit, as the door does."""
    removed = remove_terms(account.id, row_id)
    db.session.commit()
    db.session.expire_all()
    return removed


class TestTheRemove:
    """``remove_terms``: a version goes, and its days re-derive; the only one stays."""

    def test_the_rulings_mistyped_date_removed_and_saved_again(self, app, seed_user):
        """The cut to 1.0% typed Jan 1 for Mar 1: removed, Feb 10 reads 1.5% again.

        Ruling plan#2587's example.  The card's 1.5% terms date from Dec 1,
        2025; the cut to 1.0% was saved dated Jan 1 instead of Mar 1, so the
        Feb 10 purchase read 1.0%: $600.00 x 0.0100 = $6.00.  Removing the
        Jan 1 version gives its days back to Dec 1's: $600.00 x 0.0150 =
        $9.00.  Saving the cut again dated Mar 1 leaves Feb 10 at 1.5% and
        governs from Mar 1.
        """
        with app.app_context():
            account = _card(seed_user)
            _set(account, date(2025, 12, 1), _FEBRUARY)
            _set(account, date(2026, 1, 1), _MARCH)
            versions = load_card_term_versions(account.id)
            assert terms_in_effect(versions, _FEB_10).cashback_rate == Decimal("0.0100")

            assert _remove(account, _version_on(account, date(2026, 1, 1))) is True

            versions = load_card_term_versions(account.id)
            assert [v.effective_date for v in versions] == [date(2025, 12, 1)]
            assert terms_in_effect(versions, _FEB_10).cashback_rate == Decimal("0.0150")

            _set(account, date(2026, 3, 1), _MARCH)

            versions = load_card_term_versions(account.id)
            assert [v.effective_date for v in versions] == [
                date(2025, 12, 1), date(2026, 3, 1),
            ]
            assert terms_in_effect(versions, _FEB_10).cashback_rate == Decimal("0.0150")
            assert terms_in_effect(versions, date(2026, 3, 1)).cashback_rate == (
                Decimal("0.0100")
            )

    def test_the_earliest_removed_its_days_fall_to_the_next(self, app, seed_user):
        """Jan 1 removed: Feb 10, and every day before Mar 1, reads March's terms.

        The series holds flat backwards (rule 1), so the days the removed
        earliest version governed are the next version's now -- the close
        that was the 20th is the 5th on them.  The other version is
        untouched.
        """
        with app.app_context():
            account = _card(seed_user)
            _set(account, date(2026, 1, 1), _FEBRUARY)
            _set(account, date(2026, 3, 1), _MARCH)
            march_id = _version_on(account, date(2026, 3, 1))
            versions = load_card_term_versions(account.id)
            assert terms_in_effect(versions, _FEB_10).statement_close_day == 20

            assert _remove(account, _version_on(account, date(2026, 1, 1))) is True

            versions = load_card_term_versions(account.id)
            assert [v.id for v in versions] == [march_id]
            for column, value in _MARCH.items():
                assert getattr(versions[0], column) == value, column
            assert terms_in_effect(versions, _FEB_10).statement_close_day == 5
            assert terms_in_effect(versions, date(2025, 6, 1)).statement_close_day == 5

    def test_the_cards_only_version_is_refused_and_stays(self, app, seed_user):
        """One version: the refusal names its date, and the version is untouched."""
        with app.app_context():
            account = _card(seed_user)
            _set(account, date(2026, 1, 1), _FEBRUARY)
            only = _version_on(account, date(2026, 1, 1))

            with pytest.raises(CardTermsRemovalRefused) as refused:
                remove_terms(account.id, only)
            db.session.rollback()

            assert refused.value.effective_date == date(2026, 1, 1)
            assert str(refused.value) == (
                "The terms from Jan 1, 2026 are this card's only terms, so they "
                "were not removed: a card always keeps one set. To change their "
                "date, save the terms again dated the right day, then remove "
                "these."
            )
            db.session.expire_all()
            versions = load_card_term_versions(account.id)
            assert [v.id for v in versions] == [only]
            for column, value in _FEBRUARY.items():
                assert getattr(versions[0], column) == value, column

    def test_the_last_of_two_is_refused_once_the_other_is_gone(self, app, seed_user):
        """The repeat: of two versions the first goes, then the second is refused.

        The second removal is graded, because a remove right on the first
        call can be wrong on the repeat: here the count it refuses on is the
        one the first removal left.
        """
        with app.app_context():
            account = _card(seed_user)
            _set(account, date(2026, 1, 1), _FEBRUARY)
            _set(account, date(2026, 3, 1), _MARCH)
            january = _version_on(account, date(2026, 1, 1))
            march = _version_on(account, date(2026, 3, 1))

            assert _remove(account, january) is True
            with pytest.raises(CardTermsRemovalRefused):
                remove_terms(account.id, march)
            db.session.rollback()

            db.session.expire_all()
            assert [v.id for v in load_card_term_versions(account.id)] == [march]

    def test_another_cards_row_is_not_this_cards_to_remove(self, app, seed_user):
        """``False``, and the other card's version survives: the scope is BOTH ids.

        This card holds two versions, so the answer cannot be the only-version
        refusal; the other card holds two as well, so a remove that ignored
        the account would have deleted one.
        """
        with app.app_context():
            account = _card(seed_user)
            other = _card(seed_user, name="Other")
            _set(account, date(2026, 1, 1), _FEBRUARY)
            _set(account, date(2026, 3, 1), _MARCH)
            _set(other, date(2026, 1, 1), _FEBRUARY)
            _set(other, date(2026, 3, 1), _MARCH)
            theirs = _version_on(other, date(2026, 3, 1))

            assert _remove(account, theirs) is False

            assert len(load_card_term_versions(account.id)) == 2
            assert theirs in [v.id for v in load_card_term_versions(other.id)]

    def test_an_unknown_id_is_false_and_nothing_is_written(self, app, seed_user):
        """An id no row carries: ``False``, both versions kept."""
        with app.app_context():
            account = _card(seed_user)
            _set(account, date(2026, 1, 1), _FEBRUARY)
            _set(account, date(2026, 3, 1), _MARCH)

            assert _remove(account, 999_999) is False

            assert len(load_card_term_versions(account.id)) == 2

    def test_a_dormant_card_has_nothing_to_remove(self, app, seed_user):
        """No version: every id is ``False``, so the door answers 404, not a refusal."""
        with app.app_context():
            account = _card(seed_user)
            other = _card(seed_user, name="Other")
            _set(other, date(2026, 1, 1), _FEBRUARY)

            assert _remove(account, _version_on(other, date(2026, 1, 1))) is False

            assert load_card_term_versions(account.id) == []
            assert len(load_card_term_versions(other.id)) == 1
