//! Terrain chunk material — the ground blend ([`super::splat`]) as a
//! bindless [`bevy::pbr::Material`] PRÓPRIO (não `ExtendedMaterial` — ver o
//! aviso de crash abaixo) for the terrain chunks built by [`super::runtime`].
//!
//! Every chunk carries its OWN material: the eight pool textures with the
//! highest aggregate weight in that chunk + TWO RGBA8 splat planes baked per
//! chunk (plano 0 = slots 0–3, plano 1 = slots 4–7). The old single-material
//! design (13 layers + 4 global splat planes, 34 bindings) crashed the
//! NVIDIA driver's pipeline-layout path with a silent SIGSEGV; the bindless
//! chunk material stays well clear of it, with the bonus that different
//! areas of the world can render different layer sets.
//!
//! Same architecture as [`super::water_material`] / [`crate::sky`] for the
//! world constants: `viber run` rewrites the CONFIG block of
//! `shaders/terrain_chunk.wgsl` before the renderer loads it (the Bevy 0.19
//! slot-1 storage promotion never re-uploads custom material uniforms).
//!
//! Lighting é o pipeline PBR REAL da engine (2026-09-07, passe "Luz &
//! Atmosfera"): o fragment constrói um `PbrInput` à mão (sem importar
//! `pbr_fragment`, que conflituaria com a tabela bindless) e chama
//! `apply_pbr_lighting` + `main_pass_post_lighting_processing` — o terreno
//! recebe as sombras em cascata do sol, as luzes pontuais clusterizadas, a
//! luz ambiente e o SSAO, com specular pelas `roughs` por layer e o relevo
//! dos `normal.ktx2` do pool (um por layer, blend ponderado pelos pesos do
//! splat). A fog da câmara é aplicada pelo
//! `main_pass_post_lighting_processing` (define `DISTANCE_FOG` do view key).
//! O antigo `day_tint`/`sun_dir` do caminho fake continuam no uniform por
//! compatibilidade de layout mas já não são lidos. `base-color` autoral NÃO
//! é lido neste caminho (as vertex colors transportam dados de
//! parede/região, não tint).

use bevy::asset::Asset;
use bevy::math::Vec4;
use bevy::pbr::StandardMaterial;
use bevy::prelude::*;
use bevy::reflect::TypePath;
use bevy::render::render_resource::{AsBindGroup, AsBindGroupShaderType, ShaderType};
use bevy::shader::ShaderRef;

/// Template WGSL do material de chunk (defaults; `viber run` reescreve o
/// bloco CONFIG com as consts de parede antes de o renderer o carregar).
pub const TERRAIN_CHUNK_WGSL: &str = include_str!("chunk.wgsl");

const CONFIG_BEGIN: &str = "// === WORLD CONFIG";
const CONFIG_END: &str = "// === END WORLD CONFIG ===";

/// Terrain chunk material — a `Material` PRÓPRIO (não `ExtendedMaterial`).
///
/// ⚠ **TEM de ser `#[bindless]`.** Nesta stack (bevy 0.19.1 + wgpu 29.0.4 +
/// NVIDIA 595.84) um material custom NÃO-bindless com **uma única** binding
/// de textura mata o driver com SIGSEGV dentro de `vkCreatePipelineLayout`
/// (`libnvidia-gpucomp`, via `wgpu_hal::vulkan::Device::create_pipeline_layout`)
/// quando o pipeline do material é criado. Bisectado a 2026-09-05, cada passo
/// com o mesmo mundo (`worlds/qa-agua.xml`) e o material pendurado num cubo
/// para isolar a malha do terreno:
///
/// | material | resultado |
/// |----------|-----------|
/// | custom trivial, zero bindings | corre |
/// | custom, só `#[storage]` | corre |
/// | custom, `#[texture]` + `#[sampler]` | **SIGSEGV** |
/// | custom, só `#[texture]` (sem sampler) | **SIGSEGV** |
/// | custom `#[bindless]` com texturas | corre |
///
/// O `StandardMaterial` da bevy nunca sofreu disto porque JÁ É bindless — as
/// texturas entram nas binding arrays partilhadas (`bevy_render::bindless`)
/// em vez de descritores próprios no layout do material. O céu e a água
/// escaparam por não terem textura nenhuma.
///
/// Quem acrescentar um material custom com texturas a este crate tem de o
/// marcar `#[bindless]` e escrever o WGSL com o ramo `#ifdef BINDLESS` —
/// senão volta o SIGSEGV, e ele não vem com mensagem nenhuma.
#[derive(Debug, Clone, Asset, TypePath, AsBindGroup)]
#[data(0, TerrainChunkParams, binding_array(10))]
#[bindless(index_table(range(0..69)))]
pub struct TerrainChunkMaterial {
    #[texture(1)]
    #[sampler(2)]
    pub layer0: Handle<Image>,
    #[texture(3)]
    #[sampler(4)]
    pub layer1: Handle<Image>,
    #[texture(5)]
    #[sampler(6)]
    pub layer2: Handle<Image>,
    #[texture(7)]
    #[sampler(8)]
    pub layer3: Handle<Image>,
    #[texture(9)]
    #[sampler(10)]
    pub layer4: Handle<Image>,
    #[texture(11)]
    #[sampler(12)]
    pub layer5: Handle<Image>,
    #[texture(13)]
    #[sampler(14)]
    pub layer6: Handle<Image>,
    #[texture(15)]
    #[sampler(16)]
    pub layer7: Handle<Image>,
    #[texture(17)]
    #[sampler(18)]
    pub splat: Handle<Image>,
    #[texture(19)]
    #[sampler(20)]
    pub splat2: Handle<Image>,
    /// Tangent-space normal maps do pool (`normal.ktx2`), um por layer —
    /// o relevo real do chão para o PBR (T1). Slots sem normal carregam a
    /// imagem plana partilhada (`flat_normal_image`) — o WGSL devolve a
    /// normal geométrica e o blend fica intacto. A ORDEM dos campos é a da
    /// tabela de índices bindless (21–36) e tem de bater certo com
    /// `TerrainChunkBindings` no WGSL.
    #[texture(21)]
    #[sampler(22)]
    pub layer0_normal: Handle<Image>,
    #[texture(23)]
    #[sampler(24)]
    pub layer1_normal: Handle<Image>,
    #[texture(25)]
    #[sampler(26)]
    pub layer2_normal: Handle<Image>,
    #[texture(27)]
    #[sampler(28)]
    pub layer3_normal: Handle<Image>,
    #[texture(29)]
    #[sampler(30)]
    pub layer4_normal: Handle<Image>,
    #[texture(31)]
    #[sampler(32)]
    pub layer5_normal: Handle<Image>,
    #[texture(33)]
    #[sampler(34)]
    pub layer6_normal: Handle<Image>,
    #[texture(35)]
    #[sampler(36)]
    pub layer7_normal: Handle<Image>,
    /// Height maps escalares do pool (`height.ktx2`), um por layer — o
    /// height-blend entre as duas layers dominantes (a rocha sai por cima da
    /// relva na fronteira). Slots sem o ficheiro carregam a plana partilhada
    /// (`flat_height_image`, 0.5) e o blend devolve os pesos originais.
    #[texture(37)]
    #[sampler(38)]
    pub layer0_height: Handle<Image>,
    #[texture(39)]
    #[sampler(40)]
    pub layer1_height: Handle<Image>,
    #[texture(41)]
    #[sampler(42)]
    pub layer2_height: Handle<Image>,
    #[texture(43)]
    #[sampler(44)]
    pub layer3_height: Handle<Image>,
    #[texture(45)]
    #[sampler(46)]
    pub layer4_height: Handle<Image>,
    #[texture(47)]
    #[sampler(48)]
    pub layer5_height: Handle<Image>,
    #[texture(49)]
    #[sampler(50)]
    pub layer6_height: Handle<Image>,
    #[texture(51)]
    #[sampler(52)]
    pub layer7_height: Handle<Image>,
    /// AO maps escalares do pool (`ao.ktx2`), um por layer — escurece o
    /// albedo por texel (contacto entre pedras, juntas de cascalho). Slots
    /// sem o ficheiro carregam a plana partilhada (`flat_ao_image`, 1.0) =
    /// oclusão nenhuma.
    #[texture(53)]
    #[sampler(54)]
    pub layer0_ao: Handle<Image>,
    #[texture(55)]
    #[sampler(56)]
    pub layer1_ao: Handle<Image>,
    #[texture(57)]
    #[sampler(58)]
    pub layer2_ao: Handle<Image>,
    #[texture(59)]
    #[sampler(60)]
    pub layer3_ao: Handle<Image>,
    #[texture(61)]
    #[sampler(62)]
    pub layer4_ao: Handle<Image>,
    #[texture(63)]
    #[sampler(64)]
    pub layer5_ao: Handle<Image>,
    #[texture(65)]
    #[sampler(66)]
    pub layer6_ao: Handle<Image>,
    #[texture(67)]
    #[sampler(68)]
    pub layer7_ao: Handle<Image>,
    /// Tabela do chunk. Sem `#[uniform]`/`#[storage]` de campo: o
    /// `#[data(...)]` da struct manda-a para a binding array partilhada dos
    /// materiais bindless (índice 0 da tabela de índices).
    pub params: TerrainChunkParams,
}

impl AsBindGroupShaderType<TerrainChunkParams> for TerrainChunkMaterial {
    fn as_bind_group_shader_type(
        &self,
        _images: &bevy::render::render_asset::RenderAssets<bevy::render::texture::GpuImage>,
    ) -> TerrainChunkParams {
        self.params
    }
}

impl TerrainChunkMaterial {
    /// Mutable texture handle of layer `i` (0..8); the failed-slot
    /// repointing walks this.
    pub fn texture_mut(&mut self, i: usize) -> &mut Handle<Image> {
        match i {
            0 => &mut self.layer0,
            1 => &mut self.layer1,
            2 => &mut self.layer2,
            3 => &mut self.layer3,
            4 => &mut self.layer4,
            5 => &mut self.layer5,
            6 => &mut self.layer6,
            _ => &mut self.layer7,
        }
    }

    /// Mutable normal-map handle of layer `i` (0..8); the failed-normal
    /// repointing walks this.
    pub fn normal_mut(&mut self, i: usize) -> &mut Handle<Image> {
        match i {
            0 => &mut self.layer0_normal,
            1 => &mut self.layer1_normal,
            2 => &mut self.layer2_normal,
            3 => &mut self.layer3_normal,
            4 => &mut self.layer4_normal,
            5 => &mut self.layer5_normal,
            6 => &mut self.layer6_normal,
            _ => &mut self.layer7_normal,
        }
    }

    /// Mutable height-map handle of layer `i` (0..8); the failed-height
    /// repointing walks this.
    pub fn height_mut(&mut self, i: usize) -> &mut Handle<Image> {
        match i {
            0 => &mut self.layer0_height,
            1 => &mut self.layer1_height,
            2 => &mut self.layer2_height,
            3 => &mut self.layer3_height,
            4 => &mut self.layer4_height,
            5 => &mut self.layer5_height,
            6 => &mut self.layer6_height,
            _ => &mut self.layer7_height,
        }
    }

    /// Mutable AO-map handle of layer `i` (0..8); the failed-AO repointing
    /// walks this.
    pub fn ao_mut(&mut self, i: usize) -> &mut Handle<Image> {
        match i {
            0 => &mut self.layer0_ao,
            1 => &mut self.layer1_ao,
            2 => &mut self.layer2_ao,
            3 => &mut self.layer3_ao,
            4 => &mut self.layer4_ao,
            5 => &mut self.layer5_ao,
            6 => &mut self.layer6_ao,
            _ => &mut self.layer7_ao,
        }
    }
}

impl bevy::pbr::Material for TerrainChunkMaterial {
    fn fragment_shader() -> ShaderRef {
        "shaders/terrain_chunk.wgsl".into()
    }

    // Voxel wall shells carry sub-voxel thin sheets (a carved void passing a
    // few centimetres under the natural terrain) whose averaged vertices
    // yield a handful of inward-wound triangles along the sheet line; with
    // backface culling each reads as a see-through hole in the wall. Draw
    // both faces: the mis-wound slivers become rock-textured patches with
    // imperfect lighting instead of holes.
    fn specialize(
        _pipeline: &bevy::pbr::MaterialPipeline,
        descriptor: &mut bevy::render::render_resource::RenderPipelineDescriptor,
        _layout: &bevy::render::mesh::MeshVertexBufferLayoutRef,
        _key: bevy::pbr::MaterialPipelineKey<Self>,
    ) -> Result<(), bevy::render::render_resource::SpecializedMeshPipelineError> {
        descriptor.primitive.cull_mode = None;
        Ok(())
    }
}

/// Per-chunk style + placement table (uniform binding 50). `tiles[i].x` is
/// the tile size in meters of layer i, `tints/flats/roughs` its color
/// correction, flat far color and perceptual roughness; `chunk` places the
/// splat planes (xy = origin XZ, z = edge size) and names the rock layer for
/// the triplanar walls (w = 0..7, -1 = no rock layer in this chunk).
#[derive(Debug, Clone, Copy, ShaderType)]
pub struct TerrainChunkParams {
    pub tiles: [Vec4; 8],
    pub tints: [Vec4; 8],
    pub flats: [Vec4; 8],
    pub roughs: [Vec4; 8],
    pub chunk: Vec4,
    /// `rgb` = tint do HORIZONTE da atmosfera (aerial perspective r2;
    /// publicado pelo `terrain_daynight_tint` a partir do `AtmosphereState`,
    /// com `w = 1` — o default `w = 0` mantém o caminho desligado até ao
    /// primeiro publish). Era o tint dia/noite do caminho fake de luz.
    pub day_tint: Vec4,
    /// `xyz` = direção de VIAGEM da luz do sol (para onde viaja; o mesmo
    /// sol que as sombras seguem, publicado no passo quantizado do day
    /// tint), `w` = 1 quando o sistema já publicou um sol real.
    pub sun_dir: Vec4,
    /// Pele das paredes, AO VIVO (`viber.debug.ground{...}`): `x` =
    /// tri_slope (gate de declive do triplanar), `y` = tri_soft, `z` =
    /// strata_spacing, `w` = strata_strength.
    pub walls_a: Vec4,
    /// `x` = rock_darken, `y` = streaks, `z` = moss, `w` = livre.
    pub walls_b: Vec4,
}

impl TerrainChunkParams {
    /// Builds the table from eight pool slots (`super::splat` indices) plus
    /// the chunk placement, using [`SLOT_STYLES`] for the visual constants
    /// and `cfg` for the initial wall skin (`walls_a`/`walls_b` — depois
    /// `viber.debug.ground{...}` move-as ao vivo).
    pub fn from_slots(
        slots: [usize; 8],
        origin: [f32; 2],
        edge: f32,
        cfg: &TerrainChunkConfig,
    ) -> Self {
        let pick = |key: fn(&LayerStyle) -> f32| {
            let mut out = [Vec4::ZERO; 8];
            for (i, &slot) in slots.iter().enumerate() {
                out[i] = Vec4::splat(key(&SLOT_STYLES[slot]));
            }
            out
        };
        let rock = slots
            .iter()
            .position(|&slot| slot == super::splat::SLOT_MOUNTAIN_STONE)
            .map(|i| i as f32)
            .unwrap_or(-1.0);
        Self {
            tiles: pick(|s| s.tile),
            tints: slots.map(|slot| {
                let t = SLOT_STYLES[slot].tint;
                Vec4::new(t[0], t[1], t[2], 0.0)
            }),
            flats: slots.map(|slot| {
                let f = SLOT_STYLES[slot].flat;
                Vec4::new(f[0], f[1], f[2], 0.0)
            }),
            roughs: pick(|s| s.rough),
            chunk: Vec4::new(origin[0], origin[1], edge, rock),
            // w = 0: a aerial perspective só liga quando o publicador da
            // atmosfera escreve o tint do horizonte (mundos sem DayCycle
            // ficam com o albedo puro, sem mix para branco).
            day_tint: Vec4::new(1.0, 1.0, 1.0, 0.0),
            // Default até o primeiro publish do `terrain_daynight_tint` —
            // o mesmo vetor que o shader hardcoded usava antes do uniform.
            sun_dir: Vec4::new(0.35, -0.8, -0.45, 0.0),
            walls_a: Vec4::new(
                cfg.tri_slope,
                cfg.tri_soft,
                cfg.strata_spacing,
                cfg.strata_strength,
            ),
            walls_b: Vec4::new(cfg.rock_darken, cfg.streaks, cfg.moss, 0.0),
        }
    }
}

/// Visual constants of one pool slot — mirrors the tables the old
/// 13-layer shader carried as WGSL consts.
pub struct LayerStyle {
    /// Meters per texture tile (world-space UVs keep texel density constant
    /// across chunks and LODs).
    pub tile: f32,
    /// Color correction of the photographic albedo, linear space.
    pub tint: [f32; 3],
    /// Flat far color (already tinted) the detail fades into with distance.
    pub flat: [f32; 3],
    /// Perceptual roughness.
    pub rough: f32,
}

const fn style(tile: f32, tint: [f32; 3], flat: [f32; 3], rough: f32) -> LayerStyle {
    LayerStyle {
        tile,
        tint,
        flat,
        rough,
    }
}

/// Slot-ordered styles ([`super::splat::DEFAULT_LAYERS` indices). The pool
/// textures are photographic: dark and low-saturation; `tint` lifts them to
/// the authored palette (fresh grass, warm sand, white snow) in linear
/// space and `flat` is the tinted average the detail fades into — near you
/// see material, far you see COLOR, the way BOTW reads at distance.
#[rustfmt::skip]
pub const SLOT_STYLES: [LayerStyle; super::splat::LAYER_COUNT] = [
    /* grass         */ style(5.0, [2.684, 2.793, 2.124], [0.1620, 0.3050, 0.0482], 0.95),
    /* vale_grass    */ style(6.0, [1.617, 1.323, 0.876], [0.3231, 0.5647, 0.1221], 0.95),
    /* dirt          */ style(4.0, [1.429, 1.403, 1.137], [0.1441, 0.0802, 0.0452], 0.95),
    /* dirt_trail    */ style(3.0, [1.384, 1.364, 1.460], [0.2961, 0.1946, 0.0802], 0.92),
    /* forest_floor  */ style(4.5, [0.794, 1.142, 0.692], [0.0976, 0.1441, 0.0545], 0.95),
    /* gravel        */ style(3.5, [0.870, 1.010, 1.246], [0.4020, 0.3231, 0.2159], 0.90),
    /* mountain_stone*/ style(7.0, [1.356, 1.346, 1.299], [0.2623, 0.2542, 0.2307], 0.85),
    /* sand          */ style(4.0, [1.148, 1.350, 1.590], [0.6867, 0.4452, 0.2086], 0.92),
    /* desert_sand   */ style(6.0, [2.282, 1.942, 1.325], [0.6308, 0.4125, 0.1878], 0.92),
    /* snow_peak     */ style(8.0, [2.637, 2.687, 2.739], [0.8879, 0.9216, 0.9734], 0.65),
    /* swamp_mud     */ style(3.5, [0.882, 0.817, 0.864], [0.0612, 0.0762, 0.0343], 0.70),
    /* dirt_road     */ style(3.0, [1.094, 1.148, 1.102], [0.3140, 0.2384, 0.1620], 0.92),
    /* pebbles       */ style(2.5, [0.880, 0.920, 1.020], [0.2262, 0.2476, 0.2701], 0.85),
];

/// Per-world chunk shader configuration: the cliff-wall constants (triplanar
/// gate from `<Terrain cliff-angle>`, strata bands for the sedimentary look).
#[derive(Debug, Clone)]
pub struct TerrainChunkConfig {
    /// Slope (1 − |normal.y|) where the triplanar wall blend starts. Derived
    /// from the cliff trigger angle: `1 − cos(cliff-angle)`. `>= 1` disables
    /// the wall path entirely (const-folded out of the shader).
    pub tri_slope: f32,
    /// Softness of the slope → wall transition (slope units).
    pub tri_soft: f32,
    /// Vertical spacing (meters) of the strata bands on cliff walls.
    pub strata_spacing: f32,
    /// Albedo variation of the strata bands (0 = uniform wall).
    pub strata_strength: f32,
    /// Multiplier on the wall albedo: the per-slot tints brighten textures
    /// for sunlit ground (mountain_stone ×1.35); the cliff face reads rock
    /// by returning the texel darkened, whatever the world palette does.
    pub rock_darken: f32,
    /// Runoff streak strength on the wall (0 = clean rock, `<Terrain
    /// cliff-streaks>`): dark vertical water stains stretched along Y.
    pub streaks: f32,
    /// Procedural moss on wall shoulders/ledges (0 = bare rock, `<Terrain
    /// cliff-moss>`).
    pub moss: f32,
}

impl TerrainChunkConfig {
    /// Config de um `<Terrain>` com `layers` (o mesmo spec que o bootstrap
    /// usa para os splats) — o `from_world` delega aqui.
    pub fn from_terrain_spec(spec: &crate::terrain::spec::TerrainSpec) -> Self {
        // The wall gate shares the cliff trigger: a 50° wall starts at
        // slope 1 − cos(50°) ≈ 0.36. cliff-angle >= ~90° disables the path.
        let tri_slope = (1.0 - spec.cliff_angle.to_radians().cos()).clamp(0.0, 1.0);
        TerrainChunkConfig {
            tri_slope,
            tri_soft: 0.12,
            strata_spacing: 4.0,
            strata_strength: 0.25,
            rock_darken: 0.45,
            streaks: spec.cliff_streaks.clamp(0.0, 1.0),
            moss: spec.cliff_moss.clamp(0.0, 1.0),
        }
    }

    /// First `<Terrain>` with `layers` (the same spec the bootstrap bakes
    /// chunk splats for).
    pub fn from_world(entities: &[crate::recipes::EntitySpec]) -> Option<TerrainChunkConfig> {
        fn walk(specs: &[crate::recipes::EntitySpec]) -> Option<crate::terrain::spec::TerrainSpec> {
            for spec in specs {
                if let crate::recipes::EntityKind::Terrain { spec } = &spec.kind {
                    if !spec.layers.is_empty() {
                        return Some(spec.clone());
                    }
                }
                if let Some(found) = walk(&spec.children) {
                    return Some(found);
                }
            }
            None
        }
        let spec = walk(entities)?;
        Some(Self::from_terrain_spec(&spec))
    }

    /// Rewrite the CONFIG const block of the shader template (the rest of the
    /// template is untouched) — same contract as water/sky.
    pub fn render_world_shader(&self) -> String {
        let block = format!(
            "{CONFIG_BEGIN} (generated by `viber run` — edit the XML, not this block) ===\n\
const CFG_TRI_SLOPE: f32 = {};\n\
const CFG_TRI_SOFT: f32 = {};\n\
const CFG_STRATA_SPACING: f32 = {};\n\
const CFG_STRATA_STRENGTH: f32 = {};\n\
const CFG_ROCK_DARKEN: f32 = {};\n\
const CFG_STREAK: f32 = {};\n\
const CFG_MOSS: f32 = {};\n\
{CONFIG_END}",
            self.tri_slope,
            self.tri_soft,
            self.strata_spacing,
            self.strata_strength,
            self.rock_darken,
            self.streaks,
            self.moss,
        );
        let template = TERRAIN_CHUNK_WGSL;
        let Some(begin) = template.find(CONFIG_BEGIN) else {
            return template.to_string();
        };
        let Some(end_rel) = template[begin..].find(CONFIG_END) else {
            return template.to_string();
        };
        let end = begin + end_rel + CONFIG_END.len();
        let mut out = String::with_capacity(template.len() + block.len());
        out.push_str(&template[..begin]);
        out.push_str(&block);
        out.push_str(&template[end..]);
        out
    }
}

// ───────────────────────────────────────────────────────────── day/night (r5)
//
// A encosta além das serras lia-se BRANCA estourada à noite (banda branca do
// horizonte, r4): o albedo das layers é multiplicado pela LUZ da cena — e à
// noite a luz devia ser só lua (600 lux) + ambiente — mas o resultado no
// frame era ~255. O mesmo sintoma da relva (corrigido com um `day_tint` no
// `base_color` da relva): o tint segue o `<DayCycle>` via
// [`crate::worldsys::daylight_factor`] e escurece o albedo do TERRENO à
// noite, com uma toque quente na golden hour. Escrito no `base_color` de
// CADA material de chunk (o fragment devolve `base.rgb × albedo`), o mesmo
// caminho re-upload que a relva usa.

/// Albedo multiplicador do terreno para o daylight factor `day`
/// (0 = noite, 1 = dia pleno). `day = 1.0` devolve `[1, 1, 1]` bit-igual ao
/// look aprovado de dia.
/// Degraus do factor de luz em que o tint do chão é republicado. 48 passos
/// num dia de 20 min ≈ um burst a cada 25 s.
const DAY_TINT_STEPS: f32 = 48.0;

pub fn terrain_day_tint(day: f32) -> [f32; 3] {
    let day = day.clamp(0.0, 1.0);
    // Noite: silhueta azul-escura — o terreno nunca pode ler-se como dia.
    const NIGHT: [f32; 3] = [0.07, 0.09, 0.15];
    // Golden hour: mão quente leve, pico a meio da rampa.
    const GOLDEN: [f32; 3] = [1.14, 0.99, 0.82];
    let warmth = day * (1.0 - day) * 4.0;
    let mut out = [0.0f32; 3];
    for i in 0..3 {
        let neutral = NIGHT[i] + (1.0 - NIGHT[i]) * day;
        out[i] = neutral * (1.0 - warmth + GOLDEN[i] * warmth);
    }
    out
}

/// Aplica [`terrain_day_tint`] ao `base_color` dos materiais de chunk (e do
/// fallback standard), seguindo o `<DayCycle>` do mundo — e publica no
/// MESMO passo a direção do sol (`AtmosphereState.sun_dir`, convertida
/// para a direção de viagem) para a luz do terreno bater certo com as
/// sombras e a hora dourada do céu.
pub fn terrain_daynight_tint(
    clock: Option<Res<crate::worldsys::DayCycleState>>,
    atmosphere: Option<Res<crate::worldsys::AtmosphereState>>,
    chunks: Option<Res<super::runtime::TerrainChunkMaterials>>,
    mut chunk_materials: ResMut<Assets<TerrainChunkMaterial>>,
    mut standards: ResMut<Assets<StandardMaterial>>,
    mut last_step: Local<Option<f32>>,
) {
    let day = clock
        .as_deref()
        .map(|clock| {
            crate::worldsys::daylight_factor(
                clock.minute_of_day,
                clock.dawn_minute,
                clock.dusk_minute,
            )
        })
        .unwrap_or(1.0);
    let tint = terrain_day_tint(day);
    let tinted = Color::linear_rgb(tint[0], tint[1], tint[2]);
    // Direção PARA o sol → direção de VIAGEM (negar). Sem atmosphere ainda,
    // mantém o default do `from_slots` (w = 0 e o shader fica no fallback).
    let sun_travel = atmosphere
        .as_deref()
        .map(|a| -a.sun_dir.normalize_or_zero())
        .unwrap_or(Vec3::ZERO);

        let Some(chunks) = chunks else { return };
    if let Some(layers) = &chunks.layer {
        // Tocar no material marca-o Modified e re-escreve a sua entrada na
        // binding array. A 60 Hz × 4000 chunks isso é uma inundação da fila
        // do render world; o passo quantizado faz o burst acontecer ~48×
        // por dia de jogo em vez de por frame.
        let step = (day * DAY_TINT_STEPS).round() / DAY_TINT_STEPS;
        if last_step.is_none_or(|prev| (step - prev).abs() > 1e-4) {
            *last_step = Some(step);
            // r2 — AERIAL PERSPECTIVE: o campo `day_tint` passa a transportar
            // o TINT DO HORIZONTE (não o antigo multiplicador de dia/noite —
            // a luz real da r1 já trata o escurecer). O shader mistura o
            // chão distante para esta cor: as montanhas ao fundo leem-se
            // azuis/da cor do céu, não cinzentas. `w = 1` liga o caminho;
            // sem publicador (sem DayCycle/atmosphere) fica 0 = desligado.
            // O horizonte vem em valores de radiância (HDR, pico ~400) —
            // normalizado ao canal máximo para um TINT visível ≤ 1.
            let sky = atmosphere.as_deref().map(|a| a.horizon);
            let sky4 = sky.map(|h| {
                let peak = h[0].max(h[1]).max(h[2]).max(1.0);
                Vec4::new(h[0] / peak, h[1] / peak, h[2] / peak, 0.85)
            });
            let sun4 = if sun_travel != Vec3::ZERO {
                Vec4::new(sun_travel.x, sun_travel.y, sun_travel.z, 1.0)
            } else {
                Vec4::ZERO
            };
            for handle in layers.materials.values() {
                if let Some(mut material) = chunk_materials.get_mut(handle) {
                    material.params.day_tint = sky4.unwrap_or(material.params.day_tint);
                    if sun4.w > 0.0 {
                        material.params.sun_dir = sun4;
                    }
                }
            }
        }
    }
    if let Some(handle) = &chunks.standard
        && let Some(mut material) = standards.get_mut(handle)
        && material.base_color != tinted
    {
        material.base_color = tinted;
    }
}

/// Publica a chuva do `<Weather>` no canal LIVRE `walls_b.w` dos params de
/// chunk — o `chunk.wgsl` lê-o como `wet` e molha o chão (albedo escurecido
/// + roughness de poça). Sistema próprio (throttle 0.5 s) porque o passo do
/// day-tint quantiza por FASE do dia e a chuva muda dentro da fase.
pub fn terrain_rain_wetness(
    time: Res<Time>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    chunks: Option<Res<super::runtime::TerrainChunkMaterials>>,
    mut chunk_materials: ResMut<Assets<TerrainChunkMaterial>>,
    mut throttle: Local<f32>,
    mut current: Local<f32>,
) {
    *throttle -= time.delta_secs();
    if *throttle > 0.0 {
        return;
    }
    *throttle = 0.5;
    let target = weather
        .as_deref()
        .map(|w| w.rain.clamp(0.0, 1.0))
        .unwrap_or(0.0);
    // Suavização por passada (0.5 s): a chuva empapa e seca gradualmente.
    *current += (target - *current).clamp(-0.2, 0.2);
    if (*current - target).abs() < 1e-3 {
        return;
    }
    let Some(chunks) = chunks else { return };
    let Some(layers) = &chunks.layer else {
        return;
    };
    for handle in layers.materials.values() {
        if let Some(mut material) = chunk_materials.get_mut(handle) {
            material.params.walls_b.w = *current;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::physics::PhysicsSpec;
    use crate::recipes::{EntityKind, EntitySpec, TransformSpec};
    use crate::terrain::spec::TerrainSpec;

    fn spec(kind: EntityKind) -> EntitySpec {
        EntitySpec {
            name: None,
            tag: None,
            script: None,
            destructible: None,
            transform: TransformSpec::default(),
            physics: PhysicsSpec::default(),
            kind,
            children: Vec::new(),
        }
    }

    /// O shader gerado substitui o bloco CONFIG e mantém o resto intacto.
    #[test]
    fn test_render_world_shader_specializes_config() {
        let config = TerrainChunkConfig {
            tri_slope: 0.36,
            tri_soft: 0.12,
            strata_spacing: 4.0,
            strata_strength: 0.25,
            rock_darken: 0.45,
            streaks: 0.5,
            moss: 0.35,
        };
        let shader = config.render_world_shader();
        assert!(shader.contains("const CFG_TRI_SLOPE: f32 = 0.36;"));
        assert!(shader.contains("const CFG_STRATA_SPACING: f32 = 4;"));
        assert!(shader.contains("const CFG_ROCK_DARKEN: f32 = 0.45;"));
        assert!(shader.contains("const CFG_STREAK: f32 = 0.5;"));
        assert!(shader.contains("const CFG_MOSS: f32 = 0.35;"));
        assert!(!shader.contains("CFG_WORLD_SIZE"), "chunk UVs are local");
        // O corpo do shader sobrevive à substituição.
        assert!(shader.contains("fn fragment"));
        assert!(shader.contains("textureSampleGrad"));
        assert_eq!(shader.matches(CONFIG_END).count(), 1);
    }

    /// GUARDA DE CRASH — não relaxar sem reler o bloco de `TerrainChunkMaterial`.
    /// O material TEM de ser bindless e o WGSL tem de ter o ramo `BINDLESS`
    /// que lê as texturas das binding arrays partilhadas. Um material custom
    /// não-bindless com uma binding de textura segfaulta o driver NVIDIA
    /// dentro de `vkCreatePipelineLayout` — sem mensagem, só um SIGSEGV no
    /// arranque. O sintoma custou dias e este teste é o que impede a
    /// reintrodução silenciosa.
    #[test]
    fn test_chunk_material_stays_bindless() {
        let source = include_str!("layer_material.rs");
        assert!(
            source.contains("#[bindless(index_table(range(0..69)))]"),
            "TerrainChunkMaterial tem de continuar bindless (69 entradas: data + 34 pares)"
        );
        assert!(
            source.contains("#[data(0, TerrainChunkParams, binding_array(10))]"),
            "a tabela por chunk vai na binding array dos materiais bindless"
        );
        // Nenhuma binding de campo `#[uniform]`/`#[storage]` no material: o
        // `#[data]` da struct é o único caminho para a tabela.
        // A agulha é montada em duas metades de propósito: escrita inteira,
        // o literal deste teste casaria com ele próprio.
        let uniform_attr = format!("#[{}(", "uniform");
        assert!(
            !source.contains(&uniform_attr),
            "um atributo uniform de campo devolve o material ao layout não-bindless"
        );

        let wgsl = TERRAIN_CHUNK_WGSL;
        assert!(
            wgsl.contains("#ifdef BINDLESS"),
            "o WGSL precisa do ramo bindless"
        );
        assert!(
            wgsl.contains("bindless_textures_2d") && wgsl.contains("bindless_samplers_filtering"),
            "as texturas do chunk saem das binding arrays partilhadas"
        );
        assert!(
            wgsl.contains("material_and_lightmap_bind_group_slot"),
            "o slot bindless vem do `mesh` da instância"
        );
        assert!(
            wgsl.contains("var<storage> material_indices"),
            "a tabela de índices bindless está no binding 0"
        );
    }

    /// As cópias do shader de chunk em disco (`worlds/shaders/`,
    /// `assets/shaders/` — reescritas pelo `viber run` a cada arranque) não
    /// podem ficar presas a overlays de debug antigos: um T5 esquecido na
    /// cópia da raiz escrevia a splat CRUA como cor final (blocos rosa/verde
    /// dos pesos) nos mundos cujo asset root resolve para ela.
    #[test]
    fn test_shader_disk_copies_have_no_debug_overlays() {
        const DEBUG_MARKERS: [&str; 2] = [
            "out.color = vec4<f32>(s.rgb, 1.0);", // splat crua como cor final
            "T5:",
        ];
        for copy in [
            "assets/shaders/terrain_chunk.wgsl",
            "worlds/shaders/terrain_chunk.wgsl",
        ] {
            let Ok(source) = std::fs::read_to_string(copy) else {
                // A cópia ainda não existe (nunca houve um run que a
                // escrevesse) — nada a guardar.
                continue;
            };
            for marker in DEBUG_MARKERS {
                assert!(
                    !source.contains(marker),
                    "{copy} ficou presa a um overlay de debug ({marker:?}); regenera a \
                     partir de src/terrain/chunk.wgsl"
                );
            }
        }
    }

    /// O gate triplanar deriva do `cliff-angle`: 50° → slope 1−cos(50°) ≈
    /// 0.357; um cliff-angle de 90°+ desliga o caminho (slope ≥ 1).
    #[test]
    fn test_tri_slope_follows_cliff_angle() {
        let terrain = |cliff_angle: f32| {
            spec(EntityKind::Terrain {
                spec: TerrainSpec {
                    world_size: 256.0,
                    layers: vec!["grass".into()],
                    cliff_angle,
                    ..TerrainSpec::default()
                },
            })
        };
        let config = TerrainChunkConfig::from_world(&[terrain(50.0)]).expect("layers found");
        assert!((config.tri_slope - (1.0 - 50f32.to_radians().cos())).abs() < 1e-5);
        let config = TerrainChunkConfig::from_world(&[terrain(90.0)]).expect("layers found");
        assert!(config.tri_slope >= 1.0, "90° disables the wall path");
    }

    /// `from_world` lê o primeiro `<Terrain>` com camadas (incl. filhos) e
    /// devolve None sem camadas.
    #[test]
    fn test_chunk_config_from_world() {
        let terrain = |layers: Vec<String>| {
            spec(EntityKind::Terrain {
                spec: TerrainSpec {
                    world_size: 512.0,
                    layers,
                    ..TerrainSpec::default()
                },
            })
        };
        let nested = spec(EntityKind::Group).with_terrain(terrain(vec!["grass".into()]));
        let config = TerrainChunkConfig::from_world(&[spec(EntityKind::Group), nested]);
        assert!(config.expect("layers found").tri_slope > 0.0);

        let bare = TerrainChunkConfig::from_world(&[spec(EntityKind::Group)]);
        assert!(bare.is_none(), "no layers → no config");
    }

    /// A tabela de params embute os estilos dos 8 slots, o índice da layer
    /// de rocha (mountain_stone) para as paredes triplanares e a pele das
    /// paredes do config.
    #[test]
    fn test_chunk_params_pick_rock_layer() {
        use super::super::splat::{SLOT_DIRT, SLOT_GRASS, SLOT_MOUNTAIN_STONE, SLOT_SNOW_PEAK};
        let cfg = TerrainChunkConfig {
            tri_slope: 0.36,
            tri_soft: 0.12,
            strata_spacing: 4.0,
            strata_strength: 0.25,
            rock_darken: 0.45,
            streaks: 0.5,
            moss: 0.35,
        };
        let params = TerrainChunkParams::from_slots(
            [SLOT_GRASS, SLOT_MOUNTAIN_STONE, SLOT_DIRT, SLOT_SNOW_PEAK, 4, 5, 7, 12],
            [-32.0, 96.0],
            64.0,
            &cfg,
        );
        assert_eq!(params.chunk.w, 1.0, "mountain_stone é a layer 1 do chunk");
        assert_eq!(params.tiles[2].x, SLOT_STYLES[SLOT_DIRT].tile);
        assert_eq!(params.tints[3].xyz().x, SLOT_STYLES[SLOT_SNOW_PEAK].tint[0]);
        assert_eq!(params.chunk.z, 64.0);
        assert_eq!(params.walls_a.x, 0.36, "tri_slope vem do config");
        assert_eq!(params.walls_b.z, 0.35, "moss vem do config");

        let no_rock = TerrainChunkParams::from_slots(
            [SLOT_GRASS, SLOT_DIRT, SLOT_SNOW_PEAK, 4, 5, 7, 8, 12],
            [0.0, 0.0],
            64.0,
            &cfg,
        );
        assert_eq!(no_rock.chunk.w, -1.0, "sem mountain_stone → sem triplanar");
    }

    impl EntitySpec {
        fn with_terrain(mut self, terrain: EntitySpec) -> EntitySpec {
            self.children.push(terrain);
            self
        }
    }
}
