#!/usr/bin/env python3
"""Regenerate cities/discordia/walls.xml for a compact Discordia.

Placement uses **visual** LOD0 extents (collision is ~10-15 cm larger).
Pitch from collision left visible gaps between segs/gate; packing to the
painted mesh closes them. Colliders then interpenetrate — ``overlap-max``
covers that depth for ``vibegame analyze``.

Measured LOD0 / collision (metres, long x thick):
  city_wall_seg_a/b : visual 6.500 x ~0.94-1.07 ; coll 6.615 x ~1.06-1.19
  city_wall_corner  : visual ~2.426 x 2.438     ; coll 2.565 x 2.581
  city_gate_arch    : visual 9.999 x 1.509       ; coll 10.146 x 1.675
"""

from __future__ import annotations

# Visual extents (LOD0 scene bbox) — placement authority for seamless joints.
SEG = 6.50
GATE = 10.00
CORNER = 2.43
JOINT = 0.12  # visual AABB overlap — mesh ends inset vs bbox; 5 cm left slits
N = 5  # segments per half-run
# seg_b (thicker, buttress-looking) every 3rd slot, as before
B_SLOTS = {2}

PITCH = SEG - JOINT
FIRST = GATE / 2 + SEG / 2 - JOINT
# Half-extent so gate + N segs + corner meet with JOINT at every contact.
S = round(GATE / 2 + N * SEG + CORNER / 2 - (N + 1) * JOINT, 2)
# Collision longer than visual → penetration ≈ coll - pitch; budget headroom.
OVERLAP_MAX = 0.3

CENTRES = [round(FIRST + i * PITCH, 2) for i in range(N)]


def seg_kind(i: int) -> str:
    return "b" if i in B_SLOTS else "a"


def fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def gltf(mesh: str, near: int = 70, mid: int = 160, mesh_dir: str = "infra") -> str:
    return f"""            <GLTFLoader
              role="visual"
              url="/assets/meshes/{mesh_dir}/{mesh}_lod0.glb"
              lod1-url="/assets/meshes/{mesh_dir}/{mesh}_lod1.glb"
              lod2-url="/assets/meshes/{mesh_dir}/{mesh}_lod2.glb"
              lod-threshold-near="{near}"
              lod-threshold-mid="{mid}"
            ></GLTFLoader>"""


def obj(
    name: str, x: float, z: float, rot: int | None, mesh: str, near: int = 70, mid: int = 160, mesh_dir: str = "infra"
) -> str:
    rot_attr = f'\n            transform="rotation: 0 {rot} 0"' if rot is not None else ""
    return f"""          <GameObject
            name="{name}"
            place="at: {fmt(x)} {fmt(z)}; align-to-terrain: 0"{rot_attr}
            overlap-max="{OVERLAP_MAX}"
            rigidbody="type: fixed; mass: 0; gravity-scale: 0"
            collider="shape: trimesh; mesh-url: /assets/meshes/{mesh_dir}/{mesh}_collision.glb; mesh-anchor: base"
          >
{gltf(mesh, near, mid, mesh_dir)}
          </GameObject>"""


def torch_flame(x: float, z: float) -> str:
    """Chama + luz sobre um torch_post (haste sólida até y=2.15, como na praça)."""
    emitter = (
        "preset: fire; emission-rate: 22; shape-radius: 0.06; start-life-min: 0.3; start-life-max: 0.8; "
        "start-speed-min: 0.9; start-speed-max: 1.9; start-size-min: 0.16; start-size-max: 0.34; "
        "looping: 1; world-space: 1"
    )
    return f"""          <GameObject place="at: {fmt(x)} {fmt(z)}; align-to-terrain: 0">
            <ParticleSystem
              preset="fire"
              transform="pos: 0 2.14 0"
              particle-emitter="{emitter}"
            ></ParticleSystem>
          </GameObject>
          <Composition
            place="at: {fmt(x)} {fmt(z)}; align-to-terrain: 0"
            body="fixed"
            collider="none"
          >
            <PointLight
              pos="0 2.2 0"
              point-light="color: 0xffa83a; intensity: 1.4; distance: 6; decay: 2; cast-shadow: 0"
            ></PointLight>
          </Composition>"""


out: list[str] = []
out.append(
    f"""<!--
  Discordia district fragment. Loaded via Include from cities/discordia.xml.
  Edit this file for this district only. Cell coords: space-separated (at="2 1").

  GERADO — layout compacto. Muralha ±{fmt(S)}: passo visual {fmt(PITCH)} m
  (mesh LOD0 {fmt(SEG)} m - junta {fmt(JOINT)} m). Collider é ~0.12 m mais
  longo → penetração coberta por `overlap-max={OVERLAP_MAX}`.
-->
<Group name="city.walls" pos="0 0 0">
          <!-- §4.1 Muralha GLB: semi-lado {fmt(S)}, vão visual {fmt(GATE)} m,
           segs @{fmt(PITCH)} m. Mesh-anchor base; collider envelope. -->"""
)

# corners
for name, sx, sz, rot in (
    ("wall_corner_nw", -1, 1, None),
    ("wall_corner_ne", 1, 1, 90),
    ("wall_corner_se", 1, -1, 180),
    ("wall_corner_sw", -1, -1, -90),
):
    out.append(obj(name, sx * S, sz * S, rot, "city_wall_corner"))

# runs: (prefix, rotation, coordinate builder)
runs = [
    ("wall_n_w", None, lambda d: (-d, S)),
    ("wall_n_e", None, lambda d: (d, S)),
    ("wall_s_e", 180, lambda d: (d, -S)),
    ("wall_s_w", 180, lambda d: (-d, -S)),
    ("wall_e_n", 90, lambda d: (S, d)),
    ("wall_e_s", 90, lambda d: (S, -d)),
    ("wall_w_s", -90, lambda d: (-S, -d)),
    ("wall_w_n", -90, lambda d: (-S, d)),
]
for prefix, rot, at in runs:
    for i, d in enumerate(CENTRES):
        x, z = at(d)
        out.append(obj(f"{prefix}{i}", x, z, rot, f"city_wall_seg_{seg_kind(i)}"))

out.append("          <!-- Portões cardeais (sync RESPAWN_POINTS em src/main.ts). -->")
for name, x, z, rot in (
    ("gate_north", 0, S, None),
    ("gate_south", 0, -S, 180),
    ("gate_east", S, 0, 90),
    ("gate_west", -S, 0, -90),
):
    out.append(obj(name, x, z, rot, "city_gate_arch"))

out.append("          <!-- Tochas nos portões (torch_post, grupo village) com")
out.append("               chama e luz — portões apagados liam-se como tripés")
out.append("               pretos à noite. -->")
T = 5.8
for name, x, z, rot in (
    ("gate_n_torch_w", -T, S, None),
    ("gate_n_torch_e", T, S, None),
    ("gate_s_torch_w", -T, -S, 180),
    ("gate_s_torch_e", T, -S, 180),
    ("gate_e_torch_s", S, -T, 90),
    ("gate_e_torch_n", S, T, 90),
    ("gate_w_torch_s", -S, -T, -90),
    ("gate_w_torch_n", -S, T, -90),
):
    out.append(obj(name, x, z, rot, "torch_post", near=50, mid=120, mesh_dir="village"))
    out.append(torch_flame(x, z))

out.append("</Group>")

print("\n".join(out))
