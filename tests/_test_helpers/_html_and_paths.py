"""Reading what the app renders: the ``app`` package's directory, and a rendered form's options and
disabled fields.
"""

import importlib.util
import pathlib
import re


#: The ``app`` package's directory, read off the package's own import origin,
#: for a test that reads a file under ``app/`` (a template, a static asset, a
#: route module).  A read under ``app/`` never counts up from a test file's own
#: ``__file__``, this module's included: a test module moves (a split puts each
#: part one directory deeper), and a path counted from where it sat then names
#: another directory.  This module's two reads OUTSIDE ``app/`` (``migrations/``
#: and ``scripts/``, below) start from :data:`tests._repo_root.REPO_ROOT`.
APP_PACKAGE_DIR = pathlib.Path(importlib.util.find_spec("app").origin).parent


def select_option_values(html: str, select_key: str) -> list[str]:
    """Return the ``value`` attributes of every ``<option>`` inside a named ``<select>``.

    Locates the ``<select>`` element identified by ``select_key``
    (matched against either its ``id`` or its ``name`` attribute --
    Shekel's templates are inconsistent: some forms set only
    ``name=`` while others set both ``id=`` and ``name=``) and
    returns the ``value="..."`` of each ``<option>`` child it
    contains, in document order.  Returns an empty list when no
    matching select is present or it carries no option children with
    value attributes.

    Use this helper to assert dropdown contents without falsely
    matching ``value="N"`` attributes from unrelated elements
    elsewhere in the page (transaction-type IDs, pay-period IDs,
    hardcoded month numbers, recurrence-pattern IDs, etc.).  A naive
    ``f'value="{model.id}" not in html`` check fails when ``model.id``
    happens to collide with any of those siblings -- a deterministic
    bug masquerading as a flake until the colliding sequence values
    align.

    Args:
        html: The full HTML response body to search.
        select_key: The ``id`` or ``name`` attribute of the
            ``<select>`` element to scope the search to.
            Case-sensitive, matched against the literal attribute
            value.

    Returns:
        Ordered list of ``value`` strings from the named select's
        ``<option>`` children.  Empty list when the select is not
        present in ``html``.  Returned values are the raw attribute
        strings (e.g. ``"2"`` not ``2``) so callers compare against
        ``str(model.id)`` rather than the int.
    """
    select_block = re.search(
        r'<select[^>]*\b(?:id|name)="'
        + re.escape(select_key)
        + r'"[^>]*>(.*?)</select>',
        html,
        re.DOTALL,
    )
    if select_block is None:
        return []
    return re.findall(
        r'<option\b[^>]*\bvalue="([^"]*)"',
        select_block.group(1),
    )


def field_is_disabled(html: str, field_name: str) -> bool:
    """Return True if the ``<input>``/``<select>`` named ``field_name`` is disabled.

    Slices from the ``name="<field_name>"`` attribute to the end of that
    opening tag (the next ``>``) and reports whether the ``disabled``
    attribute appears there.  The grid edit popovers append ``disabled``
    after ``name=`` on a finalised row's locked money / period / category
    / due-date fields (#26), so this distinguishes a locked field from the
    still-editable Status dropdown and Notes input in the same form.

    Args:
        html: The full HTML response body to search.
        field_name: The ``name`` attribute of the input/select to inspect.

    Returns:
        True when the named field's opening tag carries ``disabled``.

    Raises:
        AssertionError: The field is absent, so a typo'd name fails loud
            rather than silently reporting an editable field as locked.
    """
    marker = f'name="{field_name}"'
    idx = html.find(marker)
    assert idx != -1, f"{marker} not found in rendered HTML"
    tag_end = html.find(">", idx)
    return "disabled" in html[idx:tag_end]
