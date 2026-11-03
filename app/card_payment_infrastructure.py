"""Shared definitions for the rule that a card payment's card is a credit card.

**Only a revolving account can be a worked-out payment's card** (plan step
``credit_card:CC-6``, design 3.5; plan#1033's design-loop comment of
2026-11-02).  ``budget.card_payment_settings`` names its card, and what makes
an account a card is its type's ``has_revolving_credit`` flag
(:func:`app.services.account_projection.is_revolving`).  The doors refuse the
cases they can reach, each with a sentence: an account re-typed away from a
card while a payment names it
(:func:`app.utils.account_validation._validate_account_type_change`).  This
module is the backstop for every writer no door stands in front of -- a psql
session, a bulk statement, a seed.

**The fact lives on three tables, so the rule takes three attachments**, the
lesson :mod:`app.deleted_row_infrastructure` records for two.  A settings row
names an account, the account names its type, and the type carries the flag;
any one can move without the others:

* a settings row is written, or re-pointed, onto a non-card
  (``ck_card_payment_card_revolves_on_settings``);
* the card is re-typed (``ck_card_payment_card_revolves_on_account``);
* a type's flag is turned off (``ck_card_payment_card_revolves_on_type``).
  Only the seed sets the flag (``app/ref_seeds.py``), so this arm guards a
  changed seed or a hand edit.

The type is READ through the account, never copied onto the settings row: a
composite key onto ``(account, type)`` would make this structural, at the cost
of a second home for each card's type (CLAUDE.md rule 14), which the trigger
does not need.

**The first two arms are deferred, and so graded on what the transaction
LEAVES.**  A writer may re-type a card and re-type it back, or delete the
settings row after re-typing, inside one transaction; each re-reads the state
at commit rather than judging the row it fired for.

**The type arm is an ordinary trigger, checked at the end of its statement,
and names no column.**  An ``UPDATE OF has_revolving_credit`` list or a
``WHEN`` naming the flag would make the trigger DEPEND on the column, and
PostgreSQL then refuses to drop or retype it: every later migration of that
column would have to drop this rule first, and the bootstrap tests that
simulate a table behind its model (``tests/test_utils/test_db_errors.py``)
were refused exactly so.  So it fires on every update of a type row and asks
the flag itself, which costs one comparison per row, and immediate rather
than deferred because the seed rewrites every built-in type on every boot: a
deferred event per row would sit queued until commit, and a queued event
makes DDL on the table illegal until then.  A flag is turned off only after
the payments naming its cards are gone, in an earlier statement.

**The settings arm LOCKS the card's account row**, ``FOR SHARE``, before it
reads the account's type, which serialises it with a concurrent re-type in
either order.  A re-type's ``UPDATE`` holds the account row until its
commit, so a settings commit arriving while it is open waits, then reads the
committed type and refuses; a settings commit that took the lock first makes
the re-type's ``UPDATE`` wait, and the re-type's own arm then sees the
committed settings row and refuses.  Without the lock each read the other's
uncommitted state as absent and both committed.  The lock is ``FOR SHARE``
because the arm runs at commit, after every write of its transaction, so
nothing later in it upgrades the lock.  **The TYPE row is not locked**: the
runtime role holds only ``SELECT`` on the ``ref`` schema
(``scripts/init_db_role.sql``), and a row lock needs ``UPDATE``.  So the type
arm is not serialised against the app's writes; its only writer is the seed,
which ``entrypoint.sh`` runs before the app serves a request.

**Three callers must produce identical infrastructure**, the contract
:mod:`app.append_only_infrastructure` states: the Alembic revision that
installs it (``d1f4a7c2b8e5``), ``scripts/init_database.py`` (fresh database,
no migration chain) and ``scripts/build_test_template.py`` (re-applied
idempotently so the latest in-code definition wins).
``scripts/_template_checks.py`` counts :data:`CARD_PAYMENT_TRIGGERS` on every
database built at head.

**Caller contract: the three tables must already exist.**
"""

from __future__ import annotations

from typing import Callable

#: The check, for one account: does a settings row name it while its type is
#: not revolving?
_CHECK_FUNCTION = "budget.check_card_payment_card_revolves"

#: The trigger function every attachment runs: which accounts to check.
_TRIGGER_FUNCTION = "budget.card_payment_card_revolves"

#: ``(trigger name, schema-qualified table)`` for each attachment.  Exported
#: so the template check can count them.
CARD_PAYMENT_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("ck_card_payment_card_revolves_on_settings", "budget.card_payment_settings"),
    ("ck_card_payment_card_revolves_on_account", "budget.accounts"),
    ("ck_card_payment_card_revolves_on_type", "ref.account_types"),
)

#: Each attachment's kind and firing clause, by trigger name: everything
#: ``CREATE`` says before the name, and between the name and ``EXECUTE
#: FUNCTION``.  The type arm names no column (the module docstring says why).
_FIRING = {
    "ck_card_payment_card_revolves_on_settings": (
        "CONSTRAINT TRIGGER",
        "AFTER INSERT OR UPDATE OF card_account_id ON {table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW",
    ),
    "ck_card_payment_card_revolves_on_account": (
        "CONSTRAINT TRIGGER",
        "AFTER UPDATE OF account_type_id ON {table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (OLD.account_type_id IS DISTINCT FROM NEW.account_type_id)",
    ),
    "ck_card_payment_card_revolves_on_type": (
        "TRIGGER",
        "AFTER UPDATE ON {table} FOR EACH ROW",
    ),
}

#: The check.  The account is locked and read first, its type's flag second,
#: each in a statement of its own: under ``READ COMMITTED`` each statement
#: reads what is committed when it starts, so the flag is read after any wait
#: for the lock.  An account deleted later in the transaction took its
#: settings row with it (``fk_card_payment_settings_owner``, CASCADE), so it
#: has nothing left to check.
_CREATE_CHECK_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_CHECK_FUNCTION}(p_account_id integer)
RETURNS void AS $$
DECLARE
    v_type_id integer;
    v_revolving boolean;
BEGIN
    SELECT a.account_type_id INTO v_type_id
    FROM budget.accounts a
    WHERE a.id = p_account_id
    FOR SHARE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    SELECT t.has_revolving_credit INTO v_revolving
    FROM ref.account_types t
    WHERE t.id = v_type_id;
    IF v_revolving THEN
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM budget.card_payment_settings s
        WHERE s.card_account_id = p_account_id
    ) THEN
        RAISE EXCEPTION
            'account % has a card payment''s settings but its type % is not '
            'a revolving credit type: only a credit card can be a card '
            'payment''s card (rule {_CHECK_FUNCTION})',
            p_account_id, v_type_id;
    END IF;
END;
$$ LANGUAGE plpgsql
"""

#: The trigger function: the accounts each attachment's row can affect.
_CREATE_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_TRIGGER_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    v_account_id integer;
BEGIN
    IF TG_TABLE_NAME = 'account_types' THEN
        IF NOT (OLD.has_revolving_credit AND NOT NEW.has_revolving_credit) THEN
            RETURN NULL;
        END IF;
        FOR v_account_id IN
            SELECT s.card_account_id
            FROM budget.card_payment_settings s
            JOIN budget.accounts a ON a.id = s.card_account_id
            WHERE a.account_type_id = NEW.id
        LOOP
            PERFORM {_CHECK_FUNCTION}(v_account_id);
        END LOOP;
    ELSIF TG_TABLE_NAME = 'accounts' THEN
        PERFORM {_CHECK_FUNCTION}(NEW.id);
    ELSE
        PERFORM {_CHECK_FUNCTION}(NEW.card_account_id);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def apply_card_payment_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently install the rule's two functions and three attachments.

    The functions are created or replaced, then every attachment is dropped
    and made again -- PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS`` -- so
    a second run leaves exactly what the first did.

    **Nothing to legalise first**: the revision that installs this creates
    ``budget.card_payment_settings`` empty in the same transaction.

    Args:
        executor: Single-argument callable that runs a SQL string --
            ``op.execute`` inside a migration, ``lambda s:
            session.execute(text(s))`` elsewhere.  Errors propagate; the
            caller owns the transaction.
    """
    executor(_CREATE_CHECK_FUNCTION_SQL)
    executor(_CREATE_TRIGGER_FUNCTION_SQL)
    for name, table in CARD_PAYMENT_TRIGGERS:
        kind, firing = _FIRING[name]
        executor(f"DROP TRIGGER IF EXISTS {name} ON {table}")
        executor(
            f"CREATE {kind} {name} {firing.format(table=table)} "
            f"EXECUTE FUNCTION {_TRIGGER_FUNCTION}()"
        )


def remove_card_payment_infrastructure(executor: Callable[[str], object]) -> None:
    """Drop every attachment, then both functions -- the downgrade's half.

    Every statement uses ``IF EXISTS``, so this is idempotent and a clean
    no-op on a database that never carried the infrastructure.

    Args:
        executor: As :func:`apply_card_payment_infrastructure`.
    """
    for name, table in CARD_PAYMENT_TRIGGERS:
        executor(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    executor(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION}()")
    executor(f"DROP FUNCTION IF EXISTS {_CHECK_FUNCTION}(integer)")
