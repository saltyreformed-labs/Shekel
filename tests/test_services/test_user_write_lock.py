"""
Shekel Budget App -- The per-user write lock, graded where it is taken (plan steps X-f1c3c, balance:X-bn)

Grades the lock that serialises every posting-ledger reconcile, every anchor
and opening compare-then-append, and every structural pay-period mutation for
one owner (:mod:`app.services.user_write_lock`).

**What broke without it, measured.**  A reconcile is a read-modify-write:
read what the ledger has posted, subtract it from what the account's facts
say it should hold, write the difference.  With the interleave forced at the
read, two concurrent true-ups on an account reconciled at ``$4,000.00`` both
answered 200 and left the account's linked ledger at ``$1,000.00`` while its
resolved assertion read ``$2,000.00``.  Both sides wrong; the trial balance
still ``$0.00``, because the anchor-equity leg mirrors the error exactly, so
nothing failed loudly.  That forced-interleave probe cannot be kept as a test:
once the lock exists the second request blocks at it, so the probe's own
mechanism is what the fix removes.  These tests grade the lock instead.

**Where it is graded moved with where it is taken** (plan step
``balance:X-bn``, rulings **R-CC106**, **R-CC114**, **R-CC115**).  Until then
each reconcile and anchor door took the lock for itself, and this module
called those services directly and graded each one's acquisition.  Now no
service takes it: every command transaction a signed-in request opens takes
it where it begins (:mod:`app.db_transaction`), and a service called outside a
request takes none -- which ``tests/test_arch/test_the_owner_lock_has_one_home.py``
enforces.  So every property below is graded on a REAL REQUEST through the
door production uses, and the same five properties survive the move:

1. **The lock is taken, and taken BEFORE the owner's data is read.**  A lock
   acquired after the read serialises nothing -- the loser has already read the
   same pre-state.  Graded as strongly as the design allows: every statement
   the request issues before the lock names no table but ``auth.users``,
   ``ref.user_roles`` and ``auth.sign_ins`` -- the signed-in user's row, the
   role row joined to it and whether the session's sign-in record is live,
   read to sign the request in (the check is by TABLE, not by row) -- and
   the door's own reads (the ledger, the governing assertion, the opening, the
   movements) are asserted to happen at all, so the ordering cannot pass over
   a run that read nothing.
2. **The signed-in user's row is read AGAIN under the lock**, because it was
   read before it (:func:`app.db_transaction._lock_the_open_transaction`); the
   role row read with it is a reference row no request writes.
3. **A render takes no lock; its write block does** (ruling **R-CC114**).  A
   plain page load runs ``READ ONLY`` and takes nothing; ``/grid``'s rolling
   top-up runs in a ``write_transaction`` block, a command transaction, which
   takes the owner's key once.
4. **It really serialises.**  A second transaction holding the same key blocks
   a save, and the same save completes when the key is free.  The only
   property that grades PostgreSQL's behaviour rather than our SQL.
5. **It is keyed on the OWNER**: one distinct key per request however much it
   writes, a companion's request takes its linked owner's key, and the
   deploy's every-owner form acquires ascending.  **That is NOT "deadlock is
   structurally impossible on every path"**: the deploy transaction takes the
   owners' keys after its migrations' table locks, which is safe only because
   the entrypoint runs it before gunicorn serves (the module docstring of
   :mod:`app.services.user_write_lock`).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.ref import AccountType
from app.services import account_service, cash_ledger
from app.services.user_write_lock import (
    lock_every_user_writes,
    take_owner_write_lock,
)
from app.utils.dates import display_today
from tests._test_helpers import (
    HeldOwnerKey,
    advisory_lock_keys,
    assert_timed_out_on_owner_key,
    bound_lock_waits,
    capture_sql_statements,
    create_loan_account,
    generate_row_of,
    make_expense_template,
    owner_lock_key,
    took_advisory_lock,
)

#: What a statement issued BEFORE the owner's lock may touch, all of it read to
#: sign the request in: the signed-in user's row (and the role row joined to
#: it), and whether the session's sign-in record is live, asked as one EXISTS
#: so no record is loaded (``app.services.sign_in_service.is_live``, ruling
#: plan#4068).  The lock is keyed on the signed-in user's owner, so neither can
#: follow it.  Anything else before the lock is a read the lock does not cover.
_READABLE_BEFORE_THE_LOCK = ("auth.users", "ref.user_roles", "auth.sign_ins")

#: The application's schemas: a ``schema.table`` word in a statement names a
#: table only when its first part is one of these.
_SCHEMAS = ("auth", "budget", "salary", "ref", "system")


def _lock_index(statements):
    """Return the index of the first owner-lock acquisition, or ``None``."""
    return next(
        (
            i for i, (sql, _params) in enumerate(statements)
            if "pg_advisory_xact_lock" in sql
        ),
        None,
    )


def _reads_before_the_lock(statements):
    """Return every statement before the first lock that names a table a sign-in does not read.

    A statement that names no schema-qualified table at all (a ``SET``, the
    audit actor's ``set_config``) reads no row and is not returned.  Every
    ``"`` is dropped before the split, so a quoted identifier
    (``"budget"."accounts"``) is read as the table it names: SQLAlchemy quotes
    nothing in this schema, but a hand-written ``text()`` statement may.

    Args:
        statements: The ``(statement, parameters)`` list from
            :func:`tests._test_helpers.capture_sql_statements`.

    Returns:
        The offending statements' text, empty when the lock precedes every
        read of the owner's data.
    """
    lock_at = _lock_index(statements)
    assert lock_at is not None, "the request took no lock at all"
    offending = []
    for sql, _params in statements[:lock_at]:
        tables = {
            ".".join(word.strip("(),").split(".")[:2])
            for word in sql.replace('"', "").split()
            if word.strip("(").split(".", 1)[0] in _SCHEMAS
        }
        if tables - set(_READABLE_BEFORE_THE_LOCK):
            offending.append(sql)
    return offending


def _read_after_the_lock(statements, table):
    """Return whether *table* is read at all, and only after the first lock.

    The non-vacuity half: an ordering assertion passes over a run that never
    read the table, so the read is asserted to exist.
    """
    lock_at = _lock_index(statements)
    reads = [
        i for i, (sql, _params) in enumerate(statements) if table in sql
    ]
    return bool(reads) and lock_at is not None and reads[0] > lock_at


def _checking_account(seed_user, name="Lock Probe Checking", balance="1500.00"):
    """Create and COMMIT a Checking account carrying one balance assertion.

    A fresh account with a non-zero anchor is the smallest thing whose
    reconcile writes: its opening correction is non-zero, so the save under
    test does real read-modify-write work rather than short-circuiting.
    Built through the service, outside any request, so it takes no lock --
    which is the design: only a request's command transaction takes it.

    Args:
        seed_user: The seeded-user fixture dict.
        name: The account's name.
        balance: The anchor balance, as a string.

    Returns:
        The created :class:`~app.models.account.Account`, committed so a
        request reads it.
    """
    checking_type = db.session.query(AccountType).filter_by(
        name="Checking",
    ).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name=name,
            anchor_balance=Decimal(balance),
        ),
    )
    db.session.commit()
    return account


def _true_up(client, account_id, balance="1750.00"):
    """PATCH a cash true-up as the grid's balance form does."""
    return client.patch(
        f"/accounts/{account_id}/true-up", data={"anchor_balance": balance},
    )


class TestASaveLocksBeforeItReadsTheOwnersData:
    """Property 1 on every door this module used to grade one service at a time."""

    def test_a_cash_true_up_reads_nothing_of_the_owners_before_the_lock(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The cash true-up: the governing assertion and the ledger, both after the lock.

        The door's compare-then-append (ruling R-EQ) and the reconcile it
        reaches (the account anchor sync) are both read-modify-writes, so both
        of their reads are named, and every statement before the lock is
        checked to name no table but the three a sign-in reads (the user's
        row, the role row joined to it and the sign-in's record).
        """
        assert seed_periods_today
        with app.app_context():
            account_id = _checking_account(seed_user).id

            response, statements = capture_sql_statements(
                lambda: _true_up(auth_client, account_id),
            )

            assert response.status_code == 200, response.status_code
            assert _reads_before_the_lock(statements) == []
            assert set(advisory_lock_keys(statements)) == {
                owner_lock_key(seed_user["user"].id),
            }
            assert _read_after_the_lock(statements, "account_anchor_history"), (
                "the door read the governing assertion before the lock, or "
                "not at all"
            )
            assert _read_after_the_lock(statements, "journal_entries"), (
                "the reconcile read the ledger before the lock, or not at all"
            )

    def test_the_signed_in_user_is_read_again_under_the_lock(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Property 2: the user row read before the lock is re-read after it.

        The password, MFA and settings doors write the signed-in user's row,
        so a copy read before the lock is stale by the time a write decides on
        it; taking the lock expires it, and the next look at the signed-in
        user reloads it -- before the save reads anything of the owner's.
        Graded by ORDER, because the save reads the user row again later for
        its own reasons, and a check that the row is read "at some point after
        the lock" passed with the expire deleted (measured 2026-09-29).  The
        behavioural half -- a change committed during the wait is SEEN -- is
        ``tests/test_routes/test_xbn_sign_in_lock.py``'s deactivated-companion
        arm.
        """
        assert seed_periods_today
        with app.app_context():
            account_id = _checking_account(seed_user).id

            response, statements = capture_sql_statements(
                lambda: _true_up(auth_client, account_id),
            )

            assert response.status_code == 200, response.status_code
            lock_at = _lock_index(statements)
            assert lock_at is not None
            after = [sql for sql, _params in statements[lock_at + 1:]]
            reread_at = next(
                (i for i, sql in enumerate(after) if "FROM auth.users" in sql),
                None,
            )
            first_budget_read_at = next(
                (i for i, sql in enumerate(after) if "budget." in sql), None,
            )
            assert None not in (reread_at, first_budget_read_at)
            assert reread_at < first_budget_read_at, (
                "the signed-in user's row was not read again under the lock "
                "before the save read the owner's data"
            )

    def test_a_loan_true_up_reads_the_governing_event_after_the_lock(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The loan true-up's compare-then-append, and its reconcile.

        The loan reconcile never had even the accidental serialisation the
        cash side lost at ruling R-EN: a loan true-up appends an event and
        UPDATEs no row, so nothing serialised it between Commit 16 and plan
        step X-f1c3c.
        """
        assert seed_periods_today
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Lock Probe Loan",
                principal=Decimal("15000.00"), rate=Decimal("0.05000"),
            )
            db.session.commit()
            loan_id = loan.id

            response, statements = capture_sql_statements(
                lambda: auth_client.post(
                    f"/accounts/{loan_id}/loan/trueup",
                    data={
                        "anchor_balance": "12345.67",
                        "anchor_date": display_today().isoformat(),
                    },
                ),
            )

            assert response.status_code == 302, response.status_code
            assert _reads_before_the_lock(statements) == []
            assert set(advisory_lock_keys(statements)) == {
                owner_lock_key(seed_user["user"].id),
            }
            assert _read_after_the_lock(statements, "loan_anchor_events")
            assert _read_after_the_lock(statements, "journal_entries")

    def test_an_opening_restatement_reads_the_opening_and_the_movements_after_the_lock(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The opening door's two reads, each graded, and the key it takes.

        The books-boundary triggers take no lock (``app.opening_infrastructure``),
        so what closes the two-transaction race is the request's lock.  The day
        bound reads ``budget.transaction_entries``, which a concurrent settle
        WRITES -- a read ahead of the lock lets the restatement pass its own
        predicate and then abort at COMMIT on the deferred trigger (found by
        adversarial review 2026-08-31, when the lock sat below that read).
        Two reads, two statements: a lock between them satisfies one and not
        the other, so both are named.
        """
        assert seed_periods_today
        with app.app_context():
            account_id = _checking_account(seed_user).id
            standing = cash_ledger.account_opening_fact(account_id)
            db.session.commit()

            response, statements = capture_sql_statements(
                lambda: auth_client.post(
                    f"/accounts/{account_id}/opening",
                    data={
                        "opened_on": (
                            standing.opened_on - timedelta(days=1)
                        ).isoformat(),
                        "opening_equity": "1234.00",
                    },
                ),
            )

            assert response.status_code == 302, response.status_code
            assert _reads_before_the_lock(statements) == []
            assert set(advisory_lock_keys(statements)) == {
                owner_lock_key(seed_user["user"].id),
            }
            assert _read_after_the_lock(statements, "account_openings")
            assert _read_after_the_lock(statements, "transaction_entries")

    def test_an_account_edit_locks_before_it_touches_the_accounts_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A type-only edit, the branch that changes NO anchor, locks before its UPDATE.

        **A DEADLOCK control rather than a race control.**  Before plan step
        X-f1e2 the anchor branch reached the lock before the ``setattr`` loop's
        ``UPDATE budget.accounts`` flushed, while a type-only edit flushed that
        UPDATE first -- two tabs, one of each, and PostgreSQL aborted one with
        an unhandled 500 on a money route, reproduced against a real database.
        """
        assert seed_periods_today
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            savings_type_id = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one().id
            form = {
                "name": account.name,
                "account_type_id": str(savings_type_id),
                "anchor_balance": str(cash_ledger.resolve_anchor(account).balance),
                "version_id": str(account.version_id),
                "is_active": "true",
            }
            account_id = account.id

        _response, statements = capture_sql_statements(
            lambda: auth_client.post(f"/accounts/{account_id}", data=form),
        )

        assert any(
            sql.strip().upper().startswith("UPDATE BUDGET.ACCOUNTS")
            for sql, _params in statements
        ), "the edit wrote no accounts row -- the test graded nothing"
        assert _reads_before_the_lock(statements) == []
        assert set(advisory_lock_keys(statements)) == {
            owner_lock_key(seed_user["user"].id),
        }

    def test_an_account_creation_locks_before_it_writes_the_origination(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The new account's opening and origination assertion, both after the lock.

        The control for plan step X-f1e2 / ruling R-ES: the app's first
        assertion about an account was once written with no lock at all, and
        the books opening (plan step X-f3c-2b-2a) is the same claim one table
        over.  The INSERTs are named, not the tables, because the stager reads
        the table before it appends: a build that read and appended nothing
        would satisfy a bare "touched the table" check.
        """
        assert seed_periods_today
        owner_id = seed_user["user"].id
        with app.app_context():
            checking_type_id = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one().id

        response, statements = capture_sql_statements(
            lambda: auth_client.post(
                "/accounts",
                data={
                    "name": "R-ES Origination Probe",
                    "account_type_id": str(checking_type_id),
                    "anchor_balance": "2500.00",
                },
            ),
        )

        assert response.status_code == 302, response.status_code
        assert _reads_before_the_lock(statements) == []
        assert set(advisory_lock_keys(statements)) == {owner_lock_key(owner_id)}
        lock_at = _lock_index(statements)
        for table in ("ACCOUNT_OPENINGS", "ACCOUNT_ANCHOR_HISTORY"):
            inserts = [
                i for i, (sql, _params) in enumerate(statements)
                if sql.strip().upper().startswith(f"INSERT INTO BUDGET.{table}")
            ]
            assert inserts and inserts[0] > lock_at, table
        # And the row the whole path exists to produce: ordering is not
        # correctness on its own.
        with app.app_context():
            account = db.session.query(Account).filter_by(
                user_id=owner_id, name="R-ES Origination Probe",
            ).one()
            written = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).all()
            assert [(row.anchor_balance, row.observed_on) for row in written] == [
                (Decimal("2500.00"), display_today()),
            ]


class TestARenderTakesNoLockAndItsWriteBlockDoes:
    """Property 3: the query arm, the ``write_transaction`` arm, and OPTIONS."""

    def test_a_page_load_that_writes_nothing_takes_no_lock(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """``GET /accounts/<id>/edit`` reads the owner's account and takes nothing.

        Its transaction is ``READ ONLY`` and the listener returns before the
        command arm; a lock here would make every page load wait behind every
        save.  The render is asserted to read the owner's data, so "no lock"
        cannot be a request that never reached the database.
        """
        assert seed_periods_today
        account_id = seed_user["account"].id
        response, statements = capture_sql_statements(
            lambda: auth_client.get(f"/accounts/{account_id}/edit"),
        )

        assert response.status_code == 200
        assert any("budget.accounts" in sql for sql, _params in statements)
        assert not took_advisory_lock(statements), advisory_lock_keys(statements)

    def test_a_render_s_write_block_takes_the_owner_s_key_once(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """``GET /grid``'s rolling top-up block is a command, and takes the key.

        Ruling R-CC114: a page load's write takes the owner's lock like any
        save.  Exactly ONE acquisition: the block's transaction takes it, and
        the render's read pass after the block is a query again and takes
        none.  It takes it whether or not the top-up then finds anything to
        do, because the block's first statement is what begins the command
        transaction.
        """
        assert seed_periods_today
        response, statements = capture_sql_statements(
            lambda: auth_client.get("/grid"),
        )

        assert response.status_code == 200
        assert advisory_lock_keys(statements) == [
            owner_lock_key(seed_user["user"].id),
        ]
        read_only = [
            i for i, (sql, _params) in enumerate(statements)
            if "READ ONLY" in sql
        ]
        lock_at = _lock_index(statements)
        assert read_only and read_only[0] < lock_at < read_only[-1], (
            "the lock was not taken between the render's snapshots, i.e. "
            "inside the write block"
        )

    def test_an_authenticated_options_takes_the_owner_s_key(
        self, auth_client, seed_user,
    ):
        """``OPTIONS`` is not a query method, so a signed-in one is a command.

        ``app.db_transaction``'s ``_QUERY_METHODS`` comment says so; graded
        here so the comment cannot drift from the code.
        """
        response, statements = capture_sql_statements(
            lambda: auth_client.options("/accounts"),
        )

        assert response.status_code == 200
        assert advisory_lock_keys(statements) == [
            owner_lock_key(seed_user["user"].id),
        ]


class TestTheLockActuallySerialises:
    """Property 4: PostgreSQL's own behaviour, not just the SQL we emit."""

    def test_a_held_key_blocks_a_save(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A second connection holding the owner's key makes a true-up wait.

        The request's acquisition inherits a short ``lock_timeout``, so
        PostgreSQL cancels the blocked statement -- the proof that the save
        waits for the holder rather than proceeding beside it -- and the
        statement that timed out is checked to be the owner's lock, not some
        other.
        """
        assert seed_periods_today
        owner_id = seed_user["user"].id
        with app.app_context():
            account = _checking_account(seed_user)

            with HeldOwnerKey(owner_id):
                bound_lock_waits()
                with pytest.raises(OperationalError) as excinfo:
                    _true_up(auth_client, account.id)
            assert_timed_out_on_owner_key(excinfo.value, owner_id)

    def test_the_same_save_completes_when_the_key_is_free(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The control: the same save under the same bound, nothing held.

        So the timeout above is the held key and not the statement being
        slow.
        """
        assert seed_periods_today
        with app.app_context():
            account = _checking_account(seed_user)
            bound_lock_waits()

            response = _true_up(auth_client, account.id)

            assert response.status_code == 200, response.status_code


class TestTheKeyIsTheOwner:
    """Property 5: one key per owner, whoever is acting."""

    def test_a_companion_s_save_takes_its_owner_s_key(
        self, companion_client, seed_user, seed_companion, seed_periods_today,
    ):
        """A companion writes its linked owner's data, so it takes the owner's key.

        Keyed on the ACTOR instead, a companion's save and the owner's would
        take two keys and serialise against nothing.  The companion's id is
        asserted to differ from the owner's, or the case could not tell them
        apart.
        """
        owner_id = seed_user["user"].id
        assert seed_companion["user"].id != owner_id
        template = make_expense_template(
            db.session, seed_user, amount="120.00", name="Hotel",
            category_key="Rent", companion_visible=True,
        )
        row = generate_row_of(template, seed_periods_today[4])
        db.session.commit()

        response, statements = capture_sql_statements(
            lambda: companion_client.post(f"/transactions/{row.id}/mark-done"),
        )

        assert response.status_code == 200, response.status_code
        assert set(advisory_lock_keys(statements)) == {owner_lock_key(owner_id)}

    def test_lock_every_user_acquires_ascending(
        self, app, seed_user, seed_second_user,
    ):
        """The deploy reconciles' every-owner form acquires ascending by user id.

        They are the only transactions that reconcile more than one owner, so
        the only ones that hold more than one key; taking them in one global
        order is what keeps two concurrent sweeps from taking the same two keys
        in opposite orders.  TWO users, or the order cannot be graded: with
        one, a reversed loop passed (neutral review of cp4, 2026-09-29).
        """
        with app.app_context():
            locked, statements = capture_sql_statements(lock_every_user_writes)
            # The ACQUISITION order, read off the emitted binds -- not the
            # returned list, which is sorted by its own ``ORDER BY`` and would
            # stay sorted even if the loop acquired in reverse.
            acquired = [key for _ns, key in advisory_lock_keys(statements)]
            assert acquired, "no lock was taken at all"
            assert acquired == sorted(acquired), (
                f"locks were ACQUIRED out of order: {acquired}"
            )
            assert acquired == locked, (
                f"the returned ids {locked} disagree with what was locked "
                f"{acquired}"
            )
            assert {
                seed_user["user"].id, seed_second_user["user"].id,
            } <= set(acquired) and len(acquired) >= 2, acquired
            db.session.rollback()

    def test_the_lock_is_re_entrant_within_one_transaction(self, app, seed_user):
        """Taking the same key twice in one transaction is harmless.

        ``scripts/init_database.py`` relies on it: it runs the three deploy
        reconciles in ONE transaction, and each takes every owner's key again.
        A non-re-entrant lock would self-deadlock on the second acquisition,
        which the short bound turns into a failure here.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.execute(text("SELECT 1"))
            bound_lock_waits()
            connection = db.session.connection()
            _result, statements = capture_sql_statements(
                lambda: [
                    take_owner_write_lock(connection, user_id),
                    take_owner_write_lock(connection, user_id),
                ],
            )
            assert advisory_lock_keys(statements) == [
                owner_lock_key(user_id), owner_lock_key(user_id),
            ]
            db.session.rollback()
