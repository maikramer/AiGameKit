//! Friendly, bilingual (PT/EN) display strings for the editor UI.
//!
//! This is purely the display layer: the XML tags and attribute keys, the `viber scene` CLI
//! and the engine's own labels keep their code names — agents and the editor edit the same
//! document either way. Every user-visible name goes through [`t`] / [`tag_display`] /
//! [`attr_display`] so the whole UI flips with the toolbar's language toggle (default:
//! Portuguese, override with `--lang en`).
//!
//! Static labels are spawned with their [`StrKey`] plus a [`LangLabel`] (or [`LangTooltip`])
//! marker; [`sync_lang_labels`] rewrites them in place when [`Lang`] changes. Panels that
//! rebuild themselves (tree, inspector, world doc) read [`Res<Lang>`] directly and add it to
//! their stale checks.

use bevy::prelude::*;
use bevy::ui_widgets::Activate;

use super::tooltip::Tooltip;

/// The editor's UI language (default: Portuguese).
#[derive(Resource, Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Lang {
    #[default]
    Pt,
    En,
}

impl Lang {
    /// Parses the `--lang` CLI value.
    pub fn parse(value: &str) -> Option<Self> {
        match value.to_ascii_lowercase().as_str() {
            "pt" | "portugues" | "português" => Some(Lang::Pt),
            "en" | "english" => Some(Lang::En),
            _ => None,
        }
    }

    /// The code shown on the toolbar's language button.
    pub fn code(self) -> &'static str {
        match self {
            Lang::Pt => "PT",
            Lang::En => "EN",
        }
    }
}

/// Every static label in the editor (dock titles, buttons, tooltips, hints).
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StrKey {
    // Docks and tabs.
    #[default]
    DockScene,
    DockInspector,
    TabConsole,
    TabLogs,
    TabWorldDoc,
    TabFiles,
    TabProfiler,
    FilesInsert,
    FilesOpenWorld,
    FilesOpenExternal,
    FilesOpened,
    FilesOpenFailed,
    BtnAddNode,
    AddNodeTitle,
    DropGlb,
    GroupContent,
    GroupShape,
    GroupLight,
    GroupWater,
    GroupFeature,
    Mode3D,
    BtnPlayHere,
    TipPlayHere,
    BtnRemoteTree,
    TipRemoteTree,
    StateNormal,
    StateHover,
    StatePressed,
    CanvasDims,
    CanvasMenu,
    SecTransform,
    SecPhysics,
    SecLight,
    SecMaterial,
    SecShape,
    SecGltf,
    EditTransform,
    EditPaste,
    ModeUi,
    ModeScript,
    ScriptTitle,
    FilesInsertTip,
    FilesOpenWorldTip,
    FilesOpenExternalTip,
    // Toolbar buttons and tooltips.
    BtnSave,
    TipSave,
    BtnPlay,
    TipPlay,
    BtnPause,
    TipPause,
    BtnStop,
    TipStop,
    TipLang,
    // Tool strip tooltips.
    TipSelect,
    TipMove,
    TipRotate,
    TipScale,
    TipTerrainBrush,
    TipSpace,
    TipFrame,
    // Status bar tool hints.
    HintSelect,
    HintMove,
    HintRotate,
    HintScale,
    HintTerrain,
    // Inspector.
    InspectNone,
    InspectGone,
    AddAttr,
    BtnRegen,
    BtnSettle,
    // World Doc.
    WdNoBake,
    WdTerrain,
    WdFeatures,
    WdFeaturesNone,
    WdOk,
    WdFailed,
    // Console.
    ConOk,
    ConFailed,
    // Gizmo verbs (edit labels).
    VerbMove,
    VerbRotate,
    VerbScale,
}

#[cfg(test)]
impl StrKey {
    /// Every key (the test checks both languages for all of them).
    pub const ALL: &[StrKey] = &[
        StrKey::DockScene,
        StrKey::DockInspector,
        StrKey::TabConsole,
        StrKey::TabLogs,
        StrKey::TabWorldDoc,
        StrKey::TabFiles,
        StrKey::TabProfiler,
        StrKey::FilesInsert,
        StrKey::FilesOpenWorld,
        StrKey::FilesOpenExternal,
        StrKey::FilesOpened,
        StrKey::FilesOpenFailed,
        StrKey::BtnAddNode,
        StrKey::AddNodeTitle,
        StrKey::DropGlb,
        StrKey::GroupContent,
        StrKey::GroupShape,
        StrKey::GroupLight,
        StrKey::GroupWater,
        StrKey::GroupFeature,
        StrKey::Mode3D,
        StrKey::BtnPlayHere,
        StrKey::TipPlayHere,
        StrKey::BtnRemoteTree,
        StrKey::TipRemoteTree,
        StrKey::StateNormal,
        StrKey::StateHover,
        StrKey::StatePressed,
        StrKey::CanvasDims,
        StrKey::CanvasMenu,
        StrKey::ModeUi,
        StrKey::ModeScript,
        StrKey::ScriptTitle,
        StrKey::FilesInsertTip,
        StrKey::FilesOpenWorldTip,
        StrKey::FilesOpenExternalTip,
        StrKey::BtnSave,
        StrKey::TipSave,
        StrKey::BtnPlay,
        StrKey::TipPlay,
        StrKey::BtnPause,
        StrKey::TipPause,
        StrKey::BtnStop,
        StrKey::TipStop,
        StrKey::TipLang,
        StrKey::TipSelect,
        StrKey::TipMove,
        StrKey::TipRotate,
        StrKey::TipScale,
        StrKey::TipTerrainBrush,
        StrKey::TipSpace,
        StrKey::TipFrame,
        StrKey::HintSelect,
        StrKey::HintMove,
        StrKey::HintRotate,
        StrKey::HintScale,
        StrKey::HintTerrain,
        StrKey::InspectNone,
        StrKey::InspectGone,
        StrKey::AddAttr,
        StrKey::BtnRegen,
        StrKey::BtnSettle,
        StrKey::WdNoBake,
        StrKey::WdTerrain,
        StrKey::WdFeatures,
        StrKey::WdFeaturesNone,
        StrKey::WdOk,
        StrKey::WdFailed,
        StrKey::ConOk,
        StrKey::ConFailed,
        StrKey::VerbMove,
        StrKey::VerbRotate,
        StrKey::VerbScale,
    ];
}

/// The localized text of a static label.
pub fn t(lang: Lang, key: StrKey) -> &'static str {
    let en = matches!(lang, Lang::En);
    match key {
        StrKey::DockScene => {
            if en {
                "Objects"
            } else {
                "Objetos"
            }
        }
        StrKey::DockInspector => {
            if en {
                "Inspector"
            } else {
                "Inspetor"
            }
        }
        StrKey::TabConsole => {
            if en {
                "Console"
            } else {
                "Consola"
            }
        }
        StrKey::TabLogs => {
            if en {
                "Logs"
            } else {
                "Registos"
            }
        }
        StrKey::TabWorldDoc => {
            if en {
                "World Overview"
            } else {
                "Resumo do Mundo"
            }
        }
        StrKey::TabFiles => {
            if en {
                "Files"
            } else {
                "Ficheiros"
            }
        }
        StrKey::FilesInsert => {
            if en {
                "Insert in scene"
            } else {
                "Inserir na cena"
            }
        }
        StrKey::FilesOpenWorld => {
            if en {
                "Open as world"
            } else {
                "Abrir como mundo"
            }
        }
        StrKey::FilesOpenExternal => {
            if en {
                "Open externally"
            } else {
                "Abrir fora"
            }
        }
        StrKey::FilesOpened => {
            if en {
                "opened"
            } else {
                "aberto"
            }
        }
        StrKey::FilesOpenFailed => {
            if en {
                "open failed"
            } else {
                "falhou ao abrir"
            }
        }
        StrKey::BtnAddNode => {
            if en {
                "Add node"
            } else {
                "Adicionar nó"
            }
        }
        StrKey::AddNodeTitle => {
            if en {
                "Add node — pick a template"
            } else {
                "Adicionar nó — escolhe um modelo"
            }
        }
        StrKey::DropGlb => {
            if en {
                "drop GLB"
            } else {
                "soltar GLB"
            }
        }
        StrKey::GroupContent => {
            if en {
                "Content"
            } else {
                "Conteúdo"
            }
        }
        StrKey::GroupShape => {
            if en {
                "Shapes"
            } else {
                "Formas"
            }
        }
        StrKey::GroupLight => {
            if en {
                "Lights"
            } else {
                "Luzes"
            }
        }
        StrKey::GroupWater => {
            if en {
                "Water"
            } else {
                "Água"
            }
        }
        StrKey::GroupFeature => {
            if en {
                "Terrain features"
            } else {
                "Acidentes do terreno"
            }
        }
        StrKey::TabProfiler => "Profiler",
        StrKey::Mode3D => "3D",
        StrKey::ModeUi => "UI",
        StrKey::ModeScript => "Script",
        StrKey::ScriptTitle => {
            if en {
                "Scripts of this world — click to preview"
            } else {
                "Scripts deste mundo — clica para pré-visualizar"
            }
        }
        StrKey::FilesInsertTip => {
            if en {
                "Insert the GLB into the scene (at the terrain under the cursor)"
            } else {
                "Insere o GLB na cena (no terreno sob o cursor)"
            }
        }
        StrKey::FilesOpenWorldTip => {
            if en {
                "Open this world in a new editor window"
            } else {
                "Abre este mundo numa nova janela do editor"
            }
        }
        StrKey::FilesOpenExternalTip => {
            if en {
                "Open with the desktop's default app"
            } else {
                "Abrir com a aplicação por omissão do sistema"
            }
        }
        StrKey::BtnPlayHere => {
            if en {
                "Play here"
            } else {
                "Jogar daqui"
            }
        }
        StrKey::TipPlayHere => {
            if en {
                "Play from here: the hero spawns where the editor camera looks (F9)"
            } else {
                "Jogar daqui: o herói aparece onde a câmara do editor olha (F9)"
            }
        }
        StrKey::BtnRemoteTree => {
            if en {
                "Remote tree"
            } else {
                "Árvore remota"
            }
        }
        StrKey::TipRemoteTree => {
            if en {
                "Dump the running game's entity tree (viber.tree) into this panel"
            } else {
                "Despeja a árvore de entidades do jogo a correr (viber.tree) neste painel"
            }
        }
        StrKey::StateNormal => "Normal",
        StrKey::StateHover => "Hover",
        StrKey::StatePressed => "Pressed",
        StrKey::CanvasDims => {
            if en {
                "size"
            } else {
                "tamanho"
            }
        }
        StrKey::CanvasMenu => "menu data",
        StrKey::SecTransform => {
            if en {
                "Transform"
            } else {
                "Transformação"
            }
        }
        StrKey::SecPhysics => {
            if en {
                "Physics"
            } else {
                "Física"
            }
        }
        StrKey::SecLight => {
            if en {
                "Light"
            } else {
                "Luz"
            }
        }
        StrKey::SecMaterial => "Material",
        StrKey::SecShape => {
            if en {
                "Shape"
            } else {
                "Forma"
            }
        }
        StrKey::SecGltf => "GLTF / LODs",
        StrKey::EditTransform => {
            if en {
                "transform edit"
            } else {
                "editar transformação"
            }
        }
        StrKey::EditPaste => {
            if en {
                "paste"
            } else {
                "colar"
            }
        }
        StrKey::BtnSave => {
            if en {
                "Save"
            } else {
                "Guardar"
            }
        }
        StrKey::TipSave => {
            if en {
                "Save (Ctrl+S)"
            } else {
                "Guardar (Ctrl+S)"
            }
        }
        StrKey::BtnPlay => "Play",
        StrKey::TipPlay => {
            if en {
                "Play the world in a separate process (F5)"
            } else {
                "Correr o mundo num processo à parte (F5)"
            }
        }
        StrKey::BtnPause => {
            if en {
                "Pause"
            } else {
                "Pausa"
            }
        }
        StrKey::TipPause => {
            if en {
                "Freeze / resume the running game (F6)"
            } else {
                "Congelar / retomar o jogo (F6)"
            }
        }
        StrKey::BtnStop => {
            if en {
                "Stop"
            } else {
                "Parar"
            }
        }
        StrKey::TipStop => {
            if en {
                "Stop the running game (F8)"
            } else {
                "Parar o jogo (F8)"
            }
        }
        StrKey::TipLang => {
            if en {
                "Switch language (PT ⇄ EN)"
            } else {
                "Mudar língua (PT ⇄ EN)"
            }
        }
        StrKey::TipSelect => {
            if en {
                "Select (Q)"
            } else {
                "Selecionar (Q)"
            }
        }
        StrKey::TipMove => {
            if en {
                "Move (W)"
            } else {
                "Mover (W)"
            }
        }
        StrKey::TipRotate => {
            if en {
                "Rotate (E)"
            } else {
                "Rodar (E)"
            }
        }
        StrKey::TipScale => {
            if en {
                "Scale (R)"
            } else {
                "Escalar (R)"
            }
        }
        StrKey::TipTerrainBrush => {
            if en {
                "Terrain brush (B, 1-5 modes)"
            } else {
                "Pincel de terreno (B, modos 1-5)"
            }
        }
        StrKey::TipSpace => {
            if en {
                "Local / global axes (T)"
            } else {
                "Eixos locais / globais (T)"
            }
        }
        StrKey::TipFrame => {
            if en {
                "Frame selection (F)"
            } else {
                "Enquadrar a seleção (F)"
            }
        }
        StrKey::HintSelect => {
            if en {
                "Q select"
            } else {
                "Q selecionar"
            }
        }
        StrKey::HintMove => {
            if en {
                "W move"
            } else {
                "W mover"
            }
        }
        StrKey::HintRotate => {
            if en {
                "E rotate"
            } else {
                "E rodar"
            }
        }
        StrKey::HintScale => {
            if en {
                "R scale"
            } else {
                "R escalar"
            }
        }
        StrKey::HintTerrain => {
            if en {
                "B terrain"
            } else {
                "B terreno"
            }
        }
        StrKey::InspectNone => {
            if en {
                "Select an object in the Objects dock or the viewport."
            } else {
                "Seleciona um objeto nos Objetos ou no viewport."
            }
        }
        StrKey::InspectGone => {
            if en {
                "The selected object no longer exists."
            } else {
                "O objeto selecionado já não existe."
            }
        }
        StrKey::AddAttr => {
            if en {
                "new (key=value)"
            } else {
                "novo (chave=valor)"
            }
        }
        StrKey::BtnRegen => {
            if en {
                "Regenerate"
            } else {
                "Regenerar"
            }
        }
        StrKey::BtnSettle => {
            if en {
                "Settle on terrain"
            } else {
                "Assentar no terreno"
            }
        }
        StrKey::WdNoBake => {
            if en {
                "no bake — pass --bake <dir>, or place one at <world_dir>/bake"
            } else {
                "sem bake — passa --bake <dir>, ou coloca-o em <world_dir>/bake"
            }
        }
        StrKey::WdTerrain => {
            if en {
                "Terrain"
            } else {
                "Terreno"
            }
        }
        StrKey::WdFeatures => {
            if en {
                "Features"
            } else {
                "Elementos"
            }
        }
        StrKey::WdFeaturesNone => {
            if en {
                "Features: none"
            } else {
                "Elementos: nenhum"
            }
        }
        StrKey::WdOk => "OK",
        StrKey::WdFailed => {
            if en {
                "FAILED"
            } else {
                "FALHOU"
            }
        }
        StrKey::ConOk => "ok",
        StrKey::ConFailed => {
            if en {
                "failed"
            } else {
                "falhou"
            }
        }
        StrKey::VerbMove => {
            if en {
                "move"
            } else {
                "mover"
            }
        }
        StrKey::VerbRotate => {
            if en {
                "rotate"
            } else {
                "rodar"
            }
        }
        StrKey::VerbScale => {
            if en {
                "scale"
            } else {
                "escalar"
            }
        }
    }
}

/// A friendly name for an XML tag; unknown tags fall back to the raw tag.
pub fn tag_display(lang: Lang, tag: &str) -> &str {
    let en = matches!(lang, Lang::En);
    match tag.to_ascii_lowercase().as_str() {
        "entity" => {
            if en {
                "Object"
            } else {
                "Objeto"
            }
        }
        "group" => {
            if en {
                "Group"
            } else {
                "Grupo"
            }
        }
        "cuboid" => {
            if en {
                "Box"
            } else {
                "Caixa"
            }
        }
        "sphere" => {
            if en {
                "Sphere"
            } else {
                "Esfera"
            }
        }
        "cylinder" => {
            if en {
                "Cylinder"
            } else {
                "Cilindro"
            }
        }
        "capsule" => {
            if en {
                "Capsule"
            } else {
                "Cápsula"
            }
        }
        "plane" => {
            if en {
                "Plane"
            } else {
                "Plano"
            }
        }
        "pointlight" => {
            if en {
                "Light"
            } else {
                "Luz"
            }
        }
        "directionallight" => {
            if en {
                "Sun"
            } else {
                "Sol"
            }
        }
        "ambientlight" => {
            if en {
                "Ambient light"
            } else {
                "Luz ambiente"
            }
        }
        "orbitcamera" | "camera" => {
            if en {
                "Camera"
            } else {
                "Câmara"
            }
        }
        "terrain" => {
            if en {
                "Terrain"
            } else {
                "Terreno"
            }
        }
        "terrainpad" => {
            if en {
                "Platform"
            } else {
                "Plataforma"
            }
        }
        "lake" => {
            if en {
                "Lake"
            } else {
                "Lago"
            }
        }
        "river" => {
            if en {
                "River"
            } else {
                "Rio"
            }
        }
        "road" => {
            if en {
                "Road"
            } else {
                "Estrada"
            }
        }
        "roadnetwork" => {
            if en {
                "Roads"
            } else {
                "Estradas"
            }
        }
        "sky" => {
            if en {
                "Sky"
            } else {
                "Céu"
            }
        }
        "include" => {
            if en {
                "Include"
            } else {
                "Inclusão"
            }
        }
        "scatter" => {
            if en {
                "Scatter"
            } else {
                "Distribuição"
            }
        }
        "composition" => {
            if en {
                "Composition"
            } else {
                "Composição"
            }
        }
        "prototype" => {
            if en {
                "Prototype"
            } else {
                "Protótipo"
            }
        }
        "playergltf" => {
            if en {
                "Player"
            } else {
                "Jogador"
            }
        }
        "gltf" | "gltfloader" => {
            if en {
                "Model"
            } else {
                "Modelo"
            }
        }
        "uistyle" => {
            if en {
                "UI style"
            } else {
                "Estilo de interface"
            }
        }
        "uiroot" => {
            if en {
                "UI"
            } else {
                "Interface"
            }
        }
        "engineconfig" => {
            if en {
                "Config"
            } else {
                "Configuração"
            }
        }
        "world" => {
            if en {
                "World"
            } else {
                "Mundo"
            }
        }
        _ => tag,
    }
}

/// A friendly name for an XML attribute; unknown keys fall back to the raw key.
pub fn attr_display(lang: Lang, key: &str) -> &str {
    let en = matches!(lang, Lang::En);
    match key {
        "translation" | "pos" => {
            if en {
                "Position"
            } else {
                "Posição"
            }
        }
        "euler" | "rotation" => {
            if en {
                "Rotation"
            } else {
                "Rotação"
            }
        }
        "scale" => {
            if en {
                "Scale"
            } else {
                "Escala"
            }
        }
        "name" => {
            if en {
                "Name"
            } else {
                "Nome"
            }
        }
        "id" => "ID",
        "src" | "url" => {
            if en {
                "File"
            } else {
                "Ficheiro"
            }
        }
        "half-size" => {
            if en {
                "Size"
            } else {
                "Tamanho"
            }
        }
        "radius" => {
            if en {
                "Radius"
            } else {
                "Raio"
            }
        }
        "height" => {
            if en {
                "Height"
            } else {
                "Altura"
            }
        }
        "width" => {
            if en {
                "Width"
            } else {
                "Largura"
            }
        }
        "base-color" | "color" => {
            if en {
                "Color"
            } else {
                "Cor"
            }
        }
        "intensity" => {
            if en {
                "Intensity"
            } else {
                "Intensidade"
            }
        }
        "model-url" => {
            if en {
                "Model"
            } else {
                "Modelo"
            }
        }
        "static" => {
            if en {
                "Static"
            } else {
                "Estático"
            }
        }
        "collider" => {
            if en {
                "Collider"
            } else {
                "Colisor"
            }
        }
        "rigidbody" => {
            if en {
                "Rigid body"
            } else {
                "Corpo rígido"
            }
        }
        "depth" => {
            if en {
                "Depth"
            } else {
                "Profundidade"
            }
        }
        "water" => {
            if en {
                "Water"
            } else {
                "Água"
            }
        }
        "count" => {
            if en {
                "Count"
            } else {
                "Quantidade"
            }
        }
        _ => key,
    }
}

/// A friendly name for an inspector fact row (`file`, `selector`, …).
pub fn fact_display(lang: Lang, key: &str) -> &str {
    let en = matches!(lang, Lang::En);
    match key {
        "file" => {
            if en {
                "File"
            } else {
                "Ficheiro"
            }
        }
        "selector" => {
            if en {
                "Path"
            } else {
                "Caminho"
            }
        }
        "children" => {
            if en {
                "Children"
            } else {
                "Filhos"
            }
        }
        "text" => {
            if en {
                "Text"
            } else {
                "Texto"
            }
        }
        _ => key,
    }
}

// --- Formatted strings (status bar, edit labels, world doc) -------------------------------

/// The `| game …` status-bar segment while the game process boots.
pub fn game_starting(lang: Lang) -> &'static str {
    match lang {
        Lang::En => " | game starting...",
        Lang::Pt => " | a iniciar o jogo…",
    }
}

/// The `| game running (viber X)` status-bar segment.
pub fn game_running(lang: Lang, version: &str) -> String {
    match lang {
        Lang::En => format!(" | game running (viber {version})"),
        Lang::Pt => format!(" | jogo a correr (viber {version})"),
    }
}

/// The status-bar suffix while the running game is frozen.
pub fn paused_suffix(lang: Lang) -> &'static str {
    match lang {
        Lang::En => " — paused",
        Lang::Pt => " — pausado",
    }
}

/// The `| N changes need a reload` status-bar segment.
pub fn needs_reload(lang: Lang, n: usize) -> String {
    match lang {
        Lang::En => format!(" | {n} change(s) need a reload (Ctrl+R)"),
        Lang::Pt if n == 1 => " | 1 alteração exige recarregar (Ctrl+R)".into(),
        Lang::Pt => format!(" | {n} alterações exigem recarregar (Ctrl+R)"),
    }
}

/// The undo history prefix shown in the status bar.
pub fn undo_prefix(lang: Lang, label: &str) -> String {
    match lang {
        Lang::En => format!("undo: {label}"),
        Lang::Pt => format!("desfazer: {label}"),
    }
}

/// The redo history prefix shown in the status bar.
pub fn redo_prefix(lang: Lang, label: &str) -> String {
    match lang {
        Lang::En => format!("redo: {label}"),
        Lang::Pt => format!("refazer: {label}"),
    }
}

pub fn nothing_to_undo(lang: Lang) -> &'static str {
    match lang {
        Lang::En => "nothing to undo",
        Lang::Pt => "nada para desfazer",
    }
}

pub fn nothing_to_redo(lang: Lang) -> &'static str {
    match lang {
        Lang::En => "nothing to redo",
        Lang::Pt => "nada para refazer",
    }
}

/// Successful save, with the written files.
pub fn saved(lang: Lang, files: &[String]) -> String {
    match lang {
        Lang::En => format!("saved: {}", files.join(", ")),
        Lang::Pt => format!("guardado: {}", files.join(", ")),
    }
}

pub fn save_nothing(lang: Lang) -> &'static str {
    match lang {
        Lang::En => "save: nothing to write",
        Lang::Pt => "guardar: nada para escrever",
    }
}

pub fn save_failed(lang: Lang, error: &anyhow::Error) -> String {
    match lang {
        Lang::En => format!("save failed: {error:#}"),
        Lang::Pt => format!("guardar falhou: {error:#}"),
    }
}

/// An undo/redo/apply call failed outright.
pub fn failed(lang: Lang, error: &anyhow::Error) -> String {
    match lang {
        Lang::En => format!("failed: {error:#}"),
        Lang::Pt => format!("falhou: {error:#}"),
    }
}

/// A queued edit failed to apply.
pub fn edit_failed(lang: Lang, label: &str, error: &anyhow::Error) -> String {
    match lang {
        Lang::En => format!("{label} failed: {error:#}"),
        Lang::Pt => format!("{label} falhou: {error:#}"),
    }
}

/// The duplicate action's undo label.
pub fn duplicate_label(lang: Lang, selector: &str) -> String {
    match lang {
        Lang::En => format!("duplicate {selector}"),
        Lang::Pt => format!("duplicar {selector}"),
    }
}

/// The delete action's undo label.
pub fn delete_label(lang: Lang, selector: &str) -> String {
    match lang {
        Lang::En => format!("delete {selector}"),
        Lang::Pt => format!("eliminar {selector}"),
    }
}

/// The inspector's `set` undo label: the friendly attribute names plus the selector.
pub fn set_label(lang: Lang, keys: &[String], selector: &str) -> String {
    let names: Vec<&str> = keys.iter().map(|key| attr_display(lang, key)).collect();
    format!("{} · {selector}", names.join(", "))
}

pub fn scatter_failed(lang: Lang, error: &anyhow::Error) -> String {
    match lang {
        Lang::En => format!("scatter failed: {error:#}"),
        Lang::Pt => format!("regeneração falhou: {error:#}"),
    }
}

pub fn terrain_failed(lang: Lang, error: &anyhow::Error) -> String {
    match lang {
        Lang::En => format!("terrain brush failed: {error:#}"),
        Lang::Pt => format!("pincel de terreno falhou: {error:#}"),
    }
}

pub fn console_malformed(lang: Lang, error: &serde_json::Error) -> String {
    match lang {
        Lang::En => format!("malformed viber.lua answer: {error}"),
        Lang::Pt => format!("resposta viber.lua malformada: {error}"),
    }
}

pub fn console_call_failed(lang: Lang, error: &dyn std::fmt::Display) -> String {
    match lang {
        Lang::En => format!("viber.lua call failed: {error}"),
        Lang::Pt => format!("chamada viber.lua falhou: {error}"),
    }
}

// --- World Doc formatted lines ------------------------------------------------------------

pub fn wd_source(lang: Lang, world: &str, version: &str) -> String {
    match lang {
        Lang::En => format!("source: {world} · viber {version}"),
        Lang::Pt => format!("origem: {world} · viber {version}"),
    }
}

pub fn wd_warning(lang: Lang, warning: &str) -> String {
    match lang {
        Lang::En => format!("warning: {warning}"),
        Lang::Pt => format!("aviso: {warning}"),
    }
}

pub fn wd_world_line(lang: Lang, size: &str, height: &str, layers: &str) -> String {
    match lang {
        Lang::En => format!("world {size} · max height {height} · layers {layers}"),
        Lang::Pt => format!("mundo {size} · altura máx {height} · camadas {layers}"),
    }
}

pub fn wd_heightmap_label(lang: Lang) -> &'static str {
    match lang {
        Lang::En => "heightmap: ",
        Lang::Pt => "mapa de alturas: ",
    }
}

pub fn wd_spec_label(lang: Lang) -> &'static str {
    match lang {
        Lang::En => "spec: ",
        Lang::Pt => "especificação: ",
    }
}

pub fn wd_spawn_groups(lang: Lang, n: usize) -> String {
    match lang {
        Lang::En => format!("Object groups ({n})"),
        Lang::Pt => format!("Grupos de objetos ({n})"),
    }
}

/// The leading kind word of a feature line (`pad {id} · …`).
pub fn wd_feature_kind(lang: Lang, kind: &str) -> &str {
    let en = matches!(lang, Lang::En);
    match kind {
        "pad" => {
            if en {
                "pad"
            } else {
                "plataforma"
            }
        }
        "lake" => {
            if en {
                "lake"
            } else {
                "lago"
            }
        }
        "river" => {
            if en {
                "river"
            } else {
                "rio"
            }
        }
        "network" => {
            if en {
                "network"
            } else {
                "rede"
            }
        }
        "road" => {
            if en {
                "road"
            } else {
                "estrada"
            }
        }
        "cliff" => {
            if en {
                "cliff"
            } else {
                "escarpa"
            }
        }
        "arch" => {
            if en {
                "arch"
            } else {
                "arco"
            }
        }
        "cave" => {
            if en {
                "cave"
            } else {
                "cova"
            }
        }
        "exclusion" => {
            if en {
                "exclusion"
            } else {
                "exclusão"
            }
        }
        _ => kind,
    }
}

pub fn wd_instances(lang: Lang, n: usize) -> String {
    match lang {
        Lang::En => format!("{n} instances"),
        Lang::Pt => format!("{n} instâncias"),
    }
}

pub fn wd_segments(lang: Lang, n: usize) -> String {
    match lang {
        Lang::En => format!("{n} segments"),
        Lang::Pt => format!("{n} segmentos"),
    }
}

// --- Live language switching ---------------------------------------------------------------

/// Marks a text node whose content is [`t`]-able; rewritten when [`Lang`] changes.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct LangLabel(pub StrKey);

/// Marks a tooltip whose content is [`t`]-able; rewritten when [`Lang`] changes.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct LangTooltip(pub StrKey);

/// Marks the toolbar's language toggle caption (shows the active language's code).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct LangButton;

/// Rewrites every marked static label and tooltip when the language changes. Newly added
/// resources read as changed, so an `--lang en` start also converges on the first frame.
pub fn sync_lang_labels(
    lang: Res<Lang>,
    mut labels: Query<(&LangLabel, &mut Text)>,
    mut tips: Query<(&LangTooltip, &mut Tooltip)>,
    mut lang_buttons: Query<(&LangButton, &mut Text), Without<LangLabel>>,
) {
    if !lang.is_changed() {
        return;
    }
    for (label, mut text) in &mut labels {
        let wanted = t(*lang, label.0);
        if text.0 != wanted {
            text.0 = wanted.to_string();
        }
    }
    for (tip, mut tooltip) in &mut tips {
        tooltip.0 = t(*lang, tip.0);
    }
    let code = lang.code();
    for (_, mut text) in &mut lang_buttons {
        if text.0 != code {
            text.0 = code.into();
        }
    }
}

/// The toolbar's language toggle: PT ⇄ EN.
pub fn toggle_lang(
    activate: On<Activate>,
    buttons: Query<(), With<LangButton>>,
    mut lang: ResMut<Lang>,
) {
    if !buttons.contains(activate.event_target()) {
        return;
    }
    *lang = match *lang {
        Lang::Pt => Lang::En,
        Lang::En => Lang::Pt,
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn static_labels_exist_in_both_languages() {
        for (key, pt, en) in [
            (StrKey::DockScene, "Objetos", "Objects"),
            (StrKey::DockInspector, "Inspetor", "Inspector"),
            (StrKey::TabWorldDoc, "Resumo do Mundo", "World Overview"),
            (StrKey::BtnSave, "Guardar", "Save"),
            (StrKey::WdFailed, "FALHOU", "FAILED"),
            (StrKey::AddAttr, "novo (chave=valor)", "new (key=value)"),
        ] {
            assert_eq!(t(Lang::Pt, key), pt);
            assert_eq!(t(Lang::En, key), en);
        }
    }

    #[test]
    fn every_key_is_non_empty_in_both_languages() {
        // Spot list of every declared key; `t`'s match has no wildcard arm, so the compiler
        // forces any new key to be translated — this only guards against empty strings.
        let keys = StrKey::ALL;
        assert!(keys.len() >= 70, "{} keys", keys.len());
        for &key in keys {
            assert!(!t(Lang::Pt, key).is_empty(), "PT empty: {key:?}");
            assert!(!t(Lang::En, key).is_empty(), "EN empty: {key:?}");
        }
    }

    #[test]
    fn tag_names_are_friendly_with_raw_fallback() {
        assert_eq!(tag_display(Lang::Pt, "Entity"), "Objeto");
        assert_eq!(tag_display(Lang::En, "Entity"), "Object");
        assert_eq!(
            tag_display(Lang::Pt, "PointLight"),
            "Luz",
            "case-insensitive"
        );
        assert_eq!(tag_display(Lang::En, "terrain"), "Terrain");
        assert_eq!(tag_display(Lang::Pt, "Sky"), "Céu");
        // Unknown tags keep their raw name.
        assert_eq!(tag_display(Lang::Pt, "MyWidget"), "MyWidget");
    }

    #[test]
    fn attr_names_are_friendly_with_raw_fallback() {
        assert_eq!(attr_display(Lang::Pt, "translation"), "Posição");
        assert_eq!(attr_display(Lang::En, "translation"), "Position");
        assert_eq!(attr_display(Lang::Pt, "half-size"), "Tamanho");
        assert_eq!(attr_display(Lang::Pt, "base-color"), "Cor");
        assert_eq!(attr_display(Lang::Pt, "custom-flag"), "custom-flag");
    }

    #[test]
    fn fact_names_are_friendly() {
        assert_eq!(fact_display(Lang::Pt, "file"), "Ficheiro");
        assert_eq!(fact_display(Lang::En, "selector"), "Path");
        assert_eq!(fact_display(Lang::Pt, "children"), "Filhos");
    }

    #[test]
    fn lang_parses_cli_values() {
        assert_eq!(Lang::parse("pt"), Some(Lang::Pt));
        assert_eq!(Lang::parse("PT"), Some(Lang::Pt));
        assert_eq!(Lang::parse("en"), Some(Lang::En));
        assert_eq!(Lang::parse("fr"), None);
        assert_eq!(Lang::default(), Lang::Pt);
    }

    #[test]
    fn needs_reload_handles_portuguese_plural() {
        assert_eq!(
            needs_reload(Lang::Pt, 1),
            " | 1 alteração exige recarregar (Ctrl+R)"
        );
        assert_eq!(
            needs_reload(Lang::Pt, 3),
            " | 3 alterações exigem recarregar (Ctrl+R)"
        );
        assert_eq!(
            needs_reload(Lang::En, 3),
            " | 3 change(s) need a reload (Ctrl+R)"
        );
    }

    #[test]
    fn set_label_uses_friendly_attr_names() {
        let keys = vec!["translation".to_string()];
        assert_eq!(
            set_label(Lang::Pt, &keys, "world.xml#0"),
            "Posição · world.xml#0"
        );
        assert_eq!(
            set_label(Lang::En, &keys, "world.xml#0"),
            "Position · world.xml#0"
        );
    }
}
