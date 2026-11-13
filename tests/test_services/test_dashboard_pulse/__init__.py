"""
Shekel Budget App -- Dashboard Pulse / Tracks Producer Tests (Loop B B-1)

Tests for ``dashboard_service._pulse`` -- the additive producers behind the
Terminal Road dashboard rebuild:

  * ``compute_pulse_section`` and its helpers: the as-of-today hero
    (balance / staleness / next-paycheck date), the 13-period projected
    end-balance chart + threshold, the full-horizon trough, the still-due
    totals on the locked B4 bases, and the current period's due-soon rows.
  * ``compute_tracks_section`` and ``_track_goal_datum``: the savings-goal
    metro tracks (trajectory passthrough) and the debt track (the debt
    summary, which since plan step X-u carries the principal-paid fraction
    that a second producer used to compute).

The module-level autouse fixture (``test_services/conftest.py``) freezes
``date.today()`` to 2026-03-20, which falls in ``seed_periods[5]`` (the
period 2026-03-13 .. 2026-03-26).  So with ``seed_periods``:

  * the current period is index 5,
  * the forward horizon is periods 5..9 (5 periods),
  * the next paycheck date is ``seed_periods[6].start_date`` (2026-03-27).

Every dollar assertion shows its arithmetic; Decimals are constructed
from strings per the testing standards.
"""
