"""Tests for ``tests/manual/forged_sign_in.py``: the scripts' one forged sign-in proves itself.

Ruling plan#3804: the scripts that render a copy as its owner forge the
owner's session, and before handing the client back the forge opens a page
behind the sign-in rules and stops the script, with the status for "nothing
was measured", when the app does not let the session in.  Measured in
plan#3602: without the proof, a copy whose owner has no two-factor had two of
the scripts exit 0 having saved only the redirect to setup.

Each of the three answers is graded here, each refusal by making the app give
it: an account the app lets in, one held at two-factor setup, one the app
refuses, and a proof page answering something else.
"""

from urllib.parse import urlsplit

import pytest

from app.extensions import db, login_manager
from app.login_gate import PUBLIC_ENDPOINTS, TWO_FACTOR_SETUP_ENDPOINTS
from app.models.user import User
from tests._two_factor_sign_in import reset_two_factor_of
from tests.manual import forged_sign_in
from tests.manual.exit_status import NOTHING_MEASURED


@pytest.fixture(name="forgeable")
def _forgeable(monkeypatch):
    """Switch strong session protection off, as every caller of the forge does first."""
    monkeypatch.setattr(login_manager, "session_protection", None)


@pytest.mark.usefixtures("forgeable")
class TestSignedInClient:
    """The forge hands back a client the app lets in, and stops for any other."""

    def test_a_session_the_app_lets_in_is_handed_back(self, app, seed_user):
        """The seed owner has two-factor: the client reaches their pages.

        The navigation bar names the signed-in user and renders only for one
        (``base.html``), so a page carrying it was rendered for them.
        """
        client = forged_sign_in.signed_in_client(app, seed_user["user"].id)

        page = client.get("/settings")
        assert page.status_code == 200
        assert seed_user["user"].display_name == "Test User"
        assert "Test User" in page.get_data(as_text=True)

    def test_an_account_without_two_factor_stops_naming_the_dev_key(
        self, app, seed_user, capsys,
    ):
        """Held at setup: nothing measured, and the fix named.

        The owner's two-factor is taken away by the one reset act, so the app
        sends the session to setup from every page.
        """
        user_id = seed_user["user"].id
        reset_two_factor_of(user_id)

        with pytest.raises(SystemExit) as stopped:
            forged_sign_in.signed_in_client(app, user_id)

        assert stopped.value.code == NOTHING_MEASURED
        said = capsys.readouterr().err
        assert f"User {user_id} has no two-factor" in said
        assert "/mfa/setup" in said
        assert "python -m tools.dev_two_factor <its email>" in said

    def test_a_session_the_app_refuses_stops_naming_the_sign_in(
        self, app, seed_user, capsys,
    ):
        """A deactivated owner: ``load_user`` refuses the session, nothing measured."""
        user_id = seed_user["user"].id
        db.session.get(User, user_id).is_active = False
        db.session.commit()

        with pytest.raises(SystemExit) as stopped:
            forged_sign_in.signed_in_client(app, user_id)

        assert stopped.value.code == NOTHING_MEASURED
        said = capsys.readouterr().err
        assert f"The app refused the forged session of user {user_id}" in said
        assert "sending it to /login" in said

    def test_any_other_answer_stops_naming_it(self, app, seed_user, monkeypatch, capsys):
        """A proof page answering neither 200 nor a sign-in redirect stops too.

        Pointed at a URL no route matches, which a signed-in session is
        answered 404 for.
        """
        monkeypatch.setattr(forged_sign_in, "PROOF_PAGE", "/no-such-page")
        user_id = seed_user["user"].id

        with pytest.raises(SystemExit) as stopped:
            forged_sign_in.signed_in_client(app, user_id)

        assert stopped.value.code == NOTHING_MEASURED
        said = capsys.readouterr().err
        assert f"/no-such-page answered 404 to the forged session of user {user_id}" in said


def test_the_proof_page_is_behind_the_two_factor_hold(app):
    """``/security`` is neither public nor a setup page, so the hold covers it.

    Were it either, a held account would reach it and the forge would pass a
    session that every other page sends to setup.
    """
    adapter = app.url_map.bind("localhost")
    endpoint, _ = adapter.match(urlsplit(forged_sign_in.PROOF_PAGE).path, method="GET")
    assert endpoint == "auth.security"
    assert endpoint not in PUBLIC_ENDPOINTS
    assert endpoint not in TWO_FACTOR_SETUP_ENDPOINTS
