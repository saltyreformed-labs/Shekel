"""
Shekel Budget App -- The recurrence vocabulary (plan steps R2e-2, R7b-2)

``ref.recurrence_units`` and ``ref.period_placements`` are tables;
:class:`~app.enums.RecurrenceUnitEnum` and
:class:`~app.enums.PeriodPlacementEnum` are what the application can actually
resolve.  Every recurrence surface used to read the TABLE -- the picker offered
its rows, both write doors accepted them, and the preview previewed them --
while ``resolve`` raises for any id no enum member names and no route catches
that.  The gap between the two sets is a 500 waiting for them to diverge.

**Plan step R7b-2 moved where that gap can be reached, and plan step R7c-c
closed the older half of it.**  A form posts no pattern id: it authors
``(interval_n, unit, placement)``, so the membership question the doors ask is
asked on the two AUTHORED axes -- ``modelled_unit`` and ``modelled_placement``.
``modelled_pattern`` had one caller left, the edit form deciding whether it
could preselect anything, and R7c-c dropped both the column it read and the
function; its cases went with them.

The load-bearing tests are the two in
:class:`TestAnUnmodelledRowIsInvisible` that ask whether the offer set is
driven by a ``ref`` TABLE -- a surplus ``recurrence_units`` row, and the
``WEEK`` member that really exists and cannot be resolved.  An adversarial
review of plan step R7b-2 is why they are asked on the UNIT table: the version
asked on the PATTERN table could not fail, because ``_picker`` does not read
that table or import its model at all.
"""
from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.ref import RecurrenceUnit
from app.services.recurrence import (
    UNREADABLE_CADENCE_MESSAGE,
    authorable_cadences,
    cadence_options,
    modelled_placement,
    modelled_unit,
)


class TestModelledUnit:
    """The membership answer for the first AUTHORED axis (plan step R7b-2)."""

    def test_every_member_round_trips_through_its_id(self, app):
        """``id -> member`` inverts ``ref_cache.recurrence_unit_id``."""
        with app.app_context():
            for member in RecurrenceUnitEnum:
                assert modelled_unit(
                    ref_cache.recurrence_unit_id(member),
                ) is member

    def test_an_id_that_is_no_row_at_all_is_none(self, app):
        """A fabricated id answers ``None``.

        This one IS submission-facing -- it is what the schema field asks of a
        posted ``recurrence_unit`` -- so the answer has to be refusable as a
        field error rather than raised.
        """
        with app.app_context():
            assert modelled_unit(99_999_999) is None


class TestModelledPlacement:
    """The membership answer for the second authored axis."""

    def test_every_member_round_trips_through_its_id(self, app):
        """``id -> member`` inverts ``ref_cache.period_placement_id``."""
        with app.app_context():
            for member in PeriodPlacementEnum:
                assert modelled_placement(
                    ref_cache.period_placement_id(member),
                ) is member

    def test_an_id_that_is_no_row_at_all_is_none(self, app):
        """A fabricated id answers ``None``."""
        with app.app_context():
            assert modelled_placement(99_999_999) is None


class TestAnUnmodelledRowIsInvisible:
    """A ``ref`` row the enum does not name reaches no recurrence surface.

    This is the property plan step R2e-3 depends on.  It keeps the ``Once``
    row so the previous image can still boot, and this class is why keeping it
    costs nothing: the row is unreachable, not merely unoffered.
    """

    def test_a_surplus_UNIT_row_does_not_move_the_offer_set(self, app):
        """The picker's options are unchanged by a new ``ref.recurrence_units`` row.

        **This test used to insert a surplus PATTERN row, and an adversarial
        review of plan step R7b-2 showed that could not fail.**  Before plan
        step R2e-2 the picker read every ``ref.recurrence_patterns`` row
        directly, so a surplus pattern row would have been offered and the
        assertion was load-bearing.  Since
        R7b-2 they were derived from ``PATTERN_DERIVATIONS``, an ENUM-keyed
        table, and ``_picker`` did not import the pattern model at all -- so
        inserting into that table could not move the answer whatever the
        producer did.  Plan step R7c-c deleted that table with the closed set;
        the options come from ``_offer.authorable_cadences`` now, which is
        derived from two predicates over the ENUM members (plan step R8-a; it
        was the anchor router until then) and reads no ``ref`` table either.

        The UNIT table is where the same mistake is now available: a picker
        that offered ``db.session.query(RecurrenceUnit).all()`` would offer
        ``WEEK`` -- which the closed set cannot store -- and this row besides.
        Comparing the whole tuple rather than a count is what catches a row
        that changes only a LABEL.
        """
        with app.app_context():
            before = cadence_options(authorable_cadences())

            surplus = RecurrenceUnit(name="Fortnight")
            db.session.add(surplus)
            db.session.flush()

            assert db.session.get(RecurrenceUnit, surplus.id) is not None
            assert cadence_options(authorable_cadences()) == before

    def test_the_offer_set_omits_a_unit_the_resolver_cannot_anchor(self, app):
        """``WEEK`` is a modelled unit with no anchor, so it is not offered.

        The property the surplus-row test above stands in for, asked of a
        member that really exists: ``RecurrenceUnitEnum.WEEK`` has a ``ref``
        row and an enum member, and a generated row cannot carry the date its
        occurrences name until plan step **R5** (``has_row_date_coordinate``,
        plan step R8-a).  A table-driven offer set would offer it and the write
        door would refuse the save.
        """
        with app.app_context():
            week_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.WEEK)

            assert modelled_unit(week_id) is RecurrenceUnitEnum.WEEK
            assert week_id not in {
                option.unit_id for option in cadence_options(authorable_cadences())
            }

    def test_a_ref_row_the_enum_does_not_name_is_not_modelled(self, app):
        """The membership answer for such a row is ``None``.

        Which is what makes the edit form refuse to preselect it: the row
        EXISTS, so the ``db.session.get`` probe this replaced would have said
        yes.

        **Asked on the UNIT table since plan step R7c-c**, which dropped the
        pattern column this used to plant a surplus row in.  Same state, same
        disposition, on the axis a rule now states its cadence with.
        """
        with app.app_context():
            surplus = RecurrenceUnit(name="Blue Moon")
            db.session.add(surplus)
            db.session.flush()

            assert db.session.get(RecurrenceUnit, surplus.id) is not None
            assert modelled_unit(surplus.id) is None

    def test_every_id_an_option_carries_resolves_on_its_own_axis(self, app):
        """Each offered id names a value on the axis its field claims.

        **There is deliberately no "and none of them is some other table's id"
        arm.**  The ``ref`` tables are separate sequences that all start at 1,
        so comparing an option's ``unit_id`` against another table's ids is
        either vacuously true or a false FAILURE depending on how far each
        sequence has run -- it measures seeding order, not the property.  What
        the two tests above establish behaviourally is the real statement: the
        offer set is not driven by any ``ref`` table.
        """
        with app.app_context():
            options = cadence_options(authorable_cadences())

            assert options, "the offer set is empty"
            for option in options:
                assert modelled_unit(option.unit_id) is not None, option
                assert modelled_placement(option.placement_id) is not None, (
                    option
                )


class TestTheUnreadableCadenceMessage:
    """What an edit form TELLS the user about a rule it cannot preselect."""

    def test_it_names_the_state_and_the_repair(self, app):
        """The copy has to do the whole job, because no control shows the state.

        Before plan step R7b-2 the message sat above a ``<select>`` that still
        carried the stored pattern as a trailing option, so the option itself
        said which rule was affected.  The two-axis controls render UNSET, so
        the sentence is the only thing the user has: it must say that the
        cadence cannot be read, that one must be chosen, and that saving
        unchanged will be refused -- the last being what keeps the warning
        honest rather than advisory.

        **The state it describes moved with the column at plan step R7c-c**:
        it was a ``pattern_id`` the enum did not name, and it is now a
        ``unit_id`` or ``placement_id`` the enums do not name.  The copy no
        longer says "pattern", which is the whole of the rename.
        """
        assert "can no longer read" in UNREADABLE_CADENCE_MESSAGE
        assert "Choose how often it repeats" in UNREADABLE_CADENCE_MESSAGE
        assert "refused" in UNREADABLE_CADENCE_MESSAGE
