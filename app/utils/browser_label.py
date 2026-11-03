"""
Shekel Budget App -- a browser's name, read from its User-Agent.

Settings > Security names each trusted device by the browser it was trusted
on (plan#2173, ruling plan#2406).  The record keeps the raw User-Agent header
(``auth.trusted_devices.user_agent``), which is a string like
``Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0``
and names nothing a person would recognise.  This module turns it into
``Firefox on Linux``.

**Presentation only.**  The label decides nothing: trust is the token's, and
a browser may send any User-Agent it likes.  So the reading is deliberately
shallow, a first-match scan of known tokens with no versions, and a header it
does not recognise reads ``Unknown browser`` rather than being guessed at.

**The order of each table is the rule.**  User-Agents claim their ancestors'
names: Edge's carries ``Chrome/`` and ``Safari/``, Opera's and Samsung
Internet's carry ``Chrome/``, every Chromium browser's carries ``Safari/``, an
Android one carries ``Linux`` and an iPhone's carries ``Mac OS X``.  So each
table lists the more specific name first, and the first match wins.
"""

#: Each browser's name and the tokens its User-Agent carries, most specific
#: first (see the module docstring for why the order is the rule).
_BROWSERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Edge", ("Edg/", "EdgA/", "EdgiOS/")),
    ("Opera", ("OPR/",)),
    ("Samsung Internet", ("SamsungBrowser/",)),
    ("Firefox", ("Firefox/", "FxiOS/")),
    ("Chrome", ("Chrome/", "CriOS/")),
    ("Safari", ("Safari/",)),
)

#: Each operating system's name and the tokens a User-Agent carries for it,
#: most specific first.
_SYSTEMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("iPhone", ("iPhone",)),
    ("iPad", ("iPad",)),
    ("Android", ("Android",)),
    ("ChromeOS", ("CrOS",)),
    ("Windows", ("Windows",)),
    ("Mac", ("Macintosh",)),
    ("Linux", ("Linux",)),
)

#: What a User-Agent naming no browser in :data:`_BROWSERS` reads as.
_UNKNOWN_BROWSER = "Unknown browser"


def _first_match(
    user_agent: str, table: tuple[tuple[str, tuple[str, ...]], ...],
) -> str | None:
    """Return the first name in *table* one of whose tokens *user_agent* holds.

    Args:
        user_agent: A User-Agent header, possibly empty.
        table: ``(name, tokens)`` pairs, most specific first.

    Returns:
        The name, or ``None`` when no token is in *user_agent*.
    """
    for name, tokens in table:
        if any(token in user_agent for token in tokens):
            return name
    return None


def browser_label(user_agent: str) -> str:
    """Return a person's name for the browser *user_agent* describes.

    Args:
        user_agent: The User-Agent header as stored, ``""`` when the browser
            sent none.

    Returns:
        ``"<browser> on <system>"`` (``"Firefox on Linux"``), the browser alone
        when no system is recognised, and ``"Unknown browser"`` in place of
        the browser when none is (``"Unknown browser on Windows"``).
    """
    browser = _first_match(user_agent, _BROWSERS) or _UNKNOWN_BROWSER
    system = _first_match(user_agent, _SYSTEMS)
    if system is None:
        return browser
    return f"{browser} on {system}"
