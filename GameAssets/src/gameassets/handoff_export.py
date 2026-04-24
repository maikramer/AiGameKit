"""Copia assets do batch (output_dir) para ``public/assets`` e gera manifest JSON para o runtime web."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .manifest import ManifestRow
from .profile import GameProfile

console = Console()


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
    prefer_parts: bool,
    with_textures: bool,
    audio_format: str = "copy",
    sfx_sample_rate: int = 22050,
    bgm_sample_rate: int = 44100,
    dry_run: bool,
) -> dict[str, Any]:
    """Resolve meshes/áudio, copia ou symlink, devolve manifest dict."""
    from .cli import (
        _audio_path_for_row_manifest,
        _part3d_output_paths,
        _part3d_profile_effective,
        _paths_for_row_manifest,
        _rigging3d_output_path,
        _texture2d_material_maps_path_manifest,
        _texture2d_profile_effective,
    )

    manifest_dir = manifest_dir.resolve()
    public_dir = public_dir.resolve()
    assets_root = public_dir / "assets"
    models_dir = assets_root / "models"
    audio_dir = assets_root / "audio"
    textures_dir = assets_root / "textures"
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
            rig_out = _rigging3d_output_path(mesh_path, sfx or "_rigged")
            p3_row = _part3d_profile_effective(profile, row)
            out_p, _ = _part3d_output_paths(mesh_path, p3_row)
            anim_out = mesh_path.with_name(f"{mesh_path.stem}_animated.glb")

            chosen: Path | None = None
            chosen_kind = "base"
            if prefer_animated and anim_out.is_file():
                chosen = anim_out
                chosen_kind = "animated"
            elif prefer_rigged and rig_out.is_file():
                chosen = rig_out
                chosen_kind = "rigged"
            elif prefer_parts and out_p.is_file():
                chosen = out_p
                chosen_kind = "parts"
            elif mesh_path.is_file():
                chosen = mesh_path
                chosen_kind = "base"
            else:
                entry["model_error"] = "GLB não encontrado no output_dir (corre batch antes do handoff)"
                out["rows"].append(entry)
                continue

            dst = models_dir / f"{pid}.glb"
            rel_url = f"/assets/models/{pid}.glb"
            entry["model"] = {
                "kind": chosen_kind,
                "source": str(chosen),
                "url": rel_url,
                "dest": str(dst),
            }
            if not dry_run:
                _install_file(chosen, dst, copy=copy)

            # LOD triplet
            lod_basename = row.id.replace("/", "_")
            lod_urls = []
            for level in range(3):
                lod_src = chosen.parent / f"{lod_basename}_lod{level}.glb"
                if lod_src.is_file():
                    dst_lod = models_dir / f"{pid}_lod{level}.glb"
                    rel_lod = f"/assets/models/{pid}_lod{level}.glb"
                    lod_urls.append(rel_lod)
                    if not dry_run:
                        _install_file(lod_src, dst_lod, copy=copy)
            if lod_urls:
                entry["model"]["lod"] = lod_urls

            # Collision mesh
            coll_src = chosen.parent / f"{chosen.stem}_collision.glb"
            if coll_src.is_file():
                dst_coll = models_dir / f"{pid}_collision.glb"
                rel_coll = f"/assets/models/{pid}_collision.glb"
                entry["model"]["collision"] = {"url": rel_coll, "source": str(coll_src), "dest": str(dst_coll)}
                if not dry_run:
                    _install_file(coll_src, dst_coll, copy=copy)

        if row.generate_audio:
            audio_src = _audio_path_for_row_manifest(profile, manifest_dir, row)
            if audio_src.is_file():
                src_ext = audio_src.suffix.lower().lstrip(".") or "wav"
                is_sfx = row.audio_profile == "effects" or (row.audio_profile is None and src_ext != "wav")
                sample_rate = sfx_sample_rate if is_sfx else bgm_sample_rate

                if audio_format == "ogg":
                    dst_a = audio_dir / f"{pid}.ogg"
                    rel_a = f"/assets/audio/{pid}.ogg"
                    if dry_run or _convert_audio(
                        audio_src, dst_a, sample_rate=sample_rate, dry_run=dry_run
                    ):
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
    prefer_parts: bool,
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
        prefer_parts=prefer_parts,
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
