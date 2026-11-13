"""The doors behind the two-factor hold use the row the login gate read, and ask nothing again.

Every account past the login gate has two-factor on: the gate holds one
without it at setup (``app/login_gate.py``, plan#3452), and to know it reads the
account's two-factor row, ``auth.mfa_configs`` (``User.has_two_factor``).  So
"Regenerate backup codes" writes its new codes onto that row, and the
re-confirm page checks its code against it, always (ruling plan#3826, built at
plan#3775): neither reads the row a second time to ask again whether two-factor
is on.  Each test counts the statements a door's request sends that read the
table: the gate's one, and none of the door's own.
"""

import re
from collections.abc import Callable

from sqlalchemy import event
from werkzeug.test import TestResponse

from app.extensions import db
from app.services import mfa_service
from tests._seed_owner_fixtures import SEED_USER_PASSWORD
from tests._two_factor_sign_in import (
    code_now,
    redirect_path,
    two_factor_row_of,
    two_factor_secret_of,
)

#: A statement that reads the two-factor table: a SELECT whose FROM names it.
_READS_THE_TWO_FACTOR_TABLE = re.compile(r"\bFROM auth\.mfa_configs\b")

#: A backup code as the codes page shows it: 28 lowercase hex characters,
#: bounded so no part of a longer hex string is counted.
_SHOWN_CODE = re.compile(r"(?<![0-9a-f])[0-9a-f]{28}(?![0-9a-f])")


def _reads_of_the_two_factor_table(
    send: Callable[[], TestResponse],
) -> tuple[TestResponse, list[str]]:
    """Run *send*, and return its result and each statement it sent that read the table.

    Args:
        send: A zero-argument callable that issues one request.

    Returns:
        ``(response, reads)``: what *send* returned, and the text of every
        statement it sent that reads ``auth.mfa_configs``, in order.
    """
    reads = []

    def on_execute(_conn, _cursor, statement, *_args, **_kwargs):
        if _READS_THE_TWO_FACTOR_TABLE.search(statement):
            reads.append(statement)

    event.listen(db.engine, "before_cursor_execute", on_execute)
    try:
        response = send()
    finally:
        event.remove(db.engine, "before_cursor_execute", on_execute)
    return response, reads


def _only_the_gate_read(reads: list[str]) -> str:
    """The failure message when a door's request read the table more than the gate's once."""
    return (
        f"the request read auth.mfa_configs {len(reads)} times; only the login "
        f"gate's read is wanted, and the door uses the row it read (ruling "
        f"plan#3826): {reads}"
    )


def test_regenerate_writes_its_codes_onto_the_row_the_gate_read(app, auth_client, seed_user):
    """Regenerate reads the two-factor row only through the gate, and its new codes are stored."""
    with app.app_context():
        response, reads = _reads_of_the_two_factor_table(
            lambda: auth_client.post("/mfa/regenerate-backup-codes"),
        )

        assert response.status_code == 200
        shown = _SHOWN_CODE.findall(response.get_data(as_text=True))
        assert len(shown) == 10
        stored = two_factor_row_of(seed_user["user"].id).backup_codes
        assert len(stored) == 10
        assert mfa_service.verify_backup_code(shown[0], stored) != -1
        assert len(reads) == 1, _only_the_gate_read(reads)


def test_reauth_checks_its_code_against_the_row_the_gate_read(app, auth_client, seed_user):
    """The re-confirm page reads the two-factor row only through the gate, and takes the right code."""
    user_id = seed_user["user"].id
    with app.app_context():
        form = {
            "password": SEED_USER_PASSWORD,
            "totp_code": code_now(two_factor_secret_of(user_id)),
        }
        response, reads = _reads_of_the_two_factor_table(
            lambda: auth_client.post("/reauth", data=form),
        )

        assert response.status_code == 302
        assert redirect_path(response) == "/dashboard"
        assert len(reads) == 1, _only_the_gate_read(reads)
