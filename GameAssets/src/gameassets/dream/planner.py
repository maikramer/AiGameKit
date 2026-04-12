"""DreamPlan dataclass + LLM-backed planner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AssetEntry:
    id: str
    idea: str
    kind: str = "prop"
    generate_3d: bool = True
    generate_audio: bool = False
    generate_rig: bool = False
    generate_animate: bool = False
    generate_parts: bool = False


@dataclass
class Placement:
    asset_id: str
    pos: str = "0 0 0"
    scale: str = "1 1 1"


@dataclass
class TerrainPlan:
    enabled: bool = False
    seed: int | None = None
    prompt: str = ""
    world_size: float = 768.0
    max_height: float = 50.0
    size: int = 2048
    river_threshold: float = 4000.0
    erosion_particles: int = 80000
    lake_min_area: int = 20000
    lake_max_count: int = 3


@dataclass
class SceneLayout:
    sky_color: str = "#87CEEB"
    ground_size: float = 50
    spawn_y: float = 5
    placements: list[Placement] = field(default_factory=list)


@dataclass
class DreamPlan:
    title: str
    genre: str
    tone: str
    style_preset: str
    assets: list[AssetEntry]
    scene: SceneLayout
    sky_prompt: str = ""
    negative_keywords: list[str] = field(default_factory=list)
    terrain: TerrainPlan | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "title": self.title,
            "genre": self.genre,
            "tone": self.tone,
            "style_preset": self.style_preset,
            "sky_prompt": self.sky_prompt,
            "negative_keywords": self.negative_keywords,
            "assets": [
                {
                    "id": a.id,
                    "idea": a.idea,
                    "kind": a.kind,
                    "generate_3d": a.generate_3d,
                    "generate_audio": a.generate_audio,
                    "generate_rig": a.generate_rig,
                    "generate_animate": a.generate_animate,
                    "generate_parts": a.generate_parts,
                }
                for a in self.assets
            ],
            "scene": {
                "sky_color": self.scene.sky_color,
                "ground_size": self.scene.ground_size,
                "spawn_y": self.scene.spawn_y,
                "placements": [{"asset_id": p.asset_id, "pos": p.pos, "scale": p.scale} for p in self.scene.placements],
            },
        }
        if self.terrain is not None:
            result["terrain"] = {
                "enabled": self.terrain.enabled,
                "seed": self.terrain.seed,
                "prompt": self.terrain.prompt,
                "world_size": self.terrain.world_size,
                "max_height": self.terrain.max_height,
                "size": self.terrain.size,
                "river_threshold": self.terrain.river_threshold,
                "erosion_particles": self.terrain.erosion_particles,
                "lake_min_area": self.terrain.lake_min_area,
                "lake_max_count": self.terrain.lake_max_count,
            }
        return result

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DreamPlan:
        assets = [
            AssetEntry(
                id=a["id"],
                idea=a["idea"],
                kind=a.get("kind", "prop"),
                generate_3d=a.get("generate_3d", True),
                generate_audio=a.get("generate_audio", False),
                generate_rig=a.get("generate_rig", False),
                generate_animate=a.get("generate_animate", False),
                generate_parts=a.get("generate_parts", False),
            )
            for a in d.get("assets", [])
        ]
        sc = d.get("scene", {})
        placements = [
            Placement(
                asset_id=p["asset_id"],
                pos=p.get("pos", "0 0 0"),
                scale=p.get("scale", "1 1 1"),
            )
            for p in sc.get("placements", [])
        ]
        terrain_data = d.get("terrain")
        terrain: TerrainPlan | None = None
        if terrain_data and isinstance(terrain_data, dict):
            terrain = TerrainPlan(
                enabled=terrain_data.get("enabled", False),
                seed=terrain_data.get("seed"),
                prompt=terrain_data.get("prompt", ""),
                world_size=float(terrain_data.get("world_size", 768.0)),
                max_height=float(terrain_data.get("max_height", 50.0)),
                size=int(terrain_data.get("size", 2048)),
                river_threshold=float(terrain_data.get("river_threshold", 4000.0)),
                erosion_particles=int(terrain_data.get("erosion_particles", 80000)),
                lake_min_area=int(terrain_data.get("lake_min_area", 20000)),
                lake_max_count=int(terrain_data.get("lake_max_count", 3)),
            )
        return cls(
            title=d.get("title", "Untitled"),
            genre=d.get("genre", ""),
            tone=d.get("tone", ""),
            style_preset=d.get("style_preset", "lowpoly"),
            sky_prompt=d.get("sky_prompt", ""),
            negative_keywords=d.get("negative_keywords", []),
            assets=assets,
            scene=SceneLayout(
                sky_color=sc.get("sky_color", "#87CEEB"),
                ground_size=sc.get("ground_size", 50),
                spawn_y=sc.get("spawn_y", 5),
                placements=placements,
            ),
            terrain=terrain,
        )


# ---------------------------------------------------------------------------
# Fallback (no LLM)
# ---------------------------------------------------------------------------


def _fallback_plan(description: str, style_preset: str) -> DreamPlan:
    """Plano razoável quando não há LLM — extrai keywords e gera uma cena variada."""
    desc_lower = description.lower()

    genre = "3D adventure"
    _genre_map = [
        ("rpg", "3D exploration RPG"),
        ("platformer", "3D platformer"),
        ("horror", "horror"),
        ("racing", "racing"),
    ]
    for kw, g in _genre_map:
        if kw in desc_lower:
            genre = g
            break

    assets: list[AssetEntry] = []
    placements: list[Placement] = []

    has_char = any(k in desc_lower for k in ("character", "hero", "player", "warrior", "knight", "mage"))
    if has_char:
        hero_idea = _extract_phrase(description, "hero") or "game hero character"
        assets.append(
            AssetEntry(
                id="hero",
                idea=hero_idea,
                kind="character",
                generate_3d=True,
                generate_rig=True,
            )
        )
        placements.append(Placement(asset_id="hero", pos="0 1 0", scale="1 1 1"))

    prop_keywords = {
        "crate": ("wooden_crate", "wooden crate with iron bands"),
        "chest": ("treasure_chest", "wooden treasure chest, closed"),
        "crystal": ("crystal", "glowing crystal gem, collectible"),
        "barrel": ("barrel", "wooden barrel"),
        "rock": ("rock", "large rock formation"),
        "pillar": ("stone_pillar", "ancient stone pillar"),
        "fountain": ("fountain", "stone water fountain"),
        "lamp": ("lamp_post", "old street lamp post"),
        "sword": ("sword", "medieval sword on the ground"),
        "shield": ("shield", "round wooden shield"),
        "potion": ("potion", "magical potion bottle"),
    }
    env_keywords = {
        "tree": ("tree", "stylized tree with round canopy"),
        "bush": ("bush", "green leafy bush"),
        "house": ("house", "small cottage house"),
        "tower": ("tower", "stone watchtower"),
        "bridge": ("bridge", "wooden bridge"),
        "fence": ("fence", "wooden fence section"),
        "ruins": ("ruins", "ancient stone ruins"),
        "mushroom": ("mushroom", "giant colorful mushroom"),
    }

    idx = 0
    offsets = [
        ("5 0.5 3", "1 1 1"),
        ("-4 0.5 6", "1 1 1"),
        ("8 0 -5", "1 1 1"),
        ("-7 0 4", "0.8 0.8 0.8"),
        ("3 0 -8", "1.2 1.2 1.2"),
    ]

    for kw, (aid, idea) in prop_keywords.items():
        if kw in desc_lower and len(assets) < 8:
            assets.append(AssetEntry(id=aid, idea=f"{idea}, {style_preset} style", kind="prop", generate_3d=True))
            pos, sc = offsets[idx % len(offsets)]
            placements.append(Placement(asset_id=aid, pos=pos, scale=sc))
            idx += 1

    tree_positions = [("-12 0 -3", "2 2 2"), ("15 0 8", "1.8 1.8 1.8"), ("-8 0 12", "2.2 2.2 2.2")]
    for kw, (aid, idea) in env_keywords.items():
        if kw in desc_lower and len(assets) < 8:
            assets.append(
                AssetEntry(
                    id=aid,
                    idea=f"{idea}, {style_preset} style",
                    kind="environment",
                    generate_3d=True,
                )
            )
            if kw == "tree":
                for tp, ts in tree_positions:
                    placements.append(Placement(asset_id=aid, pos=tp, scale=ts))
            else:
                pos, sc = offsets[idx % len(offsets)]
                placements.append(Placement(asset_id=aid, pos=pos, scale=sc))
                idx += 1

    has_sound = any(k in desc_lower for k in ("sound", "audio", "sfx", "music", "collect"))
    if has_sound and len(assets) < 8:
        assets.append(
            AssetEntry(
                id="collect_sfx",
                idea="short collect chime sound effect",
                kind="prop",
                generate_3d=False,
                generate_audio=True,
            )
        )

    if not assets:
        assets = [
            AssetEntry(
                id="main_prop",
                idea=description[:100],
                kind="prop",
                generate_3d=True,
            ),
            AssetEntry(
                id="decoration",
                idea=f"decoration for {description[:60]}",
                kind="environment",
                generate_3d=True,
            ),
        ]
        placements = [
            Placement(asset_id="main_prop", pos="3 0.5 0", scale="1 1 1"),
            Placement(asset_id="decoration", pos="-5 0 4", scale="1.5 1.5 1.5"),
        ]

    title = description.split(",")[0].strip().title()[:40] or "My Game"

    return DreamPlan(
        title=title,
        genre=genre,
        tone=description[:80],
        style_preset=style_preset,
        sky_prompt=f"bright sky, {style_preset} style, equirectangular 360, panoramic, {description[:50]}",
        assets=assets,
        scene=SceneLayout(
            ground_size=80,
            spawn_y=3,
            placements=placements,
        ),
    )


def _extract_phrase(desc: str, keyword: str) -> str:
    """Try to extract a short phrase around a keyword from the description."""
    idx = desc.lower().find(keyword)
    if idx == -1:
        return ""
    start = max(0, desc.rfind(",", 0, idx))
    if start > 0:
        start += 1
    end = desc.find(",", idx)
    if end == -1:
        end = len(desc)
    return desc[start:end].strip()[:80]


# ---------------------------------------------------------------------------
# LLM Providers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict[str, Any]:
    """Extrai o primeiro bloco JSON de uma resposta LLM (pode ter markdown)."""
    start = text.find("{")
    if start == -1:
        raise ValueError("Nenhum JSON encontrado na resposta do LLM")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("JSON incompleto na resposta do LLM")


def _call_openai(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
) -> str:
    try:
        from openai import OpenAI  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError("pip install openai  (ou define OPENAI_API_KEY + instala o pacote)") from e

    client = OpenAI(
        api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
        base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
    )
    resp = client.chat.completions.create(
        model=model or "gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


def _call_huggingface(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None,
) -> str:
    try:
        from huggingface_hub import InferenceClient  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError("pip install huggingface_hub") from e

    client = InferenceClient(model=model or "meta-llama/Llama-3.1-8B-Instruct")
    resp = client.chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=4096,
    )
    return resp.choices[0].message.content or ""


def _call_stdin(system_prompt: str, user_prompt: str) -> str:
    """Envia system+user para stdin/stdout (pipe para qualquer LLM CLI)."""
    combined = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}\n"
    proc = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
        input=combined,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.stdout


# ---------------------------------------------------------------------------
# Planner principal
# ---------------------------------------------------------------------------


def plan_game(
    description: str,
    *,
    preset_names: list[str],
    style_preset: str | None = None,
    max_assets: int = 8,
    with_audio: bool = True,
    with_sky: bool = True,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    plan_json_path: str | None = None,
) -> DreamPlan:
    """Gera DreamPlan a partir de descrição natural via LLM."""
    forced_preset = style_preset or "lowpoly"

    from .llm_context import build_system_prompt

    system_prompt = build_system_prompt(
        preset_names=preset_names,
        max_assets=max_assets,
        with_audio=with_audio,
        with_sky=with_sky,
    )

    user_prompt = (
        f"Game concept: {description}\n\n"
        f"Style preset to use: {forced_preset}\n"
        f"Maximum assets: {max_assets}\n"
        f"Include audio: {with_audio}\n"
        f"Include sky: {with_sky}\n"
        f"\nRespond ONLY with the JSON object. No extra text."
    )

    raw_text = ""
    try:
        if provider == "openai":
            raw_text = _call_openai(system_prompt, user_prompt, model=model, api_key=api_key, base_url=base_url)
        elif provider == "huggingface":
            raw_text = _call_huggingface(system_prompt, user_prompt, model=model)
        elif provider == "stdin":
            raw_text = _call_stdin(system_prompt, user_prompt)
        else:
            raise ValueError(f"Provider desconhecido: {provider}")

        data = _extract_json(raw_text)
        plan = DreamPlan.from_dict(data)
    except Exception:
        plan = _fallback_plan(description, forced_preset)

    if plan_json_path:
        from pathlib import Path

        p = Path(plan_json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    return plan
