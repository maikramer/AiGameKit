//! Terrain chunk material — the ground blend ([`super::splat`]) as a
//! bindless [`bevy::pbr::Material`] PRÓPRIO (não `ExtendedMaterial` — ver o
//! aviso de crash abaixo) for the terrain chunks built by [`super::runtime`].
//!
//! UM só material para o terreno inteiro (2026-10-16): as 13 camadas do pool
//! (albedo, normal, height, AO, roughness) + os 4 planos do splat GLOBAL
//! ([`super::splat::generate_splats`] — 13 pesos por texel, ~2 m). O fragment
//! lê os 13 pesos, fica com os 4 mais fortes por PIXEL e amostra só esses.
//! Substituiu o material por chunk (3969 materiais, top-8 por chunk, ~50 s
//! de bake no arranque, costuras onde dois chunks elegiam paletas
//! diferentes). O desenho antigo de material único (2026-09) crashava o
//! driver NVIDIA por ser NÃO-bindless; este é bindless (ver abaixo).
//!
//! Same architecture as [`super::water_material`] / [`crate::sky`] for the
//! world constants: `viber run` rewrites the CONFIG block of
//! `shaders/terrain_chunk.wesl` before the renderer loads it (the Bevy 0.19
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
pub const TERRAIN_CHUNK_WGSL: &str = include_str!("chunk.wesl");

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
// `limit(128)` — DOIS tetos, e o menor é o que mata:
// 1. **O slab tem de caber TODOS os descritores do material.** Cada par
//    `#[texture]`/`#[sampler]` contribui uma entrada para as binding arrays
//    do slab, e o LAYOUT declara essas arrays com este count. Este material
//    tem 69 pares (13 camadas × 5 mapas + 4 splats) ≈ 58 descritores únicos
//    depois do dedup de handles repetidos. Com `limit(8)` o layout declarava
//    arrays de 8, o wgpu recusava o `create_bind_group` ("Binding count
//    declared with at most 8 items, but 58 items were provided") e a app
//    SAÍA no arranque (Validation RenderError — crash 2026-10-16).
// 2. **`samplers × limit` tem de caber no cap do device.** `bindless_supported`
//    exige `max_binding_array_sampler_elements_per_shader_stage ≥
//    sampler_bindings × limit`; com o Auto (2048) o produto (69 × 2048 =
//    141 312) passava o cap observado (~131 072) e o material caía SEM
//    aviso para o layout não-bindless — terreno cinzento-azulado (e o
//    SIGSEGV NVIDIA do aviso abaixo à espreita). `128` dá 69 × 128 = 8 832
//    elementos, 14× abaixo do cap, e 128 ≥ 69 descritores com folga.
// Há UM material no mundo inteiro: uma slab chega. Guarda de regressão:
// `test_bindless_slab_limit_holds_the_whole_material` (abaixo).
#[bindless(limit(128), index_table(range(0..139)))]
pub struct TerrainChunkMaterial {
    // Tabela de índices bindless (a ORDEM é a do WGSL, que a lê como
    // `array<u32>`): albedo 1–26, normal 27–52, height 53–78, AO 79–104,
    // rough 105–130 (camada `i` em `base + 2i`, sampler em `+1`), planos
    // splat 131–138.
    #[texture(1)]
    #[sampler(2)]
    pub albedo0: Handle<Image>,
    #[texture(3)]
    #[sampler(4)]
    pub albedo1: Handle<Image>,
    #[texture(5)]
    #[sampler(6)]
    pub albedo2: Handle<Image>,
    #[texture(7)]
    #[sampler(8)]
    pub albedo3: Handle<Image>,
    #[texture(9)]
    #[sampler(10)]
    pub albedo4: Handle<Image>,
    #[texture(11)]
    #[sampler(12)]
    pub albedo5: Handle<Image>,
    #[texture(13)]
    #[sampler(14)]
    pub albedo6: Handle<Image>,
    #[texture(15)]
    #[sampler(16)]
    pub albedo7: Handle<Image>,
    #[texture(17)]
    #[sampler(18)]
    pub albedo8: Handle<Image>,
    #[texture(19)]
    #[sampler(20)]
    pub albedo9: Handle<Image>,
    #[texture(21)]
    #[sampler(22)]
    pub albedo10: Handle<Image>,
    #[texture(23)]
    #[sampler(24)]
    pub albedo11: Handle<Image>,
    #[texture(25)]
    #[sampler(26)]
    pub albedo12: Handle<Image>,
    #[texture(27)]
    #[sampler(28)]
    pub normal0: Handle<Image>,
    #[texture(29)]
    #[sampler(30)]
    pub normal1: Handle<Image>,
    #[texture(31)]
    #[sampler(32)]
    pub normal2: Handle<Image>,
    #[texture(33)]
    #[sampler(34)]
    pub normal3: Handle<Image>,
    #[texture(35)]
    #[sampler(36)]
    pub normal4: Handle<Image>,
    #[texture(37)]
    #[sampler(38)]
    pub normal5: Handle<Image>,
    #[texture(39)]
    #[sampler(40)]
    pub normal6: Handle<Image>,
    #[texture(41)]
    #[sampler(42)]
    pub normal7: Handle<Image>,
    #[texture(43)]
    #[sampler(44)]
    pub normal8: Handle<Image>,
    #[texture(45)]
    #[sampler(46)]
    pub normal9: Handle<Image>,
    #[texture(47)]
    #[sampler(48)]
    pub normal10: Handle<Image>,
    #[texture(49)]
    #[sampler(50)]
    pub normal11: Handle<Image>,
    #[texture(51)]
    #[sampler(52)]
    pub normal12: Handle<Image>,
    #[texture(53)]
    #[sampler(54)]
    pub height0: Handle<Image>,
    #[texture(55)]
    #[sampler(56)]
    pub height1: Handle<Image>,
    #[texture(57)]
    #[sampler(58)]
    pub height2: Handle<Image>,
    #[texture(59)]
    #[sampler(60)]
    pub height3: Handle<Image>,
    #[texture(61)]
    #[sampler(62)]
    pub height4: Handle<Image>,
    #[texture(63)]
    #[sampler(64)]
    pub height5: Handle<Image>,
    #[texture(65)]
    #[sampler(66)]
    pub height6: Handle<Image>,
    #[texture(67)]
    #[sampler(68)]
    pub height7: Handle<Image>,
    #[texture(69)]
    #[sampler(70)]
    pub height8: Handle<Image>,
    #[texture(71)]
    #[sampler(72)]
    pub height9: Handle<Image>,
    #[texture(73)]
    #[sampler(74)]
    pub height10: Handle<Image>,
    #[texture(75)]
    #[sampler(76)]
    pub height11: Handle<Image>,
    #[texture(77)]
    #[sampler(78)]
    pub height12: Handle<Image>,
    #[texture(79)]
    #[sampler(80)]
    pub ao0: Handle<Image>,
    #[texture(81)]
    #[sampler(82)]
    pub ao1: Handle<Image>,
    #[texture(83)]
    #[sampler(84)]
    pub ao2: Handle<Image>,
    #[texture(85)]
    #[sampler(86)]
    pub ao3: Handle<Image>,
    #[texture(87)]
    #[sampler(88)]
    pub ao4: Handle<Image>,
    #[texture(89)]
    #[sampler(90)]
    pub ao5: Handle<Image>,
    #[texture(91)]
    #[sampler(92)]
    pub ao6: Handle<Image>,
    #[texture(93)]
    #[sampler(94)]
    pub ao7: Handle<Image>,
    #[texture(95)]
    #[sampler(96)]
    pub ao8: Handle<Image>,
    #[texture(97)]
    #[sampler(98)]
    pub ao9: Handle<Image>,
    #[texture(99)]
    #[sampler(100)]
    pub ao10: Handle<Image>,
    #[texture(101)]
    #[sampler(102)]
    pub ao11: Handle<Image>,
    #[texture(103)]
    #[sampler(104)]
    pub ao12: Handle<Image>,
    #[texture(105)]
    #[sampler(106)]
    pub rough0: Handle<Image>,
    #[texture(107)]
    #[sampler(108)]
    pub rough1: Handle<Image>,
    #[texture(109)]
    #[sampler(110)]
    pub rough2: Handle<Image>,
    #[texture(111)]
    #[sampler(112)]
    pub rough3: Handle<Image>,
    #[texture(113)]
    #[sampler(114)]
    pub rough4: Handle<Image>,
    #[texture(115)]
    #[sampler(116)]
    pub rough5: Handle<Image>,
    #[texture(117)]
    #[sampler(118)]
    pub rough6: Handle<Image>,
    #[texture(119)]
    #[sampler(120)]
    pub rough7: Handle<Image>,
    #[texture(121)]
    #[sampler(122)]
    pub rough8: Handle<Image>,
    #[texture(123)]
    #[sampler(124)]
    pub rough9: Handle<Image>,
    #[texture(125)]
    #[sampler(126)]
    pub rough10: Handle<Image>,
    #[texture(127)]
    #[sampler(128)]
    pub rough11: Handle<Image>,
    #[texture(129)]
    #[sampler(130)]
    pub rough12: Handle<Image>,
    #[texture(131)]
    #[sampler(132)]
    pub splat0: Handle<Image>,
    #[texture(133)]
    #[sampler(134)]
    pub splat1: Handle<Image>,
    #[texture(135)]
    #[sampler(136)]
    pub splat2: Handle<Image>,
    #[texture(137)]
    #[sampler(138)]
    pub splat3: Handle<Image>,
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
    /// Albedo do slot `i` do pool (0..13) — o repoint de falhas percorre isto.
    pub fn texture_mut(&mut self, i: usize) -> &mut Handle<Image> {
        match i {
            0 => &mut self.albedo0,
            1 => &mut self.albedo1,
            2 => &mut self.albedo2,
            3 => &mut self.albedo3,
            4 => &mut self.albedo4,
            5 => &mut self.albedo5,
            6 => &mut self.albedo6,
            7 => &mut self.albedo7,
            8 => &mut self.albedo8,
            9 => &mut self.albedo9,
            10 => &mut self.albedo10,
            11 => &mut self.albedo11,
            _ => &mut self.albedo12,
        }
    }

    /// Normal map do slot `i` do pool (0..13) — o repoint de falhas percorre isto.
    pub fn normal_mut(&mut self, i: usize) -> &mut Handle<Image> {
        match i {
            0 => &mut self.normal0,
            1 => &mut self.normal1,
            2 => &mut self.normal2,
            3 => &mut self.normal3,
            4 => &mut self.normal4,
            5 => &mut self.normal5,
            6 => &mut self.normal6,
            7 => &mut self.normal7,
            8 => &mut self.normal8,
            9 => &mut self.normal9,
            10 => &mut self.normal10,
            11 => &mut self.normal11,
            _ => &mut self.normal12,
        }
    }

    /// Height map do slot `i` do pool (0..13) — o repoint de falhas percorre isto.
    pub fn height_mut(&mut self, i: usize) -> &mut Handle<Image> {
        match i {
            0 => &mut self.height0,
            1 => &mut self.height1,
            2 => &mut self.height2,
            3 => &mut self.height3,
            4 => &mut self.height4,
            5 => &mut self.height5,
            6 => &mut self.height6,
            7 => &mut self.height7,
            8 => &mut self.height8,
            9 => &mut self.height9,
            10 => &mut self.height10,
            11 => &mut self.height11,
            _ => &mut self.height12,
        }
    }

    /// AO map do slot `i` do pool (0..13) — o repoint de falhas percorre isto.
    pub fn ao_mut(&mut self, i: usize) -> &mut Handle<Image> {
        match i {
            0 => &mut self.ao0,
            1 => &mut self.ao1,
            2 => &mut self.ao2,
            3 => &mut self.ao3,
            4 => &mut self.ao4,
            5 => &mut self.ao5,
            6 => &mut self.ao6,
            7 => &mut self.ao7,
            8 => &mut self.ao8,
            9 => &mut self.ao9,
            10 => &mut self.ao10,
            11 => &mut self.ao11,
            _ => &mut self.ao12,
        }
    }

    /// Roughness map do slot `i` do pool (0..13) — o repoint de falhas percorre isto.
    pub fn rough_mut(&mut self, i: usize) -> &mut Handle<Image> {
        match i {
            0 => &mut self.rough0,
            1 => &mut self.rough1,
            2 => &mut self.rough2,
            3 => &mut self.rough3,
            4 => &mut self.rough4,
            5 => &mut self.rough5,
            6 => &mut self.rough6,
            7 => &mut self.rough7,
            8 => &mut self.rough8,
            9 => &mut self.rough9,
            10 => &mut self.rough10,
            11 => &mut self.rough11,
            _ => &mut self.rough12,
        }
    }

    /// Plano splat `p` (0..4).
    pub fn splat_mut(&mut self, p: usize) -> &mut Handle<Image> {
        match p {
            0 => &mut self.splat0,
            1 => &mut self.splat1,
            2 => &mut self.splat2,
            _ => &mut self.splat3,
        }
    }
}

impl bevy::pbr::Material for TerrainChunkMaterial {
    fn fragment_shader() -> ShaderRef {
        "shaders/terrain_chunk.wesl".into()
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

/// Tabela de estilo do terreno (storage, binding 10). Indexada pelo SLOT DO
/// POOL (0..13, [`super::splat::DEFAULT_LAYERS`]): `tiles[i].x` = metros por
/// tile, `tints/flats/roughs` = correção de cor, cor plana ao longe e
/// rugosidade (`roughs[i].y` = mix do MAPA, `.z` = inverter — smoothness).
/// `world` coloca o splat global: xy = canto (-X,-Z), z = lado em metros,
/// w = slot da rocha das paredes.
#[derive(Debug, Clone, Copy, ShaderType)]
pub struct TerrainChunkParams {
    pub tiles: [Vec4; super::splat::LAYER_COUNT],
    pub tints: [Vec4; super::splat::LAYER_COUNT],
    pub flats: [Vec4; super::splat::LAYER_COUNT],
    pub roughs: [Vec4; super::splat::LAYER_COUNT],
    pub world: Vec4,
    /// `rgb` = tint do HORIZONTE da atmosfera (aerial perspective;
    /// publicado pelo `terrain_daynight_tint`, `w = 1` liga).
    pub day_tint: Vec4,
    /// `xyz` = direção de viagem do sol, `w` = 1 quando publicada.
    pub sun_dir: Vec4,
    /// Pele das paredes, AO VIVO (`viber.debug.ground{...}`): `x` =
    /// tri_slope, `y` = tri_soft, `z` = strata_spacing, `w` = strata_strength.
    pub walls_a: Vec4,
    /// `x` = rock_darken, `y` = streaks, `z` = moss, `w` = chuva (0..1).
    pub walls_b: Vec4,
}

/// Fonte do mapa de roughness de um slot do pool (inventário ESTÁTICO — o
/// pool publica `roughness.ktx2` nos PBR de material granular e
/// `smoothness.ktx2` nos de superfície; alguns aliases não têm nenhum).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RoughMap {
    /// Sem mapa: o WGSL usa a constante (`roughs[i].y = 0`).
    None,
    /// `roughness.ktx2` — o valor do texel É a roughness.
    Rough,
    /// `smoothness.ktx2` — o valor do texel é a SMOOTHNESS; o WGSL inverte
    /// (`1 − s`) via `roughs[i].z = 1`.
    Smooth,
}

/// Quanto do rough final vem do mapa (0 = só a constante). Mapas
/// fotográficos têm contraste exagerado para o look estilizado — o mix
/// mantém parte da constante afinada à mão.
pub const ROUGH_MAP_MIX: f32 = 0.85;

impl TerrainChunkParams {
    /// A tabela das 13 camadas a partir de [`SLOT_STYLES`], o splat global
    /// (`origin` = canto -X/-Z, `size` = lado em m) e a pele inicial das
    /// paredes (`cfg` — depois `viber.debug.ground{...}` move-a ao vivo).
    pub fn new(origin: [f32; 2], size: f32, cfg: &TerrainChunkConfig) -> Self {
        let styles = &SLOT_STYLES;
        Self {
            tiles: core::array::from_fn(|i| Vec4::splat(styles[i].tile)),
            tints: core::array::from_fn(|i| {
                let t = styles[i].tint;
                Vec4::new(t[0], t[1], t[2], 0.0)
            }),
            flats: core::array::from_fn(|i| {
                let f = styles[i].flat;
                Vec4::new(f[0], f[1], f[2], 0.0)
            }),
            roughs: core::array::from_fn(|i| {
                let s = &styles[i];
                let (mix, invert) = match s.rough_map {
                    RoughMap::Rough => (ROUGH_MAP_MIX, 0.0),
                    RoughMap::Smooth => (ROUGH_MAP_MIX, 1.0),
                    RoughMap::None => (0.0, 0.0),
                };
                Vec4::new(s.rough, mix, invert, 0.0)
            }),
            world: Vec4::new(
                origin[0],
                origin[1],
                size,
                super::splat::SLOT_MOUNTAIN_STONE as f32,
            ),
            // w = 0: a aerial perspective só liga quando a atmosfera publica.
            day_tint: Vec4::new(1.0, 1.0, 1.0, 0.0),
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
    /// Perceptual roughness (the CONSTANT; the map, quando existe, mistura
    /// por cima com [`ROUGH_MAP_MIX`]).
    pub rough: f32,
    /// Fonte do mapa de roughness deste alias no pool.
    pub rough_map: RoughMap,
}

const fn style(
    tile: f32,
    tint: [f32; 3],
    flat: [f32; 3],
    rough: f32,
    rough_map: RoughMap,
) -> LayerStyle {
    LayerStyle {
        tile,
        tint,
        flat,
        rough,
        rough_map,
    }
}

/// Slot-ordered styles ([`super::splat::DEFAULT_LAYERS` indices). The pool
/// textures are photographic: dark and low-saturation; `tint` lifts them to
/// the authored palette (fresh grass, warm sand, white snow) in linear
/// space and `flat` is the tinted average the detail fades into — near you
/// see material, far you see COLOR, the way BOTW reads at distance.
#[rustfmt::skip]
pub const SLOT_STYLES: [LayerStyle; super::splat::LAYER_COUNT] = [
    /* grass         */ style(5.0, [2.684, 2.793, 2.124], [0.1620, 0.3050, 0.0482], 0.95, RoughMap::Rough),
    /* vale_grass    */ style(6.0, [1.617, 1.323, 0.876], [0.3231, 0.5647, 0.1221], 0.95, RoughMap::Smooth),
    /* dirt          */ style(4.0, [1.429, 1.403, 1.137], [0.1441, 0.0802, 0.0452], 0.95, RoughMap::Rough),
    /* dirt_trail    */ style(3.0, [1.384, 1.364, 1.460], [0.2961, 0.1946, 0.0802], 0.92, RoughMap::None),
    /* forest_floor  */ style(4.5, [0.794, 1.142, 0.692], [0.0976, 0.1441, 0.0545], 0.95, RoughMap::Smooth),
    /* gravel        */ style(3.5, [0.870, 1.010, 1.246], [0.4020, 0.3231, 0.2159], 0.90, RoughMap::Rough),
    /* mountain_stone*/ style(7.0, [1.356, 1.346, 1.299], [0.2623, 0.2542, 0.2307], 0.85, RoughMap::Smooth),
    /* sand          */ style(4.0, [1.148, 1.350, 1.590], [0.6867, 0.4452, 0.2086], 0.92, RoughMap::Rough),
    /* desert_sand   */ style(6.0, [2.282, 1.942, 1.325], [0.6308, 0.4125, 0.1878], 0.92, RoughMap::Smooth),
    /* snow_peak     */ style(8.0, [2.637, 2.687, 2.739], [0.8879, 0.9216, 0.9734], 0.65, RoughMap::Smooth),
    /* swamp_mud     */ style(3.5, [0.882, 0.817, 0.864], [0.0612, 0.0762, 0.0343], 0.70, RoughMap::Smooth),
    /* dirt_road     */ style(3.0, [1.094, 1.148, 1.102], [0.3140, 0.2384, 0.1620], 0.92, RoughMap::None),
    /* pebbles       */ style(2.5, [0.880, 0.920, 1.020], [0.2262, 0.2476, 0.2701], 0.85, RoughMap::Rough),
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
                if let crate::recipes::EntityKind::Terrain { spec } = &spec.kind
                    && !spec.layers.is_empty()
                {
                    return Some(spec.clone());
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
/// Degraus do factor de luz em que o tint do chão é republicado. Profiling
/// 2026-09-09: cada degrau reescreve a tabela de TODOS os materiais de
/// chunk (~4 k entradas da binding array) e lia-se como SOLUÇO de frame —
/// 48 passos (burst a cada ~25 s) era excessivo; 16 (~75 s) mantém a
/// transição imperceptível (cada degrau = 6 % do fator de luz) com um
/// terço dos bursts.
const DAY_TINT_STEPS: f32 = 16.0;

/// Tolerância do "já está publicado" (ver [`terrain_rain_wetness`]).
const WETNESS_PUBLISH_EPS: f32 = 1e-3;

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
#[allow(clippy::too_many_arguments)] // sistema Bevy com queries/params explícitos
pub fn terrain_daynight_tint(
    clock: Option<Res<crate::worldsys::DayCycleState>>,
    atmosphere: Option<Res<crate::worldsys::AtmosphereState>>,
    chunks: Option<Res<super::runtime::TerrainChunkMaterials>>,
    mut chunk_materials: ResMut<Assets<TerrainChunkMaterial>>,
    mut standards: ResMut<Assets<StandardMaterial>>,
    mut last_step: Local<Option<f32>>,
    mut sweep: Local<Option<(Vec4, Vec4)>>,
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
        // Tocar no material recria o seu bind group: o passo quantizado
        // (16 por dia de jogo) tira a frequência.
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
            let sky4 = atmosphere.as_deref().map(|a| {
                let h = a.horizon;
                let peak = h[0].max(h[1]).max(h[2]).max(1.0);
                Vec4::new(h[0] / peak, h[1] / peak, h[2] / peak, 0.85)
            });
            let sun4 = if sun_travel != Vec3::ZERO {
                Vec4::new(sun_travel.x, sun_travel.y, sun_travel.z, 1.0)
            } else {
                Vec4::ZERO
            };
            // Sem atmosfera não há o que publicar (o default de
            // `TerrainChunkParams::new` mantém o caminho desligado).
            if let Some(sky4) = sky4 {
                *sweep = Some((sky4, sun4));
            }
        }
        if let Some((sky4, sun4)) = sweep.take()
            && let Some(mut material) = chunk_materials.get_mut(&layers.material)
        {
            // Um só material: uma escrita por passo do dia (era uma passagem
            // orçamentada por milhares de materiais de chunk).
            material.params.day_tint = sky4;
            if sun4.w > 0.0 {
                material.params.sun_dir = sun4;
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
///   day-tint quantiza por FASE do dia e a chuva muda dentro da fase.
///
/// O `published` é o valor que já está no material (só se escreve quando
/// muda mais do que [`WETNESS_PUBLISH_EPS`]).
#[allow(clippy::too_many_arguments)] // sistema Bevy com queries/params explícitos
pub fn terrain_rain_wetness(
    time: Res<Time>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    chunks: Option<Res<super::runtime::TerrainChunkMaterials>>,
    mut chunk_materials: ResMut<Assets<TerrainChunkMaterial>>,
    mut throttle: Local<f32>,
    mut current: Local<f32>,
    mut published: Local<Option<f32>>,
    mut sweep: Local<Option<f32>>,
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
    let Some(chunks) = chunks else { return };
    let Some(layers) = &chunks.layer else {
        return;
    };
    // O `walls_b.w` de fábrica é 0 (seco): um mundo que nunca choveu já está
    // publicado a 0 e não paga uma passagem só para escrever zeros.
    if sweep.is_none() && (published.unwrap_or(0.0) - *current).abs() > WETNESS_PUBLISH_EPS {
        *sweep = Some(*current);
    }
    if let Some(value) = sweep.take() {
        if let Some(mut material) = chunk_materials.get_mut(&layers.material) {
            material.params.walls_b.w = value;
        }
        *published = Some(value);
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
            prototype_instance: None,
            name: None,
            tag: None,
            script: None,
            props: Vec::new(),
            links: Default::default(),
            destructible: None,
            transform: TransformSpec::default(),
            physics: PhysicsSpec::default(),
            kind,
            children: Vec::new(),
            source: None,
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
            source.contains("#[bindless(limit(128), index_table(range(0..139)))]"),
            "TerrainChunkMaterial tem de continuar bindless (139 entradas: data + 69 pares)"
        );
        assert!(
            source.contains("#[data(0, TerrainChunkParams, binding_array(10))]"),
            "a tabela de estilo vai na binding array dos materiais bindless"
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
            // WESL (Bevy 0.20): a compilação condicional é `@if(BINDLESS)`.
            wgsl.contains("@if(BINDLESS)"),
            "o shader precisa do ramo bindless"
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
        // A tabela do WGSL tem de bater certo com o número de pares do material.
        assert!(
            wgsl.contains("e: array<u32, 138>"),
            "138 entradas texture/sampler"
        );
        // Agulha em duas metades (escrita inteira casaria com este teste).
        let pairs = source.matches(&format!("    #[{}(", "sampler")).count();
        assert_eq!(pairs, 69, "13 camadas × 5 mapas + 4 planos splat");
    }

    /// REGRESSÃO do crash de arranque (2026-10-16): `limit(8)` declarava as
    /// binding arrays do slab com 8 elementos, mas o bind group do material
    /// leva ~58 descritores únicos — o wgpu recusava o `create_bind_group`
    /// ("Binding count declared with at most 8 items, but 58 items were
    /// provided") e a engine SAÍA (Validation RenderError) antes do loading
    /// screen. O limite tem de (a) caber todos os descritores do material e
    /// (b) manter `samplers × limit` abaixo do cap de elementos de sampler por
    /// stage — acima dele a Bevy desliga o bindless EM SILÊNCIO e o material
    /// cai no layout não-bindless que segfaulta o driver NVIDIA.
    #[test]
    fn test_bindless_slab_limit_holds_the_whole_material() {
        use bevy::render::render_resource::{BindlessResourceType, BindlessSlabResourceLimit};

        let slot_count = TerrainChunkMaterial::bindless_slot_count()
            .expect("TerrainChunkMaterial tem de declarar #[bindless]");
        let BindlessSlabResourceLimit::Custom(limit) = slot_count else {
            panic!(
                "o limite tem de ser explícito — o Auto (2048) excede o cap de samplers \
                 do device e desliga o bindless em silêncio"
            );
        };

        let descriptor = TerrainChunkMaterial::bindless_descriptor()
            .expect("material #[bindless] traz descriptor");
        let textures = descriptor
            .resources
            .iter()
            .filter(|t| matches!(t, BindlessResourceType::Texture2d))
            .count() as u32;
        let samplers = descriptor
            .resources
            .iter()
            .filter(|t| matches!(t, BindlessResourceType::SamplerFiltering))
            .count() as u32;
        assert!(
            textures > 0 && samplers > 0,
            "descriptor sem pares textura/sampler"
        );
        assert!(
            limit >= textures && limit >= samplers,
            "o slab ({limit}) tem de caber TODOS os descritores do material \
             ({textures} texturas / {samplers} samplers) — abaixo disso o wgpu rejeita \
             o bind group ('declared with at most {limit} items') e a app sai no arranque"
        );
        // Teto do device onde o Auto (2048 → 69 × 2048 = 141 312) já caiu para
        // não-bindless: cap observado ≈ 2^17. Manter o produto bem abaixo.
        const SAMPLER_ELEMENT_CAP: u32 = 131_072;
        assert!(
            samplers * limit <= SAMPLER_ELEMENT_CAP,
            "samplers × limit = {} excede o cap do device (~{SAMPLER_ELEMENT_CAP}) — \
             o material cai para não-bindless em silêncio (SIGSEGV NVIDIA)",
            samplers * limit
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
            "assets/shaders/terrain_chunk.wesl",
            "worlds/shaders/terrain_chunk.wesl",
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

    /// A tabela de estilo cobre as 13 camadas do pool pela ORDEM dos slots,
    /// coloca o splat global e aponta a rocha das paredes à mountain_stone.
    #[test]
    fn test_terrain_params_cover_the_pool() {
        use super::super::splat::{
            SLOT_DIRT, SLOT_DIRT_TRAIL, SLOT_GRASS, SLOT_MOUNTAIN_STONE, SLOT_SNOW_PEAK,
            SLOT_VALE_GRASS,
        };
        let cfg = TerrainChunkConfig {
            tri_slope: 0.36,
            tri_soft: 0.12,
            strata_spacing: 4.0,
            strata_strength: 0.25,
            rock_darken: 0.45,
            streaks: 0.5,
            moss: 0.35,
        };
        let params = TerrainChunkParams::new([-2000.0, -2000.0], 4000.0, &cfg);
        assert_eq!(
            params.world.w, SLOT_MOUNTAIN_STONE as f32,
            "rocha das paredes"
        );
        assert_eq!(params.world.z, 4000.0);
        assert_eq!(params.world.x, -2000.0);
        assert_eq!(params.tiles[SLOT_DIRT].x, SLOT_STYLES[SLOT_DIRT].tile);
        assert_eq!(
            params.tints[SLOT_SNOW_PEAK].x,
            SLOT_STYLES[SLOT_SNOW_PEAK].tint[0]
        );
        assert_eq!(params.walls_a.x, 0.36, "tri_slope vem do config");
        assert_eq!(params.walls_b.z, 0.35, "moss vem do config");
        // Roughness: `roughness.ktx2` → mix sem inverter; `smoothness.ktx2` →
        // mix + INVERT; sem mapa → só a constante.
        assert!((params.roughs[SLOT_GRASS].y - ROUGH_MAP_MIX).abs() < 1e-6);
        assert_eq!(params.roughs[SLOT_GRASS].z, 0.0);
        assert_eq!(
            params.roughs[SLOT_VALE_GRASS].z, 1.0,
            "smoothness entra invertido"
        );
        assert_eq!(
            params.roughs[SLOT_DIRT_TRAIL].y, 0.0,
            "sem mapa → só a constante"
        );
        assert_eq!(
            params.roughs[SLOT_DIRT_TRAIL].x,
            SLOT_STYLES[SLOT_DIRT_TRAIL].rough
        );
    }

    impl EntitySpec {
        fn with_terrain(mut self, terrain: EntitySpec) -> EntitySpec {
            self.children.push(terrain);
            self
        }
    }
}
