#!/usr/bin/env python3
"""
Define a origem de cada mesh no centro da base da AABB (mínimo no eixo vertical).

Executar com o Python do Blender (não com python.exe normal):

  blender --background --python blender_reorigin_glb_feet.py -- <pasta_public>

  # Só alguns modelos (repetir --only por ficheiro):
  blender --background --python blender_reorigin_glb_feet.py -- \\
    --only path/para/a.glb --only path/para/b.glb

Exemplo:

  blender --background --python VibeGame/tools/blender_reorigin_glb_feet.py -- VibeGame/examples/simple-rpg/public

O importador glTF do Blender converte Y-up (glTF) para Z-up (Blender); por defeito
o eixo vertical tratado como "altura" é Z. Usa --axis Y se os teus ficheiros já
estiverem orientados com Y para cima no Blender.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path


def _argv_after_dd() -> list[str]:
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1 :]
    return []


def _clear_scene() -> None:
    import bpy

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for block in list(bpy.data.meshes):
        bpy.data.meshes.remove(block)
    for block in list(bpy.data.armatures):
        bpy.data.armatures.remove(block)
    for block in list(bpy.data.materials):
        bpy.data.materials.remove(block)


def _world_bbox_axes(obj) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    from mathutils import Vector

    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def _base_cursor_world(
    bx: tuple[float, float],
    by: tuple[float, float],
    bz: tuple[float, float],
    axis: str,
) -> tuple[float, float, float]:
    cx = (bx[0] + bx[1]) * 0.5
    cy = (by[0] + by[1]) * 0.5
    cz = (bz[0] + bz[1]) * 0.5
    axis = axis.upper()
    if axis == "Z":
        return (cx, cy, bz[0])
    if axis == "Y":
        return (cx, by[0], cz)
    if axis == "X":
        return (bx[0], cy, cz)
    raise ValueError(axis)


def _should_skip_mesh(obj) -> bool:
    if obj.type != "MESH":
        return True
    return bool(obj.parent and obj.parent.type == "ARMATURE")


def _apply_euler_rotation_xyz_deg(rx: float, ry: float, rz: float) -> None:
    """
    Rotações em graus nos eixos globais X, depois Y, depois Z.

    A mesh glTF costuma vir sob um Empty ``world``. Após
    ``parent_clear(KEEP_TRANSFORM)``, definir ``rotation_euler`` + ``apply`` pode
    não alterar vértices no Blender 5 — usamos ``transform.rotate`` (GLOBAL) e
    assamos com ``transform_apply``.
    """
    import bpy

    ctx = bpy.context
    mesh_objs = [
        o
        for o in bpy.data.objects
        if o.type == "MESH" and not (o.parent and o.parent.type == "ARMATURE")
    ]
    if not mesh_objs:
        return

    for obj in mesh_objs:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        ctx.view_layer.objects.active = obj
        if obj.parent is not None:
            bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
        if abs(rx) > 1e-9:
            bpy.ops.transform.rotate(
                value=math.radians(rx),
                orient_axis="X",
                orient_type="GLOBAL",
            )
        if abs(ry) > 1e-9:
            bpy.ops.transform.rotate(
                value=math.radians(ry),
                orient_axis="Y",
                orient_type="GLOBAL",
            )
        if abs(rz) > 1e-9:
            bpy.ops.transform.rotate(
                value=math.radians(rz),
                orient_axis="Z",
                orient_type="GLOBAL",
            )
        bpy.ops.object.transform_apply(rotation=True, location=False, scale=True)


def _set_origins_to_base(axis: str) -> None:
    import bpy
    from mathutils import Vector

    mesh_objs = [o for o in bpy.data.objects if not _should_skip_mesh(o)]
    if not mesh_objs:
        return

    ctx = bpy.context
    for obj in mesh_objs:
        bx, by, bz = _world_bbox_axes(obj)
        loc = _base_cursor_world(bx, by, bz, axis)
        ctx.scene.cursor.location = Vector(loc)
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        ctx.view_layer.objects.active = obj
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR", center="MEDIAN")


def _export_gltf(path: Path, *, as_glb: bool) -> None:
    import bpy

    fmt = "GLB" if as_glb else "GLTF_EMBEDDED"
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format=fmt,
        use_selection=False,
    )


def _process_file(
    glb: Path,
    axis: str,
    dry_run: bool,
    *,
    rotate_xyz_deg: tuple[float, float, float] | None = None,
) -> bool:
    import bpy

    _clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(glb))
    if rotate_xyz_deg is not None and any(rotate_xyz_deg):
        _apply_euler_rotation_xyz_deg(*rotate_xyz_deg)
    _set_origins_to_base(axis)
    if dry_run:
        return True
    as_glb = glb.suffix.lower() == ".glb"
    # O exportador glTF exige extensão final .glb/.gltf; evitar ``nome.glb.reorigin.tmp``.
    suf = ".glb" if as_glb else ".gltf"
    out = glb.with_name(f"{glb.stem}.reorigin{suf}")
    try:
        _export_gltf(out, as_glb=as_glb)
        out.replace(glb)
    except OSError:
        if out.is_file():
            out.unlink(missing_ok=True)
        raise
    return True


def main() -> int:
    raw = _argv_after_dd()
    ap = argparse.ArgumentParser(description="Origem na base (bpy) para GLBs sob public/")
    ap.add_argument(
        "public_dir",
        type=Path,
        nargs="?",
        default=None,
        help="Pasta public (ex.: .../simple-rpg/public); ignorado se usar --only",
    )
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="PATH",
        help="Processar só este GLB/GLTF (repetir para vários ficheiros).",
    )
    ap.add_argument(
        "--axis",
        choices=("Z", "Y", "X"),
        default="Z",
        help="Eixo vertical após import no Blender (glTF→Blender: Z por defeito)",
    )
    ap.add_argument(
        "--rotate",
        nargs=3,
        type=float,
        default=None,
        metavar=("RX", "RY", "RZ"),
        help="Euler XYZ em graus (Blender): rodar a mesh após import e antes de repor a origem "
        "(ex.: -90 0 0 se o modelo ficou “de lado”).",
    )
    ap.add_argument("--dry-run", action="store_true", help="Importa e altera em memória sem gravar")
    args = ap.parse_args(raw if raw else [])

    try:
        import bpy
    except ImportError:
        print(
            "Este script tem de ser executado dentro do Blender:\n  blender --background --python ... -- <public_dir>",
            file=sys.stderr,
        )
        return 2

    public: Path | None = None
    glbs: list[Path]

    if args.only:
        glbs = []
        for raw_p in args.only:
            p = Path(raw_p).expanduser().resolve()
            if not p.is_file():
                print(f"Ficheiro inexistente: {p}", file=sys.stderr)
                return 1
            if p.suffix.lower() not in (".glb", ".gltf"):
                print(f"Não é .glb/.gltf: {p}", file=sys.stderr)
                return 1
            glbs.append(p)
        glbs.sort(key=lambda x: str(x))
        if args.public_dir is not None:
            public = args.public_dir.resolve()
    else:
        if args.public_dir is None:
            here = Path(__file__).resolve()
            guess = here.parents[2] / "examples" / "simple-rpg" / "public"
            if guess.is_dir():
                public = guess
            else:
                ap.print_help()
                print(
                    "\nIndica a pasta public, ou usa --only ficheiro.glb (repetível).",
                    file=sys.stderr,
                )
                return 2
        else:
            public = args.public_dir.resolve()
        if not public.is_dir():
            print(f"Pasta inexistente: {public}", file=sys.stderr)
            return 1

        glbs = sorted(public.rglob("*.glb")) + sorted(public.rglob("*.gltf"))

    if not glbs:
        scope = public if public is not None else "(lista --only vazia)"
        print(f"Nenhum .glb/.gltf: {scope}")
        return 0

    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)

    def _label(p: Path) -> str:
        if public is not None and p.is_relative_to(public):
            return str(p.relative_to(public))
        return str(p)

    rot = None
    if args.rotate is not None:
        rot = (args.rotate[0], args.rotate[1], args.rotate[2])

    ok = 0
    for p in glbs:
        try:
            print(f"  {_label(p)}")
            _process_file(p, args.axis, args.dry_run, rotate_xyz_deg=rot)
            ok += 1
        except Exception as e:
            print(f"  [erro] {p}: {e}", file=sys.stderr)

    print(f"Concluído: {ok}/{len(glbs)} ficheiros.")
    return 0 if ok == len(glbs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
