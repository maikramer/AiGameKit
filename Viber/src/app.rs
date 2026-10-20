//! Montagem da App do Viber — partilhada entre o binário `viber run`
//! ([`RunMode::Game`]) e o ViberEditor in-process ([`RunMode::Edit`]).
//!
//! [`build_app`] lê o mundo + `config.yaml`, especializa os shaders por mundo,
//! instala o `DefaultPlugins` (janela, asset roots multi-root) e todos os
//! plugins da engine; quem chama decide quando fazer `run()` — o editor
//! acrescenta os seus plugins antes.
//!
//! O modo Edit é ADITIVO sobre o Game: os mesmos plugins (o spawn do mundo
//! depende dos recursos deles), mas com a simulação congelada
//! (`Time<Virtual>` em pausa), scripts Luau parados ([`scripts_enabled`]),
//! sem bridge e com a câmara de editor no lugar da de jogo.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use bevy::app::{App, PluginGroup};
use bevy::ecs::schedule::IntoScheduleConfigs;
use bevy::ecs::system::{Res, ResMut};
use bevy::log::info;
use bevy::prelude::Resource;
use bevy::time::{Time, Virtual};
use bevy_kira_audio::AudioApp;

use crate::bridge;
use crate::profiler::{Group, timed};
use crate::recipes::ParsedWorld;
use crate::recipes::spawn::{self, PendingWorld};
use crate::{
    ai, ambient, animation, camera, combat, editor_camera, grass, hud, impact, luau, menus, meshopt,
    music, nav, particles, physics, physics_fx, player, postfx, profiler, prop_tint, quests,
    recipes, render_lod, save, skills, sky, spawner, terrain, textures, trail, ui, vitals,
    worldsys, xml,
};

/// Para quê a App é montada.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Resource)]
pub enum RunMode {
    /// `viber run`: o jogo.
    #[default]
    Game,
    /// ViberEditor in-process: cena congelada, editada pelo editor.
    Edit,
}

/// Opções de [`build_app`].
#[derive(Debug, Clone, Default)]
pub struct AppOptions {
    pub mode: RunMode,
    /// Porta do debug bridge (no modo Edit: agentes a editar dentro do editor).
    pub bridge_port: Option<u16>,
    /// `--camera free` (sempre ligado em [`RunMode::Edit`]).
    pub camera_free: bool,
    /// Título da janela; omisso → `config.yaml` `title` → "Viber — <mundo>".
    pub window_title: Option<String>,
    /// Entrada de chegada na cena de arranque (`viber run --entry`); numa cena que não é
    /// a principal, omisso = a primeira entrada.
    pub start_entry: Option<String>,
}

/// Run condition: scripts Luau correm (tudo menos o modo Edit; sem o
/// resource — apps mínimas de teste — correm).
pub fn scripts_enabled(mode: Option<Res<RunMode>>) -> bool {
    mode.is_none_or(|mode| *mode != RunMode::Edit)
}

fn pause_virtual_time(mut time: ResMut<Time<Virtual>>) {
    time.pause();
}

/// Tamanho do cube shadow map das PointLight (`VIBER_POINT_SHADOW_SIZE`).
///
/// Default 1024 (ver o comentário no recurso); `1536` devolve o valor do
/// passe visual r1. Tem de ser u32 par e >= 4 — o wgpu recusa tamanhos
/// ímpares em algumas plataformas.
/// Tamanho do shadow map da DIRECIONAL (`VIBER_DIR_SHADOW_SIZE`, default
/// 4096). É UMA textura de 4 camadas (~268 MB a 4096, metade a 2048).
fn dir_shadow_size() -> usize {
    std::env::var("VIBER_DIR_SHADOW_SIZE")
        .ok()
        .and_then(|raw| raw.parse::<usize>().ok())
        .filter(|size| *size >= 256 && size % 2 == 0)
        .unwrap_or_else(crate::graphics::dir_shadow_size)
}

fn point_shadow_size() -> usize {
    std::env::var("VIBER_POINT_SHADOW_SIZE")
        .ok()
        .and_then(|raw| raw.parse::<usize>().ok())
        .filter(|size| *size >= 4 && size % 2 == 0)
        .unwrap_or(1024)
}

/// Profiling do lado do RENDER (`VIBER_PROF_GPU=1`).
///
/// Os sistemas instrumentados pelo `timed` somam ~4 ms de um frame de ~44 ms
/// no `simple-rpg` — os outros 40 ms são o render app (extract, prepare,
/// queue e os passes na GPU) e não eram observáveis por via nenhuma. Com o gate ON
/// pedimos os `TIMESTAMP_QUERY` ao wgpu e ligamos o `RenderDiagnosticsPlugin`,
/// que mede cada span do render graph em CPU **e** GPU; o snapshot do
/// profiler publica-os em `gpu[]` (`viber debug prof --json`).
///
/// OPT-IN de propósito: pedir ao device uma feature que o adapter não tenha
/// FALHA a criação do renderer (Metal/WebGPU não têm timestamps), e as
/// próprias queries custam tempo por frame.
pub fn gpu_profiling_enabled() -> bool {
    matches!(
        std::env::var("VIBER_PROF_GPU").as_deref(),
        Ok("1" | "true" | "on")
    )
}

pub fn load_world(path: &Path) -> Result<ParsedWorld> {
    let loaded = xml::include::load_world(path)?;
    recipes::parse_world(&loaded.root_attrs, &loaded.nodes)
}

/// A pasta base do jogo (config, scripts, assets, `/…` dos includes): a raiz do PROJETO
/// ([`crate::project::root_of`]). Para um mundo ao lado do seu `config.yaml` é a pasta dele,
/// exatamente como sempre foi (devolvida na forma em que veio no caminho); uma cena em
/// `scenes/` sobe à pasta do projeto.
pub fn world_base_dir(path: &Path) -> Option<PathBuf> {
    let parent = path
        .parent()
        .map(|p| p.to_path_buf())
        .filter(|p| !p.as_os_str().is_empty());
    let root = crate::project::root_of(path);
    let same = |dir: &Path| dir.canonicalize().is_ok_and(|dir| dir == root);
    match parent {
        Some(parent) if same(&parent) => Some(parent),
        None if same(Path::new(".")) => None,
        _ => Some(root),
    }
}

/// world_dir (scripts/estilos relativos) + asset roots por ordem de
/// precedência (docs/ASSETS.md): a pasta do mundo SEMPRE primeiro (é lá que
/// os shaders especializados são escritos e os overrides vivem), as roots
/// extra do `config.yaml` do jogo a seguir. A engine não descobre nada por
/// conta própria — quem serve o quê é declarado pelo jogo.
pub fn world_asset_dirs(
    path: &Path,
    config: &crate::config::GameConfig,
) -> (PathBuf, Vec<PathBuf>) {
    let world_dir = world_base_dir(path).unwrap_or_else(|| PathBuf::from("."));
    let roots = config.asset_roots(&world_dir);
    (world_dir, roots)
}

pub fn build_app(path: &Path, opts: AppOptions) -> Result<App> {
    let AppOptions {
        mode,
        bridge_port,
        camera_free,
        window_title,
        start_entry,
    } = opts;
    // Modo Edit: o editor in-process é dono da navegação, do input e da janela — câmara
    // de editor, simulação congelada. O bridge só existe se o editor o pedir
    // (`vibereditor --bridge`): um agente a editar o MESMO documento que o humano.
    let camera_free = camera_free || mode == RunMode::Edit;
    // Absolute from here on: the asset root and terrain base_dir must not
    // depend on the CWD (bevy resolves relative asset roots against the exe).
    let path = &std::path::absolute(path)?;
    let world = load_world(path)?;
    // O runtime só corre mundos ESTÁTICOS (`<world static="true">`): poses finais, terreno
    // pré-gerado, vegetação em `.vinst`. Um mundo procedural é material para `viber bake`,
    // que o converte no próprio sítio — nada é gerado nem assentado no arranque.
    if !world.static_world {
        bail!(
            "{}: mundo procedural (sem `<world static=\"true\">`) — o runtime só carrega mundos estáticos. Converte-o com `viber bake {}` (escreve o estático nos mesmos ficheiros).",
            path.display(),
            path.display()
        );
    }
    // O config.yaml do jogo é OBRIGATÓRIO e lido antes de tudo: é dele que
    // vêm as asset roots e os diretórios (docs/ASSETS.md).
    let world_dir_pre = world_base_dir(path).unwrap_or_else(|| PathBuf::from("."));
    let config = crate::config::load(&world_dir_pre)?;
    // Preset de gráficos ANTES de construir a App: as cascatas do sol, o
    // shadow map e o raymarch volumétrico são lidos durante o spawn do mundo.
    let graphics = crate::graphics::resolve(config.graphics.as_deref());
    info!("graphics preset: {}", graphics.name());
    let title = window_title
        .or_else(|| config.title.clone())
        .unwrap_or_else(|| {
            format!(
                "Viber — {}",
                path.file_name().and_then(|n| n.to_str()).unwrap_or("world")
            )
        });
    // Asset roots por ordem de precedência: a pasta do mundo primeiro, as
    // roots extra do config a seguir (`VIBER_ASSET_POOL` continua a ganhar
    // para debug/CI). Calculadas ANTES de escrever o shader: materiais
    // custom resolvem o shader especializado pela 1.ª root, e o `run`
    // escreve-o sempre nela — as roots extra nunca são escritas.
    let (world_dir, asset_roots) = world_asset_dirs(path, &config);
    let asset_root = asset_roots[0].clone();
    // O shader do céu é ESPECIALIZADO por mundo: a config do <Sky>/<DayCycle>/
    // <Weather> é injectada como consts WGSL (o uniform de material custom no
    // Bevy 0.19 nunca re-uploads — ver sky.rs). O mesmo para a água (relógio
    // do glint + vento das ondas) e para o blend de camadas do terreno
    // (world span dos splats) — este último só se algum <Terrain> pedir
    // `layers`; mundos sem camadas nem tocam no ficheiro. O diretório é
    // SEMPRE `shaders/` da 1.ª root: os `Material::fragment_shader()` são fns
    // ESTÁTICAS que lêem `shaders/{sky,water,terrain_chunk}.wgsl` contra as
    // roots — a escrita e a leitura têm de casar (contrato de conteúdo,
    // docs/ASSETS.md).
    // Projeto com cenas: as constantes dos shaders são do PROJETO (os materiais não
    // re-especializam numa troca de cena) — vêm sempre da cena principal, mesmo quando se
    // arranca noutra (`viber run --scene`).
    let project = crate::project::Project::discover(path);
    let main_world = if project.declared
        && project.scene_id(path).as_deref() != Some(crate::project::MAIN_SCENE)
    {
        match load_world(&project.main_scene_path()) {
            Ok(main) => Some(main),
            Err(error) => {
                bevy::log::warn!(
                    "cena principal do projeto não carrega ({error:#}) — shaders da cena de arranque"
                );
                None
            }
        }
    } else {
        None
    };
    let shader_entities = &main_world.as_ref().unwrap_or(&world).entities;
    let sky_config = sky::SkyConfig::from_world(shader_entities).with_env_override();
    let water_config = terrain::water_material::WaterSurfaceConfig::from_world(shader_entities);
    let layers_config = terrain::layer_material::TerrainChunkConfig::from_world(shader_entities);
    let shaders_dir = asset_root.join("shaders");
    let _ = std::fs::create_dir_all(&shaders_dir);
    for (name, contents) in [
        ("sky.wesl", sky_config.render_world_shader()),
        ("water.wesl", water_config.render_world_shader()),
    ] {
        if let Err(e) = std::fs::write(shaders_dir.join(name), contents) {
            eprintln!(
                "viber: falha ao escrever {}/{name}: {e}",
                shaders_dir.display()
            );
        }
    }
    if let Some(layers_config) = &layers_config
        && let Err(e) = std::fs::write(
            shaders_dir.join("terrain_chunk.wesl"),
            layers_config.render_world_shader(),
        )
    {
        eprintln!(
            "viber: falha ao escrever {}/terrain_chunk.wesl: {e}",
            shaders_dir.display()
        );
    }
    // SSR da água (Fase B): shader ESTÁTICO (sem especialização por mundo) —
    // só se escreve com o gate ligado, para não tocar no disco de mundos que
    // não o usam.
    if crate::water_ssr::water_ssr_requested()
        && let Err(e) = std::fs::write(
            shaders_dir.join("water_ssr.wesl"),
            crate::water_ssr::WATER_SSR_WGSL,
        )
    {
        eprintln!(
            "viber: falha ao escrever {}/water_ssr.wesl: {e}",
            shaders_dir.display()
        );
    }
    let mut app = App::new();
    // Cinemáticas do mundo (`<Cinematic>`): biblioteca antes do plugin (o
    // `init_resource` dele não a substitui).
    app.insert_resource(crate::cinematic::CinematicLibrary(
        world
            .cinematics
            .iter()
            .map(|spec| (spec.id.clone(), spec.clone()))
            .collect(),
    ));
    // O modo viaja como resource: os run conditions (`scripts_enabled`, …)
    // leem-no para congelar a simulação no editor.
    app.insert_resource(mode);
    // Registered before `AssetPlugin`, which snapshots the sources when it
    // builds. The reader is multi-root (world → extras do config) and expands
    // `EXT_meshopt_compression`, so the engine reads compressed GLBs as
    // authored — no per-example mirror.
    meshopt::register_asset_source(&mut app, asset_roots.clone());
    // O config viaja COMO RESOURCE: ambient (sfx_dir), save (dir), terreno
    // (textures_dir) e o spawn (bgm_dir) leem-no de lá.
    app.insert_resource(config.clone());
    app.insert_resource(save::SaveDir(Some(config.save_dir().to_path_buf())));
    // Quests do MUNDO, lidas do DISCO (dir do config, default `quests/` ao
    // lado do world.xml) — o conteúdo deixou de vir embutido na engine. O
    // insert é ANTES dos plugins: o `init_resource` do QuestsPlugin não
    // substitui o que já existe.
    app.insert_resource(quests::QuestLog::with_dir(
        &config.quests_dir_on(&world_dir),
    ));
    // O modelo do céu também viaja como resource — o IBL (`ibl.rs`) pinta o
    // cubemap com a MESMA radiância que o domo desenha e os probes regionais
    // (`probes.rs`) usam os mesmos coeficientes. Sem ele os dois lêem `None` e
    // ficam presos em `analytic`, qualquer que seja o `<Sky model>`.
    app.insert_resource(sky::SkyModelState::from_config(&sky_config));
    // (a linha do modelo resolvido sai depois de `add_plugins` — o subscriber
    // de tracing só existe a partir daí; ver `viber debug logs`)
    // DLSS (src/dlss.rs): com a feature compilada, o `DlssInitPlugin` entra
    // sozinho no DefaultPlugins (antes do RenderPlugin) — mas EXIGE o
    // `DlssProjectId` já no mundo quando constrói (panica sem ele). Tem de
    // ser ANTES do `add_plugins` abaixo.
    #[cfg(feature = "dlss")]
    app.insert_resource(crate::dlss::project_id());
    let mut plugins = bevy::DefaultPlugins
        .set(bevy::window::WindowPlugin {
            primary_window: Some(bevy::window::Window {
                title,
                ..Default::default()
            }),
            ..Default::default()
        })
        .set(bevy::asset::AssetPlugin {
            file_path: asset_root.to_string_lossy().into_owned(),
            ..Default::default()
        });
    if bridge_port.is_some() {
        // A layer de logs do bridge tem de ser instalada no LogPlugin no boot.
        plugins = plugins.set(bridge::logs::log_plugin_with_bridge());
    }
    if gpu_profiling_enabled() {
        // As queries de timestamp são uma feature do DEVICE: têm de ser
        // pedidas na criação do renderer, não dá para ligar depois. Partimos
        // do default (que traz o `TEXTURE_ADAPTER_SPECIFIC_FORMAT_FEATURES`
        // de que o KTX2 depende) e só acrescentamos as três de timestamp.
        use bevy::render::settings::{WgpuFeatures, WgpuSettings};
        let base = WgpuSettings::default();
        plugins = plugins.set(bevy::render::RenderPlugin {
            render_creation: WgpuSettings {
                features: base.features
                    | WgpuFeatures::TIMESTAMP_QUERY
                    | WgpuFeatures::TIMESTAMP_QUERY_INSIDE_ENCODERS
                    | WgpuFeatures::TIMESTAMP_QUERY_INSIDE_PASSES,
                ..base
            }
            .into(),
            ..Default::default()
        });
    }
    app.add_plugins(plugins);
    // Picking (DefaultPickingPlugins): o backend de UI alimenta a
    // `Interaction` da UI declarativa (src/ui/interaction.rs). O MESH picking
    // não tem consumidor nenhum na engine e, com `require_markers` false
    // (default), raycastava TODAS as malhas visíveis em TODAS as câmaras por
    // frame — com markers, só entidades com `Pickable` (nenhuma) são
    // raycastadas. Se um dia se quiser clique em malhas, criar a entidade com
    // `Pickable` e desfazer esta linha.
    app.insert_resource(bevy::picking::mesh_picking::MeshPickingSettings {
        require_markers: true,
        ..Default::default()
    });
    if gpu_profiling_enabled() {
        app.add_plugins(bevy::render::diagnostic::RenderDiagnosticsPlugin);
        info!(
            "profiler: render diagnostics ON (VIBER_PROF_GPU) — `viber debug prof --json` traz gpu[]"
        );
    }
    // SSR de reflexões raster (Fase B, src/water_ssr.rs): reflexo de cena na
    // água + chão molhado na chuva por raymarch sobre o depth prepass.
    // OPT-IN (`VIBER_WATER_SSR=1`) enquanto não existir acumulação temporal
    // própria do passe — sem ela o reflexo dança com as ondas (medido).
    app.add_plugins(crate::water_ssr::WaterSsrPlugin);
    // Probes regionais (src/probes.rs): bounce de bioma nos pads com tint,
    // parallax-corrected. DEFAULT ON — `VIBER_PROBES=0` desliga.
    app.add_plugins(crate::probes::RegionalProbesPlugin);
    // Céu/dia/noite, IBL e probes regionais resolvem o modelo por AQUI (o
    // subscriber de tracing já existe): é a linha que confirma na QA que o
    // gate `<Sky model>`/`VIBER_SKY_MODEL` chegou ao render e ao IBL.
    info!(
        "sky: modelo {} (attr <Sky model> / VIBER_SKY_MODEL)",
        match sky_config.model {
            sky::SkyModel::Nishita => "nishita",
            sky::SkyModel::Analytic => "analytic",
        }
    );
    // Áudio: backend kira (bevy_kira_audio) com buses tipados — o
    // `AudioMixerSettings` (save/menu/XML) empurra volumes para os canais
    // (crate::music::mixer_sync) e tudo o que está a tocar responde ao vivo.
    // O bevy_audio/rodio do Bevy fica compilado mas sem players — nenhum
    // som nasce por ele.
    app.add_plugins(bevy_kira_audio::AudioPlugin);
    app.add_audio_channel::<crate::music::MusicBus>();
    app.add_audio_channel::<crate::music::SfxBus>();
    if let Some(port) = bridge_port {
        app.add_plugins(bridge::BridgePlugin { port });
        // O ping passa a identificar o mundo servido — o `viber debug
        // --world` valida-o contra o engine.json (registo stale → erro, não
        // mutação do mundo errado).
        app.insert_resource(bridge::BridgeIdentity {
            world: path.display().to_string(),
        });
        // Regista a engine no registo de sessões MESMO fora de `session up`:
        // assim qualquer `viber debug …` descobre a porta sozinho. Se já há
        // uma engine viva registada (sessão de outro agente), não mexe — o
        // descobridor encontra-a a ela.
        let session = crate::session::SessionPaths::for_world(path);
        let register = match session.engine_info() {
            Some(existing) if existing.pid != std::process::id() => {
                !bridge::client::port_alive(existing.port)
            }
            _ => true,
        };
        if register {
            let _ = session.write_engine(&crate::session::EngineInfo {
                pid: std::process::id(),
                port,
                world: path.display().to_string(),
                started_at_ms: std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_millis() as u64)
                    .unwrap_or(0),
                log: String::new(),
            });
        }
        // O agente que lançou a engine lê esta linha: o caminho para APONTAR
        // os seus `viber debug` a ESTA engine (e não à de outro agente).
        eprintln!(
            "viber: debug bridge at http://127.0.0.1:{port} — viber debug --world {} … (ou export VIBER_BRIDGE_PORT={port})",
            path.display()
        );
    }
    // Documento editável do mundo (`crate::scene`): a fonte das edições de agentes
    // (`viber.scene.*`) e do ViberEditor — carregado em todos os modos, é barato.
    app.add_plugins((
        crate::scene::live::ScenePlugin,
        crate::scene::sync::SceneSyncPlugin,
    ));
    app.insert_resource(crate::scene::doc::SceneDoc::load(path)?);
    // O save é do JOGO (projeto), não da cena: a chave é sempre a da cena principal.
    let save_key_path = if project.declared {
        project.main_scene_path()
    } else {
        path.to_path_buf()
    };
    app.insert_resource(save::WorldSaveKey(save::world_save_key(&save_key_path)));
    let scene_title = world.title.clone();
    // Autoloads do projeto: parseados aqui, spawnados uma vez antes da primeira cena.
    if project.declared {
        let mut autoload = Vec::new();
        for file in project.autoload_paths() {
            let loaded = crate::xml::include::load_world_in(&file, &project.root)
                .with_context(|| format!("autoload {}", file.display()))?;
            let parsed = recipes::parse_world(&loaded.root_attrs, &loaded.nodes)
                .with_context(|| format!("autoload {}", file.display()))?;
            autoload.push(parsed);
        }
        if !autoload.is_empty() {
            app.insert_resource(crate::scene_loader::PendingAutoload(autoload));
        }
    }
    app.insert_resource(crate::scene_loader::CurrentScene::for_path(
        path,
        scene_title,
    ));
    if let Some(entry) = start_entry {
        app.insert_resource(crate::scene_loader::StartEntry(Some(entry)));
    }
    app.insert_resource(PendingWorld {
        world,
        base_dir: world_base_dir(path),
    });
    // `worldsys::sun_drive` aims the directional light from it; nothing was
    // creating it, so that system failed parameter validation.
    app.init_resource::<worldsys::SunState>();
    // Luz de interior (ambiente fixo, sem noite) — ver `InteriorLighting`.
    app.init_resource::<worldsys::InteriorLighting>();
    // Sombras de qualidade (passe visual r1): mapa DIRECIONAL 4096 — o
    // default (2048) deixava as sombras das árvores granuladas mesmo ao pé do
    // herói; é UMA textura (4 cascatas), ~268 MB, e o sol justifica-a.
    // `VIBER_DIR_SHADOW_SIZE` para o A/B 4096→2048 que o PERFORMANCE.md lista
    // desde a 1.ª passagem (o default fica 4096 — sombras das árvores ao pé
    // do herói, decisão do r1). Os gêmeos das point lights e das cascatas:
    // `VIBER_POINT_SHADOW_SIZE` (aqui) e `VIBER_SHADOW_CASCADES`/
    // `VIBER_SHADOW_DISTANCE` (recipes::sun_shadow_*).
    app.insert_resource(bevy::light::DirectionalLightShadowMap {
        size: dir_shadow_size(),
    });
    // Point light: 1024. O array é `size² × 4 B × 6 faces × nº de luzes com
    // sombra` — a 1536 eram ~56 MB POR LANTERNA, ~680 MB com o orçamento de 12
    // cheio, e cada mudança de contagem REALOCA o array inteiro com o pico
    // transitório de ambos (foi o `point_light_shadow_map_texture` a falhar o
    // OOM que matou uma sessão de 10 h a 2026-09-12 03:06). O r1 subiu para
    // 1536 pelos GRANULADOS da DIRECIONAL — nas point lights o PCSS
    // (`soft_shadow_size` = radius da lanterna) desfaz a borda e o mapa
    // cobre o `range` (20 m): ~2,6 cm/texel a 1024 contra ~1,7 a 1536, numa
    // sombra cujo caster está a metros da luz. A 1024: ~25 MB/lanterna,
    // ~300 MB no pior caso. `VIBER_POINT_SHADOW_SIZE=1536` devolve o r1
    // (A/B visual de QA — os dois valores têm de ser comparados com o
    // protocolo do controle antes de reclamar diferença nenhuma).
    app.insert_resource(bevy::light::PointLightShadowMap {
        size: point_shadow_size(),
    });
    // `hud_menu_system` corre sempre (main loop), mas o `HudMenuState` só
    // nascia dentro de `build_menu` — mundos sem `TabbedModal` (o HUD é agora
    // declarativo) panicas na validação do `ResMut` todos os frames.
    app.init_resource::<hud::menu::HudMenuState>();
    // `sky::spawn_sky` needs `Assets<SkyMaterial>`; without this plugin the
    // startup system panics and leaves `Assets<Mesh>` taken out of the world.
    // O mesmo para o material de água (`Assets<WaterMaterial>` no bootstrap
    // do terreno).
    app.add_plugins(bevy::pbr::MaterialPlugin::<sky::SkyMaterial>::default());
    app.add_plugins(bevy::pbr::MaterialPlugin::<
        terrain::water_material::WaterMaterial,
    >::default());
    // O material das camadas de terreno POR CHUNK (`layers="…"`): o
    // bootstrap precisa de `Assets<TerrainChunkMaterial>` — sem este plugin
    // um mundo com camadas degradaria para o caminho legado.
    app.add_plugins(bevy::pbr::MaterialPlugin::<
        terrain::layer_material::TerrainChunkMaterial,
    >::default());
    app.add_plugins(animation::AnimationPlugin);
    app.add_plugins(physics::PhysicsPlugin {
        debug: std::env::var_os("VIBER_PHYSICS_DEBUG").is_some(),
    });
    // Pós-processamento (exposição/bloom/SSAO) na câmara do mundo; os
    // `pp-*` das `<BiomeRegion>` conduzem-no. `VIBER_NO_POSTFX=1` desliga.
    app.add_plugins(postfx::PostFxPlugin);
    // `<PostFxDebugToggle>`: teclas que comutam os gates de pós-processo ao
    // vivo (inerte sem a tag no mundo).
    app.add_plugins(crate::postfx_toggle::PostFxDebugTogglePlugin);
    // `<AdaptiveQuality>`: tiers de qualidade pelo frame-time real (SSAO,
    // volumétrico, sombras de ponto, cortes de efeitos).
    app.add_plugins(crate::adaptive_quality::AdaptiveQualityPlugin);
    // `<SpawnGate>`: segura a entidade-alvo no ar até haver collider de
    // terreno sob ela (inerte sem a tag).
    app.add_plugins(crate::spawn_gate::SpawnGatePlugin);
    // `<ProjectileTemplate>` + `viber.fire_projectile`: projéteis simples
    // (linha reta ou arco balístico) com dano por facção.
    app.add_plugins(crate::projectile::ProjectilePlugin);
    // IBL vivo do céu (LightProbe + cubemap da paleta da atmosfera, filtrado
    // na GPU) — ambiente/reflexos que seguem a hora do dia. `VIBER_NO_IBL=1`.
    app.add_plugins(crate::ibl::SkyIblPlugin);
    app.add_plugins(terrain::TerrainPlugin);
    app.add_plugins(terrain::runtime::TerrainFeaturesPlugin);
    // Sub-bosque instanciado (a relva que o `<Vegetation>` não consegue ser):
    // tiles de lâminas fundidas a 16 m à volta da câmara, vento no vertex
    // shader, paleta por bioma. `VIBER_GRASS=0` desliga; `VIBER_GRASS_DENSITY`
    // escala as densidades.
    app.add_plugins(grass::GrassPlugin);
    // Escritor único de samplers/mipmaps das texturas carregadas (o registro
    // `WorldTiledTextures` é consumido aqui; escrever o sampler noutro
    // sistema reabria a corrida clamp/REPEAT das texturas de chão).
    app.add_plugins(textures::TexturesPlugin);
    // Fase 2: Luau — scripts do `scripts_dir` do config, com `on_update(dt)`.
    // O árbitro das interações (`interact::InteractionFocus`) é registado pelo
    // próprio `LuauScriptPlugin` — quem o lê é o `viber.interacted`.
    app.add_plugins(luau::LuauScriptPlugin {
        scripts_dir: config.scripts_dir_on(&world_dir),
    });

    // ── Preset RPG (config.yaml `gameplay`, default "rpg") ─────────────
    // Combat/skills/vitals/feedback/economia/travel/save/quests/colheita
    // são DOMÍNIO de RPG: com `gameplay: none` não entram no App e um jogo
    // novo vive só de XML + Luau (worlds/lua-demo). `gameplay_rpg()` cobre
    // o default; `gameplay: racing` troca o preset por karts + corrida.
    let rpg = config.gameplay_rpg();
    // Diagnóstico do preset (o gate decide quais plugins entram): visível no
    // arranque e no analyze — `gameplay: none` sem efeito era invisível.
    // Corrida e RPG de regras vivem nos scripts do exemplo, não num preset.
    info!(
        target: "viber",
        "gameplay preset: {} (config `gameplay: {}`)",
        if rpg { "rpg" } else { "none (só engine)" },
        config.gameplay.as_deref().unwrap_or("<ausente → default rpg>")
    );
    // Serviços de ENGINE que os plugins RPG costumavam registar e que o resto
    // do runtime lê: o semáforo de input tem de existir mesmo sem RPG
    // (player_movement/profiler leem-no) e os toasts de script são UX de
    // engine — o MenusPlugin fica FORA do gate (o seu lado RPG — catálogo da
    // loja — é inerte sem vault, e `own_action` de Lua manda quando existe).
    app.init_resource::<menus::MenusOpen>();
    app.init_resource::<music::MusicLayerOverride>();
    app.init_resource::<music::ScriptSoundLoops>();
    // Latch de input (remote desktop / taps): renova "held" para
    // player_movement/vehicle_movement — ver `input_latch.rs`.
    app.init_resource::<crate::input_latch::KeyLatches>();
    app.add_systems(
        bevy::app::Update,
        crate::input_latch::key_latch_system.before(player::player_movement),
    );
    app.add_plugins(menus::MenusPlugin);
    // Save/load & opções: serviço de ENGINE (grava `world_kv`/posição/vitais
    // em qualquer preset; os campos RPG só entram quando os recursos existem).
    app.add_plugins(save::SavePlugin);
    // FX puros para mundos SEM o preset RPG: o CombatPlugin registra o kick
    // de FOV e o hit-stop; sem ele, as primitivas `viber.fov_kick/hit_stop`
    // (e o shake/kick/punch que vivem em recursos incondicionais) ficariam
    // inertes. Registo CONDICIONAL — registar o mesmo sistema 2× duplica a
    // instância no schedule e panica ao inicializar.
    // Hit-stop e FOV kick são FX da engine (`viber.hit_stop` / `fov_kick`).
    // Combate, vitals, economia, viagem, skills e quests são regras do
    // exemplo — vivem em Lua, não entram no App.
    app.init_resource::<camera::CameraFx>();
    app.add_systems(bevy::app::Update, camera::fov_kick_system);
    app.init_resource::<combat::HitStop>();
    app.init_resource::<combat::BaseTimeScale>();
    app.add_systems(bevy::app::Update, combat::hit_stop_system);
    let _ = rpg;

    // Mundo vivo (loop 9): fog/tint por BiomeRegion, orçamento de luzes,
    // gestos idle de NPC, SFX.
    app.add_plugins(ambient::AmbientPlugin);
    // day_tint para os materiais dos GltfScene (copas/casas/props) — sem
    // isto ficam com albedo de dia sob o luar e leem-se "recortados" à noite
    // (peça exploração, r10; mecanismo documentado no módulo).
    app.add_plugins(prop_tint::PropTintPlugin);
    // Física Fase 3 (loop 10): knockback cinemático + destrutíveis com queda.
    app.add_plugins(physics_fx::PhysicsFxPlugin);
    // Colheita é `scripts/game/harvest.lua` (`viber.destructibles`).
    // Sword trace (ribbon da lâmina) + bursts one-shot de partícula.
    app.add_plugins(trail::TrailPlugin);
    app.add_plugins(particles::BurstPlugin);
    // FX de impacto de combate: recoil (squash-and-stretch) do inimigo
    // atingido + anel fino no chão (slam/bomba e avisos de golpe).
    app.add_plugins(impact::ImpactFxPlugin);
    // FX de água: splash na entrada/saída e esteira de ondas de quem
    // caminha dentro do lago/rio (a lâmina é estática — o rasto tem de
    // viver no ECS, ver `terrain::water_fx`).
    app.add_plugins(terrain::water_fx::WaterFxPlugin);
    // Trauma da camera shake — o melee (e o dano recebido) somam aqui.
    app.init_resource::<camera::CameraShake>();
    // Solavanco direcional da câmara no impacto (mola; o combate soma impulsos).
    app.init_resource::<camera::CameraKick>();
    // Quests, marcadores e o guia são do exemplo (`scripts/lib/rules.lua`).
    app.add_plugins(crate::cinematic::CinematicPlugin);
    // IA (FSM Rust + respawn): criaturas de <DynamicSpawner> SEM script caem
    // aqui — sem este plugin nasciam estátuas eternas e a RespawnQueue
    // nunca drenava.
    app.add_plugins(ai::AiPlugin);
    // Navegação: navmesh Recast num tile que segue o herói, A* + evitamento
    // RVO (bevy_landmass) e preferência por estradas via custo por tipo de
    // polígono. Sem isto a IA volta à linha recta de sempre — o que é
    // exactamente o que `VIBER_NAV=0` faz.
    app.add_plugins(nav::NavPlugin);
    // LOD de render: culling por distância nas instâncias de spawner +
    // orçamento de sombras. Sem isto as ~9700 cenas glTF do simple-rpg
    // (60k entidades) entram todas nas 4 cascatas de sombra a cada frame.
    // `VIBER_RENDER_LOD=0` desliga o culling + a ladder de LOD: é o A/B
    // honesto para medir o ganho sem trocar de binário.
    if std::env::var("VIBER_RENDER_LOD").as_deref() != Ok("0") {
        app.add_plugins(render_lod::RenderLodPlugin);
    }
    // Profiler: overlay F3 (fps/frame/entidades/scripts ativos) + `viber.profiler`.
    app.add_plugins(profiler::ProfilerPlugin);
    // UI declarativa (XML + folha de estilo + `viber.ui` no Luau): o HUD do
    // mundo é autoria, não código.
    app.add_plugins(ui::UiPlugin);
    // A instalação de `viber.ui` tem de ganhar ao runtime Luau: sem ordem,
    // um script activado no frame 1 via `luau_on_add` podia ver `viber.ui`
    // nil (morria — `Added` dispara 1×) ou queimar o warn-once.
    app.add_systems(
        bevy::app::Update,
        ui::install_ui_script_api
            .before(luau::luau_on_add)
            .before(luau::luau_update),
    );
    // As mutações que os scripts enfileiram via `viber.ui.*` têm de ser
    // aplicadas DEPOIS de `luau_update` as produzir — sem ordem, chegavam um
    // frame tarde (ou em corrida com o produtor). Ordena-se o SET: o
    // `apply_ui_commands` já vive no `UiSet::Script` e re-adicioná-lo aqui
    // duplicava a instância no schedule (pânico "more than one instance").
    // Sem ciclo: a chain luau não referencia UiSets.
    app.configure_sets(
        bevy::app::Update,
        ui::UiSet::Script.after(luau::luau_update),
    );
    // E a UI PUBLICA antes dos scripts lerem: os cliques de um frame chegam
    // ao `on_update` NO MESMO frame, haja como for a ordem arbitrária entre
    // plugins — sem isto, `viber.ui.clicked(…)` perdia cliques por corrida
    // (o publish rodava depois do script e limpava a vista).
    app.configure_sets(
        bevy::app::Update,
        (ui::UiSet::Collect, ui::UiSet::Build, ui::UiSet::Bind).before(luau::luau_update),
    );
    app.add_systems(bevy::app::Startup, spawn::startup);
    // Trocas de cena (`crate::scene_loader`): o mesmo spawn, num schedule re-executável.
    app.add_systems(crate::scene_loader::SceneLoad, spawn::startup);
    app.add_plugins(crate::scene_loader::SceneLoaderPlugin);
    // Câmara livre de editor (`--camera free`, src/editor_camera.rs): a
    // navegação orbit/pan/zoom do ViberEditor via bridge (`viber.camera.*`).
    // ADITIVO — sem a flag o plugin não regista nada e o comportamento é
    // 100% o de sempre; com ela, a câmara de jogo fica inactiva e os
    // sistemas gated abaixo (input do player, terceira-pessoa) param.
    app.add_plugins(editor_camera::EditorCameraPlugin {
        enabled: camera_free,
    });
    // Spawn RUNTIME de prototypes (viber.spawn_prototype): exclusivo, no
    // PostUpdate (depois dos scripts do Update — as callbacks da entidade nova
    // correm aqui) e ANTES da propagação: sem a aresta a entidade podia
    // renderizar um frame com o `GlobalTransform` identidade (na origem).
    app.add_systems(
        bevy::app::PostUpdate,
        recipes::spawn::apply_script_spawns.before(bevy::transform::TransformSystems::Propagate),
    );
    app.add_systems(
        bevy::app::Update,
        (
            timed(Group::Hud, hud::hud_health_sync),
            hud::hud_xp_sync,
            // Balão de diálogo: única via que decrementa o timer e volta a
            // esconder — sem registo, o balão ficava no ecrã para sempre.
            timed(Group::Hud, hud::hud_balloon_update),
            // Modal [Q]: sincroniza abas/conteúdos e trata cliques.
            timed(Group::Hud, hud::menu::hud_menu_system),
        ),
    );
    app.add_systems(
        bevy::app::Update,
        (
            // Deterministic order: the rigid follow skips third-person
            // cameras, the player steers their yaw with A/D, then the
            // third-person camera trails it. All three touch OrbitCamera.
            (
                timed(Group::Camera, spawn::orbit_camera_follow),
                // Modo editor (`--camera free`): o WASD não move o herói e a
                // terceira-pessoa não disputa a pose — a câmara de editor é
                // a única activa (ver `editor_camera`).
                timed(Group::Player, player::player_movement)
                    .run_if(editor_camera::editor_camera_inactive),
                timed(Group::Camera, camera::third_person_camera)
                    .run_if(editor_camera::editor_camera_inactive),
            )
                .chain(),
            timed(Group::Camera, spawn::auto_orbit),
            timed(Group::Spawner, spawn::gltf_scene_spawner),
            timed(Group::Player, player::dialogue_interaction),
            hud::hud_prompt_update,
            hud::compass::hud_compass_update,
            hud::compass::hud_compass_landmarks_update,
            hud::hide_world_hud_in_interior,
            hud::waypoint::update_waypoint_arrow,
            timed(Group::Hud, hud::hud_minimap_update),
            timed(Group::Hud, hud::hud_minimap_landmarks_update),
            timed(Group::World, music::audio_loop_starter),
            timed(Group::World, music::mixer_sync),
            timed(Group::World, music::music_driver),
            timed(Group::World, music::script_sound_loops),
            // A bandeira de interior é lida pelo `daycycle_drive` e pelo
            // tint dos props — decidida ANTES deles, no mesmo frame.
            timed(Group::World, worldsys::interior_lighting_drive).before(worldsys::daycycle_drive),
            timed(Group::World, worldsys::daycycle_drive),
            // Regime de interior por CIMA dos drivers do mundo (sol/IBL).
            timed(Group::World, worldsys::interior_lighting_apply)
                .after(worldsys::sun_drive)
                .after(worldsys::daycycle_drive),
            // `weather_drive`/`atmosphere_drive` vivem no AmbientPlugin (dono
            // do `AtmosphereState`), ordenados contra este `sun_drive`.
            timed(Group::World, worldsys::sun_drive),
        ),
    );
    // Tuplo dividido: o Bevy limita tuples de sistemas a 20 elementos.
    // Constraints são explícitas (.after), a separação não muda semântica.
    app.add_systems(
        bevy::app::Update,
        (
            // Clamp da borda DEPOIS do movimento/dash do player (mesmo frame) —
            // sem ordem, o clamp viajava um frame atrás do WASD. A aresta para
            // `abilities_system` é no-op no preset `gameplay: none` (alvo
            // ausente do schedule é tolerado) e mantém a semântica no RPG.
            worldsys::world_border_clamp
                .after(player::player_movement)
                .after(skills::abilities_system),
            sky::sky_follow_camera,
            hud::hud_toggle,
            // Lifecycle de FX ambiente por proximidade (`src/particles.rs`):
            // monta/desmonta os bundles dos markers `AmbientFx` (espuma da
            // água, `<ParticleSystem>` autorais) pela distância da câmara —
            // ENCADEADO antes do update para os recém-montados simularem já
            // neste frame (tuplos de sistemas não garantem ordem).
            (
                timed(Group::Fx, particles::ambient_fx_manager),
                timed(Group::Fx, particles::particle_emitter_update),
            )
                .chain(),
            timed(Group::Spawner, spawner::instantiate_spawn_groups),
        ),
    );
    // Debug de vitais [H/N/K] (preset RPG). O shake no dano recebido vive no
    // FeedbackPlugin — registá-lo também aqui corria-o 2× por frame.
    // O debug de vitais [H] dano / [N] cura total / [K] +XP é batota em jogo
    // normal (o [K] ainda colidia com a loja): só com `VIBER_DEBUG_KEYS=1`.
    if rpg && std::env::var_os("VIBER_DEBUG_KEYS").is_some_and(|v| v != "0") {
        app.add_systems(bevy::app::Update, vitals::debug_damage);
    }
    if mode == RunMode::Edit {
        // Cena congelada: física, IA e animações param (tempo virtual em
        // pausa); o render, o céu e o LOD continuam a correr.
        app.add_systems(bevy::app::Startup, pause_virtual_time);
    }
    Ok(app)
}
