"""
Shekel Budget App -- Tests for the field-encryption key and its cipher

``TOTP_ENCRYPTION_KEY`` became ``FIELD_ENCRYPTION_KEY`` (and its rotation
twin ``FIELD_ENCRYPTION_KEY_OLD``) in ``bank_import:X-f6b-2`` (ledger row
BI-503): the key encrypts every ciphertext column the app stores, and
``TOTP`` named one of them.  Four things this module pins:

  1. ``app.utils.field_encryption.refuse_retired_field_key_names`` is
     what an environment still spelling an old name meets.
  2. Ruling **R-BI23**: production REQUIRES the key -- ``ProdConfig``
     refuses a missing key, the docker-secret placeholder, and a value
     Fernet cannot load (retired keys included).
  3. ``create_app`` is the door for both: it INSTANTIATES the
     configuration class, which is what runs the refusals.  Until this
     step the factory passed the class to ``from_object``, which reads
     attributes and instantiates nothing, and every production refusal
     was dead at runtime.
  4. The cipher pair ``encrypt_secret`` / ``decrypt_secret`` and the
     ``MultiFernet`` it is built on (``get_encryption_key``): the
     primary key writes, the primary-then-retired list reads, and the
     retired list's parse tolerates what an operator's hand leaves
     behind.  These moved here from
     ``tests/test_services/test_mfa_service.py`` with the pair itself,
     which lived in the MFA service until ruling **R-BI12** gave the
     key a second column to protect.
"""

import pytest
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app import create_app
from app.config import BaseConfig, ProdConfig
from app.utils.field_encryption import (
    RETIRED_FIELD_KEY_NAMES,
    build_fernet_list,
    decrypt_secret,
    encrypt_secret,
    get_encryption_key,
    refuse_retired_field_key_names,
)

# A plaintext of the shape the first ciphertext column holds (a base32
# TOTP secret).  A literal rather than ``mfa_service.generate_totp_secret``:
# the cipher does not know what it encrypts, and these tests should not
# reach into the MFA service to prove that.
_A_PLAINTEXT = "JBSWY3DPEHPK3PXP"

# A SECRET_KEY that passes production's length and placeholder checks
# and could not be mistaken for a secret by anyone, gitleaks included.
_VALID_SECRET_KEY = "a" * 64


@pytest.fixture
def a_valid_production_config(monkeypatch):
    """Patch ``ProdConfig`` so that ONLY the field key can refuse.

    The class attributes are read at import time, so the cases below
    patch the attributes rather than the environment.  Every other
    production refusal (SECRET_KEY, DATABASE_URL, the limiter) is
    satisfied here; the field key is left to each case.
    """
    monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
    monkeypatch.setattr(
        ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel",
    )
    monkeypatch.setattr(
        ProdConfig, "RATELIMIT_STORAGE_URI", "redis://redis:6379/0",
    )
    monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY_OLD", None)


class TestTheRetiredKeyNamesRefuseStartup:
    """``TOTP_ENCRYPTION_KEY`` / ``TOTP_ENCRYPTION_KEY_OLD`` in the
    environment refuse ``create_app`` (``bank_import:X-f6b-2``, BI-503).

    Nothing reads the old names, so a value found under one is a
    configuration the operator did not carry across the rename; the app
    refuses to start rather than run with MFA unavailable and a "not
    set" warning over a key the operator believes is set.  The autouse
    ``set_field_encryption_key`` fixture sets the NEW name for every
    case here, so each refusal below fires beside a correctly named key
    -- presence of the old name is the fault, not absence of the new.
    """

    def test_the_two_retired_names_are_the_old_primary_and_its_twin(self):
        """The census the refusal reads is exactly the two old spellings."""
        assert RETIRED_FIELD_KEY_NAMES == (
            "TOTP_ENCRYPTION_KEY", "TOTP_ENCRYPTION_KEY_OLD",
        )

    def test_the_old_primary_name_is_refused_beside_the_new_one(
        self, monkeypatch,
    ):
        """A stale ``.env`` line under the old name refuses, new name or not.

        The message names the variable found, the new name, and the
        runbook section that carries the rename out.
        """
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "stale-value")
        with pytest.raises(RuntimeError) as exc:
            refuse_retired_field_key_names()
        message = str(exc.value)
        assert message.startswith("TOTP_ENCRYPTION_KEY is set")
        assert "FIELD_ENCRYPTION_KEY" in message
        assert "field_encryption_key" in message
        assert (
            '"Renaming TOTP_ENCRYPTION_KEY to FIELD_ENCRYPTION_KEY"'
            in message
        )

    def test_the_old_rotation_twin_alone_is_refused(self, monkeypatch):
        """A retired key parked under the OLD twin is the data-loss shape.

        With the primary renamed and the twin not, every ciphertext
        still written under the retired key would be unreadable while
        the app reports nothing wrong -- so the twin refuses on its
        own.
        """
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", "retired-value")
        with pytest.raises(
            RuntimeError, match=r"^TOTP_ENCRYPTION_KEY_OLD is set",
        ):
            refuse_retired_field_key_names()

    def test_both_old_names_are_named_in_one_refusal(self, monkeypatch):
        """Two stale lines are reported together, not one per restart."""
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "stale-value")
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", "retired-value")
        with pytest.raises(
            RuntimeError,
            match=r"^TOTP_ENCRYPTION_KEY, TOTP_ENCRYPTION_KEY_OLD is set",
        ):
            refuse_retired_field_key_names()

    def test_an_empty_old_name_is_not_a_configuration(self, monkeypatch):
        """An exported-but-empty old name holds no key and does not refuse.

        ``.env.example`` has shipped ``TOTP_ENCRYPTION_KEY_OLD=`` since
        C-04 (2026-05-03); an operator who renamed the primary line and
        left that empty one behind has carried the rename across.
        Empty is read as unset, the way ``fernet_list_from`` reads an
        empty primary.
        """
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "")
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", "")
        refuse_retired_field_key_names()

    def test_no_old_name_passes(self, monkeypatch):
        """The positive case: neither old name present, no refusal."""
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("TOTP_ENCRYPTION_KEY_OLD", raising=False)
        refuse_retired_field_key_names()

    def test_create_app_is_the_door_in_every_environment(self, monkeypatch):
        """``create_app("testing")`` refuses before it binds an extension.

        The unit cases above grade the predicate; this one grades that
        the factory reaches it -- through ``BaseConfig.__init__``, on
        the TESTING config, so the refusal is every environment's and
        not ``ProdConfig``'s alone.  ``init_ref_cache=False`` is the
        migration host's shape, the first ``create_app`` the entrypoint
        runs -- the refusal must fire there, before ``flask db
        upgrade``, so ``shekel-deploy`` rolls the release back with the
        schema untouched.
        """
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "stale-value")
        with pytest.raises(RuntimeError, match=r"^TOTP_ENCRYPTION_KEY is set"):
            create_app("testing", init_ref_cache=False)


class TestProductionRequiresTheFieldKey:
    """Ruling **R-BI23** (developer, 2026-09-19): production refuses to
    start without a loadable ``FIELD_ENCRYPTION_KEY``.

    Under the warning-only posture this replaces, a container whose
    host ``.env`` still spelt the old name received an EMPTY new key
    (compose interpolates only the names its file states), the app
    started, ``/health`` passed, and every MFA-enrolled user met "MFA
    verification failed" at login.  The three refusals mirror
    ``SECRET_KEY``'s: missing, the docker-secret placeholder, a value
    the cipher could not build from.  ``ProdConfig.__init__`` is the
    home; ``create_app`` instantiating the class is what runs it.
    """

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_missing_key_is_refused(self, monkeypatch):
        """``None`` (the env var absent at import) refuses, naming the key."""
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", None)
        with pytest.raises(
            ValueError, match=r"^FIELD_ENCRYPTION_KEY is required in production",
        ):
            ProdConfig()

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_an_empty_key_is_refused(self, monkeypatch):
        """``""`` (the env var present and empty) refuses the same way.

        This is the shape a stale host ``.env`` produces inside the
        container: ``FIELD_ENCRYPTION_KEY: ${FIELD_ENCRYPTION_KEY:-}``
        interpolates to the empty string.
        """
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", "")
        with pytest.raises(
            ValueError, match=r"^FIELD_ENCRYPTION_KEY is required in production",
        ):
            ProdConfig()

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_the_docker_secret_placeholder_is_refused_with_its_own_hint(
        self, monkeypatch,
    ):
        """``replaced_by_docker_secret`` means the secret file did not load.

        A Fernet check alone would refuse it too, as a malformed key;
        the dedicated arm tells the operator WHICH file is missing.
        """
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY", "replaced_by_docker_secret",
        )
        with pytest.raises(ValueError, match="known placeholder") as exc:
            ProdConfig()
        assert "/run/secrets/field_encryption_key" in str(exc.value)

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_value_fernet_cannot_load_is_refused(self, monkeypatch):
        """A non-Fernet primary refuses at start, not at the first decrypt."""
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY", "not-a-fernet-key",
        )
        with pytest.raises(ValueError, match="Fernet cannot load"):
            ProdConfig()

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_retired_key_fernet_cannot_load_is_refused_too(
        self, monkeypatch,
    ):
        """A bad ``FIELD_ENCRYPTION_KEY_OLD`` entry refuses at start.

        ``MultiFernet`` would raise from it on the first decrypt
        otherwise -- every MFA login a 500 while the process reports
        healthy.
        """
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY",
            Fernet.generate_key().decode(),
        )
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY_OLD",
            Fernet.generate_key().decode() + ",not-a-fernet-key",
        )
        with pytest.raises(ValueError, match="Fernet cannot load"):
            ProdConfig()

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_loadable_key_with_retired_keys_is_accepted(self, monkeypatch):
        """The positive case: a real key and two retired keys construct.

        Without it a validator that refused everything would pass the
        cases above.
        """
        primary = Fernet.generate_key().decode()
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", primary)
        monkeypatch.setattr(
            BaseConfig, "FIELD_ENCRYPTION_KEY_OLD",
            ",".join(Fernet.generate_key().decode() for _ in range(2)),
        )
        config = ProdConfig()
        assert config.FIELD_ENCRYPTION_KEY == primary

    def test_the_field_key_is_validated_after_the_limiter(self, monkeypatch):
        """The existing refusals keep their order; the field key is last.

        An operator misconfiguring several things sees them in the
        order the class already documented (SECRET_KEY, DATABASE_URL,
        the limiter), and only then the field key.
        """
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", _VALID_SECRET_KEY)
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql:///shekel",
        )
        monkeypatch.setattr(ProdConfig, "RATELIMIT_STORAGE_URI", "memory://")
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", None)
        with pytest.raises(ValueError, match="RATELIMIT_STORAGE_URI"):
            ProdConfig()


class TestCreateAppInstantiatesTheConfiguration:
    """``create_app`` runs the configuration class's ``__init__``.

    Flask's ``from_object`` reads a class's uppercase attributes and
    instantiates nothing.  Measured on the tree before this step:
    ``create_app("production")`` returned an app under
    ``SECRET_KEY=short`` and ``RATELIMIT_STORAGE_URI=memory://`` -- every
    refusal ``ProdConfig.__init__`` states was dead at runtime, and only
    the entrypoint's shell twin of the SECRET_KEY check stood, in the
    container alone.  These cases open the door, not the predicate.
    """

    def test_a_short_secret_key_refuses_a_production_app(self, monkeypatch):
        """The oldest production refusal (F-016) fires through the factory."""
        monkeypatch.setattr(BaseConfig, "SECRET_KEY", "short")
        with pytest.raises(ValueError, match="at least 32 characters"):
            create_app("production", init_ref_cache=False)

    @pytest.mark.usefixtures("a_valid_production_config")
    def test_a_missing_field_key_refuses_a_production_app(self, monkeypatch):
        """R-BI23's refusal fires through the factory, before extensions.

        ``init_ref_cache=False`` is the migration host's shape: the
        refusal lands at entrypoint step 3, before any migration runs.
        """
        monkeypatch.setattr(BaseConfig, "FIELD_ENCRYPTION_KEY", None)
        with pytest.raises(
            ValueError, match=r"^FIELD_ENCRYPTION_KEY is required in production",
        ):
            create_app("production", init_ref_cache=False)


class TestTheCipherPair:
    """``encrypt_secret`` / ``decrypt_secret``: the pair every ciphertext
    column is written and read through.

    The autouse ``set_field_encryption_key`` fixture supplies a loadable
    primary for every case here.
    """

    def test_round_trip(self):
        """Encrypting then decrypting returns the original secret."""
        assert decrypt_secret(encrypt_secret(_A_PLAINTEXT)) == _A_PLAINTEXT

    def test_encrypted_differs_from_plaintext(self):
        """Encrypted output is not the same as the plaintext."""
        assert encrypt_secret(_A_PLAINTEXT) != _A_PLAINTEXT.encode("utf-8")

    def test_encrypt_empty_string_secret(self):
        """Encrypting an empty string round-trips correctly.

        Edge case where a secret field is cleared but encrypt is still
        called.  Fernet encrypts empty strings without error.
        """
        assert decrypt_secret(encrypt_secret("")) == ""

    def test_decrypt_corrupted_ciphertext(self):
        """Decrypting corrupted ciphertext raises InvalidToken.

        Corrupted database entries (disk errors, migration bugs) must
        produce a clear error, not silently return garbage that gets used
        as a secret.
        """
        with pytest.raises(InvalidToken):
            decrypt_secret(b"not-valid-fernet-token")

    def test_decrypt_empty_bytes(self):
        """Decrypting empty bytes raises InvalidToken.

        Empty ciphertext could happen if the database column was cleared
        without proper cleanup.
        """
        with pytest.raises(InvalidToken):
            decrypt_secret(b"")


class TestMultiFernetKeyHandling:
    """Tests for the MultiFernet primary/retired-key key list construction.

    Covers audit finding F-030 (C-04): ``get_encryption_key()`` must
    return a ``MultiFernet`` so an operator can rotate
    ``FIELD_ENCRYPTION_KEY`` without losing access to ciphertexts that
    were written under the previous primary.

    The tests pin the public contract:

      - the encryption call uses the primary key, never a retired one;
      - the decryption path tries the primary first and then each
        retired key in declaration order;
      - the comma-separated retired-key list tolerates whitespace and
        empty entries between commas;
      - any malformed retired key fails fast at startup rather than
        silently being skipped.
    """

    def test_get_encryption_key_returns_multifernet(self):
        """get_encryption_key() returns a MultiFernet, not a bare Fernet.

        The MultiFernet wrapper is what makes non-destructive key
        rotation possible.  A regression to a bare Fernet would mean
        that any ciphertext written under a retired key becomes
        unreadable the moment the operator promotes a new primary --
        the exact failure mode that finding F-030 was opened to fix.
        """
        assert isinstance(get_encryption_key(), MultiFernet)

    def test_get_encryption_key_raises_if_unset(self, monkeypatch):
        """get_encryption_key() raises RuntimeError when the primary
        key env var is unset.

        The conftest autouse fixture sets ``FIELD_ENCRYPTION_KEY`` to a
        random key for every test; this test deletes it explicitly so
        we exercise the unset path.  The application must fail loudly
        rather than silently producing a Fernet over the empty string.
        """
        monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="FIELD_ENCRYPTION_KEY"):
            get_encryption_key()

    def test_encrypt_and_decrypt_round_trip_under_primary(self, monkeypatch):
        """Round-trip with only a primary key matches the bare-Fernet
        behavior of the old implementation.

        Regression guard for the steady-state path: most production
        deploys never set ``FIELD_ENCRYPTION_KEY_OLD``, so the
        MultiFernet must behave indistinguishably from a single-key
        Fernet for that population.  The steady state is ESTABLISHED
        here rather than assumed: the autouse fixture sets only the
        primary, and a host environment carrying a retired key would
        otherwise change what this case measures (named by review).
        """
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY_OLD", "")
        assert len(build_fernet_list()) == 1
        assert decrypt_secret(encrypt_secret(_A_PLAINTEXT)) == _A_PLAINTEXT

    def test_decrypt_accepts_ciphertext_from_old_key(self, monkeypatch):
        """A ciphertext encrypted under a retired key still decrypts
        once that key has been moved into ``FIELD_ENCRYPTION_KEY_OLD``.

        This is the central guarantee of the C-04 rotation strategy:
        existing MFA enrollments survive a key rotation without
        re-enrollment.  Without this test, a refactor that drops the
        retired-key handling could silently break login for every
        previously-enrolled user.
        """
        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()

        # Encrypt a known plaintext under the old key BEFORE rotation.
        ciphertext = Fernet(old_key).encrypt(_A_PLAINTEXT.encode("utf-8"))

        # Now rotate: new is primary, old moves to retired.
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", new_key.decode())
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY_OLD", old_key.decode())

        # decrypt_secret routes through the MultiFernet, which must
        # consult the retired key after the primary fails.
        assert decrypt_secret(ciphertext) == _A_PLAINTEXT

    def test_encrypt_uses_primary_not_old(self, monkeypatch):
        """encrypt_secret produces ciphertext under the primary key,
        not any retired key.

        The MultiFernet always encrypts with the first key in its
        ordered list.  This test pins that contract by asserting two
        complementary facts about a freshly produced ciphertext:

          1. ``Fernet(primary_key)`` alone can decrypt it.
          2. ``Fernet(retired_key)`` alone CANNOT decrypt it.

        If a future refactor accidentally swapped the order or used a
        random list element for encryption, the second assertion would
        catch it.  Without this test, encryption could regress to
        producing ciphertexts that the rotation script would have to
        re-wrap on every run.
        """
        primary_key = Fernet.generate_key()
        retired_key = Fernet.generate_key()
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", primary_key.decode())
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY_OLD", retired_key.decode())

        ciphertext = encrypt_secret(_A_PLAINTEXT)

        # Primary alone must decrypt it.
        assert (
            Fernet(primary_key).decrypt(ciphertext).decode("utf-8")
            == _A_PLAINTEXT
        )

        # Retired alone must NOT decrypt it -- proves primary was used.
        with pytest.raises(InvalidToken):
            Fernet(retired_key).decrypt(ciphertext)

    def test_old_key_list_comma_separated(self, monkeypatch):
        """``FIELD_ENCRYPTION_KEY_OLD`` accepts comma-separated multiple
        retired keys.

        A long-running migration may roll the primary forward more
        than once before the rotation script catches up; in that
        window the operator stacks multiple retired keys.  The
        Fernet list must contain primary plus every retired key.

        Three retired keys is enough to catch off-by-one bugs in the
        split logic without dragging the test into the territory of
        proving all-positive-integers.
        """
        primary = Fernet.generate_key()
        retired1 = Fernet.generate_key()
        retired2 = Fernet.generate_key()
        retired3 = Fernet.generate_key()

        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", primary.decode())
        monkeypatch.setenv(
            "FIELD_ENCRYPTION_KEY_OLD",
            ",".join(k.decode() for k in (retired1, retired2, retired3)),
        )

        fernets = build_fernet_list()
        assert len(fernets) == 4, (
            f"Expected primary + 3 retired = 4 Fernets, got {len(fernets)}"
        )

        # Functional check: each retired key must be reachable through
        # decryption on the resulting MultiFernet, not just present in
        # the count.  Encrypt under each retired key in turn and verify
        # the assembled MultiFernet can read them all.
        multi = MultiFernet(fernets)
        for key in (retired1, retired2, retired3):
            ct = Fernet(key).encrypt(b"probe")
            assert multi.decrypt(ct) == b"probe"

    def test_old_key_ignores_blank_entries(self, monkeypatch):
        """Blank entries in ``FIELD_ENCRYPTION_KEY_OLD`` are skipped.

        Operators editing ``.env`` by hand can easily leave a stray
        comma after pruning a key (``key1,`` -> empty trailing entry)
        or insert a blank between commas (``key1, ,key2``).  Treating
        these as ignored rather than as invalid keys avoids a class of
        avoidable startup failures.
        """
        primary = Fernet.generate_key()
        retired1 = Fernet.generate_key()
        retired2 = Fernet.generate_key()

        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", primary.decode())
        # Mix every blank-entry pattern: empty between commas, leading
        # space, trailing comma+space.
        monkeypatch.setenv(
            "FIELD_ENCRYPTION_KEY_OLD",
            f"{retired1.decode()}, ,{retired2.decode()}, ",
        )

        fernets = build_fernet_list()
        assert len(fernets) == 3, (
            "Blank entries must be skipped; expected primary + 2 retired "
            f"= 3 Fernets, got {len(fernets)}"
        )

    def test_old_key_empty_string_is_steady_state(self, monkeypatch):
        """An empty ``FIELD_ENCRYPTION_KEY_OLD`` produces a single-key
        Fernet list.

        The steady-state production posture has the env var either
        unset or set to the empty string.  Both must yield the
        primary-only list -- an extra empty Fernet would be a runtime
        error.
        """
        primary = Fernet.generate_key()
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", primary.decode())
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY_OLD", "")

        assert len(build_fernet_list()) == 1

    def test_invalid_old_key_raises(self, monkeypatch):
        """An invalid Fernet key in ``FIELD_ENCRYPTION_KEY_OLD`` raises
        ``ValueError`` at startup.

        Failing fast is the right behavior here: a silently-skipped
        bad key would mean ciphertexts written under a missing key
        become unreadable without any startup signal.

        ``Fernet`` raises ``ValueError`` for wrong-length input and
        ``binascii.Error`` (a ``ValueError`` subclass) for non-base64
        input, so a single ``ValueError`` catch covers both forms.
        """
        primary = Fernet.generate_key()
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", primary.decode())
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY_OLD", "not-a-valid-fernet-key")

        with pytest.raises(ValueError):
            build_fernet_list()
