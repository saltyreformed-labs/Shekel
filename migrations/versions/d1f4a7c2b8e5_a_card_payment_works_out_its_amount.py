"""a card payment works out its amount

Revision ID: d1f4a7c2b8e5
Revises: 5f2c46a35a79
Create Date: 2026-11-03

Plan step **credit_card:CC-6**, its first task (plan#2672), of
``docs/design/credit_card_from_scratch.md`` 3.5 (rulings plan#2666,
plan#2667, plan#2671)::

    ref.card_payment_modes                 NEW, seeded (three rows)
    budget.transfer_templates              + uq_transfer_templates_id_to_account
    budget.card_payment_settings           NEW, EMPTY, audited
    budget.check_card_payment_card_revolves(integer)
    budget.card_payment_card_revolves()    NEW, three attachments

**The settings say HOW MUCH, as one of three amounts the card works out**:
what it owed at the end of the day before the payment, the statement
balance, or the statement's minimum.  No ``fixed`` member (plan#2666: a fixed
payment is a plain recurring transfer) and no stored figure (plan#2667, which
X-bp's ``4d4eb9cd3850`` made possible by dropping ``default_amount``).  WHEN a
payment falls is its transfer's repeat (plan#2671), so no column names a day.

**The card is named once.**  ``card_account_id`` is held equal to the
definition's ``to_account_id`` by ``fk_card_payment_settings_payment``, a
composite key onto the new ``uq_transfer_templates_id_to_account`` superkey,
and is unique: one worked-out payment per card.  The superkey is unique by
construction (``id`` is the key), so it cannot fail on any database and
changes no row; plan#2311 plans the same key for the loan's settings.  The owner key
``fk_card_payment_settings_owner`` is ``pay_calendar:C13-c``'s shape, onto
``uq_accounts_id_user``.

**Only a credit card can be the card**: :mod:`app.card_payment_infrastructure`'s
trigger, attached to the settings table and ``budget.accounts`` (deferred)
and ``ref.account_types`` (at the end of its statement), the backstop behind the doors' refusals.

**No figure moves.**  The table is created EMPTY and nothing backfills it;
no door writes it until plan#2679, and nothing prices from it until plan#2677.

**The audit trigger** ``audit_card_payment_settings`` is attached here, the
``97f92340fffc`` shape; ``AUDITED_TABLES`` carries the matching row.
``ref.card_payment_modes`` is a read-only seed catalogue and is not audited,
on the criteria that keep every other ref catalogue out.

**Inline seed rationale.**  The three rows are seeded here so
``ref_cache.init()`` resolves ``CardPaymentModeEnum`` immediately after a bare
``flask db upgrade``; ``app/ref_seeds.py`` carries the identical rows for the
idempotent reseed (the dual-seed pattern
``tests/test_models/test_posting_ref_seed_parity.py`` guards).

**One import from ``app``**: :mod:`app.card_payment_infrastructure`, so the
trigger's SQL has ONE home shared with ``scripts/init_database.py`` and the
test template -- the construction ``af07125d00f1`` uses for
:mod:`app.sighting_infrastructure`.

**Reversible.**  The downgrade drops the trigger, the table, the superkey and
the catalogue, in that order; it loses only the modes owners chose.
"""
from alembic import op
import sqlalchemy as sa

from app.card_payment_infrastructure import (
    apply_card_payment_infrastructure,
    remove_card_payment_infrastructure,
)


# Revision identifiers, used by Alembic.
revision = 'd1f4a7c2b8e5'
# The head of ``dev`` when ``card/1033`` took it in, measured 2026-11-10 at
# ``fd3f17810`` (one head; ``4d4eb9cd3850`` at authoring, before nine
# revisions landed on ``dev``).  Another lane landing a revision first makes
# two heads, which git merges cleanly and ``flask db upgrade`` refuses:
# re-point this at the measured head as the last edit before the pull request.
down_revision = '5f2c46a35a79'
branch_labels = None
depends_on = None


#: The table gaining an audit trigger here (see the docstring).
_AUDITED_NEW_TABLE = "card_payment_settings"

#: The catalogue's rows, identical to ``app/ref_seeds.py``'s and to
#: :class:`app.enums.CardPaymentModeEnum`'s values.
_SEED_CARD_PAYMENT_MODES_SQL = (
    "INSERT INTO ref.card_payment_modes (name) VALUES "
    "('card_balance'), "
    "('statement_balance'), "
    "('minimum_payment') "
    "ON CONFLICT (name) DO NOTHING"
)


def _create_card_payment_modes() -> None:
    """Create and seed ``ref.card_payment_modes``.

    Its single-column primary key and ``UNIQUE (name)`` take PostgreSQL's
    generated names, the exemption every ``ref`` lookup table has (developer
    ruling 2026-08-14, ledger row ``recurrence:F-3``).
    """
    op.create_table(
        'card_payment_modes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        schema='ref',
    )
    op.execute(_SEED_CARD_PAYMENT_MODES_SQL)


def _create_card_payment_settings() -> None:
    """Create ``budget.card_payment_settings`` with its four keys."""
    op.create_table(
        'card_payment_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('transfer_template_id', sa.Integer(), nullable=False),
        sa.Column('card_account_id', sa.Integer(), nullable=False),
        sa.Column('mode_id', sa.Integer(), nullable=False),
        # The mixin-carried columns render after the model's own; order is
        # load-bearing nowhere (``app/models/mixins.py``).
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        # The mixin's single-column owner key, convention-named by
        # PostgreSQL as ``credit_card_params``' is.
        sa.ForeignKeyConstraint(
            ['user_id'], ['auth.users.id'], ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['mode_id'], ['ref.card_payment_modes.id'],
            name='fk_card_payment_settings_mode_id', ondelete='RESTRICT',
        ),
        # The definition and the card it pays, in one key.
        sa.ForeignKeyConstraint(
            ['transfer_template_id', 'card_account_id'],
            ['budget.transfer_templates.id',
             'budget.transfer_templates.to_account_id'],
            name='fk_card_payment_settings_payment', ondelete='CASCADE',
        ),
        # The row's owner is its card's.
        sa.ForeignKeyConstraint(
            ['card_account_id', 'user_id'],
            ['budget.accounts.id', 'budget.accounts.user_id'],
            name='fk_card_payment_settings_owner', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'transfer_template_id',
            name='uq_card_payment_settings_transfer_template_id',
        ),
        sa.UniqueConstraint(
            'card_account_id', name='uq_card_payment_settings_card_account_id',
        ),
        schema='budget',
    )
    # Trigger name ``audit_<table>`` matches the convention the entrypoint
    # trigger-count health check enumerates (``tgname LIKE 'audit_%'``).  The
    # shared ``system.audit_trigger_func`` already exists.
    op.execute(
        f"DROP TRIGGER IF EXISTS audit_{_AUDITED_NEW_TABLE} "
        f"ON budget.{_AUDITED_NEW_TABLE}"
    )
    op.execute(
        f"CREATE TRIGGER audit_{_AUDITED_NEW_TABLE} "
        f"AFTER INSERT OR UPDATE OR DELETE ON budget.{_AUDITED_NEW_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )


def upgrade():
    """Create the catalogue, the superkey, the settings table and its rule.

    Order is load-bearing: the settings table's keys need the catalogue and
    the superkey, and the trigger needs all three tables it attaches to.
    """
    _create_card_payment_modes()
    op.create_unique_constraint(
        'uq_transfer_templates_id_to_account', 'transfer_templates',
        ['id', 'to_account_id'], schema='budget',
    )
    _create_card_payment_settings()
    apply_card_payment_infrastructure(op.execute)


def downgrade():
    """Drop the rule, the table, the superkey and the catalogue, in that order.

    The table's audit trigger and its own constraints go with it; the
    superkey can go only once the key onto it has.
    """
    remove_card_payment_infrastructure(op.execute)
    op.drop_table('card_payment_settings', schema='budget')
    op.drop_constraint(
        'uq_transfer_templates_id_to_account', 'transfer_templates',
        schema='budget', type_='unique',
    )
    op.drop_table('card_payment_modes', schema='ref')
