"""Group Offloading com CUDA Streams — técnica avançada de baixa VRAM.

Originalmente desenvolvida para Texture2D (FLUX.1-dev em GPUs de 6 GiB), esta
técnica oferece a **mesma pegada de VRAM** que ``enable_sequential_cpu_offload``
mas com **2-4x mais performance** ao sobrepor as transferências CPU↔GPU com o
compute via CUDA streams.

Funciona aplicando ``enable_group_offload`` (diffusers ModelMixin nativo) ou
``apply_group_offloading`` (diffusers.hooks) a cada módulo pesado do pipeline,
com ``offload_type="leaf_level"`` (granularidade máxima) e ``use_stream=True``
(sobreposição de transferências).

A função :func:`plan_group_offload` é uma **fórmula pura** (sem torch) que, dado
o orçamento de VRAM e a pegada do modelo, escolhe automaticamente o melhor setup
de group offload (``leaf_level`` vs ``block_level``, ``use_stream``, etc.). É o
auto-tuner VRAM-aware — testável em CI sem GPU.

Quando centralizar:
  - ``plan_group_offload(usable_vram_gib, footprint, quant_mode)`` — fórmula pura
  - ``try_group_offloading(pipe, config=...)`` — standalone, qualquer pipeline diffusers
  - ``DiffusionGeneratorBase._try_group_offload(pipe)`` — método da base class

Env vars:
  - ``AIGAMEKIT_GROUP_OFFLOAD`` (default "1") — kill-switch global
  - ``<TOOL>_GROUP_OFFLOAD`` (ex: ``TEXT2D_GROUP_OFFLOAD``) — override por tool
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .logging import Logger

if TYPE_CHECKING:
    from .lowvram import ModelFootprint

_logger = Logger()

# Fator de headroom para decidir leaf_level vs block_level. Se o espaço para pesos
# onloaded (usable_vram - activation) for < largest_module * fator, consideramos
# demasiado apertado para blocks inteiros → leaf_level (granularidade máxima).
GROUP_TIGHT_HEADROOM_FACTOR = 1.2


@dataclass(frozen=True)
class GroupOffloadConfig:
    """Setup de group offload resolvido pela fórmula :func:`plan_group_offload`.

    Args:
        offload_type: Granularidade — ``"leaf_level"`` (máxima, cada leaf módulo
            é seu próprio grupo) ou ``"block_level"`` (groups de transformer blocks).
        use_stream: Sobrepor transferências CPU↔GPU com compute via CUDA stream.
            É a otimização-chave: sem streams a performance ~= sequential_cpu_offload.
            Nota do diffusers: com ``use_stream=True``, ``num_blocks_per_group`` é
            forçado a 1 internamente (um block onloaded enquanto o anterior computa).
        num_blocks_per_group: Blocks por grupo (só para ``block_level``). Default
            ``1`` quando há stream (o diffusers exige).
        record_stream: Marca tensores como usados pela stream (mais rápido, ligeiramente
            mais VRAM). Só tem efeito com ``use_stream=True``.
        non_blocking: Transferências non-blocking.
    """

    offload_type: str = "leaf_level"
    use_stream: bool = True
    num_blocks_per_group: int | None = None
    record_stream: bool = False
    non_blocking: bool = False

    def summary(self) -> str:
        parts = [f"group-offload({self.offload_type}"]
        if self.use_stream:
            parts.append("stream")
        if self.record_stream:
            parts.append("record")
        return " | ".join(parts) + ")"


def plan_group_offload(
    usable_vram_gib: float,
    footprint: ModelFootprint,
    quant_mode: str,
    *,
    prefer_leaf: bool = False,
    force: bool = False,
) -> GroupOffloadConfig | None:
    """**A fórmula** — auto-tuner VRAM-aware para group offload com streams.

    Dado o orçamento de VRAM utilizável e a pegada do modelo (já considerando
    quantização), decide o melhor setup de group offload:

    - ``None``: o modelo cabe todo na GPU (pesos + ativação) → sem offload, mais rápido.
      Com ``force=True``, devolve config mesmo assim (reservar VRAM para ativação
      grande — ex. octree alto no Text3D).
    - ``leaf_level`` + stream: grupos mínimos (cada leaf). Máxima VRAM livre para
      ativação; mais sync points. Preferido em qualidade alta / ``prefer_leaf``.
    - ``block_level`` + stream: um transformer block onloaded. Menos sync, um pouco
      mais VRAM de pesos.

    Pura (sem torch/GPU) — testável em CI.

    Args:
        usable_vram_gib: VRAM utilizável em GiB (já aplicada a fração de segurança).
        footprint: Pegada do modelo (:class:`~aigamekit_shared.lowvram.ModelFootprint`).
        quant_mode: Modo de quantização em uso (para ajustar a pegada).
        prefer_leaf: Forçar ``leaf_level`` (grupos menores) mesmo com folga.
        force: Devolver config mesmo quando pesos+ativação cabem (qualidade >
            velocidade: stream pesos, VRAM para ativação).

    Returns:
        :class:`GroupOffloadConfig` ou ``None`` se não for preciso offload.
    """
    weights = footprint.weights_gib(quant_mode)
    activation = footprint.activation_gib

    # Passo 0: cabe todo na GPU? → sem offload (mais rápido), salvo force.
    if not force and weights + activation <= usable_vram_gib:
        return None

    # Não cabe (ou force). Espaço para pesos onloaded após reservar ativação.
    largest = footprint.largest_gib(quant_mode)
    headroom = max(0.0, usable_vram_gib - activation)

    if prefer_leaf or headroom < largest * GROUP_TIGHT_HEADROOM_FACTOR:
        # Grupos mínimos: leaf_level + streams — máxima VRAM para ativação/octree.
        return GroupOffloadConfig(
            offload_type="leaf_level",
            use_stream=True,
            record_stream=True,
        )
    # Folga para blocks inteiros: menos sync points, ainda com overlap de stream.
    # (diffusers força num_blocks_per_group=1 quando use_stream=True.)
    return GroupOffloadConfig(
        offload_type="block_level",
        use_stream=True,
        num_blocks_per_group=1,
        record_stream=True,
    )


def is_group_offload_enabled(*, tool_env_var: str | None = None) -> bool:
    """Verifica se o group offload está habilitado (env vars).

    Args:
        tool_env_var: Env var específica da tool (ex: ``TEXTURE2D_GROUP_OFFLOAD``).
            Se definida, tem precedência sobre o global. Se ``None``, só o global conta.
    """
    # Tool-specific env var tem precedência se definida.
    if tool_env_var:
        v = os.environ.get(tool_env_var, "").strip().lower()
        if v in ("0", "false", "no", "off"):
            return False
        if v in ("1", "true", "yes", "on"):
            return True
    # Default: global env var (default "1" = habilitado).
    return os.environ.get("AIGAMEKIT_GROUP_OFFLOAD", "1") != "0"


def try_group_offloading(
    pipe: Any,
    *,
    config: GroupOffloadConfig | None = None,
    onload_device: Any | None = None,
    offload_device: Any | None = None,
    offload_type: str = "leaf_level",
    use_stream: bool = True,
    num_blocks_per_group: int | None = None,
    record_stream: bool = False,
    non_blocking: bool = False,
    modules: tuple[str, ...] | None = None,
    log: bool = True,
    log_fn: Any = None,
) -> bool:
    """Aplica group offloading com CUDA streams a um pipeline diffusers.

    Percorre os módulos pesados do pipeline (transformer, text_encoder,
    text_encoder_2) e aplica group offloading. ModelMixin (transformer) usa
    o método nativo ``enable_group_offload``; text encoders (transformers) usam a
    função utilitária ``apply_group_offloading`` do ``diffusers.hooks``.

    **VAE é excluído por defeito**: o group offload em autoencoders conflitua com
    VAE tiling/slicing (o diffusers avisa: "may not work as expected if the first
    forward pass is executed with tiling enabled") e causa erros de device mismatch
    no decode. O VAE é gerido pelo path model_cpu/sequential do planner.

    Args:
        pipe: Objeto pipeline diffusers (FluxPipeline, SanaPipeline, etc.).
        config: :class:`GroupOffloadConfig` resolvido por :func:`plan_group_offload`.
            Se fornecido, tem precedência sobre os parâmetros individuais abaixo.
        onload_device: Device GPU para onload (default: ``cuda``).
        offload_device: Device CPU para offload (default: ``cpu``).
        offload_type: Granularidade do offload (default ``leaf_level`` — máxima).
            Alternativas: ``block_level`` (menos granular, menos overhead).
        use_stream: Sobrepor transferências com compute via CUDA streams (default ``True``).
            Esta é a otimização-chave: sem streams, a performance ~= sequential_cpu_offload.
        num_blocks_per_group: Blocks por grupo (``block_level``). Ignorado se 0/None
            salvo em ``block_level``.
        record_stream: Marca tensores como usados pela stream (mais rápido, +VRAM).
        non_blocking: Transferências non-blocking.
        modules: Tupla de nomes de atributos a aplicar. Default: transformer,
            text_encoder, text_encoder_2 (**sem vae** — conflitua com tiling/decode).
            Útil para pipelines custom (Paint3D, Hunyuan3D com "model"/"conditioner").
        log: Se ``True``, loga mensagens de sucesso/falha.
        log_fn: Função de logging (default: ``Logger().info`` / ``Logger().warn``).

    Returns:
        ``True`` se o group offloading foi aplicado com sucesso a pelo menos 1 módulo;
        ``False`` se o diffusers não suportar ou a aplicação falhar (caller deve fallback).
    """
    # config (da fórmula) tem precedência sobre os defaults.
    if config is not None:
        offload_type = config.offload_type
        use_stream = config.use_stream
        num_blocks_per_group = config.num_blocks_per_group
        record_stream = config.record_stream
        non_blocking = config.non_blocking

    def _log(msg: str) -> None:
        if log:
            (log_fn or _logger.info)(msg)

    # Default: transformer + text encoders. VAE excluído (conflitua com tiling/decode).
    target_modules = modules or ("transformer", "text_encoder", "text_encoder_2")

    # diffusers.hooks é a API canónica; ModelMixin.enable_group_offload delega nela.
    # Importado antes do torch: assim o check de "diffusers indisponível" funciona
    # mesmo em ambientes sem torch (ex: CI a testar só o planner puro).
    try:
        from diffusers.hooks import apply_group_offloading
    except ImportError as e:
        _log(f"Group offload indisponível (diffusershooks: {e})")
        return False

    import torch

    if onload_device is None:
        onload_device = torch.device("cuda")
    if offload_device is None:
        offload_device = torch.device("cpu")

    applied_any = False
    try:
        for name in target_modules:
            mod = getattr(pipe, name, None)
            if mod is None:
                continue

            # ModelMixin nativo (transformer, vae, AutoencoderKL, UNet2D, etc.).
            if hasattr(mod, "enable_group_offload"):
                mod.enable_group_offload(
                    onload_device=onload_device,
                    offload_device=offload_device,
                    offload_type=offload_type,
                    use_stream=use_stream,
                    num_blocks_per_group=num_blocks_per_group,
                    record_stream=record_stream,
                    non_blocking=non_blocking,
                )
            else:
                # nn.Module puro (text encoders transformers, wrappers custom).
                apply_group_offloading(
                    mod,
                    onload_device=onload_device,
                    offload_device=offload_device,
                    offload_type=offload_type,
                    use_stream=use_stream,
                    num_blocks_per_group=num_blocks_per_group,
                    record_stream=record_stream,
                    non_blocking=non_blocking,
                )
            applied_any = True
            _log(f"Group offload aplicado a {name} ({offload_type}, streams={'on' if use_stream else 'off'})")

    except Exception as e:
        _log(f"Group offload falhou ({e})")
        return False

    return applied_any


def try_layerwise_casting(
    pipe: Any,
    *,
    storage_dtype_name: str = "float8_e4m3fn",
    compute_dtype_name: str = "bfloat16",
    modules: tuple[str, ...] = ("transformer", "text_encoder", "text_encoder_2"),
    non_blocking: bool = True,
    log: bool = True,
    log_fn: Any = None,
) -> bool:
    """Aplica layerwise casting (fp8 storage, bf16 compute) aos módulos pesados.

    Usa ``diffusers.hooks.apply_layerwise_casting``: armazena pesos em fp8
    (~50% de redução de VRAM de pesos) e faz cast para bf16 apenas durante o
    forward. Skipa norm/embed/proj por defeito (camadas sensíveis à precisão).

    Args:
        pipe: pipeline diffusers.
        storage_dtype_name: dtype de armazenamento (default ``"float8_e4m3fn"``).
            Alternativas: ``"float8_e5m2"``. Deve ser uma string (resolvida para
            ``torch.dtype`` lazy).
        compute_dtype_name: dtype de compute (default ``"bfloat16"``).
        modules: nomes dos attrs a aplicar (transformer, text encoders).
        non_blocking: cast non-blocking (sobrepor com compute).
        log: se True, loga mensagens.
        log_fn: função de logging.

    Returns:
        True se aplicado a pelo menos 1 módulo; False se diffusers não suportar.
    """
    import torch

    def _log(msg: str) -> None:
        if log:
            (log_fn or _logger.info)(msg)

    try:
        from diffusers.hooks import apply_layerwise_casting
    except ImportError as e:
        _log(f"Layerwise casting indisponível (diffusers hooks: {e})")
        return False

    storage_dtype = getattr(torch, storage_dtype_name, None)
    compute_dtype = getattr(torch, compute_dtype_name, None)
    if storage_dtype is None or compute_dtype is None:
        _log(f"Layerwise casting: dtype inválido ({storage_dtype_name}/{compute_dtype_name})")
        return False

    applied_any = False
    try:
        for name in modules:
            mod = getattr(pipe, name, None)
            if mod is None:
                continue
            apply_layerwise_casting(
                mod,
                storage_dtype=storage_dtype,
                compute_dtype=compute_dtype,
                non_blocking=non_blocking,
            )
            applied_any = True
            _log(f"Layerwise casting aplicado a {name} ({storage_dtype_name} storage, {compute_dtype_name} compute)")
    except Exception as e:
        _log(f"Layerwise casting falhou ({e})")
        return False

    return applied_any
