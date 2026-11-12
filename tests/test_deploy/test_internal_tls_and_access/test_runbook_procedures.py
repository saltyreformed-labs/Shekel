"""C-37's runbook tests: the runbook carries the operator procedures for the cert, the Access
policy, the metrics binding and their troubleshooting.
"""

from __future__ import annotations

import re

import pytest

from ._helpers import RUNBOOK


# ──────────────────────────────────────────────────────────────────
# Runbook documentation surface.
# ──────────────────────────────────────────────────────────────────


class TestRunbookDocumentsC37Procedures:
    """docs/runbook.md carries the operator-facing procedures for the
    C-37 changes (cert generation, Access policy attachment, metrics
    binding verification, troubleshooting).  Discoverability matters:
    operators reading the runbook must find the workflow without
    context-switching to the audit findings.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full runbook source as a string."""
        return RUNBOOK.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "marker",
        (
            "scripts/generate_pg_cert.sh",
            "sslmode=require",
            "originRequest.access",
            "127.0.0.1:2000",
            "F-061",
            "F-128",
            "F-154",
            "C-37",
            "deploy/postgres",
        ),
    )
    def test_runbook_mentions_marker(self, text: str, marker: str) -> None:
        """Each load-bearing reference the operator needs appears
        somewhere in the runbook.  Catches a partial documentation
        regression where one section is updated but the troubleshooting
        table or §6 lags.
        """
        assert marker in text, (
            f"docs/runbook.md does not mention {marker!r}; the "
            f"operator workflow documentation is incomplete"
        )

    def test_runbook_has_postgres_tls_section(self, text: str) -> None:
        """A dedicated subsection covers the cert generation +
        startup procedure.  Section number is not asserted
        (renumbering is fine) but the heading text is."""
        assert re.search(
            r"^###?\s.*Postgres\s*TLS",
            text,
            re.MULTILINE,
        ), (
            "docs/runbook.md does not have a Postgres TLS section "
            "heading; the operator has no documented cert workflow"
        )

    def test_runbook_has_access_policy_section(self, text: str) -> None:
        """A dedicated subsection covers the Cloudflare Access
        attachment procedure (placeholder substitution + reload)."""
        assert re.search(
            r"^###?\s.*Access\s*Policy",
            text,
            re.MULTILINE | re.IGNORECASE,
        ), (
            "docs/runbook.md does not have an Access Policy section "
            "heading; the operator has no documented placeholder-"
            "substitution workflow"
        )
