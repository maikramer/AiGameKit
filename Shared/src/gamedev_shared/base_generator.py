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

    def _try_multi_gpu(self, pipe: Any) -> bool:
        """Tenta colocar o pipeline split entre 2 GPUs (transformer+vae / encoders).

        .. deprecated::
            Substituído por :func:`~gamedev_shared.lowvram.apply_multi_gpu` (accelerate
            dispatch), chamado automaticamente por :meth:`_place_with_planner` quando o
            planner recomenda multi-GPU. Mantido para compatibilidade (Text2Icon ainda
            chama diretamente). Novo código deve usar ``_place_with_planner``.

        Retorna ``True`` se conseguiu, ``False`` se falhou (OOM ou <2 GPUs).
        Lida com pipelines que têm ``text_encoder_2`` opcional (FLUX).
        """
        torch = _torch()
        if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
            return False

        gpu_ids = self.gpu_ids
        if not gpu_ids or len(gpu_ids) < 2:
            gpu_ids = list(range(torch.cuda.device_count()))

        primary, secondary = gpu_ids[0], gpu_ids[1]

        try:
            pipe.transformer.to(f"cuda:{primary}")
            pipe.vae.to(f"cuda:{primary}")
            pipe.text_encoder.to(f"cuda:{secondary}")
            # FLUX tem text_encoder_2; Sana/Klein não. hasattr evita AttributeError.
            if hasattr(pipe, "text_encoder_2") and pipe.text_encoder_2 is not None:
                pipe.text_encoder_2.to(f"cuda:{secondary}")
        except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
            self._log(f"Multi-GPU placement falhou ({exc})")
            return False

        self._patch_cross_device(pipe, primary, secondary)

        for gid in gpu_ids:
            alloc = torch.cuda.memory_allocated(gid) / (1024**3)
            self._log(f"  cuda:{gid} — {alloc:.2f} GB alocados")

        self._multi_gpu = True
        return True

    def _patch_cross_device(self, pipe: Any, primary: int, secondary: int) -> None:
        """Patches o pipeline para que encode_prompt corra na GPU secundária.

        Necessário porque o diffusers não suporta nativamente pipelines split
        entre GPUs para o encode_prompt. O patch redireciona a chamada para a
        GPU secundária e traz o resultado de volta para a primária.
        """
        torch = _torch()
        primary_dev = f"cuda:{primary}"
        secondary_dev = f"cuda:{secondary}"

        # Attr unificado (antes cada tool tinha o seu: _text2d_, _texture2d_, etc.)
        pipe._primary_device = torch.device(primary_dev)

        if not hasattr(pipe, "_orig_encode_prompt"):
            pipe._orig_encode_prompt = pipe.encode_prompt

        def _patched_encode_prompt(*args: Any, **kwargs: Any) -> Any:
            kwargs["device"] = secondary_dev
            result = pipe._orig_encode_prompt(*args, **kwargs)
            if isinstance(result, torch.Tensor):
                return result.to(primary_dev)
            if isinstance(result, (tuple, list)):
                return type(result)(r.to(primary_dev) if isinstance(r, torch.Tensor) else r for r in result)
            return result

        pipe.encode_prompt = _patched_encode_prompt

        @property  # type: ignore[misc]
        def _patched_execution_device(self: Any) -> Any:
            return self._primary_device

        pipe.__class__._execution_device = _patched_execution_device

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
            on_status=self._status,
        )

        if plan.offload == "none" and plan.multi_gpu_ids is None:
            self._report_vram()
        elif plan.multi_gpu_ids is not None:
            self._multi_gpu = True

        self._log(f"offload: {plan.summary()}")
        return plan

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
