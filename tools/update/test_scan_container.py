"""Which container the daily image scan reads (:func:`tools.update.scan.container`; ruling
plan#4069): the one main's deploy script names, as quill's one fetch brings ``main`` in, not
the one the checkout the scan runs from names, which on the timer is dev's tip.  Each case is
a throwaway repository (:mod:`tools.ci.scratch`)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.ci import scratch
from tools.ci.arcs import REPO
from tools.ci.gitcmd import GitError
from tools.update import scan


def _deployed(parent: Path, main: str, checkout: str) -> Path:
    """A throwaway repository whose ``main``, as quill's fetch names it, holds the deploy script
    ``main``, and whose own checkout, at a later commit, holds the script ``checkout``."""
    parent.mkdir()
    code = scratch.repository(parent)
    shipped = scratch.commit_files(code, scratch.commit(code, "base"), "main",
                                   {scan.DEPLOY_SCRIPT: main})
    scratch.point_main(code, shipped)
    scratch.commit_files(code, shipped, "dev", {scan.DEPLOY_SCRIPT: checkout})
    return code


def _script(*lines: str) -> str:
    """A deploy script of ``lines``."""
    return "\n".join(["#!/usr/bin/env bash", *lines]) + "\n"


@pytest.mark.parametrize("lines, count", [([], 0), (['CONTAINER_NAME="${SHEKEL_CONTAINER_NAME'
                                                     ':-a}"', 'CONTAINER_NAME="${SHEKEL_'
                                                     'CONTAINER_NAME:-b}"'], 2)])
def test_production_s_container_is_the_one_main_s_deploy_script_names_once(lines, count, tmp_path):
    """The default for ``SHEKEL_CONTAINER_NAME`` in main's deploy script names the container,
    as the release reads it, whatever the checkout's own script names (ruling plan#4069); a
    script naming none, or two, is refused, though the checkout's names one."""
    renamed = _script('CONTAINER_NAME="${SHEKEL_CONTAINER_NAME:-renamed}"')
    real = (REPO / scan.DEPLOY_SCRIPT).read_text(encoding="utf-8")
    assert scan.container(_deployed(tmp_path / "real", real, renamed)) == "shekel-prod-app"
    with pytest.raises(scan.ScanFailed, match=f"names production's container {count} times"):
        scan.container(_deployed(tmp_path / "refused", _script(*lines), renamed))


def test_a_checkout_holding_no_main_reads_no_container(tmp_path):
    """A checkout whose git holds no ``main`` cannot say what production is deployed with: the
    scan fails, never reading the checkout's own script."""
    code = scratch.repository(tmp_path)
    scratch.commit_files(code, scratch.commit(code, "base"), "dev",
                         {scan.DEPLOY_SCRIPT: _script(
                             'CONTAINER_NAME="${SHEKEL_CONTAINER_NAME:-shekel-prod-app}"')})
    with pytest.raises(GitError):
        scan.container(code)
