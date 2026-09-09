//! `<Sky>` procedural dome: atmospheric gradient, sun disc + glow, FBM
//! clouds, moon, stars, nebula, aurora and meteors — a custom WGSL fragment
//! shader on a camera-following inverted sphere.
//!
//! Dois modelos de céu, seleccionados por `<Sky model="analytic|nishita">`
//! (default `analytic`; override `VIBER_SKY_MODEL`):
//! - **analytic** — o gradiente estilizado por paleta (legado, intacto).
//! - **nishita** — scattering físico Rayleigh + Mie portado do
//!   `bevy_atmosphere` v0.13.0 (ver [`crate::sky_nishita`]): o gradiente e o
//!   glow do sol passam a sair do raymarch, e TUDO o resto (nuvens, lua,
//!   estrelas, via láctea, aurora, meteoros) compõe-se por cima no mesmo
//!   fragment shader.
//!
//! ARCHITECTURE NOTE — duas vias de configuração:
//!
//! 1. **Consts especializadas.** `viber run` reescreve o bloco CONFIG de
//!    `shaders/sky.wgsl` com os valores do `<Sky>`/`<DayCycle>`/`<Weather>`
//!    do mundo antes de o renderer o carregar (nuvens, estrelas, aurora…).
//! 2. **Storage per-frame** ([`SkyUniform`]). A hora do mundo NÃO
//!    pode vir de `globals.time`: `viber.debug.set_clock` (e qualquer
//!    lógica de jogo que mexa no relógio) escreve `DayCycleState`, que o
//!    tempo do motor ignora — era por isso que às 23:02 ainda se via o disco
//!    solar e a hora dourada tinha céu de noite. O sol, a lua e a paleta da
//!    hora chegam agora do [`crate::worldsys::AtmosphereState`], a mesma que
//!    alimenta a névoa e o grading.
//!
//! POR QUE STORAGE E NÃO UNIFORM (r1): com `#[uniform(0)]` o
//! `opaque_mesh_pipeline` morre em validação quando coexiste com outro
//! material cujo group(2) é storage (a água — verifico no mundo /tmp);
//! com `#[storage(0, read_only)] Handle<ShaderBuffer>` o pipeline é válido.
//!
//! REFRESH DO STORAGE: `set_data` atualiza o buffer existente a cada frame
//! pelo evento `Modified`. Como este caminho já perdeu atualizações sob
//! carga, a cada 30 frames publica também um `ShaderBuffer` novo (96 B) e
//! troca o handle no material existente. O evento `Added` força um novo
//! upload sem criar assets a cada frame; o buffer anterior é libertado
//! quando deixa de ter handles.

use bevy::asset::RenderAssetUsages;
use bevy::light::NotShadowCaster;
use bevy::math::{Vec3, Vec4};
use bevy::pbr::Material;
use bevy::prelude::*;
use bevy::render::mesh::PrimitiveTopology;
use bevy::render::render_resource::AsBindGroup;
use bevy::render::storage::ShaderBuffer;
use bevy::shader::ShaderRef;

/// Template WGSL do céu (defaults; `viber run` reescreve o bloco CONFIG com
/// os valores do mundo antes de o renderer o carregar).
pub const SKY_WGSL: &str = include_str!("sky.wgsl");

/// Ganho de saída do domo (o WGSL multiplica por isto no fim). **1.0** —
/// o r7 pôs 400 acreditando que a exposição física da câmara (EV100 ~9.7)
/// esmagava a paleta; MAS a exposição vive no path PBR do `StandardMaterial`
/// — o domo é um material CUSTOM e NUNCA a recebe. Com 400 o céu inteiro ia
/// a TMc=1.0 = o "retângulo de branco" do r7/r8. O FOG (`ambient.rs`) usa a
/// mesma escala (paleta raw) para se fundir com o horizonte do domo.
pub const SKY_RADIANCE: f32 = 1.0;

const CONFIG_BEGIN: &str = "// === WORLD CONFIG";
const CONFIG_END: &str = "// === END WORLD CONFIG ===";

/// Modelo visual do céu — `analytic` é o gradiente estilizado por paleta
/// (comportamento histórico), `nishita` é o scattering físico do
/// `bevy_atmosphere` ([`crate::sky_nishita`]) com as mesmas features por
/// cima. O `VIBER_SKY_MODEL` sobrepõe o attr do XML.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SkyModel {
    #[default]
    Analytic,
    Nishita,
}

impl SkyModel {
    pub fn from_attr(value: Option<&str>) -> Self {
        match value.map(str::trim) {
            Some(v) if v.eq_ignore_ascii_case("nishita") => SkyModel::Nishita,
            Some(v) if v.eq_ignore_ascii_case("analytic") => SkyModel::Analytic,
            _ => SkyModel::Analytic,
        }
    }
}

/// O modelo do mundo em runtime, para o IBL ([`crate::ibl`]) saber que
/// radiância pintar no cubemap. Inserido como resource no `run` depois da
/// resolução do attr + env.
#[derive(Debug, Clone, Resource)]
pub struct SkyModelState {
    pub model: SkyModel,
    pub nishita: crate::sky_nishita::NishitaModel,
}

impl SkyModelState {
    pub fn from_config(config: &SkyConfig) -> Self {
        Self {
            model: config.model,
            nishita: config.nishita_model(),
        }
    }
}

impl Default for SkyModelState {
    fn default() -> Self {
        Self {
            model: SkyModel::Analytic,
            nishita: crate::sky_nishita::NishitaModel::default(),
        }
    }
}

/// Marker for the sky dome entity.
#[derive(Debug, Component)]
pub struct SkyDome;

/// Estado per-frame do céu, empacotado para o shader.
///
/// **Storage, não uniform** — ver a nota no topo do ficheiro (coexistência
/// com o material de água no 0.19). O binding WGSL correspondente é
/// `var<storage, read>`; o struct é só `vec4`s, pelo que o layout std430
/// não tem surpresas de alinhamento.
#[derive(Debug, Clone, Copy, bevy::render::render_resource::ShaderType)]
pub struct SkyUniform {
    /// `xyz` = direção PARA o sol; `w` = fator dia (1 dia, 0 noite).
    pub sun: Vec4,
    /// `xyz` = direção PARA a lua; `w` = fator noite.
    pub moon: Vec4,
    /// `rgb` = cor do zénite; `w` = fator hora dourada.
    pub zenith: Vec4,
    /// `rgb` = cor do horizonte; `w` = delta de cobertura de nuvens do Weather.
    pub horizon: Vec4,
    /// `rgb` = tinta do sol (âmbar rasante → branco alto); `w` = tempo (s).
    pub sun_tint: Vec4,
    /// `x`,`y` = vento; `z` = reservado (0); `w` = minuto do dia.
    pub params: Vec4,
}

impl Default for SkyUniform {
    fn default() -> Self {
        Self {
            sun: Vec4::new(0.0, 1.0, 0.0, 1.0),
            moon: Vec4::new(0.0, -1.0, 0.0, 0.0),
            zenith: Vec4::new(0.085, 0.255, 0.62, 0.0),
            // w neutral: `horizon.w` é um DELTA somado à cobertura das consts;
            // o default tem de ser 0 para não alterar o céu antes do 1.º drive.
            horizon: Vec4::new(0.60, 0.755, 0.90, 0.0),
            sun_tint: Vec4::new(1.0, 0.96, 0.88, 0.0),
            // z reservado (o WGSL não lê); w = minuto do dia.
            params: Vec4::new(0.7, 0.25, 0.0, 480.0),
        }
    }
}

/// Custom sky material: a config do mundo continua nas consts WGSL
/// especializadas; o que muda a cada frame (hora, sol, lua, nuvens) chega
/// por este storage buffer.
#[derive(Debug, Clone, Asset, TypePath, AsBindGroup)]
pub struct SkyMaterial {
    #[storage(0, read_only)]
    pub data: Handle<ShaderBuffer>,
}

impl Material for SkyMaterial {
    fn fragment_shader() -> ShaderRef {
        "shaders/sky.wgsl".into()
    }

    // The dome is seen from the inside. Bevy 0.19 no longer exposes
    // `cull_mode` on `Material` (it moved into the specialization pass), so
    // the dome mesh itself is wound inward instead.
}

/// Per-world sky configuration extracted from the world XML.
#[derive(Debug, Clone)]
pub struct SkyConfig {
    /// `true` when a `<DayCycle>` drives the sun (shader derives the sun
    /// position/night from `Globals.time`); otherwise a static `<Sky>` sun.
    pub drive: bool,
    pub clock_start: f32,
    pub clock_speed: f32,
    pub dawn: f32,
    pub dusk: f32,
    pub max_elev: f32,
    pub az_base: f32,
    /// Static sun used when there is no `<DayCycle>`.
    pub sun_elevation: f32,
    pub sun_azimuth: f32,
    pub mie: f32,
    pub mie_g: f32,
    pub sun_intensity: f32,
    pub cloud_coverage: f32,
    pub cloud_density: f32,
    pub cloud_elevation: f32,
    pub star_density: f32,
    pub aurora: f32,
    pub nebula: f32,
    pub wind: [f32; 2],
    /// Modelo do céu: `analytic` (gradiente estilizado atual) ou `nishita`
    /// (scattering físico Rayleigh+Mie do bevy_atmosphere). `<Sky model=…>`
    /// ou `VIBER_SKY_MODEL`.
    pub model: SkyModel,
    /// Multiplicador do coeficiente Rayleigh físico (attr `rayleigh`).
    pub rayleigh: f32,
    /// Multiplicador do coeficiente Mie físico (attr `nishita-mie`).
    pub nishita_mie: f32,
}

impl Default for SkyConfig {
    fn default() -> Self {
        Self {
            drive: false,
            clock_start: 480.0,
            clock_speed: 1.2,
            dawn: 330.0,
            dusk: 1170.0,
            max_elev: 62.0,
            az_base: 205.0,
            sun_elevation: 17.0,
            sun_azimuth: 205.0,
            mie: 0.0035,
            mie_g: 0.8,
            sun_intensity: 2.6,
            cloud_coverage: 0.45,
            cloud_density: 0.32,
            cloud_elevation: 0.55,
            star_density: 1.0,
            aurora: 0.6,
            nebula: 0.5,
            wind: [0.7, 0.25],
            model: SkyModel::Analytic,
            rayleigh: 1.0,
            nishita_mie: 1.0,
        }
    }
}

impl SkyConfig {
    /// Scan the parsed world for `<Sky>`, `<DayCycle>` and `<Weather>` and
    /// merge their values over the defaults.
    pub fn from_world(entities: &[crate::recipes::EntitySpec]) -> SkyConfig {
        let mut config = SkyConfig::default();
        walk_entities(entities, &mut config);
        config
    }

    /// Instância do modelo físico para o IBL — os MESMOS coeficientes que o
    /// bloco CONFIG emite para o WGSL (o ganho vem de
    /// [`crate::sky_nishita::NISHITA_GAIN`], partilhado).
    pub fn nishita_model(&self) -> crate::sky_nishita::NishitaModel {
        crate::sky_nishita::NishitaModel::from_multipliers(
            self.rayleigh,
            self.nishita_mie,
            self.mie_g,
            self.sun_intensity,
        )
    }

    /// Rewrite the CONFIG const block of the shader template with this
    /// world's values (the rest of the template is untouched).
    pub fn render_world_shader(&self) -> String {
        let w = self.wind;
        let rayleigh = crate::sky_nishita::NishitaModel::from_multipliers(
            self.rayleigh,
            self.nishita_mie,
            self.mie_g,
            self.sun_intensity,
        )
        .rayleigh;
        let block = format!(
            "{CONFIG_BEGIN} (generated by `viber run` — edit the XML, not this block) ===\n\
const CFG_DRIVE: f32 = {};\n\
const CFG_CLOCK_START: f32 = {};\n\
const CFG_CLOCK_SPEED: f32 = {};\n\
const CFG_DAWN: f32 = {};\n\
const CFG_DUSK: f32 = {};\n\
const CFG_MAX_ELEV: f32 = {};\n\
const CFG_AZ_BASE: f32 = {};\n\
const CFG_SUN_ELEV: f32 = {};\n\
const CFG_SUN_AZ: f32 = {};\n\
const CFG_MIE: f32 = {};\n\
const CFG_MIE_G: f32 = {};\n\
const CFG_SUN_INTENSITY: f32 = {};\n\
const CFG_CLOUD_COVERAGE: f32 = {};\n\
const CFG_CLOUD_DENSITY: f32 = {};\n\
const CFG_CLOUD_ELEVATION: f32 = {};\n\
const CFG_STAR_DENSITY: f32 = {};\n\
const CFG_AURORA: f32 = {};\n\
const CFG_NEBULA: f32 = {};\n\
const CFG_WIND_X: f32 = {};\n\
const CFG_WIND_Z: f32 = {};\n\
const CFG_SKY_MODEL: f32 = {};\n\
const CFG_NISHITA_GAIN: f32 = {};\n\
const CFG_NISHITA_RAYLEIGH: vec3<f32> = vec3({}, {}, {});\n\
const CFG_NISHITA_MIE: f32 = {};\n\
const CFG_NISHITA_NIGHT_FLOOR: f32 = {};\n\
{CONFIG_END}",
            self.drive as i32,
            self.clock_start,
            self.clock_speed,
            self.dawn,
            self.dusk,
            self.max_elev,
            self.az_base,
            self.sun_elevation,
            self.sun_azimuth,
            self.mie,
            self.mie_g,
            self.sun_intensity,
            self.cloud_coverage,
            self.cloud_density,
            self.cloud_elevation,
            self.star_density,
            self.aurora,
            self.nebula,
            w[0],
            w[1],
            match self.model {
                SkyModel::Analytic => 0,
                SkyModel::Nishita => 1,
            },
            crate::sky_nishita::NISHITA_GAIN,
            rayleigh[0],
            rayleigh[1],
            rayleigh[2],
            crate::sky_nishita::MIE_BASE * self.nishita_mie,
            crate::sky_nishita::NIGHT_FLOOR,
        );
        let template = SKY_WGSL;
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

fn walk_entities(specs: &[crate::recipes::EntitySpec], config: &mut SkyConfig) {
    for spec in specs {
        match &spec.kind {
            // <Sky> é parseado como HudElement (legado do plumbing que
            // alimenta build_sky) — os attrs chegam aqui inteiros.
            crate::recipes::EntityKind::HudElement { tag, attrs } if tag == "sky" => {
                let f = |name: &str, default: f32| -> f32 {
                    attrs
                        .iter()
                        .find(|(k, _)| k == name)
                        .and_then(|(_, v)| v.trim().parse::<f32>().ok())
                        .filter(|v| v.is_finite())
                        .unwrap_or(default)
                };
                config.sun_elevation = f("sun-elevation", config.sun_elevation);
                config.sun_azimuth = f("sun-azimuth", config.sun_azimuth);
                config.mie = f("mie-coefficient", config.mie);
                config.mie_g = f("mie-directional-g", config.mie_g).clamp(-0.99, 0.99);
                config.sun_intensity = f("sun-intensity", config.sun_intensity).max(0.1);
                config.cloud_coverage = f("cloud-coverage", config.cloud_coverage).clamp(0.0, 1.0);
                config.cloud_density = f("cloud-density", config.cloud_density).clamp(0.0, 1.0);
                config.cloud_elevation =
                    f("cloud-elevation", config.cloud_elevation).clamp(0.0, 1.0);
                config.star_density = f("star-density", config.star_density).max(0.0);
                config.aurora = f("aurora", config.aurora).clamp(0.0, 2.0);
                config.nebula = f("nebula", config.nebula).clamp(0.0, 1.5);
                config.model = SkyModel::from_attr(
                    attrs
                        .iter()
                        .find(|(k, _)| k == "model")
                        .map(|(_, v)| v.as_str()),
                );
                config.rayleigh = f("rayleigh", config.rayleigh).max(0.0);
                config.nishita_mie = f("nishita-mie", config.nishita_mie).max(0.0);
            }
            crate::recipes::EntityKind::DayCycle {
                minute_of_day,
                minutes_per_real_second,
                dawn_minute,
                dusk_minute,
                max_sun_elevation,
                sun_azimuth_base,
                ..
            } => {
                config.drive = true;
                config.clock_start = *minute_of_day;
                config.clock_speed = *minutes_per_real_second;
                config.dawn = *dawn_minute;
                config.dusk = *dusk_minute;
                config.max_elev = *max_sun_elevation;
                config.az_base = *sun_azimuth_base;
            }
            crate::recipes::EntityKind::Weather { wind, .. } => {
                config.wind = *wind;
            }
            _ => {}
        }
        walk_entities(&spec.children, config);
    }
}

/// Spawn the dome + material for a `<Sky>` element (world config reaches the
/// shader through the specialized WGSL, not through these attributes).
pub fn build_sky(world: &mut World, meshes: &mut Assets<Mesh>, sky_mats: &mut Assets<SkyMaterial>) {
    let mesh = meshes.add(sky_dome_mesh());
    let material = world.resource_scope(
        |_world: &mut World, mut buffers: Mut<Assets<ShaderBuffer>>| {
            sky_mats.add(SkyMaterial {
                data: buffers.add(new_sky_buffer(SkyUniform::default())),
            })
        },
    );
    // Raio 850: TEM de caber dentro do far plane da câmara (o default do
    // `Camera3d` é 1000 m) — com 4000 o rim do domo era clipado pelo far e
    // ficava a "banda preta no topo" reportada no r1 (em posições longe do
    // centro, o rim aproximava-se ainda mais). 850 m cobre o conteúdo do
    // mundo (±250 m) e segue a câmara todos os frames.
    world.spawn((
        Name::new("sky"),
        Mesh3d(mesh),
        MeshMaterial3d::<SkyMaterial>(material),
        Transform::from_scale(Vec3::splat(850.0)),
        Visibility::Visible,
        NotShadowCaster,
        SkyDome,
    ));
}

/// Inverted-normal UV sphere (seen from inside) at unit radius — scale it up
/// via the camera-follow transform.
pub fn sky_dome_mesh() -> bevy::mesh::Mesh {
    let bands = 48;
    let slices = 96;
    let mut positions = Vec::with_capacity((bands + 1) * (slices + 1));
    let mut uvs = Vec::with_capacity((bands + 1) * (slices + 1));
    for b in 0..=bands {
        let phi = std::f32::consts::PI * (b as f32) / (bands as f32);
        for s in 0..=slices {
            let theta = std::f32::consts::TAU * (s as f32) / (slices as f32);
            let y = phi.cos();
            let r = phi.sin();
            positions.push([r * theta.cos(), y, r * theta.sin()]);
            uvs.push([s as f32 / slices as f32, b as f32 / bands as f32]);
        }
    }
    let mut indices = Vec::with_capacity(bands * slices * 6);
    for b in 0..bands {
        for s in 0..slices {
            let row = b * (slices + 1);
            let a = (row + s) as u32;
            let c = (row + s + 1) as u32;
            let d = ((b + 1) * (slices + 1) + s) as u32;
            let e = ((b + 1) * (slices + 1) + s + 1) as u32;
            // Winding invertido: o interior da esfera é o lado visível.
            // Os dois triângulos do quad têm de partilhar a MESMA orientação
            // — [a,e,d] ficava com a normal para FORA e era culled visto de
            // dentro (xadrez de um-triângulo-sim-um-triângulo-não no céu).
            indices.extend([a, d, e, a, e, c]);
        }
    }
    let mut mesh = bevy::mesh::Mesh::new(
        PrimitiveTopology::TriangleList,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    );
    mesh.insert_attribute(bevy::mesh::Mesh::ATTRIBUTE_POSITION, positions);
    mesh.insert_attribute(bevy::mesh::Mesh::ATTRIBUTE_UV_0, uvs);
    mesh.insert_indices(bevy::mesh::Indices::U32(indices));
    mesh
}

/// Novo `ShaderBuffer` de 96 bytes com `data` — chamado a cada 30 frames
/// (ver a nota REFRESH DO STORAGE no topo do ficheiro).
fn new_sky_buffer(data: SkyUniform) -> ShaderBuffer {
    let mut buffer = ShaderBuffer::with_size(
        std::mem::size_of::<SkyUniform>(),
        RenderAssetUsages::default(),
    );
    buffer.set_data(data);
    buffer
}

/// Empurra a paleta da hora ([`crate::worldsys::AtmosphereState`] + vento do
/// `<Weather>` + minuto do relógio) para o storage buffer do domo.
///
/// Publica um `SkyMaterial`+`ShaderBuffer` NOVOS SÓ QUANDO O VALOR MUDA
/// (r8, opção (a) da síntese da lead): a paleta muda devagar, logo isto
/// publica ~2-5×/s em vez de 60×/s — sem inundar a fila de eventos do
/// render world (que, saturada, perdia `Modified` e congelava o céu no
/// último estado — r7/r8). Cada publish é um id NOVO = cache miss no bind
/// group = upload garantido pelo path `Added` (o que se provou correto no
/// r4-r6; os frames intermediários ficam com o material anterior, que é
/// visualmente idêntico dada a dedupe).
#[allow(clippy::needless_pass_by_value)]
#[allow(clippy::too_many_arguments)]
pub fn sky_material_drive(
    time: Res<Time>,
    atmosphere: Res<crate::worldsys::AtmosphereState>,
    clock: Option<Res<crate::worldsys::DayCycleState>>,
    weather: Option<Res<crate::worldsys::WeatherState>>,
    domes: Query<(Entity, &MeshMaterial3d<SkyMaterial>), With<SkyDome>>,
    mut materials: ResMut<Assets<SkyMaterial>>,
    mut buffers: ResMut<Assets<ShaderBuffer>>,
    mut refresh_tick: Local<u32>,
    mut commands: Commands,
) {
    let minute = clock.map(|c| c.minute_of_day).unwrap_or(720.0);
    let (wind, clouds) = weather
        .map(|w| (w.wind, w.clouds))
        .unwrap_or(([0.7, 0.25], 0.0));
    let a = *atmosphere;
    // `<Weather clouds>` é um DELTA sobre a cobertura do `<Sky>` (que vive
    // nas consts especializadas) — um mundo com tempo fechado enche o céu
    // sem tocar no XML do céu.
    let cloud_delta = clouds.clamp(0.0, 1.0) * 0.45;
    let data = SkyUniform {
        sun: Vec4::new(a.sun_dir.x, a.sun_dir.y, a.sun_dir.z, a.day),
        moon: Vec4::new(a.moon_dir.x, a.moon_dir.y, a.moon_dir.z, a.night),
        zenith: Vec4::new(a.zenith[0], a.zenith[1], a.zenith[2], a.golden),
        horizon: Vec4::new(a.horizon[0], a.horizon[1], a.horizon[2], cloud_delta),
        sun_tint: Vec4::new(
            a.sun_tint[0],
            a.sun_tint[1],
            a.sun_tint[2],
            time.elapsed_secs(),
        ),
        params: Vec4::new(wind[0], wind[1], 0.0, minute),
    };
    if domes.is_empty() {
        return;
    }
    // CINTO E SUSPENSÓRIOS (r14): a cada frame, `set_data` no buffer atual
    // (1 `Modified`/frame — o path normal). A CADA 30 frames (~0.5 s), e é
    // AQUI que o céu vive ou morre: publicar um MATERIAL NOVO (com buffer
    // novo) e inseri-lo na ENTIDADE do domo. Trocar só o handle dentro do
    // asset (`material.data = nb`) e esperar pelo `Modified<SkyMaterial>`
    // provou-se frágil — sob carga os eventos de modificação do asset perdem-
    // se na fila do render world e o domo fica ETERNAMENTE no 1.º upload
    // (céu congelado, r7/r8/r13). Componente novo = binding novo = o render
    // re-extrai tudo no frame seguinte, aconteça o que acontecer à fila.
    let refresh = *refresh_tick == 0;
    *refresh_tick = (*refresh_tick + 1) % 30;
    let replacement = refresh.then(|| {
        let material = materials.add(SkyMaterial {
            data: buffers.add(new_sky_buffer(data)),
        });
        MeshMaterial3d::<SkyMaterial>(material)
    });

    for (entity, handle) in domes.iter() {
        if let Some(material) = materials.get_mut(&handle.0)
            && let Some(mut buffer) = buffers.get_mut(&material.data)
        {
            buffer.set_data(data);
        }
        if let Some(mat) = &replacement {
            commands.entity(entity).insert(mat.clone());
        }
    }
}

/// Keep the dome centered on the camera. Filtra por `Camera3d` (a query por
/// `Camera` apanhava também a câmara 2d da UI e o `single()` falhava em
/// silêncio — o domo ficava para trás e o rim aparecia no topo do frame).
pub fn sky_follow_camera(
    cameras: Query<&GlobalTransform, With<bevy::camera::Camera3d>>,
    mut domes: Query<&mut Transform, With<SkyDome>>,
) {
    // `iter().next()` e não `single()`: ≥2 câmaras 3d falhavam o `single()`
    // e o domo ficava para trás (mesma semântica do 1.º player).
    let Some(camera) = cameras.iter().next() else {
        return;
    };
    for mut transform in &mut domes {
        transform.translation = camera.translation();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// O shader gerado substitui o bloco CONFIG e mantém o resto intacto.
    #[test]
    fn test_render_world_shader_specializes_config() {
        let mut config = SkyConfig::default();
        config.drive = true;
        config.clock_start = 1140.0;
        config.aurora = 1.25;
        let shader = config.render_world_shader();
        assert!(shader.contains("const CFG_DRIVE: f32 = 1;"));
        assert!(shader.contains("const CFG_CLOCK_START: f32 = 1140;"));
        assert!(shader.contains("const CFG_AURORA: f32 = 1.25;"));
        // O corpo do shader sobrevive à substituição.
        assert!(shader.contains("fn fragment"));
        assert!(shader.contains("aurora_ribbons"));
        assert_eq!(shader.matches(CONFIG_END).count(), 1);
        // Sem marcadores (template partido), devolve o template intacto.
        assert_eq!(
            SkyConfig::default()
                .render_world_shader()
                .contains("const CFG_DRIVE"),
            true
        );
    }

    /// O ramo nishita emite as consts físicas com os MESMOS coeficientes que
    /// o IBL usa (o ganho é a constante partilhada, nunca uma cópia).
    #[test]
    fn test_render_world_shader_emits_nishita_consts() {
        let mut config = SkyConfig::default();
        config.model = SkyModel::Nishita;
        config.rayleigh = 2.8;
        config.nishita_mie = 1.5;
        let shader = config.render_world_shader();
        assert!(shader.contains("const CFG_SKY_MODEL: f32 = 1;"));
        assert!(shader.contains(&format!(
            "const CFG_NISHITA_GAIN: f32 = {};",
            crate::sky_nishita::NISHITA_GAIN
        )));
        let model = config.nishita_model();
        assert!(shader.contains(&format!(
            "const CFG_NISHITA_RAYLEIGH: vec3<f32> = vec3({}, {}, {});",
            model.rayleigh[0], model.rayleigh[1], model.rayleigh[2]
        )));
        assert!(shader.contains(&format!(
            "const CFG_NISHITA_MIE: f32 = {};",
            crate::sky_nishita::MIE_BASE * 1.5
        )));
        // O default continua analytic.
        assert!(SkyConfig::default()
            .render_world_shader()
            .contains("const CFG_SKY_MODEL: f32 = 0;"));
    }

    /// `from_world` lê `<Sky>`, `<DayCycle>` e `<Weather>` (incl. filhos).
    #[test]
    fn test_sky_config_from_world() {
        use crate::physics::PhysicsSpec;
        use crate::recipes::{EntityKind, EntitySpec, TransformSpec};

        let spec = |kind: EntityKind| EntitySpec {
            name: None,
            tag: None,
            script: None,
            destructible: None,
            transform: TransformSpec::default(),
            physics: PhysicsSpec::default(),
            kind,
            children: Vec::new(),
        };
        let world = vec![
            spec(EntityKind::Group),
            spec(EntityKind::HudElement {
                tag: "sky".to_string(),
                attrs: vec![
                    ("model".to_string(), "nishita".to_string()),
                    ("rayleigh".to_string(), "2.8".to_string()),
                    ("nishita-mie".to_string(), "1.5".to_string()),
                ],
            }),
            spec(EntityKind::DayCycle {
                minute_of_day: 1140.0,
                minutes_per_real_second: 3.0,
                dawn_minute: 330.0,
                dusk_minute: 1170.0,
                ambient_day: 0.26,
                ambient_night: 0.07,
                drive_ambient: true,
                max_sun_elevation: 55.0,
                sun_azimuth_base: 180.0,
                min_sun_elevation: 8.0,
            }),
        ];
        let config = SkyConfig::from_world(&world);
        assert!(config.drive);
        assert_eq!(config.clock_start, 1140.0);
        assert_eq!(config.clock_speed, 3.0);
        assert_eq!(config.max_elev, 55.0);
        // Defaults preservados na ausência de <Sky>.
        assert_eq!(config.cloud_coverage, 0.45);
        // O ramo físico lê o modelo e os multiplicadores.
        assert_eq!(config.model, SkyModel::Nishita);
        assert_eq!(config.rayleigh, 2.8);
        assert_eq!(config.nishita_mie, 1.5);
        assert_eq!(SkyConfig::default().model, SkyModel::Analytic);
        assert_eq!(SkyModel::from_attr(Some("NISHITA")), SkyModel::Nishita);
        assert_eq!(SkyModel::from_attr(Some("lixo")), SkyModel::Analytic);
        assert_eq!(SkyModel::from_attr(None), SkyModel::Analytic);
    }
}
