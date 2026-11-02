"""Dev runs Valkey as production does (ruling plan#2619): ``docker-compose.dev.yml``'s valkey
service and app meet over the socket volume exactly as ``docker-compose.yml``'s do, so a change
to how production reaches its store meets dev first.

An interim fence, as ``test_image_supply_chain.py``'s class holding the two files' images is
(ruling plan#2560): each file still spells how the two meet, finding plan#2635, which its owner
step plan#2561 (one home for what the two files share) answers, deleting this module; a
service file both extend cannot carry the top-level volume, so that step must find the
volume's home too.  The socket itself, its volume and its permissions are graded against a
running server in ``test_valkey_store.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.test_deploy._compose import BASE_COMPOSE, REPO_ROOT

DEV_COMPOSE = REPO_ROOT / "docker-compose.dev.yml"
#: The volume Valkey makes its socket in, in both files.
_SOCKETS = "valkey-socket"


def _parsed(path: Path) -> dict:
    """The compose file at ``path``, parsed."""
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


#: Each file parsed: production's base file, then dev's.
_FILES = {path.name: _parsed(path) for path in (BASE_COMPOSE, DEV_COMPOSE)}


def _socket_mount(service: dict) -> dict:
    """The service's one mount of the socket volume.

    Raises:
        AssertionError: when it mounts the volume other than once.
    """
    mounts = [mount for mount in service["volumes"]
              if isinstance(mount, dict) and mount["source"] == _SOCKETS]
    assert len(mounts) == 1, f"{len(mounts)} mounts of {_SOCKETS}: {service['volumes']}"
    return mounts[0]


def _named_socket(uri: str) -> str:
    """The socket file a ``valkey+unix://`` store address names: what follows its last ``@``,
    less the close of a compose default (``${RATELIMIT_STORAGE_URI:-...}``)."""
    return uri.rsplit("@", 1)[1].removesuffix("}")


def _meeting(parsed: dict) -> dict:
    """How a file's valkey service and app meet: Valkey's network, its published ports, its
    socket mount and healthcheck; the app's socket mount, the scheme and user of its store
    address and the socket it names; and the socket volume's options."""
    valkey, app = parsed["services"]["valkey"], parsed["services"]["app"]
    uri = app["environment"]["RATELIMIT_STORAGE_URI"]
    return {
        "valkey network": valkey.get("network_mode"),
        "valkey ports": valkey.get("ports"),
        "valkey mount": _socket_mount(valkey),
        "healthcheck": valkey["healthcheck"]["test"],
        "app mount": _socket_mount(app),
        "app scheme and user": "valkey+unix://shekel:" in uri,
        "app socket": _named_socket(uri),
        "volume": parsed["volumes"][_SOCKETS],
    }


def test_dev_meets_valkey_as_production_does():
    """Every part of how the valkey service and app meet is the same in both files."""
    production, dev = (_meeting(parsed) for parsed in _FILES.values())
    assert dev == production


@pytest.mark.parametrize("name", list(_FILES))
def test_valkey_is_reached_only_over_the_socket_the_app_mounts_read_only(name):
    """In each file Valkey joins no network and publishes no port, the app names the socket
    inside the folder both mount, and mounts it read-only, so it can neither remove the socket
    nor put a file of its own beside it."""
    meeting = _meeting(_FILES[name])
    assert (meeting["valkey network"], meeting["valkey ports"]) == ("none", None)
    assert meeting["app scheme and user"]
    assert meeting["app socket"].startswith(f"{meeting['valkey mount']['target']}/")
    assert meeting["app mount"]["target"] == meeting["valkey mount"]["target"]
    assert meeting["app mount"].get("read_only") is True
