"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_categories.py``, moved verbatim by ``tools/split``.
"""

from app.extensions import db
from app.models.category import Category
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password


# ── Helpers ──────────────────────────────────────────────────────────


def _create_other_user_category():
    """Create a second user with their own category.

    Returns:
        dict with keys: user, category.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    category = Category(
        user_id=other_user.id,
        group_name="Other",
        item_name="Other Item",
    )
    db.session.add(category)
    db.session.commit()

    return {"user": other_user, "category": category}
