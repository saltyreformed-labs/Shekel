"""
Shekel Budget App -- a card payment's card is a credit card (plan step credit_card:CC-6)

:mod:`app.card_payment_infrastructure`'s rule, one attachment at a time,
each with the control that the legal write commits:

* a settings row written onto a non-card, or re-pointed onto one;
* a card re-typed while a payment names it (and re-typed back inside one
  transaction, which leaves nothing to refuse);
* a revolving type's flag turned off while a payment names a card of it,
  refused by the statement itself: that arm is immediate and names no
  column, so a migration may still drop or retype the flag.

And the two orders of a race between a settings commit and a re-type, each
made deterministic by ``SET CONSTRAINTS ALL IMMEDIATE``, which runs the first
transaction's deferred check NOW and holds the transaction open.  Without the
settings arm's ``FOR SHARE`` on the account row, each case ends with both
committed and a payment on a non-card.
"""

import threading
import time

import pytest
from sqlalchemy import exc, text

from app import ref_cache
from app.enums import AcctTypeEnum, CardPaymentModeEnum
from app.extensions import db
from app.models.account import Account
from app.models.card_payment_settings import CardPaymentSettings

from ._helpers import account_of, payment_into

#: The rule's refusal, as it words it.
_REFUSED = "only a credit card can be a card payment's card"

#: How long a race waits for the second session to block, or to finish.
_WAIT_SECONDS = 10


def _settle_on(template):
    """Stage a settings row for *template* (its card comes from the definition)."""
    db.session.add(CardPaymentSettings(
        template=template, user_id=template.user_id,
        mode_id=ref_cache.card_payment_mode_id(CardPaymentModeEnum.CARD_BALANCE),
    ))


def _retype(account_id, member):
    """Re-type *account_id* to the built-in *member* by SQL, as no door would."""
    db.session.execute(text(
        "UPDATE budget.accounts SET account_type_id = :t WHERE id = :a"
    ), {"t": ref_cache.acct_type_id(member), "a": account_id})


def _type_of(account_id):
    """The committed type id of *account_id*."""
    db.session.expire_all()
    return db.session.get(Account, account_id).account_type_id


class TestTheSettingsArm:
    """A settings row's card is a card when the row is written or re-pointed."""

    def test_a_card_is_accepted(self, app, seed_user):
        """The control: a payment's settings on a credit card commit."""
        with app.app_context():
            template = payment_into(seed_user, account_of(seed_user, "Credit Card", "Visa"))
            _settle_on(template)
            db.session.commit()
            assert db.session.query(CardPaymentSettings).count() == 1

    def test_a_non_card_is_refused_at_commit(self, app, seed_user):
        """Settings on a savings account: the flush passes, the commit refuses."""
        with app.app_context():
            savings = account_of(seed_user, "Savings", "Rainy day")
            _settle_on(payment_into(seed_user, savings))
            db.session.flush()
            with pytest.raises(exc.ProgrammingError, match=_REFUSED):
                db.session.commit()
            db.session.rollback()
            assert db.session.query(CardPaymentSettings).count() == 0

    @pytest.mark.parametrize(
        ("type_name", "commits"), [("Credit Card", True), ("Savings", False)],
        ids=["onto another card", "onto a non-card"],
    )
    def test_re_pointing_a_payment_is_graded(self, app, seed_user, type_name, commits):
        """The definition and its row moved in ONE statement reach the update arm.

        The payment key is checked at the end of each statement, so a
        writable CTE moving both passes it; what is left to say whether the
        new destination may carry a payment is this rule.
        """
        with app.app_context():
            template = payment_into(seed_user, account_of(seed_user, "Credit Card", "Visa"))
            _settle_on(template)
            elsewhere = account_of(seed_user, type_name, "Elsewhere")
            db.session.commit()
            db.session.execute(text(
                "WITH moved AS ("
                " UPDATE budget.transfer_templates SET to_account_id = :a"
                " WHERE id = :t RETURNING id) "
                "UPDATE budget.card_payment_settings SET card_account_id = :a "
                "WHERE transfer_template_id = (SELECT id FROM moved)"
            ), {"a": elsewhere.id, "t": template.id})
            if commits:
                db.session.commit()
                assert db.session.query(CardPaymentSettings).one().card_account_id == (
                    elsewhere.id
                )
            else:
                with pytest.raises(exc.ProgrammingError, match=_REFUSED):
                    db.session.commit()
                db.session.rollback()


class TestTheAccountArm:
    """A card a payment names stays a card."""

    def test_a_card_with_a_payment_cannot_be_re_typed(self, app, seed_user):
        """Re-typed to Checking: refused at commit, and the type stands."""
        with app.app_context():
            card = account_of(seed_user, "Credit Card", "Visa")
            _settle_on(payment_into(seed_user, card))
            db.session.commit()
            card_type = card.account_type_id
            _retype(card.id, AcctTypeEnum.CHECKING)
            with pytest.raises(exc.ProgrammingError, match=_REFUSED):
                db.session.commit()
            db.session.rollback()
            assert _type_of(card.id) == card_type

    def test_a_card_with_no_payment_can(self, app, seed_user):
        """The control: the same re-type of a card no payment names commits."""
        with app.app_context():
            card = account_of(seed_user, "Credit Card", "Visa")
            payment_into(seed_user, card)
            db.session.commit()
            _retype(card.id, AcctTypeEnum.CHECKING)
            db.session.commit()
            assert _type_of(card.id) == ref_cache.acct_type_id(AcctTypeEnum.CHECKING)

    def test_re_typed_away_and_back_in_one_transaction_commits(self, app, seed_user):
        """Deferred: graded on what the transaction leaves, a card."""
        with app.app_context():
            card = account_of(seed_user, "Credit Card", "Visa")
            _settle_on(payment_into(seed_user, card))
            db.session.commit()
            _retype(card.id, AcctTypeEnum.CHECKING)
            _retype(card.id, AcctTypeEnum.CREDIT_CARD)
            db.session.commit()
            assert _type_of(card.id) == ref_cache.acct_type_id(AcctTypeEnum.CREDIT_CARD)


class TestTheTypeArm:
    """A revolving type keeps its flag while a payment names a card of it."""

    _FLAG_OFF = text(
        "UPDATE ref.account_types SET has_revolving_credit = FALSE WHERE id = :t"
    )

    def test_the_flag_cannot_go_while_a_payment_names_a_card(self, app, seed_user):
        """Turning Credit Card's flag off is refused by the statement itself."""
        with app.app_context():
            _settle_on(payment_into(seed_user, account_of(seed_user, "Credit Card", "Visa")))
            db.session.commit()
            with pytest.raises(exc.ProgrammingError, match=_REFUSED):
                db.session.execute(
                    self._FLAG_OFF,
                    {"t": ref_cache.acct_type_id(AcctTypeEnum.CREDIT_CARD)},
                )
            db.session.rollback()

    def test_any_other_edit_of_the_type_passes(self, app, seed_user):
        """The arm fires on every update of a type row and lets every other one by.

        Renaming the icon of the type a paid card holds commits: only the
        flag going from on to off is the arm's question.
        """
        with app.app_context():
            _settle_on(payment_into(seed_user, account_of(seed_user, "Credit Card", "Visa")))
            db.session.commit()
            db.session.execute(text(
                "UPDATE ref.account_types SET icon_class = 'bi-credit-card-2-front' "
                "WHERE id = :t"
            ), {"t": ref_cache.acct_type_id(AcctTypeEnum.CREDIT_CARD)})
            db.session.commit()

    def test_the_flag_can_go_while_none_does(self, app, seed_user):
        """The control: with a card but no payment, the same edit commits."""
        with app.app_context():
            account_of(seed_user, "Credit Card", "Visa")
            db.session.commit()
            db.session.execute(
                self._FLAG_OFF, {"t": ref_cache.acct_type_id(AcctTypeEnum.CREDIT_CARD)},
            )
            db.session.commit()


def _second_session(app, statements, params):
    """Run *statements* in a session of their own on a thread, then commit.

    Returns ``(thread, outcome)``.  ``outcome["pid"]`` is the session's
    backend, set before its first statement; ``outcome["result"]`` is
    ``"committed"`` or the database error it raised.
    """
    outcome = {}
    ready = threading.Event()

    def run():
        with app.app_context():
            try:
                outcome["pid"] = db.session.execute(
                    text("SELECT pg_backend_pid()"),
                ).scalar_one()
                ready.set()
                for statement in statements:
                    db.session.execute(text(statement), params)
                db.session.commit()
                outcome["result"] = "committed"
            except exc.DBAPIError as error:
                db.session.rollback()
                outcome["result"] = error
            finally:
                ready.set()
                db.session.remove()

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(_WAIT_SECONDS), "the second session never started"
    return thread, outcome


def _blocked(pid):
    """Whether backend *pid* is waiting on a lock it has not been granted."""
    return db.session.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = :p AND NOT granted)"
    ), {"p": pid}).scalar_one()


def _wait_until_blocked_or_done(thread, outcome):
    """Return whether the second session blocked before it finished."""
    deadline = time.monotonic() + _WAIT_SECONDS
    while time.monotonic() < deadline:
        if _blocked(outcome["pid"]):
            return True
        if not thread.is_alive():
            return False
        time.sleep(0.02)
    raise AssertionError("the second session neither blocked nor finished")


_INSERT_SETTINGS = (
    "INSERT INTO budget.card_payment_settings "
    "(transfer_template_id, card_account_id, user_id, mode_id) "
    "VALUES (:t, :c, :u, :m)"
)
_RETYPE = "UPDATE budget.accounts SET account_type_id = :checking WHERE id = :c"


class TestARaceEndsWithOneRefused:
    """The settings arm's lock on the account row serialises it with a re-type."""

    def _params(self, seed_user):
        """A card, a definition into it, and the ids both sessions write."""
        card = account_of(seed_user, "Credit Card", "Visa")
        template = payment_into(seed_user, card)
        db.session.commit()
        return {
            "t": template.id, "c": card.id, "u": seed_user["user"].id,
            "m": ref_cache.card_payment_mode_id(CardPaymentModeEnum.CARD_BALANCE),
            "checking": ref_cache.acct_type_id(AcctTypeEnum.CHECKING),
        }

    def test_a_settings_commit_waits_for_an_open_re_type(self, app, seed_user):
        """Re-type first, its check already run: the settings commit waits, then refuses.

        Without the lock the settings arm reads the committed type, a card,
        and commits; the re-type's check has already passed; both stand.
        """
        with app.app_context():
            params = self._params(seed_user)
            db.session.execute(text(_RETYPE), params)
            db.session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            thread, outcome = _second_session(app, [_INSERT_SETTINGS], params)
            blocked = _wait_until_blocked_or_done(thread, outcome)
            db.session.commit()
            thread.join(_WAIT_SECONDS)

            assert blocked, outcome.get("result")
            assert isinstance(outcome["result"], exc.ProgrammingError)
            assert _REFUSED in str(outcome["result"])
            assert db.session.query(CardPaymentSettings).count() == 0
            assert _type_of(params["c"]) == params["checking"]

    def test_a_re_type_waits_for_a_settings_commit_that_locked_first(self, app, seed_user):
        """Settings first, their check already run: the re-type waits, then refuses.

        Without the lock the re-type does not wait, its check finds no
        committed settings row, and both stand.
        """
        with app.app_context():
            params = self._params(seed_user)
            db.session.execute(text(_INSERT_SETTINGS), params)
            db.session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            thread, outcome = _second_session(app, [_RETYPE], params)
            blocked = _wait_until_blocked_or_done(thread, outcome)
            db.session.commit()
            thread.join(_WAIT_SECONDS)

            assert blocked, outcome.get("result")
            assert isinstance(outcome["result"], exc.ProgrammingError)
            assert _REFUSED in str(outcome["result"])
            assert db.session.query(CardPaymentSettings).count() == 1
            assert _type_of(params["c"]) == ref_cache.acct_type_id(
                AcctTypeEnum.CREDIT_CARD,
            )
