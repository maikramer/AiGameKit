//! A pasta do mundo como árvore navegável — o dock FileSystem do editor e o `viber scene files`.
//!
//! A árvore é só de leitura e tolerante a falhas: pastas ilegíveis são saltadas, nunca
//! rebentam o editor. Pastas de build/venv/caches ficam escondidas.

use std::path::Path;

use serde_json::{Value, json};

/// Tipo de ficheiro, pela extensão (e pelo nome, para os pontos de entrada do mundo).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileKind {
    Dir,
    /// `world.xml` — o ponto de entrada do mundo.
    World,
    /// Qualquer outro `.xml` (sub-cenas, includes, fixtures de QA).
    Scene,
    /// `.lua`
    Script,
    /// `.glb` / `.gltf`
    Gltf,
    /// `.png` `.jpg` `.jpeg` `.webp` `.ktx2` `.basis`
    Texture,
    /// `.ahgt` (heightmap, na pasta `<stem>.data/`)
    Heightmap,
    /// `.vinst` (instâncias de vegetação pré-geradas)
    Instances,
    /// `.ogg` `.mp3` `.wav` `.flac`
    Audio,
    Other,
}

impl FileKind {
    /// Classifica pelo nome do ficheiro (último componente do caminho).
    pub fn from_name(name: &str) -> Self {
        let lower = name.to_ascii_lowercase();
        if lower == "world.xml" {
            return FileKind::World;
        }
        let ext = lower
            .rsplit_once('.')
            .map(|(_, ext)| ext)
            .unwrap_or_default();
        match ext {
            "xml" => FileKind::Scene,
            "lua" => FileKind::Script,
            "glb" | "gltf" => FileKind::Gltf,
            "png" | "jpg" | "jpeg" | "webp" | "ktx2" | "basis" => FileKind::Texture,
            "ahgt" => FileKind::Heightmap,
            "vinst" => FileKind::Instances,
            "ogg" | "mp3" | "wav" | "flac" => FileKind::Audio,
            _ => FileKind::Other,
        }
    }

    /// Rótulo estável para JSON/CLI; o editor escolhe o ícone por ele.
    pub fn as_str(self) -> &'static str {
        match self {
            FileKind::Dir => "dir",
            FileKind::World => "world",
            FileKind::Scene => "scene",
            FileKind::Script => "script",
            FileKind::Gltf => "glb",
            FileKind::Texture => "texture",
            FileKind::Heightmap => "heightmap",
            FileKind::Instances => "instances",
            FileKind::Audio => "audio",
            FileKind::Other => "other",
        }
    }

    /// Abre numa tab de cena (o editor carrega-o como documento).
    pub fn is_scene(self) -> bool {
        matches!(self, FileKind::World | FileKind::Scene)
    }
}

/// Uma entrada da árvore: pasta com filhos, ou ficheiro (`children` vazio).
#[derive(Debug, Clone, PartialEq)]
pub struct FileEntry {
    pub name: String,
    pub kind: FileKind,
    pub children: Vec<FileEntry>,
}

/// Pastas que a árvore nunca mostra (build, caches, VCS, venvs).
const SKIP_DIRS: &[&str] = &["target", "node_modules", "__pycache__", ".git", ".venv"];

/// Varre a pasta do mundo: pastas primeiro (recursivas), depois ficheiros, por ordem
/// alfabética sem distinguir maiúsculas. `max_depth = 0` lista só o topo.
pub fn scan(root: &Path, max_depth: usize) -> Vec<FileEntry> {
    let Ok(read) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut dirs = Vec::new();
    let mut files = Vec::new();
    for entry in read.flatten() {
        let Ok(name) = entry.file_name().into_string() else {
            continue;
        };
        if name.starts_with('.') || SKIP_DIRS.contains(&name.to_ascii_lowercase().as_str()) {
            continue;
        }
        let Ok(meta) = entry.metadata() else {
            continue;
        };
        if meta.is_dir() {
            let children = if max_depth == 0 {
                Vec::new()
            } else {
                scan(&entry.path(), max_depth - 1)
            };
            dirs.push(FileEntry {
                name,
                kind: FileKind::Dir,
                children,
            });
        } else {
            let kind = FileKind::from_name(&name);
            files.push(FileEntry {
                name,
                kind,
                children: Vec::new(),
            });
        }
    }
    sort_entries(&mut dirs);
    sort_entries(&mut files);
    dirs.extend(files);
    dirs
}

fn sort_entries(entries: &mut [FileEntry]) {
    entries.sort_by_key(|entry| entry.name.to_lowercase());
}

/// JSON estável (CLI e editor): `[{ "name", "kind", "children" }, …]`.
pub fn to_json(entries: &[FileEntry]) -> Value {
    Value::Array(
        entries
            .iter()
            .map(|entry| {
                json!({
                    "name": entry.name,
                    "kind": entry.kind.as_str(),
                    "children": to_json(&entry.children),
                })
            })
            .collect(),
    )
}

/// Todos os caminhos (relativos à raiz, com `/`) de um dado tipo — os filtros do dock.
pub fn paths_of(entries: &[FileEntry], kind: FileKind, prefix: &str, out: &mut Vec<String>) {
    for entry in entries {
        let path = if prefix.is_empty() {
            entry.name.clone()
        } else {
            format!("{prefix}/{}", entry.name)
        };
        if entry.kind == FileKind::Dir {
            paths_of(&entry.children, kind, &path, out);
        } else if entry.kind == kind {
            out.push(path);
        }
    }
}
