"""Image supply-chain tests: production's compose override pins the app's image, and the migrate
service's with it, to SHEKEL_IMAGE_DIGEST and refuses a missing one; the base compose file and
.env.example document the pin; and every other compose image is pinned by tag and digest (audit
finding F-060, finding BAL-653).
"""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from tests.test_deploy._compose import CONFIG_ENV
from tests.test_deploy._compose import config as compose_config

from ._helpers import (
    DOCKER_COMPOSE,
    DEV_COMPOSE,
    PROD_COMPOSE_OVERRIDE,
    ENV_EXAMPLE,
    REQUIRED_DIGEST_INTERPOLATION_PATTERN,
    _docker_available,
    TAG_AND_DIGEST_PATTERN,
    PINNED_ELSEWHERE,
    _compose_images,
)


# ──────────────────────────────────────────────────────────────────
# F-060: Image digest pinning lives in the prod override.
# ──────────────────────────────────────────────────────────────────


class TestProdComposeOverridePinsByDigest:
    """deploy/docker-compose.prod.yml interpolates SHEKEL_IMAGE_DIGEST
    into the app service's image reference and FAILS LOUDLY when the
    variable is missing.  Audit findings F-060, F-155 / Commit C-36.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full prod override source as a string."""
        return PROD_COMPOSE_OVERRIDE.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Return the parsed prod override document.

        Class-scoped so the YAML is parsed once and reused across
        every test in this class.
        """
        with PROD_COMPOSE_OVERRIDE.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_app_service_overrides_image(self, parsed: dict) -> None:
        """The app service in the override declares an ``image:`` key.

        Without an override on this key, the merged compose would
        inherit the base file's ``${SHEKEL_IMAGE:-ghcr.io/saltyreformed-labs/shekel:latest}``
        and the digest pin never takes effect.
        """
        app = parsed["services"]["app"]
        assert "image" in app, (
            "deploy/docker-compose.prod.yml does not override "
            "services.app.image; the base ``:latest`` reference would "
            "be used in shared-mode production (audit finding F-060)."
        )

    def test_migrate_service_runs_the_app_s_pinned_image(self, parsed: dict) -> None:
        """The override pins the migrate service, the deploy, to the app's image.

        Without its own ``image:`` here, the migrate service would inherit the
        base file's ``:latest`` and run whatever that tag names, holding the
        database owner's password, while the app runs the pinned build.
        """
        services = parsed["services"]
        assert services["migrate"].get("image") == services["app"]["image"], (
            "deploy/docker-compose.prod.yml does not pin services.migrate.image "
            "to the app's digest; the deploy would run the base file's ``:latest``."
        )

    def test_image_uses_required_digest_interpolation(self, text: str) -> None:
        """The image override uses ``${SHEKEL_IMAGE_DIGEST:?...}``.

        The ``:?`` syntax is the load-bearing fix: it makes the
        missing-variable case a hard parse failure with a clear
        remediation message, instead of silently falling back to a
        floating tag.
        """
        assert REQUIRED_DIGEST_INTERPOLATION_PATTERN.search(text) is not None, (
            "deploy/docker-compose.prod.yml does not interpolate "
            "SHEKEL_IMAGE_DIGEST with the ``:?`` required-form syntax.  "
            "Use the literal pattern: "
            "``ghcr.io/saltyreformed-labs/shekel@${SHEKEL_IMAGE_DIGEST:?...}`` "
            "so a missing digest fails ``docker compose up`` rather "
            "than silently pulling whatever ``:latest`` resolves to "
            "(audit finding F-060)."
        )

    def test_image_namespace_matches_repository(self, text: str) -> None:
        """The image reference points at the right GHCR repo.

        A typo in the namespace would either pull the wrong image
        (best case: 404 at pull time) or pull a publicly-accessible
        image with a matching name (worst case).
        """
        assert "ghcr.io/saltyreformed-labs/shekel@" in text, (
            "deploy/docker-compose.prod.yml does not pin "
            "ghcr.io/saltyreformed-labs/shekel; check the namespace."
        )


class TestBaseComposeDocumentsOverride:
    """The base docker-compose.yml carries a comment block warning
    operators that production must override the ``:latest`` tag with
    a digest pin.  Discoverability matters: an end user reading the
    base file must not assume ``:latest`` is the prod-safe default.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full base compose source as a string."""
        return DOCKER_COMPOSE.read_text(encoding="utf-8")

    def test_image_line_carries_c36_pointer(self, text: str) -> None:
        """The block above the ``image:`` line references C-36 and F-060.

        The comment is the documentation surface for the override
        requirement; a regression that drops it would silently
        weaken the discoverability of the production hardening.
        """
        # Find the app service block start, then read forward to the
        # image: line.  The pointer comment must appear within ~50
        # lines of the image: line (it sits directly above it in the
        # current layout).
        app_match = re.search(r"^  app:\s*$", text, re.MULTILINE)
        assert app_match is not None, "no ``app:`` service in base compose"
        # Find the image line after the app service marker; it may carry
        # the YAML anchor the migrate service runs the same image by, and
        # reads a bundled install's pin, SHEKEL_IMAGE, with ``:latest`` as
        # its default (ruling plan#3753).
        image_match = re.search(
            r"^    image:\s*(?:&[\w-]+\s+)?"
            r"\$\{SHEKEL_IMAGE:-ghcr\.io/saltyreformed-labs/shekel:latest\}",
            text[app_match.start():],
            re.MULTILINE,
        )
        assert image_match is not None, (
            "base docker-compose.yml's app service lost the "
            "``image: ${SHEKEL_IMAGE:-ghcr.io/saltyreformed-labs/shekel:latest}`` "
            "line.  If "
            "the image was moved entirely to the prod override, this "
            "test must be updated to assert on the override instead."
        )
        # Slice the block from app: to the image: line and look for
        # the audit pointer.
        block = text[app_match.start():app_match.start() + image_match.end()]
        assert "F-060" in block, (
            "base docker-compose.yml's app service block no longer "
            "mentions F-060 above the image: line; the C-36 "
            "documentation marker is missing.  Operators reading the "
            "base file would not know that ``:latest`` is intentionally "
            "non-production-safe."
        )
        assert "C-36" in block, (
            "base docker-compose.yml's app service block no longer "
            "mentions C-36 above the image: line; the documentation "
            "trail to the digest-pinning override is broken."
        )


class TestEnvExampleDocumentsDigestVariable:
    """``.env.example`` declares SHEKEL_IMAGE_DIGEST so the operator
    knows to set it.  An undocumented required variable would only
    surface as a compose parse error at first ``up`` -- bad UX for
    a production deployment.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full .env.example source as a string."""
        return ENV_EXAMPLE.read_text(encoding="utf-8")

    def test_shekel_image_digest_present(self, text: str) -> None:
        """``SHEKEL_IMAGE_DIGEST=`` appears as an active assignment.

        The empty value documents the operator's responsibility
        without baking a placeholder digest that could be mistaken
        for a real pin.
        """
        assert re.search(
            r"^SHEKEL_IMAGE_DIGEST=", text, re.MULTILINE,
        ), (
            ".env.example does not declare SHEKEL_IMAGE_DIGEST; "
            "operators reading the example would not know about the "
            "required variable until ``docker compose up`` failed at "
            "parse time (audit finding F-060)."
        )

    def test_documents_remediation_workflow(self, text: str) -> None:
        """The variable's leading comment block points at the
        rotation procedure in deploy/README.md.  Discoverability:
        the reader can find the next step without context-switching.
        """
        # The pointer must appear somewhere in the comment block above
        # the assignment.  We slice the file from the comment marker
        # for the digest section to the assignment line and assert on
        # the substring.
        match = re.search(
            r"((?:^#.*\n)+)SHEKEL_IMAGE_DIGEST=",
            text,
            re.MULTILINE,
        )
        assert match is not None, (
            ".env.example's SHEKEL_IMAGE_DIGEST has no leading comment "
            "block; the operator has no inline guidance."
        )
        block = match.group(1)
        for marker in ("F-060", "C-36", "deploy/README.md"):
            assert marker in block, (
                f".env.example's SHEKEL_IMAGE_DIGEST comment block does "
                f"not mention {marker!r}; the documentation trail is "
                f"incomplete."
            )


@pytest.mark.docker
class TestComposeOverrideRequiresDigest:
    """Subprocess test: ``docker compose config`` against the merged
    base + prod override must FAIL when SHEKEL_IMAGE_DIGEST is unset
    and SUCCEED with the digest pinned.

    This is the highest-fidelity check: it exercises compose's actual
    interpolation engine, so a regression in the ``:?`` syntax or in
    the env var name surfaces here even if the file-content tests
    above pass.
    """

    @pytest.fixture
    def base_env(self) -> dict[str, str]:
        """The render's environment with every OTHER required variable
        set (``CONFIG_ENV`` less ``SHEKEL_IMAGE_DIGEST``), so the
        missing-digest test is refused for the digest and nothing else.
        Each test then layers SHEKEL_IMAGE_DIGEST on top (or omits it
        deliberately).
        """
        return {name: value for name, value in CONFIG_ENV.items()
                if name != "SHEKEL_IMAGE_DIGEST"}

    def _run_compose_config(self, env: dict[str, str]) -> subprocess.CompletedProcess:
        """Helper: run ``docker compose config`` against the merged
        base + prod override with a synthetic env."""
        return compose_config(DOCKER_COMPOSE, PROD_COMPOSE_OVERRIDE, env=env)

    def test_missing_digest_fails_compose_parse(
        self, base_env: dict[str, str],
    ) -> None:
        """Without SHEKEL_IMAGE_DIGEST, ``docker compose config`` exits
        non-zero with a remediation message that mentions the variable
        name.  This is the load-bearing security property: the
        operator CANNOT accidentally deploy without pinning a digest.
        """
        if not _docker_available():
            pytest.skip("docker not available")
        result = self._run_compose_config(base_env)
        assert result.returncode != 0, (
            f"docker compose config succeeded WITHOUT "
            f"SHEKEL_IMAGE_DIGEST set; the ``:?`` required-form "
            f"interpolation is broken.\n"
            f"  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )
        # The error message must reference the env var so the operator
        # knows what to set.  The actual message comes from the ``:?``
        # remediation text in deploy/docker-compose.prod.yml.
        combined = result.stdout + result.stderr
        assert "SHEKEL_IMAGE_DIGEST" in combined, (
            f"compose error does not mention SHEKEL_IMAGE_DIGEST; the "
            f"operator would not know what to set.\n"
            f"  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )

    def test_pinned_digest_succeeds(
        self, base_env: dict[str, str],
    ) -> None:
        """With SHEKEL_IMAGE_DIGEST set to a valid sha256 reference,
        the merged compose parses and the resulting image: line
        carries the digest.
        """
        if not _docker_available():
            pytest.skip("docker not available")
        # 64 hex chars after sha256: -- the canonical OCI digest shape.
        synthetic_digest = "sha256:" + "0" * 64
        env = {**base_env, "SHEKEL_IMAGE_DIGEST": synthetic_digest}
        result = self._run_compose_config(env)
        assert result.returncode == 0, (
            f"docker compose config failed WITH SHEKEL_IMAGE_DIGEST "
            f"set to {synthetic_digest!r}; the override pinning is "
            f"broken.\n"
            f"  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )
        merged = result.stdout
        expected_image = (
            f"ghcr.io/saltyreformed-labs/shekel@{synthetic_digest}"
        )
        assert expected_image in merged, (
            f"merged compose does not carry the pinned image "
            f"reference {expected_image!r}; check the ``image:`` "
            f"override in deploy/docker-compose.prod.yml.\n"
            f"  stdout: {result.stdout}"
        )
        # The deploy runs the web server's pinned build: a migrate
        # service left on the base file's ``:latest`` would run
        # whatever that tag names, with the owner's password.
        services = yaml.safe_load(merged)["services"]
        assert services["migrate"]["image"] == services["app"]["image"] == expected_image


class TestEveryComposeImageIsPinnedByDigest:
    """Every image a compose file names is ``name:tag@sha256:<digest>``, so a push to a tag
    cannot change what a ``docker compose up`` runs (finding BAL-653): the tag says which
    version, the digest pins its bytes.  The app's is pinned elsewhere."""

    def test_every_image_names_its_tag_and_digest(self) -> None:
        """Each compose image but the app's carries a tag and a sha256 digest; the census
        found the compose files production and dev use, and the exempt lines are still where
        the exemptions say, so neither can pass by reading nothing."""
        files, images = _compose_images()
        assert {"docker-compose.yml", "docker-compose.dev.yml",
                "deploy/docker-compose.prod.yml"} <= set(files), files
        assert PINNED_ELSEWHERE <= images.keys(), sorted(images)
        unpinned = {f"{name} {service}": image for (name, service), image in images.items()
                    if (name, service) not in PINNED_ELSEWHERE
                    and not TAG_AND_DIGEST_PATTERN.match(image)}
        assert not unpinned, (
            f"compose images pinned by tag alone: {unpinned}.  Pin each as "
            "``name:tag@sha256:<digest>``, the digest the multi-arch index's, read with "
            "``docker buildx imagetools inspect name:tag`` (finding BAL-653).")


class TestDevComposeRunsProductionsImages:
    """Each service ``docker-compose.dev.yml`` and ``docker-compose.yml`` both run from an
    image runs ONE image in both, tag and digest, so dev exercises what production runs (parity
    audit findings D04, the store, and D15, the database).  An interim fence (ruling
    plan#2560): each file still writes the pin, until step plan#2561 gives each image one
    home, a service file both extend, and deletes this class."""

    def test_each_service_both_files_run_from_an_image_runs_one_image(self) -> None:
        """The services both files run from an image (``db`` and ``valkey``; the app is built
        in dev) name the same image in each, so a bump of one file alone fails here."""
        _files, images = _compose_images()
        base, dev = ({service: image for (name, service), image in images.items()
                      if name == path.name} for path in (DOCKER_COMPOSE, DEV_COMPOSE))
        shared = {service: (base[service], dev[service]) for service in base.keys() & dev.keys()}
        assert {"db", "valkey"} <= shared.keys(), sorted(shared)
        differing = {name: pair for name, pair in shared.items() if pair[0] != pair[1]}
        assert not differing, (
            f"docker-compose.dev.yml runs other images than docker-compose.yml: {differing}.  "
            "Move both files' pins in one change (ruling plan#2560; plan#2561 gives each one "
            "home).")
