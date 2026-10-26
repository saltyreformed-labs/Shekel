"""Structural gate: an out-of-band MOUNT is not inside a self-refreshing region.

Plan step X-f1e3, finding **N-199**.

**The defect this exists to make unrepeatable.**  The anchor true-up's
back-dated acknowledgement was swapped out-of-band into ``#anchor-as-of``, a
per-surface caption.  On the dashboard that caption lives inside
``#pulse-section``, which carries ``hx-trigger="balanceChanged from:body"`` --
and the true-up's own response fires ``HX-Trigger: balanceChanged``.  So the
message swapped in and was then destroyed by the region re-fetch its own
response had triggered, within one round-trip.  Three further surfaces carried
no such element at all and were skipped outright.  One of five worked.

**Why the existing test could not see it.**  It asserted the acknowledgement's
text was in the RESPONSE BODY.  It was -- on the dashboard it was in the body
and gone from the DOM a moment later.  A response-body assertion cannot observe
what a second request does to the page; only the MOUNT's position can be
checked without a browser, and that is what this file checks.  (Section 7's
"ask of every harness: can it SEE the code under test?", paid for again.)

**What is asserted.**  For a fragment that must SURVIVE the ``balanceChanged``
its own response fires, the mount must have no ancestor that re-fetches itself
on that event.  ``#anchor-ack-mount`` lives in ``base.html``, so this is a
property of the shared layout and is graded on every page that extends it.

**The negative control is REAL, not planted** (Section 7.3).  The dashboard's
``#anchor-as-of`` genuinely IS inside ``#pulse-section``, and correctly so:
it is a durable caption that the pulse re-render redraws from its own loader.
That makes it a permanent positive subject for the detector -- if the probe
stops reporting it, the probe has stopped working, and no mutation has to be
planted to find that out.

The probe walks the rendered HTML rather than reading templates, because the
ancestry that matters is composed across ``base.html``, the page template and
its includes -- no one file contains it.

**What this gate does NOT cover, stated so it is not over-trusted:**

* **Three of the five anchor-editor surfaces.** The investment detail and cash
  detail pages need an account fixture and are ungraded here.  Both extend
  ``base.html``, so the mount is present by construction, but the two pages
  most recently restructured are the two nobody checks.
* **Only an ANCESTOR's own ``hx-trigger``.** A SIBLING element whose
  ``hx-target`` points at an ancestor of the mount would destroy the toast just
  as effectively and is invisible to this probe.
* **Whether the toast is ever SHOWN.** The mount surviving is half the
  contract; ``.toast:not(.show){display:none}``, so the other half is
  ``data-toast-auto-show`` reaching ``app.js``'s handler.  That half is graded
  in ``tests/test_routes/test_accounts/test_reconcile_prompt.py`` -- an
  adversarial review deleted the attribute and measured the ENTIRE suite still
  passing, which is how that assertion came to exist.
"""

from html.parser import HTMLParser

import pytest
from flask import url_for

# Elements HTML never closes.  Without this the probe would push them onto the
# ancestor stack and never pop them, so everything after the first ``<meta>``
# in ``base.html``'s head would report a corrupted ancestry.
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

# The event every balance-bearing region in this app re-fetches itself on.
_REFRESH_EVENT = "balanceChanged"


class _AncestorRefreshProbe(HTMLParser):
    """Find one element by id and report which ancestors self-refresh.

    Attributes:
        found: Whether an element carrying the target id was seen at all.  A
            probe that reports "no refreshing ancestors" because it never
            found the element is the failure mode this flag exists to
            separate out (Section 8: an instrument must be shown to have
            reached its subject).
        refreshing_ancestors: The ``(tag, hx-trigger)`` pairs of every
            ancestor that re-fetches itself on :data:`_REFRESH_EVENT`.
    """

    def __init__(self, target_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._target_id = target_id
        # (tag, hx-trigger value or None) for every currently-open element.
        self._stack: list[tuple[str, str | None]] = []
        self.found = False
        self.refreshing_ancestors: list[tuple[str, str]] = []

    def _check_target(self, attrs: list[tuple[str, str | None]]) -> None:
        """Record the ancestry if these attrs carry the target id.

        Called BEFORE the element is pushed, because an element is not its own
        ancestor -- the mount itself is allowed to carry anything.
        """
        if dict(attrs).get("id") != self._target_id:
            return
        self.found = True
        self.refreshing_ancestors = [
            (tag, trigger) for tag, trigger in self._stack if trigger is not None
        ]

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self._check_target(attrs)
        if tag in _VOID_TAGS:
            return
        trigger = dict(attrs).get("hx-trigger")
        self._stack.append(
            (tag, trigger if trigger and _REFRESH_EVENT in trigger else None),
        )

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        # A self-closing element can BE the target but can never be an
        # ancestor, so it is checked and not pushed.
        self._check_target(attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        # Unwind to the matching open tag rather than popping blindly, so an
        # unclosed element (legal in HTML, and present in real templates)
        # cannot desynchronize every ancestry after it.
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                return


def _probe(html: str, target_id: str) -> _AncestorRefreshProbe:
    """Run the probe for *target_id* over *html*."""
    probe = _AncestorRefreshProbe(target_id)
    probe.feed(html)
    return probe


class TestOutOfBandMountsSurviveTheirOwnTrigger:
    """``#anchor-ack-mount`` outlives the ``balanceChanged`` it rides with."""

    @pytest.mark.parametrize(
        "endpoint",
        ["dashboard.page", "grid.index", "savings.dashboard"],
        ids=["dashboard", "grid", "savings-cockpit"],
    )
    def test_the_acknowledgement_mount_has_no_self_refreshing_ancestor(
        self, app, auth_client, seed_user, seed_periods_today, endpoint,
    ):
        """The toast mount survives the refresh its own response triggers.

        The anchor true-up answers ``HX-Trigger: balanceChanged`` AND an
        out-of-band acknowledgement in the same response.  If the mount sits
        inside a region that re-fetches on that event, the response destroys
        its own message -- which is exactly what happened on the dashboard for
        as long as the acknowledgement rode ``#anchor-as-of`` (finding N-199).

        Graded on three pages that extend ``base.html``: the mount is in the
        shared layout, so the property is the layout's, and any page could
        break it by wrapping the toast container.
        """
        with app.app_context():
            response = auth_client.get(url_for(endpoint))
            assert response.status_code == 200

            probe = _probe(response.data.decode(), "anchor-ack-mount")

            assert probe.found, (
                "#anchor-ack-mount is missing from this page, so the anchor "
                "true-up's out-of-band acknowledgement would orphan-target "
                "(htmx:oobErrorNoTarget) and the user would see no sign a "
                "back-dated balance was recorded"
            )
            assert probe.refreshing_ancestors == [], (
                "#anchor-ack-mount is inside a region that re-fetches itself "
                f"on {_REFRESH_EVENT}: {probe.refreshing_ancestors}.  The "
                "true-up response fires that event, so its own "
                "acknowledgement would be wiped within one round-trip -- "
                "finding N-199, on a second mount"
            )

    def test_the_probe_fires_on_the_caption_that_is_inside_the_pulse(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control: the detector reports a REAL enclosed element.

        ``#anchor-as-of`` on the dashboard genuinely lives inside
        ``#pulse-section`` (``hx-trigger="balanceChanged from:body"``).  That
        is correct for a durable caption the pulse re-render redraws -- and it
        is precisely the position that destroyed the acknowledgement while the
        acknowledgement rode it.

        Without this control the test above would pass just as happily if the
        probe never matched anything, which is the failure mode Section 8
        names: an instrument must be shown to have reached its subject.
        """
        with app.app_context():
            response = auth_client.get(url_for("dashboard.page"))
            assert response.status_code == 200

            probe = _probe(response.data.decode(), "anchor-as-of")

            assert probe.found, (
                "the control's subject is gone: the dashboard no longer "
                "renders #anchor-as-of, so this test is no longer proving "
                "the probe can see an enclosed element.  Re-point it at "
                "another element inside a balanceChanged region rather than "
                "deleting it"
            )
            assert any(
                _REFRESH_EVENT in trigger
                for _tag, trigger in probe.refreshing_ancestors
            ), (
                "the probe did not report the dashboard's #anchor-as-of as "
                "enclosed by a self-refreshing region, but it is inside "
                "#pulse-section.  The probe is broken, and the assertions "
                "above are passing vacuously"
            )
