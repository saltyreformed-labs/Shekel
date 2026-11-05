"""A transaction's OWNER is a column, and two composite keys hold it there.

Plan step **pay_calendar:C13-a**, migration ``d4a92f6b13c8``, ruling
**R-PC32**.

**What the keys replaced.**  ``budget.transactions`` carried no owner: a row's
owner was its pay period's, and nothing required that owner to be its
ACCOUNT's.  So a row filed in one person's paycheck against another person's
account was a STORABLE state, refused only by whichever door happened to look
-- and every door that refuses a foreign row had to state the relationship by
hand, which finding **P75** counts nineteen times in ``app/``.

**Why the SHAPE of the keys is graded here.**  The nineteen comparisons each
restate the same premise in their own words, and plan step ``C13-b`` is what
retires them.  This file grades the premise itself, so the readers can go on
stating only what they read.  It is the shape
``test_c4b2_pay_period_schedule_key`` uses one table over, and for the reason
that file gives.

*Two suites outside this file assert a key by name, and neither is a second
home for what is graded here*: ``test_statement_match/test_candidates`` and
``/test_create`` each show that a case whose SUBJECT this step made
unstorable now meets a refusal, which is a fact about those suites' own
fixtures rather than about the DDL.

**Every negative here has a CONTROL beside it**, because a refusal that fires
for the wrong reason grades nothing: each cross-owner case is paired with the
same INSERT built consistently, which must SUCCEED.  Without that pair a typo
in a fixture would produce a green refusal over a row the database was
rejecting for some other constraint entirely.

**The user delete is DRIVEN rather than argued from the DDL.**  Whether a
``DELETE FROM auth.users`` succeeds depends on referential-trigger ORDER across
three cascades and two RESTRICTs, which no reading of the schema settles --
and the shape that ships was chosen BECAUSE driving it showed the obvious
alternative (``UserScopedMixin``'s ``ON DELETE CASCADE``) turned a refused
statement into one that empties the database.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.amount_ownership import AmountOwnership
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.db_errors import sqlstate_of
from tests._test_helpers import bare_expense_template

#: The three keys this step installs, spelled once.  Every assertion reads the
#: DATABASE for them rather than the model, because the model and the migration
#: are two statements of one constraint and the point is that they agree.
_ACCOUNT_KEY = "fk_transactions_owner_account"
_PERIOD_KEY = "fk_transactions_owner_period"
_USER_KEY = "fk_transactions_user_id"
_SUPERKEY = "uq_pay_periods_id_user"


def _txn_kwargs(seed_user, period, **overrides):
    """Return the kwargs for a legal, ordinary Projected transaction.

    Everything is the seeded owner's unless a case overrides it, so a case
    states only the axis it is about -- and the control that must succeed is
    this dict with nothing overridden.

    **BARE on purpose; its pricing link is a rule-less definition of the
    seeded owner's** (plan step ``balance:X-bi-7d-1``).  The subject is the
    three owner keys on ``budget.transactions``, every negative asserts a key
    BY NAME, and the control that must succeed is this dict with nothing
    overridden -- so a door cannot stand in (the producer refuses a
    stranger's paycheck one tier ABOVE the key, and R-BAL59 (3) names this
    file as where the key is graded on a bare row).  What the row stopped
    being is LINK-LESS: the family's cutover (``X-bi-7d-2``) re-cuts
    ``ck_transactions_one_pricing_link`` to ``= 1``, so every row built from
    this dict names its own definition
    (:func:`~tests._test_helpers.bare_expense_template`, always the SEEDED
    owner's, whichever owner a case then claims) and carries the paycheck's
    start as the day it is due and the occurrence it answers.  No composite
    key names ``template_id``, so the definition's single-column key is
    satisfied on every cross-owner row and never the one reported -- measured
    at 7d-1: every by-name assertion below, ``_USER_KEY`` on the user delete
    included, names the key it named before the link.
    """
    definition = bare_expense_template(
        _db.session, seed_user, name="Owner key control definition",
    )
    fields = {
        "user_id": period.user_id,
        "template_id": definition.id,
        "account_id": seed_user["account"].id,
        "pay_period_id": period.id,
        "scenario_id": seed_user["scenario"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Owner key control",
        "category_id": seed_user["categories"]["Groceries"].id,
        "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        "amount_ownership": AmountOwnership.own(Decimal("25.00")),
        "due_date": period.start_date,
        "occurs_on": period.start_date,
    }
    fields.update(overrides)
    return fields


class TestTheColumnAndItsKeysAreInstalled:
    """The storage tier holds what the model and the migration both claim."""

    def test_transactions_carries_a_not_null_user_id(self, app, db):
        """``user_id`` exists, is INTEGER, and admits no NULL.

        The TYPE is asserted and not just the name: the composite keys target
        integer columns, so a text ``user_id`` would fail at ``ADD
        CONSTRAINT`` -- but a ``BigInteger`` would not, and would then differ
        from every other ``user_id`` in the schema.
        """
        with app.app_context():
            columns = {
                c["name"]: c for c in
                inspect(db.engine).get_columns("transactions", schema="budget")
            }
            assert "user_id" in columns, (
                "budget.transactions.user_id is missing -- plan step "
                "pay_calendar:C13-a's column did not land."
            )
            assert columns["user_id"]["nullable"] is False, (
                "budget.transactions.user_id is NULLABLE; a row with no owner "
                "is exactly the state this step exists to forbid."
            )
            assert isinstance(columns["user_id"]["type"], sa.Integer), (
                f"budget.transactions.user_id is "
                f"{columns['user_id']['type']!r}, not INTEGER."
            )

    @pytest.mark.parametrize(
        "name, columns, referred, referred_columns, ondelete",
        [
            (_ACCOUNT_KEY, ["account_id", "user_id"], "accounts",
             ["id", "user_id"], "RESTRICT"),
            (_PERIOD_KEY, ["pay_period_id", "user_id"], "pay_periods",
             ["id", "user_id"], "CASCADE"),
            (_USER_KEY, ["user_id"], "users", ["id"], "RESTRICT"),
        ],
        ids=["account", "pay_period", "user"],
    )
    def test_each_key_is_installed_with_its_ruled_action(
        self, app, db, name, columns, referred, referred_columns, ondelete,
    ):
        """Each key exists over the right columns with the right ON DELETE.

        **The REFERENCED columns are asserted too, in order.**  Without that,
        ``REFERENCES budget.accounts (user_id, id)`` passes this test: both
        sides are integers and ``uq_accounts_id_user`` covers the pair either
        way round, so PostgreSQL accepts the key and it then holds the wrong
        two columns equal.  ``convalidated`` is asserted for the same reason a
        ``NOT VALID`` key would satisfy every name check while refusing
        nothing about the rows already there.

        The ACTION is asserted, not just the key: ``fk_transactions_owner_*``
        must match the single-column key beside it (RESTRICT for the account,
        CASCADE for the pay period) or a parent delete's outcome depends on
        which of the two PostgreSQL evaluated.  ``fk_transactions_user_id`` is
        RESTRICT by the developer's ruling of 2026-09-02, taken against the
        ``UserScopedMixin`` CASCADE that ``TestDeletingAnOwnerIsRefused``
        drives.
        """
        with app.app_context():
            row = db.session.execute(text("""
                SELECT confdeltype, convalidated,
                       (SELECT relname FROM pg_class
                         WHERE oid = c.confrelid) AS referred_table,
                       (SELECT array_agg(attname ORDER BY ord)
                          FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                          JOIN pg_attribute a
                            ON a.attrelid = c.conrelid
                           AND a.attnum = k.attnum) AS cols,
                       (SELECT array_agg(attname ORDER BY ord)
                          FROM unnest(c.confkey) WITH ORDINALITY AS k(attnum, ord)
                          JOIN pg_attribute a
                            ON a.attrelid = c.confrelid
                           AND a.attnum = k.attnum) AS refcols
                  FROM pg_constraint c
                 WHERE c.conrelid = 'budget.transactions'::regclass
                   AND c.conname = :name
            """), {"name": name}).one_or_none()
            assert row is not None, f"foreign key {name} is not installed"
            assert list(row.cols) == columns
            assert row.referred_table == referred
            assert list(row.refcols) == referred_columns
            assert row.convalidated is True, (
                f"{name} is NOT VALID, so it refuses nothing about the rows "
                f"that were already there."
            )
            assert {"r": "RESTRICT", "c": "CASCADE", "a": "NO ACTION",
                    "n": "SET NULL"}[row.confdeltype] == ondelete

    def test_the_pay_period_superkey_exists(self, app, db):
        """``uq_pay_periods_id_user`` is what the period key targets.

        It constrains nothing -- ``id`` is already the primary key -- and
        exists only because PostgreSQL requires a UNIQUE over exactly the
        referenced columns.  Asserted rather than assumed: dropping it would
        take ``fk_transactions_owner_period`` with it, and a schema with the
        column but not the key is the state this step is about.
        """
        with app.app_context():
            cols = db.session.execute(text("""
                SELECT array_agg(a.attname ORDER BY ord) AS cols
                  FROM pg_constraint c
                  JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                    ON TRUE
                  JOIN pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                 WHERE c.conrelid = 'budget.pay_periods'::regclass
                   AND c.conname = :name
            """), {"name": _SUPERKEY}).scalar()
            assert cols is not None, f"{_SUPERKEY} is not installed"
            assert list(cols) == ["id", "user_id"]


class TestACrossOwnerRowCannotBeWritten:
    """The state the nineteen hand-written comparisons were policing."""

    def test_the_consistent_row_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):
        """THE CONTROL: the same INSERT, built consistently, succeeds.

        Every refusal below is this row with exactly one parent moved to a
        stranger.  Without this case they would all pass just as well against
        a fixture that could not write a transaction at all.
        """
        with app.app_context():
            txn = Transaction(**_txn_kwargs(seed_user, seed_periods[0]))
            db.session.add(txn)
            db.session.flush()
            assert txn.id is not None
            assert txn.user_id == seed_user["user"].id

    def test_a_stranger_s_account_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The owner's paycheck, somebody else's account.

        Refused by ``fk_transactions_owner_account``: the row states the
        seeded owner, and ``(that account, that owner)`` is not a pair
        ``budget.accounts`` holds.
        """
        with app.app_context():
            db.session.add(Transaction(**_txn_kwargs(
                seed_user, seed_periods[0],
                account_id=seed_second_user["account"].id,
            )))
            with pytest.raises(IntegrityError) as exc:
                db.session.flush()
            assert _ACCOUNT_KEY in str(exc.value)
            db.session.rollback()

    def test_a_stranger_s_pay_period_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The owner's account, somebody else's paycheck.

        The mirror of the case above, and the reason BOTH keys are needed:
        either one alone leaves the other parent free to belong to anyone.
        """
        with app.app_context():
            db.session.add(Transaction(**_txn_kwargs(
                seed_user, seed_periods[0],
                pay_period_id=seed_second_user["bootstrap_period"].id,
            )))
            with pytest.raises(IntegrityError) as exc:
                db.session.flush()
            assert _PERIOD_KEY in str(exc.value)
            db.session.rollback()

    def test_claiming_a_stranger_as_the_owner_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Both parents the owner's, the OWNER column somebody else's.

        The shape a writer reaches by copying the owner off the wrong object:
        the row is internally plausible and every id in it exists.  Refused
        because the pair, not the id, is what the key checks.
        """
        with app.app_context():
            db.session.add(Transaction(**_txn_kwargs(
                seed_user, seed_periods[0],
                user_id=seed_second_user["user"].id,
            )))
            with pytest.raises(IntegrityError) as exc:
                db.session.flush()
            # EITHER key, because this row violates BOTH and which one
            # PostgreSQL reports is constraint-OID order -- stable only while
            # the migration happens to create the account key first.  Naming
            # one would be asserting the creation order, which is the same
            # fragility this file declines to assert for the account delete.
            assert _ACCOUNT_KEY in str(exc.value) or _PERIOD_KEY in str(
                exc.value,
            ), str(exc.value)
            db.session.rollback()

    def test_a_row_with_no_owner_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A writer that simply forgets gets a NOT NULL, not a NULL owner.

        This is the arm that makes "every writer states it" a property of the
        table rather than a census of call sites: there is no default and no
        ORM hook, so the omission is loud at the first flush.
        """
        with app.app_context():
            fields = _txn_kwargs(seed_user, seed_periods[0])
            del fields["user_id"]
            db.session.add(Transaction(**fields))
            with pytest.raises(IntegrityError) as exc:
                db.session.flush()
            # The NOT NULL message, not merely the column name: every one of
            # the three keys over this column carries "user_id" in its own
            # violation text, so a looser assertion would pass for a foreign
            # key firing instead.
            assert sqlstate_of(exc.value) == "23502", sqlstate_of(exc.value)
            assert 'null value in column "user_id"' in str(exc.value)
            db.session.rollback()


class TestAStoredRowCannotBeMovedAcrossOwners:
    """An UPDATE is the other half, and the INSERT cases do not cover it."""

    def test_repointing_the_account_at_a_stranger_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Moving a stored row onto another person's account is refused.

        The path a re-parenting door reaches -- ``transfer_service`` assigns
        ``shadow.account`` and ``shadow.pay_period_id`` on a restore and on an
        endpoint change -- so this is the constraint standing behind those
        writes rather than a hypothetical.
        """
        with app.app_context():
            txn = Transaction(**_txn_kwargs(seed_user, seed_periods[0]))
            db.session.add(txn)
            db.session.flush()

            txn.account_id = seed_second_user["account"].id
            with pytest.raises(IntegrityError) as exc:
                db.session.flush()
            assert _ACCOUNT_KEY in str(exc.value)
            db.session.rollback()

    def test_repointing_the_owner_alone_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Handing a stored row to somebody else, parents untouched.

        The half a single-column ``user_id`` key would admit: the id names a
        real user, so only the PAIR can refuse it.
        """
        with app.app_context():
            txn = Transaction(**_txn_kwargs(seed_user, seed_periods[0]))
            db.session.add(txn)
            db.session.flush()

            txn.user_id = seed_second_user["user"].id
            with pytest.raises(IntegrityError) as exc:
                db.session.flush()
            # EITHER key: this row violates both, and the docstring's own
            # point is that only the PAIR can refuse it.
            assert _ACCOUNT_KEY in str(exc.value) or _PERIOD_KEY in str(
                exc.value,
            ), str(exc.value)
            db.session.rollback()


class TestTheParentDeletesAreUnCHANGED:
    """The new keys carry their neighbours' actions, so nothing moved."""

    def test_deleting_a_pay_period_still_takes_its_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """CASCADE, as ``transactions_pay_period_id_fkey`` has always done.

        Both keys over ``pay_period_id`` cascade, which is the point: two keys
        over one column deleting differently would make this outcome depend on
        which PostgreSQL evaluated.
        """
        with app.app_context():
            period = seed_periods[-1]
            txn = Transaction(**_txn_kwargs(seed_user, period))
            db.session.add(txn)
            db.session.flush()
            txn_id = txn.id

            # The before-state, read through the SAME raw path as the after,
            # so "it is gone" cannot be satisfied by a row that was never
            # there.
            assert db.session.execute(
                text("SELECT count(*) FROM budget.transactions WHERE id = :tid"),
                {"tid": txn_id},
            ).scalar() == 1

            db.session.execute(
                text("DELETE FROM budget.pay_periods WHERE id = :pid"),
                {"pid": period.id},
            )
            db.session.flush()
            # The row is read back with a fresh SELECT rather than through
            # ``session.get``: the DELETE was raw SQL, so the identity map still
            # holds the object and ``get`` would answer from it -- a green
            # assertion over a row the database had already removed OR still
            # held, indifferently.
            survivors = db.session.execute(
                text("SELECT count(*) FROM budget.transactions WHERE id = :tid"),
                {"tid": txn_id},
            ).scalar()
            assert survivors == 0

    def test_deleting_an_account_is_still_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """RESTRICT: a transaction must not silently vanish with its account.

        The refusal may name either key over ``account_id`` -- they carry the
        same action, so which one PostgreSQL reaches first is not this test's
        business; that they AGREE is, and this asserts it by requiring the
        named key to be one of the two.

        **On its own this case would pass unchanged on ``origin/dev``**, where
        only ``transactions_account_id_fkey`` exists -- it is a regression
        guard on behaviour this step must not alter, not evidence about the
        new key.  What grades the new key's action is
        :func:`test_each_key_is_installed_with_its_ruled_action`, and the two
        together are the claim: the action is RESTRICT, and RESTRICT is still
        what happens.
        """
        with app.app_context():
            db.session.add(Transaction(**_txn_kwargs(seed_user, seed_periods[0])))
            db.session.flush()

            with pytest.raises(IntegrityError) as exc:
                db.session.execute(
                    text("DELETE FROM budget.accounts WHERE id = :aid"),
                    {"aid": seed_user["account"].id},
                )
                db.session.flush()
            assert "RESTRICT" in str(exc.value)
            assert _ACCOUNT_KEY in str(exc.value) or (
                "transactions_account_id_fkey" in str(exc.value)
            ), str(exc.value)
            db.session.rollback()


class TestDeletingAnOwnerIsRefused:
    """The arm the developer ruled on, driven rather than read off the DDL."""

    def test_a_user_holding_transactions_cannot_be_deleted(
        self, app, db, seed_user, seed_periods,
    ):
        """``DELETE FROM auth.users`` is refused, and the key naming it is ours.

        **This case is the ruling** (Josh, 2026-09-02).  Reusing
        ``UserScopedMixin`` here would have given ``user_id`` an ``ON DELETE
        CASCADE``, and driving this statement under that shape on a clone of
        the developer's database returned ``DELETE 1``, leaving 1,057
        transactions, 9 accounts, 63 pay periods, 1,342 journal entries, 184
        purchase entries, 82 balance assertions and 175 transfers all at zero
        -- because with the transactions cascading directly, the
        ``budget.accounts`` RESTRICT that refuses the statement today has no
        row left to object about.

        Asserting the CONSTRAINT NAME and not merely the refusal is what makes
        this a test of the ruled arm: without ``fk_transactions_user_id`` the
        statement is still refused, one hop later, by
        ``transactions_account_id_fkey``.  A test that accepted either would
        pass just as well against the shape the ruling rejected reaching
        through a different door.
        """
        with app.app_context():
            db.session.add(Transaction(**_txn_kwargs(seed_user, seed_periods[0])))
            db.session.flush()

            with pytest.raises(IntegrityError) as exc:
                db.session.execute(
                    text("DELETE FROM auth.users WHERE id = :uid"),
                    {"uid": seed_user["user"].id},
                )
                db.session.flush()
            assert _USER_KEY in str(exc.value)
            assert "RESTRICT" in str(exc.value)
            db.session.rollback()


class TestTheOwnerTravelsWithTheRowThroughItsWriters:
    """The doors state it, and the value they state is the parents'."""

    def test_a_generated_row_takes_its_template_s_owner(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The recurrence engine writes the owner it already knows.

        ``user_id`` sits beside ``template_id`` in that constructor rather
        than inside ``DerivedRowFields``, because a maintain pass ``setattr``s
        every derived field onto an EXISTING row and a row does not change
        hands because its template was edited.  This case grades the VALUE;
        :func:`test_the_owner_is_not_a_DERIVED_field` grades the placement.

        **The template belongs to the SECOND owner**, and that is what makes
        the case able to fail for the reason it names.  On the seeded owner's
        own template the column is NOT NULL and every candidate value in scope
        is the same integer, so the only reachable failure would be
        "generation wrote nothing" -- which the ``assert rows`` below already
        covers.  Here a constructor that read the owner off anything but the
        template writes the wrong one, and the composite keys refuse it.
        """
        from app.models.transaction_template import (  # noqa: PLC0415
            TransactionTemplate,
        )
        from app.services import recurrence_engine  # noqa: PLC0415
        from app.services.balance_at import BalanceContext  # noqa: PLC0415
        from app.services.generation_schedule import (  # noqa: PLC0415
            GenerationSchedule,
        )
        from tests._test_helpers import make_every_period_rule  # noqa: PLC0415

        with app.app_context():
            owner = seed_second_user["user"]
            template = TransactionTemplate(
                user_id=owner.id,
                account_id=seed_second_user["account"].id,
                category_id=seed_second_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                name="Owner key generation",
            )
            db.session.add(template)
            db.session.flush()
            make_every_period_rule(db.session, template)
            db.session.flush()

            their_periods = db.session.query(PayPeriod).filter_by(
                user_id=owner.id,
            ).all()
            assert their_periods, "the second owner holds no paydays"
            created = recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(owner.id),
                    {p.id for p in their_periods},
                ),
                seed_second_user["scenario"].id,
            )
            db.session.flush()

            rows = db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).all()
            assert rows, f"generation wrote nothing ({created})"
            assert {r.user_id for r in rows} == {owner.id}

    def test_a_created_row_takes_the_submitting_owner(
        self, app, auth_client, seed_user, seed_second_user,
        seed_periods_today,
    ):
        """A SUBMITTED owner is dropped and the session's is used.

        ``user_id`` is not a field on either create schema
        (``unknown = EXCLUDE``), and the route assigns it from the session
        unconditionally after the ownership probe has proved the submitted
        parents are that user's.

        **The form posts a FOREIGN ``user_id``**, which is what makes this a
        test of that rule rather than of the column's existence: a door that
        honoured the submitted value would file this row against a stranger,
        and one that merely ignored the field would be indistinguishable from
        a door that has no rule at all if the field were never sent.
        """
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "31.00",
                "account_id": seed_user["account"].id,
                "category_id": category.id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": ref_cache.txn_type_id(
                    TxnTypeEnum.EXPENSE,
                ),
                "scenario_id": seed_user["scenario"].id,
                "user_id": seed_second_user["user"].id,
            })
            assert resp.status_code == 201, resp.data[:400]

            # Found by the NAME the door defaulted, not by a figure: the row
            # states none since plan step balance:X-bi-7b (its definition
            # prices it, R-BAL21).  The definition takes the same owner.
            row = _db.session.query(Transaction).filter_by(
                name=category.display_name,
            ).one()
            assert row.user_id == seed_user["user"].id
            assert row.template.user_id == seed_user["user"].id

    def test_the_owner_is_not_a_DERIVED_field(self):
        """``user_id`` is not in ``DerivedRowFields``, and that is placement.

        The maintain loop ``setattr``s every member of that record onto rows
        it ALREADY HAS, so a ``user_id`` in it would rewrite an existing row's
        owner from its template on every pass.  On a single-owner database
        that rewrite is invisible -- it writes back the value it read -- which
        is why this is asserted as a FIELD SET and not as a value, and why it
        is asserted at all rather than left to a behavioural case that could
        not fail here.

        **It is a structural assertion and needs no database**, which is the
        whole of what it costs: no fixture, no clone, no seed.
        """
        from app.services.recurrence_engine import _amounts  # noqa: PLC0415

        assert "user_id" not in _amounts.DerivedRowFields._fields, (
            "user_id is in DerivedRowFields, so a maintain pass now "
            "rewrites an existing row's owner from its template."
        )
