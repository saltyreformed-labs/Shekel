"""Template route tests: the recurrence preview endpoint, which lists the dates a save would
produce, closing bound included, and refuses what the form cannot save without a 500.
"""

import re
from datetime import date, timedelta

import pytest

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction_template import TransactionTemplate
from app.services.pay_calendar import calendar_for
from app.utils.dates import display_today
from tests._test_helpers import cadence_payload
from tests.oracles.recurrence_baseline import EVERY_PERIOD, EVERY_N_PERIODS, MONTHLY


# ── Preview Recurrence Tests ─────────────────────────────────────────


class TestPreviewRecurrence:
    """Tests for GET /templates/preview-recurrence."""

    def test_preview_monthly(self, app, auth_client, seed_user, seed_periods_today):
        """Preview for a monthly cadence returns an occurrence list."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                    "day_of_month": "15",
                },
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data or b"No matching" in resp.data

    def test_preview_an_unmodelled_unit_is_unknown_not_blank(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A ``recurrence_units`` id the app does not model previews as UNKNOWN.

        The successor to the ``Once``-``ref``-row case: since plan step R7b-2
        the preview takes the two AXES rather than a closed-set pattern id, so
        the unmodelled input it can be handed is a unit or a placement.  The
        honest answer is "unknown", not "no preview" -- it is reachable only
        through hand-crafted input, and "no preview" would read as "this is
        fine".  ONE message covers both axes because they share a disposition
        and a reachability; the ABSENT unit below keeps its own, because that
        one is what "Does not repeat" posts and users read it.
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(),
                    "recurrence_unit": "999999",
                },
            )
            assert resp.status_code == 200
            assert b"Unknown cadence" in resp.data

    def test_preview_an_unmodelled_placement_is_unknown(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The second axis takes the same answer as the first."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(),
                    "recurrence_placement": "999999",
                },
            )
            assert resp.status_code == 200
            assert b"Unknown cadence" in resp.data

    def test_preview_a_named_unit_with_no_placement_is_unknown(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A placement is REQUIRED once a unit is named, not an optional refinement.

        Defaulting it would let the preview show a schedule the save would not
        produce: ``(1, MONTH)`` funded from the covering paycheck and the same
        cadence funded from the month's first paycheck are different rules.
        """
        with app.app_context():
            payload = cadence_payload(unit=RecurrenceUnitEnum.MONTH)
            del payload["recurrence_placement"]
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={**payload, "day_of_month": "15"},
            )
            assert resp.status_code == 200
            assert b"Unknown cadence" in resp.data

    def test_preview_no_pattern(self, app, auth_client, seed_user, seed_periods_today):
        """Preview with no cadence parameter returns the no-preview message."""
        with app.app_context():
            resp = auth_client.get("/templates/preview-recurrence")
            assert resp.status_code == 200
            assert b"No preview" in resp.data

    @pytest.mark.parametrize(
        ("cadence", "unit", "interval_n", "query"),
        [
            # A live 500 before plan step R4a, out of the authoring seam.
            (
                EVERY_N_PERIODS, RecurrenceUnitEnum.PERIOD, 0, {},
            ),
            # A live 500 MEASURED at plan step R7c-b, and by a route the
            # deleted ``_MAX_START_DATE_YEAR`` no longer covered: past the
            # saved horizon the pay calendar projects the covering paycheck by
            # adding ``cadence_days`` to a start, which overflows near
            # ``date.max``.  ``OverflowError`` comes from outside the
            # recurrence package, so this endpoint's handler never saw it.
            (
                EVERY_PERIOD, RecurrenceUnitEnum.PERIOD, 1,
                {"starts_on": "9999-12-31"},
            ),
            (
                MONTHLY, RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "9999-12-31"},
            ),
            (
                MONTHLY, RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "0001-01-01"},
            ),
            # The nominal day took the retired day / month args' place, and
            # takes their disposition: a value the date leaves no room for is
            # a second statement of a day ``starts_on`` already carries.
            (
                MONTHLY, RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "2026-04-15", "nominal_day": "30"},
            ),
            (
                MONTHLY, RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "2026-02-28", "nominal_day": "99"},
            ),
            (
                MONTHLY, RecurrenceUnitEnum.MONTH, 1,
                {"starts_on": "2026-02-28", "nominal_day": "0"},
            ),
        ],
        # Without this every case collapses to ``cadence0``..``cadence6``: a
        # ShapeCadence has no useful repr, and the cadence is what tells the
        # reader which row failed.
        ids=lambda value: getattr(value, "label", None),
    )
    def test_preview_refuses_out_of_domain_arguments_without_a_500(
        self, app, auth_client, seed_user, seed_periods_today,
        cadence, unit, interval_n, query,
    ):
        """Unbounded query args answer a muted line, never a stack trace.

        This endpoint reads ``interval_n`` / ``starts_on`` / ``nominal_day``
        straight from ``request.args``.  The two form schemas bound them and
        the columns carry ``ck_recurrence_rules_positive_interval`` /
        ``ck_recurrence_rules_starts_on_range`` /
        ``ck_recurrence_rules_nominal_day`` -- but nothing bounds THIS path,
        and it is reachable by anyone signed in.  The resolution door refuses
        every one by mirroring the columns' own domains, which is the rule the
        endpoint's docstring states: each bound lives on the column and its
        mirror in ``resolve``, never a third time here.

        **The ``day_of_month`` / ``month_of_year`` cases left this list at plan
        step R7c-b, and were not simply dropped.**  Those arguments name
        columns the write door now ENCODES, so the endpoint stopped reading
        them -- a case passing ``?day_of_month=-5`` would assert that an
        argument nothing reads is ignored, which is true of every string.  The
        ``starts_on`` cases replace them on the argument that took their place,
        and the first of those is a 500 this step MEASURED rather than
        inherited.
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(unit=unit, interval_n=interval_n),
                    **query,
                },
            )

            assert resp.status_code == 200, (
                f"{cadence.label} with {query} answered {resp.status_code}"
            )
            assert b"No preview for this cadence" in resp.data

    @pytest.mark.parametrize(
        ("label", "unit", "interval_n"),
        [
            # 10000 YEARS is 120000 months; the walk's ordinal divides back out
            # to a year past 9999 and ``date()`` raises ValueError.
            ("years past the calendar", RecurrenceUnitEnum.YEAR, 10_000),
            ("months past the calendar", RecurrenceUnitEnum.MONTH, 120_000),
            # The WEEK unit's two 500s before plan step R8-b bounded its walk:
            # 450000 weeks (3,150,000 days) steps past ``date.max``, which the
            # old walk raised OverflowError for at its next yield, and the
            # column's own ceiling made the old stride's ``timedelta`` raise
            # OverflowError before any date at all.  (A stride landing past
            # the app's calendar but inside ``date`` previewed one date either
            # way, since no paycheck hosts it; the walk's own case grades it.)
            ("weeks past date's own end", RecurrenceUnitEnum.WEEK, 450_000),
            (
                "weeks at the column's ceiling", RecurrenceUnitEnum.WEEK,
                2_147_483_647,
            ),
        ],
    )
    def test_a_huge_interval_previews_ONE_date_and_not_a_stack_trace(
        self, app, auth_client, seed_user, seed_periods_today,
        label, unit, interval_n,
    ):
        """A cadence whose second occurrence leaves the calendar fires once.

        **Opened by plan step R7b-2 and found by an adversarial review of it.**
        Before the step the preview posted a pattern id and ``decode_pattern``
        DISCARDED the submitted interval for every calendar pattern, so only
        the pay-period walk -- which cannot overflow -- ever saw it.  The form
        posts the interval as the cadence itself, so the raw query arg reaches
        ``months_per_step`` and then ``date()``, whose ``ValueError`` is not the
        ``RecurrenceResolutionError`` this endpoint catches.

        **The remedy MOVED at plan step R7c-c, and the expected answer with
        it.**  It used to be that the preview refuses what the save would
        refuse -- and the save refused, because no calendar unit had a storable
        interval above 6.  Freeing the interval is the whole of that step, so
        ``(10000, YEAR)`` is now perfectly savable, and the overflow it exposed
        is closed at its own root instead: ``_months.walk_months`` stops at the
        last month the application's calendar reaches rather than walking off
        the end of ``date``.

        So the honest answer is ONE occurrence -- the rule does fire once, and
        names no second date this application can hold -- and the invariant the
        case was written for still holds: no stack trace, and the preview shows
        what the save would produce.

        **The WEEK unit's walk took the same bound at plan step R8-b**, the step
        that made the unit authorable: ``_occurrence._week_walk`` strides on day
        ordinals and stops at ``CALENDAR_DATE_MAX``, so neither of its ways off
        the end raises.
        """
        assert seed_periods_today
        resp = auth_client.get(
            "/templates/preview-recurrence",
            query_string=cadence_payload(unit=unit, interval_n=interval_n),
        )

        assert resp.status_code == 200, label
        assert b"Next 1 occurrences" in resp.data, label

    @pytest.mark.parametrize(
        ("placement", "expected"),
        [
            # 01-06, 01-27, 02-17, 03-10 and 03-31 each in the paycheck that
            # contains it.  01-30 - 02-12 and 03-13 - 03-26 hold none of them.
            (
                PeriodPlacementEnum.CONTAINING_DATE,
                [
                    "Jan 02, 2026 - Jan 15, 2026",
                    "Jan 16, 2026 - Jan 29, 2026",
                    "Feb 13, 2026 - Feb 26, 2026",
                    "Feb 27, 2026 - Mar 12, 2026",
                    "Mar 27, 2026 - Apr 09, 2026",
                ],
            ),
            # The same five dates, each in the first paycheck starting on or
            # after it: 01-16, 01-30, 02-27, 03-13 and 04-10.
            (
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                [
                    "Jan 16, 2026 - Jan 29, 2026",
                    "Jan 30, 2026 - Feb 12, 2026",
                    "Feb 27, 2026 - Mar 12, 2026",
                    "Mar 13, 2026 - Mar 26, 2026",
                    "Apr 10, 2026 - Apr 23, 2026",
                ],
            ),
        ],
        ids=lambda value: getattr(value, "value", None),
    )
    def test_the_week_unit_previews_because_it_can_now_be_saved(
        self, auth_client, seed_periods, placement, expected,
    ):
        """The WEEK unit previews the paychecks that fund it (plan step R8-b).

        **The unsavable cadence this case pinned has MOVED THREE TIMES, and
        each move closed a real gap.**  It was ``(2, MONTH)`` until plan step
        R7c-c freed the interval, then ``(1, YEAR, first paycheck)`` until plan
        step **R8-a** measured that refusal stale, then the ``WEEK`` unit until
        plan step **R8-b** dated a weekly row from its occurrence.  The preview
        refused the unit while the save would; the save takes it now, so the
        preview must show what saving would produce.  The one reading still
        refused is graded by
        ``test_preview_refuses_a_cadence_the_form_does_not_offer``.

        **It asserts the exact list, at both placements.**  It asserted only
        that "occurrences" appeared until an adversarial review of R8-b
        measured that it could not see a wrong paycheck or the two placements
        swapped.  seed_periods is ten 14-day paychecks from 2026-01-02, and
        the rule fires every 21 days from Tuesday 2026-01-06.  Three weeks,
        not two: two weeks is one paycheck on this schedule, so ``(2, WEEK)``
        previews the same list "every paycheck" does and a unit misread as
        paychecks would pass.  Not one week either: ``(1, WEEK)`` lists each
        paycheck once per occurrence, twice here, which plan#2604 records as
        a defect, and this case does not pin it.
        """
        assert seed_periods[0].start_date == date(2026, 1, 2), (
            "precondition: the paychecks the expected lists name"
        )
        resp = auth_client.get(
            "/templates/preview-recurrence",
            query_string=cadence_payload(
                unit=RecurrenceUnitEnum.WEEK, interval_n=3,
                placement=placement, starts_on=date(2026, 1, 6),
            ),
        )

        assert resp.status_code == 200
        assert re.findall(r"<li>(.*?)</li>", resp.data.decode()) == expected

    def test_the_cadence_that_case_refused_is_now_SAVABLE(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``(2, MONTH)`` previews real dates, because it can now be stored.

        The first cadence the case above pinned as unsavable, and the one that
        says plan step R7c-c actually landed rather than merely moving a
        refusal around:
        "every other month" is the cadence this whole arc exists for -- it
        resolved and walked correctly from plan step R3 and had nowhere to be
        written -- so the preview showing its dates is the arc's own thesis
        arriving at the surface a user reads.
        """
        assert seed_periods_today
        resp = auth_client.get(
            "/templates/preview-recurrence",
            query_string=cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, interval_n=2,
            ),
        )

        assert resp.status_code == 200
        assert b"No preview" not in resp.data
        assert b"occurrences" in resp.data

    @pytest.mark.parametrize(
        ("label", "unit", "placement"),
        [
            # The WEEK unit was the other case here until plan step R8-b made
            # it authorable; it previews now
            # (``test_the_week_unit_previews_because_it_can_now_be_saved``).
            #
            # Resolves: the pay-period anchor does not read the placement
            # at all.  Offering it would store a choice the edit form cannot
            # preselect, which is plan ledger row D32's hazard by another door.
            (
                "an inert placement", RecurrenceUnitEnum.PERIOD,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
        ],
    )
    def test_preview_refuses_a_cadence_the_form_does_not_offer(
        self, app, auth_client, seed_user, seed_periods_today,
        label, unit, placement,
    ):
        """The preview asks the OFFER SET, not what happens to walk.

        **The load-bearing case of plan step R7c-c's refusal move.**  This
        reading resolves and walks perfectly well; what made it unpreviewable
        before that step was ``encode_cadence``, which the preview inherited by
        building its transient rule through the write door.  Deleting the
        encoder deleted that inheritance -- measured, the WEEK unit previewed
        five real dates for a cadence the schema refuses -- so the door asks
        ``require_authorable_cadence`` instead, over the same set the picker
        renders.

        Not reachable by clicking; this is the crafted-args door.
        """
        assert seed_periods_today
        resp = auth_client.get(
            "/templates/preview-recurrence",
            query_string=cadence_payload(unit=unit, placement=placement),
        )

        assert resp.status_code == 200, label
        assert b"No preview" in resp.data, label
        assert b"occurrences" not in resp.data, label

    def test_preview_ignores_an_unparseable_end_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?end_date=garbage`` previews the unbounded rule, it does not 500.

        The one unvalidated argument the resolution door cannot refuse:
        ``date.fromisoformat`` runs BEFORE the seam, so its ``ValueError``
        never reaches ``RecurrenceResolutionError``.  A neutral review of plan
        step R4a found it after the route's docstring had already claimed the
        whole class was closed.  An unparseable bound is dropped rather than
        refused -- see ``_recurrence_preview._submitted_iso_date``, which
        parses BOTH date bounds since plan step R7b-4 gave the opening one a
        control (two copies of "parse it or drop it" is the shape that leaves
        one of them missing a fix the other got).
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                    "day_of_month": "15",
                    "end_date": "garbage",
                },
            )

            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_every_period(self, app, auth_client, seed_user, seed_periods_today):
        """Preview for the every-paycheck cadence returns an occurrence list."""
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string=cadence_payload(),
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_every_n_periods_without_a_start_period(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Previewing "every N paychecks" with no first paycheck chosen is a 200.

        It was a 500 before plan step R2c-1, and the cause is worth keeping:
        the route hand-built a transient rule, and a SQLAlchemy column default
        is applied at INSERT rather than at instantiation -- so
        ``offset_periods`` stayed ``None`` and period selection computed
        ``period_index - None``.  Routing the preview through the authoring
        seam fixed it incidentally, because resolution always emits an int.
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string=cadence_payload(interval_n=2),
            )
            assert resp.status_code == 200
            assert b"occurrences" in resp.data

    def test_preview_ignores_a_start_period_id_entirely(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """The endpoint has no start-period argument left to attack.

        **Audit finding H3 -- pay-period structure disclosure -- was closed by
        REMOVING the surface at plan step R7b-4, not by guarding it.** The
        preview owner-checked a submitted ``start_period_id`` and fell through
        to the user's own data when it was not theirs. The recurrence's opening
        bound is a DATE now, so the endpoint reads no period id at all: a
        foreign one, a nonexistent one and a garbage one are the same
        unrecognised query argument.

        Both shapes are asserted in ONE case because they now have ONE answer,
        and this replaces two tests that compared the response to a baseline
        with the argument dropped -- a comparison that became a tautology for
        ANY ignored argument, while their docstrings went on describing an
        ownership check no code performs.
        """
        with app.app_context():
            baseline = auth_client.get(
                "/templates/preview-recurrence",
                query_string=cadence_payload(),
            )
            assert baseline.status_code == 200
            assert b"occurrences" in baseline.data

            for label, period_id in (
                ("another user's", seed_second_periods[0].id),
                ("nonexistent", 999999),
            ):
                resp = auth_client.get(
                    "/templates/preview-recurrence",
                    query_string={
                        **cadence_payload(),
                        "start_period_id": period_id,
                    },
                )
                assert resp.status_code == 200, label
                assert resp.data == baseline.data, label

    def test_preview_honours_a_submitted_starts_on(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """What REPLACED the period argument, and it must actually bind.

        The case above only says the retired argument is ignored, which any
        unrecognised key satisfies. This says the new one is READ: a preview
        bounded to a later paycheck must not list dates before it. Without
        this pair the endpoint could be ignoring both.
        """
        with app.app_context():
            later = seed_periods_today[-1]

            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    **cadence_payload(),
                    "starts_on": later.start_date.isoformat(),
                },
            )

            assert resp.status_code == 200
            body = resp.data.decode()
            assert later.start_date.strftime("%b %d, %Y") in body
            for earlier in seed_periods_today[:-1]:
                assert earlier.start_date.strftime("%b %d, %Y") not in body

    def test_create_recurring_template_ignores_a_foreign_start_period(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user, seed_second_periods,
    ):
        """POST /templates cannot be attacked through a start period at all.

        deep-quality-hunt #21/#24 was a real IDOR: the persist path's owner
        probe ran only for EVERY_N_PERIODS, so a recurring template wrote a
        foreign ``start_period_id`` onto its RecurrenceRule unchecked and the
        generation boundary came from the victim's pay period.  It was closed
        by making the probe universal.

        **Plan step R7b-4 removed the surface instead of guarding it.**  A
        transaction template's recurrence takes a DATE (``start_date``), and
        ``TemplateCreateSchema`` no longer declares ``start_period_id`` at all
        -- the field went to the TRANSFER schema, where its remaining job
        lives (placing a one-time transfer).  Marshmallow's ``EXCLUDE`` drops
        the key, so a crafted POST cannot express the attack: there is nothing
        to reject because there is nothing to accept.

        Asserted as an IGNORE rather than a refusal, and both halves matter --
        the template IS created (the foreign key changed nothing about a valid
        submission) and its rule carries no start period.  A test that only
        checked for a flash would pass against a route that had silently
        started storing the value again.
        """
        with app.app_context():
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            category = seed_user["categories"]["Rent"]
            # DERIVED, never a literal.  A seeded world "holds rows dated from
            # ``display_today()``" (tests/conftest.py), and this module is not
            # under the conftest that freezes the clock -- that one covers
            # tests/test_services/ only.  A hardcoded day therefore sits inside
            # the calendar when it is written and DECAYS INTO THE PAST as the
            # window walks forward, at which point ``span_containing`` returns
            # None and the assertion below dies on an AttributeError rather
            # than reporting anything.  ``date(2026, 7, 6)`` did exactly that
            # on 2026-09-07, when the seeded calendar opened 2026-07-13.
            #
            # One day past a span's start is mid-span for any cadence above a
            # single day, so the normalisation this test checks is still
            # exercised rather than collapsing into a no-op the way today's
            # date would on the one day in fourteen that IS a span start.
            own_span = calendar_for(
                seed_user["user"].id
            ).span_containing(display_today())
            stated_start = own_span.start_date + timedelta(days=1)
            resp = auth_client.post("/templates", data={
                "name": "Recurring IDOR Template",
                "amount": "1500.00",
                "category_id": category.id,
                "transaction_type_id": txn_type.id,
                "account_id": seed_user["account"].id,
                **cadence_payload(starts_on=stated_start),
                # Second user's period, on a field this schema does not have.
                "start_period_id": str(seed_second_periods[0].id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            template = (
                db.session.query(TransactionTemplate)
                .filter_by(name="Recurring IDOR Template")
                .one()
            )
            assert template.recurrence_rule is not None
            # The rule starts where THIS owner's submission said, never
            # anywhere the foreign period could put it.  ``starts_on`` is NOT
            # NULL from plan step R7c-b, so "carries no start" is no longer an
            # expressible outcome and the honest check is that the stored date
            # is the submitted one, normalised onto this owner's own paycheck.
            assert template.recurrence_rule.starts_on == (
                calendar_for(seed_user["user"].id)
                .span_containing(stated_start).start_date
            )


class TestThePreviewHonoursTheClosingBound:
    """The preview must show what SAVING would produce, bound included.

    Its module docstring states that contract, and plan step R7b-3 broke it
    for one review cycle: the endpoint composed the bound through the
    submission door and defaulted the mode to "never", while the script sent
    the two VALUE keys and no mode.  So every preview was unbounded, and a
    user setting "ends on a date" was shown occurrences running past it -- on
    the one surface whose whole job is to say when a commitment stops.

    Neither the frozen oracle nor the 46-rule round trip could see it: both
    instruments read a rule, and this defect lived in a query string.
    """

    def _preview(self, auth_client, seed_user, **extra):
        """GET the preview fragment for an every-paycheck cadence.

        ``starts_on`` rides with the cadence because the endpoint has nothing
        to preview without one since plan step R7c-b: a rule cannot be authored
        without stating when it first happens, so a query naming no start gets
        the same muted line a query naming no cadence does.

        Args:
            auth_client: The signed-in client.
            seed_user: The seeded owner fixture.
            **extra: Additional query args, typically the bound's controls.

        Returns:
            The decoded fragment body.
        """
        return auth_client.get("/templates/preview-recurrence", query_string={
            "recurrence_unit": str(
                ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD),
            ),
            "recurrence_placement": str(
                ref_cache.period_placement_id(
                    PeriodPlacementEnum.CONTAINING_DATE,
                ),
            ),
            "interval_n": "1",
            "starts_on": display_today().isoformat(),
            **extra,
        }).data.decode()

    def test_an_unbounded_preview_lists_occurrences(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: without a bound there are periods to show."""
        with app.app_context():
            assert "occurrences" in self._preview(auth_client, seed_user)

    def test_a_date_bound_narrows_the_preview(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A bound BEFORE the schedule opens leaves nothing to show.

        The unbounded case above lists occurrences from the same schedule, so
        the difference is the bound and nothing else.
        """
        with app.app_context():
            body = self._preview(
                auth_client, seed_user,
                recurrence_end_mode="on_date",
                end_date="2000-01-01",
            )

            assert "No matching periods found" in body

    def test_a_count_bound_narrows_the_preview(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"After 1 occurrence" shows exactly one, where unbounded shows five.

        The count is applied from the rule's FIRST OCCURRENCE, which the query
        states, so a one-occurrence rule fires once ON that date and stops.
        The preview opens the list there too (plan step R7c-b: showing five
        occurrences the user cannot see the start of answers a question nobody
        asked), so the single occurrence is visible rather than already past.

        It asserted "No matching periods found" until that step, when the
        anchor was the SCHEDULE's opening rather than an authored date -- the
        one occurrence then landed on the first payday, behind today's window.
        The count is what this measures either way: read against the unbounded
        case above, which lists five from the very same schedule, the
        difference is the bound and nothing else.  Before the mode reached this
        endpoint at all, both answered identically.
        """
        with app.app_context():
            body = self._preview(
                auth_client, seed_user,
                recurrence_end_mode="after_occurrences",
                max_occurrences="1",
            )

            assert "Next 1 occurrences" in body
            assert "Next 5 occurrences" in self._preview(
                auth_client, seed_user,
            ), "the unbounded control must differ, or this measures nothing"

    def test_a_query_stating_both_bounds_is_a_muted_line_not_a_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A hand-crafted query naming two bounds is refused, not crashed.

        It reaches the SUBMISSION door, so it earns the user-input refusal
        rather than being reported as a row written around the CHECK -- there
        is no row.
        """
        with app.app_context():
            resp = auth_client.get(
                "/templates/preview-recurrence",
                query_string={
                    "recurrence_unit": str(
                        ref_cache.recurrence_unit_id(
                            RecurrenceUnitEnum.PERIOD,
                        ),
                    ),
                    "recurrence_placement": str(
                        ref_cache.period_placement_id(
                            PeriodPlacementEnum.CONTAINING_DATE,
                        ),
                    ),
                    "recurrence_end_mode": "whenever",
                },
            )

            assert resp.status_code == 200
            assert b"No preview for this cadence" in resp.data
