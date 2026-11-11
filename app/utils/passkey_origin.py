"""
Shekel Budget App -- The Site's Passkey Origin

What an origin is, as a browser writes one, and the relying party id it gives
(plan#2538): :func:`relying_party_id`, the one statement of both.

Two layers read it.  The passkey service binds every ceremony to the origin
configuration names (``app.services.passkey_service``), and ``ProdConfig``
refuses to build on an origin no browser writes, so a production that could
complete no passkey ceremony never starts (plan#3488).  Its own module, with
nothing but the standard library under it, because ``app.config`` is the first
module of the app to load: the service imports the models, and a model imports
``app.config``, so a configuration that imported the service would import
itself half-built.
"""

from urllib.parse import urlsplit


#: The ports a browser leaves out of an origin, by scheme.
_DEFAULT_PORTS = {"https": 443, "http": 80}


def relying_party_id(origin: str) -> str:
    """Return the relying party id of *origin*: its host.

    *origin* must be an origin as a browser writes one in a ceremony's client
    data, because the completion compares the two exactly: a scheme, ``://``,
    a lower-case host and a port only where it is not the scheme's own, with
    no path, not even a trailing slash.  The scheme is ``https``, or ``http``
    for ``localhost`` alone, the only insecure origin a browser lets use a
    passkey.

    Args:
        origin: The site's origin, as configuration names it
            (``PASSKEY_ORIGIN``).

    Returns:
        Its host, for example ``shekel.example.com``.

    Raises:
        ValueError: *origin* is not such an origin.  The message names the
            setting and the value.
    """
    refusal = ValueError(
        "PASSKEY_ORIGIN must be an origin as a browser writes it, such as "
        "https://shekel.example.com: lower case, ASCII (a browser writes an "
        "international name as punycode), https (http only for localhost), "
        f"no path and no trailing slash; got {origin!r}."
    )
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError as error:
        # A bracketed host that is not an address, or a port not a number.
        raise refusal from error
    host = parts.hostname
    secure = parts.scheme == "https" or (
        parts.scheme == "http" and host == "localhost"
    )
    written = f"{parts.scheme}://{host}" + ("" if port is None else f":{port}")
    if (
        host is None or not secure or not origin.isascii() or origin != written
        or port == _DEFAULT_PORTS[parts.scheme]
    ):
        raise refusal
    return host
