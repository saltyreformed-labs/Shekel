"""
Shekel Budget App -- Auth Schema Validation Tests (commit C-26)

Tests the Marshmallow schemas added by commit C-26 of the
2026-04-15 security remediation plan.  Each schema is exercised
through ``schema.load`` for the happy path and every documented
rejection branch:

  * ``LoginSchema``           -- F-041 (route boundary input validation)
                              + F-163 (DoS on bcrypt via oversized password).
  * ``RegisterSchema``        -- F-041 + email/password rules.
  * ``ChangePasswordSchema``  -- F-041 + new-password rules,
                              accept legacy current_password lengths.
  * ``MfaVerifySchema``       -- F-163 (backup_code length cap before
                              bcrypt verification).
  * ``MfaConfirmSchema``      -- F-163 (totp_code length cap on
                              enrolment confirmation).
  * ``MfaDisableSchema``      -- F-041 + F-163 (current_password +
                              totp_code shape).
  * ``ReauthSchema``          -- F-041 + F-163 on the step-up path.

The plan calls for 22 tests covering the schema layer; this file
delivers more (one happy-path plus every documented rejection branch
for each schema) so a future change that loosens any validator is
caught directly.
"""

from datetime import date

import pytest
from marshmallow import ValidationError

from app.config import BaseConfig
from app.services.pay_rhythm import FixedDays
from app.schemas.validation import (
    ChangePasswordSchema,
    LoginSchema,
    MfaConfirmSchema,
    MfaDisableSchema,
    MfaVerifySchema,
    ReauthSchema,
    RegisterSchema,
)


# ── LoginSchema ──────────────────────────────────────────────────────


class TestLoginSchema:
    """Tests for LoginSchema (commit C-26 / F-041 + F-163)."""

    def test_valid_data_drops_a_stale_remember_field(self):
        """Happy path; a ``remember=on`` from a stale cached form is dropped.

        "Remember me" is gone (plan#2173, ruling plan#2406).  A browser
        holding the old login page still posts ``remember=on``, and the
        sign-in must neither fail on it nor carry it anywhere: the loaded
        data is exactly the two credentials.
        """
        data = LoginSchema().load({
            "email": "user@example.com",
            "password": "anylengthpassword",
            "remember": "on",
        })
        assert data == {
            "email": "user@example.com",
            "password": "anylengthpassword",
        }

    def test_email_lowercased_and_stripped(self):
        """Email is lowercased and whitespace-stripped at pre_load."""
        data = LoginSchema().load({
            "email": "  USER@Example.COM  ",
            "password": "testpass",
        })
        assert data["email"] == "user@example.com"

    def test_missing_email_rejected(self):
        """Required email missing raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            LoginSchema().load({"password": "testpass"})
        assert "email" in exc.value.messages

    def test_missing_password_rejected(self):
        """Required password missing raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            LoginSchema().load({"email": "user@example.com"})
        assert "password" in exc.value.messages

    def test_oversized_password_rejected_dos_protection(self):
        """F-163: a megabyte-sized password is rejected before bcrypt.

        Length capped at 72 characters (bcrypt's effective input cap).
        Any longer payload would be silently truncated by bcrypt; the
        schema rejects it so an attacker cannot make the server pay
        bcrypt's cost on a hash they can never reproduce.
        """
        with pytest.raises(ValidationError) as exc:
            LoginSchema().load({
                "email": "user@example.com",
                "password": "x" * 1024,
            })
        assert "password" in exc.value.messages

    def test_oversized_email_rejected(self):
        """Email longer than 255 characters is rejected."""
        long_local = "a" * 250
        with pytest.raises(ValidationError) as exc:
            LoginSchema().load({
                "email": f"{long_local}@example.com",
                "password": "testpass",
            })
        assert "email" in exc.value.messages

    def test_invalid_email_format_rejected(self):
        """Email without @ or TLD is rejected by the regex validator."""
        with pytest.raises(ValidationError) as exc:
            LoginSchema().load({
                "email": "not-an-email",
                "password": "testpass",
            })
        assert "email" in exc.value.messages
        assert "Invalid email format." in exc.value.messages["email"]

    def test_short_password_accepted_for_login(self):
        """Login accepts any historical password length down to 1 char.

        The 12-character minimum applies only when minting a *new*
        password (register, change-password).  At verification time we
        accept whatever the user typed and let bcrypt decide; rejecting
        short passwords would lock out users whose accounts pre-date
        the 12-character rule.
        """
        data = LoginSchema().load({
            "email": "user@example.com",
            "password": "short",
        })
        assert data["password"] == "short"


# ── RegisterSchema ───────────────────────────────────────────────────


class TestRegisterSchema:
    """Tests for RegisterSchema (commit C-26 / F-041)."""

    def _valid_payload(self):
        """A complete registration payload, including the pay-schedule half.

        ``last_payday`` became required at plan step X-ad-a: registration
        stopped inventing a pay period, so the owner has to state a real
        payday.  The other two default at the schema tier, and the assertions
        below pin that.
        """
        return {
            "email": "newuser@example.com",
            "display_name": "New User",
            "password": "longenoughpass",
            "confirm_password": "longenoughpass",
            "last_payday": "2026-08-05",
        }

    @pytest.mark.parametrize("stated", ["9999-01-01", "1999-12-31"])
    def test_a_payday_outside_the_APPS_CALENDAR_is_refused(self, stated):
        """Plan step **pay_calendar:C14-e-3**: the stated payday carries a range.

        It was a bare ``fields.Date`` -- the only persisted date in this
        application without one, ``history_opens_on`` beside it included.  That
        was survivable while a payday was compared and stored; it stopped being
        survivable when a payday is DISPLACED, because the shift reads
        :func:`~app.utils.business_days.federal_holidays`, which computes the
        FOLLOWING year's New Year spillover.  A stated payday in year 9999
        therefore left ``datetime``'s own domain and raised ``ValueError``, not
        ``ValidationError``, out of the service tier -- a 500 on a public form
        where the deleted arithmetic had returned a clean refusal.  Found by an
        adversarial review of that step.

        Both ends, because a bound asserted at one end is a bound half graded.
        """
        payload = dict(self._valid_payload(), last_payday=stated)

        assert "last_payday" in RegisterSchema().validate(payload)

    def test_an_untouched_history_date_loads_as_None(self):
        """A browser posts ``""`` for the optional date, and it means NULL.

        Plan step **balance:X-bh-2**.  An HTML form submits every control it
        renders, so the field arrives empty from every sign-up that skipped
        the question -- and ``fields.Date`` would refuse ``""`` as "Not a
        valid date." without the schema's own pre-load.  ``None`` is the
        answer, not the absence of one.
        """
        payload = dict(self._valid_payload(), history_opens_on="")

        assert RegisterSchema().load(payload)["history_opens_on"] is None

    def test_an_omitted_history_date_loads_as_None(self):
        """The API-shaped payload, which has no control to leave blank."""
        assert RegisterSchema().load(
            self._valid_payload(),
        )["history_opens_on"] is None

    def test_a_stated_history_date_loads_as_a_date(self):
        """THE CONTROL: the field is read, not merely tolerated."""
        payload = dict(self._valid_payload(), history_opens_on="2024-06-03")

        assert RegisterSchema().load(payload)["history_opens_on"] == date(
            2024, 6, 3,
        )

    def test_a_history_date_outside_the_apps_calendar_is_refused(self):
        """The schema tier refuses what ``ck_pay_schedule_history_opens_range``
        would, so the CHECK is never the thing a user meets.
        """
        payload = dict(self._valid_payload(), history_opens_on="9999-01-01")

        with pytest.raises(ValidationError) as caught:
            RegisterSchema().load(payload)

        assert "history_opens_on" in caught.value.messages

    def test_an_empty_display_name_still_gets_its_OWN_message(self):
        """The pre-load clears nullable empties and drops nothing else.

        Plan step balance:X-bh-2 routed this form through a normalizer to make
        the empty date null.  The full ``_normalize_empty_inputs`` would also
        DROP an empty ``display_name``, turning this schema's own "Display
        name is required." into marshmallow's "Missing data for required
        field." -- so the form takes the nullable half only, and this is the
        case that says which.
        """
        payload = dict(self._valid_payload(), display_name="")

        with pytest.raises(ValidationError) as caught:
            RegisterSchema().load(payload)

        assert caught.value.messages["display_name"] == [
            "Display name is required.",
        ]

    def test_valid_data(self):
        """Happy path: all fields satisfy their validators."""
        data = RegisterSchema().load(self._valid_payload())
        assert data["email"] == "newuser@example.com"
        assert data["display_name"] == "New User"
        assert data["password"] == "longenoughpass"
        assert data["last_payday"] == date(2026, 8, 5)

    def test_the_pay_schedule_fields_default_to_the_app_premise(self):
        """Cadence and horizon are optional and default to the app constants.

        A payload with no cadence still loads, at the biweekly premise the
        whole app is organised around, and at the ~2-year horizon.  Pinned
        against ``BaseConfig`` rather than against ``14`` and ``52`` so this
        cannot become a third statement of the same numbers.
        """
        data = RegisterSchema().load(self._valid_payload())
        # The rhythm is the loaded VALUE since plan step pay_calendar:C17-d-3
        # (ruling R-PC84); the kind defaults with the day count.
        assert data["rhythm"].cadence == FixedDays(
            BaseConfig.DEFAULT_PAY_CADENCE_DAYS,
        )
        assert data["num_periods"] == BaseConfig.DEFAULT_PAY_PERIOD_HORIZON

    def test_missing_last_payday_is_rejected_with_its_own_message(self):
        """The one pay-schedule field with no default says what it wants.

        "Missing data for required field." would be true and useless on a form
        that has just grown three inputs, so the field carries its own
        required-message.
        """
        payload = self._valid_payload()
        del payload["last_payday"]
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert exc.value.messages["last_payday"] == [
            "Enter the day you were last paid."
        ]

    @pytest.mark.parametrize(
        "field,value", [("cadence_days", 0), ("cadence_days", 366),
                        ("num_periods", 0), ("num_periods", 261)],
    )
    def test_out_of_range_pay_schedule_values_are_rejected(self, field, value):
        """The shared bounds reach this schema, not just the settings forms.

        ``RegisterSchema`` takes the SAME field builders
        ``PayPeriodGenerateSchema`` does, which is what stops the two doors
        onto one calendar from bounding their inputs differently.  Both ends of
        both ranges are exercised, one past each.
        """
        payload = self._valid_payload()
        payload[field] = value
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert field in exc.value.messages

    def test_a_credential_error_still_wins_over_a_missing_payday(self):
        """Field order keeps the message a user is most likely to need first.

        ``_first_validation_message`` flashes ONE message, taken in
        field-declaration order, so declaring ``last_payday`` after the
        credential fields is what keeps "Invalid email format." winning when
        both are wrong.  Asserted rather than left to the declaration order
        being noticed by the next person to reorder the class.
        """
        payload = self._valid_payload()
        payload["email"] = "notanemail"
        del payload["last_payday"]
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert list(exc.value.messages)[0] == "email"

    def test_short_password_rejected(self):
        """Password under 12 chars is rejected with the canonical message."""
        payload = self._valid_payload()
        payload["password"] = "12345678901"  # 11 chars
        payload["confirm_password"] = "12345678901"
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert "password" in exc.value.messages
        assert (
            "Password must be at least 12 characters."
            in exc.value.messages["password"]
        )

    def test_oversized_password_chars_rejected(self):
        """Password longer than 72 chars is rejected at the field level."""
        payload = self._valid_payload()
        payload["password"] = "x" * 73
        payload["confirm_password"] = "x" * 73
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert "password" in exc.value.messages

    def test_oversized_password_bytes_rejected(self):
        """Password whose UTF-8 encoding exceeds 72 bytes is rejected.

        18 four-byte characters fit in 72 chars but exceed 72 bytes
        (18 * 4 = 72, OK; 19 * 4 = 76, rejected).  The @validates_schema
        bytes check is the backstop for multi-byte characters that the
        char-length cap would otherwise admit.
        """
        # 19 four-byte emoji = 76 bytes, 19 chars (passes Length(max=72)).
        payload = self._valid_payload()
        big = "\U0001f600" * 19
        payload["password"] = big
        payload["confirm_password"] = big
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert "password" in exc.value.messages

    def test_confirm_mismatch_rejected(self):
        """confirm_password != password raises ValidationError."""
        payload = self._valid_payload()
        payload["confirm_password"] = "different12345"
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert "confirm_password" in exc.value.messages
        assert (
            "Password and confirmation do not match."
            in exc.value.messages["confirm_password"]
        )

    def test_invalid_email_rejected(self):
        """Email without @ or TLD is rejected."""
        payload = self._valid_payload()
        payload["email"] = "notanemail"
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert "email" in exc.value.messages
        assert "Invalid email format." in exc.value.messages["email"]

    def test_empty_display_name_rejected(self):
        """Empty display_name produces 'Display name is required.'"""
        payload = self._valid_payload()
        payload["display_name"] = ""
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert "display_name" in exc.value.messages
        assert (
            "Display name is required."
            in exc.value.messages["display_name"]
        )

    def test_missing_confirm_password_rejected(self):
        """confirm_password is required at the field level."""
        payload = self._valid_payload()
        del payload["confirm_password"]
        with pytest.raises(ValidationError) as exc:
            RegisterSchema().load(payload)
        assert "confirm_password" in exc.value.messages


# ── ChangePasswordSchema ─────────────────────────────────────────────


class TestChangePasswordSchema:
    """Tests for ChangePasswordSchema (commit C-26 / F-041)."""

    def _valid_payload(self):
        return {
            "current_password": "anylengthcurrent",
            "new_password": "anewpassword12",
            "confirm_password": "anewpassword12",
        }

    def test_valid_data(self):
        """Happy path: all fields satisfy their validators."""
        data = ChangePasswordSchema().load(self._valid_payload())
        assert data["current_password"] == "anylengthcurrent"
        assert data["new_password"] == "anewpassword12"

    def test_short_current_password_accepted(self):
        """current_password accepts legacy short passwords (no min)."""
        payload = self._valid_payload()
        payload["current_password"] = "short"  # legacy 5-char
        data = ChangePasswordSchema().load(payload)
        assert data["current_password"] == "short"

    def test_short_new_password_rejected(self):
        """new_password under 12 chars is rejected with the canonical message."""
        payload = self._valid_payload()
        payload["new_password"] = "short"
        payload["confirm_password"] = "short"
        with pytest.raises(ValidationError) as exc:
            ChangePasswordSchema().load(payload)
        assert "new_password" in exc.value.messages
        assert (
            "New password must be at least 12 characters."
            in exc.value.messages["new_password"]
        )

    def test_oversized_current_password_rejected_dos_protection(self):
        """F-163: oversized current_password is rejected before bcrypt."""
        payload = self._valid_payload()
        payload["current_password"] = "x" * 1024
        with pytest.raises(ValidationError) as exc:
            ChangePasswordSchema().load(payload)
        assert "current_password" in exc.value.messages

    def test_oversized_new_password_bytes_rejected(self):
        """new_password > 72 bytes UTF-8 rejected by @validates_schema."""
        payload = self._valid_payload()
        big = "\U0001f600" * 19  # 76 bytes
        payload["new_password"] = big
        payload["confirm_password"] = big
        with pytest.raises(ValidationError) as exc:
            ChangePasswordSchema().load(payload)
        assert "new_password" in exc.value.messages

    def test_confirm_mismatch_rejected(self):
        """confirm_password != new_password raises ValidationError."""
        payload = self._valid_payload()
        payload["confirm_password"] = "differentpass1"
        with pytest.raises(ValidationError) as exc:
            ChangePasswordSchema().load(payload)
        assert "confirm_password" in exc.value.messages
        assert (
            "New password and confirmation do not match."
            in exc.value.messages["confirm_password"]
        )

    def test_missing_current_password_rejected(self):
        """current_password is required."""
        payload = self._valid_payload()
        del payload["current_password"]
        with pytest.raises(ValidationError) as exc:
            ChangePasswordSchema().load(payload)
        assert "current_password" in exc.value.messages


# ── MfaVerifySchema ──────────────────────────────────────────────────


class TestMfaVerifySchema:
    """Tests for MfaVerifySchema (commit C-26 / F-163)."""

    def test_valid_totp_code(self):
        """Happy path: a 6-digit TOTP code passes."""
        data = MfaVerifySchema().load({"totp_code": "123456"})
        assert data["totp_code"] == "123456"
        assert data["backup_code"] == ""

    def test_valid_backup_code(self):
        """Happy path: a 28-hex backup code passes."""
        data = MfaVerifySchema().load({"backup_code": "a" * 28})
        assert data["backup_code"] == "a" * 28

    def test_neither_field_present_accepts_empty(self):
        """Both fields default to empty when neither is submitted."""
        data = MfaVerifySchema().load({})
        assert data["totp_code"] == ""
        assert data["backup_code"] == ""

    def test_oversized_backup_code_rejected_dos_protection(self):
        """F-163: a megabyte-sized backup_code is rejected before bcrypt.

        Without this cap, an attacker could submit a huge string and
        force the server to bcrypt-compare it against every stored
        backup-code hash -- amplifying request cost by an order of
        magnitude per hash slot.
        """
        with pytest.raises(ValidationError) as exc:
            MfaVerifySchema().load({"backup_code": "x" * 1024})
        assert "backup_code" in exc.value.messages

    def test_oversized_totp_code_rejected(self):
        """totp_code > 6 chars is rejected by Length(max=6)."""
        with pytest.raises(ValidationError) as exc:
            MfaVerifySchema().load({"totp_code": "1234567"})
        assert "totp_code" in exc.value.messages

    def test_whitespace_stripped_from_codes(self):
        """Leading/trailing whitespace on codes is stripped at pre_load.

        Mirrors the route's prior ``.strip()`` calls so a paste with
        trailing whitespace still passes the post-strip length cap.
        """
        data = MfaVerifySchema().load({
            "totp_code": "  123456  ",
            "backup_code": "  abc123  ",
        })
        assert data["totp_code"] == "123456"
        assert data["backup_code"] == "abc123"

    def test_unknown_fields_excluded(self):
        """Unknown fields are silently dropped (BaseSchema.Meta.unknown)."""
        data = MfaVerifySchema().load({
            "totp_code": "123456",
            "csrf_token": "abc",  # always present in real submissions
        })
        assert "csrf_token" not in data


# ── MfaConfirmSchema ─────────────────────────────────────────────────


class TestMfaConfirmSchema:
    """Tests for MfaConfirmSchema (commit C-26 / F-163)."""

    def test_valid_totp_code(self):
        """Happy path: a 6-digit TOTP code passes."""
        data = MfaConfirmSchema().load({"totp_code": "987654"})
        assert data["totp_code"] == "987654"

    def test_missing_totp_code_defaults_empty(self):
        """totp_code defaults to empty when absent (route reports 'Invalid code')."""
        data = MfaConfirmSchema().load({})
        assert data["totp_code"] == ""

    def test_oversized_totp_code_rejected(self):
        """totp_code > 6 chars is rejected."""
        with pytest.raises(ValidationError) as exc:
            MfaConfirmSchema().load({"totp_code": "1" * 100})
        assert "totp_code" in exc.value.messages

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace is stripped before length validation."""
        data = MfaConfirmSchema().load({"totp_code": " 654321 "})
        assert data["totp_code"] == "654321"


# ── MfaDisableSchema ─────────────────────────────────────────────────


class TestMfaDisableSchema:
    """Tests for MfaDisableSchema (commit C-26 / F-041 + F-163)."""

    def test_valid_data(self):
        """Happy path: both fields populated."""
        data = MfaDisableSchema().load({
            "current_password": "anyhistoricalpw",
            "totp_code": "123456",
        })
        assert data["current_password"] == "anyhistoricalpw"
        assert data["totp_code"] == "123456"

    def test_missing_current_password_rejected(self):
        """current_password is required."""
        with pytest.raises(ValidationError) as exc:
            MfaDisableSchema().load({"totp_code": "123456"})
        assert "current_password" in exc.value.messages

    def test_oversized_current_password_rejected_dos_protection(self):
        """F-163: a DoS-sized current_password is rejected before bcrypt."""
        with pytest.raises(ValidationError) as exc:
            MfaDisableSchema().load({
                "current_password": "x" * 1024,
                "totp_code": "123456",
            })
        assert "current_password" in exc.value.messages

    def test_oversized_totp_code_rejected(self):
        """totp_code > 6 chars is rejected."""
        with pytest.raises(ValidationError) as exc:
            MfaDisableSchema().load({
                "current_password": "anyhistoricalpw",
                "totp_code": "1" * 100,
            })
        assert "totp_code" in exc.value.messages

    def test_legacy_short_current_password_accepted(self):
        """current_password has min=1 so a legacy short password is accepted."""
        data = MfaDisableSchema().load({
            "current_password": "short",
            "totp_code": "123456",
        })
        assert data["current_password"] == "short"


# ── ReauthSchema ─────────────────────────────────────────────────────


class TestReauthSchema:
    """Tests for ReauthSchema (commit C-26 / F-041 + F-163)."""

    def test_valid_password_only(self):
        """Happy path: password without totp_code (no MFA on user)."""
        data = ReauthSchema().load({"password": "anyhistoricalpw"})
        assert data["password"] == "anyhistoricalpw"
        assert data["totp_code"] == ""

    def test_valid_password_with_totp(self):
        """Happy path: password and totp_code (MFA on user)."""
        data = ReauthSchema().load({
            "password": "anyhistoricalpw",
            "totp_code": "123456",
        })
        assert data["totp_code"] == "123456"

    def test_missing_password_rejected(self):
        """password is required."""
        with pytest.raises(ValidationError) as exc:
            ReauthSchema().load({"totp_code": "123456"})
        assert "password" in exc.value.messages

    def test_oversized_password_rejected_dos_protection(self):
        """F-163: oversized password is rejected before bcrypt."""
        with pytest.raises(ValidationError) as exc:
            ReauthSchema().load({"password": "x" * 1024})
        assert "password" in exc.value.messages

    def test_oversized_totp_code_rejected(self):
        """totp_code > 6 chars is rejected."""
        with pytest.raises(ValidationError) as exc:
            ReauthSchema().load({
                "password": "anyhistoricalpw",
                "totp_code": "1" * 100,
            })
        assert "totp_code" in exc.value.messages
