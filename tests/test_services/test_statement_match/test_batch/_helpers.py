"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_match/test_batch.py``, moved verbatim by ``tools/split``.
"""

from app.services import statement_match
from app.services.statement_match import PurchaseCreation, Consent, ReviewedBatch

from tests.test_services.test_statement_match._builders import a_scope, a_submission


def _batch(seed_user, matches=(), creations=(), incomes=(), skips=()):
    """Apply one reviewed pass over the seeded account.

    Args:
        seed_user: The seeded user bundle.
        matches: :class:`~app.services.statement_match.MatchSubmission` values.
        creations: :class:`PurchaseCreation` values.
        incomes: :class:`~app.services.statement_match.IncomeCreation` values
            (ruling **bank_import:R-GW**).
        skips: :class:`~app.services.statement_match.SkipRequest` values
            (ruling **bank_import:R-JG**, plan step ``bank_import:X-gj-4b``).

    Returns:
        The :class:`~app.services.statement_match.BatchOutcome`.
    """
    return statement_match.apply_reviewed(
        ReviewedBatch(
            consent=Consent.TICKED,
            matches=tuple(matches),
            creations=tuple(creations),
            incomes=tuple(incomes),
            skips=tuple(skips),
        ),
        # DERIVED HERE, so every pass sees the rows this test has staged.  The
        # ROUTE is what builds one in the app; a door that built its own would
        # force its caller to derive a second time for the refusal render.
        a_scope(seed_user),
    )


def _match(seed_user, lines=(), transactions=(), entries=(), residual=None):
    """Return one match item naming exactly these subjects.

    **The reviewed state comes off a freshly derived pass**, exactly as the
    screen's would (plan step ``bank_import:X-f6d-3``): a batch item carries
    the figure and revision the owner was looking at, and the door refuses one
    whose row has moved since.  Deriving here rather than taking the caller's
    scope keeps the helper's contract the same as it was -- a case that wants
    a STALE item builds one deliberately, and several in this package do.
    """
    return a_submission(
        a_scope(seed_user),
        lines=lines, transactions=transactions, entries=entries,
        residual=residual,
    )


def _creation(seed_user, line, *, transaction_id=None, new_envelope=None):
    """Return one creation item for *line*."""
    return PurchaseCreation(
        line_id=line.id,
        transaction_id=transaction_id,
        new_envelope=new_envelope,
    )
