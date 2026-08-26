"""Text2Sound — núcleo de geração de áudio via difusão condicionada.

Suporta a família **Stable Audio 3 Small** (rectified-flow destilada +
T5Gemma, checkpoints dedicados por domínio) via ``stable-audio-tools``:

- ``stabilityai/stable-audio-3-small-music`` — música (default do perfil music)
- ``stabilityai/stable-audio-3-small-sfx``   — efeitos (default do perfil effects)

Os modelos Stable Audio Open (1.0 / small) continuam a funcionar como legado
(ID HF explícito / aliases ``open-1.0`` / ``open-small``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import torch
from einops import rearrange
from stable_audio_tools import get_pretrained_model
from stable_audio_tools.inference.generation import generate_diffusion_cond

from aigamekit_shared.diffusion_control import GenerationAborted

from .models import MODEL_MUSIC_ID

DEFAULT_MODEL_ID = MODEL_MUSIC_ID
DEFAULT_SAMPLER = "dpmpp-3m-sde"
DEFAULT_STEPS = 100
DEFAULT_CFG_SCALE = 7.0
DEFAULT_DURATION = 30.0
DEFAULT_SIGMA_MIN = 0.3
DEFAULT_SIGMA_MAX = 500.0

# Rectified-flow samplers (Open Small / SA3) only accept euler/rk4/dpmpp/pingpong
# and older lib versions' dpmpp path had an unbound `sigma`; euler/pingpong are
# the safe defaults. Map the k-diffusion names so dpmpp-3m-sde doesn't return
# None from sample_rf.
_RF_SAMPLER_MAP = {
    "euler": "euler",
    "rk4": "rk4",
    "dpmpp": "dpmpp",
    "pingpong": "pingpong",
}

# De-click fade aplicado no corte do buffer SA3 ao ``-d`` pedido.
_DECCLICK_FADE_SECONDS = 0.01


def _crop_to_duration_declick(audio: torch.Tensor, sample_rate: int, duration: float) -> torch.Tensor:
    """Corta ``(b, d, n)`` a ``duration`` segundos com micro-fade anti-click.

    No-op quando o tensor já é mais curto que o pedido (ex.: duration acima do
    buffer do config SA3 — 120 s). O fade de 10 ms só é aplicado quando há
    corte real: é inaudível e evita um click se o modelo ainda estiver a soar
    no limite do ``seconds_total``.
    """
    max_samples = round(duration * sample_rate)
    if audio.shape[-1] <= max_samples:
        return audio
    cropped = audio[..., :max_samples]
    fade_len = min(int(_DECCLICK_FADE_SECONDS * sample_rate), max_samples // 2)
    if fade_len > 0:
        fade = torch.linspace(1.0, 0.0, fade_len, device=cropped.device, dtype=cropped.dtype)
        cropped = cropped.clone()
        cropped[..., -fade_len:] = cropped[..., -fade_len:] * fade
    return cropped


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
    negative_prompt: str | None = None
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
        gpu_ids: list[int] | None = None,
        chunked_vae: bool | None = None,
        torch_compile: bool | None = None,
        torch_compile_mode: str = "default",
        channels_last: bool = False,
    ) -> None:
        import os

        self._model_id = model_id
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._auto_clear = auto_clear
        hw = self._detect_hw_profile()
        if half_precision is None:
            if hw is not None and self._device.startswith("cuda"):
                self._half = hw.half
            else:
                self._half = self._device.startswith("cuda") and self._should_use_half()
        else:
            self._half = half_precision
        if chunked_vae is None:
            self._chunked_vae = hw.chunked_vae if hw is not None else False
        else:
            self._chunked_vae = chunked_vae
        if torch_compile is None:
            self._torch_compile = os.environ.get("AIGAMEKIT_TORCH_COMPILE", "0").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        else:
            self._torch_compile = bool(torch_compile)
        self._torch_compile_mode = torch_compile_mode or "default"
        self._channels_last = bool(channels_last)
        self._gpu_ids = gpu_ids
        self._multi_gpu: bool = False
        self._model: Any = None
        self._model_config: dict[str, Any] = {}
        self._loaded = False
        self._cache_key: tuple[Any, ...] | None = None
        self._placement_offload: str = "none"

    @staticmethod
    def _detect_hw_profile() -> Any:
        """Perfil hw-auto (None se desligado via TEXT2SOUND_HW_AUTO=0 ou indisponível)."""
        try:
            from .hardware import detect_hardware_profile, hw_auto_enabled

            if not hw_auto_enabled():
                return None
            return detect_hardware_profile()
        except Exception:
            return None

    @staticmethod
    def _should_use_half() -> bool:
        """Ativa float16 automaticamente em GPUs com <= 8 GB de VRAM."""
        if not torch.cuda.is_available():
            return False
        try:
            vram = torch.cuda.get_device_properties(0).total_memory
            return vram < 8.5 * (1024**3)
        except Exception:
            return False

    @classmethod
    def get_instance(
        cls,
        model_id: str = DEFAULT_MODEL_ID,
        device: str | None = None,
        half_precision: bool | None = None,
        gpu_ids: list[int] | None = None,
        chunked_vae: bool | None = None,
        torch_compile: bool | None = None,
        torch_compile_mode: str = "default",
        channels_last: bool = False,
    ) -> AudioGenerator:
        """Singleton thread-safe — reutiliza modelo já carregado."""
        with cls._lock:
            _cache_key = (
                model_id,
                half_precision,
                tuple(gpu_ids) if gpu_ids else None,
                chunked_vae,
                torch_compile,
                torch_compile_mode,
                channels_last,
            )
            if cls._instance is None or cls._instance._cache_key != _cache_key:
                cls._instance = cls(
                    model_id=model_id,
                    device=device,
                    half_precision=half_precision,
                    gpu_ids=gpu_ids,
                    chunked_vae=chunked_vae,
                    torch_compile=torch_compile,
                    torch_compile_mode=torch_compile_mode,
                    channels_last=channels_last,
                )
                cls._instance._cache_key = _cache_key
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

    def _is_sa3(self) -> bool:
        """True para checkpoints Stable Audio 3 (``diffusion_cond_inpaint``).

        Estes modelos geram via ``generate_diffusion_cond_inpaint`` com
        duração adaptada ao ``seconds_total`` (variable-length); os Open usam
        o caminho clássico ``generate_diffusion_cond``.
        """
        return self._model_config.get("model_type") == "diffusion_cond_inpaint"

    def _conditioning_keys(self) -> set[str] | None:
        """IDs que o conditioner espera no dict de conditioning.

        SA3 (T5Gemma) regista só ``prompt``/``seconds_total``; os modelos Open
        têm também ``seconds_start``. ``None`` quando o modelo ainda não está
        carregado ou a introspeção falha — callers assumem o formato Open
        (chaves extra são ignoradas pelo MultiConditioner; em falta é erro).
        """
        try:
            return set(self._model.conditioner.conditioners.keys())  # type: ignore[union-attr]
        except AttributeError:
            return None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.load()

    def _preflight_download(self) -> None:
        """Garante o checkpoint em disco antes do load (download com resume/progresso).

        Best-effort: se falhar, ``get_pretrained_model`` faz o download como antes.
        """
        try:
            from aigamekit_shared.model_download import ensure_model

            ensure_model(self._model_id)
        except Exception:
            pass

    def load(self) -> None:
        """Carrega o modelo pré-treinado para o device configurado."""
        if self._loaded:
            return

        try:
            from aigamekit_shared.env import ensure_pytorch_cuda_alloc_conf

            ensure_pytorch_cuda_alloc_conf()
        except ImportError:
            pass

        self._preflight_download()
        self._model, self._model_config = get_pretrained_model(self._model_id)
        # Stable Audio Open Small declares diffusion_objective='rf_denoiser'
        # (rectified flow), but stable_audio_tools' generation loop only
        # recognises 'v' / 'rectified_flow' and leaves its `sampled` variable
        # unbound otherwise (UnboundLocalError mid-diffusion). Normalise here so
        # the sampler picks the rectified-flow path.
        if getattr(self._model, "diffusion_objective", None) == "rf_denoiser":
            self._model.diffusion_objective = "rectified_flow"
        if self._half:
            self._model = self._model.half()
            # Stable Audio VAE/pretransform NaNs in fp16; keep it in fp32.
            if hasattr(self._model, "pretransform"):
                self._model.pretransform.float()
        # Chunked VAE decode: peak activation memory stays ~constant with
        # duration (the full-latent fp32 decode is what OOMed 6 GB GPUs).
        if (
            self._chunked_vae
            and getattr(self._model, "pretransform", None) is not None
            and hasattr(self._model.pretransform, "chunked")
        ):
            self._model.pretransform.chunked = True

        # VAE do stable-audio-tools: ~74 Conv com weight_norm. Sem fundir, cada
        # decode reescreve ``module.weight`` (Tensor novo) e o allocator
        # acumula ~34 MiB NVML/run — ver ``_fuse_weight_norm``.
        self._fuse_weight_norm(self._model)

        # Colocação unificada via planner: decide multi-GPU (accelerate) / group
        # offload / full-GPU conforme VRAM livre. Footprint do registry centralizado.
        # (Antes o modelo ia para .to(device) primeiro — OOM prematuro em GPUs pequenas.)
        if self._device.startswith("cuda"):
            from aigamekit_shared.hardware import cuda_gpu_free_specs
            from aigamekit_shared.lowvram import get_footprint, place_pipeline

            specs = cuda_gpu_free_specs()
            if self._gpu_ids:
                keep = set(self._gpu_ids)
                specs = [s for s in specs if s[0] in keep]
            allow_multi = self._gpu_ids is None or len(self._gpu_ids) >= 2
            plan = place_pipeline(
                self._model,
                get_footprint("stable-audio-open"),
                specs,
                allow_quant=("none",),
                allow_multi_gpu=allow_multi,
                no_split_classes=["DiTBlock", "AudioDiTBlock"],
            )
            self._placement_offload = getattr(plan, "offload", "none") or "none"
            if plan.multi_gpu_ids is not None:
                primary = plan.primary_gpu or 0
                self._device = f"cuda:{primary}"
                self._multi_gpu = True

            # Guard de placement: planos de offload que o ``apply_offload_plan``
            # não suporta nesta pipeline (ex. group_stream em stable-audio, que
            # não é diffusers) ficam NO-OP — o modelo permanece na CPU enquanto
            # o generate correria em cuda → "Expected all tensors to be on the
            # same device". Verificar onde os pesos ficaram e, se não chegaram
            # à GPU, degradar honestamente para geração em CPU (o SA3 small de
            # 0.6B é viável em CPU; medido: ocorre quando um batch vizinho
            # segura a VRAM no reload pós idle-evict).
            try:
                first_device = next(self._model.parameters()).device
            except (StopIteration, AttributeError):
                first_device = None
            if first_device is not None and first_device.type != "cuda":
                if self._half:
                    self._model = self._model.float()
                    self._half = False
                self._device = "cpu"
                self._placement_offload = "none"
                self._multi_gpu = False
        else:
            self._model = self._model.to(self._device)

        self._apply_kernel_opts()
        self._loaded = True

    def _apply_kernel_opts(self) -> None:
        """torch.compile no DiT + channels_last no pretransform (VAE) se pedido."""
        if self._device == "cpu" or self._model is None:
            return
        offload = self._placement_offload
        if self._torch_compile:
            if offload in ("model_cpu", "sequential_cpu"):
                pass  # ping-pong de device — skip
            else:
                from aigamekit_shared.quantization import apply_torch_compile, resolve_torch_compile_mode

                requested = self._torch_compile_mode
                mode = resolve_torch_compile_mode(
                    requested,
                    offload=offload,
                    group_offload_active=(offload == "group_stream"),
                )
                # ConditionerDiT wrapper: pesos de difusão em ``.model``.
                target = getattr(self._model, "model", None) or self._model
                compiled = apply_torch_compile(
                    target,
                    mode=mode,
                    offload=offload,
                    group_offload_active=(offload == "group_stream"),
                )
                if compiled is not target:
                    if getattr(self._model, "model", None) is not None:
                        self._model.model = compiled
                    else:
                        self._model = compiled

        if self._channels_last:
            from aigamekit_shared.quantization import apply_channels_last

            pre = getattr(self._model, "pretransform", None)
            if pre is not None:
                apply_channels_last(pre)

    def _try_multi_gpu(self) -> None:
        """Descontinuado — multi-GPU agora tratado por place_pipeline no load()."""
        pass

    @staticmethod
    def _fuse_weight_norm(root: Any) -> int:
        """Fundir ``weight_norm`` em Parameters estáticos (inferência).

        O VAE do ``stable-audio-tools`` aplica ``torch.nn.utils.weight_norm`` em
        dezenas de ``Conv1d`` / ``ConvTranspose1d``. O hook pré-forward
        reescreve ``module.weight`` com um Tensor novo a cada decode
        (``weight_v`` * ``weight_g``). Em inferência isso:

        1. mantém uma cópia extra dos pesos enquanto o decode corre;
        2. provoca churn de alocações que o caching allocator CUDA (com
           ``expandable_segments:True``) **não devolve** ao driver —
           medido com ``ums calibrate`` / NVML: residente +34 MiB por
           geração após as correcções de ``empty_cache``.

        Fundir uma vez no ``load`` remove o hook e congela o peso. Seguro
        porque Text2Sound nunca treina este modelo.

        Args:
            root: Módulo raiz (tipicamente o modelo Stable Audio completo).

        Returns:
            Número de hooks ``WeightNorm`` fundidos.
        """
        from torch.nn.utils import remove_weight_norm

        fused = 0
        for mod in list(root.modules()):
            for hook in list(mod._forward_pre_hooks.values()):
                if type(hook).__name__ != "WeightNorm":
                    continue
                try:
                    remove_weight_norm(mod, name=hook.name)
                except (ValueError, AttributeError):
                    continue
                fused += 1
        return fused

    def _decode_latents(self, latents: torch.Tensor) -> torch.Tensor:
        """Decodifica latents → áudio com escada de fallback de memória.

        1. Decode normal (respeita ``pretransform.chunked`` do perfil hw).
        2. OOM → re-tenta com decode chunked.
        3. OOM → decode em CPU (lento mas nunca falha por VRAM).
        """
        pre = self._model.pretransform
        dtype = next(pre.parameters()).dtype
        latents = latents.to(dtype)

        try:
            with torch.no_grad():
                return pre.decode(latents)
        except torch.cuda.OutOfMemoryError:
            self._clear_cuda()

        if hasattr(pre, "chunked") and not pre.chunked:
            pre.chunked = True
            try:
                with torch.no_grad():
                    return pre.decode(latents)
            except torch.cuda.OutOfMemoryError:
                self._clear_cuda()

        device = next(pre.parameters()).device
        try:
            pre.to("cpu")
            with torch.no_grad():
                return pre.decode(latents.cpu()).to(latents.device)
        finally:
            pre.to(device)

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
        if self._device.startswith("cuda") and torch.cuda.is_available():
            try:
                from aigamekit_shared.gpu import clear_cuda_memory

                clear_cuda_memory()
            except ImportError:
                torch.cuda.empty_cache()

    @contextmanager
    def _generation_context(self) -> Generator[None, None, None]:
        """Context manager que limpa VRAM após geração se auto_clear=True."""
        try:
            yield
        finally:
            if self._auto_clear and self._device.startswith("cuda"):
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
        prompt_hints: list[str] | None = None,
        negative_prompt: str | None = None,
        should_abort: Callable[[], bool] | None = None,
        on_step: Callable[[int, int], None] | None = None,
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
            prompt_hints: Hints adicionais para enriquecer o prompt (ex.: "seamless loop").
            negative_prompt: Negative prompt (anti-guidance). Steering away from
                described concepts via batch CFG. None/empty = sem negative prompt
                (comportamento clássico do modelo).
            should_abort: Se devolver True, aborta no próximo step do sampler (vramd cancel).
            on_step: ``(step_1based, total) -> None`` para progresso vramd.

        Returns:
            GenerationResult com tensor de áudio raw (float32, 2 canais).

        Raises:
            GenerationAborted: Cancel cooperativo a meio da difusão.
        """
        self._ensure_loaded()

        if should_abort is not None and should_abort():
            raise GenerationAborted("cancelled before diffusion")

        if seed is not None:
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)

        final_prompt = prompt
        if prompt_hints:
            final_prompt = prompt + ". " + ", ".join(prompt_hints)

        # SA3 só condiciona em prompt + seconds_total (sem seconds_start);
        # os modelos Open usam ambos. Introspeção do conditioner decide.
        cond_keys = self._conditioning_keys()
        cond: dict[str, Any] = {"prompt": final_prompt, "seconds_total": duration}
        if cond_keys is None or "seconds_start" in cond_keys:
            cond["seconds_start"] = 0
        conditioning = [cond]

        # Negative conditioning: same dict shape as the positive one. Passed
        # straight to generate_diffusion_cond which applies batch CFG. Omit
        # entirely when there's no negative prompt to preserve the classic path.
        # Com cfg_scale <= 1.0 (default SA3) o guidance é no-op matemático —
        # passar o negative só duplicaria o compute sem alterar o resultado.
        negative_conditioning: list[dict[str, Any]] | None = None
        effective_negative: str | None = None
        if negative_prompt and negative_prompt.strip() and cfg_scale > 1.0:
            effective_negative = negative_prompt.strip()
            neg_cond: dict[str, Any] = {"prompt": effective_negative, "seconds_total": duration}
            if cond_keys is None or "seconds_start" in cond_keys:
                neg_cond["seconds_start"] = 0
            negative_conditioning = [neg_cond]

        with self._generation_context():
            gen_device = f"cuda:{self._gpu_ids[0]}" if self._multi_gpu and self._gpu_ids else self._device
            rf_sampler = sampler_type
            if getattr(self._model, "diffusion_objective", "") in ("rectified_flow", "rf_denoiser"):
                rf_sampler = _RF_SAMPLER_MAP.get(sampler_type, "euler")
            has_pretransform = getattr(self._model, "pretransform", None) is not None
            # A lib re-semeia internamente (seed=-1 → aleatório); passar a seed
            # explicitamente é o que torna o --seed determinístico de facto.
            lib_seed = int(seed) if seed is not None else -1
            gen_kwargs: dict[str, Any] = dict(
                steps=steps,
                cfg_scale=cfg_scale,
                conditioning=conditioning,
                sample_size=self.sample_size,
                sampler_type=rf_sampler,
                device=gen_device,
                seed=lib_seed,
                # Latents out + decode próprio: o decode do VAE é o passo que
                # OOMa em GPUs pequenas; separado, dá para re-tentar chunked ou
                # em CPU sem repetir a difusão inteira.
                return_latents=has_pretransform,
            )
            if negative_conditioning is not None:
                gen_kwargs["negative_conditioning"] = negative_conditioning
            if should_abort is not None or on_step is not None:
                total_steps = max(1, int(steps))

                def _sampler_callback(info: dict[str, Any]) -> None:
                    # k-diffusion / sample_rf passam dict com 'i' (0-based ou 1-based).
                    raw_i = int(info.get("i", 0))
                    cur = raw_i if raw_i >= 1 else raw_i + 1
                    cur = min(max(cur, 1), total_steps)
                    if on_step is not None:
                        on_step(cur, total_steps)
                    if should_abort is not None and should_abort():
                        raise GenerationAborted("cancelled during diffusion")

                gen_kwargs["callback"] = _sampler_callback

            if self._is_sa3():
                # SA3 = modelo de inpainting: o caminho canónico
                # (generate_diffusion_cond_inpaint) adapta o tamanho do latent a
                # seconds_total (+6 s de headroom) em vez de difundir o buffer
                # inteiro do config (120 s), e aplica o schedule com
                # effective-length/dist_shift do treino. Sem init_audio é geração
                # pura. Nota: NÃO passar sigma_min/sigma_max aqui — neste caminho
                # keywords extra vazam para o forward do modelo.
                from stable_audio_tools.inference.generation import generate_diffusion_cond_inpaint

                output = generate_diffusion_cond_inpaint(self._model, **gen_kwargs)
            else:
                gen_kwargs["sigma_min"] = sigma_min
                gen_kwargs["sigma_max"] = sigma_max
                output = generate_diffusion_cond(self._model, **gen_kwargs)
            if has_pretransform:
                if should_abort is not None and should_abort():
                    raise GenerationAborted("cancelled before decode")
                output = self._decode_latents(output)

            if self._is_sa3():
                # SA3 difunde seconds_total + headroom (~6 s de padding que o
                # treino usa para o schedule/atenção); o conteúdo musical acaba
                # em seconds_total e a cauda é silêncio de padding. Cortar ao
                # pedido devolve o contrato "-d = duração do clip" (sem isto,
                # um -d 15 produzia ~21 s com branco no fim, e o crossfade de
                # seamless-loop operava sobre o buffer inteiro).
                output = _crop_to_duration_declick(output, self.sample_rate, duration)

            # Áudio para CPU e tensor CUDA largado **dentro** do contexto: o
            # ``_generation_context`` limpa a cache no ``finally``, e enquanto
            # ``output`` estivesse vivo o ``empty_cache`` não devolveria esses
            # blocos ao driver — era assim que o residente crescia ~42 MiB por
            # geração (medido com `ums calibrate`). Todos os consumidores só
            # gravam o áudio, e o ``save_audio`` já fazia ``.cpu()``.
            audio = rearrange(output, "b d n -> d (b n)").detach().to("cpu")
            del output

        return GenerationResult(
            audio=audio,
            sample_rate=self.sample_rate,
            prompt=final_prompt,
            duration=duration,
            steps=steps,
            cfg_scale=cfg_scale,
            seed=seed,
            sampler=sampler_type,
            sigma_min=sigma_min,
            sigma_max=sigma_max,
            device=self._device,
            negative_prompt=effective_negative,
        )
