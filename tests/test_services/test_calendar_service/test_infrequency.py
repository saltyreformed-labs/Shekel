"""Infrequency: the _is_infrequent helper, infrequency derived rather than enumerated (R7a-2b), and
the badge read from the owner's stored cadence.
"""

from datetime import date

from unittest.mock import patch

from app.services import calendar_infrequency, calendar_service
from tests._test_helpers import generate_row_of, restate_fixture_era
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)
from app.services.calendar_infrequency import is_infrequent as _is_infrequent
from app.services.pay_rhythm import FixedDays
from app.services.pay_calendar import PayCadence

from ._helpers import _BIWEEKLY, _MONTHLY_PAID, _add_transaction, _make_template_with_cadence


# ── Infrequency Tests ────────────────────────────────────────────────


class TestIsInfrequent:
    """Tests for the _is_infrequent helper.

    **The predicate is DERIVED since plan step R7a-2b** -- "fires fewer than
    twelve times a year" -- where it was a hand-enumerated set of three
    pattern members.  Every assertion below is unchanged: the derivation has
    to reproduce all six answers before it is allowed to extend past them,
    and ``TestInfrequencyIsDerivedNotEnumerated`` is where it does.
    """

    def test_infrequent_annual(self, app, seed_user, seed_periods, db):
        """Template with Annual pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, ANNUAL, name="Annual",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_infrequent_quarterly(self, app, seed_user, seed_periods, db):
        """Template with Quarterly pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, QUARTERLY, name="Quarterly",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_infrequent_semi_annual(self, app, seed_user, seed_periods, db):
        """Template with Semi-Annual pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, SEMI_ANNUAL, name="Semi",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_non_repeating_not_infrequent(self, app, seed_user, seed_periods, db):
        """A template that does not repeat is NOT infrequent.

        "Infrequent" means "less frequent than monthly", which is a statement
        about a CADENCE; a definition that does not repeat has none, so the
        badge does not apply.  Until plan step R2e-3 the two template kinds
        disagreed about this: a one-time TRANSACTION was already rule-less and
        answered False here, while a one-time TRANSFER carried a ``Once``
        rule, which was a member of ``_INFREQUENT_PATTERNS`` and answered
        True.  Retiring the pattern is what made them agree.
        """
        with app.app_context():
            # A rule-less definition holds rows only one way: they were
            # generated under a cadence the owner then CLEARED, which is the
            # edit door's act (``_clear_recurrence_rule``: dis-associate,
            # and delete-orphan removes the rule).
            template = _make_template_with_cadence(
                db.session, seed_user, EVERY_PERIOD, name="One-time",
            )
            txn = generate_row_of(template, seed_periods[0])
            template.recurrence_rule = None
            db.session.commit()
            assert template.recurrence_rule is None
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_monthly_not_infrequent(self, app, seed_user, seed_periods, db):
        """Template with Monthly pattern is NOT infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, MONTHLY, name="Monthly",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_every_period_not_infrequent(self, app, seed_user, seed_periods, db):
        """Template with Every Period pattern is NOT infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, EVERY_PERIOD, name="Each Period",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_no_template_not_infrequent(self, app, seed_user, seed_periods, db):
        """Manual transaction (template=None) is NOT infrequent."""
        with app.app_context():
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Manual",
                "100.00", due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False


class TestInfrequencyIsDerivedNotEnumerated:
    """What the three-member set could not answer, and now does (R7a-2b).

    The badge asked "is this pattern one of Quarterly / Semi-Annual /
    Annual".  It now asks "does this fire fewer than twelve times a year",
    which is the same answer for those three and a DIFFERENT one for two
    classes the enumeration structurally could not reach.
    """

    def test_every_three_paychecks_is_infrequent(
        self, app, seed_user, seed_periods, db,
    ):
        """Every 3 paychecks fires 8.67 times a year, so it IS infrequent.

        The enumeration said otherwise -- ``EVERY_N_PERIODS`` was not one of
        its three members, so an every-3-paychecks bill rendered as a frequent
        flow whatever its interval.  Hand-computed: 26 / 3 = 8.67 < 12.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=3, name="Every 3rd",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_every_two_paychecks_is_frequent_at_a_biweekly_cadence(
        self, app, seed_user, seed_periods, db,
    ):
        """Every 2 paychecks is 13 a year biweekly -- just over the line.

        The boundary case, and the control on the one above: 26 / 2 = 13,
        which is NOT fewer than 12, so the badge does not apply.  A rule that
        tested ``<=`` would fail here.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=2, name="Every 2nd",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_the_same_rule_answers_differently_for_a_monthly_paid_owner(
        self, app, seed_user, seed_periods, db,
    ):
        """Every 2 paychecks is 6 a year when you are paid monthly.

        The badge is per-OWNER now, which an enumeration of pattern names
        could not be: the identical row is frequent for a biweekly owner
        (13 a year) and infrequent for a monthly-paid one (12 / 2 = 6).  The
        PAIR is the assertion -- either alone would pass against a constant.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=2, name="Every 2nd",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False
            assert _is_infrequent(txn, _MONTHLY_PAID) is True

    def test_a_monthly_bill_is_never_infrequent_at_any_cadence(
        self, app, seed_user, seed_periods, db,
    ):
        """A MONTH-unit cadence fires 12 times a year whoever owns it.

        The control that the per-owner half is scoped to paycheck space: a
        monthly bill is monthly for a weekly-paid owner too, so no cadence may
        move this answer.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, MONTHLY, name="Monthly",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            for cadence in (_BIWEEKLY, _MONTHLY_PAID, PayCadence(FixedDays(7))):
                assert _is_infrequent(txn, cadence) is False


class TestTheBadgeReadsTheOWNERSStoredCadence:
    """The thread from the stored schedule to the rendered day entry.

    **Every other test in this file hands ``_is_infrequent`` a hand-built
    ``PayCadence``, so all of them would still pass if ``badge_cadence``
    returned a hardcoded biweekly value** -- which is exactly the defect class
    plan step R7a-2a existed to remove, one layer up.  An adversarial review of
    R7a-2b named the hole; this is the test that closes it, by moving the
    STORED ``pay_schedule.cadence_days`` and asserting the rendered badge
    follows.
    """

    def test_the_same_row_badges_differently_when_the_schedule_moves(
        self, app, db, seed_user, seed_periods,
    ):
        """An every-2-paychecks bill: frequent at 14 days, infrequent at 30.

        Hand-computed: 26 / 2 = 13 a year biweekly (not fewer than 12, so no
        badge) against 12 / 2 = 6 a year for a monthly-paid owner (badged).
        Nothing about the transaction changes between the two reads -- only
        the owner's stored cadence -- so a hardcoded count fails here whatever
        value it is hardcoded to.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=2, name="Every 2nd",
            )
            generate_row_of(template, seed_periods[0])
            db.session.commit()
            month = seed_periods[0].start_date

            def _badges():
                summary = calendar_service.get_month_detail(
                    seed_user["user"].id, month.year, month.month,
                    user_settings=None,
                )
                return [
                    entry.is_infrequent
                    for entries in summary.day_entries.values()
                    for entry in entries
                    if entry.name == "Every 2nd"
                ]

            restate_fixture_era(seed_user["user"].id, date(2026, 1, 2), 14)
            db.session.commit()
            assert _badges() == [False]

            restate_fixture_era(seed_user["user"].id, date(2026, 1, 2), 30)
            db.session.commit()
            assert _badges() == [True]

    def test_a_month_of_manual_rows_resolves_no_cadence_at_all(
        self, app, db, seed_user, seed_periods,
    ):
        """Nothing repeats, so the build must not read the pay schedule.

        ``badge_cadence``'s contract is "a page must not fail for a fact it
        does not use", and the predicate that enforces it tests the recurrence
        RULE rather than the transaction list -- an earlier draft tested the
        list, which would have resolved a cadence for a month of purely manual
        entries.  Patching the loader to explode is what makes that a control
        rather than a claim.
        """
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Manual",
                "100.00", due_date=seed_periods[0].start_date,
            )
            db.session.commit()
            month = seed_periods[0].start_date

            def _explode(_user_id):
                raise AssertionError(
                    "badge_cadence read the pay schedule for a month with "
                    "nothing to badge",
                )

            with patch.object(
                calendar_infrequency, "cadence_for", _explode,
            ):
                summary = calendar_service.get_month_detail(
                    seed_user["user"].id, month.year, month.month,
                    user_settings=None,
                )
            assert [
                entry.is_infrequent
                for entries in summary.day_entries.values()
                for entry in entries
            ] == [False]
