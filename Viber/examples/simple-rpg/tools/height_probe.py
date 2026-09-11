#!/usr/bin/env python3
"""Probe de alturas do heightmap `.ahgt` do Viber (ferramenta de autoria).

Lê o grid u16 do ficheiro e responde alturas/declives em coordenadas de
MUNDO, para colocar landmarks sem os enterrar. Espelha o contrato do
`HeightSampler` da engine (`src/terrain/sampler.rs`):

    grid = (world + world_size/2) / world_size * (n - 1)
    altura = raw / 65535 * max_height

O que isto devolve é a altura BASE (antes dos carves de pads/lagos/rios/
estradas e antes dos sólidos voxel de cliffs): fora dessas zonas coincide
com a superfície desenhada.

Uso:
    python3 tools/height_probe.py 104 236 -605 -725 1480 -700
    python3 tools/height_probe.py --scan            # malha grosseira 250 m
    python3 tools/height_probe.py --scan --step 100
    python3 tools/height_probe.py --profile 104 236 1480 -700   # cortes ao longo de uma linha
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import zlib
from pathlib import Path

DEFAULT_AHGT = (
    Path(__file__).resolve().parents[2]
    / "shared-assets/public/assets/terrain/terrain.ahgt"
)


class HeightField:
    def __init__(self, path: Path) -> None:
        data = path.read_bytes()
        magic, version, width, depth = struct.unpack_from("<IHHH", data, 0)
        if magic != 0x54474841:  # "AHGT" LE
            raise SystemExit(f"{path}: magic inválido 0x{magic:08x}")
        if version != 1:
            raise SystemExit(f"{path}: versão {version} não suportada")
        meta_len = struct.unpack_from("<I", data, 16)[0]
        meta = json.loads(data[20 : 20 + meta_len])
        payload = data[20 + meta_len :]
        try:
            raw = zlib.decompress(payload)
        except zlib.error:
            raw = zlib.decompress(payload, -15)
        self.width = width
        self.depth = depth
        self.world_size = float(meta["worldSize"])
        self.max_height = float(meta["maxHeight"])
        self.grid = struct.unpack_from(f"<{width * depth}H", raw, 0)

    def _grid_coord(self, world: float, n: int) -> float:
        g = (world + self.world_size / 2.0) / self.world_size * (n - 1)
        return min(max(g, 0.0), float(n - 1))

    def _texel(self, ix: int, iz: int) -> float:
        ix = min(max(ix, 0), self.width - 1)
        iz = min(max(iz, 0), self.depth - 1)
        return self.grid[iz * self.width + ix] / 65535.0 * self.max_height

    @staticmethod
    def _monotone(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
        """Catmull-Rom (tenso 0.5) clampado ao intervalo dos dois centrais.

        Espelha `monotone`/`cubic` do `src/terrain/sampler.rs`: sem o clamp o
        cubo passa por cima de degraus (pads, carves, cortes de estrada) e a
        altura amostrada mente exatamente onde interessa.
        """
        a = 2.0 * p1
        b = p2 - p0
        c = 2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3
        d = -p0 + 3.0 * p1 - 3.0 * p2 + p3
        v = 0.5 * (a + b * t + c * t * t + d * t * t * t)
        return min(max(v, min(p1, p2)), max(p1, p2))

    def height(self, x: float, z: float, smoothing: float = 1.0) -> float:
        """Altura da superfície base — `height-smoothing` como no `<Terrain>`."""
        gx = self._grid_coord(x, self.width)
        gz = self._grid_coord(z, self.depth)
        x0, z0 = int(math.floor(gx)), int(math.floor(gz))
        x1, z1 = min(x0 + 1, self.width - 1), min(z0 + 1, self.depth - 1)
        tx, tz = gx - x0, gz - z0
        h = self._texel
        top = h(x0, z0) * (1 - tx) + h(x1, z0) * tx
        bot = h(x0, z1) * (1 - tx) + h(x1, z1) * tx
        bilinear = top * (1 - tz) + bot * tz
        if smoothing <= 0.0:
            return bilinear
        rows = [
            self._monotone(h(x0 - 1, z), h(x0, z), h(x1, z), h(x0 + 2, z), tx)
            for z in (z0 - 1, z0, z1, z0 + 2)
        ]
        smooth = self._monotone(*rows, tz)
        if smoothing >= 1.0:
            return smooth
        return bilinear + (smooth - bilinear) * smoothing

    def slope_deg(self, x: float, z: float, reach: float = 6.0) -> float:
        """Declive médio de uma pega de `reach` metros em torno do ponto."""
        dx = self.height(x + reach, z) - self.height(x - reach, z)
        dz = self.height(x, z + reach) - self.height(x, z - reach)
        grad = math.hypot(dx, dz) / (2.0 * reach)
        return math.degrees(math.atan(grad))

    def relief(self, x: float, z: float, reach: float = 40.0) -> float:
        """Amplitude (max-min) da vizinhança — mede se o sítio é encosta."""
        samples = [
            self.height(x + dx, z + dz)
            for dx in (-reach, 0.0, reach)
            for dz in (-reach, 0.0, reach)
        ]
        return max(samples) - min(samples)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("coords", nargs="*", type=float, help="pares x z")
    ap.add_argument("--ahgt", type=Path, default=DEFAULT_AHGT)
    ap.add_argument("--scan", action="store_true", help="malha de alturas + declives")
    ap.add_argument("--step", type=float, default=250.0, help="passo da malha (m)")
    ap.add_argument("--extent", type=float, default=1750.0, help="meia-largura da malha")
    ap.add_argument("--profile", action="store_true", help="corte ao longo dos pontos dados")
    ap.add_argument("--samples", type=int, default=24, help="amostras por corte")
    args = ap.parse_args()

    field = HeightField(args.ahgt)
    print(
        f"# {args.ahgt.name}: {field.width}×{field.depth} texels, "
        f"world {field.world_size:.0f} m, max {field.max_height:.0f} m",
        file=sys.stderr,
    )

    if args.scan:
        step = args.step
        extent = args.extent
        n = int(extent * 2 / step) + 1
        print(f"# malha {n}×{n}, passo {step:.0f} m, coordenadas = canto superior esquerdo")
        header = "        " + "".join(f"{int(-extent + i * step):>7d}" for i in range(n))
        print(header)
        for j in range(n):
            z = -extent + j * step
            row = "".join(
                f"{field.height(-extent + i * step, z):>7.0f}" for i in range(n)
            )
            print(f"{int(z):>7d} {row}")
        print()
        print("# declives (graus)")
        print(header)
        for j in range(n):
            z = -extent + j * step
            row = "".join(
                f"{field.slope_deg(-extent + i * step, z):>7.0f}" for i in range(n)
            )
            print(f"{int(z):>7d} {row}")
        return 0

    coords = args.coords
    if args.profile:
        if len(coords) < 4 or len(coords) % 2:
            raise SystemExit("--profile precisa de pares x z (>=2 pontos)")
        pts = list(zip(coords[0::2], coords[1::2]))
        for (ax, az), (bx, bz) in zip(pts, pts[1:]):
            dist = math.hypot(bx - ax, bz - az)
            print(f"# corte ({ax:.0f},{az:.0f}) -> ({bx:.0f},{bz:.0f})  {dist:.0f} m")
            for i in range(args.samples + 1):
                t = i / args.samples
                x = ax + (bx - ax) * t
                z = az + (bz - az) * t
                print(
                    f"  t={t:4.2f}  ({x:8.1f},{z:8.1f})  "
                    f"h={field.height(x, z):7.1f}  "
                    f"declive={field.slope_deg(x, z):5.1f}°  "
                    f"relevo40={field.relief(x, z):5.1f}"
                )
        return 0

    if len(coords) % 2:
        raise SystemExit("os pontos têm de vir em pares x z")
    for x, z in zip(coords[0::2], coords[1::2]):
        print(
            f"({x:8.1f},{z:8.1f})  h={field.height(x, z):7.1f}  "
            f"declive={field.slope_deg(x, z):5.1f}°  "
            f"relevo40={field.relief(x, z):5.1f}  "
            f"relevo12={field.relief(x, z, 12):5.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
