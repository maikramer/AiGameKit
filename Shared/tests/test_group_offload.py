"""Testes do group_offload — técnica de baixa VRAM com CUDA streams."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gamedev_shared.group_offload import (
    GroupOffloadConfig,
    is_group_offload_enabled,
    plan_group_offload,
    try_group_offloading,
)
from gamedev_shared.lowvram import ModelFootprint


class TestIsGroupOffloadEnabled:
    """is_group_offload_enabled: env vars controlam a flag."""

    def test_default_enabled(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert is_group_offload_enabled() is True

    def test_global_disable(self) -> None:
        with patch.dict("os.environ", {"GAMEDEV_GROUP_OFFLOAD": "0"}):
            assert is_group_offload_enabled() is False

    def test_global_enable(self) -> None:
        with patch.dict("os.environ", {"GAMEDEV_GROUP_OFFLOAD": "1"}):
            assert is_group_offload_enabled() is True

    def test_tool_env_overrides_global(self) -> None:
        """Env var da tool tem precedência sobre o global."""
        with patch.dict("os.environ", {"GAMEDEV_GROUP_OFFLOAD": "1", "TEXTURE2D_GROUP_OFFLOAD": "0"}):
            assert is_group_offload_enabled(tool_env_var="TEXTURE2D_GROUP_OFFLOAD") is False

    def test_tool_env_false_variants(self) -> None:
        for false_val in ("0", "false", "no", "off", "FALSE"):
            with patch.dict("os.environ", {"TOOL_GO": false_val}):
                assert is_group_offload_enabled(tool_env_var="TOOL_GO") is False

    def test_tool_env_true_variants(self) -> None:
        for true_val in ("1", "true", "yes", "on", "TRUE"):
            with patch.dict("os.environ", {"TOOL_GO": true_val}):
                assert is_group_offload_enabled(tool_env_var="TOOL_GO") is True

    def test_no_tool_env_uses_global(self) -> None:
        with patch.dict("os.environ", {"GAMEDEV_GROUP_OFFLOAD": "0"}, clear=True):
            assert is_group_offload_enabled(tool_env_var="TOOL_GO") is False


class TestTryGroupOffloading:
    """try_group_offloading: aplica offload aos módulos do pipeline."""

    def test_returns_false_if_diffusers_unavailable(self) -> None:
        """Sem diffusers.hooks, retorna False (caller faz fallback)."""
        pipe = MagicMock()
        # Mockar o import dentro da função para simular ImportError.
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args, **kwargs):
            if name == "diffusers.hooks":
                raise ImportError("no diffusers")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            result = try_group_offloading(pipe, log=False)
        assert result is False

    def test_applies_to_transformer_and_vae(self) -> None:
        """ModelMixin modules usam enable_group_offload nativo."""
        transformer = MagicMock()
        vae = MagicMock()
        pipe = MagicMock()
        pipe.transformer = transformer
        pipe.vae = vae
        pipe.text_encoder = None
        pipe.text_encoder_2 = None

        # Import real do diffusers (presente no venv de teste) e patch apply_group_offloading.
        try:
            import diffusers.hooks as real_hooks
        except ImportError:
            import pytest

            pytest.skip("diffusers não instalado")

        with patch.object(real_hooks, "apply_group_offloading"):
            result = try_group_offloading(pipe, log=False)

        assert result is True
        transformer.enable_group_offload.assert_called_once()
        vae.enable_group_offload.assert_called_once()

    def test_use_stream_true_by_default(self) -> None:
        """use_stream=True é o default — a otimização-chave de performance."""
        transformer = MagicMock()
        pipe = MagicMock()
        pipe.transformer = transformer
        pipe.vae = None
        pipe.text_encoder = None
        pipe.text_encoder_2 = None

        try:
            import diffusers.hooks as real_hooks
        except ImportError:
            import pytest

            pytest.skip("diffusers não instalado")

        with patch.object(real_hooks, "apply_group_offloading"):
            try_group_offloading(pipe, log=False)

        # Verificar que use_stream=True foi passado.
        call_kwargs = transformer.enable_group_offload.call_args[1]
        assert call_kwargs["use_stream"] is True

    def test_returns_false_on_exception(self) -> None:
        """Se enable_group_offload levanta, retorna False (fallback)."""
        transformer = MagicMock()
        transformer.enable_group_offload = MagicMock(side_effect=RuntimeError("OOM"))
        pipe = MagicMock()
        pipe.transformer = transformer
        pipe.vae = None
        pipe.text_encoder = None
        pipe.text_encoder_2 = None

        try:
            import diffusers.hooks as real_hooks
        except ImportError:
            import pytest

            pytest.skip("diffusers não instalado")

        with patch.object(real_hooks, "apply_group_offloading"):
            result = try_group_offloading(pipe, log=False)

        assert result is False


class TestPlanGroupOffload:
    """plan_group_offload — a fórmula pura de auto-tuning VRAM-aware."""

    # FLUX 9B: ~18 GiB fp16, maior módulo ~7.2 GiB, ativação 1.5 GiB.
    FLUX_9B = ModelFootprint(fp16_weights_gib=18.0, activation_gib=1.5, largest_module_gib=7.2)

    def test_model_fits_no_offload(self) -> None:
        """Pesos + ativação cabem na GPU → None (sem offload, mais rápido)."""
        # 18 GiB pesos + 1.5 ativação = 19.5 ≤ 20 GiB → cabe.
        cfg = plan_group_offload(20.0, self.FLUX_9B, quant_mode="none")
        assert cfg is None

    def test_6gb_gpu_tight_leaf_level(self) -> None:
        """GPU 6 GiB + FLUX: muito apertado → leaf_level + stream (VRAM mínima)."""
        cfg = plan_group_offload(5.4, self.FLUX_9B, quant_mode="none")  # 6 GiB * 0.9
        assert cfg is not None
        assert cfg.offload_type == "leaf_level"
        assert cfg.use_stream is True
        assert cfg.record_stream is True

    def test_12gb_gpu_enough_for_blocks(self) -> None:
        """GPU 12 GiB + FLUX 9B: folga para blocks → block_level + stream."""
        # usable 12*0.9 = 10.8; activation 1.5 → headroom 9.3.
        # largest 7.2 → 7.2 * 1.2 = 8.64; headroom 9.3 >= 8.64 → block_level.
        cfg = plan_group_offload(10.8, self.FLUX_9B, quant_mode="none")
        assert cfg is not None
        assert cfg.offload_type == "block_level"
        assert cfg.use_stream is True
        assert cfg.num_blocks_per_group == 1  # diffusers exige 1 com stream

    def test_quantized_reduces_need(self) -> None:
        """Modelo int4 cabe na GPU → None mesmo com GPU pequena."""
        # int4: 18 * 0.32 = 5.76 + 1.5 = 7.26 > 5.4 (6 GiB) → ainda offload.
        cfg = plan_group_offload(5.4, self.FLUX_9B, quant_mode="sdnq-int4")
        assert cfg is not None
        # Mas com GPU maior (8 GiB usable 7.2), int4 cabe: 5.76 + 1.5 = 7.26 > 7.2 → edge.
        # Vamos a 10 GiB usable: 9.0 → 5.76 + 1.5 = 7.26 ≤ 9.0 → None.
        cfg2 = plan_group_offload(9.0, self.FLUX_9B, quant_mode="sdnq-int4")
        assert cfg2 is None


class TestGroupOffloadConfig:
    def test_summary_leaf_level(self) -> None:
        cfg = GroupOffloadConfig(offload_type="leaf_level", use_stream=True, record_stream=True)
        s = cfg.summary()
        assert "leaf_level" in s
        assert "stream" in s

    def test_summary_block_level(self) -> None:
        cfg = GroupOffloadConfig(offload_type="block_level", use_stream=True, num_blocks_per_group=1)
        s = cfg.summary()
        assert "block_level" in s

    def test_defaults(self) -> None:
        cfg = GroupOffloadConfig()
        assert cfg.offload_type == "leaf_level"
        assert cfg.use_stream is True


class TestTryGroupOffloadingWithConfig:
    """try_group_offloading aceita config e usa os seus parâmetros."""

    def test_config_overrides_defaults(self) -> None:
        """Um config block_level propaga-se ao enable_group_offload."""
        transformer = MagicMock()
        pipe = MagicMock()
        pipe.transformer = transformer
        pipe.vae = None
        pipe.text_encoder = None
        pipe.text_encoder_2 = None

        try:
            import diffusers.hooks as real_hooks
        except ImportError:
            import pytest

            pytest.skip("diffusers não instalado")

        cfg = GroupOffloadConfig(offload_type="block_level", use_stream=True, num_blocks_per_group=1)
        with patch.object(real_hooks, "apply_group_offloading"):
            try_group_offloading(pipe, config=cfg, log=False)

        call_kwargs = transformer.enable_group_offload.call_args[1]
        assert call_kwargs["offload_type"] == "block_level"
        assert call_kwargs["num_blocks_per_group"] == 1
        assert call_kwargs["use_stream"] is True

    def test_custom_modules_for_custom_pipeline(self) -> None:
        """modules= permite aplicar a pipelines custom (ex: Paint3D)."""
        custom_mod = MagicMock()
        pipe = MagicMock()
        pipe.unet = custom_mod  # nome não-standard

        try:
            import diffusers.hooks as real_hooks
        except ImportError:
            import pytest

            pytest.skip("diffusers não instalado")

        with patch.object(real_hooks, "apply_group_offloading"):
            result = try_group_offloading(pipe, modules=("unet",), log=False)

        assert result is True
        custom_mod.enable_group_offload.assert_called_once()
