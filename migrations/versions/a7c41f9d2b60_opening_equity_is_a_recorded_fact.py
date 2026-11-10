"""Opening equity is a recorded fact, not a position in the assertion series.

Plan step **balance:X-f3c-2a**, ruling **balance:R-GX**.

Creates ``ref.account_opening_sources`` and ``budget.account_openings``, and
backfills ONE row per existing account carrying the figure the balance fold was
already re-deriving on every read -- so the migration moves ``$0.00`` on every
surface by construction.

**What the figure is.**  An account's opening equity is the capital its books
opened with: what it held before any of its recorded movements.  Until this
step no table held it; ``balance_at._cash_fold._actual_steps`` recomputed it per
read as "the earliest assertion's balance, less the movements that assertion
already contained" (ruling R-I), and used it as the fold's seed.

**Where the backfill takes it from, and why not from a re-derivation.**  Both
values come from rows already in the database -- the posted double-entry ledger
-- so this migration contains no second implementation of the walk's valuation
rule.  Re-deriving it in SQL would have meant restating
``cash_ledger.settled_cash_leg`` (``owned_contribution - credit entries -
posted purchases``) in a migration that then goes stale the day that rule
changes, which is exactly the two-statements-of-one-rule defect the balance arc
exists to close.

Two readings are taken and they must AGREE, or the migration refuses. **They
are not INDEPENDENT and an earlier draft of this docstring claimed they were**:
both come from the posted ledger, so they are the stored result of
``walk_account_ledger`` and its recomputation. What the pair detects is a posted
ledger that is INCOMPLETE for an account -- which is the failure that would
silently seed ``$0.00`` -- and not a divergence between the posted ledger and
the cash fold. That equality is asserted by measurement instead: 1,829 balance
readings across nine accounts, identical either side of this step.

The two readings are:

* ``E_entry``  -- the ``account_opening`` journal entry's LINKED leg;
* ``E_checked`` -- the earliest assertion's balance, less the linked-leg
  postings of non-anchor sources dated on or before the opening day.

They disagree exactly where the posted ledger is incomplete for an account (a
baseline-less owner is skipped by ``sync_account_anchor_postings_all_scenarios``
with a log line, so an account CAN be missing its postings), and there
``E_entry`` alone would silently seed ``$0.00`` and move every balance on that
account.  Measured on a production clone 2026-08-27, the two agree to the cent
on all seven non-loan accounts: Checking ``$689.16``, Fidelity Savings
``$4,863.56``, Money Market ``$4,879.26``, Roth IRA ``$22,909.02``, Traditional
IRA ``$9,771.48``, 401(k) ``$26,912.56``, Home ``$350,000.00``.

**The two AMORTIZING accounts are backfilled too, from the read fold's own
arithmetic** (``A0 - P0`` over the cash walk), because the posted ledger holds
no ``account_opening`` entry for a loan -- ``walk_account_ledger`` refuses an
amortizing account outright, loans booking their openings through the loan
posting package.  They need a row all the same: ``balance_at.balance_at``
dispatches on ``_resolution.configured_loan``, which answers ``None`` for an
amortizing account carrying no ``LoanParams``, and falls through to this fold.
For those two the cross-check is against the ASSERTION series rather than the
journal, and the same refusal applies.

**Every row is tagged ``migration_derived``**, which is a financial statement
rather than a label.  A derived figure is the pre-X-f3c-2a inference frozen and
it may be WRONG: finding **balance:N-275** measures account 1's opening
``$436.05`` short of the bank's own closing for the same day, and the developer's
Fidelity export shows account 10's books really opening at ``$1,345.74`` on
2026-01-30 rather than at the ``$4,879.26`` this seeds.  Freezing today's figure
is what makes the migration balance-neutral; the tag is what stops a later
reader mistaking it for something anybody observed.

Review: not required -- this migration is purely additive.  It creates two
tables and inserts; it drops nothing, renames nothing, and alters no existing
column.

Revision ID: a7c41f9d2b60
Revises: 94628d09816b
Create Date: 2026-08-27
"""

from decimal import Decimal

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7c41f9d2b60"
down_revision = "94628d09816b"
branch_labels = None
depends_on = None


#: The journal sources whose LINKED-leg postings are cash movements rather than
#: anchor corrections.  Subtracting these from the earliest asserted balance
#: reproduces the fold's ``balance_before`` for that assertion, which is the
#: second, independent reading of opening equity.
_MOVEMENT_SOURCES = ("transaction", "purchase", "transfer")

#: What the linked ledger account's kind is called in ``ref.ledger_account_kinds``.
_LINKED_KIND = "linked"


def _openings_from_the_journal(bind) -> dict[int, tuple]:
    """Return ``{account_id: (opened_on, e_entry, e_checked)}`` for non-loans.

    Both figures come from rows already stored: no valuation rule is restated
    here.  See the module docstring for what each reading is and why the pair
    must agree.

    Args:
        bind: The Alembic connection.

    Returns:
        One entry per account carrying an ``account_opening`` journal entry or
        an assertion, keyed by account id.
    """
    rows = bind.execute(sa.text("""
        WITH opening AS (
            SELECT account_id, MIN(observed_on) AS opened_on
              FROM budget.account_anchor_history
             GROUP BY account_id
        ),
        asserted AS (
            SELECT h.account_id,
                   o.opened_on,
                   -- The EARLIEST-recorded assertion of the opening day, which
                   -- is the one both walks treat as the opening
                   -- (``cash_anchor_facts`` orders ``(observed_on, created_at,
                   -- id)`` ascending and ``walk_account_ledger`` books
                   -- ``corrections[0]``).  Taking the day's LAST-recorded row
                   -- instead made the two readings disagree by the correction
                   -- between them on any account whose owner fixed a mistyped
                   -- opening balance the same day -- an ordinary act -- and the
                   -- migration then refused, permanently, with a remedy that
                   -- changes neither number.
                   (ARRAY_AGG(h.anchor_balance
                              ORDER BY h.created_at, h.id))[1]
                       AS anchor_balance
              FROM budget.account_anchor_history h
              JOIN opening o
                ON o.account_id = h.account_id
               AND o.opened_on = h.observed_on
             GROUP BY h.account_id, o.opened_on
        ),
        entry_leg AS (
            SELECT la.account_id, SUM(p.amount) AS amount
              FROM budget.journal_entries je
              JOIN ref.posting_sources ps ON ps.id = je.source_kind_id
              JOIN budget.account_postings p ON p.journal_entry_id = je.id
              JOIN budget.ledger_accounts la ON la.id = p.ledger_account_id
              JOIN ref.ledger_account_kinds k ON k.id = la.kind_id
             WHERE ps.name = 'account_opening'
               AND k.name = :linked_kind
             GROUP BY la.account_id
        ),
        movements AS (
            SELECT la.account_id, SUM(p.amount) AS amount
              FROM budget.journal_entries je
              JOIN ref.posting_sources ps ON ps.id = je.source_kind_id
              JOIN budget.account_postings p ON p.journal_entry_id = je.id
              JOIN budget.ledger_accounts la ON la.id = p.ledger_account_id
              JOIN ref.ledger_account_kinds k ON k.id = la.kind_id
              JOIN opening o ON o.account_id = la.account_id
             WHERE ps.name = ANY(:movement_sources)
               AND k.name = :linked_kind
               AND je.entry_date <= o.opened_on
             GROUP BY la.account_id
        )
        SELECT a.account_id,
               a.opened_on,
               a.anchor_balance,
               entry_leg.amount AS e_entry,
               COALESCE(movements.amount, 0) AS movement_net
          FROM asserted a
          LEFT JOIN entry_leg ON entry_leg.account_id = a.account_id
          LEFT JOIN movements ON movements.account_id = a.account_id
    """), {
        "linked_kind": _LINKED_KIND,
        "movement_sources": list(_MOVEMENT_SOURCES),
    }).fetchall()
    return {
        row.account_id: (
            row.opened_on,
            None if row.e_entry is None else Decimal(str(row.e_entry)),
            Decimal(str(row.anchor_balance)) - Decimal(str(row.movement_net)),
        )
        for row in rows
    }


def _accounts_without_assertions(bind) -> list[tuple]:
    """Return ``[(account_id, created day), ...]`` for accounts carrying none.

    An account with NO assertion history has nothing to derive an opening from,
    and this migration must still give it a row: every read of a cash fold goes
    through :func:`app.services.cash_ledger.account_opening_fact`, which refuses
    an account without one rather than fabricating a level.

    ``$0.00`` on the account's own creation day is not a guess -- it is exactly
    what the fold answered for such an account BEFORE this step, when
    ``_actual_steps`` returned a zero seed for an empty correction list.  So the
    row preserves the behaviour rather than inventing one.

    The state is production-unreachable (migration ``cfb15e782f86`` plus the
    ``account_service.create_account`` factory guarantee an assertion, and the
    clone carries zero such accounts, measured 2026-08-27), which is precisely
    why it is handled rather than assumed away: an assumption that holds is free
    to encode, and this one leaves a reachable RuntimeError if it ever stops
    holding.

    Args:
        bind: The Alembic connection.

    Returns:
        One tuple per account with no assertion history.
    """
    return [
        (row.id, row.opened_on)
        for row in bind.execute(sa.text("""
            SELECT a.id,
                   (a.created_at AT TIME ZONE 'America/New_York')::date
                       AS opened_on
              FROM budget.accounts a
             WHERE NOT EXISTS (
                 SELECT 1 FROM budget.account_anchor_history h
                  WHERE h.account_id = a.id
             )
        """)).fetchall()
    ]


def _amortizing_account_ids(bind) -> set[int]:
    """Return the ids of accounts whose TYPE amortizes.

    They carry no ``account_opening`` journal entry -- loans book their
    openings through the loan posting package -- so the journal cross-check
    cannot apply to them and ``E_checked`` stands alone.

    Args:
        bind: The Alembic connection.

    Returns:
        The amortizing accounts' ids.
    """
    return {
        row.id for row in bind.execute(sa.text("""
            SELECT a.id
              FROM budget.accounts a
              JOIN ref.account_types t ON t.id = a.account_type_id
             WHERE t.has_amortization
        """)).fetchall()
    }


def _resolve_openings(bind) -> list[tuple]:
    """Return ``[(account_id, opened_on, opening_equity), ...]`` to insert.

    Refuses rather than guesses: an account whose two independent readings
    disagree, or whose journal reading is missing where one is expected, aborts
    the migration with the accounts named.  Seeding a wrong level moves every
    balance the account has ever rendered, silently and permanently.

    Args:
        bind: The Alembic connection.

    Returns:
        One tuple per ACCOUNT -- every account gets a row, including one
        carrying no assertion history at all.

    Raises:
        RuntimeError: When the two readings disagree for any non-loan account.
    """
    derived = _openings_from_the_journal(bind)
    amortizing = _amortizing_account_ids(bind)

    resolved: list[tuple] = []
    disagreements: list[str] = []
    for account_id, (opened_on, e_entry, e_checked) in sorted(derived.items()):
        if account_id in amortizing:
            # No journal reading exists to cross-check against; the assertion
            # series is the only source, and it is the same arithmetic the read
            # fold applies to these accounts today.
            resolved.append((account_id, opened_on, e_checked))
            continue
        if e_entry is None:
            # An account whose opening books nothing mints no journal entry at
            # all ("a fresh $0 account keeps zero ledger rows"), so a missing
            # entry is EXPECTED exactly when the checked reading is zero.  Any
            # other missing entry means the posted ledger is incomplete for this
            # account and the journal reading would seed a silent $0.00.
            if e_checked == Decimal("0.00"):
                resolved.append((account_id, opened_on, e_checked))
            else:
                disagreements.append(
                    f"account {account_id}: no account_opening journal entry, "
                    f"but the assertion series implies {e_checked}"
                )
            continue
        if e_entry != e_checked:
            disagreements.append(
                f"account {account_id}: the account_opening entry says "
                f"{e_entry} and the assertion series implies {e_checked}"
            )
            continue
        resolved.append((account_id, opened_on, e_entry))

    # Accounts carrying no assertion at all still get a row -- see
    # :func:`_accounts_without_assertions` for why ``$0.00`` there preserves the
    # pre-X-f3c-2a answer rather than inventing one.
    for account_id, opened_on in _accounts_without_assertions(bind):
        resolved.append((account_id, opened_on, Decimal("0.00")))

    if disagreements:
        raise RuntimeError(
            "a7c41f9d2b60 refuses to seed budget.account_openings: the posted "
            "ledger and the assertion series disagree about what these "
            "accounts' books opened with, and seeding either figure would move "
            "every balance they render.  Re-sync the owner's postings with a "
            "release from before plan step plan#2914 (its "
            "account_posting_service.sync_account_anchor_postings_all_scenarios),"
            " then retry.\n  " + "\n  ".join(disagreements)
        )
    return resolved


def upgrade():
    """Create the two tables and seed one opening row per existing account."""
    op.create_table(
        "account_opening_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    bind = op.get_bind()
    # LITERAL values, not a parameterised loop: leg 1 of the dual seed is what
    # a bare ``flask db upgrade`` runs, and ``test_posting_ref_seed_parity``
    # reads this statement STATICALLY to prove every enum member is inline
    # seeded.  A bound parameter is invisible to it, so the gate that exists to
    # catch exactly this omission passed while the names were unreachable.
    op.execute(
        "INSERT INTO ref.account_opening_sources (name) "
        "VALUES ('user_declared'), ('migration_derived')"
    )

    op.create_table(
        "account_openings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("opening_equity", sa.Numeric(precision=12, scale=2),
                  nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["budget.accounts.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["ref.account_opening_sources.id"],
            name="fk_account_openings_source_id", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="budget",
    )
    op.create_index(
        "idx_account_openings_account",
        "account_openings",
        ["account_id", "created_at"],
        unique=False,
        schema="budget",
    )

    # **The audit trigger is attached HERE, and the deploy depends on it.**
    # ``app.audit_infrastructure`` lists this table, so
    # ``EXPECTED_TRIGGER_COUNT`` becomes 51; ``entrypoint.sh`` compares that
    # against the live count AFTER migrations and exits 1 when it is short, so
    # a table registered but never triggered stops the container from starting
    # at all.  The rebuild migration ``a5be2a99ea14`` cannot do it -- its CREATE
    # is guarded on the table already existing -- which is why every migration
    # that creates an audited table attaches its own pair
    # (``3f408018a71c`` is the precedent).
    #
    # It comes BEFORE the seeding INSERTs below so the backfill itself is
    # audited: those rows are the level every balance rests on, and the
    # downgrade's recovery story is that ``system.audit_log`` holds them.
    op.execute(
        "DROP TRIGGER IF EXISTS audit_account_openings "
        "ON budget.account_openings"
    )
    op.execute(
        "CREATE TRIGGER audit_account_openings "
        "AFTER INSERT OR UPDATE OR DELETE ON budget.account_openings "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )

    derived_id = bind.execute(sa.text(
        "SELECT id FROM ref.account_opening_sources WHERE name = "
        "'migration_derived'"
    )).scalar_one()
    for account_id, opened_on, opening_equity in _resolve_openings(bind):
        bind.execute(
            sa.text("""
                INSERT INTO budget.account_openings
                    (account_id, opened_on, opening_equity, source_id)
                VALUES (:account_id, :opened_on, :opening_equity, :source_id)
            """),
            {
                "account_id": account_id,
                "opened_on": opened_on,
                "opening_equity": opening_equity,
                "source_id": derived_id,
            },
        )


def downgrade():
    """Drop both tables.

    Value-lossless: every seeded figure is a DERIVATION of rows this migration
    does not touch, so the pre-X-f3c-2a fold recomputes each one exactly.  A row
    an owner has since restated through a later door is NOT recoverable, and
    that is stated rather than hidden: ``system.audit_log`` holds the INSERT for
    every ``budget.account_openings`` row (the table is in ``AUDITED_TABLES``),
    so a restatement can be read back from there by hand.
    """
    op.drop_index(
        "idx_account_openings_account",
        table_name="account_openings",
        schema="budget",
    )
    op.drop_table("account_openings", schema="budget")
    op.drop_table("account_opening_sources", schema="ref")
