"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_recurrence_form_helpers.py``, moved verbatim by ``tools/split``.
"""

from types import SimpleNamespace

from app.routes._recurrence_form_helpers import resolve_recurrence_rule_for_update
from app.routes._recurrence_form_refusals import RecurrenceFormContext
from app.routes._redirect_target import RedirectTarget
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.services.recurrence import recurrence_spec, resolve
from tests.test_routes._recurrence_submission import submission_from


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


def _update(rule, data, user_id):
    """Drive the UPDATE door once, the way the update routes do, and return its answer.

    ``resolve_recurrence_rule_for_update`` is the door ``update_template``,
    ``update_transfer_template`` and the payroll line's update take: it merges
    the submission onto the stored rule ONCE
    (:meth:`~app.services.recurrence.RecurrenceSubmission.applied_to`), hands
    that spec to the refusals, then to ``reauthor_rule``.  A case that called
    ``reauthor_rule(rule, submission.applied_to(rule), ...)`` itself restated
    that composition and graded none of it (an adversarial review of plan step
    R7e); this is the door, with a :class:`_Definition` standing in for the
    template.

    Args:
        rule: The stored :class:`RecurrenceRule`, re-pointed in place.
        data: A post-``load()`` payload (``validated_cadence``'s shape), its
            recurrence keys consumed into the submission.
        user_id: The owner the stand-in claims, and whose read pass is built.

    Returns:
        ``None`` when the rule was re-pointed, or the refusal's ``Response``.
    """
    return resolve_recurrence_rule_for_update(
        _Definition(recurrence_rule=rule, to_account_id=None, user_id=user_id),
        ctx=RecurrenceFormContext(
            submission=submission_from(data),
            redirect=RedirectTarget("templates.edit_template", {"template_id": 1}),
        ),
        pass_ctx=BalanceContext.build(user_id),
    )
