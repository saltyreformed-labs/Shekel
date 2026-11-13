"""Architecture test: the whole suite holds ONE two-factor encryption key.

Ruling plan#3890.  ``tests/conftest.py`` writes ``TOTP_ENCRYPTION_KEY`` before
the ``app`` package is imported, so the app's config reads it at import, as
production's does, and every build, test, worker and run holds that one key.
A seeded start state is built once per worker, and its owner's two-factor
secret is encrypted under the key in force at the build
(``tests/_seeded_start_states.py``), so a key that changed between tests would
leave that secret unreadable from the next test on.
"""

import os


def test_each_test_holds_the_key_the_app_was_built_with(app):
    """The environment holds the key the session's app read at import.

    The ``app`` fixture is built once per worker from the config class body,
    which read the environment when ``app`` was first imported, so its key is
    the one ``tests/conftest.py`` wrote.  ``app.services.mfa_service`` reads
    the environment at each call, so a key there that differs from the app's
    is a key that changed after the import.  Measured 2026-11-13: an autouse
    function fixture setting a fresh key for each test, the shape the ruling
    replaced, fails this on every run.
    """
    suite_key = app.config["TOTP_ENCRYPTION_KEY"]
    assert suite_key, (
        "the app was built with no two-factor key: tests/conftest.py wrote "
        "none before the app was imported"
    )
    assert os.environ.get("TOTP_ENCRYPTION_KEY") == suite_key, (
        "this test's environment holds a two-factor key other than the one "
        "the app read at import: something set a key after tests/conftest.py, "
        "so a world built under one key would be read under another"
    )
