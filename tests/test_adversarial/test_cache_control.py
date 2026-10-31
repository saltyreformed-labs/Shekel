"""
Shekel Budget App -- Cache-Control Adversarial Tests

Audit finding F-019 (shared-device threat): a logged-out user steps
away from a shared computer; an attacker presses the browser back
button and reconstructs the dashboard from history.  The defense is
``Cache-Control: no-store, no-cache, must-revalidate`` on every
authenticated response so the browser does not retain a renderable
copy.

These tests assert the header value end-to-end against the actual
after-request hook in ``app/__init__.py``.  Browser back-button
behavior itself cannot be exercised by Flask's test client; that
requires a manual verification pass against a real browser (covered
by the runbook's manual checklist).

The static-side counterpart is also pinned here: content-versioned
static URLs (the ``v=`` parameter from ``_register_static_versioning``)
must receive a long-lived immutable lifetime, while unversioned or
hash-mismatched requests keep conditional caching, and neither may
ever carry no-store.
"""

import base64
import hashlib
from pathlib import Path

from flask import url_for

from app.routes.static_pass import static_file_version

VENDOR_DIR = Path(__file__).resolve().parents[2] / "app" / "static" / "vendor"


def test_logged_out_pages_have_no_store(auth_client, client):
    """After logout, any accidental refetch of an authenticated route
    must not be served from cache.  We assert the no-store header on
    the page that was authenticated -- the same hook applies before
    and after logout, so checking the header on the dashboard while
    logged in is sufficient evidence that the post-logout cache
    semantics are correct."""
    pre_logout = auth_client.get("/dashboard", follow_redirects=True)
    assert pre_logout.status_code == 200
    assert "no-store" in pre_logout.headers.get("Cache-Control", "")

    # Log out, then GET /dashboard directly.  Flask redirects to
    # /login because the session is gone; the redirect response
    # itself must also carry no-store so the browser does not cache
    # the 302.  The login page (after follow_redirects) must too.
    auth_client.post("/logout")
    post_logout = auth_client.get("/dashboard")
    # Status: 302 (redirect to login).  Either way, no-store applies.
    assert "no-store" in post_logout.headers.get("Cache-Control", "")

    # Anonymous client landing on the login page must also be no-store.
    landing = client.get("/login")
    assert "no-store" in landing.headers.get("Cache-Control", "")


def test_static_assets_excluded_from_no_store(client):
    """Static assets must remain cacheable.  Who serves them depends
    on the deploy mode: the bundled stack's nginx serves /static/
    itself with ``Cache-Control: public, immutable``, while the
    shared-mode stack (the actual production host) proxies /static/
    through to Flask's static handler -- so the after-request hook's
    ``request.endpoint == 'static'`` opt-out exercised here IS the
    production static behaviour in shared mode, not just a dev path.

    Vendor assets are content-versioned via VERSIONS.txt; aggressive
    caching is correct and required for performance."""
    resp = client.get("/static/vendor/htmx/htmx.min.js")
    assert resp.status_code == 200
    cache_control = resp.headers.get("Cache-Control", "")
    # The static handler must NOT have applied no-store; Flask's
    # default is empty / no-cache directive on static serving.  The
    # explicit assertion is that ``no-store`` is absent.
    assert "no-store" not in cache_control


def test_static_asset_path_resolves(client):
    """Fail loudly if a vendored file is missing.  The static path
    asserted here MUST point at a file in ``app/static/vendor/``;
    otherwise the test in test_static_assets_excluded_from_no_store
    is meaningless (Flask returns 404, no-store is moot)."""
    # All three core vendored assets must resolve.
    paths = (
        "/static/vendor/bootstrap/bootstrap.min.css",
        "/static/vendor/bootstrap/bootstrap.bundle.min.js",
        "/static/vendor/htmx/htmx.min.js",
        "/static/vendor/chart-js/chart.umd.min.js",
        "/static/vendor/bootstrap-icons/bootstrap-icons.min.css",
        "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
        "/static/vendor/fonts/fonts.css",
        "/static/vendor/fonts/inter-latin.woff2",
        "/static/vendor/fonts/jetbrainsmono-latin.woff2",
    )
    for path in paths:
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"Static asset missing or unreachable: {path}"
        )


def test_vendor_manifest_matches_the_bytes_served(client):
    """Every line of ``app/static/vendor/VERSIONS.txt`` names the SHA-384
    of the file the app serves at that path, and every vendored file
    under a manifest section has a line.

    The manifest header calls the hash "the source of truth for which
    exact bytes are served"; until 2026-09-20 nothing recomputed it, so
    a refresh that replaced a file and forgot its line (or edited the
    line and not the file) shipped green.  The hash is read from the
    served response, not the file on disk, so a static route that
    served something else would fail here too.
    """
    manifest = VENDOR_DIR / "VERSIONS.txt"
    listed: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "|" not in line:
            continue
        rel_path, _url, digest = (part.strip() for part in line.split("|"))
        listed[rel_path] = digest
    assert listed, "the manifest lists no files"

    for rel_path, digest in listed.items():
        resp = client.get(f"/static/vendor/{rel_path}")
        assert resp.status_code == 200, f"manifest names an unservable file: {rel_path}"
        served = "sha384-" + base64.b64encode(hashlib.sha384(resp.data).digest()).decode()
        assert served == digest, (
            f"{rel_path}: manifest says {digest}, the served bytes hash to {served}"
        )

    on_disk = {
        str(p.relative_to(VENDOR_DIR))
        for p in VENDOR_DIR.rglob("*")
        if p.is_file() and p.name not in ("VERSIONS.txt", "LICENSE.txt")
    }
    assert on_disk == set(listed), (
        f"vendored files with no manifest line: {sorted(on_disk - set(listed))}; "
        f"manifest lines with no file: {sorted(set(listed) - on_disk)}"
    )


def test_url_for_static_appends_content_hash(app):
    """Every ``url_for('static', ...)`` URL carries the file's content
    hash as ``v=``, so the URL changes exactly when the bytes change.

    This is the cache-busting mechanism from
    docs/design/css_architecture_audit.md: without it, the bundled
    deploy's 7-day ``immutable`` nginx caching can serve stale CSS/JS
    after an edit.  The expected value is recomputed independently via
    ``static_file_version`` so the assertion pins hook and helper to
    the same hash.
    """
    with app.test_request_context():
        url = url_for("static", filename="css/base.css")
    version = static_file_version(app.static_folder, "css/base.css")
    assert version is not None
    assert url == f"/static/css/base.css?v={version}"


def test_versioned_static_gets_long_lived_immutable_cache(client, app):
    """A static request whose ``v=`` matches the file's current hash
    is served with a year-long public immutable lifetime.

    Safe because the URL is content-addressed: changed bytes change
    the hash, so this cached entry can never be served against a page
    referencing newer content.  31536000 s = 365 days, the
    conventional ceiling for immutable assets
    (``_VERSIONED_STATIC_MAX_AGE_SECONDS`` in app/__init__.py).
    """
    version = static_file_version(app.static_folder, "css/base.css")
    resp = client.get(f"/static/css/base.css?v={version}")
    assert resp.status_code == 200
    cache_control = resp.headers.get("Cache-Control", "")
    assert "public" in cache_control
    assert "max-age=31536000" in cache_control
    assert "immutable" in cache_control
    assert "no-store" not in cache_control


def test_mismatched_version_param_keeps_conditional_caching(client):
    """A stale or forged ``v=`` must NOT receive the immutable
    lifetime -- the hook verifies the hash against the file first.

    Without this check, an attacker-distributed (or simply outdated)
    link could pin wrong bytes in a shared cache for a year.  The
    response still serves the asset, just with Flask's default
    conditional caching (``no-cache`` + ETag revalidation), and never
    with no-store.
    """
    resp = client.get("/static/css/base.css?v=000000000000")
    assert resp.status_code == 200
    cache_control = resp.headers.get("Cache-Control", "")
    assert "immutable" not in cache_control
    assert "no-store" not in cache_control
    # Positively pin the conditional-caching claim: Flask's default
    # static semantics are Cache-Control: no-cache plus an ETag, so a
    # future SEND_FILE_MAX_AGE_DEFAULT change cannot silently grant
    # mismatched-version requests a long lifetime.
    assert "no-cache" in cache_control
    assert resp.headers.get("ETag")


def test_rendered_page_links_versioned_stylesheets(client):
    """The login page's stylesheet links carry ``v=`` hashes
    end-to-end, proving the url_defaults hook fires during template
    rendering (not just in a bare test_request_context)."""
    html = client.get("/login").data.decode("utf-8")
    assert "/static/css/theme-steel-ink.css?v=" in html
    assert "/static/css/base.css?v=" in html
    assert "/static/js/app.js?v=" in html
