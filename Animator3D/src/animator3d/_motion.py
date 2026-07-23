"""Primitivos de movimento orgânico para animação procedural.

Funções puras (sem bpy) que adicionam ruído, easing e variação natural a
clips gerados por ``bpy_ops``/``humanoid``. O objectivo é eliminar o aspecto
"robótico" de ciclos perfeitamente periódicos.

Conceitos:
- **Value-noise** (``value_noise``/``noise1``/``fbm``): ruído determinístico,
  contínuo e seedável. Diferente de ``random`` (que é espectral/discreto) —
  produz variação orgânica suave entre amostras adjacentes.
- **Easing shape** (``shaped_cos``/``shaped_sin``): ciclos sinusoidais
  modulados para desacelerar nos extremos (weight shift natural).
- **Jitter helper** (``with_noise``): atalho para somar ruído a um valor.

Todas as funções são determinísticas (sem estado global) para que os clips
sejam reprodutíveis entre runs.
"""

from __future__ import annotations

import math

_TWO_PI = math.pi * 2.0


def value_noise(seed: int) -> float:
    """Ruído determinístico em [-1, 1] a partir de um inteiro (hash).

    Usa um hash multiplicação/XOR — rápido, sem dependências, reprodutível.
    Não é criptográfico, mas suficiente para jitter de animação.

    Args:
        seed: Inteiro qualquer (índice de frame, índice de stride, etc.).

    Returns:
        Float em [-1, 1].
    """
    # Hash: multiplica por um primo grande, XOR com shift, normaliza.
    h = (seed * 73856093) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 1274126177) & 0xFFFFFFFF
    # Mapeia [0, 2^32) para [-1, 1].
    return (h / 0x7FFFFFFF) - 1.0


def noise1(t: float, seed: int = 0) -> float:
    """Value-noise 1D contínuo em [-1, 1].

    Interpola ``value_noise`` entre inteiros adjacentes via smoothstep,
    produzindo ruído suave (C¹ contínuo). Diferente de ``value_noise`` puro
    (que é discreto), ``noise1`` varia suavemente com ``t``.

    Args:
        t: Coordenada contínua (pode ser tempo de frame normalizado, etc.).
        seed: Offset do hash (para canais independentes).

    Returns:
        Float em [-1, 1].
    """
    i = math.floor(t)
    f = t - i
    # Smoothstep para transição suave entre amostras.
    s = f * f * (3.0 - 2.0 * f)
    a = value_noise(i + seed * 31)
    b = value_noise(i + 1 + seed * 31)
    return a * (1.0 - s) + b * s


def fbm(t: float, octaves: int = 3, seed: int = 0) -> float:
    """Fractal Brownian Motion: soma de oitavas de ``noise1``.

    Combina várias frequências de ruído (1×, 2×, 4×, ...) com amplitude
    decrescente, produzindo variação orgânica rica — como movimento humano
    natural que tem micro-tremores sobre oscilações maiores.

    Args:
        t: Coordenada contínua.
        octaves: Número de oitavas (mais = mais detalhe, mais custo).
        seed: Offset para canais independentes (braço L vs R, etc.).

    Returns:
        Float em aproximadamente [-1, 1] (normalizado pelo somatório de pesos).
    """
    total = 0.0
    amp = 1.0
    freq = 1.0
    norm = 0.0
    for _ in range(octaves):
        total += amp * noise1(t * freq, seed=seed)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm if norm > 0 else 0.0


def shaped_cos(phi: float) -> float:
    """Cosseno "shaped" para locomoção: ``cos(φ·2π) · (0.7 + 0.3·sin²(φ·π))``.

    O cosseno puro tem velocidade angular constante (mecânico). A modulação
    por ``sin²`` faz o ciclo "lingering" nos picos (±1) e acelerar na passagem
    pelo zero — replicando o weight shift natural de um passo.

    Args:
        phi: Fase do ciclo em [0, 1[ (0 = contacto do calcanhar).

    Returns:
        Float em [-1, 1] aproximadamente (pode exceder ligeiramente nos picos).
    """
    return math.cos(phi * _TWO_PI) * (0.7 + 0.3 * math.sin(phi * math.pi) ** 2)


def shaped_sin(t: float, freq: float = 1.0) -> float:
    """Seno cúbico para respiração/balanço: ``sin³(t·2π·freq)``.

    ``sin³`` mantém o período mas achata a passagem pelo zero e realça os
    picos — a respiração "segura" no topo da inspiração em vez de ser
    perfeitamente sinusoidal (metronómica).

    Args:
        t: Tempo normalizado (0..1 = 1 ciclo).
        freq: Frequência (ciclos por unidade de t).

    Returns:
        Float em [-1, 1].
    """
    s = math.sin(t * _TWO_PI * freq)
    return s * s * s


def ease_in_out_sin(t: float) -> float:
    """Easing sinusoidal simétrico: ``sin(t·π)`` em [0, 1].

    Útil para transições que desaceleram nos extremos (t=0 e t=1).
    ``ease_in_out_sin(0) = 0``, ``ease_in_out_sin(0.5) = 1`` (pico),
    ``ease_in_out_sin(1) = 0`` (regresso suave).

    Args:
        t: Tempo normalizado [0, 1].

    Returns:
        Float em [0, 1].
    """
    return math.sin(t * math.pi)


def with_noise(value: float, amp: float, t: float, seed: int = 0) -> float:
    """Soma ruído orgânico (``fbm``) a um valor.

    Helper para jittering: ``value + amp · fbm(t, seed=seed)``.
    Por exemplo, ``with_noise(hip_swing, 0.01, t, seed=42)`` adiciona ±0.01 rad
    de variação natural a cada balanço da anca.

    Args:
        value: Valor base (amplitude do movimento).
        amp: Amplitude do ruído (fração de ``value`` ou absoluta).
        t: Coordenada contínua para o noise.
        seed: Offset para canais independentes.

    Returns:
        ``value + amp · fbm(t, seed)``.
    """
    return value + amp * fbm(t, seed=seed)
