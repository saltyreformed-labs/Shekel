"""
Shekel Budget App -- how long each standing a finished sign-in gives a browser lasts.

A finished sign-in starts three things, each of them thirty days long: the
session it signs in (ruling plan#3069), the browser's standing as known
(plan#2772, ruling plan#2995) and, when the code was typed with "Trust this
device" ticked, its trust (plan#2173, ruling plan#2406).  This module is the
one statement of those lengths.

**A leaf, importing nothing of the app's**, so every layer can read it.  The
configuration reads it: Flask's cookie age is set from :data:`KNOWN_LIFETIME`
(``app.config.BaseConfig.PERMANENT_SESSION_LIFETIME``, ruling plan#3926).  The
lengths lived in ``app.models.known_device`` until then, out of the
configuration's reach: importing a model loads the whole ``app.models``
package, which imports ``app.config`` (``app.models.pay_schedule`` reads
``BaseConfig``), so the configuration could not import it back.
"""

from datetime import timedelta


#: How long a device stays trusted after the code was typed on it (ruling
#: plan#2406).  The one statement of the length: the trust's bound
#: (``app.services.known_device_service._trusted``),
#: ``KnownDevice.trust_expires_at`` and every sentence that names it read
#: this.
TRUST_LIFETIME = timedelta(days=30)

#: How long a browser stays known after its last finished sign-in: the same
#: length as trust (ruling plan#2995), so it is stated once and named twice.
#: A trust begins at a finished sign-in, which renews this too, so the
#: browser's cookie, which lasts this long, can never die before a trust does.
#: A session ends this long after its sign-in too, however active (ruling
#: plan#3069, ``app.utils.session_helpers.sign_in_is_live``): the sign-in that
#: starts it renews the record from the same instant, so a browser that is
#: signed in is known, unless ten wrong guesses on its cookie, its own or a
#: copy's, removed its record.  Flask refuses a session cookie signed longer
#: ago than this (ruling plan#3926), which can never cut in before the
#: session's own end: a signed-in session's cookie is signed no earlier than
#: its sign-in.
KNOWN_LIFETIME = TRUST_LIFETIME
