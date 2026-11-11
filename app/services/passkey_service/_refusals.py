"""
Shekel Budget App -- Why a passkey ceremony was refused, and what the person is told.

Its own module because both halves of the package raise it: the reading of
what the browser sent (:mod:`._responses`) and the ceremonies themselves
(:mod:`app.services.passkey_service`).  The cap on an account's passkeys lives
here beside the sentence that names it, so the number is spelled once.
"""

from enum import Enum

from app.exceptions import AuthError


#: The most passkeys one account may hold (ruling plan#3472).
MAX_PASSKEYS = 10

_TRY_AGAIN = "That passkey could not be checked. Please try again."


class PasskeyRefusal(Enum):
    """Why a ceremony was refused, for the log and for the tests that grade it.

    The person is told ``str()`` of :class:`PasskeyRefused` instead: one
    sentence, the same for most of these, because a sign-in's refusal must
    name nothing about the account.
    """

    #: No state, or the challenge is ``CHALLENGE_LIFETIME`` old.
    EXPIRED = "expired"
    #: The response is not a credential the service can read.
    MALFORMED = "malformed"
    #: fido2 refused it: the wrong type of ceremony, the wrong origin or
    #: challenge, another relying party's hash, no user presence or
    #: verification, or a signature that does not verify.
    NOT_VERIFIED = "not_verified"
    #: No passkey has this id, or not one of the account's.
    UNKNOWN_CREDENTIAL = "unknown_credential"
    #: The user handle is missing where it must be present, or another's.
    WRONG_USER_HANDLE = "wrong_user_handle"
    #: The passkey's account is disabled.
    ACCOUNT_DISABLED = "account_disabled"
    #: The challenge was issued at or before the passkey last signed in.
    REPLAYED = "replayed"
    #: A counting authenticator's counter did not rise.
    COUNTER_DID_NOT_RISE = "counter_did_not_rise"
    #: An add completed by an account other than the one it was begun for.
    WRONG_ACCOUNT = "wrong_account"
    #: The new key's algorithm is not one the options offered.
    UNOFFERED_ALGORITHM = "unoffered_algorithm"
    #: The new key cannot verify a signature.
    UNUSABLE_KEY = "unusable_key"
    #: An account already holds this credential.
    ALREADY_ADDED = "already_added"
    #: The account holds :data:`MAX_PASSKEYS` already.
    TOO_MANY = "too_many"


#: What the person is told, where it is more than :data:`_TRY_AGAIN`.
_MESSAGES = {
    PasskeyRefusal.EXPIRED: "That took too long. Please try again.",
    PasskeyRefusal.ALREADY_ADDED: "That passkey has already been added.",
    PasskeyRefusal.TOO_MANY: (
        f"An account can hold up to {MAX_PASSKEYS} passkeys. "
        "Remove one to add another."
    ),
}


class PasskeyRefused(AuthError):
    """A ceremony was refused; ``str()`` is the sentence the person is told.

    Attributes:
        refusal: Why, as a :class:`PasskeyRefusal`.
    """

    def __init__(self, refusal: PasskeyRefusal):
        """Record *refusal* and its sentence.

        Args:
            refusal: Why the ceremony was refused.
        """
        super().__init__(_MESSAGES.get(refusal, _TRY_AGAIN))
        self.refusal = refusal
