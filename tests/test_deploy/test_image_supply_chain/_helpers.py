"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_deploy/test_image_supply_chain.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import yaml

from tests._repo_root import REPO_ROOT


# ── Files under assertion ──────────────────────────────────────────
DOCKERFILE = REPO_ROOT / "Dockerfile"


DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"


DEV_COMPOSE = REPO_ROOT / "docker-compose.dev.yml"


PROD_COMPOSE_OVERRIDE = REPO_ROOT / "deploy" / "docker-compose.prod.yml"


ENV_EXAMPLE = REPO_ROOT / ".env.example"


GITIGNORE = REPO_ROOT / ".gitignore"


DEPLOY_README = REPO_ROOT / "deploy" / "README.md"


DOCKER_PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"


# The Python base image's FROM lines.  We do not pin to a specific
# digest here because the digest is expected to rotate over time; the
# assertion is that the digest pin EXISTS, not that it points at one
# specific image.
#: A FROM line on the Python base image: its ``X.Y`` tag and its digest.
PYTHON_BASE_PATTERN = re.compile(
    r"^FROM\s+python:(\d+\.\d+)-slim@sha256:([0-9a-f]{64})\b",
    re.MULTILINE,
)


#: The one home of the Python version (ruling balance:R-BAL294).
PYTHON_VERSION_FILE = REPO_ROOT / ".python-version"


#: The uv stage's FROM line: astral's uv image by its version tag and its digest.
UV_STAGE_PATTERN = re.compile(
    r"FROM\s+ghcr\.io/astral-sh/uv:[\w.-]+@sha256:[0-9a-f]{64}\s+AS\s+uv", re.IGNORECASE,
)


# Pattern matching the ``SHEKEL_IMAGE_DIGEST`` interpolation in the
# prod override.  Required-form (``:?``) is what makes the missing-
# digest case a hard parse failure instead of a silent fallback to
# ``:latest``.
REQUIRED_DIGEST_INTERPOLATION_PATTERN = re.compile(
    r"image:\s+ghcr\.io/saltyreformed-labs/shekel@\$\{SHEKEL_IMAGE_DIGEST:\?[^}]+\}",
)


DOCKER_SUBPROCESS_TIMEOUT_S = 60


def _docker_available() -> bool:
    """Return True when the ``docker`` CLI is on PATH and the daemon
    answers ``docker info``.  Mirrors the helper in tests/test_deploy's
    test_deploy_configs.py / test_container_hardening.py modules.
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


# ──────────────────────────────────────────────────────────────────
# F-025 + F-062 + F-120: Dockerfile pins by digest, runs OpenSSL
#   upgrade in both stages, and runs no pip (it installs uv.lock).
# ──────────────────────────────────────────────────────────────────

#: The Dockerfile's instructions (Docker's reference): the first word of every line that is
#: not a comment, a blank or a continuation.
DOCKERFILE_INSTRUCTIONS = frozenset({
    "ADD", "ARG", "CMD", "COPY", "ENTRYPOINT", "ENV", "EXPOSE", "FROM", "HEALTHCHECK",
    "LABEL", "MAINTAINER", "ONBUILD", "RUN", "SHELL", "STOPSIGNAL", "USER", "VOLUME",
    "WORKDIR",
})


def _instructions(text: str) -> list[str]:
    """Each instruction of the Dockerfile ``text``, its continuation lines included, by
    Docker's rules for its default ``\\`` escape: a comment line is dropped wherever it
    stands, inside an instruction too, and so is a blank line inside one.

    A line it cannot place fails the read rather than going unread: one that starts no
    instruction, and a heredoc (``<<``), whose body it does not read.
    """
    instructions, lines = [], []
    for line in text.splitlines():
        if line.lstrip().startswith("#") or (lines and not line.strip()):
            continue
        lines.append(line)
        if not re.search(r"\\[ \t]*$", line):
            instructions.append("\n".join(lines))
            lines = []
    instructions = [one for one in [*instructions, "\n".join(lines)] if one.strip()]
    for instruction in instructions:
        assert instruction.split()[0].upper() in DOCKERFILE_INSTRUCTIONS, instruction
        assert "<<" not in instruction, f"a heredoc this read cannot see into: {instruction}"
    return instructions


def _stages(text: str) -> list[list[str]]:
    """The Dockerfile ``text``'s stages, each its instructions from its ``FROM`` on."""
    stages: list[list[str]] = []
    for instruction in _instructions(text):
        if re.match(r"FROM\s", instruction, re.IGNORECASE):
            stages.append([])
        if stages:
            stages[-1].append(instruction)
    return stages


def _python_stages(text: str) -> list[list[str]]:
    """The stages built ``FROM`` the Python base image (:data:`PYTHON_BASE_PATTERN`)."""
    return [stage for stage in _stages(text) if PYTHON_BASE_PATTERN.match(stage[0])]


def _uv_stages(text: str) -> list[str]:
    """The ``FROM`` instruction of each stage of the Dockerfile ``text`` named ``uv``."""
    return [stage[0] for stage in _stages(text)
            if re.search(r"\sAS\s+uv\s*$", stage[0], re.IGNORECASE)]


#: Each uv stage the uv-stage check refuses, made from the real FROM line.
UV_STAGE_REFUSED = {
    "another registry": lambda line: line.replace("ghcr.io/", "evil.example/"),
    "another of astral's repositories": lambda line: line.replace("/uv:", "/uv-tools:"),
    "a digest alone": lambda line: re.sub(r":[\w.-]+@", "@", line),
    "a tag alone": lambda line: re.sub(r"@sha256:[0-9a-f]{64}", "", line),
    "two uv stages": lambda line: f"{line}\n{line}",
}


def _runs(instructions: list[str]) -> list[str]:
    """The RUN instructions among ``instructions``."""
    return [one for one in instructions if re.match(r"RUN\s", one, re.IGNORECASE)]


def _commands(instruction: str) -> list[list[str]]:
    """The words of each command a RUN ``instruction`` runs, each without the variable
    assignments that lead it and the RUN line's own ``--`` flags.

    In the shell form a command is what ``;``, ``&``, ``|``, ``(``, ``)`` and a backtick
    separate (``&&``, ``||``, a subshell and ``$(...)`` among them); the exec form is one
    command, its JSON punctuation dropped.
    """
    shell = re.sub(r"\\[ \t]*\n", " ", instruction)
    shell = re.sub(r"^RUN(?:\s+--\S+)*\s*", "", shell, flags=re.IGNORECASE)
    if shell.startswith("["):
        shell = re.sub(r'[\[\]",]', " ", shell)
    commands = []
    for part in re.split(r"[;&|()`]", shell):
        words = part.split()
        while words and re.fullmatch(r"\w+=\S*", words[0]):
            del words[0]
        if words:
            commands.append(words)
    return commands


#: The OpenSSL packages each Python stage upgrades: the set audit finding F-025 names.
OPENSSL_PACKAGES = frozenset({"openssl", "libssl3t64", "openssl-provider-legacy"})


def _upgrades_openssl(stage: list[str]) -> bool:
    """Whether a RUN instruction of ``stage`` holds the command ``apt-get upgrade -y`` naming
    every one of :data:`OPENSSL_PACKAGES`.

    It reads the command's words, not the shell's control flow or apt's other options: an
    upgrade after ``true ||``, or one given ``-s`` (simulate), passes it.
    """
    return any(words[:2] == ["apt-get", "upgrade"] and "-y" in words
               and OPENSSL_PACKAGES <= set(words)
               for instruction in _runs(stage) for words in _commands(instruction))


#: Python's ``-m``, alone or the last of a group of one-letter options (``-Im``).
RUN_MODULE = r"(?<![\w-])-[A-Za-z]*m\s*"


#: pip by name in a command, anywhere but as a path's segment after a ``/``, in
#: ``--without-pip`` or as ``pip.conf``: ``pip``, ``pip3``, ``pip3.N``, ``pip-`` anything
#: (pip-compile), ``pipx``, Debian's ``python3-pip`` and pip's modules (``pip._internal``),
#: ``uv pip`` and ``python -m pip`` among them; ``-m`` (:data:`RUN_MODULE`) with pip or
#: ensurepip; and an ensurepip function called (``ensurepip.bootstrap()``), where reading
#: ``ensurepip.__file__`` runs nothing.
PIP_BY_NAME = re.compile(rf"(?<![/\w])(?<!--without-)pip(?:x|3[\d.]*)?(?!\w|\.conf\b)"
                         rf"|{RUN_MODULE}(?:ensure)?pip\b|ensurepip\.(?!__file__\b)")


#: A path to pip's files: ``pip`` (its package directory or its command), ``pip3`` and
#: ``pip3.N`` with or without a glob, ``pip-*`` (its dist-info, its wheel, pip-compile), and
#: ``pipx``.
PIP_PATH = re.compile(r"/pip(?:x|[\d.]*\*?|-\S*)?(?=[\s\"'/]|$)")


def _runs_pip(instruction: str) -> bool:
    """Whether a RUN ``instruction`` runs pip, by a spelling read here (rulings plan#2443,
    plan#2446).

    A command (:func:`_commands`) is refused that names pip (:data:`PIP_BY_NAME`), runs
    ``-m venv`` without ``--without-pip`` (venv seeds pip by running ensurepip), or names a
    path to pip's files (:data:`PIP_PATH`) as anything but ``rm`` does: ``rm`` deletes them
    and runs nothing, so the runtime stage's delete of the base image's pip passes, while
    ``python3 /usr/local/bin/pip install`` does not.  A spelling this does not read (a path
    built at run time, ``from ensurepip import bootstrap``, ``import ensurepip as e``,
    ``virtualenv``, ``uv venv --seed``, a script that runs pip) passes it: what reached the
    image is ci.yml's ``image`` job's to grade, by finding no pip in it and its venv exactly
    the lock's.
    """
    for words in _commands(instruction):
        command = " ".join(words)
        if (PIP_BY_NAME.search(command)
                or (re.search(rf"{RUN_MODULE}venv\b", command) and "--without-pip" not in words)
                or (words[0] != "rm" and PIP_PATH.search(command))):
            return True
    return False


def _taken_from_elsewhere(stage: list[str]) -> list[str]:
    """What ``stage`` takes from another stage or image: its ``FROM`` unless the Python base
    (:data:`PYTHON_BASE_PATTERN`), each instruction naming one by ``from=`` (``COPY --from=``,
    a RUN mount's ``from=``), each RUN with any mount (a cache mount shares its files across
    stages by its id), and each ADD, which can also fetch a URL.  A file a RUN line fetches
    itself (``curl``) is not read."""
    return [one for at, one in enumerate(stage)
            if (at == 0 and not PYTHON_BASE_PATTERN.match(one))
            or re.search(r"\bfrom=|--mount\b", one, re.IGNORECASE)
            or re.match(r"ADD\s", one, re.IGNORECASE)]


# ──────────────────────────────────────────────────────────────────
# BAL-653: every compose image pinned by tag and digest.
# ──────────────────────────────────────────────────────────────────

#: A pinned image: a tag, then the digest of its bytes (``.claude/rules/deploy.md``).
TAG_AND_DIGEST_PATTERN = re.compile(r"^[^@\s]+:[\w.-]+@sha256:[0-9a-f]{64}$")


#: The images whose own line carries no digest, each as ``(compose file, service)``: the
#: app's.  The base file names it ``:latest`` for an end user's ``docker compose up``, and
#: the prod override pins it through ``SHEKEL_IMAGE_DIGEST``
#: (:class:`TestProdComposeOverridePinsByDigest`).
PINNED_ELSEWHERE = frozenset({
    ("docker-compose.yml", "app"),
    ("deploy/docker-compose.prod.yml", "app"),
})


def _compose_images() -> tuple[list[str], dict[tuple[str, str], str]]:
    """Every committed compose file (``git ls-files``, so a new one anywhere in the tree is
    read too) and every ``image:`` its services name, by ``(compose file, service)``."""
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", "*compose*.yml", "*compose*.yaml"],
        capture_output=True, text=True, check=True, timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
    )
    files = listed.stdout.split()
    images: dict[tuple[str, str], str] = {}
    for name in files:
        with (REPO_ROOT / name).open(encoding="utf-8") as fh:
            services = yaml.safe_load(fh).get("services") or {}
        for service, spec in services.items():
            if "image" in spec:
                images[(name, service)] = spec["image"]
    return files, images
