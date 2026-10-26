"""
Shekel Budget App -- Grid Route Package (Main Budget View)

The primary view: a spreadsheet-like grid where columns are pay periods and
rows are income/expense line items.  Supports HTMX partial swaps for inline
editing, balance refresh, and carry forward.

Split of the historical monolithic ``app/routes/grid.py`` into a package,
following the ``app/routes/transactions/``, ``app/routes/accounts/`` and
``app/routes/salary/`` precedent.  **The reason is the same one that split
``transactions.mutations`` at plan step X-f1c**: the module reached pylint's
1000-line ceiling (991 of 1000 at the split), so the next change to it could
only land by deleting documentation to satisfy a gate.  One ``grid_bp``
blueprint is shared across every sub-module; the declaration lives in
:mod:`app.routes.grid._bp` (cycle-break).  **Every URL, endpoint name and
module-level name is preserved verbatim from the pre-split file**, so no
``url_for`` call site, template, test or ``app/__init__.py`` import needed an
edit -- the split is a pure move and the suite proves it.

Module map:

* :mod:`app.routes.grid._bp` -- ``grid_bp`` declaration (leaf; cycle-break).
* :mod:`app.routes.grid._shared` -- the four producers the page and the
  partials BOTH read, which is what keeps a re-rendered fragment agreeing
  with the page it replaces (rulings R-K / R-AI / R-P).
* :mod:`app.routes.grid.page` -- ``GET /grid`` and its context builders, plus
  the ``POST /create-baseline`` repair door.
* :mod:`app.routes.grid.partials` -- the three self-refreshing HTMX
  fragments: the balance row, the subtotal rows, and the mobile This Period
  summary.
"""

# ``RowKey`` is re-exported from ``app.services.grid_view_service`` so
# existing test scaffolding that imports it from this package
# (``from app.routes.grid import RowKey`` in
# ``tests/test_routes/test_grid.py``) keeps working without an
# import-path migration.  The canonical definition lives in the
# service module per mobile-first v3 plan Commit 13 / D-B.
from app.services.grid_view_service import RowKey

# Re-export ``grid_bp`` from the leaf declaration module so consumers that
# ``from app.routes.grid import grid_bp`` (notably ``app/__init__.py`` at
# factory time) resolve without an edit.
from app.routes.grid._bp import grid_bp

# Import sub-modules for the side effect of registering their route
# decorators against ``grid_bp``.  The ``noqa`` markers suppress the
# unused-import / out-of-order-import warnings that would otherwise fire on
# what is, by design, a deferred-import side-effect registration.
from app.routes.grid import page  # noqa: F401, E402
from app.routes.grid import partials  # noqa: F401, E402

# ``_ACCRUAL_ROW_LABELS`` and ``_accrual_row_label`` are re-exported for the
# tests that reach for them by their pre-split path
# (``tests/test_routes/test_grid/test_summary_rows.py``); they are internals of
# the module above, named here only so the split moved no test.
#
# **``PLAN_WINDOW_PERIODS`` was re-exported beside them under the same
# sentence, and the sentence was false**: NO test ever imported it, measured
# during recurrence plan step R-F17's adversarial review.  It went with the
# constant when that step made the Plan tab's window a span of MONTHS the
# owner's cadence resolves; the replacement is deliberately not re-exported,
# because a re-export justified by a reader that does not exist is the shape
# this project deletes rather than renames.
from app.routes.grid._shared import (  # noqa: F401, E402
    _ACCRUAL_ROW_LABELS,
    _accrual_row_label,
)


__all__ = ["RowKey", "grid_bp"]
