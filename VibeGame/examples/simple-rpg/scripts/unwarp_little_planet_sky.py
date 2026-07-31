"""Little-planet -> equirect, with fitted horizon ellipse and zenith cap fill."""

from __future__ import annotations

import sys

import numpy as np
from PIL import Image

# Usage: unwarp_little_planet_sky.py SRC DST [OUT_W] [--undo-roll]
# --undo-roll: legacy Skymap2D always rolled 50% even on little-planet frames.
# Current generator skips the roll when it detects little-planet — omit the flag.
args = [a for a in sys.argv[1:] if a != "--undo-roll"]
undo_roll = "--undo-roll" in sys.argv[1:]
SRC, DST = args[0], args[1]
OUT_W = int(args[2]) if len(args) > 2 else 4096
OUT_H = OUT_W // 2

src = np.asarray(Image.open(SRC).convert("RGB")).astype(np.float32)
if undo_roll:
    src = np.roll(src, src.shape[0] // 2, axis=0)
H, W, _ = src.shape

# --- fit the horizon ellipse to the green planet disk -----------------------
green = (src[:, :, 1] > src[:, :, 2] + 15) & (src[:, :, 1] > 60)
ys, xs = np.nonzero(green)
cx0, cy0 = xs.mean(), ys.mean()
# Boundary radius per angular bin, then least-squares fit of an axis-aligned
# ellipse ((x-cx)/rx)^2 + ((y-cy)/ry)^2 = 1 over those boundary points.
ang = np.arctan2(ys - cy0, xs - cx0)
rad = np.hypot(xs - cx0, ys - cy0)
NB = 720
bins = ((ang + np.pi) / (2 * np.pi) * NB).astype(int) % NB
edge_r = np.zeros(NB)
for b in range(NB):
    m = bins == b
    if m.any():
        edge_r[b] = np.percentile(rad[m], 99.0)
ba = (np.arange(NB) + 0.5) / NB * 2 * np.pi - np.pi
bx = cx0 + edge_r * np.cos(ba)
by = cy0 + edge_r * np.sin(ba)
keep = edge_r > 0
bx, by = bx[keep], by[keep]

# Solve A x^2 + B x + C y^2 + D y = 1 (axis-aligned conic).
M = np.stack([bx**2, bx, by**2, by], axis=1)
coef, *_ = np.linalg.lstsq(M, np.ones(len(bx)), rcond=None)
A, B, C, D = coef
CX = -B / (2 * A)
CY = -D / (2 * C)
k = 1 + A * CX**2 + C * CY**2
RX = float(np.sqrt(k / A))
RY = float(np.sqrt(k / C))
print(f"fitted centre=({CX:.1f},{CY:.1f}) rx={RX:.1f} ry={RY:.1f}")

# --- inverse stereographic --------------------------------------------------
u = (np.arange(OUT_W, dtype=np.float32) + 0.5) / OUT_W
v = (np.arange(OUT_H, dtype=np.float32) + 0.5) / OUT_H
uu, vv = np.meshgrid(u, v)
phi = uu * 2.0 * np.pi
theta_nadir = np.pi - vv * np.pi
r = np.tan(np.clip(theta_nadir, 0.0, np.pi - 1e-4) / 2.0)
sx = CX + r * RX * np.cos(phi)
sy = CY + r * RY * np.sin(phi)

valid = (sx >= 0) & (sx <= W - 1) & (sy >= 0) & (sy <= H - 1)
sxc, syc = np.clip(sx, 0, W - 1), np.clip(sy, 0, H - 1)
x0, y0 = np.floor(sxc).astype(np.int32), np.floor(syc).astype(np.int32)
x1, y1 = np.minimum(x0 + 1, W - 1), np.minimum(y0 + 1, H - 1)
fx, fy = (sxc - x0)[..., None], (syc - y0)[..., None]
out = (
    src[y0, x0] * (1 - fx) * (1 - fy)
    + src[y0, x1] * fx * (1 - fy)
    + src[y1, x0] * (1 - fx) * fy
    + src[y1, x1] * fx * fy
)

# --- fill the missing zenith cap --------------------------------------------
# Rows are ordered zenith -> nadir; the invalid region is a cap at the top whose
# lower edge wobbles with azimuth (the source canvas is 2:1, not square).
first_valid = np.argmax(valid, axis=0)  # per column
ring = out[np.clip(first_valid + 4, 0, OUT_H - 1), np.arange(OUT_W)]
# Smooth the ring around the azimuth (wrap) so the cap does not inherit the
# blotches of whatever cloud happened to sit at the source canvas border.
KERN = 257
w = np.hanning(KERN)
w /= w.sum()
ring = np.stack(
    [np.convolve(np.r_[ring[-KERN:, c], ring[:, c], ring[:KERN, c]], w, "same")[KERN:-KERN] for c in range(3)],
    axis=1,
)
zenith = ring.mean(axis=0)
# Deepen the zenith slightly so the dome does not read as a flat wash.
zenith = np.clip(zenith * np.array([0.82, 0.88, 1.0], np.float32), 0, 255)

rows = np.arange(OUT_H)[:, None]
edge = np.maximum(first_valid[None, :], 1)
t = np.clip(rows / edge, 0.0, 1.0)[..., None]  # 0 at zenith, 1 at cap edge
smooth = t * t * (3 - 2 * t)
fill = zenith[None, None, :] * (1 - smooth) + ring[None, :, :] * smooth
out = np.where(valid[..., None], out, fill)

Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(DST)
print("wrote", DST, OUT_W, "x", OUT_H)
