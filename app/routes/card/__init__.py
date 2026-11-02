"""
Shekel Budget App -- Card Route Package

The doors a revolving credit account (a Credit Card) has that no other account
kind does, under ONE ``card_bp`` blueprint shared across every sub-module; the
declaration lives in :mod:`app.routes.card._bp` (cycle-break), and each
per-concern module imports it from there and registers its routes against it
-- the ``app/routes/loan/`` package's shape.  Born at plan step
**credit_card:CC-2** with one door, the card's terms; CC-3 added the two APR
doors (set by date, remove); plan#2331 the terms' remove door; CC-11 adds the
card cockpit.

The card's PAGE is not here yet: until CC-11 a card renders on the shared cash
detail page (``accounts.cash_detail``), which hosts the terms form this
package's door receives (developer ruling **R-CC25**, 2026-09-18).

Module map:

* :mod:`app.routes.card._bp` -- ``card_bp`` declaration (leaf; cycle-break).
* :mod:`app.routes.card._helpers` -- the card gates every door in this
  package loads its account through (the kind gate, and the kind-plus-terms
  gate a feature of a configured card takes).
* :mod:`app.routes.card.terms` -- the terms doors (save by date, remove;
  ``budget.credit_card_params``).
* :mod:`app.routes.card.apr` -- the APR doors (``budget.rate_history``).
"""

# Re-export ``card_bp`` from the leaf declaration module so ``app/__init__.py``
# resolves ``app.routes.card.card_bp`` at factory time.
from app.routes.card._bp import card_bp

# Import sub-modules for the side effect of registering their route decorators
# against ``card_bp``.  The ``noqa`` markers suppress the unused-import /
# out-of-order-import warnings that would otherwise fire on what is, by design,
# a deferred-import side-effect registration.
from app.routes.card import apr, terms  # noqa: F401, E402


__all__ = ["card_bp"]
