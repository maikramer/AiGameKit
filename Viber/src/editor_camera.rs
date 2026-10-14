//! Câmara livre de editor (`--camera free`): uma única câmara orbital em
//! torno de um alvo, conduzida pelo bridge (`viber.camera.orbit/pan/zoom/
//! look_at/pose`) — a navegação de mundos do ViberEditor (preview ~400 px
//! com minimapa). Sem a flag o plugin não regista NADA e a App fica igual à
//! de sempre (ver [`EditorCameraPlugin`]).
//!
//! A pose é um estado puro (`EditorCamState`: alvo + yaw/pitch/distância) —
//! as operações de navegação ([`orbit`], [`pan`], [`zoom`], [`look_at`]) são
//! funções puras sobre esse estado, e o sistema [`editor_camera_follow`]
//! apenas projecta o estado no `Transform` da câmara com a MESMA fórmula
//! esférica da câmara de jogo ([`crate::camera::camera_offset`], altura 0).
//! Os handlers BRP (`src/bridge/mod.rs`) chamam as mesmas funções.
//!
//! Com o modo ligado, a câmara de jogo fica inactiva (`is_active = false`)
//! e os sistemas de câmara/input do jogador param por run condition
//! ([`editor_camera_inactive`]) — o WASD não move o herói.

use bevy::core_pipeline::tonemapping::Tonemapping;
use bevy::math::Vec3;
use bevy::prelude::*;

use crate::profiler::Group;
use crate::profiler::timed;
use crate::terrain::runtime::TerrainRuntime;

/// Pitch mínimo (graus) — um pouco abaixo do horizonte para enquadrar
/// vales/linhas de água sem expor o nadir do céu.
pub const MIN_PITCH_DEG: f32 = -10.0;
/// Pitch máximo (graus) — quase zénite; a 90° o `look_at` com up +Y fica
/// degenerado (gimbal lock).
pub const MAX_PITCH_DEG: f32 = 85.0;
/// Distância mínima ao alvo (m) — mais perto disto o clipping near-plane
/// engole a cena.
pub const MIN_DISTANCE: f32 = 5.0;
/// Distância máxima ao alvo (m) — o envelope dos mundos maiores do simple-rpg
/// (mapas de ~1 km) com folga.
pub const MAX_DISTANCE: f32 = 3000.0;
/// Distância de arranque (m): enqudra uma aldeia inteira de imediato.
pub const DEFAULT_DISTANCE: f32 = 120.0;
/// Pitch de arranque (graus) — o clássico isométrico suave dos editores.
pub const DEFAULT_PITCH_DEG: f32 = 35.0;
/// Metros de pan por pixel de drag, a distância 1 (escala com a distância ao
/// alvo). Calibrado para o preview ~400 px do ViberEditor: um drag de ecrã
/// inteiro a 120 m move ~96 m (≈ 2/3 da largura visível a FOV 64°) — rápido
/// o suficiente para atravessar um mapa, lento o suficiente para mirar um
/// prop.
pub const PAN_SCALE: f32 = 0.002;

/// Pose da câmara de editor — alvo orbital + yaw/pitch/distância. Vive como
/// resource; `active` distingue "plugin ligado" (câmara spawnada, métodos BRP
/// disponíveis) de um estado meramente presente (apps mínimas de teste).
#[derive(Debug, Clone, Resource)]
pub struct EditorCamState {
    /// O modo `--camera free` está ativo nesta engine.
    pub active: bool,
    /// Ponto orbital (o centro do interesse do editor).
    pub target: Vec3,
    /// Rotação horizontal em torno do alvo (graus) — mesma convenção do
    /// `OrbitCamera`: 0 põe a câmara a +Z a olhar para -Z.
    pub yaw_deg: f32,
    /// Elevação sobre o horizonte (graus, clamp [`MIN_PITCH_DEG`]..
    /// [`MAX_PITCH_DEG`]).
    pub pitch_deg: f32,
    /// Distância ao alvo (m, clamp [`MIN_DISTANCE`]..[`MAX_DISTANCE`]).
    pub distance: f32,
}

impl Default for EditorCamState {
    fn default() -> Self {
        Self {
            active: false,
            target: Vec3::ZERO,
            yaw_deg: 0.0,
            pitch_deg: DEFAULT_PITCH_DEG,
            distance: DEFAULT_DISTANCE,
        }
    }
}

impl EditorCamState {
    /// Posição do olho: órbita esférica em torno do alvo pela MESMA fórmula
    /// da câmara de jogo ([`crate::camera::camera_offset`], altura 0) —
    /// pitch 0 fica no plano do horizonte, +35° olha a cena de cima.
    pub fn eye(&self) -> Vec3 {
        self.target + crate::camera::camera_offset(self.yaw_deg, self.pitch_deg, self.distance, 0.0)
    }
}

/// Marker da entidade câmara de editor (`Camera3d` + `Transform`).
#[derive(Debug, Component)]
pub struct EditorCamera;

/// Câmaras que o modo editor NÃO desativa — as do próprio ViberEditor
/// in-process (UI do editor, preview do jogo); todas as outras são de jogo.
#[derive(Debug, Default, Clone, Copy, Component)]
pub struct KeepCameraActive;

/// Roda a órbita: `yaw += dx_deg`, `pitch` com clamp. Parâmetros não
/// finitos são rejeitados (o editor manda deltas de rato crus).
pub fn orbit(state: &mut EditorCamState, dx_deg: f32, dy_deg: f32) -> Result<(), String> {
    if !dx_deg.is_finite() || !dy_deg.is_finite() {
        return Err(format!(
            "orbit: dx_deg/dy_deg têm de ser finitos ({dx_deg}, {dy_deg})"
        ));
    }
    state.yaw_deg += dx_deg;
    state.pitch_deg = (state.pitch_deg + dy_deg).clamp(MIN_PITCH_DEG, MAX_PITCH_DEG);
    Ok(())
}

/// Pan no plano da câmara: `dx`/`dy` em píxeis de drag (o preview do
/// editor), convertidos a metros por [`PAN_SCALE`] × distância. O conteúdo
/// SEGUE o cursor — arrastar para a direita/baixo move o alvo para a
/// esquerda/trás no plano horizontal (o "grab the ground" dos editores).
pub fn pan(state: &mut EditorCamState, dx_px: f32, dy_px: f32) -> Result<(), String> {
    if !dx_px.is_finite() || !dy_px.is_finite() {
        return Err(format!("pan: dx/dy têm de ser finitos ({dx_px}, {dy_px})"));
    }
    let yaw = state.yaw_deg.to_radians();
    let (sin, cos) = yaw.sin_cos();
    // Direção horizontal do olhar (da câmara para o alvo) e a direita do
    // ecrã — right = forward × up (ver derivation no teste do pan).
    let forward = Vec3::new(-sin, 0.0, -cos);
    let right = Vec3::new(cos, 0.0, -sin);
    let meters = PAN_SCALE * state.distance;
    state.target -= right * (dx_px * meters);
    state.target -= forward * (dy_px * meters);
    Ok(())
}

/// Zoom: `distance /= factor` com clamp (`factor` 2 = aproxima para metade
/// da distância; 0.5 = afasta para o dobro). `factor` ≤ 0/não finito é
/// rejeitado — NaN deixava a câmara no infinito para sempre.
pub fn zoom(state: &mut EditorCamState, factor: f32) -> Result<(), String> {
    if !factor.is_finite() || factor <= 0.0 {
        return Err(format!("zoom: factor inválido ({factor})"));
    }
    state.distance = (state.distance / factor).clamp(MIN_DISTANCE, MAX_DISTANCE);
    Ok(())
}

/// Aponta a órbita a um novo alvo (ex.: clique no minimapa do editor).
pub fn look_at(state: &mut EditorCamState, target: Vec3) -> Result<(), String> {
    if !target.is_finite() {
        return Err(format!("look_at: alvo não finito ({target:?})"));
    }
    state.target = target;
    Ok(())
}

/// Run condition para os sistemas de jogo (câmara terceira-pessoa, input do
/// player): só correm com a câmara de editor INATIVA — recurso ausente
/// (`--camera` sem valor: App 100% atual) ou `active == false`.
pub fn editor_camera_inactive(state: Option<Res<EditorCamState>>) -> bool {
    state.is_none_or(|state| !state.active)
}

/// O olho activo do momento: a câmara de editor quando existe (modo free),
/// senão a primeira `Camera3d` (a do jogo). Quem segue "a câmara" — domo do
/// céu (`sky_follow_camera`), culling/LOD (`render_lod`), relva (`grass`) —
/// TEM de usar isto: com `--camera free` há DUAS `Camera3d` no mundo (a de
/// jogo inactiva) e `iter().next()` devolve uma arbitrária — o domo ficava
/// preso ao herói e o LOD cullava as árvores vistas da câmara de editor.
pub fn active_camera_eye<'w>(
    editor: &'w Query<&GlobalTransform, With<EditorCamera>>,
    cameras: &'w Query<&GlobalTransform, With<Camera3d>>,
) -> Option<&'w GlobalTransform> {
    editor.iter().next().or_else(|| cameras.iter().next())
}

/// Câmara livre de editor, ligada por `viber run --camera free`. Aditivo por
/// natureza: com `enabled == false` o `build` regressa de imediato e não
/// insere recursos, sistemas nem entidades — o comportamento é o de sempre.
pub struct EditorCameraPlugin {
    /// `--camera free` chegou ao CLI.
    pub enabled: bool,
}

impl Plugin for EditorCameraPlugin {
    fn build(&self, app: &mut App) {
        if !self.enabled {
            return;
        }
        app.insert_resource(EditorCamState {
            active: true,
            ..EditorCamState::default()
        })
        .add_systems(
            bevy::app::Startup,
            // DEPOIS do bootstrap do terreno: o alvo de arranque é a cota
            // real em (0,0) quando o mundo tem terreno (senão, a origem).
            spawn_editor_camera.after(crate::terrain::runtime::bootstrap),
        )
        .add_systems(
            bevy::app::Update,
            // Primeiro a supressão (nenhum frame renderiza pelas duas), depois
            // a pose — a câmara nova entra já olhando para o alvo.
            (
                timed(Group::Camera, suppress_game_cameras),
                timed(Group::Camera, editor_camera_follow),
            )
                .chain(),
        );
    }
}

/// Spawna a ÚNICA entidade `EditorCamera` e ancora o alvo no terreno. Os
/// componentes copiam a câmara de jogo do `OrbitCamera`
/// (`Camera3d::default()`; o postfx da engine equipa Exposure/
/// TonyMcMapface/prepasses em TODAS as `Camera3d` — ver
/// `postfx::attach_postfx_to_cameras` — e o `Tonemapping` explícito mantém o
/// look mesmo com `VIBER_NO_POSTFX=1`).
fn spawn_editor_camera(
    mut commands: Commands,
    mut state: ResMut<EditorCamState>,
    runtime: Option<Res<TerrainRuntime>>,
) {
    if let Some(runtime) = runtime.as_deref()
        && runtime.in_field(0.0, 0.0)
    {
        state.target = Vec3::new(0.0, runtime.sample(0.0, 0.0), 0.0);
    }
    let mut transform = Transform::from_translation(state.eye());
    transform.look_at(state.target, Vec3::Y);
    commands.spawn((
        Name::new("EditorCamera"),
        EditorCamera,
        Camera3d::default(),
        transform,
        Tonemapping::TonyMcMapface,
    ));
}

/// Modo editor ON ⇒ as câmaras de jogo (`OrbitCamera`/`AutoOrbit`) ficam
/// inactivas. `is_active = false` em vez de despawn: reversível, e o
/// `Screenshot::primary_window` do bridge continua a capturar a janela pela
/// ÚNICA câmara activa — a de editor.
fn suppress_game_cameras(
    mut cameras: Query<&mut Camera, (Without<EditorCamera>, Without<KeepCameraActive>)>,
) {
    for mut camera in &mut cameras {
        camera.is_active = false;
    }
}

/// Projecta o `EditorCamState` no `Transform` da câmara (todas as frames —
/// as mutações BRP aplicam no frame seguinte ao request).
fn editor_camera_follow(
    state: Res<EditorCamState>,
    mut cameras: Query<&mut Transform, With<EditorCamera>>,
) {
    if !state.active {
        return;
    }
    for mut transform in &mut cameras {
        transform.translation = state.eye();
        transform.look_at(state.target, Vec3::Y);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn test_eye_orbits_the_target() {
        // yaw 0 / pitch 0: a câmara senta-se a +Z do alvo (convenção
        // `camera_offset`/`OrbitCamera`), no plano do horizonte.
        let state = EditorCamState {
            active: true,
            target: Vec3::new(10.0, 2.0, -4.0),
            yaw_deg: 0.0,
            pitch_deg: 0.0,
            distance: 10.0,
        };
        assert!(state.eye().distance(Vec3::new(10.0, 2.0, 6.0)) < 1e-4);
        // yaw 90: roda para +X.
        let state = EditorCamState {
            yaw_deg: 90.0,
            ..state
        };
        assert!(state.eye().distance(Vec3::new(20.0, 2.0, -4.0)) < 1e-3);
        // pitch 35 (default): esférica completa — sin/cos no plano vertical.
        let state = EditorCamState {
            pitch_deg: DEFAULT_PITCH_DEG,
            ..state
        };
        let d = state.distance;
        let expected = Vec3::new(
            10.0 + d * DEFAULT_PITCH_DEG.to_radians().cos(),
            2.0 + d * DEFAULT_PITCH_DEG.to_radians().sin(),
            -4.0,
        );
        assert!(state.eye().distance(expected) < 1e-3);
    }

    #[test]
    fn test_orbit_adds_yaw_and_clamps_pitch() {
        let mut state = EditorCamState {
            active: true,
            yaw_deg: 30.0,
            pitch_deg: 80.0,
            distance: 100.0,
            target: Vec3::ZERO,
        };
        orbit(&mut state, 15.0, 20.0).expect("orbit válido");
        assert!(approx(state.yaw_deg, 45.0));
        assert!(
            approx(state.pitch_deg, MAX_PITCH_DEG),
            "teto 85°: {}",
            state.pitch_deg
        );
        orbit(&mut state, 0.0, -200.0).expect("orbit válido");
        assert!(
            approx(state.pitch_deg, MIN_PITCH_DEG),
            "chão -10°: {}",
            state.pitch_deg
        );
        // Yaw NÃO faz clamp — dá a volta ao alvo livremente.
        orbit(&mut state, 400.0, 0.0).expect("orbit válido");
        assert!(approx(state.yaw_deg, 45.0 + 400.0));
        assert!(orbit(&mut state, f32::NAN, 0.0).is_err());
        assert!(orbit(&mut state, 0.0, f32::INFINITY).is_err());
    }

    #[test]
    fn test_zoom_divides_distance_and_clamps() {
        let mut state = EditorCamState {
            active: true,
            yaw_deg: 0.0,
            pitch_deg: DEFAULT_PITCH_DEG,
            distance: 120.0,
            target: Vec3::ZERO,
        };
        zoom(&mut state, 2.0).expect("zoom válido");
        assert!(approx(state.distance, 60.0), "factor 2 aproxima à metade");
        zoom(&mut state, 0.5).expect("zoom válido");
        assert!(approx(state.distance, 120.0), "factor 0.5 devolve o dobro");
        zoom(&mut state, 1.0e9).expect("zoom válido");
        assert!(
            approx(state.distance, MIN_DISTANCE),
            "factor gigante cola no chão"
        );
        zoom(&mut state, 1.0e-9).expect("zoom válido");
        assert!(
            approx(state.distance, MAX_DISTANCE),
            "factor ínfimo bate no teto"
        );
        assert!(zoom(&mut state, 0.0).is_err(), "factor 0 é inválido");
        assert!(
            zoom(&mut state, -2.0).is_err(),
            "factor negativo é inválido"
        );
        assert!(zoom(&mut state, f32::NAN).is_err());
    }

    #[test]
    fn test_pan_follows_cursor_on_camera_plane() {
        // Yaw 0 (câmara a +Z a olhar -Z): direita do ecrã = +X, "frente" da
        // vista = -Z. Drag 100 px a PAN_SCALE·100 m de distância = 20 m.
        let mut state = EditorCamState {
            active: true,
            yaw_deg: 0.0,
            pitch_deg: DEFAULT_PITCH_DEG,
            distance: 100.0,
            target: Vec3::ZERO,
        };
        pan(&mut state, 100.0, 0.0).expect("pan válido");
        // Conteúdo segue o cursor: arrastar p/ a direita move o ALVO p/ -X.
        assert!(
            approx(state.target.x, -100.0 * PAN_SCALE * 100.0) && approx(state.target.z, 0.0),
            "{:?}",
            state.target
        );
        // Drag para BAIXO move o alvo na direção da câmara (+Z).
        pan(&mut state, 0.0, 100.0).expect("pan válido");
        assert!(
            approx(state.target.z, 100.0 * PAN_SCALE * 100.0),
            "{:?}",
            state.target
        );
        assert!(pan(&mut state, f32::NAN, 0.0).is_err());
        assert!(pan(&mut state, 0.0, f32::INFINITY).is_err());
    }

    #[test]
    fn test_pan_respects_yaw() {
        // Yaw 90 (câmara a +X a olhar -X): direita do ecrã = -Z — o mesmo
        // drag p/ a direita move agora o alvo em +Z, não em X.
        let mut state = EditorCamState {
            active: true,
            yaw_deg: 90.0,
            pitch_deg: DEFAULT_PITCH_DEG,
            distance: 50.0,
            target: Vec3::ZERO,
        };
        pan(&mut state, 100.0, 0.0).expect("pan válido");
        let expected = 100.0 * PAN_SCALE * 50.0;
        assert!(
            approx(state.target.x, 0.0) && approx(state.target.z, expected),
            "{:?}",
            state.target
        );
    }

    #[test]
    fn test_look_at_sets_target() {
        let mut state = EditorCamState::default();
        look_at(&mut state, Vec3::new(120.0, 8.0, -40.0)).expect("alvo válido");
        assert_eq!(state.target, Vec3::new(120.0, 8.0, -40.0));
        assert!(
            look_at(&mut state, Vec3::new(f32::NAN, 0.0, 0.0)).is_err(),
            "NaN rejeitado — deixaria a câmara perdida"
        );
    }

    /// Flag observável pelo sistema dummy abaixo (o run condition é teste de
    /// integração: `Option<Res<…>>` não se constrói à mão fora de sistema).
    #[derive(Resource, Default)]
    struct RanFlag(bool);

    #[test]
    fn test_run_condition_and_plugin_headless() {
        let mut app = App::new();
        app.add_plugins(bevy::app::TaskPoolPlugin::default())
            .add_plugins(bevy::time::TimePlugin)
            .add_plugins(EditorCameraPlugin { enabled: false });
        app.init_resource::<RanFlag>();
        app.add_systems(
            bevy::app::Update,
            (|mut ran: ResMut<RanFlag>| ran.0 = true).run_if(editor_camera_inactive),
        );
        // Plugin DESLIGADO: registo nenhum — o recurso nem existe e o dummy
        // corre (App 100% atual).
        app.update();
        assert!(app.world().resource::<RanFlag>().0, "sem plugin: corre");
        assert!(
            app.world_mut()
                .query_filtered::<(), With<EditorCamera>>()
                .iter(app.world_mut())
                .next()
                .is_none(),
            "plugin desligado não spawna câmara"
        );

        // Plugin LIGADO: recurso activo, câmara spawnada, dummy bloqueado.
        let mut app = App::new();
        app.add_plugins(bevy::app::TaskPoolPlugin::default())
            .add_plugins(bevy::time::TimePlugin)
            .add_plugins(EditorCameraPlugin { enabled: true });
        app.init_resource::<RanFlag>();
        app.add_systems(
            bevy::app::Update,
            (|mut ran: ResMut<RanFlag>| ran.0 = true).run_if(editor_camera_inactive),
        );
        // Câmara de jogo fake: tem de ficar is_active=false depois de 1 frame.
        let game_camera = app
            .world_mut()
            .spawn((Camera::default(), Transform::default()))
            .id();
        app.update();
        assert!(
            !app.world().resource::<RanFlag>().0,
            "modo editor bloqueia os sistemas gated"
        );
        assert!(
            !app.world().get::<Camera>(game_camera).unwrap().is_active,
            "câmara de jogo suprimida"
        );
        let state = app.world().resource::<EditorCamState>();
        assert!(state.active);
        let (expected_eye, expected_target) = (state.eye(), state.target);
        let editor_camera = app
            .world_mut()
            .query_filtered::<Entity, With<EditorCamera>>()
            .iter(app.world_mut())
            .next()
            .expect("câmara de editor spawnada no Startup");
        let transform = app.world().get::<Transform>(editor_camera).unwrap();
        assert!(
            transform.translation.distance(expected_eye) < 1e-3,
            "pose {:?} ≠ esperada {:?}",
            transform.translation,
            expected_eye
        );
        // A câmara OLHA para o alvo: o forward aponta do olho para o alvo.
        let forward = transform.forward();
        let to_target = (expected_target - expected_eye).normalize();
        assert!(forward.dot(to_target) > 0.999, "não está a olhar ao alvo");
    }
}
