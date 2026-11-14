"""
Shekel Budget App -- Which table holds a recurring definition's rows.

**The one table from a definition's KIND to the rows it generates** (plan step
``pay_calendar:C9``, task plan#3939): a transaction template's rows are
``budget.transactions`` keyed by ``template_id``, and a transfer template's are
``budget.transfers`` keyed by ``transfer_template_id``.  Every reader that asks
which occurrences a definition's rows already answer -- both recurrence engines'
generate and maintain passes, and the balance seam's definition walk
(``balance_at._definition_walk``), which estimates the rows generation has not
written -- takes its
:class:`~app.services._recurrence_common.TemplateRowSelector` from
:func:`row_selector_for`, so the pairing is written once.

**Why it is not in either engine, nor in** ``_recurrence_common``.  Each engine
spelled its own half in a private ``_selector`` until this step, and the balance
seam spelled the transfer half a third time; the seam's walk now takes a
transaction template too, and it cannot import either engine, because both
import ``app.services.balance_at`` (the transfer engine at module level, the
transaction engine through ``_conflicts``).  ``_recurrence_common`` is the
module both engines and the seam already share, and it stands at pylint's
1,000-line ceiling, so the table is a module of its own beside it rather than a
classmethod on the selector that would have pushed it over.

Pure lookup: no query, no write, no Flask.
"""

from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services._recurrence_common import TemplateRowSelector


def row_selector_for(template, scenario_id: int) -> TemplateRowSelector:
    """Return which rows *template*'s occurrences live in, in *scenario_id*.

    The table the two recurrence engines' difference is written in: the mapped
    class a definition's generated rows are, and that class's foreign key back
    to the definition.  Nothing else about a selector depends on the kind.

    Args:
        template: A :class:`~app.models.transaction_template.TransactionTemplate`
            or a :class:`~app.models.transfer_template.TransferTemplate`.
        scenario_id: The scenario every row must match.

    Returns:
        The :class:`~app.services._recurrence_common.TemplateRowSelector` for
        that template's rows.

    Raises:
        TypeError: When *template* is neither kind.  Answering with either
            table would query the wrong rows silently, and a row the claim
            check cannot see is an occurrence generated twice.
    """
    if isinstance(template, TransactionTemplate):
        return TemplateRowSelector(
            Transaction, Transaction.template_id, template, scenario_id,
        )
    if isinstance(template, TransferTemplate):
        return TemplateRowSelector(
            Transfer, Transfer.transfer_template_id, template, scenario_id,
        )
    raise TypeError(
        f"{type(template).__name__} is not a recurring definition: a "
        f"definition's rows live in budget.transactions (a TransactionTemplate) "
        f"or budget.transfers (a TransferTemplate), and no other table."
    )


__all__ = ["row_selector_for"]
