"""Two renders compared without their CSRF tokens (task plan#3263).

Flask-WTF signs each CSRF token with the second it is minted in, and a page
carries one in its ``csrf-token`` meta tag and one in each form's hidden
``csrf_token`` input.  Two renders that should be identical therefore differ
whenever they fall in different seconds, whatever the code does: the
trusted-device 404 pair failed two merge-queue runs that way on 2026-11-10
(finding plan#3244, the tokens signed at 15:11:09 and 15:11:10).  A test that
compares renders blanks the tokens first, through this one helper, so the
rule has one spelling.
"""

import re

#: Each token's value: a form input's ``value`` and the meta tag's ``content``.
_CSRF_VALUE = re.compile(
    r'((?:name="csrf_token" value|name="csrf-token" content)=")[^"]*"',
)


def without_csrf_tokens(page: str) -> str:
    """Return *page* with each CSRF token's value blanked, so two renders compare.

    Args:
        page: A rendered page or fragment, as text.

    Returns:
        The page as rendered, but with every token's value empty.
    """
    return _CSRF_VALUE.sub(r'\1"', page)
