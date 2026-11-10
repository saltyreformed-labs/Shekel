"""
Shekel Budget App -- The bank FEED: the SimpleFIN Bridge connection and its doors.

**Born as a MOVE-ONLY split of ``app/services/bank_feed.py``** (plan step
``bank_import:X-f6b-2``, the sync leaf's first commit, 2026-09-20, under
ruling **balance:R-IR**: *pylint's 1,000-line module ceiling stays, and the
session that breaks a module is the one that splits it*).  The flat module
stood at 900 lines and the sync leaf adds to it, so it was split BEFORE the
ceiling fired (``transfer_service`` split AT it, finding **N-145**), by
RESPONSIBILITY rather than by line count, in the shape ``status_seam``,
``transfer_service`` and ``cash_ledger`` take -- private leaves, and the
package's ``__init__`` is the surface:

* :mod:`._bridge` -- the TRANSPORT: what every request to Bridge carries
  and what refuses it.  The host pin (ruling **R-BI28**), the one request
  site, which follows no redirect (ruling **R-BI29**), the timeout, and the
  refusal that never repeats a credential.  It reads no model and touches no
  session.
* :mod:`._doors` -- the DOORS the panel calls: claim, list, map and
  disconnect (rulings **R-BI12**, **R-BI26**, **R-BI27**), the values they
  answer, and the readers of Bridge's answers and of what the mapping column
  can hold.  ``_bridge`` keeps only what SENDING needs; a value only the
  doors read stands here, beside them.

**The public surface is this module and ``__all__`` is it**: every name the
flat module exposed without an underscore, so no import site changed and
none was narrowed.  A leaf is private: ``shekel-private-module-import``
fences a cross-package import of one.

**What "moved verbatim" covers, stated against the tree the commit started
from** (``git show 4ee7fa75:app/services/bank_feed.py``, identical there to
``eb7c8154``): every constant, dataclass and function in the two leaves is
the definition that stood in the flat module -- ``ast.dump`` of each equals
``ast.dump`` of the node it replaced, graded BY NAME over all 29 top-level
nodes (the :mod:`app.models._statement_import_table_args` precedent), and
each one's source text was sliced from the flat module by line span, so the
comments inside a body and the ``#:`` comment above a constant moved with
it.  What is new: the module docstrings, split with their subjects; the
import block each leaf needs; and this file.
"""

from app.services.bank_feed._bridge import BRIDGE_HOST, BRIDGE_TIMEOUT_SECONDS
from app.services.bank_feed._doors import (
    EXTERNAL_ID_LIMIT,
    BridgeAccount,
    BridgeListing,
    DisconnectOutcome,
    FeedMapping,
    FeedState,
    MappingOutcome,
    claim_feed,
    disconnect_feed,
    feed_state,
    list_bridge_accounts,
    map_accounts,
    mappable_accounts,
)

__all__ = [
    "BRIDGE_HOST",
    "BRIDGE_TIMEOUT_SECONDS",
    "EXTERNAL_ID_LIMIT",
    "BridgeAccount",
    "BridgeListing",
    "DisconnectOutcome",
    "FeedMapping",
    "FeedState",
    "MappingOutcome",
    "claim_feed",
    "disconnect_feed",
    "feed_state",
    "list_bridge_accounts",
    "map_accounts",
    "mappable_accounts",
]
