"""The owner's Settings > Companions as its tests read it, and a companion they need.

Each active companion's row there carries her two-factor, her trusted devices
(ruling plan#3439) and the owner's Reset two-factor (ruling plan#3441):
:func:`companion_row` finds a companion's row in the page, :func:`text_of`
reads it as a person would, and :func:`landed_on_companions` checks a door sent
the owner back there.  :func:`another_owners_companion` makes the
companion a cross-owner refusal is tested with.
"""

import re

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password
from tests._two_factor_sign_in import SEED_COMPANION_EMAIL

#: Settings > Companions, where the owner manages each companion.
COMPANIONS_PAGE = "/settings?section=companions"


def text_of(html):
    """Return *html*'s text: tags dropped, whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def companion_row(page, email=SEED_COMPANION_EMAIL):
    """Return the HTML of the active companion's row that names *email*."""
    rows = [
        row for row in page.split('<div class="list-group-item">')[1:]
        if email in row and "/deactivate" in row
    ]
    assert len(rows) == 1, f"expected one active row for {email}, found {len(rows)}"
    return rows[0]


def another_owners_companion(owner_id):
    """Create a companion linked to *owner_id*, committed; return her id."""
    companion = User(
        email="foreign@shekel.local",
        password_hash=hash_password("foreignpass12"),
        display_name="Foreign Companion",
        role_id=ref_cache.role_id(RoleEnum.COMPANION),
        linked_owner_id=owner_id,
    )
    db.session.add(companion)
    db.session.flush()
    db.session.add(UserSettings(user_id=companion.id))
    db.session.commit()
    return companion.id


def landed_on_companions(response):
    """Assert a followed redirect ended on Settings > Companions; return its page."""
    assert response.status_code == 200, response.status_code
    assert response.request.path == "/settings"
    assert response.request.args.get("section") == "companions"
    return response.get_data(as_text=True)
