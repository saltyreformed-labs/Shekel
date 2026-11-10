"""Shekel Budget App -- the field-encryption key, as the environment states it.

The ONE home of what ``FIELD_ENCRYPTION_KEY`` and its rotation twin
``FIELD_ENCRYPTION_KEY_OLD`` mean, and of the cipher built from them: the
names, the parse of their values into the ordered Fernet list a
``MultiFernet`` is built from, the refusal an environment still spelling
the key's OLD names meets, and the ``encrypt_secret`` / ``decrypt_secret``
pair every ciphertext column is written and read through.  No Flask, no
models -- ``app.config`` reads this module at import time to validate a
production configuration; the MFA service, the MFA routes and the
rotation script read it at call time.

The key was ``TOTP_ENCRYPTION_KEY`` until plan step ``bank_import:X-f6b-2``
renamed it (ledger row BI-503): it encrypts every ciphertext column the
app stores (``auth.mfa_configs``' TOTP secret, ``budget.bank_feeds``' access
URL), and ``TOTP`` named one of them.  The cipher pair lived in
``app.services.mfa_service`` until the same step gave the key that second
column to protect (ruling **R-BI12**); a cipher for every encrypted column
has no business under an MFA-named module, so the pair moved here to the
key it is built from (ruling **R-BI24**), and every caller reaches it at
this one home.  ``scripts/rotate_field_key.py`` re-wraps both columns.
"""

import os

from cryptography.fernet import Fernet, MultiFernet

#: The environment variable holding the primary Fernet key.
PRIMARY_KEY_ENV = "FIELD_ENCRYPTION_KEY"

#: The environment variable holding the comma-separated retired keys a
#: rotation keeps readable until ``scripts/rotate_field_key.py`` has
#: re-wrapped every ciphertext under the primary.
RETIRED_KEYS_ENV = "FIELD_ENCRYPTION_KEY_OLD"

# The names the key answered to before the rename.  Nothing reads these
# names any more, so a value found under one is a configuration the
# operator has not carried across the rename -- and a retired key parked
# under the old rotation twin is the shape that loses every ciphertext
# still written under it.
RETIRED_FIELD_KEY_NAMES = ("TOTP_ENCRYPTION_KEY", "TOTP_ENCRYPTION_KEY_OLD")


def refuse_retired_field_key_names() -> None:
    """Refuse to start while the environment spells the key's old name.

    Run by ``BaseConfig.__init__`` -- so by ``create_app`` in every
    environment, which instantiates the configuration class -- before
    anything reads the key.  A variable that is present but EMPTY is
    not a configuration (it holds no key, the way ``fernet_list_from``
    reads an empty primary as unset), so an ``.env`` that kept an empty
    ``TOTP_ENCRYPTION_KEY_OLD=`` line after renaming the primary does
    not refuse.

    Raises:
        RuntimeError: When any name in ``RETIRED_FIELD_KEY_NAMES`` holds
            a non-empty value.  The message names the rename and the
            runbook section that carries it out, for both postures.
    """
    stale = [name for name in RETIRED_FIELD_KEY_NAMES if os.getenv(name)]
    if stale:
        raise RuntimeError(
            f"{', '.join(stale)} is set, and the app no longer reads "
            f"it: the key is {PRIMARY_KEY_ENV} (and its rotation "
            f"twin {RETIRED_KEYS_ENV}) since it became the key "
            "for every encrypted column, not the TOTP secret alone.  "
            "Rename the variable in .env, or the secret file "
            "/opt/docker/shekel/secrets/totp_encryption_key to "
            "field_encryption_key with the compose secrets block, "
            "then start again.  See docs/runbook_secrets.md "
            '"Renaming TOTP_ENCRYPTION_KEY to FIELD_ENCRYPTION_KEY".'
        )


def fernet_list_from(primary: "str | None", retired_raw: "str | None") -> list[Fernet]:
    """Parse the key values into the ordered Fernet list a MultiFernet takes.

    The primary is used for encryption AND appears first for
    decryption.  ``retired_raw`` holds zero or more comma-separated
    retired keys; each is wrapped in a Fernet and tried in order after
    the primary on decrypt.  Blank entries (empty strings,
    whitespace-only) are skipped so an operator can leave a stray comma
    after pruning a key without breaking startup.

    ONE parser, two readers: ``build_fernet_list`` hands it the
    environment at call time (the cipher), ``ProdConfig.__init__``
    hands it the values captured at import (the start-up refusal), so
    what production refuses and what the cipher would build are one
    derivation.

    Args:
        primary: The primary key's value, or ``None`` / ``""`` when unset.
        retired_raw: The retired keys' comma-separated value, or ``None``.

    Returns:
        list[Fernet]: Non-empty list with the primary key at index 0.

    Raises:
        RuntimeError: If ``primary`` is unset or empty.
        ValueError: If any configured key fails to initialize as a
            Fernet instance.  ``Fernet`` raises ``ValueError`` for
            wrong-length keys and ``binascii.Error`` (a ``ValueError``
            subclass) for non-base64 input, so a single ``ValueError``
            catch covers both invalid forms.
    """
    if not primary:
        raise RuntimeError(
            f"{PRIMARY_KEY_ENV} environment variable is not set."
        )
    fernets = [Fernet(primary)]
    for raw in (retired_raw or "").split(","):
        candidate = raw.strip()
        if candidate:
            fernets.append(Fernet(candidate))
    return fernets


def build_fernet_list() -> list[Fernet]:
    """Build the ordered Fernet list from the environment, as it is NOW.

    Read at call time rather than import time so a rotation that
    changes the environment between requests -- and a test that
    ``monkeypatch.setenv``s a fresh key -- is seen by the next cipher
    built.

    Returns:
        list[Fernet]: Non-empty list with the primary key at index 0.

    Raises:
        RuntimeError: If ``FIELD_ENCRYPTION_KEY`` is unset or empty.
        ValueError: If any configured key cannot be parsed as a Fernet
            key (wrong length or non-base64 input).
    """
    return fernet_list_from(
        os.getenv(PRIMARY_KEY_ENV), os.getenv(RETIRED_KEYS_ENV, ""),
    )


def get_encryption_key() -> MultiFernet:
    """Load the MultiFernet cipher from the environment.

    The returned cipher encrypts with the primary key
    (``FIELD_ENCRYPTION_KEY``) and decrypts with any primary-or-retired
    key listed in ``FIELD_ENCRYPTION_KEY_OLD``.  This makes
    ``FIELD_ENCRYPTION_KEY`` rotation a non-destructive operation:

      1. Move the existing primary value into ``FIELD_ENCRYPTION_KEY_OLD``.
      2. Set the new key as ``FIELD_ENCRYPTION_KEY``.  The application
         can immediately decrypt legacy ciphertexts via the retired key
         and writes new ciphertexts under the new primary.
      3. Run ``scripts/rotate_field_key.py --confirm`` to re-wrap every
         existing ciphertext under the new primary.
      4. Remove the retired value from ``FIELD_ENCRYPTION_KEY_OLD`` at
         the next deploy.

    See ``docs/runbook_secrets.md`` for the full procedure.

    The public API exposed by ``MultiFernet`` is identical to
    ``Fernet`` -- ``encrypt``, ``decrypt``, and ``rotate`` -- so all
    callers of this function continue to work unchanged.  The key list
    itself is :func:`build_fernet_list`, the one parse of the
    environment that ``ProdConfig`` also validates against at start-up.

    Returns:
        MultiFernet: A cipher initialized with the primary key first
            and any retired keys appended in declaration order.

    Raises:
        RuntimeError: If ``FIELD_ENCRYPTION_KEY`` is unset or empty.
        ValueError: If any configured key cannot be parsed as a Fernet
            key (wrong length or non-base64 input).
    """
    return MultiFernet(build_fernet_list())


def encrypt_secret(plaintext_secret: str) -> bytes:
    """Encrypt a secret for storage in a ciphertext column.

    Args:
        plaintext_secret: The secret as text -- a base32 TOTP secret,
            a bank feed's access URL.

    Returns:
        bytes: The Fernet-encrypted ciphertext, written under the
        primary key.

    Raises:
        RuntimeError: If ``FIELD_ENCRYPTION_KEY`` is unset or empty.
        ValueError: If any configured key cannot be parsed as a Fernet
            key.
    """
    return get_encryption_key().encrypt(plaintext_secret.encode("utf-8"))


def decrypt_secret(encrypted_secret: bytes) -> str:
    """Decrypt a secret read from a ciphertext column.

    Args:
        encrypted_secret: The Fernet-encrypted ciphertext bytes.

    Returns:
        str: The original plaintext secret.

    Raises:
        RuntimeError: If ``FIELD_ENCRYPTION_KEY`` is unset or empty.
        ValueError: If any configured key cannot be parsed as a Fernet
            key.
        cryptography.fernet.InvalidToken: If no configured key can
            decrypt the ciphertext.
    """
    return get_encryption_key().decrypt(encrypted_secret).decode("utf-8")
