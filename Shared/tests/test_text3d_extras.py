"""Testes dos extras de instalação do Text3D (KTX-Software Linux x Windows)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from aigamekit_shared.installer.text3d_extras import (
    _KTX_MANUAL_HINT,
    _KTX_VERSION,
    _KTX_WIN_URL,
    Text3DPostInstall,
)


class _FakeLogger:
    """Regista mensagens; sem rich."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.rich_available = False

    def _record(self, msg: str) -> None:
        self.messages.append(str(msg))

    def info(self, msg: str) -> None:
        self._record(msg)

    def step(self, msg: str) -> None:
        self._record(msg)

    def success(self, msg: str) -> None:
        self._record(msg)


class _FakeInstaller:
    def __init__(self, *, is_windows: bool, project_root: Path | None = None) -> None:
        self.is_windows = is_windows
        self.logger = _FakeLogger()
        self.project_root = project_root or Path(".").resolve()
        self.skip_models = True
        self.venv_exists = False
        self.python_cmd = "python3"
        self.venv_python = Path("python3")
        self.bin_dir = Path("~/.local/bin").expanduser()


def _make_extras(is_windows: bool = False, project_root: Path | None = None) -> Text3DPostInstall:
    return Text3DPostInstall(_FakeInstaller(is_windows=is_windows, project_root=project_root))


class TestKtxAlreadyOnPath:
    def test_returns_early_when_ktx_found(self, tmp_path: Path):
        extras = _make_extras(is_windows=True)
        with patch("aigamekit_shared.installer.text3d_extras.shutil.which", return_value="/usr/bin/ktx"):
            extras.ensure_ktx_software()
        assert any("já no PATH" in m for m in extras._i.logger.messages)
        assert not any("7-Zip" in m for m in extras._i.logger.messages)


class TestWindowsKtx:
    def test_without_7z_logs_manual_hint(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        extras = _make_extras(is_windows=True)
        with patch("aigamekit_shared.installer.text3d_extras.shutil.which", return_value=None):
            extras.ensure_ktx_software()
        msgs = "\n".join(extras._i.logger.messages)
        assert "7-Zip não encontrado" in msgs
        assert _KTX_WIN_URL in msgs  # hint com URL exato do instalador

    def test_with_7z_extracts_ktx_exe(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        extras = _make_extras(is_windows=True)

        class _FakeRunResult:
            returncode = 0

        def fake_7z(args, **kwargs):
            extract_dir = next(a[2:] for a in args if a.startswith("-o"))
            (Path(extract_dir) / "bin").mkdir(parents=True, exist_ok=True)
            (Path(extract_dir) / "bin" / "ktx.exe").write_bytes(b"KTX-BINARY")
            return _FakeRunResult()

        with (
            patch(
                "aigamekit_shared.installer.text3d_extras.shutil.which",
                side_effect=lambda n: "/usr/bin/7z" if n == "7z" else None,
            ),
            patch("aigamekit_shared.installer.text3d_extras.subprocess.run", side_effect=fake_7z),
            patch("aigamekit_shared.installer.text3d_extras.urllib.request.urlretrieve", return_value=None),
        ):
            extras.ensure_ktx_software()

        target = tmp_path / ".local" / "bin" / "ktx.exe"
        assert target.is_file()
        assert target.read_bytes() == b"KTX-BINARY"
        assert (tmp_path / ".local" / "opt" / "KTX-Software" / "bin" / "ktx.exe").is_file()
        assert any("ktx →" in m for m in extras._i.logger.messages)

    def test_7z_failure_falls_back_to_manual(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        extras = _make_extras(is_windows=True)

        class _FakeRunResult:
            returncode = 1

        with (
            patch(
                "aigamekit_shared.installer.text3d_extras.shutil.which",
                side_effect=lambda n: "/usr/bin/7z" if n == "7z" else None,
            ),
            patch("aigamekit_shared.installer.text3d_extras.subprocess.run", return_value=_FakeRunResult()),
            patch("aigamekit_shared.installer.text3d_extras.urllib.request.urlretrieve", return_value=None),
        ):
            extras.ensure_ktx_software()

        msgs = "\n".join(extras._i.logger.messages)
        assert "7-Zip falhou" in msgs
        assert not (tmp_path / ".local" / "bin" / "ktx.exe").exists()

    def test_already_extracted_is_copied_to_bin(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        dest = tmp_path / ".local" / "opt" / "KTX-Software" / "bin"
        dest.mkdir(parents=True)
        (dest / "ktx.exe").write_bytes(b"EXISTING")
        extras = _make_extras(is_windows=True)
        with patch("aigamekit_shared.installer.text3d_extras.shutil.which", return_value=None):
            extras.ensure_ktx_software()
        assert (tmp_path / ".local" / "bin" / "ktx.exe").read_bytes() == b"EXISTING"


class TestLinuxKtx:
    def test_installs_tarball_and_symlinks(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        extras = _make_extras(is_windows=False)

        class _FakeTar:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extractall(self, tdir, filter=None):
                root = Path(tdir) / f"KTX-Software-{_KTX_VERSION}"
                (root / "bin").mkdir(parents=True)
                (root / "bin" / "ktx").write_bytes(b"KTX-LINUX")

        with (
            patch("aigamekit_shared.installer.text3d_extras.shutil.which", return_value=None),
            patch("aigamekit_shared.installer.text3d_extras.platform.machine", return_value="x86_64"),
            patch("aigamekit_shared.installer.text3d_extras.tarfile.open", return_value=_FakeTar()),
            patch("aigamekit_shared.installer.text3d_extras.urllib.request.urlretrieve", return_value=None),
        ):
            extras.ensure_ktx_software()

        assert (tmp_path / ".local" / "opt" / "KTX-Software" / "bin" / "ktx").is_file()
        link = tmp_path / ".local" / "bin" / "ktx"
        assert link.is_symlink()
        assert link.resolve().read_bytes() == b"KTX-LINUX"

    def test_unknown_arch_logs_manual(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        extras = _make_extras(is_windows=False)
        with (
            patch("aigamekit_shared.installer.text3d_extras.shutil.which", return_value=None),
            patch("aigamekit_shared.installer.text3d_extras.platform.machine", return_value="mips"),
        ):
            extras.ensure_ktx_software()
        assert any("arch mips sem tarball" in m for m in extras._i.logger.messages)


class TestKtxUrlContract:
    def test_windows_asset_matches_release_layout(self):
        # O installer Windows é um .exe NSIS; o hint manual aponta para ele.
        assert _KTX_WIN_URL.endswith(f"KTX-Software-{_KTX_VERSION}-Windows-x64.exe")
        assert "KTX-Software/releases" in _KTX_MANUAL_HINT
