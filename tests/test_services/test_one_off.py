"""
Shekel Budget App -- the ONE producer of a one-off (plan step balance:X-bi-7b)

Ruling **R-BAL20**: every plan item has exactly one definition, so a one-off
is a RULE-LESS definition plus its placed row.  ``app.services.one_off`` is
the one door that mints that shape, and these cases grade each clause of what
it states against the ruling that states it:

* the DEFINITION carries the name, category, type, account, owner and both
  flags, and has no rule (``recurs`` is ``False`` on it and on its row);
* the PRICE lives on the definition as ONE version dated on the row's due
  date (**R-BAL21**); the row states no figure and resolves through amount
  rule 3 to the stated amount -- and keeps resolving to it on any date,
  because a one-version series is flat;
* the DUE DATE is the placed paycheck's START unless one is stated
  (**R-BAL22**), and the row records it as the occurrence it answers
  (``occurs_on = due_date``, **R-BAL25**);
* the row is born Projected and unflagged (``is_override`` False,
  **R-BAL28**), priced ``derived(TEMPLATE)``;
* a definition may carry NO category (**R-BAL24**, migration
  ``9c1e4b7a2d3f``), which the bank door needs;
* :func:`place_row_of` places a SECOND row of the same definition in another
  paycheck, which is the shape a bank-born envelope takes at ``X-f6c``.

Every case builds through the producer and reads back through the app's own
readers (``resolved_amount`` is the one resolver), never through a hand-built
row -- a control routed round the door would grade a shape the app never
writes.
"""
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services.one_off import (
    due_date_after_move,
    due_date_for,
    OneOffToPlace,
    place_one_off,
    place_row_of,
    restate_price,
    state_due_date,
)
from app.services.pay_calendar import calendar_for
from app.services.amount_ownership import state_own_amount
from app.services.recurrence_engine import unruled_row_fields
from app.services.template_amount_service import amount_as_of, amount_versions
from tests._test_helpers import resolved_amount
from tests._definition_price import newest_price


def _spec(seed_user, **overrides):
    """Return a Kayla's Kindle spec on the seed account, with *overrides*."""
    fields = {
        "user_id": seed_user["user"].id,
        "account_id": seed_user["account"].id,
        "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        "name": "Kayla's Kindle",
        "amount": Decimal("162.25"),
        "category_id": seed_user["categories"]["Groceries"].id,
    }
    fields.update(overrides)
    return OneOffToPlace(**fields)


def _period(seed_user, seed_periods_today, index):
    """Return the derived paycheck at *index* off the owner's calendar."""
    period = calendar_for(seed_user["user"].id).period_by_id(
        seed_periods_today[index].id,
    )
    assert period is not None
    return period


class TestWhatTheProducerMints:
    """``place_one_off``: the definition, the series, the row."""

    def test_the_definition_carries_what_the_owner_said_and_has_no_rule(
        self, app, seed_user, seed_periods_today,
    ):
        """Name, category, type, account, owner and both flags; ``recurs`` False."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user, is_envelope=True, companion_visible=True),
                period, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            definition = db.session.get(TransactionTemplate, row.template_id)
            assert definition.name == "Kayla's Kindle"
            assert definition.category_id == seed_user["categories"]["Groceries"].id
            assert definition.transaction_type_id == ref_cache.txn_type_id(
                TxnTypeEnum.EXPENSE,
            )
            assert definition.account_id == seed_user["account"].id
            assert definition.user_id == seed_user["user"].id
            assert definition.is_envelope is True
            assert definition.companion_visible is True
            assert definition.is_active is True
            assert definition.recurrence_rule is None
            assert definition.recurs is False
            assert row.recurs is False
            # The row reads the definition's flags, never its own cells.
            assert row.tracks_purchases is True
            assert row.visible_to_companion is True

    def test_the_series_holds_one_version_dated_on_the_rows_due_date(
        self, app, seed_user, seed_periods_today,
    ):
        """R-BAL21: one version, on the due date, and it is the newest price."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            versions = amount_versions(row.template)
            assert len(versions) == 1
            assert versions[0].effective_date == row.due_date
            assert Decimal(str(versions[0].amount)) == Decimal("162.25")
            assert newest_price(row.template) == Decimal("162.25")

    def test_the_row_states_no_figure_and_resolves_to_the_amount_on_any_date(
        self, app, seed_user, seed_periods_today,
    ):
        """Amount rule 3 prices it; a one-version series is flat everywhere."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            assert row.estimated_amount is None
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert resolved_amount(row) == Decimal("162.25")
            # Flat on every date, so a moved due date prices nothing differently.
            for offset in (-400, -1, 0, 1, 400):
                assert amount_as_of(
                    row.template, row.due_date + timedelta(days=offset),
                ) == Decimal("162.25")

    def test_the_row_is_due_on_its_paychecks_start_and_answers_that_occurrence(
        self, app, seed_user, seed_periods_today,
    ):
        """R-BAL22 and R-BAL25 with no stated day."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            assert row.due_date == period.start_date
            assert row.due_date == seed_periods_today[4].start_date
            assert row.occurs_on == row.due_date
            assert row.pay_period_id == seed_periods_today[4].id

    def test_a_stated_due_date_is_honoured_and_dates_the_series(
        self, app, seed_user, seed_periods_today,
    ):
        """The owner's day wins over the paycheck's start, on the row and the version."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            stated = period.start_date + timedelta(days=6)
            row = place_one_off(
                _spec(seed_user), period,
                scenario_id=seed_user["scenario"].id, due_date=stated,
            )
            db.session.commit()

            assert row.due_date == stated
            assert row.occurs_on == stated
            assert amount_versions(row.template)[0].effective_date == stated

    def test_the_row_is_born_projected_unflagged_and_the_owners(
        self, app, seed_user, seed_periods_today,
    ):
        """Projected, ``is_override`` False (R-BAL28), the spec's scenario and owner."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert row.is_override is False
            assert row.is_deleted is False
            assert row.scenario_id == seed_user["scenario"].id
            assert row.user_id == seed_user["user"].id
            assert row.account_id == seed_user["account"].id
            assert row.name == "Kayla's Kindle"
            assert row.category_id == seed_user["categories"]["Groceries"].id

    def test_a_definition_may_carry_no_category(
        self, app, seed_user, seed_periods_today,
    ):
        """R-BAL24: the bank door's row for money nothing can categorise."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user, category_id=None, name="POS DEBIT 4417"),
                period, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            assert row.template.category_id is None
            assert row.category_id is None
            assert resolved_amount(row) == Decimal("162.25")

    def test_a_zero_amount_is_a_legal_price(
        self, app, seed_user, seed_periods_today,
    ):
        """A bank-born envelope budgets nothing, because nothing budgeted it."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user, amount=Decimal("0.00")),
                period, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            assert resolved_amount(row) == Decimal("0.00")
            assert len(amount_versions(row.template)) == 1

    def test_nothing_is_committed_by_the_producer(
        self, app, seed_user, seed_periods_today,
    ):
        """The caller owns the unit of work: a rollback takes definition and row back."""
        with app.app_context():
            before = db.session.query(TransactionTemplate).count()
            period = _period(seed_user, seed_periods_today, 4)
            place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            db.session.rollback()

            assert db.session.query(TransactionTemplate).count() == before
            assert db.session.query(Transaction).filter_by(
                name="Kayla's Kindle",
            ).count() == 0


class TestPlacingARowOfAnExistingDefinition:
    """``place_row_of``: the shape a bank-born envelope takes across paychecks."""

    def test_a_second_paycheck_takes_its_own_row_of_the_same_definition(
        self, app, seed_user, seed_periods_today,
    ):
        """Two rows, one definition, each dated on its own paycheck's start."""
        with app.app_context():
            first = _period(seed_user, seed_periods_today, 4)
            second = _period(seed_user, seed_periods_today, 6)
            row = place_one_off(
                _spec(seed_user, is_envelope=True, amount=Decimal("0.00")),
                first, scenario_id=seed_user["scenario"].id,
            )
            later = place_row_of(
                row.template, second, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            assert later.template_id == row.template_id
            assert later.id != row.id
            assert later.pay_period_id == seed_periods_today[6].id
            assert later.due_date == second.start_date
            assert later.occurs_on == later.due_date
            assert later.recurs is False
            assert later.tracks_purchases is True
            assert resolved_amount(later) == Decimal("0.00")
            # Still ONE version: placing a row states no new price.
            assert len(amount_versions(row.template)) == 1
            assert db.session.query(Transaction).filter_by(
                template_id=row.template_id,
            ).count() == 2

    def test_the_five_derived_columns_are_the_maintain_twins_statement(
        self, app, seed_user, seed_periods_today,
    ):
        """A row as born equals what ``unruled_row_fields`` says about it.

        The structural half of ONE statement: the producer and the maintain
        twin read the same function, so a mutation that spelled the five
        columns a second way in either would part here.
        """
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user, is_envelope=True),
                period, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            said = unruled_row_fields(row.template, row.due_date)
            assert row.account_id == said.account_id
            assert row.name == said.name
            assert row.category_id == said.category_id
            assert row.transaction_type_id == said.transaction_type_id
            assert row.amount_ownership == said.amount_ownership
            assert row.due_date == said.due_date


class TestTheDueDateRule:
    """``due_date_for``: the one statement of R-BAL22 for a placed row."""

    def test_a_stated_day_wins_and_none_means_the_paychecks_start(
        self, app, seed_user, seed_periods_today,
    ):
        """Both arms, on the derived period."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 2)
            assert due_date_for(None, period) == period.start_date
            stated = period.start_date + timedelta(days=3)
            assert due_date_for(stated, period) == stated


class TestAMovedRowIsRePlaced:
    """``due_date_after_move``: R-BAL33, the R-BAL22 default following the placement."""

    def test_the_default_follows_and_a_stated_day_stays(
        self, app, seed_user, seed_periods_today,
    ):
        """Both arms, on derived periods; the miss the ruling names is benign."""
        with app.app_context():
            source = _period(seed_user, seed_periods_today, 2)
            target = _period(seed_user, seed_periods_today, 3)
            assert due_date_after_move(
                source.start_date, source_start=source.start_date, target=target,
            ) == target.start_date
            stated = source.start_date + timedelta(days=6)
            assert due_date_after_move(
                stated, source_start=source.start_date, target=target,
            ) == stated
            # A stated day that happens to BE the source's start reads as
            # the default and moves -- read by position, since R-BAL25
            # rejected a stored marker.
            assert due_date_after_move(
                source.start_date, source_start=source.start_date, target=target,
            ) != source.start_date


class TestTheOneWriterOfAPlacedRowsDate:
    """``state_due_date``: ``occurs_on = due_date`` as one act (R-BAL25)."""

    def test_both_columns_take_the_day(
        self, app, seed_user, seed_periods_today,
    ):
        """One call, two columns, one value."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            moved_to = period.start_date + timedelta(days=9)
            state_due_date(row, moved_to)
            db.session.commit()
            db.session.refresh(row)
            assert row.due_date == moved_to
            assert row.occurs_on == moved_to


class TestRestatePrice:
    """``restate_price``: the definition takes the figure, the row reads it (R-BAL29 / R-BAL37)."""

    def test_the_definitions_one_version_takes_the_figure_and_the_row_stays_derived(
        self, app, seed_user, seed_periods_today,
    ):
        """Kayla's Kindle `$162.25` -> `$170.00`: one version, TEMPLATE-priced, no flag."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            restate_price(row, Decimal("170.00"))
            db.session.commit()
            db.session.refresh(row)
            assert [(v.effective_date, v.amount) for v in amount_versions(row.template)] == [
                (row.due_date, Decimal("170.00")),
            ]
            assert newest_price(row.template) == Decimal("170.00")
            assert row.estimated_amount is None
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("170.00")

    def test_a_detached_row_is_re_attached(
        self, app, seed_user, seed_periods_today,
    ):
        """R-BAL37: the interim's OWN + flag row goes back to its definition."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            state_own_amount(row, Decimal("170.00"))
            row.is_override = True
            db.session.commit()

            restate_price(row, Decimal("180.00"))
            db.session.commit()
            db.session.refresh(row)
            assert row.estimated_amount is None
            assert row.is_override is False
            assert resolved_amount(row) == Decimal("180.00")
            assert newest_price(row.template) == Decimal("180.00")

    def test_the_version_the_rows_own_date_reads_is_the_one_corrected(
        self, app, seed_user, seed_periods_today,
    ):
        """A cleared cadence's several versions: the row's date picks the version."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            later = row.due_date + timedelta(days=30)
            row.template.amount_versions.append(TemplateAmountVersion(
                effective_date=later, amount=Decimal("200.00"),
            ))
            db.session.commit()

            restate_price(row, Decimal("170.00"))
            db.session.commit()
            db.session.refresh(row)
            assert [(v.effective_date, v.amount) for v in amount_versions(row.template)] == [
                (row.due_date, Decimal("170.00")),
                (later, Decimal("200.00")),
            ]
            assert resolved_amount(row) == Decimal("170.00")


class TestTheSeriesRowIsTheDefinitions:
    """The version row belongs to the minted definition and goes with it."""

    def test_the_version_names_the_definition(
        self, app, seed_user, seed_periods_today,
    ):
        """One ``template_amount_versions`` row, keyed on the new definition."""
        with app.app_context():
            period = _period(seed_user, seed_periods_today, 4)
            row = place_one_off(
                _spec(seed_user), period, scenario_id=seed_user["scenario"].id,
            )
            db.session.commit()

            stored = db.session.query(TemplateAmountVersion).filter_by(
                transaction_template_id=row.template_id,
            ).all()
            assert len(stored) == 1
            assert stored[0].effective_date == row.due_date
