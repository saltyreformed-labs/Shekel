"""The passkey service (plan#2538): adding a passkey, signing in and re-confirming with one.

Every ceremony runs against :class:`tests._passkeys.SoftwareAuthenticator`, a
real EC P-256 key signing real authenticator and client data, so each
refusal is the service refusing a real answer that is wrong in exactly one
way.  Each refusal test names the :class:`PasskeyRefusal` it expects, and
where fido2 refuses, the check it refused by, so no test passes on a refusal
it did not mean to provoke.
"""
