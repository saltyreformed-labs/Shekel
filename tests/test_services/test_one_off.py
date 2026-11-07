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
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services.one_off import (
    RowToMove,
    due_date_after_move,
    due_date_for,
    OneOffToPlace,
    place_one_off,
    place_row_of,
    restate_price,
    state_due_date,
)
from app.services.pay_calendar import DerivedPeriod, calendar_for
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


def _moved(held, *, source, stated=None):
    """A row off a card holding *held*, leaving *source*, *stated* typed beside the move."""
    return RowToMove(
        stated=held if stated is None else stated,
        held=held,
        source_start=source.start_date,
    )


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
                _moved(source.start_date, source=source), target,
            ) == target.start_date
            stated = source.start_date + timedelta(days=6)
            assert due_date_after_move(
                _moved(stated, source=source), target,
            ) == stated
            # A stated day that happens to BE the source's start reads as
            # the default and moves -- read by position, since R-BAL25
            # rejected a stored marker.
            assert due_date_after_move(
                _moved(source.start_date, source=source), target,
            ) != source.start_date

    def test_a_typed_day_off_a_card_is_read_by_position_too(
        self, app, seed_user, seed_periods_today,
    ):
        """Off a card a day typed beside the move is read as any other date is."""
        with app.app_context():
            source = _period(seed_user, seed_periods_today, 2)
            target = _period(seed_user, seed_periods_today, 3)
            held = source.start_date + timedelta(days=6)
            typed = source.start_date + timedelta(days=3)
            assert due_date_after_move(
                _moved(held, source=source, stated=typed), target,
            ) == typed
            assert due_date_after_move(
                _moved(held, source=source, stated=source.start_date), target,
            ) == target.start_date


class TestAMovedRowOnACard:
    """``due_date_after_move`` on a card: ruling plan#2976, its clauses one by one.

    The paychecks start Jan 16 and Jan 30, and today is Jan 22.  The doors and the money
    are ``tests/test_routes/test_card_row_moved.py``.
    """

    _SOURCE_START = date(2026, 1, 16)
    _TODAY = date(2026, 1, 22)

    def _after(self, seed_user, seed_periods, held, target_index, **row):
        """The date a row holding *held* is left with, moved to the paycheck at *target_index*."""
        row.setdefault("stated", held)
        row.setdefault("card_today", self._TODAY)
        return due_date_after_move(
            RowToMove(held=held, source_start=self._SOURCE_START, **row),
            _period(seed_user, seed_periods, target_index),
        )

    def test_a_bill_whose_date_has_passed_keeps_it(self, seed_user, seed_periods):
        """Jan 20 has passed, so it stays, moved to either paycheck."""
        assert self._after(seed_user, seed_periods, date(2026, 1, 20), 2) == date(2026, 1, 20)
        assert self._after(seed_user, seed_periods, date(2026, 1, 20), 0) == date(2026, 1, 20)

    def test_a_bill_dated_today_keeps_it(self, seed_user, seed_periods):
        """Today reads as passed: a row added today moves as it would tomorrow."""
        assert self._after(seed_user, seed_periods, self._TODAY, 2) == self._TODAY

    def test_a_bill_dated_ahead_follows_to_a_paycheck_not_started(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 23, a day ahead: Jan 30's paycheck has not started, so Jan 30."""
        assert self._after(seed_user, seed_periods, date(2026, 1, 23), 2) == date(2026, 1, 30)

    def test_a_bill_dated_ahead_follows_to_a_paycheck_started_or_ended(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 30, moved to Jan 16's (started) or Jan 2's (ended): today, Jan 22."""
        assert self._after(seed_user, seed_periods, date(2026, 1, 30), 1) == self._TODAY
        assert self._after(seed_user, seed_periods, date(2026, 1, 30), 0) == self._TODAY

    def test_an_extra_payment_follows_whatever_its_date(self, seed_user, seed_periods):
        """Dated before, on and after today alike: Jan 30's first day, then today."""
        for held in (date(2026, 1, 20), self._TODAY, date(2026, 1, 25)):
            assert self._after(
                seed_user, seed_periods, held, 2, is_extra_payment=True,
            ) == date(2026, 1, 30)
            assert self._after(
                seed_user, seed_periods, held, 1, is_extra_payment=True,
            ) == self._TODAY

    def test_a_date_typed_in_the_same_save_stands(self, seed_user, seed_periods):
        """Typed, it stands on a bill or a payment, the source's first day included.

        Off a card that first day reads as the default and moves; on a card it differs
        from the held date, so it was typed.
        """
        for extra in (False, True):
            for typed in (date(2026, 2, 3), self._SOURCE_START):
                assert self._after(
                    seed_user, seed_periods, date(2026, 1, 25), 2,
                    stated=typed, is_extra_payment=extra,
                ) == typed

    def test_an_extra_payment_means_nothing_off_a_card(self, seed_user, seed_periods):
        """With no ``card_today`` the row takes ruling R-BAL93: its Jan 20 stays."""
        assert self._after(
            seed_user, seed_periods, date(2026, 1, 20), 2,
            card_today=None, is_extra_payment=True,
        ) == date(2026, 1, 20)


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


class TestTheDayARepeatingRowFollowsTo:
    """``due_date_after_move`` with a rule's day (ruling plan#2991, plan#3026)."""

    _PAYCHECK = DerivedPeriod(
        period_id=1, period_index=0, start_date=date(2026, 1, 30),
        end_date=date(2026, 2, 12), end_is_projected=False,
    )

    def _moved(self, rule_day, today):
        """A bill dated ahead on Feb 13, moved into :attr:`_PAYCHECK` on *today*."""
        return due_date_after_move(
            RowToMove(
                stated=date(2026, 2, 13), held=date(2026, 2, 13),
                source_start=date(2026, 2, 13), card_today=today, rule_day=rule_day,
            ),
            self._PAYCHECK,
        )

    def test_its_rules_day_inside_the_paycheck_is_its_default(self):
        """Its rule dates it Feb 5, inside the paycheck: Feb 5, not the first day."""
        assert self._moved(date(2026, 2, 5), date(2026, 1, 22)) == date(2026, 2, 5)

    def test_today_wins_once_that_day_has_passed(self):
        """Today is Feb 8, past the rule's Feb 5: Feb 8, or a true-up would hold it."""
        assert self._moved(date(2026, 2, 5), date(2026, 2, 8)) == date(2026, 2, 8)

    def test_its_rules_day_outside_the_paycheck_is_no_default(self):
        """Its rule dates it Mar 5, another paycheck's: the first day, Jan 30."""
        assert self._moved(date(2026, 3, 5), date(2026, 1, 22)) == date(2026, 1, 30)
