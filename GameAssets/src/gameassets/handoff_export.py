"""Copia assets do batch (output_dir) para ``public/assets`` e gera manifest JSON para o runtime web."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .manifest import ManifestRow
from .paths import (
    _animator3d_output_path,
    _base_stem,
    _collision_path,
    _lod_animated_path,
    _lod_path,
    _rigging3d_output_path,
    _split_path,
    _stump_path,
    _top_path,
)
from .profile import GameProfile

console = Console()


def resolve_handoff_mesh(
    mesh_path: Path,
    row: ManifestRow,
    *,
    prefer_animated: bool,
    prefer_rigged: bool,
    rig_suffix: str = "_rigged",
) -> tuple[Path | None, str]:
    """Escolhe o GLB entregável para handoff (lod0 animado > lod0 > animated alias > rigged > base).

    Entregável canónico é sempre ``id_lod0.glb``. Aliases ``*_rigged_animated`` /
    bare ``id.glb`` só entram como fallback legado.
    """
    stem = _base_stem(mesh_path.stem)
    parent = mesh_path.parent
    lod0 = _lod_path(mesh_path, 0)
    rigged_animated = _animator3d_output_path(mesh_path)
    lod0_animated = _lod_animated_path(mesh_path, 0)
    legacy_animated = parent / f"{stem}_animated.glb"
    rig_out = _rigging3d_output_path(mesh_path, rig_suffix)
    lod0_rigged = parent / f"{stem}_lod0_rigged.glb"

    if prefer_animated:
        # LOD0 promovido (clips) — preferência sobre aliases legacy.
        if row.generate_animate and lod0.is_file():
            return lod0, "animated"
        for cand in (rigged_animated, lod0_animated, legacy_animated):
            if cand.is_file():
                return cand, "animated"

    if prefer_rigged:
        if row.generate_rig and not row.generate_animate and lod0.is_file():
            # Rig-only: lod0 já é o entregável promovido.
            return lod0, "rigged"
        for cand in (rig_out, lod0_rigged):
            if cand.is_file():
                return cand, "rigged"

    if lod0.is_file():
        return lod0, "lod0"
    if mesh_path.is_file():
        return mesh_path, "base"
    return None, "missing"


def _convert_audio(src: Path, dst: Path, *, sample_rate: int, dry_run: bool) -> bool:
    """Convert audio file using ffmpeg. Returns True on success."""
    import subprocess

    if dry_run:
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    argv = ["ffmpeg", "-y", "-i", str(src), "-ar", str(sample_rate), "-vn", "-c:a", "libvorbis", "-q:a", "4", str(dst)]
    r = subprocess.run(argv, capture_output=True, text=True)
    return r.returncode == 0


def _safe_public_id(row_id: str) -> str:
    return row_id.replace("/", "__").replace("\\", "_")


def _install_file(src: Path, dst: Path, *, copy: bool) -> None:
    try:
        if src.resolve() == dst.resolve():
            return
    except OSError:
        pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if copy:
        shutil.copy2(src, dst)
    else:
        os.symlink(src.resolve(), dst, target_is_directory=False)


def run_handoff(
    profile: GameProfile,
    rows: list[ManifestRow],
    manifest_dir: Path,
    public_dir: Path,
    *,
    copy: bool,
    prefer_animated: bool,
    prefer_rigged: bool,
    with_textures: bool,
    audio_format: str = "copy",
    sfx_sample_rate: int = 22050,
    bgm_sample_rate: int = 44100,
    dry_run: bool,
) -> dict[str, Any]:
    """Resolve meshes/áudio, copia ou symlink, devolve manifest dict.

    Modelos públicos: só ``{pid}_lod{N}.glb`` + ``{pid}_collision.glb``.
    Sem bare ``{pid}.glb``.
    """
    from .cli import (
        _audio_path_for_row_manifest,
        _paths_for_row_manifest,
        _texture2d_material_maps_path_manifest,
        _texture2d_profile_effective,
    )

    manifest_dir = manifest_dir.resolve()
    public_dir = public_dir.resolve()
    assets_root = public_dir / "assets"
    models_dir = assets_root / "models"
    audio_dir = assets_root / "audio"
    textures_dir = assets_root / "textures"
    icons_dir = assets_root / "icons"
    out: dict[str, Any] = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "public_dir": str(public_dir),
        "assets_base_url": "/assets",
        "rows": [],
    }

    for row in rows:
        entry: dict[str, Any] = {"id": row.id, "public_id": _safe_public_id(row.id)}
        pid = entry["public_id"]

        if row.generate_3d:
            _img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
            rg = profile.rigging3d
            sfx = rg.output_suffix if rg else "_rigged"
            chosen, chosen_kind = resolve_handoff_mesh(
                mesh_path,
                row,
                prefer_animated=prefer_animated,
                prefer_rigged=prefer_rigged,
                rig_suffix=sfx or "_rigged",
            )

            if chosen is None:
                entry["model_error"] = "GLB não encontrado no output_dir (corre batch antes do handoff)"
                out["rows"].append(entry)
                continue

            # Entregável canónico = *_lod0.glb (nunca bare {pid}.glb).
            dst_lod0 = models_dir / f"{pid}_lod0.glb"
            rel_lod0 = f"/assets/models/{pid}_lod0.glb"
            entry["model"] = {
                "kind": chosen_kind,
                "source": str(chosen),
                "url": rel_lod0,
                "dest": str(dst_lod0),
            }
            if not dry_run:
                _install_file(chosen, dst_lod0, copy=copy)
                # Remove bare legado se existir no destino.
                bare_dst = models_dir / f"{pid}.glb"
                if bare_dst.is_file() or bare_dst.is_symlink():
                    with contextlib.suppress(OSError):
                        bare_dst.unlink()

            lod_basename = _base_stem(row.id.replace("/", "_"))
            lod_urls = [rel_lod0]
            for level in range(1, 3):
                lod_src = chosen.parent / f"{lod_basename}_lod{level}.glb"
                if lod_src.is_file():
                    dst_lod = models_dir / f"{pid}_lod{level}.glb"
                    rel_lod = f"/assets/models/{pid}_lod{level}.glb"
                    lod_urls.append(rel_lod)
                    if not dry_run:
                        _install_file(lod_src, dst_lod, copy=copy)
            entry["model"]["lod"] = lod_urls

            # Collision: sempre {base}_collision.glb (não {chosen.stem}_collision).
            coll_src = _collision_path(mesh_path)
            if not coll_src.is_file():
                coll_src = chosen.parent / f"{lod_basename}_collision.glb"
            if coll_src.is_file():
                dst_coll = models_dir / f"{pid}_collision.glb"
                rel_coll = f"/assets/models/{pid}_collision.glb"
                entry["model"]["collision"] = {"url": rel_coll, "source": str(coll_src), "dest": str(dst_coll)}
                if not dry_run:
                    _install_file(coll_src, dst_coll, copy=copy)

            # Tree split sidecars (Stump+Top composition + halves).
            for key, src in (
                ("split_url", _split_path(mesh_path)),
                ("stump_url", _stump_path(mesh_path)),
                ("top_url", _top_path(mesh_path)),
            ):
                if not src.is_file():
                    continue
                suffix = src.name[len(lod_basename) :]  # _split.glb / _stump.glb / _top.glb
                dst = models_dir / f"{pid}{suffix}"
                rel = f"/assets/models/{pid}{suffix}"
                entry["model"][key] = rel
                if not dry_run:
                    _install_file(src, dst, copy=copy)

        if row.generate_audio:
            audio_src = _audio_path_for_row_manifest(profile, manifest_dir, row)
            if audio_src.is_file():
                src_ext = audio_src.suffix.lower().lstrip(".") or "wav"
                is_sfx = row.audio_profile == "effects" or (row.audio_profile is None and src_ext != "wav")
                sample_rate = sfx_sample_rate if is_sfx else bgm_sample_rate

                if audio_format == "ogg":
                    dst_a = audio_dir / f"{pid}.ogg"
                    rel_a = f"/assets/audio/{pid}.ogg"
                    if dry_run or _convert_audio(audio_src, dst_a, sample_rate=sample_rate, dry_run=dry_run):
                        entry["audio"] = {
                            "source": str(audio_src),
                            "url": rel_a,
                            "dest": str(dst_a),
                            "format": "ogg",
                            "sample_rate": sample_rate,
                        }
                    else:
                        dst_a = audio_dir / f"{pid}.{src_ext}"
                        rel_a = f"/assets/audio/{pid}.{src_ext}"
                        _install_file(audio_src, dst_a, copy=copy)
                        entry["audio"] = {"source": str(audio_src), "url": rel_a, "dest": str(dst_a), "format": src_ext}
                        entry["audio_warning"] = "ffmpeg conversion failed, copied original"
                else:
                    dst_a = audio_dir / f"{pid}.{src_ext}"
                    rel_a = f"/assets/audio/{pid}.{src_ext}"
                    _install_file(audio_src, dst_a, copy=copy)
                    entry["audio"] = {"source": str(audio_src), "url": rel_a, "dest": str(dst_a), "format": src_ext}
            else:
                entry["audio_error"] = f"Ficheiro em falta: {audio_src}"

        if with_textures:
            img_path, _mesh = _paths_for_row_manifest(profile, manifest_dir, row)
            if img_path.is_file():
                ext = img_path.suffix.lower() or ".png"
                dst_t = textures_dir / f"{pid}{ext}"
                rel_t = f"/assets/textures/{pid}{ext}"
                entry["texture"] = {
                    "source": str(img_path),
                    "url": rel_t,
                    "dest": str(dst_t),
                }
                if not dry_run:
                    _install_file(img_path, dst_t, copy=copy)

        # PBR maps (Materialize): normal, metallic, smoothness→roughness, ao
        tt = _texture2d_profile_effective(profile)
        if tt.materialize:
            maps_src = _texture2d_material_maps_path_manifest(profile, manifest_dir, row)
            if maps_src.is_dir():
                pbr_dir = assets_root / "pbr" / pid
                pbr_urls: list[str] = []
                for map_name in ("normal", "metallic", "smoothness", "ao"):
                    fmt = tt.materialize_format or "png"
                    src_file = maps_src / f"{map_name}.{fmt}"
                    if not src_file.is_file():
                        src_file = maps_src / f"{map_name}.png"
                    if not src_file.is_file():
                        continue
                    dst_name = "roughness" if map_name == "smoothness" else map_name
                    ext = src_file.suffix.lstrip(".")
                    dst_file = pbr_dir / f"{dst_name}.{ext}"
                    rel_pbr = f"/assets/pbr/{pid}/{dst_name}.{ext}"
                    if not dry_run:
                        _install_file(src_file, dst_file, copy=copy)
                    pbr_urls.append(rel_pbr)
                if pbr_urls:
                    entry["pbr_textures"] = pbr_urls

        out["rows"].append(entry)

    # --- Scene-level UI icons (text2icon) ---
    # Ícones gerados pelo text2icon em <output_dir>/icons/*.png → public/assets/icons/.
    icons_entries: list[dict[str, str]] = []
    src_icons_dir = Path(profile.output_dir) / "icons"
    if src_icons_dir.is_dir():
        for icon_file in sorted(src_icons_dir.glob("*.png")):
            dst_icon = icons_dir / icon_file.name
            rel_icon = f"/assets/icons/{icon_file.name}"
            icons_entries.append({"source": str(icon_file), "url": rel_icon, "dest": str(dst_icon)})
            if not dry_run:
                icons_dir.mkdir(parents=True, exist_ok=True)
                _install_file(icon_file, dst_icon, copy=copy)
    if icons_entries:
        out["icons"] = icons_entries

    manifest_path = assets_root / "gameassets_handoff.json"
    out["manifest_path"] = str(manifest_path)
    if not dry_run:
        assets_root.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    return out


def handoff_command_impl(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
    public_dir: Path,
    *,
    copy: bool,
    prefer_animated: bool,
    prefer_rigged: bool,
    with_textures: bool,
    audio_format: str = "copy",
    sfx_sample_rate: int = 22050,
    bgm_sample_rate: int = 44100,
    dry_run: bool,
) -> None:
    from .cli import _build_context

    profile, rows, _bundle, _preset = _build_context(profile_path, manifest_path, presets_local)
    manifest_dir = manifest_path.parent.resolve()
    data = run_handoff(
        profile,
        rows,
        manifest_dir,
        public_dir,
        copy=copy,
        prefer_animated=prefer_animated,
        prefer_rigged=prefer_rigged,
        with_textures=with_textures,
        audio_format=audio_format,
        sfx_sample_rate=sfx_sample_rate,
        bgm_sample_rate=bgm_sample_rate,
        dry_run=dry_run,
    )
    title = "[bold]Handoff[/bold]" + (" [cyan](dry-run)[/cyan]" if dry_run else "")
    body = json.dumps(data, ensure_ascii=False, indent=2)
    if len(body) > 12000:
        body = body[:12000] + "\n… [truncado para consola; ver ficheiro ou --dry-run com jq]"
    console.print(Panel(body, title=title, border_style="cyan"))
    if not dry_run:
        console.print(
            Panel(
                f"[green]Manifest[/green] [bold]{data['manifest_path']}[/bold]",
                border_style="green",
            )
        )
