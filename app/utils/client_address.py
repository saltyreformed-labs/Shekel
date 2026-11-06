"""
Shekel Budget App -- Who is asking: the client's real address.

Gunicorn sets ``REMOTE_ADDR`` from the socket, so behind nginx every request
looks like nginx.  :func:`believe_forwarded_address_from` puts the client's
address there instead, for a caller on the one trusted list, so
``request.remote_addr`` is the client everywhere it is read: the rate limits
and every log line.

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

from werkzeug.middleware.proxy_fix import ProxyFix

type IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
type WsgiApp = Callable[
    [dict[str, object], Callable[..., object]], Iterable[bytes],
]


def parse_trusted_networks(allow_list: str) -> tuple[IpNetwork, ...]:
    """Parse ``FORWARDED_ALLOW_IPS`` into networks, refusing a malformed entry.

    Empty means nobody is trusted, which is every process with no proxy in
    front of it.  Gunicorn's ``*`` is refused like any other entry that names
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
