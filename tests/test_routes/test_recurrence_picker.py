"""
Shekel Budget App -- the recurrence picker offers only what can be STORED (R7b-2)

The form used to pick a NAME from the closed pattern set.  It authors the two
axes the write door takes -- how often (``interval_n`` plus ``unit``) and which
paycheck funds an occurrence (``placement``).

**The claim this file exists to hold the code to is that the offer set is
DERIVED from the producer that would otherwise REFUSE the cadence.**  Before
plan step R7b-2 the picker iterated the closed pattern set while the encoder
read its own table, so "nothing offers an unauthorable cadence" held only
because the two sets happened to coincide -- protected by no gate.
``authorable_cadences`` is that producer's own answer and ``_picker`` words it,
so the refusal is unreachable through the form rather than fenced behind it
(developer ruling 2026-08-12).

**Plan step R7c-c changed WHICH producer binds, and two properties with it.**
Storage was the constraint while a cadence had to have a name; every reading
can be stored now, so what is left is whether the application can HONOUR it.
The interval is a free number box for every unit, and the
``(unit, interval)`` pair dependency is gone -- which is plan ledger row
**D32**'s defect ceasing to exist.

**Plan step R8-a changed the gate itself.**  R7c-c left it on
``anchor_family``, a three-valued router selecting between first-occurrence
derivations ruling **R-R16** had deleted; R8-a replaced it with two rules over
live facts, which widened the offer set by exactly one reading (a year-scale
cadence funded from a later paycheck) and left one withheld (the ``WEEK``
unit, until plan step R5).

Four properties, each of which fails differently:

1. every offered ``(unit, placement)`` is one the write door accepts -- swept
   over the whole rendered offer set, not sampled;
2. the "Funded from" row is RENDERED for every cadence, and says so when the
   cadence admits one funding rule (the rest of D32, developer ruling
   2026-08-16): hiding it is how a bill's funding changed with nothing on
   screen saying so;
3. the one ``interval_n`` input is enabled exactly when a cadence is chosen,
   because a disabled control does not submit and an interval beside no unit
   states half a cadence;
4. an EDIT form starts on the cadence its rule actually means, and a rule whose
   stored cadence the application cannot read renders the controls unset above
   a flashed explanation -- and cannot be destroyed by saving it unchanged.

The state property 4 needs is manufactured here: ``ref_cache.init`` requires
every ENUM member to have a ``ref`` row and says nothing about the reverse, so
a surplus row is a state the schema permits.
"""
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, RecurrenceUnit
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.services.recurrence import _picker
from app.services.recurrence import (
    END_BOUND_KINDS,
    UNREADABLE_CADENCE_MESSAGE,
    EndsAfterOccurrences,
    EndsOnDate,
    NeverEnds,
    authorable_cadences,
    cadence_options,
    end_bound_options,
    is_authorable,
    modelled_placement,
    modelled_unit,
    picker_model,
)
from tests._test_helpers import cadence_payload
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    MONTHLY_FIRST,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _unmodelled_unit_id(name="Blue Moon"):
    """Insert and commit a ``ref.recurrence_units`` row with no enum member.

    Committed rather than flushed because the route under test runs in its own
    request and session.

    **It planted a surplus ``ref.recurrence_patterns`` row until plan step
    R7c-c**, which dropped the column a rule pointed at one with.  The state a
    rule can still reach is a ``unit_id`` the enums do not model -- a seed the
    enums have diverged from, a hand edit, a partial restore -- so the same
    class of unreadable rule is manufactured on the axis that survived.

    Args:
        name: The row's name; asserted not to collide with an enum value.

    Returns:
        int: The new row's primary key.
    """
    assert name not in {member.value for member in RecurrenceUnitEnum}
    row = RecurrenceUnit(name=name)
    db.session.add(row)
    db.session.commit()
    return row.id


def _assert_unreadable(unit_id):
    """Assert the seam cannot read *unit_id*, i.e. the door earns its keep.

    Without this, a test that only asserts "the route refused" cannot tell a
    door that refuses bad input from a door that refuses everything.

    Args:
        unit_id: The id the surface just refused.
    """
    assert modelled_unit(unit_id) is None


def _savings_account(seed_user):
    """Create a second account so a transfer template has a destination."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _tag(html, element_id):
    """Return the opening tag of the element carrying ``id="element_id"``.

    ``field_is_disabled`` keys on ``name=``, which cannot separate the two
    interval controls: both post ``interval_n``, which is the whole point of
    exactly one of them being enabled.

    Args:
        html: The rendered page.
        element_id: The ``id`` attribute to find.

    Returns:
        str: The opening tag, including its attributes.

    Raises:
        AssertionError: The element is absent, so a typo fails loud.
    """
    match = re.search(rf'<(?:input|select)\b[^>]*\bid="{element_id}"[^>]*>', html)
    assert match is not None, f'id="{element_id}" not found in rendered HTML'
    return match.group(0)


def _options(html, select_id):
    """Return ``(value, is_selected)`` for each option of one ``<select>``.

    Args:
        html: The rendered page.
        select_id: The select's ``id``.

    Returns:
        list[tuple[str, bool]]: In document order.
    """
    block = re.search(
        rf'<select\b[^>]*\bid="{select_id}"[^>]*>(.*?)</select>', html, re.DOTALL,
    )
    assert block is not None, f'<select id="{select_id}"> not found'
    return [
        (match.group(1), "selected" in match.group(0))
        for match in re.finditer(
            r'<option\b[^>]*\bvalue="([^"]*)"[^>]*>', block.group(1),
        )
    ]


def _option_labels(html, select_id):
    """Return the visible TEXT of each option of one ``<select>``.

    :func:`_options`' sibling, reading the half a value comparison cannot see.
    Whitespace is collapsed because the template wraps long labels across
    lines.

    Args:
        html: The rendered page.
        select_id: The select's ``id``.

    Returns:
        list[str]: In document order.
    """
    block = re.search(
        rf'<select\b[^>]*\bid="{select_id}"[^>]*>(.*?)</select>', html, re.DOTALL,
    )
    assert block is not None, f'<select id="{select_id}"> not found'
    return [
        " ".join(text.split())
        for text in re.findall(
            r"<option\b[^>]*>(.*?)</option>", block.group(1), re.DOTALL,
        )
    ]


def _selected(html, select_id):
    """Return the single selected option value of one ``<select>``.

    ``None`` when none is selected -- which is never harmless: a ``<select>``
    with nothing selected silently submits its FIRST option, so "no selection"
    and "the first entry" are the same request.

    Raises:
        AssertionError: More than one option carries ``selected``.
    """
    chosen = [value for value, is_selected in _options(html, select_id) if is_selected]
    assert len(chosen) <= 1, f"multiple options selected in {select_id}: {chosen}"
    return chosen[0] if chosen else None


def _expense_type_id():
    """Return the Expense transaction-type id."""
    return ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


def _rule_on_cadence(owner, cadence, interval_n=None, unit_id=None):
    """Create and flush a rule of *cadence* on *owner*, bypassing the write door.

    Deliberately constructed rather than authored: ``author_rule`` resolves
    before it writes, so it REFUSES an unreadable cadence -- which is the guard
    under test -- and only a direct construction can produce the row a diverged
    ``ref`` seed would leave behind.

    **The cadence is a SHARED constant, not hard-coded here**, and the
    difference matters more than the fixture's convenience: a row whose columns
    disagree with each other is one no application path can write, and a sweep
    over such rows measures the fixture rather than the mapping.

    **The OWNER is still stated, even bypassing the door** (plan step R-F6):
    ``ck_recurrence_rules_one_owner`` refuses a rule belonging to nothing, so
    what this fixture bypasses is the RESOLUTION, not the ownership.  Assigned
    through ``owner.recurrence_rule`` so it stays kind-agnostic across the two
    definition kinds, exactly as ``author_rule`` does.

    Args:
        owner: The ``TransactionTemplate`` or ``TransferTemplate`` the rule
            belongs to.  Mutated: its ``recurrence_rule`` is set.
        cadence: One of :data:`BASELINE_CADENCES`.
        interval_n: The interval to store; defaults to the cadence's own.
        unit_id: A ``ref.recurrence_units`` id to store INSTEAD of the
            cadence's, for the unreadable-rule cases.

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    own_interval = 1 if cadence.interval_n is None else cadence.interval_n
    rule = RecurrenceRule(
        interval_n=own_interval if interval_n is None else interval_n,
        unit_id=(
            ref_cache.recurrence_unit_id(cadence.unit) if unit_id is None
            else unit_id
        ),
        placement_id=ref_cache.period_placement_id(cadence.placement),
        shift_id=ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        starts_on=date(2026, 1, 1),
    )
    owner.recurrence_rule = rule
    db.session.add(rule)
    db.session.flush()
    return rule


def _template_with_cadence(
    seed_user, cadence, interval_n=None, unit_id=None,
):
    """Create a committed transaction template carrying a rule of that cadence."""
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        name="Rent",
        default_amount=Decimal("1200.00"),
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=_expense_type_id(),
        account_id=seed_user["account"].id,
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    _rule_on_cadence(template, cadence, interval_n, unit_id)
    db.session.commit()
    return template


def _transfer_template_with_cadence(
    seed_user, destination, cadence, unit_id=None,
):
    """Create a committed transfer template carrying that cadence."""
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        name="To Savings",
        default_amount=Decimal("50.00"),
        from_account_id=seed_user["account"].id,
        to_account_id=destination.id,
        category_id=seed_user["categories"]["Rent"].id,
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    _rule_on_cadence(template, cadence, unit_id=unit_id)
    db.session.commit()
    return template


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
        yes to everything.  The ``WEEK`` unit is the reading the offer set
        withholds from plan step **R8-a**: a weekly occurrence is neither a
        payday nor a day of the month, so
        ``recurrence.compute_due_date`` has nothing to date its rows
        from and every one would carry the funding payday instead.  Plan step
        **R5** gives a row its own ``occurs_on`` and the unit becomes
        authorable with that deletion.

        **The case MOVED here at R8-a and the pair it replaced is now
        authorable.**  It was ``(1, YEAR, first paycheck)``, refused because
        ``anchor_family`` had no first-occurrence derivation for it -- a
        derivation ruling **R-R16** deleted at plan step R7c-b, leaving the
        refusal behind.  Swept over both placements, because the withholding is
        a property of the UNIT and a case pinning one placement would go green
        against a rule that had started admitting the other.
        """
        with app.app_context():
            assert not is_authorable(1, RecurrenceUnitEnum.WEEK, placement)
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
    def _script_property_reads():
        """Return every option property ``recurrence_form.js`` dereferences.

        Read FROM the script rather than restated beside it, which an
        adversarial review of plan step R7b-2 required: a hand-written list
        catches the server dropping a field and is blind to the script starting
        to read a new one -- and that direction is the silent half, because a
        property JavaScript cannot find is ``undefined``, not an error, and
        ``undefined`` is falsy.

        Returns:
            set[str]: The property names, scoped to the identifiers the file
            binds to an option (``o`` inside its filter callbacks, ``chosen``
            for the selected triple, ``opt`` for a DOM option is deliberately
            absent -- that one is an ``<option>`` element, not an offer).
        """
        source = (
            Path(__file__).resolve().parents[2]
            / "app" / "static" / "js" / "recurrence_form.js"
        ).read_text(encoding="utf-8")
        return set(re.findall(r"\b(?:o|chosen)\.([a-z_]+)\b", source))

    def test_the_script_and_the_server_name_the_same_fields(self, app):
        """The wire contract, checked in BOTH directions.

        A field the server stops emitting breaks the script silently; a field
        the script starts reading that the server never emitted breaks it
        equally silently.  Reading one side from the JS source and the other
        from the serialized payload is what makes either direction fail here.
        """
        reads = self._script_property_reads()

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
        things called "months".
        """
        assert seed_periods_today
        with app.app_context():
            expected = []
            for option in cadence_options(authorable_cadences()):
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


# ── The edit form's starting state ───────────────────────────────────


class TestAnEditFormStartsOnItsRulesCadence:
    """Property 4: the controls read back what the rule actually means."""

    @pytest.mark.parametrize(
        ("cadence", "unit", "interval", "placement"),
        [
            (
                EVERY_PERIOD, RecurrenceUnitEnum.PERIOD,
                1, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                MONTHLY, RecurrenceUnitEnum.MONTH,
                1, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                MONTHLY_FIRST, RecurrenceUnitEnum.MONTH,
                1, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
            (
                QUARTERLY, RecurrenceUnitEnum.MONTH,
                3, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                SEMI_ANNUAL, RecurrenceUnitEnum.MONTH,
                6, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                ANNUAL, RecurrenceUnitEnum.YEAR,
                1, PeriodPlacementEnum.CONTAINING_DATE,
            ),
        ],
        ids=lambda value: getattr(value, "label", None),
    )
    def test_each_stored_cadence_preselects_its_own_axes(
        self, app, auth_client, seed_user, seed_periods_today,
        cadence, unit, interval, placement,
    ):
        """Every named cadence, read back onto the three controls.

        Swept rather than sampled because a mapping wrong for ONE of them shows
        a user the wrong cadence on the screen where they are about to re-save
        it.  The interval is read off the number box for every unit since plan
        step R7c-c, where a calendar cadence used to preselect an option in a
        ``<select>`` -- and a quarterly rule reading back "1 month" is three
        times the projected spend.
        """
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_cadence(seed_user, cadence).id
            expected_unit = str(ref_cache.recurrence_unit_id(unit))
            expected_placement = str(ref_cache.period_placement_id(placement))

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert _selected(body, "recurrence_unit") == expected_unit
        assert _selected(body, "recurrence_placement") == expected_placement
        assert f'value="{interval}"' in _tag(body, "interval_n")

    def test_an_every_n_rule_prefills_its_own_interval(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A stated interval reads back off the column that stores it."""
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_cadence(
                seed_user, EVERY_N_PERIODS, interval_n=4,
            ).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert 'value="4"' in _tag(body, "interval_n")

    def test_an_interval_the_closed_set_could_never_name_reads_back(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Every 2 months" round-trips through the form.

        The cadence this whole arc exists for: it resolves and walks correctly
        and had nowhere to be written until plan step R7c-c freed the interval.
        A form that still rendered a 1 / 3 / 6 ``<select>`` would show it as
        one of those, and re-saving would silently re-cadence a bill.
        """
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_cadence(
                seed_user, MONTHLY, interval_n=2,
            ).id
            month_id = str(ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            ))

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert _selected(body, "recurrence_unit") == month_id
        assert 'value="2"' in _tag(body, "interval_n")


class TestAnUnreadableRuleCannotBeDestroyedBySavingIt:
    """A stored cadence the app cannot read renders UNSET -- and is SAFE.

    An HTML ``<select>`` whose selected value is absent from its options does
    not fail: the browser silently selects the first, and that option submits.
    Before plan step R7b-2 the picker met that by keeping the stored pattern as
    a trailing option, because the first entry is the empty "Does not repeat"
    one whose save DELETES the rule and sweeps its future rows (plan step
    R2e-1).

    The two-axis controls carry no such id, so there is nothing to keep
    selected -- they render unset, which means the unit select's first entry IS
    selected.  ``UNREADABLE_CADENCE_MESSAGE`` therefore promises the user that
    "saving it unchanged will be refused", and this class holds the code to
    that promise: the refusal is the SERVER's, made from the two facts it
    already holds (the stored rule is unreadable, the submission names no
    cadence), not a hidden field a client can drop.

    **The unreadable COLUMN moved at plan step R7c-c.**  It was a ``pattern_id``
    the enum did not name; that column is dropped, so the state a rule can still
    reach is a ``unit_id`` the enums do not name.  Same class of state, same
    disposition, same promise.
    """

    def test_the_transaction_form_renders_the_controls_unset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No cadence is preselected, because none of them is this rule's."""
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_unit_id()
            _assert_unreadable(surplus_id)
            template_id = _template_with_cadence(
                seed_user, MONTHLY, unit_id=surplus_id,
            ).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        # The empty "Does not repeat" entry is what "unset" means on a
        # <select>, and it is the whole assertion: the surplus id is a real
        # ``ref.recurrence_units`` row, so a control that rendered it would
        # be offering a cadence nothing can resolve.
        assert _selected(body, "recurrence_unit") == ""
        assert str(surplus_id) not in {
            value for value, _ in _options(body, "recurrence_unit")
        }

    def test_the_transfer_form_renders_the_controls_unset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same on the transfer form."""
        assert seed_periods_today
        with app.app_context():
            savings = _savings_account(seed_user)
            surplus_id = _unmodelled_unit_id()
            template_id = _transfer_template_with_cadence(
                seed_user, savings, MONTHLY, unit_id=surplus_id,
            ).id

        body = auth_client.get(f"/transfers/{template_id}/edit").data.decode()

        assert _selected(body, "recurrence_unit") == ""

    def test_the_user_is_told_the_cadence_is_unreadable(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A warning names the state and the repair.

        With no odd option left on the control, the sentence is the whole of
        the explanation rather than half of it.
        """
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_unit_id()
            template_id = _template_with_cadence(
                seed_user, MONTHLY, unit_id=surplus_id,
            ).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert UNREADABLE_CADENCE_MESSAGE.split(".", maxsplit=1)[0] in body

    def test_saving_it_unchanged_is_refused_and_the_rule_survives(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The load-bearing one: re-submitting the form destroys nothing.

        The user opens the edit form to change the amount and saves.  The unit
        select submits the empty value the form rendered -- which on any other
        template means "does not repeat", and would DELETE the rule and sweep
        its future rows.  It is not a choice here: the form could not offer
        this rule's cadence, so the empty submission carries no intent, and
        the server refuses rather than acting on it.
        """
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_unit_id()
            template = _template_with_cadence(
                seed_user, MONTHLY, unit_id=surplus_id,
            )
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1300.00",
                # Exactly what the rendered form submits.
                "recurrence_unit": "",
                "recurrence_placement": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            # The REASON is asserted, not just the survival: without it this
            # test passes against a route that refused the POST for an
            # unrelated reason.  **The sentence is the SCHEMA's since plan
            # step balance:X-bi-7b** (R-BAL23): a transaction template refuses
            # an empty unit before the route reads a field, so the reader
            # meets "choose how it repeats" -- what the unset picker asks --
            # and ``UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`` is now the transfer
            # door's sentence alone (``test_the_transfer_door_refuses_it_too``).
            assert b"needs a cadence" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule.id == rule_id, (
                "the rule was detached by a save the form promised to refuse"
            )
            assert db.session.get(RecurrenceRule, rule_id) is not None, (
                "the rule row was DELETED by a save the form promised to refuse"
            )
            # The refused edit committed nothing, amount included.
            assert reloaded.default_amount == Decimal("1200.00")

    def test_the_transfer_door_refuses_it_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same refusal on the other kind, because the guard is shared.

        Both routes dispatch through ``resolve_recurrence_rule_for_update``,
        and that function checks TWO refusals in order -- the loan-payment one
        and this -- so an ordering change between them would pass a suite that
        only drove the transaction form.
        """
        assert seed_periods_today
        with app.app_context():
            savings = _savings_account(seed_user)
            surplus_id = _unmodelled_unit_id()
            template = _transfer_template_with_cadence(
                seed_user, savings, MONTHLY, unit_id=surplus_id,
            )
            template_id = template.id
            rule_id = template.recurrence_rule.id

            resp = auth_client.post(f"/transfers/{template_id}", data={
                "name": "To Savings",
                "default_amount": "75.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_unit": "",
                "recurrence_placement": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransferTemplate, template_id)
            assert reloaded.recurrence_rule.id == rule_id
            assert db.session.get(RecurrenceRule, rule_id) is not None
            assert reloaded.default_amount == Decimal("50.00")

    def test_choosing_a_cadence_repairs_the_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The repair the surface asks for actually works.

        The class above proves the OTHER action is refused; a suite holding
        only that can watch the advertised remedy turn into a 500, which is
        what happened at plan step R7b-1 -- reading the rule's authored state
        on the way to REPLACING its cadence raised on the cadence being
        replaced.  ``recurrence_spec_with_cadence`` is what fixed it.
        """
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_unit_id()
            _assert_unreadable(surplus_id)
            template = _template_with_cadence(
                seed_user, MONTHLY, unit_id=surplus_id,
            )
            template_id, rule_id = template.id, template.recurrence_rule.id
            month_id = ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            )

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1200.00",
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH,
                    starts_on=date(2026, 9, 1),
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            # The SAME rule row, re-pointed -- not a new one, so every
            # generated row keeps its lineage.
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.unit_id == month_id
            # The repaired rule fires on the date the form stated, which is
            # the whole of what it says about its cycle since plan step
            # R7c-c: the day and the month are that date's.
            assert reloaded.recurrence_rule.starts_on == date(2026, 9, 1)


class TestTheWriteDoorsRefuseAnUnstorableCadence:
    """A hand-assembled POST is refused with a message, and writes nothing.

    The end-to-end half of ``validate_authorable_cadence``.  Its predecessor
    covered the equivalent for a submitted PATTERN id and was deleted with the
    field; an adversarial review of plan step R7b-2 caught that nothing had
    replaced it, so a route that accepted the triple and 500'd at the flush
    would have passed the whole suite.

    ``(1, WEEK, ...)`` is the case since plan step **R8-a**: a weekly
    occurrence is neither a payday nor a day of the month, so
    ``recurrence.compute_due_date`` has nothing to date its generated
    rows from and :func:`~app.services.recurrence._frequency
    .has_row_date_coordinate` keeps the unit out of the offer set.  The picker
    never renders the WEEK unit, so no click produces it.

    **The case has MOVED TWICE, each time because the gap it named closed.**
    It was ``(2, MONTH)`` until plan step R7c-c, which freed the interval; then
    ``(1, YEAR, first paycheck)`` until R8-a, which admitted it -- that refusal
    named a first-occurrence derivation ruling **R-R16** had already deleted.
    Plan step **R5** closes this one, and the case then has no successor: the
    offer set and the write door will admit every reading, and what is left to
    pin is that they still agree.
    """

    #: The verbatim copy the routes flash.  Pinned because the message names
    #: the two controls the user can change, and the generic
    #: "correct the highlighted errors" prompt names none -- on a redirect that
    #: highlights nothing.
    _REFUSAL = b"That repeat schedule cannot be saved yet"

    def test_a_transaction_template_is_not_created(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates refuses, flashes, and persists no rule.

        Both halves matter: a 200 alone would also be produced by a route that
        created the template and redirected to the list.
        """
        assert seed_periods_today
        with app.app_context():
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/templates", data={
                "name": "Every Other Month Bill",
                "default_amount": "10.00",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(_expense_type_id()),
                "account_id": str(seed_user["account"].id),
                **cadence_payload(unit=RecurrenceUnitEnum.WEEK),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            assert db.session.query(TransactionTemplate).filter_by(
                name="Every Other Month Bill",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_a_transfer_template_is_not_created(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers refuses the same triple the same way.

        Swept across both kinds because ``_form_errors`` exists precisely
        because the two forms once explained the same refusal differently.
        """
        assert seed_periods_today
        with app.app_context():
            savings = _savings_account(seed_user)
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/transfers", data={
                "name": "Every Other Month Transfer",
                "default_amount": "25.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                **cadence_payload(unit=RecurrenceUnitEnum.WEEK),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            assert db.session.query(TransferTemplate).filter_by(
                name="Every Other Month Transfer",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_an_edit_leaves_the_existing_rule_untouched(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates/<id> refuses and re-points nothing.

        The edit path re-AUTHORS the rule in place, so a door that accepted the
        triple would overwrite a working cadence on a row the user already
        depends on.
        """
        assert seed_periods_today
        with app.app_context():
            month_id = ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            )
            template = _template_with_cadence(seed_user, MONTHLY)
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1200.00",
                **cadence_payload(unit=RecurrenceUnitEnum.WEEK),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.unit_id == month_id


class TestAClearedIntervalBoxCannotReCadenceARule:
    """Plan step **R7c-c**: an emptied "every ___" box is refused, not read as 1.

    **The defect this pins moved money, and the whole suite was blind to it.**
    Every case in ``test_recurrence_form_helpers.py`` states ``interval_n``
    explicitly through ``validated_cadence(...)``, so nothing exercised the
    absence -- and the absence is what a browser produces: an ``<input
    type="number">`` cleared by the user submits ``""``, which
    ``_normalize_empty_inputs`` DROPS because the field is not ``allow_none``.
    Both write doors then defaulted to ``1``, so a save with an empty box
    silently re-cadenced a quarterly bill to monthly -- 12 occurrences a year
    where 4 were owed, across the whole projection.

    R7c-c is what made it reachable for the calendar units: the months
    ``<select>`` it replaced could not post an empty value, so only the PERIOD
    unit's free box carried the shape before.

    The interval box is enabled for every chosen cadence, so absence beside a
    named unit is a cleared box or a crafted POST and never "not mine to
    state".  That is why the refusal is the submission's -- see
    ``validate_authorable_cadence`` -- rather than a fourth ``KEY in data``
    read at a route site, which is the copy plan ledger row **D36** warns
    against.
    """

    #: The verbatim copy the routes flash, pinned for the reason the sibling
    #: class pins its own: the generic "correct the highlighted errors" prompt
    #: names no control, on a redirect that highlights nothing.
    _REFUSAL = b"Say how often this repeats"

    def test_an_edit_that_clears_it_leaves_the_stored_interval_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The measured case: a quarterly bill stays quarterly.

        Asserting the stored ``interval_n`` rather than the response alone is
        the point -- the pre-fix route ALSO answered 200 here, and redirected
        to a list that says nothing about the cadence.
        """
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, QUARTERLY)
            template_id, rule_id = template.id, template.recurrence_rule.id
            assert template.recurrence_rule.interval_n == 3

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1200.00",
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH,
                    interval_n=3,
                    starts_on=date(2026, 1, 1),
                ),
                # What the browser posts for a box the user emptied.
                "interval_n": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.interval_n == 3

    def test_a_create_that_clears_it_authors_no_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The create door refuses the same absence, and writes nothing.

        Swept because the create path carried the identical default and its
        harm is only different in kind: an edit re-cadences a bill that
        exists, a create authors a monthly one the user never asked for.
        """
        assert seed_periods_today
        with app.app_context():
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/templates", data={
                "name": "Water Bill",
                "default_amount": "120.00",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(_expense_type_id()),
                "account_id": str(seed_user["account"].id),
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH, interval_n=3),
                "interval_n": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            assert db.session.query(TransactionTemplate).filter_by(
                name="Water Bill",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_a_stated_interval_still_saves(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control: the refusal is about ABSENCE, not the field.

        Without it a route that refused every submission carrying an interval
        would pass both cases above -- and re-cadencing a bill deliberately is
        the ordinary edit this form exists for.
        """
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, QUARTERLY)
            template_id = template.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1200.00",
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH,
                    interval_n=6,
                    starts_on=date(2026, 1, 1),
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule.interval_n == 6

    def test_an_amount_only_edit_states_no_cadence_and_is_unaffected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A submission naming NO unit is still a partial update.

        The rule is conditional on a cadence being named, exactly as
        ``validate_recurrence_states_a_start`` is: refusing an absent interval
        unconditionally would make every amount-only edit unsavable, which is
        the failure mode the placement half was written to avoid.
        """
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, QUARTERLY)
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1300.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.default_amount == Decimal("1300.00")
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.interval_n == 3


class TestTheCopyTheUserActuallyReads:
    """The picker's words, pinned verbatim.

    Restored after an adversarial review of plan step R7b-2 found the whole
    label surface unpinned: the migration's successor test compared option
    VALUES between the two forms and never read a label.  The template chooses
    between two of them -- ``unit_label_one if interval_n == 1 else
    unit_label_many`` -- so swapping the pair in ``_picker._UNIT_LABELS``
    renders "1 months", "3 month", "6 month", "1 years" with every other test
    in the suite still green.  ``CadenceOption``'s ``KeyError``-on-missing-copy
    design catches an ABSENT label; nothing caught a wrong one.
    """

    def test_the_unit_options_read_as_plurals(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The unit select is worded for "Repeats <unit>", so plural.

        **The first entry is a PLACEHOLDER on the transaction form since plan
        step balance:X-bi-7b** (ruling R-BAL23): a transaction definition with
        no rule is a one-off, made at the Budget grid, so this form offers no
        "Does not repeat" -- the empty entry asks for a cadence and the schema
        refuses its value.  The transfer form keeps the option
        (``test_the_transfer_form_still_offers_does_not_repeat``).
        """
        assert seed_periods_today
        body = auth_client.get("/templates/new").data.decode()

        assert _option_labels(body, "recurrence_unit") == [
            "Choose how it repeats", "paychecks", "months", "years",
        ]

    def test_the_transfer_form_still_offers_does_not_repeat(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer form IS the one-time transfer's door until its own step decides."""
        assert seed_periods_today
        body = auth_client.get("/transfers/new").data.decode()

        assert _option_labels(body, "recurrence_unit") == [
            "Does not repeat", "paychecks", "months", "years",
        ]

    # ``test_the_interval_options_agree_with_their_own_counts`` was here until
    # plan step R7c-c.  It pinned the interval ``<select>``'s labels -- "1
    # paycheck", "1 month", "3 months", "6 months", "1 year" -- which was the
    # one place the template picked between ``unit_label_one`` and
    # ``unit_label_many``.  That control is deleted: every interval is a free
    # number box, so the count is typed rather than chosen and there is no
    # per-count noun to word.  Both labels still ride on the option, because
    # plan step R8's controls will need them; nothing renders the singular
    # today, which is why nothing pins it.

    def test_the_placement_options_are_worded_over_the_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Generic over the occurrence DATE rather than per unit.

        The old picker's "Monthly (first paycheck of month)" fused a unit, an
        interval, an anchor day and this choice into one name, which is why
        "every other month, funded from the first paycheck" had nowhere to
        live.
        """
        assert seed_periods_today
        body = auth_client.get("/templates/new").data.decode()

        assert _option_labels(body, "recurrence_placement") == [
            "The paycheck that covers the date",
            "The first paycheck starting on or after the date",
        ]

    def test_both_forms_word_every_cadence_identically(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A user meets one vocabulary whichever kind they are creating.

        Every CADENCE reads the same on both forms.  The one entry that differs
        is the empty one, by ruling (R-BAL23, plan step balance:X-bi-7b): the
        transfer form's "Does not repeat" is a choice and the transaction
        form's "Choose how it repeats" is a placeholder, so it is compared
        separately above rather than folded into this equality.
        """
        assert seed_periods_today
        transaction = auth_client.get("/templates/new").data.decode()
        transfer = auth_client.get("/transfers/new").data.decode()

        assert _option_labels(transaction, "recurrence_unit")[1:] == (
            _option_labels(transfer, "recurrence_unit")[1:]
        )
        assert _option_labels(transaction, "recurrence_placement") == (
            _option_labels(transfer, "recurrence_placement")
        )


class TestADeliberateClearIsRefusedOnATransactionTemplate:
    """The empty unit is no longer a choice on this kind (R-BAL23, balance:X-bi-7b).

    This class was the negative control for the unreadable-cadence refusal
    above -- "a rule the form COULD show is cleared by the empty unit" -- and
    the developer ruled that door shut (2026-09-13): a transaction definition
    with no rule is a one-off, made at the Budget grid, so a cadence cannot be
    CLEARED from the form.  The control that a route refusing EVERY save would
    still fail is that a real cadence change lands (``test_a_modelled_rule_
    can_be_re_pointed``); the transfer twin's clear is graded in
    ``test_recurrence_clear``.
    """

    def test_a_modelled_rule_is_not_cleared_by_the_empty_unit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The empty unit meets ``A_CADENCE_IS_REQUIRED``; rule and amount stand."""
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, EVERY_PERIOD)
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1300.00",
                "recurrence_unit": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"needs a cadence" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule is not None
            assert reloaded.recurrence_rule.id == rule_id
            assert db.session.get(RecurrenceRule, rule_id) is not None
            assert reloaded.default_amount == Decimal("1200.00")

    def test_a_modelled_rule_can_be_re_pointed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The firing control: a stated cadence still lands on the same door."""
        assert seed_periods_today
        with app.app_context():
            template = _template_with_cadence(seed_user, EVERY_PERIOD)
            template_id, rule_id = template.id, template.recurrence_rule.id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1200.00",
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"needs a cadence" not in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule is not None
            assert reloaded.recurrence_rule.id == rule_id
            assert reloaded.recurrence_rule.unit_id == ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.MONTH,
            )


@pytest.mark.usefixtures("app")
class TestTheEndBoundOfferSet:
    """What the "Ends" control offers, derived from the shape set (R7b-3).

    The same property plan step R7b-2 gave the cadence controls, applied to
    the closing bound: the offer set and the dispatch that accepts a
    submission read ONE table, so a shape is offerable and submittable
    together or neither.
    """

    def test_it_offers_every_shape_and_only_those(self):
        """Derived from ``END_BOUND_KINDS``, in the tuple's own order.

        Never first, because it is the default a create form starts on.
        """
        offered = [option.token for option in end_bound_options()]

        assert offered == [kind.token for kind in END_BOUND_KINDS]

    def test_every_offer_is_worded(self):
        """A blank ``<option>`` is a choice the user cannot understand."""
        for option in end_bound_options():
            assert option.label.strip(), f"{option.token} has no label"

    def test_a_shape_without_copy_raises_rather_than_rendering_blank(self):
        """The contract ``_UNIT_LABELS`` holds, for the bound's shapes.

        A shape added at plan step R8 without copy fails at the first render
        instead of shipping an empty entry in a control that decides when a
        bill stops being charged.
        """
        with patch.object(
            _picker, "END_BOUND_KINDS",
            (*END_BOUND_KINDS, SimpleNamespace(token="unworded")),
        ):
            with pytest.raises(KeyError):
                end_bound_options()

    def test_each_offer_names_the_control_its_shape_needs(self):
        """The script shows one input and disables the rest from this.

        Stated by the OFFER rather than by the template, so a shape and the
        control it reads a value from cannot be added apart.  The unbounded
        shape names none, which is what "no value input" is.
        """
        by_token = {
            option.token: option.needs_field_id
            for option in end_bound_options()
        }

        assert by_token[NeverEnds.token] is None
        assert by_token[EndsOnDate.token] == "field-end-date"
        assert by_token[EndsAfterOccurrences.token] == "field-max-occurrences"

    def test_the_picker_model_carries_them(self):
        """One producer call per render, for every control on the form."""
        assert picker_model(authorable_cadences()).end_bounds == end_bound_options()
