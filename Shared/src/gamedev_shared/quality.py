"""QualityEngine — unified cross-tool quality preset resolution.

Loads two YAML files (quality-profiles.yaml, asset-categories.yaml) and
resolves optimal per-tool parameters from a quality tier + asset category.

Usage::

    engine = QualityEngine()
    r = engine.resolve("text2sound", quality="medium", category="weapon")
    # r.params = {steps: 32, cfg_scale: 8.0, sampler: "pingpong", ...}
    # r.audio_kind = "sfx_impact"
    # r.model_id = "stabilityai/stable-audio-open-small"
    # r.prompt_hints = ["immediate attack, no silence at start"]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parent / "data"

VALID_QUALITIES = ("fast", "low", "medium", "high", "highest")

# Audio model IDs (kept in-sync with Text2Sound/models.py)
_MODEL_MUSIC_ID = "stabilityai/stable-audio-open-1.0"
_MODEL_EFFECTS_ID = "stabilityai/stable-audio-open-small"


@dataclass(frozen=True)
class QualityResolution:
    """Fully resolved parameters for one tool + quality + category combo."""

    params: dict[str, Any]
    """Merged parameter dict ready to apply (e.g. {steps: 32, cfg_scale: 6.0})."""

    category: str | None = None
    """Resolved asset category name (e.g. "weapon")."""

    audio_kind: str | None = None
    """Audio kind for text2sound (e.g. "sfx_impact"). None for non-audio tools."""

    model_id: str | None = None
    """HF model ID for text2sound. Resolved from audio_kind → model field."""

    prompt_hints: list[str] = field(default_factory=list)
    """Hints to inject into the generation prompt (per audio_kind)."""

    source: str = "quality_profile"
    """Where the winning value was resolved:
    "explicit" | "category" | "quality_profile" | "default".
    """


class QualityEngine:
    """Cross-tool quality presets backed by two YAML files.

    Resolution priority: *overrides > category > quality_profile > built-in defaults*.
    """

    def __init__(
        self,
        profiles_path: Path | None = None,
        categories_path: Path | None = None,
    ) -> None:
        self._profiles: dict[str, Any] = _load_yaml(profiles_path or DATA_DIR / "quality-profiles.yaml")
        self._categories: dict[str, Any] = _load_yaml(categories_path or DATA_DIR / "asset-categories.yaml")

    # ── Public API ────────────────────────────────────────────────────

    def resolve(
        self,
        tool: str,
        quality: str = "medium",
        category: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> QualityResolution:
        """Resolve optimal parameters for *tool* at *quality* tier.

        Args:
            tool: Tool name — ``"text2sound"``, ``"text3d"``, ``"text2d"``,
                  ``"paint3d"``, ``"simplify"``, etc. Must exist in the
                  quality profiles YAML.
            quality: Quality tier — ``fast | low | medium | high | highest``.
            category: Optional asset category (e.g. ``"weapon"``) for
                      tool-specific overrides from ``asset-categories.yaml``.
            overrides: Manual parameter overrides (highest priority).

        Returns:
            ``QualityResolution`` with merged params, metadata, and hints.
        """
        # 1. Base from quality profile
        params: dict[str, Any] = {}
        source_map: dict[str, str] = {}

        profile_data = self._resolve_profile(tool, quality)
        if profile_data:
            params.update(profile_data)
            for key in profile_data:
                source_map[key] = "quality_profile"

        # 2. Category overrides
        cat_data = self._category_tool_params(tool, category)
        if cat_data:
            params.update(cat_data)
            for key in cat_data:
                source_map[key] = "category"

        # 3. Explicit overrides (highest priority)
        if overrides:
            params.update(overrides)
            for key in overrides:
                source_map[key] = "explicit"

        # Determine dominant source
        dominant = "explicit" if overrides else ("category" if cat_data else "quality_profile")

        # Audio-specific metadata
        audio_kind: str | None = None
        model_id: str | None = None
        prompt_hints: list[str] = []

        if tool == "text2sound":
            audio_kind = self._resolve_audio_kind(category)
            if audio_kind:
                kind_data = self._audio_kind_data(audio_kind)
                # Merge kind defaults only if not already set by overrides
                for k in ("sampler", "cfg_scale_default"):
                    tk = {"cfg_scale_default": "cfg_scale"}.get(k, k)
                    if tk not in params and kind_data.get(k) is not None:
                        params[tk] = kind_data[k]
                        source_map[tk] = "audio_kind"
                model_id = self._resolve_model_id(kind_data)
                if kind_data.get("loop_hint"):
                    prompt_hints.append(kind_data.get("prompt_hint", "seamless loop"))
                elif kind_data.get("prompt_hint"):
                    prompt_hints.append(kind_data["prompt_hint"])

        return QualityResolution(
            params=params,
            category=category,
            audio_kind=audio_kind,
            model_id=model_id,
            prompt_hints=prompt_hints,
            source=dominant,
        )

    def list_qualities(self) -> list[str]:
        """Return valid quality tier names (sorted)."""
        return sorted(self._profiles.get("profiles", {}).keys())

    def list_categories(self) -> list[str]:
        """Return valid asset category names (sorted)."""
        return sorted(self._categories.get("categories", {}).keys())

    def list_audio_kinds(self) -> list[str]:
        """Return valid audio kind names (sorted)."""
        return sorted(self._categories.get("audio_kinds", {}).keys())

    def category_info(self, name: str) -> dict[str, Any]:
        """Return full category metadata dict (target_faces, audio, text3d, paint, ...)."""
        cats = self._categories.get("categories", {})
        if name not in cats:
            raise KeyError(f"Unknown category: {name!r}. Valid: {', '.join(self.list_categories())}")
        return dict(cats[name])

    def audio_kind_info(self, name: str) -> dict[str, Any]:
        """Return full audio kind metadata dict."""
        kinds = self._categories.get("audio_kinds", {})
        if name not in kinds:
            raise KeyError(f"Unknown audio kind: {name!r}. Valid: {', '.join(self.list_audio_kinds())}")
        return dict(kinds[name])

    # ── Internal helpers ──────────────────────────────────────────────

    def _resolve_profile(self, tool: str, quality: str) -> dict[str, Any]:
        profiles = self._profiles.get("profiles", {})
        if quality not in profiles:
            raise KeyError(f"Unknown quality: {quality!r}. Valid: {', '.join(self.list_qualities())}")
        return dict(profiles[quality].get(tool, {}))

    def _category_tool_params(self, tool: str, category: str | None) -> dict[str, Any] | None:
        """Extract tool-specific params from a category entry."""
        if not category:
            return None
        cats = self._categories.get("categories", {})
        cat = cats.get(category)
        if cat is None:
            return None

        # Map tool name to category key
        tool_map: dict[str, str] = {
            "text3d": "text3d",
            "paint3d": "paint",
            "text2sound": "audio",
            "simplify": "simplify",
        }
        cat_key = tool_map.get(tool, tool)
        tool_data = cat.get(cat_key)
        if tool_data and isinstance(tool_data, dict):
            return dict(tool_data)
        return None

    def _resolve_audio_kind(self, category: str | None) -> str | None:
        if not category:
            return None
        cats = self._categories.get("categories", {})
        cat = cats.get(category)
        if cat is None:
            return None
        audio = cat.get("audio", {})
        return audio.get("kind") if isinstance(audio, dict) else None

    def _audio_kind_data(self, kind: str) -> dict[str, Any]:
        kinds = self._categories.get("audio_kinds", {})
        return dict(kinds.get(kind, {}))

    @staticmethod
    def _resolve_model_id(kind_data: dict[str, Any]) -> str | None:
        model = kind_data.get("model", "")
        if model == "effects":
            return _MODEL_EFFECTS_ID
        if model == "music":
            return _MODEL_MUSIC_ID
        return None


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
