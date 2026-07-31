#!/usr/bin/env python3
"""Esculpe o relevo do Vale do Cristal por cima do heightmap gerado pelo Terrain3D.

Porquê
------
O heightmap do `terrain-diffusion` é bonito à escala do continente, mas a zona
jogável (±160 m à volta da cidade) sai praticamente plana: 32→45 m de variação
em 320 m. Sem verticalidade, os quatro biomas cardeais só se distinguem pelo
nevoeiro, o horizonte não fecha e "Picos Gelados" é um campo que *desce*.

Este script relê o heightmap original (`heightmap.base.png`, nunca escrito) e
escreve `heightmap.png` com:

  · vale central re-assentado em VALLEY_Y (o terrain-diffusion é incondicional
    — a altitude do centro é lotaria de seed; o contrato do mundo exige ~36 m);
  · vale central intocado (cidade, anel, rio e pontes mantêm as cotas de hoje);
  · maciço a oeste com corredor até à arena do ogro (Picos Gelados);
  · dunas a leste (Deserto) e bacia rebaixada a sul (Pântano);
  · colinas a norte (Floresta Sombria);
  · cristas nas diagonais — separam as cunhas em vales próprios;
  · anel montanhoso na borda do mundo a fechar o horizonte;
  · patamares planos nos landmarks (arena, choça, oásis, ninho, …) e um vale
    fluvial ao longo do path do `<River>` para a água não subir cristas.

O detalhe de alta frequência do heightmap original é preservado e amplificado
nas zonas esculpidas, para o relevo novo não parecer liso demais.

Determinístico: mesma entrada ⇒ mesma saída. Correr de novo é seguro.

    python3 scripts/sculpt_terrain.py            # escreve heightmap.png
    python3 scripts/sculpt_terrain.py --restore  # repõe o original
    python3 scripts/sculpt_terrain.py --report   # só imprime perfis, não escreve
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

TERRAIN_DIR = Path(__file__).resolve().parent.parent / "public" / "assets" / "terrain"
BASE_PNG = TERRAIN_DIR / "heightmap.base.png"
OUT_PNG = TERRAIN_DIR / "heightmap.png"
TERRAIN_JSON = TERRAIN_DIR / "terrain.json"

# --- contratos com o mundo (index.html / public/world) ------------------------
CITY_KEEP_R = 144.0  # vale intocado: pad 192x192 + falloff 32 + folga (world 2×)
CITY_BLEND_R = 232.0  # a partir daqui o relevo esculpido manda
VALLEY_Y = 36.0  # cota do vale central no heightmap original

# Landmarks que precisam de chão plano (x, z, raio_núcleo, raio_falloff).
# Coordenadas vindas de public/world/**.xml — manter sincronizado.
FLAT_ZONES: list[tuple[float, float, float, float]] = [
    # --- Picos Gelados (landmarks/peaks.xml) ---
    (-290.0, 12.0, 52.0, 44.0),  # arena do ogro
    (-190.0, 27.0, 24.0, 28.0),  # jazida de cristais
    (-216.0, 60.0, 28.0, 28.0),  # mina de cristal
    (-172.0, 42.0, 24.0, 28.0),  # posto goblin
    (-184.0, -68.0, 32.0, 32.0),  # lago gelado
    (-156.0, -8.0, 14.0, 20.0),  # mojão 1
    (-200.0, 6.0, 14.0, 20.0),  # mojão 2
    (-244.0, -12.0, 14.0, 20.0),  # mojão 3
    # --- Floresta Sombria (landmarks/forest.xml) ---
    (-16.0, 266.0, 40.0, 40.0),  # clareira da bruxa
    (16.0, 290.0, 28.0, 32.0),  # chefe bruxa
    (36.0, 144.0, 20.0, 24.0),  # bosque encantado
    (-92.0, 174.0, 28.0, 32.0),  # posto avançado arruinado
    (104.0, 236.0, 26.0, 30.0),  # círculo de menires
    (-60.0, 128.0, 28.0, 28.0),  # acampamento de lenhador
    (28.0, 192.0, 18.0, 24.0),  # poço da encruzilhada
    # --- Deserto (landmarks/desert.xml) ---
    (190.0, 64.0, 32.0, 36.0),  # arco monumental
    (140.0, 92.0, 32.0, 32.0),  # oásis
    (170.0, -70.0, 28.0, 32.0),  # acampamento de bandidos
    (260.0, 16.0, 24.0, 28.0),  # ninho de escorpiões
    (290.0, -12.0, 40.0, 40.0),  # chefe verme
    (216.0, -36.0, 26.0, 28.0),  # caravana destruída
    (240.0, 80.0, 26.0, 30.0),  # obelisco solar
    (156.0, -116.0, 22.0, 26.0),  # cisterna seca
    (300.0, 122.0, 30.0, 32.0),  # ruínas do templo
    # --- Pântano (landmarks/swamp.xml) ---
    (16.0, -190.0, 40.0, 36.0),  # lagoa grande
    (-68.0, -156.0, 28.0, 28.0),  # lagoa oeste
    (84.0, -236.0, 28.0, 28.0),  # lagoa leste
    (18.0, -226.0, 24.0, 32.0),  # passadiço
    (20.0, -260.0, 32.0, 36.0),  # choça
    (-12.0, -290.0, 32.0, 36.0),  # chefe bog warden
    (52.0, -184.0, 24.0, 26.0),  # barca encalhada
    (-56.0, -216.0, 26.0, 28.0),  # cemitério afundado
    (-96.0, -264.0, 26.0, 28.0),  # altar de ossos
    (68.0, -248.0, 24.0, 26.0),  # ruína submersa
    # --- Vale — laguinho NW e cabeceiras das pontes ---
    (-80.0, 104.0, 28.0, 28.0),
    (4.0, -116.0, 32.0, 36.0),
    (-108.0, -2.0, 32.0, 36.0),
]

# Path do <River> em spawn/ring.xml (x z x z …). O corredor fluvial é aplanado
# num perfil descendente para a água não ter de subir as cristas novas.
RIVER_PATH: list[tuple[float, float]] = [
    (410, -124),
    (340, -110),
    (280, -90),
    (220, -76),
    (164, -80),
    (96, -116),
    (24, -124),
    (-40, -124),
    (-90, -132),
    (-116, -116),
    (-116, -40),
    (-116, 10),
    (-124, 44),
    (-150, 64),
    (-180, 96),
    (-220, 124),
    (-260, 160),
    (-310, 210),
    (-360, 270),
    (-420, 330),
    (-482, 384),
]
RIVER_CORRIDOR_W = 30.0  # meia-largura do leito aplanado (m) — world 2×
RIVER_CORRIDOR_FALLOFF = 52.0  # blend de volta ao relevo (m)


def smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    """Hermite 3t^2 - 2t^3 entre dois limiares (vetorizado, clampado)."""
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _box_blur_axis(a: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """Média móvel de janela 2r+1 num eixo, via soma acumulada."""
    k = 2 * radius + 1
    pad_spec = [(0, 0)] * a.ndim
    pad_spec[axis] = (radius + 1, radius)
    csum = np.cumsum(np.pad(a, pad_spec, mode="edge"), axis=axis)
    hi = np.take(csum, np.arange(k, csum.shape[axis]), axis=axis)
    lo = np.take(csum, np.arange(0, csum.shape[axis] - k), axis=axis)
    return (hi - lo) / k


def box_blur(a: np.ndarray, radius: int) -> np.ndarray:
    """Blur separável — evita depender do scipy só para uma passagem de média."""
    if radius < 1:
        return a
    return _box_blur_axis(_box_blur_axis(a, radius, 0), radius, 1)


def load_terrain_meta() -> tuple[float, float]:
    meta = json.loads(TERRAIN_JSON.read_text())["terrain"]
    return float(meta["world_size"]), float(meta["max_height"])


def seat_base_to_valley(base_m: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Re-assenta o vale central na cota VALLEY_Y.

    O terrain-diffusion é incondicional: a altitude do centro do mapa é
    lotaria de seed/região (um seed dá 36 m, outro 133 m). O contrato do
    mundo — cotas dos landmarks, perfil do rio, campos do sculpt — parte de
    VALLEY_Y no vale, por isso o base é transladado na vertical até a média
    do disco central (r < CITY_KEEP_R) bater em VALLEY_Y.

    O micro-relevo é invariante a translações (o campo `detail` não muda) e
    fora do vale quem manda é o sculpt; vales profundos que fiquem abaixo de
    zero são cortados no piso 0. Determinístico: mesmo base ⇒ mesmo shift.
    """
    center = base_m[r < CITY_KEEP_R]
    if center.size == 0:
        return base_m
    shift = float(center.mean()) - VALLEY_Y
    if abs(shift) < 1.0:
        return base_m
    seated = np.clip(base_m - shift, 0.0, None)
    print(f"vale re-assentado: centro do base {float(center.mean()):.0f} m -> {VALLEY_Y:.0f} m (shift {-shift:+.0f} m)")
    return seated


def build_grid(n: int, world: float) -> tuple[np.ndarray, np.ndarray]:
    """Coordenadas de mundo (x, z) por píxel.

    Convenção do sampler: u cresce com +x; a linha 0 da imagem é +z (topo).
    """
    axis = (np.arange(n) / (n - 1) - 0.5) * world
    x = np.broadcast_to(axis[None, :], (n, n))
    z = np.broadcast_to(axis[::-1][:, None], (n, n))
    return x.astype(np.float64), z.astype(np.float64)


def wedge_weights(x: np.ndarray, z: np.ndarray) -> dict[str, np.ndarray]:
    """Peso suave de cada cunha cardeal + diagonais (soma ≈ 1)."""
    taper = 52.0
    ax, az = np.abs(x), np.abs(z)
    north = smoothstep(0.0, taper, z - ax)
    south = smoothstep(0.0, taper, -z - ax)
    east = smoothstep(0.0, taper, x - az)
    west = smoothstep(0.0, taper, -x - az)
    cardinal = north + south + east + west
    diag = np.clip(1.0 - cardinal, 0.0, 1.0)
    return {"n": north, "s": south, "e": east, "w": west, "d": diag}


def sculpt(x: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Campo de altura alvo (metros) antes do blend com o vale central."""
    r = np.hypot(x, z)
    w = wedge_weights(x, z)

    # --- Norte: Floresta Sombria — colinas largas, declives que as árvores
    #     ainda aceitam (spawners cortam a >38°) --------------------------------
    hills = (np.sin(x / 46.0) * np.cos(z / 58.0) + 0.6 * np.sin((x + z) / 31.0)) * 7.0
    north = VALLEY_Y + 38.0 * smoothstep(70.0, 210.0, r) + hills * smoothstep(70.0, 120.0, r)

    # --- Leste: Deserto — dunas em bandas, sobe para o interior --------------
    dunes = 10.0 * np.sin(x / 41.0 + 0.7 * np.sin(z / 74.0)) + 4.5 * np.sin(z / 33.0)
    east = VALLEY_Y + 40.0 * smoothstep(70.0, 220.0, r) + dunes * smoothstep(70.0, 115.0, r)

    # --- Sul: Pântano — bacia rebaixada, depois rebordo ---------------------
    bowl = -13.0 * smoothstep(66.0, 110.0, r) * (1.0 - smoothstep(150.0, 250.0, r))
    south = VALLEY_Y + bowl + 62.0 * smoothstep(190.0, 320.0, r)

    # --- Oeste: Picos Gelados — maciço com corredor até à arena -------------
    # Corredor livre em |z| ≲ 18 (caminho do portão oeste à arena do ogro).
    # Declive dos flancos ≈ 40°: a textura do terreno é mapeada em plano, e a
    # partir de ~50° estica em riscas verticais que parecem chuva branca na
    # encosta. Manter a subida espalhada por ~80 m em vez de ~40 m resolve —
    # o volume da montanha vem do alcance, não da inclinação.
    # O declive máximo de um smoothstep de altura H sobre largura W é 1.5·H/W:
    # com H=84 m em W=82 m dava ~52°. H=56 m em W=122 m dá ~35°.
    flank = smoothstep(18.0, 140.0, np.abs(z))
    corridor_floor = VALLEY_Y + 24.0 * smoothstep(70.0, 200.0, r)
    ridge = 56.0 * smoothstep(72.0, 195.0, r) * flank + 44.0 * smoothstep(160.0, 300.0, r) * flank
    west = corridor_floor + ridge

    # --- Diagonais: cristas que separam as cunhas em vales próprios ---------
    diag = VALLEY_Y + 84.0 * smoothstep(72.0, 215.0, r) + 48.0 * smoothstep(200.0, 330.0, r)

    h = w["n"] * north + w["s"] * south + w["e"] * east + w["w"] * west + w["d"] * diag

    # --- Borda do mundo: anel montanhoso a fechar o horizonte ---------------
    # Piso, não soma — e só a partir de r=300, senão o VALLEY_Y do próprio piso
    # impedia a bacia do pântano de descer abaixo dos 36 m.
    rim = VALLEY_Y + 168.0 * smoothstep(300.0, 560.0, r)
    return np.maximum(h, np.where(r < 300.0, -1.0e9, rim))


def apply_flat_zones(h: np.ndarray, x: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Assenta patamares planos nos landmarks (cota = média do núcleo)."""
    out = h.copy()
    for cx, cz, core, falloff in FLAT_ZONES:
        d = np.hypot(x - cx, z - cz)
        inner = d <= core
        if not inner.any():
            continue
        target = float(np.mean(h[inner]))
        blend = 1.0 - smoothstep(core, core + falloff, d)
        out = out * (1.0 - blend) + target * blend
    return out


def _window(n: int, world: float, cx: float, cz: float, reach: float) -> tuple[slice, slice]:
    """Sub-janela de píxeis que cobre um disco de raio `reach` em (cx, cz)."""
    px = (n - 1) / world
    col = (cx + world / 2.0) * px
    row = (n - 1) - (cz + world / 2.0) * px
    pad = reach * px + 1.0
    r0 = int(max(0, np.floor(row - pad)))
    r1 = int(min(n, np.ceil(row + pad) + 1))
    c0 = int(max(0, np.floor(col - pad)))
    c1 = int(min(n, np.ceil(col + pad) + 1))
    return slice(r0, r1), slice(c0, c1)


def apply_river_valley(h: np.ndarray, x: np.ndarray, z: np.ndarray, world: float) -> np.ndarray:
    """Talha um vale ao longo do path do <River> com perfil monotonicamente
    descendente — sem isto a água atravessaria as cristas novas a subir.

    Trabalha por janelas à volta de cada estação: a grelha é 2048², percorrê-la
    inteira por estação seria ~800 M operações por passagem.
    """
    n = h.shape[0]
    pts = np.array(RIVER_PATH, dtype=np.float64)
    seg = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    n_st = max(2, int(arc[-1] / 4.0))
    s = np.linspace(0.0, arc[-1], n_st)
    sx = np.interp(s, arc, pts[:, 0])
    sz = np.interp(s, arc, pts[:, 1])

    # Cota de cada estação: mínimo local do relevo esculpido, suavizado e
    # forçado a descer da nascente (índice 0) para a foz.
    profile = np.full(n_st, VALLEY_Y)
    for i in range(n_st):
        rs, cs = _window(n, world, sx[i], sz[i], 26.0)
        d = np.hypot(x[rs, cs] - sx[i], z[rs, cs] - sz[i])
        near = d <= 26.0
        if near.any():
            profile[i] = float(np.min(h[rs, cs][near]))
    profile = np.convolve(np.pad(profile, 4, mode="edge"), np.ones(9) / 9.0, mode="valid")
    profile = np.minimum.accumulate(profile)  # nunca sobe

    reach = RIVER_CORRIDOR_W + RIVER_CORRIDOR_FALLOFF
    dist = np.full(h.shape, 1e9)
    target = np.zeros_like(h)
    for i in range(n_st):
        rs, cs = _window(n, world, sx[i], sz[i], reach)
        d = np.hypot(x[rs, cs] - sx[i], z[rs, cs] - sz[i])
        closer = d < dist[rs, cs]
        dist[rs, cs] = np.where(closer, d, dist[rs, cs])
        target[rs, cs] = np.where(closer, profile[i], target[rs, cs])

    blend = 1.0 - smoothstep(RIVER_CORRIDOR_W, reach, dist)
    return h * (1.0 - blend) + target * blend


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--restore", action="store_true", help="repõe heightmap.base.png")
    ap.add_argument("--report", action="store_true", help="só imprime perfis")
    args = ap.parse_args()

    if not BASE_PNG.exists():
        print(f"ERRO: falta {BASE_PNG} (cópia intocada do heightmap do Terrain3D).")
        return 1

    if args.restore:
        shutil.copyfile(BASE_PNG, OUT_PNG)
        print(f"reposto {OUT_PNG.name} a partir de {BASE_PNG.name}")
        return 0

    world, max_h = load_terrain_meta()
    img = Image.open(BASE_PNG)
    arr = np.asarray(img)
    if arr.ndim == 3:
        arr = arr[..., 0]
    n = arr.shape[0]
    levels = 65535.0 if arr.dtype == np.uint16 else 255.0
    base_m = arr.astype(np.float64) / levels * max_h

    x, z = build_grid(n, world)
    r = np.hypot(x, z)

    # Re-assenta o vale na cota do contrato antes de qualquer blend — sem
    # isto um base com centro alto (lotaria do seed) cria uma falésia no anel
    # de transição e descalibrava rio/landmarks.
    base_m = seat_base_to_valley(base_m, r)

    target = sculpt(x, z)

    # Micro-relevo do heightmap original, amplificado onde subimos terreno —
    # sem isto os flancos novos saem lisos como plástico.
    px_per_m = (n - 1) / world
    detail = base_m - box_blur(base_m, max(1, round(22.0 * px_per_m)))
    gain = 1.0 + 1.8 * smoothstep(VALLEY_Y + 6.0, VALLEY_Y + 70.0, target)
    target = target + detail * gain

    target = apply_flat_zones(target, x, z)
    target = apply_river_valley(target, x, z, world)

    # Vale central intocado; transição suave para o relevo esculpido.
    keep = 1.0 - smoothstep(CITY_KEEP_R, CITY_BLEND_R, r)
    out_m = base_m * keep + target * (1.0 - keep)
    out_m = np.clip(out_m, 0.0, max_h)

    if args.report:
        report(out_m, base_m, world, max_h, n)
        return 0

    out = np.rint(out_m / max_h * levels).astype(arr.dtype)
    Image.fromarray(out, mode=img.mode).save(OUT_PNG)
    print(f"escrito {OUT_PNG.name}  ({n}x{n}, {img.mode}, max_height={max_h:.0f} m)")
    report(out_m, base_m, world, max_h, n)
    return 0


def report(out_m: np.ndarray, base_m: np.ndarray, world: float, max_h: float, n: int) -> None:
    def h_at(field: np.ndarray, wx: float, wz: float) -> float:
        u = round((wx + world / 2) / world * (n - 1))
        v = round((wz + world / 2) / world * (n - 1))
        return float(field[n - 1 - v, u])

    print(f"{'eixo':<14}{'0':>6}{'40':>6}{'80':>6}{'120':>6}{'160':>6}{'220':>6}{'300':>6}")
    axes = {
        "N floresta": lambda d: (0.0, d),
        "S pântano": lambda d: (0.0, -d),
        "E deserto": lambda d: (d, 0.0),
        "O picos": lambda d: (-d, 0.0),
        "NE diagonal": lambda d: (d * 0.707, d * 0.707),
        "SO diagonal": lambda d: (-d * 0.707, -d * 0.707),
    }
    for name, fn in axes.items():
        row = "".join(f"{h_at(out_m, *fn(d)):>6.0f}" for d in (0, 40, 80, 120, 160, 220, 300))
        print(f"{name:<14}{row}")
    print(f"antes: {base_m.min():.0f}-{base_m.max():.0f} m   depois: {out_m.min():.0f}-{out_m.max():.0f} m")


if __name__ == "__main__":
    sys.exit(main())
