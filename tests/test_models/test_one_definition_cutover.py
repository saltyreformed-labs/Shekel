"""The cutover mints every bare row a definition and states the shape in the schema.

Plan step **balance:X-bi-7d-2**, rulings **R-BAL20**, **R-BAL21**, **R-BAL22**,
**R-BAL25**, **R-BAL28**, **R-BAL37**, **R-BAL67** and **R-BAL73**.  Migration
``596408fab6f1`` mints a rule-less definition per BARE row (one naming no
definition, no transfer and no credit source), opens its one-version series
on the row's due date, dates the undated on their paycheck's start, links
and declares each row, re-attaches the residue rows the 7b-1 interim left
detached, drops both flag columns, re-cuts ``ck_transactions_one_pricing_link``
to ``= 1`` and moves both link keys to ``RESTRICT``.

**The migration's arms are DRIVEN, not described** (the pattern
``test_covering_cutover`` set for X-bi-3d).  A bare row is unstorable at head,
so the cases that need one run the migration's own ``downgrade()`` over the
test's private clone first, plant the pre-cutover shape by raw SQL -- the
only writer that can still spell it -- and run ``upgrade()`` or its
module-level callables against the test connection.  Each such case first
steps down through ``c956c1d83108`` (the ``below_the_transfer_column_drop``
fixture): this revision's CHECK and its bare-row predicate name
``transfer_id``, which head's table no longer has.  Every refusal is a
control SHOWN TO FIRE (``docs/plans/verification.md`` standard 4), and every
figure the cutover moves off a row is read back through the app's one
resolver to the cent.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import (
    AmountSourceEnum,
    MovementFigureSourceEnum,
    SettledDayBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services.amount_ownership import state_own_amount
from app.services.one_off import place_row_of
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    constraint_name_from,
    derived_span,
    load_migration_module,
    one_off_row_of,
    open_books_before_the_first_assertion,
    payback_row_of,
    resolved_amount,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "596408fab6f1_every_plan_item_has_one_definition.py"
)

_ONE_LINK = "ck_transactions_one_pricing_link"
_TEMPLATE_KEY = "fk_transactions_template_id"
_PAYBACK_KEY = "fk_transactions_credit_payback_for"
_PRIOR_TEMPLATE_KEY = "transactions_template_id_fkey"


def _run(step):
    """Run a migration step against this test's clone."""
    run_migration_callable(step, db.session)


def _conn():
    """The test connection the migration's module-level callables take."""
    return db.session.connection()


def _constraint(name):
    """Return *name*'s definition on ``budget.transactions``, or ``None``."""
    return db.session.execute(sa.text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'budget.transactions'::regclass AND conname = :n"
    ), {"n": name}).scalar()


def _row_columns():
    """Return the set of column names ``budget.transactions`` carries now."""
    return set(db.session.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'transactions'"
    )).scalars())


def _assert_pre_cutover_schema():
    """Both flag columns present, the CHECK at ``<= 1``, both keys ``SET NULL``."""
    assert {"is_envelope", "companion_visible"} <= _row_columns()
    assert _constraint(_ONE_LINK).endswith("<= 1))")
    assert _constraint(_PRIOR_TEMPLATE_KEY).endswith("ON DELETE SET NULL")
    assert _constraint(_TEMPLATE_KEY) is None
    assert _constraint(_PAYBACK_KEY).endswith("ON DELETE SET NULL")


def _assert_post_cutover_schema():
    """Both flag columns gone, the CHECK at ``= 1``, both keys ``RESTRICT``."""
    assert not {"is_envelope", "companion_visible"} & _row_columns()
    assert _constraint(_ONE_LINK).endswith("= 1))")
    assert _constraint(_TEMPLATE_KEY).endswith("ON DELETE RESTRICT")
    assert _constraint(_PRIOR_TEMPLATE_KEY) is None
    assert _constraint(_PAYBACK_KEY).endswith("ON DELETE RESTRICT")


def _plant_bare(  # pylint: disable=too-many-arguments
    seed_user, period, *, name, amount, kind=TxnTypeEnum.EXPENSE,
    status=StatusEnum.PROJECTED, due_date=None, is_envelope=False,
    companion_visible=False, settled=None, declared=False,
):
    """Insert a BARE row by raw SQL, after the downgrade has made it storable.

    The pre-cutover one-off: no link, OWNING its figure (or, with
    *declared*, declaring the TEMPLATE relation with no link and no figure --
    the shape the refusal probe names), its flags in the row's own cells,
    undated unless *due_date* says otherwise.  *settled* is
    ``(figure, settled_on, MovementFigureSourceEnum)`` for a settled row: the
    day pair lands on the row and the figure with its source on the row's
    COVERING MOVEMENT, the record's one home since plan step
    ``balance:X-bi-4b-2`` (through ``X-bi-4b-1`` the row's own
    ``settled_amount`` / ``settled_basis_id`` were planted beside the day).

    Returns:
        The new row's id.
    """
    params = {
        "uid": seed_user["user"].id,
        "aid": seed_user["account"].id,
        "pid": period.id,
        "sid": seed_user["scenario"].id,
        "stid": ref_cache.status_id(status),
        "name": name,
        "cat": seed_user["categories"]["Groceries"].id,
        "ttid": ref_cache.txn_type_id(kind),
        "est": None if declared else Decimal(amount),
        "src": (
            ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE)
            if declared else None
        ),
        "due": due_date,
        "env": is_envelope,
        "comp": companion_visible,
        "son": None, "sday": None,
    }
    if settled is not None:
        _figure, settled_on, _source = settled
        params.update({
            "son": settled_on,
            "sday": ref_cache.settled_day_basis_id(SettledDayBasisEnum.ENTERED),
        })
    row_id = db.session.execute(sa.text("""
        INSERT INTO budget.transactions
            (user_id, account_id, pay_period_id, scenario_id, status_id, name,
             category_id, transaction_type_id, estimated_amount,
             amount_source_id, due_date, is_envelope, companion_visible,
             settled_on, settled_day_basis_id, is_override, is_deleted,
             version_id, created_at, updated_at)
        VALUES (:uid, :aid, :pid, :sid, :stid, :name, :cat, :ttid, :est, :src,
                :due, :env, :comp, :son, :sday, FALSE, FALSE,
                1, now(), now())
        RETURNING id
    """), params).scalar_one()
    if settled is not None:
        figure, settled_on, source = settled
        db.session.execute(sa.text("""
            INSERT INTO budget.transaction_entries
                (transaction_id, account_id, owner_id, user_id, amount,
                 description, purchased_on, settled_on, settled_day_basis_id,
                 is_credit, covers_settlement, figure_source_id, version_id,
                 created_at, updated_at)
            VALUES (:tid, :aid, :uid, :uid, :amt, :name, :son, :son, :sday,
                    FALSE, TRUE, :fsrc, 1, now(), now())
        """), {
            "tid": row_id, "aid": params["aid"], "uid": params["uid"],
            "amt": figure, "name": name, "son": settled_on,
            "sday": params["sday"],
            "fsrc": ref_cache.movement_figure_source_id(source),
        })
    return row_id


def _one_off(seed_user, period, *, name="Kayla's Kindle", amount="162.25",
             **flags):
    """Place a one-off's row through the producer, committed."""
    row = one_off_row_of(
        period, name=name, amount=Decimal(amount),
        user_id=seed_user["user"].id, account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=seed_user["categories"]["Groceries"].id, **flags,
    )
    db.session.commit()
    return row


@pytest.mark.usefixtures("below_the_transfer_column_drop")
class TestTheCutoverMintsADefinitionPerBareRow:
    """Steps 2 to 4 of the migration, driven over planted pre-cutover rows."""

    def test_every_bare_row_is_minted_dated_declared_and_priced_to_the_cent(
        self, app, db, seed_user, seed_periods, capsys,
    ):
        """Five bare rows -- the shapes production held -- through ``upgrade()``.

        An undated Projected envelope the companion may see; a dated Paid
        bill on the ``derived`` basis; a Paid bill on the ``corrected`` basis
        (plan ``$36.00``, settled ``$18.00`` -- DBCode's shape); an undated
        Received income; a Cancelled row.  After the cutover each names its
        own definition carrying its name, price and flags, is dated (the
        stated day, else the paycheck's start), records that day in
        ``occurs_on``, is TEMPLATE-declared with no override, and resolves to
        the cent to what it stored -- while what it RECORDED at settle is
        untouched.
        """
        with app.app_context():
            open_books_before_the_first_assertion(
                db.session, seed_user["account"],
                also_before=seed_periods[0].start_date,
            )
            db.session.commit()
            _run(_MIGRATION.downgrade)
            _assert_pre_cutover_schema()
            period = seed_periods[1]
            start = period.start_date
            stated_day = start.replace(day=start.day + 3)
            planted = {
                "envelope": _plant_bare(
                    seed_user, period, name="Homeschool Curriculum",
                    amount="1000.00", is_envelope=True, companion_visible=True,
                ),
                "derived": _plant_bare(
                    seed_user, period, name="Claude Max", amount="100.00",
                    status=StatusEnum.DONE, due_date=stated_day,
                    settled=(Decimal("100.00"), start, MovementFigureSourceEnum.RESOLVED),
                ),
                "corrected": _plant_bare(
                    seed_user, period, name="DBCode Lifetime License",
                    amount="36.00", status=StatusEnum.DONE,
                    settled=(Decimal("18.00"), start, MovementFigureSourceEnum.TYPED),
                ),
                "income": _plant_bare(
                    seed_user, period, name="Dental Reimbursement",
                    amount="166.40", kind=TxnTypeEnum.INCOME,
                    status=StatusEnum.RECEIVED,
                    settled=(Decimal("166.40"), start, MovementFigureSourceEnum.RESOLVED),
                ),
                "cancelled": _plant_bare(
                    seed_user, period, name="Clothes", amount="80.00",
                    status=StatusEnum.CANCELLED, is_envelope=True,
                ),
            }
            db.session.commit()
            definitions_before = db.session.query(TransactionTemplate).count()

            _run(_MIGRATION.upgrade)

            _assert_post_cutover_schema()
            assert "minted 5 definition(s), dated 4 row(s), re-attached 0" in (
                capsys.readouterr().out
            )
            assert (
                db.session.query(TransactionTemplate).count()
                == definitions_before + 5
            )
            db.session.expire_all()
            expected = {
                "envelope": ("Homeschool Curriculum", "1000.00", True, True, start),
                "derived": ("Claude Max", "100.00", False, False, stated_day),
                "corrected": ("DBCode Lifetime License", "36.00", False, False, start),
                "income": ("Dental Reimbursement", "166.40", False, False, start),
                "cancelled": ("Clothes", "80.00", True, False, start),
            }
            template_source = ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE)
            for key, row_id in planted.items():
                name, plan, envelope, companion, due = expected[key]
                row = db.session.get(Transaction, row_id)
                definition = row.template
                assert definition is not None and row.is_placed, key
                assert definition.recurs is False
                assert (definition.name, definition.default_amount) == (
                    name, Decimal(plan),
                ), key
                assert (definition.is_envelope, definition.companion_visible) == (
                    envelope, companion,
                ), key
                assert (row.tracks_purchases, row.visible_to_companion) == (
                    envelope, companion,
                ), key
                assert (definition.is_active, definition.sort_order) == (True, 0)
                assert (row.due_date, row.occurs_on) == (due, due), key
                versions = definition.amount_versions
                assert [(v.effective_date, v.amount) for v in versions] == [
                    (due, Decimal(plan)),
                ], key
                assert row.amount_source_id == template_source, key
                assert row.estimated_amount is None and row.is_override is False
                assert resolved_amount(row) == Decimal(plan), key
            # What the settle RECORDED stands: the corrected figure and the
            # derived ones, on the rows' covering movements.
            corrected = db.session.get(Transaction, planted["corrected"])
            assert settled_figure(corrected) == Decimal("18.00")
            assert settled_figure(
                db.session.get(Transaction, planted["derived"]),
            ) == Decimal("100.00")
            # The definitions are minted under the OWNER's names, so two
            # one-offs sharing a name are two definitions (paired by row id).
            assert len({
                db.session.get(Transaction, row_id).template_id
                for row_id in planted.values()
            }) == 5

    def test_two_bare_rows_of_one_name_are_paired_by_row_id(
        self, app, db, seed_user, seed_periods,
    ):
        """Names repeat across production's bare rows; each still gets ITS figure.

        Two "Claude Max" rows at ``$100.00`` and ``$200.00``: a pairing by
        name would give one of them the other's price, which is why the
        migration pairs by row id through its temporary map.
        """
        with app.app_context():
            _run(_MIGRATION.downgrade)
            first = _plant_bare(
                seed_user, seed_periods[1], name="Claude Max", amount="100.00",
            )
            second = _plant_bare(
                seed_user, seed_periods[2], name="Claude Max", amount="200.00",
            )
            db.session.commit()

            _run(_MIGRATION.upgrade)

            db.session.expire_all()
            rows = [db.session.get(Transaction, first), db.session.get(Transaction, second)]
            assert rows[0].template_id != rows[1].template_id
            assert [resolved_amount(row) for row in rows] == [
                Decimal("100.00"), Decimal("200.00"),
            ]
            assert [row.template.default_amount for row in rows] == [
                Decimal("100.00"), Decimal("200.00"),
            ]

    def test_a_credit_sources_payback_stays_linked_to_its_parent(
        self, app, db, seed_user, seed_periods,
    ):
        """A bare Credit row that is a payback's PARENT is minted; the payback is not.

        15 of production's 34 bare rows are payback parents.  The payback
        carries ``credit_payback_for_id`` as its one link and is left alone
        (it is not bare); its parent becomes a placed row, and the pair
        stays paired.
        """
        with app.app_context():
            _run(_MIGRATION.downgrade)
            parent = _plant_bare(
                seed_user, seed_periods[1], name="Cube storage", amount="45.00",
                status=StatusEnum.CREDIT, companion_visible=True,
            )
            db.session.commit()
            payback = db.session.execute(sa.text("""
                INSERT INTO budget.transactions
                    (user_id, account_id, pay_period_id, scenario_id, status_id,
                     name, category_id, transaction_type_id, estimated_amount,
                     credit_payback_for_id, is_override, is_deleted, version_id,
                     created_at, updated_at)
                VALUES (:uid, :aid, :pid, :sid, :stid, 'CC Payback: Cube storage',
                        :cat, :ttid, 45.00, :parent, FALSE, FALSE, 1, now(), now())
                RETURNING id
            """), {
                "uid": seed_user["user"].id, "aid": seed_user["account"].id,
                "pid": seed_periods[2].id, "sid": seed_user["scenario"].id,
                "stid": ref_cache.status_id(StatusEnum.PROJECTED),
                "cat": seed_user["categories"]["Groceries"].id,
                "ttid": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                "parent": parent,
            }).scalar_one()
            db.session.commit()

            _run(_MIGRATION.upgrade)

            db.session.expire_all()
            parent_row = db.session.get(Transaction, parent)
            payback_row = db.session.get(Transaction, payback)
            assert parent_row.is_placed and parent_row.template.companion_visible
            assert payback_row.credit_payback_for_id == parent
            assert payback_row.template_id is None
            assert resolved_amount(payback_row) == Decimal("45.00")
            # A row with no definition answers False (ruling R-BAL73).
            assert (payback_row.tracks_purchases, payback_row.visible_to_companion) == (
                False, False,
            )


class TestTheRefusals:
    """Every control the migration carries, SHOWN TO FIRE."""

    @pytest.mark.usefixtures("below_the_transfer_column_drop")
    def test_an_unmintable_bare_row_refuses_the_cutover_before_any_write(
        self, app, db, seed_user, seed_periods,
    ):
        """A bare row with no figure, or a date outside the version range, refuses.

        Both shapes the schema admits on a bare row and the definition's
        tables refuse: a row declaring the TEMPLATE relation with no link
        (``ck_transactions_amount_ownership`` lets it store no figure) and a
        row stated due in 1999.  The probe names both with their reasons,
        ``upgrade()`` raises before writing, and NOTHING has changed: the
        schema is the pre-cutover one and no definition was minted.
        """
        with app.app_context():
            _run(_MIGRATION.downgrade)
            declared = _plant_bare(
                seed_user, seed_periods[1], name="Declares nothing",
                amount="0", declared=True,
            )
            ancient = _plant_bare(
                seed_user, seed_periods[1], name="Y2K", amount="10.00",
                due_date=date(1999, 12, 31),
            )
            db.session.commit()
            definitions_before = db.session.query(TransactionTemplate).count()

            probe = _MIGRATION.rows_the_cutover_cannot_mint(_conn())
            assert [(row_id, reason) for row_id, _name, reason in probe] == [
                (declared, "stores no figure to price its definition from"),
                (ancient, "its due date is outside the version table's range"),
            ]

            with pytest.raises(RuntimeError, match="NOTHING has been changed") as exc:
                _run(_MIGRATION.upgrade)
            assert "2 bare row(s)" in str(exc.value)
            db.session.rollback()
            _assert_pre_cutover_schema()
            assert db.session.query(TransactionTemplate).count() == definitions_before
            assert db.session.get(Transaction, ancient).template_id is None

    @pytest.mark.usefixtures("below_the_transfer_column_drop")
    def test_a_bare_survivor_makes_the_one_link_check_refuse_the_ddl(
        self, app, db, seed_user, seed_periods,
    ):
        """The ``= 1`` CHECK is the proof that the mint covered every row.

        The mint is patched to write nothing, so a bare row survives to the
        DDL: ``ADD CONSTRAINT`` validates the whole table, refuses naming
        ``ck_transactions_one_pricing_link``, and the transaction rolls back
        whole -- the schema is still the pre-cutover one afterwards.
        """
        with app.app_context():
            _run(_MIGRATION.downgrade)
            survivor = _plant_bare(
                seed_user, seed_periods[1], name="Left behind", amount="5.00",
            )
            db.session.commit()

            with patch.object(_MIGRATION, "mint_definitions", return_value=(0, 0)):
                with pytest.raises(sa.exc.IntegrityError) as exc:
                    _run(_MIGRATION.upgrade)
            assert constraint_name_from(exc.value) == _ONE_LINK
            db.session.rollback()
            _assert_pre_cutover_schema()
            assert db.session.get(Transaction, survivor).template_id is None

    def test_both_restrict_keys_refuse_the_delete_that_would_null_a_link(
        self, app, db, seed_user, seed_periods,
    ):
        """After the cutover a definition with a row, and a Credit source with a payback, cannot go alone.

        The two deletes ``SET NULL`` used to perform silently: each would
        have manufactured a zero-link row, and each is refused naming its
        key.  The second is finding **CC-352**'s window, disclosed: the
        bulk paths that delete a Credit source without its payback meet this
        refusal until the card cutover deletes every payback (R-CC17).
        """
        with app.app_context():
            open_books_before_the_first_assertion(
                db.session, seed_user["account"],
                also_before=seed_periods[0].start_date,
            )
            envelope = _one_off(
                seed_user, seed_periods[1], name="Card envelope", is_envelope=True,
            )
            payback = payback_row_of(
                db.session, seed_user, envelope, Decimal("30.00"),
                seed_periods[1].start_date,
            )
            db.session.commit()

            with pytest.raises(sa.exc.IntegrityError) as exc:
                db.session.execute(sa.text(
                    "DELETE FROM budget.transaction_templates WHERE id = :d"
                ), {"d": envelope.template_id})
            assert constraint_name_from(exc.value) == _TEMPLATE_KEY
            db.session.rollback()

            with pytest.raises(sa.exc.IntegrityError) as exc:
                db.session.execute(sa.text(
                    "DELETE FROM budget.transactions WHERE id = :p"
                ), {"p": envelope.id})
            assert constraint_name_from(exc.value) == _PAYBACK_KEY
            db.session.rollback()
            assert db.session.get(Transaction, payback.id).credit_payback_for_id == envelope.id


class TestTheResidueRowsAreReattached:
    """Step 5 of the migration (ruling R-BAL37), driven at head."""

    def test_a_detached_only_row_restates_its_definition_and_re_attaches(
        self, app, db, seed_user, seed_periods,
    ):
        """The 7b-1 interim's shape: OWN ``$170.00`` under a definition at ``$162.25``.

        The version the row's date reads is restated to the row's figure,
        ``default_amount`` follows, and the row is declared TEMPLATE-priced
        with the flag cleared -- so the row, the definition and the series
        read ``$170.00`` and nothing else.
        """
        with app.app_context():
            row = _one_off(seed_user, seed_periods[1])
            state_own_amount(row, Decimal("170.00"))
            row.is_override = True
            db.session.commit()
            assert row.amount_source_id is None and row.is_override

            assert _MIGRATION.reattach_residue_rows(_conn()) == 1
            db.session.commit()

            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.estimated_amount is None and row.is_override is False
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert row.template.default_amount == Decimal("170.00")
            assert [v.amount for v in row.template.amount_versions] == [Decimal("170.00")]
            assert resolved_amount(row) == Decimal("170.00")

    def test_a_flagged_but_template_priced_row_only_loses_the_flag(
        self, app, db, seed_user, seed_periods,
    ):
        """No figure of its own to restate: the definition's price stands."""
        with app.app_context():
            row = _one_off(seed_user, seed_periods[1])
            row.is_override = True
            db.session.commit()

            assert _MIGRATION.reattach_residue_rows(_conn()) == 1
            db.session.commit()

            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.is_override is False
            assert row.template.default_amount == Decimal("162.25")
            assert resolved_amount(row) == Decimal("162.25")

    def test_a_many_row_definitions_own_row_is_left_alone(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling R-BAL43: a sibling's OWN figure is legitimate, not residue.

        A rule-less definition holding two placed rows, one of them OWN with
        the flag: the re-attach selects nothing, the definition keeps its
        price and the OWN row its figure.
        """
        with app.app_context():
            first = _one_off(seed_user, seed_periods[1], name="Amazon", amount="0.00")
            sibling = place_row_of(
                first.template, derived_span(seed_periods[2]),
                scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()
            state_own_amount(sibling, Decimal("120.00"))
            sibling.is_override = True
            db.session.commit()

            assert _MIGRATION.reattach_residue_rows(_conn()) == 0
            db.session.commit()

            db.session.expire_all()
            sibling = db.session.get(Transaction, sibling.id)
            assert sibling.is_override is True
            assert sibling.estimated_amount == Decimal("120.00")
            assert sibling.template.default_amount == Decimal("0.00")
            assert resolved_amount(db.session.get(Transaction, first.id)) == Decimal("0.00")


@pytest.mark.usefixtures("below_the_transfer_column_drop")
class TestTheDowngradeIsSchemaOnly:
    """Ruling R-BAL67: the schema returns and no row moves."""

    def _snapshot(self):
        """Every row of the three tables the migration writes, by id."""
        return {
            table: db.session.execute(sa.text(
                f"SELECT * FROM budget.{table} ORDER BY id"
            )).mappings().all()
            for table in (
                "transactions", "transaction_templates", "template_amount_versions",
            )
        }

    def test_the_downgrade_restores_the_schema_under_the_prior_names_and_moves_no_row(
        self, app, db, seed_user, seed_periods, capsys,
    ):
        """Down, then up again: the same rows, and a re-upgrade mints nothing.

        A placed one-off and its definition go through both directions.  The
        downgrade re-adds both columns at ``false``, restores the CHECK to
        ``<= 1`` and both keys to ``SET NULL`` under the names they carried,
        and leaves every row as it was (the two re-added cells aside); the
        re-upgrade selects nothing, so it mints 0 and dates 0.
        """
        with app.app_context():
            row = _one_off(seed_user, seed_periods[1], is_envelope=True)
            before = self._snapshot()

            _run(_MIGRATION.downgrade)

            _assert_pre_cutover_schema()
            after_down = self._snapshot()
            for table in ("transaction_templates", "template_amount_versions"):
                assert after_down[table] == before[table], table
            assert len(after_down["transactions"]) == len(before["transactions"])
            for was, now in zip(before["transactions"], after_down["transactions"]):
                assert dict(now, is_envelope=None, companion_visible=None) == dict(
                    was, is_envelope=None, companion_visible=None,
                )
                assert (now["is_envelope"], now["companion_visible"]) == (False, False)
            # The row still names its definition and is still priced by it:
            # the pre-cutover application reads exactly this shape.
            db.session.expire_all()
            assert db.session.get(Transaction, row.id).template_id == row.template_id
            assert resolved_amount(db.session.get(Transaction, row.id)) == Decimal("162.25")

            _run(_MIGRATION.upgrade)

            _assert_post_cutover_schema()
            assert "minted 0 definition(s), dated 0 row(s), re-attached 0" in (
                capsys.readouterr().out
            )
            assert self._snapshot() == before
