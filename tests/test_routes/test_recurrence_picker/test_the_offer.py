"""The recurrence picker: nothing offered is unauthorable, the script reads what the server
serialized, and each form renders its offer once.
"""

import json
import re
from pathlib import Path

import pytest

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.recurrence import (
    authorable_cadences,
    cadences_without_a_card,
    cadence_options,
    is_authorable,
    modelled_placement,
    modelled_unit,
    picker_model,
)
from tests.oracles.recurrence_baseline import EVERY_N_PERIODS, QUARTERLY, ANNUAL

from ._helpers import _tag, _options, _template_with_cadence


# ── The offer set ────────────────────────────────────────────────────


class TestNothingOfferedIsUnauthorable:
    """Property 1: every offered pair is one the write door accepts."""

    def test_every_offered_pair_is_authorable(self, app):
        """Swept over the whole offer set, not sampled.

        The producer and the validator read ONE table, so this cannot fail
        without the derivation in ``_frequency`` being broken -- which is
        precisely the coupling worth a gate, because the two were separate
        producers that merely agreed until plan step R7b-2.
        """
        with app.app_context():
            options = cadence_options(authorable_cadences())

            assert options, "the offer set is empty"
            for option in options:
                unit = modelled_unit(option.unit_id)
                placement = modelled_placement(option.placement_id)
                assert unit is not None, option
                assert placement is not None, option
                # Every positive interval is authorable on an offered pair
                # since plan step R7c-c; 1 stands for the class.
                assert is_authorable(1, unit, placement), (
                    f"the picker offers {(unit, placement)}, which the write "
                    f"door cannot store"
                )

    @pytest.mark.parametrize("placement", list(PeriodPlacementEnum))
    def test_the_unauthorable_reading_is_really_unauthorable(
        self, app, placement,
    ):
        """The premise the sweep above rests on, stated as a measurement.

        Without this the sweep passes against an ``is_authorable`` that says
        yes to everything.  The one reading still refused is a pay-period
        cadence funded from a LATER paycheck: its placement is inert, because
        both placements carry a payday back to its own paycheck, so offering
        it would store a choice the edit form cannot preselect.

        **The refused reading has MOVED THREE TIMES, each move closing a real
        gap.**  It was ``(1, YEAR, first paycheck)`` until plan step R8-a, whose
        refusal named a first-occurrence derivation ruling **R-R16** had
        deleted; then the ``WEEK`` unit until plan step **R8-b**, which dates a
        weekly row from its occurrence.  Every other unit is authorable at
        BOTH placements, swept here because a rule admitting one placement and
        not the other would pass a case that pinned only one.
        """
        with app.app_context():
            assert not is_authorable(
                1, RecurrenceUnitEnum.PERIOD,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )
            assert is_authorable(1, RecurrenceUnitEnum.WEEK, placement)
            assert is_authorable(1, RecurrenceUnitEnum.YEAR, placement)
            assert is_authorable(1, RecurrenceUnitEnum.MONTH, placement)

    def test_the_month_unit_offers_BOTH_placements(self, app):
        """Property 2: plan ledger row **D32**'s defect ceasing to exist.

        A placement belonged to the ``(unit, interval)`` PAIR while
        ``MONTHLY_FIRST`` had no quarterly twin, so raising a monthly rule's
        interval silently reassigned its funding choice.  With the closed set
        gone, the MONTH unit offers both placements and the interval selects no
        offer at all -- so there is no reassignment left to notice.
        """
        with app.app_context():
            month_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH)
            month_placements = {
                option.placement_id for option in cadence_options(authorable_cadences())
                if option.unit_id == month_id
            }

            assert month_placements == {
                ref_cache.period_placement_id(member)
                for member in PeriodPlacementEnum
            }

    def test_the_week_unit_is_offered_worded_and_wired(self, app):
        """Plan step R8-b: weeks are offered at both placements, with their own copy.

        Both placements, because a weekly date can fall strictly inside a
        paycheck, where the two fund it differently.  "week" after a 1 and
        "weeks" after any other count.  The wire says there is no day of the
        month to ask about, so the script keeps the "repeating on" control
        hidden, and that two occurrences can share a month, so it offers the
        per-month ceiling.
        """
        with app.app_context():
            week_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.WEEK)
            weekly = [
                option for option in cadence_options(authorable_cadences())
                if option.unit_id == week_id
            ]

            assert {option.placement_id for option in weekly} == {
                ref_cache.period_placement_id(member)
                for member in PeriodPlacementEnum
            }
            for option in weekly:
                assert (option.unit_label_one, option.unit_label_many) == (
                    "week", "weeks",
                )
                assert option.wire.has_day_of_month_coordinate is False
                assert option.wire.can_repeat_within_month is True


# ── The rendered controls ────────────────────────────────────────────


class TestTheScriptCanReadWhatTheServerSerialized:
    """The wire contract between ``picker_model`` and ``recurrence_form.js``.

    **Not a substitute for driving the form in a browser**, and it is here
    because it covers the ONE failure in that gap a Python suite can see: the
    script reads each fact off the JSON by NAME, and a renamed or dropped field
    is ``undefined`` in JavaScript rather than an error.  ``undefined`` is
    falsy, so the Day of Month row would simply stop appearing -- no console
    error, no failing request, and a monthly bill authored with no day.

    The visibility behaviour itself (which control shows when, and that the
    hidden one does not submit) still needs a browser.
    """

    #: The names the script dereferences off one entry of
    #: ``data-cadence-options``.  Every one of these is a property access on an
    #: option object in ``recurrence_form.js``.
    #: ``months_per_unit`` LEFT this set at plan step R7c-b, with the Month
    #: control it fed: ruling R-R16 put the cycle's month on ``starts_on``, so
    #: there is no control to narrow and no fact for the script to read.
    #:
    #: ``has_day_of_month_coordinate`` JOINED it at the same step, keyed on
    #: the UNIT, and it decides the "repeating on" control.  Its pair-keyed
    #: sibling ``schedules_on_day_of_month`` decided the Due Day row and LEFT
    #: this set -- and the wire -- at plan step recurrence:R5-a with that row
    #: (ruling R-R96).
    #: The two disagree for exactly a first-paycheck month cadence, and the
    #: script reading the pair-keyed fact where it needed the unit-keyed one
    #: silently erased a month-end rule's ``nominal_day`` on an ordinary
    #: edit.
    #:
    #: ``interval_n`` LEFT it at plan step R7c-c: every positive interval is
    #: authorable on every offered pair, so an offer names none and the script
    #: has no interval to filter on.
    #:
    #: ``can_repeat_within_month`` JOINED at plan step salary:R15-a: the
    #: per-month ceiling control shows and enables only beside a unit that
    #: can put two occurrences in a month, and that is a fact the SERVER
    #: states about the offer rather than one the script infers.
    _READ_BY_THE_SCRIPT = frozenset({
        "unit_id", "placement_id",
        "has_day_of_month_coordinate", "can_repeat_within_month",
    })

    @staticmethod
    def _script_property_reads(app):
        """Return every option property ``recurrence_form.js`` dereferences.

        Read FROM the script rather than restated beside it, which an
        adversarial review of plan step R7b-2 required: a hand-written list
        catches the server dropping a field and is blind to the script starting
        to read a new one -- and that direction is the silent half, because a
        property JavaScript cannot find is ``undefined``, not an error, and
        ``undefined`` is falsy.

        Args:
            app: The application, whose static folder holds the script.

        Returns:
            set[str]: The property names, scoped to the identifiers the file
            binds to an option (``o`` inside its filter callbacks, ``chosen``
            for the selected triple, ``opt`` for a DOM option is deliberately
            absent -- that one is an ``<option>`` element, not an offer).
        """
        source = (
            Path(app.static_folder) / "js" / "recurrence_form.js"
        ).read_text(encoding="utf-8")
        return set(re.findall(r"\b(?:o|chosen)\.([a-z_]+)\b", source))

    def test_the_script_and_the_server_name_the_same_fields(self, app):
        """The wire contract, checked in BOTH directions.

        A field the server stops emitting breaks the script silently; a field
        the script starts reading that the server never emitted breaks it
        equally silently.  Reading one side from the JS source and the other
        from the serialized payload is what makes either direction fail here.
        """
        reads = self._script_property_reads(app)

        assert reads == self._READ_BY_THE_SCRIPT, (
            f"recurrence_form.js now dereferences {sorted(reads)}; update "
            f"_READ_BY_THE_SCRIPT and picker_model together"
        )

        with app.app_context():
            emitted = json.loads(picker_model(authorable_cadences()).options_json)

            assert emitted, "the serialized offer set is empty"
            for entry in emitted:
                missing = reads - set(entry)
                assert not missing, (
                    f"recurrence_form.js reads {sorted(missing)}, which "
                    f"picker_model does not serialize; JavaScript would read "
                    f"undefined and silently hide a control"
                )

    def test_the_json_carries_every_option_the_form_rendered(self, app):
        """The script filters the SAME set the three controls were built from.

        Not a round-trip of ``json.dumps`` -- an adversarial review pointed out
        the obvious form of this assertion is ``json.loads(json.dumps(x)) == x``
        and cannot fail.  What is checked is that the serialized set covers
        every triple the projections drew from, so no cadence the user can
        select is one the script has no entry for.
        """
        with app.app_context():
            model = picker_model(authorable_cadences())
            emitted = json.loads(model.options_json)
            serialized_keys = {
                (entry["unit_id"], entry["placement_id"])
                for entry in emitted
            }

            for projection in (model.units, model.placements):
                for option in projection:
                    assert (
                        option.unit_id, option.placement_id,
                    ) in serialized_keys

    def test_the_projections_are_subsets_of_the_offer_set(self, app):
        """Each control's list is drawn FROM the offer set, not beside it."""
        with app.app_context():
            model = picker_model(authorable_cadences())

            for projection in (model.units, model.placements):
                assert set(projection) <= set(model.options)

    def test_each_projection_holds_one_entry_per_distinct_key(self, app):
        """The de-duplication rule, per control.

        The interval ``<select>`` plan step R7c-c deleted is what shipped the
        defect this pins: MONTHLY and MONTHLY FIRST were two offers over one
        ``(1, MONTH)`` interval, so an un-projected list rendered "1 month"
        twice and marked BOTH selected.  The UNIT select carries the same
        hazard -- the MONTH unit still has two offers -- which is why the rule
        outlived the control that exposed it.
        """
        with app.app_context():
            model = picker_model(authorable_cadences())

            units = [option.unit_id for option in model.units]
            placements = [option.placement_id for option in model.placements]

            assert len(units) == len(set(units))
            assert len(placements) == len(set(placements))
            assert len(model.units) < len(model.options), (
                "the unit projection is not de-duplicating anything, so this "
                "rule is no longer being exercised"
            )


class TestBothFormsRenderTheSameControls:
    """The one producer serves both template kinds."""

    @pytest.mark.parametrize(
        "url", ["/templates/new", "/transfers/new"],
    )
    def test_the_unit_select_offers_each_offered_unit_once(
        self, app, auth_client, seed_user, seed_periods_today, url,
    ):
        """One ``<option>`` per unit however many readings it has.

        The MONTH unit has two offered readings (both placements) and must
        still render a single entry, or the user is asked to choose between two
        things called "months".  The set each form renders is the offer set
        less a card's due dates (``cadences_without_a_card``, plan step
        credit_card:CC-6): no form offers them until a transfer into a card
        can take them.
        """
        assert seed_periods_today
        with app.app_context():
            expected = []
            for option in cadence_options(cadences_without_a_card()):
                if str(option.unit_id) not in expected:
                    expected.append(str(option.unit_id))

        resp = auth_client.get(url)
        body = resp.data.decode()

        assert resp.status_code == 200
        # The leading "" is the "Does not repeat" entry, which is the form's
        # own and not a cadence (plan step R2e-3).
        assert [
            value for value, _ in _options(body, "recurrence_unit")
        ] == [""] + expected

    def test_both_forms_offer_identical_cadences(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A user meets one vocabulary whichever kind they are creating."""
        assert seed_periods_today
        with app.app_context():
            transaction = auth_client.get("/templates/new").data.decode()
            transfer = auth_client.get("/transfers/new").data.decode()

        for select_id in ("recurrence_unit", "recurrence_placement"):
            assert _options(transaction, select_id) == _options(
                transfer, select_id,
            ), select_id

    @pytest.mark.parametrize(
        "url", ["/templates/new", "/transfers/new"],
    )
    def test_the_interval_box_does_not_submit_on_a_create_form(
        self, app, auth_client, seed_user, seed_periods_today, url,
    ):
        """Property 3, at the "Does not repeat" default.

        A disabled control does not submit, and an interval beside no unit
        states half a cadence.  On a create form no cadence is chosen, so it
        must not submit.

        **There were TWO controls posting ``interval_n`` until plan step
        R7c-c** -- a free number box and a ``<select>`` of the month intervals
        the closed set could name -- with exactly one enabled, because two
        spellings of one field must never reach the schema together.  Every
        interval is authorable on every unit now, so there is one control and
        the question is whether a cadence was chosen at all.
        """
        assert seed_periods_today
        resp = auth_client.get(url)
        body = resp.data.decode()

        assert "disabled" in _tag(body, "interval_n")

    @pytest.mark.parametrize(
        "cadence",
        [EVERY_N_PERIODS, QUARTERLY, ANNUAL],
        ids=lambda value: value.label,
    )
    def test_the_interval_box_submits_on_an_edit_form(
        self, app, auth_client, seed_user, seed_periods_today, cadence,
    ):
        """Property 3 on the form that starts with a cadence chosen.

        The server renders the state matching the stored rule, so the form is
        correct before any script runs -- which is what a user with a slow
        connection submits.  Swept over a paycheck cadence and two calendar
        ones because the ENABLED state used to depend on which of the two
        controls a unit used, and a rule that only exercised one unit would
        have missed the other's control being left disabled.
        """
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_cadence(
                seed_user, cadence,
                interval_n=3 if cadence == EVERY_N_PERIODS else None,
            ).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert "disabled" not in _tag(body, "interval_n")

    @pytest.mark.parametrize(
        "url", ["/templates/new", "/transfers/new"],
    )
    def test_the_funding_row_is_rendered_on_both_forms(
        self, app, auth_client, seed_user, seed_periods_today, url,
    ):
        """Property 2: the "Funded from" control is always on the page.

        Plan ledger row **D32**, developer ruling 2026-08-16.  The row used to
        hide itself whenever the chosen cadence admitted one placement, which
        is how a bill's funding rule came to change with nothing on screen
        saying so.  ONE cadence still admits one -- paychecks, where the
        placement is inert -- and for it the row renders with the help text
        that explains it rather than disappearing.  It was TWO until plan step
        **R8-a**, which admitted the YEAR unit's deferring reading; that
        refusal cited a first-occurrence derivation ruling R-R16 had deleted.

        Both sentences are rendered by the SERVER, which is what the script
        swaps between: a script that authored copy could word the state two
        ways.
        """
        assert seed_periods_today
        body = auth_client.get(url).data.decode()

        assert '<select id="recurrence_placement"' in body
        assert 'id="placement-help"' in body
        assert "Which paycheck pays for each occurrence." in body
        assert (
            "This cadence has one funding rule, so there is nothing to choose."
        ) in body
