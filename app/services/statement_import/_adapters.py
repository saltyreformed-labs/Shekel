"""The SOURCE ADAPTER registry -- ruling **R-FP**'s seam, in one table.

*A statement importer is a SOURCE ADAPTER over one normalized line shape*, so
everything downstream of this module -- verification, identity, recording, and
in later leaves the matching and its review -- is source-independent.  Adding a
way for a statement to arrive is a parser plus one row here plus one
:class:`~app.enums.StatementSourceEnum` member; it is never a branch in the
importer.

**One adapter today, and that is a scope decision rather than an oversight.**
SECU's checking CSV is the only format the developer's own accounts need for
this leaf: the Capital One card export has no Shekel account to be imported
into until the credit-card arc creates one, and ``X-f6b``'s automated source is
its own step.  What this module buys with one entry is that the second one
costs nothing structural -- which is the whole of R-FP.
"""

from __future__ import annotations

from app.enums import StatementSourceEnum

from . import _secu_csv
from ._errors import StatementParseError
from ._line import ParsedStatement

#: Which parser reads which source.  A ``dict`` keyed by the ENUM MEMBER rather
#: than by the ref-table id, because this mapping is about code and a ref id is
#: a database fact -- and because a member that gains no entry here fails at the
#: lookup below with a message naming it, rather than resolving to some
#: default parser that would read the wrong file happily.
_PARSERS = {
    StatementSourceEnum.SECU_CHECKING_CSV: _secu_csv.parse,
}


def supported_sources() -> "list[StatementSourceEnum]":
    """Return the sources an import can actually be performed from.

    What the upload form offers.  Derived from :data:`_PARSERS` rather than
    listed a second time, so a source whose ref row exists but whose parser
    does not cannot be offered -- the form and the importer answer "which
    sources work" from one place.

    Returns:
        The supported members, in enum declaration order.
    """
    return [member for member in StatementSourceEnum if member in _PARSERS]


def parse_statement(
    source: StatementSourceEnum, payload: bytes,
) -> ParsedStatement:
    """Return what *payload* says, read by *source*'s adapter.

    Args:
        source: Which adapter to read the bytes with.  The USER states this on
            the upload form; it is not sniffed from the content.  Guessing the
            format would mean guessing which account a file is for whenever two
            institutions' exports look alike, and ruling R-FP makes that
            mapping a fact rather than a guess.
        payload: The uploaded file's raw bytes.

    Returns:
        The :class:`~._line.ParsedStatement` -- what the file calls its own
        account, its lines in CHRONOLOGICAL order, and the per-FILE facts it
        states about itself.  **One value rather than a widening tuple**: a
        source states more than two things (SECU opens with a ``Balance as of``
        header), and every reader was positional at the moment that stopped
        being obvious.

    Raises:
        StatementParseError: When *source* has no parser, or when its parser
            refuses the payload.
    """
    parser = _PARSERS.get(source)
    if parser is None:
        raise StatementParseError(
            f"There is no importer for {source.value!r} yet.  Nothing was "
            f"imported."
        )
    return parser(payload)
