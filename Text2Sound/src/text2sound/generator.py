"""Text2Sound — núcleo de geração de áudio via difusão condicionada.

Suporta ``stabilityai/stable-audio-open-1.0`` (música, até ~47s) e
``stabilityai/stable-audio-open-small`` (efeitos, até ~11s), ambos via
``stable-audio-tools`` e ``get_pretrained_model``.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import torch
from einops import rearrange
from stable_audio_tools import get_pretrained_model
from stable_audio_tools.inference.generation import generate_diffusion_cond

from .models import MODEL_MUSIC_ID

DEFAULT_MODEL_ID = MODEL_MUSIC_ID
DEFAULT_SAMPLER = "dpmpp-3m-sde"
DEFAULT_STEPS = 100
DEFAULT_CFG_SCALE = 7.0
DEFAULT_DURATION = 30.0
DEFAULT_SIGMA_MIN = 0.3
DEFAULT_SIGMA_MAX = 500.0


@dataclass
class GenerationResult:
    """Resultado de uma geração de áudio."""

    audio: torch.Tensor
    sample_rate: int
    prompt: str
    duration: float
    steps: int
    cfg_scale: float
    seed: int | None
    sampler: str
    sigma_min: float
    sigma_max: float
    device: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AudioGenerator:
    """Gerador de áudio text-to-sound com cache de modelo singleton.

    O modelo é carregado uma única vez e reutilizado entre gerações.
    A VRAM é limpa automaticamente após cada geração quando ``auto_clear=True``.
    """

    _instance: AudioGenerator | None = None
    _lock = threading.Lock()

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        device: str | None = None,
        auto_clear: bool = True,
        half_precision: bool | None = None,
        low_vram: bool = False,
        gpu_ids: list[int] | None = None,
    ) -> None:
        self._model_id = model_id
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._auto_clear = auto_clear
        if half_precision is None:
            self._half = self._device == "cuda" and low_vram and self._should_use_half()
        else:
            self._half = half_precision
        self._gpu_ids = gpu_ids
        self._multi_gpu: bool = False
        self._model: Any = None
        self._model_config: dict[str, Any] = {}
        self._loaded = False

    @staticmethod
    def _should_use_half() -> bool:
        """Ativa float16 automaticamente em GPUs com <= 8 GB de VRAM."""
        if not torch.cuda.is_available():
            return False
        try:
            vram = torch.cuda.get_device_properties(0).total_mem
            return vram < 8.5 * (1024**3)
        except Exception:
            return True

    @classmethod
    def get_instance(
        cls,
        model_id: str = DEFAULT_MODEL_ID,
        device: str | None = None,
        half_precision: bool | None = None,
        low_vram: bool = False,
        gpu_ids: list[int] | None = None,
    ) -> AudioGenerator:
        """Singleton thread-safe — reutiliza modelo já carregado."""
        with cls._lock:
            if cls._instance is None or cls._instance._model_id != model_id:
                cls._instance = cls(
                    model_id=model_id,
                    device=device,
                    half_precision=half_precision,
                    low_vram=low_vram,
                    gpu_ids=gpu_ids,
                )
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Libera singleton e VRAM associada."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.unload()
                cls._instance = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def device(self) -> str:
        return self._device

    @property
    def half_precision(self) -> bool:
        """True se o modelo está em float16 (manual ou heurística VRAM)."""
        return self._half

    @property
    def sample_rate(self) -> int:
        self._ensure_loaded()
        return int(self._model_config["sample_rate"])

    @property
    def sample_size(self) -> int:
        self._ensure_loaded()
        return int(self._model_config["sample_size"])

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.load()

    def load(self) -> None:
        """Carrega o modelo pré-treinado para o device configurado."""
        if self._loaded:
            return

        try:
            from gamedev_shared.env import ensure_pytorch_cuda_alloc_conf

            ensure_pytorch_cuda_alloc_conf()
        except ImportError:
            pass

        self._model, self._model_config = get_pretrained_model(self._model_id)
        if self._half:
            self._model = self._model.half()
        self._model = self._model.to(self._device)

        if self._gpu_ids and len(self._gpu_ids) >= 2 and self._device == "cuda":
            self._try_multi_gpu()

        self._loaded = True

    def _try_multi_gpu(self) -> None:
        """Tenta dispatch multi-GPU via accelerate (MultiGPUPlanner)."""
        try:
            from gamedev_shared.multi_gpu import MultiGPUPlanner

            planner = (
                MultiGPUPlanner()
                .for_model(self._model)
                .with_gpus(self._gpu_ids)  # type: ignore[arg-type]
                .no_split(["DiTBlock", "AudioDiTBlock"])
            )
            plan = planner.plan()
            if plan.status == "multi_gpu":
                self._model = planner.apply()
                primary = plan.primary_device
                self._device = f"cuda:{primary}" if isinstance(primary, int) else primary
                self._multi_gpu = True
        except Exception:
            pass

    def unload(self) -> None:
        """Descarrega modelo e libera VRAM."""
        if not self._loaded:
            return
        del self._model
        self._model = None
        self._model_config = {}
        self._loaded = False
        self._clear_cuda()

    def _clear_cuda(self) -> None:
        if self._device == "cuda" and torch.cuda.is_available():
            try:
                from gamedev_shared.gpu import clear_cuda_memory

                clear_cuda_memory()
            except ImportError:
                torch.cuda.empty_cache()

    @contextmanager
    def _generation_context(self) -> Generator[None, None, None]:
        """Context manager que limpa VRAM após geração se auto_clear=True."""
        try:
            yield
        finally:
            if self._auto_clear and self._device == "cuda":
                self._clear_cuda()

    def generate(
        self,
        prompt: str,
        duration: float = DEFAULT_DURATION,
        steps: int = DEFAULT_STEPS,
        cfg_scale: float = DEFAULT_CFG_SCALE,
        seed: int | None = None,
        sigma_min: float = DEFAULT_SIGMA_MIN,
        sigma_max: float = DEFAULT_SIGMA_MAX,
        sampler_type: str = DEFAULT_SAMPLER,
    ) -> GenerationResult:
        """Gera áudio estéreo a partir de um prompt de texto.

        Args:
            prompt: Descrição textual do áudio desejado.
            duration: Duração em segundos (limite máximo depende do modelo; ver ``ModelSpec`` / CLI).
            steps: Passos de difusão (mais = melhor qualidade, mais lento).
            cfg_scale: Classifier-free guidance scale.
            seed: Seed para reprodutibilidade (None = aleatório).
            sigma_min: Mínimo do noise schedule.
            sigma_max: Máximo do noise schedule.
            sampler_type: Tipo de sampler (dpmpp-3m-sde, etc.).

        Returns:
            GenerationResult com tensor de áudio raw (float32, 2 canais).
        """
        self._ensure_loaded()

        if seed is not None:
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)

        conditioning = [
            {
                "prompt": prompt,
                "seconds_start": 0,
                "seconds_total": duration,
            }
        ]

        with self._generation_context():
            gen_device = f"cuda:{self._gpu_ids[0]}" if self._multi_gpu and self._gpu_ids else self._device
            output = generate_diffusion_cond(
                self._model,
                steps=steps,
                cfg_scale=cfg_scale,
                conditioning=conditioning,
                sample_size=self.sample_size,
                sigma_min=sigma_min,
                sigma_max=sigma_max,
                sampler_type=sampler_type,
                device=gen_device,
            )

        audio = rearrange(output, "b d n -> d (b n)")

        return GenerationResult(
            audio=audio,
            sample_rate=self.sample_rate,
            prompt=prompt,
            duration=duration,
            steps=steps,
            cfg_scale=cfg_scale,
            seed=seed,
            sampler=sampler_type,
            sigma_min=sigma_min,
            sigma_max=sigma_max,
            device=self._device,
        )
