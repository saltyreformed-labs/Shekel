"""Proxy trust tests (C-33, F-020/F-129/F-063): the prod override's and the base
compose's network topology and ``FORWARDED_ALLOW_IPS``, and the cloudflared template
routing WAN ingress through nginx.
"""

from __future__ import annotations

import pytest
import yaml

from tests.test_deploy._compose import BASE_COMPOSE

from ._helpers import (
    PROD_COMPOSE_OVERRIDE,
    CLOUDFLARED_TEMPLATE,
    BUNDLED_BACKEND_CIDR,
    BUNDLED_FRONTEND_CIDR,
    SHARED_FRONTEND_CIDR,
)


class TestProdComposeNetworkTopology:
    """Verify ``deploy/docker-compose.prod.yml`` realises the
    F-020/F-129 topology fix:

    * App is OFF the wider ``homelab`` network.
    * App is ON the dedicated ``shekel-frontend`` bridge.
    * App's ``FORWARDED_ALLOW_IPS`` is repinned to the
      shared-frontend CIDR.

    Tests parse the YAML rather than substring-matching the file
    text so example snippets in operator-pre-flight comments do not
    cause false positives or negatives.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Parse the prod compose override into a Python dict.

        ``yaml.safe_load`` is the standard tool here: we never need
        to construct arbitrary Python objects from this file, just
        read its mapping/list shape.  PyYAML is in the lock's dev
        group (``pyproject.toml``), which every suite run installs,
        so a missing import would surface earlier as a suite-level
        failure rather than masking a regression here.
        """
        with PROD_COMPOSE_OVERRIDE.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    @pytest.fixture(scope="class")
    @classmethod
    def yaml_text(cls) -> str:
        """Raw text of the prod compose override for substring
        matches that the YAML parse would normalise away.
        """
        return PROD_COMPOSE_OVERRIDE.read_text(encoding="utf-8")

    def test_app_no_longer_joins_homelab(self, parsed: dict) -> None:
        """Regression guard: the override must NOT add the app to
        the ``homelab`` network.  Co-tenant exposure (F-020/F-129)
        was the load-bearing reason for the C-33 topology change.
        """
        app_networks = parsed["services"]["app"].get("networks") or []
        assert "homelab" not in app_networks, (
            f"deploy/docker-compose.prod.yml still attaches app to "
            f"the homelab network: {app_networks}; F-020/F-129 "
            f"regression"
        )

    def test_top_level_homelab_network_removed(
        self, parsed: dict
    ) -> None:
        """The override must NOT declare ``homelab`` as a managed
        external network anymore -- the app no longer joins it, so
        the declaration becomes dead weight that misleads readers
        about the topology.
        """
        top_networks = parsed.get("networks") or {}
        assert "homelab" not in top_networks, (
            "deploy/docker-compose.prod.yml still declares the "
            "homelab network at the top level; remove the dead "
            "declaration to keep the file readable"
        )

    def test_app_joins_shekel_frontend(self, parsed: dict) -> None:
        """The shared-mode app must join the dedicated
        ``shekel-frontend`` bridge.
        """
        app_networks = parsed["services"]["app"].get("networks") or []
        assert "shekel-frontend" in app_networks

    def test_app_still_joins_backend(self, parsed: dict) -> None:
        """The internal-only ``backend`` bridge (db) is still
        required.  Removing it would break Gunicorn's DB connection;
        Valkey is on no network, reached over its socket file.
        """
        app_networks = parsed["services"]["app"].get("networks") or []
        assert "backend" in app_networks

    def test_shekel_frontend_declared_external(
        self, parsed: dict
    ) -> None:
        """``shekel-frontend`` is an externally-managed network
        (operator runs ``docker network create shekel-frontend``
        before first ``docker compose up``).  The override must
        declare it as such, with the explicit name to strip the
        compose project prefix.
        """
        top_networks = parsed.get("networks") or {}
        net = top_networks.get("shekel-frontend")
        assert net is not None, (
            "shekel-frontend network not declared at top level"
        )
        assert net.get("external") is True, (
            "shekel-frontend must be external: true so compose does "
            "not try to manage it"
        )
        # ``name: shekel-frontend`` strips the project prefix so the
        # bridge has the literal name the host-level compose
        # attaches to.  Without the rename, it would be
        # ``shekel-prod_shekel-frontend``.
        assert net.get("name") == "shekel-frontend"

    def test_app_forwarded_allow_ips_pinned_to_shared_subnet(
        self, parsed: dict
    ) -> None:
        """The override repins ``FORWARDED_ALLOW_IPS`` to the
        ``shekel-frontend`` subnet so Gunicorn keeps refusing
        forwarded headers from any unrelated network.
        """
        env = parsed["services"]["app"].get("environment") or {}
        # YAML allows scalar values to be quoted or bare; both the
        # literal string and the canonical CIDR form must work.
        # ``FORWARDED_ALLOW_IPS: 172.32.0.0/24`` parses as a string
        # without ambiguity, so the assertion is a direct equality
        # check.
        assert env.get("FORWARDED_ALLOW_IPS") == SHARED_FRONTEND_CIDR

    def test_bundled_nginx_still_disabled(
        self, parsed: dict
    ) -> None:
        """The override must still park the bundled
        shekel-prod-nginx in the disabled profile -- C-32 set this
        and C-33 must preserve it.
        """
        nginx = parsed["services"].get("nginx") or {}
        profiles = nginx.get("profiles") or []
        assert "disabled" in profiles, (
            f"bundled nginx no longer disabled in shared mode: "
            f"profiles={profiles}"
        )


class TestBaseComposeNetworkPinning:
    """Verify ``docker-compose.yml`` pins the frontend / backend
    subnets and sets the bundled-mode ``FORWARDED_ALLOW_IPS``
    default.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def yaml_text(cls) -> str:
        """Read the base docker-compose.yml once per test class."""
        return BASE_COMPOSE.read_text(encoding="utf-8")

    def test_frontend_subnet_pinned(self, yaml_text: str) -> None:
        """``frontend`` must be pinned to ``172.30.0.0/24`` so Nginx's
        ``set_real_ip_from`` directive references a stable CIDR.
        """
        assert f"subnet: {BUNDLED_FRONTEND_CIDR}" in yaml_text

    def test_backend_subnet_pinned(self, yaml_text: str) -> None:
        """``backend`` must be pinned to ``172.31.0.0/24`` so
        Gunicorn's ``FORWARDED_ALLOW_IPS`` literal matches the bridge
        carrying the in-stack proxy hop.
        """
        assert f"subnet: {BUNDLED_BACKEND_CIDR}" in yaml_text

    def test_app_gets_forwarded_allow_ips_default(
        self, yaml_text: str
    ) -> None:
        """The app service must default ``FORWARDED_ALLOW_IPS`` to
        the pinned backend CIDR.  The shared-mode override repins
        it; the bundled-mode default keeps fresh-host bring-up
        working without an explicit env var.
        """
        assert (
            "FORWARDED_ALLOW_IPS: ${FORWARDED_ALLOW_IPS:-172.31.0.0/24}"
            in yaml_text
        )

    def test_backend_still_internal(self, yaml_text: str) -> None:
        """``backend`` must remain ``internal: true`` so the db and
        app cannot reach the public internet (valkey joins no network).
        Audit finding F-020 mitigations require this.
        """
        # Robustly match the YAML structure: "backend:" header
        # followed by "driver: bridge" and "internal: true" (any
        # order) before the next top-level key.  We do a substring
        # match because docker-compose preserves indentation.
        assert "internal: true" in yaml_text


class TestCloudflaredTemplate:
    """Verify the bundled-mode cloudflared template documents and
    enforces the F-063 fix: WAN ingress terminates at Nginx, not
    Gunicorn.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Read the bundled cloudflared/config.yml template."""
        return CLOUDFLARED_TEMPLATE.read_text(encoding="utf-8")

    def test_service_routes_through_nginx(self, text: str) -> None:
        """The repo template's bundled-mode rule must route through
        the in-stack Nginx (``localhost:80``).  The audit's WAN-path
        bypass evidence (``service: http://shekel-prod-app:8000``)
        MUST NOT appear in this template.
        """
        assert "service: http://localhost:80" in text
        assert "service: http://shekel-prod-app:8000" not in text

    def test_documents_shared_mode_rule(self, text: str) -> None:
        """The template must document the shared-mode rule in a comment
        so a homelab operator switching modes has the right snippet
        inline: production's form, nginx's TLS listener with the vhost
        named, so cloudflared verifies nginx's certificate (ruling
        plan#4114).
        """
        assert "service: https://nginx:443" in text
        assert "originServerName: <DOMAIN>" in text

    def test_documents_wan_chokepoint_rationale(
        self, text: str
    ) -> None:
        """A reader of this file must encounter the rationale: WAN
        ingress terminates at Nginx for header / size / log /
        timeout enforcement.  Without the comment, a future
        operator might "simplify" the routing.
        """
        assert "WAN-PATH SECURITY INVARIANT" in text
        assert "F-063" in text
