"""
Unit tests for ``app.routes._recurrence_conflict_chooser`` (Loop B, P3).

The chooser's own contracts -- parsing the per-instance keep/use decisions a
submit carries, and dispatching them without ever touching a row outside the
raised conflict set.  Split out of ``test_recurrence_form_helpers`` alongside
the module they cover (plan step R2e-1).

The route-level behaviour of the regeneration these helpers sit under lives in
``test_recurrence_clear.py`` (a cleared recurrence) and in the template /
transfer CRUD suites (an amount change).
"""
from decimal import Decimal

import pytest

from werkzeug.datastructures import MultiDict

from app.exceptions import RecurrenceConflict
from app.routes._recurrence_conflict_chooser import (
    RecurrenceConflictKind,
    apply_conflict_decisions,
    parse_conflict_decisions,
)


class TestParseConflictDecisions:
    """The chooser's per-instance keep/use parser (Loop B, P3)."""

    def test_no_marker_returns_none(self):
        """A first-time edit submit (no chooser marker) parses to None, so the
        route knows to render the chooser rather than resolve."""
        form = MultiDict({"default_amount": "10.00"})
        assert parse_conflict_decisions(form) is None

    def test_marker_parses_valid_decisions_and_drops_malformed(self):
        """With the marker, valid ``conflict_decision_<id>`` pairs parse; a
        non-integer id or an unrecognized value is dropped (the surviving ids
        are re-checked against the real conflict set at apply time)."""
        form = MultiDict({
            "conflict_apply": "1",
            "conflict_decision_5": "keep",
            "conflict_decision_9": "use",
            "conflict_decision_x": "use",     # non-integer id -> dropped
            "conflict_decision_7": "bogus",   # invalid value -> dropped
            "default_amount": "10.00",
        })
        assert parse_conflict_decisions(form) == {5: "keep", 9: "use"}

    def test_marker_without_decisions_is_empty_map(self):
        """The marker with no decision fields parses to an empty map (an Apply
        that resolves nothing), distinct from None (render the chooser)."""
        assert parse_conflict_decisions(MultiDict({"conflict_apply": "1"})) == {}


#: The read pass the dispatch hands through to the resolver, which the spy
#: records without reading: a sentinel, since no case here builds a pass.
_PASS = object()


class TestApplyConflictDecisions:
    """The chooser's resolution dispatch (Loop B, P3)."""

    @staticmethod
    def _spy_kind(calls):
        """A :class:`RecurrenceConflictKind` whose resolver only records.

        ``**kwargs`` rather than named parameters, because what the case below
        asserts is that NOTHING beyond the three positional arguments is passed
        -- a named default would make "omitted" and "passed as None" the same
        observation, which is the shape that lets a dispatch drift without any
        test noticing.

        **It took a ``use_states_a_figure`` until plan step X-au-f** (ruling
        **R-JD**).  Both kinds' generated rows store no figure now, so "use"
        means the same thing on both tables -- hand this row back to its
        definition -- and the field, its branch and the resolver's
        ``new_amount`` went together.
        """
        def fake_resolve(ids, action, ctx, **kwargs):
            calls.append({
                "ids": list(ids), "action": action,
                "ctx": ctx, "kwargs": dict(kwargs),
            })

        return RecurrenceConflictKind(
            # The kind's amount RULE, where this was the amount column's NAME
            # until plan step X-au-c2b: the chooser renders the figure as money
            # and a derived row carries no column to read.  Unused by the
            # dispatch under test, so it is the identity here.
            model=None, resolve_amount=lambda row: row, regenerate_fn=None,
            preview_fn=None, resolve_fn=fake_resolve, update_endpoint="x",
        )

    def test_ignores_ids_outside_conflict_set(self):
        """An id absent from the raised conflict set never reaches
        ``resolve_fn``, so the chooser cannot be used to mutate an arbitrary
        owned row.  ``resolve_fn`` is a spy here; the partition into use/keep
        must exclude the out-of-set id (999).

        **It was parametrised over both kinds until plan step X-au-f**, because
        the two dispatched to resolvers with DIFFERENT signatures and running
        one shape left the other's arm ungraded.  There is one arm now
        (ruling **R-JD**), so the parametrisation had nothing left to vary.
        """
        calls = []
        kind = self._spy_kind(calls)
        conflict = RecurrenceConflict(overridden=[10], deleted=[20])
        decisions = {10: "keep", 20: "use", 999: "use"}  # 999 not in the set

        apply_conflict_decisions(
            kind=kind, conflict=conflict, decisions=decisions, ctx=_PASS,
        )

        update = next(c for c in calls if c["action"] == "update")
        keep = next(c for c in calls if c["action"] == "keep")
        assert update["ids"] == [20]            # only the in-set "use"
        assert keep["ids"] == [10]              # only the in-set "keep"
        assert 999 not in update["ids"]
        assert 999 not in keep["ids"]

    def test_USE_carries_no_figure_for_either_kind(self):
        """"Use" hands a row BACK to its definition and states no figure.

        **This replaces ``test_the_kind_decides_whether_use_carries_a_figure``,
        whose subject plan step X-au-f deleted** (ruling **R-JD**).  That case
        asserted a transfer's resolver took ``new_amount=`` and a transaction's
        did not; neither does now, because neither generated row stores a
        figure and the definition's own effective-dated series prices both on
        the row's due date.

        It asserts the KWARGS rather than a value, exactly as its predecessor
        did and for the same reason: the claim is that the argument is ABSENT,
        which an assertion on a value could not tell from ``None``.  A resolver
        that grew the parameter back would fail here rather than silently
        re-storing a figure on every "use".
        """
        calls = []
        kind = self._spy_kind(calls)
        conflict = RecurrenceConflict(overridden=[10], deleted=[20])

        apply_conflict_decisions(
            kind=kind, conflict=conflict, decisions={10: "use", 20: "use"},
            ctx=_PASS,
        )

        update = next(c for c in calls if c["action"] == "update")
        assert update["ids"] == [10, 20]
        assert update["kwargs"] == {}
        # "Keep" never carries a figure for either kind.
        assert next(c for c in calls if c["action"] == "keep")["kwargs"] == {}
