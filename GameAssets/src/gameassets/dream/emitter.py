"""Converte DreamPlan em ficheiros prontos para batch + scaffold VibeGame."""

from __future__ import annotations

import csv
import io
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
            "texture": True,
        }

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


# ---------------------------------------------------------------------------
# manifest.csv
# ---------------------------------------------------------------------------

CSV_HEADERS = [
    "id",
    "idea",
    "kind",
    "generate_3d",
    "generate_audio",
    "generate_rig",
    "generate_animate",
    "generate_parts",
    "image_source",
]


def emit_manifest_csv(plan: DreamPlan) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_HEADERS)
    writer.writeheader()
    for a in plan.assets:
        writer.writerow(
            {
                "id": a.id,
                "idea": a.idea,
                "kind": a.kind,
                "generate_3d": str(a.generate_3d).lower(),
                "generate_audio": str(a.generate_audio).lower(),
                "generate_rig": str(a.generate_rig).lower(),
                "generate_animate": str(a.generate_rig).lower(),
                "generate_parts": str(a.generate_parts).lower(),
                "image_source": "",
            }
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# world XML (scene for VibeGame <world>)
# ---------------------------------------------------------------------------


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def emit_world_xml(plan: DreamPlan) -> str:
    """Gera bloco <world> para inserir no index.html do VibeGame."""
    sky_attr = ""
    if plan.scene.sky_color:
        sky_attr = f' sky="{_xml_escape(plan.scene.sky_color)}"'

    lines = [f'<world canvas="#game-canvas"{sky_attr}>']

    gs = plan.scene.ground_size or 50
    ground_color = _ground_color_for_genre(plan.genre)
    lines.append(f'  <static-part pos="0 -0.5 0" shape="box" size="{gs} 1 {gs}" color="{ground_color}"></static-part>')

    spawn_y = plan.scene.spawn_y if plan.scene.spawn_y is not None else 3
    lines.append(f'  <player pos="0 {spawn_y} 0"></player>')
    lines.append('  <orbit-camera target-distance="14" target-pitch="-0.4"></orbit-camera>')
    lines.append("")

    three_d_ids = {a.id for a in plan.assets if a.generate_3d}
    skip_gltf_ids = {a.id for a in plan.assets if a.generate_3d and a.generate_rig}

    for p in plan.scene.placements:
        if p.asset_id not in three_d_ids:
            continue
        if p.asset_id in skip_gltf_ids:
            continue
        pos = p.pos or "0 0 0"
        scale = p.scale or "1 1 1"
        url = f"/assets/models/{p.asset_id}.glb"
        lines.append(f'  <gltf-load url="{_xml_escape(url)}" transform="pos: {pos}; scale: {scale}"></gltf-load>')

    lines.append("</world>")
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


def _dream_animated_character_model_id(plan: DreamPlan) -> str | None:
    for a in plan.assets:
        if a.generate_3d and a.generate_rig:
            return a.id
    return None


def emit_main_ts(plan: DreamPlan, *, with_sky: bool = True) -> str:
    model_id = _dream_animated_character_model_id(plan)
    if model_id is not None:
        return _emit_main_ts_character_animation(plan, model_id=model_id, with_sky=with_sky)

    imports = ["configure", "run"]
    extra_imports: list[str] = []

    if with_sky and plan.sky_prompt:
        imports.append("applyEquirectSkyEnvironment")

    lines = [
        f"import {{ {', '.join(imports)} }} from 'vibegame';",
    ]
    if extra_imports:
        lines.extend(extra_imports)

    lines.append("")
    lines.append("async function bootstrap(): Promise<void> {")
    lines.append("  configure({ canvas: '#game-canvas' });")
    lines.append("  const runtime = await run();")

    if with_sky and plan.sky_prompt:
        lines.append("  const state = runtime.getState();")
        lines.append("  try {")
        lines.append("    await applyEquirectSkyEnvironment(state, '/assets/sky/sky.png');")
        lines.append("  } catch {")
        lines.append("    console.warn('[dream] Sky env map not loaded (optional).');")
        lines.append("  }")

    lines.append("}")
    lines.append("")
    lines.append("void bootstrap();")
    lines.append("")
    return "\n".join(lines)


def _emit_main_ts_character_animation(plan: DreamPlan, *, model_id: str, with_sky: bool) -> str:
    """Bootstrap com GltfAnimator e clipes Animator3D (idle / walk / run) para o herói rigado."""
    url = f"/assets/models/{model_id}.glb"
    sky_import = (
        "import {\n"
        "  applyEquirectSkyEnvironment,\n"
        "  configure,\n"
        "  defineQuery,\n"
        "  getScene,\n"
        "  GltfAnimator,\n"
        "  isKeyDown,\n"
        "  Parent,\n"
        "  Player,\n"
        "  Renderer,\n"
        "  run,\n"
        "  WorldTransform,\n"
        "} from 'vibegame';\n"
    )
    no_sky_import = (
        "import {\n"
        "  configure,\n"
        "  defineQuery,\n"
        "  getScene,\n"
        "  GltfAnimator,\n"
        "  isKeyDown,\n"
        "  Parent,\n"
        "  Player,\n"
        "  Renderer,\n"
        "  run,\n"
        "  WorldTransform,\n"
        "} from 'vibegame';\n"
    )
    header = sky_import if (with_sky and plan.sky_prompt) else no_sky_import
    header += "import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';\n"
    header += "import { Box3, Clock } from 'three';\n\n"
    header += "const CLIP_IDLE = 'Animator3D_BreatheIdle';\n"
    header += "const CLIP_WALK = 'Animator3D_Walk';\n"
    header += "const CLIP_RUN = 'Animator3D_Run';\n"
    header += f"const HERO_MODEL_URL = '{url}';\n\n"
    header += "const MOVE_KEYS = [\n"
    header += "  'KeyW',\n  'KeyA',\n  'KeyS',\n  'KeyD',\n"
    header += "  'ArrowUp',\n  'ArrowDown',\n  'ArrowLeft',\n  'ArrowRight',\n"
    header += "] as const;\n\n"
    header += "const playerQuery = defineQuery([Player]);\n"
    header += "const parentQuery = defineQuery([Parent]);\n\n"
    header += "interface HeroHandle {\n  animator: GltfAnimator;\n  yOffset: number;\n}\n\n"
    header += (
        "async function loadHero(scene: import('three').Scene): Promise<HeroHandle> {\n"
        "  const loader = new GLTFLoader();\n"
        "  const gltf = await loader.loadAsync(HERO_MODEL_URL);\n"
        "  gltf.scene.name = 'dream-player-model';\n"
        "  scene.add(gltf.scene);\n"
        "  const box = new Box3().setFromObject(gltf.scene);\n"
        "  const yOffset = -box.min.y;\n"
        "  const animator = new GltfAnimator(gltf, { crossfadeDuration: 0.2 });\n"
        "  animator.play(CLIP_IDLE);\n"
        "  return { animator, yOffset };\n"
        "}\n\n"
    )
    header += (
        "function hideBoxCharacterParts(\n"
        "  state: ReturnType<Awaited<ReturnType<typeof run>>['getState']>\n"
        ") {\n"
        "  const world = state.world;\n"
        "  const players = playerQuery(world);\n"
        "  if (players.length === 0) return;\n"
        "  const playerEid = players[0];\n"
        "  const allParented = parentQuery(world);\n"
        "  const childrenOfPlayer = allParented.filter(\n"
        "    (e: number) => Parent.entity[e] === playerEid\n"
        "  );\n"
        "  for (const child of childrenOfPlayer) {\n"
        "    const grandchildren = allParented.filter(\n"
        "      (e: number) => Parent.entity[e] === child\n"
        "    );\n"
        "    for (const gc of grandchildren) {\n"
        "      if (state.hasComponent(gc, Renderer)) {\n"
        "        Renderer.visible[gc] = 0;\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}\n\n"
    )
    header += (
        "function startGameLoop(\n"
        "  hero: HeroHandle,\n"
        "  state: ReturnType<Awaited<ReturnType<typeof run>>['getState']>\n"
        ") {\n"
        "  const clock = new Clock();\n"
        "  function tick() {\n"
        "    const dt = clock.getDelta();\n"
        "    const moving = MOVE_KEYS.some((k) => isKeyDown(k));\n"
        "    const sprint = isKeyDown('ShiftLeft') || isKeyDown('ShiftRight');\n"
        "    let wantClip = CLIP_IDLE;\n"
        "    if (moving) {\n"
        "      wantClip = sprint ? CLIP_RUN : CLIP_WALK;\n"
        "    }\n"
        "    if (hero.animator.activeClipName !== wantClip) {\n"
        "      hero.animator.play(wantClip);\n"
        "    }\n"
        "    hero.animator.update(dt);\n"
        "    const players = playerQuery(state.world);\n"
        "    if (players.length > 0) {\n"
        "      const eid = players[0];\n"
        "      const root = hero.animator.root;\n"
        "      root.position.set(\n"
        "        WorldTransform.posX[eid],\n"
        "        WorldTransform.posY[eid] + hero.yOffset,\n"
        "        WorldTransform.posZ[eid]\n"
        "      );\n"
        "      root.quaternion.set(\n"
        "        WorldTransform.rotX[eid],\n"
        "        WorldTransform.rotY[eid],\n"
        "        WorldTransform.rotZ[eid],\n"
        "        WorldTransform.rotW[eid]\n"
        "      );\n"
        "    }\n"
        "    requestAnimationFrame(tick);\n"
        "  }\n"
        "  tick();\n"
        "}\n\n"
    )
    header += "async function bootstrap(): Promise<void> {\n"
    header += "  configure({ canvas: '#game-canvas' });\n"
    header += "  const runtime = await run();\n"
    header += "  const state = runtime.getState();\n"
    if with_sky and plan.sky_prompt:
        header += "  try {\n"
        header += "    await applyEquirectSkyEnvironment(state, '/assets/sky/sky.png');\n"
        header += "  } catch {\n"
        header += "    console.warn('[dream] Sky env map not loaded (optional).');\n"
        header += "  }\n"
    header += "  const scene = getScene(state);\n"
    header += "  if (scene) {\n"
    header += "    try {\n"
    header += "      const hero = await loadHero(scene);\n"
    header += "      hideBoxCharacterParts(state);\n"
    header += "      startGameLoop(hero, state);\n"
    header += "    } catch (err) {\n"
    header += "      console.warn('[dream] Animated character model not found, using box character.', err);\n"
    header += "    }\n"
    header += "  }\n"
    header += "}\n\nvoid bootstrap();\n"
    return header


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

    manifest = output_dir / "manifest.csv"
    manifest.write_text(emit_manifest_csv(plan), encoding="utf-8")
    paths["manifest_csv"] = manifest

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
