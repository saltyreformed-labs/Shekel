"""Read passes: the current pay period, a read pass over paydays or an account's owner, call
counters, a planted pricing basis, and what a test reads off the pay calendar.
"""

from contextlib import contextmanager

from ._users_and_eras import eras_of


def current_pay_period(user_id, as_of=None):
    """Return the ORM :class:`PayPeriod` row covering *as_of* for *user_id*.

    **The ONE place the suite asks "which paycheck is this owner in", and it
    asks the APPLICATION** (plan step C2-f3a).  It replaced
    ``pay_period_service.get_current_period``, which that step deleted after
    moving its three ``app/`` call sites onto
    :meth:`~app.services.pay_calendar.PayCalendar.period_containing` -- and the
    two defects it was deleted FOR are exactly the two a hand-rolled test
    helper would have reproduced.  Its ``.first()`` carried no ``ORDER BY``
    (ledger row **P19**), and it read the process clock rather than the
    owner's civil day (row **P49**).

    So this does not re-implement the search.  It runs the same derivation the
    application runs and then resolves the row, which is what keeps the suite
    from being able to disagree with the app about which period is current --
    a test that seeds state into "the current period" and a page that renders
    "the current period" must mean one period, or the assertion grades nothing.

    **It returns the ORM ROW deliberately**, where ``app/`` now holds
    :class:`~app.services.pay_calendar.DerivedPeriod` values.  A test needs a
    row because the factories take one (``make_investment_account``,
    ``create_loan_account``, every ``Transaction(pay_period_id=...)`` seed) and
    because ``tests/`` legitimately writes this table where ``app/`` may not
    (see ``pay_period_write``'s ``TestThereIsOneWriter``).  The identity comes
    from the derivation either way, so the row and the derived value name the
    same paycheck by construction rather than by two searches agreeing.

    Args:
        user_id: The owner whose schedule to search.
        as_of: The civil day to place.  Defaults to
            :func:`~app.utils.dates.display_today`, the owner's own day and
            the clock ``seed_periods_today`` builds its schedule around, so
            the default lands inside the seeded window rather than one UTC
            midnight past it.

    Returns:
        The covering :class:`~app.models.pay_period.PayPeriod`, or ``None``
        when no SAVED period covers *as_of* -- which is a real answer and the
        one three routes still branch on.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dependency
    # avoidance as the factories in ``_accounts.py`` and ``_loans.py``.
    from app.extensions import db as _db
    from app.models.pay_period import PayPeriod as _PayPeriod
    from app.services.pay_calendar import calendar_for
    from app.utils.dates import display_today

    period = calendar_for(user_id).period_containing(
        display_today() if as_of is None else as_of,
    )
    if period is None:
        return None
    return _db.session.get(_PayPeriod, period.period_id)


def read_pass_over_paydays(
    paydays, cadence_days, as_of, user_id=1, history_opens_on=None,
):
    """Return a :class:`BalanceContext` whose pay calendar is *paydays*.

    **The ONE place a test seeds the read pass's pay-calendar memo**, and that
    is the whole point of it existing (plan finding **P54**, ruled by the
    developer 2026-08-16).

    :class:`~app.services.balance_at.BalanceContext` derives the owner's
    calendar lazily into a field its own module docstring declares PRIVATE,
    because that module owns the derivation.  A unit test with no database
    cannot let it derive -- ``calendar_for`` would query -- so it must seed the
    memo, which means naming that private field.  Three sites wanted to by plan
    step ``C2-f2d``, and N sites reaching into one private field is how a memo
    becomes a de-facto public seam.

    **The two alternatives were weighed and refused.**  A named constructor on
    the seam (``BalanceContext.for_test(calendar=...)``) ships a production
    entry point whose only caller is this suite, which is ``CLAUDE.md`` rule
    13's speculative shape.  An optional ``calendar=`` on the real
    :meth:`~app.services.balance_at.BalanceContext.build` hands EVERY
    production caller a way to supply a calendar the module did not derive,
    with nothing checking it even belongs to that owner -- a new way to hold
    contradictory state, to solve a test-ergonomics problem.  Making the
    calendar EAGER on the pass was refused on measurement: deriving one can
    raise for an owner with no pay schedule, so every render would begin
    failing over a fact most of them never read.

    **Every type here is the real one** -- a real
    :class:`~app.services.pay_calendar.PayCalendar` derived from real paydays
    (:func:`derived_window`'s own discipline: the ends and ordinals are the
    derivation's, so a test cannot express a schedule production could not),
    and a real frozen :class:`BalanceContext`.  It cannot fail silently either:
    rename the memo field and the seed misses, the pass falls through to
    ``calendar_for``, and the query raises outside an app context.

    Args:
        paydays: The paydays opening each period, in any order.
        cadence_days: Days between paydays, 1..365.  It sets the LAST period's
            end and nothing else.
        as_of: The day this read pass is pinned to.
        user_id: The owning user (default ``1``).  The calendar is derived for
            the SAME id the pass carries, so the two cannot disagree -- which
            is the pairing the seeded memo could otherwise express.
        history_opens_on: How far back the owner's paychecks reach, or ``None``
            -- the default, which is the nullable column's own; see
            :func:`payroll_basis` for what saying nothing means.

    Returns:
        A :class:`~app.services.balance_at.BalanceContext` with no baseline
        scenario and its calendar memo pre-filled.  ``scenario`` is ``None``
        because a pure unit case has no database to resolve one from; a test
        that reaches a scenario-scoped seam entry will get that entry's own
        named refusal rather than a fake.

    Raises:
        PayCalendarError: Anything the derivation refuses -- a duplicate
            payday, a cadence outside 1..365, a payday that is not a plain
            ``date``.
    """
    from app.services.balance_at import (  # pylint: disable=import-outside-toplevel
        BalanceContext,
    )
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PayCalendar,
    )

    pairs = [
        (index + 1, payday) for index, payday in enumerate(sorted(paydays))
    ]
    calendar = PayCalendar.from_paydays(
        pairs,
        eras_of(pairs, cadence_days),
        user_id=user_id,
        history_opens_on=history_opens_on,
    )
    return BalanceContext(
        user_id=user_id,
        scenario=None,
        as_of=as_of,
        _calendars={user_id: calendar},
    )


def read_pass(account, scenario, as_of):
    """Return a :class:`BalanceContext` for *account*'s OWNER, pinned at *as_of*.

    **The DB-backed twin of :func:`read_pass_over_paydays`**, and what a test
    holds where it used to build a bare
    :class:`~app.services.cash_ledger.AmountBasis` with :func:`basis_for` and
    hand it to the cash fold alongside an account and a date.  Plan step
    **X-i4** made that triple unstateable: the fold comes out of the pass
    (``assembled_fold(account, ctx)``), which memoizes it and refuses
    an account the pass does not own.

    **It takes the ACCOUNT rather than a user id, deliberately**, which is the
    same discipline :func:`read_pass_over_paydays` states for the calendar one
    tier over: the pass is built for the owner of the very account it is about
    to be asked for, so a test cannot express the mis-pairing production can no
    longer express either.  A test that WANTS the mismatch -- the ones grading
    :class:`~app.exceptions.ForeignAccountError` -- constructs the two halves by
    hand and says so at the call.

    It builds the context DIRECTLY rather than through
    :meth:`~app.services.balance_at.BalanceContext.build`, because the caller
    already holds the scenario its fixture seeded and ``build`` would re-resolve
    the owner's baseline from the database -- a second answer to a question the
    test has already answered, and one that would silently pick a different
    scenario for a fixture holding more than one.  It is therefore invisible to
    :func:`counting_read_passes`, exactly as ``read_pass_over_paydays`` is.

    Args:
        account: Any account of the owner the pass is for; ``user_id`` is read
            off it.
        scenario: The scenario whose rows the pass values.
        as_of: The reader's NOW -- ruling R-G's clamp floor, and the date each
            loan resolves at.  Required, because a fold pinned to the ambient
            clock is the fixture-depends-on-the-calendar shape
            ``.claude/rules/testing.md`` names.

    Returns:
        A real frozen :class:`~app.services.balance_at.BalanceContext` with
        empty memos.
    """
    from app.services.balance_at import (  # pylint: disable=import-outside-toplevel
        BalanceContext,
    )

    return BalanceContext(
        user_id=account.user_id, scenario=scenario, as_of=as_of,
    )


@contextmanager
def counting_calls(*targets):
    """Count calls to each ``(module path, attribute name)`` in *targets*.

    **The ONE instrument for "how many times did this render run that"**,
    shared by the architecture gate
    (``tests/test_arch/test_one_read_pass_per_render/``) and the render
    harness (``tests/manual/verify_retirement_render.py``) -- the same sharing
    :func:`counting_read_passes` below states its own case for, and for the
    same reason: two copies of a measuring instrument is the shape where one is
    later taught something the other is not, and the one that was not keeps
    grading the old question.

    Patched on the OWNING module and on every ``app.services`` module that
    imported the name directly, because a producer that holds its own reference
    would otherwise go uncounted -- and an undercount reads exactly like the
    improvement it is supposed to be measuring.

    Args:
        targets: ``(module path, attribute name)`` pairs, e.g.
            ``("app.services.retirement_projection", "load_projection_batch")``.

    Yields:
        A ``{name: int}`` dict whose values are the counts so far; read it
        after the block exits.
    """
    # pylint: disable=import-outside-toplevel
    import importlib
    import sys

    counts = {name: 0 for _, name in targets}
    restore = []
    for module_path, name in targets:
        real = getattr(importlib.import_module(module_path), name)

        def counting(*args, _real=real, _name=name, **kwargs):
            counts[_name] += 1
            return _real(*args, **kwargs)

        for module in list(sys.modules.values()):
            if getattr(module, "__name__", "").startswith("app.") and (
                getattr(module, name, None) is real
            ):
                restore.append((module, name, real))
                setattr(module, name, counting)
    try:
        yield counts
    finally:
        for module, name, real in restore:
            setattr(module, name, real)


@contextmanager
def counting_read_passes():
    """Count every ``BalanceContext.build`` while the block runs.

    **The ONE instrument for "how many read passes did this open"**, shared by
    the architecture gate (``tests/test_arch/test_one_read_pass_per_render/``)
    and the cutover harness (``tests/manual/verify_retirement_pass_cutover.py``)
    -- which is what plan step ``C2-f2d-1``'s adversarial code review asked for
    after both shipped their own copy of it. Two copies of a measuring
    instrument is the shape where one is later taught something the other is
    not, and the one that was not keeps grading the old question.

    Patched on the CLASS rather than on any module's imported name: several
    producers hold their own reference to ``BalanceContext``, so patching a
    single module's attribute would count some builds and miss others -- and a
    counter that undercounts reads exactly like a gate that passes.

    **It counts ``build``, not construction.** A direct
    ``BalanceContext(user_id=..., scenario=..., as_of=...)`` is invisible to it.
    Nothing in ``app/`` does that today, but :func:`read_pass_over_paydays`
    above does, so the path is live in this repository and a producer that took
    it would go uncounted.

    Yields:
        A ``{"n": int}`` dict whose ``n`` is the count so far; read it after
        the block exits.
    """
    # pylint: disable=import-outside-toplevel
    from app.services.balance_at import BalanceContext

    counter = {"n": 0}
    real = BalanceContext.build.__func__

    def counting(cls, user_id, as_of=None):
        counter["n"] += 1
        return real(cls, user_id, as_of)

    BalanceContext.build = classmethod(counting)
    try:
        yield counter
    finally:
        BalanceContext.build = classmethod(real)


class PlantedPricing:
    """A stand-in for one of the amount model's live DERIVATIONS.

    Plan step X-au-c2b made :class:`~app.services.cash_ledger.AmountBasis` hold
    the two derivations behind a row's live figure -- the owner's salary
    projection and the scenario's loan resolutions -- rather than the
    ``{transaction_id: Decimal}`` maps they used to produce.  Tests whose
    subject is a VALUATION rule ("when the basis says X, what does this
    reduction return") need to plant an answer without seeding a salary profile
    or a loan, and this is what they plant it with.

    It satisfies the seam: :meth:`net_for` is what
    ``income_service.salary_net_for`` asks.

    **It answered a LOAN half too until plan step X-au-g-2c-2**, and planting
    was keyed by TRANSACTION id because that half (``LoanPricing.live_cash``)
    took a row.  That method is deleted -- a transfer shadow was DERIVED from
    that step and had no stored figure for a read-time override to supersede,
    and plan step ``balance:X-bi-6-4d-2`` deleted the shadows -- so the seam is
    salary alone and the key is the pair the salary derivation is keyed on.
    Answering the old key would have been worse than failing: the loan half was
    asked FIRST, so every planted figure landed there, and a helper that kept
    answering by row id after the arm was deleted would have gone quietly inert.

    Use it ONLY where the derivation is an input to the rule under test.  A test
    OF the amount model builds a real basis (``amount_basis``) and seeds the
    profile, because a planted map cannot grade a derivation.
    """

    def __init__(self, overrides=None):
        """Plant ``{(template_id, pay_period_id): Decimal}`` as the answers.

        Args:
            overrides: The live figures to answer with, keyed the way the
                salary derivation is keyed, or ``None`` for a derivation that
                answers for nothing -- the common case, and what a row set with
                no salary row gets.
        """
        self._overrides = dict(overrides or {})

    def net_for(self, template_id, pay_period_id):
        """Return the planted net for one template and period, or ``None``.

        Args:
            template_id: The row's definition.
            pay_period_id: The row's pay period.

        Returns:
            The planted ``Decimal``, or ``None`` when nothing was planted for
            that pair.
        """
        return self._overrides.get((template_id, pay_period_id))

    def derive_cash(self, due_date, period_start, loan_account_id, extra):
        """REFUSE: a planted basis cannot price a loan.

        :func:`planted_basis` puts this same object in ``AmountBasis.loans``,
        where the amount model's rule 4 calls ``derive_cash``.  Without this the
        call is an ``AttributeError`` on a class that simply has no such method
        -- a failure that names the DOUBLE rather than the mistake, and one an
        adversarial review of plan step X-au-g-2c-2 asked to be made legible.

        The class docstring's "use it ONLY where the derivation is an input to
        the rule under test" was the whole of the guarantee before this; a
        promise a test can break silently is not one.

        **The signature took a payment SHADOW until plan step X-au-f-2**, where
        ruling **R-BAL10** re-typed the real producer onto the two dating VALUES
        so a parent TRANSFER could answer it.  A double whose signature has
        drifted from its subject stops standing in for it, so this mirrors the
        real one exactly.

        Args:
            due_date: Ignored.
            period_start: Ignored.
            loan_account_id: Ignored.
            extra: Ignored.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError(
            "A planted basis cannot price a loan payment: PlantedPricing "
            "stands in for the SALARY derivation only. This transfer reached "
            "AmountRule.LOAN_PAYMENT, so the case needs a real basis "
            "(amount_basis) over a seeded loan -- a planted map cannot grade "
            "a derivation."
        )


def planted_basis(*rows, overrides=None):
    """An :class:`~app.services.cash_ledger.AmountBasis` answering *overrides*.

    The basis a producer would hand a valuation rule, with the live salary
    derivation planted (:class:`PlantedPricing`) so no test of a reduction needs
    a salary profile to reach the override seam.  The ``loans`` field is planted
    with the same object, which answers nothing there: since plan step
    X-au-g-2c-2 the only thing a caller asks it is ``derive_cash``, and a
    reduction under test never does.

    Args:
        rows: The rows this basis will price.  A basis was built OVER a row set
            until plan step X-au-c2b, and every call site named its rows; they
            are kept because the first row still decides one thing -- the
            SCENARIO the basis declares.  ``resolve_transaction_amount`` refuses
            a row from another scenario, so a double that named the wrong one
            would grade that refusal instead of the rule under test.
        overrides: ``{(template_id, pay_period_id): Decimal}`` live figures to
            plant on the salary derivation.

    Returns:
        The :class:`~app.services.cash_ledger.AmountBasis`.
    """
    from app.services.cash_ledger import (  # pylint: disable=import-outside-toplevel
        AmountBasis,
    )

    planted = PlantedPricing(overrides)
    return AmountBasis(
        user_id=0,
        scenario_id=getattr(rows[0], "scenario_id", 0) if rows else 0,
        salary=planted,
        loans=planted,
    )


def transfer_amount(xfer):
    """What one parent TRANSFER is worth -- its amount, resolved.

    **Written when plan step X-au-f moved a generated transfer's figure from a
    column to a value**, the last per-kind cutover: X-au-g-2c-2 had done the
    same for each transfer shadow, which a helper of its own priced (deleted at
    plan step ``balance:X-bi-6-4d-2``, which deleted the shadows), and X-au-d
    (salary) and X-au-e (ordinary recurring rows) came between.  A case
    asserting ``xfer.amount == Decimal(...)`` on a generated transfer would
    compare a figure against ``None``; what those cases were ABOUT -- what the
    transfer is worth -- is asked of the amount model here.

    An AD-HOC transfer still owns its figure, so this answers the same column it
    always did for one; the helper is right for both kinds, which is what stops
    a case having to know which it holds.

    A FRESH basis per call: a case that edits a transfer and re-reads must see
    the new figure, and a basis memoizes for the length of a read pass.

    Args:
        xfer: The transfer to price.

    Returns:
        The ``Decimal`` the transfer resolves to.

    Raises:
        AmountUnresolvable: From the amount model, for a transfer whose rule
            cannot price it -- a refusal is never a fallback.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.cash_ledger import derived_amount_basis, resolve_transfer_amount

    return resolve_transfer_amount(
        xfer, derived_amount_basis(xfer.user_id, xfer.scenario_id),
    )


def basis_for(account, scenario):
    """The read pass's :class:`~app.services.cash_ledger.AmountBasis`.

    What a route or a top-level producer holds and threads into the cash fold
    since plan step X-au-c2b -- pinned to an owner and a scenario, never to a
    row set, and lazy, so building one in a fixture costs nothing until a rule
    asks.  The seam entries take one where they took a bare ``scenario_id``,
    because the scenario a row set is loaded under and the derivations it is
    priced through are one decision rather than two a caller could get apart.

    Args:
        account: Any account of the owner; only ``user_id`` is read.
        scenario: The scenario whose rows are being valued.

    Returns:
        The unresolved :class:`~app.services.cash_ledger.AmountBasis`.
    """
    from app.services.cash_ledger import (  # pylint: disable=import-outside-toplevel
        derived_amount_basis,
    )

    return derived_amount_basis(account.user_id, scenario.id)


def all_periods(user_id):
    """Return every one of *user_id*'s pay periods as ORM rows, payday order.

    **A TEST helper because the application has no such reader any more.**
    ``pay_period_service.get_all_periods`` was the last of that module's six
    ``get_*`` readers, and pay-calendar plan step C2-f3c deleted it with its
    last caller: the recurrence generation seam now takes a
    :class:`~app.services.pay_calendar.PayCalendar`, which IS the owner's whole
    schedule, so nothing in ``app/`` asks a separate reader for the rows.
    Keeping a production function alive for test callers alone is the
    speculative shape ``CLAUDE.md`` rule 13 forbids, and ruling **P54** already
    settled where the replacement lives: one shared helper here, never a
    ``for_test`` door on the real API.

    **Ordered by ``start_date``, and since plan step ``pay_calendar:C4-c``
    there is no other order to ask for**: ``period_index`` was a column until
    that step dropped it, and a row is now the payday alone.

    A row carries no SPAN either.  A caller that wants one asks
    :func:`derived_span`, which is the calendar's answer for that row.

    Args:
        user_id: The owning user.

    Returns:
        The owner's :class:`~app.models.pay_period.PayPeriod` rows, earliest
        payday first.  Empty for an owner with no schedule.
    """
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.pay_period import (  # pylint: disable=import-outside-toplevel
        PayPeriod,
    )

    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )


def derived_span(period):
    """Return the :class:`DerivedPeriod` the application answers for *period*.

    **The suite's one door onto "how far does this paycheck run", and plan step
    ``pay_calendar:C4-c`` is why it has to be a door at all.**  A test read
    ``period.end_date`` and ``period.period_index`` off the ORM row until that
    step dropped both columns.  Neither is a property of a row: the ordinal is
    the payday's position in the owner's sorted set and the end is the day
    before the NEXT payday, so both are answers about the WHOLE payday set and
    ``pay_calendar.calendar_for`` is what computes them.

    Resolving through the owner's real calendar rather than constructing a
    :class:`DerivedPeriod` by hand is what keeps a case from asserting against
    bounds the application never computes -- the property
    :func:`period_window` exists for one level up.

    Args:
        period: A saved :class:`~app.models.pay_period.PayPeriod` row.

    Returns:
        Its :class:`~app.services.pay_calendar.DerivedPeriod` -- ``start_date``,
        ``end_date``, ``period_index`` and ``end_is_projected``.

    Raises:
        AssertionError: *period* is not in its own owner's calendar, which
            means it was never flushed or belongs to another owner.  A silent
            ``None`` here would make every assertion downstream vacuous.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        calendar_for,
    )

    resolved = calendar_for(period.user_id).period_by_id(period.id)
    assert resolved is not None, (
        f"pay period {period.id} is not in user {period.user_id}'s own "
        f"calendar -- it was never flushed, or it belongs to someone else"
    )
    return resolved


def last_covered_day(period):
    """Return the last day *period* covers, DERIVED.

    ``period.end_date`` until plan step ``pay_calendar:C4-c`` dropped that
    column; :func:`derived_span` carries why it is a question about the whole
    payday set.  This spelling exists because most callers want the DAY and
    reading ``derived_span(p).end_date`` at every one of them puts the word
    ``end_date`` back on a page where the column no longer exists.

    Args:
        period: A saved :class:`~app.models.pay_period.PayPeriod` row.

    Returns:
        The inclusive last day of its span.
    """
    return derived_span(period).end_date
