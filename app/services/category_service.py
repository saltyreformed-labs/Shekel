"""
Shekel Budget App -- Category Service

Shared queries over ``budget.categories``.  Flask-isolated per the
project architecture rule (``CLAUDE.md`` Architecture section): takes
plain data (a user id), returns plain SQLAlchemy objects, never imports
``request``/``session``.  The caller owns the surrounding transaction.
"""

from app.extensions import db
from app.models.category import Category


def list_active_categories(user_id: int) -> list[Category]:
    """Return a user's active categories ordered for display dropdowns.

    Read by the routes that render a category picker, directly or through
    :func:`list_categories_for_edit` (the grid's Add Transaction modal is the
    exception: ``grid.page`` filters its own list of every category), so the
    option list is consistently ordered by
    ``(sort_order, group_name, item_name)`` -- the user-assignable
    ``sort_order`` leads so the picker matches the order shown on the
    categories settings page (``settings._load_categories_context``
    orders the same rows that way) and mirrors the sibling
    ``account_service.list_active_accounts`` ordering for the same forms
    (deep-quality-hunt #66; the identical account defect was P-2).
    Archived categories (``is_active = False``) are excluded because
    they are not selectable targets for new rows.

    Args:
        user_id: ``auth.users.id`` of the owner whose categories to list.

    Returns:
        The owner's active :class:`Category` rows, ordered by
        ``sort_order``, then ``group_name``, then ``item_name``.
    """
    return (
        db.session.query(Category)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Category.sort_order, Category.group_name, Category.item_name)
        .all()
    )


def list_categories_for_edit(
    user_id: int, own: Category | None,
) -> list[Category]:
    """Return what an EDIT form's category picker offers: active, plus its own.

    The owner's active categories (:func:`list_active_categories`), with the
    edited record's OWN category appended when it is not among them, which
    for a category the owner holds means it is archived.  A ``<select>``
    whose stored value is missing submits its FIRST option, so a form built
    from the active list alone re-files a record whose category was archived
    under whichever category sorts first on an untouched save.  The archived
    category is offered only to keep: it is not a target a new record can
    pick, so the create forms still read :func:`list_active_categories`.

    One producer for the two edit forms that offer it: the recurring bill's
    (``templates.edit_template``, task plan#3267) and a placed row's popover
    (``transactions.forms.render_full_edit``).

    Args:
        user_id: ``auth.users.id`` of the owner whose categories to list.
        own: The edited record's category, or ``None`` for a record that has
            none yet.

    Returns:
        The active categories in :func:`list_active_categories`' order, then
        *own* when it is not among them.
    """
    categories = list_active_categories(user_id)
    if own is not None and own not in categories:
        categories.append(own)
    return categories
