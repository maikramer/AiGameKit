"""DreamPlan dataclass + LLM-backed planner (providers, cache, refine)."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
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
    icon_prompts: list[str] = field(default_factory=list)
    # Provenance + determinismo (não vão ao LLM; roundtrip em dream_plan.json)
    seed: int | None = None
    source: str = ""  # "llm:openai" | "llm:ollama" | "cache" | "fallback" | "refine:openai" | "refine-failed"
    source_detail: str = ""  # modelo, cache hit ou erro do provider
    repairs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "title": self.title,
            "genre": self.genre,
            "tone": self.tone,
            "style_preset": self.style_preset,
            "sky_prompt": self.sky_prompt,
            "icon_prompts": self.icon_prompts,
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
        if self.seed is not None:
            result["seed"] = self.seed
        if self.source:
            result["source"] = self.source
        if self.source_detail:
            result["source_detail"] = self.source_detail
        if self.repairs:
            result["repairs"] = list(self.repairs)
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
            icon_prompts=[str(x) for x in d.get("icon_prompts", []) if str(x).strip()],
            assets=assets,
            scene=SceneLayout(
                sky_color=sc.get("sky_color", "#87CEEB"),
                ground_size=sc.get("ground_size", 50),
                spawn_y=sc.get("spawn_y", 5),
                placements=placements,
            ),
            terrain=terrain,
            seed=d.get("seed"),
            source=str(d.get("source", "")),
            source_detail=str(d.get("source_detail", "")),
            repairs=[str(r) for r in d.get("repairs", [])],
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


def _ollama_base_url(base_url: str | None) -> str:
    host = base_url or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host.rstrip("/")


def _call_ollama(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None,
    base_url: str | None,
) -> str:
    """Ollama local via API nativa ``/api/chat`` (stdlib urllib, sem deps)."""
    import urllib.request

    url = f"{_ollama_base_url(base_url)}/api/chat"
    payload = {
        "model": model or os.environ.get("DREAM_OLLAMA_MODEL", "llama3.1:8b"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.7},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout_s = float(os.environ.get("DREAM_OLLAMA_TIMEOUT", "180"))
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = (data.get("message") or {}).get("content", "")
    if not content:
        raise RuntimeError(f"resposta ollama sem conteúdo: {str(data)[:200]}")
    return content


PROVIDERS = ("openai", "huggingface", "ollama", "stdin")

_DEFAULT_MODEL_BY_PROVIDER = {
    "openai": "gpt-4o-mini",
    "huggingface": "meta-llama/Llama-3.1-8B-Instruct",
    "ollama": "llama3.1:8b",
    "stdin": "",
}

_PROVIDER_HINTS = {
    "openai": "define OPENAI_API_KEY (ou --llm-api-key / --llm-base-url)",
    "huggingface": "pip install huggingface_hub e valida o token HF",
    "ollama": "corre `ollama serve` e `ollama pull llama3.1:8b` (env: OLLAMA_HOST, DREAM_OLLAMA_MODEL)",
    "stdin": "pipe a resposta de um LLM CLI via stdin",
}


def _call_provider(
    provider: str,
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
) -> str:
    if provider == "openai":
        return _call_openai(system_prompt, user_prompt, model=model, api_key=api_key, base_url=base_url)
    if provider == "huggingface":
        return _call_huggingface(system_prompt, user_prompt, model=model)
    if provider == "ollama":
        return _call_ollama(system_prompt, user_prompt, model=model, base_url=base_url)
    if provider == "stdin":
        return _call_stdin(system_prompt, user_prompt)
    raise ValueError(f"Provider desconhecido: {provider}")


# ---------------------------------------------------------------------------
# Cache de planos (mesma descrição + flags ⇒ mesmo plano, sem novo call LLM)
# ---------------------------------------------------------------------------

_DREAM_CACHE_VERSION = 2


def _dream_cache_dir() -> Path:
    env = os.environ.get("AIGAMEKIT_DREAM_CACHE")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "aigamekit" / "dream" / "plans"


def _plan_cache_key(
    *,
    description: str,
    provider: str,
    model: str | None,
    max_assets: int,
    with_audio: bool,
    with_sky: bool,
    style_preset: str | None,
    preset_names: list[str],
    prompt_version: str,
) -> str:
    payload = json.dumps(
        {
            "v": _DREAM_CACHE_VERSION,
            "prompt": prompt_version,
            "description": description,
            "provider": provider,
            "model": model or "",
            "max_assets": max_assets,
            "with_audio": with_audio,
            "with_sky": with_sky,
            "style_preset": style_preset or "",
            "presets": sorted(preset_names),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _load_cached_plan(path: Path) -> DreamPlan | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        plan = DreamPlan.from_dict(data)
    except Exception:
        return None
    plan.source = "cache"
    return plan


def _store_cached_plan(path: Path, plan: DreamPlan) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # cache é best-effort


def _export_plan_json(plan: DreamPlan, plan_json_path: str | None) -> None:
    if not plan_json_path:
        return
    p = Path(plan_json_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


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
    use_cache: bool = True,
) -> DreamPlan:
    """Gera DreamPlan a partir de descrição natural via LLM.

    Mesma descrição + mesmas flags reutiliza o plano em cache (``--replan``
    força novo call). Falha do provider → fallback determinístico com o motivo
    em ``plan.source_detail`` (nunca falha em silêncio). Todo plano passa pelo
    lint + auto-reparo (``planlint.lint_and_repair``).
    """
    from .llm_context import DREAM_PROMPT_VERSION, build_system_prompt
    from .planlint import lint_and_repair

    forced_preset = style_preset or "lowpoly"

    cacheable = use_cache and provider in ("openai", "huggingface", "ollama")
    cache_path: Path | None = None
    if cacheable:
        key = _plan_cache_key(
            description=description,
            provider=provider,
            model=model,
            max_assets=max_assets,
            with_audio=with_audio,
            with_sky=with_sky,
            style_preset=style_preset,
            preset_names=preset_names,
            prompt_version=DREAM_PROMPT_VERSION,
        )
        cache_path = _dream_cache_dir() / f"{key}.json"
        if cache_path.is_file():
            cached = _load_cached_plan(cache_path)
            if cached is not None:
                cached.source_detail = f"cache hit {cache_path.name[:12]} (--replan para regenerar)"
                _export_plan_json(cached, plan_json_path)
                return cached

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

    plan: DreamPlan
    try:
        raw_text = _call_provider(provider, system_prompt, user_prompt, model=model, api_key=api_key, base_url=base_url)
        data = _extract_json(raw_text)
        plan = DreamPlan.from_dict(data)
        plan.source = f"llm:{provider}"
        plan.source_detail = model or _DEFAULT_MODEL_BY_PROVIDER.get(provider, "")
    except Exception as exc:
        plan = _fallback_plan(description, forced_preset)
        plan.source = "fallback"
        hint = _PROVIDER_HINTS.get(provider, "")
        plan.source_detail = f"{provider}: {exc}" + (f" — {hint}" if hint else "")

    plan, repairs, _residual = lint_and_repair(plan, max_assets=max_assets)
    plan.repairs = repairs

    if plan.source.startswith("llm:") and cache_path is not None:
        _store_cached_plan(cache_path, plan)

    _export_plan_json(plan, plan_json_path)
    return plan


def refine_plan(
    plan: DreamPlan,
    instruction: str,
    *,
    preset_names: list[str],
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_assets: int = 8,
    with_audio: bool = True,
    with_sky: bool = True,
) -> DreamPlan:
    """Refina um plano existente com uma instrução natural ("add a dragon").

    Em sucesso devolve o plano refinado (lint + auto-reparo aplicados). Em
    falha do provider devolve o plano ORIGINAL intocado com
    ``source="refine-failed"`` e o motivo em ``source_detail`` — refine nunca
    destrói trabalho. Seeds pinados são preservados; terrain só desaparece se
    o LLM devolver ``enabled=false`` explicitamente.
    """
    from .llm_context import build_refine_prompt
    from .planlint import lint_and_repair

    old_seed = plan.seed
    old_terrain = copy.deepcopy(plan.terrain)

    embed = plan.to_dict()
    for key in ("source", "source_detail", "repairs"):
        embed.pop(key, None)

    system_prompt = build_refine_prompt(
        preset_names=preset_names,
        max_assets=max_assets,
        with_audio=with_audio,
        with_sky=with_sky,
    )
    user_prompt = (
        f"CURRENT PLAN JSON:\n{json.dumps(embed, ensure_ascii=False, indent=2)}\n\n"
        f"INSTRUCTION: {instruction}\n\n"
        "Respond ONLY with the FULL updated JSON object."
    )

    try:
        raw_text = _call_provider(provider, system_prompt, user_prompt, model=model, api_key=api_key, base_url=base_url)
        data = _extract_json(raw_text)
        new_plan = DreamPlan.from_dict(data)
    except Exception as exc:
        kept = copy.deepcopy(plan)
        kept.source = "refine-failed"
        hint = _PROVIDER_HINTS.get(provider, "")
        kept.source_detail = f"{provider}: {exc}" + (f" — {hint}" if hint else "")
        return kept

    if new_plan.seed is None:
        new_plan.seed = old_seed
    if old_terrain is not None and new_plan.terrain is None:
        new_plan.terrain = old_terrain  # omitir não é desligar; para remover, enabled=false
    if (
        new_plan.terrain is not None
        and old_terrain is not None
        and new_plan.terrain.seed is None
        and old_terrain.seed is not None
    ):
        new_plan.terrain.seed = old_terrain.seed

    new_plan, repairs, _residual = lint_and_repair(new_plan, max_assets=max_assets)
    new_plan.repairs = repairs
    new_plan.source = f"refine:{provider}"
    new_plan.source_detail = model or _DEFAULT_MODEL_BY_PROVIDER.get(provider, "")
    return new_plan


def apply_seed(plan: DreamPlan, seed: int) -> DreamPlan:
    """Pina seed determinístico: ``game.yaml seed_base`` + terrain seed (se não fixado)."""
    plan.seed = seed
    if plan.terrain is not None and plan.terrain.seed is None:
        plan.terrain.seed = seed
    return plan


def load_plan_path(path: str | Path) -> DreamPlan:
    """Carrega DreamPlan de um dream_plan.json (hand-edit friendly)."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return DreamPlan.from_dict(data)
