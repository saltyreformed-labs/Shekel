"""
Shekel Budget App -- the random tokens a browser holds and the server keeps only hashed.

Two records name a browser by a token one of its cookies carries: its known
browser's record (``auth.known_devices``, through the device cookie, ruling
plan#2994) and its sign-in's record (``auth.sign_ins``, through the session
cookie, ruling plan#3925).  Whoever holds the token is who the record means, so
each token is 256 random bits (ruling plan#2406) and each table stores only its
hash, which :data:`TOKEN_HASH_CHECK` pins so a writer that stored the token
itself is refused at the database.

The token is random, so the hash needs no salt or stretching: there is no
dictionary to search, so a fast hash loses nothing a slow one would keep, and
it lets a lookup find the row by an index.

This module is the one statement of the token's size, how it is made and how it
is stored, so the two records cannot drift apart in any of the three.
"""

import hashlib
import secrets


#: Bytes of randomness in a token: 256 bits (ruling plan#2406).
TOKEN_BYTES = 32

#: Characters in a SHA-256 digest written as hex: the length of every
#: ``token_hash`` column.
TOKEN_HASH_LENGTH = 64

#: The CHECK every ``token_hash`` column carries: exactly the lowercase hex
#: digest :func:`token_hash` writes, so a raw token (43 URL-safe characters) or
#: an uppercase digest, which no lookup could find, is refused.
TOKEN_HASH_CHECK = f"token_hash ~ '^[0-9a-f]{{{TOKEN_HASH_LENGTH}}}$'"


def new_token() -> str:
    """Return a new token: :data:`TOKEN_BYTES` random bytes, URL-safe.

    Returns:
        The token, as the cookie carries it.  Its caller stores only
        :func:`token_hash` of it.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    """Return the stored form of *token*: its SHA-256 as lowercase hex.

    Args:
        token: The token, as the cookie carries it.

    Returns:
        :data:`TOKEN_HASH_LENGTH` lowercase hex digits.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
