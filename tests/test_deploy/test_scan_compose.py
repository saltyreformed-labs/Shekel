"""The daily image scan's scanner, ``docker-compose.scan.yml`` (rulings plan#3704, plan#3706,
plan#3708): Grype by tag and digest, holding the saved app image read-only and nothing else of
the host, its database downloaded fresh into a tmpfs on every run and refused when stale.

Text-based, as the base compose file's hardening tests are (``test_container_hardening.py``):
the file is parsed, never run.  What Grype does under these settings was measured on the
rootless daemon 2026-11-13 (plan#3715): a run on a real image's file exits 0 with its JSON on
standard output; an update source it cannot reach, a database older than the age allowed, a
file that is no image and an unset ``SCAN_IMAGE`` each exit 1 with nothing there.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_COMPOSE = REPO_ROOT / "docker-compose.scan.yml"

#: Grype's ``-nonroot`` image by its ``v``-spelled tag and its digest (ruling plan#3706).
GRYPE_PIN = re.compile(r"anchore/grype:v[0-9]+(?:\.[0-9]+)*-nonroot@sha256:[0-9a-f]{64}")
#: Where the scanner reads the saved image, and the one mount it holds.
IMAGE_MOUNT = {
    "type": "bind",
    "source": "${SCAN_IMAGE:?SCAN_IMAGE must name the saved image file to scan}",
    "target": "/scan/image.tar",
    "read_only": True,
}
#: The database settings, each written so no refusal rests on a default (ruling plan#3708).
DATABASE = {
    "GRYPE_DB_CACHE_DIR": "/tmp/grype-db",
    "GRYPE_DB_AUTO_UPDATE": "true",
    "GRYPE_DB_REQUIRE_UPDATE_CHECK": "true",
    "GRYPE_DB_VALIDATE_AGE": "true",
    "GRYPE_DB_MAX_ALLOWED_BUILT_AGE": "120h",
    "GRYPE_CHECK_FOR_APP_UPDATE": "false",
}


def _document() -> dict:
    """The scanner's compose file, parsed."""
    return yaml.safe_load(SCAN_COMPOSE.read_text(encoding="utf-8"))


def _scan() -> dict:
    """The scanner's one service."""
    services = _document()["services"]
    assert list(services) == ["scan"], sorted(services)
    return services["scan"]


class TestTheScannerHoldsOnlyTheImageItReads:
    """The scanner runs Grype's own image and holds the saved app image, read-only, and no
    other part of the host: no Docker socket, no secret, no port (ruling plan#3706)."""

    def test_it_runs_grype_s_nonroot_image_by_tag_and_digest(self) -> None:
        """The image line pins Grype's ``-nonroot`` variant by tag and digest, its project
        named, and its command reads the mounted file as a saved image, writing JSON."""
        assert _document()["name"] == "shekel-scan"
        scan = _scan()
        assert GRYPE_PIN.fullmatch(scan["image"]), scan["image"]
        assert scan["command"] == ["docker-archive:/scan/image.tar", "--output", "json"]

    def test_its_one_mount_is_the_image_file_read_only(self) -> None:
        """The one volume is the saved image, bound read-only from the file ``SCAN_IMAGE``
        names, and the compose file names no Docker socket and no secret anywhere."""
        scan = _scan()
        assert scan["volumes"] == [IMAGE_MOUNT]
        text = SCAN_COMPOSE.read_text(encoding="utf-8")
        assert "docker.sock" not in text
        assert "secrets" not in _document() and "secrets" not in scan

    def test_it_is_hardened_and_capped_as_every_service_is(self) -> None:
        """A read-only root, every capability dropped and none gained, no port and no
        privilege, and a cap on its CPUs, memory and processes (``.claude/rules/deploy.md``);
        it runs as the image's own non-root user, so no ``user:`` overrides it."""
        scan = _scan()
        assert scan["read_only"] is True
        assert scan["cap_drop"] == ["ALL"]
        assert scan["security_opt"] == ["no-new-privileges:true"]
        assert not {"ports", "privileged", "cap_add", "user", "network_mode"} & scan.keys()
        assert set(scan["deploy"]["resources"]["limits"]) == {"cpus", "memory", "pids"}


class TestTheDatabaseIsFreshAndAStaleOneIsRefused:
    """Grype downloads its database each run into a tmpfs, so no run reads one an earlier
    run left, and refuses one built more than 120 hours ago or one it could not check for a
    newer one (ruling plan#3708)."""

    def test_every_database_setting_is_written_in_the_file(self) -> None:
        """The environment is exactly the database settings, each its value."""
        assert _scan()["environment"] == DATABASE

    def test_the_database_lives_in_the_tmpfs(self) -> None:
        """The cache directory lies under the one tmpfs, which nothing outlives."""
        (mount,) = _scan()["tmpfs"]
        root = mount.split(":", 1)[0]
        assert Path(DATABASE["GRYPE_DB_CACHE_DIR"]).is_relative_to(root), (mount, root)
