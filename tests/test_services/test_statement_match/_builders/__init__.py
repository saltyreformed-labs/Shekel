"""The rows these tests match against, built without the code under test.

``seed_user`` creates an account, a scenario and categories and **no
transaction at all** -- a fact this arc has already paid for once, when a test
in the previous leaf compared ``[] == []`` and reported it as proof that
nothing moved.  So every test here builds its own rows, through the ORM rather
than through the services the accept door calls, so a broken settle verb cannot
also break the fixture that would have caught it.

**A row of a DEFINITION is the one exception, and it is not an exception to
that argument** (plan step balance:X-cf): the recurrence engine generates it
(:func:`tests._test_helpers.generate_row_of`), because a hand-built
``Transaction(template_id=...)`` was a row the application cannot produce --
undated, owning a figure with ``is_override=False``.  The engine is not the
code under test here; the settle still is, and it stays bare.

The builders live in this package's three modules, one subject each,
moved verbatim from the single module this package replaced (plan#3568).  This
module imports every name they define, so an import of a builder from this
package reads it where it always did.
"""

from ._ledger_rows import a_transaction, a_purchase, an_assertion
from ._statement_rows import (
    an_import,
    a_merchant,
    a_bank_line,
    a_sighting,
    the_merchant_id,
    a_statement,
    a_rule,
    an_account_whose_books_hide_a_line,
)
from ._filing_and_the_pass import (
    a_one_off_envelope,
    an_envelope,
    an_unexplained_outflow,
    filed_by,
    filed_acts,
    a_later_period,
    a_payday_on,
    a_scope,
    accepted_acts,
    a_bars,
    an_answers,
    a_purchase_in_a_minted_envelope,
    a_basis,
    a_submission,
    _attribution,
)
