"""Re-confirming with a passkey, through the re-confirm page (plan#2538, task plan#3485).

Every case drives the page as a browser does: it reads the passkey form off
``/reauth``, asks the options at the URL the form names, has the software
authenticator (``tests/_passkeys.py``) sign them, and posts the form's own
controls with the answer written into ``credential``, as ``passkey.js``
writes it.  So a control the template renders is posted as a browser posts
it, and a URL it names is the one asked.

Graded here:

* the page offers the passkey to an account holding one, beside the password
  and code, and to no other (ruling plan#3541), and its options name the
  account's own passkeys alone;
* a passkey re-confirm does what a password re-confirm does: it unlocks a
  ``@fresh_login_required()`` action, restarts the fresh-login window and not
  the session, honours a safe ``next``, renews a known browser's cookie, and
  spends one of this door's tries first, five per counter;
* the strangers' lock is not asked, a refused passkey counts on no record,
  and a success clears no stranger's count (ruling plan#3469);
* another account's passkey, a replayed answer and an expired challenge are
  refused, the window left as it was;
* a passkey account's password, and its password and code, still re-confirm.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pyotp
import pytest
import time_machine
from sqlalchemy import select

from app.extensions import db
from app.models.known_device import KnownDevice
from app.models.user import User
from app.services import known_device_service
from app.services.passkey_service import CHALLENGE_LIFETIME
from app.utils.session_helpers import (
    FRESH_LOGIN_AT_KEY,
    SESSION_CREATED_AT_KEY,
    SESSION_SIGNED_IN_AT_KEY,
)
from tests._form_controls import rendered_form_controls
from tests._guess_records import record_of, set_record
from tests._limited_app import limited_app, signed_in_browser, stolen_session, through_nginx
from tests._passkeys import (
    SoftwareAuthenticator,
    add_committed,
    ask_options,
    b64url,
    passkey_row,
    post_answer,
)
from tests._seed_owner_fixtures import SEED_USER_EMAIL, SEED_USER_PASSWORD
from tests._sign_in import browser_count, make_a_stranger
from tests._test_helpers import enable_mfa

#: The sentence a refusal says unless the ceremony lapsed: one for another
#: account's passkey, a forged signature and a replayed answer alike.
_TRY_AGAIN = "That passkey could not be checked. Please try again."

#: The sentence a re-confirm with no live ceremony says.
_TOO_LONG = "That took too long. Please try again."

#: The re-confirm page's own sentence for a wrong password.
_INVALID_PASSWORD = "Invalid password."

#: How old a fresh-login stamp is made, to need a re-confirm: past
#: ``FRESH_LOGIN_MAX_AGE_MINUTES``'s five.
_STALE = timedelta(minutes=6)

#: Re-confirm's tries per counter, 5 per 15 minutes (rulings plan#3023,
#: plan#3071): spelled here as the oracle.
_TRIES = 5

#: What the limit's refusal page says.
_TOO_MANY = "made too many attempts"

#: The session key the re-confirm's ceremony waits under.
_STATE_KEY = "_passkey_re_confirm_state"

#: The address the rate-limited cases' owner signs in from.
_OWNERS_ADDRESS = "7.7.7.7"


class _ReConfirmPage(HTMLParser):
    """The re-confirm page's passkey form, each form's inputs, and the scripts it loads.

    Attributes:
        action: The passkey form's ``action``, or ``None`` if there is none.
        options_url: Its ``data-options-url``.
        inputs: Each form's ``action``, mapped to the names of its inputs.
        scripts: The path of each ``<script src>`` the page loads.
    """

    def __init__(self):
        super().__init__()
        self.action = None
        self.options_url = None
        self.inputs = {}
        self.scripts = []
        self._form = None

    def handle_starttag(self, tag, attrs):
        """Read the passkey form's two URLs, each form's inputs and each script's source."""
        attributes = dict(attrs)
        if tag == "form":
            self._form = attributes.get("action")
            self.inputs[self._form] = []
            if "data-passkey-re-confirm" in attributes:
                self.action = self._form
                self.options_url = attributes.get("data-options-url")
        elif tag == "input" and self._form is not None:
            self.inputs[self._form].append(attributes.get("name"))
        elif tag == "script" and attributes.get("src"):
            # The path alone: each script's URL carries its version's query.
            self.scripts.append(urlsplit(attributes["src"]).path)

    def handle_endtag(self, tag):
        """Leave the form whose inputs were being read."""
        if tag == "form":
            self._form = None


def _page(client, query="", **request):
    """GET the re-confirm page (with *query*); return its text and what it offers."""
    response = client.get("/reauth" + query, **request)
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    parsed = _ReConfirmPage()
    parsed.feed(page)
    return page, parsed


def _offered(client, query="", **request):
    """GET the re-confirm page; assert it offers the passkey; return its text and form."""
    page, form = _page(client, query, **request)
    assert form.action is not None, "the re-confirm page renders no passkey form"
    return page, form


def _re_confirm(client, authenticator, query="", **get):
    """Run a whole passkey re-confirm through the page; return the form's response."""
    page, form = _offered(client, query)
    options = ask_options(client, page, form)
    return post_answer(client, page, form, authenticator.get(options, **get))


def _re_confirm_from(client, authenticator, address):
    """Run a whole passkey re-confirm through the page from *address*; return its response."""
    page, form = _offered(client, **address)
    answer = authenticator.get(ask_options(client, page, form, **address))
    return post_answer(client, page, form, answer, **address)


def _make_stale(client):
    """Age the browser's fresh-login stamp past the window, so a step-up asks again."""
    with client.session_transaction() as session:
        session[FRESH_LOGIN_AT_KEY] = (datetime.now(timezone.utc) - _STALE).isoformat()


def _fresh_login_at(client):
    """Return the browser's fresh-login stamp, as the session holds it."""
    with client.session_transaction() as session:
        return session.get(FRESH_LOGIN_AT_KEY)


def _refused_with(client, response):
    """Assert *response* sent the browser back to re-confirm; return the page it shows."""
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/reauth"
    return client.get(response.headers["Location"]).get_data(as_text=True)


def _lands_on(response):
    """Return the path a finished re-confirm's redirect names."""
    assert response.status_code == 302
    return urlsplit(response.headers["Location"]).path


def _device_cookie_set(response, user_id):
    """Return whether *response* sets *user_id*'s device cookie."""
    return any(
        header.split("=", 1)[0].endswith(f"shekel_device_{user_id}")
        for header in response.headers.getlist("Set-Cookie")
    )


def _refusals(caplog):
    """Return each passkey refusal logged, as ``(refusal, user_id)``."""
    return [
        (record.refusal, record.user_id) for record in caplog.records
        if getattr(record, "event", None) == "reauth_failed"
        and hasattr(record, "refusal")
    ]


def _forger(authenticator):
    """Return a key claiming *authenticator*'s passkey, its signature by another key."""
    forger = SoftwareAuthenticator(credential_id=authenticator.credential_id)
    forger.user_handle = authenticator.user_handle
    return forger


@pytest.fixture(name="owner")
def _owner(seed_user, auth_client):
    """The seed user, signed in on ``auth_client``, holding one passkey.

    Returns:
        ``(user, passkey_id, authenticator)``.
    """
    # Pylint: ``unused-argument`` -- ``auth_client`` is asked for so the
    # browser signs in before the passkey is added; the case reads it by name.
    # pylint: disable=unused-argument
    user = seed_user["user"]
    passkey_id, authenticator = add_committed(user)
    return user, passkey_id, authenticator


class TestThePage:
    """The re-confirm page offers a passkey to an account holding one, beside its password."""

    def test_a_passkey_account_is_offered_its_passkey_beside_the_password(
        self, auth_client, owner,
    ):
        """The passkey form posts to its door and names its options; the password form stays."""
        # Pylint: ``unused-argument`` -- ``owner`` adds the account's passkey.
        # pylint: disable=unused-argument
        page, form = _offered(auth_client)

        assert form.action == "/reauth/passkey"
        assert form.options_url == "/reauth/passkey/options"
        assert sorted(rendered_form_controls(page, form.action)) == [
            "credential", "csrf_token",
        ]
        assert sorted(form.inputs["/reauth"]) == ["csrf_token", "password", "totp_code"]
        assert "Re-confirm with your passkey" in page
        assert "/static/js/passkey.js" in form.scripts

    def test_an_account_with_no_passkey_is_offered_none(self, auth_client):
        """No passkey form, no passkey script: the password form alone."""
        page, form = _page(auth_client)

        assert form.action is None
        assert "Re-confirm with your passkey" not in page
        assert "/static/js/passkey.js" not in form.scripts
        assert sorted(form.inputs["/reauth"]) == ["csrf_token", "password", "totp_code"]

    @pytest.mark.usefixtures("owner")
    def test_the_form_keeps_a_safe_next_and_drops_an_unsafe_one(self, auth_client):
        """The passkey form posts back the page's ``next`` only where it is safe."""
        _, safe = _offered(auth_client, "?next=%2Fsettings")
        _, unsafe = _offered(auth_client, "?next=https%3A%2F%2Fevil.com")

        assert safe.action == "/reauth/passkey?next=/settings"
        assert unsafe.action == "/reauth/passkey"

    def test_no_options_for_an_account_holding_none(self, auth_client):
        """No passkey, no ceremony: options naming none would let the device offer anyone's.

        The page offers no passkey form here, so the options are asked as a
        tab rendered before the account's last passkey was removed asks them.
        """
        response = auth_client.post("/reauth/passkey/options")

        assert response.status_code == 404
        with auth_client.session_transaction() as session:
            assert _STATE_KEY not in session

    def test_the_options_name_only_the_accounts_own_passkeys(
        self, auth_client, owner, seed_second_user,
    ):
        """Another account's passkey is not named; the ceremony waits in the session."""
        _, passkey_id, _ = owner
        add_committed(seed_second_user["user"])
        page, form = _offered(auth_client)

        options = ask_options(auth_client, page, form)

        assert options == {"publicKey": {
            "challenge": options["publicKey"]["challenge"],
            "timeout": 270_000,
            "rpId": "shekel.test",
            "allowCredentials": [{
                "type": "public-key",
                "id": b64url(passkey_row(passkey_id).credential_id),
            }],
            "userVerification": "required",
        }}
        with auth_client.session_transaction() as session:
            state = session[_STATE_KEY]
        assert state["ceremony"] == "re_confirm"
        assert state["challenge"] == options["publicKey"]["challenge"]


class TestAReConfirm:
    """A passkey re-confirm does what a password re-confirm does."""

    def test_it_unlocks_a_step_up_action(self, auth_client, owner, seed_companion):
        """Stale, the owner is sent to re-confirm; by passkey, the action then runs.

        Deactivating a companion asks the re-confirm
        (``@fresh_login_required()``).  The page is reached by the redirect the
        decorator sends, as a browser follows it; its ``next`` is the action's
        absolute URL, which is refused, so the owner lands on the dashboard.
        """
        _, _, authenticator = owner
        companion_id = seed_companion["user"].id
        deactivate = f"/settings/companions/{companion_id}/deactivate"
        _make_stale(auth_client)
        bounced = auth_client.post(deactivate)
        assert _lands_on(bounced) == "/reauth"
        assert db.session.get(User, companion_id).is_active is True

        response = _re_confirm(
            auth_client, authenticator, "?" + urlsplit(bounced.headers["Location"]).query,
        )

        assert _lands_on(response) == "/dashboard"
        assert _lands_on(auth_client.post(deactivate)) == "/settings"
        db.session.expire_all()
        assert db.session.get(User, companion_id).is_active is False

    def test_the_window_restarts_and_the_session_keeps_its_age(
        self, auth_client, owner, caplog,
    ):
        """The fresh-login stamp moves to now; the session's two sign-in stamps do not.

        A re-confirm is not a sign-in (ruling plan#3069).  The passkey's use is
        recorded, and the success event names it.
        """
        user, passkey_id, authenticator = owner
        _make_stale(auth_client)
        with auth_client.session_transaction() as session:
            created = session[SESSION_CREATED_AT_KEY]
            signed_in = session[SESSION_SIGNED_IN_AT_KEY]
        before = datetime.now(timezone.utc)

        with caplog.at_level(logging.INFO):
            response = _re_confirm(auth_client, authenticator)

        after = datetime.now(timezone.utc)
        assert _lands_on(response) == "/dashboard"
        assert before <= datetime.fromisoformat(_fresh_login_at(auth_client)) <= after
        with auth_client.session_transaction() as session:
            assert session[SESSION_CREATED_AT_KEY] == created
            assert session[SESSION_SIGNED_IN_AT_KEY] == signed_in
        assert before <= passkey_row(passkey_id).last_used_at <= after
        successes = [
            (record.user_id, getattr(record, "passkey_id", None))
            for record in caplog.records
            if getattr(record, "event", None) == "reauth_success"
        ]
        assert successes == [(user.id, passkey_id)]

    def test_a_safe_next_is_honoured(self, auth_client, owner):
        """``/settings`` asked from, ``/settings`` landed on."""
        response = _re_confirm(auth_client, owner[2], "?next=%2Fsettings")

        assert response.headers["Location"] == "/settings"

    def test_an_unsafe_next_is_not(self, auth_client, owner):
        """Posted straight to the door with an absolute ``next``: the dashboard instead.

        The page's form drops an unsafe ``next`` already, so the post here
        names it itself: the route's own check is what is graded.
        """
        page, form = _offered(auth_client)
        answer = owner[2].get(ask_options(auth_client, page, form))

        response = post_answer(
            auth_client, page, form, answer,
            action="/reauth/passkey?next=https%3A%2F%2Fevil.com",
        )

        assert "evil.com" not in response.headers["Location"]
        assert _lands_on(response) == "/dashboard"

    def test_a_known_browser_gets_a_new_cookie_and_its_count_cleared(self, auth_client, owner):
        """One wrong password on its own record; the passkey clears it and renews the token.

        The old token names nothing afterwards, so a stolen copy of it stops
        working (ruling plan#3070).
        """
        user, _, authenticator = owner
        wrong = auth_client.post("/reauth", data={"password": "not-it"})
        assert _INVALID_PASSWORD in wrong.get_data(as_text=True)
        assert browser_count(user.id) == 1
        old_token = auth_client.get_cookie(f"shekel_device_{user.id}").value

        response = _re_confirm(auth_client, authenticator)

        assert _device_cookie_set(response, user.id)
        new_token = auth_client.get_cookie(f"shekel_device_{user.id}").value
        assert new_token != old_token
        assert known_device_service.find_browser(user.id, old_token) is None
        assert known_device_service.find_browser(user.id, new_token) is not None
        assert browser_count(user.id) == 0

    def test_a_stranger_gets_no_cookie_and_clears_no_strangers_count(self, auth_client, owner):
        """A browser whose record has gone: re-confirmed, no cookie, three guesses stay three.

        A known proof refills no stranger's guesses (ruling plan#3469).  With
        no record to renew, the passkey's use is all the re-confirm writes, and
        it is kept, so the answer cannot be sent again.
        """
        user, passkey_id, authenticator = owner
        make_a_stranger(user.id)
        set_record(
            SEED_USER_EMAIL, count=3, locked_until=None,
            last_attempt_at=datetime.now(timezone.utc),
        )
        db.session.commit()
        used_before = passkey_row(passkey_id).last_used_at

        response = _re_confirm(auth_client, authenticator)

        assert _lands_on(response) == "/dashboard"
        assert passkey_row(passkey_id).last_used_at > used_before
        assert not _device_cookie_set(response, user.id)
        assert record_of(SEED_USER_EMAIL).failed_login_count == 3
        assert db.session.execute(
            select(KnownDevice).where(KnownDevice.user_id == user.id)
        ).scalars().all() == []

    def test_an_account_the_lock_holds_re_confirms_by_passkey(self, auth_client, owner):
        """A stranger's browser: the lock refuses its right password, not its passkey.

        The record is left as the password's refusal left it.
        """
        user, _, authenticator = owner
        make_a_stranger(user.id)
        locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
        set_record(
            SEED_USER_EMAIL, count=0, locked_until=locked_until,
            last_attempt_at=datetime.now(timezone.utc),
        )
        db.session.commit()
        _make_stale(auth_client)
        stale = _fresh_login_at(auth_client)
        password = auth_client.post("/reauth", data={"password": SEED_USER_PASSWORD})
        assert password.status_code == 200
        assert _fresh_login_at(auth_client) == stale
        held = record_of(SEED_USER_EMAIL)
        held = (held.failed_login_count, held.locked_until)

        response = _re_confirm(auth_client, authenticator)

        assert _lands_on(response) == "/dashboard"
        assert _fresh_login_at(auth_client) != stale
        record = record_of(SEED_USER_EMAIL)
        assert (record.failed_login_count, record.locked_until) == held
        assert held[1] == locked_until

    def test_a_companion_re_confirms_by_her_passkey(self, companion_client, seed_companion):
        """Her own passkey restarts her window, and she lands on her own page.

        Never the owner's dashboard, which answers her 404: the one landing
        rule a sign-in follows, a safe ``next`` or none.
        """
        _, authenticator = add_committed(seed_companion["user"])
        _make_stale(companion_client)
        before = datetime.now(timezone.utc)

        response = _re_confirm(companion_client, authenticator)

        assert _lands_on(response) == "/companion/"
        assert companion_client.get(response.headers["Location"]).status_code == 200
        assert datetime.fromisoformat(_fresh_login_at(companion_client)) >= before
        assert _lands_on(
            _re_confirm(companion_client, authenticator, "?next=%2Fsettings"),
        ) == "/companion/"

    def test_an_account_with_a_code_is_asked_no_code(self, auth_client, owner):
        """The passkey stands in for the password and the code alike (ruling plan#3465)."""
        user, _, authenticator = owner
        enable_mfa(db.session, user.id)
        _make_stale(auth_client)
        stale = _fresh_login_at(auth_client)

        response = _re_confirm(auth_client, authenticator)

        assert _lands_on(response) == "/dashboard"
        assert _fresh_login_at(auth_client) != stale

    def test_a_sign_in_begun_meanwhile_leaves_the_re_confirm_in_flight(self, auth_client, owner):
        """Another tab's sign-in options wait under their own key; the re-confirm completes."""
        page, form = _offered(auth_client)
        options = ask_options(auth_client, page, form)
        token = rendered_form_controls(page, form.action)["csrf_token"][0]
        assert auth_client.post(
            "/login/passkey/options", headers={"X-CSRFToken": token},
        ).status_code == 200

        response = post_answer(auth_client, page, form, owner[2].get(options))

        assert _lands_on(response) == "/dashboard"


class TestThePasswordStays:
    """An account with a passkey keeps its password and code (ruling plan#3541)."""

    def test_its_password_still_re_confirms(self, auth_client, owner):
        """No code on the account: the password alone, as before it held a passkey."""
        # Pylint: ``unused-argument`` -- ``owner`` adds the account's passkey.
        # pylint: disable=unused-argument
        _make_stale(auth_client)
        before = datetime.now(timezone.utc)

        response = auth_client.post("/reauth", data={"password": SEED_USER_PASSWORD})

        assert _lands_on(response) == "/dashboard"
        assert datetime.fromisoformat(_fresh_login_at(auth_client)) >= before

    def test_its_password_and_code_still_re_confirm(self, auth_client, owner):
        """With a code: a wrong one is still refused, and the right one re-confirms."""
        user, _, _ = owner
        secret = enable_mfa(db.session, user.id)
        _make_stale(auth_client)
        stale = _fresh_login_at(auth_client)
        wrong_code = auth_client.post(
            "/reauth", data={"password": SEED_USER_PASSWORD, "totp_code": "000000"},
        )
        assert "Invalid authentication code." in wrong_code.get_data(as_text=True)
        assert _fresh_login_at(auth_client) == stale

        response = auth_client.post("/reauth", data={
            "password": SEED_USER_PASSWORD, "totp_code": pyotp.TOTP(secret).now(),
        })

        assert _lands_on(response) == "/dashboard"
        assert _fresh_login_at(auth_client) != stale


class TestARefusal:
    """Each refusal sends the browser back to re-confirm, the window as it was."""

    def test_another_accounts_passkey(self, auth_client, owner, seed_second_user, caplog):
        """The second user's passkey, answering the owner's ceremony: refused, its use not kept."""
        user, _, _ = owner
        theirs_id, theirs = add_committed(seed_second_user["user"])
        used_before = passkey_row(theirs_id).last_used_at
        _make_stale(auth_client)
        stale = _fresh_login_at(auth_client)

        with caplog.at_level(logging.WARNING):
            page = _refused_with(auth_client, _re_confirm(auth_client, theirs))

        assert _TRY_AGAIN in page
        assert _fresh_login_at(auth_client) == stale
        assert passkey_row(theirs_id).last_used_at == used_before
        assert _refusals(caplog) == [("unknown_credential", user.id)]

    def test_a_replayed_answer(self, auth_client, owner, caplog):
        """An answer and its ceremony copied before their use, sent again after it: refused.

        The ceremony waits in the session cookie, so a copy of the cookie
        taken before the answer was posted holds it still; the passkey's last
        use, recorded at the first post, is what refuses the second.
        """
        user, passkey_id, authenticator = owner
        page, form = _offered(auth_client)
        answer = authenticator.get(ask_options(auth_client, page, form))
        with auth_client.session_transaction() as session:
            copied = dict(session[_STATE_KEY])
        assert _lands_on(post_answer(auth_client, page, form, answer)) == "/dashboard"
        used = passkey_row(passkey_id).last_used_at
        _make_stale(auth_client)
        stale = _fresh_login_at(auth_client)
        with auth_client.session_transaction() as session:
            session[_STATE_KEY] = copied

        with caplog.at_level(logging.WARNING):
            shown = _refused_with(auth_client, post_answer(auth_client, page, form, answer))

        assert _TRY_AGAIN in shown
        assert _fresh_login_at(auth_client) == stale
        assert passkey_row(passkey_id).last_used_at == used
        assert _refusals(caplog) == [("replayed", user.id)]

    def test_a_second_submit_of_one_answer(self, auth_client, owner):
        """The ceremony leaves the session at the first submit, so the second meets none."""
        page, form = _offered(auth_client)
        answer = owner[2].get(ask_options(auth_client, page, form))
        assert _lands_on(post_answer(auth_client, page, form, answer)) == "/dashboard"

        shown = _refused_with(auth_client, post_answer(auth_client, page, form, answer))

        assert _TOO_LONG in shown

    def test_an_expired_challenge(self, auth_client, owner, caplog):
        """Answered five minutes after its options: refused as too long, the window as it was."""
        user, _, authenticator = owner
        page, form = _offered(auth_client)
        answer = authenticator.get(ask_options(auth_client, page, form))
        _make_stale(auth_client)
        stale = _fresh_login_at(auth_client)

        # The session is read inside the travel: a cookie signed then is
        # refused once the clock is back, as one signed in the future.
        with caplog.at_level(logging.WARNING):
            with time_machine.travel(datetime.now(timezone.utc) + CHALLENGE_LIFETIME):
                shown = _refused_with(auth_client, post_answer(auth_client, page, form, answer))
                after = _fresh_login_at(auth_client)

        assert _TOO_LONG in shown
        assert after == stale
        assert _refusals(caplog) == [("expired", user.id)]

    def test_a_refused_passkey_counts_on_no_record(self, auth_client, owner):
        """A forged answer at a known browser writes neither its count nor the account's.

        The browser's own record holds one wrong password beforehand and the
        account's two, so a count moving on either, either way, is seen.
        """
        user, _, authenticator = owner
        auth_client.post("/reauth", data={"password": "not-it"})
        assert browser_count(user.id) == 1
        set_record(
            SEED_USER_EMAIL, count=2, locked_until=None,
            last_attempt_at=datetime.now(timezone.utc),
        )
        db.session.commit()

        shown = _refused_with(auth_client, _re_confirm(auth_client, _forger(authenticator)))

        assert _TRY_AGAIN in shown
        record = record_of(SEED_USER_EMAIL)
        assert (record.failed_login_count, record.locked_until) == (2, None)
        assert browser_count(user.id) == 1

    def test_a_refusal_keeps_the_pages_next(self, auth_client, owner):
        """Sent back to the page it came from, its safe ``next`` still on it."""
        response = _re_confirm(auth_client, _forger(owner[2]), "?next=%2Fsettings")

        assert response.headers["Location"] == "/reauth?next=/settings"


class TestTheTries:
    """One of this door's tries is spent first, five per counter, its own (ruling plan#3071)."""

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

    def test_a_stolen_session_that_spends_the_accounts_tries_cannot_refuse_the_owner(
        self, rate_app, seed_user,
    ):
        """A copy of the session spends the account's five and is refused a sixth; not the owner.

        The copy holds no device cookie, so it is a stranger and spends the
        account's counter; the owner's browser is known and spends its own.
        """
        address = through_nginx(_OWNERS_ADDRESS)
        with rate_app.app_context():
            owner = signed_in_browser(rate_app, _OWNERS_ADDRESS)
            _, authenticator = add_committed(seed_user["user"])
            thief = stolen_session(rate_app, owner)
            page, form = _offered(thief, **address)
            for attempt in range(_TRIES):
                response = post_answer(thief, page, form, "", **address)
                assert _lands_on(response) == "/reauth", f"try {attempt + 1} was not answered"

            refused = post_answer(thief, page, form, "", **address)
            assert refused.status_code == 429
            assert _TOO_MANY in refused.get_data(as_text=True)

            assert _lands_on(_re_confirm_from(owner, authenticator, address)) == "/dashboard"

    def test_the_try_is_spent_before_the_answer_is_checked(self, rate_app, seed_user):
        """Five spent, a sixth with a right answer is refused unchecked: nothing it would write is.

        The window does not restart and the passkey's use is not recorded, so
        the limit answers before the answer is read, not after it is kept.
        """
        address = through_nginx(_OWNERS_ADDRESS)
        with rate_app.app_context():
            owner = signed_in_browser(rate_app, _OWNERS_ADDRESS)
            passkey_id, authenticator = add_committed(seed_user["user"])
            page, form = _offered(owner, **address)
            for _ in range(_TRIES):
                post_answer(owner, page, form, "", **address)
            answer = authenticator.get(ask_options(owner, page, form, **address))
            _make_stale(owner)
            stale = _fresh_login_at(owner)
            used = passkey_row(passkey_id).last_used_at

            refused = post_answer(owner, page, form, answer, **address)

            assert refused.status_code == 429
            assert _fresh_login_at(owner) == stale
            assert passkey_row(passkey_id).last_used_at == used

    def test_the_password_doors_tries_are_its_own(self, rate_app, seed_user):
        """Five passkey tries spent leave the password door's five: a password is still checked."""
        address = through_nginx(_OWNERS_ADDRESS)
        with rate_app.app_context():
            owner = signed_in_browser(rate_app, _OWNERS_ADDRESS)
            add_committed(seed_user["user"])
            page, form = _offered(owner, **address)
            for _ in range(_TRIES):
                post_answer(owner, page, form, "", **address)
            assert post_answer(owner, page, form, "", **address).status_code == 429

            checked = owner.post("/reauth", data={"password": "not-it"}, **address)

            assert checked.status_code == 200
            assert _INVALID_PASSWORD in checked.get_data(as_text=True)


class TestTheFormToken:
    """With the form token checked, as production checks it: what passkey.js sends is enough."""

    def test_the_pages_token_carries_the_options_and_the_answer(
        self, app, auth_client, owner, monkeypatch,
    ):
        """The form's token in the options' header, and in the form, re-confirms the owner.

        Flask-WTF reads the setting per request, so switching it on for the
        live app checks every request after this.
        """
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
        _make_stale(auth_client)
        stale = _fresh_login_at(auth_client)

        response = _re_confirm(auth_client, owner[2])

        assert _lands_on(response) == "/dashboard"
        assert _fresh_login_at(auth_client) != stale

    @pytest.mark.usefixtures("owner")
    def test_the_options_are_refused_without_the_token(self, app, auth_client, monkeypatch):
        """No header, no options: the fetch's token is what the check reads."""
        monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)

        assert auth_client.post("/reauth/passkey/options").status_code == 400
