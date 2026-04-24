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
from .utils.autotune import autotune_generate, autotune_segment, get_max_parts_for_vram, get_vram_gb
from .utils.dit_quantization import load_dit_quantized, want_quantized_dit
from .utils.flash_attn_shim import install_shim as _install_flash_shim
from .utils.memory import clear_cuda_memory, format_bytes

_logger = Logger()

# Injetar shim de flash_attn ANTES de qualquer import do XPart/Sonata
_install_flash_shim()


def _vae_latent2mesh(vae: Any, decoded: torch.Tensor, **kwargs: Any) -> Any:
    """Chama ``latent2mesh_2`` apenas com argumentos suportados pela ShapeVAE instalada."""
    fn = vae.latent2mesh_2
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(decoded, **kwargs)
    call_kw = {k: v for k, v in kwargs.items() if k in params}
    return fn(decoded, **call_kw)


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
    - Quantização 4-bit/8-bit via bitsandbytes ou torchao
    - torch.compile para acelerar inferência
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
        enable_attention_slicing: bool = _d.DEFAULT_ENABLE_ATTENTION_SLICING,
        low_vram: bool = _d.DEFAULT_LOW_VRAM_MODE,
        gpu_ids: list[int] | None = None,
    ):
        self.model_path = model_path
        self.cpu_offload = cpu_offload
        self.verbose = verbose
        self.autotune = autotune
        self.quantization_mode = quantization_mode
        self.quantize_dit = quantize_dit
        self.enable_torch_compile = enable_torch_compile
        self.enable_attention_slicing = enable_attention_slicing
        self.low_vram = low_vram

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
        """Download/cache do modelo via huggingface_hub."""
        if self._model_dir is not None:
            return self._model_dir

        from huggingface_hub import snapshot_download

        self._log(f"A descarregar modelo de {self.model_path}...")
        self._model_dir = snapshot_download(
            repo_id=self.model_path,
            repo_type="model",
        )
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

        t0 = time.time()
        model_dir = self._ensure_model_dir()

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
        use_q = self.low_vram and want_quantized_dit(self.device, model_dir)
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

        # Aplicar quantização adicional via torchao se solicitado
        if self.low_vram and self.quantize_dit and self.quantization_mode.startswith("torchao"):
            self._log(f"A aplicar quantização torchao ({self.quantization_mode}) ao DiT...")
            try:
                from gamedev_shared.quantization import apply_torchao_quantization

                self._model = apply_torchao_quantization(
                    self._model, mode=self.quantization_mode.replace("torchao-", "") + "_weight_only"
                )
                self._log("  DiT quantizado com torchao")
            except Exception as e:
                self._log(f"  AVISO: torchao quantização falhou ({e})")

        # Aplicar quantização via SDNQ se solicitado
        if self.low_vram and self.quantize_dit and self.quantization_mode.startswith("sdnq"):
            self._log(f"A aplicar quantização SDNQ ({self.quantization_mode}) ao DiT...")
            try:
                from gamedev_shared.sdnq import quantize_model

                self._model = quantize_model(self._model, preset=self.quantization_mode)
                self._log("  DiT quantizado com SDNQ")
            except Exception as e:
                self._log(f"  AVISO: SDNQ quantização falhou ({e})")

        # Aplicar torch.compile se solicitado
        if self.enable_torch_compile:
            self._log("A aplicar torch.compile ao DiT...")
            try:
                from gamedev_shared.quantization import apply_torch_compile

                self._model = apply_torch_compile(
                    self._model,
                    mode=_d.DEFAULT_TORCH_COMPILE_MODE,
                    fullgraph=False,
                )
                self._log("  DiT compilado com torch.compile")
            except Exception as e:
                self._log(f"  AVISO: torch.compile falhou ({e})")

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

        # --- Scheduler ---
        scheduler_config = EasyDict(configs["scheduler"])
        self._scheduler = instantiate_from_config(scheduler_config)

        # --- P3-SAM (bbox predictor) ---
        self._log("A carregar P3-SAM (451 MB FP32 → ~225 MB FP16)...")
        p3sam_config = EasyDict(configs["p3sam"])
        p3sam_config["params"]["ckpt_path"] = os.path.join(model_dir, "p3sam/p3sam.safetensors")
        _patch_space_hardcodes()
        self._bbox_predictor = instantiate_from_config(p3sam_config)

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

    def segment(
        self,
        mesh: trimesh.Trimesh,
        *,
        postprocess: bool = _d.DEFAULT_POSTPROCESS,
        threshold: float = _d.DEFAULT_POSTPROCESS_THRESHOLD,
        seed: int = 42,
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

            if self.device == "cuda" and self.cpu_offload and not self._secondary_device:
                self._log("  Offloading P3-SAM para CPU...")
                if hasattr(self._bbox_predictor, "to"):
                    _offload_to_cpu(self._bbox_predictor)
                import gc

                gc.collect()
                torch.cuda.empty_cache()
                torch.cuda.synchronize()

            num_parts = len(np.unique(face_ids[face_ids >= 0]))
            self._log(f"  Detectadas {num_parts} partes")
        return aabb, face_ids, clean_mesh

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
        batch_offset: int = 0,
    ) -> trimesh.Scene:
        """Processa um único batch de partes através de Conditioner → DiT → VAE.

        Args:
            mesh: Mesh original normalizada
            aabb_batch: AABBs para este batch (num_parts_in_batch, 2, 3)
            part_surface_batch: Dados de superfície das partes (1, num_parts, N, dim)
            obj_surface: Dados de superfície do objeto (1, N_obj, dim)
            batch_offset: Índice de offset para logging (parte X de Y)

        Returns:
            Scene com as partes geradas deste batch
        """
        from diffusers.utils.torch_utils import randn_tensor

        device = self.device
        dtype = self.dtype
        out = trimesh.Scene()

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

        cond_chunks: list[torch.Tensor] = []
        failed_part_indices: list[int] = []
        for chunk_start in range(0, total_parts, effective_cond_bs):
            chunk_end = min(chunk_start + effective_cond_bs, total_parts)
            ps = part_surf_flat[chunk_start:chunk_end].to(device=cond_device, dtype=dtype)
            os_ = obj_surf_flat[chunk_start:chunk_end].to(device=cond_device, dtype=dtype)
            try:
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    c = self._conditioner(ps, os_)
                if isinstance(c, dict):
                    cond_chunks.append({k: v.cpu() if hasattr(v, "cpu") else v for k, v in c.items()})
                else:
                    cond_chunks.append(c.cpu())
            except RuntimeError as e:
                err_msg = str(e)
                if "algorithm" in err_msg or "OutOfMemory" in err_msg:
                    self._log(f"  [A] Conditioner falhou no chunk [{chunk_start}:{chunk_end}]")
                    failed_part_indices.extend(range(chunk_start, chunk_end))
                else:
                    raise
            finally:
                del ps, os_
                torch.cuda.empty_cache()

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

        from partgen.utils.mesh_utils import fix_mesh

        for i in tqdm(range(num_parts), desc="Decode partes", mininterval=0.5):
            try:
                part_latent = latents_cpu[i].unsqueeze(0).to(device=vae_device, dtype=dtype)
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    decoded = 1.0 / self._vae.scale_factor * part_latent
                    decoded = self._vae(decoded)
                    part_mesh_data = _vae_latent2mesh(
                        self._vae,
                        decoded,
                        octree_resolution=octree_res,
                        num_chunks=n_chunks,
                        mc_level=mc_level,
                        mc_algo=mc_algo,
                    )

                raw_tm = _decode_output_to_trimesh(part_mesh_data)
                part_mesh = fix_mesh(raw_tm) if raw_tm is not None else None
                if part_mesh is not None and len(part_mesh.vertices) > 0:
                    out.add_geometry(part_mesh, node_name=f"part_{batch_offset + i}")
                    if self.verbose:
                        self._log(f"    Parte {batch_offset + i}: {len(part_mesh.faces)} faces")

                del part_latent, decoded
                torch.cuda.empty_cache()

            except Exception as e:
                self._log(f"    Parte {batch_offset + i} falhou: {e}")

        del latents_cpu
        if self.cpu_offload and not self._secondary_device:
            self._log("  [C] Offloading ShapeVAE para CPU...")
            _offload_to_cpu(self._vae)

        return out

    @torch.no_grad()
    def generate(
        self,
        mesh_path: str | Path,
        aabb: np.ndarray,
        *,
        octree_resolution: int | None = None,
        num_inference_steps: int | None = None,
        guidance_scale: float = _d.DEFAULT_GUIDANCE_SCALE,
        num_chunks: int | None = None,
        mc_level: float = _d.DEFAULT_MC_LEVEL,
        mc_algo: str = _d.DEFAULT_MC_ALGO,
        seed: int = 42,
        surface_pc_size: int | None = None,
        bbox_num_points: int | None = None,
        cond_batch_size: int | None = None,
    ) -> trimesh.Scene:
        """
        Gera partes a partir de uma mesh segmentada.

        Usa CPU offloading sequencial:
        Conditioner (encode) → offload → DiT (denoise) → offload → VAE (decode)
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

        pl.seed_everything(seed, workers=True)

        mesh_path = str(mesh_path)
        mesh = trimesh.load(mesh_path, force="mesh")

        num_parts_aabb = int(aabb.shape[0])
        vram_gb = None
        if torch.cuda.is_available():
            try:
                vram_gb = float(torch.cuda.get_device_properties(0).total_memory) / (1024**3)
            except Exception:
                vram_gb = None
        if self.autotune:
            gt = autotune_generate(
                mesh, num_parts_aabb, vram_gb=vram_gb, dit_quantized=self._dit_quantized, low_vram=self.low_vram
            )
            octree_res = gt.octree_resolution if octree_resolution is None else octree_resolution
            n_steps = gt.num_inference_steps if num_inference_steps is None else num_inference_steps
            n_chunks = gt.num_chunks if num_chunks is None else num_chunks
            pc_sz = gt.surface_pc_size if surface_pc_size is None else surface_pc_size
            bbox_pts = gt.bbox_num_points if bbox_num_points is None else bbox_num_points
            cond_bs = gt.cond_batch_size if cond_batch_size is None else cond_batch_size

            # Se VRAM muito limitada, reduzir steps para acelerar (DiT vai para CPU)
            if vram_gb is not None and vram_gb < 8.0 and num_inference_steps is None and not self._dit_quantized:
                original_steps = n_steps
                n_steps = min(n_steps, 20)  # Máximo 20 steps se DiT puder ir a CPU
                if n_steps != original_steps:
                    self._log(
                        f"  [AUTOTUNE] VRAM limitada ({vram_gb:.1f} GB). Reduzindo steps: {original_steps} → {n_steps}"
                    )

            self._log(
                f"  Autotune generate: octree={octree_res} chunks={n_chunks} steps={n_steps} "
                f"cond_batch={cond_bs} (índice={gt.pressure_index}, "
                f"partes={num_parts_aabb}, geometria={gt.geometry_score:.2f})"
            )

            # Verificar se precisamos dividir em múltiplos batches devido à VRAM
            max_parts_per_batch = gt.max_parts_allowed if gt.max_parts_allowed > 0 else num_parts_aabb
        else:
            octree_res = _d.DEFAULT_OCTREE_RESOLUTION if octree_resolution is None else octree_resolution
            n_steps = _d.DEFAULT_NUM_INFERENCE_STEPS if num_inference_steps is None else num_inference_steps
            n_chunks = _d.DEFAULT_NUM_CHUNKS if num_chunks is None else num_chunks
            pc_sz = 81920 if surface_pc_size is None else surface_pc_size
            bbox_pts = 81920 if bbox_num_points is None else bbox_num_points
            cond_bs = num_parts_aabb if cond_batch_size is None else cond_batch_size
            # Calcular max_parts baseado na VRAM disponível
            vram_gb_calc = vram_gb if vram_gb else (get_vram_gb() if torch.cuda.is_available() else None)
            max_parts_calc = (
                get_max_parts_for_vram(vram_gb_calc, dit_quantized=self._dit_quantized) if vram_gb_calc else None
            )
            max_parts_per_batch = max_parts_calc if max_parts_calc else num_parts_aabb

        # Limpar memória antes de começar o processamento X-Part
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
        aabb_t = aabb_t[valid_parts_mask].unsqueeze(0)
        part_surface_inbbox = part_surface_inbbox.unsqueeze(0)

        _, num_parts, N, _ = part_surface_inbbox.shape
        self._log(f"  Partes válidas: {num_parts}, pontos/parte: {N}")

        _batch_size, num_parts, N, _dim = part_surface_inbbox.shape
        self._log(f"  Partes válidas: {num_parts}, pontos/parte: {N}")

        # Decidir se precisamos dividir em múltiplos batches
        num_batches = (num_parts + max_parts_per_batch - 1) // max_parts_per_batch
        if num_batches > 1:
            self._log(
                f"  [AUTOTUNE] Dividindo {num_parts} partes em {num_batches} batches "
                f"(máx {max_parts_per_batch} partes por batch)"
            )

        # Cena final para combinar todos os resultados
        final_scene = trimesh.Scene()
        total_generated = 0

        # Processar cada batch sequencialmente
        for batch_idx in range(num_batches):
            start_idx = batch_idx * max_parts_per_batch
            end_idx = min(start_idx + max_parts_per_batch, num_parts)
            end_idx - start_idx

            if num_batches > 1:
                self._log(f"  === Batch {batch_idx + 1}/{num_batches} (partes {start_idx}-{end_idx - 1}) ===")

            # Extrair dados deste batch
            aabb_batch = aabb_t[0, start_idx:end_idx].unsqueeze(0)  # (1, num_in_batch, 2, 3)
            part_surf_batch = part_surface_inbbox[0, start_idx:end_idx].unsqueeze(0)  # (1, num_in_batch, N, dim)

            # Processar este batch
            batch_scene = self._generate_batch(
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
                mc_algo=mc_algo,
                batch_offset=start_idx,
            )

            # Adicionar geometrias do batch à cena final
            for geom_name, geom in batch_scene.geometry.items():
                final_scene.add_geometry(geom, node_name=geom_name)
            total_generated += len(batch_scene.geometry)

            if num_batches > 1 and batch_idx < num_batches - 1:
                # Limpar memória entre batches
                torch.cuda.empty_cache()
                self._log(f"  Batch {batch_idx + 1} completo. {len(batch_scene.geometry)} partes geradas.")

        # Desnormalizar as meshes finais
        if total_generated > 0:
            self._log(f"  Desnormalizando {total_generated} partes...")
            for _name, geom in list(final_scene.geometry.items()):
                if isinstance(geom, trimesh.Trimesh):
                    geom.vertices = geom.vertices * scale + center

        self._log(f"  Total: {total_generated} partes geradas com sucesso")
        return final_scene

    # ------------------------------------------------------------------
    # Pipeline completo: segment + generate
    # ------------------------------------------------------------------

    def __call__(
        self,
        mesh_path: str | Path,
        *,
        octree_resolution: int | None = None,
        num_inference_steps: int | None = None,
        num_chunks: int | None = None,
        seed: int = 42,
        postprocess: bool = _d.DEFAULT_POSTPROCESS,
        threshold: float = _d.DEFAULT_POSTPROCESS_THRESHOLD,
        point_num: int | None = None,
        prompt_num: int | None = None,
        surface_pc_size: int | None = None,
        bbox_num_points: int | None = None,
        cond_batch_size: int | None = None,
    ) -> tuple[trimesh.Scene, np.ndarray, trimesh.Trimesh]:
        """
        Pipeline completo: segmenta e gera partes.

        Returns:
            (parts_scene, face_ids, segmented_mesh)
        """
        with profile_span("part3d_decompose", sync_cuda=True):
            mesh = trimesh.load(str(mesh_path), force="mesh", process=False)

            aabb, face_ids, clean_mesh = self.segment(
                mesh,
                postprocess=postprocess,
                threshold=threshold,
                seed=seed,
                point_num=point_num,
                prompt_num=prompt_num,
            )

            parts_scene = self.generate(
                mesh_path,
                aabb,
                octree_resolution=octree_resolution,
                num_inference_steps=num_inference_steps,
                num_chunks=num_chunks,
                seed=seed,
                surface_pc_size=surface_pc_size,
                bbox_num_points=bbox_num_points,
                cond_batch_size=cond_batch_size,
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

    xpart_dir = os.path.join(space_dir, "XPart")
    p3sam_dir = os.path.join(space_dir, "P3-SAM")

    for d in (xpart_dir, p3sam_dir, space_dir):
        if d not in sys.path and os.path.isdir(d):
            sys.path.insert(0, d)


def _ensure_xpart_code() -> str:
    """Garante que o código do Space HF está disponível localmente."""
    from huggingface_hub import snapshot_download

    space_dir = snapshot_download(
        repo_id="tencent/Hunyuan3D-Part",
        repo_type="space",
    )
    return space_dir


def _patch_space_hardcodes() -> None:
    """Corrige hardcodes do Space HF que assumem Docker / GPUs grandes.

    Aplica patches directamente nos ficheiros fonte do cache HF:
    1. P3-SAM/model.py: download_root='/root/sonata' → ~/.cache/sonata
    2. auto_mask_api.py: point_num/prompt_num hardcoded para baixa VRAM
    3. auto_mask_api.py: get_mask batch size para caber em ~6 GB
    """
    space_dir = _ensure_xpart_code()
    safe_root = os.path.join(os.path.expanduser("~"), ".cache", "sonata")

    # --- 1. Corrigir sonata download root ---
    p3sam_model = os.path.join(space_dir, "P3-SAM", "model.py")
    if os.path.isfile(p3sam_model):
        with open(p3sam_model) as f:
            content = f.read()
        if "download_root='/root/sonata'" in content:
            content = content.replace(
                "download_root='/root/sonata'",
                f"download_root='{safe_root}'",
            )
            with open(p3sam_model, "w") as f:
                f.write(content)

    # --- 2. Corrigir hardcodes de memória em auto_mask_api.py ---
    api_file = os.path.join(space_dir, "XPart", "partgen", "bbox_estimator", "auto_mask_api.py")
    if os.path.isfile(api_file):
        with open(api_file) as f:
            content = f.read()
        changed = False

        # mesh_sam() ignora os argumentos e hardcoda:
        #   point_num = 100000   → remover (usar o argumento)
        #   prompt_num = 400     → remover (usar o argumento)
        for bad_line in (
            "    point_num = 100000\n",
            "    prompt_num = 400\n",
        ):
            if bad_line in content:
                content = content.replace(bad_line, "")
                changed = True

        # get_mask() faz feats.repeat(1, batch, 1) — com 50K pontos x batch
        # prompts já consome GBs. Forçar bs=4 para caber em ~5.6 GB VRAM.
        for old_bs in ("        bs = 64\n", "        bs = 8\n"):
            if old_bs in content:
                content = content.replace(old_bs, "        bs = 4\n")
                changed = True

        if changed:
            with open(api_file, "w") as f:
                f.write(content)

    # Invalidar módulos já importados
    import sys

    for key in list(sys.modules.keys()):
        mod = sys.modules[key]
        mod_file = str(getattr(mod, "__file__", "") or "")
        if space_dir in mod_file and ("model" in key or "auto_mask" in key):
            del sys.modules[key]
