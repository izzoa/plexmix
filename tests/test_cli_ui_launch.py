"""Tests for ``plexmix ui``: production by default, app-dir resolution, one backend worker.

Reflex is never imported for real (``sys.modules["reflex"]`` is stubbed) and
``subprocess.run`` is patched, so no build or server is started.
"""

import os
import stat
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Dict, Iterator, List

import click
import pytest
import typer
from click.testing import Result
from typer.testing import CliRunner

from plexmix.cli import ui_cmd

runner = CliRunner()

REDIS_SENTINEL = "redis://:SENTINEL-REDIS-0000@redis.invalid:6379/0"


def _normalize(text: str) -> str:
    """Collapse whitespace so Rich's line wrapping cannot split asserted phrases."""
    return " ".join(text.split())


def _make_cli() -> typer.Typer:
    """A root app with ``ui`` registered exactly as ``plexmix.cli.main`` does.

    The real root callback loads config and reconfigures logging, so the command is
    mounted on a bare root here; ``test_real_cli_registers_ui_command`` covers the
    real registration.
    """
    cli = typer.Typer()

    @cli.callback()
    def _root() -> None:
        """Test root."""

    cli.command("ui")(ui_cmd.launch_ui)
    return cli


def _make_app_dir(root: Path) -> Path:
    """Create ``rxconfig.py`` + ``plexmix_ui/`` under *root* and return it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "rxconfig.py").write_text("config = None\n")
    (root / "plexmix_ui").mkdir(exist_ok=True)
    (root / "plexmix_ui" / "__init__.py").write_text("")
    return root


def _make_module_file(path: Path) -> Path:
    """Create a stand-in ``plexmix/cli/ui_cmd.py`` at *path* and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


class _RunRecorder:
    """Stands in for ``subprocess.run`` and records each call."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, cmd: List[str], **kwargs: Any) -> "subprocess.CompletedProcess[bytes]":
        self.calls.append({"cmd": list(cmd), **kwargs})
        return subprocess.CompletedProcess(cmd, self.returncode)

    @property
    def cmd(self) -> List[str]:
        assert len(self.calls) == 1, f"expected one subprocess.run call, got {len(self.calls)}"
        return list(self.calls[0]["cmd"])

    @property
    def env(self) -> Dict[str, str]:
        assert len(self.calls) == 1, f"expected one subprocess.run call, got {len(self.calls)}"
        env = self.calls[0]["env"]
        assert isinstance(env, dict)
        return env


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every test away from the real keyring, config, and launch-related env."""
    data_dir = tmp_path / "plexmix-data"
    data_dir.mkdir()
    (data_dir / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(data_dir))
    for name in (
        "PLEXMIX_ALLOWED_HOSTS",
        "PLEXMIX_API_URL",
        "GRANIAN_WORKERS",
        "REFLEX_REDIS_URL",
        "REDIS_URL",
        "REFLEX_STATES_WORKDIR",
    ):
        monkeypatch.delenv(name, raising=False)
    # launch_ui may insert the app's import path; never leak that into other tests.
    monkeypatch.setattr(sys, "path", list(sys.path))


@pytest.fixture
def fake_reflex(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("reflex")
    monkeypatch.setitem(sys.modules, "reflex", module)
    return module


@pytest.fixture
def run_recorder(monkeypatch: pytest.MonkeyPatch) -> _RunRecorder:
    recorder = _RunRecorder()
    monkeypatch.setattr(subprocess, "run", recorder)
    return recorder


@pytest.fixture
def checkout_app_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A checkout-shaped app dir that ``launch_ui`` resolves to."""
    app_dir = _make_app_dir(tmp_path / "checkout")
    (app_dir / "src" / "plexmix").mkdir(parents=True)
    monkeypatch.setattr(ui_cmd, "_resolve_app_dir", lambda module_file=None: app_dir)
    yield app_dir


def _invoke(args: List[str]) -> Result:
    return runner.invoke(_make_cli(), args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# 6.1 Production by default, --dev on request, --prod still accepted
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_reflex", "checkout_app_dir")
class TestLaunchMode:
    def test_default_launch_serves_production_build(self, run_recorder: _RunRecorder) -> None:
        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        cmd = run_recorder.cmd
        assert cmd[:4] == [sys.executable, "-m", "reflex", "run"]
        assert cmd[-2:] == ["--env", "prod"]
        output = _normalize(result.output)
        assert "Building the web UI (first start can take a few minutes)" in output
        assert "Hot-reloading" not in output

    def test_prod_flag_still_accepted(self, run_recorder: _RunRecorder) -> None:
        result = _invoke(["ui", "--prod"])

        assert result.exit_code == 0, result.output
        assert run_recorder.cmd[-2:] == ["--env", "prod"]
        assert "Building the web UI" in _normalize(result.output)

    def test_dev_flag_runs_hot_reload_server(self, run_recorder: _RunRecorder) -> None:
        result = _invoke(["ui", "--dev"])

        assert result.exit_code == 0, result.output
        assert "--env" not in run_recorder.cmd
        assert "prod" not in run_recorder.cmd
        output = _normalize(result.output)
        assert "Hot-reloading enabled (dev mode)" in output
        assert "Building the web UI" not in output

    def test_last_mode_flag_wins(self, run_recorder: _RunRecorder) -> None:
        result = _invoke(["ui", "--prod", "--dev"])

        assert result.exit_code == 0, result.output
        assert "--env" not in run_recorder.cmd

    def test_ports_and_host_are_passed_through(self, run_recorder: _RunRecorder) -> None:
        result = _invoke(["ui", "--host", "0.0.0.0", "--port", "3100", "--backend-port", "8110"])

        assert result.exit_code == 0, result.output
        cmd = run_recorder.cmd
        assert cmd[cmd.index("--frontend-port") + 1] == "3100"
        assert cmd[cmd.index("--backend-port") + 1] == "8110"
        assert cmd[cmd.index("--backend-host") + 1] == "0.0.0.0"

    def test_child_exit_status_propagates(self, run_recorder: _RunRecorder) -> None:
        run_recorder.returncode = 3

        result = _invoke(["ui"])

        assert result.exit_code == 3


def test_help_lists_dev_and_prod() -> None:
    result = runner.invoke(_make_cli(), ["ui", "--help"], terminal_width=200)
    # On GitHub Actions Typer colours its help text, and the colour codes split an
    # option name such as "--dev". Compare the plain text.
    help_text = click.unstyle(result.output)

    assert result.exit_code == 0, result.output
    assert "--dev" in help_text
    assert "--prod" in help_text


def test_real_cli_registers_ui_command() -> None:
    from plexmix.cli.main import app

    commands = {info.name: info.callback for info in app.registered_commands}
    assert commands.get("ui") is ui_cmd.launch_ui


def test_missing_reflex_prints_install_hint(
    monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder, checkout_app_dir: Path
) -> None:
    # A None entry in sys.modules makes ``import reflex`` raise ImportError.
    monkeypatch.setitem(sys.modules, "reflex", None)

    result = _invoke(["ui"])

    assert result.exit_code == 1
    assert "pip install 'plexmix[ui]'" in _normalize(result.output)
    assert run_recorder.calls == []


# ---------------------------------------------------------------------------
# 6.2 App-dir resolution for checkout, wheel, missing and read-only layouts
# ---------------------------------------------------------------------------


class TestResolveAppDir:
    def test_checkout_layout_uses_repo_root(self, tmp_path: Path) -> None:
        repo = _make_app_dir(tmp_path / "repo")
        module_file = _make_module_file(repo / "src" / "plexmix" / "cli" / "ui_cmd.py")

        assert ui_cmd._resolve_app_dir(module_file) == repo.resolve()

    def test_wheel_layout_uses_site_packages_root(self, tmp_path: Path) -> None:
        site_packages = _make_app_dir(tmp_path / "venv" / "lib" / "python3.12" / "site-packages")
        module_file = _make_module_file(site_packages / "plexmix" / "cli" / "ui_cmd.py")

        assert ui_cmd._resolve_app_dir(module_file) == site_packages.resolve()

    def test_checkout_candidate_is_checked_first(self, tmp_path: Path) -> None:
        repo = _make_app_dir(tmp_path / "repo")
        _make_app_dir(repo / "src")
        module_file = _make_module_file(repo / "src" / "plexmix" / "cli" / "ui_cmd.py")

        assert ui_cmd._resolve_app_dir(module_file) == repo.resolve()

    def test_rxconfig_without_app_package_is_not_an_app_dir(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "rxconfig.py").write_text("config = None\n")
        module_file = _make_module_file(repo / "src" / "plexmix" / "cli" / "ui_cmd.py")

        with pytest.raises(typer.Exit) as excinfo:
            ui_cmd._resolve_app_dir(module_file)

        assert excinfo.value.exit_code == 1
        assert "Cannot locate the PlexMix web UI app" in _normalize(capsys.readouterr().out)

    def test_missing_app_exits_with_install_hint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module_file = _make_module_file(
            tmp_path
            / "venv"
            / "lib"
            / "python3.12"
            / "site-packages"
            / "plexmix"
            / "cli"
            / "ui_cmd.py"
        )

        with pytest.raises(typer.Exit) as excinfo:
            ui_cmd._resolve_app_dir(module_file)

        assert excinfo.value.exit_code == 1
        output = _normalize(capsys.readouterr().out)
        assert "Cannot locate the PlexMix web UI app" in output
        assert "pip install 'plexmix[ui]'" in output
        assert "poetry install -E ui" in output

    def test_shallow_install_path_does_not_crash(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Fewer than four parents: the checkout candidate must be skipped, not IndexError.
        with pytest.raises(typer.Exit) as excinfo:
            ui_cmd._resolve_app_dir(Path("/plexmix/cli/ui_cmd.py"))

        assert excinfo.value.exit_code == 1
        assert "Cannot locate the PlexMix web UI app" in _normalize(capsys.readouterr().out)

    @pytest.mark.skipif(
        not hasattr(os, "geteuid") or os.geteuid() == 0,
        reason="root bypasses directory permissions",
    )
    def test_read_only_app_dir_exits_with_actionable_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        site_packages = _make_app_dir(tmp_path / "venv" / "lib" / "python3.12" / "site-packages")
        module_file = _make_module_file(site_packages / "plexmix" / "cli" / "ui_cmd.py")
        site_packages.chmod(0o555)
        try:
            with pytest.raises(typer.Exit) as excinfo:
                ui_cmd._resolve_app_dir(module_file)
        finally:
            site_packages.chmod(0o755)

        assert excinfo.value.exit_code == 1
        output = _normalize(capsys.readouterr().out)
        assert "not writable" in output
        assert ".web/" in output
        assert "virtual environment" in output

    def test_this_checkout_resolves_to_repo_root(self) -> None:
        repo_root = Path(ui_cmd.__file__).resolve().parents[3]
        if not (repo_root / "rxconfig.py").is_file():
            pytest.skip("not running from a source checkout")

        assert ui_cmd._resolve_app_dir() == repo_root


@pytest.mark.usefixtures("fake_reflex")
class TestLaunchUsesResolvedAppDir:
    def test_checkout_dir_is_cwd_and_src_is_on_sys_path(
        self, run_recorder: _RunRecorder, checkout_app_dir: Path
    ) -> None:
        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert run_recorder.calls[0]["cwd"] == str(checkout_app_dir)
        assert sys.path[0] == str(checkout_app_dir / "src")

    def test_wheel_dir_is_cwd_and_used_as_import_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder
    ) -> None:
        site_packages = _make_app_dir(tmp_path / "site-packages")
        (site_packages / "plexmix").mkdir()
        monkeypatch.setattr(ui_cmd, "_resolve_app_dir", lambda module_file=None: site_packages)

        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert run_recorder.calls[0]["cwd"] == str(site_packages)
        assert sys.path[0] == str(site_packages)

    def test_missing_app_dir_exits_before_launch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder
    ) -> None:
        real_resolve = ui_cmd._resolve_app_dir
        orphan = _make_module_file(tmp_path / "orphan" / "plexmix" / "cli" / "ui_cmd.py")
        monkeypatch.setattr(
            ui_cmd, "_resolve_app_dir", lambda module_file=None: real_resolve(orphan)
        )

        result = _invoke(["ui"])

        assert result.exit_code == 1
        assert "pip install 'plexmix[ui]'" in _normalize(result.output)
        assert run_recorder.calls == []


# ---------------------------------------------------------------------------
# 6.3 Single backend worker; Redis settings dropped with a warning
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_reflex", "checkout_app_dir")
class TestChildEnvironment:
    def test_granian_pinned_to_one_worker(self, run_recorder: _RunRecorder) -> None:
        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert run_recorder.env["GRANIAN_WORKERS"] == "1"

    def test_granian_worker_override_is_replaced(
        self, monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder
    ) -> None:
        monkeypatch.setenv("GRANIAN_WORKERS", "4")

        result = _invoke(["ui", "--dev"])

        assert result.exit_code == 0, result.output
        assert run_recorder.env["GRANIAN_WORKERS"] == "1"

    @pytest.mark.parametrize("name", ["REFLEX_REDIS_URL", "REDIS_URL"])
    def test_redis_url_is_dropped_with_warning(
        self, name: str, monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder
    ) -> None:
        monkeypatch.setenv(name, REDIS_SENTINEL)

        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert name not in run_recorder.env
        output = _normalize(result.output)
        assert f"Warning: {name} is set" in output
        assert "single backend worker" in output
        # The value can carry a password: it must never be echoed.
        assert "SENTINEL-REDIS-0000" not in result.output

    def test_both_redis_urls_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder
    ) -> None:
        monkeypatch.setenv("REFLEX_REDIS_URL", REDIS_SENTINEL)
        monkeypatch.setenv("REDIS_URL", REDIS_SENTINEL)

        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert "REFLEX_REDIS_URL" not in run_recorder.env
        assert "REDIS_URL" not in run_recorder.env
        output = _normalize(result.output)
        assert "Warning: REFLEX_REDIS_URL is set" in output
        assert "Warning: REDIS_URL is set" in output
        assert "SENTINEL-REDIS-0000" not in result.output

    def test_no_warning_without_redis(self, run_recorder: _RunRecorder) -> None:
        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert "Warning" not in result.output

    def test_interpreter_scripts_dir_is_on_path(
        self, monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder
    ) -> None:
        # Reflex starts the prod backend as a bare ``granian`` command, so an
        # un-activated venv/pipx interpreter's scripts dir must be on the child PATH.
        monkeypatch.setenv("PATH", "/usr/bin")

        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        entries = run_recorder.env["PATH"].split(os.pathsep)
        assert entries[0] == str(Path(sys.executable).parent)
        assert "/usr/bin" in entries

    def test_scripts_dir_already_on_path_is_not_duplicated(
        self, monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder
    ) -> None:
        scripts_dir = str(Path(sys.executable).parent)
        monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", scripts_dir]))

        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert run_recorder.env["PATH"].split(os.pathsep).count(scripts_dir) == 1

    def test_api_url_and_allowed_hosts_still_forwarded(
        self, monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder
    ) -> None:
        monkeypatch.setenv("PLEXMIX_ALLOWED_HOSTS", "plexmix.example.test")

        result = _invoke(["ui", "--api-url", "http://plexmix.example.test:8110"])

        assert result.exit_code == 0, result.output
        env = run_recorder.env
        assert env["PLEXMIX_API_URL"] == "http://plexmix.example.test:8110"
        assert env["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] == "plexmix.example.test"

    def test_parent_environment_is_not_mutated(
        self, monkeypatch: pytest.MonkeyPatch, run_recorder: _RunRecorder
    ) -> None:
        monkeypatch.setenv("REDIS_URL", REDIS_SENTINEL)

        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert os.environ.get("REDIS_URL") == REDIS_SENTINEL
        assert "GRANIAN_WORKERS" not in os.environ


# ---------------------------------------------------------------------------
# Reflex's state pickles (they hold the signed sign-in cookie) are owner-only
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
@pytest.mark.usefixtures("fake_reflex")
class TestPrivateStatesDir:
    def test_states_dir_created_owner_only(
        self, run_recorder: _RunRecorder, checkout_app_dir: Path
    ) -> None:
        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        states_dir = checkout_app_dir / ".states"
        assert states_dir.is_dir()
        assert stat.S_IMODE(states_dir.stat().st_mode) == 0o700

    def test_existing_states_dir_is_tightened(
        self, run_recorder: _RunRecorder, checkout_app_dir: Path
    ) -> None:
        states_dir = checkout_app_dir / ".states"
        states_dir.mkdir(mode=0o755)
        os.chmod(states_dir, 0o755)
        (states_dir / "old.pkl").write_bytes(b"x")

        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert stat.S_IMODE(states_dir.stat().st_mode) == 0o700
        assert (states_dir / "old.pkl").exists()  # Reflex itself clears the files

    def test_reflex_states_workdir_is_honoured(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_recorder: _RunRecorder,
        checkout_app_dir: Path,
    ) -> None:
        absolute = tmp_path / "elsewhere" / "states"
        monkeypatch.setenv("REFLEX_STATES_WORKDIR", str(absolute))
        assert _invoke(["ui"]).exit_code == 0
        assert stat.S_IMODE(absolute.stat().st_mode) == 0o700
        assert not (checkout_app_dir / ".states").exists()

    def test_relative_workdir_resolves_against_the_app_dir(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        assert ui_cmd._states_dir(app_dir, {}) == app_dir / ".states"
        assert ui_cmd._states_dir(app_dir, {"REFLEX_STATES_WORKDIR": "  "}) == app_dir / ".states"
        assert ui_cmd._states_dir(app_dir, {"REFLEX_STATES_WORKDIR": "st"}) == app_dir / "st"

    def test_failure_only_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        run_recorder: _RunRecorder,
        checkout_app_dir: Path,
    ) -> None:
        def deny(path: Any, mode: int) -> None:
            raise PermissionError("denied")

        monkeypatch.setattr(ui_cmd.os, "chmod", deny)

        result = _invoke(["ui"])

        assert result.exit_code == 0, result.output
        assert "could not restrict access" in _normalize(result.output)
        assert len(run_recorder.calls) == 1
