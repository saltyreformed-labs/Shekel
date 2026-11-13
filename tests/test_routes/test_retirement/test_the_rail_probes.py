"""The rail's end-year probes, and its region's one spelling."""

import re
from datetime import date
from decimal import Decimal

from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.utils.dates import display_today
from tests._repo_root import REPO_ROOT
from tests._test_helpers import make_recurring_raise

from ._helpers import _create_salary_profile, _TestTheRailProbesARaisesEndYearMembers


class TestTheRailProbesARaisesEndYear(_TestTheRailProbesARaisesEndYearMembers):
    """The rail's end-year pair is a WHAT-IF the readiness GET carries (S3-f-2b)."""

    def test_what_the_rail_emits_is_the_stored_plan(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The pre-filled controls, submitted verbatim, resolve to no what-if.

        Every rail row submits on every refresh, so a point that treated the
        pre-filled pair as an override would derive one plan twice and state a
        zero delta (row P57's shape).  The controls are read back off the
        rendered page rather than hand-written, so this grades the pair the
        browser actually sends.
        """
        raise_id, _ = self._seed_forever_raise(seed_user, db)
        html = auth_client.get("/retirement").data.decode()
        controls = self._rail_controls(html)
        assert controls == {
            f"raise_end_mode_{raise_id}": "none",
            f"raise_end_year_{raise_id}": "",
        }

        status, fragment = self._fragment(auth_client, controls)
        assert status == 200
        assert 'data-readiness="verdict"' in fragment
        assert 'data-readiness="deltas"' not in fragment, (
            "the rail's own pre-filled pair was read as a what-if"
        )

    def test_a_probed_end_year_moves_the_verdict_and_the_levers(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Ending the raise after its first year is a what-if with a delta.

        A shorter raise is a smaller final salary, so the income target and
        the required savings FALL and the funded ratio RISES: the delta line
        renders with a positive points change, and the lever card -- solved
        against the displayed what-if since plan step C2-f2d-4 -- comes back
        different from the stored plan's.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        _, stored = self._fragment(auth_client, {})
        status, probed = self._fragment(auth_client, {
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective),
        })
        assert status == 200
        assert 'data-readiness="deltas"' in probed
        points = re.search(r"Funded change: (-?[\d.]+) points", probed)
        assert points, "the delta line did not state the funded change"
        assert Decimal(points.group(1)) > 0, (
            "ending the raise early did not raise the funded ratio, so the "
            "probe did not reach the income target"
        )
        stored_lines = re.search(r'<div id="lever-outcomes".*?</div>', stored, re.S)
        probed_lines = re.search(r'<div id="lever-outcomes".*?</div>', probed, re.S)
        assert stored_lines and probed_lines
        assert stored_lines.group(0) != probed_lines.group(0), (
            "the lever card was solved at the stored raise set beside a "
            "verdict stated at the probed one"
        )

    def test_a_probe_on_a_raise_that_does_not_exist_is_refused(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A stale bookmark or a URL edit is a designed 422, not a silent no-op.

        The refusal is keyed to the stale id's mode control, which no rendered
        row carries: the rail comes back as the rows ARE -- the stale row
        gone, the live row clean -- and the sentence itself renders nowhere.
        A rail-level notice for a refusal that names no row is a known
        opening, not built here.
        """
        raise_id, _ = self._seed_forever_raise(seed_user, db)
        stale = raise_id + 999
        html = self._refused(auth_client, {
            f"raise_end_mode_{stale}": "none",
            f"raise_end_year_{stale}": "",
        })
        rows = self._rows(html)
        assert set(rows) == {str(raise_id)}
        assert "is-invalid" not in rows[str(raise_id)]
        assert "Not one of your recurring raises" not in html

    def test_a_probe_on_another_owners_raise_is_refused_and_discloses_nothing(
        self, auth_client, seed_user, seed_second_user, db, seed_periods_today,
    ):
        """Another owner's raise id is refused EXACTLY as a dead id is.

        The IDOR axis the dead-id case above cannot measure (an adversarial
        review of this step).  The probe is resolved against THIS owner's rows
        -- never by querying the submitted id -- so a foreign id has no row to
        be graded against, and the refusal is the constant not-found sentence.
        **The probe names a year the rule would ACCEPT for the foreign row**
        (a second adversarial review, at plan step S3-f-4, when the answer
        became the rail): a resolver that looked the row up by id would find
        it, accept the year and answer 200 with the card, so the 422 is the
        assertion that carries the control.  A year BEFORE the foreign
        effective year -- the case as first written -- is refused by such a
        resolver too, with "takes effect in <year>" keyed to a control no row
        renders, and every assertion on the rail passed with the IDOR in
        place; the JSON body this case read until S3-f-4 was what caught it.
        """
        self._seed_forever_raise(seed_user, db)
        foreign_profile = _create_salary_profile(seed_second_user, db.session)
        foreign_effective = display_today().year + 7
        foreign = make_recurring_raise(
            foreign_profile.id, db.session, effective_date=date(foreign_effective, 1, 1),
        )
        db.session.commit()

        html = self._refused(auth_client, {
            f"raise_end_mode_{foreign.id}": "year",
            f"raise_end_year_{foreign.id}": str(foreign_effective + 1),
        })
        assert str(foreign.id) not in self._rows(html), (
            "the rail rendered a row for another owner's raise"
        )
        # No row renders the foreign id and the refusal is keyed to its
        # control, so no message renders at all; a message here would be a
        # fact about the row.  (Checked on the messages rather than on the
        # whole body: the csrf tokens the rows carry are random text.)
        assert "invalid-feedback" not in html

    def test_a_year_before_the_raises_effective_year_is_refused(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The ONE end-year rule, applied against the ROW, answers the rail too.

        The salary form refuses this payload through ``RaiseCreateSchema``; the
        rail refuses it through ``plan_with`` with the same sentence, because
        both call ``salary_raises.end_year_of``.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        html = self._refused(auth_client, {
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
        })
        row = self._rows(html)[str(raise_id)]
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

    def test_a_mode_outside_the_vocabulary_is_refused_by_the_schema(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The field-tier half of the rule stays the schema's, as on the form.

        marshmallow's nested report is re-keyed onto the row's mode control,
        exactly as the Save re-keys it -- the readiness GET no longer emits
        that nested shape (ledger row SAL-548's second 422 shape).
        """
        raise_id, _ = self._seed_forever_raise(seed_user, db)
        html = self._refused(auth_client, {
            f"raise_end_mode_{raise_id}": "forever",
        })
        row = self._rows(html)[str(raise_id)]
        select = re.search(r"<select[^>]*>", row).group(0)
        assert "is-invalid" in select
        assert "Must be one of: year, none." in row
        assert "raise_probes" not in html

    def test_a_refused_probe_echoes_the_swr_what_if_and_every_other_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The rail comes back stating what is ON SCREEN, not the stored plan.

        Every rail what-if rides on every refresh, so the refusal echoes the
        query string: the other raise row's pair, and the SWR what-if -- which
        arrives under the GET's name ``swr`` and must land in the SWR box AND
        its mirror, or the next refresh would carry the stored rate under a
        box stating 3.5.  The date row, which no refresh carries, states its
        stored value.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id).one()
        )
        other = make_recurring_raise(
            profile.id, db.session, effective_date=date(effective + 1, 1, 1),
            terminal_year=effective + 4,
        )
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.safe_withdrawal_rate = Decimal("0.0400")
        planned = settings.planned_retirement_date
        db.session.commit()
        other_id = other.id

        # The other row's probe DIFFERS from its stored year, so a rail that
        # stated the stored value instead of the echo would fail here (an
        # adversarial review: probing the stored year measured nothing).
        html = self._refused(auth_client, {
            "swr": "3.5",
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
            f"raise_end_mode_{other_id}": "year",
            f"raise_end_year_{other_id}": str(effective + 3),
        })
        rows = self._rows(html)
        assert "is-invalid" in rows[str(raise_id)]
        other_row = rows[str(other_id)]
        assert "is-invalid" not in other_row
        assert '<option value="year" selected>' in other_row
        assert f'value="{effective + 3}"' in other_row, "the other row's what-if was not echoed"
        assert f'value="{effective + 4}"' not in other_row
        swr_box = re.search(r'<input[^>]*name="safe_withdrawal_rate"[^>]*>', html).group(0)
        assert 'value="3.5"' in swr_box, "the SWR what-if was reset to the stored rate"
        assert "is-invalid" not in swr_box
        mirror = re.search(r'<input[^>]*id="swr-whatif-mirror"[^>]*>', html).group(0)
        assert 'value="3.5"' in mirror, "the mirror lost the what-if the box states"
        date_input = re.search(r'<input[^>]*name="planned_retirement_date"[^>]*>', html).group(0)
        assert f'value="{planned.isoformat()}"' in date_input

    def test_no_swr_what_if_leaves_the_box_on_its_stored_rate(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An untouched mirror submits ``swr=`` -- no what-if -- and echoes nothing.

        The mirror starts empty and every refresh carries it, so the empty
        string is the ordinary case; a rail that echoed it would blank the
        SWR box on every refused probe.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.safe_withdrawal_rate = Decimal("0.0350")
        db.session.commit()

        html = self._refused(auth_client, {
            "swr": "",
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
        })
        swr_box = re.search(r'<input[^>]*name="safe_withdrawal_rate"[^>]*>', html).group(0)
        assert 'value="3.50"' in swr_box
        mirror = re.search(r'<input[^>]*id="swr-whatif-mirror"[^>]*>', html).group(0)
        assert 'value=""' in mirror


class TestTheRailRegionIsSpelledOnce:
    """The rail's region and the refresh trigger are named in four files.

    ``dashboard.html`` renders ``#assumptions-region`` and the
    ``#readiness-refresh`` trigger; every rail Save form targets the region;
    ``retirement_controls.js`` watches the region for the post-save reload
    and aborts the trigger's request on a keystroke; and the readiness GET
    retargets its refusal at the region (plan step salary:S3-f-4, ruling
    **R-SAL33**).  No pytest renders htmx, so a rename in one file passes
    every route test and breaks the page: this is the gate an adversarial
    review of that step asked for, in the shape of
    ``test_header_name_matches_js_listener``.
    """

    @staticmethod
    def _source(relative):
        return (REPO_ROOT / relative).read_text()

    def test_the_rail_region_selector_matches_every_spelling(self):
        """The route's selector is the dashboard's id, the forms' target, the JS's guard."""
        # pylint: disable=import-outside-toplevel
        from app.routes.retirement import _RAIL_REGION

        assert _RAIL_REGION == "#assumptions-region"
        dashboard = self._source("app/templates/retirement/dashboard.html")
        assert 'id="assumptions-region"' in dashboard
        rail = self._source("app/templates/retirement/_assumptions.html")
        targets = re.findall(r'hx-target="([^"]+)"', rail)
        assert targets and set(targets) == {_RAIL_REGION}, targets
        controls = self._source("app/static/js/retirement_controls.js")
        assert 'event.target.id !== "assumptions-region"' in controls

    def test_the_refresh_trigger_id_matches_the_abort_handle(self):
        """The JS aborts the request of the element the dashboard renders."""
        dashboard = self._source("app/templates/retirement/dashboard.html")
        controls = self._source("app/static/js/retirement_controls.js")
        assert 'id="readiness-refresh"' in dashboard
        assert 'hx-sync="this:replace"' in dashboard
        assert 'document.getElementById("readiness-refresh")' in controls
