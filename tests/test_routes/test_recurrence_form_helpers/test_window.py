"""Recurrence form helper tests: ``refuse_inverted_window``, the update door's refusal of an edit
whose stop falls before its start (R7c-b).
"""

from datetime import date
from decimal import Decimal

from flask import Response, get_flashed_messages

from app import ref_cache
from app.enums import RecurrenceUnitEnum
from app.routes._recurrence_form_helpers import resolve_recurrence_rule_for_update
from app.routes._recurrence_form_refusals import RecurrenceFormContext
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import end_bound_before_start_message
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.services.recurrence import EMPTY, NEVER_ENDS, EndsOnDate, build_transient_rule
from app.services.recurring_definition import resolved_definition
from tests._test_helpers import (
    bind_rule_to_loan,
    create_loan_account,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_loan_payment_template,
    submission_from,
    validated_cadence,
)
from app.services import balance_at, loan_recurrence_sync
from tests.oracles.recurrence_baseline import MONTHLY

from ._helpers import _Definition


class TestAnUpdateMayNotInvertTheWindow:
    """``refuse_inverted_window``: the UPDATE door's half of one rule (R7c-b).

    ``ck_recurrence_rules_valid_window`` was drafted for this step and held
    back on a developer ruling -- the column carried DERIVED loan-payment
    windows as well as authored ones, and an empty derived window is a correct
    answer a constraint cannot tell from a user's mistake -- until plan step
    R7d-g deleted the derived writer and landed it (see
    ``tests/test_models/test_recurrence_window_check.py``).  The rule still
    lives at the two AUTHORING doors, because a CHECK answers with an
    ``IntegrityError`` and a door with a sentence; this class is the one the
    schema could not hold: on an update either half of the pair may be the
    STORED value, which no schema sees.

    Both directions are cases the review found only one of.  Each was an
    unhandled ``CheckViolation`` out of ``update_template``'s autoflush while
    the CHECK was drafted in, and each is a silent zero-generation without a
    door.
    """

    @staticmethod
    def _template_with(rule, user_id):
        """Return the minimal stand-in the refusal reads of a template.

        Args:
            rule: The :class:`RecurrenceRule` it owns.  TRANSIENT here, which
                is why the owner is stated separately: a rule reports its
                ``user_id`` through the definition owning it since plan step
                R-F6, and an unsaved rule has no owner to report.
            user_id: The owner the stand-in claims.

        Returns:
            A :class:`_Definition` carrying the three attributes the refusal
            touches, and answering ``recurs`` off the first.  A real
            ``TransactionTemplate`` would drag its own FK graph in for a
            question about two dates.
        """
        return _Definition(
            recurrence_rule=rule,
            to_account_id=None,
            user_id=user_id,
        )

    @staticmethod
    def _ctx(data, end_bound):
        """Return a form context carrying *data*'s submission and a real redirect.

        Args:
            data: The post-``load()`` payload, its recurrence keys consumed
                into the submission (:func:`~tests._test_helpers.submission_from`).
            end_bound: The submitted closing bound, or ``None``.

        Returns:
            The :class:`RecurrenceFormContext`.
        """
        return RecurrenceFormContext(
            submission=submission_from(data, end_bound),
            redirect=RedirectTarget(
                "templates.edit_template", {"template_id": 1},
            ),
        )

    def _monthly_rule(self, seed_user, starts_on, end_bound=NEVER_ENDS):
        """Author a monthly rule starting on *starts_on*.

        Args:
            seed_user: The ``seed_user`` fixture dict.
            starts_on: The rule's first occurrence.
            end_bound: Its closing bound.

        Returns:
            The flushed :class:`RecurrenceRule`.
        """
        return build_transient_rule(
            submission_from(
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.MONTH, starts_on=starts_on,
                    ),
                    "starts_on": starts_on,
                },
                end_bound,
            ).authored(seed_user["user"].id),
            calendar_for(seed_user["user"].id),
        )

    def test_a_loan_cleared_before_its_first_installment_stores_no_inverted_pair(
        self, app, auth_client, seed_user, db, seed_periods, monkeypatch,  # pylint: disable=unused-argument
    ):
        """The EMPTY derived window is a VALUE the door composes, never a stored pair (plan step R7d-g).

        The same ruling that held ``ck_recurrence_rules_valid_window`` back
        (developer, 2026-08-15) named this loan: it originates 2026-06-20 with
        a ``payment_day`` of 15 -- first installment 2026-07-15 -- and is
        cleared the day after origination, so its window is
        ``[2026-07-15, 2026-06-21]``, correct at nought occurrences.  Until
        R7d-g the sync WROTE that pair into the columns and this case held
        that the update door's refusal carried a skip for it, because the
        owner had no control that could repair the app's own inverted pair.

        Nothing writes it now.  The column stays NULL after the loan clears,
        the composed door answers ``EMPTY`` for the definition, and an
        ordinary cadence edit -- which states neither bound -- is not
        refused, with no skip to pass on: the stored pair is not inverted
        because there is no stored pair.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 7, 1))
            loan = create_loan_account(
                seed_user, db.session, name="Cleared Early",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 6, 20), payment_day=15,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=15,
            )
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()

            loan_recurrence_sync.sync_loan_payment_start(loan.id)
            db.session.commit()

            rule = tpl.recurrence_rule
            db.session.refresh(rule)
            assert rule.starts_on == date(2026, 7, 15)
            assert rule.end_date is None and rule.max_occurrences is None, (
                "a writer stored the derived window: "
                f"starts_on={rule.starts_on} end_date={rule.end_date}"
            )
            pass_ctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.is_standing_loan_payment(tpl, pass_ctx), (
                "precondition: this is the loan's standing payment"
            )
            assert resolved_definition(tpl, pass_ctx).closing.derived == EMPTY

            with app.test_request_context():
                refusal = resolve_recurrence_rule_for_update(
                    tpl,
                    ctx=self._ctx(
                        {
                            **validated_cadence(
                                unit=RecurrenceUnitEnum.MONTH,
                                states_a_start=False,
                            ),
                        },
                        None,
                    ),
                    pass_ctx=pass_ctx,
                )

            assert refusal is None, (
                "an ordinary cadence edit of a loan payment whose loan cleared "
                "early was refused: something stored an inverted pair"
            )

    def test_clearing_the_start_while_setting_an_earlier_end_is_refused(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The cleared date box drops the key, so the STORED start applies.

        ``starts_on`` is not ``allow_none``, so an emptied control arrives
        ABSENT rather than as a stated ``None`` -- which the update door reads
        as "leave the stored date alone".  The submitted bound then lands below
        a date the payload never mentioned, and no schema can see the pair.
        """
        with app.test_request_context():
            rule = self._monthly_rule(seed_user, date(2026, 6, 1))

            refusal = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                ctx=self._ctx(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
                        ),
                    },
                    EndsOnDate(on=date(2026, 5, 31)),
                ),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert isinstance(refusal, Response)
            assert rule.end_date is None, "the rule must be left untouched"

    def test_a_cadence_switch_that_lifts_the_stored_start_past_the_stop_is_refused(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The pair the DOOR would store inverts though the authored pair does not (plan step R7d-g).

        A MONTH rule ``[2025-06-01, 2025-12-01]`` is ordered and stored.  The
        owner switches "Repeats" to every paycheck and restates neither bound:
        ``refuse_inverted_window`` grades the stored pair, still ordered, and
        passes; the re-author then normalises the start onto the owner's
        FIRST payday, 2026-01-02, past the stop.  The write door refuses off
        the pair it would have stored, this dispatcher words it with the
        inverted-window sentence naming that payday, and the rule reads as it
        did.  Until R7d-g-1's review this was ``ck_recurrence_rules_valid_window``
        answering the flush with a 500.
        """
        with app.test_request_context():
            rule = self._monthly_rule(
                seed_user, date(2025, 6, 1), EndsOnDate(on=date(2025, 12, 1)),
            )
            calendar = calendar_for(seed_user["user"].id)
            assert calendar.opening_bound() == date(2026, 1, 2)

            refusal = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                ctx=self._ctx(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.PERIOD, states_a_start=False,
                        ),
                    },
                    None,
                ),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )
            flashed = get_flashed_messages()

            assert isinstance(refusal, Response)
            assert end_bound_before_start_message(
                date(2025, 12, 1), date(2026, 1, 2),
            ) in flashed, flashed
            assert rule.starts_on == date(2025, 6, 1)
            assert rule.end_date == date(2025, 12, 1)
            assert ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH) == rule.unit_id

    def test_moving_the_start_PAST_a_stored_end_is_refused(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The direction no reviewer listed, and the schema cannot see it AT ALL.

        The form states a new "Starts on" and says nothing about the "Ends"
        control, so the payload carries no bound -- and the stored one is what
        the save keeps.  ``require_end_bound_after_start`` runs only when the
        SUBMISSION states both, so it returns early here.
        """
        with app.test_request_context():
            rule = self._monthly_rule(
                seed_user, date(2026, 6, 1),
                end_bound=EndsOnDate(on=date(2026, 7, 1)),
            )

            refusal = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                ctx=self._ctx(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            starts_on=date(2026, 9, 1),
                        ),
                        "starts_on": date(2026, 9, 1),
                    },
                    None,
                ),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert isinstance(refusal, Response)
            assert rule.starts_on == date(2026, 6, 1), (
                "the rule must be left untouched"
            )

    def test_an_end_ON_the_new_start_is_ALLOWED(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The boundary, and the control for both cases above.

        A rule whose closing bound is its own first occurrence fires exactly
        once, which is a real cadence.  Without this arm a door that refused
        every stated bound would pass the two refusals.
        """
        with app.test_request_context():
            rule = self._monthly_rule(seed_user, date(2026, 6, 1))

            outcome = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                ctx=self._ctx(
                    {
                        **validated_cadence(
                            unit=RecurrenceUnitEnum.MONTH,
                            starts_on=date(2026, 6, 1),
                        ),
                        "starts_on": date(2026, 6, 1),
                    },
                    EndsOnDate(on=date(2026, 6, 1)),
                ),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert outcome is None
            assert rule.end_date == date(2026, 6, 1)

    def test_an_amount_only_edit_states_neither_and_is_not_graded(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """A partial update carries no recurrence keys and must stay savable.

        The rule's own stored window was checked when it was written, so
        re-grading it on an edit that mentions neither value would refuse an
        edit that changed nothing about the recurrence.
        """
        with app.test_request_context():
            rule = self._monthly_rule(
                seed_user, date(2026, 6, 1),
                end_bound=EndsOnDate(on=date(2026, 7, 1)),
            )

            outcome = resolve_recurrence_rule_for_update(
                self._template_with(rule, seed_user["user"].id),
                ctx=self._ctx({"amount": Decimal("10.00")}, None),
                pass_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert outcome is None
            assert rule.end_date == date(2026, 7, 1)
