"""
Shekel Budget App -- Who is asking: the client's real address, and what a limit counts.

Gunicorn sets ``REMOTE_ADDR`` from the socket, so behind nginx every request
looks like nginx.  :func:`believe_forwarded_address_from` puts the client's
address there instead, for a caller on the one trusted list, so
``request.remote_addr`` is the client everywhere it is read: the rate limits
and every log line.  :func:`rate_limit_key` is what every limit counts by.

**The one trusted list is ``FORWARDED_ALLOW_IPS``**, the setting gunicorn reads
for the forwarded scheme (``gunicorn.conf.py``).  The two read one setting so
there is no second list to keep in step.  Only the address is taken here:
gunicorn already owns the scheme from that same list, so a ``ProxyFix`` that
also took ``X-Forwarded-Proto`` would be a second answer to one question.

Why the RIGHTMOST ``X-Forwarded-For`` entry: nginx appends the client's address
to whatever the request arrived with (``$proxy_add_x_forwarded_for``), so the
rightmost entry is the one nginx wrote and every entry to its left is the
client's own to invent.
"""

import ipaddress
from collections.abc import Callable, Iterable

from flask_limiter.util import get_remote_address
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

type IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
type WsgiApp = Callable[
    [dict[str, object], Callable[..., object]], Iterable[bytes],
]

# What one connection is handed by nearly every network that speaks IPv6.  A
# host can answer from any of the 2^64 addresses under it, so counting a public
# IPv6 address alone would give one machine a fresh count for every request.
_IPV6_ONE_CLIENT_PREFIX = 64


def parse_trusted_networks(allow_list: str) -> tuple[IpNetwork, ...]:
    """Parse ``FORWARDED_ALLOW_IPS`` into networks, refusing a malformed entry.

    Empty means nobody is trusted, so each client counts as the socket's peer:
    a process run directly, and the dev container, which sets no list.
    Gunicorn's ``*`` is refused like any other entry that names
    no network: trusting every caller is the mistake this list exists to
    prevent, and failing at start is louder than believing every client's
    header.

    Args:
        allow_list: Comma-separated addresses and CIDRs.

    Returns:
        The networks named, in order.

    Raises:
        ValueError: When an entry is not an address or a CIDR with no host
            bits set.  The message names the entry.
    """
    networks = []
    for entry in (part.strip() for part in allow_list.split(",")):
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry))
        except ValueError as exc:
            raise ValueError(
                f"FORWARDED_ALLOW_IPS entry {entry!r} is not an address or a "
                f"CIDR with no host bits set: {exc}"
            ) from exc
    return tuple(networks)


def _parsed_address(address: str) -> IpAddress | None:
    """Return *address* parsed, an IPv4-mapped IPv6 address as its IPv4 self.

    Args:
        address: The text of an address.

    Returns:
        The address, or ``None`` when *address* is not one.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    if parsed.version == 6 and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def believe_forwarded_address_from(
    wsgi_app: WsgiApp, trusted_networks: tuple[IpNetwork, ...],
) -> WsgiApp:
    """Wrap *wsgi_app* so a trusted caller's forwarded address becomes the client's.

    A request whose direct caller is in *trusted_networks* gets ``REMOTE_ADDR``
    from the rightmost ``X-Forwarded-For`` entry.  Any other caller keeps its
    own address and its header is ignored, so anything that reaches the app's
    port without going through the proxy cannot claim another address.

    Args:
        wsgi_app: The application to wrap.
        trusted_networks: What :func:`parse_trusted_networks` returned.

    Returns:
        The wrapped application.
    """
    through_the_proxy = ProxyFix(wsgi_app, x_for=1, x_proto=0)

    def choose_by_direct_caller(
        environ: dict[str, object], start_response: Callable[..., object],
    ) -> Iterable[bytes]:
        """Send the request through the address fix only for a trusted caller."""
        caller = _parsed_address(str(environ.get("REMOTE_ADDR", "")))
        if caller is not None and any(
            caller in network for network in trusted_networks
        ):
            return through_the_proxy(environ, start_response)
        return wsgi_app(environ, start_response)

    return choose_by_direct_caller


def _counted_client(address: str) -> str:
    """Return what a rate limit counts *address* as.

    A public IPv6 address counts by its /64 (see
    :data:`_IPV6_ONE_CLIENT_PREFIX`).  A private one counts alone: a tailnet's
    devices, for one, share their leading bits and are still separate phones.
    What is not an address counts as the text it is, because a key function
    that raised would turn every request from that caller into a 500.

    Args:
        address: The request's client address.

    Returns:
        The address, or the /64 holding it.
    """
    parsed = _parsed_address(address)
    if parsed is None:
        return address
    if parsed.version == 6 and parsed.is_global:
        return str(
            ipaddress.IPv6Network(
                (parsed, _IPV6_ONE_CLIENT_PREFIX), strict=False,
            )
        )
    return str(parsed)


def rate_limit_key() -> str:
    """Return what the current request's rate limits count against.

    A signed-in request counts against its account, so nobody else's traffic
    can spend that account's budget.  Anyone else counts against their address,
    which includes a signed-out visitor to a gated page: the default limits run
    before the login gate, so they spend their own count for that page and are
    then sent to sign in (ruling plan#3009).  A route's own ``@limiter.limit``
    runs inside its view, after the gate, so the one gated route that has one,
    ``/reauth``, sends a signed-out visitor to sign in uncounted.

    ``current_user`` costs no statement here: the request-id hook has already
    resolved it by the time any limit runs.

    Returns:
        ``user:<id>`` or ``ip:<client>``.
    """
    if current_user.is_authenticated:
        return f"user:{current_user.id}"
    return f"ip:{_counted_client(get_remote_address())}"
