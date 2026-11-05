"""The arcs and the repository root: each one home (rule 14).

The tracker tool (``tools/quill``) makes one label per arc and accepts an arc only
from :data:`ARCS`; the other tools find the checkout by :data:`REPO`.  Until step
X-cx's L4 the arcs were spelled twice, here (then in the plan gate's
``_registry.py``) and as the tracker tool's own list, with nothing holding the two
equal.

Each arc's document, the heading of its steps section and how a message named that
section sat beside each arc until step X-cx's S3b: the plan gate graded the
documents and X-cx's migration lifted each step's entry from them, and S3b deleted
both, so no reader of the three is left.  Each arc's reasoning document stays in the
repository.

Adding an arc is one entry here, its slug and its label's colour (:data:`ARC_COLORS`).  The
tracker's labels follow on the next ``python -m tools.quill.setup_tracker --apply``.
"""
from __future__ import annotations

from pathlib import Path

#: The repository root (``tools/ci/arcs.py`` -> its root).
REPO = Path(__file__).resolve().parents[2]

#: Each arc, by the slug its tracker label carries, with that label's colour beside it, so
#: no arc's colour hangs on its place in a list (ruling plan#2219: each label's colour is
#: its own).  ``tooling`` is ruling plan#2179's, so ``balance`` is no catch-all; its scope is
#: ``docs/plans/implementation_plan_tooling.md``'s, and its colour the developer's.
ARC_COLORS = {
    "balance": "0e8a16",
    "recurrence": "1d76db",
    "pay_calendar": "5319e7",
    "credit_card": "d93f0b",
    "bank_import": "006b75",
    "salary": "c5a100",
    "tooling": "793625",
}

#: The arcs, in the tracker's label order.
ARCS = tuple(ARC_COLORS)
