"""
Shekel Budget App -- Pay Period Route Tests

Tests for the pay period generation form and endpoint:
  - Form rendering
  - Successful generation with defaults and custom values
  - Validation errors (missing/invalid fields)
  - Double-submit (duplicates skipped by service)
"""

from datetime import date

from app.enums import StatusEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import pay_schedule_service
from app.services.pay_rhythm import FixedDays
from tests._test_helpers import (
    record_paydays_across_a_hole,
    shift_form_value,
    rhythm_of,
    add_txn,
    freeze_today,
    last_covered_day,
)


def _spans(session, user_id):
    """Return the owner's ``(start_date, end_date)`` spans, payday ascending."""
    return [
        (period.start_date, last_covered_day(period))
        for period in session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.start_date)
        .all()
    ]


# ── Tests ────────────────────────────────────────────────────────────


class TestPayPeriodGenerate:
    """Tests for GET/POST /pay-periods/generate."""

    def test_generate_form_redirects_to_settings(self, app, bare_auth_client, bare_user):
        """GET /pay-periods/generate returns 302 redirect to settings dashboard."""
        with app.app_context():
            resp = bare_auth_client.get("/pay-periods/generate")
            assert resp.status_code == 302
            assert "/settings" in resp.headers["Location"]
            assert "section=pay-periods" in resp.headers["Location"]

    def test_generate_periods_success(self, app, bare_auth_client, bare_user):
        """POST /pay-periods/generate creates periods and redirects to grid."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "num_periods": "10",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Generated 10 pay periods" in resp.data

            periods = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).all()
            assert len(periods) == 10

    def test_generate_missing_start_date(self, app, bare_auth_client, bare_user):
        """POST /pay-periods/generate without start_date returns 422 with field error."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "num_periods": "10",
            })

            assert resp.status_code == 422
            assert b"Start Date" in resp.data
            assert b"Please fix the following errors" in resp.data

    def test_generate_cadence_zero(self, app, bare_auth_client, bare_user):
        """POST /pay-periods/generate with cadence_days=0 returns 422 with field error."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "cadence_kind": "fixed_days",
                "cadence_days": "0",
                "shift": shift_form_value(),
            })

            assert resp.status_code == 422
            assert b"Cadence Days" in resp.data
            assert b"Please fix the following errors" in resp.data

    def test_generate_single_period(self, app, bare_auth_client, bare_user):
        """POST /pay-periods/generate with num_periods=1 creates one period."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-04-01",
                "num_periods": "1",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Generated 1 pay periods" in resp.data

            periods = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).all()
            assert len(periods) == 1

    def test_generate_twice_CONTINUES_rather_than_restating_the_phase(
        self, app, bare_auth_client, bare_user,
    ):
        """A second submit appends; it does not re-state the rhythm.

        **This case asserted "duplicates skipped" until plan step
        ``pay_calendar:C14-f``** (ruling **R-PC63**, ledger row **P80**), and
        the inversion is that step in one property.  The old door took a
        ``start_date`` from an owner who already had one, so a repeated post
        landed on paydays that already existed and ``_apply`` skipped them --
        the count stayed at 5 by COLLISION, not by design.  That same
        indifference to the stored phase is what let three posts naming
        unrelated days write paydays 196 days apart.

        The door now asks an owner who holds a rhythm only HOW MANY MORE, so
        the second post means "five more" and the count is 10.  The submitted
        ``start_date`` and ``cadence_days`` are not consulted -- which is what
        the next case pins.

        **The idempotence is genuinely gone and that is a consequence worth
        naming rather than hiding**: a stale page double-submitted now appends
        instead of colliding.  It is the property the Extend door has always
        had, and this door IS the Extend door for such an owner since R-PC63.
        """
        with app.app_context():
            data = {
                "start_date": "2026-05-01",
                "num_periods": "5",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            }

            # First submit ESTABLISHES: this owner holds no paydays.
            bare_auth_client.post("/pay-periods/generate", data=data,
                             follow_redirects=True)
            first_count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert first_count == 5

            # Second submit CONTINUES: five more, appended past the last.
            resp = bare_auth_client.post("/pay-periods/generate", data=data,
                                    follow_redirects=True)
            assert resp.status_code == 200

            spans = _spans(db.session, bare_user["user"].id)
            assert len(spans) == 10
            # Every gap is the STORED cadence -- the batch continued the
            # rhythm rather than re-opening it at the submitted 2026-05-01.
            paydays = [start for start, _end in spans]
            assert {(b - a).days for a, b in zip(paydays, paydays[1:])} == {14}

    def test_P80s_irregular_payday_set_is_UNWRITABLE_through_this_door(
        self, app, bare_auth_client, bare_user,
    ):
        """Ledger row **P80**'s own worked example cannot be written.

        Plan step ``pay_calendar:C14-f``, ruling **R-PC63**.  P80 is
        *"no check anywhere sees an IRREGULAR payday set"*: this door accepted
        any payday at or after the owner's floor, so three posts naming
        unrelated days wrote ``[2026-01-02, 2026-01-16, 2026-07-31]`` at
        cadence 14 and derived a **196-day paycheck** -- six months of rows
        filing into one grid column, with ``scripts/integrity_check.py``
        reporting green.

        The step was originally specified as a CHECK for that state
        (**R-PC55**).  A check is a reconciler for a duplication and rule 14
        says delete a home instead, so the door stopped asking an owner who
        already holds a rhythm to restate it.  The bad state is unrepresentable
        **through this door**, which is why this asserts the SPACING rather
        than a warning.

        **IT DOES NOT CLOSE P80, and a first draft of this docstring said it
        did.**  This step's adversarial review found ``regenerate`` still
        renders a "Corrected first payday" with no ceiling, and the same
        irregular set was then MEASURED through that route: a **140-day gap**,
        HTTP 200.  P80's own predicate -- *no check anywhere sees an irregular
        payday set* -- is still true after this change.  The row stays OPEN and
        the remaining door is named in the route's own comment.

        **Posted as a client would, not as the form emits.**  The card is not
        rendered for an owner who holds paydays, so a browser cannot reach this
        at all -- and that is exactly why the test must not go through one.
        P80's write was a direct POST; the UI is an affordance and the route is
        the control.

        *What this case no longer covers, said out loud so its absence is not
        read as a gap*: it asserted **R-PC1**'s forward-only floor and the 422
        that renders it.  This door cannot reach that refusal any more, because
        it no longer forwards a payday.  The rule itself is unmoved and is
        covered at its own layer -- ``test_pay_period_write/`` pins the
        message in eleven places, and the doors that still state a payday
        (regenerate, reset) still meet it.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            # P80's opening payday, at P80's cadence.
            bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02", "num_periods": "2",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            }, follow_redirects=True)
            assert [start for start, _end in _spans(db.session, user_id)] == [
                date(2026, 1, 2), date(2026, 1, 16),
            ]

            # THE P80 POST: a payday 196 days past the last one, which the old
            # door accepted because it sat above the floor.
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-07-31", "num_periods": "1",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            }, follow_redirects=True)
            assert resp.status_code == 200

            paydays = [start for start, _end in _spans(db.session, user_id)]
            # 2026-07-31 was IGNORED -- the appended payday continues the
            # rhythm, so no 196-day gap exists to be warned about.
            assert date(2026, 7, 31) not in paydays
            assert paydays == [
                date(2026, 1, 2), date(2026, 1, 16), date(2026, 1, 30),
            ]
            gaps = {(b - a).days for a, b in zip(paydays, paydays[1:])}
            assert gaps == {14}, f"P80's irregular set became writable: {gaps}"

    def test_an_owner_emptied_back_to_zero_paydays_may_ESTABLISH_again(
        self, app, bare_auth_client, bare_user,
    ):
        """The dispatch asks the PAYDAYS, not the schedule row.

        Plan step ``pay_calendar:C14-f``, ruling **R-PC63**.  It pins the
        SEMANTICS of ``routes.pay_periods._holds_paydays``: it asks the
        paydays, not the schedule row, so a row with no paydays still
        ESTABLISHES.

        **The state is built directly because NO DOOR PRODUCES IT, and that is
        stated rather than hidden.**  A first draft of this docstring called
        the owner "reachable" and named truncate and reset as the producers;
        this step's adversarial review measured both false -- truncate always
        keeps the named period ("THIS DOOR can never empty a schedule") and
        reset re-records inside the same call.  So the raw ``delete()`` below
        is not a shortcut to a real scenario, it is the only way to construct
        one, and this case grades the predicate's meaning rather than a user
        journey.  It earns its place by failing when ``_holds_paydays`` is
        weakened to ``get_schedule() is not None`` -- verified by mutation,
        and it is the ONLY case that fails under that mutation while the two
        dispatch cases still pass.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-05-01", "num_periods": "3",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            }, follow_redirects=True)
            assert len(_spans(db.session, user_id)) == 3

            # Empty the calendar, leaving the row and its era behind.
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete()
            db.session.commit()
            emptied = pay_schedule_service.get_schedule(user_id)
            assert emptied is not None, "the row must survive for this to bite"
            assert len(emptied.eras) == 1, "so must the era"

            # ESTABLISH again, at a wholly new phase.
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2027-03-04", "num_periods": "2",
                "cadence_kind": "fixed_days",
                "cadence_days": "7",
                "shift": shift_form_value(),
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert [start for start, _end in _spans(db.session, user_id)] == [
                date(2027, 3, 4), date(2027, 3, 11),
            ]
            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(7)


# ── Negative Path Tests ─────────────────────────────────────────────


class TestPayPeriodNegativePaths:
    """Tests for pay period generation validation and edge cases."""

    def test_generate_invalid_date_format(self, app, bare_auth_client, bare_user):
        """Non-date string for start_date returns 422 with validation error."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "not-a-date",
                "num_periods": "10",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            })
            assert resp.status_code == 422
            assert b"Start Date" in resp.data

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_negative_num_periods(self, app, bare_auth_client, bare_user):
        """Negative num_periods returns 422 (Range min=1 on schema)."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "-5",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            })
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_zero_num_periods(self, app, bare_auth_client, bare_user):
        """Zero num_periods returns 422 (Range min=1 on schema)."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "0",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            })
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_extremely_large_num_periods(self, app, bare_auth_client, bare_user):
        """num_periods exceeding max=260 returns 422 validation error."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "999999",
                "cadence_kind": "fixed_days",
                "cadence_days": "14",
                "shift": shift_form_value(),
            })
            # PayPeriodGenerateSchema has Range(min=1, max=260) on num_periods.
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_negative_cadence_days(self, app, bare_auth_client, bare_user):
        """Negative cadence_days returns 422 (Range min=1 on schema)."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-01-02",
                "num_periods": "10",
                "cadence_kind": "fixed_days",
                "cadence_days": "-1",
                "shift": shift_form_value(),
            })
            assert resp.status_code == 422

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_missing_all_fields(self, app, bare_auth_client, bare_user):
        """Empty form data returns 422 with required field errors."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={})
            assert resp.status_code == 422
            # start_date is the only truly required field
            # (num_periods and cadence_days have load_defaults).
            assert b"Start Date" in resp.data

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0

    def test_generate_cadence_zero_db_state(self, app, bare_auth_client, bare_user):
        """Cadence zero returns 422 and creates no pay periods in the DB."""
        with app.app_context():
            resp = bare_auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-03-01",
                "cadence_kind": "fixed_days",
                "cadence_days": "0",
                "shift": shift_form_value(),
            })
            assert resp.status_code == 422
            assert b"Cadence Days" in resp.data

            count = db.session.query(PayPeriod).filter_by(
                user_id=bare_user["user"].id,
            ).count()
            assert count == 0



class TestShorteningTheSchedulePastASettledDayGoesThrough:
    """The doors that used to refuse a coverage withdrawal now carry it out.

    **Plan step C3-b shipped a refusal on four routes and the developer deleted
    it 2026-08-11**, because the defect it named was not one: a settled row's
    cash day falling outside the reported window is absent from BOTH sides of
    ruling R-K's identity and reports as the ``period_timing`` remainder
    (``test_cash_period_view.py``:
    ``test_a_settle_day_past_the_window_keeps_every_column_exact``).  What the
    refusal cost was real -- 5 of production's 61 truncation points blocked,
    with re-dating a settled row as the only way past.

    Graded at the ROUTE and not only at the service, for the reason the class
    it replaces existed: "the user can now do this" rested on reading the
    handler list, and a stale ``except`` clause would have kept flashing a
    refusal no service raises.
    """

    def _settled_row_past(self, db_session, seed_user, period, day):
        """File a SETTLED row in *period* whose money moved on *day*."""
        return add_txn(
            db_session, seed_user, period, "Tuition", "2100.00",
            status_enum=StatusEnum.DONE, due_date=period.start_date,
            settled_on=day,
        )

    def test_truncate_removes_the_tail_and_keeps_the_stranded_row(
        self, app, db, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """The door the refusal actually blocked.

        A payday at 2026-07-01 stretches the last seeded paycheck to
        2026-06-30, and a settled row inside it cleared 2026-06-15.  Truncating
        the successor away drops that paycheck back to 2026-05-21, so the
        settle day ends up covered by nothing.  The tail goes, the row stays
        exactly as it was, and the page reports success rather than the day.
        """
        freeze_today(monkeypatch, date(2025, 12, 1))
        with app.app_context():
            user_id = seed_user["user"].id
            row = self._settled_row_past(
                db.session, seed_user, seed_periods[-1], date(2026, 6, 15),
            )
            row_id = row.id
            record_paydays_across_a_hole(
                user_id=user_id, first_payday=date(2026, 7, 1),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            before = db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count()

            resp = auth_client.post("/pay-periods/truncate", data={
                "keep_through_period_id": str(seed_periods[-1].id),
                "confirm_discard": "true",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"2026-06-15" not in resp.data
            assert db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count() == before - 1
            survivor = db.session.get(Transaction, row_id)
            assert survivor.settled_on == date(2026, 6, 15)
            assert survivor.pay_period_id == seed_periods[-1].id

    def test_generate_can_only_WIDEN_the_covered_interval(
        self, app, db, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """A batch through this door cannot pull the horizon back at all.

        **This case asserted the OPPOSITE until plan step
        ``pay_calendar:C4-c``**, and the inversion is that step in one
        property.  The horizon was the LAST ROW'S STORED ``end_date``, written
        at whatever cadence the batch that created it ran at; editing
        ``budget.pay_schedule.cadence_days`` afterwards -- finding **P28**'s
        legacy shape -- left the two disagreeing, and a later generate
        rewrote the stored end DOWN to the new cadence's projection.  A door
        could therefore take a settled row's cash day out of every paycheck.

        There is one value now.  The floor is ``latest payday + the stored
        cadence`` and the new horizon is ``new payday + the new cadence - 1``,
        so a batch that RECORDS a payday leaves the horizon at least
        ``old horizon + the new cadence`` -- strictly greater, for every
        cadence in 1..365.  A batch every one of whose requested paydays
        already exists records none, and ``_apply`` then skips
        ``upsert_schedule`` entirely, so the horizon is unchanged and the post
        is still accepted: **non-decreasing** is the property this door has,
        and strictly increasing is what it has when it writes.  *An adversarial
        review corrected that sentence, 2026-09-01.*  Either way the state the
        deleted case built is unreachable through this door, and this is the
        assertion that says so rather than the absence of a test.

        Driven at the SMALLEST cadence the schema admits, because that is where
        the margin is thinnest: one day.

        **Since plan step ``pay_calendar:C14-f`` the property holds for a
        STRONGER reason** (ruling **R-PC63**): this owner already has a rhythm,
        so the door no longer forwards the submitted payday or cadence at all
        -- it continues the stored one, and a tail-append cannot move an
        existing end. The margin argument above is what protected the case
        while the door could still state a cadence, and it is kept because the
        doors that still can (regenerate, reset) rest on it.
        """
        freeze_today(monkeypatch, date(2025, 12, 1))
        with app.app_context():
            user_id = seed_user["user"].id
            record_paydays_across_a_hole(
                user_id=user_id, first_payday=date(2026, 7, 1),
                num_periods=1, rhythm=rhythm_of(180),
            )
            db.session.commit()
            before_horizon = max(end for _start, end in _spans(db.session, user_id))
            # 2026-07-01 + 180 - 1.
            assert before_horizon == date(2026, 12, 27)

            # Since ``pay_calendar:C14-f`` this owner holds a rhythm, so the
            # door CONTINUES it: both fields below are ignored and the appended
            # payday is the stored 180-day cadence past the last one.  They are
            # still POSTED, deliberately -- a cadence of 1 and a backward-ish
            # payday are the strongest thing a client could send, and the
            # property must hold against what a client sends rather than
            # against what the form now offers.
            resp = auth_client.post("/pay-periods/generate", data={
                "start_date": "2026-12-28", "num_periods": "1",
                "cadence_kind": "fixed_days",
                "cadence_days": "1",
                "shift": shift_form_value(),
            })

            assert resp.status_code == 302
            after_horizon = max(end for _start, end in _spans(db.session, user_id))
            # 2026-07-01 + 180 (the appended payday) + 180 - 1.
            assert after_horizon == date(2027, 6, 25)
            assert after_horizon > before_horizon
            # The submitted cadence of 1 never reached the schedule.
            assert pay_schedule_service.resolve_cadence(user_id) == FixedDays(180)
