"""Container boundary coverage using only fake Singularity and EICrecon executables."""

import os
import subprocess
import time
from pathlib import Path

import pytest


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def container_fixture(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "workspace"
    root.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()
    prefix = root / "eicrecon"
    (prefix / "bin").mkdir(parents=True)
    plugin = root / "plugin"
    (plugin / "plugins").mkdir(parents=True)
    image = root / "image.sif"
    image.touch()
    geometry = root / "geometry"
    geometry.mkdir()
    (geometry / "epic_craterlake.xml").touch()
    (plugin / "plugins" / "quantum_centauro.so").touch()
    _write(prefix / "bin" / "eicrecon-this.sh", """#!/usr/bin/env bash
if [[ ${FAKE_EICRECON_SETUP_EXIT:-0} != 0 ]]; then
    return "$FAKE_EICRECON_SETUP_EXIT"
fi
export PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd):$PATH"
export FROM_LOCAL_EICRECON=yes
""")
    _write(prefix / "bin" / "eicrecon", """#!/usr/bin/env bash
printf 'eicrecon:%s\n' "$PWD" >> "$LIFECYCLE"
if [[ ${EICRECON_WAIT_FOR_TERM:-0} == 1 ]]; then
    trap 'printf "eicrecon-term:%s\\n" "$PWD" >> "$LIFECYCLE"; exit 143' TERM
    printf 'eicrecon-ready:%s\\n' "$PWD" >> "$LIFECYCLE"
    while :; do sleep 1; done
fi
printf 'DETECTOR=%s\nDETECTOR_CONFIG=%s\nDD4HEP=%s\nFROM_LOCAL_EICRECON=%s\nEICrecon_MY=%s\nJANA_PLUGIN_PATH=%s\n' "$DETECTOR" "$DETECTOR_CONFIG" "$DD4HEP" "$FROM_LOCAL_EICRECON" "$EICrecon_MY" "$JANA_PLUGIN_PATH" > "$INNER"
printf '<%s>\n' "$@" >> "$INNER"
exit "${EICRECON_EXIT:-0}"
""")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    epic_setup = tmp_path / "thisepic.sh"
    _write(epic_setup, f"""printf 'setup:%s\\n' "$PWD" >> "$LIFECYCLE"
mkdir -p calibrations fieldmaps gdml
touch calibrations/fake fieldmaps/fake gdml/fake
if [[ ${{FAKE_SETUP_EXIT:-0}} != 0 ]]; then
    return "$FAKE_SETUP_EXIT"
fi
export DETECTOR=epic DETECTOR_CONFIG=${{1:-epic}} DETECTOR_PATH={geometry} DD4HEP=/opt/local/examples
""")
    _write(fake_bin / "mktemp", """#!/usr/bin/env bash
workdir="$FAKE_TMP/workdir"
printf 'mktemp:%s:%s\n' "$workdir" "$*" >> "$LIFECYCLE"
mkdir -p "$workdir"
printf '%s\n' "$workdir"
""")
    _write(fake_bin / "rm", """#!/usr/bin/env bash
workdir="${!#}"
printf 'cleanup:%s\n' "$workdir" >> "$LIFECYCLE"
/bin/rm "$@"
""")
    singularity = tmp_path / "fake-singularity"
    _write(singularity, """#!/usr/bin/env bash
printf '<%s>\n' "$@" > "$OUTER"
while [[ "$1" != eic-shell ]]; do shift; done
command=$2
command=${command//\\/opt\\/detector\\/epic-main\\/bin\\/thisepic.sh/$FAKE_EPIC_SETUP}
exec bash -c "$command" "${@:3}"
""")
    return {"root": root, "caller": caller, "prefix": prefix, "plugin": plugin, "image": image, "geometry": geometry, "fake_bin": fake_bin, "singularity": singularity, "epic_setup": epic_setup}


def _run(fixture: dict[str, Path], *arguments: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    wrapper = Path(__file__).parents[2] / "scripts" / "eicrecon-container"
    env = os.environ | {
        "EIC_CONTAINER_BIND_ROOT": str(fixture["root"]),
        "EIC_CONTAINER_SIF": str(fixture["image"]),
        "EICRECON_PREFIX": str(fixture["prefix"]),
        "QUANTUM_CENTAURO_PREFIX": str(fixture["plugin"]),
        "SINGULARITY_BIN": str(fixture["singularity"]),
        "PATH": f'{fixture["fake_bin"]}:{os.environ["PATH"]}',
        "OUTER": str(fixture["root"] / "outer.txt"),
        "INNER": str(fixture["root"] / "inner.txt"),
        "FAKE_EPIC_SETUP": str(fixture["epic_setup"]),
        "FAKE_TMP": str(fixture["root"] / "temporary"),
        "LIFECYCLE": str(fixture["root"] / "lifecycle.txt"),
        "JANA_PLUGIN_PATH": "/existing plugins",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(wrapper), *arguments], cwd=fixture["caller"], env=env, capture_output=True, text=True, check=False, timeout=5)


def test_wrapper_preserves_outer_and_inner_argv_and_plugin_environment(container_fixture: dict[str, Path]) -> None:
    root = container_fixture["root"]
    arguments = (f"-Poutput={root}/runs/a file.root", "space ; $ metachar", "")
    result = _run(container_fixture, *arguments)

    assert result.returncode == 0, result.stderr
    outer_argv = (root / "outer.txt").read_text(encoding="utf-8").splitlines()
    assert outer_argv[:5] == [
        "<exec>",
        "<--bind>",
        f"<{root}:{root}>",
        f"<{container_fixture['image']}>",
        "<eic-shell>",
    ]
    assert "thisepic.sh \"$detector_config\"" in outer_argv[5]
    assert outer_argv[6:] == [
        "<eicrecon-container>", "<epic_craterlake>", "<0>",
        f"<{container_fixture['prefix']}>",
        f"<{container_fixture['plugin']}>",
        f"<{arguments[0]}>",
        f"<{arguments[1]}>",
        "<>",
    ]
    assert (root / "inner.txt").read_text(encoding="utf-8").splitlines() == [
        "DETECTOR=epic",
        "DETECTOR_CONFIG=epic_craterlake",
        "DD4HEP=/opt/local/examples",
        "FROM_LOCAL_EICRECON=yes",
        f"EICrecon_MY={container_fixture['plugin']}",
        f"JANA_PLUGIN_PATH={container_fixture['plugin']}/plugins:/existing plugins",
        f"<{arguments[0]}>",
        f"<{arguments[1]}>",
        "<>",
    ]
    workdir = root / "temporary" / "workdir"
    assert (root / "lifecycle.txt").read_text(encoding="utf-8").splitlines() == [
        f"mktemp:{workdir}:-d /tmp/eicrecon-container.XXXXXXXXXX",
        f"setup:{workdir}",
        f"eicrecon:{workdir}",
        f"cleanup:{workdir}",
    ]
    assert not workdir.exists()
    assert not any((container_fixture["caller"] / name).exists() for name in ("calibrations", "fieldmaps", "gdml"))


@pytest.mark.parametrize("variable", ["EIC_CONTAINER_SIF", "EICRECON_PREFIX", "QUANTUM_CENTAURO_PREFIX", "EIC_CONTAINER_BIND_ROOT"])
def test_wrapper_rejects_symlink_declarations_before_singularity(container_fixture: dict[str, Path], variable: str) -> None:
    link = container_fixture["root"] / f"{variable}-link"
    link.symlink_to(container_fixture["image"] if variable == "EIC_CONTAINER_SIF" else container_fixture["prefix"], target_is_directory=variable != "EIC_CONTAINER_SIF")
    result = _run(container_fixture, extra_env={variable: str(link)})

    assert result.returncode == 2
    assert "must not be a symlink" in result.stderr
    assert not (container_fixture["root"] / "outer.txt").exists()


def test_wrapper_resolves_a_safe_singularity_executable_symlink(container_fixture: dict[str, Path]) -> None:
    link = container_fixture["root"] / "singularity"
    link.symlink_to(container_fixture["singularity"])

    result = _run(container_fixture, "--preflight", extra_env={"SINGULARITY_BIN": str(link)})

    assert result.returncode == 0, result.stderr
    assert (container_fixture["root"] / "outer.txt").exists()


def test_wrapper_rejects_bind_escape_and_control_characters_before_singularity(container_fixture: dict[str, Path], tmp_path: Path) -> None:
    escaped = tmp_path / "outside.sif"
    escaped.touch()
    result = _run(container_fixture, extra_env={"EIC_CONTAINER_SIF": str(escaped)})
    assert result.returncode == 2
    assert "escapes EIC_CONTAINER_BIND_ROOT" in result.stderr
    assert not (container_fixture["root"] / "outer.txt").exists()


@pytest.mark.parametrize("variable", ["EIC_CONTAINER_SIF", "EICRECON_PREFIX", "QUANTUM_CENTAURO_PREFIX", "EIC_CONTAINER_BIND_ROOT", "SINGULARITY_BIN"])
@pytest.mark.parametrize("unsafe_suffix", [":colon", ",comma", " whitespace", "#comment", "~home", "=assignment", ";command", "$expansion", "'quote", '\\backslash'])
def test_wrapper_rejects_unsafe_infrastructure_paths_before_singularity(
    container_fixture: dict[str, Path], variable: str, unsafe_suffix: str
) -> None:
    result = _run(container_fixture, extra_env={variable: f"{container_fixture['root']}/unsafe{unsafe_suffix}"})

    assert result.returncode == 2
    assert "contains characters outside the safe Singularity bind allowlist" in result.stderr
    assert not (container_fixture["root"] / "outer.txt").exists()


def test_preflight_enters_container_and_prints_local_eicrecon_geometry_and_plugin_evidence(container_fixture: dict[str, Path]) -> None:
    result = _run(container_fixture, "--preflight")
    assert result.returncode == 0, result.stderr
    assert (container_fixture["root"] / "outer.txt").exists()
    assert not (container_fixture["root"] / "inner.txt").exists()
    assert "preflight detector_config=epic_craterlake" in result.stdout
    assert f"preflight eicrecon={container_fixture['prefix']}/bin/eicrecon" in result.stdout
    assert f"preflight plugin={container_fixture['plugin']}/plugins/quantum_centauro.so" in result.stdout
    workdir = container_fixture["root"] / "temporary" / "workdir"
    assert (container_fixture["root"] / "lifecycle.txt").read_text(encoding="utf-8").splitlines() == [
        f"mktemp:{workdir}:-d /tmp/eicrecon-container.XXXXXXXXXX",
        f"setup:{workdir}",
        f"cleanup:{workdir}",
    ]
    assert not workdir.exists()

    (container_fixture["geometry"] / "safe_override.xml").touch()
    result = _run(container_fixture, "--preflight", extra_env={"EIC_DETECTOR_CONFIG": "safe_override"})
    assert result.returncode == 0, result.stderr
    assert "preflight detector_config=safe_override" in result.stdout

    (container_fixture["root"] / "outer.txt").unlink()
    result = _run(container_fixture, "-Poutput=/tmp/unsafe\nvalue")
    assert result.returncode == 2
    assert "control characters" in result.stderr
    assert not (container_fixture["root"] / "outer.txt").exists()


@pytest.mark.parametrize("token", ["epic/config", "../epic", "epic config", "-epic", "epic;config", "$(epic)", "epic`config`"])
def test_preflight_rejects_unsafe_detector_override_before_singularity(container_fixture: dict[str, Path], token: str) -> None:
    result = _run(container_fixture, "--preflight", extra_env={"EIC_DETECTOR_CONFIG": token})

    assert result.returncode == 2
    assert "EIC_DETECTOR_CONFIG must be a safe basename token" in result.stderr
    assert not (container_fixture["root"] / "outer.txt").exists()


def test_preflight_rejects_detector_setup_that_changes_requested_token(container_fixture: dict[str, Path]) -> None:
    _write(container_fixture["epic_setup"], f"export DETECTOR=epic DETECTOR_CONFIG=epic DETECTOR_PATH={container_fixture['geometry']} DD4HEP=/opt/local/examples\n")

    result = _run(container_fixture, "--preflight")

    assert result.returncode == 1
    assert "thisepic.sh did not set DETECTOR_CONFIG to requested token: epic_craterlake" in result.stderr
    assert not (container_fixture["root"] / "inner.txt").exists()


@pytest.mark.parametrize(
    ("arguments", "environment", "expected_status"),
    [
        (("--preflight",), {"FAKE_SETUP_EXIT": "23"}, 23),
        (("-Pthreads=1",), {"EICRECON_EXIT": "24"}, 24),
    ],
)
def test_wrapper_cleans_isolated_workdir_after_setup_and_runtime_failures(
    container_fixture: dict[str, Path], arguments: tuple[str, ...], environment: dict[str, str], expected_status: int
) -> None:
    result = _run(container_fixture, *arguments, extra_env=environment)

    workdir = container_fixture["root"] / "temporary" / "workdir"
    assert result.returncode == expected_status
    assert (container_fixture["root"] / "lifecycle.txt").read_text(encoding="utf-8").splitlines()[-1] == f"cleanup:{workdir}"
    assert not workdir.exists()


def test_wrapper_preserves_eicrecon_setup_return_and_skips_runtime(container_fixture: dict[str, Path]) -> None:
    result = _run(container_fixture, "-Pthreads=1", extra_env={"FAKE_EICRECON_SETUP_EXIT": "29"})

    root = container_fixture["root"]
    workdir = root / "temporary" / "workdir"
    assert result.returncode == 29
    assert not (root / "inner.txt").exists()
    assert (root / "lifecycle.txt").read_text(encoding="utf-8").splitlines() == [
        f"mktemp:{workdir}:-d /tmp/eicrecon-container.XXXXXXXXXX",
        f"setup:{workdir}",
        f"cleanup:{workdir}",
    ]
    assert not workdir.exists()


def test_wrapper_forces_tmp_template_despite_inherited_tmpdir(container_fixture: dict[str, Path]) -> None:
    inherited_tmpdir = container_fixture["caller"] / "inherited-tmp"
    inherited_tmpdir.mkdir()

    result = _run(container_fixture, "--preflight", extra_env={"TMPDIR": str(inherited_tmpdir)})

    workdir = container_fixture["root"] / "temporary" / "workdir"
    assert result.returncode == 0, result.stderr
    assert (container_fixture["root"] / "lifecycle.txt").read_text(encoding="utf-8").splitlines()[0] == (
        f"mktemp:{workdir}:-d /tmp/eicrecon-container.XXXXXXXXXX"
    )
    assert not workdir.exists()
    assert not any(inherited_tmpdir.iterdir())
    assert not any((container_fixture["caller"] / name).exists() for name in ("calibrations", "fieldmaps", "gdml"))


def test_wrapper_forwards_term_reaps_child_and_cleans_workdir(container_fixture: dict[str, Path]) -> None:
    wrapper = Path(__file__).parents[2] / "scripts" / "eicrecon-container"
    env = os.environ | {
        "EIC_CONTAINER_BIND_ROOT": str(container_fixture["root"]),
        "EIC_CONTAINER_SIF": str(container_fixture["image"]),
        "EICRECON_PREFIX": str(container_fixture["prefix"]),
        "QUANTUM_CENTAURO_PREFIX": str(container_fixture["plugin"]),
        "SINGULARITY_BIN": str(container_fixture["singularity"]),
        "PATH": f'{container_fixture["fake_bin"]}:{os.environ["PATH"]}',
        "OUTER": str(container_fixture["root"] / "outer.txt"),
        "INNER": str(container_fixture["root"] / "inner.txt"),
        "FAKE_EPIC_SETUP": str(container_fixture["epic_setup"]),
        "FAKE_TMP": str(container_fixture["root"] / "temporary"),
        "LIFECYCLE": str(container_fixture["root"] / "lifecycle.txt"),
        "JANA_PLUGIN_PATH": "/existing plugins",
        "EICRECON_WAIT_FOR_TERM": "1",
    }
    process = subprocess.Popen(["bash", str(wrapper)], cwd=container_fixture["caller"], env=env, text=True)
    lifecycle = container_fixture["root"] / "lifecycle.txt"
    for _ in range(100):
        if lifecycle.exists() and "eicrecon-ready:" in lifecycle.read_text(encoding="utf-8"):
            break
        process.poll()
        if process.returncode is not None:
            pytest.fail(f"wrapper exited before child became ready: {process.returncode}")
        time.sleep(0.01)
    else:
        process.kill()
        process.wait(timeout=5)
        pytest.fail("fake EICrecon did not become ready")

    process.terminate()
    assert process.wait(timeout=5) == 143

    workdir = container_fixture["root"] / "temporary" / "workdir"
    assert lifecycle.read_text(encoding="utf-8").splitlines()[-2:] == [
        f"eicrecon-term:{workdir}",
        f"cleanup:{workdir}",
    ]
    assert not workdir.exists()
    assert not any((container_fixture["caller"] / name).exists() for name in ("calibrations", "fieldmaps", "gdml"))


def test_preflight_rejects_extra_arguments_before_singularity(container_fixture: dict[str, Path]) -> None:
    result = _run(container_fixture, "--preflight", "unexpected")

    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert not (container_fixture["root"] / "outer.txt").exists()


def test_wrapper_source_never_uses_eval_or_the_image_eicrecon() -> None:
    source = (Path(__file__).parents[2] / "scripts" / "eicrecon-container").read_text(encoding="utf-8")
    assert "eval" not in source
    assert "/opt/local/bin/eicrecon" not in source
