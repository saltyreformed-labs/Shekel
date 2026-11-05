"""The environment a script runs in as a child, under the production config, on one database."""
from __future__ import annotations

import os
import secrets


def production_env(database_url: str) -> dict:
    """Build the child's environment: the production config, on *database_url*.

    Only what Python needs to run is inherited.  ``ProdConfig`` refuses to
    start without a SECRET_KEY of real length and a non-memory rate-limit
    store; neither is contacted by a child that only reads and writes its
    database.  An explicitly EMPTY ``DATABASE_URL_APP`` (the config resolver
    reads it as unset) leaves no other database to reach.

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
        "RATELIMIT_STORAGE_URI": "redis://127.0.0.1:6379/0",
    })
    return env
