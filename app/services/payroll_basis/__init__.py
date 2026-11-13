"""The paycheck engine's owner-level input: a salary profile and its calendar.

Plan step **R-F16** (shipped at ``4258ce28``).
Its own module rather than a type inside
:mod:`app.services.paycheck_calculator`, which sits at the 1000-line ceiling
this step's own additions pushed it past -- and because nine modules outside
that one CONSTRUCT this value while only it computes a paycheck, so the input
has more consumers than the producer does.  Every one of the nine imports it
from HERE; an earlier draft of this sentence made the consumer argument while
all nine still reached the type through the engine, which is a claim its own
import graph refuted.

No Flask, no clock, no database access of its own.  What it reads off the
profile it is handed -- the raise rows, since plan step salary:R15-b each
deduction's recurrence rule, and since salary:X-av-3a the pay list -- it reads
as ATTRIBUTES the caller loaded (the engine's loader eager-loads all three;
``test_projection_inputs`` counts the
statements a walk issues and finds none), converting each to a value through
the reader that owns its vocabulary: :func:`~app.services.salary_raises
.terms_of` for a raise, :func:`~app.services.recurrence.recurrence_spec` for
a rule.  The profile is typed loosely on purpose (see the class).

The package
-----------

**A package since task plan#3576** (rulings **R-IR** and **plan#3544**: a
module an edit would take past pylint's 1,000-line ceiling is split by the
card making the edit).  The module stood at 990 lines, and that task's shared
paid-before rule and public read of the walk's raises took it past.
:mod:`._line_cadence` holds the deduction cadences' memo, :mod:`._values` the
walk's values and the helpers that price with them, and :mod:`._basis` the
class.  Both public names are re-exported below, so no caller's import moved.
"""
from ._basis import PayrollBasis
from ._values import RhythmChangeWithoutPay

__all__ = ["PayrollBasis", "RhythmChangeWithoutPay"]
