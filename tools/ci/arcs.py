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

Adding an arc is one entry here.  The tracker's labels follow on the next
``python -m tools.quill.setup_tracker --apply``.
"""
from __future__ import annotations

from pathlib import Path

#: The repository root (``tools/ci/arcs.py`` -> its root).
REPO = Path(__file__).resolve().parents[2]

#: The arcs, by the slug each one's tracker label carries.  Their order is the
#: tracker's label order, which assigns each label its colour.
ARCS = ("balance", "recurrence", "pay_calendar", "credit_card", "bank_import", "salary")
