"""A real uv, under the project's own settings, refuses a source build whose build tool no
hash covers (task plan#2575).

pyproject.toml's ``[tool.uv]`` sets ``require-build-hashes`` (task plan#2550, ruling
plan#2494): every build tool a source build installs must be one of its
``build-constraint-dependencies``, each held by its hash.
:mod:`test_image_supply_chain.test_dockerfile` reads that the line is there.  These tests run
uv on it:

* uv reads the checkout's own settings with no warning but the one the experimental setting
  draws on every run, and the control shows the read warns of a setting it cannot parse.
* A scratch project carrying the checkout's ``[tool.uv]`` tables whole, and depending on a
  source package whose build tool is a local wheel no hash covers, is built offline both ways
  uv builds one: to install it (``uv sync``: the image's, CI's and a local sync), and to read
  its metadata while resolving (``uv lock``: the Monday refresh's).  uv refuses each, and with
  the setting taken out builds each, so the refusal is the setting's.

A uv that no longer knew the setting would say so only as a warning: it cannot parse the
file's settings, and drops some of them, ``exclude-newer`` and this one among them, while
still reading each listed build tool's pin and hash, so a tool listed nowhere is installed
with no hash checked (measured 2026-11-02, uv 0.13.6, with the key misspelled).  In CI that
uv fails first at ``.github/actions/install-lock``'s ``uv sync --locked``, as the lock holds
the ``exclude-newer`` the dropped settings no longer give; these tests are the check that
names the setting, and the only one of a uv that still knows it but no longer refuses.

The uv run is the one on PATH.  In CI that is the Dockerfile's: the ``test`` job's
install-lock puts the uv ``.github/actions/dockerfile-uv`` exports after the venv's ``bin``,
which holds none, so an update of the Dockerfile's uv that drops or renames the setting fails
the pull request that brings it.  Locally it is the uv a ``uv sync`` here runs.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from tests.test_deploy._compose import REPO_ROOT

#: The project file every uv run reads its settings from, the build hashes it requires among
#: them (task plan#2550).
PYPROJECT = REPO_ROOT / "pyproject.toml"
#: The line of :data:`PYPROJECT` that requires them, as written (its ``[tool.uv]`` holds it
#: once, which ``test_image_supply_chain.test_dockerfile`` checks).
BUILD_HASHES_SETTING = "require-build-hashes = true\n"
#: The checkout's settings, as committed.
_TEXT = PYPROJECT.read_text(encoding="utf-8")
#: The source package the scratch project depends on, and the build tool its build needs.
_PACKAGE, _TOOL = "shekel-probe", "shekel-probe-backend"
#: The one version both are made at.
_VERSION = "1.0"
#: What uv says refusing the tool (uv 0.13.6).
_REFUSAL = f"Hash-checking is required, but no trusted hash was provided for: `{_TOOL}`"
#: What uv warns when it cannot read a file's settings, before going on without some of them.
_UNREAD = "Failed to parse `pyproject.toml` during settings discovery"
#: How the warning starts that uv prints on every run under the experimental setting.
_EXPERIMENTAL = "warning: The `--require-build-hashes` option is experimental"
#: Each way uv builds a source package: whether the package declares its metadata in its
#: ``[project]`` table (uv resolves it unbuilt and builds it to install it) or not (uv builds
#: it to read its metadata while resolving), and the uv command that builds it.
_BUILDS = {"to install it, by uv sync": (True, "sync"),
           "to resolve it, by uv lock": (False, "lock")}
#: A TOML table's header: ``[name]`` or ``[[name]]``, its name in group 1.
_HEADER = re.compile(r"\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?")
#: How long one uv run may take: offline, it builds one wheel by copying a file.
_UV_TIMEOUT_S = 30


def _uv_tables(pyproject: str) -> str:
    """The text of each of ``pyproject``'s tables that ``[tool.uv]`` holds, ``[tool.uv]``
    itself and every ``[tool.uv.*]`` and ``[[tool.uv.*]]``, in order."""
    kept, keeping = [], False
    for line in pyproject.splitlines(keepends=True):
        if header := _HEADER.fullmatch(line.strip()):
            keeping = header[1] == "tool.uv" or header[1].startswith("tool.uv.")
        if keeping:
            kept.append(line)
    return "".join(kept)


def _wheel(directory: Path, name: str, files: dict[str, bytes]) -> Path:
    """A wheel of ``name`` at :data:`_VERSION` for any Python, holding ``files`` and the
    metadata a wheel carries, written into ``directory``; its path."""
    dist = f"{name.replace('-', '_')}-{_VERSION}"
    files = {**files,
             f"{dist}.dist-info/METADATA":
                 f"Metadata-Version: 2.1\nName: {name}\nVersion: {_VERSION}\n".encode(),
             f"{dist}.dist-info/WHEEL":
                 b"Wheel-Version: 1.0\nGenerator: shekel-test\nRoot-Is-Purelib: true\n"
                 b"Tag: py3-none-any\n"}
    record = [f"{member},sha256="
              f"{base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()},"
              f"{len(data)}" for member, data in files.items()]
    path = directory / f"{dist}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        for member, data in files.items():
            wheel.writestr(member, data)
        wheel.writestr(f"{dist}.dist-info/RECORD",
                       "\n".join([*record, f"{dist}.dist-info/RECORD,,"]) + "\n")
    return path


def _links(root: Path, static: bool) -> None:
    """``root / "links"``, a directory for ``--find-links``: :data:`_TOOL`'s wheel, a build
    backend whose ``build_wheel`` copies out the wheel its source carries, and
    :data:`_PACKAGE`'s source distribution alone, which names that tool as its build
    requirement and carries its own built wheel, so uv must build it and installs the tool to
    do so.  Its ``[project]`` table declares its metadata where ``static``; else uv must build
    it to learn its metadata (``PKG-INFO``'s version 2.1 is one uv does not trust)."""
    links, built = root / "links", root / "built"
    links.mkdir()
    built.mkdir()
    module = _TOOL.replace("-", "_")
    package = _wheel(built, _PACKAGE, {f"{_PACKAGE.replace('-', '_')}/__init__.py": b""})
    _wheel(links, _TOOL, {f"{module}/__init__.py": (
        "import os, shutil\n"
        "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
        f"    return os.path.basename(shutil.copy({package.name!r}, wheel_directory))\n"
    ).encode()})
    project = f'\n[project]\nname = "{_PACKAGE}"\nversion = "{_VERSION}"\n' if static else ""
    source = f"{_PACKAGE.replace('-', '_')}-{_VERSION}"
    members = {
        "pyproject.toml": (f'[build-system]\nrequires = ["{_TOOL}"]\n'
                           f'build-backend = "{module}"\n{project}').encode(),
        "PKG-INFO": f"Metadata-Version: 2.1\nName: {_PACKAGE}\nVersion: {_VERSION}\n".encode(),
        package.name: package.read_bytes(),
    }
    with tarfile.open(links / f"{source}.tar.gz", "w:gz") as sdist:
        for member, data in members.items():
            info = tarfile.TarInfo(f"{source}/{member}")
            info.size = len(data)
            sdist.addfile(info, io.BytesIO(data))


def _scratch(root: Path, tables: str, static: bool) -> None:
    """A scratch project at ``root / "project"`` whose ``[tool.uv]`` is ``tables`` and whose
    one dependency is :data:`_PACKAGE`, from :func:`_links` (``static`` as there).  It
    declares each dependency group the checkout does, empty, so a ``default-groups`` of the
    checkout's names one it has."""
    _links(root, static)
    project = root / "project"
    project.mkdir()
    groups = "".join(f"{name} = []\n" for name in tomllib.loads(_TEXT)["dependency-groups"])
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "scratch"\nversion = "0"\n'
        f'requires-python = ">={sys.version_info.major}.{sys.version_info.minor}"\n'
        f'dependencies = ["{_PACKAGE}"]\n\n[dependency-groups]\n{groups}\n{tables}',
        encoding="utf-8")


def _uv(cwd: Path, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """The uv on PATH run with ``arguments`` in ``cwd``, with a cache of its own under ``root``
    (none holds the package built) and none of the caller's user configuration, ``UV_``
    variables or ``VIRTUAL_ENV``; its exit status and output."""
    uv = shutil.which("uv")
    assert uv is not None, "no uv on PATH: CI's install action puts the Dockerfile's there"
    env = {name: value for name, value in os.environ.items()
           if not name.startswith("UV_") and name != "VIRTUAL_ENV"}
    env.update(UV_CACHE_DIR=str(root / "cache"), XDG_CONFIG_HOME=str(root / "config"))
    return subprocess.run([uv, *arguments], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=_UV_TIMEOUT_S, check=False)


def _build(root: Path, command: str) -> subprocess.CompletedProcess[str]:
    """``uv lock`` or ``uv sync`` (``command``) of :func:`_scratch`'s project in ``root``,
    offline from its links alone, with the running Python."""
    return _uv(root / "project", root, command, "--offline", "--no-index",
               "--find-links", str(root / "links"), "--python", sys.executable)


def _read(directory: Path, root: Path) -> subprocess.CompletedProcess[str]:
    """``uv export --frozen --offline`` in ``directory``: uv reads its settings and its lock,
    and resolves, builds and writes nothing."""
    return _uv(directory, root, "export", "--frozen", "--offline")


def _warnings(stderr: str) -> list[str]:
    """Each warning in uv's ``stderr`` (a line starting ``warning:``) but the one the
    experimental setting draws (:data:`_EXPERIMENTAL`)."""
    return [line for line in stderr.splitlines()
            if line.startswith("warning:") and not line.startswith(_EXPERIMENTAL)]


def test_uv_reads_the_checkout_s_settings_with_no_warning(tmp_path: Path) -> None:
    """uv reads the checkout's own pyproject.toml, and the lock it writes, with no warning but
    the experimental setting's: none that it could not parse a setting, and none that a
    ``uv.toml`` beside it takes the place of its ``[tool.uv]``."""
    read = _read(REPO_ROOT, tmp_path)
    assert (read.returncode, _warnings(read.stderr)) == (0, []), read.stderr


def test_a_setting_uv_cannot_read_is_warned_of_and_its_build_goes_unchecked(
        tmp_path: Path) -> None:
    """The control for the read above: the setting's key misspelled, as a uv that no longer
    knew it would read it, and the scratch project builds :data:`_TOOL` with no hash checked,
    then the same read of it warns :data:`_UNREAD`."""
    _scratch(tmp_path, _uv_tables(_TEXT).replace(BUILD_HASHES_SETTING,
                                                 "require-build-hashess = true\n"), True)
    built = _build(tmp_path, "sync")
    assert (built.returncode, _REFUSAL in built.stderr) == (0, False), built.stderr
    read = _read(tmp_path / "project", tmp_path)
    assert [_UNREAD in warning for warning in _warnings(read.stderr)] == [True], read.stderr


@pytest.mark.parametrize("build", list(_BUILDS))
def test_uv_refuses_a_build_tool_no_hash_covers_under_the_project_s_settings(
        tmp_path: Path, build: str) -> None:
    """The scratch project carries the checkout's ``[tool.uv]`` tables whole (read back, they
    are the checkout's), and uv refuses to install :data:`_TOOL` to build :data:`_PACKAGE`,
    naming the missing hash.  Built to install it, the project first resolves unbuilt, so the
    refusal is the install's build."""
    static, command = _BUILDS[build]
    tables = _uv_tables(_TEXT)
    assert (tomllib.loads(tables)["tool"]["uv"]
            == tomllib.loads(_TEXT)["tool"]["uv"]), "the copy is not the checkout's [tool.uv]"
    _scratch(tmp_path, tables, static)
    if static:
        resolved = _build(tmp_path, "lock")
        assert resolved.returncode == 0, resolved.stderr
    refused = _build(tmp_path, command)
    assert refused.returncode == 1 and _REFUSAL in refused.stderr, (
        f"uv did not refuse a build tool no hash covers (exit {refused.returncode}):\n"
        f"{refused.stderr}")


@pytest.mark.parametrize("build", list(_BUILDS))
def test_the_same_build_passes_with_the_setting_taken_out(tmp_path: Path, build: str) -> None:
    """The control for the refusal: the same tables less the setting's line, and uv installs
    the tool unchecked and builds the package, so the refusal above is the setting's, never
    the scratch project's or its files'."""
    static, command = _BUILDS[build]
    tables = _uv_tables(_TEXT)
    assert tables.count(BUILD_HASHES_SETTING) == 1
    _scratch(tmp_path, tables.replace(BUILD_HASHES_SETTING, ""), static)
    built = _build(tmp_path, command)
    assert built.returncode == 0, built.stderr
    assert f"Built {_PACKAGE}=={_VERSION}" in built.stderr, built.stderr
