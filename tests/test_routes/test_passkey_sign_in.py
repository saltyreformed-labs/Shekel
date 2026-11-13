"""Signing in with a passkey, through the sign-in page (plan#2538, task plan#3483).

Every case drives the page as a browser does: it reads the passkey form off
``/login``, asks the options at the URL the form names, has the software
authenticator (``tests/_passkeys.py``) sign them, and posts the form's own
controls with the answer written into ``credential``, as ``passkey.js``
writes it.  So a control the template renders is posted as a browser posts
it, and a URL it names is the one asked.

Graded here:

* the page offers the passkey: the form, its options' URL, and the email
  box's ``username webauthn`` autofill;
* a passkey signs in on its own (ruling plan#3465), a code or no code, through
  the one completion: the browser becomes known and is never trusted, and a
  companion lands on her page;
* the strangers' lock is not asked and a refused passkey counts nowhere
  (ruling plan#3469);
* every refusal says one plain sentence that names nothing about the account;
* the owner's lock is taken after the passkey names its account and before
  the passkey is read (the reads before it are ``app.db_transaction``'s list);
* the sign-in is limited per internet address as ``/login`` is.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import CONFIG_MAP, create_app
from app.extensions import db
from app.models.known_device import KnownDevice
from app.models.user import User
from tests._form_controls import rendered_form_controls
from tests._guess_records import record_of, set_record
from tests._limited_app import limited_app, through_nginx
from tests._passkeys import (
    SoftwareAuthenticator,
    add_committed,
    ask_options,
    passkey_row,
    post_answer,
)
from tests._seed_owner_fixtures import SEED_USER_EMAIL, SEED_USER_PASSWORD
from tests._sign_in import browser_count, make_known
from tests._test_helpers import (
    HeldOwnerKey,
    advisory_lock_keys,
    assert_timed_out_on_owner_key,
    bound_lock_waits,
    capture_sql_statements,
    enable_mfa,
    owner_lock_key,
)

#: The sentence every refusal but a lapsed ceremony's says: one for a passkey
#: no account holds, one that does not verify, one of a disabled account, so
#: the answer names nothing about the account.
_TRY_AGAIN = "That passkey could not be checked. Please try again."

#: The sentence a sign-in with no ceremony in flight says.
_TOO_LONG = "That took too long. Please try again."

#: ``/login``'s limit per address, 5 per 15 minutes, which the passkey sign-in
#: shares by ruling plan#3469: spelled here as the oracle.
_LIMIT = 5


class _PasskeyForm(HTMLParser):
    """The sign-in page's passkey form: where it posts and where its options are.

    Attributes:
        action: The form's ``action``, or ``None`` if the page has no such form.
        options_url: Its ``data-options-url``.
        email_autocomplete: The email box's ``autocomplete``.
    """

    def __init__(self):
        super().__init__()
        self.action = None
        self.options_url = None
        self.email_autocomplete = None

    def handle_starttag(self, tag, attrs):
        """Read the passkey form's two URLs and the email box's autofill."""
        attributes = dict(attrs)
        if tag == "form" and "data-passkey-sign-in" in attributes:
            self.action = attributes.get("action")
            self.options_url = attributes.get("data-options-url")
        elif tag == "input" and attributes.get("name") == "email":
            self.email_autocomplete = attributes.get("autocomplete")


def _page(client):
    """GET the sign-in page; return its text and its passkey form."""
    response = client.get("/login")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    form = _PasskeyForm()
    form.feed(page)
    assert form.action is not None, "the sign-in page renders no passkey form"
    return page, form


def _sign_in(client, authenticator, **get):
    """Run a whole passkey sign-in through the page; return the form's response."""
    page, form = _page(client)
    options = ask_options(client, page, form)
    return post_answer(client, page, form, authenticator.get(options, **get))


def _signed_in_as(client):
    """Return the user id the browser's session is signed in as, or ``None``."""
    with client.session_transaction() as session:
        signed_in = session.get("_user_id")
    return None if signed_in is None else int(signed_in)


def _refused_with(client, response):
    """Assert *response* sent the browser back to sign in; return the page it shows."""
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/login"
    assert _signed_in_as(client) is None
    return client.get("/login").get_data(as_text=True)


@pytest.fixture(name="owner")
def _owner(seed_user):
    """The seed user, holding one passkey: ``(user, passkey_id, authenticator)``."""
    user = seed_user["user"]
    passkey_id, authenticator = add_committed(user)
    return user, passkey_id, authenticator


class TestThePage:
    """The sign-in page offers a passkey, both ways."""

    def test_the_page_carries_the_passkey_form_and_its_script(self, client):
        """The form posts to the sign-in, names its options, and renders its two fields."""
        page, form = _page(client)

        assert form.action == "/login/passkey"
        assert form.options_url == "/login/passkey/options"
        assert sorted(rendered_form_controls(page, form.action)) == [
            "credential", "csrf_token",
        ]
        assert "Sign in with a passkey" in page
        assert "/static/js/passkey.js" in page

    def test_the_email_box_offers_a_passkey_from_its_autofill(self, client):
        """``webauthn`` in the box's autofill is what lets the phone offer a passkey there."""
        _, form = _page(client)

        assert form.email_autocomplete == "username webauthn"

    def test_the_options_name_no_account_and_wait_in_the_session(self, client):
        """No credential is listed; the ceremony waits in the session for its answer."""
        page, form = _page(client)

        options = ask_options(client, page, form)

        assert options == {"publicKey": {
            "challenge": options["publicKey"]["challenge"],
            "timeout": 270_000,
            "rpId": "shekel.test",
            "userVerification": "required",
        }}
        with client.session_transaction() as session:
            state = session["_passkey_sign_in_state"]
        assert state["ceremony"] == "sign_in"
        assert state["challenge"] == options["publicKey"]["challenge"]


class TestASignIn:
    """A passkey signs in on its own, through the one completion."""

    def test_a_passkey_signs_the_owner_in(self, client, owner, caplog):
        """The owner lands on the dashboard, signed in, the passkey's use recorded."""
        user, passkey_id, authenticator = owner
        added_use = passkey_row(passkey_id).last_used_at

        with caplog.at_level(logging.INFO):
            response = _sign_in(client, authenticator)

        assert response.status_code == 302
        assert urlsplit(response.headers["Location"]).path == "/dashboard"
        assert _signed_in_as(client) == user.id
        assert passkey_row(passkey_id).last_used_at > added_use
        events = [
            (record.event, getattr(record, "passkey_id", None))
            for record in caplog.records if getattr(record, "event", None)
        ]
        assert ("passkey_sign_in", passkey_id) in events

    def test_an_account_with_a_code_is_asked_no_code(self, client, owner):
        """A passkey is the second factor: the sign-in skips the code form (ruling plan#3465)."""
        user, _, authenticator = owner
        enable_mfa(db.session, user.id)

        response = _sign_in(client, authenticator)

        assert urlsplit(response.headers["Location"]).path == "/dashboard"
        assert _signed_in_as(client) == user.id

    def test_the_browser_becomes_known_and_is_not_trusted(self, client, owner):
        """Its record is made and never trusted, so its next password sign-in asks the code."""
        user, _, authenticator = owner
        enable_mfa(db.session, user.id)

        _sign_in(client, authenticator)
        client.post("/logout")
        password = client.post(
            "/login", data={"email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD},
        )

        devices = db.session.execute(
            select(KnownDevice).where(KnownDevice.user_id == user.id)
            .execution_options(populate_existing=True)
        ).scalars().all()
        assert [device.trusted_at for device in devices] == [None]
        assert urlsplit(password.headers["Location"]).path == "/mfa/verify"

    def test_a_companion_lands_on_her_page(self, client, seed_companion):
        """A companion's passkey signs her in to the companion view, as her password does."""
        companion = seed_companion["user"]
        _, authenticator = add_committed(companion)

        response = _sign_in(client, authenticator)

        assert urlsplit(response.headers["Location"]).path == "/companion/"
        assert _signed_in_as(client) == companion.id

    def test_a_browser_signed_in_already_is_sent_on(self, client, owner):
        """A second tab's answer, posted once the first signed in, lands where ``/login`` sends."""
        user, passkey_id, authenticator = owner
        page, form = _page(client)
        options = ask_options(client, page, form)
        client.post("/login", data={"email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD})
        used_before = passkey_row(passkey_id).last_used_at

        response = post_answer(client, page, form, authenticator.get(options))

        assert urlsplit(response.headers["Location"]).path == "/dashboard"
        assert _signed_in_as(client) == user.id
        assert passkey_row(passkey_id).last_used_at == used_before
        with client.session_transaction() as session:
            assert "_passkey_sign_in_state" not in session


class TestTheStrangersLock:
    """Ruling plan#3469: a passkey gets past the lock, and a refused one counts nowhere."""

    def test_an_account_the_lock_holds_signs_in_by_passkey(self, client, owner):
        """Locked against passwords, the account still signs in; its record is left as it was."""
        user, _, authenticator = owner
        locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
        set_record(
            SEED_USER_EMAIL, count=0, locked_until=locked_until,
            last_attempt_at=datetime.now(timezone.utc),
        )
        db.session.commit()

        response = _sign_in(client, authenticator)

        assert urlsplit(response.headers["Location"]).path == "/dashboard"
        assert _signed_in_as(client) == user.id
        record = record_of(SEED_USER_EMAIL)
        assert (record.failed_login_count, record.locked_until) == (0, locked_until)

    def test_a_sign_in_clears_no_strangers_count(self, client, owner):
        """A known proof refills no stranger's guesses: three wrong passwords stay three."""
        _, _, authenticator = owner
        set_record(
            SEED_USER_EMAIL, count=3, locked_until=None,
            last_attempt_at=datetime.now(timezone.utc),
        )
        db.session.commit()

        _sign_in(client, authenticator)

        assert record_of(SEED_USER_EMAIL).failed_login_count == 3

    def test_a_refused_passkey_counts_on_no_record(self, client, owner):
        """A forged answer at a known browser writes neither the account's nor the browser's count.

        The account's record holds two wrong passwords beforehand and the
        browser's one, so a count moving on either, either way, is seen.
        """
        user, _, authenticator = owner
        make_known(client)
        # One wrong password at the known browser: its own count is 1.
        client.post("/login", data={"email": SEED_USER_EMAIL, "password": "not-it"})
        assert browser_count(user.id) == 1
        set_record(
            SEED_USER_EMAIL, count=2, locked_until=None,
            last_attempt_at=datetime.now(timezone.utc),
        )
        db.session.commit()
        forger = SoftwareAuthenticator(credential_id=authenticator.credential_id)
        forger.user_handle = authenticator.user_handle

        page = _refused_with(client, _sign_in(client, forger))

        assert _TRY_AGAIN in page
        record = record_of(SEED_USER_EMAIL)
        assert (record.failed_login_count, record.locked_until) == (2, None)
        assert browser_count(user.id) == 1


class TestARefusalNamesNothing:
    """Every refusal sends the browser back with one plain sentence, signed out."""

    @pytest.mark.usefixtures("owner")
    def test_a_passkey_no_account_holds(self, client, caplog):
        """A key never added is refused with the sentence a bad signature gets."""
        with caplog.at_level(logging.WARNING):
            page = _refused_with(
                client, _sign_in(client, SoftwareAuthenticator(), user_handle=None),
            )

        assert _TRY_AGAIN in page
        refusals = [
            (record.refusal, record.user_id) for record in caplog.records
            if getattr(record, "event", None) == "passkey_sign_in_failed"
        ]
        assert refusals == [("unknown_credential", None)]

    def test_a_signature_by_another_key(self, client, owner, caplog):
        """The passkey's id with another key's signature: the same sentence, the account logged."""
        user, passkey_id, authenticator = owner
        forger = SoftwareAuthenticator(credential_id=authenticator.credential_id)
        forger.user_handle = authenticator.user_handle
        used_before = passkey_row(passkey_id).last_used_at

        with caplog.at_level(logging.WARNING):
            page = _refused_with(client, _sign_in(client, forger))

        assert _TRY_AGAIN in page
        assert passkey_row(passkey_id).last_used_at == used_before
        refusals = [
            (record.refusal, record.user_id) for record in caplog.records
            if getattr(record, "event", None) == "passkey_sign_in_failed"
        ]
        assert refusals == [("not_verified", user.id)]

    def test_a_disabled_account(self, client, owner):
        """A disabled account's own passkey is refused with the same sentence."""
        user, _, authenticator = owner
        db.session.get(User, user.id).is_active = False
        db.session.commit()

        page = _refused_with(client, _sign_in(client, authenticator))

        assert _TRY_AGAIN in page

    def test_a_passkey_removed_after_its_options(self, client, owner):
        """Removed between the options and the answer: the account is not found by it."""
        _, passkey_id, authenticator = owner
        page, form = _page(client)
        options = ask_options(client, page, form)
        db.session.delete(passkey_row(passkey_id))
        db.session.commit()

        shown = _refused_with(client, post_answer(client, page, form, authenticator.get(options)))

        assert _TRY_AGAIN in shown

    @pytest.mark.usefixtures("owner")
    @pytest.mark.parametrize("answer", ["", "not json", json.dumps({"type": "public-key"})])
    def test_an_answer_that_is_no_credential(self, client, answer):
        """An empty, unreadable or shapeless answer is refused alike."""
        page, form = _page(client)
        ask_options(client, page, form)

        shown = _refused_with(client, post_answer(client, page, form, answer))

        assert _TRY_AGAIN in shown

    def test_a_second_submit_of_one_answer(self, client, owner):
        """The ceremony leaves the session at the first submit, so the second meets none."""
        user, _, authenticator = owner
        page, form = _page(client)
        answer = authenticator.get(ask_options(client, page, form))
        assert _signed_in_as(client) is None
        post_answer(client, page, form, answer)
        assert _signed_in_as(client) == user.id
        client.post("/logout")

        shown = _refused_with(client, post_answer(client, page, form, answer))

        assert _TOO_LONG in shown

    @pytest.mark.parametrize("held", [True, False], ids=["its-passkey", "no-ones"])
    def test_an_answer_with_no_options_asked(self, client, owner, held):
        """A browser that never asked the options has no ceremony: any passkey reads alike.

        Refused before the account is looked up, so the sentence for a real
        passkey and for one no account holds is the same, and no account is
        read.
        """
        _, _, authenticator = owner
        key = authenticator if held else SoftwareAuthenticator()
        page, form = _page(client)
        other = client.application.test_client()
        stray = key.get(ask_options(other, *_page(other)))

        response, statements = capture_sql_statements(
            lambda: post_answer(client, page, form, stray),
        )

        assert _TOO_LONG in _refused_with(client, response)
        assert not any("auth.passkeys" in sql for sql, _ in statements)


class TestTheOwnersLock:
    """Found, then locked, then read: ``app.db_transaction``'s order for a sign-in."""

    def test_the_lock_falls_between_finding_the_account_and_reading_the_passkey(
        self, client, owner,
    ):
        """The read before the lock loads the account by a join; the passkey is read under it."""
        user, _, authenticator = owner
        page, form = _page(client)
        options = ask_options(client, page, form)
        answer = authenticator.get(options)

        response, statements = capture_sql_statements(
            lambda: post_answer(client, page, form, answer),
        )

        assert urlsplit(response.headers["Location"]).path == "/dashboard"
        texts = [sql for sql, _ in statements]
        lock_at = next(i for i, sql in enumerate(texts) if "pg_advisory_xact_lock" in sql)
        found_at = next(
            i for i, sql in enumerate(texts)
            if "FROM auth.users JOIN auth.passkeys" in sql
        )
        read_at = next(
            i for i, sql in enumerate(texts)
            if "FROM auth.passkeys" in sql and "FOR UPDATE" in sql
        )
        assert found_at < lock_at < read_at
        assert not any(
            "auth.passkeys" in sql for sql in texts[:found_at]
        ), "a passkey was read before the account was found"
        assert advisory_lock_keys(statements)[0] == owner_lock_key(user.id)

    def test_a_companion_waits_on_her_owners_key(self, client, seed_user, seed_companion):
        """A companion acts on her owner's budget, so her sign-in takes the owner's key."""
        _, authenticator = add_committed(seed_companion["user"])

        response, statements = capture_sql_statements(
            lambda: _sign_in(client, authenticator),
        )

        assert urlsplit(response.headers["Location"]).path == "/companion/"
        assert advisory_lock_keys(statements)[0] == owner_lock_key(seed_user["user"].id)

    def test_a_held_key_makes_the_sign_in_wait(self, client, owner):
        """Another transaction holding the owner's key blocks the sign-in."""
        user, _, authenticator = owner
        page, form = _page(client)
        answer = authenticator.get(ask_options(client, page, form))

        with HeldOwnerKey(user.id):
            bound_lock_waits()
            with pytest.raises(OperationalError) as caught:
                post_answer(client, page, form, answer)

        assert_timed_out_on_owner_key(caught.value, user.id)

    def test_the_same_sign_in_completes_when_the_key_is_free(self, client, owner):
        """The control: the same bound, nothing held."""
        user, _, authenticator = owner
        page, form = _page(client)
        answer = authenticator.get(ask_options(client, page, form))

        bound_lock_waits()
        post_answer(client, page, form, answer)

        assert _signed_in_as(client) == user.id


class TestTheFormToken:
    """With the form token checked, as production checks it: what passkey.js sends is enough."""

    @pytest.fixture(name="guarded")
    def _guarded(self, app, monkeypatch):
        """A fresh app that checks the form token.

        Yields:
            The app.
        """
        # Pylint: ``unused-argument`` -- ``app`` is asked for so the suite's app is
        # built first, as ``tests/_limited_app.py`` says each fresh app needs.
        # pylint: disable=unused-argument
        monkeypatch.setattr(CONFIG_MAP["testing"], "WTF_CSRF_ENABLED", True)
        guarded = create_app("testing")
        try:
            yield guarded
        finally:
            with guarded.app_context():
                db.engine.dispose()

    def test_the_pages_token_carries_the_options_and_the_answer(self, guarded, owner):
        """The form's token in the options' header, and in the form, signs the owner in."""
        user, _, authenticator = owner
        browser = guarded.test_client()
        page, form = _page(browser)

        response = post_answer(browser, page, form, authenticator.get(ask_options(browser, page, form)))

        assert urlsplit(response.headers["Location"]).path == "/dashboard"
        assert _signed_in_as(browser) == user.id

    def test_the_options_are_refused_without_the_token(self, guarded):
        """No header, no options: the fetch's token is what the check reads."""
        browser = guarded.test_client()
        _page(browser)

        assert browser.post("/login/passkey/options").status_code == 400

    def test_a_lapsed_token_is_refused_with_the_400_the_page_reloads_for(self, guarded):
        """An hour on, the page's own token is refused with a 400 (Flask-WTF's hour)."""
        browser = guarded.test_client()
        page, form = _page(browser)
        token = rendered_form_controls(page, form.action)["csrf_token"][0]

        with time_machine.travel(datetime.now(timezone.utc) + timedelta(hours=1, seconds=1)):
            response = browser.post(form.options_url, headers={"X-CSRFToken": token})

        assert response.status_code == 400


class TestTheLimit:
    """The sign-in is limited per internet address as ``/login`` is (ruling plan#3469)."""

    @pytest.fixture(name="rate_app")
    def _rate_app(self, app, monkeypatch):
        """A fresh app with the limiter on from the start, as in production.

        Yields:
            The app.
        """
        # Pylint: ``unused-argument`` -- ``app`` is asked for so the suite's app is
        # built first: the last app built decides whether the one limiter is on.
        # pylint: disable=unused-argument
        with limited_app(monkeypatch) as limited:
            yield limited

    def test_an_address_gets_five_tries_and_another_its_own(self, rate_app, owner):
        """The sixth answer from one address is refused; another address's first is not."""
        browser = rate_app.test_client()
        address = through_nginx("8.8.8.8")
        page, form = _page(browser)
        for attempt in range(_LIMIT):
            response = post_answer(browser, page, form, "", **address)
            assert response.status_code == 302, f"try {attempt + 1} was not answered"

        assert post_answer(browser, page, form, "", **address).status_code == 429
        other = rate_app.test_client()
        other_page, other_form = _page(other)
        assert post_answer(
            other, other_page, other_form, owner[2].get(ask_options(other, other_page, other_form)),
            **through_nginx("9.9.9.9"),
        ).status_code == 302
        assert _signed_in_as(other) == owner[0].id

    def test_the_password_forms_tries_are_its_own(self, rate_app, owner):
        """Spending ``/login``'s five leaves the passkey's untouched: each door counts its own."""
        browser = rate_app.test_client()
        address = through_nginx("8.8.8.8")
        for _ in range(_LIMIT):
            browser.post("/login", data={}, **address)
        assert browser.post("/login", data={}, **address).status_code == 429

        page, form = _page(browser)
        response = post_answer(
            browser, page, form, owner[2].get(ask_options(browser, page, form)), **address,
        )

        assert urlsplit(response.headers["Location"]).path == "/dashboard"
