#!/usr/bin/env python3
"""
Define a origem de cada mesh no centro da base da AABB (mínimo no eixo vertical).

Executar com o Python do Blender (não com python.exe normal):

  blender --background --python blender_reorigin_glb_feet.py -- <pasta_public>

Exemplo:

  blender --background --python VibeGame/tools/blender_reorigin_glb_feet.py -- VibeGame/examples/simple-rpg/public

O importador glTF do Blender converte Y-up (glTF) para Z-up (Blender); por defeito
o eixo vertical tratado como "altura" é Z. Usa --axis Y se os teus ficheiros já
estiverem orientados com Y para cima no Blender.
"""

from __future__ import annotations

import argparse
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


def _process_file(glb: Path, axis: str, dry_run: bool) -> bool:
    import bpy

    _clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(glb))
    _set_origins_to_base(axis)
    if dry_run:
        return True
    as_glb = glb.suffix.lower() == ".glb"
    out = glb.with_name(glb.name + ".reorigin.tmp")
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
        help="Pasta public (ex.: .../simple-rpg/public)",
    )
    ap.add_argument(
        "--axis",
        choices=("Z", "Y", "X"),
        default="Z",
        help="Eixo vertical após import no Blender (glTF→Blender: Z por defeito)",
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

    if args.public_dir is None:
        here = Path(__file__).resolve()
        guess = here.parents[2] / "examples" / "simple-rpg" / "public"
        if guess.is_dir():
            public = guess
        else:
            ap.print_help()
            print("\nIndica a pasta public ou coloca o script em VibeGame/tools/.", file=sys.stderr)
            return 2
    else:
        public = args.public_dir.resolve()
    if not public.is_dir():
        print(f"Pasta inexistente: {public}", file=sys.stderr)
        return 1

    glbs = sorted(public.rglob("*.glb")) + sorted(public.rglob("*.gltf"))
    if not glbs:
        print(f"Nenhum .glb/.gltf em {public}")
        return 0

    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)

    ok = 0
    for p in glbs:
        try:
            print(f"  {p.relative_to(public) if p.is_relative_to(public) else p}")
            _process_file(p, args.axis, args.dry_run)
            ok += 1
        except Exception as e:
            print(f"  [erro] {p}: {e}", file=sys.stderr)

    print(f"Concluído: {ok}/{len(glbs)} ficheiros.")
    return 0 if ok == len(glbs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
