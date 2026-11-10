"""
Shekel Budget App -- Root-Scope Static Passthrough and Asset Versioning

Serves a small number of static files at the application root
rather than under ``/static/``, and owns the static-asset
content-hash helpers used for cache busting: the service worker's
cache name (:func:`_static_asset_version`, a hash over every cached
asset) and the per-file ``v=`` URL parameter
(:func:`static_file_version`, appended to every
``url_for('static', ...)`` URL by ``_register_static_versioning``
in ``app/__init__.py``).

Two root-scope entries are served here.  ``/sw.js`` -- the browser
scopes a service worker to the directory its file is served from, so
a worker at ``/static/sw.js`` would only see ``/static/...`` requests
and could not intercept app-route fetches.  Hosting the worker at
``/sw.js`` widens the scope to ``/`` so the static-asset cache also
covers requests issued from any page in the app.  ``/manifest.json``
-- the PWA manifest, rendered rather than served static so its icon
``src`` URLs carry the same content-hash ``?v=`` parameter every
other static asset URL gets (see :func:`web_manifest`); a plain
string inside the static JSON is invisible to the versioning hook.

The route is exempt from Flask-Limiter for the same reason
``/health`` is: the browser may request ``/sw.js`` on every page
load to check for an updated worker (per the SW HTTP cache rules),
and rate-limiting that path would surface as silent SW staleness.
The file the route serves contains no user data, so the rate-limit
budget gains nothing from defending it.

The served worker's cache name is versioned by content: the
``__ASSET_VERSION__`` placeholder in ``sw.js`` is replaced at serve
time with a short hash of every cached static asset (see
:func:`_static_asset_version`).  The hash changes whenever any of
those assets changes, which changes the worker bytes the browser
sees and makes the worker's ``activate`` handler evict the prior
cache, so returning users pick up changed CSS/JS without a manual
cache-name bump.
"""

import hashlib
import json
from pathlib import Path

from flask import Blueprint, Response, current_app, url_for
from werkzeug.security import safe_join

from app.rate_limits import limiter


static_pass_bp = Blueprint("static_pass", __name__)

# Placeholder token embedded in app/static/sw.js, replaced at serve
# time with the content hash below.  Must match the literal in sw.js.
_VERSION_PLACEHOLDER = "__ASSET_VERSION__"

# Static subdirectories and root-level files whose contents the
# service worker caches.  These MUST mirror STATIC_PREFIXES in
# app/static/sw.js: the version hash covers exactly the files the
# worker will cache, so a change to any cached asset (and nothing
# else) changes the cache name.  No root-level files are SW-cached
# today: the PWA manifest moved to the /manifest.json route (which
# versions its icon URLs) and is served fresh, not from the SW cache.
_CACHED_STATIC_DIRS = ("vendor", "css", "js", "img", "fonts")
_CACHED_STATIC_FILES: tuple[str, ...] = ()

# Hex-digest prefix length used as the version token.  48 bits is
# ample to keep an accidental collision between two distinct asset
# sets effectively impossible for a single application.
_VERSION_HEX_LEN = 12

# Memoized version per static folder.  The cached assets do not change
# within a running process (a new deploy is a new process), so the
# tree walk runs once per worker rather than on every /sw.js request.
# Deliberate asymmetry with _FILE_VERSION_CACHE below: per-file ``v=``
# hashes re-key on mtime so the dev server tracks in-place edits,
# while this whole-tree hash stays fixed for the process -- in dev the
# SW cache name can lag an edit until restart, which is harmless (new
# ``v=`` URLs miss the SW cache and fetch fresh).
_VERSION_CACHE: dict[str, str] = {}


def _static_asset_version(static_folder: str) -> str:
    """Return a short content hash over the service-worker-cached assets.

    Walks the subdirectories and files named in ``_CACHED_STATIC_DIRS``
    and ``_CACHED_STATIC_FILES`` (which mirror ``STATIC_PREFIXES`` in
    ``app/static/sw.js``) and folds each file's path (relative to
    ``static_folder``) and its bytes into a SHA-256 digest, processing
    files in sorted-path order.  Sorting makes the digest deterministic
    across processes and machines; hashing the bytes makes it change
    exactly when a cached asset changes.

    Args:
        static_folder: Absolute path to the Flask static folder.

    Returns:
        The first ``_VERSION_HEX_LEN`` hex characters of the digest,
        memoized per ``static_folder`` so repeated calls do not re-walk
        the tree.
    """
    cached = _VERSION_CACHE.get(static_folder)
    if cached is not None:
        return cached

    root = Path(static_folder)
    files: list[Path] = []
    for subdir in _CACHED_STATIC_DIRS:
        directory = root / subdir
        if directory.is_dir():
            files.extend(p for p in directory.rglob("*") if p.is_file())
    for name in _CACHED_STATIC_FILES:
        candidate = root / name
        if candidate.is_file():
            files.append(candidate)

    digest = hashlib.sha256()
    for path in sorted(files):
        # Relative path keeps the hash independent of the install
        # location; the bytes capture the actual content.
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())

    version = digest.hexdigest()[:_VERSION_HEX_LEN]
    _VERSION_CACHE[static_folder] = version
    return version


# Memoized per-file version for ``static_file_version``: absolute file
# path -> (mtime at hash time, hash).  Keying on mtime means the dev
# server re-hashes a file when it is edited in place, while production
# hashes each file exactly once (a deployed image's static files never
# change without a new process).
_FILE_VERSION_CACHE: dict[str, tuple[float, str]] = {}


def static_file_version(static_folder: str, filename: str) -> str | None:
    """Return a short content hash for one static file, or ``None``.

    Backs the ``v=`` query parameter appended to every
    ``url_for('static', ...)`` URL (see ``_register_static_versioning``
    in ``app/__init__.py``): the URL changes exactly when the file's
    bytes change, so long-lived HTTP caching (nginx's ``expires 7d`` +
    ``immutable`` in the bundled deploy; the verified-version branch of
    the after-request hook when Flask serves static in shared mode)
    can never pin a stale asset against a fresh page.

    Args:
        static_folder: Absolute path to the Flask static folder.
        filename: Asset path relative to ``static_folder``, exactly as
            passed to ``url_for('static', filename=...)``.

    Returns:
        The first ``_VERSION_HEX_LEN`` hex characters of the file's
        SHA-256 digest, or ``None`` when ``filename`` does not resolve
        to a file inside ``static_folder`` (missing file or a
        path-escaping name) -- callers then emit the URL unversioned,
        preserving the pre-versioning behavior.
    """
    joined = safe_join(static_folder, filename)
    if joined is None:
        return None
    path = Path(joined)
    try:
        mtime = path.stat().st_mtime
        cached = _FILE_VERSION_CACHE.get(str(path))
        if cached is not None and cached[0] == mtime:
            return cached[1]
        digest = hashlib.sha256(path.read_bytes())
    except OSError:
        # Missing file, or it vanished between stat and read; emit the
        # unversioned URL rather than failing the page render.
        return None
    version = digest.hexdigest()[:_VERSION_HEX_LEN]
    _FILE_VERSION_CACHE[str(path)] = (mtime, version)
    return version


@static_pass_bp.route("/sw.js")
@limiter.exempt
def service_worker() -> Response:
    """Serve ``app/static/sw.js`` at the root scope ``/sw.js``.

    Reads the on-disk worker and substitutes the ``__ASSET_VERSION__``
    placeholder with the content hash from
    :func:`_static_asset_version`, so the worker's cache name tracks the
    deployed static assets.  Serving at ``/sw.js`` (rather than
    redirecting to ``/static/sw.js``) keeps the request path at the root
    so the browser scopes the registered worker to ``/``.

    The response is built directly rather than via
    ``send_from_directory`` because the body is the substituted text,
    not the raw file.  ``Content-Type: application/javascript`` is set
    explicitly: some browsers reject a worker served with the generic
    ``text/javascript`` MIME type.  The app-wide after-request hook adds
    ``Cache-Control: no-store`` for this non-``static`` endpoint, so the
    browser always re-checks for an updated worker.

    Returns:
        A Flask ``Response`` with the version-substituted worker source
        and an explicit JavaScript MIME type.
    """
    static_folder = current_app.static_folder
    version = _static_asset_version(static_folder)
    source = (Path(static_folder) / "sw.js").read_text(encoding="utf-8")
    body = source.replace(_VERSION_PLACEHOLDER, version)
    return Response(body, mimetype="application/javascript")


@static_pass_bp.route("/manifest.json")
@limiter.exempt
def web_manifest() -> Response:
    """Serve the PWA manifest with content-versioned icon URLs.

    Reads ``app/static/manifest.json`` and rewrites each icon ``src``
    through ``url_for('static', ...)`` so the URL carries the same
    ``?v=<content hash>`` parameter ``_register_static_versioning``
    appends to every other static asset (see
    :func:`static_file_version`).  A plain-string ``src`` inside the
    static JSON is invisible to that hook, so a re-baked icon could be
    pinned stale for up to the static cache lifetime; routing the
    manifest through ``url_for`` closes that gap (the accepted residue
    in ``docs/design/css_architecture_audit.md`` section 5).

    Mirrors :func:`service_worker`: the body is a transform of an
    on-disk file, so it is built directly rather than served static.
    The app-wide after-request hook adds ``Cache-Control: no-store`` to
    this non-``static`` endpoint, so the browser re-reads the manifest
    and picks up a changed icon URL rather than caching the manifest
    itself.  Exempt from Flask-Limiter for the same reason as
    :func:`service_worker`: a public, no-user-data asset the browser
    fetches on its own, where a 429 would surface as PWA-install
    flakiness rather than protect anything.

    Returns:
        A Flask ``Response`` with the icon-versioned manifest and the
        ``application/manifest+json`` MIME type.
    """
    static_folder = current_app.static_folder
    manifest = json.loads(
        (Path(static_folder) / "manifest.json").read_text(encoding="utf-8")
    )
    # static_url_path is '/static'; the trailing slash gives the URL
    # prefix to strip so each icon src maps back to the filename its
    # ?v= content hash is keyed on.
    static_prefix = f"{current_app.static_url_path}/"
    for icon in manifest.get("icons", []):
        src = icon.get("src", "")
        if src.startswith(static_prefix):
            icon["src"] = url_for("static", filename=src[len(static_prefix):])
    body = json.dumps(manifest, indent=2)
    return Response(body, mimetype="application/manifest+json")
