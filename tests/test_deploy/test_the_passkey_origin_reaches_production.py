"""The passkey origin reaches the production app by the deploy's own files (plan#3488).

``ProdConfig`` refuses to start without ``PASSKEY_ORIGIN``, or with one no
browser writes (``tests/test_config/test_production_config.py``), so the files
that start production must hand the app one: the shared-mode override names
production's own, and the base file passes a bundled install's from its
``.env``.  Read from the files as parsed, so no daemon is needed; the merged
result ``docker compose`` builds of the two is graded in
``test_deploy_configs.py``'s ``TestDeployComposeParses``.
"""
from __future__ import annotations

import yaml

from app.utils.passkey_origin import relying_party_id
from tests.test_deploy._compose import REPO_ROOT, service

PROD_COMPOSE_OVERRIDE = REPO_ROOT / "deploy" / "docker-compose.prod.yml"


def _override_app_environment() -> dict[str, object]:
    """Return the app service's ``environment`` in the shared-mode override, as parsed."""
    with PROD_COMPOSE_OVERRIDE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)["services"]["app"]["environment"]


def test_the_override_names_production_s_own_origin():
    """The override names ``https://shekel.saltyreformed.com``, in a form the boot accepts.

    Production keeps that one name on the home network and through the
    Cloudflare tunnel (plan#2538's facts), so one passkey serves both.
    Written out here, so a moved name fails a test rather than passing on
    its own value, and checked by the boot's own statement of an origin.
    """
    origin = _override_app_environment()["PASSKEY_ORIGIN"]
    assert origin == "https://shekel.saltyreformed.com"
    assert relying_party_id(origin) == "shekel.saltyreformed.com"


def test_the_base_file_passes_a_bundled_install_s_origin_with_no_default():
    """The base file hands the app ``.env``'s value, and none when it names none.

    ``${PASSKEY_ORIGIN:-}``: an install whose ``.env`` names no origin
    reaches the app with the empty string, which ``ProdConfig`` refuses by
    name.  No default, since no address is every install's; and not
    ``${PASSKEY_ORIGIN:?...}``, which compose evaluates in the base file
    before the override replaces the value, so it would fail the parse of
    shared-mode production, whose host ``.env`` names none.
    """
    assert service("app")["environment"]["PASSKEY_ORIGIN"] == "${PASSKEY_ORIGIN:-}"
