"""The daily image scan's scanner, ``docker-compose.scan.yml`` (rulings plan#3704, plan#3706,
plan#3708, plan#3875, plan#3876): Grype by tag and digest, holding the saved app image read-only
and the one file it writes its catalog to, and nothing else of the host, its database
downloaded fresh into a tmpfs on every run and refused when stale; and the probe, the image's
own python, holding nothing of the host and on no network.

Text-based, as the base compose file's hardening tests are (``test_container_hardening.py``):
the file is parsed, never run.  What Grype does under these settings was measured on the
rootless daemon 2026-11-13 (plan#3715): a run on a real image's file exits 0 with its JSON on
standard output; an update source it cannot reach, a database older than the age allowed, a
file that is no image and an unset ``SCAN_IMAGE`` each exit 1 with nothing there.  And
2026-11-13 (plan#3798): the scanner writes its catalog into the file it is handed, made 0666
for it, and the probe runs production's image loaded by its id, making no network.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_COMPOSE = REPO_ROOT / "docker-compose.scan.yml"

#: Grype's ``-nonroot`` image by its ``v``-spelled tag and its digest (ruling plan#3706).
GRYPE_PIN = re.compile(r"anchore/grype:v[0-9]+(?:\.[0-9]+)*-nonroot@sha256:[0-9a-f]{64}")
#: Where the scanner reads the saved image, and where it writes its catalog: its two mounts.
IMAGE_MOUNT = {
    "type": "bind",
    "source": "${SCAN_IMAGE:?SCAN_IMAGE must name the saved image file to scan}",
    "target": "/scan/image.tar",
    "read_only": True,
}
CATALOG_MOUNT = {
    "type": "bind",
    "source": "${SCAN_CATALOG:?SCAN_CATALOG must name the file the scanner writes its catalog to}",
    "target": "/scan/catalog.json",
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


def _service(name: str) -> dict:
    """The compose file's service ``name``, once it holds exactly the scanner and the probe."""
    services = _document()["services"]
    assert list(services) == ["scan", "probe"], sorted(services)
    return services[name]


def _scan() -> dict:
    """The scanner's service."""
    return _service("scan")


class TestTheScannerHoldsOnlyTheImageItReads:
    """The scanner runs Grype's own image and holds the saved app image, read-only, and the
    one file it writes its catalog to, and no other part of the host: no Docker socket, no
    secret, no port (rulings plan#3706, plan#3875)."""

    def test_it_runs_grype_s_nonroot_image_by_tag_and_digest(self) -> None:
        """The image line pins Grype's ``-nonroot`` variant by tag and digest, its project
        named, and its command reads the mounted file as a saved image, writing JSON on its
        standard output and its catalog, a CycloneDX report, into the catalog's mount."""
        assert _document()["name"] == "shekel-scan"
        scan = _scan()
        assert GRYPE_PIN.fullmatch(scan["image"]), scan["image"]
        assert scan["command"] == ["docker-archive:/scan/image.tar", "--output", "json",
                                   "--output", f"cyclonedx-json={CATALOG_MOUNT['target']}"]

    def test_its_mounts_are_the_image_read_only_and_the_catalog_s_file(self) -> None:
        """The two volumes are the saved image, bound read-only from the file ``SCAN_IMAGE``
        names, and the catalog's file, bound from the one ``SCAN_CATALOG`` names, the one the
        scanner writes; the compose file names no Docker socket and no secret anywhere."""
        scan = _scan()
        assert scan["volumes"] == [IMAGE_MOUNT, CATALOG_MOUNT]
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


class TestTheProbeHoldsNothingOfTheHost:
    """The probe runs the image the scan loaded, by the id ``SCAN_PROBE`` names, as its own
    python, on no network, holding no mount, no variable, no secret and no port, hardened and
    capped as the scanner is, as the image's own non-root user (ruling plan#3876)."""

    def test_it_runs_the_loaded_image_s_own_python(self) -> None:
        """Its image is the one the scan names; its entrypoint the image's python, isolated
        from its environment and user site, running the script the scan hands it."""
        probe = _service("probe")
        assert probe["image"] == (
            "${SCAN_PROBE:?SCAN_PROBE must name the loaded image whose python reports itself}")
        assert probe["entrypoint"] == ["python", "-I", "-c"]
        assert "command" not in probe

    def test_it_holds_nothing_and_reaches_no_network(self) -> None:
        """No volume, variable, tmpfs, secret or port; no network at all."""
        probe = _service("probe")
        assert probe["network_mode"] == "none"
        assert not {"volumes", "environment", "env_file", "tmpfs", "secrets", "ports",
                    "networks"} & probe.keys()

    def test_it_is_hardened_and_capped_as_every_service_is(self) -> None:
        """A read-only root, every capability dropped and none gained, no privilege, its
        image's own user, and a cap on its CPUs, memory and processes."""
        probe = _service("probe")
        assert probe["read_only"] is True
        assert probe["cap_drop"] == ["ALL"]
        assert probe["security_opt"] == ["no-new-privileges:true"]
        assert not {"privileged", "cap_add", "user"} & probe.keys()
        assert set(probe["deploy"]["resources"]["limits"]) == {"cpus", "memory", "pids"}
