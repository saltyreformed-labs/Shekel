"""
Shekel Budget App -- Sign-in Model (auth schema)

One finished sign-in that is still signed in (ruling plan#3925): the server's
own word that a session is live.  A session cookie is signed but the server
used to keep nothing of it, so signing out could only edit the cookie the
browser sent back, and a copy taken earlier kept working.  Now each finished
sign-in writes a row, the session cookie carries the row's token, and
``load_user`` refuses a session whose token names no live row of its user.
Signing out deletes the row, so every copy of the cookie dies with it.

**A live row is a live session, and nothing else says so.**  There is no
``ended_at``, no revoked flag and no expiry column (CLAUDE.md rule 14): a
sign-in ends by its row being deleted, or by its age, which is derived from
:attr:`SignIn.signed_in_at` at read time (``app.services.sign_in_service``,
the one statement of when a row is live).

**The token is never stored**: only its SHA-256, as lowercase hex, which
``ck_sign_ins_token_hash_sha256_hex`` pins, as ``auth.known_devices`` does for
the device cookie's token (``app.utils.bearer_tokens``, the one statement of
the token, its hash and the CHECK).

**Not the browser's record.**  ``auth.known_devices`` says a browser is known,
which by ruling plan#2997 nothing a person presses may end, and ten wrong
guesses delete it (ruling plan#2996).  A sign-in ends at every sign-out.  Two
facts with two lifetimes, so two tables: a sign-out that touched the browser's
record would end a standing the ruling keeps, and wrong guesses that deleted a
sign-in would let a thief holding a copy of the device cookie sign its owner
out.

Related service: app/services/sign_in_service.py
"""

from app.extensions import db
from app.models.mixins import BrowserTokenMixin
from app.utils.bearer_tokens import TOKEN_HASH_CHECK


class SignIn(BrowserTokenMixin, db.Model):
    """One finished sign-in, live until it is deleted or ages out.

    A sign-in writes it and a sign-out deletes it.  The one update is of its
    token, when the browser holding it presses "Log out all other sessions"
    or changes its password (ruling plan#4010), so a copy of the old cookie
    is refused; :attr:`signed_in_at` is never updated, so it is also when the
    row was made.
    """

    __tablename__ = "sign_ins"
    __table_args__ = (
        db.PrimaryKeyConstraint("id", name="pk_sign_ins"),
        db.UniqueConstraint("token_hash", name="uq_sign_ins_token_hash"),
        db.CheckConstraint(
            TOKEN_HASH_CHECK, name="ck_sign_ins_token_hash_sha256_hex",
        ),
        db.Index("ix_sign_ins_user_id", "user_id"),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer)
    # The sign-in's instant, the same one its browser's record was renewed at
    # (ruling plan#3069), so the session ends when the browser stops being
    # known.
    signed_in_at = db.Column(db.DateTime(timezone=True), nullable=False)

    def __repr__(self):
        return f"<SignIn id={self.id} user_id={self.user_id}>"
