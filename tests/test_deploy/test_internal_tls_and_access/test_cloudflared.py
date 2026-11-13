"""C-37's cloudflared tests: the tunnel's Access policy and TLS verification (F-061), and its
metrics endpoint bound to loopback (F-128).
"""

from __future__ import annotations

import re

import pytest
import yaml

from ._helpers import CLOUDFLARED_CONFIG


# ──────────────────────────────────────────────────────────────────
# F-061: Cloudflare Access policy + noTLSVerify rationale.
# ──────────────────────────────────────────────────────────────────


class TestCloudflaredAccessPolicy:
    """The cloudflared config carries an originRequest.access block
    on the production ingress rule so a request without a valid
    Access JWT is rejected at the cloudflared edge before it ever
    reaches Nginx.  Audit finding F-061.

    The committed file uses placeholder values (``<TEAM_NAME>``,
    ``<AUD_TAG>``); these tests assert the structural shape, not the
    placeholder values themselves -- a runtime check that the
    placeholders have been replaced is the operator's responsibility
    and is documented in docs/runbook/6_cloudflare.md §6.4a.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the cloudflared config text."""
        return CLOUDFLARED_CONFIG.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Parse the cloudflared config as YAML.

        The placeholder values (``<TUNNEL_ID>``, ``<TEAM_NAME>``,
        ``<AUD_TAG>``, ``<DOMAIN>``) are valid YAML strings so
        ``yaml.safe_load`` succeeds; we just cannot interpret them
        as Cloudflare cares about, which is fine for these tests.
        """
        return yaml.safe_load(CLOUDFLARED_CONFIG.read_text(encoding="utf-8"))

    def test_production_rule_has_access_block(self, parsed: dict) -> None:
        """The production ingress rule (the one matching ``<DOMAIN>``)
        must declare an ``originRequest.access`` block.

        cloudflared evaluates ingress rules top-to-bottom; the first
        rule with a hostname is the production rule.  The catch-all
        (``service: http_status:404``) at the bottom does not need
        an Access block because it never serves real content.
        """
        ingress = parsed.get("ingress")
        assert isinstance(ingress, list) and len(ingress) >= 2, (
            "cloudflared ingress missing or too short -- expected at "
            "least one production rule plus the catch-all 404"
        )
        # The first rule with a hostname must carry the Access block.
        # We do not require a specific position -- a future change
        # might add a staging rule between the production rule and
        # the catch-all.
        production_rules = [
            rule for rule in ingress
            if isinstance(rule, dict) and "hostname" in rule
        ]
        assert production_rules, (
            "no ingress rule with a hostname -- the catch-all alone "
            "is not a valid Cloudflare Tunnel config"
        )
        for rule in production_rules:
            origin_request = rule.get("originRequest") or {}
            access = origin_request.get("access")
            assert isinstance(access, dict), (
                f"ingress rule for hostname={rule.get('hostname')!r} "
                f"missing originRequest.access block (audit F-061)"
            )

    def test_access_block_required_true(self, parsed: dict) -> None:
        """``access.required: true`` is the load-bearing flag.

        Without ``required: true``, cloudflared treats the Access
        check as advisory -- a request without a valid JWT still
        forwards to the origin.  The audit fix demands hard
        enforcement at the cloudflared edge.
        """
        ingress = parsed["ingress"]
        production_rules = [
            rule for rule in ingress
            if isinstance(rule, dict) and "hostname" in rule
        ]
        for rule in production_rules:
            access = rule["originRequest"]["access"]
            assert access.get("required") is True, (
                f"ingress rule for hostname={rule.get('hostname')!r} "
                f"has access.required != True (audit F-061): "
                f"{access!r}"
            )

    def test_access_block_carries_team_name_placeholder(
        self, parsed: dict
    ) -> None:
        """``teamName`` is a placeholder (``<TEAM_NAME>``) in the
        committed template.  The presence of the key is what matters
        here -- the placeholder substitution is the operator's step.
        """
        ingress = parsed["ingress"]
        production_rules = [
            rule for rule in ingress
            if isinstance(rule, dict) and "hostname" in rule
        ]
        for rule in production_rules:
            access = rule["originRequest"]["access"]
            team_name = access.get("teamName")
            assert team_name, (
                f"ingress rule for hostname={rule.get('hostname')!r} "
                f"missing access.teamName (audit F-061)"
            )

    def test_access_block_carries_aud_tag_list(
        self, parsed: dict
    ) -> None:
        """``audTag`` must be a non-empty list.

        Cloudflare allows multiple AUDs per ingress rule (e.g. one
        application protecting both a primary and a staging
        hostname).  We do not require a specific count -- a single
        AUD is the common case.
        """
        ingress = parsed["ingress"]
        production_rules = [
            rule for rule in ingress
            if isinstance(rule, dict) and "hostname" in rule
        ]
        for rule in production_rules:
            access = rule["originRequest"]["access"]
            aud_tag = access.get("audTag")
            assert isinstance(aud_tag, list) and aud_tag, (
                f"ingress rule for hostname={rule.get('hostname')!r} "
                f"access.audTag is not a non-empty list (audit "
                f"F-061): {aud_tag!r}"
            )

    def test_documents_access_rationale_in_comment(
        self, text: str
    ) -> None:
        """The cloudflared template must carry a comment explaining
        the Access policy so a future operator does not "simplify" by
        deleting the block.  The comment cites F-061 / C-37 so the
        audit trail is inline.
        """
        assert "F-061" in text, (
            "cloudflared/config.yml does not mention F-061 -- the "
            "operator has no inline pointer to the audit finding "
            "behind the Access policy block"
        )
        assert "C-37" in text, (
            "cloudflared/config.yml does not mention C-37 -- the "
            "remediation commit reference is missing from the "
            "rationale comment"
        )
        # The rationale must explain the auth surface, not just
        # echo the directive.  We assert on a fragment of the
        # block-comment phrase.
        assert "Cloudflare Access" in text


# ──────────────────────────────────────────────────────────────────
# F-128: cloudflared metrics endpoint binds loopback only.
# ──────────────────────────────────────────────────────────────────


class TestCloudflaredMetricsLoopback:
    """The cloudflared config pins the Prometheus metrics endpoint to
    127.0.0.1:2000 instead of the default 0.0.0.0:2000.  Audit finding
    F-128.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the cloudflared config text."""
        return CLOUDFLARED_CONFIG.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Parse the cloudflared config as YAML."""
        return yaml.safe_load(CLOUDFLARED_CONFIG.read_text(encoding="utf-8"))

    def test_metrics_directive_present(self, parsed: dict) -> None:
        """The top-level ``metrics`` key must exist.

        Without this directive, cloudflared falls back to its
        internal default which historically binds 0.0.0.0:2000
        (audit finding F-128).
        """
        assert "metrics" in parsed, (
            "cloudflared/config.yml has no top-level ``metrics`` "
            "directive -- the binding falls back to 0.0.0.0:2000 "
            "(audit F-128)"
        )

    def test_metrics_binds_loopback_only(self, parsed: dict) -> None:
        """The metrics directive must bind to 127.0.0.1.

        Other loopback shapes (``localhost:2000``, ``[::1]:2000``)
        are functionally equivalent for the security property but
        the canonical form is 127.0.0.1 -- pin the literal string
        so a regression to ``0.0.0.0`` or a sloppy ``:2000`` (no
        host) fails the test loudly.
        """
        metrics = parsed["metrics"]
        assert isinstance(metrics, str), (
            f"cloudflared metrics directive is not a string: "
            f"{metrics!r}"
        )
        assert metrics.startswith("127.0.0.1:"), (
            f"cloudflared metrics directive is not loopback-only "
            f"(audit F-128): {metrics!r}"
        )

    def test_documents_loopback_rationale(self, text: str) -> None:
        """The metrics directive must carry an inline comment citing
        F-128 / C-37 so a future operator who deletes the directive
        re-introduces the audit finding by name in the diff.
        """
        # Find the line with metrics: 127.0.0.1 and walk backward
        # through the immediately-preceding comment block.
        match = re.search(
            r"((?:^#.*\n)+)metrics:\s*127\.0\.0\.1",
            text,
            re.MULTILINE,
        )
        assert match is not None, (
            "cloudflared/config.yml ``metrics: 127.0.0.1:2000`` "
            "has no leading comment block; the operator has no "
            "inline rationale"
        )
        block = match.group(1)
        assert "F-128" in block, (
            "metrics directive comment does not mention F-128 -- the "
            "audit-finding pointer is missing"
        )
        assert "C-37" in block, (
            "metrics directive comment does not mention C-37 -- the "
            "remediation commit pointer is missing"
        )
