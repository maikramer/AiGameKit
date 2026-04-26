"""Small utility helpers (seed, dry_run, timing, audio paths, profile helpers)."""

from __future__ import annotations

import zlib
from pathlib import Path
from typing import Any

from .cli_rich import click
from .manifest import ManifestRow, effective_image_source, load_manifest
from .presets import get_preset, load_presets_bundle
from .profile import (
    GameProfile,
    Text2SoundProfile,
    Texture2DProfile,
    load_profile,
)
from .runner import resolve_binary


def _dry_run_emit(
    plan: list[dict[str, Any]] | None,
    *,
    phase: str,
    row_id: str | None,
    argv: list[str],
) -> None:
    """Regista um passo do dry-run (JSON) ou imprime argv no terminal."""
    if plan is not None:
        plan.append({"phase": phase, "row_id": row_id, "argv": argv})
    else:
        from rich.console import Console

        Console().print(f"[dim]{' '.join(argv)}[/dim]")


def _dry_run_header(plan: list[dict[str, Any]] | None, message: str) -> None:
    """Cabeçalho de fase no dry-run (argv vazio)."""
    if plan is not None:
        plan.append({"phase": message, "row_id": None, "argv": []})
    else:
        from rich.console import Console

        Console().print(f"[dim]{message}[/dim]")


def _seed_for_row(profile: GameProfile, row_id: str) -> int | None:
    if profile.seed_base is None:
        return None
    h = zlib.adler32(row_id.encode("utf-8")) & 0x7FFFFFFF
    return profile.seed_base + h


def _safe_row_dirname(row_id: str) -> str:
    """Parte do id do manifest segura para nome de pasta (ex.: Props/crate → Props__crate_01)."""
    return row_id.replace("/", "__").replace("\\", "_")


def _text2sound_profile_effective(profile: GameProfile) -> Text2SoundProfile:
    """Opções Text2Sound do perfil ou defaults."""
    return profile.text2sound or Text2SoundProfile()


def _audio_path_for_row(profile: GameProfile, row: ManifestRow) -> Path:
    """Ficheiro de áudio final (extensão do perfil text2sound)."""
    ts = _text2sound_profile_effective(profile)
    ext = (ts.audio_format or "wav").lower().strip().lstrip(".")
    root = Path(profile.output_dir)
    rid = row.id
    if profile.path_layout == "flat":
        parts = rid.split("/")
        if len(parts) >= 2:
            sub = Path(*parts[:-1])
            base = parts[-1]
            dir_ = root / sub
        else:
            dir_ = root
            base = rid
        return dir_ / f"{base}.{ext}"
    return root / profile.audio_subdir / f"{rid}.{ext}"


def _audio_path_for_row_manifest(
    profile: GameProfile,
    manifest_dir: Path,
    row: ManifestRow,
) -> Path:
    rel = _audio_path_for_row(profile, row)
    if rel.is_absolute():
        return rel.resolve()
    return (manifest_dir / rel).resolve()


def _append_text2sound_profile_args(ts: Text2SoundProfile, argv: list[str]) -> None:
    """Extensões do perfil para `text2sound generate`."""
    if ts.duration is not None:
        argv.extend(["-d", str(ts.duration)])
    if ts.steps is not None:
        argv.extend(["-s", str(ts.steps)])
    if ts.cfg_scale is not None:
        argv.extend(["-c", str(ts.cfg_scale)])
    fmt = (ts.audio_format or "wav").lower().strip().lstrip(".")
    argv.extend(["-f", fmt])
    if ts.preset and ts.preset.lower() != "none":
        argv.extend(["-p", ts.preset])
    if ts.sigma_min is not None:
        argv.extend(["--sigma-min", str(ts.sigma_min)])
    if ts.sigma_max is not None:
        argv.extend(["--sigma-max", str(ts.sigma_max)])
    if ts.sampler:
        argv.extend(["--sampler", ts.sampler])
    if ts.trim is not None:
        argv.append("--trim" if ts.trim else "--no-trim")
    if ts.model_id:
        argv.extend(["-m", ts.model_id])
    if ts.half_precision is True:
        argv.append("--half")
    elif ts.half_precision is False:
        argv.append("--no-half")


def _text2sound_args_for_row(
    profile: Text2SoundProfile,
    row: ManifestRow,
    argv: list[str],
) -> None:
    """Append text2sound args: per-row overrides take precedence over global profile."""
    duration = row.audio_duration if row.audio_duration is not None else profile.duration
    steps = row.audio_steps if row.audio_steps is not None else profile.steps
    cfg = row.audio_cfg_scale if row.audio_cfg_scale is not None else profile.cfg_scale
    trim = row.audio_trim if row.audio_trim is not None else profile.trim
    preset = row.audio_preset if row.audio_preset is not None else profile.preset

    if duration is not None:
        argv.extend(["-d", str(duration)])
    if steps is not None:
        argv.extend(["-s", str(steps)])
    if cfg is not None:
        argv.extend(["-c", str(cfg)])
    fmt = (profile.audio_format or "wav").lower().strip().lstrip(".")
    argv.extend(["-f", fmt])
    if preset and preset.lower() != "none":
        argv.extend(["-p", preset])
    if profile.sigma_min is not None:
        argv.extend(["--sigma-min", str(profile.sigma_min)])
    if profile.sigma_max is not None:
        argv.extend(["--sigma-max", str(profile.sigma_max)])
    if profile.sampler:
        argv.extend(["--sampler", profile.sampler])
    if trim is not None:
        argv.append("--trim" if trim else "--no-trim")
    if row.audio_profile:
        argv.extend(["--profile", row.audio_profile])
    elif profile.model_id:
        argv.extend(["-m", profile.model_id])
    if profile.half_precision is True:
        argv.append("--half")
    elif profile.half_precision is False:
        argv.append("--no-half")


def _texture2d_profile_effective(profile: GameProfile) -> Texture2DProfile:
    """Opções Texture2D do perfil ou defaults (para linhas texture2d sem bloco no YAML)."""
    return profile.texture2d or Texture2DProfile()


def _row_uses_texture2d(profile: GameProfile, row: ManifestRow) -> bool:
    return effective_image_source(profile, row) == "texture2d"


def _texture2d_material_maps_path(profile: GameProfile, row: ManifestRow) -> Path:
    """Destino dos mapas PBR (Materialize CLI) quando a linha usa texture2d."""
    tt = _texture2d_profile_effective(profile)
    sub = tt.materialize_maps_subdir or "pbr_maps"
    return Path(profile.output_dir) / sub / _safe_row_dirname(row.id)


def _texture2d_material_maps_path_manifest(
    profile: GameProfile,
    manifest_dir: Path,
    row: ManifestRow,
) -> Path:
    rel = _texture2d_material_maps_path(profile, row)
    if rel.is_absolute():
        return rel.resolve()
    return (manifest_dir / rel).resolve()


def _append_text2d_profile_args(profile: GameProfile, argv: list[str]) -> None:
    """Extensões do perfil (resolução, VRAM) para `text2d generate`."""
    t2 = profile.text2d
    if not t2:
        return
    if t2.width is not None:
        argv.extend(["-W", str(t2.width)])
    if t2.height is not None:
        argv.extend(["-H", str(t2.height)])
    if t2.low_vram:
        argv.append("--low-vram")
    if t2.cpu:
        argv.append("--cpu")
    if t2.steps is not None:
        argv.extend(["-s", str(t2.steps)])
    if t2.guidance_scale is not None:
        argv.extend(["-g", str(t2.guidance_scale)])


def _append_texture2d_profile_args(tt: Texture2DProfile, argv: list[str]) -> None:
    """Extensões do perfil para `texture2d generate`."""
    if tt.width is not None:
        argv.extend(["-W", str(tt.width)])
    if tt.height is not None:
        argv.extend(["-H", str(tt.height)])
    if tt.steps is not None:
        argv.extend(["-s", str(tt.steps)])
    if tt.guidance_scale is not None:
        argv.extend(["-g", str(tt.guidance_scale)])
    if tt.negative_prompt:
        argv.extend(["-n", tt.negative_prompt])
    if tt.preset and tt.preset.lower() != "none":
        argv.extend(["-p", tt.preset])
    if tt.cfg_scale is not None:
        argv.extend(["--cfg-scale", str(tt.cfg_scale)])
    if tt.lora_strength is not None:
        argv.extend(["--lora-strength", str(tt.lora_strength)])
    if tt.model_id:
        argv.extend(["-m", tt.model_id])


def _resolve_materialize_bin_texture2d(tt: Texture2DProfile) -> str:
    """Binário Materialize para o fluxo texture2d (override no perfil ou MATERIALIZE_BIN)."""
    if tt.materialize_bin:
        return tt.materialize_bin
    return resolve_binary("MATERIALIZE_BIN", "materialize")


def _materialize_diffuse_argv(
    materialize_bin: str,
    tt: Texture2DProfile,
    diffuse_path: Path,
    output_dir: Path,
) -> list[str]:
    """Invocação materialize <difuso> -o <dir> (mapas PBR a partir de imagem)."""
    args = [materialize_bin, str(diffuse_path), "-o", str(output_dir)]
    fmt = tt.materialize_format or "png"
    args.extend(["-f", fmt])
    args.extend(["-q", str(tt.materialize_quality)])
    if tt.materialize_verbose:
        args.append("-v")
    return args


def _timing_append(rec: dict[str, Any], key: str, seconds: float) -> None:
    t = rec.setdefault("timings_sec", {})
    t[key] = round(seconds, 4)


def _row_wants_rig(row: ManifestRow, has_rigging_profile: bool) -> bool:
    """Auto-detect rig eligibility: explicit column OR character kind + profile block."""
    return bool(row.generate_rig or (has_rigging_profile and row.kind == "character"))


def _row_wants_parts(row: ManifestRow, has_parts_profile: bool) -> bool:
    """Auto-detect parts eligibility: explicit column OR profile block + 3D."""
    return bool(row.generate_parts or (has_parts_profile and row.generate_3d))


def _row_wants_audio(row: ManifestRow, has_audio_profile: bool) -> bool:
    """Auto-detect audio eligibility: explicit column OR profile block."""
    return bool(row.generate_audio or has_audio_profile)


def _row_wants_animate(row: ManifestRow, with_rig: bool, has_rigging_profile: bool) -> bool:
    """Linha elegível para game-pack quando animate está activo."""
    return bool(row.generate_animate or (with_rig and _row_wants_rig(row, has_rigging_profile)))


def _resolve_manifest_path(raw: str | Path) -> Path:
    """Resolve manifest path: if no extension, try .yaml, .yml."""
    p = Path(raw)
    if p.suffix.lower() in (".yaml", ".yml"):
        return p
    for ext in (".yaml", ".yml"):
        candidate = p.with_suffix(ext)
        if candidate.is_file():
            return candidate
    return p.with_suffix(".yaml")


def _build_context(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
) -> tuple[GameProfile, list[ManifestRow], dict[str, Any], dict[str, Any]]:
    resolved = _resolve_manifest_path(manifest_path)
    if not resolved.is_file():
        raise click.ClickException(f"Manifest não encontrado: {manifest_path} (tentado {resolved})")
    profile = load_profile(profile_path)
    if profile.generation:
        from .profile import apply_generation_profile

        profile = apply_generation_profile(profile, profile.generation)
    rows = load_manifest(resolved)
    bundle = load_presets_bundle(presets_local)
    preset = get_preset(bundle, profile.style_preset)
    return profile, rows, bundle, preset


def effective_face_ratio(profile: GameProfile, row: ManifestRow) -> float:
    """Face ratio for the current row (manifest override > game.yaml generation profile)."""
    gen_name = row.generation or profile.generation
    if not gen_name:
        return 1.0
    from .generation_profiles import get_profile

    return get_profile(gen_name).simplify_face_ratio
