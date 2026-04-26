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
class Paint3DProfile:
    """Opções passadas ao CLI paint3d texture / texture-batch."""

    style: str = "hunyuan"
    preserve_origin: bool = True
    max_views: int | None = None
    view_resolution: int | None = None
    render_size: int | None = None
    texture_size: int | None = None
    bake_exp: int | None = None
    smooth: bool = True
    smooth_passes: int | None = None
    low_vram_mode: bool = False
    # Quick paint (solid / perlin)
    solid_color: str = "#888888"
    perlin_tint: str = "#7a7268"
    perlin_frequency: float = 4.0
    perlin_octaves: int = 4
    perlin_contrast: float = 0.55
    perlin_seed: int | None = None


@dataclass
class Text3DProfile:
    """Opções passadas ao CLI text3d generate (subconjunto)."""

    preset: str | None = None  # fast | balanced | hq
    low_vram: bool = False
    export_origin: str = "feet"
    steps: int | None = None
    octree_resolution: int | None = None
    num_chunks: int | None = None
    mc_level: float | None = None
    allow_shared_gpu: bool = False
    gpu_kill_others: bool = True
    full_gpu: bool = False
    model_subfolder: str | None = None
    guidance: float | None = None
    simplify_texture_size: int | None = None


@dataclass
class Text2DProfile:
    """Opções passadas ao CLI text2d generate (subconjunto)."""

    low_vram: bool = False
    cpu: bool = False
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance_scale: float | None = None


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
class LODProfile:
    """LOD triplet generation via ``text3d lod``."""

    lod1_ratio: float = 0.42
    lod2_ratio: float = 0.14
    min_faces_lod1: int = 500
    min_faces_lod2: int = 150
    meshfix: bool = False


@dataclass
class CollisionProfile:
    """Mesh de colisão via ``text3d collision`` (convex hull simplificado)."""

    max_faces: int = 300
    convex_hull: bool = True


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
    paint3d: Paint3DProfile | None = None
    text2sound: Text2SoundProfile | None = None
    rigging3d: Rigging3DProfile | None = None
    animator3d: Animator3DProfile | None = None
    part3d: Part3DProfile | None = None
    lod: LODProfile | None = None
    collision: CollisionProfile | None = None
    generation: str | None = None

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
            t2s = raw_t2.get("steps")
            t2g = raw_t2.get("guidance_scale")
            try:
                wi = int(w) if w is not None else None
                he = int(h) if h is not None else None
                t2s_i = int(t2s) if t2s is not None else None
                t2g_f = float(t2g) if t2g is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError("text2d.width, height, steps e guidance_scale devem ser números válidos") from e
            t2 = Text2DProfile(
                low_vram=bool(raw_t2.get("low_vram", False)),
                cpu=bool(raw_t2.get("cpu", False)),
                width=wi,
                height=he,
                steps=t2s_i,
                guidance_scale=t2g_f,
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
            st = raw_t3.get("steps")
            oc = raw_t3.get("octree_resolution")
            nc = raw_t3.get("num_chunks")
            mcl = raw_t3.get("mc_level")
            try:
                st_i = int(st) if st is not None else None
                oc_i = int(oc) if oc is not None else None
                nc_i = int(nc) if nc is not None else None
                mcl_f = float(mcl) if mcl is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError(
                    "text3d.steps, octree_resolution, num_chunks e mc_level devem ser números válidos"
                ) from e
            hy_guid = raw_t3.get("guidance")
            try:
                hy_guid_f = float(hy_guid) if hy_guid is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError("text3d.guidance deve ser um número") from e
            allow_sg = bool(raw_t3.get("allow_shared_gpu", False))
            gko = raw_t3.get("gpu_kill_others")
            g_kill = True if gko is None else bool(gko)
            full_gpu = bool(raw_t3.get("full_gpu", False))
            model_sub = raw_t3.get("model_subfolder")
            model_sub_s = str(model_sub).strip() if model_sub not in (None, "") else None
            sts = raw_t3.get("simplify_texture_size")
            try:
                sts_i = int(sts) if sts is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError("text3d.simplify_texture_size deve ser inteiro") from e
            eo_raw = raw_t3.get("export_origin", "feet")
            eo = str(eo_raw).strip().lower() if eo_raw not in (None, "") else "feet"
            valid_eo = frozenset({"feet", "center", "none"})
            if eo not in valid_eo:
                raise ValueError(f"text3d.export_origin deve ser um de: {', '.join(sorted(valid_eo))}")
            t3 = Text3DProfile(
                preset=pr,
                low_vram=bool(raw_t3.get("low_vram", False)),
                export_origin=eo,
                steps=st_i,
                octree_resolution=oc_i,
                num_chunks=nc_i,
                mc_level=mcl_f,
                allow_shared_gpu=allow_sg,
                gpu_kill_others=g_kill,
                full_gpu=full_gpu,
                model_subfolder=model_sub_s,
                guidance=hy_guid_f,
                simplify_texture_size=sts_i,
            )
        p3d: Paint3DProfile | None = None
        raw_p3d = data.get("paint3d")
        if isinstance(raw_p3d, dict):
            ps_raw = raw_p3d.get("style", "hunyuan")
            ps = str(ps_raw).strip().lower() if ps_raw not in (None, "") else "hunyuan"
            valid_ps = frozenset({"hunyuan", "solid", "perlin"})
            if ps not in valid_ps:
                raise ValueError("paint3d.style deve ser hunyuan (Paint 2.1 IA), solid ou perlin (paint3d quick)")
            pmv = raw_p3d.get("max_views")
            pvr = raw_p3d.get("view_resolution")
            prs = raw_p3d.get("render_size")
            pts = raw_p3d.get("texture_size")
            pbe = raw_p3d.get("bake_exp")
            psp = raw_p3d.get("smooth_passes")
            try:
                pmv_i = int(pmv) if pmv is not None else None
                pvr_i = int(pvr) if pvr is not None else None
                prs_i = int(prs) if prs is not None else None
                pts_i = int(pts) if pts is not None else None
                pbe_i = int(pbe) if pbe is not None else None
                psp_i = int(psp) if psp is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError(
                    "paint3d.max_views, view_resolution, render_size, "
                    "texture_size, bake_exp e smooth_passes devem ser inteiros"
                ) from e
            psc = raw_p3d.get("solid_color", "#888888")
            psc_s = str(psc).strip() if psc not in (None, "") else "#888888"
            ptint = raw_p3d.get("perlin_tint", "#7a7268")
            ptint_s = str(ptint) if ptint not in (None, "") else "#7a7268"
            try:
                pf = float(raw_p3d.get("perlin_frequency", 4.0))
                pcon = float(raw_p3d.get("perlin_contrast", 0.55))
            except (TypeError, ValueError) as e:
                raise ValueError("paint3d.perlin_frequency e perlin_contrast devem ser números") from e
            try:
                po = int(raw_p3d.get("perlin_octaves", 4))
            except (TypeError, ValueError) as e:
                raise ValueError("paint3d.perlin_octaves deve ser inteiro") from e
            pps = raw_p3d.get("perlin_seed")
            pps_i: int | None
            if pps is None or (isinstance(pps, str) and str(pps).strip() == ""):
                pps_i = None
            else:
                try:
                    pps_i = int(pps)
                except (TypeError, ValueError) as e:
                    raise ValueError("paint3d.perlin_seed deve ser inteiro ou omitido") from e
            paint_smooth_val = raw_p3d.get("smooth")
            paint_smooth = bool(paint_smooth_val) if paint_smooth_val is not None else True
            p3d = Paint3DProfile(
                style=ps,
                preserve_origin=bool(raw_p3d.get("preserve_origin", True)),
                max_views=pmv_i,
                view_resolution=pvr_i,
                render_size=prs_i,
                texture_size=pts_i,
                bake_exp=pbe_i,
                smooth=paint_smooth,
                smooth_passes=psp_i,
                low_vram_mode=bool(raw_p3d.get("low_vram_mode", False)),
                solid_color=psc_s,
                perlin_tint=ptint_s,
                perlin_frequency=pf,
                perlin_octaves=po,
                perlin_contrast=pcon,
                perlin_seed=pps_i,
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
        lod: LODProfile | None = None
        raw_lod = data.get("lod")
        if isinstance(raw_lod, dict):
            lr1 = raw_lod.get("lod1_ratio")
            lr2 = raw_lod.get("lod2_ratio")
            mf1 = raw_lod.get("min_faces_lod1")
            mf2 = raw_lod.get("min_faces_lod2")
            try:
                lr1_f = float(lr1) if lr1 is not None else 0.42
                lr2_f = float(lr2) if lr2 is not None else 0.14
                mf1_i = int(mf1) if mf1 is not None else 500
                mf2_i = int(mf2) if mf2 is not None else 150
            except (TypeError, ValueError) as e:
                raise ValueError("lod.lod1_ratio, lod2_ratio, min_faces_lod1, min_faces_lod2 devem ser números") from e
            if not 0 < lr2_f < lr1_f <= 1.0:
                raise ValueError("lod: esperado 0 < lod2_ratio < lod1_ratio <= 1.0")
            lod = LODProfile(
                lod1_ratio=lr1_f,
                lod2_ratio=lr2_f,
                min_faces_lod1=mf1_i,
                min_faces_lod2=mf2_i,
                meshfix=bool(raw_lod.get("meshfix", False)),
            )
        coll: CollisionProfile | None = None
        raw_coll = data.get("collision")
        if isinstance(raw_coll, dict):
            mf = raw_coll.get("max_faces")
            ch = raw_coll.get("convex_hull")
            try:
                mf_i = int(mf) if mf is not None else 300
            except (TypeError, ValueError) as e:
                raise ValueError("collision.max_faces deve ser um número inteiro") from e
            if mf_i < 4:
                raise ValueError("collision.max_faces deve ser ≥ 4")
            coll = CollisionProfile(
                max_faces=mf_i,
                convex_hull=bool(ch) if ch is not None else True,
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
        gen_raw = data.get("generation")
        gen_name: str | None = None
        if gen_raw is not None:
            gen_name = str(gen_raw).strip().lower()
            from .generation_profiles import VALID_GENERATION_PROFILES

            if gen_name not in VALID_GENERATION_PROFILES:
                raise ValueError(f"generation deve ser um de: {', '.join(VALID_GENERATION_PROFILES)}")
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
            paint3d=p3d,
            text2sound=ts2,
            rigging3d=rg3,
            animator3d=anim3,
            part3d=p3,
            lod=lod,
            collision=coll,
            generation=gen_name,
        )


def load_profile(path: Path) -> GameProfile:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return GameProfile.from_dict(raw or {})


def apply_generation_profile(profile: GameProfile, generation_name: str) -> GameProfile:
    """Merge generation profile defaults into *profile*, preserving explicit settings.

    Only fills ``None`` / default fields in Text2DProfile, Text3DProfile, Text2SoundProfile.
    Returns a new GameProfile — the original is not mutated.
    """
    from .generation_profiles import get_profile

    gp = get_profile(generation_name)

    t2 = profile.text2d or Text2DProfile()
    t2 = Text2DProfile(
        low_vram=t2.low_vram,
        cpu=t2.cpu,
        width=t2.width if t2.width is not None else gp.text2d_width,
        height=t2.height if t2.height is not None else gp.text2d_height,
        steps=t2.steps if t2.steps is not None else gp.text2d_steps,
        guidance_scale=t2.guidance_scale if t2.guidance_scale is not None else gp.text2d_guidance,
    )

    t3 = profile.text3d or Text3DProfile()
    t3 = Text3DProfile(
        preset=t3.preset if t3.preset is not None else gp.text3d_preset,
        low_vram=t3.low_vram,
        export_origin=t3.export_origin,
        steps=t3.steps,
        octree_resolution=t3.octree_resolution,
        num_chunks=t3.num_chunks,
        mc_level=t3.mc_level,
        allow_shared_gpu=t3.allow_shared_gpu,
        gpu_kill_others=t3.gpu_kill_others,
        full_gpu=t3.full_gpu,
        model_subfolder=t3.model_subfolder,
        guidance=t3.guidance if t3.guidance is not None else gp.text3d_guidance,
        simplify_texture_size=(
            t3.simplify_texture_size if t3.simplify_texture_size is not None else gp.simplify_texture_size
        ),
    )

    p3d = profile.paint3d or Paint3DProfile()
    p3d = Paint3DProfile(
        style=p3d.style,
        preserve_origin=p3d.preserve_origin,
        max_views=p3d.max_views if p3d.max_views is not None else gp.paint_max_views,
        view_resolution=p3d.view_resolution if p3d.view_resolution is not None else gp.paint_view_resolution,
        render_size=p3d.render_size if p3d.render_size is not None else gp.paint_render_size,
        texture_size=p3d.texture_size if p3d.texture_size is not None else gp.paint_texture_size,
        bake_exp=p3d.bake_exp if p3d.bake_exp is not None else gp.paint_bake_exp,
        smooth=p3d.smooth if p3d.smooth else gp.paint_smooth,
        smooth_passes=p3d.smooth_passes if p3d.smooth_passes is not None else gp.paint_smooth_passes,
        low_vram_mode=p3d.low_vram_mode,
        solid_color=p3d.solid_color,
        perlin_tint=p3d.perlin_tint,
        perlin_frequency=p3d.perlin_frequency,
        perlin_octaves=p3d.perlin_octaves,
        perlin_contrast=p3d.perlin_contrast,
        perlin_seed=p3d.perlin_seed,
    )

    ts2 = profile.text2sound or Text2SoundProfile()
    ts2 = Text2SoundProfile(
        duration=ts2.duration,
        steps=ts2.steps if ts2.steps is not None else gp.text2sound_steps,
        cfg_scale=ts2.cfg_scale,
        audio_format=ts2.audio_format,
        preset=ts2.preset,
        sigma_min=ts2.sigma_min,
        sigma_max=ts2.sigma_max,
        sampler=ts2.sampler,
        trim=ts2.trim,
        model_id=ts2.model_id,
        half_precision=ts2.half_precision,
    )

    import copy

    merged = copy.copy(profile)
    merged.text2d = t2
    merged.text3d = t3
    merged.paint3d = p3d
    merged.text2sound = ts2
    merged.generation = generation_name
    return merged
