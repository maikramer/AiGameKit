//! World-system elements: DayCycle, Weather, WorldBorder, BiomeRegion and
//! engine config — parsed into resources and driven by systems here.

#[cfg(test)]
use std::sync::Arc;

use bevy::math::Quat;
use bevy::math::Vec3;
use bevy::prelude::*;

/// `<DayCycle>` clock: advances minute-of-day and ramps the ambient light.
#[derive(Debug, Clone, Resource)]
pub struct DayCycleState {
    pub minute_of_day: f32,
    pub minutes_per_real_second: f32,
    pub dawn_minute: f32,
    pub dusk_minute: f32,
    pub ambient_day: f32,
    pub ambient_night: f32,
    /// Ambient brightness the world authored (`<AmbientLight brightness>`),
    /// used as the full-day anchor for the day/night ramp. `0` until the
    /// first drive tick captures it.
    pub ambient_reference: f32,
    pub drive_ambient: bool,
    /// Elevação máxima do sol ao meio-dia (graus).
    pub max_sun_elevation: f32,
    /// Azimute do nascer do sol (graus).
    pub sun_azimuth_base: f32,
    /// Piso da elevação usada pela LUZ direcional (`min-sun-elevation`) —
    /// o sol astronómico continua a descer; só a luz não segue abaixo.
    pub min_sun_elevation: f32,
}

impl DayCycleState {
    #[allow(clippy::too_many_arguments)]
    pub fn from_parts(
        minute_of_day: f32,
        minutes_per_real_second: f32,
        dawn_minute: f32,
        dusk_minute: f32,
        ambient_day: f32,
        ambient_night: f32,
        drive_ambient: bool,
        max_sun_elevation: f32,
        sun_azimuth_base: f32,
        min_sun_elevation: f32,
    ) -> Self {
        Self {
            minute_of_day,
            minutes_per_real_second,
            dawn_minute,
            dusk_minute,
            ambient_day,
            ambient_night,
            // Captured from the live ambient light on the first drive tick.
            ambient_reference: 0.0,
            drive_ambient,
            max_sun_elevation,
            sun_azimuth_base,
            min_sun_elevation,
        }
    }
}

/// Daylight factor `0.0` (night) → `1.0` (day) with 60-minute dawn/dusk ramps.
pub fn daylight_factor(minute: f32, dawn: f32, dusk: f32) -> f32 {
    const RAMP: f32 = 60.0;
    if minute < dawn - RAMP {
        0.0
    } else if minute < dawn {
        (minute - (dawn - RAMP)) / RAMP
    } else if minute < dusk {
        1.0
    } else if minute < dusk + RAMP {
        1.0 - (minute - dusk) / RAMP
    } else {
        0.0
    }
}

/// `<Weather>` wind/cloud/rain config.
///
/// `rain` é INTENSIDADE CONTÍNUA 0..1 (não bool): 0 = seco, 1 = tempestade.
/// Com `cycle` a falso é estática como autorada (backwards compat); com
/// `cycle` a verdadeiro o [`weather_drive`] rola novos alvos e faz o lerp
/// de [`WEATHER_TRANSITION_SECS`].
#[derive(Debug, Clone, Resource)]
pub struct WeatherState {
    pub wind: [f32; 2],
    pub wind_strength: f32,
    pub clouds: f32,
    pub rain: f32,
    pub cycle: bool,
}

/// Período do ciclo de weather (`<Weather cycle>`) em segundos.
///
/// NOTA de contrato (WS-A): o attr `cycle` é parseado como BOOL no parser
/// (`recipes::finish_weather`, ficheiro de outro workstream), pelo que
/// "cycle em segundos" não é autorável — `cycle="1"` liga o ciclo com ESTE
/// período; `cycle="0"` mantém a chuva estática como declarada.
pub const WEATHER_CYCLE_DEFAULT_SECS: f32 = 240.0;
/// Duração do lerp entre o alvo de chuva antigo e o novo (s).
pub const WEATHER_TRANSITION_SECS: f32 = 10.0;

/// Estado do scheduler do `<Weather cycle>` — criado preguiçosamente pelo
/// [`weather_drive`] quando o mundo declara o ciclo.
#[derive(Debug, Clone, Resource)]
pub struct WeatherScheduler {
    /// Seed do mundo (a do `<Terrain>`, 0 sem terreno) — "mesma seed, mesmo
    /// mundo" é lei: o k-ésimo ciclo produz SEMPRE o mesmo alvo.
    pub seed: u64,
    /// Índice do ciclo corrente (0 = o estado autoral; os rollpoints são 1, 2…).
    pub index: u64,
    pub period: f32,
    pub timer: f32,
    /// Intensidade de chuva ALVO do ciclo corrente (0..1).
    pub target: f32,
}

impl WeatherScheduler {
    pub fn new(seed: u64, initial_rain: f32, period: f32) -> Self {
        let period = if period > 0.0 {
            period
        } else {
            WEATHER_CYCLE_DEFAULT_SECS
        };
        Self {
            seed,
            index: 0,
            period,
            timer: period,
            target: initial_rain.clamp(0.0, 1.0),
        }
    }

    /// Avança `dt` e devolve o alvo corrente (rolando um novo quando o timer
    /// esgota). Função pura sobre `&mut self` para teste.
    pub fn tick(&mut self, dt: f32) -> f32 {
        self.timer -= dt;
        if self.timer <= 0.0 {
            self.timer += self.period;
            self.index += 1;
            self.target = weather_cycle_target(self.seed, self.index);
        }
        self.target
    }
}

/// Alvo de intensidade de chuva do `index`-ésimo ciclo — SplitMix64 (o RNG
/// determinístico do spawner) semeado por `seed XOR índice·golden`: a mesma
/// seed+índice dá SEMPRE o mesmo alvo, mundos diferentes divergem.
///
/// O quadrado enviesa para tempo mais seco do que chuvoso (média ~0.33; a
/// raiz daria o oposto, ~0.67) mas preserva tempestades ocasionais (r → 1
/// só quando o uniforme chega perto de 1).
pub fn weather_cycle_target(seed: u64, index: u64) -> f32 {
    let mut rng = crate::spawner::Rng::new((seed ^ index.wrapping_mul(0x9E37_79B9_7F4A_7C15)) | 1);
    let u = rng.next_f32();
    u * u
}

/// Aproxima `current` de `target` à velocidade CONSTANTE `1/transition` por
/// segundo — nunca ultrapassa o alvo, nunca regride ao secar (monótono).
pub fn rain_toward(current: f32, target: f32, dt: f32, transition: f32) -> f32 {
    let current = current.clamp(0.0, 1.0);
    let delta = target.clamp(0.0, 1.0) - current;
    if delta.abs() <= f32::EPSILON || dt <= 0.0 {
        return current;
    }
    let step = (dt / transition.max(f32::EPSILON)).min(delta.abs());
    current + delta.signum() * step
}

/// Roda o scheduler e escreve a intensidade contínua em `WeatherState.rain`.
///
/// Mundos SEM `<Weather cycle>` (ou `cycle="0"`) nunca chegam aqui: chuva
/// estática como autorada. Corre ANTES de [`atmosphere_drive`] (mesmo frame).
#[allow(clippy::needless_pass_by_value)]
pub fn weather_drive(
    time: Res<Time>,
    mut commands: Commands,
    weather: Option<ResMut<WeatherState>>,
    scheduler: Option<ResMut<WeatherScheduler>>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
) {
    let Some(mut weather) = weather else {
        return;
    };
    if !weather.cycle {
        return;
    }
    let dt = time.delta_secs().clamp(0.0, 0.5);
    let Some(mut scheduler) = scheduler else {
        // 1.º tick com ciclo: semeia pelo mundo (seed do terreno; 0 sem ele)
        // e parte do estado autoral — sem salto no frame de arranque.
        let seed = runtime.as_ref().map(|r| r.spec.seed).unwrap_or(0);
        commands.insert_resource(WeatherScheduler::new(seed, weather.rain, 0.0));
        return;
    };
    let target = scheduler.tick(dt);
    weather.rain = rain_toward(weather.rain, target, dt, WEATHER_TRANSITION_SECS);
}

/// `<BiomeRegion>` polygon + fog/tint data (fog rendering follow-up).
#[derive(Debug, Clone, Resource)]
pub struct BiomeRegionData {
    pub id: String,
    /// `display-name`: nome de exposição da zona no HUD (`zone.name`).
    /// Vazio = a engine usa a sua tabela de fallback (e, em último caso,
    /// deriva um título do próprio id).
    pub display_name: String,
    pub polygon: Vec<[f32; 2]>,
    pub fog_density: f32,
    pub tint: Option<[f32; 3]>,
    /// `pp-exposure`: linear exposure multiplier inside the region
    /// (`None` = the world base). Read by [`crate::postfx`].
    pub pp_exposure: Option<f32>,
    /// `pp-bloom-strength`: bloom intensity inside the region.
    pub pp_bloom_strength: Option<f32>,
}

/// Todas as `<BiomeRegion>` do mundo (loop 9: fog/tint por bioma).
#[derive(Debug, Clone, Resource, Default)]
pub struct BiomeRegions {
    pub list: Vec<BiomeRegionData>,
}

/// `<WorldBorder>` config.
#[derive(Debug, Clone, Resource)]
pub struct WorldBorderConfig {
    pub radius: f32,
    pub warn_seconds: f32,
    pub margin: f32,
}

/// Generic engine config element kept as raw data.
#[derive(Debug, Clone, Resource)]
pub struct EngineConfigData {
    pub tag: String,
    pub attrs: Vec<(String, String)>,
}

/// Deferred world-system requests collected while spawning entities.
#[derive(Debug, Resource, Default)]
pub struct PendingWorldSystems {
    pub day_cycle: Option<DayCycleState>,
    pub weather: Option<WeatherState>,
    pub border: Option<WorldBorderConfig>,
    pub interior_scene: Option<InteriorSceneConfig>,
    pub biomes: Vec<BiomeRegionData>,
    pub configs: Vec<EngineConfigData>,
}

/// `<InteriorScene at="x z" size="w d">` — uma cena de interior declarada
/// como BOLSA, fora da área do mapa.
///
/// PORQUÊ: os ambientes internos não vivem no mundo, vivem noutro sítio. Se
/// esse sítio for dentro do campo do heightmap, o mundo inteiro continua a
/// mexer lá dentro — a cota do terreno atravessa o soalho, a névoa e a tinta
/// do bioma entram na sala, chove dentro de casa e a `WorldBorder` devolve o
/// herói ao vale a meio da visita. A bolsa é a declaração que diz à engine
/// "aqui não há mundo": o retângulo é isento do clamp de fronteira, trata-se
/// como bioma nenhum (névoa/tinta/exposição neutras) e não chove.
///
/// O sítio escolhido é FORA da pegada do heightmap (`|x|` ou `|z|` >
/// `world_size/2`), onde o terreno já não gera colunas nem colliders — ver
/// `TerrainRuntime::in_field`. A distância faz o resto: conteúdo a >2 km não
/// entra em render-distance, em cull-distance nem em raio de ativação.
#[derive(Debug, Clone, Copy, PartialEq, bevy::prelude::Resource)]
pub struct InteriorSceneConfig {
    /// Canto mínimo do retângulo (XZ, metros).
    pub min: [f32; 2],
    /// Canto máximo do retângulo (XZ, metros).
    pub max: [f32; 2],
}

impl InteriorSceneConfig {
    pub fn contains(&self, x: f32, z: f32) -> bool {
        x >= self.min[0] && x <= self.max[0] && z >= self.min[1] && z <= self.max[1]
    }
}

impl PendingWorldSystems {
    /// Take all deferred requests out (leaving the accumulator empty).
    pub fn consume(&mut self) -> PendingWorldSystems {
        std::mem::take(self)
    }
}

/// Marks entities authored at y≈0 that must sit on the terrain surface once
/// the carved world exists (the original engine seated statics via CCT).
#[derive(Debug, Component)]
pub struct SeatOnTerrain;

/// Marcador de "assenta como UNIDADE": em cima de [`SeatOnTerrain`], força o
/// seating pelo centro do conteúdo MESMO quando o span passa o limiar
/// [`SEAT_CENTER_MAX_EXTENT_M`] — é o contrato das `<Composition>` (um
/// objeto inteiro nunca é partido em partes para assentar; uma caixa de
/// 240 m continua a subir em bloco).
#[derive(Debug, Component)]
pub struct SeatAsUnit;

/// One-shot: seat authored groups on the carved terrain surface.
///
/// Only the OUTERMOST seatable group of each subtree is seated as a whole
/// (seating a nested group would add the ground height a second time on top
/// of an ancestor that had already been raised to it — `simple-rpg` nests the
/// city two deep, which used to float the whole village ~49 m).
///
/// WHERE the ground is sampled matters: `SeatOnTerrain` groups are authored
/// at `translation="0 0 0"` and their content is placed in *world* coords on
/// the children, so the origin of the group is often NOT where the content
/// lives (biome landmark groups, the interiors grid at 859,281…). Sampling at
/// the group origin seated a forest landmark at the plaza height (~24.6 m),
/// burying or floating it by tens of metres. Instead the AABB of the direct
/// children decides:
///
/// - content co-located (AABB span ≤ [`SEAT_CENTER_MAX_EXTENT_M`]) → sample
///   at the AABB centre, so a group whose content sits away from its origin
///   lands at the height of the place the content actually is (the village
///   keeps seating at the plaza pad);
/// - content spread across the map (span above the threshold: landmark
///   groups, lantern rows, frontier ridges) → the group itself stays put and
///   each direct child is seated individually at its own XZ (group children
///   recurse through the same rule, plain props lift straight to their local
///   ground).
///
/// World XZ of a child is approximated as `group translation + child local
/// translation` (identity rotation/scale — every authored group in practice).
/// The lift is upward-only: authored elevated content keeps its offset.
///
/// Runs once the terrain runtime exists; later spawns (spawn groups) are
/// already placed by [`crate::spawner::compute_placements`].
pub fn seat_statics_once(
    mut done: Local<bool>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut transforms: Query<(Entity, &mut Transform)>,
    parents: Query<(Entity, &ChildOf)>,
    seated: Query<(), With<SeatOnTerrain>>,
    unit: Query<(), With<SeatAsUnit>>,
) {
    if *done {
        return;
    }
    let Some(runtime) = runtime else {
        return;
    };
    // Índice pai → filhos: um passe sobre todas as relações de hierarquia.
    let mut children_of: std::collections::HashMap<Entity, Vec<Entity>> =
        std::collections::HashMap::new();
    for (child, parent) in &parents {
        children_of.entry(parent.parent()).or_default().push(child);
    }
    let roots: Vec<Entity> = transforms
        .iter()
        .map(|(e, _)| e)
        .filter(|e| seated.contains(*e) && !has_seated_ancestor(*e, &parents, &seated))
        .collect();
    for root in roots {
        seat_group(
            root,
            &runtime,
            &children_of,
            &seated,
            &unit,
            &mut transforms,
        );
    }
    *done = true;
}

/// Extensão máxima (m, lado maior do AABB XZ do conteúdo direto) para sentar
/// um grupo pelo CENTRO do conteúdo. Acima disto o grupo espalha conteúdo
/// pelo mapa inteiro (landmarks de bioma, lanternas, cristas) e o centro não
/// fica em cima de nenhum deles — cada filho é então sentado à sua própria
/// cota local.
const SEAT_CENTER_MAX_EXTENT_M: f32 = 60.0;

/// Senta `entity` (grupo com `SeatOnTerrain`) pelo seu conteúdo, ver
/// [`seat_statics_once`].
fn seat_group(
    entity: Entity,
    runtime: &crate::terrain::runtime::TerrainRuntime,
    children_of: &std::collections::HashMap<Entity, Vec<Entity>>,
    seated: &Query<(), With<SeatOnTerrain>>,
    unit: &Query<(), With<SeatAsUnit>>,
    transforms: &mut Query<(Entity, &mut Transform)>,
) {
    let Ok((_, group_transform)) = transforms.get(entity) else {
        return;
    };
    let group_translation = group_transform.translation;
    let mut kid_xz: Vec<(Entity, Vec2)> = Vec::new();
    if let Some(kids) = children_of.get(&entity) {
        for kid in kids {
            if let Ok((_, t)) = transforms.get(*kid) {
                kid_xz.push((*kid, Vec2::new(t.translation.x, t.translation.z)));
            }
        }
    }
    let Some((_, first)) = kid_xz.first() else {
        // Grupo sem conteúdo transformado (ex.: só spawners, que não spawmam
        // entidades): comportamento antigo — amostra no próprio XZ.
        seat_at(
            entity,
            group_translation.x,
            group_translation.z,
            runtime,
            transforms,
        );
        return;
    };
    let (mut min, mut max) = (*first, *first);
    for (_, xz) in &kid_xz {
        min = min.min(*xz);
        max = max.max(*xz);
    }
    let extent = (max.x - min.x).max(max.y - min.y);
    if extent <= SEAT_CENTER_MAX_EXTENT_M || unit.contains(entity) {
        // Conteúdo co-localizado: senta pelo CENTRO do AABB do conteúdo.
        let center = (min + max) * 0.5;
        seat_at(
            entity,
            group_translation.x + center.x,
            group_translation.z + center.y,
            runtime,
            transforms,
        );
        return;
    }
    // Conteúdo espalhado pelo mapa: o grupo não se move como bloco; cada
    // filho fica à cota do seu próprio sítio.
    for (kid, xz) in kid_xz {
        if seated.contains(kid) {
            seat_group(kid, runtime, children_of, seated, unit, transforms);
        } else {
            seat_at(
                kid,
                group_translation.x + xz.x,
                group_translation.z + xz.y,
                runtime,
                transforms,
            );
        }
    }
}

/// Levanta `entity` até ao solo amostrado em (`x`, `z`) — só para cima:
/// conteúdo autoral elevado (tectos, tochas penduradas) mantém a cota.
fn seat_at(
    entity: Entity,
    x: f32,
    z: f32,
    runtime: &crate::terrain::runtime::TerrainRuntime,
    transforms: &mut Query<(Entity, &mut Transform)>,
) {
    let Ok((_, mut transform)) = transforms.get_mut(entity) else {
        return;
    };
    // Fora da pegada do heightmap não há chão para onde assentar: sem esta
    // guarda, o `column` saturado na orla levantava uma cena de interior
    // inteira (ou cada filho dela) à cota da borda do mundo.
    if !runtime.in_field(x, z) {
        return;
    }
    // Levantar um static afundado até ao topo do SPAN em que ele está: um
    // static correctamente pousado sob um overhang não é puxado através da
    // rocha (o topo do mundo ali é teto), e um static enterrado sobe até à
    // primeira superfície acima de si. Spans chegam top-down.
    let y = transform.translation.y;
    let spans = runtime.column(x, z);
    let ground = spans
        .iter()
        .rev()
        .map(|span| span.top)
        .find(|&top| top <= y + 0.25)
        .unwrap_or_else(|| {
            spans
                .first()
                .map(|span| span.top)
                .unwrap_or(f32::NEG_INFINITY)
        });
    if y < ground - 0.25 {
        transform.translation.y = ground;
    }
}

/// True when any ancestor of `entity` also carries [`SeatOnTerrain`].
fn has_seated_ancestor(
    entity: Entity,
    parents: &Query<(Entity, &ChildOf)>,
    seated: &Query<(), With<SeatOnTerrain>>,
) -> bool {
    let mut current = entity;
    // Depth guard: worlds compose with `<Include>` and could nest deeply, but
    // a cycle would hang the startup frame.
    for _ in 0..64 {
        let Ok((_, parent)) = parents.get(current) else {
            return false;
        };
        if seated.get(parent.parent()).is_ok() {
            return true;
        }
        current = parent.parent();
    }
    false
}

/// `<Composition place="at: x z; …">` — colocação EXPLÍCITA no terreno,
/// resolvida uma única vez quando o terreno existe (o mesmo gatilho do
/// seating). Diferenças para [`SeatOnTerrain`]:
///
/// - o ponto de amostragem é `at` (mundo), não o centro do conteúdo;
/// - a cota é definida exatamente (sobe E desce), não apenas para cima;
/// - `align-to-terrain` orienta o +Y do objeto à normal do terreno
///   (yaw autoral preservado) — cadeiras numa ladeira assentam na ladeira.
#[derive(Debug, Clone, Component)]
pub struct PendingPlace {
    /// Ponto de amostragem em XZ mundo.
    pub at: Vec2,
    /// Orientar o objeto à normal do terreno.
    pub align_to_terrain: bool,
    /// Somado em Y após a amostragem.
    pub base_y_offset: f32,
}

/// One-shot: resolve todos os [`PendingPlace`] quando o terreno existe.
///
/// O XZ do objeto é REESCRITO para `at` (a composição vai PARA onde o autor
/// pediu — semântica do `place` do VibeGame), compensando a translation do
/// pai para o caso aninhado. Só altera a pose uma vez; depois o componente
/// sai da entidade.
#[allow(clippy::type_complexity)]
pub fn resolve_pending_place(
    mut done: Local<bool>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut pending: Query<(Entity, &mut Transform, &PendingPlace, Option<&ChildOf>)>,
    globals: Query<&GlobalTransform>,
    mut commands: Commands,
) {
    if *done {
        return;
    }
    let Some(runtime) = runtime else {
        return;
    };
    for (entity, mut transform, place, parent) in &mut pending {
        // XZ do PAI em mundo: um `<Use>`/composition aninhado num grupo com
        // translation mantém o offset — `at` é sempre XZ MUNDO.
        let parent_xz = parent
            .and_then(|p| globals.get(p.parent()).ok())
            .map(|g| Vec2::new(g.translation().x, g.translation().z))
            .unwrap_or(Vec2::ZERO);
        let local_xz = place.at - parent_xz;
        let authored_y = transform.translation.y;
        let ground = place_ground_y(&runtime, place.at.x, place.at.y, authored_y);
        transform.translation.x = local_xz.x;
        transform.translation.z = local_xz.y;
        transform.translation.y = ground + place.base_y_offset;
        if place.align_to_terrain {
            let normal = terrain_normal(&runtime, place.at.x, place.at.y);
            transform.rotation = align_up_to(normal, transform.rotation);
        }
        commands.entity(entity).remove::<PendingPlace>();
    }
    *done = true;
}

/// Cota do solo para colocação explícita: o topo do span em que o objeto
/// está (mesma regra do `seat_at` — um `place` sob um saliente não teleporta
/// para cima do teto), caindo para o primeiro span.
fn place_ground_y(
    runtime: &crate::terrain::runtime::TerrainRuntime,
    x: f32,
    z: f32,
    authored_y: f32,
) -> f32 {
    let spans = runtime.column(x, z);
    spans
        .iter()
        .rev()
        .map(|span| span.top)
        .find(|&top| top <= authored_y + 0.25)
        .or_else(|| spans.first().map(|span| span.top))
        .unwrap_or(authored_y)
}

/// Normal do terreno por diferenças finitas do sampler de alturas.
fn terrain_normal(runtime: &crate::terrain::runtime::TerrainRuntime, x: f32, z: f32) -> Vec3 {
    const EPS: f32 = 0.5;
    let hl = runtime.sample(x - EPS, z);
    let hr = runtime.sample(x + EPS, z);
    let hd = runtime.sample(x, z - EPS);
    let hu = runtime.sample(x, z + EPS);
    Vec3::new(hl - hr, 2.0 * EPS, hd - hu).normalize_or(Vec3::Y)
}

/// Quaternion que orienta +Y à normal do terreno, preservando o heading
/// autoral (a rotação de inclinação aplica-se ANTES — em mundo).
pub fn align_up_to(normal: Vec3, authored: Quat) -> Quat {
    let n = normal.normalize_or(Vec3::Y);
    if (n - Vec3::Y).length_squared() < 1e-6 {
        return authored;
    }
    Quat::from_rotation_arc(Vec3::Y, n) * authored
}

/// Iluminância da luz direcional à noite, como fração da que o mundo
/// autorou (`<DirectionalLight illuminance>`): a **lua**. Baixa o bastante
/// para uma tocha valer a pena, alta o bastante para o chão continuar a ler-se.
pub const MOONLIGHT_RATIO: f32 = 0.06;
/// Elevação mínima (graus) da luz direcional.
///
/// O sol real desce a −25° à noite; apontar a luz direcional para lá
/// iluminava a cena POR BAIXO — o chão (normal +Y) apagava por completo
/// enquanto paredes e props ainda apanhavam luz de raspão, e o especular
/// varria a imagem conforme a câmara rodava ("mais escuro/mais colorido
/// consoante o ângulo"). A luz nunca passa abaixo deste ângulo.
pub const MIN_LIGHT_ELEVATION_DEG: f32 = 8.0;
/// Elevação da lua (graus) — alta, para banhar o chão em vez de o rasar.
pub const MOON_ELEVATION_DEG: f32 = 52.0;
/// Cor linear da luz da lua (azul frio).
pub const MOON_COLOR: [f32; 3] = [0.40, 0.54, 0.86];

/// Iluminância/cor que o mundo autorou para uma `DirectionalLight`, capturada
/// antes de [`sun_drive`] começar a escrevê-las.
#[derive(Debug, Clone, Copy, Component)]
pub struct SunLightBase {
    pub illuminance: f32,
    pub color: Color,
}

/// Posição do sol (e estado dia/noite) calculada a partir do relógio.
#[derive(Debug, Clone, Copy, Resource, Default)]
pub struct SunState {
    /// Direção PARA o sol, normalizada (mundo, Y-up).
    pub dir: Vec3,
    pub elevation_deg: f32,
    /// 0 = dia pleno, 1 = noite plena.
    pub night: f32,
}

/// Elevação (graus) do sol para o minuto do dia: sobe `max_elevation` ao
/// meio-dia e desce abaixo do horizonte à noite.
///
/// dawn/dusk degenerados não podem produzir NaN: `dawn==dusk` zera o arco
/// diurno e `dawn=0`/`dusk=1440` zera o noturno — ambos dividiam por 0 e o
/// NaN propagava-se ao sol, à luz direcional e ao ambiente. 1 min é o menor
/// arco com sentido; para além do clamp a semântica é a mesma.
pub fn sun_elevation(minute: f32, dawn: f32, dusk: f32, max_elevation: f32) -> f32 {
    const NIGHT_HALF_ARC: f32 = 25.0;
    let day_len = (dusk - dawn).max(1.0);
    if minute >= dawn && minute < dusk {
        let t = (minute - dawn) / day_len;
        (std::f32::consts::PI * t).sin() * max_elevation
    } else {
        let dusk_len = (24.0 * 60.0 - dusk + dawn).max(1.0);
        let t = if minute >= dusk {
            (minute - dusk) / dusk_len
        } else {
            (minute + (24.0 * 60.0 - dusk)) / dusk_len
        };
        -(std::f32::consts::PI * t).sin().abs() * NIGHT_HALF_ARC
    }
}

/// Azimute (graus) do sol: avança 180° durante o dia, 180° durante a noite.
/// Mesmos clamps de arco do [`sun_elevation`] — dawn/dusk degenerados nunca
/// dividem por 0.
pub fn sun_azimuth(minute: f32, dawn: f32, dusk: f32, base: f32) -> f32 {
    let day_len = (dusk - dawn).max(1.0);
    if minute >= dawn && minute < dusk {
        base + 180.0 * (minute - dawn) / day_len
    } else {
        let dusk_len = (24.0 * 60.0 - dusk + dawn).max(1.0);
        let t = if minute >= dusk {
            (minute - dusk) / dusk_len
        } else {
            (minute + (24.0 * 60.0 - dusk)) / dusk_len
        };
        base + 180.0 + 180.0 * t
    }
}

/// Advance the day clock and ramp `GlobalAmbientLight.brightness`.
///
/// O ambiente leva um FILL dourado/noturno: o sol da cena vale ~10k lux e o
/// `AmbientLight` autoral ~110 — a razão 1:90 esmaga qualquer sombra em
/// silhueta preta (o gap "primeiro plano em preto puro" do golden hour).
/// O fill usa a elevação do sol ([`SunState`], um frame velho — é suave) e
/// replica a curva golden de [`atmosphere_for_elevation`] para que céu,
/// névoa e ambiente concordem sobre que horas são.
#[allow(clippy::needless_pass_by_value)]
pub fn daycycle_drive(
    time: Res<Time>,
    clock: Option<ResMut<DayCycleState>>,
    ambient: Option<ResMut<GlobalAmbientLight>>,
    sun: Res<SunState>,
    mut ambient_color_ref: Local<[f32; 3]>,
    mut ambient_color_captured: Local<bool>,
) {
    let (Some(mut clock), Some(mut ambient)) = (clock, ambient) else {
        return;
    };
    // minutes_per_real_second é MINUTOS de jogo por segundo real (default
    // 1.2 → dia de 20 min): o /60 antigo tratava-o como fração de segundo
    // e o dia durava ~20 h — o dusk autoral nunca chegava numa sessão.
    clock.minute_of_day =
        (clock.minute_of_day + clock.minutes_per_real_second * time.delta_secs()) % (24.0 * 60.0);
    if !clock.drive_ambient {
        return;
    }
    // Capture the world's own ambient before this system starts writing it.
    if clock.ambient_reference <= 0.0 {
        clock.ambient_reference = ambient.brightness.max(1.0);
    }
    let day = daylight_factor(clock.minute_of_day, clock.dawn_minute, clock.dusk_minute);
    // `ambient-day-intensity` / `ambient-night-intensity` are VibeGame's
    // three.js ambient *intensities* — a 0..1 scale. Bevy's
    // `AmbientLight::brightness` is in lux and the same worlds author it in the
    // hundreds (`simple-rpg`: `brightness="110"`). Writing 0.26 straight into
    // it dropped the ambient by ~400x and left the whole village in the dark.
    //
    // So the pair is used as a day/night *ratio* against the brightness the
    // world authored: full day keeps that value, night falls to
    // `night / day` of it.
    let scale = if clock.ambient_day > f32::EPSILON {
        (day * clock.ambient_day + (1.0 - day) * clock.ambient_night) / clock.ambient_day
    } else {
        day
    };
    // Fill de sombras AGRESSIVO (r3): o sol da cena é 10k lux e o ambiente
    // autoral ~110 — sem isto a sombra é 1:90 e lê-se como preto puro (o
    // crítico reprovou o golden hour DUAS vezes por isto). +máx ×2.2 na
    // hora dourada e ×1.9 à noite: o chão em sombra lê TEXTURA (o poço e as
    // muralhas têm de se ver) e as luzes pontuais ganham contraste contra
    // um fundo que não é preto absoluto. Um frame velho de elevação não se
    // nota — o dia inteiro dura 20 minutos.
    let night = 1.0 - (sun.elevation_deg / 6.0).clamp(0.0, 1.0);
    let golden = (-(((sun.elevation_deg - 4.0) / 8.0).powi(2))).exp() * (1.0 - night);
    let fill = 2.8 * golden + 0.8 * night;
    ambient.brightness = clock.ambient_reference * (scale * (1.0 + fill)).clamp(0.0, 3.0);
    // Cor do ambiente: esfria para azul-profundo à noite (a cor autoral é
    // capturada UMA vez — flag explícita: a sentinela `== 0.0` recapturava
    // para sempre quando a cor autoral ERA preta/azul-pura (r=g=0), e o
    // lerp noturno derivava a cor de cada tick em vez de partir dela).
    if !*ambient_color_captured {
        let c = ambient.color.to_srgba();
        *ambient_color_ref = [c.red, c.green, c.blue];
        *ambient_color_captured = true;
    }
    // Golden entra no lerp com peso leve: o split quente/frio existe, mas a
    // base da muralha em sombra não pode ficar turva (r13 — transição do
    // lift suavizada; sombras longas mantêm-se pela LUZ, não pelo ambiente).
    let cool_mix = (night.clamp(0.0, 1.0) * 0.8 + golden * 0.3).min(1.0);
    let r = ambient_color_ref[0] + (0.30 - ambient_color_ref[0]) * cool_mix;
    let g = ambient_color_ref[1] + (0.40 - ambient_color_ref[1]) * cool_mix;
    let b = ambient_color_ref[2] + (0.72 - ambient_color_ref[2]) * cool_mix;
    ambient.color = Color::srgba(r, g, b, 1.0);
}

/// Publish [`SunState`] from the day clock and aim the directional light.
///
/// NOTE (Claude): `main.rs` already scheduled this system but the body had not
/// been written yet, so the binary did not build. This is the straightforward
/// composition of the `sun_elevation` / `sun_azimuth` helpers that were already
/// here — replace it if the intended behaviour differs.
#[allow(clippy::needless_pass_by_value)]
pub fn sun_drive(
    clock: Option<Res<DayCycleState>>,
    mut sun: ResMut<SunState>,
    mut lights: Query<(
        Entity,
        &mut Transform,
        &mut DirectionalLight,
        Option<&SunLightBase>,
    )>,
    mut commands: Commands,
) {
    let Some(clock) = clock else {
        return;
    };
    let elevation_deg = sun_elevation(
        clock.minute_of_day,
        clock.dawn_minute,
        clock.dusk_minute,
        clock.max_sun_elevation,
    );
    let azimuth_deg = sun_azimuth(
        clock.minute_of_day,
        clock.dawn_minute,
        clock.dusk_minute,
        clock.sun_azimuth_base,
    );
    // Direction *towards* the sun (Y-up) — a posição astronómica real, que é
    // o que o céu procedural e a lógica de jogo leem.
    sun.dir = sky_direction(elevation_deg, azimuth_deg);
    sun.elevation_deg = elevation_deg;
    // Night ramps in as the sun drops below the horizon.
    sun.night = (1.0 - (elevation_deg / 6.0).clamp(0.0, 1.0)).clamp(0.0, 1.0);

    // A luz direcional NÃO segue o sol abaixo do horizonte: passa a ser a
    // lua (alta, fria, fraca) com um crossfade pelo mesmo ramp dawn/dusk do
    // ambiente. Sem isto a noite ficava com o chão preto e o resto lavado.
    let day = daylight_factor(clock.minute_of_day, clock.dawn_minute, clock.dusk_minute);
    let sun_dir = sky_direction(
        elevation_deg.max(clock.min_sun_elevation.max(0.0)),
        azimuth_deg,
    );
    let moon_dir = sky_direction(MOON_ELEVATION_DEG, azimuth_deg + 180.0);
    let light_dir = moon_dir
        .lerp(sun_dir, day)
        .try_normalize()
        .unwrap_or(moon_dir);
    let scale = MOONLIGHT_RATIO + (1.0 - MOONLIGHT_RATIO) * day;

    // Sunlight travels from the sun into the scene; bevy shines a directional
    // light along the entity's -Z (same convention as `recipes::spawn`).
    let rotation = Quat::from_rotation_arc(-Vec3::Z, -light_dir);
    for (entity, mut transform, mut light, base) in &mut lights {
        let base = match base {
            Some(base) => *base,
            None => {
                let captured = SunLightBase {
                    illuminance: light.illuminance,
                    color: light.color,
                };
                commands.entity(entity).insert(captured);
                captured
            }
        };
        transform.rotation = rotation;
        light.illuminance = base.illuminance * scale;
        light.color = mix_linear(MOON_COLOR, base.color, day);
    }
}

// ── Atmosfera: paleta única partilhada por céu, fog e grading ───────────
//
// O gap contra o BOTW não era "faltam nuvens" — era **perspetiva aérea**.
// No baseline, o que estava a 300 m tinha a mesma cor e o mesmo contraste do
// que estava a 3 m, e o céu era um degradê que ignorava o relógio do mundo
// (o shader derivava a hora de `globals.time`, que o `set_clock` não mexe;
// às 23:02 ainda se via o disco solar).
//
// [`AtmosphereState`] resolve as duas coisas: é a ÚNICA fonte da paleta da
// hora — zénite, horizonte, cor do sol, fog, exposição — e é lida por
// `sky.rs` (uniform do domo), `ambient.rs` (DistanceFog na câmara) e
// `postfx.rs` (exposição/bloom). Céu, névoa e grading deixam de poder
// discordar sobre que horas são.

/// Cor linear (RGB) — mistura em espaço linear, como toda a iluminação.
type Rgb = [f32; 3];

fn mix3(a: Rgb, b: Rgb, t: f32) -> Rgb {
    let t = t.clamp(0.0, 1.0);
    [
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    ]
}

fn smooth_step(edge0: f32, edge1: f32, x: f32) -> f32 {
    if (edge1 - edge0).abs() < f32::EPSILON {
        return if x < edge0 { 0.0 } else { 1.0 };
    }
    let t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

// Paleta por fase (linear). Os valores do horizonte são deliberadamente
// PÁLIDOS e pouco saturados — é isso que faz uma crista a 400 m separar-se
// da crista a 800 m em vez de as duas lerem como a mesma maquete verde.
const ZENITH_DAY: Rgb = [0.085, 0.255, 0.62];
const HORIZON_DAY: Rgb = [0.60, 0.755, 0.90];
const ZENITH_GOLD: Rgb = [0.095, 0.16, 0.42];
const HORIZON_GOLD: Rgb = [0.44, 0.26, 0.12];
// Noite AZUL legível (a assinatura BOTW): o céu noturno não é preto — é um
// azul profundo que se lê como céu, e é esse contraste que faz a lua, as
// estrelas e as fogueiras valerem ouro. Antes era [0.008,0.016,0.055] /
// [0.035,0.062,0.135] = "só escuro" (gap nº 9 do gauntlet).
const ZENITH_NIGHT: Rgb = [0.008, 0.016, 0.050];
const HORIZON_NIGHT: Rgb = [0.030, 0.050, 0.110];
/// Azul do crepúsculo (blue hour) — o sol já se pôs, o céu ainda não é noite.
const ZENITH_TWILIGHT: Rgb = [0.020, 0.045, 0.135];
const HORIZON_TWILIGHT: Rgb = [0.20, 0.155, 0.30];

const SUN_TINT_HIGH: Rgb = [1.0, 0.96, 0.88];
const SUN_TINT_LOW: Rgb = [1.0, 0.46, 0.14];
const MOON_TINT: Rgb = [0.62, 0.72, 1.0];

/// Paleta atmosférica da hora corrente — publicada por [`atmosphere_drive`].
#[derive(Debug, Clone, Copy, Resource)]
pub struct AtmosphereState {
    /// Direção PARA o sol (mundo, Y-up) — a astronómica, pode estar abaixo.
    pub sun_dir: Vec3,
    /// Direção PARA a lua (oposta ao sol, elevada).
    pub moon_dir: Vec3,
    /// `1` dia pleno, `0` noite plena (rampa pela elevação do sol).
    pub day: f32,
    /// `1` noite plena.
    pub night: f32,
    /// Pico quando o sol raspa o horizonte (hora dourada / crepúsculo).
    pub golden: f32,
    /// Cor do céu no zénite / no horizonte (linear).
    pub zenith: Rgb,
    pub horizon: Rgb,
    /// Cor da névoa de distância (linear) — o horizonte, ligeiramente
    /// dessaturado, é o que dá a perspetiva aérea.
    pub fog: Rgb,
    /// Cor do inscattering do sol na névoa (o lado do mundo virado ao sol
    /// fica dourado ao pôr-do-sol; é o que faltava nas "paredes cinzentas").
    pub sun_tint: Rgb,
    /// Multiplicador de exposição sugerido (1 = base do mundo).
    pub exposure_scale: f32,
    /// Reforço de bloom sugerido (fontes quentes à noite / sol rasante).
    pub bloom_boost: f32,
}

impl Default for AtmosphereState {
    fn default() -> Self {
        Self {
            sun_dir: Vec3::Y,
            moon_dir: -Vec3::Y,
            day: 1.0,
            night: 0.0,
            golden: 0.0,
            zenith: ZENITH_DAY,
            horizon: HORIZON_DAY,
            fog: HORIZON_DAY,
            sun_tint: SUN_TINT_HIGH,
            exposure_scale: 1.0,
            bloom_boost: 0.0,
        }
    }
}

/// Resolve a paleta atmosférica para uma elevação solar (graus).
///
/// Separado do sistema para ser testável sem `App`: as transições (noite →
/// crepúsculo → dourado → dia) são o coração do "não é só o dia com o brilho
/// baixado".
pub fn atmosphere_for_elevation(elevation_deg: f32) -> AtmosphereState {
    // Rampas pela elevação do sol, não pelo relógio: assim o mesmo código
    // serve mundos com dawn/dusk diferentes.
    let day = smooth_step(-3.0, 10.0, elevation_deg);
    let night = 1.0 - smooth_step(-9.0, -1.0, elevation_deg);
    // Dourado: pico com o sol entre ~0° e ~10°.
    let golden = (-(((elevation_deg - 4.0) / 8.0).powi(2))).exp() * (1.0 - night);
    // Crepúsculo (blue hour): o sol já desceu mas o céu ainda tem luz.
    let twilight = smooth_step(-12.0, -2.0, elevation_deg) * (1.0 - day);

    // Noite → crepúsculo → dia, com o dourado sobreposto por cima do dia.
    let mut zenith = mix3(ZENITH_NIGHT, ZENITH_TWILIGHT, twilight);
    let mut horizon = mix3(HORIZON_NIGHT, HORIZON_TWILIGHT, twilight);
    zenith = mix3(zenith, ZENITH_DAY, day);
    horizon = mix3(horizon, HORIZON_DAY, day);
    zenith = mix3(zenith, ZENITH_GOLD, golden * 0.85);
    horizon = mix3(horizon, HORIZON_GOLD, golden * 0.9);

    // Névoa = horizonte puxado ao azul e dessaturado: a "cortina" que separa
    // as camadas de serra. De dia é fria; ao pôr-do-sol herda o dourado do
    // horizonte (mas menos saturado, senão o mundo inteiro vira laranja).
    let luma = horizon[0] * 0.25 + horizon[1] * 0.5 + horizon[2] * 0.25;
    let mut fog = mix3(horizon, [luma, luma, luma], 0.22);
    // Um empurrão de azul/violeta na névoa diurna — a assinatura da
    // perspetiva aérea do BOTW.
    fog = mix3(
        fog,
        [fog[0] * 0.90, fog[1] * 0.98, fog[2] * 1.14],
        day * 0.7,
    );

    let sun_tint = mix3(
        SUN_TINT_LOW,
        SUN_TINT_HIGH,
        smooth_step(2.0, 26.0, elevation_deg),
    );

    // Exposição: a noite fecha ~1 stop (é o que faz uma fogueira valer ouro)
    // sem cair no preto, e a golden hour abre ~meio stop. O +1.60 do r3
    // abria 1.3 stops na golden e estourava o frame inteiro assim que o
    // inscattering entrava (a névoa é somada DEPOIS da exposição).
    let exposure_scale = 0.52 + 0.48 * day + 0.55 * golden;
    // Bloom: mais à noite (lua/lanternas/fogueiras com halo) e com o sol
    // rasante (glare largo na água e nas cristas).
    let bloom_boost = 0.16 * night + 0.18 * golden;

    AtmosphereState {
        sun_dir: Vec3::Y,
        moon_dir: -Vec3::Y,
        day,
        night,
        golden,
        zenith,
        horizon,
        fog,
        sun_tint: if night > 0.5 {
            mix3(MOON_TINT, sun_tint, 1.0 - night)
        } else {
            sun_tint
        },
        exposure_scale,
        bloom_boost,
    }
}

/// Publica [`AtmosphereState`] a partir do relógio (corre depois de
/// [`sun_drive`] e DEPOIS de [`weather_drive`] — a chuva do frame entra na
/// paleta no mesmo tick).
#[allow(clippy::needless_pass_by_value)]
pub fn atmosphere_drive(
    clock: Option<Res<DayCycleState>>,
    sun: Res<SunState>,
    weather: Option<Res<WeatherState>>,
    mut atmosphere: ResMut<AtmosphereState>,
) {
    let elevation = if clock.is_some() {
        sun.elevation_deg
    } else {
        // Sem <DayCycle> o mundo é estático: dia pleno.
        45.0
    };
    let mut next = atmosphere_for_elevation(elevation);
    next.sun_dir = if sun.dir.length_squared() > 0.0 {
        sun.dir
    } else {
        Vec3::Y
    };
    // A lua nasce oposta ao sol e alta — o mesmo arco que a luz da lua usa
    // em `sun_drive`, para que disco e sombra apontem ao mesmo sítio.
    let azimuth = next.sun_dir.x.atan2(next.sun_dir.z).to_degrees();
    next.moon_dir = sky_direction(MOON_ELEVATION_DEG, azimuth + 180.0);
    // CHUVA (WS-A): fecha ~meio stop e lava o contraste — a intensidade é a
    // contínua do <Weather> (o fog ×(1+0.6·rain) vive no `biome_fog_system`).
    // Só recursos runtime: o shader em disco NUNCA é re-escrito aqui.
    let rain = weather.map(|w| w.rain.clamp(0.0, 1.0)).unwrap_or(0.0);
    next.exposure_scale *= 1.0 - 0.25 * rain;
    *atmosphere = next;
}

/// Direção unitária (mundo, Y-up) para uma elevação/azimute em graus.
fn sky_direction(elevation_deg: f32, azimuth_deg: f32) -> Vec3 {
    let (el, az) = (elevation_deg.to_radians(), azimuth_deg.to_radians());
    Vec3::new(el.cos() * az.sin(), el.sin(), el.cos() * az.cos()).normalize_or_zero()
}

/// Interpola `night` (RGB linear) → `day` em espaço linear por `t`.
fn mix_linear(night: [f32; 3], day: Color, t: f32) -> Color {
    let t = t.clamp(0.0, 1.0);
    let d = day.to_linear();
    Color::LinearRgba(bevy::color::LinearRgba::rgb(
        night[0] + (d.red - night[0]) * t,
        night[1] + (d.green - night[1]) * t,
        night[2] + (d.blue - night[2]) * t,
    ))
}

/// Keep the player inside the world disc (radius − margin).
#[allow(clippy::needless_pass_by_value)]
pub fn world_border_clamp(
    border: Option<Res<WorldBorderConfig>>,
    scene: Option<Res<InteriorSceneConfig>>,
    mut players: Query<&mut Transform, With<crate::player::Player>>,
    mut logged: Local<bool>,
) {
    let Some(border) = border else {
        return;
    };
    let limit = border.radius - border.margin;
    // radius <= margin (ex.: radius="0") não tem disco interior: o scale
    // negativo espelhava o herói para o lado oposto do mundo.
    if limit <= 0.0 {
        return;
    }
    for mut transform in &mut players {
        let pos = transform.translation;
        // Dentro da bolsa o herói está declaradamente fora do mundo: o clamp
        // devolvê-lo-ia ao vale a meio da visita.
        if scene.as_deref().is_some_and(|s| s.contains(pos.x, pos.z)) {
            continue;
        }
        let dist_sq = pos.x * pos.x + pos.z * pos.z;
        if dist_sq > limit * limit {
            let scale = limit / dist_sq.sqrt();
            transform.translation.x = pos.x * scale;
            transform.translation.z = pos.z * scale;
            // Uma vez por sessão: colado ao limite logava 60×/s e enchia o
            // ring de 1000 entradas da bridge.
            if !*logged {
                *logged = true;
                bevy::log::info!("world border: player returned inside r={limit}");
            }
        } else {
            *logged = false;
        }
    }
}

/// Point-in-polygon test on XZ (ray cast) for biome regions.
pub fn point_in_biome(polygon: &[[f32; 2]], x: f32, z: f32) -> bool {
    let mut inside = false;
    let n = polygon.len();
    if n < 3 {
        return false;
    }
    let mut j = n - 1;
    for i in 0..n {
        let (xi, zi) = (polygon[i][0], polygon[i][1]);
        let (xj, zj) = (polygon[j][0], polygon[j][1]);
        if ((zi > z) != (zj > z)) && (x < (xj - xi) * (z - zi) / (zj - zi) + xi) {
            inside = !inside;
        }
        j = i;
    }
    inside
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_daylight_factor_ramps() {
        assert_eq!(daylight_factor(100.0, 330.0, 1170.0), 0.0); // night
        assert_eq!(daylight_factor(600.0, 330.0, 1170.0), 1.0); // mid-day
        let mid = daylight_factor(300.0, 330.0, 1170.0); // dawn ramp
        assert!((0.0..1.0).contains(&mid));
        assert_eq!(daylight_factor(1231.0, 330.0, 1170.0), 0.0); // after dusk ramp
    }

    /// dawn/dusk degenerados nunca produzem NaN/inf no arco solar — o NaN
    /// propagava-se à luz direcional e ao ambiente (ronda 2 de bugs).
    #[test]
    fn test_sun_arc_survives_degenerate_dawn_dusk() {
        for (dawn, dusk) in [
            (330.0, 330.0),
            (1170.0, 330.0),
            (0.0, 1440.0),
            (1440.0, 0.0),
        ] {
            for minute in [0.0, 60.0, 330.0, 719.5, 1170.0, 1439.0] {
                let el = sun_elevation(minute, dawn, dusk, 62.0);
                let az = sun_azimuth(minute, dawn, dusk, 205.0);
                assert!(
                    el.is_finite(),
                    "elevação finita dawn={dawn} dusk={dusk} m={minute}: {el}"
                );
                assert!(
                    az.is_finite(),
                    "azimute finito dawn={dawn} dusk={dusk} m={minute}: {az}"
                );
            }
        }
        // O caso patológico real: dawn=0/dusk=1440 zerava o dusk_len e a
        // meia-noite do relógio dava 0/0.
        assert!(sun_elevation(0.0, 0.0, 1440.0, 62.0).is_finite());
        // `daylight_factor` não divide por (dusk−dawn) — locked para não
        // regredir para uma versão que divida.
        for (dawn, dusk) in [(330.0, 330.0), (1170.0, 330.0), (0.0, 1440.0)] {
            for minute in [0.0, 300.0, 600.0, 1200.0, 1439.0] {
                assert!(daylight_factor(minute, dawn, dusk).is_finite());
            }
        }
    }

    /// Lei do repo: mesma seed + mesmo índice → mesmo alvo de chuva.
    #[test]
    fn test_weather_cycle_target_is_deterministic() {
        for seed in [0u64, 42, 0xDEAD_BEEF] {
            for index in [0u64, 1, 7, 1000] {
                let a = weather_cycle_target(seed, index);
                let b = weather_cycle_target(seed, index);
                assert_eq!(a, b, "seed {seed} índice {index} tem de repetir");
                assert!((0.0..=1.0).contains(&a), "alvo em 0..1: {a}");
            }
        }
        // Índices diferentes (mesma seed) divergem — é o que faz o tempo mudar.
        assert_ne!(weather_cycle_target(7, 1), weather_cycle_target(7, 2));
        // Sementes diferentes no mesmo índice também.
        assert_ne!(weather_cycle_target(1, 5), weather_cycle_target(2, 5));
        // Enviesado para seco (quadrado do uniforme): média bem abaixo de 0.5.
        let mean: f32 = (0..256u64).map(|i| weather_cycle_target(9, i)).sum::<f32>() / 256.0;
        assert!(mean < 0.45, "média seca: {mean}");
    }

    /// O lerp de chuva aproxima-se à velocidade constante, sem overshoot.
    #[test]
    fn test_rain_toward_is_monotonic_and_clamped() {
        let mut value = 0.0_f32;
        let mut previous = value;
        for _ in 0..200 {
            value = rain_toward(value, 1.0, 0.1, 10.0);
            assert!(value >= previous, "nunca regride a subir");
            assert!(value <= 1.0, "nunca ultrapassa o alvo");
            previous = value;
        }
        assert!((value - 1.0).abs() < 1e-6, "converge: {value}");
        // Descida igualmente monótona, e dt gigante NÃO salta o alvo.
        let one_step = rain_toward(1.0, 0.0, 100.0, 10.0);
        assert_eq!(one_step, 0.0);
        // Fora de gama satura em vez de explodir.
        assert_eq!(rain_toward(-3.0, 0.5, 1.0, 10.0), 0.0 + 0.1);
        assert_eq!(rain_toward(2.0, 0.5, 1.0, 10.0), 0.9);
        // dt 0 é no-op.
        assert_eq!(rain_toward(0.3, 0.9, 0.0, 10.0), 0.3);
    }

    /// O scheduler rola um novo alvo a cada `period` s, com determinismo
    /// "mesma seed, mesmo mundo" e default 240 s.
    #[test]
    fn test_weather_scheduler_rolls_each_period() {
        let mut scheduler = WeatherScheduler::new(77, 0.0, 30.0);
        assert_eq!(scheduler.target, 0.0, "parte do estado autoral");
        let mut last = scheduler.target;
        for _ in 0..29 {
            last = scheduler.tick(1.0);
        }
        assert_eq!(scheduler.index, 0, "antes do período não rola");
        scheduler.tick(1.0);
        assert_eq!(scheduler.index, 1, "rolou no fim do 1.º período");
        assert_eq!(scheduler.target, weather_cycle_target(77, 1));
        assert!(
            (scheduler.timer - 30.0).abs() < 1e-4,
            "timer recomeçado cheio: {}",
            scheduler.timer
        );
        let _ = last;

        // dt maior que o período rola EXATAMENTE um ciclo por tick (sem
        // espiral de mortes por acumulação).
        scheduler.tick(120.0);
        assert_eq!(scheduler.index, 2);

        // Default: período 0 → 240 s.
        assert_eq!(
            WeatherScheduler::new(0, 0.0, 0.0).period,
            WEATHER_CYCLE_DEFAULT_SECS
        );
        // E a mesma seed+índice reproduce a mesma sequência inteira.
        let mut a = WeatherScheduler::new(123, 0.5, 10.0);
        let mut b = WeatherScheduler::new(123, 0.5, 10.0);
        for _ in 0..50 {
            assert_eq!(a.tick(3.0), b.tick(3.0));
        }
    }

    /// A chuva fecha exposição no [`atmosphere_drive`] (×(1−0.25·rain)).
    #[test]
    fn test_atmosphere_drive_couples_rain_to_exposure() {
        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins);
        app.init_resource::<SunState>();
        app.init_resource::<crate::worldsys::AtmosphereState>();
        app.insert_resource(WeatherState {
            wind: [0.0, 0.0],
            wind_strength: 0.0,
            clouds: 0.0,
            rain: 1.0,
            cycle: false,
        });
        app.add_systems(bevy::app::Update, atmosphere_drive);
        app.update();

        let dry = atmosphere_for_elevation(45.0).exposure_scale;
        let wet = app
            .world()
            .resource::<crate::worldsys::AtmosphereState>()
            .exposure_scale;
        assert!(
            (wet - dry * 0.75).abs() < 1e-5,
            "tempestade fecha 25 % da exposição: {wet} vs base {dry}"
        );
    }

    #[test]
    fn test_point_in_biome() {
        let square = vec![[-10.0, -10.0], [10.0, -10.0], [10.0, 10.0], [-10.0, 10.0]];
        assert!(point_in_biome(&square, 0.0, 0.0));
        assert!(!point_in_biome(&square, 50.0, 50.0));
    }

    /// A `<Group>` nested inside another must not be seated twice.
    ///
    /// Regression: `SeatOnTerrain` is on every group and writes a *local* Y,
    /// so seating a child on top of an already-seated parent added the ground
    /// height again. `simple-rpg` nests its city two deep, which put the plaza
    /// at 2x the terrain height and its props at 3x — the whole village
    /// floated ~49 m over the ground the hero walks on.
    ///
    /// With content-centre seating this fixture is the degenerate case: the
    /// outer group's single direct child sits at (0,0), so the AABB centre is
    /// the group origin and the outer group still seats exactly there.
    #[test]
    fn test_seat_statics_only_moves_the_outermost_group() {
        use crate::terrain::brush::BrushGrid;
        use crate::terrain::heightmap::HeightMapU16;
        use crate::terrain::runtime::TerrainRuntime;
        use crate::terrain::spec::TerrainSpec;

        let spec = TerrainSpec {
            world_size: 64.0,
            max_height: 100.0,
            ..TerrainSpec::default()
        };
        // Flat field at half of `max_height` → ground sits at 50 m.
        let map = HeightMapU16 {
            width: 33,
            depth: 33,
            data: vec![u16::MAX / 2; 33 * 33],
        };
        let grid = BrushGrid::from_height_map(&map, spec.world_size, spec.max_height, 0.0)
            .expect("grid builds");
        let ground = grid.sample(0.0, 0.0);
        assert!(ground > 40.0, "fixture ground is well above zero: {ground}");

        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins);
        app.insert_resource(TerrainRuntime {
            spec,
            grid: Arc::new(grid),
            water: Vec::new(),
            roads: Vec::new(),
            pads: Vec::new(),
            voxel: Arc::new(crate::terrain::voxel::VoxelField::default()),
        });
        app.add_systems(bevy::app::Update, seat_statics_once);

        let outer = app
            .world_mut()
            .spawn((Transform::default(), SeatOnTerrain))
            .id();
        let inner = app
            .world_mut()
            .spawn((Transform::default(), SeatOnTerrain, ChildOf(outer)))
            .id();
        // A prop that is not itself a group still rides the hierarchy.
        let prop = app
            .world_mut()
            .spawn((Transform::from_xyz(1.0, 0.0, 1.0), ChildOf(inner)))
            .id();

        app.update();

        let local_y =
            |app: &bevy::app::App, e| app.world().get::<Transform>(e).unwrap().translation.y;
        assert!(
            (local_y(&app, outer) - ground).abs() < 0.5,
            "the outermost group is seated on the ground"
        );
        assert_eq!(
            local_y(&app, inner),
            0.0,
            "the nested group keeps its authored local Y"
        );
        assert_eq!(local_y(&app, prop), 0.0, "props keep their authored offset");
    }

    /// A group whose content sits AWAY from its own origin must sample the
    /// ground at the content, not at the group origin.
    ///
    /// Regression: biome landmark groups are authored at `translation="0 0 0"`
    /// with children placed in world coords, so seating at the group origin
    /// landed every landmark at the plaza height (~24.6 m) — forest landmarks
    /// buried up to +65 m, desert ones floating −6..−20 m.
    #[test]
    fn test_seat_statics_samples_the_content_centre_not_the_origin() {
        use crate::terrain::brush::BrushGrid;
        use crate::terrain::heightmap::HeightMapU16;
        use crate::terrain::runtime::TerrainRuntime;
        use crate::terrain::spec::TerrainSpec;

        let spec = TerrainSpec {
            world_size: 64.0,
            max_height: 100.0,
            ..TerrainSpec::default()
        };
        // Ramp along the grid rows → ground(x,z) grows with z.
        let map = HeightMapU16 {
            width: 33,
            depth: 33,
            data: (0..33 * 33)
                .map(|i| ((i / 33) as u16).saturating_mul(1900))
                .collect(),
        };
        let grid = BrushGrid::from_height_map(&map, spec.world_size, spec.max_height, 0.0)
            .expect("grid builds");
        let at_origin = grid.sample(0.0, 0.0);
        let at_content = grid.sample(30.0, 30.0);
        assert!(
            (at_content - at_origin).abs() > 5.0,
            "fixture ramp separates the two sample points: {at_origin} vs {at_content}"
        );

        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins);
        app.insert_resource(TerrainRuntime {
            spec,
            grid: Arc::new(grid),
            water: Vec::new(),
            roads: Vec::new(),
            pads: Vec::new(),
            voxel: Arc::new(crate::terrain::voxel::VoxelField::default()),
        });
        app.add_systems(bevy::app::Update, seat_statics_once);

        let outer = app
            .world_mut()
            .spawn((Transform::default(), SeatOnTerrain))
            .id();
        app.world_mut()
            .spawn((Transform::from_xyz(30.0, 0.0, 30.0), ChildOf(outer)));

        app.update();

        let outer_y = app.world().get::<Transform>(outer).unwrap().translation.y;
        assert!(
            (outer_y - at_content).abs() < 0.1,
            "the group is seated at the height of its content ({at_content}), got {outer_y}"
        );
    }

    /// A group whose content is spread across the whole map must NOT be
    /// seated by its content centre (the centre represents nothing); each
    /// direct child is seated at its own XZ instead, and nested seatable
    /// groups recurse through the same rule.
    #[test]
    fn test_seat_statics_spread_group_seats_each_child_individually() {
        use crate::terrain::brush::BrushGrid;
        use crate::terrain::heightmap::HeightMapU16;
        use crate::terrain::runtime::TerrainRuntime;
        use crate::terrain::spec::TerrainSpec;

        let spec = TerrainSpec {
            world_size: 200.0,
            max_height: 100.0,
            ..TerrainSpec::default()
        };
        let map = HeightMapU16 {
            width: 33,
            depth: 33,
            data: (0..33 * 33)
                .map(|i| ((i / 33) as u16).saturating_mul(1900))
                .collect(),
        };
        let grid = BrushGrid::from_height_map(&map, spec.world_size, spec.max_height, 0.0)
            .expect("grid builds");
        let at_a = grid.sample(0.0, 0.0);
        let at_b = grid.sample(0.0, 70.0);
        assert!(
            (at_b - at_a).abs() > 5.0,
            "fixture ramp separates the two sample points: {at_a} vs {at_b}"
        );
        // The nested group seats by ITS content centre: inner translation
        // (0,35) + inner prop offset (2,37) → (2,72).
        let at_inner = grid.sample(2.0, 72.0);

        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins);
        app.insert_resource(TerrainRuntime {
            spec,
            grid: Arc::new(grid),
            water: Vec::new(),
            roads: Vec::new(),
            pads: Vec::new(),
            voxel: Arc::new(crate::terrain::voxel::VoxelField::default()),
        });
        app.add_systems(bevy::app::Update, seat_statics_once);

        let huge = app
            .world_mut()
            .spawn((Transform::default(), SeatOnTerrain))
            .id();
        let prop_a = app
            .world_mut()
            .spawn((Transform::from_xyz(0.0, 0.0, 0.0), ChildOf(huge)))
            .id();
        let prop_b = app
            .world_mut()
            .spawn((Transform::from_xyz(0.0, 0.0, 70.0), ChildOf(huge)))
            .id();
        // Nested seatable group, 70+ m away from the first prop: the span
        // blows past the centre threshold, so the children seat alone.
        let inner = app
            .world_mut()
            .spawn((
                Transform::from_xyz(0.0, 0.0, 35.0),
                SeatOnTerrain,
                ChildOf(huge),
            ))
            .id();
        let inner_prop = app
            .world_mut()
            .spawn((Transform::from_xyz(2.0, 0.0, 37.0), ChildOf(inner)))
            .id();

        app.update();

        let local_y =
            |app: &bevy::app::App, e| app.world().get::<Transform>(e).unwrap().translation.y;
        assert_eq!(
            local_y(&app, huge),
            0.0,
            "the spread group itself stays put"
        );
        assert!(
            (local_y(&app, prop_a) - at_a).abs() < 0.1,
            "prop A seats at its own local ground {at_a}"
        );
        assert!(
            (local_y(&app, prop_b) - at_b).abs() < 0.1,
            "prop B seats at its own local ground {at_b}"
        );
        assert!(
            (local_y(&app, inner) - at_inner).abs() < 0.1,
            "the nested group seats at its own content centre {at_inner}"
        );
        assert_eq!(
            local_y(&app, inner_prop),
            0.0,
            "the nested group's prop keeps its authored local offset"
        );
    }

    /// The day/night intensities are a ratio, not an absolute brightness.
    ///
    /// Regression: `ambient-day-intensity` is a three.js 0..1 intensity while
    /// Bevy's `AmbientLight::brightness` is in lux, and the same worlds author
    /// it in the hundreds. Writing 0.26 straight into it dropped the ambient
    /// by ~400x and left the village in the dark.
    #[test]
    fn test_daycycle_ambient_is_a_ratio_of_the_authored_brightness() {
        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins);
        app.insert_resource(GlobalAmbientLight {
            brightness: 110.0,
            ..Default::default()
        });
        app.insert_resource(DayCycleState::from_parts(
            600.0, // midday
            0.0,   // clock frozen, so the test is about the ramp only
            330.0, 1170.0, 0.26, 0.07, true, 62.0, 205.0, 8.0,
        ));
        // Sol a pino: fill de sombras (golden/night) a 0 — o teste isola o
        // ramp dia/noite do ambiente, não o lift do fill.
        app.insert_resource(SunState {
            dir: Vec3::Y,
            elevation_deg: 90.0,
            night: 0.0,
        });
        app.add_systems(bevy::app::Update, daycycle_drive);

        app.update();
        let midday = app.world().resource::<GlobalAmbientLight>().brightness;
        assert!(
            (midday - 110.0).abs() < 1.0,
            "full day keeps the authored brightness, got {midday}"
        );

        // Midnight falls to the night/day ratio of it, not to 0.07 lux.
        app.world_mut()
            .resource_mut::<DayCycleState>()
            .minute_of_day = 60.0;
        app.update();
        let night = app.world().resource::<GlobalAmbientLight>().brightness;
        let expected = 110.0 * (0.07 / 0.26);
        assert!(
            (night - expected).abs() < 1.0,
            "night is the authored brightness scaled by night/day, got {night} (expected {expected})"
        );
        assert!(night > 10.0, "night is dim, not black");
    }

    /// À noite a direcional vira lua: vem de CIMA e fica fraca.
    ///
    /// Regressão: `sun_drive` apontava a luz para o sol astronómico, que à
    /// noite está a −25°. A cena era iluminada por baixo — o chão (normal +Y)
    /// ficava preto enquanto paredes e props apanhavam luz de raspão, com o
    /// especular a varrer a imagem conforme a câmara rodava.
    #[test]
    fn test_night_light_comes_from_above_and_dims() {
        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins);
        app.init_resource::<SunState>();
        app.insert_resource(DayCycleState::from_parts(
            600.0, // midday
            0.0,   // clock frozen: the test is about the ramp only
            330.0, 1170.0, 0.26, 0.13, true, 62.0, 205.0, 8.0,
        ));
        let light = app
            .world_mut()
            .spawn((
                DirectionalLight {
                    illuminance: 10_000.0,
                    ..Default::default()
                },
                Transform::default(),
            ))
            .id();
        app.add_systems(bevy::app::Update, sun_drive);

        app.update();
        let day_lux = app
            .world()
            .entity(light)
            .get::<DirectionalLight>()
            .unwrap()
            .illuminance;
        assert!(
            (day_lux - 10_000.0).abs() < 1.0,
            "full day keeps the authored illuminance, got {day_lux}"
        );

        app.world_mut()
            .resource_mut::<DayCycleState>()
            .minute_of_day = 60.0; // midnight
        app.update();

        let sun = *app.world().resource::<SunState>();
        assert!(
            sun.elevation_deg < 0.0,
            "the astronomical sun is still below the horizon at midnight"
        );

        let entity = app.world().entity(light);
        let night_lux = entity.get::<DirectionalLight>().unwrap().illuminance;
        let expected = 10_000.0 * MOONLIGHT_RATIO;
        assert!(
            (night_lux - expected).abs() < 1.0,
            "night dims to the moonlight ratio, got {night_lux} (expected {expected})"
        );
        assert!(night_lux > 0.0, "moonlight is dim, not off");

        // A luz viaja ao longo de -Z da entidade: -Z rodado tem de apontar
        // para BAIXO (componente Y negativa) — luz vinda de cima.
        let travel = entity.get::<Transform>().unwrap().rotation * Vec3::NEG_Z;
        assert!(
            travel.y < -0.5,
            "moonlight travels downwards onto the ground, got {travel:?}"
        );
    }
}

/// Helpers puros do `place` de `<Composition>`: cota do solo e alinhamento
/// à normal.
#[cfg(test)]
mod place_tests {
    use super::*;

    #[test]
    fn test_align_up_to_identity_when_normal_is_up() {
        let authored = Quat::from_rotation_y(std::f32::consts::FRAC_PI_2);
        assert_eq!(align_up_to(Vec3::Y, authored), authored);
    }

    #[test]
    fn test_align_up_to_tilts_preserving_yaw() {
        // Normal a 45° no plano XZ+Y (ladeira suave).
        let normal = Vec3::new(0.0, 1.0, 1.0).normalize();
        let yaw = Quat::from_rotation_y(std::f32::consts::FRAC_PI_2);
        let aligned = align_up_to(normal, yaw);
        // O +Y local do resultado aponta na direção da normal.
        let up = aligned * Vec3::Y;
        assert!((up - normal).length() < 1e-4, "up={up:?} normal={normal:?}");
        // O heading (projeção XZ do forward) continua a apontar para o yaw
        // autoral — a inclinação não roda o objeto à volta de Y.
        let forward_world = aligned * Vec3::NEG_Z;
        let forward_yaw = yaw * Vec3::NEG_Z;
        let angle = |v: Vec3| v.z.atan2(v.x);
        let d = (angle(forward_world) - angle(forward_yaw)).rem_euclid(std::f32::consts::TAU);
        assert!(
            d < 0.2 || d > std::f32::consts::TAU - 0.2,
            "yaw preservado: {d} rad de desvio"
        );
    }

    #[test]
    fn test_place_ground_y_uses_flat_fixture() {
        use crate::terrain::brush::BrushGrid;
        use crate::terrain::heightmap::HeightMapU16;
        use crate::terrain::runtime::TerrainRuntime;
        use crate::terrain::spec::TerrainSpec;
        use std::sync::Arc;

        let spec = TerrainSpec {
            world_size: 64.0,
            max_height: 100.0,
            ..TerrainSpec::default()
        };
        let map = HeightMapU16 {
            width: 33,
            depth: 33,
            data: vec![u16::MAX / 2; 33 * 33],
        };
        let grid = BrushGrid::from_height_map(&map, spec.world_size, spec.max_height, 0.0)
            .expect("grid builds");
        let runtime = TerrainRuntime {
            spec,
            grid: Arc::new(grid),
            water: Vec::new(),
            roads: Vec::new(),
            pads: Vec::new(),
            voxel: Arc::new(crate::terrain::voxel::VoxelField::default()),
        };
        let ground = runtime.sample(0.0, 0.0);
        // `place` define a cota EXATA (sobe e desce), ao contrário do seating.
        assert_eq!(place_ground_y(&runtime, 0.0, 0.0, 0.0), ground);
        assert_eq!(place_ground_y(&runtime, 0.0, 0.0, 80.0), ground);
    }

    /// A bolsa só isenta do clamp/DENTRO do retângulo — e um retângulo
    /// degenerado (min == max) não isenta nada.
    #[test]
    fn test_interior_scene_contains() {
        let scene = InteriorSceneConfig {
            min: [2564.0, 2570.0],
            max: [2752.0, 2742.0],
        };
        assert!(scene.contains(2658.0, 2656.0), "centro");
        assert!(scene.contains(2564.0, 2570.0), "canto (inclusivo)");
        assert!(!scene.contains(2563.0, 2656.0), "fora por x");
        assert!(!scene.contains(2658.0, 2743.0), "fora por z");
        // O mundo (world-size 4000) fica longe: a bolsa é mesmo fora.
        assert!(!scene.contains(0.0, 0.0));
        assert!(!scene.contains(860.0, 780.0), "o Alto de Vael não é bolsa");
    }
}
