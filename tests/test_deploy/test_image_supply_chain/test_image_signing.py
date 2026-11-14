"""Image supply-chain tests: CI signs the published image with keyless Cosign, .gitignore keeps a
signing key out of the tree, and deploy/README.md documents the digest rotation (audit finding
F-155).
"""

from __future__ import annotations

import re

import pytest
import yaml

from ._helpers import GITIGNORE, DEPLOY_README, DOCKER_PUBLISH_WORKFLOW


# ──────────────────────────────────────────────────────────────────
# F-155: Cosign signing wired into CI.
# ──────────────────────────────────────────────────────────────────


class TestDockerPublishWorkflowSignsImage:
    """``.github/workflows/docker-publish.yml`` installs cosign and
    signs the just-pushed image with keyless OIDC.  Audit finding
    F-155 / Commit C-36.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Return the parsed workflow YAML."""
        with DOCKER_PUBLISH_WORKFLOW.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the raw workflow text for substring assertions."""
        return DOCKER_PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_grants_id_token_write(self, parsed: dict) -> None:
        """Keyless OIDC requires ``id-token: write`` permissions.

        Without this, ``cosign sign`` fails with a 403 from the OIDC
        token endpoint.  We assert on the structured permissions
        block (not just the substring) so a regression that
        accidentally removes the permission surfaces here.
        """
        job = parsed["jobs"]["build-and-push"]
        permissions = job.get("permissions") or {}
        assert permissions.get("id-token") == "write", (
            "build-and-push job does not grant id-token: write; "
            "cosign keyless OIDC will fail with a 403 (audit finding "
            "F-155).  Add it to the job's permissions block."
        )

    def test_workflow_installs_cosign(self, parsed: dict) -> None:
        """A step installs cosign by sigstore/cosign-installer pinned by commit, and
        installs the installer's own default cosign.

        The installer's pinned commit is cosign's one home (ruling plan#2442): its
        default cosign is the binary it checks against the sha256 the action holds at
        that commit, so an update of the installer's line moves cosign with it and an
        upstream release cannot change what signs without a change here.  A
        ``cosign-release`` of the workflow's own would be a second home, which an
        update of the installer's line leaves behind.
        """
        steps = parsed["jobs"]["build-and-push"]["steps"]
        installer_step = next(
            (s for s in steps if "sigstore/cosign-installer" in (s.get("uses") or "")),
            None,
        )
        assert installer_step is not None, (
            ".github/workflows/docker-publish.yml does not install "
            "cosign via sigstore/cosign-installer; the sign step "
            "below would fail with ``cosign: not found``."
        )
        assert re.fullmatch(r"sigstore/cosign-installer@[0-9a-f]{40}",
                            installer_step["uses"]), (
            f"sigstore/cosign-installer is not pinned by commit "
            f"({installer_step['uses']!r}); the cosign it installs would move "
            f"with whatever the reference names."
        )
        assert "cosign-release" not in (installer_step.get("with") or {}), (
            "sigstore/cosign-installer is given a cosign-release; the installer's "
            "pinned commit is cosign's one home (ruling plan#2442), and an update "
            "of the installer's line would leave this one behind."
        )

    def test_workflow_signs_with_keyless_oidc(self, text: str) -> None:
        """A step invokes ``cosign sign --yes`` against the build digest.

        We check on the raw text because the actual sign command is
        in a multi-line shell ``run:`` block that's awkward to assert
        on via the parsed YAML.  ``--yes`` is required to suppress
        the interactive confirmation that would otherwise block CI.
        """
        assert "cosign sign --yes" in text, (
            ".github/workflows/docker-publish.yml does not run "
            "``cosign sign --yes``; without --yes the sign step "
            "blocks waiting for an interactive confirmation that "
            "never arrives in CI (audit finding F-155)."
        )
        # The sign target must reference the digest, not a tag.
        # Signing by tag would let a tag swap detach the signature
        # from the image bytes it attests to.
        assert "${DIGEST}" in text or "$DIGEST" in text, (
            ".github/workflows/docker-publish.yml does not pass the "
            "build digest to cosign sign; signing by tag alone is "
            "the F-155 anti-pattern (a tag swap would silently "
            "detach the signature)."
        )

    def test_workflow_outputs_digest_for_pinning(self, text: str) -> None:
        """The workflow emits the build digest in a discoverable spot.

        The digest is what the operator pastes into
        SHEKEL_IMAGE_DIGEST; without an emit step, every deploy
        requires the operator to look up the digest manually.
        """
        # Match either the workflow summary write or a literal echo.
        assert "GITHUB_STEP_SUMMARY" in text, (
            ".github/workflows/docker-publish.yml does not write the "
            "build digest to GITHUB_STEP_SUMMARY; operators have no "
            "discoverable surface for the SHEKEL_IMAGE_DIGEST value."
        )
        assert "SHEKEL_IMAGE_DIGEST" in text, (
            ".github/workflows/docker-publish.yml does not name "
            "SHEKEL_IMAGE_DIGEST in the digest emit step; the "
            "operator has to know which env var to set."
        )


class TestGitignoreExcludesCosignKeys:
    """.gitignore excludes cosign private key files so a careless
    ``git add cosign.key`` in the repo root cannot leak a signing
    key.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full .gitignore source as a string."""
        return GITIGNORE.read_text(encoding="utf-8")

    def test_root_cosign_key_excluded(self, text: str) -> None:
        """``cosign.key`` (root path or any nested path) is ignored."""
        assert re.search(
            r"^cosign\.key\b", text, re.MULTILINE,
        ), (
            ".gitignore does not exclude cosign.key at the repo root; "
            "a stray ``git add`` could leak the signing key (audit "
            "finding F-155 hardening)."
        )

    def test_nested_cosign_keys_excluded(self, text: str) -> None:
        """A glob covers cosign.key files in subdirectories.

        Operators may keep keys under deploy/ or scripts/; the
        gitignore must catch those paths too.
        """
        # ``**/cosign.key`` is the canonical recursive form.  Accept
        # any equivalent that catches subdirectories.
        assert re.search(
            r"^\*\*/cosign\.key\b|^\*\.cosign\.key\b",
            text,
            re.MULTILINE,
        ), (
            ".gitignore does not exclude cosign.key in subdirectories; "
            "a key dropped under deploy/ or scripts/ would track."
        )


class TestDeployReadmeDocumentsRotation:
    """deploy/README.md carries the Image-digest-pinning + Cosign
    rotation procedure.  Operators reading the README must find the
    workflow without context-switching to the audit findings.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full deploy README source as a string."""
        return DEPLOY_README.read_text(encoding="utf-8")

    def test_readme_section_exists(self, text: str) -> None:
        """The README has an ``Image Digest Pinning`` section header."""
        assert re.search(
            r"^##\s+Image\s+Digest\s+Pinning",
            text,
            re.MULTILINE | re.IGNORECASE,
        ), (
            "deploy/README.md does not have an ``Image Digest Pinning`` "
            "section; the operator has no documented rotation procedure."
        )

    @pytest.mark.parametrize(
        "marker",
        (
            "SHEKEL_IMAGE_DIGEST",
            "cosign verify",
            "F-060",
            "F-155",
            "C-36",
        ),
    )
    def test_readme_mentions_marker(self, text: str, marker: str) -> None:
        """Each load-bearing marker the operator needs appears in the
        README.  Catches a partial documentation regression where
        the workflow is renamed but the documentation lags.
        """
        assert marker in text, (
            f"deploy/README.md does not mention {marker!r}; the "
            f"operator workflow documentation is incomplete."
        )
