"""
Shekel Budget App -- Accounts Route Package

Split of the historical monolithic ``app/routes/accounts.py`` into a
package of per-sub-domain modules.  Commit 21 of the financial-
calculation audit follow-up remediation (F-1).

Direction: Option A (single blueprint, file-split by import).  One
``accounts_bp`` blueprint is shared across every sub-module; the
declaration lives in :mod:`app.routes.accounts._bp` (F-25 fix; see
that module's docstring for why the blueprint moved out of this init).
The per-sub-domain modules (``crud``, ``anchor``, ``types``,
``detail``) import the blueprint from ``_bp`` and register their
decorators against it.  Every URL is preserved verbatim from the
pre-split file; no ``url_for`` call site needed an edit and
``app/__init__.py`` continues to import ``accounts_bp`` from this
package by the same name (re-exported below).

Module map:

* :mod:`app.routes.accounts._bp` -- ``accounts_bp`` blueprint
  declaration (leaf module; F-25 cycle-break).
* :mod:`app.routes.accounts.crud` -- Account CRUD endpoints
  (``list_accounts``, ``new_account``, ``create_account``,
  ``edit_account``, ``update_account``).
* :mod:`app.routes.accounts.lifecycle` -- An account's archive, unarchive
  and hard-delete (``archive_account``, ``unarchive_account``,
  ``hard_delete_account``), split from ``crud`` at task plan#3697 as the
  transfer routes split theirs (ruling R-R84).
* :mod:`app.routes.accounts.anchor` -- The grid and Net Worth Cockpit
  anchor true-up endpoints, consumers of
  :func:`app.services.anchor_service.apply_anchor_true_up`.
* :mod:`app.routes.accounts.reconcile` -- The outstanding-purchase panel
  ("which of these has your bank taken?") its context builder and both
  mounts share.  Split out of ``anchor`` when that module reached the
  1000-line ceiling; the boundary is WRITE DOOR against WHAT IS STILL
  OUTSTANDING, and it took two cross-module private names public with it
  (finding N-33's shape).
* :mod:`app.routes.accounts.difference` -- The true-up form's read-only
  DIFFERENCE preview (plan step X-f2-a, ruling R-EU): what the records produce
  for the day the form names, what was typed, and the gap.  Split out of
  ``anchor`` when plan step X-f2-b's acknowledgement re-key pushed that module
  past the 1000-line ceiling; the boundary is WRITE DOOR against a PREVIEW of
  a save that has not happened, and it took two names public with it.
* :mod:`app.routes.accounts.history` -- The Balance history card (plan step
  X-f2-b, ruling R-EV): every balance the user has told an account it held,
  beside what its records produced just before each one.  A third subject
  beside ``detail``'s page and ``anchor``'s write door, split for the reason
  ``reconcile`` was.
* :mod:`app.routes.accounts.opening` -- What the books OPENED with (plan
  step X-f3c-2b-2a, ruling **R-HG**): the card the shared account edit page
  renders and the POST that appends a restatement.  A write door for an
  append-only MONEY fact, split from ``crud`` for the reason ``history`` was
  split from ``detail`` -- ``crud`` writes plain columns on one row and this
  goes through a service that re-bases the posted ledger under the owner's
  write lock.  The balance-history card LINKS here rather than editing in
  place: one door, two entrances, and the second is the only surface every
  account kind reaches.
* :mod:`app.routes.accounts.statements` -- What the BANK said (plan step
  ``bank_import:X-f6a-1``, ruling R-FP): the statement import page and its
  write door.  A fourth subject beside ``anchor``'s assertion, ``reconcile``'s
  outstanding set and ``history``'s record of past assertions -- and the only
  one of them sourced from outside the app.  It RECORDS and does not
  reconcile.
* :mod:`app.routes.accounts.statement_merchants` -- WHERE YOUR MERCHANTS GO
  (plan step ``bank_import:X-gk``, ruling **bank_import:R-IC**): one row per
  merchant this account has ever seen, carrying its standing answer or *You
  have not said*, edited ONE merchant at a time.  Its boundary against
  ``statement_reconcile`` is the one R-IC draws: that page's receipt offers a
  rule only for a merchant the pass just filed spending for, and measured on a
  clone of the developer's own database 2026-08-31 the three partial surfaces
  of the day left 32 of his 62 merchants on no surface at all.  **It MOVES NO
  MONEY** and posts to the rule door ``statement_reconcile`` also uses.
* :mod:`app.routes.accounts.statement_reconcile` -- ONE PAGE ON FOUR VERBS
  (plan step ``bank_import:X-gj-1b``, rulings **bank_import:R-HP**..**R-HX**):
  the Reconcile screen, and since plan step ``bank_import:X-gi-2`` the ONLY
  statement-reconciling screen there is: it replaced a review QUEUE, a REGISTER
  and a hand-build WORKBENCH, and that step deleted all three.  Every bank line
  ends on MATCH, ADD, TRANSFER or SKIP, and the inbox is the lines with none
  yet.
  **It MOVES MONEY through doors that already exist** -- it applies through
  ``apply_reviewed``, which the three deleted pages posted to before it, and
  releases through ``_statement_release`` alongside the import
  receipt (plan step ``bank_import:X-gj-1c``), opening none of its own.  Its
  two SETTLED tabs came with that step, and with them the register's whole job:
  the acts it listed, the bound it applied, the link past that bound and the
  Undo on each.  **What its URL asks for is read by
  :mod:`app.routes.accounts._reconcile_query`** -- the tab, the lifted bound and
  the card whose MATCH pane renders in the document -- and every one of those is
  read by a ROUTE, before its door, which plan step ``bank_import:X-gi-1`` made
  structural after one of them answered a 404 over a committed money pass.
  The three pages it replaced are GONE, in that order and for that reason:
  ruling **R-HU** sequenced it as "they stay alive beside it, nothing removed
  on the way in", plan step ``bank_import:X-gi-1`` repointed or deleted every
  inbound link, and ``bank_import:X-gi-2`` deleted the nine endpoints, their
  seven templates, ``statement_review.js`` and the three route test modules.
* :mod:`app.routes.accounts.bank_agreement` -- The two records SIDE BY SIDE
  (plan step ``bank_import:X-f6e-2``, ruling R-GF): a per-day comparison of
  what the app's own rows moved against what the bank's lines did, and of the
  two running balances.  It reports and never gates.  Its boundary against
  ``statements`` is ``difference``'s against ``anchor`` -- a read-only
  comparison of what a write door wrote is not that door's subject.
* :mod:`app.routes.accounts.types` -- Account-type CRUD for the
  per-user custom catalogue (commit C-28 / F-044).
* :mod:`app.routes.accounts.detail` -- Per-account detail pages.  The
  Fable 5 overhaul merged the former checking / interest pages into one
  ``cash_detail`` page serving every cash account kind (checking,
  ``has_interest`` types, and plain Savings / Credit Card / custom);
  ``checking_detail`` / ``interest_detail`` remain as redirect stubs.
  Also hosts ``update_interest_params`` and the ``property_detail`` /
  ``update_appreciation_params`` pair.  Balances route through the
  balance-at seam per the E-25 / Commit-8 canonical-producer contract.

Shared validation helpers and Marshmallow schema singletons live in
:mod:`app.utils.account_validation` so every sub-module imports the
same instance, preserving the pre-split "one schema constructed at
module load" behaviour.
"""

# Re-export ``accounts_bp`` from the leaf declaration module so
# consumers that ``from app.routes.accounts import accounts_bp``
# (notably ``app/__init__.py`` at factory-time) continue to resolve
# without an edit.  Pre-F-25 the blueprint was declared inline here;
# moving it to ``_bp`` was the smallest cycle-break that preserved
# the public package surface.
from app.routes.accounts._bp import accounts_bp


# Import sub-modules for the side effect of registering their route
# decorators against ``accounts_bp``.  The ``noqa`` markers suppress
# the unused-import / out-of-order-import warnings that would
# otherwise fire on what is, by design, a deferred-import side-
# effect registration.
from app.routes.accounts import crud  # noqa: F401, E402
from app.routes.accounts import lifecycle  # noqa: F401, E402
from app.routes.accounts import reconcile  # noqa: F401, E402
from app.routes.accounts import history  # noqa: F401, E402
from app.routes.accounts import anchor  # noqa: F401, E402
from app.routes.accounts import difference  # noqa: F401, E402
from app.routes.accounts import outstanding  # noqa: F401, E402
from app.routes.accounts import opening  # noqa: F401, E402
from app.routes.accounts import types  # noqa: F401, E402
from app.routes.accounts import detail  # noqa: F401, E402
from app.routes.accounts import statements  # noqa: F401, E402
from app.routes.accounts import statement_reconcile  # noqa: F401, E402
from app.routes.accounts import statement_merchants  # noqa: F401, E402
from app.routes.accounts import bank_agreement  # noqa: F401, E402


__all__ = ["accounts_bp"]
