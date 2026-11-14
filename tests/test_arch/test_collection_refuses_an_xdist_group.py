"""Architecture test: collection refuses a test that carries an ``xdist_group``.

plan#2954 deleted every ``xdist_group`` and ``--dist=loadgroup``, so the suite
runs under xdist's default scheduling and the marker would keep nothing
together: a test that relied on it would fail only when the scheduler split it
from its partner, the failure pytest 9.2.0 caused.  Ruling plan#3768 made
collection refuse the marker, and ruling plan#3814 made the refusal a collection
error: ``tests/conftest.py``'s ``pytest_make_collect_report`` wrapper fails the
collector whose report holds a test or class carrying it.

What this file grades: the wrapper's body, driven as pytest drives a wrapper,
over real ``pytest.CollectReport`` objects holding stand-in nodes; and that the
real collection runs it.  It runs no nested pytest, so how the error reads under
xdist and in each shard rests on the measurement the wrapper's docstring dates.
"""

import pytest

from tests import conftest

_COLLECTOR = "tests/test_x.py"


class _Node:
    """A collected test or class as the refusal reads it: a node id and its marks.

    ``get_closest_marker`` is all the refusal asks of a node, so a test of it
    needs no collection and adds no marker to a real node.
    """

    def __init__(self, nodeid, *decorators):
        """Carry each of *decorators*' marks, e.g. ``pytest.mark.xdist_group("g")``."""
        self.nodeid = nodeid
        self._marks = [decorator.mark for decorator in decorators]

    def get_closest_marker(self, name):
        """The first carried mark named *name*, or ``None``, as a node answers."""
        return next((mark for mark in self._marks if mark.name == name), None)


def _collected(report):
    """Run conftest's wrapper around *report* as pytest does, and return its result.

    pytest runs a ``wrapper=True`` hook to its ``yield``, sends the inner
    result in, and takes what the generator returns.
    """
    wrapper = conftest.pytest_make_collect_report()
    next(wrapper)
    with pytest.raises(StopIteration) as stop:
        wrapper.send(report)
    return stop.value.value


def _passed(*nodes):
    """A passed report of ``_COLLECTOR`` holding *nodes*."""
    return pytest.CollectReport(_COLLECTOR, "passed", None, list(nodes))


class TestACollectorHoldingAGroupFails:
    """A marked test or class fails its collector's report, named."""

    def test_the_report_fails_naming_each_marked_node_and_no_other(self):
        """A marked class and a marked test are named; the plain test is not.

        The failed report keeps the collector's node id and holds no nodes, so
        pytest collects nothing beneath it.
        """
        report = _collected(_passed(
            _Node(f"{_COLLECTOR}::test_plain"),
            _Node(f"{_COLLECTOR}::TestCls", pytest.mark.xdist_group("a")),
            _Node(f"{_COLLECTOR}::test_fn", pytest.mark.xdist_group("b")),
        ))
        assert report.failed
        assert report.nodeid == _COLLECTOR
        assert report.result == []
        assert f"\n  {_COLLECTOR}::TestCls\n  {_COLLECTOR}::test_fn\n" in report.longrepr
        assert "test_plain" not in report.longrepr

    def test_the_message_says_where_each_kind_of_state_belongs(self):
        """The error says the suite has no groups and where a test's state goes."""
        report = _collected(_passed(_Node(f"{_COLLECTOR}::t", pytest.mark.xdist_group("g"))))
        assert report.longrepr.startswith("The suite has no xdist_group (plan#2954)")
        assert "its rows in its own clone of the database" in report.longrepr
        assert "its process-wide state in a session-scoped fixture" in report.longrepr

    def test_the_failed_report_keeps_what_collection_captured(self):
        """Output captured while collecting still shows beside the error."""
        passed = _passed(_Node(f"{_COLLECTOR}::t", pytest.mark.xdist_group("g")))
        passed.sections.append(("Captured stdout", "printed on import"))
        refused = _collected(passed)
        assert refused.failed
        assert refused.sections == [("Captured stdout", "printed on import")]


class TestAnyOtherReportIsUntouched:
    """The wrapper returns pytest's own report when nothing it holds is marked."""

    def test_a_report_with_no_marked_node_is_returned_as_is(self):
        """A plain test, and one with some other mark, leave the report alone."""
        report = _passed(
            _Node(f"{_COLLECTOR}::test_plain"),
            _Node(f"{_COLLECTOR}::test_slow", pytest.mark.slow_fixture),
        )
        assert _collected(report) is report

    def test_a_report_that_failed_for_its_own_reason_keeps_its_error(self):
        """A file that failed to import keeps its own error, not ours."""
        report = pytest.CollectReport(_COLLECTOR, "failed", "ImportError: no module", None)
        assert _collected(report) is report


def test_the_real_collection_runs_the_refusal(request):
    """This conftest's function is registered once, as a wrapper of the hook.

    The tests above drive the function directly, so they would pass with it
    placed where pytest never loads it, or registered as a plain hook, which
    hands pytest the generator itself as the report.
    """
    hook = request.config.pluginmanager.hook.pytest_make_collect_report
    ours = [impl for impl in hook.get_hookimpls() if impl.plugin is conftest]
    assert len(ours) == 1
    assert ours[0].wrapper
