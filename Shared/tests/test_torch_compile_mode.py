"""Unit tests para resolve_torch_compile_mode / apply_channels_last helpers."""

from __future__ import annotations

from aigamekit_shared.quantization import resolve_torch_compile_mode


class TestResolveTorchCompileMode:
    def test_default_unchanged(self) -> None:
        assert resolve_torch_compile_mode("default") == "default"
        assert resolve_torch_compile_mode("default", offload="model_cpu") == "default"

    def test_cudagraphs_full_gpu(self) -> None:
        assert resolve_torch_compile_mode("reduce-overhead", offload="none") == "reduce-overhead"
        assert resolve_torch_compile_mode("max-autotune", offload="none") == "max-autotune"

    def test_cudagraphs_downgrade_with_offload(self) -> None:
        assert resolve_torch_compile_mode("reduce-overhead", offload="model_cpu") == "default"
        assert resolve_torch_compile_mode("max-autotune", offload="group_stream") == "default"
        assert resolve_torch_compile_mode("reduce-overhead", group_offload_active=True) == "default"


class TestAttentionBackendDispatch:
    def test_uses_diffusers_attention_dispatch_enum(self, monkeypatch) -> None:
        import sys
        import types
        from unittest.mock import MagicMock

        from aigamekit_shared.attention import select_attention_backend

        fake_enum = types.SimpleNamespace(NATIVE="native", SAGE="sage", FLASH="flash", FLEX="flex")
        fake_dispatch = types.ModuleType("diffusers.models.attention_dispatch")
        fake_dispatch.AttentionBackendName = fake_enum  # type: ignore[attr-defined]
        fake_models = types.ModuleType("diffusers.models")
        fake_diffusers = types.ModuleType("diffusers")

        monkeypatch.setitem(sys.modules, "diffusers", fake_diffusers)
        monkeypatch.setitem(sys.modules, "diffusers.models", fake_models)
        monkeypatch.setitem(sys.modules, "diffusers.models.attention_dispatch", fake_dispatch)

        transformer = MagicMock()
        pipe = types.SimpleNamespace(transformer=transformer, vae=MagicMock())
        applied = select_attention_backend(pipe, backend="sdpa")
        assert applied == "sdpa"
        transformer.set_attention_backend.assert_called_once_with("native")
        pipe.vae.set_attention_backend.assert_not_called()

    def test_fallback_to_sdpa_when_transformer_rejects_backend(self, monkeypatch) -> None:
        import sys
        import types
        from unittest.mock import MagicMock

        from aigamekit_shared.attention import select_attention_backend

        fake_enum = types.SimpleNamespace(SAGE="sage", NATIVE="native")
        fake_dispatch = types.ModuleType("diffusers.models.attention_dispatch")
        fake_dispatch.AttentionBackendName = fake_enum  # type: ignore[attr-defined]

        monkeypatch.setitem(sys.modules, "diffusers.models.attention_dispatch", fake_dispatch)

        transformer = MagicMock()
        transformer.set_attention_backend.side_effect = ValueError("sage unsupported")
        pipe = types.SimpleNamespace(transformer=transformer)
        applied = select_attention_backend(pipe, backend="sage")
        assert applied == "sdpa"


class TestChannelsLastConvOnly:
    def test_applies_to_vae_and_unet_but_skips_dit_transformer(self, monkeypatch) -> None:
        import types

        from aigamekit_shared.base_generator import DiffusionGeneratorBase

        applied_targets: list[object] = []

        def _fake_apply(mod, *, log_fn=None):
            applied_targets.append(mod)
            return True

        monkeypatch.setattr("aigamekit_shared.quantization.apply_channels_last", _fake_apply)

        class _DummyGen(DiffusionGeneratorBase):
            def _load_pipeline(self):
                return None

            def generate(self, prompt: str, **kwargs):
                return None, {}

        gen = _DummyGen(device="cpu", channels_last=True)
        gen.device = "cuda"

        vae, unet, transformer = object(), object(), object()
        pipe = types.SimpleNamespace(vae=vae, unet=unet, transformer=transformer)
        gen._maybe_apply_channels_last(pipe, plan=None)
        assert applied_targets == [vae, unet]
        assert transformer not in applied_targets
