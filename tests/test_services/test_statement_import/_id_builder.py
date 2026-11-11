"""Stage a bank source's own id for a recorded line, the way the record door does.

Plan step plan#2232: the id lives once, in ``budget.line_source_ids``, not on
a sighting.  A case whose subject is an id the app already holds arranges it
here, beside the rows :mod:`tests.test_services.test_statement_match._builders`
stages, rather than in that shared module (already past 1,000 lines).
"""

from app.extensions import db
from app.models.line_source_id import LineSourceId


def an_id(line, source_id, external_id):
    """Stage and return *source_id*'s id *external_id* for *line*.

    Args:
        line: The recorded :class:`~app.models.statement_import.BankStatementLine`.
        source_id: The ``ref.statement_sources`` id whose id this is.
        external_id: What that source calls the line.

    Returns:
        The flushed :class:`~app.models.line_source_id.LineSourceId`.
    """
    row = LineSourceId(
        account_id=line.account_id, source_id=source_id,
        external_id=external_id, line_id=line.id,
    )
    db.session.add(row)
    db.session.flush()
    return row
