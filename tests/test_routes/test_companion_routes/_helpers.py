"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_companion_routes.py``, moved verbatim by ``tools/split``.
"""

from app.extensions import db
from tests._test_helpers import make_expense_template


# ── Helpers ──────────────────────────────────────────────────────────


def _make_template(
    seed_user, *, companion_visible, track=False, name="Item", amount="500.00",
):
    """Create a priced, every-paycheck expense definition for the seed_user owner.

    A case's row is the definition's own (:func:`generate_row_of`, plan step
    balance:X-cf-4), so the name and the figure a case wants on its row are
    stated HERE -- the definition is what names and prices a generated row.

    Args:
        seed_user: The seed_user fixture dict.
        companion_visible: Whether the template is companion-visible.
        track: Whether to enable is_envelope.
        name: Template name.
        amount: The definition's stated price, as a string.

    Returns:
        The created TransactionTemplate object (flushed, ID available).
    """
    return make_expense_template(
        db.session, seed_user, amount=amount, name=name,
        category_key=next(iter(seed_user["categories"])),
        is_envelope=track, companion_visible=companion_visible,
    )
