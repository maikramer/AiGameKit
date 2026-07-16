"""
Part3D pipeline — Hunyuan3D-Part com CPU offloading sequencial para ~6 GB VRAM.

Estratégia de memória (single-GPU):
  1. Carregar P3-SAM na GPU, segmentar, mover para CPU
  2. Carregar Conditioner, codificar condições, mover para CPU
  3. Carregar DiT, denoising loop (pico VRAM ~3.5 GB em FP16), mover para CPU
  4. Carregar ShapeVAE, decode latents → mesh por parte, mover para CPU

Cada fase limpa o cache CUDA entre transições.

Multi-GPU (--gpu-ids 0,1):
  - DiT: residente na GPU primária (inteiro, ~3.3 GB FP16)
  - Conditioner + P3-SAM + ShapeVAE: residentes na GPU secundária (~1.4 GB)
  - Sem CPU offloading — componentes ficam nas respetivas GPUs
"""

from __future__ import annotations

import contextlib
import gc
import inspect
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import trimesh
from tqdm import tqdm

from gamedev_shared.logging import Logger
from gamedev_shared.profiler import profile_span

from . import defaults as _d
from .utils.autotune import (
    autotune_generate,
    autotune_segment,
    get_free_vram_gb,
    get_max_parts_for_vram,
    get_vram_gb,
    refresh_generate_limits,
    should_compile_dit,
)
from .utils.dit_quantization import load_dit_quantized, want_quantized_dit
from .utils.flash_attn_shim import install_shim as _install_flash_shim
from .utils.memory import clear_cuda_memory, format_bytes

_logger = Logger()

# Injetar shim de flash_attn ANTES de qualquer import do XPart/Sonata
_install_flash_shim()


def _vae_decode_mesh(vae: Any, decoded: torch.Tensor, *, decode_path: str, **kwargs: Any) -> Any:
    """Decode latents → mesh via FlashVDM/hierarchical (``latents2mesh``) ou fast path.

    Args:
        decode_path: ``latents2mesh`` (volume_decoder moderno) ou ``latent2mesh_2`` (legacy).
    """
    call_kw = dict(kwargs)
    # Space MCSurfaceExtractor.run exige keyword-only: mc_level, bounds, octree_resolution.
    call_kw.setdefault("bounds", 1.01)
    call_kw.setdefault("mc_level", _d.DEFAULT_MC_LEVEL)
    if decode_path == "latents2mesh":
        fn = vae.latents2mesh
        # Surface extractor uses mc_level/bounds; ignore mc_algo/mc_mode here (set at configure).
        call_kw.pop("mc_algo", None)
        call_kw.pop("mc_mode", None)
    else:
        fn = vae.latent2mesh_2
        # Space API uses mc_mode, not mc_algo.
        if "mc_mode" not in call_kw and "mc_algo" in call_kw:
            call_kw["mc_mode"] = call_kw.pop("mc_algo")
        else:
            call_kw.pop("mc_algo", None)
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(decoded, **call_kw)
    # latents2mesh is **kwargs passthrough — keep all keys for volume_decoder + extractor.
    if decode_path == "latents2mesh":
        return fn(decoded, **call_kw)
    filtered = {k: v for k, v in call_kw.items() if k in params}
    return fn(decoded, **filtered)


def _mc_level_candidates(primary: float) -> list[float]:
    """MC levels to try when FlashVDM returns empty / out-of-range grids."""
    base = float(primary)
    out: list[float] = []
    for v in (base, 0.0, -0.01, 0.01, -1.0 / 256.0, -1.0 / 128.0, 0.05, -0.05):
        if not any(abs(v - x) < 1e-9 for x in out):
            out.append(v)
    return out


def _decode_part_with_mc_retries(
    vae: Any,
    decoded: torch.Tensor,
    *,
    decode_path: str,
    octree_resolution: int,
    num_chunks: int,
    mc_level: float,
    mc_algo: str,
    vae_device: str,
) -> Any:
    """Decode one part; retry alternate ``mc_level`` when marching cubes rejects the grid."""
    last_err: Exception | None = None
    for lvl in _mc_level_candidates(mc_level):
        try:
            if decode_path == "latents2mesh":
                decode_ctx = (
                    torch.autocast("cuda", dtype=torch.bfloat16)
                    if str(vae_device).startswith("cuda")
                    else contextlib.nullcontext()
                )
                with decode_ctx:
                    part_mesh_data = _vae_decode_mesh(
                        vae,
                        decoded,
                        decode_path=decode_path,
                        octree_resolution=octree_resolution,
                        num_chunks=num_chunks,
                        mc_level=lvl,
                        mc_algo=mc_algo,
                    )
            else:
                part_mesh_data = _vae_decode_mesh(
                    vae,
                    decoded.float() if hasattr(decoded, "float") else decoded,
                    decode_path=decode_path,
                    octree_resolution=octree_resolution,
                    num_chunks=num_chunks,
                    mc_level=lvl,
                    mc_algo=mc_algo,
                )
            raw = _decode_output_to_trimesh(part_mesh_data)
            if raw is not None and len(raw.vertices) > 0:
                if abs(lvl - float(mc_level)) > 1e-9:
                    _logger.dim(f"    MC retry ok com mc_level={lvl}")
                return part_mesh_data
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "surface level" in msg or "mesh_v" in msg or "none" in msg:
                continue
            raise
    if last_err is not None:
        raise last_err
    return None


def _decode_output_to_trimesh(part_mesh_data: Any) -> trimesh.Trimesh | None:
    """Normaliza saída do VAE (Trimesh, lista, ou ``Latent2MeshOutput`` com mesh_v/mesh_f)."""
    if part_mesh_data is None:
        return None
    if isinstance(part_mesh_data, trimesh.Trimesh):
        return part_mesh_data
    if isinstance(part_mesh_data, (list, tuple)):
        for item in part_mesh_data:
            m = _decode_output_to_trimesh(item)
            if m is not None:
                return m
        return None
    mesh_v = getattr(part_mesh_data, "mesh_v", None)
    mesh_f = getattr(part_mesh_data, "mesh_f", None)
    if mesh_v is not None and mesh_f is not None:
        v = np.asarray(mesh_v)
        f = np.asarray(mesh_f)
        if v.size > 0 and f.size > 0:
            return trimesh.Trimesh(vertices=v, faces=f, process=False)
    return None


def _log_vram(prefix: str = "") -> None:
    if torch.cuda.is_available():
        alloc = torch.cuda.memory_allocated()
        reserved = torch.cuda.memory_reserved()
        _logger.dim(f"{prefix}alocado={format_bytes(alloc)} reservado={format_bytes(reserved)}")


def _to_device(module: torch.nn.Module, device: str, dtype: torch.dtype | None = None) -> None:
    """Move módulo para device (e opcionalmente muda dtype)."""
    if dtype is not None:
        module.to(device=device, dtype=dtype)
    else:
        module.to(device=device)


def _offload_to_cpu(module: torch.nn.Module) -> None:
    """Move módulo para CPU e limpa cache CUDA."""
    module.to("cpu")
    clear_cuda_memory()


class Part3DPipeline:
    """
    Decompõe uma mesh 3D em partes semânticas usando Hunyuan3D-Part.

    Pipeline: P3-SAM (segmentação) → X-Part (geração de partes).
    Optimizado para GPUs com ~6 GB VRAM via CPU offloading sequencial.

    Otimizações disponíveis:
    - Pré-quant DiT (optimum-quanto qint8) quando artefactos existem
    - Quantização runtime SDNQ (``auto``/``int8``/``int4`` → presets sdnq-*)
    - FlashVDM / Hierarchical volume decode (ShapeVAE latents2mesh)
    - torch.compile + channels_last + Sage/SDPA attention
    - Attention slicing para reduzir pico de VRAM
    """

    def __init__(
        self,
        model_path: str = _d.DEFAULT_HF_REPO,
        device: str | None = None,
        dtype: str = _d.DEFAULT_DTYPE,
        cpu_offload: bool = _d.DEFAULT_CPU_OFFLOAD,
        verbose: bool = False,
        autotune: bool = True,
        quantization_mode: str = _d.DEFAULT_QUANTIZATION_MODE,
        quantize_dit: bool = _d.DEFAULT_QUANTIZE_DIT,
        enable_torch_compile: bool = _d.DEFAULT_TORCH_COMPILE,
        torch_compile_mode: str = _d.DEFAULT_TORCH_COMPILE_MODE,
        enable_attention_slicing: bool = _d.DEFAULT_ENABLE_ATTENTION_SLICING,
        memory_efficient: bool = _d.DEFAULT_MEMORY_EFFICIENT,
        gpu_ids: list[int] | None = None,
        sdnq_preset: str | None = None,
        volume_decoder: str = _d.DEFAULT_VOLUME_DECODER,
        mc_algo: str = _d.DEFAULT_MC_ALGO,
        channels_last: bool = _d.DEFAULT_CHANNELS_LAST,
        quality: str | None = None,
        refine_labels: bool = _d.DEFAULT_REFINE_LABELS,
        bbox_merge_iou: float = _d.SPACE_BBOX_MERGE_IOU,
        mask_nms_iou: float = _d.SPACE_MASK_NMS_IOU,
        secondary_mask_iou: float = _d.SPACE_SECONDARY_MASK_IOU,
        min_cluster_support: int = _d.SPACE_MIN_CLUSTER_SUPPORT,
        min_predicted_iou: float = _d.SPACE_MIN_PREDICTED_IOU,
        prompt_batch_size: int = _d.SPACE_PROMPT_BATCH_SIZE,
        detail_levels: int = _d.DEFAULT_DETAIL_LEVELS,
        multi_head: bool = _d.SPACE_MULTI_HEAD,
        head_min_score: float = _d.SPACE_HEAD_MIN_SCORE,
        head_score_ratio: float = _d.SPACE_HEAD_SCORE_RATIO,
        consensus: bool = _d.SPACE_CONSENSUS,
        consensus_vote: float = _d.SPACE_CONSENSUS_VOTE,
        segment_mode: str = _d.DEFAULT_SEGMENT_MODE,
        geometry_params: Any | None = None,
        parts_mode: str = _d.DEFAULT_PARTS_MODE,
        xpart_max_area_frac: float = _d.DEFAULT_XPART_MAX_AREA_FRAC,
        aabb_margin_frac: float = _d.DEFAULT_AABB_MARGIN_FRAC,
        xpart_skip_thin_ratio: float = _d.DEFAULT_XPART_SKIP_THIN_RATIO,
        xpart_skip_aspect: float = _d.DEFAULT_XPART_SKIP_ASPECT,
        preserve_thin_topology: bool = _d.DEFAULT_PRESERVE_THIN_TOPOLOGY,
        exclusive_partition: bool = _d.DEFAULT_EXCLUSIVE_PARTITION,
        exclusive_samples_per_part: int = _d.DEFAULT_EXCLUSIVE_SAMPLES_PER_PART,
        cap_part_holes: bool = _d.DEFAULT_CAP_PART_HOLES,
    ):
        self.model_path = model_path
        self.cpu_offload = cpu_offload
        self.verbose = verbose
        self.autotune = autotune
        self.quantization_mode = quantization_mode
        self.quantize_dit = quantize_dit
        self.enable_torch_compile = enable_torch_compile
        self.torch_compile_mode = torch_compile_mode
        self.enable_attention_slicing = enable_attention_slicing
        self.memory_efficient = memory_efficient
        self.sdnq_preset = sdnq_preset
        self.volume_decoder = volume_decoder
        self.mc_algo = mc_algo
        self.channels_last = channels_last
        self.quality = quality
        self.refine_labels = refine_labels
        self.bbox_merge_iou = bbox_merge_iou
        self.mask_nms_iou = mask_nms_iou
        self.secondary_mask_iou = secondary_mask_iou
        self.min_cluster_support = min_cluster_support
        self.min_predicted_iou = min_predicted_iou
        self.prompt_batch_size = prompt_batch_size
        self.detail_levels = max(0, detail_levels)
        self.multi_head = multi_head
        self.aabb_margin_frac = float(aabb_margin_frac)
        self.xpart_skip_thin_ratio = float(xpart_skip_thin_ratio)
        self.xpart_skip_aspect = float(xpart_skip_aspect)
        self.preserve_thin_topology = bool(preserve_thin_topology)
        self.exclusive_partition = bool(exclusive_partition)
        self.exclusive_samples_per_part = max(64, int(exclusive_samples_per_part))
        self.head_min_score = head_min_score
        self.head_score_ratio = head_score_ratio
        self.consensus = consensus
        self.consensus_vote = consensus_vote
        self._torch_compile_dit_active = False
        mode = str(segment_mode or _d.DEFAULT_SEGMENT_MODE).strip().lower()
        if mode not in {"p3sam", "geometry", "hybrid"}:
            raise ValueError(f"segment_mode inválido: {segment_mode!r}")
        self.segment_mode = mode
        self.geometry_params = geometry_params
        pmode = str(parts_mode or _d.DEFAULT_PARTS_MODE).strip().lower()
        if pmode not in {"xpart", "faces", "hybrid"}:
            raise ValueError(f"parts_mode inválido: {parts_mode!r}")
        self.parts_mode = pmode
        self.xpart_max_area_frac = float(xpart_max_area_frac)
        self.xpart_large_octree = int(getattr(_d, "DEFAULT_XPART_LARGE_OCTREE", 128))
        self.cap_part_holes = bool(cap_part_holes)
        self._vae_decode_path = "latent2mesh_2"
        self._volume_decoder_resolved: str | None = None

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.dtype = getattr(torch, dtype) if isinstance(dtype, str) else dtype

        self._model: torch.nn.Module | None = None
        self._conditioner: torch.nn.Module | None = None
        self._vae: torch.nn.Module | None = None
        self._scheduler: Any = None
        self._bbox_predictor: Any = None
        self._model_dir: str | None = None
        self._dit_quantized = False
        self._gpu_ids = gpu_ids
        self._dit_multi_gpu = False
        self._secondary_device: str | None = None
        if gpu_ids is not None and len(gpu_ids) >= 2 and torch.cuda.is_available() and torch.cuda.device_count() >= 2:
            self._secondary_device = f"cuda:{gpu_ids[1]}"

        self._loaded = False

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(msg)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _ensure_model_dir(self) -> str:
        """Preflight do modelo (download com resume/progresso) via helper partilhado."""
        if self._model_dir is not None:
            return self._model_dir

        from gamedev_shared.model_download import ensure_model

        self._log(f"A descarregar modelo de {self.model_path}...")
        self._model_dir = str(ensure_model(self.model_path, on_status=self._log))
        self._log(f"Modelo em: {self._model_dir}")
        return self._model_dir

    def _load_configs(self) -> dict[str, Any]:
        """Carrega configurações JSON de cada componente."""
        d = self._ensure_model_dir()
        configs = {}
        for name in ("model", "conditioner", "shapevae", "scheduler", "p3sam"):
            cfg_path = os.path.join(d, name, "config.json")
            if not os.path.exists(cfg_path):
                cfg_path = os.path.join(d, "p3sam", "config.json") if name == "p3sam" else cfg_path
            with open(cfg_path) as f:
                configs[name] = json.load(f)
        return configs

    def load(self) -> None:
        """Carrega todos os componentes (na CPU se cpu_offload=True)."""
        if self._loaded:
            return

        with profile_span("part3d_load"):
            self._load_impl()

    def _load_impl(self) -> None:
        from easydict import EasyDict
        from safetensors.torch import load_file

        from .utils.kernel_accel import (
            apply_channels_last_modules,
            compile_modules,
            configure_vae_acceleration,
            enable_sage_attention_env,
            resolve_volume_decoder,
        )

        t0 = time.time()
        model_dir = self._ensure_model_dir()

        attn_backend = enable_sage_attention_env()
        self._log(f"Attention backend: {attn_backend}")

        self._log("A carregar configurações...")
        configs = self._load_configs()

        # Precisamos do instantiate_from_config do XPart
        # Vamos importar do código do Space (que está embebido no repo HF)
        _setup_xpart_imports(model_dir)

        from partgen.utils.misc import instantiate_from_config

        load_device = "cpu"

        # --- Model (DiT) ---
        model_config = EasyDict(configs["model"])
        self._model = instantiate_from_config(model_config)
        self._dit_quantized = False
        use_q = self.memory_efficient and want_quantized_dit(self.device, model_dir)
        if use_q:
            try:
                if load_dit_quantized(self._model, model_dir):
                    self._dit_quantized = True
                    self._log("A carregar DiT quantizado (qint8 weight-only, artefactos model-dit-qint8.*)...")
                    self._log(f"  DiT: {_count_params(self._model):.0f}M params [quantizado]")
                else:
                    self._log("  DiT quantizado em falta; a carregar FP16.")
            except Exception as e:
                self._log(f"  AVISO: DiT quantizado falhou ({e}); a usar FP16.")
                self._dit_quantized = False

        if not self._dit_quantized:
            self._log("A carregar DiT model (6.63 GB FP32 → ~3.3 GB FP16)...")
            model_ckpt = load_file(os.path.join(model_dir, "model/model.safetensors"), device=load_device)
            self._model.load_state_dict(model_ckpt)
            del model_ckpt
            self._model.to(dtype=self.dtype)
            self._model.eval()
            self._log(f"  DiT: {_count_params(self._model):.0f}M params")

        # Runtime SDNQ: após FP16 load (ou se pré-quant falhou). Não re-quantiza
        # DiT já carregado via artefactos quanto qint8.
        if not self._dit_quantized:
            from .utils.sdnq_resolve import resolve_sdnq_preset

            preset = self.sdnq_preset or resolve_sdnq_preset(
                self.quantization_mode,
                memory_efficient=self.memory_efficient,
                quantize_dit=self.quantize_dit,
            )
            if preset is not None:
                self._log(f"A aplicar quantização SDNQ ({preset}) ao DiT...")
                try:
                    from gamedev_shared.sdnq import quantize_model

                    self._model = quantize_model(self._model, preset=preset, dequantize_fp32=False)
                    self._dit_quantized = True
                    self._log(f"  DiT quantizado com SDNQ ({preset})")
                except Exception as e:
                    self._log(f"  AVISO: SDNQ quantização falhou ({e})")

        # --- Multi-GPU: DiT on primary GPU, auxiliaries on secondary ---
        if self._gpu_ids is not None and torch.cuda.is_available() and torch.cuda.device_count() >= 2:
            self._log(f"A configurar multi-GPU (GPUs {self._gpu_ids})...")
            self._dit_device = f"cuda:{self._gpu_ids[0]}"
            self._secondary_device = f"cuda:{self._gpu_ids[1]}"
            self._dit_multi_gpu = True
            self._log(f"  DiT → {self._dit_device}, auxiliares → {self._secondary_device}")

        # --- Conditioner ---
        self._log("A carregar Conditioner (1.76 GB FP32 → ~880 MB FP16)...")
        conditioner_config = EasyDict(configs["conditioner"])
        self._conditioner = instantiate_from_config(conditioner_config)
        cond_ckpt = load_file(os.path.join(model_dir, "conditioner/conditioner.safetensors"), device=load_device)
        self._conditioner.load_state_dict(cond_ckpt)
        del cond_ckpt
        self._conditioner.to(dtype=self.dtype)
        self._conditioner.eval()
        self._log(f"  Conditioner: {_count_params(self._conditioner):.0f}M params")

        # --- ShapeVAE ---
        self._log("A carregar ShapeVAE (656 MB FP32 → ~328 MB FP16)...")
        shapevae_config = EasyDict(configs["shapevae"])
        self._vae = instantiate_from_config(shapevae_config)
        vae_ckpt = load_file(os.path.join(model_dir, "shapevae/shapevae.safetensors"), device=load_device)
        self._vae.load_state_dict(vae_ckpt)
        del vae_ckpt
        self._vae.to(dtype=self.dtype)
        self._vae.eval()
        self._log(f"  ShapeVAE: {_count_params(self._vae):.0f}M params")

        # FlashVDM / Hierarchical / vanilla volume decode
        self._volume_decoder_resolved = resolve_volume_decoder(
            self.volume_decoder,
            quality=self.quality,
            memory_efficient=self.memory_efficient,
        )
        self._vae_decode_path = configure_vae_acceleration(
            self._vae,
            volume_decoder=self._volume_decoder_resolved,
            mc_algo=self.mc_algo,
            log_fn=self._log,
        )

        # --- Scheduler ---
        scheduler_config = EasyDict(configs["scheduler"])
        self._scheduler = instantiate_from_config(scheduler_config)

        # --- P3-SAM (bbox predictor) ---
        self._log("A carregar P3-SAM (451 MB FP32 → ~225 MB FP16)...")
        p3sam_config = EasyDict(configs["p3sam"])
        p3sam_config["params"]["ckpt_path"] = os.path.join(model_dir, "p3sam/p3sam.safetensors")
        _patch_space_hardcodes(
            bbox_merge_iou=self.bbox_merge_iou,
            mask_nms_iou=self.mask_nms_iou,
            secondary_mask_iou=self.secondary_mask_iou,
            min_cluster_support=self.min_cluster_support,
            min_predicted_iou=self.min_predicted_iou,
            prompt_batch_size=self.prompt_batch_size,
            multi_head=self.multi_head,
            head_min_score=self.head_min_score,
            head_score_ratio=self.head_score_ratio,
            consensus=self.consensus,
            consensus_vote=self.consensus_vote,
        )
        self._bbox_predictor = instantiate_from_config(p3sam_config)
        import sys

        auto_mask_module = sys.modules.get(type(self._bbox_predictor).__module__)
        configure_mask_quality = getattr(auto_mask_module, "configure_gamedev_mask_quality", None)
        if callable(configure_mask_quality):
            configure_mask_quality(
                mask_nms_iou=self.mask_nms_iou,
                secondary_mask_iou=self.secondary_mask_iou,
                min_cluster_support=self.min_cluster_support,
                min_predicted_iou=self.min_predicted_iou,
                prompt_batch_size=self.prompt_batch_size,
                bbox_merge_iou=self.bbox_merge_iou,
                multi_head=self.multi_head,
                head_min_score=self.head_min_score,
                head_score_ratio=self.head_score_ratio,
                consensus=self.consensus,
                consensus_vote=self.consensus_vote,
            )

        if self.channels_last and self.device == "cuda":
            apply_channels_last_modules(
                [self._model, self._vae, self._conditioner],
                log_fn=self._log,
            )

        if self.enable_torch_compile:
            # Conditioner usa torch_cluster.fps — Dynamo falha (fake tensor): nunca compilar.
            # DiT compile em ≤8 GB + offload → OOM; autotune decide.
            vram_now = get_vram_gb()
            free_now = get_free_vram_gb()
            compile_dit = should_compile_dit(
                vram_gb=vram_now,
                memory_efficient=self.memory_efficient,
                cpu_offload=self.cpu_offload,
                free_vram_gb=free_now,
            )
            mods: dict[str, Any] = {"ShapeVAE": self._vae}
            if compile_dit:
                mods["DiT"] = self._model
            else:
                self._log(
                    "torch.compile DiT skip (VRAM/offload) — só ShapeVAE; Conditioner nunca compila (torch_cluster.fps)"
                )
            compiled = compile_modules(
                mods,
                mode=self.torch_compile_mode,
                cpu_offload=self.cpu_offload,
                log_fn=self._log,
            )
            if compile_dit:
                self._model = compiled.get("DiT", self._model)
            self._vae = compiled.get("ShapeVAE", self._vae)
            self._torch_compile_dit_active = compile_dit
        else:
            self._torch_compile_dit_active = False

        gc.collect()
        elapsed = time.time() - t0
        if self._secondary_device:
            self._log(
                f"Modelos carregados em {elapsed:.1f}s (CPU, FP16) — "
                f"multi-GPU: DiT em {self._gpu_ids}, auxiliares em {self._secondary_device}"
            )
        else:
            self._log(f"Modelos carregados em {elapsed:.1f}s (CPU, FP16)")
        self._loaded = True

    # ------------------------------------------------------------------
    # Segmentação (P3-SAM)
    # ------------------------------------------------------------------

    def _segment_detail_passes(
        self,
        mesh: trimesh.Trimesh,
        face_ids: np.ndarray,
        *,
        seed: int,
        point_num: int,
        prompt_num: int,
    ) -> np.ndarray:
        """Re-segment large labels in local coordinates and keep useful splits."""
        if self.detail_levels <= 0:
            return face_ids

        from .utils.hierarchical import (
            detail_partition_is_useful,
            large_region_candidates,
            merge_detail_partition,
            prune_detail_partition,
        )
        from .utils.label_refine import refine_face_labels

        labels = np.asarray(face_ids, dtype=np.int64).copy()
        for level in range(self.detail_levels):
            candidates = large_region_candidates(
                mesh,
                labels,
                min_area_frac=_d.DEFAULT_DETAIL_PARENT_MIN_AREA_FRAC,
                max_regions=_d.DEFAULT_DETAIL_MAX_PARENTS,
            )
            if not candidates:
                break
            accepted = 0
            for rank, (parent_label, parent_faces, parent_frac) in enumerate(candidates):
                submesh = trimesh.Trimesh(
                    vertices=np.asarray(mesh.vertices).copy(),
                    faces=np.asarray(mesh.faces)[parent_faces].copy(),
                    process=False,
                )
                detail_points = min(point_num, _d.DEFAULT_DETAIL_POINT_NUM)
                detail_prompts = min(prompt_num, _d.DEFAULT_DETAIL_PROMPT_NUM)
                self._log(
                    f"  Detail L{level + 1}: label={parent_label} area={parent_frac:.1%}, "
                    f"faces={len(parent_faces)}, points={detail_points}, prompts={detail_prompts}"
                )
                try:
                    _aabb, child_ids, child_mesh = self._bbox_predictor.predict_aabb(
                        submesh,
                        seed=seed + (level + 1) * 1009 + rank,
                        post_process=False,
                        threshold=1.0,
                        point_num=detail_points,
                        prompt_num=detail_prompts,
                        clean_mesh_flag=False,
                        show_info=self.verbose,
                    )
                    if len(child_ids) != len(parent_faces) or len(child_mesh.faces) != len(parent_faces):
                        self._log("  Detail rejeitado: topologia local mudou")
                        continue
                    child_ids = refine_face_labels(
                        child_mesh,
                        child_ids,
                        iterations=_d.DEFAULT_REFINE_ITERATIONS,
                        smooth_angle_deg=_d.DEFAULT_REFINE_SMOOTH_ANGLE_DEG,
                        concave_factor=_d.DEFAULT_REFINE_CONCAVE_FACTOR,
                        island_min_frac=_d.DEFAULT_REFINE_ISLAND_MIN_FRAC,
                        island_min_faces=_d.DEFAULT_REFINE_ISLAND_MIN_FACES,
                        data_weight=_d.DEFAULT_REFINE_DATA_WEIGHT,
                        boundary_hops=_d.DEFAULT_REFINE_BOUNDARY_HOPS,
                    )
                    child_ids = prune_detail_partition(
                        child_mesh,
                        child_ids,
                        min_child_frac=_d.DEFAULT_DETAIL_CHILD_MIN_AREA_FRAC,
                    )
                    if not detail_partition_is_useful(
                        np.asarray(child_mesh.area_faces),
                        child_ids,
                        min_child_frac=_d.DEFAULT_DETAIL_CHILD_MIN_AREA_FRAC,
                        max_dominant_frac=_d.DEFAULT_DETAIL_MAX_DOMINANT_FRAC,
                    ):
                        self._log("  Detail rejeitado: divisão sem filhos significativos")
                        continue
                    labels = merge_detail_partition(labels, parent_faces, child_ids)
                    accepted += 1
                    self._log(
                        f"  Detail aceite: label {parent_label} → {len(np.unique(child_ids[child_ids >= 0]))} filhos"
                    )
                except Exception as e:
                    self._log(f"  AVISO: detail pass da label {parent_label} falhou ({e})")
                finally:
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
            if accepted == 0:
                break
        return labels

    def _segment_geometry(self, mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray, trimesh.Trimesh]:
        """CPU geometry-first segmentation into general crease-bounded regions."""
        from .utils.geometry_segment import aabbs_from_face_ids, segment_mesh_geometry

        self._log("Fase 1: geometry — regiões conectadas delimitadas por creases")
        with profile_span("part3d_segment_geometry"):
            clean_mesh, face_ids = segment_mesh_geometry(mesh, params=self.geometry_params)
            # Optional boundary cleanup is still available, but the initial
            # partition itself is already connected and crease-aware.
            if self.refine_labels:
                from .utils.label_refine import refine_face_labels

                face_ids = refine_face_labels(
                    clean_mesh,
                    face_ids,
                    iterations=min(8, _d.DEFAULT_REFINE_ITERATIONS),
                    smooth_angle_deg=_d.DEFAULT_REFINE_SMOOTH_ANGLE_DEG,
                    concave_factor=_d.DEFAULT_REFINE_CONCAVE_FACTOR,
                    island_min_frac=0.35,
                    island_min_faces=max(48, _d.DEFAULT_REFINE_ISLAND_MIN_FACES),
                    data_weight=0.7,
                    boundary_hops=1,
                )
            aabb = aabbs_from_face_ids(clean_mesh, face_ids)
            n_parts = len(np.unique(face_ids[face_ids >= 0]))
            self._log(f"  Geometry: {n_parts} partes")
        return aabb, face_ids, clean_mesh

    def segment(
        self,
        mesh: trimesh.Trimesh,
        *,
        postprocess: bool = _d.DEFAULT_POSTPROCESS,
        threshold: float = _d.DEFAULT_POSTPROCESS_THRESHOLD,
        seed: int | None = 42,
        point_num: int | None = None,
        prompt_num: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, trimesh.Trimesh]:
        """
        Segmenta mesh em partes semânticas.

        Returns:
            (aabb, face_ids, cleaned_mesh)
            - aabb: array (K, 2, 3) com bounding boxes de cada parte
            - face_ids: array (F,) com ID da parte para cada face
            - cleaned_mesh: mesh limpa pelo P3-SAM
        """
        from gamedev_shared.seed_utils import resolve_effective_seed

        seed = resolve_effective_seed(seed)
        mesh = mesh.copy()
        vertices_before = len(mesh.vertices)
        mesh.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=7)
        mesh.remove_unreferenced_vertices()
        if len(mesh.vertices) < vertices_before:
            self._log(f"  Topologia de análise soldada: {vertices_before} → {len(mesh.vertices)} vértices")

        if self.segment_mode == "geometry":
            return self._segment_geometry(mesh)

        self.load()
        self._log("Fase 1: P3-SAM — segmentação de partes")

        with profile_span("part3d_segment", sync_cuda=True):
            vram_gb = None
            if torch.cuda.is_available():
                try:
                    vram_gb = float(torch.cuda.get_device_properties(0).total_memory) / (1024**3)
                except Exception:
                    vram_gb = None
            if self.autotune:
                st = autotune_segment(mesh, vram_gb=vram_gb)
                pn = st.point_num if point_num is None else point_num
                pr = st.prompt_num if prompt_num is None else prompt_num
                self._log(
                    f"  Autotune segment: point_num={pn} prompt_num={pr} "
                    f"(índice={st.pressure_index}, geometria={st.geometry_score:.2f}, tier_vram={st.vram_tier})"
                )
            else:
                pn = 50000 if point_num is None else point_num
                pr = 128 if prompt_num is None else prompt_num

            # Point count drives GPU peak. Prompt inference is micro-batched;
            # 400 prompts match training/inference and mainly add runtime.
            if vram_gb is not None and vram_gb < 8.0:
                if pn > 56000:
                    self._log(f"  [VRAM] point_num {pn} → 56000")
                    pn = 56000
                if pr > 400:
                    self._log(f"  [VRAM] prompt_num {pr} → 400")
                    pr = 400

            # Garantir DiT/VAE/Conditioner em CPU antes do P3-SAM (pico SAM).
            if self.device == "cuda" and self.cpu_offload and not self._secondary_device:
                for attr in ("_model", "_vae", "_conditioner"):
                    mod = getattr(self, attr, None)
                    if mod is not None:
                        with contextlib.suppress(Exception):
                            _offload_to_cpu(mod)
                clear_cuda_memory()

            # Escolher device para P3-SAM: secondary GPU se multi-GPU, senão primário
            sam_device = self._secondary_device if self._secondary_device else self.device
            if self.device == "cuda" and self.cpu_offload:
                self._log(f"  Movendo P3-SAM para {sam_device}...")
                if hasattr(self._bbox_predictor, "to"):
                    _to_device(self._bbox_predictor, sam_device)
                _log_vram("P3-SAM na GPU: ") if self.verbose else None

            aabb, face_ids, clean_mesh = self._bbox_predictor.predict_aabb(
                mesh,
                seed=seed,
                post_process=postprocess,
                threshold=threshold,
                point_num=pn,
                prompt_num=pr,
            )

            if self.detail_levels > 0:
                with profile_span("part3d_hierarchical_detail", sync_cuda=True):
                    face_ids = self._segment_detail_passes(
                        clean_mesh,
                        face_ids,
                        seed=seed,
                        point_num=pn,
                        prompt_num=pr,
                    )
                    from .utils.label_refine import aabbs_from_face_ids

                    aabb = aabbs_from_face_ids(clean_mesh, face_ids)

            if self.device == "cuda" and self.cpu_offload and not self._secondary_device:
                self._log("  Offloading P3-SAM para CPU...")
                if hasattr(self._bbox_predictor, "to"):
                    _offload_to_cpu(self._bbox_predictor)
                import gc

                gc.collect()
                torch.cuda.empty_cache()
                torch.cuda.synchronize()

            if self.segment_mode == "hybrid":
                with profile_span("part3d_hybrid_geometry_snap"):
                    try:
                        from .utils.geometry_segment import snap_semantic_labels_to_geometry
                        from .utils.label_refine import aabbs_from_face_ids

                        labels_before = face_ids.copy()
                        face_ids, geometry_regions = snap_semantic_labels_to_geometry(
                            clean_mesh,
                            face_ids,
                            params=self.geometry_params,
                        )
                        aabb = aabbs_from_face_ids(clean_mesh, face_ids)
                        changed = int(np.count_nonzero(face_ids != labels_before))
                        n_regions = len(np.unique(geometry_regions[geometry_regions >= 0]))
                        self._log(f"  Hybrid geometry snap: {n_regions} super-regiões, {changed} faces corrigidas")
                    except Exception as e:
                        self._log(f"  AVISO: geometry snap híbrido falhou ({e}); a usar labels P3-SAM")

            if self.refine_labels:
                with profile_span("part3d_label_refine"):
                    try:
                        from .utils.label_refine import aabbs_from_face_ids, refine_face_labels

                        n_before = len(np.unique(face_ids[face_ids >= 0]))
                        face_ids = refine_face_labels(
                            clean_mesh,
                            face_ids,
                            iterations=_d.DEFAULT_REFINE_ITERATIONS,
                            smooth_angle_deg=_d.DEFAULT_REFINE_SMOOTH_ANGLE_DEG,
                            concave_factor=_d.DEFAULT_REFINE_CONCAVE_FACTOR,
                            island_min_frac=(
                                0.0 if self.segment_mode == "hybrid" else _d.DEFAULT_REFINE_ISLAND_MIN_FRAC
                            ),
                            island_min_faces=_d.DEFAULT_REFINE_ISLAND_MIN_FACES,
                            data_weight=_d.DEFAULT_REFINE_DATA_WEIGHT,
                            boundary_hops=_d.DEFAULT_REFINE_BOUNDARY_HOPS,
                            split_components=self.segment_mode != "hybrid",
                        )
                        aabb = aabbs_from_face_ids(clean_mesh, face_ids)
                        n_after = len(np.unique(face_ids[face_ids >= 0]))
                        self._log(f"  Labels refinadas (crease-aware): {n_before} → {n_after} partes")
                    except Exception as e:
                        self._log(f"  AVISO: refinamento de labels falhou ({e}); a usar labels P3-SAM crus")

            num_parts = len(np.unique(face_ids[face_ids >= 0]))
            self._log(f"  Detectadas {num_parts} partes")
        return aabb, face_ids, clean_mesh

    def segment_file(
        self,
        mesh_path: str | Path,
        *,
        segmentation_proxy_path: str | Path | None = None,
        **segment_kwargs: Any,
    ) -> tuple[np.ndarray, np.ndarray, trimesh.Trimesh]:
        """Segment a file, optionally using an aligned low-poly proxy."""
        from gamedev_shared.bpy_mesh import load_mesh_as_trimesh

        target_mesh = load_mesh_as_trimesh(mesh_path)
        if segmentation_proxy_path is None:
            return self.segment(target_mesh, **segment_kwargs)

        from .utils.label_refine import aabbs_from_face_ids, refine_face_labels
        from .utils.label_transfer import transfer_face_labels

        proxy_mesh = load_mesh_as_trimesh(segmentation_proxy_path)
        _proxy_aabb, proxy_ids, clean_proxy = self.segment(proxy_mesh, **segment_kwargs)
        with profile_span("part3d_proxy_label_transfer"):
            transferred = transfer_face_labels(clean_proxy, proxy_ids, target_mesh)
            target_mesh.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=7)
            if self.refine_labels:
                transferred = refine_face_labels(
                    target_mesh,
                    transferred,
                    iterations=_d.DEFAULT_REFINE_ITERATIONS,
                    smooth_angle_deg=_d.DEFAULT_REFINE_SMOOTH_ANGLE_DEG,
                    concave_factor=_d.DEFAULT_REFINE_CONCAVE_FACTOR,
                    island_min_frac=_d.DEFAULT_REFINE_ISLAND_MIN_FRAC,
                    island_min_faces=_d.DEFAULT_REFINE_ISLAND_MIN_FACES,
                    data_weight=_d.DEFAULT_REFINE_DATA_WEIGHT,
                    boundary_hops=_d.DEFAULT_REFINE_BOUNDARY_HOPS,
                )
            aabb = aabbs_from_face_ids(target_mesh, transferred)
        self._log(
            f"  Proxy labels transferidas: {len(proxy_ids)} faces → {len(transferred)} faces, "
            f"{len(np.unique(transferred[transferred >= 0]))} partes"
        )
        return aabb, transferred, target_mesh

    # ------------------------------------------------------------------
    # Geração de partes (X-Part)
    # ------------------------------------------------------------------

    def _generate_batch(
        self,
        mesh: trimesh.Trimesh,
        aabb_batch: torch.Tensor,
        part_surface_batch: torch.Tensor,
        obj_surface: torch.Tensor,
        octree_res: int,
        n_steps: int,
        n_chunks: int,
        cond_bs: int,
        seed: int,
        mc_level: float,
        mc_algo: str,
        batch_labels: list[int] | None = None,
        batch_offset: int = 0,
    ) -> tuple[trimesh.Scene, list[int]]:
        """Processa um único batch de partes através de Conditioner → DiT → VAE.

        Args:
            mesh: Mesh original normalizada
            aabb_batch: AABBs para este batch (num_parts_in_batch, 2, 3)
            part_surface_batch: Dados de superfície das partes (1, num_parts, N, dim)
            obj_surface: Dados de superfície do objeto (1, N_obj, dim)
            batch_labels: Label ids estáveis para nomear ``part_{label}``
            batch_offset: Índice de offset para logging / fallback de nomes

        Returns:
            (Scene com partes geradas, lista de label ids com sucesso)
        """
        from diffusers.utils.torch_utils import randn_tensor

        device = self.device
        dtype = self.dtype
        out = trimesh.Scene()
        succeeded: list[int] = []

        batch_size, num_parts, N, dim = part_surface_batch.shape
        total_parts = batch_size * num_parts

        # ---- FASE A: Encode conditions (Conditioner na GPU, chunked) ----
        try:
            import spconv.pytorch as _spconv_pt
            from spconv.pytorch.conv import ConvAlgo

            _spconv_pt.constants.SPCONV_USE_DIRECT_TABLE = True
            for m in self._conditioner.modules():
                if hasattr(m, "algo") and hasattr(ConvAlgo, "Native"):
                    m.algo = ConvAlgo.Native
        except Exception:
            pass

        effective_cond_bs = min(cond_bs, total_parts)
        cond_device = self._secondary_device if self._secondary_device else device
        self._log(
            f"  [A] Conditioner → {cond_device} (batch {batch_offset}-{batch_offset + num_parts}, "
            f"{num_parts} partes em lotes de {effective_cond_bs})..."
        )
        _to_device(self._conditioner, cond_device)
        if self.verbose:
            _log_vram("Conditioner na GPU: ")

        part_surf_flat = part_surface_batch.reshape(total_parts, N, dim)
        obj_surf_flat = obj_surface.expand(total_parts, -1, -1)

        def _is_oom(exc: BaseException) -> bool:
            msg = str(exc).lower()
            return "out of memory" in msg or "oom" in msg

        def _encode_range(
            start: int,
            end: int,
            *,
            parts: torch.Tensor = part_surf_flat,
            objs: torch.Tensor = obj_surf_flat,
        ) -> Any | None:
            ps = parts[start:end].to(device=cond_device, dtype=dtype)
            os_ = objs[start:end].to(device=cond_device, dtype=dtype)
            try:
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    c = self._conditioner(ps, os_)
                if isinstance(c, dict):
                    return {k: v.cpu() if hasattr(v, "cpu") else v for k, v in c.items()}
                return c.cpu()
            except RuntimeError as e:
                if not (_is_oom(e) or "algorithm" in str(e).lower()):
                    raise
                return None
            finally:
                del ps, os_
                torch.cuda.empty_cache()

        cond_chunks: list[Any] = []
        failed_part_indices: list[int] = []
        chunk_start = 0
        while chunk_start < total_parts:
            chunk_end = min(chunk_start + effective_cond_bs, total_parts)
            encoded = _encode_range(chunk_start, chunk_end)
            if encoded is not None:
                cond_chunks.append(encoded)
                chunk_start = chunk_end
                continue
            # OOM: reduzir batch → 1 e retry
            if chunk_end - chunk_start > 1:
                self._log(f"  [A] OOM Conditioner chunk [{chunk_start}:{chunk_end}] — retry parte-a-parte")
                effective_cond_bs = 1
                continue
            import gc

            gc.collect()
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            encoded = _encode_range(chunk_start, chunk_end)
            if encoded is not None:
                cond_chunks.append(encoded)
            else:
                self._log(f"  [A] Conditioner falhou na parte {chunk_start} após retry")
                failed_part_indices.append(chunk_start)
            chunk_start = chunk_end

        del part_surf_flat, obj_surf_flat

        if not cond_chunks:
            self._log(f"  [A] Batch {batch_offset}-{batch_offset + num_parts}: todas as partes falharam no encode")
            return out

        # Concatenar condições na CPU
        if isinstance(cond_chunks[0], dict):
            cond_cpu: dict[str, torch.Tensor] | torch.Tensor = {}
            for k in cond_chunks[0]:
                vals = [ch[k] for ch in cond_chunks if isinstance(ch[k], torch.Tensor)]
                if vals:
                    cond_cpu[k] = torch.cat(vals, dim=0)
        else:
            cond_cpu = torch.cat(cond_chunks, dim=0)
        del cond_chunks

        # Limpar todos os tensores temporários da GPU antes de carregar DiT
        import gc

        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

        if self.cpu_offload and not self._secondary_device:
            self._log("  [A] Offloading Conditioner para CPU...")
            _offload_to_cpu(self._conditioner)
            # AGRESSIVO: deletar conditioner temporariamente para garantir liberação de memória
            temp_conditioner = self._conditioner
            self._conditioner = None
            gc.collect()
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            # Forçar liberação de memória reservada do CUDA
            if hasattr(torch.cuda, "memory_stats"):
                torch.cuda.reset_peak_memory_stats()
            if self.verbose:
                _log_vram("Após remover Conditioner: ")

        # ---- FASE B: Denoising loop (DiT na GPU ou CPU) ----
        dit_device = device
        if self._dit_multi_gpu:
            dit_device = self._dit_device
            self._log(f"  [B] DiT multi-GPU (denoising {n_steps} steps)...")
            _to_device(self._model, dit_device)
            if self.enable_attention_slicing and hasattr(self._model, "enable_attention_slicing"):
                with contextlib.suppress(Exception):
                    self._model.enable_attention_slicing()
        else:
            self._log(f"  [B] DiT → GPU (denoising {n_steps} steps)...")
            try:
                _to_device(self._model, device)
                # Aplicar attention slicing se habilitado
                if self.enable_attention_slicing and hasattr(self._model, "enable_attention_slicing"):
                    try:
                        self._model.enable_attention_slicing()
                        self._log("  [B] Attention slicing habilitado no DiT")
                    except Exception as e:
                        if self.verbose:
                            self._log(f"  [B] AVISO: attention slicing não aplicado: {e}")
            except RuntimeError as e:
                if "out of memory" in str(e).lower():
                    self._log("  [B] OOM ao carregar DiT. Usando CPU (mais lento)...")
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
                    gc.collect()
                    dit_device = "cpu"
                    # Mover condições para CPU também
                    cond_cpu = (
                        {k: v.cpu() for k, v in cond_cpu.items()} if isinstance(cond_cpu, dict) else cond_cpu.cpu()
                    )
                    _to_device(self._model, "cpu")
                    self._log("  [B] DiT carregado na CPU. Denoising será ~10x mais lento.")
                else:
                    raise

        # Mover condições para o device do DiT (GPU ou CPU)
        if isinstance(cond_cpu, dict):
            cond = {k: v.to(device=dit_device, dtype=dtype) for k, v in cond_cpu.items()}
        else:
            cond = cond_cpu.to(device=dit_device, dtype=dtype)
        del cond_cpu
        if self.verbose and str(dit_device).startswith("cuda"):
            _log_vram("DiT na GPU: ")
        elif self.verbose and dit_device == "cpu":
            self._log("  [B] DiT na CPU (modo lento)")

        latent_shape = self._vae.latent_shape
        latents = randn_tensor((num_parts, *latent_shape), device=dit_device, dtype=dtype)

        num_tokens = torch.tensor(
            [np.array([latent_shape[0]] * aabb_batch.shape[1])] * aabb_batch.shape[0],
            device=dit_device,
        )
        aabb_dit = aabb_batch.to(device=dit_device, dtype=dtype)

        sigmas = np.linspace(0, 1, n_steps)
        self._scheduler.set_timesteps(sigmas=sigmas, device=dit_device)
        timesteps = self._scheduler.timesteps

        if str(dit_device).startswith("cuda"):
            torch.cuda.empty_cache()

        autocast_ctx = (
            torch.autocast("cuda", dtype=torch.bfloat16) if str(dit_device).startswith("cuda") else torch.no_grad()
        )
        with autocast_ctx:
            for _i, t in enumerate(tqdm(timesteps, desc="Denoising", mininterval=0.5)):
                latent_model_input = latents
                timestep = t.expand(latent_model_input.shape[0]).to(latents.dtype)
                timestep = timestep / self._scheduler.config.num_train_timesteps

                noise_pred = self._model(
                    latent_model_input,
                    timestep,
                    cond,
                    aabb=aabb_dit,
                    num_tokens=num_tokens,
                    guidance_cond=None,
                )

                outputs = self._scheduler.step(noise_pred, t, latents)
                latents = outputs.prev_sample

        del cond, aabb_dit, num_tokens
        latents_cpu = latents.cpu()
        del latents

        if self.cpu_offload and str(dit_device).startswith("cuda") and not self._secondary_device:
            if not self._dit_multi_gpu:
                self._log("  [B] Offloading DiT para CPU...")
                _offload_to_cpu(self._model)
            # Restaurar conditioner para próximo batch
            if "temp_conditioner" in locals() and temp_conditioner is not None:
                self._conditioner = temp_conditioner
                del temp_conditioner
                gc.collect()

        # ---- FASE C: Decode latents → mesh (VAE na GPU) ----
        vae_device = self._secondary_device if self._secondary_device else device
        self._log(f"  [C] ShapeVAE → {vae_device} (decode {num_parts} partes)...")
        _to_device(self._vae, vae_device)
        if self.verbose:
            _log_vram("ShapeVAE na GPU: ")

        from part3d.utils.mesh_bpy import fix_mesh

        for i in tqdm(range(num_parts), desc="Decode partes", mininterval=0.5):
            try:
                part_latent = latents_cpu[i].unsqueeze(0).to(device=vae_device, dtype=dtype)
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    decoded = 1.0 / self._vae.scale_factor * part_latent
                    decoded = self._vae(decoded)
                part_mesh_data = _decode_part_with_mc_retries(
                    self._vae,
                    decoded,
                    decode_path=self._vae_decode_path,
                    octree_resolution=octree_res,
                    num_chunks=n_chunks,
                    mc_level=mc_level,
                    mc_algo=mc_algo,
                    vae_device=str(vae_device),
                )

                raw_tm = _decode_output_to_trimesh(part_mesh_data)
                part_mesh = fix_mesh(raw_tm) if raw_tm is not None else None
                label = int(batch_labels[i]) if batch_labels is not None and i < len(batch_labels) else batch_offset + i
                if part_mesh is not None and len(part_mesh.vertices) > 0:
                    out.add_geometry(part_mesh, geom_name=f"part_{label}", node_name=f"part_{label}")
                    succeeded.append(label)
                    if self.verbose:
                        self._log(f"    Parte {label}: {len(part_mesh.faces)} faces")
                else:
                    self._log(f"    Parte {label} falhou: decode sem mesh (MC vazio)")

                del part_latent, decoded
                torch.cuda.empty_cache()

            except Exception as e:
                label = int(batch_labels[i]) if batch_labels is not None and i < len(batch_labels) else batch_offset + i
                self._log(f"    Parte {label} falhou: {e}")

        del latents_cpu
        if self.cpu_offload and not self._secondary_device:
            self._log("  [C] Offloading ShapeVAE para CPU...")
            _offload_to_cpu(self._vae)

        return out, succeeded

    @torch.no_grad()
    def generate(
        self,
        mesh_path: str | Path,
        aabb: np.ndarray,
        *,
        part_labels: np.ndarray | list[int] | None = None,
        octree_resolution: int | None = None,
        num_inference_steps: int | None = None,
        guidance_scale: float = _d.DEFAULT_GUIDANCE_SCALE,
        num_chunks: int | None = None,
        mc_level: float = _d.DEFAULT_MC_LEVEL,
        mc_algo: str | None = None,
        seed: int = 42,
        surface_pc_size: int | None = None,
        bbox_num_points: int | None = None,
        cond_batch_size: int | None = None,
    ) -> tuple[trimesh.Scene, list[int]]:
        """
        Gera partes a partir de uma mesh segmentada.

        Usa CPU offloading sequencial:
        Conditioner (encode) → offload → DiT (denoise) → offload → VAE (decode)

        Returns:
            (parts_scene, succeeded_label_ids)
        """
        self.load()
        self._log("Fase 2: X-Part — geração de partes com CPU offloading")

        try:
            import torch_cluster  # noqa: F401 — exigido pelo conditioner X-Part (fps)
        except ImportError as e:
            raise RuntimeError(
                "Falta o pacote torch-cluster (PyG). No venv Part3D: "
                "python -m pip install torch-cluster --no-build-isolation"
            ) from e

        import pytorch_lightning as pl

        from gamedev_shared.seed_utils import resolve_effective_seed

        seed = resolve_effective_seed(seed)
        pl.seed_everything(seed, workers=True)

        mesh_path = str(mesh_path)
        from gamedev_shared.bpy_mesh import load_mesh_as_trimesh

        mesh = load_mesh_as_trimesh(mesh_path)

        aabb = np.asarray(aabb, dtype=np.float64)
        margin = float(getattr(self, "aabb_margin_frac", _d.DEFAULT_AABB_MARGIN_FRAC) or 0.0)
        if margin > 0 and aabb.size > 0:
            from .utils.label_refine import expand_aabbs

            aabb = expand_aabbs(aabb, margin_frac=margin)
            self._log(f"  AABB margin: +{margin:.1%} por eixo (geração X-Part)")
        if part_labels is None:
            labels_all = np.arange(int(aabb.shape[0]), dtype=np.int64)
        else:
            labels_all = np.asarray(part_labels, dtype=np.int64)
            if labels_all.shape[0] != aabb.shape[0]:
                raise ValueError(f"part_labels length {labels_all.shape[0]} != aabb parts {aabb.shape[0]}")

        num_parts_aabb = int(aabb.shape[0])
        vram_gb = None
        if torch.cuda.is_available():
            try:
                vram_gb = float(torch.cuda.get_device_properties(0).total_memory) / (1024**3)
            except Exception:
                vram_gb = None
        free_vram_gb = get_free_vram_gb() if torch.cuda.is_available() else None
        compile_flag = bool(self.enable_torch_compile and getattr(self, "_torch_compile_dit_active", False))
        if self.autotune:
            gt = autotune_generate(
                mesh,
                num_parts_aabb,
                vram_gb=vram_gb,
                dit_quantized=self._dit_quantized,
                memory_efficient=self.memory_efficient,
                compile_active=self.enable_torch_compile,
                cpu_offload=self.cpu_offload,
                free_vram_gb=free_vram_gb,
            )
            octree_res = gt.octree_resolution if octree_resolution is None else octree_resolution
            n_steps = gt.num_inference_steps if num_inference_steps is None else num_inference_steps
            n_chunks = gt.num_chunks if num_chunks is None else num_chunks
            pc_sz = gt.surface_pc_size if surface_pc_size is None else surface_pc_size
            bbox_pts = gt.bbox_num_points if bbox_num_points is None else bbox_num_points
            cond_bs = gt.cond_batch_size if cond_batch_size is None else cond_batch_size
            compile_flag = bool(gt.compile_dit and getattr(self, "_torch_compile_dit_active", False))

            # Se VRAM muito limitada, reduzir steps para acelerar (DiT vai para CPU)
            if vram_gb is not None and vram_gb < 8.0 and num_inference_steps is None and not self._dit_quantized:
                original_steps = n_steps
                n_steps = min(n_steps, 20)  # Máximo 20 steps se DiT puder ir a CPU
                if n_steps != original_steps:
                    self._log(
                        f"  [AUTOTUNE] VRAM limitada ({vram_gb:.1f} GB). Reduzindo steps: {original_steps} → {n_steps}"
                    )

            max_parts_per_batch = gt.max_parts_allowed if gt.max_parts_allowed > 0 else 1
            self._log(
                f"  Autotune generate: octree={octree_res} chunks={n_chunks} steps={n_steps} "
                f"cond_batch={cond_bs} max_parts/batch={max_parts_per_batch} "
                f"compile_dit={compile_flag} (índice={gt.pressure_index}, "
                f"partes={num_parts_aabb}, geometria={gt.geometry_score:.2f})"
            )
        else:
            octree_res = _d.DEFAULT_OCTREE_RESOLUTION if octree_resolution is None else octree_resolution
            n_steps = _d.DEFAULT_NUM_INFERENCE_STEPS if num_inference_steps is None else num_inference_steps
            n_chunks = _d.DEFAULT_NUM_CHUNKS if num_chunks is None else num_chunks
            pc_sz = 81920 if surface_pc_size is None else surface_pc_size
            bbox_pts = 81920 if bbox_num_points is None else bbox_num_points
            vram_gb_calc = vram_gb if vram_gb else (get_vram_gb() if torch.cuda.is_available() else None)
            max_parts_calc = get_max_parts_for_vram(
                vram_gb_calc,
                dit_quantized=self._dit_quantized,
                compile_active=compile_flag,
                free_vram_gb=free_vram_gb,
            )
            max_parts_per_batch = max_parts_calc if max_parts_calc else 1
            if cond_batch_size is None:
                from .utils.autotune import _compute_cond_batch_size

                cond_bs = _compute_cond_batch_size(num_parts_aabb, vram_gb_calc, free_vram_gb=free_vram_gb)
            else:
                cond_bs = cond_batch_size

        effective_mc = self.mc_algo if mc_algo is None else mc_algo
        if self._volume_decoder_resolved:
            self._log(
                f"  Volume decode: {self._volume_decoder_resolved} path={self._vae_decode_path} mc={effective_mc}"
            )
        if self.device == "cuda":
            import gc

            gc.collect()
            torch.cuda.empty_cache()
            torch.cuda.synchronize()

        # Normalizar mesh
        vertices = mesh.vertices
        min_xyz = np.min(vertices, axis=0)
        max_xyz = np.max(vertices, axis=0)
        center = (min_xyz + max_xyz) / 2.0
        scale = np.max(max_xyz - min_xyz) / 2 / 0.8
        mesh.vertices = (vertices - center) / scale
        self._log(f"  Mesh normalizada: center={center}, scale={scale:.4f}")

        # Normalizar aabb
        aabb_t = torch.from_numpy(aabb).float()
        aabb_t = (aabb_t - torch.from_numpy(center).float()) / scale

        # Importar utilidades do XPart
        from partgen.utils.mesh_utils import (
            SampleMesh,
            load_surface_points,
            sample_bbox_points_from_trimesh,
        )

        # Preparar dados de superfície
        self._log("  Preparando dados de superfície...")
        rng = np.random.default_rng(seed=seed)
        obj_surface_raw = SampleMesh(mesh.vertices, mesh.faces, -1, seed=seed)
        obj_surface, _ = load_surface_points(
            rng,
            obj_surface_raw["random_surface"],
            obj_surface_raw["sharp_surface"],
            pc_size=pc_sz,
            pc_sharpedge_size=0,
            return_sharpedge_label=True,
            return_normal=True,
        )
        obj_surface = obj_surface.unsqueeze(0)

        part_surface_inbbox, valid_parts_mask = sample_bbox_points_from_trimesh(
            mesh, aabb_t, num_points=bbox_pts, seed=seed
        )
        valid_np = valid_parts_mask.detach().cpu().numpy().astype(bool)
        if valid_np.ndim > 1:
            valid_np = valid_np.reshape(-1)
        labels_valid = labels_all[valid_np]
        aabb_t = aabb_t[valid_parts_mask].unsqueeze(0)
        part_surface_inbbox = part_surface_inbbox.unsqueeze(0)

        _, num_parts, N, _ = part_surface_inbbox.shape
        self._log(f"  Partes válidas: {num_parts}, pontos/parte: {N}")

        # Cena final para combinar todos os resultados
        final_scene = trimesh.Scene()
        total_generated = 0
        succeeded: list[int] = []

        # Batches adaptativos: reavalia VRAM livre entre batches (anti-OOM/fragmentação).
        start_idx = 0
        batch_idx = 0
        while start_idx < num_parts:
            cond_bs, max_parts_per_batch = refresh_generate_limits(
                num_parts=num_parts - start_idx,
                vram_gb=vram_gb,
                dit_quantized=self._dit_quantized,
                compile_active=compile_flag,
                cond_batch_size=cond_bs,
                max_parts_allowed=max_parts_per_batch,
            )
            end_idx = min(start_idx + max_parts_per_batch, num_parts)
            batch_idx += 1
            if num_parts > max_parts_per_batch:
                self._log(
                    f"  === Batch {batch_idx} (partes {start_idx}-{end_idx - 1}, "
                    f"máx {max_parts_per_batch}/batch, cond_bs={cond_bs}) ==="
                )

            aabb_batch = aabb_t[0, start_idx:end_idx].unsqueeze(0)
            part_surf_batch = part_surface_inbbox[0, start_idx:end_idx].unsqueeze(0)
            batch_labels = [int(x) for x in labels_valid[start_idx:end_idx]]

            batch_scene, batch_ok = self._generate_batch(
                mesh=mesh,
                aabb_batch=aabb_batch,
                part_surface_batch=part_surf_batch,
                obj_surface=obj_surface,
                octree_res=octree_res,
                n_steps=n_steps,
                n_chunks=n_chunks,
                cond_bs=cond_bs,
                seed=seed,
                mc_level=mc_level,
                mc_algo=effective_mc,
                batch_labels=batch_labels,
                batch_offset=start_idx,
            )

            for geom_name, geom in batch_scene.geometry.items():
                final_scene.add_geometry(geom, geom_name=geom_name, node_name=geom_name)
            total_generated += len(batch_scene.geometry)
            succeeded.extend(batch_ok)

            start_idx = end_idx
            if start_idx < num_parts:
                import gc

                gc.collect()
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                self._log(f"  Batch {batch_idx} completo. {len(batch_scene.geometry)} partes geradas.")

        # Desnormalizar as meshes finais
        if total_generated > 0:
            self._log(f"  Desnormalizando {total_generated} partes...")
            for _name, geom in list(final_scene.geometry.items()):
                if isinstance(geom, trimesh.Trimesh):
                    geom.vertices = geom.vertices * scale + center

        self._log(f"  Total: {total_generated} partes geradas com sucesso")
        return final_scene, succeeded

    # ------------------------------------------------------------------
    # Pipeline completo: segment + generate
    # ------------------------------------------------------------------

    def __call__(
        self,
        mesh_path: str | Path,
        *,
        segmentation_proxy_path: str | Path | None = None,
        octree_resolution: int | None = None,
        num_inference_steps: int | None = None,
        num_chunks: int | None = None,
        seed: int | None = 42,
        postprocess: bool = _d.DEFAULT_POSTPROCESS,
        threshold: float = _d.DEFAULT_POSTPROCESS_THRESHOLD,
        point_num: int | None = None,
        prompt_num: int | None = None,
        surface_pc_size: int | None = None,
        bbox_num_points: int | None = None,
        cond_batch_size: int | None = None,
        mc_algo: str | None = None,
    ) -> tuple[trimesh.Scene, np.ndarray, trimesh.Trimesh]:
        """
        Pipeline completo: segmenta e gera partes.

        Returns:
            (parts_scene, face_ids, segmented_mesh)
        """
        with profile_span("part3d_decompose", sync_cuda=True):
            from gamedev_shared.seed_utils import resolve_effective_seed

            seed = resolve_effective_seed(seed)
            aabb, face_ids, clean_mesh = self.segment_file(
                mesh_path,
                segmentation_proxy_path=segmentation_proxy_path,
                postprocess=postprocess,
                threshold=threshold,
                seed=seed,
                point_num=point_num,
                prompt_num=prompt_num,
            )

            from .utils.face_split import (
                label_ids_ordered,
                merge_xpart_with_face_fallback,
                split_mesh_by_face_ids,
                thin_part_mask,
                xpart_candidate_mask,
            )

            labels = label_ids_ordered(face_ids)
            if self.parts_mode == "faces":
                self._log(
                    "Fase 2: face-split (sem X-Part) — malha oca Hunyuan deixa cascas ao apagar partes; "
                    "usa --parts-mode hybrid para diffusion"
                )
                parts_scene = split_mesh_by_face_ids(clean_mesh, face_ids, cap_holes=self.cap_part_holes)
                return parts_scene, face_ids, clean_mesh

            # Opt-in: partes finas/alongadas com topologia original (MC derrete
            # escadas/bandeiras). Por defeito corre X-Part em todas — evita
            # escada-dupla / furos quando a feature está colada ao volume.
            thin = (
                thin_part_mask(
                    clean_mesh,
                    face_ids,
                    max_thin_ratio=self.xpart_skip_thin_ratio,
                    min_aspect=self.xpart_skip_aspect,
                )
                if (self.preserve_thin_topology and len(labels) > 0)
                else np.zeros(len(labels), dtype=bool)
            )
            thin_labels = {int(x) for x in labels[thin]} if len(labels) and thin.any() else set()
            if thin_labels:
                self._log(
                    f"  X-Part skip {len(thin_labels)} partes finas/alongadas "
                    f"(thin≤{self.xpart_skip_thin_ratio:.2f} ou aspect≥{self.xpart_skip_aspect:.1f}) "
                    f"→ face topology: {sorted(thin_labels)}"
                )

            cand = (
                xpart_candidate_mask(clean_mesh, face_ids, max_area_frac=self.xpart_max_area_frac)
                if len(labels) > 0
                else np.ones(0, dtype=bool)
            )
            # Compact/large só entre labels não-finas (quando preserve_thin).
            run_mask = ~thin if len(thin) == len(cand) else np.ones(len(labels), dtype=bool)
            compact_sel = cand & run_mask
            large_sel = (~cand) & run_mask
            compact_aabb, compact_labels = aabb[compact_sel], labels[compact_sel]
            large_aabb, large_labels = aabb[large_sel], labels[large_sel]
            if int(compact_sel.sum()) + int(large_sel.sum()) > 0:
                self._log(
                    f"  X-Part: {int(compact_sel.sum())} compactas (octree cheio) + "
                    f"{int(large_sel.sum())} grandes (octree≤{self.xpart_large_octree})"
                    + (f" + {len(thin_labels)} finas (face)" if thin_labels else "")
                )

            parts_scene = trimesh.Scene()
            succeeded: list[int] = []

            def _run_xpart(aabb_batch: np.ndarray, label_batch: np.ndarray, *, octree: int | None) -> None:
                nonlocal parts_scene, succeeded
                if aabb_batch.shape[0] == 0:
                    return
                scene, ok = self.generate(
                    mesh_path,
                    aabb_batch,
                    part_labels=label_batch,
                    octree_resolution=octree,
                    num_inference_steps=num_inference_steps,
                    num_chunks=num_chunks,
                    mc_algo=mc_algo,
                    seed=seed,
                    surface_pc_size=surface_pc_size,
                    bbox_num_points=bbox_num_points,
                    cond_batch_size=cond_batch_size,
                )
                for name, geom in scene.geometry.items():
                    parts_scene.add_geometry(geom, geom_name=name, node_name=name)
                succeeded.extend(ok)

            full_octree = octree_resolution
            large_octree = self.xpart_large_octree
            if full_octree is not None:
                large_octree = min(int(full_octree), int(self.xpart_large_octree))

            _run_xpart(compact_aabb, compact_labels, octree=full_octree)
            _run_xpart(large_aabb, large_labels, octree=large_octree)

            if self.parts_mode == "hybrid" or thin_labels:
                before = len(parts_scene.geometry)
                parts_scene = merge_xpart_with_face_fallback(
                    clean_mesh,
                    face_ids,
                    parts_scene,
                    succeeded,
                    cap_holes=self.cap_part_holes,
                    prefer_face_labels=thin_labels,
                )
                filled = len(parts_scene.geometry) - before
                if filled > 0 or thin_labels:
                    self._log(
                        f"  Hybrid: face-split para finas={len(thin_labels)} + falhas MC (+{max(0, filled)} meshes)"
                    )

            if self.exclusive_partition and len(parts_scene.geometry) > 1:
                from .utils.exclusive_partition import exclusive_surface_partition, partition_stats

                before_scene = parts_scene
                parts_scene = exclusive_surface_partition(
                    parts_scene,
                    samples_per_part=self.exclusive_samples_per_part,
                )
                stats = partition_stats(before_scene, parts_scene)
                if stats["faces_dropped"] > 0:
                    self._log(
                        f"  Exclusive partition: -{stats['faces_dropped']} faces overlap "
                        f"({stats['faces_before']}→{stats['faces_after']}, "
                        f"{stats['parts_before']}→{stats['parts_after']} parts)"
                    )

            return parts_scene, face_ids, clean_mesh

    # ------------------------------------------------------------------
    # Limpeza
    # ------------------------------------------------------------------

    def unload(self) -> None:
        """Liberta todos os modelos da memória."""
        for attr in ("_model", "_conditioner", "_vae", "_bbox_predictor"):
            obj = getattr(self, attr, None)
            if obj is not None:
                del obj
                setattr(self, attr, None)
        self._scheduler = None
        self._loaded = False
        clear_cuda_memory()
        self._log("Pipeline descarregado.")

    def __enter__(self) -> Part3DPipeline:
        return self

    def __exit__(self, *args: Any) -> None:
        self.unload()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_params(module: torch.nn.Module) -> float:
    """Conta parâmetros em milhões."""
    return sum(p.numel() for p in module.parameters()) / 1e6


def _setup_xpart_imports(model_dir: str) -> None:
    """Configura sys.path para importar módulos do XPart do Space HF."""
    import sys

    # O código do XPart vive dentro do Space tencent/Hunyuan3D-Part
    # Precisamos do código Python do Space, não apenas dos pesos
    # Vamos clonar/descarregar se necessário
    space_dir = _ensure_xpart_code()

    # Remover pymeshlab do mesh_utils ANTES de qualquer import partgen.
    _patch_mesh_utils_bpy(space_dir)

    xpart_dir = os.path.join(space_dir, "XPart")
    p3sam_dir = os.path.join(space_dir, "P3-SAM")

    for d in (xpart_dir, p3sam_dir, space_dir):
        if d not in sys.path and os.path.isdir(d):
            sys.path.insert(0, d)


_BPY_MESH_UTILS_TAIL = """\
# GAMEDEV_BPY_MESH_PATCH — repair via bpy (part3d.utils.mesh_bpy); no pymeshlab.


def pymeshlab2trimesh(mesh):
    raise RuntimeError("pymeshlab disabled in GameDev Part3D; use part3d.utils.mesh_bpy.fix_mesh")


def trimesh2pymeshlab(mesh):
    raise RuntimeError("pymeshlab disabled in GameDev Part3D; use part3d.utils.mesh_bpy.fix_mesh")


def remove_overlength_edge(mesh, max_length: float):
    return mesh


def remove_floater(mesh):
    return mesh


def fix_mesh(mesh):
    from part3d.utils.mesh_bpy import fix_mesh as _fix_mesh_bpy

    return _fix_mesh_bpy(mesh)
"""


def _patch_mesh_utils_bpy(space_dir: str) -> None:
    """Substitui bloco pymeshlab de ``partgen.utils.mesh_utils`` por bpy.

    O Space HF faz ``import pymeshlab`` no topo — isso rebenta o load do
    conditioner/VAE. Aqui removemos o import e redireccionamos ``fix_mesh``
    para :func:`part3d.utils.mesh_bpy.fix_mesh` (mesmo estilo Text3D).
    """
    path = os.path.join(space_dir, "XPart", "partgen", "utils", "mesh_utils.py")
    if not os.path.isfile(path):
        return

    with open(path, encoding="utf-8") as f:
        content = f.read()

    if "GAMEDEV_BPY_MESH_PATCH" in content:
        return

    if "import pymeshlab" not in content and "def fix_mesh" not in content:
        return

    content = content.replace("import pymeshlab\n", "# import pymeshlab  # GAMEDEV: removed\n")

    marker = "def pymeshlab2trimesh"
    idx = content.find(marker)
    if idx < 0:
        # Sem helpers pymeshlab — só garantir que fix_mesh existe via append
        if "def fix_mesh" in content:
            # Substituir corpo de fix_mesh se ainda referenciar pymeshlab
            pass
        content = content.rstrip() + "\n\n" + _BPY_MESH_UTILS_TAIL
    else:
        content = content[:idx] + _BPY_MESH_UTILS_TAIL

    # Evitar corromper blob partilhado do HF cache: unlink symlink → ficheiro real
    if os.path.islink(path):
        os.unlink(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    # Invalidar import cache se já carregado
    import sys

    for key in list(sys.modules):
        if key.endswith("mesh_utils") or key == "partgen.utils.mesh_utils":
            mod_file = str(getattr(sys.modules[key], "__file__", "") or "")
            if "mesh_utils" in mod_file or key.endswith("mesh_utils"):
                del sys.modules[key]


def _ensure_xpart_code() -> str:
    """Garante que o código do Space HF está disponível localmente."""
    from huggingface_hub import snapshot_download

    space_dir = snapshot_download(
        repo_id="tencent/Hunyuan3D-Part",
        repo_type="space",
    )
    return space_dir


def _patch_space_hardcodes(
    bbox_merge_iou: float | None = None,
    *,
    mask_nms_iou: float = _d.SPACE_MASK_NMS_IOU,
    secondary_mask_iou: float = _d.SPACE_SECONDARY_MASK_IOU,
    min_cluster_support: int = _d.SPACE_MIN_CLUSTER_SUPPORT,
    min_predicted_iou: float = _d.SPACE_MIN_PREDICTED_IOU,
    prompt_batch_size: int = _d.SPACE_PROMPT_BATCH_SIZE,
    multi_head: bool = _d.SPACE_MULTI_HEAD,
    head_min_score: float = _d.SPACE_HEAD_MIN_SCORE,
    head_score_ratio: float = _d.SPACE_HEAD_SCORE_RATIO,
    consensus: bool = _d.SPACE_CONSENSUS,
    consensus_vote: float = _d.SPACE_CONSENSUS_VOTE,
) -> None:
    """Corrige hardcodes do Space HF que assumem Docker / GPUs grandes.

    Delegado em :mod:`part3d.utils.space_patch` (funções puras testáveis):
    1. P3-SAM/model.py: download_root='/root/sonata' → ~/.cache/sonata
    2. auto_mask_api.py: point_num/prompt_num hardcoded para baixa VRAM
    3. auto_mask_api.py: get_mask batch size para caber em ~6 GB
    4. auto_mask_api.py: cutoffs de área + bbox-IoU (anti-fuse porta/moldura)
    5. auto_mask_api.py: NMS consensus + fix_label/connected_region rápidos
    6. surface_extractors.py: marching_cubes precisa float32 (não BF16)
    """
    from .utils.space_patch import apply_space_patches

    space_dir = _ensure_xpart_code()
    apply_space_patches(
        space_dir,
        part_area_merge=_d.SPACE_PART_AREA_MERGE,
        area_ratio_keep=_d.SPACE_AREA_RATIO_KEEP,
        bbox_merge_iou=_d.SPACE_BBOX_MERGE_IOU if bbox_merge_iou is None else bbox_merge_iou,
        mask_nms_iou=mask_nms_iou,
        secondary_mask_iou=secondary_mask_iou,
        min_cluster_support=min_cluster_support,
        min_predicted_iou=min_predicted_iou,
        prompt_batch_size=prompt_batch_size,
        multi_head=multi_head,
        head_min_score=head_min_score,
        head_score_ratio=head_score_ratio,
        consensus=consensus,
        consensus_vote=consensus_vote,
    )

    # Invalidar módulos já importados
    import sys

    for key in list(sys.modules.keys()):
        mod = sys.modules[key]
        mod_file = str(getattr(mod, "__file__", "") or "")
        if space_dir in mod_file and (
            "model" in key or "auto_mask" in key or "surface_extract" in key or "volume_decoder" in key
        ):
            del sys.modules[key]
