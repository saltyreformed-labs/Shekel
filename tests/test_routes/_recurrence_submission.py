"""The recurrence submission a schema hands a route helper, for suites that drive one directly.

Its callers are the route-helper suites under ``tests/test_routes/``
(``test_recurrence_form_helpers/`` and ``test_recurrence_form_derived_stop.py``),
so it lives beside them rather than in ``tests/_test_helpers.py``, which stood
past the 1,000-line ceiling (10,083 lines on 2026-11-10) and takes no new lines
(rulings R-IR, R-BAL301).  Plan step R7e added it there; task plan#3414 moved
it here, with its import at module scope.  That module defers its app imports
because some of its importers run before the Flask app exists; the route
suites this one serves import app modules at module scope themselves.

The payload it reads is :func:`~tests._test_helpers.validated_cadence`'s shape.
"""
from app.schemas.validation._recurrence_submission import (
    RECURRENCE_END_BOUND_KEY,
    consume_recurrence_submission,
)


def submission_from(data, end_bound=None):
    """Return the submission a recurrence schema hands a route for *data*.

    Plan step R7e moved what a recurrence form STATES out of the payload's
    keys and into one :class:`~app.services.recurrence.RecurrenceSubmission`,
    which the schema's ``@post_load`` composes through
    :func:`~app.schemas.validation._recurrence_submission.consume_recurrence_submission`.
    A test that drives a route helper directly with a post-``load()`` payload
    (:func:`~tests._test_helpers.validated_cadence`'s shape) reaches the value
    through that SAME consumer, so the wire meaning of each field keeps one
    statement and this helper restates none of it.

    **It CONSUMES *data*'s recurrence keys, as the schema does**: every
    recurrence key leaves the dict and everything else stays, so a test's
    ``data == {...}`` assertion still grades "no recurrence key reaches the
    caller's model constructor" -- the contract the route helpers' own pops
    held until R7e and the consumer holds now.

    Args:
        data: A post-``load()`` payload, mutated in place.
        end_bound: The composed closing bound the form stated, or ``None``
            for one it did not mention.  A separate argument because the
            route helpers took it apart from the payload until R7e (the
            context's ``end_bound``); it is placed where the schema's bound
            composer leaves it before the consumer reads it.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSubmission`.
    """
    if end_bound is not None:
        data[RECURRENCE_END_BOUND_KEY] = end_bound
    return consume_recurrence_submission(data)
