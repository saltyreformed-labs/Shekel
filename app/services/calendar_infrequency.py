"""Shekel Budget App -- Is this calendar flow an INFREQUENT one?

The analytics calendar asterisks a day cell holding a definition that repeats
less often than monthly.  Plan step **R7a-2b** made that a DERIVATION over the
two-axis ``(interval_n, unit)`` reading, where it was a hand-enumerated set of
three pattern names living in ``calendar_service``.

**Its own module because it is the one part of the calendar that is about RECURRENCE
rather than about calendars**, and because ``calendar_service`` sits at 857 of pylint's
1,000-line ceiling (measured 2026-09-01; it read 891 undated, and the module has moved
twice since): the derivation needs its reasoning written down, and a cap is a forcing
function rather than a ceiling to raise (the planning conventions' rule 4).  Two
facts moved here with it -- what
"infrequent" MEANS, and when the owner's pay cadence has to be resolved to
answer -- and neither has a second home.

Pure apart from :func:`badge_cadence`, which is the one function here that
reads the database, and reads only the owner's cadence.
"""
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.services.balance_at import EstimatedFlow
from app.services.pay_calendar import PayCadence, PayCalendarError, cadence_for
from app.services.recurrence import cadence_of
from app.services.transfer_legs import PlanItem
from app.utils.money import MONTHS_PER_YEAR

# What "infrequent" MEANS: a definition that fires less often than once a
# month.
#
# **DERIVED since plan step R7a-2b**, where this was a hand-enumerated
# ``frozenset`` of Quarterly, Semi-Annual and Annual.  It reproduces those
# three (4, 2 and 1 a year) and **flips two shapes a user can author TODAY**:
# ``Every N Periods`` at ``interval_n >= 3`` (26/3 = 8.67 a year biweekly --
# less often than quarterly, and badged frequent), and ``Every Period`` for an
# owner paid every 32 days or less often (11 a year).  It is per-OWNER too,
# which an enumeration of pattern NAMES could not be: ``Every 2 Periods`` is 13
# a year biweekly and 6 monthly-paid.
_INFREQUENT_BELOW_PER_YEAR = MONTHS_PER_YEAR


def badge_cadence(
    user_id: int,
    transactions: list[PlanItem],
    estimated: "list[EstimatedFlow]",
) -> PayCadence | None:
    """Return the owner's pay cadence, or ``None`` when nothing can be badged.

    The infrequent badge is per-OWNER since plan step R7a-2b, so the build
    needs the owner's cadence -- but only if there is a REPEATING row to badge,
    and a page must not fail for a fact it does not use.  The test is on the
    recurrence rule rather than on the transaction list: a month of purely
    manual entries reads no cadence, and an earlier draft testing
    ``if not transactions`` would have resolved one for them.

    **The absence is provably not a missing value.** ``budget.transactions``
    carries ``pay_period_id NOT NULL``, so a transaction implies a pay period,
    which since plan step C4-b-2 implies a ``budget.pay_schedule`` row
    (``fk_pay_periods_schedule``) and therefore a stored cadence.  *That second
    link used to be "or by inferring from that period's own length", the arm
    findings **P8** and **P35** were about; the key replaced an inference with
    a guarantee.*  An owner with
    no transactions therefore cannot reach :func:`is_infrequent` at all, and
    is the only owner for whom this answers ``None``.  The type says
    "unreadable BECAUSE unread" rather than "unknown".  An ESTIMATED
    occurrence (plan step ``pay_calendar:C9``, task plan#3945) repeats as its
    row would, and sits on a paycheck past the last saved one, so its owner
    has a saved period and the same guarantee.

    Args:
        user_id: The owner whose calendar is being built.
        transactions: The items this build will badge -- rows, and since
            leaf ``balance:X-bi-6-1b`` transfer legs, which never repeat
            (``TransferLeg.recurs`` is ``False``: a transfer's rule is its
            own definition's, not a transaction rule).
        estimated: The estimated occurrences this build will badge past the
            last saved paycheck (:func:`estimate_is_infrequent`).

    Returns:
        The owner's :class:`~app.services.pay_calendar.PayCadence`, or ``None``
        when nothing in this build repeats.

    Raises:
        PayCalendarError: The owner has a repeating row and no resolvable
            cadence.  *Its one named route was plan finding **P8** -- a
            schedule-row-less owner's cadence INFERRED from their last period's
            stored length, which nothing bounded above -- and plan step C4-b-2
            closed it: ``fk_pay_periods_schedule`` forbids that owner and the
            inference is deleted, so a repeating row's NOT NULL
            ``pay_period_id`` now guarantees a schedule row behind it as well
            as a period.*  Declared still, because this function RESOLVES the
            cadence itself (:func:`~app.services.pay_calendar.cadence_for`
            below) rather than reading one a caller hands it -- so the refusal
            is its own to make and belongs in its own signature.
    """
    if not any(txn.recurs for txn in transactions) and not any(
        _estimate_recurs(flow) for flow in estimated
    ):
        return None
    return cadence_for(user_id)


def is_infrequent(
    txn: PlanItem, pay_cadence: PayCadence | None,
) -> bool:
    """Check whether a transaction's recurrence fires less often than monthly.

    True when the definition's cadence fires fewer than
    :data:`_INFREQUENT_BELOW_PER_YEAR` times a year -- the three the retired
    enumeration listed, and the two shapes of today's closed set it got wrong;
    see that constant's comment.  False for a transaction that does not
    ``recurs`` -- no template, or a template with no recurrence rule, the
    last of which is how a non-recurring definition is modelled (plan step
    R2e-3); the one accessor since plan step balance:X-bi-7a, where this
    function and :func:`badge_cadence` each spelled it inline.

    Args:
        txn: The row -- or transfer leg, which answers ``False`` at the
            ``recurs`` gate -- whose definition is being classified.
        pay_cadence: How often the owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`), or ``None`` when
            this build had nothing to badge (:func:`badge_cadence`).  Read
            only for a paycheck-space cadence, which is the one kind whose
            frequency is a property of the owner rather than of the calendar.

    Returns:
        Whether the badge applies.

    Raises:
        RecurrenceResolutionError: The rule names a pattern this application
            does not model.  **NEW at plan step R7a-2b**, where the enumerated
            set answered ``False`` for such a rule and the calendar rendered
            it as a frequent flow.  The same disposition every other reader of
            a stored rule has, and the same one the Recurring surface states
            (``recurring_view._build_section``): a badge computed from a
            cadence nobody can derive is a guess.  Reachable only through a
            rule naming the surviving ``Once`` ``ref`` row, which no write door
            can author (plan step R2e-2) and no production row uses.
        PayCalendarError: *pay_cadence* is ``None`` beside a repeating
            definition.  Structurally unreachable -- ``budget.transactions``
            carries ``pay_period_id NOT NULL``, so a transaction reaching here
            implies a pay period implies a resolvable cadence, and
            :func:`badge_cadence` answers ``None`` only for a build with no
            transactions at all.  Raised rather than badged either way: a
            silent ``False`` here would tell the user an annual bill is a
            frequent one.
    """
    if not txn.recurs:
        return False
    return _rule_is_infrequent(
        txn.template.recurrence_rule, pay_cadence, f"transaction {txn.id}",
    )


def estimate_is_infrequent(
    flow: EstimatedFlow, pay_cadence: PayCadence | None,
) -> bool:
    """Check whether an estimated occurrence's definition fires less often than monthly.

    The badge :func:`is_infrequent` gives the row the occurrence would become
    (plan step ``pay_calendar:C9``, task plan#3945): a transaction template's
    by its rule, and a transfer template's never, as its leg never repeats.

    Args:
        flow: The estimated occurrence past the last saved paycheck.
        pay_cadence: How often the owner is paid, or ``None`` when this build
            had nothing to badge (:func:`badge_cadence`).

    Returns:
        Whether the badge applies.

    Raises:
        RecurrenceResolutionError: As :func:`is_infrequent`.
        PayCalendarError: As :func:`is_infrequent`.
    """
    if not _estimate_recurs(flow):
        return False
    return _rule_is_infrequent(
        flow.template.recurrence_rule, pay_cadence,
        f"template {flow.template.id}'s estimated occurrence",
    )


def _estimate_recurs(flow: EstimatedFlow) -> bool:
    """Return whether the row an estimated occurrence would become repeats.

    A transaction template's row does: the tier walks only templates carrying
    a recurrence rule.  A transfer template's is drawn as a LEG, which never
    repeats (``TransferLeg.recurs``).

    Args:
        flow: The estimated occurrence.

    Returns:
        ``True`` for a transaction template's occurrence.
    """
    return isinstance(flow.template, TransactionTemplate)


def _rule_is_infrequent(
    rule: RecurrenceRule, pay_cadence: PayCadence | None, subject: str,
) -> bool:
    """Return whether *rule* fires fewer than :data:`_INFREQUENT_BELOW_PER_YEAR` times a year.

    The one statement of the badge, for a row's definition and an estimated
    occurrence's alike.

    Args:
        rule: The definition's recurrence rule.
        pay_cadence: How often the owner is paid, or ``None``.
        subject: What carries the rule, for the refusal's message.

    Returns:
        Whether the badge applies.

    Raises:
        RecurrenceResolutionError: The rule names a pattern this application
            does not model.
        PayCalendarError: *pay_cadence* is ``None``.
    """
    if pay_cadence is None:
        raise PayCalendarError(
            f"{subject} has a recurrence rule but this calendar build "
            f"resolved no pay cadence.  A repeating item sits on a pay "
            f"period, so its owner has one and therefore a cadence; reaching "
            f"here means the build was told it had nothing to badge and then "
            f"found something."
        )
    cadence = cadence_of(rule)
    return cadence.occurrences_per_year(pay_cadence) < (
        _INFREQUENT_BELOW_PER_YEAR
    )


__all__ = ["badge_cadence", "estimate_is_infrequent", "is_infrequent"]
