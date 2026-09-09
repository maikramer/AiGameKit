#!/usr/bin/env python3
"""Gerador do heightmap do `simple-rpg` (Viber) — `terrain.ahgt`.

Porquê um gerador e não um heightmap de difusão: o campo antigo (Terrain3D,
worldSize 8000) tinha 200 m de amplitude mas o conteúdo do jogo vive em ±300 m
do centro, onde o campo é uma planície de 30 m de amplitude — o mundo lia-se
como um campo de golfe. O gerador abaixo autora a SILHUETA primeiro (bacias,
planaltos, mesas, cristas e um pico dominante colocados à mão em coordenadas
de mundo), junta ruído ridged/fbm por máscara de montanha, erode
(termal + hidráulica por gotas) e só depois volta a achatar os corredores
jogáveis (praça, artérias, leitos de lago, pads e o vale do rio).

Contratos que o ficheiro TEM de respeitar (senão o mundo parte):

* `worldSize` = 4000 e `maxHeight` = 200 batem com `<Terrain>` em world.xml.
  A engine avisa e mantém o valor do XML se divergirem (runtime.rs).
* Grelha 4096² row-major `[z][x]`, u16 deflate — `x = -2000 + ix*texel`,
  `z = -2000 + iz*texel`, `texel = 4000/4095`.
* Corredores das artérias (N/S/E/O), praça, pads e leitos de lago ficam
  suaves: o `RoadNetwork` só tolera 19° de rampa e o `<Lake>` é um carve
  lower-only (numa encosta abriria uma vala em vez de um lago).
* O rio norte é prefixo-mínimo: o vale tem de descer de E para O.

Uso:  python3 gen_heightmap.py [-o terrain.ahgt] [--preview preview.png]
"""

from __future__ import annotations

import argparse
import json
import struct
import zlib
from pathlib import Path

import numpy as np
from scipy import ndimage

WORLD = 4000.0
MAX_H = 200.0
OUT_N = 4096  # grelha final (≈0.977 m/texel)
WORK_N = 1024  # grelha de desenho + erosão (≈3.9 m/texel)
SEED = 20260903

# ---------------------------------------------------------------------------
# Ruído
# ---------------------------------------------------------------------------


def value_noise(n: int, cells: int, rng: np.random.Generator) -> np.ndarray:
    """Value-noise `n×n` com `cells` células por lado, interpolação cúbica."""
    lat = rng.random((cells + 3, cells + 3), dtype=np.float64)
    zoom = n / cells
    out = ndimage.zoom(lat, zoom, order=3, mode="grid-wrap")
    # `zoom` de um lattice maior: recorta o miolo para evitar bordas do spline.
    off = int(zoom)
    return out[off : off + n, off : off + n]


def fbm(n: int, cells: int, octaves: int, rng: np.random.Generator, gain: float = 0.5) -> np.ndarray:
    total = np.zeros((n, n))
    amp, norm, c = 1.0, 0.0, cells
    for _ in range(octaves):
        total += amp * value_noise(n, max(2, int(round(c))), rng)
        norm += amp
        amp *= gain
        c *= 2.0
    return total / norm


def ridged(n: int, cells: int, octaves: int, rng: np.random.Generator, gain: float = 0.52) -> np.ndarray:
    """Ridged multifractal — cristas afiadas em vez de bolhas."""
    total = np.zeros((n, n))
    amp, norm, c, weight = 1.0, 0.0, cells, np.ones((n, n))
    for _ in range(octaves):
        s = 1.0 - np.abs(2.0 * value_noise(n, max(2, int(round(c))), rng) - 1.0)
        s *= s
        s *= np.clip(weight, 0.0, 1.0)
        weight = s * 2.0
        total += amp * s
        norm += amp
        amp *= gain
        c *= 2.0
    return total / norm


# ---------------------------------------------------------------------------
# Primitivas de desenho (coordenadas de mundo, metros)
# ---------------------------------------------------------------------------


def smoothstep(a: float, b: float, t: np.ndarray) -> np.ndarray:
    x = np.clip((t - a) / (b - a) if b != a else np.sign(t - a) * 0.5 + 0.5, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def wobbly_radius(X, Z, cx, cz, base, amp=0.0, phase=0.0, k1=3.0, k2=7.0):
    """Raio angularmente perturbado — nada no mundo é um círculo perfeito."""
    if amp <= 0.0:
        return base
    ang = np.arctan2(Z - cz, X - cx)
    return base * (1.0 + amp * (0.62 * np.sin(k1 * ang + phase) + 0.38 * np.sin(k2 * ang - 1.7 * phase)))


def cone(X, Z, cx, cz, r, h, exp=1.55, wob=0.16, phase=0.0):
    rr = wobbly_radius(X, Z, cx, cz, r, wob, phase)
    d = np.hypot(X - cx, Z - cz) / np.maximum(rr, 1e-3)
    return h * np.clip(1.0 - d, 0.0, 1.0) ** exp


def dome(X, Z, cx, cz, r, h, wob=0.2, phase=0.0):
    rr = wobbly_radius(X, Z, cx, cz, r, wob, phase)
    d = np.hypot(X - cx, Z - cz) / np.maximum(rr, 1e-3)
    return h * smoothstep(1.0, 0.0, d)


def mesa(X, Z, cx, cz, r, h, edge=26.0, wob=0.22, phase=0.0):
    """Butte de topo plano e flanco quase vertical (silhueta do deserto)."""
    rr = wobbly_radius(X, Z, cx, cz, r, wob, phase)
    d = np.hypot(X - cx, Z - cz)
    return h * smoothstep(0.0, 1.0, np.clip((rr - d) / max(edge, 1.0), 0.0, 1.0))


def seg_distance(X, Z, pts):
    """Distância ao polilinha `pts` = [(x,z), ...]."""
    best = np.full(X.shape, 1e9)
    for (x0, z0), (x1, z1) in zip(pts[:-1], pts[1:]):
        dx, dz = x1 - x0, z1 - z0
        ll = dx * dx + dz * dz
        if ll < 1e-9:
            best = np.minimum(best, np.hypot(X - x0, Z - z0))
            continue
        t = np.clip(((X - x0) * dx + (Z - z0) * dz) / ll, 0.0, 1.0)
        best = np.minimum(best, np.hypot(X - (x0 + t * dx), Z - (z0 + t * dz)))
    return best


def seg_param_height(X, Z, pts, heights):
    """Altura interpolada ao longo do polilinha, projectada no ponto mais perto."""
    best_d = np.full(X.shape, 1e9)
    best_h = np.zeros(X.shape)
    for i, ((x0, z0), (x1, z1)) in enumerate(zip(pts[:-1], pts[1:])):
        dx, dz = x1 - x0, z1 - z0
        ll = max(dx * dx + dz * dz, 1e-9)
        t = np.clip(((X - x0) * dx + (Z - z0) * dz) / ll, 0.0, 1.0)
        d = np.hypot(X - (x0 + t * dx), Z - (z0 + t * dz))
        h = heights[i] + t * (heights[i + 1] - heights[i])
        take = d < best_d
        best_d = np.where(take, d, best_d)
        best_h = np.where(take, h, best_h)
    return best_d, best_h


def ridge_line(X, Z, pts, heights, width, shoulder):
    """Crista ao longo de um traçado: cume `heights`, flancos até `shoulder`."""
    d, h = seg_param_height(X, Z, pts, heights)
    return h * smoothstep(width + shoulder, width * 0.35, d)


# ---------------------------------------------------------------------------
# Erosão
# ---------------------------------------------------------------------------


def thermal_erosion(h: np.ndarray, talus_m: float, iters: int, rate: float = 0.35) -> np.ndarray:
    """Talude: o material acima do ângulo de repouso escorrega para o vizinho.

    Produz sopés de cascalho e fundos de vale planos — a leitura "montanha
    real" em vez de "bolha de ruído".
    """
    h = h.copy()
    shifts = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    for _ in range(iters):
        delta = np.zeros_like(h)
        for sx, sz in shifts:
            diag = 1.4142 if (sx and sz) else 1.0
            nb = np.roll(np.roll(h, sz, axis=0), sx, axis=1)
            move = np.maximum(h - nb - talus_m * diag, 0.0) * (rate / len(shifts))
            delta -= move
            delta += np.roll(np.roll(move, -sz, axis=0), -sx, axis=1)
        h += delta
    return h


def droplet_erosion(
    h: np.ndarray,
    world_m: float,
    rng: np.random.Generator,
    drops: int = 260_000,
    steps: int = 44,
    erode: float = 0.30,
    deposit: float = 0.24,
    inertia: float = 0.055,
    capacity: float = 5.0,
    max_dig: float = 0.5,
) -> np.ndarray:
    """Erosão hidráulica por gotas, todas as gotas em paralelo (numpy).

    Escava ravinas e V-valleys nos flancos e deposita nos fundos — é o que
    transforma um campo de ruído em algo que o olho lê como drenagem.
    """
    n = h.shape[0]
    field = h.copy()
    px = rng.uniform(1.5, n - 2.5, drops)
    pz = rng.uniform(1.5, n - 2.5, drops)
    dx = np.zeros(drops)
    dz = np.zeros(drops)
    water = np.ones(drops)
    sediment = np.zeros(drops)
    speed = np.ones(drops)
    MAX_DIG = max_dig  # teto absoluto de escavação por gota/passo (m)

    for _ in range(steps):
        ix = px.astype(np.int32)
        iz = pz.astype(np.int32)
        np.clip(ix, 0, n - 2, out=ix)
        np.clip(iz, 0, n - 2, out=iz)
        fx = px - ix
        fz = pz - iz
        h00 = field[iz, ix]
        h10 = field[iz, ix + 1]
        h01 = field[iz + 1, ix]
        h11 = field[iz + 1, ix + 1]
        gx = (h10 - h00) * (1 - fz) + (h11 - h01) * fz
        gz = (h01 - h00) * (1 - fx) + (h11 - h10) * fx
        old_h = (h00 * (1 - fx) + h10 * fx) * (1 - fz) + (h01 * (1 - fx) + h11 * fx) * fz

        dx = dx * inertia - gx * (1 - inertia)
        dz = dz * inertia - gz * (1 - inertia)
        norm = np.hypot(dx, dz)
        live = norm > 1e-6
        dx = np.where(live, dx / np.maximum(norm, 1e-6), 0.0)
        dz = np.where(live, dz / np.maximum(norm, 1e-6), 0.0)
        px = px + dx
        pz = pz + dz
        np.clip(px, 1.5, n - 2.5, out=px)
        np.clip(pz, 1.5, n - 2.5, out=pz)

        nix = px.astype(np.int32)
        niz = pz.astype(np.int32)
        np.clip(nix, 0, n - 2, out=nix)
        np.clip(niz, 0, n - 2, out=niz)
        nfx = px - nix
        nfz = pz - niz
        g00 = field[niz, nix]
        g10 = field[niz, nix + 1]
        g01 = field[niz + 1, nix]
        g11 = field[niz + 1, nix + 1]
        new_h = (g00 * (1 - nfx) + g10 * nfx) * (1 - nfz) + (g01 * (1 - nfx) + g11 * nfx) * nfz
        dh = new_h - old_h  # <0 a descer

        cap = np.maximum(-dh, 0.0) * speed * water * capacity + 0.012
        excess = sediment - cap
        # A subir ou com sedimento a mais → deposita; a descer com folga → escava.
        amount = np.where(dh > 0.0, np.minimum(sediment, dh), np.where(excess > 0.0, excess * deposit, 0.0))
        # Escavar no MÁXIMO uma fração da descida e nunca mais que `MAX_DIG` m
        # por passo: o bound clássico `-dh` deixava a gota cortar a descida
        # INTEIRA de um penhasco — a parede ficava mais funda e a gota seguinte
        # escavava o dobro (×2 por visita ⇒ min -3e17 em 20 iterações).
        dig = np.minimum(np.minimum(-excess * erode, np.maximum(-dh, 0.0) * 0.30), MAX_DIG)
        take = np.where((dh <= 0.0) & (excess < 0.0), dig, 0.0)
        sediment = sediment - amount + take
        change = amount - take

        w00 = (1 - fx) * (1 - fz)
        w10 = fx * (1 - fz)
        w01 = (1 - fx) * fz
        w11 = fx * fz
        flat = iz * n + ix
        np.add.at(field.reshape(-1), flat, change * w00)
        np.add.at(field.reshape(-1), flat + 1, change * w10)
        np.add.at(field.reshape(-1), flat + n, change * w01)
        np.add.at(field.reshape(-1), flat + n + 1, change * w11)

        speed = np.sqrt(np.maximum(speed * speed - dh * 0.85, 0.02))
        water *= 0.975
    return field


# ---------------------------------------------------------------------------
# O mundo
# ---------------------------------------------------------------------------

VALE_FLOOR = 36.0  # cota da praça / referência do vale


def build_design(X, Z, rng):
    """Camada autorada: bacias, planaltos, mesas, cristas e o pico dominante."""
    h = np.full(X.shape, VALE_FLOOR)
    r = np.hypot(X, Z)

    # ---- Vale central: prato suave de raio 130, ombro a subir até 260 -----
    h += 22.0 * smoothstep(150.0, 300.0, r)  # ombro do vale
    h += 8.0 * dome(X, Z, 40, 60, 200, 1.0, wob=0.3, phase=0.7)
    h -= 5.0 * dome(X, Z, -60, -40, 170, 1.0, wob=0.3, phase=2.1)

    # ---- OESTE: bacia do pântano (x < -120) -------------------------------
    swamp = smoothstep(-110.0, -230.0, X) * smoothstep(300.0, 120.0, np.abs(Z))
    h -= 21.0 * swamp
    h -= 5.0 * dome(X, Z, -230, -30, 200, 1.0, wob=0.35, phase=1.3)
    # Cômoros de turfa (leitura de pântano, não de campo)
    h += 2.6 * swamp * (fbm(X.shape[0], 34, 3, rng) - 0.5) * 2.0
    # Muro oeste do horizonte: serra baixa e escura
    h += ridge_line(
        X, Z,
        [(-980, 620), (-880, 300), (-830, -40), (-900, -420), (-1020, -720)],
        [118, 152, 140, 158, 126], width=110, shoulder=280,
    )

    # ---- ESTE: deserto — bacia de dunas, depois mesas ---------------------
    desert = smoothstep(110.0, 240.0, X) * smoothstep(320.0, 140.0, np.abs(Z))
    h -= 8.0 * desert
    # Planalto seco a partir de x≈380
    h += 62.0 * smoothstep(330.0, 640.0, X)
    for cx, cz, rr, hh, ph in [
        (352, 168, 52, 46, 0.3),
        (430, -96, 66, 62, 1.9),
        (336, -228, 44, 38, 3.4),
        (556, 82, 84, 74, 0.9),
        (520, -330, 70, 66, 2.6),
        (688, 250, 96, 82, 1.2),
        (660, -140, 78, 70, 4.1),
    ]:
        h = np.maximum(h, mesa(X, Z, cx, cz, rr, VALE_FLOOR + hh + 34.0 * smoothstep(330.0, 640.0, np.array(float(cx))), edge=22, phase=ph))
    # Muralha de arenito no horizonte este
    h += ridge_line(
        X, Z,
        [(900, 700), (860, 300), (880, -60), (930, -460), (1000, -760)],
        [86, 104, 96, 112, 92], width=130, shoulder=300,
    )

    # ---- NORTE: floresta — colinas + vale do rio -------------------------
    forest = smoothstep(120.0, 260.0, Z) * smoothstep(340.0, 150.0, np.abs(X))
    h += 20.0 * forest
    h += 16.0 * dome(X, Z, -150, 300, 180, 1.0, wob=0.3, phase=0.4)
    h += 14.0 * dome(X, Z, 150, 320, 170, 1.0, wob=0.3, phase=2.8)
    # Crista arborizada de fundo (z 380-560)
    h += ridge_line(
        X, Z,
        [(-520, 470), (-260, 430), (10, 460), (280, 430), (540, 480)],
        [72, 88, 78, 92, 70], width=90, shoulder=220,
    )
    # Muro norte do horizonte
    h += ridge_line(
        X, Z,
        [(-900, 880), (-400, 820), (60, 900), (520, 830), (980, 890)],
        [104, 128, 112, 134, 108], width=140, shoulder=320,
    )

    # ---- SUL: picos gelados — a estrela do horizonte ---------------------
    # Sopé a subir de z=-130 até z=-260
    h += 46.0 * smoothstep(-130.0, -270.0, Z)
    # Ombro/arête que liga os cumes
    h += ridge_line(
        X, Z,
        [(-320, -300), (-150, -352), (20, -318), (120, -336), (270, -392)],
        [58, 92, 70, 96, 74], width=70, shoulder=170,
    )
    # Pico dominante — visível da praça a ~350 m
    h = np.maximum(h, cone(X, Z, 96, -334, 210, 196.0, exp=1.42, phase=0.6))
    h = np.maximum(h, cone(X, Z, -156, -368, 175, 174.0, exp=1.5, phase=2.4))
    h = np.maximum(h, cone(X, Z, 8, -486, 205, 188.0, exp=1.45, phase=4.0))
    h = np.maximum(h, cone(X, Z, 262, -430, 165, 168.0, exp=1.5, phase=1.1))
    h = np.maximum(h, cone(X, Z, -330, -470, 150, 158.0, exp=1.55, phase=5.2))
    # Parede sul do horizonte
    h += ridge_line(
        X, Z,
        [(-980, -880), (-420, -800), (40, -900), (500, -820), (980, -880)],
        [122, 150, 132, 152, 124], width=150, shoulder=330,
    )
    # Garganta da artéria sul (x≈-12): rasga o sopé para a estrada passar
    gorge = seg_distance(X, Z, [(-6, -140), (-10, -210), (-12, -260), (-12, -300), (-40, -360), (-90, -430)])
    h -= 42.0 * smoothstep(150.0, 25.0, gorge) * smoothstep(-140.0, -240.0, Z)

    # ---- Anel exterior: nada de bordas planas até ao fim do mundo --------
    h += 34.0 * smoothstep(1050.0, 1750.0, r)
    return h


def apply_detail(h, X, Z, rng, n):
    """Ruído por máscara: cristas nas montanhas, ondulação nas planícies."""
    relief = np.clip((h - VALE_FLOOR) / 90.0, 0.0, 1.0)
    mountain = smoothstep(0.30, 0.80, relief)

    hills = (fbm(n, 12, 5, rng) - 0.5) * 2.0
    h += 9.5 * hills * (0.35 + 0.65 * (1.0 - mountain))

    crags = ridged(n, 9, 6, rng) - 0.42
    h += 74.0 * crags * mountain

    fine = (fbm(n, 46, 4, rng) - 0.5) * 2.0
    h += 3.4 * fine * (0.5 + 0.9 * mountain)

    # Dunas do deserto: cristas longas e anisotrópicas
    dune_mask = smoothstep(110.0, 250.0, X) * smoothstep(360.0, 150.0, np.abs(Z)) * (1.0 - mountain)
    dunes = np.sin((X * 0.55 + Z * 0.82) / 27.0 + 2.4 * fbm(n, 7, 2, rng))
    h += 4.6 * dune_mask * (0.5 * dunes + 0.5 * np.sin((X * 0.9 - Z * 0.44) / 61.0))
    return h


def flatten_corridors(h, X, Z, n):
    """Devolve as garantias de jogabilidade depois do ruído e da erosão."""
    texel = WORLD / (n - 1)
    smooth = ndimage.gaussian_filter(h, sigma=max(1.0, 26.0 / texel), mode="nearest")
    very_smooth = ndimage.gaussian_filter(h, sigma=max(1.0, 55.0 / texel), mode="nearest")

    # --- Praça + muralha (r<115): prato quase plano na cota do vale -------
    r = np.hypot(X, Z)
    plaza = smoothstep(150.0, 62.0, r)
    plaza_h = VALE_FLOOR + 1.5 * np.sin(X / 90.0) + 1.2 * np.cos(Z / 110.0)
    h = h * (1.0 - plaza) + plaza_h * plaza

    # --- Artérias: leito suave e sem crags dentro de 34 m ----------------
    arteries = [
        [(0, 0), (0, 32), (-3, 128), (2, 198), (6, 230), (-8, 286), (-30, 350), (-60, 430)],
        [(0, 0), (0, -32), (0, -84), (0, -184), (-12, -260), (-12, -290), (-60, -380), (-160, -430)],
        [(0, 0), (32, 0), (124, 2), (200, 10), (290, -4), (420, -120), (560, -240)],
        [(0, 0), (-32, 0), (-171, 2), (-209, 3), (-292, 12), (-400, 30), (-506, 30)],
        # Trilhos de bioma (florestal / deserto / pântano / picos)
        [(4, 156), (-32, 160), (-88, 176)],
        [(8, 230), (56, 220), (100, 236)],
        [(160, 6), (142, 76)],
        [(200, 10), (164, -92), (156, -110)],
        [(-160, 2), (-214, 48), (-260, 92)],
        [(-220, -18), (-188, -52)],
        [(-2, -208), (-54, -214)],
        [(-2, -208), (42, -184)],
    ]
    for pts in arteries:
        d = seg_distance(X, Z, pts)
        w = smoothstep(58.0, 16.0, d)
        h = h * (1.0 - w) + smooth * w
        w2 = smoothstep(110.0, 40.0, d)
        h = h * (1.0 - 0.45 * w2) + very_smooth * (0.45 * w2)

    # --- Bancadas planas: lagos e pads ------------------------------------
    benches = [
        # (x, z, raio útil, feather)
        (-190, -16, 34, 46),  # Lagoa Grande (pântano)
        (-156, 68, 24, 34),
        (-236, -84, 28, 38),
        (-80, 104, 26, 36),  # lagoa da floresta
        (140, 92, 24, 34),  # oásis
        (68, -184, 32, 44),  # tarn alpino
        (-12, -290, 62, 80),  # aldeia de montanha (pads)
        (2, 198, 40, 60),  # margens da ponte do rio
        (6, 230, 40, 60),
        (859, 281, 160, 90),  # campo de treino remoto
        (-540, 30, 36, 48),
    ]
    for cx, cz, rad, feather in benches:
        d = np.hypot(X - cx, Z - cz)
        inside = d <= rad + feather
        if not inside.any():
            continue
        level = float(np.median(very_smooth[d <= max(rad, 8.0)]))
        w = smoothstep(rad + feather, rad * 0.75, d)
        h = h * (1.0 - w) + level * w

    # --- Vale do rio norte: desce sempre de E para O ----------------------
    river = [
        (314, 125), (287, 121), (258, 121), (236, 141), (209, 162), (180, 188),
        (150, 193), (121, 199), (92, 189), (62, 203), (33, 209), (4, 215),
        (-25, 213), (-55, 215), (-84, 232), (-113, 260), (-133, 289),
        (-158, 318), (-186, 340),
    ]
    prof = np.linspace(0.0, 1.0, len(river))
    bed = list(VALE_FLOOR + 15.0 - 15.0 * prof)  # 51 → 36 m, monótono
    d_r, h_r = seg_param_height(X, Z, river, bed)
    valley = smoothstep(190.0, 12.0, d_r)
    h = h * (1.0 - valley) + (h_r + 3.0 * smoothstep(12.0, 190.0, d_r) * 6.0) * valley
    return h


def soft_knee(h: np.ndarray, knee: float, top: float) -> np.ndarray:
    """Compressão assintótica acima de `knee` até `top` (C1 no joelho).

    O clip duro fabricava planaltos imensos à cota do teto ondequer que a
    soma (design + ruído) ultrapassava `MAX_H` — as muralhas do horizonte
    liam-se como mesas cortadas a faca. O joelho mantém monotonia e declives
    relativos: cristas altas continuam mais altas que as vizinhas, só chegam
    menos perto do teto.
    """
    out = np.minimum(h, knee)
    hi = h > knee
    span = max(top - knee, 1e-3)
    out[hi] = knee + span * (1.0 - np.exp(-(h[hi] - knee) / span))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=str(Path(__file__).with_name("terrain.ahgt")))
    ap.add_argument("--preview", default="")
    ap.add_argument("--work", type=int, default=WORK_N)
    args = ap.parse_args()

    rng = np.random.default_rng(SEED)
    n = args.work
    axis = np.linspace(-WORLD / 2.0, WORLD / 2.0, n)
    X, Z = np.meshgrid(axis, axis)  # X varia em colunas, Z em linhas

    print(f"[1/6] desenho ({n}²)")
    h = build_design(X, Z, rng)
    print("[2/6] detalhe (ridged/fbm/dunas)")
    h = apply_detail(h, X, Z, rng, n)
    print("[3/6] erosão termal")
    cell_m = WORLD / (n - 1)
    h = thermal_erosion(h, talus_m=0.62 * cell_m, iters=42, rate=0.5)
    print("[4/6] erosão hidráulica")
    h = droplet_erosion(h, WORLD, rng, drops=240_000, steps=42)
    h = ndimage.gaussian_filter(h, sigma=0.7, mode="nearest")
    print("[5/6] corredores jogáveis")
    h = flatten_corridors(h, X, Z, n)

    print(f"[6/6] upsample → {OUT_N}² + detalhe fino")
    big = ndimage.zoom(h, OUT_N / n, order=3, mode="nearest")
    big = big[:OUT_N, :OUT_N]
    if big.shape != (OUT_N, OUT_N):
        pad = ((0, OUT_N - big.shape[0]), (0, OUT_N - big.shape[1]))
        big = np.pad(big, pad, mode="edge")
    axis_b = np.linspace(-WORLD / 2.0, WORLD / 2.0, OUT_N)
    Xb, Zb = np.meshgrid(axis_b, axis_b)
    gz, gx = np.gradient(big, WORLD / (OUT_N - 1))
    slope = np.hypot(gx, gz)
    steep = smoothstep(0.35, 0.95, slope)
    # Estratos de rocha nas paredes (BOTW lê penhascos em bandas horizontais)
    strata = np.sin(big * (2.0 * np.pi / 7.5))
    big += 0.85 * steep * strata
    big += 1.5 * steep * (fbm(OUT_N, 180, 2, rng) - 0.5) * 2.0
    # Reprotege praça e artérias do detalhe fino
    r_b = np.hypot(Xb, Zb)
    guard = smoothstep(150.0, 70.0, r_b)
    big = big * (1.0 - guard) + ndimage.gaussian_filter(big, sigma=6.0, mode="nearest") * guard

    big = soft_knee(big, knee=140.0, top=MAX_H - 0.5)
    big = np.clip(big, 0.0, MAX_H - 0.5)
    print(
        f"    cotas: min {big.min():.1f} max {big.max():.1f} média {big.mean():.1f} | "
        f"vale(r<120) {big[r_b < 120].mean():.1f}"
    )
    deg = np.degrees(np.arctan(slope))
    print(f"    declive: média {deg.mean():.1f}° p95 {np.percentile(deg, 95):.1f}° max {deg.max():.1f}°")

    raw = np.clip(np.round(big / MAX_H * 65535.0), 0, 65535).astype("<u2")
    meta = json.dumps(
        {"worldSize": WORLD, "maxHeight": MAX_H, "originX": 0, "originZ": 0},
        separators=(",", ":"),
    ).encode()
    payload = zlib.compress(raw.tobytes(), 9)
    header = struct.pack("<IHHHIII", 0x54474841, 1, OUT_N, OUT_N, 0, 0, len(meta))
    # header: magic(4) ver(2) w(2) d(2) pad(2) reservado(4) reservado(4)... 20 bytes
    header = (
        struct.pack("<I", 0x54474841)
        + struct.pack("<H", 1)
        + struct.pack("<H", OUT_N)
        + struct.pack("<H", OUT_N)
        + b"\x00" * 6
        + struct.pack("<I", len(meta))
    )
    assert len(header) == 20, len(header)
    Path(args.out).write_bytes(header + meta + payload)
    print(f"→ {args.out}  ({(len(header) + len(meta) + len(payload)) / 1e6:.1f} MB)")

    if args.preview:
        from PIL import Image

        img = (big / MAX_H * 255.0).astype(np.uint8)
        Image.fromarray(img[::-1]).save(args.preview)
        print(f"→ {args.preview}")


if __name__ == "__main__":
    main()
