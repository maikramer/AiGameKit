"""Perfil de jogo (game.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _parse_output_dir(raw: Any) -> str:
    """Raiz dos assets (defeito: diretório atual — só `images/` e `meshes/`, sem pasta `outputs/`)."""
    if raw is None:
        return "."
    s = str(raw).strip()
    return s if s else "."


@dataclass
class Text3DProfile:
    """Opções passadas ao CLI text3d generate (subconjunto)."""

    preset: str | None = None  # fast | balanced | hq
    low_vram: bool = False
    texture: bool = False
    # Origem ao exportar GLB (shape): feet = base AABB em Y=0 e XZ centrado (defeito para props/personagens).
    # Sobrescreve TEXT3D_EXPORT_ORIGIN no batch gameassets (reprodutibilidade).
    export_origin: str = "feet"
    # Após paint3d texture: repor convenção "pés" na mesh final (alinhado com Text3D).
    paint_preserve_origin: bool = True
    # Se qualquer um estiver definido, não se passa --preset (o text3d aplica preset por cima de --steps)
    steps: int | None = None
    octree_resolution: int | None = None
    num_chunks: int | None = None
    no_mesh_repair: bool = False
    mesh_smooth: int | None = None
    mc_level: float | None = None
    # Repasse aos CLIs text3d generate / paint3d (VRAM / exclusividade GPU)
    allow_shared_gpu: bool = False
    gpu_kill_others: bool = True
    # Com textura o batch corre em fases (shape → paint3d texture).
    phased_batch: bool = False
    # GPU pura: Text2D inteiro na GPU; no paint3d activa --paint-full-gpu quando aplicável.
    full_gpu: bool = False
    # Subpasta do modelo Hunyuan3D shape (ex.: hunyuan3d-dit-v2-1)
    model_subfolder: str | None = None
    # --- Paint3D texture options (aplicáveis quando texture=true) ---
    # Número de vistas multiview para texturização (menos = mais rápido; padrão 4)
    paint_max_views: int | None = None
    # Resolução das vistas internas (menor = mais rápido; padrão 512)
    paint_view_resolution: int | None = None
    # Resolução de rasterização para back-projection (padrão upstream: 2048)
    paint_render_size: int | None = None
    # Resolução do atlas UV final (padrão upstream: 4096)
    paint_texture_size: int | None = None
    # Expoente de blending entre vistas (maior = costuras mais nítidas; padrão 6)
    paint_bake_exp: int | None = None
    # --- Otimizações de VRAM para Paint3D ---
    # Modo de quantização: auto, none, fp8, int8, int4, quanto-int8, quanto-int4
    paint_quantization: str | None = None
    # Usar Tiny VAE (TAESD) para reduzir VRAM do VAE
    paint_tiny_vae: bool = False
    # Habilitar torch.compile para acelerar inferência
    paint_torch_compile: bool = False
    # Modo low-vram (ativa todas as otimizações agressivas)
    paint_low_vram_mode: bool = False


@dataclass
class Text2DProfile:
    """Opções passadas ao CLI text2d generate (subconjunto)."""

    low_vram: bool = False
    cpu: bool = False
    width: int | None = None
    height: int | None = None


@dataclass
class Text2SoundProfile:
    """Opções passadas ao CLI text2sound generate (subconjunto)."""

    duration: float | None = None
    steps: int | None = None
    cfg_scale: float | None = None
    audio_format: str = "wav"
    preset: str | None = None
    sigma_min: float | None = None
    sigma_max: float | None = None
    sampler: str | None = None
    trim: bool | None = None
    model_id: str | None = None
    # None = auto (CLI); True/False = --half / --no-half
    half_precision: bool | None = None


@dataclass
class Texture2DProfile:
    """Opções passadas ao CLI texture2d generate (HF seamless) + Materialize opcional para PBR."""

    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance_scale: float | None = None
    negative_prompt: str | None = None
    preset: str | None = None
    cfg_scale: float | None = None
    lora_strength: float | None = None
    model_id: str | None = None
    # PBR a partir do PNG difuso (CLI materialize, não text3d)
    materialize: bool = False
    materialize_bin: str | None = None
    materialize_format: str = "png"
    materialize_quality: int = 95
    materialize_verbose: bool = False
    materialize_maps_subdir: str = "pbr_maps"


@dataclass
class Animator3DProfile:
    """Opções para ``animator3d game-pack`` após Rigging3D (GLB rigado → GLB com clips)."""

    preset: str = "humanoid"


@dataclass
class Rigging3DProfile:
    """Opções para o CLI rigging3d pipeline após Text3D (GLB → GLB rigado)."""

    # hero.glb → hero_rigged.glb (sufixo antes da extensão)
    output_suffix: str = "_rigged"
    # Opcional: raiz UniRig empacotada (equivalente a RIGGING3D_ROOT)
    root: str | None = None
    # Opcional: intérprete Python com torch/bpy (equivalente a RIGGING3D_PYTHON)
    python: str | None = None


@dataclass
class Part3DProfile:
    """Opções para ``part3d decompose`` após Text3D (GLB → partes semânticas + mesh segmentada)."""

    octree_resolution: int | None = None
    steps: int | None = None
    num_chunks: int | None = None
    # True: só P3-SAM (mesh com cores por parte); não gera GLB multi-parte X-Part
    segment_only: bool = False
    no_cpu_offload: bool = False
    verbose: bool = False
    # hero.glb → hero_parts.glb e hero_segmented.glb (sufixos antes de .glb)
    parts_suffix: str = "_parts"
    segmented_suffix: str = "_segmented"
    # --- Otimizações de VRAM ---
    # Modo de quantização: auto, none, int8, int4, torchao-int8, torchao-int4
    quantization: str | None = None
    # Não quantizar o DiT mesmo quando disponível
    no_quantize_dit: bool = False
    # Habilitar torch.compile para acelerar inferência
    torch_compile: bool = False
    # Desabilitar attention slicing
    no_attention_slicing: bool = False
    # Modo low-vram (ativa todas as otimizações agressivas)
    low_vram_mode: bool = False


@dataclass
class Skymap2DProfile:
    """Opções passadas ao CLI skymap2d generate (HF equirectangular 360°)."""

    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance_scale: float | None = None
    negative_prompt: str | None = None
    preset: str | None = None
    cfg_scale: float | None = None
    lora_strength: float | None = None
    model_id: str | None = None


@dataclass
class GameProfile:
    title: str
    genre: str
    tone: str
    style_preset: str
    negative_keywords: list[str] = field(default_factory=list)
    output_dir: str = "."
    images_subdir: str = "images"
    meshes_subdir: str = "meshes"
    audio_subdir: str = "audio"
    # split: images_subdir/id.png e meshes_subdir/id.glb (comportamento clássico)
    # flat: output_dir / dirname(id) / basename.ext — PNG e GLB na mesma pasta (ex.: Godot por categoria)
    path_layout: str = "split"
    seed_base: int | None = None
    image_ext: str = "png"
    # text2d: FLUX Klein (Text2D) · texture2d: texturas seamless (Texture2D) · skymap2d: skymaps 360° (Skymap2D)
    image_source: str = "text2d"
    text2d: Text2DProfile | None = None
    texture2d: Texture2DProfile | None = None
    skymap2d: Skymap2DProfile | None = None
    text3d: Text3DProfile | None = None
    text2sound: Text2SoundProfile | None = None
    rigging3d: Rigging3DProfile | None = None
    animator3d: Animator3DProfile | None = None
    part3d: Part3DProfile | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameProfile:
        if not isinstance(data, dict):
            raise ValueError("Perfil deve ser um mapa YAML")
        required = ("title", "genre", "tone", "style_preset")
        for key in required:
            if key not in data or data[key] in (None, ""):
                raise ValueError(f"Campo obrigatório em game.yaml: {key}")
        neg = data.get("negative_keywords") or []
        if isinstance(neg, str):
            neg = [neg]
        if not isinstance(neg, list):
            raise ValueError("negative_keywords deve ser lista ou string")
        ext = str(data.get("image_ext") or "png").lower().lstrip(".")
        if ext not in ("png", "jpg", "jpeg"):
            raise ValueError("image_ext deve ser png, jpg ou jpeg")
        t2: Text2DProfile | None = None
        raw_t2 = data.get("text2d")
        if isinstance(raw_t2, dict):
            w = raw_t2.get("width")
            h = raw_t2.get("height")
            try:
                wi = int(w) if w is not None else None
                he = int(h) if h is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError("text2d.width e text2d.height devem ser inteiros") from e
            t2 = Text2DProfile(
                low_vram=bool(raw_t2.get("low_vram", False)),
                cpu=bool(raw_t2.get("cpu", False)),
                width=wi,
                height=he,
            )
        tex2: Texture2DProfile | None = None
        raw_tex2 = data.get("texture2d")
        if isinstance(raw_tex2, dict):
            w = raw_tex2.get("width")
            h = raw_tex2.get("height")
            st = raw_tex2.get("steps")
            gs = raw_tex2.get("guidance_scale")
            cfg = raw_tex2.get("cfg_scale")
            lr = raw_tex2.get("lora_strength")
            mq = raw_tex2.get("materialize_quality")
            try:
                wi_t = int(w) if w is not None else None
                he_t = int(h) if h is not None else None
                st_i = int(st) if st is not None else None
                gs_f = float(gs) if gs is not None else None
                cfg_f = float(cfg) if cfg is not None else None
                lr_f = float(lr) if lr is not None else None
                mq_i = int(mq) if mq is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError(
                    "texture2d.width, height, steps, guidance_scale, cfg_scale, "
                    "lora_strength e materialize_quality devem ser números válidos"
                ) from e
            neg_prompt = raw_tex2.get("negative_prompt")
            neg_s = str(neg_prompt).strip() if neg_prompt not in (None, "") else None
            pr = raw_tex2.get("preset")
            pr_s = str(pr).strip() if pr not in (None, "") else None
            mid = raw_tex2.get("model_id")
            mid_s = str(mid).strip() if mid not in (None, "") else None
            msd = raw_tex2.get("materialize_maps_subdir")
            msd_s = str(msd).strip() if msd not in (None, "") else "pbr_maps"
            mf = raw_tex2.get("materialize_format")
            mf_s = str(mf).strip().lower() if mf not in (None, "") else "png"
            if mf_s not in ("png", "jpg", "jpeg", "tga", "exr"):
                raise ValueError("texture2d.materialize_format deve ser png, jpg, tga ou exr")
            mat_bin = raw_tex2.get("materialize_bin")
            mat_bin_s = str(mat_bin).strip() if mat_bin not in (None, "") else None
            mq_final = mq_i if mq_i is not None else 95
            if not 0 <= mq_final <= 100:
                raise ValueError("texture2d.materialize_quality deve estar entre 0 e 100")
            tex2 = Texture2DProfile(
                width=wi_t,
                height=he_t,
                steps=st_i,
                guidance_scale=gs_f,
                negative_prompt=neg_s,
                preset=pr_s,
                cfg_scale=cfg_f,
                lora_strength=lr_f,
                model_id=mid_s,
                materialize=bool(raw_tex2.get("materialize", False)),
                materialize_bin=mat_bin_s,
                materialize_format="jpg" if mf_s == "jpeg" else mf_s,
                materialize_quality=mq_final,
                materialize_verbose=bool(raw_tex2.get("materialize_verbose", False)),
                materialize_maps_subdir=msd_s,
            )
        sky2: Skymap2DProfile | None = None
        raw_sky2 = data.get("skymap2d")
        if isinstance(raw_sky2, dict):
            w = raw_sky2.get("width")
            h = raw_sky2.get("height")
            st = raw_sky2.get("steps")
            gs = raw_sky2.get("guidance_scale")
            cfg = raw_sky2.get("cfg_scale")
            lr = raw_sky2.get("lora_strength")
            try:
                wi_s = int(w) if w is not None else None
                he_s = int(h) if h is not None else None
                st_s = int(st) if st is not None else None
                gs_s = float(gs) if gs is not None else None
                cfg_s = float(cfg) if cfg is not None else None
                lr_s = float(lr) if lr is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError(
                    "skymap2d.width, height, steps, guidance_scale, cfg_scale e lora_strength devem ser números válidos"
                ) from e
            neg_prompt_s = raw_sky2.get("negative_prompt")
            neg_ss = str(neg_prompt_s).strip() if neg_prompt_s not in (None, "") else None
            pr_s2 = raw_sky2.get("preset")
            pr_ss = str(pr_s2).strip() if pr_s2 not in (None, "") else None
            mid_s2 = raw_sky2.get("model_id")
            mid_ss = str(mid_s2).strip() if mid_s2 not in (None, "") else None
            sky2 = Skymap2DProfile(
                width=wi_s,
                height=he_s,
                steps=st_s,
                guidance_scale=gs_s,
                negative_prompt=neg_ss,
                preset=pr_ss,
                cfg_scale=cfg_s,
                lora_strength=lr_s,
                model_id=mid_ss,
            )
        ts2: Text2SoundProfile | None = None
        raw_ts2 = data.get("text2sound")
        if isinstance(raw_ts2, dict):
            dur = raw_ts2.get("duration")
            st = raw_ts2.get("steps")
            cfg = raw_ts2.get("cfg_scale")
            smin = raw_ts2.get("sigma_min")
            smax = raw_ts2.get("sigma_max")
            try:
                dur_f = float(dur) if dur is not None else None
                st_i = int(st) if st is not None else None
                cfg_f = float(cfg) if cfg is not None else None
                smin_f = float(smin) if smin is not None else None
                smax_f = float(smax) if smax is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError(
                    "text2sound.duration, steps, cfg_scale, sigma_min e sigma_max devem ser números válidos"
                ) from e
            af_raw = raw_ts2.get("audio_format") or raw_ts2.get("format")
            af = str(af_raw or "wav").lower().strip().lstrip(".")
            if af not in ("wav", "flac", "ogg"):
                raise ValueError("text2sound.audio_format deve ser wav, flac ou ogg")
            pr_a = raw_ts2.get("preset")
            pr_as = str(pr_a).strip() if pr_a not in (None, "") else None
            samp = raw_ts2.get("sampler")
            samp_s = str(samp).strip() if samp not in (None, "") else None
            mid_a = raw_ts2.get("model_id")
            mid_as = str(mid_a).strip() if mid_a not in (None, "") else None
            trim_v = raw_ts2.get("trim")
            trim_b: bool | None = None if trim_v is None else bool(trim_v)
            hp_raw = raw_ts2.get("half_precision")
            hp_b: bool | None = None if hp_raw is None else bool(hp_raw)
            ts2 = Text2SoundProfile(
                duration=dur_f,
                steps=st_i,
                cfg_scale=cfg_f,
                audio_format=af,
                preset=pr_as,
                sigma_min=smin_f,
                sigma_max=smax_f,
                sampler=samp_s,
                trim=trim_b,
                model_id=mid_as,
                half_precision=hp_b,
            )
        t3: Text3DProfile | None = None
        raw_t3 = data.get("text3d")
        if isinstance(raw_t3, dict):
            pr = raw_t3.get("preset")
            if pr is not None and pr not in ("fast", "balanced", "hq"):
                raise ValueError("text3d.preset deve ser fast, balanced ou hq")
            tx = raw_t3.get("texture")
            tx = True if tx is None else bool(tx)
            st = raw_t3.get("steps")
            oc = raw_t3.get("octree_resolution")
            nc = raw_t3.get("num_chunks")
            ms = raw_t3.get("mesh_smooth")
            mcl = raw_t3.get("mc_level")
            try:
                st_i = int(st) if st is not None else None
                oc_i = int(oc) if oc is not None else None
                nc_i = int(nc) if nc is not None else None
                ms_i = int(ms) if ms is not None else None
                mcl_f = float(mcl) if mcl is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError(
                    "text3d.steps, octree_resolution, num_chunks, mesh_smooth e mc_level devem ser números válidos"
                ) from e
            allow_sg = bool(raw_t3.get("allow_shared_gpu", False))
            gko = raw_t3.get("gpu_kill_others")
            g_kill = True if gko is None else bool(gko)
            phased = bool(raw_t3.get("phased_batch", False))
            full_gpu = bool(raw_t3.get("full_gpu", False))
            model_sub = raw_t3.get("model_subfolder")
            model_sub_s = str(model_sub).strip() if model_sub not in (None, "") else None
            # Paint3D texture options (performance tuning)
            pmv = raw_t3.get("paint_max_views")
            pvr = raw_t3.get("paint_view_resolution")
            prs = raw_t3.get("paint_render_size")
            pts = raw_t3.get("paint_texture_size")
            pbe = raw_t3.get("paint_bake_exp")
            try:
                pmv_i = int(pmv) if pmv is not None else None
                pvr_i = int(pvr) if pvr is not None else None
                prs_i = int(prs) if prs is not None else None
                pts_i = int(pts) if pts is not None else None
                pbe_i = int(pbe) if pbe is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError(
                    "text3d.paint_max_views, paint_view_resolution, paint_render_size, "
                    "paint_texture_size e paint_bake_exp devem ser inteiros"
                ) from e
            # Paint3D otimizações de VRAM
            paint_quant = raw_t3.get("paint_quantization")
            paint_quant_s = str(paint_quant).strip().lower() if paint_quant not in (None, "") else None
            valid_quant_modes = (
                "auto",
                "none",
                "fp8",
                "int8",
                "int4",
                "quanto-int8",
                "quanto-int4",
                "sdnq-int8",
                "sdnq-uint8",
                "sdnq-int4",
            )
            if paint_quant_s and paint_quant_s not in valid_quant_modes:
                raise ValueError(f"text3d.paint_quantization deve ser um de: {', '.join(valid_quant_modes)}")
            eo_raw = raw_t3.get("export_origin", "feet")
            eo = str(eo_raw).strip().lower() if eo_raw not in (None, "") else "feet"
            valid_eo = frozenset({"feet", "center", "none"})
            if eo not in valid_eo:
                raise ValueError(f"text3d.export_origin deve ser um de: {', '.join(sorted(valid_eo))}")
            paint_preserve = bool(raw_t3.get("paint_preserve_origin", True))
            t3 = Text3DProfile(
                preset=pr,
                low_vram=bool(raw_t3.get("low_vram", False)),
                texture=tx,
                export_origin=eo,
                paint_preserve_origin=paint_preserve,
                steps=st_i,
                octree_resolution=oc_i,
                num_chunks=nc_i,
                no_mesh_repair=bool(raw_t3.get("no_mesh_repair", False)),
                mesh_smooth=ms_i,
                mc_level=mcl_f,
                allow_shared_gpu=allow_sg,
                gpu_kill_others=g_kill,
                phased_batch=phased,
                full_gpu=full_gpu,
                model_subfolder=model_sub_s,
                paint_max_views=pmv_i,
                paint_view_resolution=pvr_i,
                paint_render_size=prs_i,
                paint_texture_size=pts_i,
                paint_bake_exp=pbe_i,
                paint_quantization=paint_quant_s,
                paint_tiny_vae=bool(raw_t3.get("paint_tiny_vae", False)),
                paint_torch_compile=bool(raw_t3.get("paint_torch_compile", False)),
                paint_low_vram_mode=bool(raw_t3.get("paint_low_vram_mode", False)),
            )
        rg3: Rigging3DProfile | None = None
        raw_rg = data.get("rigging3d")
        if isinstance(raw_rg, dict):
            sfx = raw_rg.get("output_suffix")
            sfx_s = str(sfx).strip() if sfx not in (None, "") else "_rigged"
            if not sfx_s.startswith("_"):
                sfx_s = f"_{sfx_s}" if sfx_s else "_rigged"
            rg_root = raw_rg.get("root")
            rg_root_s = str(rg_root).strip() if rg_root not in (None, "") else None
            rg_py = raw_rg.get("python")
            rg_py_s = str(rg_py).strip() if rg_py not in (None, "") else None
            rg3 = Rigging3DProfile(
                output_suffix=sfx_s,
                root=rg_root_s,
                python=rg_py_s,
            )
        anim3: Animator3DProfile | None = None
        raw_anim = data.get("animator3d")
        if isinstance(raw_anim, dict):
            pr_a = raw_anim.get("preset")
            pr_as = str(pr_a).strip().lower() if pr_a not in (None, "") else "humanoid"
            valid_presets = ("humanoid", "creature", "flying")
            if pr_as not in valid_presets:
                raise ValueError(f"animator3d.preset deve ser um de: {', '.join(valid_presets)}")
            anim3 = Animator3DProfile(preset=pr_as)
        p3: Part3DProfile | None = None
        raw_p3 = data.get("part3d")
        if isinstance(raw_p3, dict):
            oc = raw_p3.get("octree_resolution")
            st = raw_p3.get("steps")
            nc = raw_p3.get("num_chunks")
            try:
                oc_i = int(oc) if oc is not None else None
                st_i = int(st) if st is not None else None
                nc_i = int(nc) if nc is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError("part3d.octree_resolution, steps e num_chunks devem ser inteiros válidos") from e
            ps = raw_p3.get("parts_suffix")
            ss = raw_p3.get("segmented_suffix")
            ps_s = str(ps).strip() if ps not in (None, "") else "_parts"
            ss_s = str(ss).strip() if ss not in (None, "") else "_segmented"
            # Part3D otimizações de VRAM
            p3_quant = raw_p3.get("quantization")
            p3_quant_s = str(p3_quant).strip().lower() if p3_quant not in (None, "") else None
            valid_p3_quants = (
                "auto",
                "none",
                "int8",
                "int4",
                "torchao-int8",
                "torchao-int4",
                "sdnq-int8",
                "sdnq-uint8",
                "sdnq-int4",
            )
            if p3_quant_s and p3_quant_s not in valid_p3_quants:
                raise ValueError(f"part3d.quantization deve ser um de: {', '.join(valid_p3_quants)}")
            p3 = Part3DProfile(
                octree_resolution=oc_i,
                steps=st_i,
                num_chunks=nc_i,
                segment_only=bool(raw_p3.get("segment_only", False)),
                no_cpu_offload=bool(raw_p3.get("no_cpu_offload", False)),
                verbose=bool(raw_p3.get("verbose", False)),
                parts_suffix=ps_s,
                segmented_suffix=ss_s,
                quantization=p3_quant_s,
                no_quantize_dit=bool(raw_p3.get("no_quantize_dit", False)),
                torch_compile=bool(raw_p3.get("torch_compile", False)),
                no_attention_slicing=bool(raw_p3.get("no_attention_slicing", False)),
                low_vram_mode=bool(raw_p3.get("low_vram_mode", False)),
            )
        sb = data.get("seed_base")
        if sb is not None:
            try:
                sb = int(sb)
            except (TypeError, ValueError) as e:
                raise ValueError("seed_base deve ser um inteiro") from e
        pl = str(data.get("path_layout") or "split").strip().lower()
        if pl not in ("split", "flat"):
            raise ValueError("path_layout deve ser split ou flat")
        isrc = str(data.get("image_source") or "text2d").strip().lower()
        if isrc not in ("text2d", "texture2d", "skymap2d"):
            raise ValueError("image_source deve ser text2d, texture2d ou skymap2d")
        if isrc == "texture2d" and tex2 is None:
            tex2 = Texture2DProfile()
        if isrc == "skymap2d" and sky2 is None:
            sky2 = Skymap2DProfile()
        audio_sd = str(data.get("audio_subdir") or "audio").strip() or "audio"
        return cls(
            title=str(data["title"]),
            genre=str(data["genre"]),
            tone=str(data["tone"]),
            style_preset=str(data["style_preset"]),
            negative_keywords=[str(x) for x in neg],
            output_dir=_parse_output_dir(data.get("output_dir")),
            images_subdir=str(data.get("images_subdir") or "images"),
            meshes_subdir=str(data.get("meshes_subdir") or "meshes"),
            audio_subdir=audio_sd,
            path_layout=pl,
            seed_base=sb,
            image_ext="jpg" if ext == "jpeg" else ext,
            image_source=isrc,
            text2d=t2,
            texture2d=tex2,
            skymap2d=sky2,
            text3d=t3,
            text2sound=ts2,
            rigging3d=rg3,
            animator3d=anim3,
            part3d=p3,
        )


def load_profile(path: Path) -> GameProfile:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return GameProfile.from_dict(raw or {})
