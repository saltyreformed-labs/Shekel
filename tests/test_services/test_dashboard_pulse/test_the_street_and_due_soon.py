"""The street band's day-span and today's offset within the period, and the current period's
due-soon rows and their stations, one per calendar day.
"""

from datetime import date

from app.services import dashboard_service
from tests._test_helpers import last_covered_day, dashboard_section

from ._helpers import _CURRENT_IDX, _add_expense, _undated_transfer_out


# ── Street: period day-span and today's offset, shared basis ────────


class TestPulseStreet:
    """The street band's day-span and today's offset within the period.

    Basis (shared with the due-soon rows' ``day_offset``): the period
    start is day 0, so an event due on the start sits at 0, the period end
    sits at ``days_total``, and ``today_offset`` is days since the start.
    """

    def test_street_days_total_and_today_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """days_total spans the period; today_offset is days since the start.

        Current period 5: start 2026-03-13, end 2026-03-26; frozen today
        2026-03-20.
            days_total   = (2026-03-26 - 2026-03-13).days = 13.
            today_offset = (2026-03-20 - 2026-03-13).days = 7.
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            street = result["street"]
            assert street["days_total"] == 13
            assert street["today_offset"] == 7

    def test_street_shares_due_soon_basis(
        self, app, seed_user, seed_periods, db,
    ):
        """A due-soon row's day_offset shares the street's day-0 basis.

        A bill due on the period END date sits at day_offset == days_total
        (the period-end station's position), and a bill due on the period
        START sits at day_offset 0 -- proving the three numbers (start,
        today, end) ride one axis so the band's percentage math lines up.
        """
        with app.app_context():
            current = seed_periods[_CURRENT_IDX]
            _add_expense(
                db.session, seed_user, current,
                "Due on end", "10.00", due_date=last_covered_day(current),
            )
            _add_expense(
                db.session, seed_user, current,
                "Due on start", "20.00", due_date=current.start_date,
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            street = result["street"]
            offsets = {
                b["name"]: b["day_offset"] for b in result["due_soon"]
            }
            # Period-start event sits at day 0.
            assert offsets["Due on start"] == 0
            # Period-end event sits at days_total -- the period-end station.
            assert offsets["Due on end"] == street["days_total"]


# ── Due soon: current-period rows with day offset + undated flag ────


class TestPulseDueSoon:
    """The current period's due-soon rows for the street / mobile list."""

    def test_due_soon_only_current_period(
        self, app, seed_user, seed_periods, db,
    ):
        """Only the current period's unpaid rows appear (next period excluded).

        A bill in period 5 (current) and a bill in period 6 (next): only
        the current-period bill is in due_soon.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Current bill", "100.00", due_date=date(2026, 3, 22),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX + 1],
                "Next bill", "200.00", due_date=date(2026, 3, 30),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            names = [b["name"] for b in result["due_soon"]]
            assert names == ["Current bill"]

    def test_due_soon_day_offset_and_undated(
        self, app, seed_user, seed_periods, db,
    ):
        """Dated rows carry day_offset from period start; undated flagged.

        Current period 5 starts 2026-03-13.  A bill due 2026-03-22 has
        day_offset = (03-22 - 03-13).days = 9.  An undated transfer-out
        leg carries day_offset None and undated True, and sorts AFTER
        dated rows.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Dated bill", "100.00", due_date=date(2026, 3, 22),
            )
            _undated_transfer_out(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Undated jar", "50.00",
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            due_soon = result["due_soon"]
            # Dated first (chronological), undated last.
            assert [b["name"] for b in due_soon] == [
                "Dated bill", "Transfer to Undated jar",
            ]

            dated = due_soon[0]
            assert dated["undated"] is False
            # (2026-03-22 - 2026-03-13).days = 9.
            assert dated["day_offset"] == 9

            undated = due_soon[1]
            assert undated["undated"] is True
            assert undated["day_offset"] is None

    def test_due_soon_sorted_by_due_date(
        self, app, seed_user, seed_periods, db,
    ):
        """Dated due-soon rows sort by due_date ascending."""
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Late", "100.00", due_date=date(2026, 3, 25),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Early", "100.00", due_date=date(2026, 3, 15),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert [b["name"] for b in result["due_soon"]] == ["Early", "Late"]


# ── Due-soon stations: per-day grouping for the street axis ─────────


class TestPulseDueSoonStations:
    """The dated due-soon rows grouped into one station per calendar day.

    Bills sharing a due_date share a day_offset and so must collapse into a
    single street station (one dot, a stacked label) instead of overlapping
    on one point.  The visible cap is 3: a station shows up to three bills,
    then reports the rest as ``extra_count`` for a "+N more" line.
    """

    def test_same_day_bills_collapse_into_one_station(
        self, app, seed_user, seed_periods, db,
    ):
        """Four bills on one due date -> one station, three shown, one extra.

        Current period 5 starts 2026-03-13.  Four bills due 2026-03-15 share
        day_offset = (03-15 - 03-13).days = 2.  With the cap at 3, the
        station shows the first three by name and folds the fourth into
        extra_count.
        """
        with app.app_context():
            for name in ("A bill", "B bill", "C bill", "D bill"):
                _add_expense(
                    db.session, seed_user, seed_periods[_CURRENT_IDX],
                    name, "10.00", due_date=date(2026, 3, 15),
                )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            stations = result["due_soon_stations"]
            assert len(stations) == 1
            station = stations[0]
            assert station["day_offset"] == 2  # (03-15 - 03-13).days
            assert station["count"] == 4
            # Cap is 3: first three by name shown, fourth folded away.
            assert [b["name"] for b in station["visible_items"]] == [
                "A bill", "B bill", "C bill",
            ]
            assert station["extra_count"] == 1  # 4 - 3

    def test_distinct_days_form_separate_axis_ordered_stations(
        self, app, seed_user, seed_periods, db,
    ):
        """Bills on different due dates make one station each, in axis order.

        A bill due 2026-03-22 (day_offset 9) and one due 2026-03-15
        (day_offset 2) yield two stations ordered by day_offset [2, 9].
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Later", "10.00", due_date=date(2026, 3, 22),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Earlier", "10.00", due_date=date(2026, 3, 15),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            stations = result["due_soon_stations"]
            # day_offset 2 = (03-15 - 03-13); day_offset 9 = (03-22 - 03-13).
            assert [s["day_offset"] for s in stations] == [2, 9]
            assert [s["count"] for s in stations] == [1, 1]
            assert all(s["extra_count"] == 0 for s in stations)
            assert stations[0]["visible_items"][0]["name"] == "Earlier"
            assert stations[1]["visible_items"][0]["name"] == "Later"

    def test_single_bill_station_has_no_overflow(
        self, app, seed_user, seed_periods, db,
    ):
        """One dated bill -> a one-item station with extra_count 0."""
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Solo", "10.00", due_date=date(2026, 3, 18),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            stations = result["due_soon_stations"]
            assert len(stations) == 1
            assert stations[0]["count"] == 1
            assert len(stations[0]["visible_items"]) == 1
            assert stations[0]["extra_count"] == 0

    def test_station_carries_shared_days_until_due(
        self, app, seed_user, seed_periods, db,
    ):
        """The station's days_until_due is the bills' shared value.

        Two bills due 2026-03-22 with frozen today 2026-03-20 each have
        days_until_due = (03-22 - 03-20).days = 2; the station reports that
        single value (it drives the dot's overdue/soon/normal state).
        """
        with app.app_context():
            for name in ("First", "Second"):
                _add_expense(
                    db.session, seed_user, seed_periods[_CURRENT_IDX],
                    name, "10.00", due_date=date(2026, 3, 22),
                )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            station = result["due_soon_stations"][0]
            assert station["days_until_due"] == 2  # (03-22 - 03-20).days
            assert all(
                b["days_until_due"] == 2 for b in station["visible_items"]
            )

    def test_undated_bills_excluded_from_stations(
        self, app, seed_user, seed_periods, db,
    ):
        """Undated rows stay off the axis but remain in due_soon (shelf).

        An undated transfer-out leg has no day_offset, so it forms no
        station, but it still appears in the flat due_soon list the "anytime
        this period" shelf renders.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Dated", "10.00", due_date=date(2026, 3, 18),
            )
            _undated_transfer_out(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Undated jar", "10.00",
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            stations = result["due_soon_stations"]
            # Only the dated bill forms a station.
            assert len(stations) == 1
            assert stations[0]["visible_items"][0]["name"] == "Dated"
            # The undated leg is still on the flat list (shelf intact).
            undated = [b for b in result["due_soon"] if b["undated"]]
            assert [b["name"] for b in undated] == ["Transfer to Undated jar"]
