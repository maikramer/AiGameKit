"""Converte DreamPlan em ficheiros prontos para batch (game.yaml, manifest.yaml) + world.bsn."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .planner import DreamPlan

# ---------------------------------------------------------------------------
# game.yaml
# ---------------------------------------------------------------------------


def emit_game_yaml(plan: DreamPlan, *, with_audio: bool = True) -> str:
    """Gera string YAML válida a partir do DreamPlan."""
    doc: dict[str, Any] = {
        "title": plan.title,
        "genre": plan.genre,
        "tone": plan.tone,
        "style_preset": plan.style_preset,
        "output_dir": ".",
        "path_layout": "split",
        "images_subdir": "images",
        "meshes_subdir": "meshes",
        "image_ext": "png",
        "seed_base": plan.seed if plan.seed is not None else 42,
        "generation": "medium",
    }
    if plan.negative_keywords:
        doc["negative_keywords"] = plan.negative_keywords

    doc["text2d"] = {"width": 768, "height": 768}

    has_3d = any(a.generate_3d for a in plan.assets)
    if has_3d:
        doc["text3d"] = {
            "preset": "fast",
            "export_origin": "feet",
        }
        doc["paint3d"] = {"preserve_origin": True}
        # Round 2 — master pipeline default ON.
        doc["master_pipeline"] = True
        doc["master_validate"] = True
        doc["master_bake_normals"] = False

    has_rig = any(a.generate_rig for a in plan.assets)
    if has_rig:
        doc["rigging3d"] = {"output_suffix": "_rigged"}
        doc["animator3d"] = {"preset": "humanoid"}

    if with_audio and any(a.generate_audio for a in plan.assets):
        doc["audio_subdir"] = "audio"
        doc["text2sound"] = {
            "quality": "medium",
            "audio_format": "wav",
        }

    if plan.icon_prompts:
        # Sem width/height: o modo icon do text2d tem defaults próprios (512x512).
        doc["icons"] = {
            "prompts": plan.icon_prompts,
            "transparent": True,
        }

    return yaml.dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)


def emit_manifest_yaml(plan: DreamPlan) -> str:
    """Gera string YAML do manifest a partir do DreamPlan."""
    assets: list[dict[str, Any]] = []
    for a in plan.assets:
        pipeline: list[str] = []
        if a.generate_3d:
            pipeline.append("3d")
        if a.generate_audio:
            pipeline.append("audio")
        if a.generate_rig:
            pipeline.append("rig")
        if a.generate_animate:
            pipeline.append("animate")
        entry: dict[str, Any] = {
            "id": a.id,
            "idea": a.idea,
            "kind": a.kind or "prop",
            "pipeline": pipeline,
        }
        if a.generate_audio:
            entry["audio"] = {"duration": 2, "profile": "effects"}
        assets.append(entry)
    return yaml.dump({"assets": assets}, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# world .bsn (esboço declarativo da cena)
# ---------------------------------------------------------------------------

# Caminho canónico do heightmap no projeto do jogo (o runtime lê-o do disco).
TERRAIN_REL_PATH = "world.data/terrain.ahgt"

_WORLD_NODE = "bevy_world_xml::bsn::WorldNode"
_WORLD_PROP = "bevy_world_xml::bsn::WorldProp"
_TRANSFORM = "bevy_transform::components::transform::Transform"
_VOXEL_TERRAIN = "bevy_ext_terrain::voxel::VoxelTerrain"
_CHILDREN = "bevy_ecs::hierarchy::Children"
_IDENTITY_SCALE = (1.0, 1.0, 1.0)
_ORIGIN = (0.0, 0.0, 0.0)


def _ron_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _bsn_float(value: float) -> str:
    return repr(float(value))


def _vec3(values: tuple[float, float, float]) -> str:
    x, y, z = values
    return f"glam::Vec3 {{ x: {_bsn_float(x)}, y: {_bsn_float(y)}, z: {_bsn_float(z)} }}"


def _transform(translation: tuple[float, float, float], scale: tuple[float, float, float]) -> str:
    return (
        f"{_TRANSFORM} {{\n"
        f"    translation: {_vec3(translation)},\n"
        "    rotation: glam::Quat { x: 0.0, y: 0.0, z: 0.0, w: 1.0 },\n"
        f"    scale: {_vec3(scale)},\n"
        "}\n"
    )


def _parse_vec3(text: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    parts = text.split()
    if len(parts) != 3:
        return default
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError:
        return default


def _world_node(
    tag: str,
    attrs: list[tuple[str, str]],
    *,
    pose: bool,
    origin_path: tuple[int, ...],
) -> str:
    out = f'{_WORLD_NODE} {{\n    tag: "{_ron_escape(tag)}",\n    attrs: [\n'
    for key, value in attrs:
        out += f'        {_WORLD_PROP} {{ key: "{_ron_escape(key)}", value: "{_ron_escape(value)}" }},\n'
    out += "    ],\n"
    out += '    text: "",\n'
    out += '    origin_file: "world.bsn",\n'
    out += f"    origin_path: [{', '.join(str(i) for i in origin_path)}],\n"
    out += f"    pose: {'true' if pose else 'false'},\n"
    out += "}\n"
    return out


def _entity(
    name: str,
    tag: str,
    attrs: list[tuple[str, str]],
    *,
    origin_path: tuple[int, ...],
    translation: tuple[float, float, float] = _ORIGIN,
    scale: tuple[float, float, float] = _IDENTITY_SCALE,
    pose: bool = True,
    extra: str = "",
    children: tuple[str, ...] = (),
) -> str:
    out = f'#"{_ron_escape(name)}"\n'
    out += _transform(translation, scale)
    out += _world_node(tag, attrs, pose=pose, origin_path=origin_path)
    out += extra
    if children:
        out += f"{_CHILDREN} [\n"
        for index, child in enumerate(children):
            if index:
                out += "    --\n"
            out += "".join(f"    {line}\n" for line in child.splitlines())
        out += "]\n"
    return out


def emit_world_bsn(plan: DreamPlan) -> str:
    """Gera o esboço da cena em ``world.bsn`` (formato do Vael).

    O `.bsn` é uma árvore de entidades com ``WorldNode { tag, attrs, text }``;
    o loader do jogo reconstrói o DSL a partir dela. Emite chão
    (``Composition``/``Box``) ou terreno (``Terrain`` + `.ahgt`), o herói
    (``PlayerGLTF``) e os placements estáticos (``GltfScene``), mais a câmara.
    O céu é uma definição do jogo (``world/settings/environment.bsn``), não um
    nó da cena; ``clear-color`` guarda a cor de fundo do plano.
    """
    terrain_enabled = plan.terrain is not None and plan.terrain.enabled
    children: list[str] = []
    origin = 1

    if terrain_enabled:
        tp = plan.terrain
        assert tp is not None
        world_size = tp.world_size or plan.scene.ground_size or 256
        max_height = tp.max_height or 50
        children.append(
            _entity(
                "Terrain",
                "Terrain",
                [
                    ("heightmap", TERRAIN_REL_PATH),
                    ("world-size", f"{world_size:g}"),
                    ("max-height", f"{max_height:g}"),
                ],
                pose=False,
                origin_path=(origin,),
                extra=f'{_VOXEL_TERRAIN} {{\n    heightmap: "{TERRAIN_REL_PATH}",\n}}\n',
            )
        )
    else:
        half = (plan.scene.ground_size or 50) / 2
        half_text = f"{half:g}"
        children.append(
            _entity(
                "ground",
                "Composition",
                [("name", "ground"), ("body", "fixed"), ("collider", "auto")],
                origin_path=(origin,),
                children=(
                    _entity(
                        "Box",
                        "Box",
                        [
                            ("half-size", f"{half_text} 0.5 {half_text}"),
                            ("translation", "0 -0.5 0"),
                            ("color", _ground_color_for_genre(plan.genre)),
                        ],
                        translation=(0.0, -0.5, 0.0),
                        origin_path=(origin, 0),
                    ),
                ),
            )
        )
    origin += 1

    three_d_ids = {a.id for a in plan.assets if a.generate_3d}
    rigged_ids = {a.id for a in plan.assets if a.generate_3d and a.generate_rig}
    hero_placed = False

    for placement in plan.scene.placements:
        if placement.asset_id not in three_d_ids:
            continue
        url = f"/assets/models/{placement.asset_id}_lod0.glb"
        if placement.asset_id in rigged_ids and not hero_placed:
            pos = placement.pos or "0 60 0"
            children.append(
                _entity(
                    "player",
                    "PlayerGLTF",
                    [("name", "player"), ("pos", pos), ("model-url", url)],
                    translation=_parse_vec3(pos, (0.0, 60.0, 0.0)),
                    origin_path=(origin,),
                )
            )
            hero_placed = True
        else:
            pos = placement.pos or "0 0 0"
            scale = placement.scale or "1 1 1"
            children.append(
                _entity(
                    f"GltfScene {placement.asset_id}",
                    "GltfScene",
                    [("url", url)],
                    translation=_parse_vec3(pos, _ORIGIN),
                    scale=_parse_vec3(scale, _IDENTITY_SCALE),
                    origin_path=(origin,),
                )
            )
        origin += 1

    children.append(
        _entity(
            "OrbitCamera",
            "OrbitCamera",
            [("distance", "14"), ("pitch", "18")],
            origin_path=(origin,),
        )
    )

    return _entity(
        "world",
        "world",
        [
            ("static", "true"),
            ("clear-color", plan.scene.sky_color or "#87CEEB"),
            ("title", plan.title),
        ],
        origin_path=(0,),
        children=tuple(children),
    )


def _ground_color_for_genre(genre: str) -> str:
    """Pick a sensible ground color based on the game genre/theme."""
    g = genre.lower()
    if any(k in g for k in ("desert", "sand", "wasteland")):
        return "#c2a860"
    if any(k in g for k in ("snow", "ice", "winter", "arctic")):
        return "#e8e8f0"
    if any(k in g for k in ("space", "sci-fi", "cyber")):
        return "#2a2a3a"
    if any(k in g for k in ("dungeon", "cave", "horror")):
        return "#3a3a3a"
    return "#4a7a3a"


# ---------------------------------------------------------------------------
# Emitir tudo para disco
# ---------------------------------------------------------------------------


def emit_all(
    plan: DreamPlan,
    output_dir: Path,
    *,
    with_sky: bool = True,
    with_audio: bool = True,
) -> dict[str, Path]:
    """Escreve os ficheiros gerados (game.yaml, manifest.yaml, world.bsn) e devolve mapa nome→caminho.

    Args:
        plan: Plano validado do dream.
        output_dir: Pasta onde escrever (normalmente ``_batch/`` do projeto).
        with_sky: Mantido por compatibilidade com a CLI; já não altera os ficheiros
            emitidos (o sky depende só de ``plan.sky_prompt``).
        with_audio: Quando ``False``, o game.yaml não inclui o bloco ``text2sound``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}

    game_yaml = output_dir / "game.yaml"
    game_yaml.write_text(emit_game_yaml(plan, with_audio=with_audio), encoding="utf-8")
    paths["game_yaml"] = game_yaml

    manifest = output_dir / "manifest.yaml"
    manifest.write_text(emit_manifest_yaml(plan), encoding="utf-8")
    paths["manifest_yaml"] = manifest

    world_bsn_str = emit_world_bsn(plan)
    world_bsn_path = output_dir / "world.bsn"
    world_bsn_path.write_text(world_bsn_str, encoding="utf-8")
    paths["world_bsn"] = world_bsn_path

    return paths
