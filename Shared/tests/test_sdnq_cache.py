"""Testes do SDNQ Disk Cache bit-exact (aigamekit_shared.sdnq)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aigamekit_shared.sdnq import (
    SDNQ_CACHE_DIR_ENV,
    SDNQ_CACHE_ENV,
    SDNQ_CACHE_SCHEMA_VERSION,
    build_sdnq_cache_key,
    load_sdnq_cached_model,
    load_sdnq_state_cache,
    resolve_hf_snapshot_revision,
    save_sdnq_cached_model,
    save_sdnq_state_cache,
    sdnq_cache_dir,
    sdnq_cache_enabled,
)


class TestSdnqCacheConfig:
    """Env vars, fingerprint determinism e invalidação de chave."""

    def test_enabled_by_default_and_kill_switch(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert sdnq_cache_enabled() is True
        for val in ("0", "false", "no", "off", "FALSE"):
            with patch.dict("os.environ", {SDNQ_CACHE_ENV: val}):
                assert sdnq_cache_enabled() is False
        with patch.dict("os.environ", {SDNQ_CACHE_ENV: "1"}):
            assert sdnq_cache_enabled() is True

    def test_cache_dir_override(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {SDNQ_CACHE_DIR_ENV: str(tmp_path / "custom")}):
            assert sdnq_cache_dir() == tmp_path / "custom"

    def test_resolve_hf_snapshot_revision_reads_refs_main(self, tmp_path: Path) -> None:
        ref = tmp_path / "models--org--repo" / "refs" / "main"
        ref.parent.mkdir(parents=True)
        ref.write_text("abc123def456\n", encoding="utf-8")
        assert resolve_hf_snapshot_revision("org/repo", cache_dir=tmp_path) == "abc123def456"

    def test_resolve_hf_snapshot_revision_local_dir(self, tmp_path: Path) -> None:
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        rev = resolve_hf_snapshot_revision(str(tmp_path))
        assert rev.startswith("local:")

    def test_cache_key_deterministic_and_invalidates_on_changes(self) -> None:
        k1 = build_sdnq_cache_key(
            "black-forest-labs/flux-3-image-klein",
            "transformer",
            "sdnq-int4",
            revision="rev1",
            modules_to_not_convert=["early_stream_modulations", "single_stream_modulations"],
            dequantize_fp32=False,
        )
        k2 = build_sdnq_cache_key(
            "black-forest-labs/flux-3-image-klein",
            "transformer",
            "sdnq-int4",
            revision="rev1",
            # Ordem diferente das skip keys não muda o hash
            modules_to_not_convert=["single_stream_modulations", "early_stream_modulations"],
            dequantize_fp32=False,
        )
        assert k1 == k2
        assert k1.startswith("transformer-sdnq-int4-")

        # Mudanças em qualquer eixo invalidam a chave
        assert k1 != build_sdnq_cache_key(
            "black-forest-labs/flux-3-image-klein",
            "transformer",
            "sdnq-uint8",
            revision="rev1",
            modules_to_not_convert=["early_stream_modulations", "single_stream_modulations"],
            dequantize_fp32=False,
        )
        assert k1 != build_sdnq_cache_key(
            "black-forest-labs/flux-3-image-klein",
            "transformer",
            "sdnq-int4",
            revision="rev2",
            modules_to_not_convert=["early_stream_modulations", "single_stream_modulations"],
            dequantize_fp32=False,
        )
        assert k1 != build_sdnq_cache_key(
            "black-forest-labs/flux-3-image-klein",
            "transformer",
            "sdnq-int4",
            revision="rev1",
            modules_to_not_convert=None,
            dequantize_fp32=False,
        )
        assert k1 != build_sdnq_cache_key(
            "black-forest-labs/flux-3-image-klein",
            "transformer",
            "sdnq-int4",
            revision="rev1",
            modules_to_not_convert=["early_stream_modulations", "single_stream_modulations"],
            dequantize_fp32=True,
        )

    def test_load_returns_none_on_missing_or_incomplete_cache(self, tmp_path: Path) -> None:
        assert load_sdnq_cached_model(tmp_path / "missing") is None

        incomplete = tmp_path / "incomplete"
        incomplete.mkdir()
        (incomplete / "config.json").write_text("{}", encoding="utf-8")
        (incomplete / "quantization_config.json").write_text("{}", encoding="utf-8")
        (incomplete / "cache_meta.json").write_text(json.dumps({"complete": False}), encoding="utf-8")
        assert load_sdnq_cached_model(incomplete) is None


class TestSdnqCacheRoundtrip:
    """Paridade bit-exact do cache SDNQ com double-quant (scale_dtype='uint8')."""

    def test_flux3_double_quant_roundtrip_is_bit_exact(self, tmp_path: Path) -> None:
        torch = pytest.importorskip("torch")
        pytest.importorskip("sdnq")
        pytest.importorskip("safetensors")
        pytest.importorskip("accelerate")
        diffusers_flux3 = pytest.importorskip("diffusers.models.transformers.transformer_flux3")

        from aigamekit_shared.sdnq import quantize_model, register_sdnq

        register_sdnq()
        model = diffusers_flux3.Flux3Transformer2DModel(
            num_layers=1,
            num_single_layers=1,
            attention_head_dim=32,
            num_attention_heads=8,
            joint_attention_dim=256,
            in_channels=32,
            cond_in_channels=32,
            flow_map=True,
            axes_dims_rope=(8, 8, 8, 8),
        )
        for name, param in model.named_parameters():
            if "norm_q" not in name and "norm_k" not in name:
                param.data = param.data.to(torch.bfloat16)

        quantized = quantize_model(
            model,
            "sdnq-int4",
            quantization_device="cpu",
            return_device="cpu",
            dequantize_fp32=False,
            modules_to_not_convert=["early_stream_modulations", "single_stream_modulations"],
        )

        # Modulações adaLN ficam em bf16 puro (não quantizadas)
        assert quantized.early_stream_modulations.image.linear.weight.dtype == torch.bfloat16
        assert quantized.single_stream_modulations.image.linear.weight.dtype == torch.bfloat16

        cache_entry = tmp_path / "flux3_transformer"
        save_sdnq_cached_model(quantized, cache_entry, meta={"component": "transformer"})
        assert (cache_entry / "cache_meta.json").is_file()

        reloaded = load_sdnq_cached_model(
            cache_entry,
            torch_dtype=torch.bfloat16,
            device="cpu",
            dequantize_fp32=False,
        )
        assert reloaded is not None
        assert reloaded.early_stream_modulations.image.linear.weight.dtype == torch.bfloat16
        assert reloaded.single_transformer_blocks[0].attn.norm_q.weight.dtype == torch.float32

        torch.manual_seed(42)
        hs = torch.randn(1, 4, 32, dtype=torch.bfloat16)
        enc = torch.randn(1, 4, 256, dtype=torch.bfloat16)
        img_ids = torch.zeros(4, 4, dtype=torch.long)
        txt_ids = torch.zeros(4, 4, dtype=torch.long)
        ts = torch.tensor([0.5], dtype=torch.bfloat16)
        tts = torch.tensor([0.25], dtype=torch.bfloat16)
        with torch.inference_mode():
            y1 = quantized(
                hidden_states=hs,
                encoder_hidden_states=enc,
                timestep=ts,
                target_timestep=tts,
                img_ids=img_ids,
                txt_ids=txt_ids,
            ).sample
            y2 = reloaded(
                hidden_states=hs,
                encoder_hidden_states=enc,
                timestep=ts,
                target_timestep=tts,
                img_ids=img_ids,
                txt_ids=txt_ids,
            ).sample
        assert (y1 - y2).abs().max().item() == 0.0

    def test_qwen3_tied_weights_roundtrip_zero_meta_buffers(self, tmp_path: Path) -> None:
        torch = pytest.importorskip("torch")
        pytest.importorskip("sdnq")
        pytest.importorskip("safetensors")
        pytest.importorskip("accelerate")
        transformers = pytest.importorskip("transformers")
        if not hasattr(transformers, "Qwen3Config"):
            pytest.skip("transformers sem Qwen3Config")

        from aigamekit_shared.sdnq import quantize_model, register_sdnq

        register_sdnq()
        cfg = transformers.Qwen3Config(
            vocab_size=256,
            hidden_size=128,
            intermediate_size=256,
            num_hidden_layers=1,
            num_attention_heads=4,
            num_key_value_heads=2,
        )
        lm = transformers.Qwen3ForCausalLM(cfg)
        for p in lm.parameters():
            p.data = p.data.to(torch.bfloat16)
        qlm = quantize_model(lm, "sdnq-int4", quantization_device="cpu", return_device="cpu", dequantize_fp32=False)

        cache_entry = tmp_path / "qwen3_te"
        save_sdnq_cached_model(qlm, cache_entry)
        reloaded = load_sdnq_cached_model(cache_entry, torch_dtype=torch.bfloat16, device="cpu", dequantize_fp32=False)
        assert reloaded is not None
        assert not any(b.device.type == "meta" for b in reloaded.buffers())

        ids = torch.randint(0, 256, (1, 8))
        with torch.inference_mode():
            o1 = qlm(input_ids=ids).logits
            o2 = reloaded(input_ids=ids).logits
        assert (o1 - o2).abs().max().item() == 0.0

    def test_custom_module_state_cache_bit_exact_roundtrip(self, tmp_path: Path) -> None:
        torch = pytest.importorskip("torch")
        pytest.importorskip("sdnq")
        pytest.importorskip("safetensors")
        pytest.importorskip("accelerate")

        from aigamekit_shared.sdnq import quantize_model, register_sdnq

        register_sdnq()

        class _MiniCustomDiT(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.x_embedder = torch.nn.Linear(64, 256)
                self.block_fc1 = torch.nn.Linear(256, 512)
                self.block_fc2 = torch.nn.Linear(512, 256)
                self.norm = torch.nn.LayerNorm(256)
                self.final_layer = torch.nn.Linear(256, 64)
                self.register_buffer("pos_bias", torch.linspace(-0.1, 0.1, 256))

            def forward(self, x: torch.Tensor) -> torch.Tensor:
                h = self.x_embedder(x) + self.pos_bias.to(x.dtype)
                h = self.block_fc2(torch.nn.functional.gelu(self.block_fc1(h)))
                return self.final_layer(self.norm(h))

        torch.manual_seed(7)
        model = _MiniCustomDiT().to(torch.float16)
        quantized = quantize_model(
            model,
            "sdnq-int4",
            quantization_device="cpu",
            return_device="cpu",
            dequantize_fp32=False,
            modules_to_not_convert=["x_embedder", "final_layer"],
        )
        assert quantized.x_embedder.weight.dtype == torch.float16
        assert quantized.final_layer.weight.dtype == torch.float16

        cache_entry = tmp_path / "custom_dit"
        save_sdnq_state_cache(quantized, cache_entry, meta={"component": "dit_model"})
        assert (cache_entry / "cache_meta.json").is_file()
        assert (cache_entry / "model.safetensors").is_file()

        reloaded = load_sdnq_state_cache(
            cache_entry,
            _MiniCustomDiT,
            torch_dtype=torch.float16,
            device="cpu",
            dequantize_fp32=False,
        )
        assert reloaded is not None
        assert reloaded.x_embedder.weight.dtype == torch.float16
        assert reloaded.final_layer.weight.dtype == torch.float16
        assert not any(b.device.type == "meta" for b in reloaded.buffers())

        x = torch.randn(2, 4, 64, dtype=torch.float16)
        with torch.inference_mode():
            y1 = quantized(x)
            y2 = reloaded(x)
        assert (y1 - y2).abs().max().item() == 0.0

    def test_state_cache_returns_none_on_miss_or_wrong_format(self, tmp_path: Path) -> None:
        assert load_sdnq_state_cache(tmp_path / "missing", lambda: None) is None

        bad = tmp_path / "wrong_format"
        bad.mkdir()
        (bad / "quantization_config.json").write_text("{}", encoding="utf-8")
        (bad / "cache_meta.json").write_text(
            json.dumps({"schema": SDNQ_CACHE_SCHEMA_VERSION, "complete": True, "format": "diffusers"}),
            encoding="utf-8",
        )
        assert load_sdnq_state_cache(bad, lambda: None) is None
