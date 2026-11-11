"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_config.py``, moved verbatim by ``tools/split``.
"""

from app.config import _DATABASE_URL_SENTINEL


# A plausible SECRET_KEY shape for tests that expect ProdConfig to
# accept the value: 64-character hex (256 bits).  Not a real secret;
# tests that deploy this value would still fail because it is short
# entropy and well-known.  Used only for unit tests of ProdConfig.
_VALID_SECRET_KEY = "0123456789abcdef" * 4  # 64 chars


# A rate-limit store password for the tests that expect a configuration
# beside Valkey (ProdConfig, DevContainerConfig) to build: from ruling
# plan#2633 such a configuration refuses to build without one.
_STORE_PASSWORD = "a-store-password"


# Where those configurations name their store (ruling plan#2633), written
# out here so a moved address fails a test rather than passing on its own
# constant.
_STORE_SOCKET = "valkey+unix:///run/valkey/valkey.sock"


# A passkey origin for the tests that expect ProdConfig to build: from
# plan#3488 it refuses to build without one, or with one no browser
# writes.  Not TestConfig's own, so a test reading it back reads the
# value it set.
_PASSKEY_ORIGIN = "https://budget.example.com"


# Helper URIs for the sentinel tests.  Constructed at module import so
# the sentinel substring inside them comes from the production
# constant -- a refactor that renames the sentinel constant cannot
# silently drift these fixtures out of sync.
_SENTINEL_DATABASE_URL = (
    f"postgresql://shekel_user:{_DATABASE_URL_SENTINEL}@localhost:5432/shekel"
)


_SENTINEL_TEST_DATABASE_URL = (
    f"postgresql://shekel_user:{_DATABASE_URL_SENTINEL}@localhost:5433/shekel_test"
)


_CLEAN_DATABASE_URL = (
    "postgresql://shekel_user:real-password-not-the-sentinel@localhost:5432/shekel"
)


_CLEAN_TEST_DATABASE_URL = (
    "postgresql://shekel_user:real-password-not-the-sentinel@localhost:5433/shekel_test"
)
