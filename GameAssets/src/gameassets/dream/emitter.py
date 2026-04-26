"""Converte DreamPlan em ficheiros prontos para batch + scaffold VibeGame."""

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
        "seed_base": 42,
    }
    if plan.negative_keywords:
        doc["negative_keywords"] = plan.negative_keywords

    doc["text2d"] = {"low_vram": True, "width": 768, "height": 768}

    has_3d = any(a.generate_3d for a in plan.assets)
    if has_3d:
        doc["text3d"] = {
            "preset": "fast",
            "low_vram": False,
            "export_origin": "feet",
        }
        doc["paint3d"] = {"preserve_origin": True}

    has_rig = any(a.generate_rig for a in plan.assets)
    if has_rig:
        doc["rigging3d"] = {"output_suffix": "_rigged"}
        doc["animator3d"] = {"preset": "humanoid"}

    if with_audio and any(a.generate_audio for a in plan.assets):
        doc["audio_subdir"] = "audio"
        doc["text2sound"] = {
            "duration": 10,
            "steps": 100,
            "cfg_scale": 4.5,
            "audio_format": "wav",
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
        if a.generate_parts:
            pipeline.append("parts")
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
# world XML (scene for VibeGame <Scene>)
# ---------------------------------------------------------------------------


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def emit_world_xml(plan: DreamPlan) -> str:
    """Gera bloco <Scene> para inserir no index.html do VibeGame."""
    has_sky_image = bool(plan.sky_prompt)
    sky_attr = ""
    if plan.scene.sky_color and not has_sky_image:
        sky_attr = f' sky="{_xml_escape(plan.scene.sky_color)}"'

    lines = [f'<Scene canvas="#game-canvas"{sky_attr}>']

    if has_sky_image:
        lines.append('  <Skybox url="/assets/sky/sky.png"></Skybox>')

    terrain_enabled = plan.terrain is not None and plan.terrain.enabled
    if terrain_enabled:
        tp = plan.terrain
        ws = tp.world_size or plan.scene.ground_size or 256
        mh = tp.max_height or 50
        lines.append(
            f'  <Terrain heightmap="/assets/terrain/heightmap.png"'
            f' terrain-data-url="/assets/terrain/terrain.json"'
            f' world-size="{ws}" max-height="{mh}"></Terrain>'
        )
    else:
        gs = plan.scene.ground_size or 50
        ground_color = _ground_color_for_genre(plan.genre)
        lines.append(
            f'  <static-part pos="0 -0.5 0" shape="box" size="{gs} 1 {gs}" color="{ground_color}"></static-part>'
        )

    spawn_y = plan.scene.spawn_y if plan.scene.spawn_y is not None else 3
    lines.append(f'  <Player pos="0 {spawn_y} 0"></Player>')
    lines.append('  <OrbitCamera target-distance="14" target-pitch="-0.4"></OrbitCamera>')
    lines.append("")

    three_d_ids = {a.id for a in plan.assets if a.generate_3d}
    rigged_ids = {a.id for a in plan.assets if a.generate_3d and a.generate_rig}

    for p in plan.scene.placements:
        if p.asset_id not in three_d_ids:
            continue
        url = f"/assets/models/{p.asset_id}.glb"
        if p.asset_id in rigged_ids:
            pos = p.pos or "0 60 0"
            lines.append(f'  <PlayerGLTF model-url="{_xml_escape(url)}" pos="{_xml_escape(pos)}"></PlayerGLTF>')
        else:
            pos = p.pos or "0 0 0"
            scale = p.scale or "1 1 1"
            lines.append(f'  <GLTFLoader url="{_xml_escape(url)}" transform="pos: {pos}; scale: {scale}"></GLTFLoader>')

    lines.append("</Scene>")
    return "\n".join(lines) + "\n"


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
# main.ts
# ---------------------------------------------------------------------------


def emit_main_ts(plan: DreamPlan, *, with_sky: bool = True) -> str:
    imports = ["configure", "run", "loadSceneManifest"]

    lines = [
        f"import {{ {', '.join(imports)} }} from 'vibegame';",
        "",
        "async function bootstrap(): Promise<void> {",
        "  configure({ canvas: '#game-canvas' });",
        "  const runtime = await run();",
        "  const state = runtime.getState();",
        "  try {",
        "    await loadSceneManifest(state);",
        "  } catch {",
        "    console.warn('[dream] Scene manifest not loaded (optional).');",
        "  }",
        "}",
        "",
        "void bootstrap();",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# index.html
# ---------------------------------------------------------------------------


def emit_index_html(plan: DreamPlan, world_xml: str) -> str:
    title = _xml_escape(plan.title)
    indented_world = "\n".join("    " + line if line.strip() else "" for line in world_xml.splitlines())
    return f"""\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{title}</title>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: 100%; height: 100%; overflow: hidden; background: #111; }}
      #game-canvas {{ display: block; width: 100%; height: 100%; }}
    </style>
  </head>
  <body>
{indented_world}
    <canvas id="game-canvas"></canvas>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
"""


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
    """Escreve todos os ficheiros gerados e devolve mapa nome→caminho."""
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}

    game_yaml = output_dir / "game.yaml"
    game_yaml.write_text(emit_game_yaml(plan, with_audio=with_audio), encoding="utf-8")
    paths["game_yaml"] = game_yaml

    manifest = output_dir / "manifest.yaml"
    manifest.write_text(emit_manifest_yaml(plan), encoding="utf-8")
    paths["manifest_yaml"] = manifest

    world_xml_str = emit_world_xml(plan)
    world_xml_path = output_dir / "world.xml"
    world_xml_path.write_text(world_xml_str, encoding="utf-8")
    paths["world_xml"] = world_xml_path

    main_ts = output_dir / "main.ts"
    main_ts.write_text(emit_main_ts(plan, with_sky=with_sky), encoding="utf-8")
    paths["main_ts"] = main_ts

    index_html = output_dir / "index.html"
    index_html.write_text(emit_index_html(plan, world_xml_str), encoding="utf-8")
    paths["index_html"] = index_html

    return paths
