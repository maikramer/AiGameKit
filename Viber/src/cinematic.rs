//! Cinemáticas — sequências de câmara com legendas, títulos, fades e sons, declaradas
//! no XML do mundo e tocadas pelo jogo, por scripts, pelo bridge/CLI e pelo editor.
//!
//! ```xml
//! <Cinematic id="prologo" skippable="true" letterbox="true">
//!   <Fade at="0" duration="1.5" from="1" to="0" />
//!   <Shot duration="6" from="0 90 160" to="0 70 90" look="0 40 0" ease="in-out" />
//!   <Shot duration="4" anchor="player" from="4 3 6" look="0 1.6 0" />
//!   <Title at="1" duration="4" sub="Capítulo I">O Silêncio de Vael</Title>
//!   <Line at="2" duration="4" speaker="Ancião Edmar">Há trinta invernos…</Line>
//!   <Sound at="0.5" clip="roar" />
//! </Cinematic>
//! ```
//!
//! * `<Shot>` corre em SEQUÊNCIA (cada um começa onde o anterior acaba): a câmara
//!   vai de `from`→`to` enquanto olha de `look`→`look-to` (omissos = estáticos),
//!   com `ease` (`linear|in|out|in-out`) e `fov` opcional. `anchor` torna as
//!   posições RELATIVAS: `player`, `self` (a entidade passada ao tocar — o chefe
//!   que acordou) ou o `name` de uma entidade.
//! * `<Line>`, `<Title>`, `<Fade>` e `<Sound>` têm tempo ABSOLUTO (`at`, s).
//!
//! Durante a reprodução: o input do herói fica bloqueado (`MenusOpen::cinematic`),
//! o herói fica invulnerável, o HUD esconde-se e aparecem as barras de cinema.
//! [Esc]/[Espaço]/[Enter] saltam (com `skippable`). No fim sai um evento
//! `{type="cinematic_end", id, skipped}` para os scripts.
//!
//! Superfícies: `viber.cinematic(id[, anchor])` (Lua), `viber.cinematic.*` (bridge),
//! `viber cinematic list|show|play|stop|shot` (CLI) e o Inspector do editor
//! (pré-visualizar + "plano da câmara").

use std::collections::HashMap;

use bevy::prelude::*;
use serde_json::{Value, json};

use crate::xml::XmlNode;
use crate::xml::values::parse_vec3;

// ── dados ───────────────────────────────────────────────────────────────

/// Curva de um plano.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ease {
    Linear,
    In,
    Out,
    InOut,
}

impl Ease {
    fn parse(text: &str) -> Result<Self, String> {
        Ok(match text.trim().to_ascii_lowercase().as_str() {
            "linear" => Ease::Linear,
            "in" => Ease::In,
            "out" => Ease::Out,
            "in-out" | "inout" | "" => Ease::InOut,
            other => {
                return Err(format!(
                    "ease `{other}` desconhecido (linear|in|out|in-out)"
                ));
            }
        })
    }

    pub fn apply(self, t: f32) -> f32 {
        let t = t.clamp(0.0, 1.0);
        match self {
            Ease::Linear => t,
            Ease::In => t * t,
            Ease::Out => 1.0 - (1.0 - t) * (1.0 - t),
            Ease::InOut => t * t * (3.0 - 2.0 * t),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Ease::Linear => "linear",
            Ease::In => "in",
            Ease::Out => "out",
            Ease::InOut => "in-out",
        }
    }
}

/// Referencial das posições de um plano.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum Anchor {
    World,
    Player,
    /// A entidade passada ao tocar (`viber.cinematic(id, viber.self_id())`).
    SelfEntity,
    /// Uma entidade pelo `name`.
    Named(String),
}

impl Anchor {
    fn parse(text: Option<&str>) -> Anchor {
        match text.map(str::trim) {
            None | Some("") | Some("world") => Anchor::World,
            Some("player") => Anchor::Player,
            Some("self") => Anchor::SelfEntity,
            Some(name) => Anchor::Named(name.to_owned()),
        }
    }

    fn name(&self) -> String {
        match self {
            Anchor::World => "world".into(),
            Anchor::Player => "player".into(),
            Anchor::SelfEntity => "self".into(),
            Anchor::Named(name) => name.clone(),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Shot {
    pub start: f32,
    pub duration: f32,
    pub from: Vec3,
    pub to: Vec3,
    pub look: Vec3,
    pub look_to: Vec3,
    pub ease: Ease,
    pub anchor: Anchor,
    pub fov: Option<f32>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Line {
    pub at: f32,
    pub duration: f32,
    pub speaker: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TitleCard {
    pub at: f32,
    pub duration: f32,
    pub text: String,
    pub sub: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Fade {
    pub at: f32,
    pub duration: f32,
    pub from: f32,
    pub to: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SoundCue {
    pub at: f32,
    pub clip: String,
}

/// Uma cinemática lida do XML.
#[derive(Debug, Clone, PartialEq)]
pub struct CinematicSpec {
    pub id: String,
    pub skippable: bool,
    pub letterbox: bool,
    pub hide_hud: bool,
    pub shots: Vec<Shot>,
    pub lines: Vec<Line>,
    pub titles: Vec<TitleCard>,
    pub fades: Vec<Fade>,
    pub sounds: Vec<SoundCue>,
    /// Fim: o maior entre o último plano e o último elemento temporizado.
    pub duration: f32,
}

fn num(node: &XmlNode, key: &str, default: f32) -> Result<f32, String> {
    match node.attr(key) {
        None => Ok(default),
        Some(raw) => raw
            .trim()
            .parse::<f32>()
            .ok()
            .filter(|v| v.is_finite())
            .ok_or_else(|| format!("<{}> {key}=\"{raw}\" não é um número", node.tag)),
    }
}

fn flag(node: &XmlNode, key: &str, default: bool) -> bool {
    match node.attr(key).map(str::trim) {
        Some("false" | "0" | "no") => false,
        Some("true" | "1" | "yes") => true,
        _ => default,
    }
}

fn vec3(node: &XmlNode, key: &str) -> Result<Option<Vec3>, String> {
    node.attr(key)
        .map(|raw| {
            parse_vec3(raw, key)
                .map(Vec3::from)
                .map_err(|e| format!("<{}> {e:#}", node.tag))
        })
        .transpose()
}

/// Lê um `<Cinematic>`. Erros dizem o elemento e o atributo.
pub fn parse(node: &XmlNode) -> Result<CinematicSpec, String> {
    let id = node
        .attr("id")
        .map(str::trim)
        .filter(|id| !id.is_empty())
        .ok_or("<Cinematic> sem id")?
        .to_owned();
    let mut spec = CinematicSpec {
        id,
        skippable: flag(node, "skippable", true),
        letterbox: flag(node, "letterbox", true),
        hide_hud: flag(node, "hide-hud", true),
        shots: Vec::new(),
        lines: Vec::new(),
        titles: Vec::new(),
        fades: Vec::new(),
        sounds: Vec::new(),
        duration: 0.0,
    };
    let mut clock = 0.0f32;
    for child in &node.children {
        match child.tag.to_ascii_lowercase().as_str() {
            "shot" => {
                let duration = num(child, "duration", 4.0)?;
                if duration <= 0.0 {
                    return Err(format!("<Shot> #{} com duration ≤ 0", spec.shots.len()));
                }
                let from = vec3(child, "from")?.ok_or("<Shot> sem from")?;
                let look = vec3(child, "look")?.ok_or("<Shot> sem look")?;
                spec.shots.push(Shot {
                    start: clock,
                    duration,
                    from,
                    to: vec3(child, "to")?.unwrap_or(from),
                    look,
                    look_to: vec3(child, "look-to")?.unwrap_or(look),
                    ease: Ease::parse(child.attr("ease").unwrap_or(""))?,
                    anchor: Anchor::parse(child.attr("anchor")),
                    fov: child
                        .attr("fov")
                        .map(|_| num(child, "fov", 60.0))
                        .transpose()?,
                });
                clock += duration;
            }
            "line" => spec.lines.push(Line {
                at: num(child, "at", 0.0)?,
                duration: num(child, "duration", 3.5)?,
                speaker: child.attr("speaker").unwrap_or("").trim().to_owned(),
                text: child.text.trim().to_owned(),
            }),
            "title" => spec.titles.push(TitleCard {
                at: num(child, "at", 0.0)?,
                duration: num(child, "duration", 3.0)?,
                text: child.text.trim().to_owned(),
                sub: child.attr("sub").unwrap_or("").trim().to_owned(),
            }),
            "fade" => spec.fades.push(Fade {
                at: num(child, "at", 0.0)?,
                duration: num(child, "duration", 1.0)?.max(0.01),
                from: num(child, "from", 1.0)?.clamp(0.0, 1.0),
                to: num(child, "to", 0.0)?.clamp(0.0, 1.0),
            }),
            "sound" => spec.sounds.push(SoundCue {
                at: num(child, "at", 0.0)?,
                clip: child.attr("clip").unwrap_or("").trim().to_owned(),
            }),
            other => {
                return Err(format!(
                    "<Cinematic id=\"{}\">: filho <{other}> desconhecido (Shot|Line|Title|Fade|Sound)",
                    spec.id
                ));
            }
        }
    }
    if spec.shots.is_empty() {
        return Err(format!("<Cinematic id=\"{}\"> sem nenhum <Shot>", spec.id));
    }
    let timed_end = spec
        .lines
        .iter()
        .map(|l| l.at + l.duration)
        .chain(spec.titles.iter().map(|t| t.at + t.duration))
        .chain(spec.fades.iter().map(|f| f.at + f.duration))
        .fold(0.0f32, f32::max);
    spec.duration = clock.max(timed_end);
    spec.sounds.sort_by(|a, b| a.at.total_cmp(&b.at));
    Ok(spec)
}

impl CinematicSpec {
    /// O plano ativo em `t` (o último segura a pose depois de acabar).
    pub fn shot_at(&self, t: f32) -> Option<(&Shot, f32)> {
        let shot = self
            .shots
            .iter()
            .rev()
            .find(|s| t >= s.start)
            .or_else(|| self.shots.first())?;
        let k = ((t - shot.start) / shot.duration).clamp(0.0, 1.0);
        Some((shot, shot.ease.apply(k)))
    }

    /// Pose da câmara em `t` (olho, alvo, fov) dado o ponto-âncora do plano.
    pub fn pose_at(
        &self,
        t: f32,
        anchor_pos: impl Fn(&Anchor) -> Vec3,
    ) -> Option<(Vec3, Vec3, Option<f32>)> {
        let (shot, k) = self.shot_at(t)?;
        let base = anchor_pos(&shot.anchor);
        Some((
            base + shot.from.lerp(shot.to, k),
            base + shot.look.lerp(shot.look_to, k),
            shot.fov,
        ))
    }

    /// Escuridão do ecrã em `t` (0 = imagem, 1 = preto): o último fade que já começou.
    pub fn fade_at(&self, t: f32) -> f32 {
        self.fades
            .iter()
            .filter(|f| t >= f.at)
            .max_by(|a, b| a.at.total_cmp(&b.at))
            .map(|f| {
                let k = ((t - f.at) / f.duration).clamp(0.0, 1.0);
                f.from + (f.to - f.from) * k
            })
            .unwrap_or(0.0)
    }

    /// A fala visível em `t`.
    pub fn line_at(&self, t: f32) -> Option<&Line> {
        self.lines
            .iter()
            .filter(|l| t >= l.at && t < l.at + l.duration)
            .max_by(|a, b| a.at.total_cmp(&b.at))
    }

    /// O título visível em `t` e a sua opacidade (entra/sai em 0,6 s).
    pub fn title_at(&self, t: f32) -> Option<(&TitleCard, f32)> {
        self.titles
            .iter()
            .filter(|c| t >= c.at && t < c.at + c.duration)
            .max_by(|a, b| a.at.total_cmp(&b.at))
            .map(|card| {
                let local = t - card.at;
                let edge = 0.6f32.min(card.duration * 0.5);
                let alpha = (local / edge)
                    .min((card.duration - local) / edge)
                    .clamp(0.0, 1.0);
                (card, alpha)
            })
    }

    /// JSON estável (CLI `viber cinematic show`, bridge).
    pub fn to_json(&self) -> Value {
        let v = |p: Vec3| json!([p.x, p.y, p.z]);
        json!({
            "id": self.id,
            "duration": self.duration,
            "skippable": self.skippable,
            "letterbox": self.letterbox,
            "hide_hud": self.hide_hud,
            "shots": self.shots.iter().map(|s| json!({
                "start": s.start, "duration": s.duration, "from": v(s.from), "to": v(s.to),
                "look": v(s.look), "look_to": v(s.look_to), "ease": s.ease.name(),
                "anchor": s.anchor.name(), "fov": s.fov,
            })).collect::<Vec<_>>(),
            "lines": self.lines.iter().map(|l| json!({"at": l.at, "duration": l.duration, "speaker": l.speaker, "text": l.text})).collect::<Vec<_>>(),
            "titles": self.titles.iter().map(|c| json!({"at": c.at, "duration": c.duration, "text": c.text, "sub": c.sub})).collect::<Vec<_>>(),
            "fades": self.fades.iter().map(|f| json!({"at": f.at, "duration": f.duration, "from": f.from, "to": f.to})).collect::<Vec<_>>(),
            "sounds": self.sounds.iter().map(|s| json!({"at": s.at, "clip": s.clip})).collect::<Vec<_>>(),
        })
    }
}

/// Todas as cinemáticas de uma árvore XML (pré-ordem), com os erros de parse.
pub fn collect(nodes: &[XmlNode]) -> (Vec<CinematicSpec>, Vec<String>) {
    fn rec(nodes: &[XmlNode], out: &mut Vec<CinematicSpec>, errors: &mut Vec<String>) {
        for node in nodes {
            if node.tag.eq_ignore_ascii_case("cinematic") {
                match parse(node) {
                    Ok(spec) => out.push(spec),
                    Err(error) => errors.push(error),
                }
            } else {
                rec(&node.children, out, errors);
            }
        }
    }
    let (mut out, mut errors) = (Vec::new(), Vec::new());
    rec(nodes, &mut out, &mut errors);
    (out, errors)
}

/// O `<Shot>` que um agente/editor grava a partir de uma pose de câmara.
pub fn shot_xml(eye: Vec3, look: Vec3, duration: f32) -> String {
    let f = |p: Vec3| crate::scene::pose::format_vec3(p);
    format!(
        r#"<Shot duration="{}" from="{}" look="{}" />"#,
        crate::scene::pose::format_num(duration),
        f(eye),
        f(look)
    )
}

// ── runtime ─────────────────────────────────────────────────────────────

/// Biblioteca das cinemáticas do mundo (do XML, no arranque).
#[derive(Resource, Debug, Default, Clone)]
pub struct CinematicLibrary(pub HashMap<String, CinematicSpec>);

/// Pedido para tocar. `inline` (editor/bridge) toca a versão do documento em vez da
/// da biblioteca — as edições aparecem sem recarregar o mundo.
#[derive(Message, Debug, Clone)]
pub struct PlayCinematic {
    pub id: String,
    pub anchor: Option<Entity>,
    pub inline: Option<CinematicSpec>,
}

/// Pedido para parar (conta como salto).
#[derive(Message, Debug, Clone, Default)]
pub struct StopCinematic;

/// Fim de uma cinemática.
#[derive(Message, Debug, Clone)]
pub struct CinematicEnded {
    pub id: String,
    pub skipped: bool,
}

/// O que está a tocar.
#[derive(Resource, Debug, Default)]
pub struct CinematicPlayback {
    pub active: Option<Active>,
}

#[derive(Debug, Clone)]
pub struct Active {
    pub spec: CinematicSpec,
    pub t: f32,
    pub self_anchor: Option<Entity>,
    named: HashMap<String, Option<Entity>>,
    sounds_fired: usize,
    /// Posição do herói ao começar (âncora `player` estável — o herói pode ser
    /// empurrado durante a cena e a câmara não deve tremer com ele).
    player_at_start: Vec3,
    /// Visibilidade autoral dos nós de HUD escondidos.
    hidden: Vec<(Entity, Visibility)>,
    /// Espera em negro (início, corte longo, saída) até o streaming acabar.
    hold: Option<Hold>,
    /// Tempos dos cortes LONGOS (fronteiras entre planos com o olho a saltar
    /// mais de [`CUT_DISTANCE`]) — calculados no 1.º frame da câmara.
    cuts: Option<Vec<f32>>,
    /// O próximo frame da câmara tem de limpar o histórico temporal (TAA).
    reset_taa: bool,
    /// Pedido de reset da exposição automática (início de uma espera).
    reset_exposure: bool,
    /// Último olho da cena e posição do herói (decidir a espera da saída).
    last_eye: Option<Vec3>,
    /// Ponto olhado no último frame (foco do DoF durante a cena).
    last_look: Option<Vec3>,
    player_now: Vec3,
    /// Fase de saída: a câmara já é do jogo, o ecrã espera em negro.
    outro: bool,
    outro_snapped: bool,
    skipped: bool,
}

/// Espera em negro com fade de entrada.
#[derive(Debug, Clone, Copy, Default)]
struct Hold {
    elapsed: f32,
    frames: u32,
    /// `Some(k)` = a desvanecer (0→1); `None` = ainda à espera do streaming.
    fading: Option<f32>,
}

/// Um corte cujo olho salta mais do que isto (m) passa por negro + streaming.
pub const CUT_DISTANCE: f32 = 25.0;
/// Espera mínima/máxima em negro (s) e duração do fade de entrada.
const HOLD_MIN: f32 = 0.6;
/// Frames mínimos de espera: o medidor de exposição (recomeçado no início da
/// espera) precisa de HISTOGRAMAS, e com o streaming a construir o motor corre
/// a poucos fps — segundos sozinhos não chegam.
const HOLD_MIN_FRAMES: u32 = 12;
/// Velocidade da exposição automática (stops/s) durante a espera em negro: o
/// medidor vê a cena por baixo do véu e tem de chegar ao alvo ANTES do fade —
/// às velocidades de jogo (0,5–1,2 stops/s) o corte saía cinzento 1–2 s.
const EXPOSURE_SNAP_SPEED: f32 = 40.0;
/// Um salto do herói maior do que isto num frame (m) conta como teleporte.
const TELEPORT_JUMP: f32 = 20.0;
/// Quanto tempo a exposição fica instantânea depois de um teleporte (s).
const TELEPORT_SNAP_SECS: f32 = 0.6;
const HOLD_MAX: f32 = 3.5;
/// Teto da espera INICIAL: a primeira cena do jogo costuma tocar logo no
/// arranque, com os pipelines das malhas ainda a compilar (segundos).
const HOLD_MAX_START: f32 = 7.0;
const HOLD_FADE: f32 = 0.7;
/// Antes de um corte longo o ecrã escurece nestes segundos.
const PRE_CUT_RAMP: f32 = 0.3;

/// Orçamentos de streaming (terreno, LOD de malhas) multiplicados — vale
/// enquanto uma cinemática espera em negro por um destino distante.
#[derive(Resource, Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct StreamingBoost(pub bool);

impl Active {
    /// Escuridão total do véu (fades autorais, esperas e rampas de corte).
    pub fn veil(&self) -> f32 {
        let mut dark = self.spec.fade_at(self.t);
        if let Some(hold) = self.hold {
            dark = dark.max(match hold.fading {
                None => 1.0,
                Some(k) => 1.0 - k,
            });
        } else if let Some(cuts) = &self.cuts
            && let Some(next) = cuts.iter().find(|c| **c > self.t)
            && next - self.t < PRE_CUT_RAMP
        {
            dark = dark.max(1.0 - (next - self.t) / PRE_CUT_RAMP);
        }
        dark.clamp(0.0, 1.0)
    }

    fn start_hold(&mut self) {
        self.hold = Some(Hold::default());
        self.reset_taa = true;
        self.reset_exposure = true;
    }
}

impl CinematicPlayback {
    pub fn playing(&self) -> Option<&str> {
        self.active.as_ref().map(|a| a.spec.id.as_str())
    }

    /// Distância de foco da cena (olho → ponto olhado) e se é um corte (o foco
    /// salta em vez de deslizar). `None` fora de uma cena ou na saída.
    pub fn focus(&self) -> Option<(f32, bool)> {
        let active = self.active.as_ref().filter(|a| !a.outro)?;
        let distance = active.last_eye?.distance(active.last_look?);
        Some((distance.max(0.5), active.hold.is_some()))
    }
}

/// Raízes de UI (HUD) que a cena esconde.
type HudRoots<'w, 's> = Query<
    'w,
    's,
    (Entity, &'static mut Visibility),
    (With<Node>, Without<ChildOf>, Without<CinematicOverlay>),
>;

/// Raiz do overlay de cinema (barras, legenda, título, fade).
#[derive(Component)]
struct CinematicOverlay;
#[derive(Component)]
struct LetterboxBar;
#[derive(Component)]
struct FadeVeil;
#[derive(Component)]
struct TitleText;
#[derive(Component)]
struct TitleSub;
#[derive(Component)]
struct LineSpeaker;
#[derive(Component)]
struct LineText;
#[derive(Component)]
struct LinePanel;

/// Altura das barras de cinema (% do ecrã).
const LETTERBOX_PCT: f32 = 11.0;

pub struct CinematicPlugin;

impl Plugin for CinematicPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<CinematicLibrary>()
            .init_resource::<CinematicPlayback>()
            .init_resource::<StreamingBoost>()
            .add_message::<PlayCinematic>()
            .add_message::<StopCinematic>()
            .add_message::<CinematicEnded>()
            .add_message::<crate::ambient::SfxEvent>()
            .add_systems(
                Update,
                (
                    start_cinematics,
                    tick_cinematic,
                    ensure_overlay,
                    drive_overlay,
                )
                    .chain(),
            )
            .add_systems(
                PostUpdate,
                drive_camera.before(bevy::transform::TransformSystems::Propagate),
            );
        // Pipelines por compilar (mundo de render → main): uma malha cujo
        // pipeline ainda compila NÃO se desenha — no arranque o ecrã é só céu
        // e névoa (o "cinzento"). A espera em negro conta com isto também.
        let pending = PipelinesPending::default();
        app.insert_resource(pending.clone());
        if let Some(render_app) = app.get_sub_app_mut(bevy::render::RenderApp) {
            render_app.insert_resource(pending).add_systems(
                bevy::render::Render,
                count_pending_pipelines.in_set(bevy::render::RenderSystems::Cleanup),
            );
        }
    }
}

/// Nº de pipelines de render à espera de compilar (escrito pelo mundo de render).
#[derive(Resource, Debug, Default, Clone)]
pub struct PipelinesPending(pub std::sync::Arc<std::sync::atomic::AtomicUsize>);

impl PipelinesPending {
    pub fn get(&self) -> usize {
        self.0.load(std::sync::atomic::Ordering::Relaxed)
    }
}

fn count_pending_pipelines(
    cache: Res<bevy::render::render_resource::PipelineCache>,
    pending: Res<PipelinesPending>,
) {
    pending.0.store(
        cache.waiting_pipelines().count(),
        std::sync::atomic::Ordering::Relaxed,
    );
}

/// Arranque: a biblioteca a partir dos nós do mundo carregado.
pub fn library_from_nodes(nodes: &[XmlNode]) -> CinematicLibrary {
    let (specs, errors) = collect(nodes);
    for error in errors {
        warn!(target: "viber::cinematic", "{error}");
    }
    CinematicLibrary(specs.into_iter().map(|s| (s.id.clone(), s)).collect())
}

#[allow(clippy::too_many_arguments)]
fn start_cinematics(
    mut requests: MessageReader<PlayCinematic>,
    library: Res<CinematicLibrary>,
    mut playback: ResMut<CinematicPlayback>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    named: Query<(Entity, &Name)>,
    mut ui_nodes: HudRoots<'_, '_>,
    mode: Option<Res<crate::app::RunMode>>,
    mut ended: MessageWriter<CinematicEnded>,
) {
    let Some(request) = requests.read().last().cloned() else {
        return;
    };
    let Some(spec) = request
        .inline
        .clone()
        .or_else(|| library.0.get(&request.id).cloned())
    else {
        warn!(target: "viber::cinematic", "cinemática `{}` não existe", request.id);
        return;
    };
    // Uma cinemática nova interrompe a corrente (conta como salto dela).
    if let Some(previous) = playback.active.take() {
        restore_hud(&mut ui_nodes, &previous.hidden);
        ended.write(CinematicEnded {
            id: previous.spec.id,
            skipped: true,
        });
    }
    let mut wanted: HashMap<String, Option<Entity>> = HashMap::new();
    for shot in &spec.shots {
        if let Anchor::Named(name) = &shot.anchor {
            wanted.insert(name.clone(), None);
        }
    }
    if !wanted.is_empty() {
        for (entity, name) in &named {
            if let Some(slot) = wanted.get_mut(name.as_str())
                && slot.is_none()
            {
                *slot = Some(entity);
            }
        }
    }
    // O HUD só se esconde no jogo — no editor os nós de topo são o próprio editor.
    let editing = mode.as_deref() == Some(&crate::app::RunMode::Edit);
    let mut hidden = Vec::new();
    if spec.hide_hud && !editing {
        for (entity, mut visibility) in &mut ui_nodes {
            hidden.push((entity, *visibility));
            *visibility = Visibility::Hidden;
        }
    }
    info!(target: "viber::cinematic", "▶ {} ({:.1} s)", spec.id, spec.duration);
    playback.active = Some(Active {
        player_at_start: players
            .iter()
            .next()
            .map(GlobalTransform::translation)
            .unwrap_or_default(),
        spec,
        t: 0.0,
        self_anchor: request.anchor,
        named: wanted,
        sounds_fired: 0,
        hidden,
        // Começa SEMPRE em negro até o destino estar pronto (o 1.º plano pode
        // estar a 1 km do herói).
        hold: Some(Hold::default()),
        cuts: None,
        reset_taa: true,
        reset_exposure: true,
        last_eye: None,
        last_look: None,
        player_now: Vec3::ZERO,
        outro: false,
        outro_snapped: false,
        skipped: false,
    });
}

fn restore_hud(ui_nodes: &mut HudRoots<'_, '_>, hidden: &[(Entity, Visibility)]) {
    for (entity, previous) in hidden {
        if let Ok((_, mut visibility)) = ui_nodes.get_mut(*entity) {
            *visibility = *previous;
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn tick_cinematic(
    real: Res<Time<Real>>,
    keys: Option<Res<ButtonInput<KeyCode>>>,
    mut stops: MessageReader<StopCinematic>,
    mut playback: ResMut<CinematicPlayback>,
    mut boost: ResMut<StreamingBoost>,
    terrain: Option<Res<crate::terrain::plugin::ChunkLodState>>,
    mesh_lod: Option<Res<crate::render_lod::MeshLodStats>>,
    grass: Option<Res<crate::grass::GrassField>>,
    pipelines: Option<Res<PipelinesPending>>,
    mut menus: Option<ResMut<crate::menus::MenusOpen>>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
    mut ended: MessageWriter<CinematicEnded>,
    mut events: Option<ResMut<crate::luau::ScriptEventQueue>>,
    mut ui_nodes: HudRoots<'_, '_>,
    players: Query<Entity, With<crate::player::Player>>,
    mut commands: Commands,
) {
    let stop_requested = stops.read().count() > 0;
    let playing = playback.active.is_some();
    if let Some(menus) = menus.as_deref_mut()
        && menus.cinematic != playing
    {
        menus.cinematic = playing;
    }
    let Some(active) = playback.active.as_mut() else {
        boost.set_if_neq(StreamingBoost(false));
        return;
    };
    let dt = real.delta_secs();
    // Herói intocável enquanto a câmara não é dele.
    for player in &players {
        commands
            .entity(player)
            .try_insert(crate::feedback::Invulnerable { timer: 0.25 });
    }
    // O mundo em volta da câmara ainda se está a construir?
    let streaming = terrain.as_deref().is_some_and(|t| t.busy)
        || mesh_lod.as_deref().is_some_and(|m| m.pending > 0)
        || grass.as_deref().is_some_and(|g| g.busy)
        || pipelines.as_deref().is_some_and(|p| p.get() > 0);
    let skip_pressed = keys
        .as_deref()
        .is_some_and(|k| k.any_just_pressed([KeyCode::Escape, KeyCode::Space, KeyCode::Enter]));
    if !active.outro
        && (stop_requested || (active.spec.skippable && active.t > 0.4 && skip_pressed))
    {
        active.skipped = true;
        active.t = active.spec.duration;
        active.hold = None;
    }

    // 1. Espera em negro (início, corte longo, saída).
    let mut finish = false;
    if let Some(hold) = active.hold.as_mut() {
        hold.elapsed += dt;
        hold.frames += 1;
        match hold.fading {
            None => {
                let ready =
                    hold.elapsed >= HOLD_MIN && hold.frames >= HOLD_MIN_FRAMES && !streaming;
                let cap = if active.t <= 0.0 && !active.outro {
                    HOLD_MAX_START
                } else {
                    HOLD_MAX
                };
                if ready || hold.elapsed >= cap {
                    info!(
                        target: "viber::cinematic",
                        "{} t={:.2}: espera {:.2} s ({}) → fade",
                        active.spec.id,
                        active.t,
                        hold.elapsed,
                        if ready { "streaming pronto" } else { "teto" }
                    );
                    hold.fading = Some(0.0);
                    active.reset_taa = true;
                }
            }
            Some(k) => {
                let k = k + dt / HOLD_FADE;
                if k >= 1.0 {
                    active.hold = None;
                    finish = active.outro;
                } else {
                    hold.fading = Some(k);
                }
            }
        }
    }
    let waiting = active.hold.is_some_and(|h| h.fading.is_none());
    boost.set_if_neq(StreamingBoost(waiting));

    // 2. O relógio só anda fora da espera (o que está em negro não conta).
    if !waiting && !active.outro {
        let before = active.t;
        active.t += dt;
        if let Some(cut) = active
            .cuts
            .as_ref()
            .and_then(|cuts| cuts.iter().copied().find(|c| *c > before && *c <= active.t))
        {
            active.t = cut;
            info!(target: "viber::cinematic", "{} t={cut:.2}: corte longo → negro", active.spec.id);
            active.start_hold();
        }
    }
    while let Some(cue) = active.spec.sounds.get(active.sounds_fired) {
        if cue.at > active.t {
            break;
        }
        active.sounds_fired += 1;
        if let Some(clip) = crate::luau::sfx_clip_from_str(&cue.clip) {
            sfx.write(crate::ambient::SfxEvent {
                clip,
                position: None,
            });
        }
    }

    // 3. Fim: se a câmara está longe do herói, sai por negro (a câmara de jogo
    // volta para trás dele por baixo do véu); senão acaba já.
    if !active.outro && active.t >= active.spec.duration {
        let far = active
            .last_eye
            .is_some_and(|eye| eye.distance(active.player_now) > CUT_DISTANCE);
        if far {
            active.outro = true;
            active.start_hold();
        } else {
            finish = true;
        }
    }
    if !finish {
        return;
    }
    let done = playback.active.take().expect("ativa acima");
    boost.set_if_neq(StreamingBoost(false));
    restore_hud(&mut ui_nodes, &done.hidden);
    if let Some(menus) = menus.as_deref_mut() {
        menus.cinematic = false;
    }
    let skipped = done.skipped;
    info!(target: "viber::cinematic", "■ {}{}", done.spec.id, if skipped { " (saltada)" } else { "" });
    if let Some(events) = events.as_deref_mut() {
        events.push(crate::luau::ScriptGameEvent::CinematicEnd {
            id: done.spec.id.clone(),
            skipped,
        });
    }
    ended.write(CinematicEnded {
        id: done.spec.id,
        skipped,
    });
}

/// A câmara que a cena conduz: a de editor quando existe, senão a do jogo.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn drive_camera(
    mut playback: ResMut<CinematicPlayback>,
    mut editor: Query<
        (
            &mut Transform,
            Option<&mut Projection>,
            Option<&mut bevy::anti_alias::taa::TemporalAntiAliasing>,
        ),
        (
            With<crate::editor_camera::EditorCamera>,
            Without<crate::recipes::spawn::OrbitCamera>,
        ),
    >,
    mut game: Query<
        (
            &mut Transform,
            Option<&mut Projection>,
            Option<&mut bevy::anti_alias::taa::TemporalAntiAliasing>,
            &crate::recipes::spawn::OrbitCamera,
        ),
        Without<crate::editor_camera::EditorCamera>,
    >,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    anchors: Query<&GlobalTransform, Without<Camera>>,
    mut exposures: Query<(Entity, &mut bevy::post_process::auto_exposure::AutoExposure)>,
    mut saved_speeds: Local<Option<(f32, f32)>>,
    mut teleport: Local<(Option<Vec3>, f32)>,
    mut exposure_reset: Local<
        Option<(
            Vec<(Entity, bevy::post_process::auto_exposure::AutoExposure)>,
            u8,
        )>,
    >,
    time: Res<Time<Real>>,
    mut commands: Commands,
) {
    // Teleporte do herói (viagem rápida, entrar numa casa, respawn): o mundo
    // à volta muda de luz de uma vez — a exposição salta em vez de deslizar.
    let hero = players.iter().next().map(GlobalTransform::translation);
    let mut reset_exposure = playback
        .active
        .as_mut()
        .is_some_and(|a| std::mem::take(&mut a.reset_exposure));
    if let (Some(now), Some(before)) = (hero, teleport.0)
        && now.distance(before) > TELEPORT_JUMP
    {
        teleport.1 = TELEPORT_SNAP_SECS;
        reset_exposure = true;
    }
    teleport.0 = hero;
    teleport.1 = (teleport.1 - time.delta_secs()).max(0.0);
    // Exposição: instantânea durante a espera em negro (e na saída) ou logo a
    // seguir a um teleporte; as velocidades autorais de volta no resto.
    let snapping = teleport.1 > 0.0 || playback.active.as_ref().is_some_and(|a| a.hold.is_some());
    // Reset REAL da exposição automática em cada espera em negro (início,
    // corte longo, saída) e em cada teleporte do herói: o medidor, nos saltos
    // grandes de cena, ficava preso lavado/cinzento 1–2 s. Tirar o componente
    // e repô-lo dois frames depois recomeça-o limpo; a velocidade de snap faz
    // o resto por baixo do véu. (Exposição FIXA durante a cena foi testada e
    // rejeitada: é a do herói, e um plano noutro bioma saía escuro.)
    match exposure_reset.as_mut() {
        None if reset_exposure => {
            let mut stash = Vec::new();
            for (entity, exposure) in &exposures {
                let mut fresh = exposure.clone();
                if let Some((brighten, darken)) = *saved_speeds {
                    fresh.speed_brighten = brighten;
                    fresh.speed_darken = darken;
                }
                stash.push((entity, fresh));
                commands
                    .entity(entity)
                    .remove::<bevy::post_process::auto_exposure::AutoExposure>();
            }
            if !stash.is_empty() {
                *exposure_reset = Some((stash, 2));
            }
        }
        Some((stash, frames)) => {
            if *frames > 0 {
                *frames -= 1;
            } else {
                for (entity, exposure) in stash.drain(..) {
                    commands.entity(entity).try_insert(exposure);
                }
                *exposure_reset = None;
                // O medidor novo adapta-se depressa um bocado.
                teleport.1 = teleport.1.max(TELEPORT_SNAP_SECS);
            }
        }
        _ => {}
    }
    for (_, mut exposure) in &mut exposures {
        if snapping {
            if saved_speeds.is_none() {
                *saved_speeds = Some((exposure.speed_brighten, exposure.speed_darken));
            }
            exposure.speed_brighten = EXPOSURE_SNAP_SPEED;
            exposure.speed_darken = EXPOSURE_SNAP_SPEED;
        } else if let Some((brighten, darken)) = *saved_speeds {
            exposure.speed_brighten = brighten;
            exposure.speed_darken = darken;
        }
    }
    if !snapping {
        *saved_speeds = None;
    }
    let Some(active) = playback.active.as_mut() else {
        return;
    };
    let player = players
        .iter()
        .next()
        .map(GlobalTransform::translation)
        .unwrap_or(active.player_at_start);
    active.player_now = player;
    let reset = std::mem::take(&mut active.reset_taa);
    let reset_taa = |taa: Option<Mut<bevy::anti_alias::taa::TemporalAntiAliasing>>| {
        if reset && let Some(mut taa) = taa {
            taa.reset = true;
        }
    };
    // Saída por negro: a câmara volta a ser do jogo; no 1.º frame põe-se já
    // atrás do herói (senão o follow suave varria o mapa desde o último plano).
    if active.outro {
        if !active.outro_snapped {
            active.outro_snapped = true;
            for (mut transform, _, taa, orbit) in &mut game {
                let offset = crate::camera::camera_offset(
                    orbit.yaw_deg,
                    orbit.pitch_state_deg,
                    orbit.distance,
                    orbit.height,
                );
                *transform = Transform::from_translation(player + offset)
                    .looking_at(player + Vec3::Y * 1.5, Vec3::Y);
                reset_taa(taa);
            }
        }
        return;
    }
    let anchor_pos = |anchor: &Anchor| match anchor {
        Anchor::World => Vec3::ZERO,
        Anchor::Player => player,
        Anchor::SelfEntity => active
            .self_anchor
            .and_then(|e| anchors.get(e).ok())
            .map(GlobalTransform::translation)
            .unwrap_or(player),
        Anchor::Named(name) => active
            .named
            .get(name)
            .copied()
            .flatten()
            .and_then(|e| anchors.get(e).ok())
            .map(GlobalTransform::translation)
            .unwrap_or(player),
    };
    // Cortes longos: fronteiras entre planos onde o olho salta.
    if active.cuts.is_none() {
        let spec = &active.spec;
        let cuts: Vec<f32> = spec
            .shots
            .windows(2)
            .filter(|pair| {
                let (a, b) = (&pair[0], &pair[1]);
                let end = anchor_pos(&a.anchor) + a.to;
                let start = anchor_pos(&b.anchor) + b.from;
                end.distance(start) > CUT_DISTANCE
            })
            .map(|pair| pair[1].start)
            .collect();
        active.cuts = Some(cuts);
    }
    let Some((eye, look, fov)) = active.spec.pose_at(active.t, anchor_pos) else {
        return;
    };
    active.last_eye = Some(eye);
    active.last_look = Some(look);
    let pose = Transform::from_translation(eye).looking_at(look, Vec3::Y);
    let apply = |transform: &mut Transform, projection: Option<Mut<Projection>>| {
        *transform = pose;
        if let (Some(fov), Some(mut projection)) = (fov, projection)
            && let Projection::Perspective(perspective) = projection.as_mut()
        {
            perspective.fov = fov.to_radians();
        }
    };
    if let Some((mut transform, projection, taa)) = editor.iter_mut().next() {
        apply(&mut transform, projection);
        reset_taa(taa);
        return;
    }
    for (mut transform, projection, taa, _) in &mut game {
        apply(&mut transform, projection);
        reset_taa(taa);
    }
}

/// Cria o overlay na primeira reprodução (exclusivo: precisa das fontes do HUD).
fn ensure_overlay(world: &mut World) {
    if world.resource::<CinematicPlayback>().active.is_none() {
        return;
    }
    let mut existing = world.query_filtered::<(), With<CinematicOverlay>>();
    if existing.iter(world).next().is_some() {
        return;
    }
    if world.get_resource::<Assets<Font>>().is_none()
        || world.get_resource::<Assets<Image>>().is_none()
    {
        return; // app headless sem UI
    }
    let hud = crate::hud::assets::HudAssets::get(world);
    let font = |size: f32| TextFont {
        font: hud.font.clone().into(),
        font_size: size.into(),
        ..Default::default()
    };
    world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                top: Val::Px(0.0),
                bottom: Val::Px(0.0),
                ..Default::default()
            },
            GlobalZIndex(200),
            Pickable::IGNORE,
            Visibility::Hidden,
            Name::new("cinematic:overlay"),
            CinematicOverlay,
        ))
        .with_children(|root| {
            root.spawn((
                Node {
                    position_type: PositionType::Absolute,
                    left: Val::Px(0.0),
                    right: Val::Px(0.0),
                    top: Val::Px(0.0),
                    bottom: Val::Px(0.0),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.0)),
                FadeVeil,
            ));
            for top in [true, false] {
                root.spawn((
                    Node {
                        position_type: PositionType::Absolute,
                        left: Val::Px(0.0),
                        right: Val::Px(0.0),
                        top: if top { Val::Px(0.0) } else { Val::Auto },
                        bottom: if top { Val::Auto } else { Val::Px(0.0) },
                        height: Val::Percent(0.0),
                        ..Default::default()
                    },
                    BackgroundColor(Color::BLACK),
                    LetterboxBar,
                ));
            }
            root.spawn(Node {
                position_type: PositionType::Absolute,
                left: Val::Px(0.0),
                right: Val::Px(0.0),
                top: Val::Percent(36.0),
                flex_direction: FlexDirection::Column,
                align_items: AlignItems::Center,
                row_gap: Val::Px(6.0),
                ..Default::default()
            })
            .with_children(|card| {
                card.spawn((
                    Text::new(""),
                    font(18.0),
                    TextColor(Color::srgba(0.95, 0.85, 0.6, 0.0)),
                    TitleSub,
                ));
                card.spawn((
                    Text::new(""),
                    font(46.0),
                    TextColor(Color::srgba(1.0, 0.97, 0.9, 0.0)),
                    TitleText,
                ));
            });
            root.spawn((
                Node {
                    position_type: PositionType::Absolute,
                    left: Val::Percent(18.0),
                    right: Val::Percent(18.0),
                    bottom: Val::Percent(LETTERBOX_PCT + 3.0),
                    flex_direction: FlexDirection::Column,
                    align_items: AlignItems::Center,
                    row_gap: Val::Px(4.0),
                    padding: UiRect::axes(Val::Px(18.0), Val::Px(10.0)),
                    border_radius: BorderRadius::all(Val::Px(8.0)),
                    ..Default::default()
                },
                BackgroundColor(Color::srgba(0.03, 0.025, 0.02, 0.62)),
                Visibility::Hidden,
                LinePanel,
            ))
            .with_children(|panel| {
                panel.spawn((
                    Text::new(""),
                    font(15.0),
                    TextColor(Color::srgb(0.98, 0.82, 0.48)),
                    LineSpeaker,
                ));
                panel.spawn((
                    Text::new(""),
                    font(19.0),
                    TextColor(Color::srgb(0.96, 0.94, 0.9)),
                    TextLayout::justify(Justify::Center),
                    LineText,
                ));
            });
        });
}

#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn drive_overlay(
    playback: Res<CinematicPlayback>,
    mut roots: Query<&mut Visibility, (With<CinematicOverlay>, Without<LinePanel>)>,
    mut bars: Query<&mut Node, With<LetterboxBar>>,
    mut veils: Query<&mut BackgroundColor, (With<FadeVeil>, Without<LinePanel>)>,
    mut titles: Query<
        (&mut Text, &mut TextColor),
        (
            With<TitleText>,
            Without<TitleSub>,
            Without<LineText>,
            Without<LineSpeaker>,
        ),
    >,
    mut subs: Query<
        (&mut Text, &mut TextColor),
        (
            With<TitleSub>,
            Without<TitleText>,
            Without<LineText>,
            Without<LineSpeaker>,
        ),
    >,
    mut panels: Query<&mut Visibility, (With<LinePanel>, Without<CinematicOverlay>)>,
    mut speakers: Query<
        &mut Text,
        (
            With<LineSpeaker>,
            Without<LineText>,
            Without<TitleText>,
            Without<TitleSub>,
        ),
    >,
    mut lines: Query<
        &mut Text,
        (
            With<LineText>,
            Without<LineSpeaker>,
            Without<TitleText>,
            Without<TitleSub>,
        ),
    >,
) {
    let Ok(mut root) = roots.single_mut() else {
        return;
    };
    let Some(active) = playback.active.as_ref() else {
        root.set_if_neq(Visibility::Hidden);
        return;
    };
    root.set_if_neq(Visibility::Inherited);
    let t = active.t;
    let spec = &active.spec;
    // Barras: entram no 1.º meio segundo e saem no último.
    let bar = if spec.letterbox {
        let k = (t / 0.5).min((spec.duration - t) / 0.5).clamp(0.0, 1.0);
        LETTERBOX_PCT * k
    } else {
        0.0
    };
    for mut node in &mut bars {
        let wanted = Val::Percent(bar);
        if node.height != wanted {
            node.height = wanted;
        }
    }
    let dark = active.veil();
    for mut veil in &mut veils {
        veil.0 = Color::srgba(0.0, 0.0, 0.0, dark);
    }
    let (title, sub, alpha) = match spec.title_at(t) {
        Some((card, alpha)) => (card.text.as_str(), card.sub.as_str(), alpha),
        None => ("", "", 0.0),
    };
    for (mut text, mut color) in &mut titles {
        if text.0 != title {
            text.0 = title.to_owned();
        }
        color.0 = color.0.with_alpha(alpha);
    }
    for (mut text, mut color) in &mut subs {
        if text.0 != sub {
            text.0 = sub.to_owned();
        }
        color.0 = color.0.with_alpha(alpha);
    }
    let line = spec.line_at(t);
    for mut panel in &mut panels {
        panel.set_if_neq(if line.is_some() {
            Visibility::Inherited
        } else {
            Visibility::Hidden
        });
    }
    if let Some(line) = line {
        for mut text in &mut speakers {
            if text.0 != line.speaker {
                text.0 = line.speaker.clone();
            }
        }
        for mut text in &mut lines {
            if text.0 != line.text {
                text.0 = line.text.clone();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(xml: &str) -> Result<CinematicSpec, String> {
        let node = crate::xml::parse_fragment(xml).unwrap().remove(0);
        parse(&node)
    }

    const SAMPLE: &str = r#"<Cinematic id="t">
        <Fade at="0" duration="1" from="1" to="0" />
        <Shot duration="2" from="0 10 0" to="10 10 0" look="0 0 0" ease="linear" />
        <Shot duration="3" anchor="player" from="0 2 5" look="0 1 0" fov="50" />
        <Title at="0.5" duration="2" sub="Capítulo I">Vael</Title>
        <Line at="1" duration="2" speaker="Ancião">Olá.</Line>
        <Sound at="0.2" clip="roar" />
      </Cinematic>"#;

    #[test]
    fn shots_run_in_sequence_and_timed_items_are_absolute() {
        let s = spec(SAMPLE).unwrap();
        assert_eq!(s.shots.len(), 2);
        assert_eq!(s.shots[1].start, 2.0);
        assert_eq!(s.duration, 5.0);
        assert_eq!(s.shots[1].anchor, Anchor::Player);
        let (eye, look, fov) = s.pose_at(1.0, |_| Vec3::ZERO).unwrap();
        assert_eq!(eye, Vec3::new(5.0, 10.0, 0.0));
        assert_eq!(look, Vec3::ZERO);
        assert_eq!(fov, None);
        // âncora: posições relativas ao herói
        let (eye, _, fov) = s.pose_at(3.0, |_| Vec3::new(100.0, 0.0, 0.0)).unwrap();
        assert_eq!(eye, Vec3::new(100.0, 2.0, 5.0));
        assert_eq!(fov, Some(50.0));
        assert!((s.fade_at(0.5) - 0.5).abs() < 1e-5);
        assert_eq!(s.fade_at(3.0), 0.0);
        assert_eq!(s.line_at(1.5).unwrap().speaker, "Ancião");
        assert!(s.line_at(3.5).is_none());
        let (card, alpha) = s.title_at(1.5).unwrap();
        assert_eq!(card.sub, "Capítulo I");
        assert!(alpha > 0.99);
        assert_eq!(s.to_json()["shots"][1]["anchor"], "player");
    }

    #[test]
    fn bad_cinematics_explain_themselves() {
        assert!(
            spec(r#"<Cinematic><Shot from="0 0 0" look="0 0 1" /></Cinematic>"#)
                .unwrap_err()
                .contains("sem id")
        );
        assert!(
            spec(r#"<Cinematic id="x" />"#)
                .unwrap_err()
                .contains("sem nenhum")
        );
        assert!(
            spec(r#"<Cinematic id="x"><Shot from="0 0 0" /></Cinematic>"#)
                .unwrap_err()
                .contains("sem look")
        );
        assert!(
            spec(r#"<Cinematic id="x"><Shot from="0 0 0" look="1 1 1" ease="zig" /></Cinematic>"#)
                .unwrap_err()
                .contains("ease")
        );
        assert!(
            spec(r#"<Cinematic id="x"><Camera /></Cinematic>"#)
                .unwrap_err()
                .contains("desconhecido")
        );
    }

    #[test]
    fn shot_xml_roundtrips() {
        let xml = shot_xml(Vec3::new(1.0, 2.5, -3.0), Vec3::new(0.0, 1.0, 0.0), 4.0);
        let s = spec(&format!(r#"<Cinematic id="x">{xml}</Cinematic>"#)).unwrap();
        assert_eq!(s.shots[0].from, Vec3::new(1.0, 2.5, -3.0));
        assert_eq!(s.shots[0].duration, 4.0);
    }
}
