"""Proxy trust tests (C-33, F-015): ``gunicorn.conf.py`` refuses an unset or empty
``FORWARDED_ALLOW_IPS`` and reflects a set value verbatim.
"""

from __future__ import annotations

import os
import runpy

import pytest

from ._helpers import GUNICORN_CONF, BUNDLED_BACKEND_CIDR, SHARED_FRONTEND_CIDR


class TestGunicornForwardedAllowIps:
    """Verify ``gunicorn.conf.py`` honours the F-015 fix:

    * No fallback default; a missing or empty ``FORWARDED_ALLOW_IPS``
      raises at config-load time.
    * A set value is reflected verbatim into ``forwarded_allow_ips``.
    """

    def _run_gunicorn_conf(self, env_value: str | None) -> dict:
        """Execute ``gunicorn.conf.py`` as a fresh module with a
        controlled ``FORWARDED_ALLOW_IPS`` environment variable.

        Uses ``runpy.run_path`` rather than ``import gunicorn.conf``
        because the file is a top-level config script (not a package
        module) and Python's import system caches successful imports;
        re-running the file with new env values requires fresh
        execution.

        Returns the module's globals dict so the caller can assert on
        ``forwarded_allow_ips``.
        """
        original = os.environ.get("FORWARDED_ALLOW_IPS")
        try:
            if env_value is None:
                os.environ.pop("FORWARDED_ALLOW_IPS", None)
            else:
                os.environ["FORWARDED_ALLOW_IPS"] = env_value
            return runpy.run_path(str(GUNICORN_CONF), run_name="__main__")
        finally:
            if original is None:
                os.environ.pop("FORWARDED_ALLOW_IPS", None)
            else:
                os.environ["FORWARDED_ALLOW_IPS"] = original

    def test_unset_env_raises_runtimeerror(self) -> None:
        """A missing ``FORWARDED_ALLOW_IPS`` raises ``RuntimeError``
        at config-load time.  This is the fail-closed posture audit
        finding F-015 calls for: a misconfigured production deploy
        cannot start under the historic loose RFC 1918 trust by
        accident.
        """
        with pytest.raises(RuntimeError) as excinfo:
            self._run_gunicorn_conf(env_value=None)
        # The error must name the variable so an operator reading
        # the docker logs can fix it without spelunking through
        # gunicorn.conf.py.
        assert "FORWARDED_ALLOW_IPS" in str(excinfo.value)
        # And must reference the audit commit so the fix path is
        # traceable from the message alone.
        assert "C-33" in str(excinfo.value)

    def test_empty_string_env_raises_runtimeerror(self) -> None:
        """``FORWARDED_ALLOW_IPS=`` (set but empty) is a misconfig the
        loader must reject -- otherwise an operator who clears the
        value to "disable" trust would actually leave Gunicorn
        accepting forwarded headers from no-one (silently breaking
        the proxy chain rather than failing loudly).
        """
        with pytest.raises(RuntimeError) as excinfo:
            self._run_gunicorn_conf(env_value="")
        assert "FORWARDED_ALLOW_IPS" in str(excinfo.value)

    def test_whitespace_only_env_raises_runtimeerror(self) -> None:
        """``FORWARDED_ALLOW_IPS="   "`` is functionally equivalent to
        unset and must also raise.  Catches a YAML quoting mistake
        in compose where the value is a multi-line empty literal.
        """
        with pytest.raises(RuntimeError):
            self._run_gunicorn_conf(env_value="   ")

    def test_pinned_backend_cidr_passes_through(self) -> None:
        """The bundled compose default ``172.31.0.0/24`` reflects
        verbatim into ``forwarded_allow_ips``.  Stripping is allowed
        (an operator might leave a trailing newline in YAML); the
        normalised value MUST match exactly.
        """
        globals_ = self._run_gunicorn_conf(env_value=BUNDLED_BACKEND_CIDR)
        assert globals_["forwarded_allow_ips"] == BUNDLED_BACKEND_CIDR

    def test_pinned_shared_frontend_cidr_passes_through(self) -> None:
        """The shared-mode override default ``172.32.0.0/24`` reflects
        verbatim.  Mirrors the bundled-CIDR test for the production
        deployment path the maintainer actually runs.
        """
        globals_ = self._run_gunicorn_conf(env_value=SHARED_FRONTEND_CIDR)
        assert (
            globals_["forwarded_allow_ips"] == SHARED_FRONTEND_CIDR
        )

    def test_single_ip_passes_through(self) -> None:
        """Audit recommendation says the strictest setting is "the
        exact container IP of the nginx service".  A single literal
        IP is valid input.
        """
        globals_ = self._run_gunicorn_conf(env_value="172.31.0.10")
        assert globals_["forwarded_allow_ips"] == "172.31.0.10"

    def test_no_rfc1918_fallback_in_source(self) -> None:
        """Regression guard: the loose ``172.16.0.0/12,192.168.0.0/16,
        10.0.0.0/8`` fallback must be GONE from gunicorn.conf.py.  A
        future commit re-adding it would silently restore the F-015
        spoofing surface.  Match the join string the historic default
        used so a partial re-add is also caught.
        """
        text = GUNICORN_CONF.read_text(encoding="utf-8")
        assert "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8" not in text
