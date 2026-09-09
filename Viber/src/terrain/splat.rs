//! Splat map generator — CPU bakes the per-texel blend weights of the
//! terrain layer material ([`super::layer_material`]).
//!
//! Replaces the height/slope vertex tint: instead of one diffuse texture
//! modulated by banding colours, the terrain blends up to
//! [`LAYER_COUNT`] ground textures from the shared pool. The blend weights
//! live in four RGBA8 splat planes (4 weights each; the grass slot closes
//! the remainder to 1.0) generated once per world at bootstrap from the
//! carved heightfield — same inputs the old tint used (altitude, slope) plus
//! the water and road registries:
//!
//! * **Altitude** — `snow_peak` above `snow-height`, `dirt` patches on the
//!   mid bands.
//! * **Slope** — `mountain_stone` on cliffs, `gravel` on the band below.
//! * **Water** (the shore contract) — lake/river floors go `sand` in the
//!   shallows and `pebbles` on the deep bed, the shore fades
//!   sand→terrain over `shore_width` meters outside the waterline, and the
//!   wet fringe carries dithered `swamp_mud` + incrusted pebbles.
//! * **Roads** — a worn `dirt_trail` core with `dirt_road` fringes under
//!   every road ribbon, so the alpha-feathered ribbon lands on matching
//!   ground instead of grass.
//! * **Noise** — deterministic value-noise fields pick the pool
//!   *variations* (grass↔vale_grass, sand↔desert_sand) and break every band
//!   into organic patches.
//!
//! Everything is a pure function of `(grid, water, roads, params)` — no ECS,
//! no assets — so the whole map is unit-testable headless.

use bevy::asset::RenderAssetUsages;
use bevy::image::Image;
use bevy::math::Vec2;
use bevy::render::render_resource::{Extent3d, TextureFormat};

use super::brush::{BrushGrid, smootherstep01};
use super::roads::RoadPath;
use super::water::{WaterBody, WaterKind};
use crate::textures::patch_image;

/// Texture slots of the terrain layer shader, in binding order. Each slot
/// binds one pool texture (`/assets/textures/<alias>/albedo.ktx2`); the XML
/// `layers` attribute may list any subset in any order (unknown names are
/// parser warnings, missing files are audit errors).
///
/// The defaults cover every ground texture in the pool: the two grass
/// variations, the two dirt variations (+ road), the two sand variations and
/// the stone/snow/mud endpoints — plus the water-bed `pebbles` (Ground021,
/// river pebbles).
pub const DEFAULT_LAYERS: [&str; LAYER_COUNT] = [
    "grass",
    "vale_grass",
    "dirt",
    "dirt_trail",
    "forest_floor",
    "gravel",
    "mountain_stone",
    "sand",
    "desert_sand",
    "snow_peak",
    "swamp_mud",
    "dirt_road",
    "pebbles",
];

/// Slot count of the terrain layer shader (the WGSL template is written for
/// exactly this many).
pub const LAYER_COUNT: usize = 13;

/// Pool alias → albedo texture path RELATIVE ao `terrain_textures_dir` do
/// config.yaml do jogo. `None` when `alias` is not a pool name (callers then
/// treat it as a raw texture path).
pub fn pool_albedo(alias: &str) -> Option<String> {
    if DEFAULT_LAYERS.contains(&alias) {
        Some(format!("{alias}/albedo.ktx2"))
    } else {
        None
    }
}

/// Pool alias → tangent-space normal map path (UASTC linear) RELATIVE ao
/// `terrain_textures_dir`. `None` quando `alias` não é um nome do pool. O
/// ficheiro pode não existir em nenhuma root — quem chama tem de vigiar a
/// carga e repontar para a normal plana.
pub fn pool_normal(alias: &str) -> Option<String> {
    if DEFAULT_LAYERS.contains(&alias) {
        Some(format!("{alias}/normal.ktx2"))
    } else {
        None
    }
}

/// Pool alias → height map path (escalar, LINEAR) RELATIVE ao
/// `terrain_textures_dir`. Alimenta o height-blend entre as duas layers
/// dominantes do fragmento (a rocha "sai por cima" da relva na fronteira).
pub fn pool_height(alias: &str) -> Option<String> {
    if DEFAULT_LAYERS.contains(&alias) {
        Some(format!("{alias}/height.ktx2"))
    } else {
        None
    }
}

/// Pool alias → ambient-occlusion map path (escalar, LINEAR) RELATIVE ao
/// `terrain_textures_dir`. Escurece o albedo por texel (contacto entre
/// pedras, juntas de cascalho).
pub fn pool_ao(alias: &str) -> Option<String> {
    if DEFAULT_LAYERS.contains(&alias) {
        Some(format!("{alias}/ao.ktx2"))
    } else {
        None
    }
}

/// Canonical layer list: position = splat slot ([`DEFAULT_LAYERS`] order).
///
/// Os pesos do splat e os materiais de chunk são indexados por slot DEFAULT,
/// mas a lista autoral vinha na ordem escrita — `layers="sand grass"` trocava
/// relva↔areia no mundo inteiro e um subconjunto lia a posição autoral como
/// índice de slot (praias de relva). Aliases conhecidos vão para o slot
/// deles; caminhos de textura fora do pool preenchem os slots livres pela
/// ordem escrita; duplicados mantêm a primeira ocorrência. Buracos ficam
/// como `""` (o spawn trata-os como "sem textura autoral") e a lista volta
/// aparada pelo fim.
pub fn canonicalize_layers(entries: &[String]) -> Vec<String> {
    let mut out = vec![String::new(); LAYER_COUNT];
    let mut free: Vec<usize> = (0..LAYER_COUNT).collect();
    for entry in entries {
        let entry = entry.trim();
        if entry.is_empty() {
            continue;
        }
        let target = match DEFAULT_LAYERS.iter().position(|a| *a == entry) {
            Some(i) if out[i].is_empty() => Some(i),
            Some(_) => None, // alias duplicado: mantém o primeiro
            None => free.first().copied(),
        };
        if let Some(i) = target {
            out[i] = entry.to_string();
            free.retain(|&f| f != i);
        }
    }
    while out.last().is_some_and(String::is_empty) {
        out.pop();
    }
    out
}

// Slot indices — the splat planes pack 4 weights per RGBA channel.
pub const SLOT_GRASS: usize = 0;
pub const SLOT_VALE_GRASS: usize = 1;
pub const SLOT_DIRT: usize = 2;
pub const SLOT_DIRT_TRAIL: usize = 3;
pub const SLOT_FOREST_FLOOR: usize = 4;
pub const SLOT_GRAVEL: usize = 5;
pub const SLOT_MOUNTAIN_STONE: usize = 6;
pub const SLOT_SAND: usize = 7;
pub const SLOT_DESERT_SAND: usize = 8;
pub const SLOT_SNOW_PEAK: usize = 9;
pub const SLOT_SWAMP_MUD: usize = 10;
pub const SLOT_DIRT_ROAD: usize = 11;
pub const SLOT_RIVERBED: usize = 12;

/// Splat-plane / channel of each slot: plane 0..3, channel 0..3 (RGBA).
/// Grass packs in plane 0 R but is written as the closing remainder.
const SLOT_PACKING: [(usize, usize); LAYER_COUNT] = [
    (0, 0), // grass
    (0, 1), // vale_grass
    (0, 2), // dirt
    (0, 3), // dirt_trail
    (1, 0), // forest_floor
    (1, 1), // gravel
    (1, 2), // mountain_stone
    (1, 3), // sand
    (2, 0), // desert_sand
    (2, 1), // snow_peak
    (2, 2), // swamp_mud
    (2, 3), // dirt_road
    (3, 0), // pebbles (river/lake bed)
];

/// Ground climate of a region: which pool textures close the splat budget
/// where no override (cliff, snow line, shore, road) claims it.
///
/// Sem isto o chão é sempre relva com manchas: o deserto lia-se verde com
/// cactos por cima e o pântano verde com árvores mortas por cima — a crítica
/// número 1 contra o BOTW, onde a **cor do próprio terreno** identifica a
/// região antes de qualquer prop.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Biome {
    /// Vale temperado — relva (o default de qualquer mundo).
    Vale,
    /// Floresta — manta de folhada sobre relva escura.
    Forest,
    /// Deserto — areia; a relva não sobrevive aqui.
    Desert,
    /// Pântano — lodo escuro e turfa.
    Swamp,
    /// Picos — cascalho, rocha exposta e a linha de neve muito mais baixa.
    Peaks,
}

impl Biome {
    /// `None` para nomes fora do vocabulário (o chamador avisa e ignora).
    pub fn from_name(name: &str) -> Option<Self> {
        match name.trim().to_ascii_lowercase().as_str() {
            "vale" | "valley" | "grass" | "temperate" => Some(Self::Vale),
            "forest" | "floresta" | "woods" => Some(Self::Forest),
            "desert" | "deserto" | "dunes" => Some(Self::Desert),
            "swamp" | "pantano" | "pântano" | "bog" | "marsh" => Some(Self::Swamp),
            "peaks" | "picos" | "alpine" | "mountain" => Some(Self::Peaks),
            _ => None,
        }
    }

    /// Mistura de solo (soma 1), viés da linha de neve, viés da banda de
    /// rocha e quanto o bioma tolera as manchas de terra/folhada.
    fn palette(self) -> BiomeBlend {
        let mut ground = [0.0f32; LAYER_COUNT];
        let (snow_bias, rock_bias, patchiness, aridity) = match self {
            Biome::Vale => {
                ground[SLOT_GRASS] = 0.56;
                ground[SLOT_VALE_GRASS] = 0.44;
                // 0.75: a 1.0 as manchas de terra/folhada liam-se como
                // borrões escuros espalhados pelo vale inteiro (crítica do
                // autor, 2026-09-07) — variedade sim, sardas não.
                (0.0, 0.0, 0.75, 0.0)
            }
            Biome::Forest => {
                ground[SLOT_FOREST_FLOOR] = 0.46;
                ground[SLOT_GRASS] = 0.40;
                ground[SLOT_VALE_GRASS] = 0.14;
                (0.02, 0.0, 1.15, 0.0)
            }
            Biome::Desert => {
                ground[SLOT_DESERT_SAND] = 0.62;
                ground[SLOT_SAND] = 0.32;
                ground[SLOT_GRAVEL] = 0.06;
                (0.10, 0.04, 0.25, 1.0)
            }
            Biome::Swamp => {
                ground[SLOT_SWAMP_MUD] = 0.60;
                ground[SLOT_GRASS] = 0.24;
                ground[SLOT_DIRT] = 0.16;
                (0.06, 0.02, 0.55, 0.0)
            }
            Biome::Peaks => {
                ground[SLOT_GRAVEL] = 0.52;
                ground[SLOT_MOUNTAIN_STONE] = 0.34;
                ground[SLOT_VALE_GRASS] = 0.14;
                (-0.26, -0.08, 0.35, 0.35)
            }
        };
        BiomeBlend {
            ground,
            snow_bias,
            rock_bias,
            patchiness,
            aridity,
        }
    }
}

/// Paleta resolvida num ponto — a mistura dos biomas que lá se cruzam.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BiomeBlend {
    /// Mistura de solo normalizada (soma 1).
    pub ground: [f32; LAYER_COUNT],
    /// Somado a `snow-height` (negativo = neve começa mais baixo).
    pub snow_bias: f32,
    /// Somado às arestas da banda de rocha (negativo = rocha aparece antes).
    pub rock_bias: f32,
    /// Escala das manchas de folhada/terra.
    pub patchiness: f32,
    /// 1 = árido; suprime relva de qualquer origem.
    pub aridity: f32,
}

/// Uma cunha cardeal de bioma: tudo o que sai do núcleo naquela direção.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BiomeWedge {
    /// Direção da cunha em XZ (normalizada pelo construtor).
    pub dir: Vec2,
    pub biome: Biome,
}

/// Campo de biomas: um núcleo neutro (o vale) e N cunhas cardeais à volta.
///
/// Vazio = mundo inteiro `Vale`, que é o comportamento anterior a esta
/// funcionalidade — mundos sem biomas declarados não mudam de aspeto.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct BiomeField {
    pub wedges: Vec<BiomeWedge>,
    /// Raio (m) do núcleo neutro à volta da origem.
    pub core: f32,
    /// Largura (m) da transição núcleo → cunhas.
    pub blend: f32,
}

impl BiomeField {
    /// Expoente da cunha: 3 dá ~45° de domínio com transição macia.
    const WEDGE_SHARPNESS: f32 = 3.0;
    /// Amplitude (rad) do warp que quebra a fronteira em raio perfeito.
    const EDGE_WARP: f32 = 0.62;
    /// Comprimento de onda (m) desse warp.
    const EDGE_WARP_M: f32 = 240.0;

    pub fn is_empty(&self) -> bool {
        self.wedges.is_empty()
    }

    /// Paleta interpolada em `(x, z)`.
    pub fn sample(&self, x: f32, z: f32, seed: u64) -> BiomeBlend {
        let vale = Biome::Vale.palette();
        if self.wedges.is_empty() {
            return vale;
        }
        // A fronteira entre cunhas é uma reta a 45°; sem o warp lê-se como
        // uma costura pintada no chão a atravessar o mapa.
        let warp = (value_noise(x / Self::EDGE_WARP_M, z / Self::EDGE_WARP_M, seed, 9) - 0.5)
            * Self::EDGE_WARP;
        let r = (x * x + z * z).sqrt();
        let angle = z.atan2(x) + warp;
        let dir = Vec2::new(angle.cos(), angle.sin());
        let core = 1.0 - smoothstep(self.core, self.core + self.blend.max(1.0), r);

        let mut acc = BiomeBlend {
            ground: [0.0; LAYER_COUNT],
            snow_bias: 0.0,
            rock_bias: 0.0,
            patchiness: 0.0,
            aridity: 0.0,
        };
        let mut weights: Vec<f32> = Vec::with_capacity(self.wedges.len());
        let mut total = 0.0;
        for wedge in &self.wedges {
            let w = dir.dot(wedge.dir).max(0.0).powf(Self::WEDGE_SHARPNESS);
            total += w;
            weights.push(w);
        }
        let outer = 1.0 - core;
        if total <= 1e-6 {
            return vale;
        }
        let mut add = |blend: BiomeBlend, w: f32| {
            for slot in 0..LAYER_COUNT {
                acc.ground[slot] += blend.ground[slot] * w;
            }
            acc.snow_bias += blend.snow_bias * w;
            acc.rock_bias += blend.rock_bias * w;
            acc.patchiness += blend.patchiness * w;
            acc.aridity += blend.aridity * w;
        };
        add(vale, core);
        for (wedge, w) in self.wedges.iter().zip(&weights) {
            add(wedge.biome.palette(), outer * w / total);
        }
        // Fecha a soma: as cunhas já somam 1 por construção, mas o warp e o
        // clamp podem deixar migalhas.
        let sum: f32 = acc.ground.iter().sum();
        if sum > 1e-4 {
            for slot in 0..LAYER_COUNT {
                acc.ground[slot] /= sum;
            }
        } else {
            acc = vale;
        }
        acc
    }
}

/// Generator parameters (parsed from the `<Terrain>` attributes).
#[derive(Debug, Clone, PartialEq)]
pub struct SplatParams {
    /// Shore band outside the waterline: sand fades to the terrain texture
    /// over this many meters (`shore-width`).
    pub shore_width: f32,
    /// Normalized altitude where `snow_peak` takes over (the tint's
    /// `snow-height`, reused so worlds keep one knob).
    pub snow_height: f32,
    /// Procedural seed of the terrain — perturbs the noise fields.
    pub seed: u64,
    /// Splat texel size in meters; `0` = auto (`world/2048` floor 1 m).
    pub texel: f32,
    /// Climas do chão. Vazio = o mundo todo é `Biome::Vale`.
    pub biomes: BiomeField,
    /// Knobs ao vivo (`viber.debug.ground{...}`) — defaults = r7.
    pub tuning: SplatTuning,
}

/// Live tuning of the ground noise layers — os knobs que o
/// `viber.debug.ground{...}` move SEM rebuild (o rebake dos splats lê-os).
/// Defaults = o comportamento de r7.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SplatTuning {
    /// Escala sobre a `patchiness` do bioma (manchas de terra/folhada).
    pub patchiness: f32,
    /// Largura do ombro de cascalho abaixo da linha de rocha (slope units).
    pub gravel_shoulder: f32,
    /// Densidade das manchas de `dirt` (0 = off).
    pub dirt_density: f32,
    /// Densidade das manchas de `forest_floor` (0 = off).
    pub forest_density: f32,
    /// Banda de areia fora da linha de água (m; Some = sobrepõe
    /// `shore-width`).
    pub shore_width: Option<f32>,
    /// Suavidade das REGIÕES relva-escura↔relva-clara (`n_region`): 0 =
    /// banda fina de hoje (regiões contrastadas — as "manchas"), 1 =
    /// tudo fundido num verde único. Default 0.
    pub vale_soft: f32,
}
impl Default for SplatTuning {
    fn default() -> Self {
        Self {
            // Defaults r7 (crítica "manchas no chão", 2026-09-07): variedade
            // SUBTIL — os blobs de dois verdes e as manchas de terra/folhada
            // cheias eram lidas como sujidade. `viber.debug.ground{...}`
            // move tudo ao vivo (0..2 reintroduz a variedade cheia).
            patchiness: 1.0,
            gravel_shoulder: 0.05,
            dirt_density: 0.6,
            forest_density: 0.6,
            shore_width: None,
            vale_soft: 0.6,
        }
    }
}

impl Default for SplatParams {
    fn default() -> Self {
        Self {
            shore_width: 5.0,
            snow_height: 0.75,
            seed: 0,
            texel: 0.0,
            biomes: BiomeField::default(),
            tuning: SplatTuning::default(),
        }
    }
}

/// Baked splat map: four RGBA8 planes of `size²` texels covering the whole
/// world (`texel` meters per texel, origin at the -X/-Z corner).
///
/// Retired from production by the per-chunk planes ([`generate_chunk_splats`])
/// but kept as the test oracle for the weight field.
#[allow(dead_code)]
#[derive(Debug, Clone, PartialEq)]
pub struct SplatMap {
    pub size: u32,
    pub texel: f32,
    /// Plane bytes, RGBA8 row-major, alpha = channel 3.
    pub planes: [Vec<u8>; 4],
}

/// AABB of a water body / road, expanded by `margin` — texels outside every
/// box skip the distance queries (the hot loop cost of a 4 km world).
struct Bounds {
    min: Vec2,
    max: Vec2,
}

impl Bounds {
    fn contains(&self, p: Vec2) -> bool {
        p.x >= self.min.x && p.x <= self.max.x && p.y >= self.min.y && p.y <= self.max.y
    }
}

fn water_bounds(body: &WaterBody, margin: f32) -> Bounds {
    match body.kind {
        WaterKind::Lake => {
            let reach = body.radius * 1.28 + margin + 2.0;
            Bounds {
                min: body.at - Vec2::splat(reach),
                max: body.at + Vec2::splat(reach),
            }
        }
        WaterKind::River => {
            let mut min = Vec2::splat(f32::INFINITY);
            let mut max = Vec2::splat(f32::NEG_INFINITY);
            for s in &body.stations {
                min = min.min(*s);
                max = max.max(*s);
            }
            let pad = Vec2::splat(body.water_width * 0.5 + margin + 2.0);
            Bounds {
                min: min - pad,
                max: max + pad,
            }
        }
    }
}

fn road_bounds(road: &RoadPath, margin: f32) -> Bounds {
    let mut min = Vec2::splat(f32::INFINITY);
    let mut max = Vec2::splat(f32::NEG_INFINITY);
    for s in &road.stations {
        min = min.min(*s);
        max = max.max(*s);
    }
    let pad = Vec2::splat(margin);
    Bounds {
        min: min - pad,
        max: max + pad,
    }
}

/// Bakes the splat map for a carved world.
///
/// Retired from production by [`generate_chunk_splats`] (kept as the test
/// oracle for the weight field).
#[allow(dead_code)]
pub fn generate_splats(
    grid: &BrushGrid,
    water: &[WaterBody],
    roads: &[RoadPath],
    params: &SplatParams,
) -> SplatMap {
    let world = grid.world_size();
    let texel = if params.texel > 0.0 {
        params.texel
    } else {
        (world / 2048.0).max(1.0)
    };
    let size = ((world / texel).round() as u32).clamp(16, 4096);

    let shore = params
        .tuning
        .shore_width
        .unwrap_or(params.shore_width)
        .max(0.5);
    let water_boxes: Vec<(Bounds, &WaterBody)> = water
        .iter()
        .filter(|b| b.radius > 0.0 || !b.stations.is_empty())
        .map(|b| (water_bounds(b, shore), b))
        .collect();
    let road_boxes: Vec<(Bounds, &RoadPath)> = roads
        .iter()
        .filter(|r| !r.bridge)
        .map(|r| (road_bounds(r, 4.0), r))
        .collect();

    let mut planes: [Vec<u8>; 4] = [
        vec![0u8; (size as usize) * (size as usize) * 4],
        vec![0u8; (size as usize) * (size as usize) * 4],
        vec![0u8; (size as usize) * (size as usize) * 4],
        vec![0u8; (size as usize) * (size as usize) * 4],
    ];
    let epsilon = grid.texel();
    let mut ctx = TexelCtx {
        params,
        shore,
        water_boxes: &water_boxes,
        road_boxes: &road_boxes,
        cliff: None,
    };

    for tz in 0..size {
        let world_z = (tz as f32 + 0.5) * texel - world * 0.5;
        for tx in 0..size {
            let world_x = (tx as f32 + 0.5) * texel - world * 0.5;
            let y = grid.sample(world_x, world_z);
            let normal = grid.sample_normal(world_x, world_z, epsilon);
            let weights = weights_at(world_x, world_z, y, normal.y, grid.max_height(), &mut ctx);
            let i = ((tz as usize * size as usize) + tx as usize) * 4;
            for slot in 0..LAYER_COUNT {
                let (plane, channel) = SLOT_PACKING[slot];
                // 251 keeps the u8 sum (with the grass remainder) ≤ 255.
                let v = (weights[slot] * 251.0).round().clamp(0.0, 251.0) as u8;
                planes[plane][i + channel] = v;
            }
        }
    }

    SplatMap {
        size,
        texel,
        planes,
    }
}

/// Per-texel query inputs (world-space position + sampled surface).
pub struct TexelCtx<'a> {
    params: &'a SplatParams,
    shore: f32,
    water_boxes: &'a [(Bounds, &'a WaterBody)],
    road_boxes: &'a [(Bounds, &'a RoadPath)],
    /// Region-filtered cliff mask: inside the CORE the wall band paints as
    /// one solid rock mass (no per-texel patches); the dilated ring fades
    /// the surrounding grass/snow so the wall reads as a single block.
    cliff: Option<&'a crate::terrain::cliffs::CliffMask>,
}

/// Noise band centers/widths — all thresholds are normalized 0..1 noise.
const NOISE_REGION: f32 = 110.0; // grass ↔ vale_grass regions (m)
const NOISE_FOREST: f32 = 22.0; // forest-floor patches (m)
const NOISE_DIRT: f32 = 13.0; // dirt patches (m)
const NOISE_SAND: f32 = 90.0; // sand ↔ desert regions (m)
const NOISE_MUD: f32 = 9.0; // mud dithering (m)
const NOISE_DIRT_REGION: f32 = 45.0; // onde nascem as manchas de terra (m)
const NOISE_FOREST_REGION: f32 = 80.0; // onde nascem os bosques (m)
const NOISE_BED: f32 = 6.0; // manchas de seixo no leito (m)
/// Profundidade (m) onde o leito começa a trocar areia por seixo e onde a
/// troca é total — água rasa mantém a rim de areia.
const BED_DEPTH_START: f32 = 0.35;
const BED_DEPTH_FULL: f32 = 0.9;

/// Blend weights for one texel. The output sums to exactly 1 (grass closes
/// the remainder), so the shader never sees a hole in the surface.
pub fn weights_at(
    x: f32,
    z: f32,
    y: f32,
    normal_y: f32,
    max_height: f32,
    ctx: &mut TexelCtx,
) -> [f32; LAYER_COUNT] {
    let p = ctx.params;
    let h = (y / max_height.max(1.0)).clamp(0.0, 1.0);
    let slope = 1.0 - normal_y.clamp(0.0, 1.0);
    let climate = p.biomes.sample(x, z, p.seed);

    // Campos de peso em single-octave: a oitava de detalhe do fbm
    // esfarelava as fronteiras das manchas em sal-e-pimenta visível a
    // curta distância. Grão fino fica só na lama (dithering pretendido).
    let n_region = value_noise(x / NOISE_REGION, z / NOISE_REGION, p.seed, 0);
    let n_forest = value_noise(x / NOISE_FOREST, z / NOISE_FOREST, p.seed, 1);
    let n_dirt = value_noise(x / NOISE_DIRT, z / NOISE_DIRT, p.seed, 2);
    let n_sand = value_noise(x / NOISE_SAND, z / NOISE_SAND, p.seed, 3);
    let n_mud = fbm(x, z, NOISE_MUD, p.seed, 4);
    // Máscaras regionais (manchas só nascem onde a região as quer — sem
    // isto os thresholds no centro da mediana do noise fragmentavam o
    // mundo inteiro em parches de terra/floresta).
    let n_dirt_region = value_noise(x / NOISE_DIRT_REGION, z / NOISE_DIRT_REGION, p.seed, 5);
    let n_forest_region = value_noise(x / NOISE_FOREST_REGION, z / NOISE_FOREST_REGION, p.seed, 6);

    // ── Shore: sand on lake/river floors, fading out across the shore band.
    // (Antes do bloco da neve: a supressão de neve submersa precisa das
    // mesmas máscaras de profundidade que o leito.)
    let (sand_mask, mud_band, shallow, bed_floor) = shore_weights(x, z, y, ctx);

    // ── Hard override layers first: cliffs and the snow line take budget
    //    away from everything below them (rock beats snow on a cliff, the
    //    same precedence the old vertex tint used).
    //
    // As arestas seguem o bioma: nos picos a rocha nasce mais cedo e a neve
    // muito mais baixo; no deserto a neve é empurrada para fora do mapa.
    let rock0 = (0.30 + climate.rock_bias).clamp(0.05, 0.9);
    let stone = smoothstep(rock0, rock0 + 0.22, slope);
    let snow_h = (p.snow_height + climate.snow_bias).clamp(0.0, 1.5);
    let snow = smoothstep(snow_h - 0.06, snow_h + 0.10, h)
        // A neve agarra-se ao plano; a parede fica rocha nua (BOTW).
        * smoothstep(0.34, 0.16, slope)
        * (1.0 - stone)
        // Submersa a neve derrete: num piso de lago fundo acima da linha de
        // neve o orçamento ficava NEGATIVO (`budget = 1−stone−snow−bed`) e
        // `scale = budget/used` invertia os pesos restantes — neve/seixo
        // sem areia nem lama. Os fatores de alagamento do leito
        // (`bed_floor`, franja rasa `shallow`) suprimem-na como ao bed.
        * (1.0 - bed_floor.max(shallow));
    let budget = 1.0 - stone - snow;
    // O leito profundo entra em MANCHAS de noise — um tapete uniforme lê-se
    // como anel pintado; as manchas alternam seixo ↔ areia/cascalho como um
    // fundo de rio real. A franja húmida em seco ganha seixos salpicados
    // (as "pedras incrustadas" da margem), dithered pelo mesmo grão da lama.
    let n_bed = fbm(x, z, NOISE_BED, p.seed, 7);
    let bed_patch = smoothstep(0.38, 0.62, n_bed);
    // (1−stone): numa margem íngreme submersa a rocha ganha — seixos não
    // assentam em parede, e budget = 1−stone−snow−bed tem de ficar ≥ 0.
    let bed =
        (bed_floor + mud_band * 0.45 * smoothstep(0.55, 0.80, n_mud)).min(1.0) * (1.0 - stone);
    let bed_pebbles = bed * (0.30 + 0.70 * bed_patch);
    let bed_gravel = bed * 0.35 * (1.0 - bed_patch);
    let bed_total = (bed_pebbles + bed_gravel).min(1.0);
    // Num clima árido a margem é duna, não praia de rio: a aridez do bioma
    // puxa a divisão sand↔desert_sand para o lado seco. O leito não segue o
    // deserto — um rio no deserto tem pedras na mesma.
    let desert = smoothstep(0.40, 0.52, n_sand).max(climate.aridity * 0.75);
    let sand = sand_mask * (1.0 - desert) * (1.0 - bed_total);
    // Duna NUNCA submersa: o noise de 90 m punha desert_sand (o rosado) no
    // FUNDO dos lagos — o leito é seixo/areia de rio, não deserto. O
    // comentário acima já prometia isto para o leito; o termo de noise
    // violava-o em clima húmido.
    let desert_w = sand_mask * desert * (1.0 - bed_floor.min(1.0));
    // Mud: shallow water + the wet fringe right above the waterline,
    // dithered so it reads as damp patches instead of a painted ring.
    let mud = (shallow * 0.5 + mud_band * 0.45) * (0.5 + 0.5 * n_mud) * (1.0 - stone);

    // ── Mid layers: gravel shoulder under the cliffs, forest patches and
    //    dirt patches on gentle ground.
    //
    // A banda de cascalho arrancava a 0.16 (≈33°) e cobria metade do mundo
    // assim que o terreno deixou de ser uma planície: agora é um ombro
    // estreito, colado ao sopé da rocha. (r7: o ombro antigo
    // [rock0−0.11, rock0−0.01] apanhava TODA a meia-encosta dos morros
    // proceduralmente ondulados e lia-se como manchas cinzentas — o cascalho
    // é o colar DEBAIXO da pedra, não a encosta toda.) Largura ao vivo:
    // `tuning.gravel_shoulder`.
    let tuning = &ctx.params.tuning;
    let gravel = smoothstep(
        rock0 - tuning.gravel_shoulder,
        rock0 - 0.005,
        slope,
    ) * (1.0 - stone)
        * (1.0 - snow);
    let patch = climate.patchiness * tuning.patchiness;
    // Manchas mais RARAS (limiares subidos em r7): a 0.57–0.66 o fbm abria
    // borriões escuros em toda a colina; a faixa estreita deixa só os
    // núcleos das manchas e as bordas dissolve-em-relva.
    let forest = smoothstep(0.60, 0.70, n_forest)
        * smoothstep(0.55, 0.67, n_forest_region)
        * smoothstep(0.38, 0.28, slope)
        * (1.0 - snow)
        * (1.0 - sand_mask)
        * patch
        * tuning.forest_density;
    let dirt = smoothstep(0.62, 0.72, n_dirt)
        * smoothstep(0.58, 0.68, n_dirt_region)
        // Piso do vale fica relva; terra nasce em altitude média (laderas
        // e transições) — em plano plano as fronteiras das regiões liam-se
        // como linhas artificiais.
        * smoothstep(0.10, 0.28, h)
        * (1.0 - snow)
        * (1.0 - sand_mask)
        * patch
        * tuning.dirt_density;

    // ── Worn road shoulders (skipped underwater/over sand: road ribbons
    //    cross rivers on bridges only, but a drowned trail must not bleach
    //    the lake floor).
    let d_road = road_distance(x, z, ctx);
    let road_band = 1.0 - smootherstep(-0.4, 1.2, d_road);
    let road_fringe = (1.0 - smootherstep(1.0, 2.4, d_road)) * (1.0 - road_band);
    let trail = road_band * (1.0 - sand_mask);
    let dirt_road = road_fringe * (1.0 - sand_mask);

    // ── Normalize everything that shares `budget`, grass closes the rest.
    // O leito (seixo+cascalho) também consome orçamento — é um override
    // shore-like, à frente das manchas. Clamp defensivo: overrides podem
    // somar mais que 1 (franja de lama sobre neve residual) — sem o clamp
    // `scale` ficava negativo e invertia o `rest`.
    let budget = (budget - bed_total).max(0.0);
    let mut rest = [sand, desert_w, mud, gravel, forest, dirt, trail, dirt_road];
    let used: f32 = rest.iter().sum();
    let scale = if used > budget && used > 0.0 {
        budget / used
    } else {
        1.0
    };
    for r in &mut rest {
        *r *= scale;
    }
    let rest_sum: f32 = rest.iter().sum();

    // ── O resto do orçamento fecha com a paleta do BIOMA (era sempre
    //    relva). É isto que faz o deserto ler-se areia e o pântano lodo
    //    sem depender dos props plantados por cima.
    let ground = (budget - rest_sum).max(0.0);
    // Dentro da paleta, o par relva/vale_grass ainda se reparte pelo ruído
    // de região — senão o vale inteiro fica de um verde só. `vale_soft`
    // (ao vivo) alarga a banda: 0 = regiões contrastadas de hoje, 1 = os
    // dois verdes fundidos (as "manchas" desaparecem).
    let vale_band = 0.06 + tuning.vale_soft.clamp(0.0, 1.0) * 0.94;
    let vale = smoothstep(0.5 - vale_band * 0.5, 0.5 + vale_band * 0.5, n_region);
    let mut out = [0.0; LAYER_COUNT];
    let g_grass = climate.ground[SLOT_GRASS] + climate.ground[SLOT_VALE_GRASS];
    for (slot, weight) in climate.ground.iter().enumerate() {
        if slot == SLOT_GRASS || slot == SLOT_VALE_GRASS {
            continue;
        }
        out[slot] = ground * weight;
    }
    out[SLOT_GRASS] = ground * g_grass * (1.0 - vale);
    out[SLOT_VALE_GRASS] = ground * g_grass * vale;
    // `+=`: os overrides e as manchas SOMAM-SE à paleta do bioma (que já
    // pode ter posto peso em cascalho, rocha, areia ou lodo). Com `=` a
    // paleta dos picos e do deserto era apagada pelas manchas.
    out[SLOT_DIRT] += rest[5];
    out[SLOT_DIRT_TRAIL] += rest[6];
    out[SLOT_FOREST_FLOOR] += rest[4];
    out[SLOT_GRAVEL] += rest[3] + bed_gravel;
    out[SLOT_MOUNTAIN_STONE] += stone;
    out[SLOT_SAND] += rest[0];
    out[SLOT_DESERT_SAND] += rest[1];
    out[SLOT_SNOW_PEAK] += snow;
    out[SLOT_SWAMP_MUD] += rest[2];
    out[SLOT_DIRT_ROAD] += rest[7];
    out[SLOT_RIVERBED] += bed_pebbles;
    // ── Cliff regions (region-filtered mask): dentro do CORE a banda é um
    //    bloco de pedra sólida — o peso por declive sozinho dava remendos
    //    que fragmentavam a parede de perto. O anel dilatado esbate a
    //    relva/neve vizinhas para a massa ler-se contínua. O TALUS autoral
    //    (cone de detritos no pé) pinta cascalho sujo de pedra — nunca
    //    relva/neve por cima do apron.
    if let Some(mask) = ctx.cliff {
        let p = Vec2::new(x, z);
        if mask.is_core_at(p) {
            for slot in out.iter_mut() {
                *slot = 0.0;
            }
            out[SLOT_MOUNTAIN_STONE] = 1.0;
            return out;
        }
        if mask.is_talus_at(p) {
            for slot in out.iter_mut() {
                *slot = 0.0;
            }
            out[SLOT_GRAVEL] = 0.82;
            out[SLOT_MOUNTAIN_STONE] = 0.18;
            return out;
        }
        let f = mask.factor(p);
        if f > 0.0 {
            out[SLOT_MOUNTAIN_STONE] = (out[SLOT_MOUNTAIN_STONE] + f * 1.5).min(1.2);
            let fade = 1.0 - f * 0.7;
            out[SLOT_GRASS] *= fade;
            out[SLOT_VALE_GRASS] *= fade;
            out[SLOT_SNOW_PEAK] *= fade;
        }
    }
    out
}

/// `(sand mask, wet-fringe mud, shallow-water band, deep-bed mask)` at a
/// texel.
///
/// `sand` is 1 on flooded ground and fades to 0 across `shore_width` meters
/// outside the waterline; `mud` hugs the waterline on dry land; `shallow` is
/// the flooded strip right at the shore; `bed` is the deep floor where
/// pebbles take over from sand.
fn shore_weights(x: f32, z: f32, y: f32, ctx: &mut TexelCtx) -> (f32, f32, f32, f32) {
    let p = Vec2::new(x, z);
    let mut sand = 0.0f32;
    let mut mud = 0.0f32;
    let mut shallow = 0.0f32;
    let mut bed = 0.0f32;
    for (bounds, body) in ctx.water_boxes {
        if !bounds.contains(p) {
            continue;
        }
        let d_edge = body.distance_to_waterline(p);
        if d_edge > ctx.shore {
            continue;
        }
        let Some(surf) = body.blend_surface_y(p, ctx.shore) else {
            continue;
        };
        let depth = surf - y; // > 0 → flooded
        if depth > 0.0 {
            // Lake/river floor: sand in the shallows, pebbles once the water
            // is deep enough to hide a sand rim.
            sand = sand.max(1.0);
            shallow = shallow.max(smoothstep(0.6, 0.05, depth));
            bed = bed.max(smoothstep(BED_DEPTH_START, BED_DEPTH_FULL, depth));
        } else {
            // Shore band: sand fades out, mud hugs the waterline.
            let t = smootherstep01((d_edge / ctx.shore).clamp(0.0, 1.0));
            sand = sand.max(1.0 - t);
            mud = mud.max(1.0 - smoothstep(0.0, ctx.shore * 0.7, d_edge));
        }
    }
    (sand, mud, shallow, bed)
}

/// Signed distance to the nearest non-bridge road ribbon (≤ 0 on it).
fn road_distance(x: f32, z: f32, ctx: &mut TexelCtx) -> f32 {
    let p = Vec2::new(x, z);
    let mut best = f32::INFINITY;
    for (bounds, road) in ctx.road_boxes {
        if !bounds.contains(p) {
            continue;
        }
        best = best.min(road.distance_to_road(p));
    }
    best
}

// ── Deterministic noise ─────────────────────────────────────────────────

/// 2-octave value noise at wavelength `meters`, hashed per integer lattice
/// with the terrain seed. Deterministic across runs and platforms (pure
/// integer mixing before the float mapping).
fn fbm(x: f32, z: f32, meters: f32, seed: u64, octave_salt: u64) -> f32 {
    let freq = 1.0 / meters.max(0.001);
    let base = value_noise(x * freq, z * freq, seed, octave_salt);
    let detail = value_noise(x * freq * 3.1, z * freq * 3.1, seed, octave_salt + 100);
    base * 0.72 + detail * 0.28
}

/// Single-octave value noise on a hashed lattice.
fn value_noise(x: f32, z: f32, seed: u64, salt: u64) -> f32 {
    let ix = x.floor();
    let iz = z.floor();
    let fx = x - ix;
    let fz = z - iz;
    let ux = fx * fx * (3.0 - 2.0 * fx);
    let uz = fz * fz * (3.0 - 2.0 * fz);
    let a = lattice_hash(ix, iz, seed, salt);
    let b = lattice_hash(ix + 1.0, iz, seed, salt);
    let c = lattice_hash(ix, iz + 1.0, seed, salt);
    let d = lattice_hash(ix + 1.0, iz + 1.0, seed, salt);
    let top = a + (b - a) * ux;
    let bottom = c + (d - c) * ux;
    top + (bottom - top) * uz
}

/// 0..1 hash of one lattice corner (SplitMix64-style finalizer).
fn lattice_hash(ix: f32, iz: f32, seed: u64, salt: u64) -> f32 {
    let xi = (ix as i64).to_le_bytes();
    let zi = (iz as i64).to_le_bytes();
    let mut h = seed ^ (salt << 17).wrapping_mul(0x9E37_79B9_7F4A_7C15);
    for byte in xi.iter().chain(zi.iter()) {
        h ^= u64::from(*byte);
        h = h.wrapping_mul(0x9E37_79B9_7F4A_7C15);
        h ^= h >> 29;
    }
    (h >> 11) as f32 / (1u64 << 53) as f32
}

/// GLSL-style smoothstep that also accepts INVERTED bands (`edge0 > edge1`),
/// which is how the shallow-water ring fades (`0.6 → 0.05 m` of depth).
fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    let span = edge1 - edge0;
    let t = if span.abs() < f32::EPSILON {
        if x >= edge1 { 1.0 } else { 0.0 }
    } else {
        ((x - edge0) / span).clamp(0.0, 1.0)
    };
    t * t * (3.0 - 2.0 * t)
}

/// Six-power smootherstep over an edge band — used where the sand fade must
/// have zero slope at both ends (no visible band edge).
fn smootherstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    smootherstep01((x - edge0) / (edge1 - edge0).max(f32::EPSILON))
}

/// 1×1 white RGBA8 image — the fallback a layer slot falls back to when its
/// texture AND the grass slot both failed to load (the ground stays visible,
/// just untextured, instead of the material never preparing).
pub fn solid_white_image() -> Image {
    Image::new(
        Extent3d {
            width: 1,
            height: 1,
            depth_or_array_layers: 1,
        },
        bevy::render::render_resource::TextureDimension::D2,
        vec![255, 255, 255, 255],
        TextureFormat::Rgba8UnormSrgb,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    )
}

/// Normal tangente PLANA (0.5, 0.5, 1) em formato LINEAR — o fallback dos
/// slots sem `normal.ktx2` no asset root (e o repoint de cargas falhadas).
/// O shader faz `*2−1` e obtém (0, 0, 1): a normal geométrica, blend intacto.
pub fn flat_normal_image() -> Image {
    Image::new(
        Extent3d {
            width: 1,
            height: 1,
            depth_or_array_layers: 1,
        },
        bevy::render::render_resource::TextureDimension::D2,
        vec![128, 128, 255, 255],
        TextureFormat::Rgba8Unorm,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    )
}

/// Height PLANA (0.5) — o fallback dos slots sem `height.ktx2` no asset
/// root. Com alturas iguais o height-blend devolve 0.5 e os pesos originais
/// ficam intactos: mundos sem o mapa degradam para o blend de sempre.
pub fn flat_height_image() -> Image {
    Image::new(
        Extent3d {
            width: 1,
            height: 1,
            depth_or_array_layers: 1,
        },
        bevy::render::render_resource::TextureDimension::D2,
        vec![128, 128, 128, 255],
        TextureFormat::Rgba8Unorm,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    )
}

/// AO neutro (1.0) — o fallback dos slots sem `ao.ktx2`: oclusão nenhuma,
/// albedo intacto.
pub fn flat_ao_image() -> Image {
    Image::new(
        Extent3d {
            width: 1,
            height: 1,
            depth_or_array_layers: 1,
        },
        bevy::render::render_resource::TextureDimension::D2,
        vec![255, 255, 255, 255],
        TextureFormat::Rgba8Unorm,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    )
}

/// Converts the baked planes into four Bevy images (RGBA8 data channels,
/// mip chain + anisotropy via the shared texture patcher). Splat UVs live in
/// 0..1 world coverage, so ClampToEdge is correct here.
#[allow(dead_code)]
pub fn splat_images(map: &SplatMap) -> [Image; 4] {
    let size = map.size;
    let mut images = map.planes.clone().map(|data| {
        Image::new(
            Extent3d {
                width: size,
                height: size,
                depth_or_array_layers: 1,
            },
            bevy::render::render_resource::TextureDimension::D2,
            data,
            TextureFormat::Rgba8Unorm,
            RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
        )
    });
    for (image, data) in images.iter_mut().zip(&map.planes) {
        image.data = Some(data.clone());
        // Mip chain + linear/aniso-8 sampler; NOT world-tiled (0..1 UVs).
        patch_image(image, false);
    }
    images
}

// ─────────────────────────────────────────────── per-chunk splat (r6)
//
// O material por chunk pede um plano splat POR CHUNK: 4 pesos RGBA (as 4
// texturas do chunk, renormalizadas para somar 1) em vez dos 4 planos
// globais de 13 pesos. O campo de pesos continua a ser o MESMO
// ([`weights_at`]) — muda o empacotamento: por chunk escolhem-se as 4
// camadas de maior peso agregado e renormaliza-se texel a texel.

/// Splat texels per chunk edge (32² → 2 m/texel com o chunk default de 64 m,
/// a densidade do plano global 2048² de um mundo de 4 km).
/// Splat texels por borda de chunk (64² = 1 m/texel num chunk de 64 m — o
/// MESMO passo do LOD0 do terreno; 32² esbatia margens de lago/estradas a
/// 2 m). O bake corre para TODO o mundo no bootstrap: ×4 texels por chunk é
/// o custo do detalhe (4 k texels × ~4 k chunks), aceite em troca de
/// fronteiras de material nítidas.
pub const CHUNK_SPLAT_TEXELS: u32 = 64;

/// Baked splat of ONE chunk: the eight pool slots it renders with plus the
/// TWO RGBA8 weight planes (`size²` texels each, row-major; plane 0 =
/// slots 0–3 nos canais RGBA, plane 1 = slots 4–7, weights renormalized to
/// sum 1 across BOTH planes).
#[derive(Debug, Clone, PartialEq)]
pub struct ChunkSplat {
    pub slots: [usize; 8],
    pub size: u32,
    pub rgba: Vec<u8>,
    pub rgba2: Vec<u8>,
}

/// Bakes one chunk splat from a full weights grid (per-texel [`LAYER_COUNT`]
/// weights) plus the per-slot aggregate — the two passes of
/// [`generate_chunk_splats`] split so tests can drive a single chunk.
///
/// `force_rock`/`force_bed`/`force_shore` garantem que parede, leito e a
/// PRAIA DA MARGEM sobrevivem à eleição top-8: um texel a 100% seixo num
/// chunk de relva lê-se relva depois da renormalização — os overrides
/// existem porque essas camadas são as que o olho apanha (sob água
/// transparente, na parede a triplanar, na linha de areia da praia).
fn pack_chunk_splat(
    weights: &[[f32; LAYER_COUNT]],
    size: u32,
    force_rock: bool,
    force_bed: bool,
    force_shore: bool,
) -> ChunkSplat {
    let mut sums = [0.0f32; LAYER_COUNT];
    for w in weights {
        for (slot, &weight) in w.iter().enumerate() {
            sums[slot] += weight;
        }
    }
    // Top-8 by aggregate weight; order is descending so the dominant layer
    // rides channel R of plane 0 (and the repointing falls back to it).
    let mut slots: [usize; LAYER_COUNT] = core::array::from_fn(|i| i);
    slots.sort_by(|&a, &b| sums[b].total_cmp(&sums[a]));
    let mut selected: Vec<usize> = slots[..8].to_vec();
    // Overrides entram do FIM da tabela (os picks de menor peso) — num chunk
    // de gorge com lago ao pé rock/bed/sand coexistem em vez de um apagar o
    // outro. Evict só picks NATURAIS: um slot forçado por um override
    // anterior nunca é despejado por um seguinte.
    let forced = [SLOT_MOUNTAIN_STONE, SLOT_RIVERBED, SLOT_SAND];
    let wanted = [
        (force_rock, SLOT_MOUNTAIN_STONE),
        (force_bed, SLOT_RIVERBED),
        (force_shore, SLOT_SAND),
    ];
    for (needed, slot) in wanted {
        if needed && !selected.contains(&slot) {
            if selected.len() >= 8 {
                let pos = selected
                    .iter()
                    .rposition(|&s| !forced.contains(&s))
                    .unwrap_or(selected.len() - 1);
                selected.remove(pos);
            }
            selected.push(slot);
        }
    }
    let top: [usize; 8] = selected.try_into().expect("8 slots");

    let mut rgba = vec![0u8; (size * size * 4) as usize];
    let mut rgba2 = vec![0u8; (size * size * 4) as usize];
    for (i, w) in weights.iter().enumerate() {
        let mut picked = [0.0f32; 8];
        for (k, &slot) in top.iter().enumerate() {
            picked[k] = w[slot];
        }
        let total: f32 = picked.iter().sum();
        // Full fallback (a chunk outside every field): all weight on the
        // dominant slot of plane 0 — the ground never renders unblended
        // white. A normalização é CONJUNTA aos 8 pesos: renormalizar cada
        // plano por separado multiplicava texels de fronteira.
        let packed = if total < 1e-5 {
            [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        } else {
            picked.map(|weight| weight / total)
        };
        for (channel, &weight) in packed.iter().enumerate() {
            // 251 keeps the u8 sum (with rounding) ≤ 255.
            let byte = (weight * 251.0).round().clamp(0.0, 251.0) as u8;
            if channel < 4 {
                rgba[i * 4 + channel] = byte;
            } else {
                rgba2[i * 4 + channel - 4] = byte;
            }
        }
    }
    ChunkSplat {
        slots: top,
        size,
        rgba,
        rgba2,
    }
}

/// Bakes the per-chunk splat planes of a whole carved world: one entry per
/// `rows × rows` chunk of `chunk_edge` meters, row-major from (-X,-Z).
///
/// Same weight field as the retired global planes ([`generate_splats`]) —
/// see [`weights_at`]; chunks whose aggregate picks differ render different
/// layer sets, which is the point of per-chunk materials.
pub fn generate_chunk_splats(
    grid: &BrushGrid,
    water: &[WaterBody],
    roads: &[RoadPath],
    params: &SplatParams,
    chunk_edge: f32,
    rows: u32,
    cliff: Option<&crate::terrain::cliffs::CliffMask>,
) -> Vec<ChunkSplat> {
    let world = grid.world_size();
    let size = CHUNK_SPLAT_TEXELS;
    let texel = chunk_edge / size as f32;
    let shore = params
        .tuning
        .shore_width
        .unwrap_or(params.shore_width)
        .max(0.5);
    let water_boxes: Vec<(Bounds, &WaterBody)> = water
        .iter()
        .filter(|b| b.radius > 0.0 || !b.stations.is_empty())
        .map(|b| (water_bounds(b, shore), b))
        .collect();
    let road_boxes: Vec<(Bounds, &RoadPath)> = roads
        .iter()
        .filter(|r| !r.bridge)
        .map(|r| (road_bounds(r, 4.0), r))
        .collect();
    let epsilon = grid.texel();
    let half = world * 0.5;
    let mut ctx = TexelCtx {
        params,
        shore,
        water_boxes: &water_boxes,
        road_boxes: &road_boxes,
        cliff,
    };

    let mut out = Vec::with_capacity((rows * rows) as usize);
    for cz in 0..rows {
        for cx in 0..rows {
            let origin = Vec2::new(
                -half + cx as f32 * chunk_edge,
                -half + cz as f32 * chunk_edge,
            );
            let mut weights = vec![[0.0f32; LAYER_COUNT]; (size * size) as usize];
            for tz in 0..size {
                let z = origin.y + (tz as f32 + 0.5) * texel;
                for tx in 0..size {
                    let x = origin.x + (tx as f32 + 0.5) * texel;
                    let y = grid.sample(x, z);
                    let normal = grid.sample_normal(x, z, epsilon);
                    weights[(tz * size + tx) as usize] =
                        weights_at(x, z, y, normal.y, grid.max_height(), &mut ctx);
                }
            }
            // Chunks com paredes na máscara GARANTEM a mountain_stone entre
            // os 4 slots — sem ela o índice rock do material ficaria -1 e o
            // gate triplanar não tinha textura nenhuma para projetar.
            // Grelha 4×4 sobre o chunk INTEIRO (±10% para lá da borda, para
            // a pedra atravessar a emenda): as 4 sondas antigas duplicavam-se
            // e concentravam-se na metade min — paredes no terço max nunca
            // eram apanhadas e liam-se plastilina em faixas alinhadas à
            // grelha de chunks. Primeiro hit sai (chunks de relva não pagam
            // as 16 sondas).
            let has_core = cliff.is_some_and(|m| {
                for ix in 0..4 {
                    for iz in 0..4 {
                        let fx = -0.1 + 0.4 * ix as f32;
                        let fz = -0.1 + 0.4 * iz as f32;
                        if m.is_core_at(Vec2::new(
                            origin.x + chunk_edge * fx,
                            origin.y + chunk_edge * fz,
                        )) {
                            return true;
                        }
                    }
                }
                false
            });
            // Um texel a ≥25% seixo lê-se leito: o chunk tem de carregar a
            // textura mesmo que o agregado do leito perca a eleição top-8.
            let has_bed = weights.iter().any(|w| w[SLOT_RIVERBED] >= 0.25);
            // Idem para a PRAIA: a areia da margem perdia a eleição em
            // chunks de relva e a praia quebrava em costuras retas de chunk.
            let has_shore = weights.iter().any(|w| w[SLOT_SAND] >= 0.25);
            out.push(pack_chunk_splat(&weights, size, has_core, has_bed, has_shore));
        }
    }
    out
}

/// One chunk's splat plane as a Bevy image (mip chain + linear/aniso via the
/// shared patcher; ClampToEdge — UVs are 0..1 over the chunk). `plane` 0/1:
/// os canais RGBA do plano cobrem os slots 0–3 / 4–7 do [`ChunkSplat`].
pub fn chunk_splat_plane_image(splat: &ChunkSplat, plane: usize) -> Image {
    let rgba = if plane == 0 { &splat.rgba } else { &splat.rgba2 };
    let mut image = Image::new(
        Extent3d {
            width: splat.size,
            height: splat.size,
            depth_or_array_layers: 1,
        },
        bevy::render::render_resource::TextureDimension::D2,
        rgba.clone(),
        bevy::render::render_resource::TextureFormat::Rgba8Unorm,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    );
    image.data = Some(rgba.clone());
    patch_image(&mut image, false);
    image
}

/// Plane 0 do splat de chunk (slots 0–3).
pub fn chunk_splat_image(splat: &ChunkSplat) -> Image {
    chunk_splat_plane_image(splat, 0)
}

/// Plane 1 do splat de chunk (slots 4–7).
pub fn chunk_splat2_image(splat: &ChunkSplat) -> Image {
    chunk_splat_plane_image(splat, 1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::brush::BrushGrid;
    use crate::terrain::heightmap::HeightMapU16;
    use crate::terrain::water::LakeShape;

    /// Flat 64 m world with one deep lake and one river.
    struct World {
        grid: BrushGrid,
        water: Vec<WaterBody>,
        roads: Vec<RoadPath>,
    }

    fn flat_world() -> World {
        let map = HeightMapU16 {
            width: 65,
            depth: 65,
            data: vec![u16::MAX / 2; 65 * 65], // 25 m of 50 m peak
        };
        let mut grid = BrushGrid::from_height_map(&map, 64.0, 50.0, 1.0).expect("flat grid");
        // Carve a lake (returns the registry body).
        let lake = crate::terrain::water::carve_lake(
            &mut grid,
            &crate::terrain::LakeSpec {
                at: Vec2::new(-20.0, -10.0),
                radius: 8.0,
                depth: 3.0,
                ..crate::terrain::LakeSpec::default()
            },
            0,
        )
        .expect("lake carved");
        let river = crate::terrain::water::carve_river(
            &mut grid,
            &crate::terrain::RiverSpec {
                path: vec![Vec2::new(10.0, -32.0), Vec2::new(30.0, 32.0)],
                width: 6.0,
                depth: 2.0,
                ..crate::terrain::RiverSpec::default()
            },
            1,
            &[],
        )
        .expect("river carved");
        World {
            grid,
            water: vec![lake, river],
            roads: Vec::new(),
        }
    }

    fn params() -> SplatParams {
        SplatParams {
            shore_width: 4.0,
            snow_height: 0.75,
            seed: 7,
            texel: 1.0,
            biomes: BiomeField::default(),
            tuning: SplatTuning::default(),
        }
    }

    fn weights_of(world: &World, x: f32, z: f32) -> [f32; LAYER_COUNT] {
        let boxes_w: Vec<(Bounds, &WaterBody)> = world
            .water
            .iter()
            .map(|b| (water_bounds(b, 4.0), b))
            .collect();
        let boxes_r: Vec<(Bounds, &RoadPath)> = Vec::new();
        let mut ctx = TexelCtx {
            params: &params(),
            shore: 4.0,
            water_boxes: &boxes_w,
            road_boxes: &boxes_r,
            cliff: None,
        };
        let y = world.grid.sample(x, z);
        let ny = world.grid.sample_normal(x, z, 1.0).y;
        weights_at(x, z, y, ny, world.grid.max_height(), &mut ctx)
    }

    #[test]
    fn test_weights_sum_to_one_everywhere() {
        let world = flat_world();
        for z in -31..31 {
            for x in -31..31 {
                let w = weights_of(&world, x as f32, z as f32);
                let sum: f32 = w.iter().sum();
                assert!(
                    (sum - 1.0).abs() < 1e-3,
                    "weights at ({x},{z}) sum to {sum}: {w:?}"
                );
                assert!(
                    w.iter().all(|v| *v >= -1e-4 && *v <= 1.0 + 1e-4),
                    "weights at ({x},{z}) out of range: {w:?}"
                );
            }
        }
    }

    #[test]
    fn test_lake_floor_is_sandy_and_shore_blends() {
        let world = flat_world();
        let lake = &world.water[0];
        // Center of the lake: deep water → the bed (pebbles + gravel mix)
        // takes over from sand.
        let floor = weights_of(&world, lake.at.x, lake.at.y);
        let bed_total = floor[SLOT_RIVERBED] + floor[SLOT_GRAVEL];
        assert!(
            bed_total > 0.55,
            "deep lake floor must read riverbed, got {bed_total}"
        );
        let sand_total = floor[SLOT_SAND] + floor[SLOT_DESERT_SAND];
        assert!(
            sand_total < 0.45,
            "deep floor must not stay sandy, got {sand_total}"
        );

        // Walk out from the waterline: sand must fade monotonically to < 0.1
        // before 2× the shore width.
        // A areia ancora-se na LINHA DE ÁGUA REAL (contorno × mirror_reach),
        // que fica além do raio nominal — o walk pode SUBIR até cruzar a
        // linha; a partir daí desvanece a monotonamente.
        let mut prev = 0.0f32;
        let mut rising = true;
        let mut peak_seen = false;
        let mut passed = false;
        for d in [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0] {
            let p = lake.at + Vec2::new(lake.radius.max(1.0) + d, 0.0);
            let w = weights_of(&world, p.x, p.y);
            let sand_total = w[SLOT_SAND] + w[SLOT_DESERT_SAND];
            if rising {
                if sand_total < prev - 1e-3 {
                    rising = false;
                    peak_seen = prev > 0.3;
                }
            } else {
                assert!(
                    sand_total <= prev + 1e-3,
                    "sand must fade monotonically after the waterline: {sand_total} after {prev} at {d} m"
                );
            }
            prev = prev.max(sand_total);
            if d > 4.0 && sand_total < 0.1 {
                passed = true;
            }
        }
        assert!(peak_seen, "sand must peak at the real waterline");
        assert!(passed, "sand must vanish past the shore band");
    }

    /// O leito entra em manchas: nem seixo a 1 no fundo inteiro, nem zero.
    #[test]
    fn test_riverbed_comes_in_patches() {
        let world = flat_world();
        let lake = &world.water[0];
        let mut max_bed = 0.0f32;
        let mut min_bed = 1.0f32;
        for dz in -5..=5 {
            for dx in -5..=5 {
                let p = lake.at + Vec2::new(dx as f32 * 1.3, dz as f32 * 1.3);
                let w = weights_of(&world, p.x, p.y);
                let bed = w[SLOT_RIVERBED];
                max_bed = max_bed.max(bed);
                min_bed = min_bed.min(bed);
            }
        }
        assert!(
            max_bed > 0.55 && min_bed < 0.45,
            "bed must vary between pebble patches and sand: {min_bed}..{max_bed}"
        );
    }

    #[test]
    fn test_shore_has_blend_midpoint() {
        // The user contract: the shore BLENDs sand into the ground texture —
        // somewhere in the band the weights must be mixed, not stepped.
        let world = flat_world();
        let lake = &world.water[0];
        let mut mixed = false;
        for d in 0..=20 {
            let p = lake.at + Vec2::new(lake.radius.max(1.0) + d as f32 * 0.25, 0.0);
            let w = weights_of(&world, p.x, p.y);
            let sand_total = w[SLOT_SAND] + w[SLOT_DESERT_SAND];
            let grass_total = w[SLOT_GRASS] + w[SLOT_VALE_GRASS];
            if sand_total > 0.15 && sand_total < 0.85 && grass_total > 0.1 {
                mixed = true;
            }
        }
        assert!(mixed, "shore band must contain a genuine sand↔terrain mix");
    }

    #[test]
    fn test_cliffs_take_mountain_stone() {
        let boxes_w: Vec<(Bounds, &WaterBody)> = Vec::new();
        let boxes_r: Vec<(Bounds, &RoadPath)> = Vec::new();
        let mut ctx = TexelCtx {
            params: &params(),
            shore: 4.0,
            water_boxes: &boxes_w,
            road_boxes: &boxes_r,
            cliff: None,
        };
        // Vertical wall, mid altitude: stone wins, grass disappears.
        let w = weights_at(0.0, 0.0, 20.0, 0.1, 50.0, &mut ctx);
        assert!(w[SLOT_MOUNTAIN_STONE] > 0.9, "cliff: {w:?}");
        // Gentle ground: no stone.
        let w = weights_at(0.0, 0.0, 20.0, 1.0, 50.0, &mut ctx);
        assert!(w[SLOT_MOUNTAIN_STONE] < 1e-4, "flat: {w:?}");
    }

    /// Authored talus aprons paint gravel over stone (dirty scree) and kill
    /// grass/snow — even on gentle ground the splat would otherwise give
    /// back to grass. O aluguel é pago via `CliffBand::build` + mask
    /// `add_authored_bands`, o caminho VIVO do mundo voxel.
    #[test]
    fn test_talus_apron_paints_gravel() {
        use crate::terrain::brush::BrushGrid;
        use crate::terrain::cliffs::CliffSide;
        use crate::terrain::voxel::CliffBand;
        // Natural step + talus apron on the drop side.
        let mut grid =
            BrushGrid::new(vec![0u16; 128 * 128], 128, 128, 128.0, 50.0, 1.0).expect("grid");
        grid.begin_stroke("step");
        for z in 0..128 {
            for x in 0..128 {
                let cx = grid.cell_center(x, z).x;
                let h = if cx < 0.0 { 20.0 } else { 2.0 };
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        let spec = crate::terrain::CliffSpec {
            path: vec![
                bevy::math::Vec2::new(0.0, -40.0),
                bevy::math::Vec2::new(0.0, 40.0),
            ],
            side: CliffSide::Auto,
            noise: 0.0,
            talus: true,
            ..crate::terrain::CliffSpec::default()
        };
        let band = CliffBand::build(&spec, &grid, grid.texel()).expect("band");
        let mask = crate::terrain::cliffs::CliffMask::build_with(&grid, 50.0, 120.0, 4.0, 8.0);
        let mut mask = mask;
        mask.add_authored_bands(&[band]);
        assert!(
            mask.is_talus_at(bevy::math::Vec2::new(9.0, 0.0)),
            "the apron rasterized where the test samples"
        );
    }

    #[test]
    fn test_snow_above_snow_height() {
        let boxes_w: Vec<(Bounds, &WaterBody)> = Vec::new();
        let boxes_r: Vec<(Bounds, &RoadPath)> = Vec::new();
        let mut ctx = TexelCtx {
            params: &params(),
            shore: 4.0,
            water_boxes: &boxes_w,
            road_boxes: &boxes_r,
            cliff: None,
        };
        // Above the 0.75 line, flat: snow dominates whatever patches want.
        let w = weights_at(0.0, 0.0, 42.0, 1.0, 50.0, &mut ctx);
        assert!(w[SLOT_SNOW_PEAK] > 0.9, "peak: {w:?}");
        // Below it: none.
        let w = weights_at(0.0, 0.0, 20.0, 1.0, 50.0, &mut ctx);
        assert!(w[SLOT_SNOW_PEAK] < 1e-4, "low: {w:?}");
    }

    /// Piso de lago FUNDO acima da linha de neve: a neve submersa derrete —
    /// sem a supressão o orçamento ficava NEGATIVO e `scale = budget/used`
    /// invertia os pesos restantes (neve/seixo sem areia nem lama).
    #[test]
    fn test_snow_melts_on_flooded_texels() {
        // Espelho a 40 m: um texel a 38.5 m tem h = 0.77 (> neve 0.75) e
        // profundidade 1.5 m (leito total) — a combinação que partia o budget.
        let body = WaterBody {
            kind: WaterKind::Lake,
            at: Vec2::new(0.0, 0.0),
            radius: 10.0,
            carve_radius: 12.5,
            water_y: 40.0,
            mirror_reach: 1.0,
            shape: LakeShape::default(),
            stations: Vec::new(),
            surface_y: Vec::new(),
            water_width: 0.0,
            half_width: Vec::new(),
            depths: Vec::new(),
            cascades: Vec::new(),
        };
        let boxes_w: Vec<(Bounds, &WaterBody)> = vec![(water_bounds(&body, 4.0), &body)];
        let boxes_r: Vec<(Bounds, &RoadPath)> = Vec::new();
        let mut ctx = TexelCtx {
            params: &params(),
            shore: 4.0,
            water_boxes: &boxes_w,
            road_boxes: &boxes_r,
            cliff: None,
        };
        let w = weights_at(0.0, 0.0, 38.5, 1.0, 50.0, &mut ctx);
        assert!(
            w[SLOT_SNOW_PEAK] < 1e-4,
            "snow must melt underwater: {:?}",
            w[SLOT_SNOW_PEAK]
        );
        assert!(
            w.iter().all(|v| *v >= -1e-4),
            "no negative weights on a flooded snow texel: {w:?}"
        );
        let sum: f32 = w.iter().sum();
        assert!((sum - 1.0).abs() < 1e-3, "weights still close to 1: {sum}");
        assert!(
            w[SLOT_RIVERBED] + w[SLOT_GRAVEL] > 0.55,
            "deep flooded floor reads as bed: {:?}",
            w
        );
    }

    #[test]
    fn test_deterministic_and_seed_shifts_noise() {
        let world = flat_world();
        let a = weights_of(&world, 3.0, 4.0);
        let b = weights_of(&world, 3.0, 4.0);
        assert_eq!(a, b, "same inputs → same weights");

        let mut n_a = 0.0;
        let mut n_b = 0.0;
        for z in -20..20 {
            for x in -20..20 {
                n_a += value_noise(x as f32 * 0.1, z as f32 * 0.1, 1, 0);
                n_b += value_noise(x as f32 * 0.1, z as f32 * 0.1, 2, 0);
            }
        }
        assert!(
            (n_a - n_b).abs() > 1.0,
            "different seeds must shift the noise fields"
        );
    }

    #[test]
    fn test_generate_splats_packs_planes_and_images() {
        let world = flat_world();
        let map = generate_splats(&world.grid, &world.water, &world.roads, &params());
        assert_eq!(map.size, 64, "1 m texel over a 64 m world");
        for plane in &map.planes {
            assert_eq!(plane.len(), 64 * 64 * 4);
        }
        // The lake sits at (-20,-10) — texel ((x+32), (z+32)) = (12, 22).
        // Deep floor: bed (pebbles plane 3 R + gravel plane 1 G) over sand.
        let li = (22 * 64 + 12) * 4;
        let bed_total = f32::from(map.planes[3][li]) + f32::from(map.planes[1][li + 1]);
        assert!(
            bed_total > 140.0,
            "lake floor texel must be bed: {bed_total}"
        );
        // All weights ≤ 251 so plane sums stay in u8 territory per texel.
        assert!(map.planes.iter().all(|p| p.iter().all(|&v| v <= 251)));

        let images = splat_images(&map);
        assert_eq!(images.len(), 4);
        for image in &images {
            assert_eq!(image.width(), 64);
            assert!(image.texture_descriptor.mip_level_count > 1, "mips baked");
        }
    }

    #[test]
    fn test_pool_aliases_resolve_to_pool_paths() {
        // RELATIVO ao `terrain_textures_dir` do config do jogo — o prefixo
        // vem de `config::GameConfig::terrain_albedo`.
        assert_eq!(pool_albedo("grass").as_deref(), Some("grass/albedo.ktx2"));
        assert_eq!(
            pool_normal("snow_peak").as_deref(),
            Some("snow_peak/normal.ktx2")
        );
        assert!(pool_albedo("not_a_texture").is_none());
        assert_eq!(DEFAULT_LAYERS.len(), LAYER_COUNT);
    }

    /// O plano por chunk escolhe as 4 camadas dominantes e renormaliza os
    /// pesos a somar ~1 por texel; chunks diferentes podem escolher slots
    /// diferentes (o ponto do material por chunk).
    #[test]
    fn test_generate_chunk_splats_renormalizes_and_picks_top8() {
        let world = flat_world();
        let chunk_edge = 32.0; // flat_world é um mundo 64 m → 2×2 chunks
        let rows = 2;
        let splats = generate_chunk_splats(
            &world.grid,
            &world.water,
            &world.roads,
            &params(),
            chunk_edge,
            rows,
            None,
        );
        assert_eq!(splats.len(), (rows * rows) as usize);
        for splat in &splats {
            assert_eq!(splat.size, CHUNK_SPLAT_TEXELS);
            assert_eq!(
                splat.rgba.len(),
                (CHUNK_SPLAT_TEXELS * CHUNK_SPLAT_TEXELS * 4) as usize
            );
            assert_eq!(splat.rgba2.len(), splat.rgba.len());
            assert_eq!(splat.slots.len(), 8);
            assert!(splat.slots.iter().all(|&s| s < LAYER_COUNT));
            assert!(splat.rgba.iter().all(|&v| v <= 251));
            assert!(splat.rgba2.iter().all(|&v| v <= 251));
            // Cada texel: os 8 pesos renormalizados (planos 0+1 SOMADOS)
            // fecham ~1 — o clamp de 251 por canal deixa a soma u8 abaixo
            // de 255. (Um plano sozinho pode ficar a 0: o peso do texel
            // vive inteiro nos slots 4–7.)
            for i in 0..(splat.size * splat.size) as usize {
                let sum: u16 = (0..4)
                    .map(|c| splat.rgba[i * 4 + c] as u16 + splat.rgba2[i * 4 + c] as u16)
                    .sum();
                assert!(sum >= 245, "texel {i} soma {sum} — pesos têm de fechar ~1");
            }
        }
        // O chunk (0,0) contém o lago (-20,-10); o (1,1) é relva — a AREIA
        // da margem é contrato forçado (costura de praia não pode quebrar
        // entre chunks) e o leito também; o chunk seco fica nas relvas.
        let water_chunk = &splats[0]; // cz=0,cx=0 → canto -X/-Z
        let far_chunk = &splats[3]; // cz=1,cx=1 → canto +X/+Z
        assert!(
            water_chunk.slots.contains(&SLOT_SAND),
            "chunk do lago deve carregar areia da margem: {:?}",
            water_chunk.slots
        );
        assert!(
            water_chunk.slots.contains(&SLOT_SAND) || water_chunk.slots.contains(&SLOT_RIVERBED),
            "chunk do lago deve carregar areia/leito: {:?}",
            water_chunk.slots
        );
        assert!(
            far_chunk.slots.contains(&SLOT_GRASS) || far_chunk.slots.contains(&SLOT_VALE_GRASS),
            "chunk de relva: {:?}",
            far_chunk.slots
        );

        let image = chunk_splat_image(&splats[0]);
        assert_eq!(image.width(), CHUNK_SPLAT_TEXELS);
        assert!(image.texture_descriptor.mip_level_count > 1, "mips baked");
        let image2 = chunk_splat2_image(&splats[0]);
        assert_eq!(image2.width(), CHUNK_SPLAT_TEXELS);
    }
}
