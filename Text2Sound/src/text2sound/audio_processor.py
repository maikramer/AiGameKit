"""Text2Sound — processamento e exportação de áudio.

Normalização de pico, conversão de formatos e remoção de silêncio no início e no fim.
Usa soundfile (libsndfile) para escrita — portável, sem dependência de CUDA.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch

SUPPORTED_FORMATS = ("wav", "flac", "ogg")
DEFAULT_FORMAT = "ogg"

_SF_SUBTYPES = {
    "wav": "PCM_16",
    "flac": "PCM_16",
    "ogg": "VORBIS",
}


def peak_normalize(audio: torch.Tensor) -> torch.Tensor:
    """Normaliza áudio pelo valor de pico para a gama [-1, 1]."""
    peak = torch.max(torch.abs(audio))
    if peak > 0:
        audio = audio / peak
    return audio.clamp(-1, 1)


def to_int16(audio: torch.Tensor) -> torch.Tensor:
    """Converte tensor float [-1, 1] para int16."""
    return audio.to(torch.float32).clamp(-1, 1).mul(32767).to(torch.int16)


def trim_silence(
    audio: torch.Tensor,
    sample_rate: int,
    threshold_db: float = -60.0,
    buffer_ms: int = 200,
) -> torch.Tensor:
    """Remove silêncio no início e no fim do áudio.

    Localiza o primeiro e o último sample acima do limiar (mono = max por canal)
    e corta o sinal, mantendo um pequeno buffer em cada extremo (fade natural).

    Args:
        audio: Tensor (channels, samples) float.
        sample_rate: Taxa de amostragem.
        threshold_db: Limiar em dB abaixo do qual se considera silêncio.
        buffer_ms: Buffer mínimo (ms) antes do primeiro som e após o último.
    """
    threshold_linear = 10 ** (threshold_db / 20.0)
    mono = audio.abs().max(dim=0).values

    above_threshold = torch.nonzero(mono > threshold_linear, as_tuple=True)[0]
    if len(above_threshold) == 0:
        return audio

    first_sound = above_threshold[0].item()
    last_sound = above_threshold[-1].item()
    buffer_samples = int(sample_rate * buffer_ms / 1000)

    start_idx = max(0, first_sound - buffer_samples)
    end_idx = min(last_sound + buffer_samples, audio.shape[-1])

    if start_idx >= end_idx:
        return audio

    return audio[:, start_idx:end_idx]


def crop_to_duration(
    audio: torch.Tensor,
    sample_rate: int,
    crop_seconds: float,
    fade_out_seconds: float = 0.06,
) -> torch.Tensor:
    """Hard-truncate to ``crop_seconds`` with an optional linear fade-out.

    Stable Audio models emit a fixed-length buffer regardless of the requested
    duration; ``--trim`` only strips silence (the model rarely produces any at
    the tail). This truncates to the gameplay-relevant length the caller asked
    for, then fades the last ``fade_out_seconds`` to avoid a click at the cut.

    Args:
        audio: Tensor (channels, samples).
        sample_rate: Sample rate in Hz.
        crop_seconds: Target length in seconds; shorter audio is returned as-is.
        fade_out_seconds: Linear fade-out applied to the tail (0 = hard cut).
    """
    max_samples = int(crop_seconds * sample_rate)
    if audio.shape[-1] <= max_samples:
        return audio
    cropped = audio[:, :max_samples].clone()
    if fade_out_seconds > 0:
        fade_n = min(int(fade_out_seconds * sample_rate), max_samples // 2)
        if fade_n > 1:
            curve = torch.linspace(1.0, 0.0, fade_n, device=cropped.device, dtype=cropped.dtype)
            cropped[:, -fade_n:] = cropped[:, -fade_n:] * curve
    return cropped


def apply_edge_fade(
    audio: torch.Tensor,
    sample_rate: int,
    fade_in_ms: float = 5,
    fade_out_ms: float = 20,
) -> torch.Tensor:
    """Micro fade-in/out to eliminate clicks at clip boundaries.

    Applies very short linear fades at the start and end of the audio
    tensor to prevent audible clicks from abrupt start/stop.

    Args:
        audio: Tensor (channels, samples) float, modified in-place if possible.
        sample_rate: Taxa de amostragem.
        fade_in_ms: Fade-in duration in milliseconds.
        fade_out_ms: Fade-out duration in milliseconds.

    Returns:
        Tensor with fades applied (channels, samples).
    """
    if audio.shape[-1] == 0:
        return audio

    fade_in_samples = max(1, int(sample_rate * fade_in_ms / 1000))
    fade_out_samples = max(1, int(sample_rate * fade_out_ms / 1000))
    fade_in_samples = min(fade_in_samples, audio.shape[-1] // 2)
    fade_out_samples = min(fade_out_samples, audio.shape[-1] // 2)

    result = audio.clone()

    if fade_in_samples > 1:
        fade_in_curve = torch.linspace(0.0, 1.0, fade_in_samples, device=audio.device, dtype=audio.dtype)
        result[:, :fade_in_samples] = result[:, :fade_in_samples] * fade_in_curve

    if fade_out_samples > 1:
        fade_out_curve = torch.linspace(1.0, 0.0, fade_out_samples, device=audio.device, dtype=audio.dtype)
        result[:, -fade_out_samples:] = result[:, -fade_out_samples:] * fade_out_curve

    return result


def apply_seamless_loop_crossfade(
    audio: torch.Tensor,
    sample_rate: int,
    crossfade_ms: float = 500.0,
) -> torch.Tensor:
    """Apply equal-power crossfade between the end and start of audio for seamless looping.

    Blends the last ``crossfade_ms`` milliseconds with the first ``crossfade_ms``
    milliseconds, then **drops the head** that was folded into the tail: the
    loop region is ``audio[n:]``. At the wrap point the blended tail has
    converged onto ``audio[n-1]`` and playback restarts at ``audio[n]`` —
    sample-continuous.

    As curvas são ``cos``/``sin`` em **amplitude** (equal-power): para material
    não-correlacionado (head e tail de um loop são trechos musicais distintos,
    corr ≈ 0.05 medido) a potência soma ``cos² + sin² = 1`` — constante. As
    curvas ``cos²/sin²`` anteriores só preservam RMS para material
    perfeitamente coerente; com material não-correlacionado a potência soma
    ``cos⁴ + sin⁴`` → dip de -3 dB no meio da costura (medido: -12.9% de dip
    com cos²/sin² vs +3.6% de bump suave com cos/sin).

    Keeping the original length instead (the previous behaviour) is audibly
    wrong: the first ``n`` samples play once at the start *and again* inside
    the blended tail, and the wrap jumps from ``≈audio[n-1]`` back to
    ``audio[0]`` — a stutter plus a discontinuity every cycle.

    Args:
        audio: Tensor (channels, samples) float.
        sample_rate: Sample rate in Hz.
        crossfade_ms: Crossfade duration in milliseconds.

    Returns:
        Tensor (channels, samples - n) ready for native gapless looping.
    """
    total_samples = audio.shape[-1]
    if total_samples == 0:
        return audio

    n = int(sample_rate * crossfade_ms / 1000)
    # Clamp crossfade to at most half the audio length
    n = min(n, total_samples // 2)
    if n < 2:
        return audio.clone()

    t = torch.linspace(0, torch.pi / 2, n, device=audio.device, dtype=audio.dtype)
    fade_out = torch.cos(t)  # (n,)
    fade_in = torch.sin(t)  # (n,)

    tail = audio[:, -n:]  # last n samples
    head = audio[:, :n]  # first n samples

    # Broadcast: (channels, n) * (n,) → (channels, n)
    crossfaded = tail * fade_out + head * fade_in

    result = audio[:, n:].clone()
    result[:, -n:] = crossfaded
    return result


def seamless_generation_duration(
    duration: float,
    crossfade_ms: float,
    loop_edge_trim_s: float,
) -> float:
    """Duração a **gerar** para o loop final medir exactamente ``duration``.

    ``save_audio`` remove ``2x loop_edge_trim_s`` (intro/outro musicais) e o
    fold do crossfade consome ``crossfade_ms`` (loop = gerado - 2·edge - xf).
    Inverter a conta devolve o comprimento de geração que aterra em ``-d``
    exacto — essencial para loops alinhados a compassos (ex.: 16 s = 8
    compassos a 120 BPM; gerar só 16 s deixaria o loop em 15,5 s = 31 beats,
    off-grid, deslocando o groove meio compasso por ciclo).
    """
    return float(duration) + float(crossfade_ms) / 1000.0 + 2.0 * max(0.0, float(loop_edge_trim_s))


# Fold mínimo ao calcular o crossfade dinâmico (protege contra trims excessivos).
_MIN_LOOP_FOLD_SECONDS = 0.15

# Janela/limiar do tail trim adaptativo. Piso 75% (não 85%: a flutuação
# natural de música orquestral é ±20% por janela; 85% parava em swells
# dentro do decaimento). K janelas consecutivas acima do piso para parar —
# um swell isolado dentro do outro não pode travar o corte.
_ADAPTIVE_WINDOW_S = 0.25
_ADAPTIVE_FLOOR_RATIO = 0.75
_ADAPTIVE_CONSECUTIVE_OK = 3


def _adaptive_tail_trim_samples(
    audio: torch.Tensor,
    sample_rate: int,
    min_samples: int,
    max_samples: int,
    *,
    window_s: float = _ADAPTIVE_WINDOW_S,
    floor_ratio: float = _ADAPTIVE_FLOOR_RATIO,
    consecutive_ok: int = _ADAPTIVE_CONSECUTIVE_OK,
) -> int:
    """Samples a cortar na **cauda** até o material ficar em steady-state.

    O modelo condicionado por ``seconds_total`` compõe um other/outro (fade
    musical) no fim da geração; um corte fixo deixa escapar material em
    decaimento que no loop vira um dip de energia periódico (medido: cauda
    17-50% abaixo do corpo em seeds com outro profundo). Avança janelas de
    RMS a partir de ``min_samples`` enquanto a energia está abaixo de
    ``floor_ratio`` x mediana — e só para após ``consecutive_ok`` janelas
    seguidas acima do piso — com clamp a ``max_samples``.
    """
    if max_samples <= min_samples:
        return min(min_samples, max_samples)
    mono = audio.abs().max(dim=0).values
    n = mono.shape[-1]
    w = max(1, int(window_s * sample_rate))
    med = mono.median()
    floor = max(float(med) * floor_ratio, 1e-6)
    pos = min_samples
    streak = 0
    while pos < max_samples:
        seg = mono[n - pos - w : n - pos]
        if seg.numel() == 0:
            break
        if float(seg.mean()) >= floor:
            streak += 1
            if streak >= consecutive_ok:
                break
        else:
            streak = 0
        pos += w
    return min(pos, max_samples)


def _shape_seamless_loop_exact(
    audio: torch.Tensor,
    sample_rate: int,
    *,
    loop_edge_trim_s: float,
    crossfade_ms: float,
    target_seconds: float,
) -> torch.Tensor:
    """Edge trim (cauda adaptativa) + fold que aterra em ``target_seconds``.

    O corte de cabeça é fixo (intros curtas); o de cauda é adaptativo por
    energia. O fold do crossfade absorve a diferença: ``fold = len - target``
    (clampado a ≥ 50 ms), garantindo comprimento final exacto mesmo quando a
    cauda adaptativa come mais do que o mínimo.
    """
    target_n = round(target_seconds * sample_rate)
    total = audio.shape[-1]
    min_fold = max(int(_MIN_LOOP_FOLD_SECONDS * sample_rate), 2)

    head_edge = int(loop_edge_trim_s * sample_rate) if loop_edge_trim_s > 0 else 0
    # Orçamento da cauda: manter pelo menos target + fold mínimo após os cortes.
    max_tail = total - head_edge - target_n - min_fold
    tail_edge = head_edge
    if loop_edge_trim_s > 0 and max_tail > head_edge:
        tail_edge = _adaptive_tail_trim_samples(audio, sample_rate, head_edge, max_tail)
    elif loop_edge_trim_s > 0 and max_tail > 0:
        tail_edge = min(head_edge, max_tail)

    if total - head_edge - tail_edge > target_n:
        audio = audio[:, head_edge : total - tail_edge]

    fold_n = audio.shape[-1] - target_n
    fold_n = min(max(fold_n, min_fold), audio.shape[-1] // 2)
    fold_ms = fold_n / sample_rate * 1000.0
    return apply_seamless_loop_crossfade(audio, sample_rate, crossfade_ms=fold_ms)


# Bit-depth → soundfile subtype. 24-bit is only meaningful for lossless
# formats (WAV/FLAC); OGG Vorbis is always lossy regardless of this setting.
_SF_SUBTYPES_BY_DEPTH = {
    (16, "wav"): "PCM_16",
    (24, "wav"): "PCM_24",
    (16, "flac"): "PCM_16",
    (24, "flac"): "PCM_24",
    (16, "ogg"): "VORBIS",
    (24, "ogg"): "VORBIS",
}

# Curated compressor presets — string key in YAML → pedalboard params.
# Tuned for game-audio use cases; not meant to expose every knob.
_COMPRESSOR_PRESETS: dict[str, dict[str, float]] = {
    # Punchy / aggressive for transients: impacts, weapons, destruction.
    "punch": {"threshold_db": -18.0, "ratio": 4.0, "attack_ms": 3.0, "release_ms": 80.0},
    # Gentle glue for sustained/looping material: ambients, vehicles, mechanical.
    "glue": {"threshold_db": -24.0, "ratio": 2.0, "attack_ms": 10.0, "release_ms": 200.0},
    # Mastering glue for music loops.
    "master_glue": {"threshold_db": -20.0, "ratio": 2.5, "attack_ms": 8.0, "release_ms": 150.0},
    # Barely-there control for clean/short SFX (UI, collectibles).
    "transparent": {"threshold_db": -22.0, "ratio": 1.5, "attack_ms": 5.0, "release_ms": 120.0},
}

# librosa/soundfile/pedalboard/pyloudnorm convention is (samples, channels).
# Internally the DSP here works in numpy float32 with that layout.


def _measure_loudness_lufs(audio_np: np.ndarray, sample_rate: int) -> float:
    """Measure integrated LUFS (EBU R128, K-weighted, gated).

    Args:
        audio_np: shape (samples,) or (samples, channels), float32.
        sample_rate: Hz.

    Returns:
        Integrated loudness in LUFS, or -inf / a very low value for silence.
    """
    import pyloudnorm as pyln

    meter = pyln.Meter(sample_rate)
    # pyloudnorm expects (samples, channels); 1D is treated as mono.
    return float(meter.integrated_loudness(audio_np))


def apply_mastering_chain(
    audio: torch.Tensor,
    sample_rate: int,
    *,
    high_pass_hz: float | None = None,
    compressor_preset: str | None = None,
    compressor_enabled: bool | None = None,
    lufs_target: float | None = None,
    true_peak_db: float | None = None,
    headroom_db: float = 0.3,
) -> torch.Tensor:
    """Apply an optional mastering chain (pedalboard + pyloudnorm).

    The chain, in order (each stage skipped if its param is None):
      1. High-pass filter — removes DC offset and sub-audible rumble.
      2. Compressor — dynamic-range control, selected by ``compressor_preset``.
      3. LUFS normalization (EBU R128) — measure + gain to ``lufs_target``.
      4. True-peak limiter — hard ceiling at ``true_peak_db`` dB.

    A fixed headroom margin (``headroom_db``) is left below 0 dBFS at the very
    end via a Gain stage, so PCM quantization rounding cannot clip.

    This is a pure function: returns a new tensor, never mutates the input.
    When every optional param is None, the audio is returned unchanged.

    Args:
        audio: Tensor (channels, samples) float.
        sample_rate: Sample rate in Hz.
        high_pass_hz: High-pass cutoff in Hz (None / 0 = skip).
        compressor_preset: Key into ``_COMPRESSOR_PRESETS`` (None = skip).
        compressor_enabled: Explicit override; when False the compressor is
            skipped even if ``compressor_preset`` is set. Defaults to the
            preset's presence.
        lufs_target: Target integrated loudness in LUFS (None = skip LUFS).
        true_peak_db: Limiter ceiling in dB (None = skip limiter).
        headroom_db: Margin below 0 dBFS applied at the end (default 0.3).

    Returns:
        Tensor (channels, samples) float32, same shape as input.
    """
    if audio.shape[-1] == 0:
        return audio

    # Decide whether there's any work to do.
    want_hp = bool(high_pass_hz and high_pass_hz > 0)
    want_comp = bool(compressor_preset) and (compressor_enabled is not False)
    want_lufs = lufs_target is not None
    want_limiter = true_peak_db is not None
    if not (want_hp or want_comp or want_lufs or want_limiter):
        return audio

    try:
        from pedalboard import Compressor, HighpassFilter, Limiter, Pedalboard
    except ImportError:
        # pedalboard unavailable — degrade gracefully, preserve old behaviour.
        return audio

    # (channels, samples) → (samples, channels) for pedalboard.
    audio_in = audio.detach().cpu().to(torch.float32).numpy().T
    original_shape = audio_in.shape
    # Ensure 2D (mono → (N, 1)) so pedalboard treats it consistently.
    if audio_in.ndim == 1:
        audio_in = audio_in.reshape(-1, 1)

    # Stage 1: high-pass + compressor run first (shape the signal).
    shaping: list = []
    if want_hp:
        shaping.append(HighpassFilter(cutoff_frequency_hz=float(high_pass_hz)))
    if want_comp:
        if compressor_preset not in _COMPRESSOR_PRESETS:
            raise ValueError(
                f"compressor_preset '{compressor_preset}' desconhecido. "
                f"Opções: {', '.join(sorted(_COMPRESSOR_PRESETS))}"
            )
        shaping.append(Compressor(**_COMPRESSOR_PRESETS[compressor_preset]))

    if shaping:
        shaped_board = Pedalboard(shaping)
        audio_in = shaped_board(audio_in, sample_rate)

    # Stage 2: LUFS normalization — measure loudness on the shaped signal (post
    # HP/compressor) and apply a digital gain to hit the target. This runs
    # BEFORE the limiter so the limiter can clamp any peaks the gain pushes up.
    if want_lufs:
        import pyloudnorm as pyln

        # pyloudnorm's integrated loudness needs >= 1 block (0.4s default) of
        # audio. Very short SFX (e.g. 0.5s after crop) can fall below this and
        # raise "Audio must have length greater than the block size". In that
        # case fall back to peak normalization (still respects the headroom).
        n_samples = audio_in.shape[0]
        min_lufs_samples = int(0.45 * sample_rate)  # 0.4s block + small margin
        if n_samples >= min_lufs_samples:
            meter = pyln.Meter(sample_rate)
            measured = meter.integrated_loudness(audio_in)
            if np.isfinite(measured):
                delta_db = float(lufs_target) - float(measured)
                audio_in = audio_in * (10.0 ** (delta_db / 20.0))
        else:
            # Audio too short for LUFS measurement — fall back to peak
            # normalization to a safe level.
            peak = float(np.max(np.abs(audio_in)))
            if peak > 1e-6:
                target_peak = 10.0 ** (-(1.0 + (headroom_db or 0.0)) / 20.0)
                audio_in = audio_in * (target_peak / peak)

    # Stage 3: true-peak limiter — runs AFTER LUFS gain to clamp any peaks the
    # gain pushed above the ceiling. This is correct mastering order: shape →
    # normalize loudness → limit. The limiter is the last line of defense.
    if want_limiter:
        lim_board = Pedalboard([Limiter(threshold_db=float(true_peak_db), release_ms=100.0)])
        audio_in = lim_board(audio_in, sample_rate)
        # Hard-clip at the ceiling as an absolute guarantee. Pedalboard's Limiter
        # (like analog limiters) permits inter-sample overshoot; the hard clip
        # ensures no sample exceeds the threshold. Industry-standard "brickwall".
        ceiling_linear = 10.0 ** (float(true_peak_db) / 20.0)
        audio_in = np.clip(audio_in, -ceiling_linear, ceiling_linear)

    # Final headroom margin — digital attenuation below 0 dBFS so PCM/OGG
    # quantization rounding cannot clip.
    if headroom_db and headroom_db > 0:
        audio_in = audio_in * (10.0 ** (-float(headroom_db) / 20.0))

    processed = np.clip(audio_in, -1.0, 1.0)

    # Restore original layout: if input was mono 1D, keep it 1D.
    if len(original_shape) == 1:
        processed = processed[:, 0]
    out = torch.from_numpy(np.ascontiguousarray(processed.T)).to(torch.float32)
    return out


def save_audio(
    audio: torch.Tensor,
    sample_rate: int,
    output_path: Path,
    fmt: str = DEFAULT_FORMAT,
    as_int16: bool = True,
    normalize: bool = True,
    trim: bool = False,
    metadata: dict[str, Any] | None = None,
    trim_buffer_ms: int = 200,
    trim_threshold_db: float = -60.0,
    apply_fade: bool = True,
    seamless_loop: bool = False,
    crossfade_ms: float = 500.0,
    loop_edge_trim_s: float = 0.0,
    loop_target_seconds: float | None = None,
    crop_seconds: float | None = None,
    fade_out_seconds: float = 0.06,
    # --- DSP mastering chain (pedalboard) ---
    lufs_target: float | None = None,
    high_pass_hz: float | None = None,
    compressor_preset: str | None = None,
    compressor_enabled: bool | None = None,
    true_peak_db: float | None = None,
    headroom_db: float = 0.3,
    bit_depth: int = 16,
    ogg_quality: float | None = None,
) -> Path:
    """Processa e grava áudio num ficheiro.

    Pipeline: peak-normalize (legacy) → trim → crop → loop/fade →
    mastering chain → write.

    Args:
        audio: Tensor (channels, samples).
        sample_rate: Taxa de amostragem.
        output_path: Caminho de saída (extensão será ajustada ao formato).
        fmt: Formato de saída (wav, flac, ogg).
        as_int16: Converter para int16 antes de gravar (WAV).
        normalize: Aplicar normalização de pico. Ignorado quando
            ``lufs_target`` está definido (LUFS normalization substitui).
        trim: Remover silêncio no início e no fim.
        metadata: Metadados para gravar num .json ao lado do áudio.
        trim_buffer_ms: Buffer em ms ao cortar silêncio (passado a trim_silence).
        trim_threshold_db: Limiar em dB para o trim de silêncio (-30 mais agressivo, -60 conservador).
        apply_fade: Aplicar micro fade-in/out nas bordas do clip.
        seamless_loop: Apply equal-power crossfade for seamless loop playback.
        crossfade_ms: Crossfade duration in milliseconds (only used when seamless_loop=True).
        loop_edge_trim_s: Seconds of musical intro/outro removed from each edge
            before the loop crossfade (only used when seamless_loop=True).
        loop_target_seconds: Comprimento FINAL exacto do loop (normalmente o
            ``-d`` do CLI). Quando definido, o corte de bordas é adaptativo
            (a cauda avança enquanto há material abaixo do limiar de energia)
            e o fold do crossfade é calculado para aterrar exactamente neste
            valor — loops alinhados a compassos.
        lufs_target: Target integrated LUFS (EBU R128). Ativa a cadeia de
            mastering e desativa o peak-normalize legacy.
        high_pass_hz: Filtro high-pass em Hz (None/0 = desligado).
        compressor_preset: Preset de compressor (punch|glue|master_glue|transparent).
        compressor_enabled: Override explícito do compressor (False desliga mesmo
            com preset definido).
        true_peak_db: Teto do limiter true-peak em dB (None = sem limiter).
        headroom_db: Margem abaixo de 0 dBFS aplicada no fim da cadeia.
        bit_depth: 16 (default) ou 24 para WAV/FLAC (sem efeito em OGG).
        ogg_quality: Qualidade Vorbis 0.0-1.0 (apenas OGG; None = default libsndfile).

    Returns:
        Caminho do ficheiro de áudio gravado.
    """
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Formato '{fmt}' não suportado. Opções: {', '.join(SUPPORTED_FORMATS)}")
    if bit_depth not in (16, 24):
        raise ValueError(f"bit_depth deve ser 16 ou 24 (recebido {bit_depth}).")

    mastering_active = lufs_target is not None
    audio = audio.cpu().to(torch.float32)

    # Peak-normalize only when LUFS mastering is NOT active — the mastering
    # chain (LUFS normalize + limiter) replaces it and is mutually exclusive.
    if normalize and not mastering_active:
        audio = peak_normalize(audio)

    if trim and not seamless_loop:
        # Para loops NÃO corre trim de silêncio: com buffer 0 ele rapa uma
        # fatia variável das bordas (medido: 87 ms num loop de 16 s) e parte
        # a matemática de comprimento exacto; as bordas pertencem ao edge
        # trim + fold, não ao trim de silêncio. O comentário histórico do
        # buffer (misturar cauda musical com quase-silêncio) também se
        # resolve aqui — não há trim nenhum no caminho de loop.
        audio = trim_silence(audio, sample_rate, threshold_db=trim_threshold_db, buffer_ms=trim_buffer_ms)

    if crop_seconds is not None:
        audio = crop_to_duration(audio, sample_rate, crop_seconds, fade_out_seconds)

    if seamless_loop:
        # Stable Audio composes an *intro* (attack/swell) and an *outro*
        # (decay to near-silence) for the requested duration. Looped as-is,
        # the outro→intro wrap is an audible energy dip plus a repeated
        # intro transient every cycle. Cutting the musical edges keeps only
        # steady-state material for the loop.
        if loop_target_seconds is not None:
            audio = _shape_seamless_loop_exact(
                audio,
                sample_rate,
                loop_edge_trim_s=loop_edge_trim_s,
                crossfade_ms=crossfade_ms,
                target_seconds=loop_target_seconds,
            )
        else:
            if loop_edge_trim_s > 0:
                edge = int(loop_edge_trim_s * sample_rate)
                if audio.shape[-1] > edge * 3:
                    audio = audio[:, edge:-edge]
            audio = apply_seamless_loop_crossfade(audio, sample_rate, crossfade_ms=crossfade_ms)
    elif apply_fade:
        audio = apply_edge_fade(audio, sample_rate)

    # Mastering chain runs AFTER shaping: LUFS/limiter must see the final
    # signal (post-trim, post-loop) to target and protect it correctly.
    if mastering_active or high_pass_hz or compressor_preset or true_peak_db is not None:
        chain_kwargs = dict(
            high_pass_hz=high_pass_hz,
            compressor_preset=compressor_preset,
            compressor_enabled=compressor_enabled,
            lufs_target=lufs_target,
            true_peak_db=true_peak_db,
            headroom_db=headroom_db,
        )
        if seamless_loop:
            # Compressor/limiter são **stateful**: no início do ficheiro o
            # envelope parte de zero e no fim está activo — a diferença de
            # ganho quebra a costura do loop (jump no wrap 0.085 vs p99 0.043
            # medido). Render em buffer dobrado (loop x2) e extrair a 2ª
            # cópia: o estado no arranque herda o estado do fim → jump volta
            # ao nível de um passo normal (0.010). LUFS integrado é idêntico
            # sobre conteúdo duplicado.
            half = audio.shape[-1]
            audio = apply_mastering_chain(torch.cat([audio, audio], dim=1), sample_rate, **chain_kwargs)[:, half:]
        else:
            audio = apply_mastering_chain(audio, sample_rate, **chain_kwargs)

    output_path = output_path.with_suffix(f".{fmt}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # (channels, samples) → (samples, channels) for soundfile
    audio_np: np.ndarray = np.ascontiguousarray(audio.numpy().T)

    # OGG Vorbis is lossy and can introduce inter-sample overshoot during
    # quantization — a signal peaking at 0.94 can decode back at 1.1+ on sharp
    # transients. Pull the peak down to a safe ceiling before lossy encoding so
    # the decoded result stays under 0 dBFS. WAV/FLAC are lossless and need no
    # extra margin.
    if fmt == "ogg":
        ogg_safety_peak = 0.70  # ≈ -3.1 dBFS, absorbs worst-case Vorbis overshoot
        current_peak = float(np.max(np.abs(audio_np)))
        if current_peak > ogg_safety_peak and current_peak > 1e-6:
            audio_np = audio_np * (ogg_safety_peak / current_peak)

    subtype = _SF_SUBTYPES_BY_DEPTH.get((bit_depth, fmt), _SF_SUBTYPES.get(fmt, "PCM_16"))

    if fmt == "ogg" and ogg_quality is not None:
        _write_ogg_with_quality(str(output_path), audio_np, sample_rate, float(ogg_quality))
    else:
        sf.write(str(output_path), audio_np, sample_rate, subtype=subtype)

    if metadata:
        if seamless_loop:
            metadata["seamless_loop"] = True
            metadata["crossfade_ms"] = crossfade_ms
            if loop_edge_trim_s > 0:
                metadata["loop_edge_trim_s"] = loop_edge_trim_s
        if crop_seconds is not None:
            metadata["crop_seconds"] = crop_seconds
            metadata["fade_out_seconds"] = fade_out_seconds
        if mastering_active or high_pass_hz or compressor_preset or true_peak_db is not None:
            metadata["mastering"] = {
                "lufs_target": lufs_target,
                "high_pass_hz": high_pass_hz,
                "compressor_preset": compressor_preset,
                "compressor_enabled": compressor_enabled,
                "true_peak_db": true_peak_db,
                "headroom_db": headroom_db,
            }
        if bit_depth != 16:
            metadata["bit_depth"] = bit_depth
        if fmt == "ogg" and ogg_quality is not None:
            metadata["ogg_quality"] = ogg_quality
        meta_path = output_path.with_suffix(output_path.suffix + ".json")
        meta_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return output_path


def _write_ogg_with_quality(path: str, audio_np: np.ndarray, sample_rate: int, quality: float) -> None:
    """Write OGG Vorbis com qualidade Vorbis explícita (0.0-1.0).

    A via anterior tentava um comando C interno do soundfile que NUNCA existiu
    na binding Python (``_SoundFile.command``) — o setter nunca corria, o
    ficheiro saía sempre com a qualidade default e o metadata registava o valor
    pedido como se aplicado. Agora: re-encode via ``ffmpeg`` (qscale 0-10 ==
    Vorbis quality 0.0-1.0); sem ffmpeg no sistema, escreve com o default e
    avisa (não claims silenciosos).
    """
    import logging
    import shutil
    import subprocess
    import tempfile

    quality = max(0.0, min(1.0, quality))
    log = logging.getLogger(__name__)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is not None:
        try:
            with tempfile.TemporaryDirectory(prefix="t2s_ogg_") as td:
                wav = Path(td) / "in.wav"
                sf.write(str(wav), audio_np, sample_rate, subtype="PCM_16")
                subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-i",
                        str(wav),
                        "-c:a",
                        "libvorbis",
                        "-q:a",
                        str(round(quality * 10)),
                        str(path),
                    ],
                    capture_output=True,
                    timeout=180,
                    check=True,
                )
            return
        except Exception as e:  # ffmpeg falhou — cair para o default com aviso
            log.warning("ffmpeg falhou no encode OGG (%s) — a usar qualidade default", e)
    else:
        log.warning("ffmpeg não encontrado — ogg_quality=%.2f NÃO aplicado (default libsndfile)", quality)
    sf.write(path, audio_np, sample_rate, subtype="VORBIS")
