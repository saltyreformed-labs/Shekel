"""The environment a script runs in as a child, under the production config, on one database."""
from __future__ import annotations

import os
import secrets


def production_env(database_url: str) -> dict:
    """Build the child's environment: the production config, on *database_url*.

    Only what Python needs to run is inherited.  ``ProdConfig`` refuses to
    start without a SECRET_KEY of real length, its passkey origin (plan#3488)
    and its rate-limit store's password; a child that only reads and writes
    its database uses none of them.
    An explicitly EMPTY ``DATABASE_URL_APP`` (the config resolver reads it as
    unset) leaves no other database to reach.

    Args:
        database_url: The URL of this test's database, password included.

    Returns:
        The environment mapping for :func:`subprocess.run`.
    """
    env = {
        key: os.environ[key]
        for key in ("PATH", "PYTHONPATH", "PYTHONHOME", "HOME", "VIRTUAL_ENV",
                    "LANG", "LC_ALL")
        if key in os.environ
    }
    env.update({
        "FLASK_ENV": "production",
        "DATABASE_URL": database_url,
        "DATABASE_URL_APP": "",
        "SECRET_KEY": secrets.token_hex(32),
        # ``.test`` is reserved (RFC 2606): it names no real site.
        "PASSKEY_ORIGIN": "https://shekel.test",
        "VALKEY_PASSWORD": secrets.token_hex(16),
    })
    return env
