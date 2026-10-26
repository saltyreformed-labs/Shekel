"""The ONE writer (C3-b): a write touches no row it did not name (C4-c), only ``pay_period_write``
constructs or deletes a pay period, the ids it takes are scoped to the owner (C2-f3b), the
owner-id reader, and the retired count is the intersection.
"""

import logging
from datetime import date

from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import pay_period_admin, pay_period_write
from tests._repo_root import REPO_ROOT
from tests._test_helpers import (
    rhythm_of,
    add_txn,
    all_periods,
    capture_sql_statements,
    record_paydays_across_a_hole,
)

from ._helpers import _paydays


class TestAWriteTouchesNoRowItDidNotName:
    """Recording a payday INSERTs one row; retiring one DELETEs it.  Nothing else.

    **This class replaced ``TestItMaterialisesTheWholeDerivation`` at plan step
    ``pay_calendar:C4-c``, and the replacement is that step in one sentence.**
    While ``end_date`` and ``period_index`` were columns, every write had to
    re-materialise the owner's WHOLE calendar -- a cache refreshed only at its
    edge is a second source of truth, so an interior hole would survive every
    forward append.  Four cases graded that machinery: a hole repaired by the
    next batch, an INTERIOR hole repaired by an append at the far end (finding
    **N-127**), the WARNING the repair logs, and the delete that re-projects
    the newly-last end.  None of them can be written now: opening a hole meant
    writing a stored end BELOW the next payday, and there is no stored end.

    What replaces them is the property the deletion BUYS, which is stronger
    than any of them and is not vacuous: a write to ``budget.pay_periods``
    issues no ``UPDATE`` at all, so no row the operation did not name can move.
    Graded as a statement census rather than argued.
    """

    def test_recording_a_payday_issues_no_UPDATE(self, app, db, bare_user):
        """An append writes INSERTs, and touches no existing row.

        The whole-calendar rewrite this replaced emitted an ``UPDATE`` per row
        whose stored columns disagreed with the derivation.  With nothing
        derived stored there is nothing to rewrite, and a future writer that
        started mutating a period in place -- the shape that would re-open
        finding **P1** -- fails here.

        Deliberately an OFF-cadence append: on-cadence, a writer that still
        recomputed a stored end would coincidentally write the same value and
        emit no statement, so the census would pass on the defect.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=3, rhythm=rhythm_of(14),
            )
            db.session.commit()

            created, statements = capture_sql_statements(
                lambda: pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 2, 20),
                    num_periods=1, rhythm=rhythm_of(7),
                ),
            )
            db.session.commit()

            # **The positive control, and an adversarial review of this step is
            # why it is here** (2026-09-01).  ``updates == []`` is satisfied by
            # a write that did NOTHING -- a batch that recorded no payday, or a
            # capture whose listener never fired -- so the census had to say
            # first that there was a write to census.
            assert len(created) == 1
            inserts = [
                text for text, _params in statements
                if text.lstrip().upper().startswith("INSERT INTO BUDGET.PAY_PERIODS")
            ]
            assert len(inserts) == 1, statements

            updates = [
                text for text, _params in statements
                if text.lstrip().upper().startswith("UPDATE BUDGET.PAY_PERIODS")
            ]
            assert updates == []

    def test_retiring_a_payday_issues_no_UPDATE(self, app, db, bare_user):
        """A truncate deletes rows, and touches no survivor.

        The other direction, and it is the one the deleted machinery worked
        hardest on: a delete used to leave the newly-last survivor a stored end
        its own schedule no longer justified, so ``retire_paydays`` re-derived
        what survived.  The survivor's end is derived on every read now, so the
        delete is a delete.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, rhythm=rhythm_of(14),
            )
            db.session.commit()

            deleted, statements = capture_sql_statements(
                lambda: pay_period_admin.truncate_pay_periods(
                    user_id, keep_through_period_id=created[1].id,
                ),
            )
            db.session.commit()

            # The positive control, for the reason its sibling above states:
            # a truncate that deleted nothing emits no DELETE and no UPDATE,
            # and would pass an ``updates == []`` census on its own.
            assert deleted == 2
            deletes = [
                text for text, _params in statements
                if text.lstrip().upper().startswith("DELETE FROM BUDGET.PAY_PERIODS")
            ]
            assert len(deletes) == 1, statements

            updates = [
                text for text, _params in statements
                if text.lstrip().upper().startswith("UPDATE BUDGET.PAY_PERIODS")
            ]
            assert updates == []

    def test_an_existing_period_keeps_its_id_and_its_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Identity survives a later write, which is what the FKs rest on.

        ``budget.pay_periods.id`` is what three inbound foreign keys point at,
        so a writer that recreated rows rather than appending beside them would
        strand a transaction quietly.  Nothing here rewrites a row today; this
        is the assertion that says so from the outside.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Rent", "1200.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()
            period_id, txn_id = seed_periods[0].id, txn.id

            pay_period_admin.extend_pay_periods(user_id, 1)
            db.session.commit()

            assert db.session.get(PayPeriod, period_id) is not None
            assert db.session.get(
                Transaction, txn_id,
            ).pay_period_id == period_id


class TestThereIsOneWriter:
    """The module boundary IS the fence, so it is asserted rather than assumed.

    Plan step C3-b consolidated three write sites into one module (developer
    ruling): the ``PayPeriod`` constructor and the two bulk ``DELETE``\\ s.  No
    pylint checker enforces it -- finding ``balance:N-147`` already records that
    two checkers police their rule with hand-maintained module-name lists, and
    a third would widen that finding.  What holds the boundary instead is that
    the census below is one grep, and this test is that grep.
    """

    def test_only_pay_period_write_constructs_or_deletes_a_pay_period(self):
        """``app/`` holds exactly one PayPeriod construction and one DELETE."""
        # pylint: disable=import-outside-toplevel
        import ast

        # Three shapes, and an adversarial review of this step is why it is
        # not one.  The first cut matched only a bare ``PayPeriod(...)`` and a
        # bare ``.delete()``, so ``models.PayPeriod(...)``, a bulk ``.update()``
        # and a raw ``execute(text(...))`` would all have slipped past a test
        # whose whole purpose is to say they cannot.
        #
        # **What it cannot see, said rather than implied**: ``session.add(x)``,
        # ``session.merge(x)`` and ``bulk_save_objects([...])`` name no class,
        # so no AST can tell a pay period from anything else there.  Shape 1
        # covers the reachable half of that -- a row has to be CONSTRUCTED
        # before it can be added -- and the rest is what the module boundary is
        # for.
        def _names_the_class(node):
            """Whether *node* refers to the ``PayPeriod`` class as a symbol."""
            return (
                isinstance(node, ast.Name) and node.id == "PayPeriod"
            ) or (
                isinstance(node, ast.Attribute) and node.attr == "PayPeriod"
            )

        def _queries_the_table(node):
            """Whether a ``.delete()`` / ``.update()`` receiver queries the table."""
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in {"query", "filter_by", "filter"}
                    and any(
                        _names_the_class(arg)
                        or (isinstance(arg, ast.Attribute)
                            and _names_the_class(arg.value))
                        for arg in inner.args
                    )
                ):
                    return True
            return False

        writes = set()
        for path in sorted((REPO_ROOT / "app").rglob("*.py")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
                if not isinstance(node, ast.Call):
                    continue
                # 1. Constructing a row, by any spelling of the class name.
                if _names_the_class(node.func):
                    writes.add(relative)
                # 2. Deleting or bulk-updating rows through a query over them.
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"delete", "update"}
                    and _queries_the_table(node.func.value)
                ):
                    writes.add(relative)
                # 3. A raw statement naming the table, which skips the ORM.
                elif (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "text"
                    and any(
                        isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and "pay_periods" in arg.value
                        for arg in node.args
                    )
                ):
                    writes.add(relative)

        assert sorted(writes) == ["app/services/pay_period_write.py"]

    def test_the_readers_module_writes_nothing(self):
        """``pay_period_service`` holds no write of any kind after the split."""
        source = (
            REPO_ROOT / "app" / "services" / "pay_period_service.py"
        ).read_text()
        for forbidden in ("db.session.add", "db.session.delete", ".delete("):
            assert forbidden not in source, (
                f"pay_period_service must hold no write; found {forbidden!r}"
            )


class TestTheWriterTakesIdsAndScopesThemToTheOwner:
    """Both doors take ``budget.pay_periods.id`` values and read the rows here.

    Plan step **C2-f3b**.  ``retire_paydays`` took ``(periods, doomed)`` -- two
    lists of ORM rows the caller had queried -- and ``record_paydays`` took a
    ``retiring`` list it read one attribute off.  So ``pay_period_admin`` had to
    hold ORM rows for a set of integers, which is the last thing keeping that
    module reading ``budget.pay_periods``.  The ORM read moved here, into the
    module that owns the table, and what crosses the seam is ids.

    That makes the OWNER scoping structural rather than a property of the two
    callers: the delete set is this owner's rows LESS the survivors, so an id
    naming somebody else's period (or naming nothing) can only ever retire
    nothing.  The old shape trusted the caller to have queried an owner-scoped
    list, and returned ``len(doomed)`` whether or not those rows existed.
    """

    def test_it_deletes_exactly_the_periods_the_ids_name(
        self, app, db, seed_user,
    ):
        """Three ids in, three periods gone, three reported."""
        with app.app_context():
            user_id = seed_user["user"].id
            created = record_paydays_across_a_hole(
                user_id, date(2026, 1, 2), 5, rhythm_of(14),
            )
            db.session.flush()
            doomed = {period.id for period in created[2:]}

            assert pay_period_write.retire_paydays(user_id, doomed) == 3
            survivors = {
                period.id
                for period in all_periods(user_id)
            }
            assert doomed & survivors == set()

    def test_another_owners_id_retires_nothing_and_is_not_counted(
        self, app, db, seed_user, seed_second_periods,
    ):
        """The count is the intersection, never the size of the argument.

        The first owner asks to retire a period belonging to the second.  Under
        the old signature the caller supplied both lists, so this was a
        caller-side property; here the writer resolves the rows itself and the
        foreign id is simply not in the set it can reach.  It is graded on BOTH
        sides -- nothing deleted, and nothing counted -- because a door that
        deleted nothing while reporting "1 removed" is what the settings page
        would flash at the user.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            foreign = seed_second_periods[4]
            before = {
                period.id
                for period in all_periods(
                    foreign.user_id,
                )
            }

            assert pay_period_write.retire_paydays(user_id, {foreign.id}) == 0
            after = {
                period.id
                for period in all_periods(
                    foreign.user_id,
                )
            }
            assert after == before

    def test_recording_a_batch_ignores_a_foreign_retiring_id(
        self, app, db, seed_user, seed_second_periods,
    ):
        """``record_paydays`` scopes ``retiring_ids`` the same way.

        Regenerate and reset reach the table through this door rather than
        through ``retire_paydays``, so the same property has to hold on both or
        the scoping is only half structural.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            record_paydays_across_a_hole(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            db.session.flush()
            foreign = seed_second_periods[4]

            pay_period_write.record_paydays(
                user_id, date(2026, 2, 13), 2, rhythm_of(14),
                retiring_ids={foreign.id},
            )
            db.session.flush()
            assert db.session.get(PayPeriod, foreign.id) is not None
            assert len(all_periods(user_id)) == 6

    def test_an_id_naming_no_row_at_all_is_an_idempotent_no_op(
        self, app, db, seed_user,
    ):
        """A stale id -- one a concurrent truncate already deleted -- changes nothing.

        The confirm-discard panel re-posts the id the user reviewed, so this is
        reachable rather than hypothetical; ``truncate_pay_periods`` refuses it
        at its own resolve, and this grades the writer beneath that door.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            created = record_paydays_across_a_hole(
                user_id, date(2026, 1, 2), 4, rhythm_of(14),
            )
            db.session.flush()
            before = _paydays(user_id)
            gone = max(period.id for period in created) + 10_000

            assert pay_period_write.retire_paydays(user_id, {gone}) == 0
            assert _paydays(user_id) == before


class TestTheOwnerIdReader:
    """``owner_period_ids`` is the door for a caller that means "all of them".

    Plan step **C2-f3b**, corrected by an adversarial review of it.  Reset spelled
    this ``calendar_for(user_id).saved()``, which made the door that REPAIRS a
    broken schedule depend on the schedule being derivable; the identity of a row
    is not a derived value and is not reached through one.
    """

    def test_it_returns_every_id_and_only_this_owners(
        self, app, db, seed_user, seed_second_periods,
    ):
        """Two owners, two disjoint answers."""
        with app.app_context():
            user_id = seed_user["user"].id
            created = record_paydays_across_a_hole(
                user_id, date(2026, 1, 2), 4, rhythm_of(14),
            )
            db.session.flush()

            mine = pay_period_write.owner_period_ids(user_id)
            theirs = pay_period_write.owner_period_ids(
                seed_second_periods[0].user_id,
            )
            assert {period.id for period in created} <= mine
            assert mine & theirs == set()
            assert theirs == {period.id for period in seed_second_periods}

    def test_an_owner_with_no_schedule_gets_an_empty_set(self, app, bare_user):
        """Empty is a legal answer, so reset can run on a schedule-less owner."""
        with app.app_context():
            assert pay_period_write.owner_period_ids(
                bare_user["user"].id,
            ) == set()

    def test_it_reads_no_column_a_derivation_could_move(self, app, db, seed_user):
        """The identity read is a bare id query, not a calendar.

        Graded as a statement census rather than by argument: an implementation
        that resolved the ids through ``calendar_for`` would issue that
        function's ``budget.pay_schedule`` read, and an owner whose stored
        cadence cannot define a calendar would reach ``derive_periods`` and a
        500 on the door that exists to repair them.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            record_paydays_across_a_hole(user_id, date(2026, 1, 2), 3, rhythm_of(14))
            db.session.flush()

            ids, statements = capture_sql_statements(
                lambda: pay_period_write.owner_period_ids(user_id),
            )
            assert ids
            assert len(statements) == 1
            assert "pay_schedule" not in statements[0][0].lower()


class TestTheRetiredCountIsTheIntersection:
    """``len(current) - len(keep)`` graded BETWEEN its extremes.

    The three cases above it are all-own, all-foreign and all-stale, so an
    implementation returning ``min(len(doomed_ids), len(current))`` survives them
    (adversarial review, 2026-08-19).  A MIXED set is what separates the two.
    """

    def test_a_mixed_set_counts_and_deletes_only_the_owned_half(
        self, app, db, seed_user, seed_second_periods,
    ):
        """Two of this owner's ids, one foreign and one stale -> 2."""
        with app.app_context():
            user_id = seed_user["user"].id
            created = record_paydays_across_a_hole(
                user_id, date(2026, 1, 2), 5, rhythm_of(14),
            )
            db.session.flush()
            mine = {created[3].id, created[4].id}
            stale = max(period.id for period in created) + 10_000
            mixed = mine | {seed_second_periods[2].id, stale}

            before = {
                period.id
                for period in all_periods(user_id)
            }
            assert pay_period_write.retire_paydays(user_id, mixed) == 2
            survivors = {
                period.id
                for period in all_periods(user_id)
            }
            assert survivors & mine == set()
            assert survivors == before - mine
            # The foreign owner is untouched, which the count alone cannot say.
            assert db.session.get(
                PayPeriod, seed_second_periods[2].id,
            ) is not None

    def test_the_generated_event_reports_the_same_intersection(
        self, app, db, seed_user, seed_second_periods, caplog,
    ):
        """``retired=`` counts what went, not the size of the argument.

        The field moved from ``len(retiring_ids)`` to ``len(current) - len(keep)``
        at this step and nothing read it; a log line that says "3 retired" beside
        two deletions is a record of an operation that did not happen.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            created = record_paydays_across_a_hole(
                user_id, date(2026, 1, 2), 4, rhythm_of(14),
            )
            db.session.flush()
            mixed = {created[3].id, seed_second_periods[2].id}

            with caplog.at_level(logging.INFO):
                # The tail reopens where it stood (02-13, retired and
                # re-recorded); a day past 02-27 would skip a paycheck and be
                # refused (plan step C17-c-2a).  The case is about the
                # retired COUNT in the emitted event.
                pay_period_write.record_paydays(
                    user_id, date(2026, 2, 13), 2, rhythm_of(14),
                    retiring_ids=mixed,
                )
            retired = [
                record.retired for record in caplog.records
                if getattr(record, "event", None) == "pay_periods_generated"
            ]
            assert retired[-1] == 1
