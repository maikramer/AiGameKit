"""Base class para generators de difusão — extrai a infraestrutura comum.

Os 4 generators 2D do monorepo (Text2D, Text2Icon, Texture2D, Skymap2D) partilham
~60-65% de código idêntico: logging, cache clear, warmup/unload lifecycle, device
resolution, multi-GPU placement, VRAM reporting, batch generation, e save_image.

Esta base class detém toda essa infraestrutura. Cada generator concreto herda e
faz override apenas de ``_load_pipeline`` (como o pipeline é construído) e
``generate`` (parâmetros e pós-processamento específicos).

Lazy import: a base não importa torch/diffusers no topo — as tools continuam a
fazer lazy import dentro de ``_load_pipeline``. Isto mantém o módulo importável
sem deps GPU instaladas (útil para o UMS e testes).
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .logging import Logger

_logger = Logger()


def _torch():
    """Lazy import do torch (evita dependência GPU ao importar o módulo)."""
    import torch

    return torch


def torch_dtype_for(device: str | None) -> Any:
    """Resolve o dtype torch para um device.

    CUDA → bfloat16 (formato nativo dos modelos FLUX/Sana); CPU → float32.
    Case-insensitive (aceita ``"CPU"``, ``"cuda:0"``, etc.).
    """
    torch = _torch()
    d = (device or "").lower()
    if d.startswith("cpu"):
        return torch.float32
    if d.startswith("cuda") and torch.cuda.is_available():
        if getattr(torch.cuda, "is_bf16_supported", lambda: False)():
            return torch.bfloat16
        return torch.float16
    return torch.float32


class DiffusionGeneratorBase(ABC):
    """Base class para generators de difusão com infraestrutura partilhada.

    Cada generator concreto herda esta class e implementa:
      - ``_load_pipeline()``: constrói e devolve o objeto pipeline (diffusers, etc.)
      - ``generate(prompt, ...)``: executa uma geração

    A base fornece: lifecycle (warmup/unload), logging, cache clear, device
    resolution, multi-GPU placement, VRAM reporting, batch generation, save_image.
    """

    def __init__(
        self,
        *,
        device: str | None = None,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        memory_efficient: bool = False,
        group_offload: bool = False,
        torch_compile: bool | None = None,
        torch_compile_mode: str = "default",
        step_cache: str | None = None,
        channels_last: bool = False,
    ) -> None:
        torch = _torch()

        # Device resolution: default cuda se disponível.
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        elif device.startswith("cuda") and not torch.cuda.is_available():
            device = "cpu"

        self.device = device
        self.verbose = verbose
        self.model_id = model_id
        self.cache_dir = cache_dir
        self.gpu_ids = gpu_ids
        self.memory_efficient = memory_efficient
        self.group_offload = group_offload
        # None = respeitar env GAMEDEV_TORCH_COMPILE; True/False = override CLI.
        self.torch_compile = torch_compile
        self.torch_compile_mode = torch_compile_mode
        # None = respeitar env GAMEDEV_STEP_CACHE; str = "off"|"auto"|"first_block"|"taylorseer".
        self.step_cache = step_cache
        self.channels_last = channels_last
        self.torch_dtype = torch_dtype_for(device)

        self._pipe: Any = None
        self._on_status: Callable[[str], None] | None = None
        self._multi_gpu = False

    # ------------------------------------------------------------------
    # Logging e status
    # ------------------------------------------------------------------

    def set_status_callback(self, fn: Callable[[str], None] | None) -> None:
        """Callback opcional (ex. Rich) para mensagens de fase durante o load."""
        self._on_status = fn

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(msg)

    def _status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)
        else:
            _logger.step(msg)

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _clear_cache(self) -> None:
        """gc.collect() + torch.cuda.empty_cache(). Chamado antes/após inferência."""
        import gc

        gc.collect()
        torch = _torch()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def warmup(self) -> None:
        """Carrega o pipeline (download HF + pesos). Idempotente."""
        self._load_pipeline()

    @abstractmethod
    def _load_pipeline(self) -> Any:
        """Constrói e devolve o pipeline. Deve ser idempotente (retornar se já carregado).

        Implementações concretas devem:
          1. Verificar ``if self._pipe is not None: return self._pipe``
          2. Construir o pipeline (from_pretrained, quantização, LoRA, etc.)
          3. Aplicar colocação de device (``self._place_pipeline(pipe)`` ou custom)
          4. Guardar em ``self._pipe`` e retornar
        """

    def unload(self) -> None:
        """Descarrega o pipeline e liberta VRAM. Idempotente."""
        if self._pipe is None:
            return
        del self._pipe
        self._pipe = None
        self._clear_cache()

    # ------------------------------------------------------------------
    # Device / generator helpers
    # ------------------------------------------------------------------

    def _build_generator(self, seed: int | None) -> Any:
        """Constrói um torch.Generator no device correto com a seed dada."""
        torch = _torch()
        if self._multi_gpu and self.gpu_ids:
            gen_device = f"cuda:{self.gpu_ids[0]}"
        elif self.device != "cpu":
            gen_device = "cuda"
        else:
            gen_device = "cpu"
        generator = torch.Generator(device=gen_device)
        if seed is not None:
            generator.manual_seed(seed)
        return generator

    def _resolve_seed(self, seed: int | None) -> int:
        """Resolve seed None/<0 para uma seed aleatória via seed_utils."""
        from .seed_utils import generate_seed

        if seed is None or seed < 0:
            return generate_seed()
        return seed

    # ------------------------------------------------------------------
    # VRAM reporting
    # ------------------------------------------------------------------

    def _reset_peak_mem_stats(self) -> None:
        """Reset dos contadores de pico de VRAM em todas as GPUs ativas."""
        torch = _torch()
        if not torch.cuda.is_available() or self.device == "cpu":
            return
        for gid in self.gpu_ids or range(torch.cuda.device_count()):
            if gid < torch.cuda.device_count():
                torch.cuda.reset_peak_memory_stats(gid)

    def _report_vram(self) -> None:
        """Reporta VRAM alocada após carga do pipeline (log detalhado)."""
        torch = _torch()
        if not torch.cuda.is_available() or self.device == "cpu":
            return
        with contextlib.suppress(Exception):
            torch.cuda.synchronize()
        for gid in self.gpu_ids or range(torch.cuda.device_count()):
            if gid >= torch.cuda.device_count():
                continue
            alloc = torch.cuda.memory_allocated(gid) / (1024**3)
            peak = torch.cuda.max_memory_allocated(gid) / (1024**3)
            self._log(f"  cuda:{gid} — {alloc:.2f} GB alocados (pico {peak:.2f} GB)")

    # ------------------------------------------------------------------
    # Multi-GPU placement
    # ------------------------------------------------------------------
    # NOTA: O split multi-GPU manual (_try_multi_gpu + _patch_cross_device) foi
    # removido. Agora é tratado pelo planner unificado via accelerate
    # (apply_multi_gpu em lowvram.py), chamado automaticamente por
    # _place_with_planner quando o plano recomenda multi-GPU.

    # ------------------------------------------------------------------
    # Group Offloading com CUDA Streams
    # ------------------------------------------------------------------

    def _try_group_offload(self, pipe: Any, *, config: Any | None = None) -> bool:
        """Aplica group offloading (leaf_level + CUDA streams) ao pipeline.

        Mesma pegada de VRAM que ``enable_sequential_cpu_offload`` mas com as
        transferências CPU↔GPU sobrepostas ao compute via streams — tipicamente
        2-4x mais rápido em GPUs pequenas. Retorna ``False`` se falhar (caller
        deve fazer fallback para sequential/model_cpu offload).

        Gatilho: ``self.group_offload=True`` + device CUDA + env var
        ``GAMEDEV_GROUP_OFFLOAD`` (default habilitado).

        Args:
            pipe: pipeline diffusers.
            config: :class:`~gamedev_shared.group_offload.GroupOffloadConfig`
                (de ``plan_group_offload``). Se ``None``, usa ``leaf_level``+stream.
        """
        from .group_offload import is_group_offload_enabled, try_group_offloading

        if self.device == "cpu" or not self.group_offload:
            return False
        if not is_group_offload_enabled():
            return False

        return try_group_offloading(pipe, config=config, log=True, log_fn=self._log)

    def _place_with_planner(
        self,
        pipe: Any,
        footprint: Any,
        *,
        quant_mode: str = "none",
        allow_quant: tuple[str, ...] | None = None,
        model_attr: str | None = None,
        no_split_classes: list[str] | None = None,
        offload_modules: tuple[str, ...] | None = None,
        allow_group_offload: bool = True,
        target_resolution: int | None = None,
    ) -> Any:
        """**Entry point unificado** para colocar o pipeline na GPU.

        Resolve specs CUDA (com **VRAM livre**), delega a
        :func:`~gamedev_shared.lowvram.place_pipeline` — que decide multi-GPU vs
        offload vs full-GPU e **aplica** (multi-GPU via accelerate, offload via
        diffusers hooks, com cascade fallback).

        Subclasses 2D (Text2D, Skymap2D, Text2Icon, Texture2D) chamam isto em vez
        de duplicar a lógica de offload. Cada uma fornece só o ``footprint``.

        Args:
            pipe: pipeline diffusers.
            footprint: :class:`~gamedev_shared.lowvram.ModelFootprint`. Se tem
                ``architecture``, resolve ``no_split_classes`` do registry.
            quant_mode: quantização em uso (para ajustar a pegada no planner).
            allow_quant: subconjunto de modos permitidos. ``("none",)`` quando o
                checkpoint já vem pré-quantizado do hub (ex: Skymap2D uint4).
            model_attr: attr do ``nn.Module`` pesado para multi-GPU accelerate
                (ex: ``"transformer"`` no FLUX). Default ``None`` = dispatch do pipe.
            no_split_classes: override das classes no-split para multi-GPU.

        Returns:
            :class:`~gamedev_shared.lowvram.OffloadPlan` resolvido e aplicado.
        """
        from .hardware import cuda_gpu_free_specs
        from .lowvram import place_pipeline

        if self.device == "cpu":
            from .lowvram import _cpu_plan

            plan = _cpu_plan(("device CPU",))
            if hasattr(pipe, "to"):
                pipe.to("cpu")
            return plan

        # VRAM livre (3-tuple) — respeita GPUs ocupadas por outros processos.
        specs = cuda_gpu_free_specs()
        if self.gpu_ids:
            keep = set(self.gpu_ids)
            specs = [s for s in specs if s[0] in keep]
        allow_multi = self.gpu_ids is None or len(self.gpu_ids) >= 2

        plan = place_pipeline(
            pipe,
            footprint,
            specs,
            quant_mode=quant_mode,
            allow_multi_gpu=allow_multi,
            allow_quant=allow_quant,
            model_attr=model_attr,
            no_split_classes=no_split_classes,
            offload_modules=offload_modules,
            allow_group_offload=allow_group_offload,
            target_resolution=target_resolution,
            on_status=self._status,
        )

        if plan.offload == "none" and plan.multi_gpu_ids is None:
            self._report_vram()
        elif plan.multi_gpu_ids is not None:
            self._multi_gpu = True

        self._log(f"offload: {plan.summary()}")
        return plan

    def _torch_compile_enabled(self) -> bool:
        """Resolve se compile está activo: instance flag > env ``GAMEDEV_TORCH_COMPILE``."""
        import os

        if self.torch_compile is not None:
            return bool(self.torch_compile)
        return os.environ.get("GAMEDEV_TORCH_COMPILE", "0").strip().lower() in ("1", "true", "yes", "on")

    def _maybe_compile_transformer(self, pipe: Any, plan: Any) -> None:
        """Compila o transformer via torch.compile quando seguro.

        **Opt-in** via ``--compile`` / ``GAMEDEV_TORCH_COMPILE=1``: cold-start do
        compile (28-90s) só compensa em server/batch (modelo reutilizado).

        - ``mode=default``: ok com ``group_stream`` (sem CUDA graphs).
        - ``reduce-overhead`` / ``max-autotune``: só ``offload=="none"`` (cudagraphs).
        - ``model_cpu`` / ``sequential_cpu``: skip (ping-pong de device quebra graphs).
        """
        if self.device == "cpu" or not self._torch_compile_enabled():
            return
        offload = getattr(plan, "offload", "none")
        if offload in ("model_cpu", "sequential_cpu"):
            self._log(f"torch.compile skip (offload={offload} move módulos entre devices)")
            return

        # DiT/Sana → transformer; SD1.5 (Texture2D) → unet.
        attr = "transformer" if getattr(pipe, "transformer", None) is not None else "unet"
        model = getattr(pipe, attr, None)
        if model is None:
            return
        from gamedev_shared.quantization import apply_torch_compile, resolve_torch_compile_mode

        requested = self.torch_compile_mode or "default"
        mode = resolve_torch_compile_mode(
            requested,
            offload=offload,
            group_offload_active=(offload == "group_stream"),
        )
        if mode != requested:
            self._log(f"torch.compile mode={requested} → {mode} (offload={offload})")

        # Regional compile (compile_repeated_blocks) se disponível — cold start mais
        # rápido que full torch.compile. Só em full-GPU (regional + offload instável).
        if offload == "none" and hasattr(model, "compile_repeated_blocks"):
            try:
                model.compile_repeated_blocks()
                self._log(f"torch.compile (regional) aplicado ao {attr}")
                return
            except Exception as exc:
                self._log(f"compile_repeated_blocks falhou ({exc}); fallback para torch.compile")
        compiled = apply_torch_compile(
            model,
            mode=mode,
            offload=offload,
            group_offload_active=(offload == "group_stream"),
        )
        if compiled is not model:
            setattr(pipe, attr, compiled)
            self._log(f"torch.compile ({mode}) aplicado ao {attr}")

    def _maybe_apply_step_cache(self, pipe: Any, plan: Any) -> None:
        """Aplica step caching (FirstBlockCache/TaylorSeer) quando seguro.

        Só activa quando o plano é ``OFFLOAD_NONE`` (modelo cabe sem offload) —
        step caching é speed, não VRAM. CLI ``--step-cache`` > env ``GAMEDEV_STEP_CACHE``.
        """
        if plan.offload != "none" or self.device == "cpu":
            return
        from .step_cache import apply_step_cache, get_step_cache_mode

        mode = self.step_cache if self.step_cache is not None else get_step_cache_mode()
        if mode == "off":
            return
        apply_step_cache(pipe, method=mode, log_fn=self._log)

    def _maybe_apply_channels_last(self, pipe: Any, plan: Any) -> None:
        """Aplica ``channels_last`` ao VAE (e transformer se Conv) quando pedido.

        Opt-in via ``self.channels_last``. Seguro com qualquer offload — formato
        de memória, não muda device placement.
        """
        if not self.channels_last or self.device == "cpu":
            return
        from gamedev_shared.quantization import apply_channels_last

        for attr in ("vae", "transformer", "unet"):
            mod = getattr(pipe, attr, None)
            if mod is None:
                continue
            if apply_channels_last(mod, log_fn=lambda m, a=attr: self._log(f"{a}: {m}")):
                pass  # logged inside

    def _maybe_select_attention_backend(self, pipe: Any, plan: Any) -> None:
        """Selecciona attention backend optimizado (Sage/Flash) quando disponível.

        Aplica só ao transformer (NÃO ao VAE — Sage pode causar stuck decode).
        Controlado por ``GAMEDEV_ATTENTION_BACKEND`` env (default "auto").
        """
        if self.device == "cpu":
            return
        from .attention import select_attention_backend

        select_attention_backend(pipe, backend="auto", log_fn=self._log)

    # ------------------------------------------------------------------
    # Batch generation
    # ------------------------------------------------------------------

    def generate_batch(self, prompts: list[str], **kwargs: Any) -> Any:
        """Gera múltiplas imagens a partir de uma lista de prompts.

        **Generator** que yielding ``(image, metadata, idx)`` para cada prompt,
        com error handling por item (item falhado → ``(None, {"error": ...}, idx)``).
        Seed incrementa por prompt se fornecida (seed + idx).

        Contrato uniformizado para todas as tools (Texture2D, Skymap2D, Text2Icon):
        o caller itera com per-item error continuation::

            for image, metadata, idx in gen.generate_batch(prompts, **params):
                if image is None:
                    print(f"erro: {metadata['error']}")
                    continue
                save(image, metadata)

        Yields:
            Tuple ``(image_or_None, metadata_dict, idx)``.
        """
        base_seed = kwargs.pop("seed", None)
        for idx, prompt in enumerate(prompts):
            iter_kwargs = dict(kwargs)
            if base_seed is not None:
                iter_kwargs["seed"] = base_seed + idx
            try:
                image, metadata = self.generate(prompt, **iter_kwargs)
                yield image, metadata, idx
            except Exception as exc:
                yield None, {"error": str(exc), "prompt": prompt}, idx

    # ------------------------------------------------------------------
    # Image saving
    # ------------------------------------------------------------------

    @staticmethod
    def save_image(image: Any, path: Path | str, image_format: str | None = None) -> Path:
        """Guarda uma PIL.Image num path, criando o diretório pai se preciso."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format=image_format)
        return path

    # ------------------------------------------------------------------
    # Hooks opcionais (tools podem fazer override)
    # ------------------------------------------------------------------

    def _augment_prompt(self, prompt: str, **kwargs: Any) -> str:
        """Hook para augmentação de prompt (ex: trigger words, seamless, equirect).

        Default: retorna o prompt inalterado.
        """
        return prompt

    def _post_process(self, image: Any, metadata: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        """Hook para pós-processamento da imagem gerada (ex: bg removal, seamless fix).

        Default: retorna imagem + metadata inalterados.
        """
        return image, metadata
