"""Vendored C runtime: source pinning, build command and cache behaviour."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from motion3d.sprong import runtime


class TestVendoredSources:
    def test_version_matches_the_vendored_header(self) -> None:
        assert runtime.vendored_version() == "1.1.0"

    def test_header_and_license_are_present(self) -> None:
        assert (runtime.VENDOR_DIR / "include" / "sprongmotion.h").is_file()
        assert (runtime.VENDOR_DIR / "LICENSE").is_file()
        assert (runtime.VENDOR_DIR / "NOTICE").is_file()

    def test_every_translation_unit_is_vendored(self) -> None:
        sources = sorted(path.name for path in (runtime.VENDOR_DIR / "src").glob("*.c"))
        assert len(sources) == 15
        expected = {
            "sm_bind.c",
            "sm_context.c",
            "sm_features.c",
            "sm_generator.c",
            "sm_gguf.c",
            "sm_layers.c",
            "sm_model.c",
            "sm_motion.c",
            "sm_options.c",
            "sm_path.c",
            "sm_pose.c",
            "sm_sampling.c",
            "sm_threads.c",
            "sm_timeline.c",
            "sm_tokenizer.c",
        }
        assert set(sources) == expected

    def test_source_digest_is_stable_and_hex(self) -> None:
        first = runtime.source_digest()
        second = runtime.source_digest()
        assert first == second
        assert len(first) == 64
        int(first, 16)

    def test_source_digest_tracks_content(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        before = runtime.source_digest()
        probe = runtime.VENDOR_DIR / "src" / "sm_probe.c"
        probe.write_text("/* probe */", encoding="utf-8")
        try:
            assert runtime.source_digest() != before
        finally:
            probe.unlink()

    def test_library_suffix_is_platform_specific(self) -> None:
        assert runtime.library_suffix() in {".so", ".dylib", ".dll"}

    def test_build_dir_is_versioned_and_hashed(self) -> None:
        directory = runtime.build_dir()
        assert "1.1.0" in directory.name
        assert runtime.source_digest()[:12] in directory.name


class TestBuildCommand:
    def test_keeps_results_reproducible(self) -> None:
        command = runtime.build_command("cc")
        assert "-ffp-contract=off" in command
        assert "-ffp-contract=fast" not in command

    def test_links_a_shared_library(self) -> None:
        command = runtime.build_command("cc")
        assert "-shared" in command or "-dynamiclib" in command

    def test_pic_and_visibility(self) -> None:
        command = runtime.build_command("cc")
        assert "-fPIC" in command
        assert "-fvisibility=hidden" in command
        assert "-DSM_SHARED" in command

    def test_no_werror(self) -> None:
        assert "-Werror" not in runtime.build_command("cc")

    def test_includes_the_vendored_header_dir(self) -> None:
        command = runtime.build_command("cc")
        assert str(runtime.VENDOR_DIR / "include") in command

    def test_compiles_every_source(self) -> None:
        command = runtime.build_command("cc")
        for source in (runtime.VENDOR_DIR / "src").glob("*.c"):
            assert str(source) in command

    def test_extra_cflags_are_appended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SPRONG_CFLAGS", "-O3 -march=native")
        command = runtime.build_command("cc")
        assert "-O3" in command and "-march=native" in command

    def test_compiler_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SPRONG_CC", "/opt/cc9")
        assert runtime.build_command()[0] == "/opt/cc9"

    def test_cc_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SPRONG_CC", raising=False)
        monkeypatch.setenv("CC", "clang-19")
        assert runtime.build_command()[0] == "clang-19"

    def test_default_compiler_is_cc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SPRONG_CC", raising=False)
        monkeypatch.delenv("CC", raising=False)
        assert runtime.build_command()[0] == "cc"


class TestEnsureRuntime:
    def test_sprong_lib_override_is_used(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = tmp_path / "libfake.so"
        fake.write_bytes(b"")
        monkeypatch.setenv("SPRONG_LIB", str(fake))
        assert runtime.ensure_runtime() == fake

    def test_missing_sprong_lib_override_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SPRONG_LIB", str(tmp_path / "gone.so"))
        with pytest.raises(runtime.RuntimeBuildError, match="SPRONG_LIB"):
            runtime.ensure_runtime()

    def test_missing_compiler_raises_with_instructions(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("SPRONG_LIB", raising=False)
        monkeypatch.setenv("SPRONG_CC", "definitely-not-a-compiler")
        with pytest.raises(runtime.RuntimeBuildError, match="compilador"):
            runtime.build_runtime(rebuild=True)

    def test_runtime_info_shape(self) -> None:
        info = runtime.runtime_info()
        for key in ("vendor_dir", "compiler", "platform", "version", "digest", "build_dir", "lib", "lib_state"):
            assert key in info
        assert info["lib_state"] in {"ok", "not built"}


@pytest.mark.skipif(shutil.which("cc") is None, reason="sem compilador C")
class TestRealBuild:
    def test_builds_a_loadable_library(self) -> None:
        library = runtime.build_runtime()
        assert library.is_file()
        assert library.stat().st_size > 50_000

    def test_cached_build_is_reused(self) -> None:
        first = runtime.build_runtime()
        second = runtime.build_runtime()
        assert first == second

    def test_rebuild_recreates_the_file(self) -> None:
        library = runtime.build_runtime()
        stamp = library.stat().st_mtime_ns
        runtime.build_runtime(rebuild=True)
        assert library.is_file() and library.stat().st_mtime_ns >= stamp

    def test_build_status_callback(self) -> None:
        messages: list[str] = []
        runtime.build_runtime(rebuild=True, on_status=messages.append)
        assert messages and "SprongMotion" in messages[0]

    def test_library_exports_the_api(self) -> None:
        library = runtime.ensure_runtime()
        result = subprocess.run(
            ["nm", "-D", "--defined-only", str(library)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("nm indisponível")
        assert "sm_generate_motion_ex" in result.stdout
        assert "sm_load_model" in result.stdout

    def test_cache_lives_under_the_aigamekit_cache(self) -> None:
        assert "aigamekit" in str(runtime.build_dir())
        assert runtime.build_dir().is_relative_to(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"))
