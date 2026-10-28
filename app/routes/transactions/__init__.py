"""
Shekel Budget App -- Transaction Route Package

CRUD operations and status workflow for individual transactions; returns
HTMX fragments for inline editing in the grid.  Split of the historical
monolithic ``app/routes/transactions.py`` into a package of per-concern
modules, following the ``app/routes/accounts/`` and ``app/routes/salary/``
precedent.  One ``transactions_bp`` blueprint is shared across every
sub-module; the declaration lives in :mod:`app.routes.transactions._bp`
(cycle-break).  Each per-concern module imports the blueprint from ``_bp``
and registers its route decorators against it.  Every URL and endpoint
name is preserved verbatim from the pre-split file, so no ``url_for`` call
site, template, or ``app/__init__.py`` import needed an edit
(``app/__init__.py`` continues to import ``transactions_bp`` from this
package by the same name, re-exported below).

Module map:

* :mod:`app.routes.transactions._bp` -- ``transactions_bp`` declaration
  (leaf; cycle-break).
* :mod:`app.routes.transactions._helpers` -- shared Marshmallow schema
  singletons, the credit-payback unique-index constant, and the render /
  ownership / FK helpers; the ``RenderTarget`` bundle and the phone card
  they answer with live in :mod:`app.routes._mobile_card`, shared with the
  transfer routes.
* :mod:`app.routes.transactions.forms` -- read-only GET HTMX partials.
* :mod:`app.routes.transactions.create` -- create handlers.
* :mod:`app.routes.transactions.mutations` -- PATCH/DELETE edit + the
  mark-done / credit / cancel status workflow (co-located so their shared
  apply / commit / error-tail parallel code stays intra-file).  The three
  branches where those routes landed on a transfer SHADOW row and
  re-expressed the request as a transfer update (``_shadow_mutations``,
  split out at plan step X-f1c) are gone since leaf ``balance:X-bi-6-1``:
  the grid draws a transfer's leg off its parent and the leg's cell calls
  the transfer's own routes, and a shadow row is a 404 at
  :func:`~app.routes.transactions._helpers._get_owned_transaction`.
* :mod:`app.routes.transactions.carry_forward` -- carry-forward routes.
"""

# Re-export ``transactions_bp`` from the leaf declaration module so
# consumers that ``from app.routes.transactions import transactions_bp``
# (notably ``app/__init__.py`` at factory time) resolve without an edit.
from app.routes.transactions._bp import transactions_bp

# Import sub-modules for the side effect of registering their route
# decorators against ``transactions_bp``.  The ``noqa`` markers suppress
# the unused-import / out-of-order-import warnings that would otherwise
# fire on what is, by design, a deferred-import side-effect registration.
from app.routes.transactions import forms  # noqa: F401, E402
from app.routes.transactions import create  # noqa: F401, E402
from app.routes.transactions import mutations  # noqa: F401, E402
from app.routes.transactions import carry_forward  # noqa: F401, E402


__all__ = ["transactions_bp"]
