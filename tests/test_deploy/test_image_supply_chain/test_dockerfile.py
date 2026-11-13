"""Image supply-chain tests: the Dockerfile pins its base images by digest, upgrades OpenSSL in
both Python stages, installs uv.lock with uv and runs no pip, and keeps the C-34 hardening and
psycopg's C implementation (audit findings F-025, F-062, F-120); and it pins the process
timezone to the app's (parity audit finding M01).
"""

from __future__ import annotations

import re
import tomllib

import pytest

from app.utils.dates import DISPLAY_TIMEZONE
from tests.test_deploy.test_uv_build_hashes import BUILD_HASHES_SETTING, PYPROJECT

from ._helpers import (
    DOCKERFILE,
    PYTHON_BASE_PATTERN,
    PYTHON_VERSION_FILE,
    UV_STAGE_PATTERN,
    _instructions,
    _stages,
    _python_stages,
    _uv_stages,
    UV_STAGE_REFUSED,
    _runs,
    _commands,
    OPENSSL_PACKAGES,
    _upgrades_openssl,
    _runs_pip,
    _taken_from_elsewhere,
)


class TestDockerfileDigestPin:
    """Dockerfile FROM lines pin the base image by sha256 digest.

    The audit complaint (F-060 for the GHCR image, F-025 for the
    Python base) is that floating tags allow silent image swaps and
    defeat reproducible builds.  Pinning by sha256 digest closes
    that surface.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full Dockerfile source as a string."""
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_both_stages_pin_by_digest(self, text: str) -> None:
        """Both Python FROM lines must include ``@sha256:<digest>``.

        The Dockerfile is multi-stage: a builder stage that installs
        psycopg-c build deps and a runtime stage that copies only the
        venv, both on the Python base image (the uv stage before them
        supplies only the installer binary).  A digest pin on only one
        Python stage would let the other drift to whatever its tag
        resolves to at build time -- defeating reproducibility.
        """
        matches = PYTHON_BASE_PATTERN.findall(text)
        # Exactly two FROM lines are on the Python base (builder + runtime).
        assert len(matches) == 2, (
            f"expected exactly 2 ``FROM python:<X.Y>-slim@sha256:`` lines "
            f"in Dockerfile, got {len(matches)}.  Both stages must pin "
            f"to the same digest for reproducible builds (audit "
            f"findings F-025, F-060)."
        )

    def test_both_stages_share_one_digest(self, text: str) -> None:
        """The builder and runtime FROM lines reference the same digest.

        A drift between the two would mean stage 2's runtime libraries
        (libssl3t64 in particular) might come from a different OpenSSL
        revision than the one stage 1 linked psycopg-c against.  The
        symmetry is the load-bearing invariant.
        """
        stages = PYTHON_BASE_PATTERN.findall(text)
        assert len(stages) >= 2, (
            f"Dockerfile has fewer than 2 digest-pinned Python FROM lines: "
            f"{stages}"
        )
        unique = {digest for _version, digest in stages}
        assert len(unique) == 1, (
            f"Dockerfile Python FROM lines pin to different digests: "
            f"{unique}.  Stage 1 (builder) and stage 2 (runtime) must use "
            f"the same base image digest so OpenSSL revisions match "
            f"across stages."
        )

    def test_both_stages_run_the_python_version_file(self, text: str) -> None:
        """Both Python FROM tags name the minor version ``.python-version`` holds.

        ``.python-version`` is the one home of the Python version (ruling
        balance:R-BAL294): uv, CI and the local venv read it.  A FROM line
        cannot read a file, so this is where the image is held to it.  The
        build itself also refuses a mismatch (``UV_PYTHON_DOWNLOADS=never``
        leaves uv no interpreter to fetch), and ci.yml's ``image`` job builds
        the image before every merge (task plan#2366), so a mismatch is a red
        build there too; this check is the one naming the two files to move
        together.
        """
        wanted = PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()
        versions = [version for version, _digest in PYTHON_BASE_PATTERN.findall(text)]
        assert versions and all(version == wanted for version in versions), (
            f"Dockerfile Python FROM tags {versions} do not all name "
            f"``.python-version``'s {wanted!r}; move the FROM lines (tag "
            f"and digest) and ``.python-version`` in the same change."
        )

    def test_no_floating_tag_only_from(self, text: str) -> None:
        """No FROM line uses a tag without a digest pin.

        Catches a regression where a maintainer reverts to
        ``FROM python:3.14-slim`` (no digest).  The committed file
        either pins by digest or the test fails.
        """
        from_lines = [
            line.strip() for line in text.splitlines()
            if line.strip().startswith("FROM ")
        ]
        for line in from_lines:
            assert "@sha256:" in line, (
                f"FROM line lacks ``@sha256:`` digest pin: {line!r}.  "
                f"Replace with ``FROM <image>@sha256:<digest>`` "
                f"(audit findings F-025, F-060)."
            )

    def test_the_uv_stage_is_astral_s_uv_image_by_tag_and_digest(self, text: str) -> None:
        """The one stage named ``uv``, which the builder copies the installer from and CI
        exports (``.github/actions/dockerfile-uv``), is built FROM astral's uv image by its
        version tag and its digest (:data:`UV_STAGE_PATTERN`).

        A digest pin alone (:meth:`test_no_floating_tag_only_from`) passes a uv image from
        any registry.  Until task plan#2484 the install action's own reading of this line
        refused one; BuildKit, which reads it there now, refuses none.
        """
        found = _uv_stages(text)
        assert [UV_STAGE_PATTERN.fullmatch(one) is not None for one in found] == [True], (
            f"the Dockerfile's stages named ``uv``: {found}.  It must hold exactly one, "
            f"``FROM ghcr.io/astral-sh/uv:<version>@sha256:<digest> AS uv``."
        )

    @pytest.mark.parametrize("name", list(UV_STAGE_REFUSED))
    def test_the_uv_stage_check_refuses_each_other_image(self, text: str, name: str) -> None:
        """The real uv stage's FROM line replaced by each of :data:`UV_STAGE_REFUSED`: an
        image of another registry or another of astral's repositories, astral's uv by digest
        alone or tag alone, and the line twice; each fails the check."""
        real, = _uv_stages(text)
        assert text.count(real + "\n") == 1
        mutated = text.replace(real + "\n", UV_STAGE_REFUSED[name](real) + "\n")
        assert mutated != text
        assert [UV_STAGE_PATTERN.fullmatch(one) is not None
                for one in _uv_stages(mutated)] != [True], name


class TestDockerfileOpenSSLUpgrade:
    """Both Python stages run apt-get upgrade for the OpenSSL packages.

    Defense-in-depth on top of the digest pin: the digest gives
    reproducibility, the apt-get upgrade gives currency.  A new CVE
    that lands in Debian's trixie repos between digest refreshes is
    picked up on the next image build.  Audit finding F-025.
    """

    #: The two lines of each stage's RUN instruction that run the upgrade.
    UPGRADE = ("    && apt-get upgrade -y --no-install-recommends \\\n"
               "        openssl libssl3t64 openssl-provider-legacy \\\n")

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full Dockerfile source as a string."""
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_each_python_stage_runs_the_openssl_upgrade(self, text: str) -> None:
        """The builder and the runtime stage each hold ``apt-get upgrade -y`` naming every
        package F-025 names, as a command of a RUN instruction of their own
        (:func:`_upgrades_openssl`, which reads its words, not whether the shell reaches it).

        Since ruling plan#2443 the upgrade is the image's only OpenSSL control.  The
        runtime stage carries its own libssl3t64 (postgresql-client needs it), which an
        upgrade in the builder alone would leave at the base image's.  A comment naming the
        command is no RUN instruction, and a command that only names it runs nothing.
        """
        stages = _python_stages(text)
        assert len(stages) == 2, [stage[0] for stage in stages]
        missing = [stage[0] for stage in stages if not _upgrades_openssl(stage)]
        assert not missing, (
            f"these Dockerfile stages run no ``apt-get upgrade -y`` of "
            f"{sorted(OPENSSL_PACKAGES)}: {missing}.  Each stage on the Python base must "
            f"run it, so libssl3t64 in the runtime stage is also patched (audit finding "
            f"F-025)."
        )

    def test_the_upgrade_check_refuses_a_runtime_stage_that_does_not_run_it(
        self, text: str,
    ) -> None:
        """Measured on the real Dockerfile, whose header comment names the command too: the
        runtime stage's upgrade deleted, one of its packages dropped, its lines turned into
        comments inside the RUN instruction, or the command only echoed, and the runtime
        stage alone fails, the builder's upgrade still read."""
        assert text.count(self.UPGRADE) == 2, "the upgrade lines moved; re-cut these mutations"
        head, _, tail = text.rpartition(self.UPGRADE)
        for upgrade in ("", self.UPGRADE.replace(" libssl3t64", ""),
                        "".join(f"#{line}\n" for line in self.UPGRADE.splitlines()),
                        self.UPGRADE.replace("&& apt-get", "&& echo apt-get")):
            builder, runtime = _python_stages(head + upgrade + tail)
            assert _upgrades_openssl(builder), upgrade
            assert not _upgrades_openssl(runtime), upgrade


class TestDockerfileInstallsTheLock:
    """The image installs ``uv.lock`` with uv, and no pip runs in the build.

    Ruling balance:R-BAL294: every package, indirect ones included, is
    installed at the version and sha256 the lock records.  This replaces
    audit finding F-120's control (a pip upgraded past CVE-2026-1703 before
    ``pip install -r requirements.txt``): with no pip invoked in the build
    and none seeded into ``/opt/venv``, that CVE has no build-time pip to
    reach, which the old control only narrowed.  The runtime stage deletes
    the base image's own pip with ``rm`` (rulings plan#2443, plan#2446), and
    ci.yml's ``image`` job checks the built image holds none.
    """

    #: The one thing the runtime stage takes from another stage or image.
    VENV_COPY = "COPY --from=builder /opt/venv /opt/venv"

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full Dockerfile source as a string."""
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_no_pip_runs_in_the_build(self, text: str) -> None:
        """No RUN instruction runs pip (:func:`_runs_pip`).

        A ``pip install`` would install outside the lock's hashes; uv
        installs everything.
        """
        pip_runs = [one for one in _runs(_instructions(text)) if _runs_pip(one)]
        assert not pip_runs, (
            f"Dockerfile runs pip: {pip_runs}.  The image installs only "
            f"``uv.lock``, through ``uv sync --locked`` (ruling "
            f"balance:R-BAL294)."
        )

    @pytest.mark.parametrize("command", [
        "RUN pip install requests",
        "RUN pip3 install requests",
        "RUN python -m pip install requests",
        "RUN python3 -I -mpip install requests",
        "RUN uv pip install requests",
        "RUN pip-compile requirements.in",
        "RUN pipx install ruff",
        "RUN apt-get install -y --no-install-recommends python3-pip",
        "RUN apt-get update && pip install requests",
        "RUN apt-get update \\\n    && pip install requests",
        "RUN /usr/local/bin/pip install requests",
        "RUN apt-get update \\\n    && /usr/local/bin/pip3 install requests",
        'RUN "${scripts}"/pip install requests',
        "RUN version=\"$(/usr/local/bin/pip --version)\"",
        "RUN python3 /usr/local/bin/pip install requests",
        'RUN env "${scripts}"/pip3 install requests',
        "RUN rm -r /tmp/x && python3 /usr/local/bin/pip install requests",
        "RUN cp /usr/local/bin/pip /tmp/installer",
        "RUN python -m ensurepip",
        "RUN python -Im ensurepip",
        "RUN python -c 'import ensurepip; ensurepip.bootstrap()'",
        "RUN python -c 'from pip._internal.cli.main import main; main([\"install\", \"x\"])'",
        "RUN python -m venv /opt/venv",
        "RUN python -Im venv /opt/venv",
        'RUN ["python", "-m", "ensurepip"]',
    ], ids=["pip", "pip3", "python -m pip", "-mpip", "uv pip", "pip-compile", "pipx",
            "pip's Debian package", "after an operator", "on a continuation line", "by its path",
            "by its path on a continuation line", "by a variable's path",
            "in a command substitution", "its path an argument to python",
            "its path an argument to env", "its path in the command after an rm",
            "its path an argument to cp", "-m ensurepip", "-Im ensurepip",
            "ensurepip.bootstrap", "pip's own module", "-m venv, which seeds pip",
            "-Im venv", "the exec form"])
    def test_the_pip_check_refuses_each_spelling_it_reads(self, command: str) -> None:
        """Each spelling :func:`_runs_pip` reads is refused, wherever its command stands."""
        assert _runs_pip(command), command

    @pytest.mark.parametrize("command", [
        'RUN rm -r "${scripts}"/pip "${scripts}"/pip3* "${bundled}"/pip-*.whl',
        "RUN --network=none rm -r /usr/local/bin/pip",
        "RUN python -m venv --without-pip /opt/venv",
        "RUN python -c 'import ensurepip; print(ensurepip.__file__)'",
        "RUN set -o pipefail && apt-get update",
        "RUN cp pip.conf /etc/pip.conf",
    ], ids=["rm of pip's files", "rm after the RUN line's flags", "venv without pip",
            "ensurepip's path read", "pipefail", "a file named like pip's config"])
    def test_the_pip_check_passes_each_command_that_runs_no_pip(self, command: str) -> None:
        """A command that names pip's files, its module or a word starting like it, and runs
        no pip, passes :func:`_runs_pip`."""
        assert not _runs_pip(command), command

    def test_the_pip_check_passes_a_line_naming_pip_s_files_it_runs_nothing_of(
            self, text: str) -> None:
        """The runtime stage's ``rm`` of the base image's pip names pip's files and runs no
        pip, so it passes; it is a RUN instruction of the Dockerfile, so the check over the
        real file reads it."""
        deleting = [one for one in _runs(_instructions(text)) if "rm -r " in one
                    and '"${purelib}"/pip ' in one]
        assert len(deleting) == 1, deleting
        assert not _runs_pip(deleting[0]), deleting[0]

    def test_the_builder_syncs_the_lock_with_its_build_hashes(
        self, text: str,
    ) -> None:
        """The builder runs exactly ``uv sync --locked --compile-bytecode --no-install-project
        --no-default-groups --group server`` in a RUN instruction, so no flag overrides the
        project's settings and none restates one, and pyproject.toml sets uv's
        ``require-build-hashes``: with that line turned into a comment, which still names it,
        a flag added to it, or the setting gone, the check fails.

        ``--locked`` refuses a lock that no longer matches pyproject.toml,
        and ``require-build-hashes`` refuses a source build whose tools no
        hash covers (pyproject.toml's ``build-constraint-dependencies``,
        ruling plan#2494), in this sync (task plan#2550).
        """
        exact = ["uv", "sync", "--locked", "--compile-bytecode", "--no-install-project",
                 "--no-default-groups", "--group", "server"]

        def syncs(dockerfile: str) -> bool:
            builder, = (stage for stage in _stages(dockerfile)
                        if re.search(r"\sAS\s+builder$", stage[0], re.IGNORECASE))
            return any(words == exact for one in _runs(builder) for words in _commands(one))

        def requires_hashes(pyproject: str) -> bool:
            return tomllib.loads(pyproject)["tool"]["uv"].get("require-build-hashes") is True

        assert syncs(text), (
            "the Dockerfile's builder stage no longer installs with exactly "
            f"``{' '.join(exact)}``; the image would not be held to "
            "uv.lock's versions and hashes (ruling balance:R-BAL294)."
        )
        sync = "RUN uv sync --locked"
        assert text.count(sync) == 1
        assert not syncs(text.replace(sync, "# " + sync).replace(
            "\n        --no-default-groups --group server\n", "\n"))
        for flag in ("--no-require-build-hashes", "--require-build-hashes", "--no-config"):
            assert not syncs(text.replace(sync, f"{sync} {flag}")), flag
        pyproject = PYPROJECT.read_text(encoding="utf-8")
        assert requires_hashes(pyproject), (
            "pyproject.toml no longer sets ``require-build-hashes = true`` "
            "under ``[tool.uv]``; a source build could install a build tool "
            "no hash covers (ruling plan#2494)."
        )
        assert pyproject.count(BUILD_HASHES_SETTING) == 1
        assert not requires_hashes(pyproject.replace(BUILD_HASHES_SETTING, ""))

    def test_the_runtime_stage_takes_only_the_builder_s_venv(self, text: str) -> None:
        """The runtime stage is built ``FROM`` the Python base and takes from another stage
        or image only the builder's venv (:func:`_taken_from_elsewhere`), so no copy of the
        uv stage's or the builder's uv.

        The installer is a build tool; an image that carried it would
        hand anything with code execution in the container a way to
        install packages.  What the venv itself holds is not read here:
        ci.yml's ``image`` job finds its packages exactly the lock's
        runtime and server set, and finds no file by uv's names in the
        built image, so neither a uv the builder copied into the venv nor
        the uv package passes (task plan#2543).
        """
        runtime = _stages(text)[-1]
        assert _taken_from_elsewhere(runtime) == [self.VENV_COPY], (
            "the runtime stage takes something from another stage or image "
            "besides the builder's /opt/venv, or no longer takes the venv: "
            f"{_taken_from_elsewhere(runtime)}.  uv must stay in the builder."
        )

    @pytest.mark.parametrize("line", [
        "COPY --from=uv /uv /usr/local/bin/uv",
        "COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:" + "a" * 64 + " /uv /usr/local/bin/uv",
        "COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv",
        "COPY --from=0 /uv /usr/local/bin/uv",
        "copy --from=uv /uv /usr/local/bin/uv",
        "RUN --mount=type=bind,from=uv,source=/uv,target=/mnt/uv cp /mnt/uv /usr/local/bin/",
        "RUN --mount=type=cache,id=uvx,target=/c cp /c/uv /usr/local/bin/uv",
        "ADD https://example.com/uv /usr/local/bin/uv",
    ], ids=["the uv stage", "the uv image", "the builder's uv", "a stage by its index",
            "lowercase", "a RUN mount", "a cache mount shared by its id", "an ADD"])
    def test_the_runtime_check_refuses_each_way_it_reads_of_taking_uv(
            self, text: str, line: str) -> None:
        """Each line added to the real runtime stage after its venv copy is refused."""
        assert text.count(self.VENV_COPY + "\n") == 1
        mutated = text.replace(self.VENV_COPY + "\n", f"{self.VENV_COPY}\n{line}\n")
        assert _taken_from_elsewhere(_stages(mutated)[-1]) == [self.VENV_COPY, line]

    def test_the_runtime_check_refuses_a_stage_built_on_another_and_a_venv_copy_gone(
            self, text: str) -> None:
        """The runtime stage built ``FROM builder`` (which holds uv), its venv copy widened to
        all of ``/opt`` or gone, each fails; a comment naming a copy of uv passes."""
        runtime_from = _stages(text)[-1][0]
        assert text.count(runtime_from + "\n") == 1
        for mutated in (text.replace(runtime_from + "\n", "FROM builder\n"),
                        text.replace(self.VENV_COPY, "COPY --from=builder /opt /opt"),
                        text.replace(self.VENV_COPY + "\n", "")):
            assert _taken_from_elsewhere(_stages(mutated)[-1]) != [self.VENV_COPY]
        commented = text.replace(self.VENV_COPY + "\n",
                                 f"{self.VENV_COPY}\n# COPY --from=uv /uv /usr/local/bin/uv\n")
        assert _taken_from_elsewhere(_stages(commented)[-1]) == [self.VENV_COPY]


class TestDockerfilePreservesC34Invariants:
    """The C-36 refresh must not regress the C-34 / C-35 hardening.

    C-34 added the ``/home/shekel/app/state`` pre-create + chown for
    the seed sentinel volume.  A careless rewrite of the Dockerfile
    could drop those lines.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full Dockerfile source as a string."""
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_state_dir_is_precreated(self, text: str) -> None:
        """``/home/shekel/app/state`` is created and shekel-owned.

        See tests/test_deploy/test_seed_credential_hygiene.py
        (TestComposeAppStateVolume) for the full rationale.  Repeated
        here so a C-36-only test run also catches the regression.
        """
        assert "/home/shekel/app/state" in text, (
            "Dockerfile no longer pre-creates /home/shekel/app/state; "
            "regresses C-34 / F-022."
        )
        assert "chown shekel:shekel /home/shekel/app/state" in text, (
            "Dockerfile no longer chowns /home/shekel/app/state to shekel; "
            "regresses C-34 / F-022. Polyglot CI/DF-03 narrowed the prior "
            "recursive chown -R /home/shekel/app to the two dirs that need "
            "it (state + /var/www/static); state must stay shekel-owned so "
            "the volume inherits shekel ownership on first mount."
        )

    def test_runs_as_unprivileged_shekel_user(self, text: str) -> None:
        """``USER 1000:1000`` appears so Gunicorn runs as the
        unprivileged shekel user, created at that fixed uid/gid.
        Required for the C-35 cap_drop ALL + no-new-privileges
        hardening to remain effective.  Numeric rather than by name
        since the 2026-09-19 dependency refresh (hadolint DL3066): a
        runtime enforcing run-as-non-root can verify a number without
        the image's passwd file, and the developer confirmed the
        expected text changed.
        """
        assert "USER 1000:1000" in text, (
            "Dockerfile no longer drops to the shekel user (uid 1000); "
            "the C-35 cap_drop ALL hardening assumes the process is "
            "already non-root.  Restore ``USER 1000:1000`` before EXPOSE."
        )
        assert "useradd --create-home --uid 1000 --gid 1000 shekel" in text, (
            "The shekel user is no longer created at the fixed uid 1000 "
            "that ``USER 1000:1000`` names; the two lines must agree."
        )

    def test_healthcheck_present(self, text: str) -> None:
        """The HEALTHCHECK directive survives the C-36 refresh.

        A container run from the image reports a health status even with
        no compose file's ``healthcheck:`` over it.
        """
        assert "HEALTHCHECK" in text, (
            "Dockerfile lost its HEALTHCHECK directive; a container run from "
            "the image with no compose healthcheck would report no health."
        )


class TestDockerfileRequiresPsycopgC:
    """The runtime stage REQUIRES psycopg's C implementation.

    Ruling balance:R-BAL209 chose ``psycopg[c]`` compiled against the image's
    libpq, and psycopg falls back to its pure-Python implementation silently
    when the C one will not import.  ``PSYCOPG_IMPL=c`` turns that fallback
    into an import failure, so an image without psycopg-c cannot start
    rather than running slower unnoticed.  ci.yml's ``image`` job builds the
    image before every merge and refuses one that does not run the C
    implementation (task plan#2366), but an image that holds psycopg-c runs it
    with this line or without it, so that job cannot see the line dropped:
    this text assertion is what holds it.
    """

    def test_the_runtime_stage_sets_psycopg_impl_c(self) -> None:
        """``ENV PSYCOPG_IMPL=c`` sits after the LAST ``FROM``, the runtime stage.

        A line in the builder stage would not reach the image that runs.
        """
        runtime_stage = DOCKERFILE.read_text(encoding="utf-8").rsplit(
            "\nFROM ", 1,
        )[1]
        assert "\nENV PSYCOPG_IMPL=c\n" in runtime_stage, (
            "the Dockerfile's runtime stage no longer sets "
            "``ENV PSYCOPG_IMPL=c``; an image without psycopg-c would serve "
            "every query through psycopg's pure-Python fallback unnoticed "
            "(ruling balance:R-BAL209)."
        )


class TestDockerfilePinsTheAppsTimezone:
    """The runtime stage pins the process timezone to the app's civil zone.

    The app still reads the process clock as its civil day in places
    (``date.today()``), and the base image's default zone is UTC, so an image
    without the pin turns the user's day over at 20:00 New York (parity audit
    finding M01; rulings plan#3807, plan#3811).  The zone is the one
    ``app.utils.dates.DISPLAY_TIMEZONE`` names, read here rather than spelled
    a second time, so a move of the app's zone that leaves this line behind
    fails.  The clock rework, plan#1068, deletes the pin and this class once
    each user's own zone answers every civil-day read (ruling plan#3857).
    """

    def test_the_runtime_stage_sets_tz_to_the_display_timezone(self) -> None:
        """``ENV TZ=`` the zone's key is an instruction of the LAST stage, the one the
        image runs (:func:`_stages`), in the one-line ``ENV NAME=value`` form the
        rehearsal kit reads (``tools/rehearsal/bench.py``'s ``_IMAGE_ENV``).

        A line in the builder stage would not reach the image that runs.
        """
        wanted = f"ENV TZ={DISPLAY_TIMEZONE.key}"
        runtime = _stages(DOCKERFILE.read_text(encoding="utf-8"))[-1]
        assert wanted in runtime, (
            f"the Dockerfile's runtime stage does not set ``{wanted}``; the "
            "image's processes would run on UTC and turn the user's day over "
            "hours early each night (parity audit finding M01)."
        )
