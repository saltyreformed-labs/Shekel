"""The runner's ``gh`` faked, for the tests of the modules that read and write this repository's
issues by it (:mod:`tools.update.due`, :mod:`tools.update.scan`): as a recorder of what each
module's own calls hand it (:class:`Issues`), or as a program first on PATH, which the real
listing and sender (:func:`tools.update.alert.listed`, :func:`tools.update.alert.sent`) run
(:func:`on_path`).  What the real ``gh`` does with each call is measured in
:mod:`tools.update.alert`'s notes."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

#: The repository each fake answers for, and the issue link it answers each write with.
REPOSITORY = "owner/shekel"
LINK = "https://github.com/owner/shekel/issues/9"


class Issues:
    """The listing and the sender as a module's run calls them: each write recorded and answered
    with :data:`LINK`, and the listing answered with ``issues``, a line each, or a refusal."""

    def __init__(self, *issues: str, listing_status: int = 0, refused=()):
        """Hold the listing's lines, its status and each issue number whose write is refused,
        None for a new issue's."""
        self.issues, self.listing_status, self.refused = issues, listing_status, refused
        self.written = []

    def listing(self, named):
        """The listing of the open issues of the repository ``named``."""
        assert named == REPOSITORY
        return subprocess.CompletedProcess(
            ["gh"], self.listing_status, "".join(f"{issue}\n" for issue in self.issues),
            "gh: Bad credentials (HTTP 401)\n" if self.listing_status else "")

    def send(self, named, body, number=None):
        """Record a write to the repository ``named``; answer it."""
        assert named == REPOSITORY
        self.written.append((number, body))
        if number in self.refused:
            return subprocess.CompletedProcess(["gh"], 1, "", "gh: refused (HTTP 403)\n")
        return subprocess.CompletedProcess(["gh"], 0, LINK + "\n", "")


def on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, listing: str = "") -> None:
    """A ``gh`` first on PATH that records each call's arguments and input, a directory of
    files per call, answers a listing with ``listing`` and any other call with the link; and
    the token and :data:`REPOSITORY` set, as its job sets them."""
    (tmp_path / "listing").write_text(listing, encoding="utf-8")
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\n'
                  'call="$(dirname "$0")/call.$$"\n'
                  'printf "%s\\n" "$@" > "${call}.args"\n'
                  'case "$*" in\n'
                  f'  *--paginate*) cat "{tmp_path}/listing" ;;\n'
                  f'  *) cat > "${{call}}.input"; echo {LINK} ;;\n'
                  'esac\n', encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_TOKEN", "t0k")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)


def calls(tmp_path: Path) -> list[tuple[list[str], object]]:
    """Each call the ``gh`` of :func:`on_path` recorded: its arguments, and the JSON it read when
    it read any, in no set order but the sorted one of what a test compares."""
    found = []
    for args in tmp_path.glob("call.*.args"):
        written = args.with_suffix(".input")
        found.append((args.read_text(encoding="utf-8").splitlines(),
                      json.loads(written.read_text(encoding="utf-8"))
                      if written.exists() else None))
    return sorted(found, key=lambda call: call[0])
