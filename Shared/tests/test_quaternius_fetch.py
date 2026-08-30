"""Quaternius multi-pack fetcher (UAL1 + UAL2) — CPU-first, sem rede.

Os testes de fetch usam locks sintéticos + zips locais em tmp_path; nada toca
o cache real (~/.cache) nem o itch.io.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from aigamekit_shared import quaternius_fetch as qf

# ---------------------------------------------------------------------------
# locks / registry
# ---------------------------------------------------------------------------


class TestPackRegistry:
    def test_pack_names_contains_all_packs(self) -> None:
        assert set(qf.pack_names()) == {"quaternius", "quaternius2", "villager"}

    def test_quaternius_pack_names_subset(self) -> None:
        """Packs Quaternius (GLB único) são subconjunto; villager é por-ficheiro."""
        assert set(qf.quaternius_pack_names()) == {"quaternius", "quaternius2"}
        assert "villager" not in qf.quaternius_pack_names()

    @pytest.mark.parametrize("pack", ["quaternius", "quaternius2"])
    def test_lock_has_required_fields(self, pack: str) -> None:
        lock = qf._lock_data(pack)
        for key in (
            "version",
            "name",
            "license",
            "game_slug",
            "game_id",
            "upload_id",
            "upload_filename",
            "expected_sha256",
            "expected_size",
            "inner_dir",
            "files",
        ):
            assert lock.get(key), f"{pack}: campo {key} em falta"
        for fkey in ("glb", "glb_root_motion", "fbx"):
            assert lock["files"].get(fkey), f"{pack}: files.{fkey} em falta"

    def test_villager_lock_is_per_file_pack(self) -> None:
        """Pack KevDev: creator próprio, cache própria, marker em vez de GLB único."""
        lock = qf._lock_data("villager")
        for key in (
            "version",
            "name",
            "license",
            "game_slug",
            "game_id",
            "upload_id",
            "upload_filename",
            "expected_sha256",
            "expected_size",
            "itch_base",
            "cache_dir",
            "marker",
        ):
            assert lock.get(key), f"villager: campo {key} em falta"
        assert lock["itch_base"] == "https://kevdev.itch.io"
        assert lock["cache_dir"] == "itch/villager"
        assert lock["inner_dir"] == ""  # zip sem pasta-raiz
        assert lock["upload_id"] != qf._lock_data("quaternius")["upload_id"]

    @pytest.mark.parametrize("pack", ["quaternius", "quaternius2", "villager"])
    def test_lock_sha256_is_hex64(self, pack: str) -> None:
        sha = qf._lock_data(pack)["expected_sha256"]
        assert len(str(sha)) == 64
        int(str(sha), 16)  # hex válido

    def test_unknown_pack_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="Pack itch desconhecido"):
            qf._lock_data("quaternius3")

    def test_default_pack_is_ual1(self) -> None:
        assert qf.DEFAULT_PACK == "quaternius"
        assert qf._lock_data() == qf._lock_data("quaternius")


# ---------------------------------------------------------------------------
# cache_dir precedence
# ---------------------------------------------------------------------------


class TestCacheDir:
    def test_vramd_env_wins(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path))
        assert qf.cache_dir() == tmp_path

    def test_xdg_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("VRAMD_CACHE_DIR", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert qf.cache_dir() == tmp_path / "aigamekit"

    def test_home_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VRAMD_CACHE_DIR", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert qf.cache_dir() == Path.home() / ".cache" / "aigamekit"

    def test_cache_root_is_quaternius_subdir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path))
        assert qf.quaternius_cache_root() == tmp_path / "quaternius"


# ---------------------------------------------------------------------------
# is_pack_cached
# ---------------------------------------------------------------------------


class TestIsPackCached:
    def test_false_when_zip_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path))
        assert qf.is_pack_cached() is False
        assert qf.is_pack_cached(pack="quaternius2") is False

    def test_true_without_verify_when_file_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path))
        root = qf.quaternius_cache_root()
        root.mkdir(parents=True)
        (root / "Universal Animation Library[Standard].zip").write_bytes(b"dummy")
        assert qf.is_pack_cached(verify=False) is True
        # com verify o sha256 não bate
        assert qf.is_pack_cached(verify=True) is False


# ---------------------------------------------------------------------------
# fetch offline (locks sintéticos)
# ---------------------------------------------------------------------------


def _make_pack_zip(path: Path) -> None:
    """Zip sintético com a árvore interna mínima (GLB/FBX placeholders)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("FakePack/Unreal-Godot/Fake.glb", b"GLB")
        zf.writestr("FakePack/Unreal-Godot/Fake_RM.glb", b"GLB-RM")
        zf.writestr("FakePack/Unity/Fake.fbx", b"FBX")
        zf.writestr("FakePack/Unity/Fake_RM.fbx", b"FBX-RM")


def _fake_lock(zip_path: Path) -> dict:
    return {
        "version": "v-test",
        "name": "Fake Pack",
        "game_slug": "fake-pack",
        "upload_id": 1,
        "upload_filename": zip_path.name,
        "expected_sha256": qf._sha256(zip_path),
        "expected_size": zip_path.stat().st_size,
        "inner_dir": "FakePack",
        "files": {
            "glb": "Unreal-Godot/Fake.glb",
            "glb_root_motion": "Unreal-Godot/Fake_RM.glb",
            "fbx": "Unity/Fake.fbx",
            "fbx_root_motion": "Unity/Fake_RM.fbx",
        },
    }


class TestFetchOffline:
    def test_happy_path_from_cached_zip(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path / "cache"))
        root = qf.quaternius_cache_root()
        root.mkdir(parents=True)
        zip_path = root / "FakePack.zip"
        _make_pack_zip(zip_path)
        monkeypatch.setattr(qf, "_lock_data", lambda pack=qf.DEFAULT_PACK: _fake_lock(zip_path))

        statuses: list[str] = []
        pack = qf.fetch_quaternius_pack(on_status=statuses.append)

        assert pack.glb == root / "extracted" / "FakePack" / "Unreal-Godot" / "Fake.glb"
        assert pack.glb.is_file()
        assert pack.fbx.is_file()
        assert pack.glb_root_motion.is_file()
        assert pack.pack == qf.DEFAULT_PACK
        assert any("em cache" in s for s in statuses)

    def test_downloads_when_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path / "cache"))

        # zip determinístico: bytes gerados UMA vez; o lock e o _fake_download
        # usam-nos, logo o sha256 bate sempre.
        staged = tmp_path / "staged.zip"
        _make_pack_zip(staged)
        zip_bytes = staged.read_bytes()
        zip_path = qf.quaternius_cache_root() / "FakePack.zip"
        lock = _fake_lock(staged)
        lock["upload_filename"] = "FakePack.zip"

        def _fake_resolve(lock_arg: dict, pack: str = qf.DEFAULT_PACK) -> str:
            return "http://fake-signed-url"

        def _fake_download(
            url: str,
            dest: Path,
            *,
            referer_base: str = qf._ITCH_BASE,
            expected_sha256: str | None = None,
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zip_bytes)

        monkeypatch.setattr(qf, "_resolve_signed_download_url", _fake_resolve)
        monkeypatch.setattr(qf, "_download", _fake_download)
        monkeypatch.setattr(qf, "_lock_data", lambda pack=qf.DEFAULT_PACK: lock)

        pack = qf.fetch_quaternius_pack()
        assert pack.glb.is_file()
        assert zip_path.is_file()
        # bytes idênticos ao staged ⇒ sha do lock bate ⇒ cache válido
        assert qf.is_pack_cached() is True

    def test_extract_idempotent(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "FakePack.zip"
        _make_pack_zip(zip_path)
        inner1 = qf._extract(zip_path, tmp_path / "ex", "FakePack", "Unreal-Godot/Fake.glb")
        glb = inner1 / "Unreal-Godot" / "Fake.glb"
        mtime = glb.stat().st_mtime_ns
        inner2 = qf._extract(zip_path, tmp_path / "ex", "FakePack", "Unreal-Godot/Fake.glb")
        assert inner1 == inner2
        assert glb.stat().st_mtime_ns == mtime  # não re-extraiu

    def test_get_glb_path_uses_default_pack(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str] = {}

        def _fake_fetch(**kwargs):
            seen["pack"] = kwargs.get("pack", qf.DEFAULT_PACK)
            return qf.QuaterniusPack(
                root=Path("/tmp"),
                glb=Path("/tmp/a.glb"),
                fbx=Path("/tmp/a.fbx"),
                glb_root_motion=Path("/tmp/a_rm.glb"),
                version="v",
            )

        monkeypatch.setattr(qf, "fetch_quaternius_pack", _fake_fetch)
        assert qf.get_glb_path() == Path("/tmp/a.glb")
        assert seen["pack"] == "quaternius"

    def test_fetch_passes_pack_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str] = {}

        def _fake_fetch(**kwargs):
            seen["pack"] = kwargs.get("pack", qf.DEFAULT_PACK)
            return qf.QuaterniusPack(
                root=Path("/tmp"),
                glb=Path("/tmp/b.glb"),
                fbx=Path("/tmp/b.fbx"),
                glb_root_motion=Path("/tmp/b_rm.glb"),
                version="v",
            )

        monkeypatch.setattr(qf, "fetch_quaternius_pack", _fake_fetch)
        qf.fetch_quaternius_pack(pack="quaternius2", on_status=None)
        assert seen["pack"] == "quaternius2"


# ---------------------------------------------------------------------------
# fetch_itch_pack genérico (packs por-ficheiro, ex.: villager/KevDev)
# ---------------------------------------------------------------------------


def _fake_files_lock(zip_path: Path) -> dict:
    """Lock sintético de pack por-ficheiro: sem inner_dir, marker em vez de GLB."""
    return {
        "version": "1.0-free",
        "name": "Fake File Pack",
        "game_slug": "fake-files",
        "upload_id": 2,
        "upload_filename": zip_path.name,
        "expected_sha256": qf._sha256(zip_path),
        "expected_size": zip_path.stat().st_size,
        "inner_dir": "",
        "cache_dir": "itch/fake",
        "marker": "Models/Rig.fbx",
        "files": {},
    }


class TestFetchItchGeneric:
    def test_per_file_pack_extracts_to_root(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path / "cache"))
        # zip staged FORA do cache; o lock é patchado antes de qualquer leitura
        # de cache_root (senão o fetch tocaria na rede real).
        zip_path = tmp_path / "FakeFiles.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Models/Rig.fbx", b"FBX")
            zf.writestr("Animations/Clip.fbx", b"FBX")
        zip_bytes = zip_path.read_bytes()
        monkeypatch.setattr(qf, "_lock_data", lambda pack="villager": _fake_files_lock(zip_path))
        monkeypatch.setattr(qf, "_resolve_signed_download_url", lambda lock_arg, pack=qf.DEFAULT_PACK: "http://fake")

        def _fake_download(
            url: str,
            dest: Path,
            *,
            referer_base: str = qf._ITCH_BASE,
            expected_sha256: str | None = None,
        ) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zip_bytes)

        monkeypatch.setattr(qf, "_download", _fake_download)

        statuses: list[str] = []
        pack = qf.fetch_itch_pack(pack="villager", on_status=statuses.append)

        # inner_dir vazio: conteúdo extraído na raiz (zip sem pasta-topo).
        assert pack.root == qf.cache_root("villager") / "extracted"
        assert (pack.root / "Models/Rig.fbx").is_file()
        assert pack.version == "1.0-free"
        assert pack.pack == "villager"

    def test_cache_root_from_lockfile_cache_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path))
        assert qf.cache_root("villager") == tmp_path / "itch" / "villager"
        # packs Quaternius mantêm o layout antigo.
        assert qf.cache_root("quaternius2") == tmp_path / "quaternius"

    def test_fetch_quaternius_guard_rejects_per_file_pack(self) -> None:
        with pytest.raises(ValueError, match="fetch_itch_pack"):
            qf.fetch_quaternius_pack(pack="villager")

    def test_lock_without_marker_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path / "cache"))
        zip_path = tmp_path / "x.zip"
        zip_path.write_bytes(b"z")
        lock = _fake_files_lock(zip_path)
        lock["marker"] = ""
        monkeypatch.setattr(qf, "_lock_data", lambda pack="villager": lock)
        monkeypatch.setattr(qf, "_resolve_signed_download_url", lambda lock_arg, pack=qf.DEFAULT_PACK: "http://fake")
        monkeypatch.setattr(
            qf,
            "_download",
            lambda url, dest, *, referer_base=qf._ITCH_BASE, expected_sha256=None: (
                dest.parent.mkdir(parents=True, exist_ok=True) or dest.write_bytes(b"z")
            ),
        )
        with pytest.raises(ValueError, match="marker"):
            qf.fetch_itch_pack(pack="villager")

    def test_fetch_itch_pack_works_for_quaternius_too(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path / "cache"))
        root = qf.cache_root("quaternius")
        root.mkdir(parents=True)
        zip_path = root / "FakePack.zip"
        _make_pack_zip(zip_path)
        monkeypatch.setattr(qf, "_lock_data", lambda pack=qf.DEFAULT_PACK: _fake_lock(zip_path))
        pack = qf.fetch_itch_pack()
        assert pack.root == root / "extracted" / "FakePack"
        assert pack.pack == "quaternius"
