"""
Shekel Budget App -- Unified Spending Report Service Tests (S-P1, D7 rebuild)

Hand-confirmed tests for :mod:`app.services.spending_report_service`: the
category breakdown and its shares, window filtering, the estimate-surprises
kernel (capped list + net), the hero band (vs-prior / vs-average with
None-safety and per-window-type prior arithmetic), the trailing window
series (the chart / hero one-source identity, and the twelve windows it
is derived from), the window-over-window deltas (items, groups, and the
By-change rows with their zero-current rider), and the empty / no-account
contracts.  Every value assertion carries the arithmetic that produces it.

**The chart's windows are DERIVED off the owner's pay calendar** since plan
step C2-f3d; ``TestTheChartReadsTheDerivedOrdinal`` is that step's firing
control, and it fails on the ``period_index`` queries it replaced.

The privates these tests exercise moved into the package's submodules at plan
step X-au-c2b, when finding **N-270**'s 1000-line ceiling forced the split;
they are imported from where they live rather than re-exported, so the test
names the module that owns each rule.
"""
