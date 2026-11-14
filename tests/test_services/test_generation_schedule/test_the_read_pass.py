"""R4b-1: a generate pass takes the read pass, one schedule read serves a request, and no ORM pay
period enters the seam.
"""

from __future__ import annotations

import pytest

from app.exceptions import RecurrenceWindowError
from app.routes._period_population import populate_new_periods
from app.services import recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._repo_root import REPO_ROOT
from tests._test_helpers import (
    counting_calls,
    counting_read_passes,
    create_account_of_type,
    make_transfer_template,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD

from ._helpers import _CALENDAR_DOOR, _make_template, _append_period


class TestTheGeneratePassTakesTheReadPass:
    """What plan step R7d-c-1 changed, and the ordering rule it bought.

    :class:`~app.services.generation_schedule.GenerationSchedule` took a
    :class:`~app.services.pay_calendar.PayCalendar` until that step and takes a
    :class:`~app.services.balance_at.BalanceContext` now, deriving the calendar
    from it.  The reason is plan step R7d-c-2: a loan payment's closing bound
    stops being the ``budget.recurrence_rules.end_date`` column and becomes a
    fold over the loan, which needs a read pass, and the 2026-08-16 ruling
    forbids a producer below the route building one.

    Three properties are graded here, and only the first is a tidiness claim:

    * the value derives NOTHING of its own -- the pass's memo is the request's
      one derivation;
    * a pass whose calendar was resolved BEFORE the write that created the
      window's periods is REFUSED, loudly, rather than silently generating
      into a schedule that does not hold them.  That is the whole safety
      argument for letting a lazily-derived value in here at all;
    * a repopulation opens exactly ONE pass for the whole batch, whatever the
      template count, so the batch holds one calendar derivation, one
      baseline-scenario resolution and one answer per loan.  **It is NOT an
      order-independence claim**: a first draft said a transfer this loop
      generates into a loan would change a later definition's bound, and
      ``period_population``'s docstring carries the measurement that refutes
      it -- future rows do not move a derived payoff, because the ESTIMATED
      tier prices every uncovered future installment from the DEFINITION.
    """

    def test_the_schedule_derives_nothing_of_its_own(
        self, app, db, seed_user, seed_periods,
    ):
        """The pass's memo is the derivation; the value adds none.

        The COLD case is the one that matters: it pins that constructing this
        value costs exactly ONE derivation rather than none (which would mean
        the calendar came from somewhere else) or two.
        """
        with app.app_context():
            cold = BalanceContext.build(seed_user["user"].id)
            with counting_calls(_CALENDAR_DOOR) as counts:
                schedule = GenerationSchedule.for_pass(cold)
            assert counts["calendar_for"] == 1, (
                f"constructing over a cold pass derived the owner's calendar "
                f"{counts['calendar_for']} times; the pass memoizes one"
            )

            with counting_calls(_CALENDAR_DOOR) as again:
                second = GenerationSchedule.for_pass(cold)
                assert second.calendar is schedule.calendar, (
                    "two schedules over one pass must answer the SAME calendar "
                    "object, or the memo is not what they are reading"
                )
            assert again["calendar_for"] == 0, (
                f"a second schedule over the same pass derived "
                f"{again['calendar_for']} more calendars; the memo is what "
                f"holds a request to one"
            )

    def test_a_pass_resolved_before_the_write_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A stale pass cannot generate into periods it cannot see.

        The failure mode this refusal exists for: a caller builds the read
        pass, reads its calendar for some earlier decision, THEN records new
        paydays, then asks this value to write into them.  The pass's memo
        still holds the pre-write schedule, so the new ids are not in it -- and
        without the refusal the window would simply intersect to nothing and
        the extend would report "generated 0 rows" for every definition the
        owner has.
        """
        with app.app_context():
            stale = BalanceContext.build(seed_user["user"].id)
            stale.calendar()  # resolved BEFORE the write below

            created = _append_period(seed_user, seed_periods)
            new_ids = {period.id for period in created}

            with pytest.raises(RecurrenceWindowError, match="are not in user"):
                GenerationSchedule.for_period_ids(stale, new_ids)

            # The CONTROL: the same window, a pass whose memo is still empty
            # when it is asked -- which is the order ``period_population``
            # runs in.  Without this the raise above would be satisfied by any
            # window this owner cannot write into, including a broken one.
            fresh = BalanceContext.build(seed_user["user"].id)
            schedule = GenerationSchedule.for_period_ids(fresh, new_ids)
            assert schedule.write_period_ids == frozenset(new_ids), (
                "a pass resolved AFTER the write must accept the same window "
                "the stale one was refused for"
            )

    def test_a_stale_pass_through_for_pass_is_NOT_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The refusal above does NOT cover :meth:`for_pass`, and here is what does.

        **An adversarial review of plan step R7d-c-1 measured this after the
        docstring claimed the opposite.**  :meth:`for_period_ids` catches a
        stale pass because its window comes from somewhere else; ``for_pass``
        takes its window FROM the calendar, so the window is a subset by
        construction and ``__post_init__`` has nothing to compare.  A stale
        pass there does not raise -- it UNDER-GENERATES in silence, leaving the
        periods it cannot see empty.

        Pinned rather than fixed, because the type cannot tell a stale memo
        from a fresh one and no ``for_pass`` caller in ``app/`` creates a pay
        period.  What this case protects is the next reader of that docstring:
        if a later step teaches ``for_pass`` to refuse, this test says so by
        failing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(seed_user, EVERY_PERIOD)
            db.session.flush()

            stale = BalanceContext.build(seed_user["user"].id)
            covered_before = {
                period.period_id
                for period in stale.calendar().saved()
            }

            created_periods = _append_period(seed_user, seed_periods)
            new_ids = {period.id for period in created_periods}

            # NO raise -- the contrast with the case above is the whole point.
            schedule = GenerationSchedule.for_pass(stale)
            assert schedule.write_period_ids.isdisjoint(new_ids), (
                "a stale pass's window cannot name a period recorded after it "
                "resolved; if it can, the memo is not what for_pass reads"
            )

            created = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            placed = {row.pay_period_id for row in created}
            assert placed, "the pass generated nothing, so nothing is graded"
            assert placed <= covered_before, (
                "a stale pass wrote into a period it could not see"
            )
            assert placed.isdisjoint(new_ids), (
                f"the stale pass covered {placed & new_ids}, so this case is "
                f"no longer measuring the silent gap it was written for"
            )

    def test_one_read_pass_serves_the_whole_repopulation(
        self, app, db, seed_user, seed_periods,
    ):
        """Seven definitions across BOTH engines, one pass for the batch.

        ``populate_periods_from_active_templates`` runs both engines over every
        active definition the owner has.  A pass rebuilt per template would
        derive the owner's whole pay calendar once per definition, resolve the
        baseline scenario once per definition, and -- from plan step R7d-c-2 --
        re-fold every destination loan once per definition.  The count is ONE
        for the batch.

        **The counted call is the ROUTE's half since ruling R-R38**, and what
        the count grades moved with it.  The producer cannot open a second pass
        any more -- it takes one and has no constructor -- so "one per batch
        rather than one per template" is now a property of the SIGNATURE.  What
        this case still measures is that ``populate_new_periods`` opens exactly
        one for the batch rather than looping the doors' periods and opening
        one each, which no signature prevents.  Counting the producer alone
        would read ZERO and grade nothing.
        """
        with app.app_context():
            for index in range(6):
                template = _make_template(seed_user, EVERY_PERIOD)
                template.name = f"Recurring {index}"
                db.session.flush()
            # The TRANSFER loop is a second pass over a second table, and an
            # adversarial review of plan step R7d-c-1 found this case graded
            # only the transaction one: with no transfer template the engine
            # iterates nothing, so a per-template rebuild THERE would read one
            # pass and pass.
            destination = create_account_of_type(
                seed_user, db.session, "Savings", name="Holiday Fund",
            )
            make_transfer_template(db.session, seed_user, destination)
            db.session.flush()

            created = _append_period(seed_user, seed_periods)
            new_ids = {period.id for period in created}

            with counting_read_passes() as passes:
                written = populate_new_periods(seed_user["user"].id, created)
            assert written >= 7, (
                f"the count was taken over a repopulation that wrote {written} "
                f"rows; SEVEN definitions were seeded across the two engines "
                f"and both loops have to have run for this count to grade them"
            )
            assert passes["n"] == 1, (
                f"the repopulation opened {passes['n']} read passes for one "
                f"batch of {written} rows; one pass per batch is what makes "
                f"the answer independent of template order"
            )


class TestOneScheduleReadServesTheWholeRequest:
    """The N+1 an adversarial review measured, and the gate against its return.

    ``rows_to_generate_in_period`` runs once per envelope row being carried
    forward.  Building a ``GenerationSchedule`` inside it cost one schedule
    read AND one full forward occurrence walk per row -- measured at 12 queries
    for the preview and 24 for the execute over 12 envelope rows, against a
    baseline of 0.  The schedule is resolved once in
    ``_build_carry_forward_context`` and threaded.

    **The instrument changed at pay-calendar plan step C2-f3c and the claim got
    stronger.**  It counted ``pay_period_service.get_all_periods``, which the
    value used to read for itself; that reader is deleted and the value TOOK
    the caller's :class:`~app.services.pay_calendar.PayCalendar`.  So the
    number this asserts is ZERO derivations inside the service -- the request's
    one derivation happens at its door, which is what ledger row **P68** asked
    for.  ``tests/test_arch/test_one_read_pass_per_render/`` grades the
    render's total; this grades the service's share of it.

    **Plan step R7d-c-1 made it the read PASS, and the zero now has a
    precondition worth stating**: the derivation is the pass's memo rather than
    a value the route computed, so it happens wherever it is first asked for.
    The route asks at its door (``_resolve_carry_forward_context`` reads
    ``balance_ctx.calendar()`` to look both periods up), so zero below the door
    is still the shape the application runs -- and the second measurement below
    grades the thing that actually prevents the N+1: handed a pass whose memo
    is EMPTY, the service derives ONE calendar for twelve envelope rows, not
    twelve.  The old assertion could not see that at all; a service deriving
    one per row from its own ``calendar_for`` would have failed it, but so
    would a service deriving exactly one, and only the first is the defect.
    """

    def test_a_twelve_row_carry_forward_derives_no_calendar(
        self, app, db, seed_user, seed_periods,
    ):
        """Twelve envelope rows, zero calendar derivations inside the service."""
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the carry-forward service
            # is this class's collaborator only.
            from app.services import carry_forward_service  # pylint: disable=import-outside-toplevel

            scenario_id = seed_user["scenario"].id
            for index in range(12):
                template = _make_template(
                    seed_user, EVERY_PERIOD,
                )
                template.name = f"Envelope {index}"
                template.is_envelope = True
                db.session.flush()
                recurrence_engine.generate_for_template(
                    template,
                    GenerationSchedule.for_pass(
                        BalanceContext.build(seed_user["user"].id),
                    ),
                    scenario_id,
                )
            db.session.commit()

            # **The N+1 gate proper** (plan step R7d-c-1), and it runs FIRST
            # because the execute below carries these rows and a second
            # preview over the same pair then plans nothing.  A pass whose memo
            # is EMPTY derives ONE calendar for the whole preview, not one per
            # envelope row -- which is the claim the two zeros further down
            # cannot make, because a memo that was already full satisfies them
            # whatever the service would have caused on its own.
            cold = BalanceContext.build(seed_user["user"].id)
            with counting_calls(_CALENDAR_DOOR) as cold_counts:
                cold_preview = carry_forward_service.preview_carry_forward(
                    seed_periods[0].id, seed_periods[1].id, scenario_id,
                    balance_ctx=cold,
                )
            assert len(cold_preview.plans) == 12, (
                "the cold count was taken over a preview that planned nothing, "
                "so the envelope branch this grades never ran"
            )
            assert cold_counts["calendar_for"] == 1, (
                f"twelve envelope rows caused "
                f"{cold_counts['calendar_for']} calendar derivations on a pass "
                f"that had none; the memo is what holds it to one, and one per "
                f"row is the N+1 this class exists to prevent"
            )

            # The ROUTE's shape: one pass, and its calendar derived at the
            # door -- ``_resolve_carry_forward_context`` reads it to look both
            # periods up before the service is called at all.
            balance_ctx = BalanceContext.build(seed_user["user"].id)
            balance_ctx.calendar()

            with counting_calls(_CALENDAR_DOOR) as counts:
                preview = carry_forward_service.preview_carry_forward(
                    seed_periods[0].id, seed_periods[1].id, scenario_id,
                    balance_ctx=balance_ctx,
                )
            assert len(preview.plans) == 12, (
                "the count was taken over a preview that planned nothing, so "
                "the envelope branch this grades never ran"
            )
            assert counts["calendar_for"] == 0, (
                f"the preview derived the owner's calendar "
                f"{counts['calendar_for']} times below its door; it takes the "
                f"one the route's pass already holds"
            )

            with counting_calls(_CALENDAR_DOOR) as counts:
                carried = carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id, scenario_id,
                    balance_ctx=balance_ctx,
                )
            assert carried == 12, (
                "the count was taken over an execute that carried nothing"
            )
            assert counts["calendar_for"] == 0, (
                f"the execute path derived the owner's calendar "
                f"{counts['calendar_for']} times below its door; it takes the "
                f"one the route's pass already holds"
            )

            # **The instrument's own control**, and an adversarial review of
            # plan step C2-f3c is why it is here: ``== 0`` is satisfied
            # identically by "the service derives nothing" and by "the counter
            # is blind", and only one of those is the claim.
            #
            # It calls the door the way ``app/`` code calls it -- through the
            # module attribute -- because that is the only shape
            # ``counting_calls`` can see.  The instrument patches every
            # ``app.*`` module holding the real function as an attribute of
            # that NAME, so a producer that did ``from ... import calendar_for
            # as _cal`` would derive uncounted and both zeros above would read
            # green through the N+1 this class exists to prevent.  That hole is
            # the instrument's, not this step's; what this control pins is that
            # the counter is live for the ordinary spelling.
            from app.services import pay_calendar  # pylint: disable=import-outside-toplevel

            with counting_calls(_CALENDAR_DOOR) as control:
                pay_calendar.calendar_for(seed_user["user"].id)
            assert control["calendar_for"] == 1, (
                f"the counter saw {control['calendar_for']} of one direct call "
                f"to the door it is keyed on, so both zeros above mean nothing"
            )


class TestNoOrmPayPeriodEntersTheSeam:
    """The generation seam names ``budget.pay_periods`` in exactly one way: ids.

    **What makes plan step C4 safe for this seam**, and the property
    pay-calendar plan step C2-f3c actually delivered.  Every module below used
    to hold an ORM ``PayPeriod`` -- the schedule value carried a tuple of them,
    the placements carried one each, the row select joined the table on
    ``end_date``, and the amount producer resolved an id back to a derived
    period.  Reading a period's BOUNDS off the row means reading
    ``end_date`` and ``period_index``, the two columns C4-c dropped, and finding
    those readers by hand is what ledger row **P70** records going wrong: an
    AST census found SIX in query position where the step's own specification
    named two.

    Graded on the IMPORT rather than on the attribute, because
    ``txn.pay_period.end_date`` reaches the same columns through a relationship
    with no ``PayPeriod`` name in sight, while a module that imports the model
    at all is a module that can grow one.  The seam takes ids in and derived
    periods through, so it has no reason to name the mapped class.
    """

    #: Every module of the generation seam, as repository-relative paths.  The
    #: two engines, the value they share, the shared queries, and the
    #: orchestrator above them.
    _SEAM = (
        "app/services/generation_schedule.py",
        "app/services/period_population.py",
        "app/services/transfer_recurrence.py",
        "app/services/_recurrence_common.py",
        "app/services/recurrence_engine/__init__.py",
        "app/services/recurrence_engine/_amounts.py",
        "app/services/recurrence_engine/_conflicts.py",
        "app/services/recurrence_engine/_generate.py",
        "app/services/recurrence_engine/_maintain.py",
        "app/services/recurrence_engine/_plan.py",
    )

    def _pay_period_imports(self, relative_path):
        """Return every import of the pay-period MODEL in *relative_path*.

        Keyed on what was imported rather than on the local binding, so
        ``from app.models.pay_period import PayPeriod as Row`` cannot hide.

        Args:
            relative_path: Path under the repository root to parse.

        Returns:
            The set of ``(module, name)`` pairs naming the model.
        """
        # Pylint: ``import-outside-toplevel`` -- this census is the only user.
        import ast  # pylint: disable=import-outside-toplevel

        tree = ast.parse((REPO_ROOT / relative_path).read_text())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    # BOTH spellings: ``from app.models.pay_period import
                    # PayPeriod`` and ``from app.models import pay_period``.
                    # An adversarial review of plan step C2-f3c found the
                    # second walked straight past this census.
                    if module.endswith("models.pay_period"):
                        found.add((module, alias.name))
                    elif f"{module}.{alias.name}".endswith("models.pay_period"):
                        found.add((module, alias.name))
            elif isinstance(node, ast.Import):
                found.update(
                    (alias.name, alias.name) for alias in node.names
                    if alias.name.endswith("models.pay_period")
                )
        return found

    def test_no_seam_module_imports_the_pay_period_model(self):
        """None of the ten modules names ``app.models.pay_period``."""
        offenders = {
            relative: self._pay_period_imports(relative)
            for relative in self._SEAM
            if self._pay_period_imports(relative)
        }
        assert offenders == {}, offenders

    def test_the_census_sees_an_import_when_there_is_one(self):
        """The CONTROL: a module that DOES import the model is detected.

        Without it the assertion above passes for a census that parses nothing
        -- a wrong path, a swallowed error, an ``ast`` walk that misses
        ``ImportFrom``.  ``pay_period_write`` is the one module in ``app/``
        whose whole job is writing that table, so it will always import it.
        """
        assert ("app.models.pay_period", "PayPeriod") in (
            self._pay_period_imports("app/services/pay_period_write.py")
        )

    def test_the_census_sees_every_spelling_of_the_import(self, tmp_path):
        """The CONTROL for the census's own BRANCHES, one per import form.

        The live tree exercises exactly one of the three: ``from
        app.models.pay_period import PayPeriod``.  An adversarial review of
        plan step C2-f3c found the ``from app.models import pay_period``
        spelling walking past the census unseen, and the ``ast.Import`` arm
        with no firing control at all -- nothing in ``app/`` writes ``import
        app.models.pay_period``, so it could have been deleted silently.
        """
        # Pylint: ``import-outside-toplevel`` -- this control is the only user.
        import ast  # pylint: disable=import-outside-toplevel

        spellings = {
            "from app.models.pay_period import PayPeriod\n":
                ("app.models.pay_period", "PayPeriod"),
            "from app.models import pay_period\n":
                ("app.models", "pay_period"),
            "import app.models.pay_period\n":
                ("app.models.pay_period", "app.models.pay_period"),
        }
        for source, expected in spellings.items():
            tree = ast.parse(source)
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        if module.endswith("models.pay_period"):
                            found.add((module, alias.name))
                        elif f"{module}.{alias.name}".endswith(
                            "models.pay_period",
                        ):
                            found.add((module, alias.name))
                elif isinstance(node, ast.Import):
                    found.update(
                        (alias.name, alias.name) for alias in node.names
                        if alias.name.endswith("models.pay_period")
                    )
            assert expected in found, (source, found)
