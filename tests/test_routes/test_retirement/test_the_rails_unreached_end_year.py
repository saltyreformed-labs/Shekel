"""
Shekel Budget App -- the /retirement rail's end-year Save refuses a raise no
paycheck gets (task plan#3500).

Ruling **plan#3492** (6A): the Save refuses an end year that leaves a yearly
raise reaching no paycheck, under that raise's end-year control, in the
page's own words.  One rule for every door that writes a raise: the salary
form's add and edit refuse the same state since task plan#3169.  The rail's
probe, a what-if, refuses nothing new: it writes nothing.

**The ruling's made-up case**: 3% every June 1 since 2024, no end year, and
the pay recorded from 2026-01-02, whose paycheck pays the period 2025-12-14
through 2025-12-27 (it ends six days before the payday).  Recorded pay
includes every raise dated on or before 2025-12-14 (ruling plan#3091), so
ending the raise after 2025 leaves its two applications, 2024-06-01 and
2025-06-01, both in that pay and no paycheck priced under either.  Ending it
after 2026 adds Monday 2026-06-01, which the 2026-06-19 paycheck (paying
2026-05-31 through 2026-06-13) pays from that day on, and every paycheck
after it.

It sits in the retirement routes' package beside the rail's save and probe
tests (task plan#3776), whose seeding and reading helpers it shares from the
package's ``_helpers.py``: the classes' member mixins, which are not ``Test*``
classes, so binding them here collects nothing a second time.
"""

import re
from datetime import date
from decimal import Decimal

import pytest

from app.models.salary_profile import SalaryProfile
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from tests._seed_owner_fixtures import log_in_seed_user
from tests._test_helpers import freeze_today, make_recurring_raise

from ._helpers import (
    _seed_underfunded,
    _TestTheRailProbesARaisesEndYearMembers,
    _TestTheRailSavesARaisesEndYearMembers,
)

#: The rail-save and rail-probe tests' own reading helpers.
_SAVES = _TestTheRailSavesARaisesEndYearMembers
_PROBES = _TestTheRailProbesARaisesEndYearMembers

#: Ruling plan#3492's words for the made-up case: the year saved, and the
#: payday of the entry holding the raise's last application.
_REFUSED = (
    "Every year of this raise through 2025 is already in your pay from "
    "2026-01-02, so it changes no paycheck. Remove it on the salary page instead."
)


def _seed_the_rulings_case(auth_client, seed_user, db, monkeypatch) -> int:
    """Seed the ruling's raise on a profile paid from 2026-01-02; return the raise id.

    Today is pinned inside the 52 paydays from 2026-01-02 (``seed_periods_52``)
    so the page renders a current paycheck on every day the case runs, and
    the client signs in again on the moved clock (the idle timeout reads it).
    The profile's one pay entry is on the first saved payday, 2026-01-02
    (``start_test_pay_list``'s default).
    """
    freeze_today(monkeypatch, date(2026, 3, 20))
    log_in_seed_user(auth_client)
    _seed_underfunded(seed_user, db.session)
    profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=seed_user["user"].id).one()
    )
    row = make_recurring_raise(
        profile.id, db.session, effective_date=date(2024, 6, 1),
        percentage=Decimal("0.0300"),
    )
    db.session.commit()
    return row.id


def _the_rows_controls_ending_after(auth_client, raise_id: int, year: int) -> dict:
    """The raise row's controls as the rail renders them, the end year set to *year*.

    Read off the dashboard, so the Save posts what the row's form submits,
    with the owner's two edits: "ends after" chosen and the year typed.
    """
    # Pylint: protected-access -- the reader is the rail-probe tests' own,
    # shared rather than spelled a second time.
    controls = _PROBES._rail_controls(  # pylint: disable=protected-access
        auth_client.get("/retirement").data.decode(),
    )
    assert controls == {
        f"raise_end_mode_{raise_id}": "none",
        f"raise_end_year_{raise_id}": "",
    }
    controls[f"raise_end_mode_{raise_id}"] = "year"
    controls[f"raise_end_year_{raise_id}"] = str(year)
    return controls


@pytest.mark.usefixtures("seed_periods_52")
class TestTheRailRefusesAnEndYearNoPaycheckGets:
    """The Save refuses an end year recorded pay wholly holds, and saves one reaching pay."""

    # Pylint: protected-access -- the reading helpers are the rail-save
    # class's own, shared rather than spelled a second time.
    # pylint: disable=protected-access

    def test_an_end_year_recorded_pay_holds_is_refused_on_the_year_control(
        self, auth_client, seed_user, db, monkeypatch,
    ):
        """Ending the raise after 2025 is a designed 422 in the ruling's words, nothing written.

        The message renders on the row, the YEAR box marked invalid and the
        mode select not, the posted pair echoed, and nothing written: the
        row's end year and its optimistic-lock counter are as they were.
        """
        raise_id = _seed_the_rulings_case(auth_client, seed_user, db, monkeypatch)
        before = _SAVES._stored(db, raise_id)

        resp = auth_client.post(
            "/retirement/settings",
            data=_the_rows_controls_ending_after(auth_client, raise_id, 2025),
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 422
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        row = _SAVES._row(resp.data.decode(), raise_id)
        assert _REFUSED in row
        year_input = re.search(
            rf'<input[^>]*name="raise_end_year_{raise_id}"[^>]*>', row, re.S,
        ).group(0)
        assert "is-invalid" in year_input, "the refusal is not on the year control"
        assert 'value="2025"' in year_input, "the refused year was not echoed"
        select = re.search(r"<select[^>]*>", row).group(0)
        assert "is-invalid" not in select, "the refusal landed on the mode control"
        assert '<option value="year" selected>' in row, "the posted mode was not echoed"
        assert _SAVES._stored(db, raise_id) == before, "a refused end year was written"

    def test_an_end_year_still_reaching_pay_is_saved(
        self, auth_client, seed_user, db, monkeypatch,
    ):
        """Ending it after 2026 is saved: 2026-06-01 lands after the entry's paycheck opens.

        The rail comes back clean at 200, stating the saved year.
        """
        raise_id = _seed_the_rulings_case(auth_client, seed_user, db, monkeypatch)

        resp = auth_client.post(
            "/retirement/settings",
            data=_the_rows_controls_ending_after(auth_client, raise_id, 2026),
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        assert _SAVES._stored(db, raise_id)[0] == 2026
        row = _SAVES._row(resp.data.decode(), raise_id)
        assert "is-invalid" not in row
        assert 'value="2026"' in row

    def test_the_probe_at_the_same_end_year_is_not_refused(
        self, auth_client, seed_user, db, monkeypatch,
    ):
        """The what-if at "ends after 2025" draws the card: a probe writes nothing.

        The same pair the refused Save posts, sent as the rail's what-if
        sends it, answers the readiness card at 200 rather than the rail at
        422, and the row is unchanged.
        """
        raise_id = _seed_the_rulings_case(auth_client, seed_user, db, monkeypatch)
        before = _SAVES._stored(db, raise_id)

        resp = auth_client.get(
            "/retirement/readiness",
            query_string=_the_rows_controls_ending_after(auth_client, raise_id, 2025),
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        assert _REFUSED not in resp.data.decode()
        assert _SAVES._stored(db, raise_id) == before
