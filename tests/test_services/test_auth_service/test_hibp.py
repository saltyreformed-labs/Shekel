"""Auth service tests: the breached-password check."""

import hashlib

import pytest
import requests

from app.services import auth_service
from app.exceptions import ValidationError


# ---------------------------------------------------------------------------
# HIBP breached-password check (audit finding F-086 / commit C-11)
# ---------------------------------------------------------------------------


def _hibp_response_for(plain_password, count=1, extra_lines=()):
    """Build a fake HIBP API response body containing the given password.

    HIBP returns one suffix-and-count pair per line.  The full SHA-1 of
    the password is split into prefix/suffix; this helper constructs a
    response in the format the API would return for a positive match,
    plus optional decoy lines.

    Args:
        plain_password: The plaintext to embed (the suffix is computed
            from its SHA-1).
        count: The breach count to associate with the matched suffix.
            Tests use 1 or higher for a real breach record; a zero count
            is HIBP padding noise and the service ignores it (a matching
            suffix with count 0 does NOT raise -- see
            test_zero_count_match_not_rejected).
        extra_lines: Iterable of additional ``(suffix, count)`` tuples
            mixed into the response to simulate real HIBP output.

    Returns:
        str: The complete response body in HIBP format.
    """
    sha1 = hashlib.sha1(plain_password.encode("utf-8")).hexdigest().upper()
    suffix = sha1[5:]
    lines = [f"{suffix}:{count}"]
    for extra_suffix, extra_count in extra_lines:
        lines.append(f"{extra_suffix}:{extra_count}")
    return "\r\n".join(lines)


class _FakeHibpResponse:
    """Minimal stand-in for ``requests.Response`` used in HIBP tests.

    Matches the subset of the real interface that
    ``_check_pwned_password`` uses: ``text`` and ``raise_for_status``.
    Constructing real ``requests.Response`` instances would require
    extra plumbing; this stub keeps the test setup obvious.
    """

    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        """Mimic requests.Response.raise_for_status for the 4xx path."""
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class TestHibpCheck:
    """Tests for the HIBP k-anonymity breach check."""

    def test_check_disabled_skips_request(self, monkeypatch):
        """When HIBP_CHECK_ENABLED=false, no HTTP call is issued.

        The conftest autouse fixture sets this to false so the rest
        of the suite never hits HIBP.  This test confirms the toggle
        actually short-circuits.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "false")
        called = {"count": 0}

        def _spy(*_args, **_kwargs):
            called["count"] += 1
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _spy)
        # No exception even on a known-breached password because we
        # never contact HIBP.
        auth_service._check_pwned_password("password")
        assert called["count"] == 0

    def test_breached_password_raises_validation_error(self, monkeypatch):
        """A password whose hash matches a HIBP record is rejected.

        Mocks ``requests.get`` to return a body containing the
        password's SHA-1 suffix.  The function must raise
        ``ValidationError`` with the user-facing message that does
        NOT name HIBP or quote the breach count (avoid leaking the
        upstream identity to a curious user).
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "definitely-breached-password"
        body = _hibp_response_for(password, count=42)
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(body),
        )
        with pytest.raises(ValidationError, match="known data breach"):
            auth_service._check_pwned_password(password)

    def test_zero_count_match_not_rejected(self, monkeypatch):
        """A matching suffix with count 0 is HIBP padding, not a breach.

        deep-quality-hunt #43: the loop now honours the Add-Padding
        protocol the request asks for (`Add-Padding: true`).  Padded
        lines carry a count of 0; a count-0 line whose suffix happens to
        equal the queried suffix must NOT be treated as a breach.  Before
        the fix the loop ignored the count entirely and raised on any
        suffix match, contradicting its own docstring.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "padding-collision-not-a-real-breach"
        body = _hibp_response_for(password, count=0)
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(body),
        )
        # Returns None and does NOT raise -- a count-0 match is padding.
        result = auth_service._check_pwned_password(password)  # pylint: disable=assignment-from-none
        assert result is None

    def test_non_integer_count_match_not_rejected(self, monkeypatch):
        """A matching suffix with a malformed (non-integer) count is skipped.

        deep-quality-hunt #43: the inline comment documents that a line
        with a non-integer count is a protocol violation to skip, not a
        breach.  The loop now parses the count and skips a line whose
        count is not an integer even when the suffix matches.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "malformed-count-line"
        # The helper f-strings the count, so a non-numeric value yields a
        # "<suffix>:notanumber" line with a matching suffix.
        body = _hibp_response_for(password, count="notanumber")
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(body),
        )
        result = auth_service._check_pwned_password(password)  # pylint: disable=assignment-from-none
        assert result is None

    def test_clean_password_passes(self, monkeypatch):
        """A password whose suffix is absent from the response is accepted.

        Sends a body containing only decoy lines so the search loop
        completes without finding the real suffix.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        decoys = "\r\n".join(f"{'A' * 35}:{i}" for i in range(1, 6))
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(decoys),
        )
        # Returns None on accept; absence of exception is the primary
        # assertion.  Pylint flags ``assignment-from-none`` because the
        # function's documented return-type is None on the happy path,
        # but the test deliberately captures the value to make the
        # contract self-documenting in the test source.
        result = auth_service._check_pwned_password(  # pylint: disable=assignment-from-none
            "this-passphrase-is-clean",
        )
        assert result is None

    def test_network_error_fails_open(self, monkeypatch):
        """A requests.RequestException is logged and treated as accept.

        Fail-open is the documented posture: a transient HIBP outage
        must not stop a legitimate user from registering.  The
        warning log is the operator-side signal that breach-check is
        currently degraded.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")

        def _raise_timeout(*_args, **_kwargs):
            raise requests.Timeout("simulated timeout")

        monkeypatch.setattr(requests, "get", _raise_timeout)
        # Returns None and does NOT raise.
        result = auth_service._check_pwned_password("any-password")  # pylint: disable=assignment-from-none
        assert result is None

    def test_5xx_response_fails_open(self, monkeypatch):
        """A 5xx response from HIBP is logged and treated as accept.

        ``requests.Response.raise_for_status`` raises ``HTTPError``,
        which is a subclass of ``RequestException``, so the same
        fail-open path catches it.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse("", status_code=503),
        )
        result = auth_service._check_pwned_password("any-password")  # pylint: disable=assignment-from-none
        assert result is None

    def test_uses_k_anonymity_prefix(self, monkeypatch):
        """The HTTP call sends only the SHA-1 prefix, never the full hash.

        Verifies the k-anonymity property: the URL contains exactly 5
        hex characters of the hash.  Sending more would defeat the
        privacy guarantee that the password's identity stays local.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "audit-this-call"
        captured = {}

        def _capture(url, *args, **kwargs):
            captured["url"] = url
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _capture)
        auth_service._check_pwned_password(password)

        # URL ends with /range/<5-char prefix>.  The full SHA-1 has 40
        # characters and the suffix is 35; only the 5-char prefix
        # should appear in the URL.
        full_sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        assert captured["url"].endswith(f"/range/{full_sha1[:5]}")
        assert full_sha1[5:] not in captured["url"]

    def test_request_uses_padding_header(self, monkeypatch):
        """The HTTP request sets Add-Padding: true.

        Padding asks HIBP to randomise the response size so a passive
        observer cannot use response length to narrow the candidate
        set.  Required for the k-anonymity property to hold against
        a network-level adversary.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        captured = {}

        def _capture(_url, *args, **kwargs):
            captured["headers"] = kwargs.get("headers", {})
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _capture)
        auth_service._check_pwned_password("any-password")

        assert captured["headers"].get("Add-Padding") == "true"

    def test_request_honours_timeout_env(self, monkeypatch):
        """The HTTP call passes the configured ``HIBP_TIMEOUT_SECONDS``.

        The default is 3 seconds; the test uses a custom value to
        prove the env var is read at call time.  A failure here would
        mean an operator could not tune the timeout without restarting
        Python.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        monkeypatch.setenv("HIBP_TIMEOUT_SECONDS", "1.5")
        captured = {}

        def _capture(*_args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _capture)
        auth_service._check_pwned_password("any-password")

        assert captured["timeout"] == pytest.approx(1.5)

    def test_malformed_response_line_skipped(self, monkeypatch):
        """A response with a malformed line (no colon) does not crash.

        A protocol violation by HIBP should be safer to ignore than
        to surface as a 500.  The function must keep scanning for the
        real suffix on the well-formed lines.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "test-password-malformed"
        sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        body = "this-line-has-no-colon\r\n" + f"{sha1[5:]}:1"
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(body),
        )
        with pytest.raises(ValidationError, match="known data breach"):
            auth_service._check_pwned_password(password)

    def test_hash_password_invokes_hibp_check(self, monkeypatch):
        """``hash_password`` runs the breach check before bcrypt.

        Verifies the integration point that closes F-086 across all
        password-set paths (register_user, change_password, companion
        creation/edit) without each having to call HIBP individually.
        Asserts that a known-breached input raises before bcrypt
        produces a hash.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        password = "another-breached-password"
        body = _hibp_response_for(password)
        monkeypatch.setattr(
            requests, "get",
            lambda *args, **kwargs: _FakeHibpResponse(body),
        )
        with pytest.raises(ValidationError, match="known data breach"):
            auth_service.hash_password(password)

    def test_hash_password_skips_hibp_when_too_long(self, monkeypatch):
        """The 72-byte length check fires before HIBP.

        Password-too-long is a deterministic local check; the upstream
        HIBP query is the slow remote step.  Ordering matters because
        an attacker submitting a 73-byte password would otherwise
        burn an HIBP request per attempt.
        """
        monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")
        called = {"count": 0}

        def _spy(*_args, **_kwargs):
            called["count"] += 1
            return _FakeHibpResponse("")

        monkeypatch.setattr(requests, "get", _spy)
        with pytest.raises(ValidationError, match="too long"):
            auth_service.hash_password("a" * 73)
        assert called["count"] == 0
