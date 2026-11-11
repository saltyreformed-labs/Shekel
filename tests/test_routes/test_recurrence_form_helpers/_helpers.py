"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_recurrence_form_helpers.py``, moved verbatim by ``tools/split``.
"""

from types import SimpleNamespace

from app.services.pay_calendar import calendar_for
from app.services.recurrence import recurrence_spec, resolve


class _Definition(SimpleNamespace):
    """A definition's stand-in, answering ``recurs`` off its own rule.

    The refusals and the dispatcher read ``recurs`` since plan step
    ``balance:X-ci-1`` -- the one accessor for "does this definition repeat",
    a ``DerivedFlag`` over ``recurrence_rule`` on all three definition models
    -- so a stand-in carrying a bare ``recurrence_rule`` no longer answers
    what they ask.  A PROPERTY rather than a frozen attribute, because the
    dispatcher re-points and clears the rule it is handed and a value fixed at
    construction would answer for the rule the stand-in used to hold.
    """

    @property
    def recurs(self):
        return self.recurrence_rule is not None


def _phase_of(rule):
    """Return the cycle phase *rule* resolves to.

    **Read through the RESOLVER since plan step R7c-c**, which dropped
    ``budget.recurrence_rules.offset_periods``.  Every case below asserted the
    column; the phase is derived from the rule's own first occurrence on every
    read now, which is what makes defect **D1**'s stale-phase shape
    unconstructible rather than guarded -- so the resolved answer is both what
    the cases meant and the only thing left to read.

    Args:
        rule: The rule to resolve.

    Returns:
        int: Its ``offset_periods`` phase.
    """
    return resolve(
        recurrence_spec(rule), calendar_for(rule.user_id),
    ).offset_periods


def _phase_of_spec(spec):
    """Return the cycle phase *spec* resolves to.

    :func:`_phase_of`'s sibling for the cases that read what the FORM
    authored rather than what a row stored -- which is what
    :meth:`~app.services.recurrence.RecurrenceSubmission.authored` answers
    (``recurrence_spec_from_form`` until plan step R7e), since plan step R-F6
    split reading a submission from writing it.  Same producer, same question,
    one hop earlier.

    Args:
        spec: The :class:`~app.services.recurrence.RecurrenceSpec` to resolve.

    Returns:
        int: Its ``offset_periods`` phase.
    """
    return resolve(spec, calendar_for(spec.user_id)).offset_periods
