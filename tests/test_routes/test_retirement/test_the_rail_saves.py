"""The rail's last refused probes, and its end-year Save, which writes the row."""

import logging
import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import RaiseTypeEnum
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER, RETARGET_HEADER
from app.models.user import UserSettings
from app.services.balance_at import BalanceContext
from app.utils.dates import display_today
from tests._csrf_tokens import without_csrf_tokens
from tests._test_helpers import freeze_today, make_recurring_raise
from tests.conftest import log_in_seed_user

from ._helpers import (
    _create_salary_profile,
    _TestTheRailProbesARaisesEndYearMembers,
    _TestTheRailSavesARaisesEndYearMembers,
)


class TestTheRailProbesARaisesEndYear(_TestTheRailProbesARaisesEndYearMembers):
    """A refused end-year probe is the rail's own refusal body (S3-f-2b)."""

    def test_an_swr_what_if_outside_its_range_is_refused_on_its_box(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The SWR box's refusal renders on the box, under the GET's own name.

        The box is one control with two names -- ``safe_withdrawal_rate`` on
        its Save, ``swr`` on the what-if -- and the browser's ``max`` fences
        only a form submit, not the htmx refresh, so 150 reaches the schema.
        Until plan step salary:S3-f-4 the answer was a JSON the page never
        rendered.
        """
        raise_id, _ = self._seed_forever_raise(seed_user, db)
        html = self._refused(auth_client, {
            "swr": "150",
            f"raise_end_mode_{raise_id}": "none",
            f"raise_end_year_{raise_id}": "",
        })
        swr_box = re.search(r'<input[^>]*name="safe_withdrawal_rate"[^>]*>', html).group(0)
        assert "is-invalid" in swr_box
        assert 'value="150"' in swr_box
        feedback = re.search(
            r'name="safe_withdrawal_rate".*?<div class="invalid-feedback">(.*?)</div>',
            html, re.S,
        )
        assert feedback and "Must be" in feedback.group(1)
        assert "is-invalid" not in self._rows(html)[str(raise_id)]

    def test_a_lever_refusal_beside_a_bad_year_is_the_rail_with_the_row_unasked(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """One answer for every refusal, and the tiers are asked in order.

        A stepper outside its range is the SCHEMA's refusal, which answers
        before the rows are consulted -- the ONE end-year rule is asked only
        of a payload the schema accepted, as on the Save -- so the year row
        comes back unmarked, its pair echoed.  The steppers are controls
        outside the rail, so the refusal itself renders nowhere yet; the
        answer is still the rail, not a JSON.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        html = self._refused(auth_client, {
            "months": "181",
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
        })
        row = self._rows(html)[str(raise_id)]
        assert "is-invalid" not in row
        assert f'value="{effective - 1}"' in row
        assert "invalid-feedback" not in html

    def test_the_refusal_is_the_saves_own_body(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """ONE 422 shape: the what-if's refusal IS the Save's, retargeted.

        The same pair refused by the same rule through both doors renders the
        same rail, byte for byte once the per-render csrf tokens are set
        aside; the Save's answer is built for its form's target and the GET's
        carries the retarget that points it there.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        pair = {
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
        }
        probed = auth_client.get(
            "/retirement/readiness", query_string=pair,
            headers={"HX-Request": "true"},
        )
        saved = auth_client.post(
            "/retirement/settings", data=pair, headers={"HX-Request": "true"},
        )
        assert probed.status_code == saved.status_code == 422
        assert probed.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        assert saved.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        assert probed.headers.get(RETARGET_HEADER) == "#assumptions-region"
        assert RETARGET_HEADER not in saved.headers

        assert without_csrf_tokens(probed.data.decode()) == without_csrf_tokens(
            saved.data.decode(),
        )
        assert f"it takes effect in {effective}." in probed.data.decode()


class TestTheRailSavesARaisesEndYear(_TestTheRailSavesARaisesEndYearMembers):
    """The rail's end-year pair has a Save that writes the ROW (S3-f-3).

    Plan step **salary:S3-f-3** (rulings **R-SAL22** and **R-SAL24**).  Each
    recurring-raise row is a form posting the SAME two controls its what-if
    sends to ``retirement.update_settings``, which resolves them against the
    rows the rail lists, applies the ONE end-year rule, writes
    ``salary.salary_raises.terminal_year`` and runs the regeneration every
    salary write runs.  The cases post WHAT THE TEMPLATE EMITS
    (``feedback_a_route_test_must_post_what_the_template_emits``): the first
    reads the rendered row back and submits it verbatim; the rest vary one
    control.
    """

    def test_what_the_rail_emits_saved_back_writes_nothing(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The pre-filled pair, submitted verbatim, is a no-op on the row.

        A rail row carries its stored answer, so a Save nobody changed must
        leave the row where it was: the same ``terminal_year`` and -- the
        claim the year alone cannot grade -- the same optimistic-lock counter,
        so no UPDATE was issued against the raise.
        """
        raise_id, _, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)
        before = self._stored(db, raise_id)
        html = auth_client.get("/retirement").data.decode()
        controls = TestTheRailProbesARaisesEndYear._rail_controls(html)
        assert controls == {
            f"raise_end_mode_{raise_id}": "none",
            f"raise_end_year_{raise_id}": "",
        }

        resp = auth_client.post(
            "/retirement/settings", data=controls,
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        assert 'id="assumptions-panel"' in resp.data.decode()
        assert self._stored(db, raise_id) == before

    def test_an_end_year_is_written_and_the_paychecks_regenerated(
        self, auth_client, seed_user, db, seed_periods_52, monkeypatch,
    ):
        """Ending the raise after its first year writes the column AND re-prices.

        The write is the row's ``terminal_year``.  The re-price is TODAY's
        paycheck as a fresh pass prices it after each Save, the figure the
        Recurring and savings pages show
        (``obligations_aggregator.todays_price``): the grid derives each
        paycheck row from the same rows this door wrote, so the two cannot
        disagree.  Ending the raise a year earlier is the smaller figure
        (``$3,230.77`` against ``$3,392.31`` gross), which the second Save
        shows.  Its observable was the template's ``default_amount``, which
        the regeneration re-stated at today's net, until plan step X-bp
        deleted the re-statement; the regeneration's call is graded by
        :meth:`test_two_rows_posted_together_both_write` and
        :meth:`test_the_saved_raises_own_profile_is_regenerated_and_no_other`.

        **Built on fixed days, so it holds in any month** (plan step
        salary:X-av-3a).  The one pay entry sits on the first payday,
        2026-01-02, at $3,076.92 ($80,000.00 / 26), and a raise is priced
        only where it lands AFTER the entry (ruling R-SAL59), so both
        landings must follow the entry and precede the payday priced.  No
        calendar built from the wall clock holds that: ``seed_periods_today``
        spans 20 weeks, and a yearly raise lands twice in no fewer than 12
        months.  So the calendar is ``seed_periods_52`` (paydays 2026-01-02
        through 2027-12-17), today is 2027-03-20 (the 2027-03-12 payday's
        period, so the effective year is 2026), and the raise moves to
        February -- the first month after the entry's payday -- landing
        2026-02-01 and 2027-02-01.  Each step rounds to the cent (R-SAL60):
        believed forever, 3,076.92 x 1.05 = 3,230.766 -> 3,230.77, then
        x 1.05 = 3,392.3085 -> 3,392.31; ended after 2026, 3,230.77.
        """
        # Pin both clocks to one civil day: the calendar below is fixed, so
        # "today" must be its 2027-03-12 payday's period, the four UTC hours
        # after a New York midnight included.
        freeze_today(monkeypatch, date(2027, 3, 20))
        # Sign in again on the moved date: ``auth_client`` signed in on the
        # real clock, and the idle timeout measures against the clock this
        # freeze moves, so on any real date more than the idle window away
        # the old session is expired here, as a real one would be.
        log_in_seed_user(auth_client)
        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_52, effective_month=2,
        )

        def _todays_paycheck():
            """Today's paycheck, priced by a fresh pass over what was saved."""
            db.session.expire_all()
            ctx = BalanceContext.build(seed_user["user"].id)
            return ctx.paychecks().for_profile(
                db.session.get(SalaryProfile, profile_id),
            ).at(ctx.calendar().period_containing(display_today()))

        forever = self._save(auth_client, raise_id, "none", "")
        assert forever.status_code == 200
        priced_forever = _todays_paycheck()
        assert priced_forever.earnings.gross_biweekly == Decimal("3392.31")

        ended = self._save(auth_client, raise_id, "year", str(effective))
        assert ended.status_code == 200
        assert self._stored(db, raise_id)[0] == effective
        priced_ended = _todays_paycheck()
        assert priced_ended.earnings.gross_biweekly == Decimal("3230.77")
        assert priced_ended.earnings.net_pay < priced_forever.earnings.net_pay, (
            "a raise believed one year less did not lower today's paycheck"
        )

        html = ended.data.decode()
        row = self._row(html, raise_id)
        assert '<option value="year" selected>' in row
        assert f'value="{effective}"' in row

    def test_no_end_year_with_a_stale_year_in_the_box_writes_null(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The mode is authoritative (R-SAL13): "no end year" clears the column.

        A browser fills the year box from the stored value, so choosing "no
        end year" on a raise that ends in three years submits that year
        beside the mode -- the pair the salary form's own test posts for the
        same reason.
        """
        current_payday = max(
            period.start_date for period in seed_periods_today
            if period.start_date <= display_today()
        )
        ends = current_payday.year - 1 + 3
        raise_id, _, _ = self._seed_saveable_raise(
            seed_user, db, seed_periods_today, terminal_year=ends,
        )
        assert self._stored(db, raise_id)[0] == ends

        resp = self._save(auth_client, raise_id, "none", str(ends))

        assert resp.status_code == 200
        assert self._stored(db, raise_id)[0] is None
        row = self._row(resp.data.decode(), raise_id)
        assert '<option value="none" selected>' in row
        year_input = re.search(
            r'<input[^>]*name="raise_end_year_%d"[^>]*>' % raise_id, row, re.S,
        ).group(0)
        assert 'value=""' in year_input, "the cleared row still shows the old year"

    def test_a_year_before_the_effective_year_is_refused_on_the_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The ONE end-year rule refuses, and the refusal renders on the row.

        The same sentence the salary form and the probe give, because all
        three call ``salary_raises.end_year_of``; here it is a designed 422
        fragment with the year control marked invalid and the submitted pair
        echoed on THIS row, and nothing written.
        """
        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        resp = self._save(auth_client, raise_id, "year", str(effective - 1))

        assert resp.status_code == 422
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        row = self._row(html, raise_id)
        assert (
            f"A raise cannot end before it starts: it takes effect in {effective}."
            in row
        )
        year_input = re.search(
            r'<input[^>]*name="raise_end_year_%d"[^>]*>' % raise_id, row, re.S,
        ).group(0)
        assert "is-invalid" in year_input
        assert f'value="{effective - 1}"' in year_input, "the refused year was not echoed"
        assert '<option value="year" selected>' in row, "the submitted mode was not echoed"
        assert self._stored(db, raise_id)[0] is None

    def test_a_mode_outside_the_vocabulary_is_refused_on_the_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The field-tier half of the rule is the schema's, keyed to the control.

        The schema reports the pair's refusal in marshmallow's nested shape;
        the route re-keys it onto ``raise_end_mode_<id>`` so the fragment
        renders it where the rail renders every other field error.
        """
        raise_id, _, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        resp = self._save(auth_client, raise_id, "forever", "")

        assert resp.status_code == 422
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        row = self._row(resp.data.decode(), raise_id)
        select = re.search(r"<select[^>]*>", row).group(0)
        assert "is-invalid" in select
        assert "Must be one of: year, none." in row
        assert self._stored(db, raise_id)[0] is None

    def test_a_refusal_on_one_row_leaves_every_other_row_stating_its_value(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The echo is per row: a 422 for one raise must not blank another.

        Two recurring raises, one Save refused; the untouched row still
        pre-selects its stored mode and fills its stored year.
        """
        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_today,
        )
        other = make_recurring_raise(
            profile_id, db.session, effective_date=date(effective + 1, 1, 1),
            terminal_year=effective + 4,
        )
        db.session.commit()
        other_id = other.id

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.safe_withdrawal_rate = Decimal("0.0350")
        planned = settings.planned_retirement_date
        db.session.commit()

        resp = self._save(auth_client, raise_id, "year", str(effective - 1))

        assert resp.status_code == 422
        html = resp.data.decode()
        other_row = self._row(html, other_id)
        assert '<option value="year" selected>' in other_row
        assert f'value="{effective + 4}"' in other_row
        assert "is-invalid" not in other_row
        # The older rows too: a rail 422 used to render them EMPTY (they echoed
        # ``fd.get(name, '')`` whatever form was refused), and an emptied date
        # row then Saved cleared the pension's planned date.
        swr = re.search(r'<input[^>]*name="safe_withdrawal_rate"[^>]*>', html).group(0)
        assert 'value="3.50"' in swr, "the SWR row lost its stored value on another row's 422"
        date_input = re.search(r'<input[^>]*name="planned_retirement_date"[^>]*>', html).group(0)
        assert f'value="{planned.isoformat()}"' in date_input, (
            "the date row lost its stored value on another row's 422"
        )

    def test_another_owners_raise_id_is_404_and_writes_nothing(
        self, auth_client, seed_user, seed_second_user, db, seed_periods_today, caplog,
    ):
        """Not yours is not found, as everywhere; the refusal leaves a trail.

        The pair is resolved against THIS owner's rail rows -- never by
        querying the submitted id -- so a foreign id has no row to be graded
        against and is the constant 404; ``log_refused_lookup`` records it at
        the one severity the ambiguity supports.
        """
        self._seed_saveable_raise(seed_user, db, seed_periods_today)
        foreign_profile = _create_salary_profile(seed_second_user, db.session)
        foreign = make_recurring_raise(
            foreign_profile.id, db.session, effective_date=date(display_today().year + 2, 1, 1),
        )
        db.session.commit()
        foreign_id = foreign.id

        with caplog.at_level(logging.INFO, logger="app.utils.auth_helpers"):
            resp = self._save(auth_client, foreign_id, "year", "2099")

        assert resp.status_code == 404
        assert self._stored(db, foreign_id)[0] is None
        assert any(
            "Owner-scoped lookup found no such record" in record.getMessage()
            for record in caplog.records
        ), "the refused id left no audit trail"

    def test_a_dead_id_and_a_one_time_raise_are_404(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Only a row the rail LISTS can be saved: recurring, on an active profile.

        A one-time raise of the owner's own is not a rail row (it has no end
        year to answer, and the column forbids it one), so a pair naming it
        is refused exactly as a dead id is.
        """
        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_today,
        )
        one_time = SalaryRaise(
            salary_profile_id=profile_id,
            raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
            effective_date=date(effective + 1, 6, 1),
            percentage=Decimal("0.0200"), is_recurring=False,
        )
        db.session.add(one_time)
        db.session.commit()
        one_time_id = one_time.id

        assert self._save(auth_client, raise_id + 999, "none", "").status_code == 404
        assert self._save(auth_client, one_time_id, "year", str(effective + 1)).status_code == 404
        assert self._stored(db, one_time_id)[0] is None

    def test_an_archived_profiles_raise_is_not_a_rail_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The rail lists ACTIVE profiles' raises; an archived one's is a 404."""
        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_today,
        )
        db.session.get(SalaryProfile, profile_id).is_active = False
        db.session.commit()

        resp = self._save(auth_client, raise_id, "year", str(effective + 2))

        assert resp.status_code == 404
        assert self._stored(db, raise_id)[0] is None

    def test_an_id_that_is_not_an_id_is_refused_by_the_schema(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """``raise_end_mode_abc`` is a refused KEY, keyed to a control, never a 500.

        ``RowId`` refuses the key; marshmallow reports it under ``{"key":
        [...]}`` and the route re-keys that onto the mode control of the id
        as submitted, so the arm has a home even though no row renders it.
        """
        raise_id, _, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        resp = auth_client.post(
            "/retirement/settings",
            data={"raise_end_mode_abc": "none", "raise_end_year_abc": ""},
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 422
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        assert 'id="assumptions-panel"' in resp.data.decode()
        assert self._stored(db, raise_id)[0] is None

    def test_a_concurrent_edit_is_a_409_conflict_on_the_row(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch,
    ):
        """A stale race rolls back and re-renders the rail fresh at 409.

        The regeneration flushes under the optimistic lock (the raise, its
        template, its rows), so a concurrent edit surfaces there as
        ``StaleDataError``; the route answers the designed-conflict shape
        every fragment route uses -- the fragment, the message on the row,
        409 so htmx swaps it and the post-save reload does not fire -- and
        the write is rolled back.
        """
        # Pylint: import-outside-toplevel -- the route module and the
        # exception are needed only to simulate the race, as the salary
        # package's own stale-race case does.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel
        from sqlalchemy.orm.exc import StaleDataError  # pylint: disable=import-outside-toplevel

        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        def _raise_stale(_ctx, _profile):
            raise StaleDataError("simulated concurrent edit")

        monkeypatch.setattr(
            retirement_mod.salary_regeneration,
            "regenerate_salary_transactions", _raise_stale,
        )

        resp = self._save(auth_client, raise_id, "year", str(effective + 2))

        assert resp.status_code == 409
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        row = self._row(html, raise_id)
        assert "This raise was changed by another action while you were editing." in row
        assert "is-invalid" in re.search(r"<select[^>]*>", row).group(0)
        assert self._stored(db, raise_id)[0] is None, "the write survived the rollback"
        assert '<option value="none" selected>' in row, (
            "the conflict re-render did not state the FRESH row"
        )

    def test_a_database_failure_is_a_designed_500_on_the_row(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch, caplog,
    ):
        """The rail's twin of the salary package's ``handle_db_error`` arm.

        The service re-raises any non-stale ``SQLAlchemyError`` so the
        route's own handler reports it; an unhandled 500 here is a click
        that did nothing and said nothing, so the route rolls back and
        answers the fragment at 500 with the message on the row, the
        designed-fragment header set so htmx swaps it.
        """
        # Pylint: import-outside-toplevel -- the route module and the error
        # class are needed only to simulate the failure.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel
        from sqlalchemy.exc import DataError  # pylint: disable=import-outside-toplevel

        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        def _fail(_ctx, _profile):
            raise DataError("stmt", None, Exception("simulated NUMERIC overflow"))

        monkeypatch.setattr(
            retirement_mod.salary_regeneration, "regenerate_salary_transactions", _fail,
        )

        with caplog.at_level(logging.ERROR, logger="app.routes.retirement"):
            resp = self._save(auth_client, raise_id, "year", str(effective + 2))

        assert resp.status_code == 500
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        row = self._row(html, raise_id)
        assert "Failed to save this end year." in row
        assert self._stored(db, raise_id)[0] is None, "the write survived the rollback"
        assert any(
            "failed to save raise end years" in record.getMessage()
            and str(raise_id) in record.getMessage()
            for record in caplog.records
        )

    def test_the_retained_rows_are_told_to_the_owner(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch,
    ):
        """What the regeneration declined to touch is flashed, not dropped.

        The service RETURNS the retained ids (plan step R10-a, ruling
        **R-SAL24**); the route flashes them, and the reload the rail's
        success swap triggers is where the owner reads it.
        """
        # Pylint: import-outside-toplevel -- the route module is needed only
        # to stub the regeneration's answer.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel

        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)
        monkeypatch.setattr(
            retirement_mod.salary_regeneration,
            "regenerate_salary_transactions", lambda _ctx, _profile: [7],
        )

        resp = self._save(auth_client, raise_id, "year", str(effective + 2))

        assert resp.status_code == 200
        assert self._stored(db, raise_id)[0] == effective + 2
        with auth_client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("1 upcoming instance kept the value" in text for _, text in flashes)

    def test_the_saved_raises_own_profile_is_regenerated_and_no_other(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch,
    ):
        """Two profiles, one Save: the raise's OWN profile is regenerated.

        Every other case seeds one profile, so a regeneration that reached
        for ``salary_profiles[0]`` instead of the row's profile would pass
        them all while redrawing the wrong template's rows for an owner with
        two (an adversarial review of this step named the mutation).  The
        raise sits on the SECOND profile, and the regeneration is called for
        it alone.  Its observable was each template's ``default_amount``,
        which the regeneration re-stated, until plan step X-bp deleted the
        re-statement, so the call is counted, as
        :meth:`test_two_rows_posted_together_both_write` counts it.
        """
        # Pylint: import-outside-toplevel -- the route module is needed only
        # to count the regeneration's calls.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel

        # The first profile, with no raise, is what _seed_underfunded builds.
        _, effective, first_id = self._seed_saveable_raise(seed_user, db, seed_periods_today)
        first_raise = db.session.query(SalaryRaise).filter_by(
            salary_profile_id=first_id,
        ).one()
        db.session.delete(first_raise)
        second = _create_salary_profile(seed_user, db.session, name="Second Job")
        second_id = second.id
        row = make_recurring_raise(second_id, db.session, effective_date=date(effective, 1, 1))
        db.session.commit()
        raise_id = row.id
        real = retirement_mod.salary_regeneration.regenerate_salary_transactions
        calls = []

        def _counted(ctx, profile):
            calls.append(profile.id)
            return real(ctx, profile)

        monkeypatch.setattr(
            retirement_mod.salary_regeneration, "regenerate_salary_transactions", _counted,
        )

        resp = self._save(auth_client, raise_id, "year", str(effective))

        assert resp.status_code == 200
        assert self._stored(db, raise_id)[0] == effective
        assert first_id != second_id
        assert calls == [second_id], (
            "the regeneration ran for a profile other than the raise's own"
        )

    def test_a_non_htmx_save_writes_and_redirects_to_retirement(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The plain-form fallback writes the same column and lands on the page."""
        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        resp = self._save(auth_client, raise_id, "year", str(effective + 1), htmx=False)

        assert resp.status_code == 302
        assert "/retirement" in resp.headers["Location"]
        assert self._stored(db, raise_id)[0] == effective + 1

    def test_two_rows_posted_together_both_write(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch,
    ):
        """Every pair the POST carries is resolved and written.

        No rendered form posts two rows, but the arm is a loop over the pairs
        the schema gathered rather than a special case for one, so the general
        shape is what is graded: both rows written, and ONE regeneration for
        the one profile both raises sit on.
        """
        # Pylint: import-outside-toplevel -- the route module is needed only
        # to count the regeneration's calls.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel

        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_today,
        )
        other = make_recurring_raise(
            profile_id, db.session, effective_date=date(effective + 1, 1, 1),
        )
        db.session.commit()
        other_id = other.id
        real = retirement_mod.salary_regeneration.regenerate_salary_transactions
        calls = []

        def _counted(ctx, profile):
            calls.append(profile.id)
            return real(ctx, profile)

        monkeypatch.setattr(
            retirement_mod.salary_regeneration, "regenerate_salary_transactions", _counted,
        )

        resp = auth_client.post(
            "/retirement/settings",
            data={
                f"raise_end_mode_{raise_id}": "year",
                f"raise_end_year_{raise_id}": str(effective + 5),
                f"raise_end_mode_{other_id}": "year",
                f"raise_end_year_{other_id}": str(effective + 6),
            },
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        assert self._stored(db, raise_id)[0] == effective + 5
        assert self._stored(db, other_id)[0] == effective + 6
        assert calls == [profile_id]
