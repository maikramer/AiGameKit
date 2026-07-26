"""Testes do base_generator — infraestrutura partilhada (com mock, sem GPU)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gamedev_shared.base_generator import DiffusionGeneratorBase, torch_dtype_for


class _MockGenerator(DiffusionGeneratorBase):
    """Generator concreto de teste."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.load_calls = 0

    def _load_pipeline(self) -> Any:
        self.load_calls += 1
        if self._pipe is None:
            self._pipe = MagicMock(name="mock_pipeline")
        return self._pipe

    def generate(self, prompt: str, **kwargs: Any) -> tuple[Any, dict]:
        pipe = self._load_pipeline()
        return pipe(prompt=prompt), {"prompt": prompt}


class TestTorchDtypeFor:
    """torch_dtype_for helper — usa torch real (presente no venv de teste)."""

    def test_cpu_returns_float32(self) -> None:
        torch = pytest.importorskip("torch")

        result = torch_dtype_for("cpu")
        assert result == torch.float32

    def test_cuda_returns_bfloat16_if_available(self) -> None:
        torch = pytest.importorskip("torch")

        if not torch.cuda.is_available():
            pytest.skip("Sem GPU CUDA disponível")
        result = torch_dtype_for("cuda")
        assert result == torch.bfloat16


class TestLifecycle:
    """warmup / unload / idempotência."""

    def test_warmup_loads_pipeline(self) -> None:
        gen = _MockGenerator(device="cpu")
        gen.warmup()
        assert gen.load_calls == 1
        assert gen._pipe is not None

    def test_warmup_is_idempotent(self) -> None:
        gen = _MockGenerator(device="cpu")
        gen.warmup()
        gen.warmup()
        # _load_pipeline deve ser chamado mas _pipe só criado 1x (mock retorna mesmo).
        assert gen._pipe is not None

    def test_unload_clears_pipe(self) -> None:
        gen = _MockGenerator(device="cpu")
        gen.warmup()
        assert gen._pipe is not None
        gen.unload()
        assert gen._pipe is None

    def test_unload_idempotent_when_not_loaded(self) -> None:
        gen = _MockGenerator(device="cpu")
        gen.unload()  # não deve levantar
        assert gen._pipe is None


class TestLogging:
    """_log / _status / set_status_callback."""

    def test_verbose_log_calls_logger(self, capsys: Any) -> None:
        gen = _MockGenerator(device="cpu", verbose=True)
        gen._log("test message")
        captured = capsys.readouterr()
        assert "test message" in captured.out

    def test_non_verbose_no_log(self, capsys: Any) -> None:
        gen = _MockGenerator(device="cpu", verbose=False)
        gen._log("hidden message")
        captured = capsys.readouterr()
        assert "hidden message" not in captured.out

    def test_status_callback(self) -> None:
        gen = _MockGenerator(device="cpu")
        messages: list[str] = []
        gen.set_status_callback(lambda msg: messages.append(msg))
        gen._status("phase 1")
        assert messages == ["phase 1"]


class TestSeedResolution:
    """_resolve_seed gera seed para None/<0."""

    def test_none_seed_generates_random(self) -> None:
        gen = _MockGenerator(device="cpu")
        seed = gen._resolve_seed(None)
        assert isinstance(seed, int)
        assert seed >= 0

    def test_negative_seed_generates_random(self) -> None:
        gen = _MockGenerator(device="cpu")
        seed = gen._resolve_seed(-1)
        assert isinstance(seed, int)
        assert seed >= 0

    def test_explicit_seed_kept(self) -> None:
        gen = _MockGenerator(device="cpu")
        assert gen._resolve_seed(42) == 42


class TestSaveImage:
    """save_image static method."""

    def test_save_creates_file(self, tmp_path: Any) -> None:
        from PIL import Image

        img = Image.new("RGB", (10, 10), (255, 0, 0))
        path = tmp_path / "subdir" / "test.png"
        result = DiffusionGeneratorBase.save_image(img, path)
        assert result == path
        assert path.exists()

    def test_save_with_string_path(self, tmp_path: Any) -> None:
        from PIL import Image

        img = Image.new("RGB", (10, 10))
        path = str(tmp_path / "out.png")
        DiffusionGeneratorBase.save_image(img, path)
        from pathlib import Path

        assert Path(path).exists()


class TestGenerateBatch:
    """generate_batch — generator que yielding (image, metadata, idx) com error handling."""

    def test_batch_yields_3tuples_per_prompt(self) -> None:
        gen = _MockGenerator(device="cpu")
        prompts = ["a", "b", "c"]
        results = list(gen.generate_batch(prompts))
        assert len(results) == 3
        for image, metadata, idx in results:
            assert image is not None
            assert isinstance(metadata, dict)
            assert isinstance(idx, int)

    def test_batch_yields_correct_indices(self) -> None:
        gen = _MockGenerator(device="cpu")
        results = list(gen.generate_batch(["a", "b", "c"]))
        indices = [r[2] for r in results]
        assert indices == [0, 1, 2]

    def test_batch_increments_seed(self) -> None:
        gen = _MockGenerator(device="cpu")
        seeds_seen: list[int] = []

        def mock_generate(prompt: str, **kwargs: Any) -> tuple[Any, dict]:
            seeds_seen.append(kwargs.get("seed"))
            return MagicMock(), {"prompt": prompt}

        gen.generate = mock_generate  # type: ignore[method-assign]
        list(gen.generate_batch(["a", "b", "c"], seed=100))
        assert seeds_seen == [100, 101, 102]

    def test_batch_error_yields_none_image(self) -> None:
        """Item falhado → (None, {error: ...}, idx) — caller continua."""
        gen = _MockGenerator(device="cpu")

        def flaky_generate(prompt: str, **kwargs: Any) -> tuple[Any, dict]:
            if prompt == "fail":
                raise RuntimeError("boom")
            return MagicMock(), {"prompt": prompt}

        gen.generate = flaky_generate  # type: ignore[method-assign]
        results = list(gen.generate_batch(["ok", "fail", "ok"]))
        assert len(results) == 3
        assert results[0][0] is not None  # "ok" → image
        assert results[1][0] is None  # "fail" → None
        assert "boom" in results[1][1]["error"]
        assert results[2][0] is not None  # "ok" → image


class TestHooks:
    """Hooks opcionais (_augment_prompt, _post_process)."""

    def test_default_augment_prompt_passthrough(self) -> None:
        gen = _MockGenerator(device="cpu")
        assert gen._augment_prompt("hello") == "hello"

    def test_default_post_process_passthrough(self) -> None:
        gen = _MockGenerator(device="cpu")
        img, meta = gen._post_process("img", {"k": "v"})
        assert img == "img"
        assert meta == {"k": "v"}

    def test_override_augment_prompt(self) -> None:
        class _CustomGen(_MockGenerator):
            def _augment_prompt(self, prompt: str, **kwargs: Any) -> str:
                return f"[enhanced] {prompt}"

        gen = _CustomGen(device="cpu")
        assert gen._augment_prompt("hello") == "[enhanced] hello"
