//! SSR forward na água — reflexo de cena por raymarch screen-space
//! (Fase B do plano de reflexões, docs/VISUAL_ROADMAP.md P1.7).
//!
//! A água é `ExtendedMaterial` — nenhum passe de iluminação de terceiros a
//! alcança, portanto este é quem dá à água o reflexo da CENA (props, terreno
//! nas encostas, herói), sobrepondo-se ao espelho do céu que o IBL já dá.
//!
//! **Arquitetura:** um system de pós-processo no `Core3d` (padrão
//! `fullscreen_material` do bevy: `ViewTarget::post_process_write`, triângulo
//! fullscreen) ANTES do TAA — o acumulo temporal denoiza o reflexo (máscaras
//! todas suaves: cortes binários piscam com o jitter do TAA). Por pixel:
//! 1. posição do mundo reconstruída do **depth prepass** (a água está lá —
//!    o `ExtendedMaterial` não desliga prepass);
//! 2. teste de água por COTA: `|y − water_y| < band` contra as cotas dos
//!    corpos extraídas do `TerrainRuntime` (lagos = espelho plano, rios =
//!    média por corpo). Sem máscara nem passe extra — a água auto-seleciona-se;
//! 3. se é água: raio refletido pela **normal do prepass** (world-space,
//!    `n*0.5+0.5` — traz as ondas!), raymarch linear + bisseção curta
//!    (McGuire 2014, "Efficient GPU Screen-Space Ray Tracing") contra o
//!    mesmo depth, amostra da cor HDR no ponto de hit;
//! 4. composto por Fresnel (Schlick, f0 0.02) com fade de borda. Sem hit →
//!    cor original (o espelho do céu do IBL fica de fallback — degradação
//!    grata, sem double-count: o hit tem céu na cor amostrada).
//!
//! **Gates:** env `VIBER_WATER_SSR=1` — **opt-in** enquanto o passe não tiver
//! acumulação temporal própria (ver [`water_ssr_requested`]). Câmaras
//! precisam de `DepthPrepass`+`NormalPrepass` (o postfx já insere) + o
//! marcador [`WaterSsr`].
//!
//! **Limites V1:** corpos além de 8 não entram; faces verticais (cortinas de
//! cascata) não são detectadas pelo teste de cota; água reflete água nos
//! overlaps (a cor de hit inclui o que a transparent pass desenhou).

use bevy::anti_alias::taa::temporal_anti_alias;
use bevy::core_pipeline::prepass::ViewPrepassTextures;
use bevy::core_pipeline::schedule::Core3d;
use bevy::core_pipeline::{Core3dSystems, FullscreenShader};
use bevy::prelude::*;
use bevy::render::extract_component::{ExtractComponent, ExtractComponentPlugin};
use bevy::render::render_resource::{
    BindGroup, BindGroupEntries, BindGroupLayoutDescriptor, BindGroupLayoutEntry, BindingType,
    Buffer, BufferBindingType, BufferDescriptor, BufferSize, BufferUsages,
    CachedRenderPipelineId, Canonical, ColorTargetState, ColorWrites, Extent3d, FragmentState,
    Operations, PipelineCache, RenderPassColorAttachment, RenderPassDescriptor, RenderPipeline,
    RenderPipelineDescriptor, Sampler, SamplerBindingType, SamplerDescriptor, ShaderStages,
    Specializer, SpecializerKey, Texture, TextureDescriptor, TextureDimension, TextureFormat,
    TextureSampleType, TextureUsages, TextureView, TextureViewDimension, TextureViewId,
    Variants, VertexState,
};
use bevy::core_pipeline::tonemapping::tonemapping;
use bevy::render::renderer::{RenderContext, RenderDevice, RenderQueue, ViewQuery};
use bevy::render::view::{ExtractedView, ViewTarget};
use bevy::render::{Extract, Render, RenderApp, RenderStartup, RenderSystems};

use crate::terrain::runtime::TerrainRuntime;

/// Quantos corpos de água entram no teste de cota (os 8 primeiros do
/// `TerrainRuntime::water`; mundos com mais lagos do que isso perdem os
/// excedentes — o simple-rpg tem 5).
const MAX_SURFACES: usize = 8;

/// Banda (m) de tolerância do teste de cota do espelho. Ondas do shader
/// deslocam ±0.35 m no pico (`CFG_WAVE_AMP` 2.0) — 0.6 m cobre com folga
/// sem roubar chão plano à beira-água.
const BAND_M: f32 = 0.6;

/// Alcance do raymarch (m). Reflexos de cena interessantes vivem a <100 m;
/// passos × dt definem a granularidade.
const MAX_DIST_M: f32 = 140.0;
const STEPS: u32 = 24;
/// Espessura do teste de hit (m) — altura em que o raio "bate" na cena.
const THICKNESS_M: f32 = 1.2;
/// F0 de Schlick da água.
const FRESNEL_F0: f32 = 0.02;
/// Fade de borda (px) — mata os hits no clip do ecrã.
const EDGE_FADE_PX: f32 = 48.0;
/// Peso do reflexo na ÁGUA (multiplica o Fresnel; clampado a 1 no shader).
/// O Fresnel físico da água a ângulos moderados é 2-5% — correto e
/// invisível. O cheat dos jogos: ×4 para o espelho LER como espelho
/// (Cyberpunk), mantendo a dependência angular.
const STRENGTH: f32 = 4.0;
/// Força do reflexo no CHÃO MOLHADO (multiplicada pela intensidade de chuva
/// do `<Weather>`): o look "rua molhada" — poças espelhadas sem material
/// nenhum novo. Chão seco não reflete (rain 0 ⇒ efeito 0).
const GROUND_STRENGTH: f32 = 0.55;

/// O gate de runtime: **opt-in** (`VIBER_WATER_SSR=1`). Medição de 2026-09-10
/// (5 frames seguidos, herói parado): sem o passe o frame varia 1.6-2.2; com
/// ele, 8-10 — um espelho screen-space sem acumulação temporal própria DANÇA
/// com as ondas. Os tweaks (dither estável por píxel, hit com confiança
/// suave, passos geométricos, blur por rugosidade) reduzem mas não eliminam.
/// Fica OPT-IN até existir o histórico temporal do passe (reprojeção + clamp
/// de vizinhança — o próximo trabalho de verdade).
pub fn water_ssr_requested() -> bool {
    matches!(
        std::env::var("VIBER_WATER_SSR")
            .ok()
            .map(|v| v.trim().to_ascii_lowercase())
            .as_deref(),
        Some("1" | "true" | "yes" | "on")
    )
}

/// Marcador na câmara 3D que recebe o passe (postfx insere com o gate on).
#[derive(Component, ExtractComponent, Clone, Copy, Default, Debug)]
pub struct WaterSsr;

/// Template WGSL do passe — `viber run` escreve-o em `shaders/water_ssr.wgsl`
/// (contrato de conteúdo como sky/water; sem especialização por mundo, o
/// ficheiro é estático). Self-contained: zero `#import` — as matrizes e as
/// cotas chegam por uniforms próprios, o que o deixa compilável pelo harness
/// naga sem stubs.
pub const WATER_SSR_WGSL: &str = r#"
// SSR da água — raymarch screen-space sobre o depth/normal prepass.
// Documentação: src/water_ssr.rs (Fase B, docs/VISUAL_ROADMAP.md P1.7).

struct SsrView {
    inv_clip_from_view: mat4x4<f32>, // clip → view
    clip_from_view:     mat4x4<f32>, // view → clip
    world_from_view:    mat4x4<f32>, // view → world
    view_from_world:    mat4x4<f32>, // world → view
    cam_pos: vec4<f32>,              // xyz = câmara no mundo
    viewport: vec4<f32>,             // (w, h, 1/w, 1/h)
};

struct SsrParams {
    // cotas dos corpos (x = water_y) — campos explícitos: indexação dinâmica
    // de arrays em uniform crashes o compilador NV 595.84 (bissectado).
    s0: vec4<f32>,
    s1: vec4<f32>,
    s2: vec4<f32>,
    s3: vec4<f32>,
    s4: vec4<f32>,
    s5: vec4<f32>,
    s6: vec4<f32>,
    s7: vec4<f32>,
    count: f32,
    max_dist: f32,
    thickness: f32,
    f0: f32,
    steps: f32,
    edge_fade: f32,
    strength: f32,
    band: f32,
    rain: f32,
    ground: f32,
};

@group(0) @binding(0) var input_color: texture_2d<f32>;
@group(0) @binding(1) var samp: sampler;
@group(0) @binding(2) var prepass_depth: texture_depth_2d;
@group(0) @binding(3) var prepass_normal: texture_2d<f32>;
@group(0) @binding(4) var<uniform> ssr_view: SsrView;
@group(0) @binding(5) var<uniform> ssr_params: SsrParams;
// histórico do reflexo: rgb = cor acumulada, a = confiança do raio
@group(0) @binding(6) var ssr_history: texture_2d<f32>;

struct FullscreenVertexOutput {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

struct SsrOut {
    @location(0) color: vec4<f32>,
    // histórico acumulado deste frame (vai para a textura de histórico)
    @location(1) hist: vec4<f32>,
};

fn world_to_uv(p_world: vec3<f32>) -> vec2<f32> {
    // Projeção JITTERED (a MESMA do depth buffer): amostra o texel CERTO.
    // Com a matriz unjittered lia-se o texel vizinho, e um vizinho diferente
    // por frame — era a fonte da piscação (medido 2026-09-10).
    let c = ssr_view.clip_from_view * (ssr_view.view_from_world * vec4<f32>(p_world, 1.0));
    let ndc = c.xy / c.w;
    return vec2<f32>((ndc.x + 1.0) * 0.5, 0.5 - ndc.y * 0.5);
}

fn screen_to_world(uv: vec2<f32>, d: f32) -> vec3<f32> {
    // uv top-left (convenção do fullscreen_vertex do bevy) → NDC
    let ndc = vec4<f32>(uv.x * 2.0 - 1.0, 1.0 - 2.0 * uv.y, d, 1.0);
    let v = ssr_view.inv_clip_from_view * ndc;
    return (ssr_view.world_from_view * vec4<f32>(v.xyz / v.w, 1.0)).xyz;
}

fn px_of(uv: vec2<f32>) -> vec2<i32> {
    let p = vec2<i32>(floor(uv * ssr_view.viewport.xy));
    return clamp(p, vec2<i32>(0i), vec2<i32>(ssr_view.viewport.xy) - vec2<i32>(1i));
}

fn hash12(p: vec2<f32>) -> f32 {
    var p3 = fract(vec3<f32>(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

fn fres_hint(n_world: vec3<f32>, v_dir: vec3<f32>) -> f32 {
    let cos_t = clamp(dot(-v_dir, n_world), 0.0, 1.0);
    return 0.02 + 0.98 * pow(1.0 - cos_t, 5.0);
}

fn edge_fade(uv: vec2<f32>, fade_px: f32) -> f32 {
    let d = min(
        min(uv.x, 1.0 - uv.x) * ssr_view.viewport.x,
        min(uv.y, 1.0 - uv.y) * ssr_view.viewport.y,
    );
    return clamp(d / max(fade_px, 1.0), 0.0, 1.0);
}

@fragment
fn fragment(in: FullscreenVertexOutput) -> SsrOut {
    let uv = in.uv;
    let src = textureSampleLevel(input_color, samp, uv, 0.0);
    if (ssr_params.count < 0.5) { return SsrOut(src, vec4<f32>(0.0)); }

    let d0 = textureLoad(prepass_depth, px_of(uv), 0);
    if (d0 <= 0.0) { return SsrOut(src, vec4<f32>(0.0)); } // céu — pass-through
    let p_world = screen_to_world(uv, d0);

    // 1) máscara de água SUAVE — cota com borda fina (smoothstep): cortes
    //    binários piscam frame a frame com o jitter do TAA e as ondas.
    let n_count = i32(ssr_params.count);
    var water_d = 1.0e9;
    if (n_count >= 1) { water_d = min(water_d, abs(p_world.y - ssr_params.s0.x)); }
    if (n_count >= 2) { water_d = min(water_d, abs(p_world.y - ssr_params.s1.x)); }
    if (n_count >= 3) { water_d = min(water_d, abs(p_world.y - ssr_params.s2.x)); }
    if (n_count >= 4) { water_d = min(water_d, abs(p_world.y - ssr_params.s3.x)); }
    if (n_count >= 5) { water_d = min(water_d, abs(p_world.y - ssr_params.s4.x)); }
    if (n_count >= 6) { water_d = min(water_d, abs(p_world.y - ssr_params.s5.x)); }
    if (n_count >= 7) { water_d = min(water_d, abs(p_world.y - ssr_params.s6.x)); }
    if (n_count >= 8) { water_d = min(water_d, abs(p_world.y - ssr_params.s7.x)); }
    let water_m = 1.0 - smoothstep(ssr_params.band * 0.6, ssr_params.band * 1.4, water_d);

    // 2) chão molhado SUAVE — chuva × força × "para cima" (rua molhada,
    //    não paredes); sem cortes, tudo smoothstep.
    let wet = ssr_params.rain * ssr_params.ground;
    let n_world = normalize(textureLoad(prepass_normal, px_of(uv), 0).rgb * 2.0 - 1.0);
    let ground_m = wet * smoothstep(0.5, 0.8, n_world.y) * (1.0 - water_m);
    let effect = max(ssr_params.strength * water_m, ground_m);
    if (effect < 0.003) { return SsrOut(src, vec4<f32>(0.0)); }

    // 3) raio refletido; fade SUAVE perto do horizonte do reflexo — o corte
    //    duro em r_dir.y fazia o reflexo piscar nas cristas das ondas.
    let v_dir = normalize(p_world - ssr_view.cam_pos.xyz);
    let r_dir = reflect(v_dir, n_world);
    let up = smoothstep(0.0, 0.12, r_dir.y);
    if (up <= 0.0) { return SsrOut(src, vec4<f32>(0.0)); } // ray para baixo

    // 3) raymarch com passo GEOMÉTRICO + bisseção (McGuire 2014).
    //    Passos lineares de max_dist/steps (~6 m) atravessavam troncos e
    //    postes sem os ver: o reflexo ficava "mudo" perto e cheio de pop.
    //    Agora o passo COMEÇA fino (~0.5 m) e cresce ×GROW por iteração — a
    //    mesma contagem de passos gasta a precisão onde ela se vê.
    let steps = i32(ssr_params.steps);
    let max_d = ssr_params.max_dist;
    const GROW: f32 = 1.18;
    // soma geométrica: step0 * (GROW^steps − 1)/(GROW − 1) = max_d
    var step_len = max_d * (GROW - 1.0) / (pow(GROW, f32(steps)) - 1.0);
    // jitter de arranque (sub-passo): quebra o banding dos planos, TAA denoisa
    // DITHER de arranque por-píxel (hash SÓ do ecrã — estável por frame):
    // sem ele o raymarch faz alias contra o depth jittered do TAA e o
    // flicker MEDIDO sobe de ~2 para ~8 (2026-09-10); o hash do mundo
    // mudava a cada frame e piscava muito mais.
    var t_prev = step_len * hash12(uv * ssr_view.viewport.xy);
    var hit_uv = vec2<f32>(0.0);
    var hit_t = 0.0;
    var hit_conf = 0.0;
    var hit = false;
    for (var s = 0; s < steps; s++) {
        let t = t_prev + step_len;
        let q = p_world + r_dir * t;
        let uvq = world_to_uv(q);
        if (uvq.x < 0.0 || uvq.x > 1.0 || uvq.y < 0.0 || uvq.y > 1.0) { break; }
        let dq = textureLoad(prepass_depth, px_of(uvq), 0);
        if (dq > 0.0) {
            let q_scene = screen_to_world(uvq, dq);
            // penetração: >0 = o raio passou PARA BAIXO da superfície da cena
            let pen = q_scene.y - q.y;
            // CONFIANÇA SUAVE pela penetração: marginal (pen≈0) esbate em vez
            // de ligar/desligar (era o piscar nas silhuetas); fundo demais =
            // raio ATRÁS de geometria fina (tronco/muro) — reflexo falso.
            // PERMISSIVA: a maioria dos hits é RASANTE (pen pequeno) — exigir
            // penetração alta deixava a confiança ~0 e a água "não refletia
            // nada" (medido 2026-09-10). Agora qualquer hit real (>2 cm)
            // conta; só o sub-cm esbate (anti-piscar na silhueta).
            let conf = smoothstep(0.0, 0.02, pen)
                * (1.0 - smoothstep(ssr_params.thickness * 0.85, ssr_params.thickness, pen));
            if (conf > 0.0) {
                // bisseção entre t_prev (acima) e t (abaixo)
                var lo = t_prev;
                var hi = t;
                for (var b = 0; b < 5; b++) {
                    let mid = (lo + hi) * 0.5;
                    let qm = p_world + r_dir * mid;
                    let uvm = world_to_uv(qm);
                    let dm = textureLoad(prepass_depth, px_of(uvm), 0);
                    var below = false;
                    if (dm > 0.0) {
                        let wm = screen_to_world(uvm, dm);
                        below = qm.y < wm.y;
                    }
                    if (below) { hi = mid; } else { lo = mid; }
                }
                hit_uv = world_to_uv(p_world + r_dir * hi);
                hit_t = hi;
                hit_conf = conf;
                hit = true;
                break;
            }
        }
        t_prev = t;
        step_len *= GROW;
    }
    // sem hit NESTE frame: o histórico decai suavemente (o blink de
    // hit/miss vira fade — é o coração do anti-flicker).
    let hist_nh = textureSampleLevel(ssr_history, samp, uv, 0.0);
    if (!hit) {
        // SEGURA a cor (o reflexo não pode desaparecer por um miss
        // intermitente) e só a confiança decai devagar: sem hit durante
        // ~0.5 s a água deixa de refletir, mas um miss isolado não pisca.
        let decayed = vec4<f32>(hist_nh.rgb, hist_nh.a * 0.97);
        let mix_d = clamp(effect * up * fres_hint(n_world, v_dir) * decayed.a, 0.0, 1.0);
        return SsrOut(vec4<f32>(mix(src.rgb, decayed.rgb, mix_d), src.a), decayed);
    }

    // 4) Fresnel + fades + amostra do hit com DESFOCO progressivo.
    //    Um espelho perfeito em água com ondas lê-se duro/aliased; o blur de
    //    3 taps ao longo da diagonal cresce com a distância do hit (proxy de
    //    rugosidade/atenuação) e amacia o reflexo sem passe extra.
    let fade = edge_fade(hit_uv, ssr_params.edge_fade) * edge_fade(uv, ssr_params.edge_fade);
    // fade de alcance: nada de "pop" ao atingir max_dist
    let fade_dist = 1.0 - smoothstep(max_d * 0.6, max_d, hit_t);
    if (fade <= 0.0 || fade_dist <= 0.0) { return SsrOut(src, vec4<f32>(0.0)); }
    // piso de desfoco: amacia o shimmer residual do conteúdo amostrado
    // RUGOSIDADE screen-space: a variação local da normal (ondas, juntas da
    // malha) alarga o reflexo. Sem isto um espelho duro em água ondulada
    // "dança" pixel a pixel e lê-se como piscação; com isto vira brilho
    // suave — é o que os AAA fazem (SSR + GGX aproximado).
    let n_var = clamp((length(dpdx(n_world)) + length(dpdy(n_world))) * 2.0, 0.0, 1.0);
    let spread = clamp(
        sqrt(hit_t) * 0.0008 + n_var * 0.012,
        0.0006,
        0.02,
    );
    // 5 taps em cruz rodada (a água tem estrutura diagonal)
    let o1 = vec2<f32>(1.0, 0.3) * spread;
    let o2 = vec2<f32>(0.3, -1.0) * spread;
    let r_c = textureSampleLevel(input_color, samp, hit_uv, 0.0).rgb;
    let r_1 = textureSampleLevel(input_color, samp, hit_uv + o1, 0.0).rgb;
    let r_2 = textureSampleLevel(input_color, samp, hit_uv - o1, 0.0).rgb;
    let r_3 = textureSampleLevel(input_color, samp, hit_uv + o2, 0.0).rgb;
    let r_4 = textureSampleLevel(input_color, samp, hit_uv - o2, 0.0).rgb;
    let refl = (r_c * 2.0 + r_1 + r_2 + r_3 + r_4) * (1.0 / 6.0);
    // ── ACUMULAÇÃO TEMPORAL ────────────────────────────────────────────
    // O raio muda de direção a cada frame com as ondas e cada amostra é um
    // ponto duro; sem acumular, o reflexo pisca (medido: 8-10 de variação
    // por frame contra 1.6-3.5 sem o passe). Aqui o reflexo converge por
    // mistura adaptativa: diferença grande = confia no atual (sem fantasma),
    // diferença pequena = acumula (mata o piscar).
    let hist_prev = textureSampleLevel(ssr_history, samp, uv, 0.0);
    let cur = vec4<f32>(refl, hit_conf);
    let diff = length(hist_prev.rgb - cur.rgb) + abs(hist_prev.a - cur.a) * 0.5;
    let alpha = clamp(0.3 + (1.0 - exp(-diff * 3.0)) * 0.55, 0.3, 0.9);
    let acc = mix(hist_prev, cur, alpha);
    let cos_t = clamp(dot(-v_dir, n_world), 0.0, 1.0);
    let fres = ssr_params.f0 + (1.0 - ssr_params.f0) * pow(1.0 - cos_t, 5.0);
    let mix_t = clamp(effect * up * fres * fade * fade_dist * acc.a, 0.0, 1.0);
    return SsrOut(vec4<f32>(mix(src.rgb, acc.rgb, mix_t), src.a), acc);
}
"#;

// ── GPU side ────────────────────────────────────────────────────────────────

/// Uniform por view (288 B, layout manual — ver `pack_view_uniform`).
#[derive(Resource)]
struct WaterSsrPipeline {
    layout: BindGroupLayoutDescriptor,
    sampler: Sampler,
    variants: Variants<RenderPipeline, WaterSsrSpecializer>,
    params_buf: Buffer,
}

/// Chave de especialização do pipeline (formato do alvo — HDR/LDR).
#[derive(PartialEq, Eq, Hash, Clone, Copy, SpecializerKey)]
struct WaterSsrPipelineKey {
    target_format: TextureFormat,
}

struct WaterSsrSpecializer;

#[derive(Component)]
struct WaterSsrPipelineId(CachedRenderPipelineId);

/// Uma textura de histórico do reflexo (rgb = cor acumulada, a = confiança).
struct SsrHistory {
    _tex: Texture,
    view: TextureView,
}

/// Bind groups das DUAS views do ping-pong (qual é source troca a cada
/// efeito da cadeia — criamos ambos e escolhemos pelo id no render) + o
/// histórico temporal do reflexo (duas texturas alternadas por frame).
#[derive(Component)]
struct WaterSsrViewGpu {
    view_buf: Buffer,
    a: (TextureViewId, BindGroup),
    b: (TextureViewId, BindGroup),
    hist: [SsrHistory; 2],
    /// Qual das duas é LIDA neste frame (a outra é escrita).
    parity: usize,
    size: bevy::math::UVec2,
}

#[derive(Resource, Default)]
struct ExtractedSsrScene {
    surfaces: Vec<f32>,
    rain: f32,
}

impl Specializer<RenderPipeline> for WaterSsrSpecializer {
    type Key = WaterSsrPipelineKey;

    fn specialize(
        &self,
        key: Self::Key,
        descriptor: &mut RenderPipelineDescriptor,
    ) -> Result<Canonical<Self::Key>, bevy::ecs::error::BevyError> {
        let fragment = descriptor.fragment_mut()?;
        fragment.set_target(
            0,
            ColorTargetState {
                format: key.target_format,
                blend: None,
                write_mask: ColorWrites::ALL,
            },
        );
        Ok(key.into())
    }
}

/// Textura de histórico do reflexo (persistente por view; recriada no resize).
fn create_history(device: &RenderDevice, width: u32, height: u32) -> SsrHistory {
    let tex = device.create_texture(&TextureDescriptor {
        label: Some("water_ssr_history".into()),
        size: Extent3d {
            width: width.max(1),
            height: height.max(1),
            depth_or_array_layers: 1,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: TextureDimension::D2,
        format: TextureFormat::Rgba16Float,
        usage: TextureUsages::RENDER_ATTACHMENT | TextureUsages::TEXTURE_BINDING,
        view_formats: &[],
    });
    let view = tex.create_view(&Default::default());
    SsrHistory { _tex: tex, view }
}

fn init_pipeline(
    mut commands: Commands,
    render_device: Res<RenderDevice>,
    asset_server: Res<AssetServer>,
    fullscreen_shader: Res<FullscreenShader>,
) {
    let entries = [
        BindGroupLayoutEntry {
            binding: 0,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Texture {
                sample_type: TextureSampleType::Float { filterable: true },
                view_dimension: TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        },
        BindGroupLayoutEntry {
            binding: 1,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Sampler(SamplerBindingType::Filtering),
            count: None,
        },
        BindGroupLayoutEntry {
            binding: 2,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Texture {
                sample_type: TextureSampleType::Depth,
                view_dimension: TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        },
        BindGroupLayoutEntry {
            binding: 3,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Texture {
                sample_type: TextureSampleType::Float { filterable: false },
                view_dimension: TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        },
        BindGroupLayoutEntry {
            binding: 4,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Buffer {
                ty: BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: BufferSize::new(288),
            },
            count: None,
        },
        BindGroupLayoutEntry {
            binding: 5,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Buffer {
                ty: BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: BufferSize::new(176),
            },
            count: None,
        },
        BindGroupLayoutEntry {
            binding: 6,
            visibility: ShaderStages::FRAGMENT,
            ty: BindingType::Texture {
                sample_type: TextureSampleType::Float { filterable: true },
                view_dimension: TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        },
    ];
    let layout = BindGroupLayoutDescriptor::new("water_ssr_layout", &entries);
    let sampler = render_device.create_sampler(&SamplerDescriptor::default());
    let shader = asset_server.load("shaders/water_ssr.wgsl");
    let vertex_state = VertexState {
        shader: fullscreen_shader.shader(),
        shader_defs: Vec::new(),
        entry_point: Some("fullscreen_vertex_shader".into()),
        buffers: Vec::new(),
    };
    let desc = RenderPipelineDescriptor {
        label: Some("water_ssr_pipeline".into()),
        layout: vec![layout.clone()],
        vertex: vertex_state,
        fragment: Some(FragmentState {
            shader,
            // alvo 0 = pós-processo (formato especializado), alvo 1 = histórico
            targets: vec![None, Some(ColorTargetState {
                format: TextureFormat::Rgba16Float,
                blend: None,
                write_mask: ColorWrites::ALL,
            })],
            ..Default::default()
        }),
        ..Default::default()
    };
    commands.insert_resource(WaterSsrPipeline {
        layout,
        sampler,
        variants: Variants::new(WaterSsrSpecializer, desc),
        params_buf: render_device.create_buffer(&BufferDescriptor {
            label: Some("water_ssr_params".into()),
            size: 176,
            usage: BufferUsages::COPY_DST | BufferUsages::UNIFORM,
            mapped_at_creation: false,
        }),
    });
}

/// Extrai as cotas dos corpos de água (primeiras `MAX_SURFACES`) e a
/// intensidade de chuva corrente (`<Weather>`) — o driver do chão molhado.
fn extract_scene(
    runtime: Extract<Option<Res<TerrainRuntime>>>,
    weather: Extract<Option<Res<crate::worldsys::WeatherState>>>,
    mut out: ResMut<ExtractedSsrScene>,
) {
    out.surfaces.clear();
    if let Some(rt) = runtime.as_deref() {
        for body in rt.water.iter().take(MAX_SURFACES) {
            out.surfaces.push(body.water_y);
        }
    }
    out.rain = weather.as_deref().map(|w| w.rain).unwrap_or(0.0).clamp(0.0, 1.0);
}

/// Pack do uniform de view (288 B: 4 mat4 + 2 vec4 — ordem EXATA do WGSL).
/// TUDO jittered (a projeção efetiva do frame): reconstrução E projeção das
/// amostras têm de casar com o depth buffer que o TAA renderizou com jitter.
fn pack_view_uniform(
    clip_from_view: &bevy::math::Mat4,
    world_from_view: &GlobalTransform,
    viewport: bevy::math::UVec4,
) -> Vec<u8> {
    let mut data = Vec::with_capacity(288);
    let mut push_mat = |m: &bevy::math::Mat4, data: &mut Vec<u8>| {
        for f in m.to_cols_array() {
            data.extend_from_slice(&f.to_le_bytes());
        }
    };
    let view_from_world = Mat4::from(world_from_view.affine().inverse());
    push_mat(&clip_from_view.inverse(), &mut data);
    push_mat(clip_from_view, &mut data);
    push_mat(&world_from_view.to_matrix(), &mut data);
    push_mat(&view_from_world, &mut data);
    let cam = world_from_view.translation();
    for f in [cam.x, cam.y, cam.z, 0.0] {
        data.extend_from_slice(&f.to_le_bytes());
    }
    let (w, h) = (viewport.z as f32, viewport.w as f32);
    for f in [w, h, 1.0 / w.max(1.0), 1.0 / h.max(1.0)] {
        data.extend_from_slice(&f.to_le_bytes());
    }
    debug_assert_eq!(data.len(), 288);
    data
}

/// Pack do uniform de params (168 úteis, 176 B com o alinhamento final).
fn pack_params_uniform(surfaces: &[f32], rain: f32) -> Vec<u8> {
    let mut data = Vec::with_capacity(176);
    for i in 0..MAX_SURFACES {
        let y = surfaces.get(i).copied().unwrap_or(0.0);
        data.extend_from_slice(&y.to_le_bytes());
        data.extend_from_slice(&0.0f32.to_le_bytes());
        data.extend_from_slice(&0.0f32.to_le_bytes());
        data.extend_from_slice(&0.0f32.to_le_bytes());
    }
    let _ = &data;
    for f in [
        surfaces.len() as f32,
        MAX_DIST_M,
        THICKNESS_M,
        FRESNEL_F0,
        STEPS as f32,
        EDGE_FADE_PX,
        STRENGTH,
        BAND_M,
        rain,
        GROUND_STRENGTH,
    ] {
        data.extend_from_slice(&f.to_le_bytes());
    }
    // Uniforms alinham o tamanho do struct a 16 B: 128 (s0..s7) + 40 = 168 → 176.
    while data.len() % 16 != 0 {
        data.extend_from_slice(&0.0f32.to_le_bytes());
    }
    debug_assert_eq!(data.len(), 176);
    data
}

#[allow(clippy::too_many_arguments)]
fn prepare_bind_groups(
    mut commands: Commands,
    mut pipeline: Option<ResMut<WaterSsrPipeline>>,
    pipeline_cache: Res<PipelineCache>,
    render_device: Res<RenderDevice>,
    queue: Res<RenderQueue>,
    surfaces: Res<ExtractedSsrScene>,
    mut views: Query<
        (
            Entity,
            &ExtractedView,
            &ViewTarget,
            Option<&ViewPrepassTextures>,
            Option<&mut WaterSsrViewGpu>,
        ),
        With<WaterSsr>,
    >,
) {
    let Some(mut pipeline) = pipeline else { return };
    queue.write_buffer(
        &pipeline.params_buf,
        0,
        &pack_params_uniform(&surfaces.surfaces, surfaces.rain),
    );
    let bind_group_layout = pipeline_cache.get_bind_group_layout(&pipeline.layout);

    for (entity, view, target, prepass_opt, mut gpu) in &mut views {
        let Some(prepass) = prepass_opt else { continue };
        let Some(depth) = prepass.depth.as_ref() else { continue };
        let Some(normal) = prepass.normal.as_ref() else { continue };
        let key = WaterSsrPipelineKey {
            target_format: view.target_format,
        };
        let Ok(pid) = pipeline.variants.specialize(&pipeline_cache, key) else {
            continue;
        };
        let (w, h) = (view.viewport.z.max(1), view.viewport.w.max(1));
        let view_bytes =
            pack_view_uniform(&view.clip_from_view, &view.world_from_view, view.viewport);

        if let Some(g) = gpu.as_mut() {
            if g.size != bevy::math::UVec2::new(w, h) {
                g.hist = [
                    create_history(&render_device, w, h),
                    create_history(&render_device, w, h),
                ];
                g.size = bevy::math::UVec2::new(w, h);
                g.parity = 0;
            }
            queue.write_buffer(&g.view_buf, 0, &view_bytes);
            let hist_read = g.hist[g.parity].view.clone();
            let view_buf_g = g.view_buf.clone();
            let make = |texture: &TextureView| {
                (
                    texture.id(),
                    render_device.create_bind_group(
                        "water_ssr_bind_group",
                        &bind_group_layout,
                        &BindGroupEntries::sequential((
                            texture,
                            &pipeline.sampler,
                            &depth.texture.default_view,
                            &normal.texture.default_view,
                            view_buf_g.as_entire_binding(),
                            pipeline.params_buf.as_entire_binding(),
                            &hist_read,
                        )),
                    ),
                )
            };
            g.a = make(&target.main_texture_view());
            g.b = make(&target.main_texture_other_view());
            g.parity ^= 1; // este frame escreve na que NÃO foi lida
        } else {
            let view_buf = render_device.create_buffer(&BufferDescriptor {
                label: Some("water_ssr_view_uniform".into()),
                size: 288,
                usage: BufferUsages::COPY_DST | BufferUsages::UNIFORM,
                mapped_at_creation: false,
            });
            queue.write_buffer(&view_buf, 0, &view_bytes);
            let hist = [
                create_history(&render_device, w, h),
                create_history(&render_device, w, h),
            ];
            let hist_read = hist[0].view.clone();
            let make = |texture: &TextureView| {
                (
                    texture.id(),
                    render_device.create_bind_group(
                        "water_ssr_bind_group",
                        &bind_group_layout,
                        &BindGroupEntries::sequential((
                            texture,
                            &pipeline.sampler,
                            &depth.texture.default_view,
                            &normal.texture.default_view,
                            view_buf.as_entire_binding(),
                            pipeline.params_buf.as_entire_binding(),
                            &hist_read,
                        )),
                    ),
                )
            };
            let a = make(&target.main_texture_view());
            let b = make(&target.main_texture_other_view());
            commands.entity(entity).insert((
                WaterSsrPipelineId(pid),
                WaterSsrViewGpu {
                    view_buf,
                    a,
                    b,
                    hist,
                    parity: 1,
                    size: bevy::math::UVec2::new(w, h),
                },
            ));
        }
    }
}

/// O passe: triângulo fullscreen com `post_process_write` (padrão
/// `fullscreen_material` do bevy). ANTES do TAA: o raymarch lê o depth com
/// jitter temporal — composto depois do TAA, o reflexo vibra (nunca é
/// acumulado); antes, o TAA denoiza o reflexo como o resto da imagem.
fn water_ssr_pass(
    view: ViewQuery<(
        &ViewTarget,
        &WaterSsrViewGpu,
        &WaterSsrPipelineId,
    )>,
    pipeline_cache: Res<PipelineCache>,
    mut ctx: RenderContext,
) {
    let (view_target, view_gpu, pipeline_id) = view.into_inner();
    let Some(pipeline) = pipeline_cache.get_render_pipeline(pipeline_id.0) else {
        return;
    };
    let post_process = view_target.post_process_write();
    let source = post_process.source;
    let destination = post_process.destination;

    let (_, bind_group) = if view_gpu.a.0 == source.id() {
        &view_gpu.a
    } else {
        &view_gpu.b
    };

    // 2.º alvo = escrita do HISTÓRICO temporal (a textura que NÃO foi lida
    // neste frame); o pipeline declara os dois formatos.
    // `parity` já foi trocado no prepare: aponta para a que NÃO foi lida.
    let hist_write = &view_gpu.hist[view_gpu.parity].view;
    let pass_descriptor = RenderPassDescriptor {
        label: Some("water_ssr_pass".into()),
        color_attachments: &[
            Some(RenderPassColorAttachment {
                view: destination,
                depth_slice: None,
                resolve_target: None,
                ops: Operations::default(),
            }),
            Some(RenderPassColorAttachment {
                view: hist_write,
                depth_slice: None,
                resolve_target: None,
                ops: Operations::default(),
            }),
        ],
        depth_stencil_attachment: None,
        timestamp_writes: None,
        occlusion_query_set: None,
        multiview_mask: None,
    };
    {
        let mut render_pass = ctx.command_encoder().begin_render_pass(&pass_descriptor);
        render_pass.set_pipeline(pipeline);
        render_pass.set_bind_group(0, bind_group, &[]);
        render_pass.draw(0..3, 0..1);
    }
}

// ── Plugin ──────────────────────────────────────────────────────────────────

/// Liga o SSR da água (`VIBER_WATER_SSR=1`): passe de pós-processo que dá à
/// água o reflexo da cena (raymarch sobre o depth prepass). Sem o env é um
/// no-op garantido.
pub struct WaterSsrPlugin;

impl Plugin for WaterSsrPlugin {
    fn build(&self, app: &mut App) {
        if !water_ssr_requested() {
            return;
        }
        info!("viber: water SSR ativo (VIBER_WATER_SSR) — reflexo de cena na água");
        app.add_plugins(ExtractComponentPlugin::<WaterSsr>::default());
        let Some(render_app) = app.get_sub_app_mut(RenderApp) else {
            return;
        };
        render_app
            .init_resource::<ExtractedSsrScene>()
            .add_systems(RenderStartup, init_pipeline)
            .add_systems(ExtractSchedule, extract_scene)
            .add_systems(
                Render,
                prepare_bind_groups.in_set(RenderSystems::PrepareBindGroups),
            )
            // DEPOIS do TAA — OBRIGATÓRIO nesta stack: antes dele o
            // ping-pong do ViewTarget troca os buffers contra as histories do
            // TAA e a imagem 3D vira lixo (medido 2026-09-10: frames
            // alternando gradientes cinza/castanho). Anti-flicker aqui é
            // feito DENTRO do passe: hash de jitter estável por píxel + hit
            // com confiança suave (sem cortes binários).
            .add_systems(
                Core3d,
                water_ssr_pass
                    .in_set(Core3dSystems::PostProcess)
                    .after(temporal_anti_alias)
                    .before(tonemapping),
            );
    }
}

// ── Testes ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// O parser do gate: valor None/inválido → off; "1"/"true"/"on" → on.
    #[test]
    fn test_requested_parse() {
        let parse = |v: Option<String>| -> bool {
            matches!(
                v.as_deref().map(str::trim).map(str::to_ascii_lowercase).as_deref(),
                Some("1" | "true" | "yes" | "on")
            )
        };
        assert!(!parse(None));
        assert!(parse(Some("1".into())));
        assert!(parse(Some(" ON ".into())));
        assert!(!parse(Some("0".into())));
        assert!(!parse(Some("no".into())));
    }

    #[test]
    fn test_view_uniform_packing_layout() {
        // 288 B = 4 mat4 (64) + vec4 cam (16) + vec4 viewport (16) — o pacto
        // com o WGSL é posicional; o tamanho é a metade que o naga valida.
        let packed = pack_view_uniform(
            &Mat4::IDENTITY,
            &GlobalTransform::from_translation(Vec3::ONE),
            bevy::math::UVec4::new(0, 0, 1280, 720),
        );
        assert_eq!(packed.len(), 288);
        // cam_pos (após 4 mat4 = 256): xyz da tradução + w=0
        let cam: Vec<f32> = packed[256..272]
            .chunks_exact(4)
            .map(|c| f32::from_le_bytes(c.try_into().unwrap()))
            .collect();
        assert_eq!(cam, [1.0, 1.0, 1.0, 0.0]);
        // viewport (último vec4, 272..288)
        let tail: Vec<f32> = packed[272..288]
            .chunks_exact(4)
            .map(|c| f32::from_le_bytes(c.try_into().unwrap()))
            .collect();
        assert_eq!(tail, [1280.0, 720.0, 1.0 / 1280.0, 1.0 / 720.0]);
    }

    #[test]
    fn test_params_uniform_packing() {
        let packed = pack_params_uniform(&[10.0, 20.0], 0.75);
        // 168 úteis alinhados a 16 → 176
        assert_eq!(packed.len(), 176);
        // primeiro vec4 = surface[0].x = 10.0
        let first = f32::from_le_bytes(packed[0..4].try_into().unwrap());
        assert_eq!(first, 10.0);
        // count em 128
        let count = f32::from_le_bytes(packed[128..132].try_into().unwrap());
        assert_eq!(count, 2.0);
        // rain em 160, ground em 164
        let rain = f32::from_le_bytes(packed[160..164].try_into().unwrap());
        assert_eq!(rain, 0.75);
        let ground = f32::from_le_bytes(packed[164..168].try_into().unwrap());
        assert_eq!(ground, GROUND_STRENGTH);
    }

    /// O WGSL não pode ter imports (o harness naga valida-o cru) nem overlays.
    #[test]
    fn test_shader_template_contract() {
        assert!(!WATER_SSR_WGSL.contains("#import"), "self-contained: sem #import");
        assert!(WATER_SSR_WGSL.contains("fn fragment"));
        assert!(WATER_SSR_WGSL.contains("prepass_depth"));
        for marker in ["DEBUG SPLIT", "DEBUG OVERLAY"] {
            assert!(!WATER_SSR_WGSL.contains(marker), "overlay {marker} esquecido");
        }
    }
}
