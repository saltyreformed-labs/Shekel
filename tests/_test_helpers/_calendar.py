"""The owner's pay calendar: opening it, recording paydays across a hole, rebuilding it, and
counting the pay periods a block hydrates.
"""

from contextlib import contextmanager
from datetime import date as _real_date

from app.services import pay_era_write, pay_schedule_service

from ._users_and_eras import rhythm_of
from ._anchors import _governing_opening_day
from ._read_passes import all_periods


def open_owner_calendar(user_id, first_payday, num_periods=1, cadence_days=14):
    """Open *user_id*'s pay calendar through the writer that owns the table.

    **Plan step ``pay_calendar:C4-b-1``.**  For a test that hand-builds a
    ``User`` and needs a pay period before ``account_service.create_account``,
    which refuses an owner who has none.  The sites this replaced each carried
    the same five lines -- a bare ``PayPeriod(...)`` setting ``end_date`` and
    ``period_index``, the two values ``pay_period_write._write_derivation``
    DERIVES, and no ``budget.pay_schedule`` row beside them.  *No count is
    stated here: two drafts of this docstring and the plan's own sentence gave
    three different ones, which is what a number nothing recomputes does.*

    That pairing is one no application door can produce: ``record_paydays``
    upserts the owner's cadence in the same call that records a payday (the
    cadence rule, plan step C3-b), and ``registration_service.register_user`` reaches
    the table only through it.  So every one of those sites built the single
    owner shape production does not have -- pay-calendar finding **P8** -- and
    the derived columns they typed were free to disagree with the derivation
    that reads them.

    Use :func:`rebuild_calendar` instead when the owner ALREADY has periods:
    this writes, that resets.

    Args:
        user_id: The owning user's id, already flushed.
        first_payday: The opening payday.
        num_periods: How many periods to record from it, default one.
        cadence_days: Days between them, persisted as the owner's cadence.

    Returns:
        The created :class:`~app.models.pay_period.PayPeriod` rows, payday
        ascending -- ``period_index`` 0..n-1 and each end derived, so a caller
        that wants one period may index ``[0]``.  **EMPTY when every requested
        payday is already on the table**, which is ``record_paydays``' own
        contract for a batch that records nothing: an ``[0]`` on that answer is
        an ``IndexError``.  No caller re-requests an existing payday today, and
        the day one does it should read the answer rather than index it.
    """
    from app.services import (  # pylint: disable=import-outside-toplevel
        pay_period_write,
    )

    return pay_period_write.record_paydays(
        user_id=user_id,
        first_payday=first_payday,
        num_periods=num_periods,
        rhythm=rhythm_of(cadence_days),
    )


def record_paydays_across_a_hole(user_id, first_payday, num_periods, rhythm):
    """Record a batch the writer REFUSES for skipping a paycheck of the plan.

    **Plan step ``pay_calendar:C17-c-2a`` (rulings R-PC67, R-PC76).**  A
    batch whose first new payday falls at or past the SECOND payday the
    owner's plan projects after their record leaves a whole paycheck
    missing, and ``pay_period_write.record_paydays`` refuses it through
    ``pay_period_batch.reject_skipped_paycheck`` -- from every door, which is
    the point of asking it in the one writer.  So a hole is now a state no
    application door can write, and :func:`rebuild_calendar`'s predicate
    says where such a state is built: here, by hand, in one shared helper.

    **Who needs it.**  ``seed_user`` opens its calendar at
    :data:`~tests.conftest.SEED_USER_BOOTSTRAP_START` (2024-01-05) so the
    seeded account has a paycheck to open in, and a case that then wants a
    2026 block beside it -- two years of planned paychecks skipped -- was
    writing it through the writer until this step.  Every such case is about
    the 2026 rows, not the hole, and this keeps the rows, the ordinals and
    the eras exactly as the writer used to leave them.  A case that means to
    GRADE the refusal calls ``record_paydays`` and expects the
    ``ValidationError``; a fixture whose batch the writer would ACCEPT is
    refused here, so this helper cannot quietly become the default door.

    **It composes the writer's own producers in the writer's order and skips
    ONE refusal.**  The days are ``pay_period_batch.requested_paydays``'
    (displaced under the rhythm, as the writer records them); the era rule is
    the writer's (``pay_era_write.eras_describing`` / ``era_to_mint``), so a
    batch stating a rhythm the covering era does not hold mints an era at its
    first payday and one continuing the era mints none; the floor is asked of
    the same list the writer hands it (the minted era's first payday in
    front); the statements run in ``_apply``'s order (the schedule row, the
    retire, the mint, the insert) and the session is expired when an era was
    retired or minted.  What it does not ask is the ceiling, and it asserts
    that the ceiling WOULD have fired.  What it does NOT reproduce: the
    ``pay_periods_generated`` log event; the writer's own
    ``reject_undatable_payday``, ``reject_out_of_range_batch_size`` and
    ``reject_out_of_range_cadence`` (``mint_era`` re-asks the last for a
    minting batch); and ``reject_shift_on_short_cadence`` at the writer
    (``mint_era`` asks it too) -- a case grading any of them belongs on
    ``record_paydays``.

    Args:
        user_id: The owning user's id.
        first_payday: The batch's first NOMINAL payday.
        num_periods: How many paydays the batch covers, existing ones included.
        rhythm: The batch's :class:`~app.services.pay_rhythm.Rhythm`.

    Returns:
        The created :class:`~app.models.pay_period.PayPeriod` rows, payday
        ascending and flushed -- ``record_paydays``' own answer shape.

    Raises:
        ValidationError: The floor refuses the batch (a payday inside a
            paycheck the owner already has), exactly as the writer would.
        AssertionError: The writer would have ACCEPTED this batch -- the
            case should call ``record_paydays``.
    """
    from app.exceptions import ValidationError  # pylint: disable=import-outside-toplevel
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
    from app.services import pay_period_batch  # pylint: disable=import-outside-toplevel

    held = {period.start_date for period in all_periods(user_id)}
    requested = pay_period_batch.requested_paydays(first_payday, num_periods, rhythm)
    new_paydays = [payday for payday in requested if payday not in held]
    stored = pay_schedule_service.resolve_schedule(user_id)
    standing = pay_era_write.eras_describing(stored, held)
    era = None
    if new_paydays:
        era = pay_era_write.era_to_mint(standing, first_payday, rhythm)
    pay_period_batch.reject_backward_payday(
        held, new_paydays if era is None else [requested[0], *new_paydays],
        None if stored is None else stored.eras,
    )
    try:
        pay_period_batch.reject_skipped_paycheck(held, new_paydays, standing)
    except ValidationError:
        pass
    else:
        raise AssertionError(
            f"record_paydays would accept a batch from {first_payday} x "
            f"{num_periods} paid {rhythm.cadence.phrase} for user {user_id}; "
            f"this helper is only for a batch that skips a paycheck of the "
            f"plan.  Call pay_period_write.record_paydays instead."
        )
    pay_schedule_service.ensure_schedule_row(user_id)
    retired = pay_era_write.retire_eras(
        user_id, tuple(e.effective_from for e in standing),
    )
    if era is not None:
        pay_era_write.mint_era(user_id, era)
    created = [PayPeriod(user_id=user_id, start_date=payday) for payday in new_paydays]
    db.session.add_all(created)
    db.session.flush()
    if retired or era is not None:
        db.session.expire_all()
    return created


def rebuild_calendar(user_id, first_payday, num_periods, cadence_days):
    """Rebuild *user_id*'s WHOLE schedule on a fixed-days rhythm, through the reset door.

    :func:`rebuild_calendar_on`'s fixed-days spelling, which is what every
    caller before plan step ``pay_calendar:C17-d-2`` meant by a cadence;
    that function carries the argument and the books bound.

    Args:
        user_id: The owning user's id.
        first_payday: The opening payday of the rebuilt schedule.
        num_periods: How many periods to build from it.
        cadence_days: Days between them, persisted as the owner's cadence by
            the writer (the cadence rule, plan step C3-b).

    Returns:
        :func:`rebuild_calendar_on`'s answer.
    """
    return rebuild_calendar_on(
        user_id, first_payday, num_periods, rhythm_of(cadence_days),
    )


def rebuild_calendar_on(user_id, first_payday, num_periods, rhythm):
    """Rebuild *user_id*'s WHOLE pay-period schedule through the reset door.

    **Plan step ``pay_calendar:C4-b-1``.**  The one place the test tree says
    "give this owner that calendar", and it says it by calling
    ``pay_period_admin.reset_pay_periods`` -- the settings-page correction at
    ``POST /pay-periods/reset``, which wipes every period, records the new
    batch through the writer and re-syncs the loan genesis postings and the
    account anchor corrections onto what it built.

    **A test that builds a pay period by hand builds a row no owner can
    have**, and that is not a style point.  ``end_date`` and ``period_index``
    are DERIVED from the payday set; a hand-built row sets them to whatever
    the author typed, and it wrote no ``budget.pay_schedule`` row, so
    ``pay_schedule_service.resolve_schedule`` fell back to INFERRING the
    cadence from that same hand-typed ``end_date``.  Eight cases in
    ``test_recurrence_engine.test_dates.TestDueDateGeneration`` passed only through that
    loop: they wrote a 28-day February period, the fallback read 28 back as
    the owner's cadence, and the derived span therefore matched the typed one.
    Give the owner a real cadence and the same rows derive a 14-day span and
    generate nothing.  Ruling **P54**'s shape: one shared helper here, never a
    ``for_test`` door on the real API.

    **The LOOP is now unbuildable and the reason to come here is stronger for
    it** (plan step ``pay_calendar:C4-b-2``).  ``fk_pay_periods_schedule``
    refuses a pay period whose owner has no ``budget.pay_schedule`` row, so a
    hand-built row for a schedule-less owner is an ``IntegrityError`` rather
    than a silently self-confirming assertion.

    **What that does NOT do is make every remaining hand-built site wrong, and
    this paragraph states a PREDICATE rather than a list** -- an adversarial
    review measured a first draft's list as a set defined by subtraction,
    naming two categories where there are at least three.  The predicate: a
    hand-built ``PayPeriod(...)`` is legitimate exactly when the row it needs
    is one no application door can write -- a corrupt ordinal, a gap, an
    overlapping span (take ``bare_user_with_cadence``) -- or when nothing
    reaches a database at all.  A site that merely finds it convenient is one
    this helper should have.  ``grep -rn 'PayPeriod($' tests/`` answered 38
    across 19 files on 2026-09-01, and the third category the draft missed is
    real: rows hand-built for an owner who already carries a schedule row from
    a fixture, which the key admits and which nothing here classifies.  Re-run
    the grep; do not trust the number.

    Args:
        user_id: The owning user's id.
        first_payday: The opening payday of the rebuilt schedule, a day on
            *rhythm*'s grid.
        num_periods: How many periods to build from it.
        rhythm: The :class:`~app.services.pay_rhythm.Rhythm` the schedule
            runs on, persisted as the owner's era by the writer.  A
            day-of-month cadence here (plan step ``pay_calendar:C17-d-2``)
            is what lets the cross-page fixtures state a calendar-monthly
            schedule through the door rather than by hand (ledger row
            **P78**).

    Returns:
        The owner's periods, payday ascending -- :func:`all_periods`' answer,
        for the reason that function gives.

    Raises:
        PayPeriodResetBlocked: The owner has a settled transaction, which the
            reset door refuses; build the calendar before settling anything.
    """
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.account import Account  # pylint: disable=import-outside-toplevel
    from app.services import (  # pylint: disable=import-outside-toplevel
        pay_period_admin,
    )

    # **The BOOKS bound the opening payday, and since plan step
    # ``pay_calendar:C4-b-1`` nothing else does.**  Two accidental protections
    # went when ``conftest._drop_seed_user_bootstrap`` did, and an adversarial
    # review of that step found both: the hand-rolled version APPENDED beside
    # the owner's existing paydays, so ``reject_backward_payday`` refused any
    # first payday earlier than one cadence after the latest -- and where that
    # let something through, a backward-only restatement moved the books to
    # meet it.  The reset door retires every surviving payday in the SAME call
    # that records the new batch, so that refusal returns early on an empty
    # surviving set and every opening day became legal.
    #
    # A pay period opening at or before an account's books contradicts ruling
    # ``balance:R-HG`` -- an opening equity is the CLOSING balance for its own
    # day -- so it is refused here, loudly, rather than built and left for
    # whichever balance case notices.  **On this door rather than on one of its
    # callers**: a second adversarial review found the first placement was on
    # ``conftest._reset_seed_calendar``, which is one of three callers, and the
    # one a new case is least likely to use --
    # ``rebuild_calendar_from_spans`` reaches this directly and already types
    # arbitrary years.
    #
    # **Per account it is the GOVERNING row, not the latest DAY**, and the
    # difference is not academic: ``budget.account_openings`` is append-only
    # and the governing row is the one recorded LAST (ruling ``balance:R-HE``),
    # so an account restated BACKWARD carries a row with a later ``opened_on``
    # that no longer governs.  The seeded Checking is exactly that -- 2024-01-05
    # from the factory, then 2024-01-04 from
    # ``open_books_before_the_first_assertion`` -- and a first cut of this
    # guard reduced with ``max(opened_on)``, read 2024-01-05, and refused the
    # legal opening day.  ``test_it_admits_the_first_day_it_legally_can``, the
    # second direction of this guard's own control, is what measured it.
    books = max(
        (
            day for day in (
                _governing_opening_day(db.session, account)
                for account in db.session.query(Account)
                .filter(Account.user_id == user_id).all()
            )
            if day is not _real_date.max
        ),
        default=None,
    )
    assert books is None or first_payday > books, (
        f"user {user_id} has an account whose books open {books}, and this "
        f"asks for a calendar opening {first_payday}, which is on or before "
        f"them.  An opening equity is the CLOSING balance for its own day "
        f"(ruling balance:R-HG), so no pay period may open at or before one.  "
        f"Ask for a later first payday, or open that account's books earlier."
    )
    pay_period_admin.reset_pay_periods(
        user_id, first_payday, num_periods, rhythm,
    )
    # The door is one transaction its ROUTE commits, so a test caller commits
    # it, and expires: the caller may hold rows the wipe deleted.
    db.session.commit()
    db.session.expire_all()
    return all_periods(user_id)


def rebuild_calendar_from_spans(user_id, spans):
    """Rebuild *user_id*'s calendar so its periods OPEN on each span's start.

    :func:`rebuild_calendar` for a case that wants several periods the writer
    cannot space evenly -- "January, April, July and October", which is four
    paydays 90, 91 and 92 days apart.  One reset opens the schedule and each
    later payday is appended by the same writer, so every row is still the
    derivation's.

    **What a span's END is worth here, stated because it is NOT what a
    hand-built row gave.**  Every period's end is the day BEFORE the next
    payday, so an interior span's stated end is honoured only when the next
    span opens the day after it; where the caller asks for gapped spans the
    interior ends run WIDER than asked, because a gap is not expressible in a
    derived calendar (``docs/plans/implementation_plan_pay_calendar.md``
    section 3).  The LAST span is the one end the derivation projects, and it
    is exact: the owner's cadence is that span's length.  Callers here assert
    on the occurrence's own day, which lands in the same period under either
    width.

    Args:
        user_id: The owning user's id.
        spans: ``[(start, end), ...]``, ascending and non-overlapping, at
            least one.  Each ``start`` becomes a payday.

    Returns:
        The owner's periods, payday ascending -- one per span.

    **Every interior span is a HOLE the writer refuses** (plan step
    ``pay_calendar:C17-c-2a``, ruling R-PC67): "January, April, July and
    October" at a 31-day cadence skips two planned paychecks between each
    pair, so the appends go through :func:`record_paydays_across_a_hole`,
    the tree's one door for the state no application door writes.  Spans
    the writer WOULD accept belong in :func:`rebuild_calendar`; that helper
    says so by refusing them.

    Raises:
        ValidationError: Two spans open closer together than the last span's
            length, which is the forward-only rule
            ``pay_period_batch.reject_backward_payday`` states.
        AssertionError: Two spans open within one planned paycheck of each
            other, a batch the writer accepts -- use :func:`rebuild_calendar`.
    """
    from app.extensions import db  # pylint: disable=import-outside-toplevel

    last_start, last_end = spans[-1]
    cadence_days = (last_end - last_start).days + 1
    rebuild_calendar(user_id, spans[0][0], 1, cadence_days)
    for start, _end in spans[1:]:
        record_paydays_across_a_hole(
            user_id, start, 1, rhythm_of(cadence_days),
        )
    # COMMIT the appends too.  :func:`rebuild_calendar` commits the opening
    # payday because the door it wraps leaves the transaction to its caller,
    # and ``record_paydays`` only FLUSHES -- so without this the calendar was
    # half committed and half pending.  Nothing broke (a test client reuses the
    # live app context, so the same session sees both), but a half-committed
    # calendar is an asymmetry no reader would predict.
    db.session.commit()
    return all_periods(user_id)


@contextmanager
def pay_periods_hydrated():
    """Count the ``PayPeriod`` ORM entities LOADED inside this block.

    The measurement a statement count cannot make: an eager load rides inside
    another query as a JOIN and issues no statement of its own, and a
    ``db.session.get`` served from the identity map issues none either.  Both
    hydrate an entity, which is what this counts.

    **The event and not the identity map**, because the map holds WEAK
    references: a probe that counted survivors after the call read zero for a
    producer that had hydrated twelve, since nothing outside the producer
    holds a ``Transaction`` to keep its ``pay_period`` back-reference alive.
    That was measured -- a first cut of this helper read the map and could not
    fire.  ``loaded_as_persistent`` fires per load and cannot be collected
    away.

    **A caller must EXPUNGE first, and this is the one thing about the probe
    that is not obvious.**  ``loaded_as_persistent`` fires on a LOAD, so an
    instance already in the session's identity map is returned by
    ``db.session.get`` without firing anything: a fixture that hands back live
    ORM rows makes this read ZERO for a producer that is hydrating on every
    call, and the guard then passes on exactly the code it exists to refuse.
    Measured at plan step C2-f3e -- with ten periods preloaded, a
    ``db.session.get(PayPeriod, ...)`` inside this block records nothing.  An
    earlier wording of this paragraph claimed the probe counts such a call and
    it does not.

    Shared here rather than per-suite because two SUITES now assert the same
    structural property -- no ORM ``PayPeriod`` on this path.  Written for plan
    step C2-f3d (``test_spending_report_service``), moved here whole by plan
    step C2-f3e (``test_transaction_auth``); both are pay-calendar steps, and
    an earlier wording of this sentence said "two arcs".

    Yields:
        The list of hydrated :class:`~app.models.pay_period.PayPeriod`
        instances, appended to as the block runs.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.models.pay_period import PayPeriod

    loaded = []

    def _record(_session, instance):
        if isinstance(instance, PayPeriod):
            loaded.append(instance)

    event.listen(Session, "loaded_as_persistent", _record)
    try:
        yield loaded
    finally:
        event.remove(Session, "loaded_as_persistent", _record)
