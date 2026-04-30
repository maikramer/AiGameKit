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
DEFAULT_FORMAT = "wav"

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
    apply_fade: bool = True,
) -> Path:
    """Processa e grava áudio num ficheiro.

    Args:
        audio: Tensor (channels, samples).
        sample_rate: Taxa de amostragem.
        output_path: Caminho de saída (extensão será ajustada ao formato).
        fmt: Formato de saída (wav, flac, ogg).
        as_int16: Converter para int16 antes de gravar (WAV).
        normalize: Aplicar normalização de pico.
        trim: Remover silêncio no início e no fim.
        metadata: Metadados para gravar num .json ao lado do áudio.
        trim_buffer_ms: Buffer em ms ao cortar silêncio (passado a trim_silence).
        apply_fade: Aplicar micro fade-in/out nas bordas do clip.

    Returns:
        Caminho do ficheiro de áudio gravado.
    """
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Formato '{fmt}' não suportado. Opções: {', '.join(SUPPORTED_FORMATS)}")

    audio = audio.cpu().to(torch.float32)

    if normalize:
        audio = peak_normalize(audio)

    if trim:
        audio = trim_silence(audio, sample_rate, buffer_ms=trim_buffer_ms)

    if apply_fade:
        audio = apply_edge_fade(audio, sample_rate)

    output_path = output_path.with_suffix(f".{fmt}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # (channels, samples) → (samples, channels) for soundfile
    audio_np: np.ndarray = audio.numpy().T
    subtype = _SF_SUBTYPES.get(fmt, "PCM_16")
    sf.write(str(output_path), audio_np, sample_rate, subtype=subtype)

    if metadata:
        meta_path = output_path.with_suffix(output_path.suffix + ".json")
        meta_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return output_path
