"""
Shekel Budget App -- the card's terms as a dated series (``app.services.card_terms``)

Plan#2331, developer ruling **plan#2333**: a card's terms are versions, each
in force from its ``effective_date`` until the next one's, so a change governs
from its day and every statement and reward before it stands.  What is
pinned:

* the walk (:func:`~app.services.card_terms.terms_in_effect`): before, at,
  between and after two versions -- the earlier governs up to the day before
  the later, the later from its own day -- and before the EARLIEST version the
  earliest governs (the series holds flat backwards), while an empty series
  is ``None``; in any input order;
* the read (:func:`~app.services.card_terms.load_card_term_versions`) is the
  account's own versions, earliest first;
* the write (:func:`~app.services.card_terms.set_terms`): a new date adds a
  version and leaves the one before it as it was; the SAME date corrects that
  version in place (one row, its id kept, every term rewritten, a blank
  nullable term cleared, ``updated_at`` moved) -- the second write graded,
  because a producer right on the first write can be wrong on the repeat.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.extensions import db
from app.models.credit_card_params import CreditCardParams
from app.services.card_terms import (
    load_card_term_versions,
    set_terms,
    terms_in_effect,
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


def _version(effective_date):
    """A stand-in version: the walk reads only ``effective_date``."""
    return SimpleNamespace(effective_date=effective_date)


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


class TestTheWalk:
    """Which version governs a day."""

    JAN = _version(date(2026, 1, 1))
    MAR = _version(date(2026, 3, 1))

    def test_the_earlier_version_governs_up_to_the_day_before_the_later(self):
        """Jan 1 through Feb 28 read January's; Mar 1 is March's from its own day."""
        versions = [self.JAN, self.MAR]
        assert terms_in_effect(versions, date(2026, 1, 1)) is self.JAN
        assert terms_in_effect(versions, date(2026, 2, 10)) is self.JAN
        assert terms_in_effect(versions, date(2026, 2, 28)) is self.JAN
        assert terms_in_effect(versions, date(2026, 3, 1)) is self.MAR
        assert terms_in_effect(versions, date(2027, 6, 1)) is self.MAR

    def test_before_the_earliest_version_the_earliest_governs(self):
        """The series holds flat backwards: Dec 31 and years before read January's.

        So the date the first terms are saved never makes a statement before
        it vanish -- the cycle open on that day began at the close before it.
        """
        versions = [self.JAN, self.MAR]
        assert terms_in_effect(versions, date(2025, 12, 31)) is self.JAN
        assert terms_in_effect(versions, date(2020, 1, 1)) is self.JAN

    def test_the_input_order_does_not_matter(self):
        """Newest-first input answers exactly as earliest-first does."""
        versions = [self.MAR, self.JAN]
        assert terms_in_effect(versions, date(2025, 12, 31)) is self.JAN
        assert terms_in_effect(versions, date(2026, 2, 28)) is self.JAN
        assert terms_in_effect(versions, date(2026, 3, 1)) is self.MAR

    def test_an_empty_series_is_none(self):
        """No version: the dormant card, and the one ``None`` the walk returns."""
        assert terms_in_effect([], date(2026, 3, 1)) is None


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
