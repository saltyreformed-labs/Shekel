"""Tests for the semantic ``is_settled`` archive predicates (CRIT-05 / E-22).

The audit's CRIT-05 finding ("Irreversible silent hard-delete of
RECEIVED settled-income history") proved that the pre-fix predicates
``template_has_paid_history`` and ``transfer_template_has_paid_history``
enumerated ``[DONE, SETTLED]`` by ID and silently omitted ``RECEIVED``
-- the status assigned to every income paycheck on mark-done.  The
guard in ``hard_delete_template`` then permanently destroyed real
received-income history while telling the user it was "permanently
deleted." These tests pin the post-fix behavior: both predicates
return True for any status carrying ``Status.is_settled = True``
(Paid, Received, Settled per ``ref_seeds.py``) and False otherwise.

Test IDs C21-1..C21-7 trace to ``remediation_plan.md`` Section 9
"Commit 21" subsection E.  The companion route-level tests live in
``tests/test_routes/test_templates.py``; this file is the unit-level
pin on the predicate boolean output and on the absence of the prior
ID enumeration in the production source.
"""

from decimal import Decimal
from pathlib import Path

from app import ref_cache
from app.enums import MovementFigureSourceEnum
from app.models.ref import Status
from app.models.settled_figure import SettledFigure
from app.services import transfer_service
from app.utils.archive_helpers import (
    template_has_paid_history,
    transfer_template_has_paid_history,
)
from tests._test_helpers import (
    create_savings_account,
    generate_row_of,
    generate_transfer_of,
    make_expense_template,
    make_income_template,
    make_transfer_template,
    typed,
)


def _make_template_with_status_txn(app, db_, seed_user, period, status_name):
    """Create an expense template plus its engine-generated row at a status.

    Returns the (template, transaction) pair.  All boilerplate lives
    here so each test pins exactly one status and asserts the
    predicate's boolean output without restating account/category/
    scenario plumbing.  The row is the definition's own, written by the
    engine (:func:`generate_row_of`, plan step balance:X-cf) -- which only
    ever writes Projected -- and the status under test is then laid on it
    bare, the way the row's readers here meet it.  Resolves Status by name
    (test scaffolding only -- production code uses cached IDs per
    CLAUDE.md rule 4 / E-15).
    """
    status = db_.session.query(Status).filter_by(name=status_name).one()
    template = make_expense_template(
        db_.session, seed_user, amount="500.00",
        name=f"Template-{status_name}",
    )
    txn = generate_row_of(template, period)
    txn.status_id = status.id
    db_.session.commit()
    return template, txn


def _make_income_template_with_status_txn(app, db_, seed_user, period, status_name):
    """Create an income template plus its engine-generated row at a status.

    Income templates are the specific case CRIT-05 documents: mark-done
    on an income transaction assigns ``RECEIVED``, the pre-fix
    predicate omitted RECEIVED, and the guard fell through to the
    unconditional bulk delete.  Built as the expense pair above is, through
    :func:`make_income_template` and the engine.
    """
    status = db_.session.query(Status).filter_by(name=status_name).one()
    template = make_income_template(
        db_.session, seed_user, amount="2000.00",
        name=f"IncomeTemplate-{status_name}",
    )
    txn = generate_row_of(template, period)
    txn.status_id = status.id
    db_.session.commit()
    return template, txn


def _make_transfer_template_with_status(app, db_, seed_user, period, status_name):
    """Create a transfer template plus its engine-generated transfer at a status.

    The transfer is the definition's own, written by the transfer engine
    with its two shadow legs (:func:`generate_transfer_of`, plan step
    balance:X-ch), and the status under test is then laid on it through
    ``transfer_service.update_transfer`` -- the one door that keeps the three
    rows equal (Transfer Invariant 3), and for a settled status the settle
    itself, which records the definition's figure on the ``derived`` basis.
    Resolves Status by name (test scaffolding only -- production code uses
    cached IDs per CLAUDE.md rule 4 / E-15).
    """
    status = db_.session.query(Status).filter_by(name=status_name).one()
    savings_account = create_savings_account(
        seed_user, db_.session, f"Savings-{status_name}", Decimal("0.00"),
    )
    xfer_template = make_transfer_template(
        db_.session, seed_user, savings_account, amount="100.00",
    )
    xfer = generate_transfer_of(xfer_template, period)
    if xfer.status_id != status.id:
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id, status_id=status.id,
        )
    db_.session.commit()
    return xfer_template, xfer


class TestTemplateHasPaidHistorySemanticIsSettled:
    """``template_has_paid_history`` uses ``Status.is_settled`` (CRIT-05).

    The pre-fix predicate enumerated ``[DONE, SETTLED]`` by ID and
    silently omitted ``RECEIVED``.  Post-fix, the predicate filters
    on the semantic ``Status.is_settled`` boolean so every settled
    status (current: Paid, Received, Settled per ``ref_seeds.py``;
    future: any new settled row) is covered without enumeration.
    """

    def test_received_income_returns_true(self, app, db, seed_user, seed_periods_today):
        """C21-1 (predicate half): RECEIVED on an income template -> True.

        This is the bug CRIT-05 documents end-to-end: a recurring
        income template whose paycheck was marked RECEIVED previously
        returned False from this predicate and let the route's bulk
        delete physically destroy the paycheck.  ``RECEIVED`` has
        ``is_settled=True, is_immutable=True`` in ``ref_seeds.py``,
        identical protection to ``DONE`` / ``SETTLED``; the post-fix
        predicate must say so.
        """
        with app.app_context():
            template, _ = _make_income_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Received",
            )
            assert template_has_paid_history(template.id) is True

    def test_paid_expense_returns_true(self, app, db, seed_user, seed_periods_today):
        """C21-3 (predicate half): PAID on an expense template -> True.

        Regression keep for the pre-fix behavior that was already
        correct.  ``Paid`` carries ``is_settled=True``; the predicate
        must continue to return True after the enumeration -> boolean
        swap.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Paid",
            )
            assert template_has_paid_history(template.id) is True

    def test_received_returns_true(self, app, db, seed_user, seed_periods_today):
        """C21-2 (predicate half): RECEIVED -> True.

        Regression keep, and the case CRIT-05 was actually about: the pre-fix
        code enumerated ``[DONE, SETTLED]`` by name and silently omitted
        RECEIVED, letting a normal user permanently destroy real received
        income history.  The specimen here was ``Settled`` -- the terminal
        archive -- until plan step **balance:X-am** deleted that status, which
        is the second time the settled SET has moved under this predicate
        without it needing an edit: it reads ``Status.is_settled``.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Received",
            )
            assert template_has_paid_history(template.id) is True

    def test_projected_returns_false(self, app, db, seed_user, seed_periods_today):
        """C21-4 (predicate half): PROJECTED only -> False.

        ``Projected`` carries ``is_settled=False``, so the predicate
        must let the template be permanently deleted.  The intended
        behavior the route relies on for the no-history path.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Projected",
            )
            assert template_has_paid_history(template.id) is False

    def test_cancelled_returns_false(self, app, db, seed_user, seed_periods_today):
        """``CANCELLED`` is not settled, so the predicate returns False.

        ``Cancelled`` has ``is_settled=False, is_immutable=True,
        excludes_from_balance=True`` -- the user explicitly voided
        the transaction, no money actually moved, no financial
        history exists to protect.  Pins that the predicate honors
        the ``is_settled`` semantics rather than over-broad
        immutability protection.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Cancelled",
            )
            assert template_has_paid_history(template.id) is False

    def test_credit_returns_false(self, app, db, seed_user, seed_periods_today):
        """``CREDIT`` is not settled, so the predicate returns False.

        ``Credit`` has ``is_settled=False`` (the payback transaction
        is the settled half of the credit-card workflow).  Same
        rationale as the Cancelled pin: the predicate keys on
        ``is_settled``, not on ``is_immutable``.
        """
        with app.app_context():
            template, _ = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Credit",
            )
            assert template_has_paid_history(template.id) is False

    def test_soft_deleted_settled_BLOCKS_deletion(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A soft-deleted settled row is payment history (**R-JE**).

        **This assertion was ``is False`` until plan step balance:X-au-e**, on
        the ground that "a row the user already discarded cannot block
        subsequent template cleanup".  The developer ruled the opposite on
        2026-09-03, closing finding **N-444**, and the reason is that the two
        halves of one door disagreed: this predicate filtered
        ``is_deleted = False`` while ``crud.hard_delete_template``'s bulk
        delete did not, so such a row was invisible to the GATE and excluded
        from the DELETE.  The template went, ``fk_transactions_template`` is ON
        DELETE SET NULL, and the row was left holding its money with no link to
        the definition that made it.

        Harmless while that survivor OWNED its figure.  X-au-e declares every
        non-override template row DERIVED, so the same survivor now carries
        ``amount_source_id = template`` with no template to read: unpriceable
        on any revert to Projected, and unrestorable by the cutover migration's
        own downgrade, whose restore joins on the ``template_id`` that is now
        NULL (**N-440**).

        The cost is stated rather than hidden: such a template is archived
        instead of permanently deleted.  Zero rows of this shape exist on the
        2026-09-03 production clone.
        """
        with app.app_context():
            template, txn = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Paid",
            )
            txn.is_deleted = True
            db.session.commit()
            assert template_has_paid_history(template.id) is True

    def test_soft_deleted_PROJECTED_still_does_not_block(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The negative control for the case above: it is SETTLED that counts.

        Without this, ``template_has_paid_history`` returning ``True`` for
        every soft-deleted row -- a predicate that had dropped the status test
        rather than the ``is_deleted`` one -- would pass the case above and
        block the permanent deletion of any template with a discarded
        projected row.
        """
        with app.app_context():
            template, txn = _make_template_with_status_txn(
                app, db, seed_user, seed_periods_today[0], "Projected",
            )
            txn.is_deleted = True
            db.session.commit()
            assert template_has_paid_history(template.id) is False


class TestTransferTemplateHasPaidHistorySemanticIsSettled:
    """``transfer_template_has_paid_history`` uses ``Status.is_settled``.

    Mirror of the transaction-template tests above; the transfer
    template's bulk-delete path has the same data-loss exposure that
    CRIT-05 documents for transactions.  Pin equivalent behavior so
    a future caller cannot regress one predicate without the other.
    """

    def test_received_returns_true(self, app, db, seed_user, seed_periods_today):
        """C21-6 (predicate half): RECEIVED transfer shadow -> True.

        Even though transfers are structurally always expense/income
        pairs, the predicate must protect any settled-status row --
        ``RECEIVED`` included.

        **The status is laid on the engine's transfer BARE here**, unlike the
        cases beside it: ``transfer_service`` refuses Projected -> Received
        on a transfer (an income status on a pair that is always one expense
        and one income), so this is a row no door writes, and the case is a
        defence-in-depth grade of the predicate over the ``is_settled``
        column rather than over a state the application produces.  Laid
        bare, the transfer holds no side record, the shape of a ``$0.00``
        close, so it stores the figure that close stores, ``0.00`` and typed
        (ruling **R-BAL365**): since plan step ``balance:X-bi-6-4d-2``'s
        4d-4b-2 leaf the band trigger's figure arm refuses a settled transfer
        storing no figure.
        """
        with app.app_context():
            xfer_template, xfer = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Projected",
            )
            xfer.status_id = (
                db.session.query(Status).filter_by(name="Received").one().id
            )
            xfer.settled_figure_pair = SettledFigure(
                Decimal("0.00"),
                ref_cache.movement_figure_source_id(
                    MovementFigureSourceEnum.TYPED,
                ),
            )
            db.session.commit()
            assert transfer_template_has_paid_history(xfer_template.id) is True

    def test_paid_returns_true(self, app, db, seed_user, seed_periods_today):
        """PAID transfer -> True (regression keep)."""
        with app.app_context():
            xfer_template, _ = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Paid",
            )
            assert transfer_template_has_paid_history(xfer_template.id) is True

    def test_cancelled_returns_false(self, app, db, seed_user, seed_periods_today):
        """CANCELLED transfer -> False: it is immutable but NOT settled.

        The pair that keeps the predicate honest about WHICH column it reads.
        ``Cancelled`` carries ``is_immutable=True`` and ``is_settled=False``,
        so a predicate that had drifted onto immutability -- the plausible
        wrong column, since every non-Projected status is immutable -- would
        block a permanent delete that this one permits.

        This case was ``Settled -> True`` until plan step **balance:X-am**
        deleted the terminal archive.  That specimen only ever restated the
        Paid case beside it; this one grades something neither of the others
        could.
        """
        with app.app_context():
            xfer_template, _ = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Cancelled",
            )
            assert transfer_template_has_paid_history(xfer_template.id) is False

    def test_projected_returns_false(self, app, db, seed_user, seed_periods_today):
        """PROJECTED transfer only -> False (intended permanent-delete path)."""
        with app.app_context():
            xfer_template, _ = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Projected",
            )
            assert transfer_template_has_paid_history(xfer_template.id) is False

    def test_soft_deleted_settled_BLOCKS_deletion(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A soft-deleted settled transfer is payment history (**R-JE**).

        **This assertion was ``is False`` until plan step balance:X-au-f**, and
        the transaction twin's took the identical turn one step earlier -- see
        :meth:`TestTemplateHasPaidHistory.test_soft_deleted_settled_BLOCKS_deletion`
        for the developer's 2026-09-03 ruling and the argument, which applies
        here unchanged now that the second half of it is true one table over.

        What made this side wait: the predicate kept its ``is_deleted`` filter
        while a generated TRANSFER still stored its own amount, so a survivor
        left behind by a hard delete -- ``fk_transfers_transfer_template_id`` is
        ON DELETE SET NULL -- was priced by rule 1 off the column it held and
        harmed nothing.  X-au-f empties that column, so the same survivor now
        declares TEMPLATE and names no template: ``_stated_amount`` REFUSES it,
        and the cutover migration's own downgrade cannot restore it either,
        because its placeholder arm joins on the ``transfer_template_id`` that
        is now NULL.

        The cost is stated rather than hidden: such a template is archived
        instead of permanently deleted.

        **Hidden through the transfer's own delete door since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2): it was
        flagged on the ORM row, which the database now refuses at commit for
        a transfer holding its payments (the deleted-row rule's transfer arm).
        The door takes both payments off in the same save (ruling
        **credit_card:R-CC75**).

        **The Paid transfer is a ``$0.00`` close**, a typed ``$0.00`` taking
        both payments off before the delete: a delete that takes a Paid
        transfer's payments off sets it back to Projected (ruling
        **R-BAL246**; :meth:`test_a_deleted_transfer_that_held_payments_is_no_history`
        grades that), and a ``$0.00`` close held none, so it stays hidden in
        its Paid status -- the soft-deleted settled transfer this predicate
        counts.  A setup change only, every assertion unchanged (a rule-5
        re-expression the developer approved 2026-10-09, "Approve both").
        """
        with app.app_context():
            xfer_template, xfer = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Paid",
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, figure=typed(Decimal("0")),
            )
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            assert xfer.is_deleted is True and xfer.status.is_settled
            assert transfer_template_has_paid_history(xfer_template.id) is True

    def test_a_deleted_transfer_that_held_payments_is_no_history(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A deleted Paid transfer whose payments came off is a PLAN, not history.

        Ruling **R-BAL246** ("Back as a plan, now", developer 2026-10-09):
        the delete that takes a Paid transfer's ``$100.00`` payments off sets
        it back to Projected, so nothing settled is left against the
        definition and its permanent delete is no longer turned into an
        archive.  The control for the ``$0.00`` close above, which held
        nothing and keeps Paid.
        """
        with app.app_context():
            xfer_template, xfer = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Paid",
            )
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            assert xfer.is_deleted is True
            assert not xfer.status.is_settled
            assert transfer_template_has_paid_history(xfer_template.id) is False

    def test_soft_deleted_PROJECTED_still_does_not_block(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The negative control for the case above: it is SETTLED that counts.

        Without this, a predicate that had dropped the STATUS test rather than
        the ``is_deleted`` one would pass the case above and block the permanent
        deletion of any template holding a discarded projected transfer.  The
        transaction twin carries the same pair, for the same reason.
        """
        with app.app_context():
            xfer_template, xfer = _make_transfer_template_with_status(
                app, db, seed_user, seed_periods_today[0], "Projected",
            )
            xfer.is_deleted = True
            db.session.commit()
            assert transfer_template_has_paid_history(xfer_template.id) is False


class TestPredicateSourceUsesIsSettledNotIds:
    """Mechanical guard: the production source never enumerates status IDs.

    CLAUDE.md rule 4 / E-15 require ``IDs for logic, strings for
    display``; CRIT-05's specific failure mode was an enumeration
    (``[paid_id, settled_id]``) that silently omitted RECEIVED.  A
    future regression that re-introduces an enumeration would re-open
    the irreversible data-loss path, so the guard against it is a
    source-level mechanical check rather than another behavioral
    test -- behavior tests cannot catch the regression where someone
    re-adds an enum but happens to include all current settled IDs
    while still missing a future one.
    """

    def test_predicate_source_has_no_paid_settled_enumeration(self):
        """C21-7: ``archive_helpers.py`` source contains no ``[paid_id, settled_id]``.

        The pre-fix predicates carried the literal token
        ``[paid_id, settled_id]`` (and the equivalent
        ``[DONE_ID, SETTLED_ID]``).  The post-fix predicates filter
        by ``Status.is_settled.is_(True)``.  This grep-equivalent
        assertion is the load-bearing source-level lock: it would
        fire on any future refactor that drops the JOIN and goes
        back to enumerating status IDs.
        """
        source = Path("app/utils/archive_helpers.py").read_text(encoding="utf-8")
        # **These name the band as it was when CRIT-05 was written, and one
        # of them can no longer fail**: plan step **balance:X-am** deleted the
        # ``Settled`` status, so an enumeration spelling it would not import.
        # They are kept as the historical record of the exact defect...
        assert "[paid_id, settled_id]" not in source
        assert "[DONE_ID, SETTLED_ID]" not in source
        assert "[StatusEnum.DONE, StatusEnum.SETTLED]" not in source
        # ...and THIS is the one that grades the regression as it would be
        # spelled today.  Without it the guard pointed only at a band that no
        # longer exists, which is a control aimed at the past.
        assert "[StatusEnum.DONE, StatusEnum.RECEIVED]" not in source
        assert "[paid_id, received_id]" not in source
        # And the post-fix expression is actually present in both
        # predicates -- guards against a future "fix" that drops the
        # boolean filter entirely.
        assert source.count("Status.is_settled.is_(True)") == 2
