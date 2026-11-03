"""
Shekel Budget App -- ``budget.card_payment_settings`` (plan step credit_card:CC-6, plan#2672)

The settings of a card's worked-out payment: which of three amounts the card
works out (rulings plan#2666, plan#2667), held to one definition and one card.
These tests pin the schema half:

* the catalogue holds exactly the enum's three members, each resolved by the
  cache (no ``fixed``: plan#2666);
* assigning the definition states the card (the relationship joins on both
  columns of the payment key, so ``card_account_id`` is copied, never typed);
* the card is named ONCE: a row naming a card its definition does not pay is
  unstorable, and the definition's destination cannot move under it;
* one row per definition and one per card; the owner is the card's by key;
* the row dies with its definition, and a mode in use cannot be deleted;
* the table is audited, and the migration round-trips on this test's clone.

The rule that the card is a credit card is
:mod:`.test_the_card_is_a_card`'s.
"""

import pytest
import sqlalchemy
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.audit_infrastructure import AUDITED_TABLES
from app.enums import CardPaymentModeEnum
from app.extensions import db
from app.models.card_payment_settings import CardPaymentSettings
from app.models.ref import CardPaymentMode
from app.models.transfer_template import TransferTemplate
from tests._test_helpers import load_migration_module, run_migration_callable

from ._helpers import account_of, payment_into

_MIGRATION = load_migration_module(
    "d1f4a7c2b8e5_a_card_payment_works_out_its_amount.py",
)


def _card(seed_user, name="Visa"):
    """A Credit Card account of *seed_user*'s."""
    return account_of(seed_user, "Credit Card", name)


def _mode(member=CardPaymentModeEnum.STATEMENT_BALANCE):
    """The ``ref.card_payment_modes`` id of *member*."""
    return ref_cache.card_payment_mode_id(member)


def _settings(template, **overrides):
    """An UNFLUSHED settings row for *template*, through the relationship."""
    row = CardPaymentSettings(
        template=template, user_id=template.user_id, mode_id=_mode(),
    )
    for column, value in overrides.items():
        setattr(row, column, value)
    return row


def _raw_insert(template_id, card_id, user_id):
    """Insert a settings row by SQL, so no ORM sync states the card for it."""
    db.session.execute(sqlalchemy.text(
        "INSERT INTO budget.card_payment_settings "
        "(transfer_template_id, card_account_id, user_id, mode_id) "
        "VALUES (:t, :c, :u, :m)"
    ), {"t": template_id, "c": card_id, "u": user_id, "m": _mode()})


class TestTheCatalogue:
    """Three amounts, each a row the cache resolves, and no fourth."""

    def test_the_rows_are_the_enums_members_exactly(self, app):
        """``ref.card_payment_modes`` holds one row per member, and nothing else.

        The seed-parity test grades the seed LISTS against the enum; this
        grades what the template database holds, and that ``fixed`` (ruling
        plan#2666) is in neither.
        """
        with app.app_context():
            names = {row.name for row in db.session.query(CardPaymentMode)}
            assert names == {member.value for member in CardPaymentModeEnum}
            assert names == {"card_balance", "statement_balance", "minimum_payment"}
            ids = {_mode(member) for member in CardPaymentModeEnum}
            assert len(ids) == 3


class TestTheCardIsNamedOnce:
    """``fk_card_payment_settings_payment``: the card is the definition's destination."""

    def test_assigning_the_definition_states_the_card(self, app, seed_user):
        """The control: a row given only its definition stores that card.

        ``card_account_id`` is never set here; SQLAlchemy copies it from the
        definition's ``to_account_id`` because the relationship joins on both
        columns of the key.  A row reached back through the definition is the
        same row.
        """
        with app.app_context():
            card = _card(seed_user)
            template = payment_into(seed_user, card)
            db.session.add(_settings(template))
            db.session.commit()

            stored = db.session.query(CardPaymentSettings).one()
            assert stored.card_account_id == card.id
            assert stored.transfer_template_id == template.id
            assert stored.user_id == seed_user["user"].id
            assert stored.mode_id == _mode(CardPaymentModeEnum.STATEMENT_BALANCE)
            db.session.expire_all()
            assert db.session.get(
                TransferTemplate, template.id,
            ).card_payment_settings.id == stored.id

    def test_a_card_the_definition_does_not_pay_is_unstorable(self, app, seed_user):
        """A row naming one card for a definition paying another is refused by name."""
        with app.app_context():
            visa = _card(seed_user)
            amex = _card(seed_user, name="Amex")
            template = payment_into(seed_user, visa)
            db.session.commit()
            with pytest.raises(IntegrityError, match="fk_card_payment_settings_payment"):
                _raw_insert(template.id, amex.id, seed_user["user"].id)
            db.session.rollback()

    def test_the_definitions_destination_cannot_move_under_it(self, app, seed_user):
        """Moving a paid definition to another card is refused by the same key.

        The transfer edit door refuses this first with a sentence
        (``test_card_payment_doors.py``); this is the database's half, for a
        writer no door stands in front of.
        """
        with app.app_context():
            visa = _card(seed_user)
            amex = _card(seed_user, name="Amex")
            template = payment_into(seed_user, visa)
            db.session.add(_settings(template))
            db.session.commit()
            with pytest.raises(IntegrityError, match="fk_card_payment_settings_payment"):
                db.session.execute(sqlalchemy.text(
                    "UPDATE budget.transfer_templates SET to_account_id = :a "
                    "WHERE id = :t"
                ), {"a": amex.id, "t": template.id})
            db.session.rollback()


class TestOnePerDefinitionAndOnePerCard:
    """The two unique keys, and the owner key."""

    def test_a_definition_holds_one_row(self, app, seed_user):
        """A second row for one definition is refused by a unique key.

        Either key may be the one named: the payment key makes a second row
        for one definition name the same card, so the card's key refuses it
        too, and PostgreSQL reports whichever index it checks first.
        """
        with app.app_context():
            template = payment_into(seed_user, _card(seed_user))
            db.session.add(_settings(template))
            db.session.commit()
            with pytest.raises(
                IntegrityError,
                match="uq_card_payment_settings_(transfer_template_id|card_account_id)",
            ):
                _raw_insert(template.id, template.to_account_id, seed_user["user"].id)
            db.session.rollback()

    def test_a_card_holds_one_worked_out_payment(self, app, seed_user):
        """Two definitions into one card: the second's settings are refused by name.

        Two worked-out payments on one day would each subtract the other, so
        the card has one.  The second definition itself is legal: it is a
        fixed or extra payment, a plain transfer (ruling plan#2666).
        """
        with app.app_context():
            card = _card(seed_user)
            first = payment_into(seed_user, card)
            second = payment_into(seed_user, card, name="Extra to the card")
            db.session.add(_settings(first))
            db.session.commit()
            db.session.add(_settings(second))
            with pytest.raises(
                IntegrityError, match="uq_card_payment_settings_card_account_id",
            ):
                db.session.commit()
            db.session.rollback()

    def test_another_owner_is_unstorable(self, app, seed_user, seed_second_user):
        """A row whose owner is not the card's is refused by the owner key."""
        with app.app_context():
            template = payment_into(seed_user, _card(seed_user))
            db.session.commit()
            with pytest.raises(IntegrityError, match="fk_card_payment_settings_owner"):
                _raw_insert(
                    template.id, template.to_account_id,
                    seed_second_user["user"].id,
                )
            db.session.rollback()


class TestWhatDeletesIt:
    """The row goes with its definition; a mode in use stays."""

    def test_the_row_dies_with_its_definition(self, app, seed_user):
        """A raw DELETE of the definition takes the row (CASCADE, not the ORM's orphan rule)."""
        with app.app_context():
            template = payment_into(seed_user, _card(seed_user))
            db.session.add(_settings(template))
            db.session.commit()
            db.session.execute(sqlalchemy.text(
                "DELETE FROM budget.transfer_templates WHERE id = :t"
            ), {"t": template.id})
            db.session.commit()
            assert db.session.query(CardPaymentSettings).count() == 0

    def test_a_mode_in_use_cannot_be_deleted(self, app, seed_user):
        """``fk_card_payment_settings_mode_id`` is RESTRICT."""
        with app.app_context():
            template = payment_into(seed_user, _card(seed_user))
            db.session.add(_settings(template))
            db.session.commit()
            with pytest.raises(IntegrityError, match="fk_card_payment_settings_mode_id"):
                db.session.execute(sqlalchemy.text(
                    "DELETE FROM ref.card_payment_modes WHERE id = :m"
                ), {"m": _mode()})
            db.session.rollback()


class TestAuditAndMigration:
    """The audit trigger, and the revision's round trip on this test's clone."""

    def test_the_table_is_audited(self, app):
        """``AUDITED_TABLES`` names it and its trigger is attached."""
        with app.app_context():
            assert ("budget", "card_payment_settings") in AUDITED_TABLES
            assert db.session.execute(sqlalchemy.text(
                "SELECT EXISTS (SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'audit_card_payment_settings')"
            )).scalar()

    def test_the_revision_round_trips(self, app):
        """Down drops all it made; up restores it, seed and rule included.

        Counted by name: the two tables, the superkey, the two functions and
        the three rule attachments.
        """
        census = sqlalchemy.text(
            "SELECT "
            "to_regclass('budget.card_payment_settings') IS NOT NULL, "
            "to_regclass('ref.card_payment_modes') IS NOT NULL, "
            "(SELECT count(*) FROM pg_constraint "
            " WHERE conname = 'uq_transfer_templates_id_to_account'), "
            "(SELECT count(*) FROM pg_proc p "
            " JOIN pg_namespace n ON n.oid = p.pronamespace "
            " WHERE n.nspname = 'budget' AND p.proname IN "
            " ('check_card_payment_card_revolves', 'card_payment_card_revolves')), "
            "(SELECT count(*) FROM pg_trigger "
            " WHERE tgname LIKE 'ck\\_card\\_payment\\_card\\_revolves%' "
            " AND NOT tgisinternal)"
        )
        with app.app_context():
            assert tuple(db.session.execute(census).one()) == (True, True, 1, 2, 3)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.commit()
            assert tuple(db.session.execute(census).one()) == (False, False, 0, 0, 0)
            run_migration_callable(_MIGRATION.upgrade, db.session)
            db.session.commit()
            assert tuple(db.session.execute(census).one()) == (True, True, 1, 2, 3)
            names = db.session.execute(sqlalchemy.text(
                "SELECT name FROM ref.card_payment_modes ORDER BY name"
            )).scalars().all()
            assert names == ["card_balance", "minimum_payment", "statement_balance"]
