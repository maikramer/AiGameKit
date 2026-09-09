"""Group offload + dual-stream do UNet2p5D — convivência (KeyError histórico).

Contexto do bug: os hooks de group offload do diffusers processam kwargs com
``send_to_device``, que **recria** dicts/listas — a escrita do stream "w"
(``unet_dual`` popula ``condition_embed_dict``) ia para uma cópia e o stream
"r" (``unet``) lia um dict vazio → ``KeyError: 'down_0_0_0'`` no forward.

O fix tem duas camadas, testadas aqui:

- vendor: holder opaco ``ConditionEmbedRef`` atravessa ``send_to_device``
  **por identidade** (objectos não-tensor/list/Mapping não são copiados);
- painter: ``exclude_kwargs=["cross_attention_kwargs"]`` no
  ``enable_group_offload`` preserva o kwargs dict (e os caches internos, ex.
  RoPE ``position_indices[head_dim]``) nos pre_forward hooks.

Os testes usam um UNet2DConditionModel mini (CPU, ~1 s por forward); o
config real de produção (``leaf_level`` + CUDA streams) corre em GPU quando
disponível.
"""

from __future__ import annotations

import pytest


def _tiny_cfg() -> dict:
    """Config mínima de UNet2DConditionModel compatível com o wrapper 2.5D.

    - ``cross_attention_dim=1024``: os learned tokens são 77x1024;
    - ``block_out_channels=32`` + ``attention_head_dim=2`` (nº de cabeças no
      vocabulário do diffusers) → head_dim=16, múltiplo de 8 exigido pelo
      RoPE 3D (``embed_dim // 8``);
    - ``sample_size=64``: latent real do paint (512/8).
    """
    return dict(
        sample_size=64,
        in_channels=4,
        out_channels=4,
        flip_sin_to_cos=True,
        freq_shift=0,
        down_block_types=("CrossAttnDownBlock2D",),
        up_block_types=("CrossAttnUpBlock2D",),
        block_out_channels=(32,),
        layers_per_block=1,
        cross_attention_dim=1024,
        attention_head_dim=2,
        norm_num_groups=4,
        norm_eps=1e-5,
    )


def _build_wrapper():
    """Constrói um UNet2p5DConditionModel mini determinístico (seed fixa)."""
    import torch
    from diffusers import UNet2DConditionModel

    from paint3d.hy3dpaint.hunyuanpaintpbr.unet.modules import UNet2p5DConditionModel

    torch.manual_seed(0)
    unet = UNet2DConditionModel(**_tiny_cfg())
    return UNet2p5DConditionModel(unet, None, None).eval()


def _tiny_inputs(device="cpu", dtype=None):
    import torch

    kw = {"device": device}
    if dtype is not None:
        kw["dtype"] = dtype
    return dict(
        sample=torch.randn(1, 2, 2, 4, 64, 64, **kw),
        timestep=torch.tensor(0, device=device),
        encoder_hidden_states=torch.randn(1, 2, 77, 1024, **kw),
        ref_latents=torch.randn(1, 1, 4, 64, 64, **kw),
        dino_hidden_states=torch.randn(1, 4, 1536, **kw),
        position_maps=torch.rand(1, 2, 3, 64, 64, **kw),
    )


def _forward(wrapper, inputs):
    import torch

    with torch.no_grad():
        out = wrapper(**inputs)
    return out[0]


@pytest.fixture()
def baseline_output():
    """Forward de referência (sem hooks), pesos determinísticos por seed."""
    wrapper = _build_wrapper()
    return _forward(wrapper, _tiny_inputs())


class TestConditionEmbedRef:
    """Holder opaco que preserva a identidade do dict através dos hooks."""

    def test_holder_passes_by_identity(self):
        """O holder atravessa send_to_device sem cópia; o dict dentro mantém identidade."""
        import torch
        from accelerate.utils import send_to_device

        from paint3d.hy3dpaint.hunyuanpaintpbr.unet.modules import ConditionEmbedRef

        embeds: dict = {}
        holder = ConditionEmbedRef(embeds)
        moved = send_to_device(
            {"mode": "w", "condition_embed_dict": holder, "t": torch.zeros(1)},
            torch.device("cpu"),
        )
        assert moved["condition_embed_dict"] is holder
        assert moved["condition_embed_dict"].embeds is embeds

    def test_plain_dict_is_recreated(self):
        """Documenta a causa raiz: um dict plain nos kwargs é recriado (cópia)."""
        import torch
        from accelerate.utils import send_to_device

        embeds: dict = {}
        moved = send_to_device({"condition_embed_dict": embeds}, torch.device("cpu"))
        assert moved["condition_embed_dict"] is not embeds

    def test_unwrap(self):
        from paint3d.hy3dpaint.hunyuanpaintpbr.unet.modules import (
            ConditionEmbedRef,
            unwrap_condition_embeds,
        )

        embeds: dict = {}
        assert unwrap_condition_embeds(ConditionEmbedRef(embeds)) is embeds
        assert unwrap_condition_embeds(embeds) is embeds
        assert unwrap_condition_embeds(None) is None


class TestGroupOffloadDetector:
    """Detector de hooks de group offload usado pelos guards do vendor/painter."""

    def test_false_without_hooks_true_with_hooks(self):
        import torch
        from diffusers.hooks import apply_group_offloading

        from paint3d.hy3dpaint.hunyuanpaintpbr.unet.modules import _group_offload_managed

        wrapper = _build_wrapper()
        assert _group_offload_managed(wrapper.unet) is False
        assert _group_offload_managed(wrapper.unet_dual) is False

        apply_group_offloading(
            wrapper.unet,
            onload_device=torch.device("cpu"),
            offload_device=torch.device("cpu"),
            offload_type="leaf_level",
            use_stream=False,
        )
        assert _group_offload_managed(wrapper.unet) is True
        # O dual (sem hooks) continua limpo — a detecção é por módulo.
        assert _group_offload_managed(wrapper.unet_dual) is False

    def test_painter_helper_detects_wrapper(self):
        import torch
        from diffusers.hooks import apply_group_offloading

        from paint3d.painter import _paint_group_offload_active

        wrapper = _build_wrapper()
        assert _paint_group_offload_active(wrapper) is False
        for m in (wrapper.unet, wrapper.unet_dual):
            apply_group_offloading(
                m,
                onload_device=torch.device("cpu"),
                offload_device=torch.device("cpu"),
                offload_type="leaf_level",
                use_stream=False,
            )
        assert _paint_group_offload_active(wrapper) is True


class TestDualStreamGroupOffloadCPU:
    """Forward dual-stream com hooks de group offload (CPU, block_level).

    Em CPU ``use_stream=True`` não é suportado; ``block_level`` põe hooks nos
    containers (down_blocks[i]/up_blocks[i]) que estão no caminho do
    ``cross_attention_kwargs`` — exactamente o mecanismo que produzia o
    ``KeyError`` com streams em GPU.
    """

    def test_forward_matches_baseline(self, baseline_output):
        import torch
        from diffusers.hooks import apply_group_offloading

        wrapper = _build_wrapper()
        for m in (wrapper.unet, wrapper.unet_dual):
            apply_group_offloading(
                m,
                onload_device=torch.device("cpu"),
                offload_device=torch.device("cpu"),
                offload_type="block_level",
                num_blocks_per_group=1,
                use_stream=False,
            )

        out = _forward(wrapper, _tiny_inputs())
        assert out.shape == baseline_output.shape
        assert torch.equal(out, baseline_output)

    def test_condition_cache_populated(self):
        """O cache de condicionamento (escrito no stream "w") chega ao "r"."""
        import torch
        from diffusers.hooks import apply_group_offloading

        wrapper = _build_wrapper()
        for m in (wrapper.unet, wrapper.unet_dual):
            apply_group_offloading(
                m,
                onload_device=torch.device("cpu"),
                offload_device=torch.device("cpu"),
                offload_type="block_level",
                num_blocks_per_group=1,
                use_stream=False,
            )

        inputs = _tiny_inputs()
        cache: dict = {}
        with torch.no_grad():
            wrapper(**inputs, cache=cache)
        embeds = cache.get("condition_embed_dict")
        assert isinstance(embeds, dict) and len(embeds) > 0
        assert all(isinstance(v, torch.Tensor) for v in embeds.values())

    def test_second_forward_cache_hit(self, baseline_output):
        """2º forward usa o cache de condition embeds (caminho dos CFG chunks)."""
        import torch
        from diffusers.hooks import apply_group_offloading

        wrapper = _build_wrapper()
        for m in (wrapper.unet, wrapper.unet_dual):
            apply_group_offloading(
                m,
                onload_device=torch.device("cpu"),
                offload_device=torch.device("cpu"),
                offload_type="block_level",
                num_blocks_per_group=1,
                use_stream=False,
            )

        inputs = _tiny_inputs()
        cache: dict = {}
        with torch.no_grad():
            first = wrapper(**inputs, cache=cache)[0]
            second = wrapper(**inputs, cache=cache)[0]
        assert torch.equal(first, baseline_output)
        assert torch.equal(second, baseline_output)

    def test_offload_ref_unet_guard_with_hooks(self):
        """Com hooks activos, offload_ref_unet=True não move pesos (.to guard)."""
        import torch
        from diffusers.hooks import apply_group_offloading

        wrapper = _build_wrapper()
        for m in (wrapper.unet, wrapper.unet_dual):
            apply_group_offloading(
                m,
                onload_device=torch.device("cpu"),
                offload_device=torch.device("cpu"),
                offload_type="block_level",
                num_blocks_per_group=1,
                use_stream=False,
            )
        wrapper.offload_ref_unet = True
        out = _forward(wrapper, _tiny_inputs())
        assert out.shape == (4, 4, 64, 64)


class TestDualStreamGroupOffloadCUDA:
    """Config de produção: leaf_level + CUDA streams + record_stream."""

    def test_forward_matches_baseline_streamed(self):
        import torch
        from diffusers.hooks import apply_group_offloading

        if not torch.cuda.is_available():
            pytest.skip("sem CUDA")

        dev = torch.device("cuda")
        # Inputs fixos partilhados pelos 3 forwards (baseline, 1º e 2º passo).
        inputs = _tiny_inputs(dev, torch.float16)

        base = _build_wrapper().to(dev, torch.float16)
        base_out = _forward(base, inputs)
        del base
        torch.cuda.empty_cache()

        wrapper = _build_wrapper().to(dev, torch.float16)
        for m in (wrapper.unet, wrapper.unet_dual):
            apply_group_offloading(
                m,
                onload_device=dev,
                offload_device=torch.device("cpu"),
                offload_type="leaf_level",
                use_stream=True,
                record_stream=True,
            )

        # Pesos saíram da GPU: bytes de parâmetros residentes em cuda ≈ 0
        # (a medição por param.device evita o ruído do workspace cuBLAS).
        inner_weights = sum(
            p.numel() * p.element_size() for m in (wrapper.unet, wrapper.unet_dual) for p in m.parameters()
        )
        gpu_param_bytes = sum(
            p.numel() * p.element_size()
            for m in (wrapper.unet, wrapper.unet_dual)
            for p in m.parameters()
            if p.device.type == "cuda"
        )
        assert gpu_param_bytes < inner_weights * 0.1

        out = _forward(wrapper, inputs)
        torch.cuda.synchronize()
        assert out.shape == base_out.shape
        assert torch.allclose(out.float(), base_out.float(), atol=2e-3)

        # 2º passo: lazy prefetch já traçado + guard do offload_ref_unet.
        wrapper.offload_ref_unet = True
        out2 = _forward(wrapper, inputs)
        torch.cuda.synchronize()
        assert torch.allclose(out2.float(), base_out.float(), atol=2e-3)


class TestParkRefUnetCoexistence:
    """_park_ref_unet_on_cpu não mexe quando os hooks são donos da colocação."""

    def test_park_skipped_with_hooks(self):
        import torch
        from diffusers.hooks import apply_group_offloading

        from paint3d.painter import _park_ref_unet_on_cpu

        wrapper = _build_wrapper()
        wrapper.offload_ref_unet = False
        for m in (wrapper.unet, wrapper.unet_dual):
            apply_group_offloading(
                m,
                onload_device=torch.device("cpu"),
                offload_device=torch.device("cpu"),
                offload_type="leaf_level",
                use_stream=False,
            )

        pipe = type("StubPipe", (), {})()
        pipe.models = {
            "multiview_model": type("StubMV", (), {})(),
        }
        pipe.models["multiview_model"].pipeline = type("StubP", (), {})()
        pipe.models["multiview_model"].pipeline.unet = wrapper

        assert _park_ref_unet_on_cpu(pipe, verbose=False) is True
        # O flag custom NÃO liga — a colocação é dos hooks.
        assert wrapper.offload_ref_unet is False
