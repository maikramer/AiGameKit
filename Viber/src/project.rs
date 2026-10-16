//! Projeto + cenas (estilo Unity/Godot).
//!
//! Um **projeto** é a pasta com o `config.yaml`. Uma **cena** é um ficheiro XML com raiz
//! `<world static="true">` (ou `<scene>`): um universo próprio — entities, terreno (ou
//! nenhum), navmesh, física. A cena principal é o `world.xml` (ou `project.main-scene`); as
//! outras vivem em `scenes/` (ou `project.scenes`) e o seu **id** é o caminho relativo a esse
//! diretório sem `.xml` (`interiors/chapel`). `main` é sempre a principal.
//!
//! ```yaml
//! project:
//!   main-scene: world.xml
//!   scenes: scenes
//!   autoload:                 # fragmentos persistentes (jogador, HUD) — spawnados 1×
//!     - world/autoload/player.xml
//! ```
//!
//! Os caminhos `/…` dos `<Include>`, o `config.yaml`, os scripts e os assets resolvem SEMPRE
//! contra a raiz do projeto ([`root_of`]) — também numa cena dentro de `scenes/`. Um mundo
//! fora do projeto (um `qa-*.xml` ao lado) corre como sempre correu: a raiz é a pasta dele.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use serde::Deserialize;

use crate::config::CONFIG_FILE;

/// Id reservado da cena principal.
pub const MAIN_SCENE: &str = "main";
/// Diretório de cenas por omissão.
pub const DEFAULT_SCENES_DIR: &str = "scenes";
/// Cena principal por omissão.
pub const DEFAULT_MAIN_SCENE: &str = "world.xml";
/// Quantos níveis acima da pasta de uma cena se procura o projeto.
const MAX_ROOT_SEARCH: usize = 4;

/// O bloco `project:` do `config.yaml` (todo opcional).
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
pub struct ProjectConfig {
    #[serde(default, rename = "main-scene")]
    pub main_scene: Option<PathBuf>,
    #[serde(default)]
    pub scenes: Option<PathBuf>,
    #[serde(default)]
    pub autoload: Vec<PathBuf>,
}

impl ProjectConfig {
    pub fn scenes_dir(&self) -> PathBuf {
        self.scenes
            .clone()
            .unwrap_or_else(|| PathBuf::from(DEFAULT_SCENES_DIR))
    }

    pub fn main_scene(&self) -> PathBuf {
        self.main_scene
            .clone()
            .unwrap_or_else(|| PathBuf::from(DEFAULT_MAIN_SCENE))
    }
}

/// Lê só o bloco `project:` de um `config.yaml` (sem validar o resto — a raiz tem de ser
/// encontrada antes de o config completo ser carregado).
fn read_project_block(config: &Path) -> Option<ProjectConfig> {
    let text = std::fs::read_to_string(config).ok()?;
    let value: serde_yaml::Value = serde_yaml::from_str(&text).ok()?;
    match value.get("project") {
        Some(block) => serde_yaml::from_value(block.clone()).ok(),
        None => Some(ProjectConfig::default()),
    }
}

/// A raiz do projeto de um ficheiro de mundo/cena: a própria pasta se tiver `config.yaml`;
/// senão o ancestral mais próximo (até 4 níveis) cujo `config.yaml` declara um diretório de
/// cenas que contém o ficheiro. Sem nenhum, a pasta do ficheiro (comportamento de sempre).
pub fn root_of(path: &Path) -> PathBuf {
    let abs = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let parent = abs
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    if parent.join(CONFIG_FILE).is_file() {
        return parent;
    }
    let mut current = parent.parent();
    for _ in 0..MAX_ROOT_SEARCH {
        let Some(dir) = current else {
            break;
        };
        let config = dir.join(CONFIG_FILE);
        if config.is_file() {
            if let Some(block) = read_project_block(&config) {
                let scenes = dir.join(block.scenes_dir());
                let scenes = scenes.canonicalize().unwrap_or(scenes);
                if abs.starts_with(&scenes) {
                    return dir.to_path_buf();
                }
            }
            // Um config.yaml que não reclama o ficheiro fecha a procura: é outro jogo.
            break;
        }
        current = dir.parent();
    }
    parent
}

/// Uma entrada de cena (`<SceneEntry>` ou `<ScenePortal>`, que também é entrada).
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct EntryInfo {
    pub name: String,
    /// `"entry"` ou `"portal"`.
    pub kind: &'static str,
}

/// Um portal declarado numa cena.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct PortalInfo {
    pub name: String,
    pub target: String,
    pub entry: Option<String>,
}

/// Resumo barato de uma cena (sem spawn): o que o lint, o editor e `viber scene scenes`
/// precisam para ligar portais e entradas.
#[derive(Debug, Clone, serde::Serialize)]
pub struct SceneInfo {
    pub id: String,
    /// Relativo à raiz do projeto, com `/`.
    pub path: String,
    pub title: Option<String>,
    pub main: bool,
    pub entries: Vec<EntryInfo>,
    pub portals: Vec<PortalInfo>,
    /// Erro de parse (a cena continua listada).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// O projeto: raiz, cena principal, diretório de cenas, autoloads.
#[derive(Debug, Clone)]
pub struct Project {
    pub root: PathBuf,
    pub config: ProjectConfig,
    /// `true` quando o `config.yaml` tem um bloco `project:` — só então há autoloads e
    /// `change_scene` (um mundo sem ele é um mundo de uma só cena, como sempre foi).
    pub declared: bool,
}

impl Project {
    /// O projeto da pasta `root` (que tem o `config.yaml`).
    pub fn at(root: &Path) -> Project {
        let root = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
        let config_path = root.join(CONFIG_FILE);
        let declared = std::fs::read_to_string(&config_path)
            .ok()
            .and_then(|text| serde_yaml::from_str::<serde_yaml::Value>(&text).ok())
            .is_some_and(|value| value.get("project").is_some());
        let config = read_project_block(&config_path).unwrap_or_default();
        Project {
            root,
            config,
            declared,
        }
    }

    /// O projeto a que pertence `path` (um ficheiro de cena, ou a pasta do projeto).
    pub fn discover(path: &Path) -> Project {
        if path.is_dir() {
            return Project::at(path);
        }
        Project::at(&root_of(path))
    }

    pub fn main_scene_path(&self) -> PathBuf {
        self.root.join(self.config.main_scene())
    }

    pub fn scenes_dir(&self) -> PathBuf {
        self.root.join(self.config.scenes_dir())
    }

    pub fn autoload_paths(&self) -> Vec<PathBuf> {
        self.config
            .autoload
            .iter()
            .map(|p| self.root.join(p))
            .collect()
    }

    /// O id de uma cena do projeto (`main` ou o caminho relativo a `scenes/` sem `.xml`), ou
    /// `None` se o ficheiro não é uma cena deste projeto.
    pub fn scene_id(&self, path: &Path) -> Option<String> {
        let abs = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
        let main = self.main_scene_path();
        let main = main.canonicalize().unwrap_or(main);
        if abs == main {
            return Some(MAIN_SCENE.to_string());
        }
        let scenes = self.scenes_dir();
        let scenes = scenes.canonicalize().unwrap_or(scenes);
        let rel = abs.strip_prefix(&scenes).ok()?;
        if rel.extension().and_then(|e| e.to_str()) != Some("xml") {
            return None;
        }
        Some(slash_path(&rel.with_extension("")))
    }

    /// O ficheiro de uma cena pelo id (`main`, `interiors/chapel`, ou um caminho relativo à
    /// raiz como `scenes/interiors/chapel.xml`). `None` se não existe.
    pub fn resolve_scene(&self, id: &str) -> Option<PathBuf> {
        let id = id.trim().trim_start_matches('/');
        if id.is_empty() {
            return None;
        }
        if id == MAIN_SCENE {
            let main = self.main_scene_path();
            return main.is_file().then_some(main);
        }
        let by_id = self.scenes_dir().join(format!("{id}.xml"));
        if by_id.is_file() {
            return Some(by_id);
        }
        let by_path = self.root.join(id);
        if by_path.is_file() && self.scene_id(&by_path).is_some() {
            return Some(by_path);
        }
        None
    }

    /// Todos os ficheiros de cena: a principal primeiro, depois `scenes/**.xml` por ordem.
    pub fn scene_files(&self) -> Vec<PathBuf> {
        let mut out = Vec::new();
        let main = self.main_scene_path();
        if main.is_file() {
            out.push(main);
        }
        let mut found = Vec::new();
        collect_xml(&self.scenes_dir(), &mut found);
        found.sort();
        out.extend(found);
        out
    }

    /// Resumo de todas as cenas do projeto.
    pub fn list_scenes(&self) -> Vec<SceneInfo> {
        self.scene_files()
            .iter()
            .filter_map(|path| self.scene_info(path))
            .collect()
    }

    /// Resumo de uma cena (entradas e portais, incluindo os dos `<Include>`).
    pub fn scene_info(&self, path: &Path) -> Option<SceneInfo> {
        let id = self.scene_id(path)?;
        let rel = path
            .strip_prefix(&self.root)
            .map(slash_path)
            .unwrap_or_else(|_| path.display().to_string());
        let mut info = SceneInfo {
            main: id == MAIN_SCENE,
            id,
            path: rel,
            title: None,
            entries: Vec::new(),
            portals: Vec::new(),
            error: None,
        };
        match crate::xml::include::load_world_in(path, &self.root) {
            Ok(loaded) => {
                info.title = loaded
                    .root_attrs
                    .iter()
                    .find(|(k, _)| k == "title")
                    .map(|(_, v)| v.clone());
                scan_entries(&loaded.nodes, &mut info);
            }
            Err(error) => info.error = Some(format!("{error:#}")),
        }
        Some(info)
    }

    /// Cria uma cena nova vazia (`template`) com o id dado. Falha se já existe.
    pub fn new_scene(&self, id: &str, template: SceneTemplate) -> Result<PathBuf> {
        let id = id.trim().trim_matches('/');
        if id.is_empty() || id == MAIN_SCENE || id.contains("..") {
            bail!("id de cena inválido: `{id}`");
        }
        let path = self.scenes_dir().join(format!("{id}.xml"));
        if path.exists() {
            bail!("a cena `{id}` já existe ({})", path.display());
        }
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).with_context(|| format!("criar {}", dir.display()))?;
        }
        let title = id.rsplit('/').next().unwrap_or(id);
        std::fs::write(&path, template.render(title))
            .with_context(|| format!("escrever {}", path.display()))?;
        Ok(path)
    }
}

/// Modelos de cena nova.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SceneTemplate {
    /// Só uma luz, uma entrada e o chão.
    Empty,
    /// Sala interior `width × depth` (m): chão, câmara fixa, luz interior, entrada e saída.
    Interior { width: f32, depth: f32 },
}

impl SceneTemplate {
    pub fn render(self, title: &str) -> String {
        match self {
            SceneTemplate::Empty => format!(
                r##"<world static="true" title="{title}">
  <DirectionalLight name="sun" euler="-50 30 0" illuminance="8000" shadows="true"/>
  <Cuboid name="floor" translation="0 -0.5 0" half-size="20 0.5 20" base-color="#6b6b6b" collider="auto" body="fixed"/>
  <SceneEntry name="start" translation="0 0.6 0"/>
</world>
"##
            ),
            SceneTemplate::Interior { width, depth } => {
                let (hw, hd) = (width * 0.5, depth * 0.5);
                format!(
                    r##"<world static="true" title="{title}">
  <InteriorScene at="0 0" size="{w} {d}" camera-distance="16" camera-pitch="58" camera-yaw="0"/>
  <Cuboid name="floor" translation="0 -0.25 0" half-size="{hw} 0.25 {hd}" base-color="#7a6a55" collider="auto" body="fixed"/>
  <PointLight name="lamp" translation="0 4 0" intensity="60000" range="25" color="#ffd9a0"/>
  <SceneEntry name="start" translation="0 0.6 {entry_z}"/>
  <ScenePortal name="exit" translation="0 0 {exit_z}" target="main" entry="@back" prompt="Sair" auto-radius="1.6"/>
</world>
"##,
                    w = width + 6.0,
                    d = depth + 6.0,
                    entry_z = -hd + 2.5,
                    exit_z = -hd + 0.5,
                )
            }
        }
    }
}

fn scan_entries(nodes: &[crate::xml::XmlNode], info: &mut SceneInfo) {
    for node in nodes {
        let tag = node.tag.to_ascii_lowercase();
        let name = node.attr("name").map(str::to_owned);
        match tag.as_str() {
            "sceneentry" => {
                if let Some(name) = name {
                    info.entries.push(EntryInfo {
                        name,
                        kind: "entry",
                    });
                }
            }
            "sceneportal" => {
                if let Some(name) = &name {
                    info.entries.push(EntryInfo {
                        name: name.clone(),
                        kind: "portal",
                    });
                }
                info.portals.push(PortalInfo {
                    name: name.unwrap_or_default(),
                    target: node.attr("target").unwrap_or_default().to_string(),
                    entry: node.attr("entry").map(str::to_owned),
                });
            }
            _ => {}
        }
        scan_entries(&node.children, info);
    }
}

fn collect_xml(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(read) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in read.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_xml(&path, out);
        } else if path.extension().and_then(|e| e.to_str()) == Some("xml") {
            out.push(path);
        }
    }
}

fn slash_path(path: &Path) -> String {
    path.components()
        .map(|c| c.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("/")
}

#[cfg(test)]
mod tests {
    use super::*;

    const CONFIG: &str = "assets:\n  roots: []\n  bgm_dir: bgm\n  sfx_dir: sfx\n  terrain_textures_dir: t\n\
                          game:\n  scripts_dir: scripts\nsave:\n  dir: /tmp/x\n";

    fn project(extra: &str) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join(CONFIG_FILE), format!("{CONFIG}{extra}")).unwrap();
        std::fs::write(
            dir.path().join("world.xml"),
            r#"<world static="true"><ScenePortal name="portal.house" target="house" entry="from-town"/></world>"#,
        )
        .unwrap();
        std::fs::create_dir_all(dir.path().join("world")).unwrap();
        std::fs::write(
            dir.path().join("world/frag.xml"),
            r#"<world><SceneEntry name="from-town" translation="0 0 2"/></world>"#,
        )
        .unwrap();
        std::fs::create_dir_all(dir.path().join("scenes/interiors")).unwrap();
        std::fs::write(
            dir.path().join("scenes/house.xml"),
            r#"<world static="true" title="Casa"><Include src="/world/frag.xml"/><ScenePortal name="exit" target="main" entry="@back"/></world>"#,
        )
        .unwrap();
        std::fs::write(
            dir.path().join("scenes/interiors/chapel.xml"),
            r#"<world static="true"/>"#,
        )
        .unwrap();
        dir
    }

    #[test]
    fn root_of_world_next_to_config_is_its_folder() {
        let dir = project("");
        let root = dir.path().canonicalize().unwrap();
        assert_eq!(root_of(&dir.path().join("world.xml")), root);
    }

    #[test]
    fn root_of_scene_in_scenes_dir_is_the_project() {
        let dir = project("");
        let root = dir.path().canonicalize().unwrap();
        assert_eq!(root_of(&dir.path().join("scenes/house.xml")), root);
        assert_eq!(
            root_of(&dir.path().join("scenes/interiors/chapel.xml")),
            root
        );
    }

    #[test]
    fn root_of_file_outside_scenes_dir_is_its_folder() {
        let dir = project("");
        let other = dir.path().join("world/frag.xml");
        assert_eq!(
            root_of(&other),
            dir.path().join("world").canonicalize().unwrap()
        );
    }

    #[test]
    fn custom_scenes_dir_is_honoured() {
        let dir = project("project:\n  scenes: levels\n");
        std::fs::create_dir_all(dir.path().join("levels")).unwrap();
        std::fs::write(dir.path().join("levels/a.xml"), "<world static=\"true\"/>").unwrap();
        let root = dir.path().canonicalize().unwrap();
        assert_eq!(root_of(&dir.path().join("levels/a.xml")), root);
        // `scenes/` deixou de ser o diretório de cenas.
        assert_ne!(root_of(&dir.path().join("scenes/house.xml")), root);
        let p = Project::at(dir.path());
        assert_eq!(
            p.scene_id(&dir.path().join("levels/a.xml")).as_deref(),
            Some("a")
        );
    }

    #[test]
    fn scene_ids_round_trip() {
        let dir = project("project:\n  autoload: [world/frag.xml]\n");
        let p = Project::discover(&dir.path().join("scenes/house.xml"));
        assert!(p.declared);
        assert_eq!(
            p.scene_id(&dir.path().join("world.xml")).as_deref(),
            Some("main")
        );
        assert_eq!(
            p.scene_id(&dir.path().join("scenes/interiors/chapel.xml"))
                .as_deref(),
            Some("interiors/chapel")
        );
        assert_eq!(p.scene_id(&dir.path().join("world/frag.xml")), None);
        assert!(p.resolve_scene("interiors/chapel").is_some());
        assert!(p.resolve_scene("scenes/house.xml").is_some());
        assert!(p.resolve_scene("main").is_some());
        assert!(p.resolve_scene("nope").is_none());
        assert!(p.resolve_scene("world/frag.xml").is_none(), "não é cena");
        assert_eq!(p.autoload_paths(), vec![p.root.join("world/frag.xml")]);
    }

    #[test]
    fn undeclared_project_has_no_autoload() {
        let dir = project("");
        let p = Project::at(dir.path());
        assert!(!p.declared);
        assert!(p.autoload_paths().is_empty());
    }

    #[test]
    fn list_scenes_sees_entries_through_includes() {
        let dir = project("");
        let p = Project::at(dir.path());
        let scenes = p.list_scenes();
        let ids: Vec<_> = scenes.iter().map(|s| s.id.as_str()).collect();
        assert_eq!(ids, ["main", "house", "interiors/chapel"]);
        let house = &scenes[1];
        assert_eq!(house.title.as_deref(), Some("Casa"));
        let names: Vec<_> = house.entries.iter().map(|e| e.name.as_str()).collect();
        assert_eq!(names, ["from-town", "exit"]);
        assert_eq!(scenes[0].portals[0].target, "house");
    }

    #[test]
    fn new_scene_writes_a_parseable_static_world() {
        let dir = project("");
        let p = Project::at(dir.path());
        let path = p
            .new_scene(
                "interiors/tavern",
                SceneTemplate::Interior {
                    width: 20.0,
                    depth: 16.0,
                },
            )
            .unwrap();
        assert!(
            p.new_scene("interiors/tavern", SceneTemplate::Empty)
                .is_err()
        );
        let loaded = crate::xml::include::load_world_in(&path, &p.root).unwrap();
        assert!(
            loaded
                .root_attrs
                .iter()
                .any(|(k, v)| k == "static" && v == "true")
        );
        let info = p.scene_info(&path).unwrap();
        assert_eq!(info.id, "interiors/tavern");
        assert_eq!(info.portals[0].target, "main");
        assert!(p.new_scene("main", SceneTemplate::Empty).is_err());
        assert!(p.new_scene("../escape", SceneTemplate::Empty).is_err());
    }
}
