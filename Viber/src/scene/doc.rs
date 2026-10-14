//! O documento editável de um mundo: o `world.xml` e cada ficheiro que ele inclui
//! (`<Include>`), cada um guardado como o seu próprio [`XmlDocument`] — gravar escreve
//! exatamente o ficheiro de onde o nó veio, sem achatar includes.
//!
//! Os nós endereçam-se por [`NodeRef`] (ficheiro + caminho de índices) e, para agentes,
//! por **seletores** em texto:
//!
//! * `world/village.xml#3/0` — ficheiro relativo à pasta do mundo + caminho de índices
//!   (`#` sozinho = elemento raiz desse ficheiro; ficheiro omisso = o mundo: `#2/1`);
//! * `name=well` / `id=enemy-arrow` — o único nó com esse atributo (erro se houver 0 ou >1).
//!
//! [`SceneDoc::selector`] devolve sempre a forma canónica por caminho.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, anyhow, bail};

use crate::xml::emit::emit_document;
use crate::xml::include::{MAX_INCLUDE_DEPTH, resolve_include_path};
use crate::xml::{XmlDocument, XmlNode, parse_file};

/// Índice de um ficheiro em [`SceneDoc::files`]; `0` é o próprio mundo.
pub type FileId = usize;

/// Um nó de um ficheiro: caminho de índices de filhos a partir do elemento raiz.
/// Caminho vazio = o elemento raiz (`<world>`) desse ficheiro.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Default, PartialOrd, Ord)]
pub struct NodeRef {
    pub file: FileId,
    pub path: Vec<usize>,
}

impl NodeRef {
    pub fn new(file: FileId, path: Vec<usize>) -> Self {
        Self { file, path }
    }

    /// O elemento raiz de um ficheiro.
    pub fn root(file: FileId) -> Self {
        Self {
            file,
            path: Vec::new(),
        }
    }

    pub fn is_root(&self) -> bool {
        self.path.is_empty()
    }

    /// O pai (o raiz não tem).
    pub fn parent(&self) -> Option<NodeRef> {
        let (_, parent) = self.path.split_last()?;
        Some(NodeRef::new(self.file, parent.to_vec()))
    }

    /// O filho `index` deste nó.
    pub fn child(&self, index: usize) -> NodeRef {
        let mut path = self.path.clone();
        path.push(index);
        NodeRef::new(self.file, path)
    }
}

/// Um ficheiro XML carregado.
#[derive(Debug, Clone)]
pub struct SceneFile {
    /// Caminho canónico absoluto.
    pub path: PathBuf,
    pub doc: XmlDocument,
    /// Alterações por gravar.
    pub dirty: bool,
    /// Caminho do nó `<Include>` → o ficheiro que ele carrega.
    pub includes: Vec<(Vec<usize>, FileId)>,
}

/// O mundo em edição.
#[derive(Debug, Clone, bevy::prelude::Resource)]
pub struct SceneDoc {
    pub files: Vec<SceneFile>,
    /// Pasta do mundo (base dos seletores e dos includes `/…`).
    pub root_dir: PathBuf,
    /// Sobe a cada alteração (painéis/sync reconstroem quando muda).
    pub revision: u64,
    /// O heightmap do `<Terrain heightmap>`, carregado pela primeira operação de terreno
    /// (`terrain_brush`/`terrain_patch`) — dezenas de MB, por isso nunca à cabeça.
    pub terrain: Option<super::terrain::TerrainDoc>,
}

impl SceneDoc {
    /// Carrega `world` e, recursivamente, os ficheiros incluídos. Um include partido fica como
    /// nó simples (com aviso) para o resto do mundo continuar editável.
    pub fn load(world: &Path) -> Result<Self> {
        let world = world
            .canonicalize()
            .with_context(|| format!("world file not found: {}", world.display()))?;
        let root_dir = world.parent().map(Path::to_path_buf).unwrap_or_default();
        let mut doc = SceneDoc {
            files: Vec::new(),
            root_dir,
            revision: 0,
            terrain: None,
        };
        let root_dir = doc.root_dir.clone();
        doc.load_file(&world, &root_dir, 0)?;
        Ok(doc)
    }

    fn load_file(&mut self, path: &Path, root_dir: &Path, depth: usize) -> Result<FileId> {
        let parsed = parse_file(path)?;
        let id = self.files.len();
        self.files.push(SceneFile {
            path: path.to_path_buf(),
            doc: parsed,
            dirty: false,
            includes: Vec::new(),
        });
        if depth >= MAX_INCLUDE_DEPTH {
            return Ok(id);
        }
        let dir = path.parent().map(Path::to_path_buf).unwrap_or_default();
        let mut found = Vec::new();
        collect_includes(&self.files[id].doc.children, &mut Vec::new(), &mut found);
        for (node_path, src) in found {
            let Ok(resolved) = resolve_include_path(&src, &dir, root_dir).canonicalize() else {
                bevy::log::warn!("include not found: {src} (from {})", path.display());
                continue;
            };
            // Já carregado (fragmento partilhado ou ciclo): liga, não recarrega.
            if let Some(existing) = self.files.iter().position(|file| file.path == resolved) {
                self.files[id].includes.push((node_path, existing));
                continue;
            }
            match self.load_file(&resolved, root_dir, depth + 1) {
                Ok(child) => self.files[id].includes.push((node_path, child)),
                Err(error) => bevy::log::warn!("include {src}: {error:#}"),
            }
        }
        Ok(id)
    }

    pub fn world_path(&self) -> &Path {
        &self.files[0].path
    }

    /// Caminho de um ficheiro relativo à pasta do mundo, com `/` (a forma dos seletores).
    pub fn relative_path(&self, file: FileId) -> String {
        let path = &self.files[file].path;
        path.strip_prefix(&self.root_dir)
            .unwrap_or(path)
            .components()
            .map(|c| c.as_os_str().to_string_lossy())
            .collect::<Vec<_>>()
            .join("/")
    }

    /// O ficheiro com este caminho relativo (ou absoluto).
    pub fn file_by_path(&self, rel: &str) -> Option<FileId> {
        let rel = rel.trim().trim_start_matches("./");
        (0..self.files.len()).find(|&file| {
            self.relative_path(file) == rel || self.files[file].path == Path::new(rel)
        })
    }

    /// O nó de onde veio uma entidade (`SceneSource`) — `None` se o ficheiro não faz parte
    /// do documento ou o caminho já não existe.
    pub fn node_ref_for(&self, origin: &crate::xml::NodeOrigin) -> Option<NodeRef> {
        let file = self
            .files
            .iter()
            .position(|file| *file.path == *origin.file)?;
        let node = NodeRef::new(file, origin.path.iter().map(|&i| i as usize).collect());
        (node.is_root() || self.node(&node).is_some()).then_some(node)
    }

    /// A origem (como a carimba o `load_world`) de um nó — o inverso de [`Self::node_ref_for`].
    pub fn origin_of(&self, node: &NodeRef) -> crate::xml::NodeOrigin {
        crate::xml::NodeOrigin {
            file: std::sync::Arc::from(self.files[node.file].path.as_path()),
            path: node.path.iter().map(|&i| i as u32).collect(),
        }
    }

    /// Seletor canónico: `ficheiro#i/j/k`.
    pub fn selector(&self, node: &NodeRef) -> String {
        let path = node
            .path
            .iter()
            .map(usize::to_string)
            .collect::<Vec<_>>()
            .join("/");
        format!("{}#{path}", self.relative_path(node.file))
    }

    /// Resolve um seletor (ver o doc do módulo) num nó existente.
    pub fn resolve(&self, selector: &str) -> Result<NodeRef> {
        let selector = selector.trim();
        if let Some((file, path)) = selector.split_once('#') {
            let file = if file.is_empty() {
                0
            } else {
                self.file_by_path(file).ok_or_else(|| {
                    anyhow!("seletor `{selector}`: ficheiro `{file}` não faz parte do mundo")
                })?
            };
            let path = if path.is_empty() {
                Vec::new()
            } else {
                path.split('/')
                    .map(|part| {
                        part.parse::<usize>().with_context(|| {
                            format!("seletor `{selector}`: `{part}` não é um índice")
                        })
                    })
                    .collect::<Result<Vec<_>>>()?
            };
            let node = NodeRef::new(file, path);
            if !node.is_root() && self.node(&node).is_none() {
                bail!("seletor `{selector}`: nó inexistente");
            }
            return Ok(node);
        }
        if let Some((key, value)) = selector.split_once('=') {
            let found = self.find_by_attr(key.trim(), value.trim());
            return match found.as_slice() {
                [one] => Ok(one.clone()),
                [] => bail!("seletor `{selector}`: nenhum nó com {key}=\"{value}\""),
                many => bail!(
                    "seletor `{selector}`: ambíguo ({} nós) — usa um caminho: {}",
                    many.len(),
                    many.iter()
                        .take(5)
                        .map(|n| self.selector(n))
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            };
        }
        bail!("seletor `{selector}` inválido: usa `ficheiro#i/j` ou `atributo=valor`")
    }

    /// O primeiro nó (pré-ordem, todos os ficheiros) que satisfaz `pred`.
    pub fn walk_find(&self, mut pred: impl FnMut(&XmlNode) -> bool) -> Option<NodeRef> {
        let mut found = None;
        self.walk(|node, xml| {
            if found.is_none() && pred(xml) {
                found = Some(node.clone());
            }
        });
        found
    }

    /// Todos os nós (em todos os ficheiros) cujo atributo `key` vale `value`.
    pub fn find_by_attr(&self, key: &str, value: &str) -> Vec<NodeRef> {
        let mut out = Vec::new();
        self.walk(|node_ref, node| {
            if node.attr(key) == Some(value) {
                out.push(node_ref.clone());
            }
        });
        out
    }

    /// Visita todos os nós (não os raiz) de todos os ficheiros, em pré-ordem.
    pub fn walk(&self, mut visit: impl FnMut(&NodeRef, &XmlNode)) {
        fn rec(nodes: &[XmlNode], at: &mut NodeRef, visit: &mut impl FnMut(&NodeRef, &XmlNode)) {
            for (index, node) in nodes.iter().enumerate() {
                at.path.push(index);
                visit(at, node);
                rec(&node.children, at, visit);
                at.path.pop();
            }
        }
        for file in 0..self.files.len() {
            let mut at = NodeRef::root(file);
            rec(&self.files[file].doc.children, &mut at, &mut visit);
        }
    }

    pub fn node(&self, node: &NodeRef) -> Option<&XmlNode> {
        node_at(&self.files.get(node.file)?.doc.children, &node.path)
    }

    pub fn node_mut(&mut self, node: &NodeRef) -> Option<&mut XmlNode> {
        node_at_mut(&mut self.files.get_mut(node.file)?.doc.children, &node.path)
    }

    /// Os filhos de um nó (do raiz: os filhos do elemento raiz do ficheiro).
    pub fn children(&self, node: &NodeRef) -> Option<&Vec<XmlNode>> {
        if node.is_root() {
            return self.files.get(node.file).map(|file| &file.doc.children);
        }
        self.node(node).map(|node| &node.children)
    }

    pub fn children_mut(&mut self, node: &NodeRef) -> Option<&mut Vec<XmlNode>> {
        if node.is_root() {
            return self
                .files
                .get_mut(node.file)
                .map(|file| &mut file.doc.children);
        }
        self.node_mut(node).map(|node| &mut node.children)
    }

    /// Atributo de um nó ou, no raiz, do elemento raiz do ficheiro.
    pub fn attr(&self, node: &NodeRef, name: &str) -> Option<&str> {
        if node.is_root() {
            let file = self.files.get(node.file)?;
            return file
                .doc
                .root_attrs
                .iter()
                .find(|(k, _)| k == name)
                .map(|(_, v)| v.as_str());
        }
        self.node(node)?.attr(name)
    }

    /// O ficheiro que um nó `<Include>` carrega.
    pub fn include_target(&self, node: &NodeRef) -> Option<FileId> {
        self.files
            .get(node.file)?
            .includes
            .iter()
            .find(|(path, _)| *path == node.path)
            .map(|(_, file)| *file)
    }

    /// Marca o ficheiro como alterado e sobe a revisão.
    pub fn touch(&mut self, file: FileId) {
        if let Some(file) = self.files.get_mut(file) {
            file.dirty = true;
        }
        self.revision += 1;
    }

    /// Reconstrói o mapa de `<Include>` de um ficheiro depois de inserções/remoções (os
    /// caminhos dos irmãos mudam). Includes novos não são carregados aqui.
    pub fn reindex_includes(&mut self, file: FileId) {
        let Some(scene_file) = self.files.get(file) else {
            return;
        };
        let mut found = Vec::new();
        collect_includes(&scene_file.doc.children, &mut Vec::new(), &mut found);
        let dir = scene_file
            .path
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_default();
        let mut includes = Vec::new();
        for (node_path, src) in found {
            let Ok(resolved) = resolve_include_path(&src, &dir, &self.root_dir).canonicalize()
            else {
                continue;
            };
            if let Some(target) = self.files.iter().position(|f| f.path == resolved) {
                includes.push((node_path, target));
            }
        }
        self.files[file].includes = includes;
    }

    pub fn is_dirty(&self) -> bool {
        self.files.iter().any(|file| file.dirty) || self.terrain.as_ref().is_some_and(|t| t.dirty)
    }

    /// Ficheiros com alterações por gravar (relativos ao mundo), heightmap incluído.
    pub fn dirty_paths(&self) -> Vec<String> {
        let mut out: Vec<String> = (0..self.files.len())
            .filter(|&file| self.files[file].dirty)
            .map(|file| self.relative_path(file))
            .collect();
        let mut terrain_paths = |dirty: bool, path: &std::path::Path| {
            if !dirty {
                return;
            }
            let shown = path
                .strip_prefix(&self.root_dir)
                .unwrap_or(path)
                .to_string_lossy()
                .replace('\\', "/");
            out.push(shown);
        };
        if let Some(terrain) = self.terrain.as_ref() {
            terrain_paths(terrain.dirty, &terrain.path);
            if terrain.paint_dirty {
                terrain_paths(true, &crate::terrain::paint::paint_path(&terrain.path));
            }
        }
        out
    }

    /// O heightmap do mundo (carrega-o na primeira chamada): o `heightmap` do primeiro
    /// `<Terrain>` que o declara, relativo à pasta do mundo.
    pub fn terrain_mut(&mut self) -> Result<&mut super::terrain::TerrainDoc> {
        if self.terrain.is_none() {
            let mut found = None;
            self.walk(|_, node| {
                if found.is_none()
                    && node.tag.eq_ignore_ascii_case("terrain")
                    && let Some(map) = node.attr("heightmap")
                {
                    let num = |key: &str| node.attr(key).and_then(|v| v.trim().parse::<f32>().ok());
                    found = Some((
                        map.trim().trim_start_matches('/').to_owned(),
                        num("world-size"),
                        num("max-height"),
                    ));
                }
            });
            let (map, world_size, max_height) =
                found.ok_or_else(|| anyhow!("terreno: nenhum <Terrain heightmap=…> no mundo (só heightmaps pré-gerados são editáveis)"))?;
            let path = self.root_dir.join(&map);
            if !map.ends_with(".ahgt") {
                bail!(
                    "terreno: `{map}` não é um .ahgt — só o heightmap pré-gerado (`viber bake`) é editável"
                );
            }
            self.terrain = Some(super::terrain::TerrainDoc::load(
                &path, world_size, max_height,
            )?);
        }
        Ok(self.terrain.as_mut().expect("carregado acima"))
    }

    /// Escreve de volta todos os ficheiros alterados; devolve quantos.
    pub fn save(&mut self) -> Result<usize> {
        let mut written = 0;
        for file in self.files.iter_mut().filter(|file| file.dirty) {
            if let Some(dir) = file.path.parent() {
                std::fs::create_dir_all(dir)
                    .with_context(|| format!("creating {}", dir.display()))?;
            }
            std::fs::write(&file.path, emit_document(&file.doc))
                .with_context(|| format!("writing {}", file.path.display()))?;
            file.dirty = false;
            written += 1;
        }
        if let Some(terrain) = self.terrain.as_mut().filter(|t| t.dirty || t.paint_dirty) {
            terrain.save()?;
            written += 1;
        }
        if written > 0 {
            self.revision += 1;
        }
        Ok(written)
    }
}

/// Rótulo curto de um nó (árvore de cena, listagens): `Tag "nome"`, caindo para `id`/`src`/`url`.
pub fn node_label(node: &XmlNode) -> String {
    let hint = ["name", "id", "src", "url", "model-url", "gltf"]
        .iter()
        .find_map(|key| node.attr(key));
    match hint {
        // Caminhos mostram só o nome do ficheiro.
        Some(hint) => format!(
            "{} \"{}\"",
            node.tag,
            hint.rsplit('/').next().unwrap_or(hint)
        ),
        None => node.tag.clone(),
    }
}

fn collect_includes(
    nodes: &[XmlNode],
    prefix: &mut Vec<usize>,
    out: &mut Vec<(Vec<usize>, String)>,
) {
    for (index, node) in nodes.iter().enumerate() {
        prefix.push(index);
        if node.tag.eq_ignore_ascii_case("include") {
            if let Some(src) = node
                .attr("src")
                .map(str::trim)
                .filter(|src| !src.is_empty())
            {
                out.push((prefix.clone(), src.to_owned()));
            }
        } else {
            collect_includes(&node.children, prefix, out);
        }
        prefix.pop();
    }
}

fn node_at<'a>(nodes: &'a [XmlNode], path: &[usize]) -> Option<&'a XmlNode> {
    let (first, rest) = path.split_first()?;
    let node = nodes.get(*first)?;
    if rest.is_empty() {
        Some(node)
    } else {
        node_at(&node.children, rest)
    }
}

fn node_at_mut<'a>(nodes: &'a mut [XmlNode], path: &[usize]) -> Option<&'a mut XmlNode> {
    let (first, rest) = path.split_first()?;
    let node = nodes.get_mut(*first)?;
    if rest.is_empty() {
        Some(node)
    } else {
        node_at_mut(&mut node.children, rest)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// Mundo de teste: um Group com 2 filhos, um Include para `world/village.xml`, um Sky.
    pub(crate) fn fixture(dir: &Path) -> PathBuf {
        std::fs::create_dir_all(dir.join("world")).unwrap();
        std::fs::write(
            dir.join("world.xml"),
            r#"<world sky="day">
  <Entity name="hero" translation="0 1 0" />
  <Group name="props">
    <Entity name="crate" />
    <Entity name="barrel" />
  </Group>
  <Include src="/world/village.xml" />
</world>"#,
        )
        .unwrap();
        std::fs::write(
            dir.join("world/village.xml"),
            r#"<world><Entity name="well" translation="4 0 2" /><Entity name="crate" /></world>"#,
        )
        .unwrap();
        dir.join("world.xml")
    }

    #[test]
    fn loads_world_and_includes_as_separate_files() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = SceneDoc::load(&fixture(tmp.path())).unwrap();
        assert_eq!(doc.files.len(), 2);
        assert_eq!(doc.relative_path(1), "world/village.xml");
        let include = NodeRef::new(0, vec![2]);
        assert_eq!(doc.include_target(&include), Some(1));
    }

    #[test]
    fn path_selectors_roundtrip() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = SceneDoc::load(&fixture(tmp.path())).unwrap();
        let barrel = doc.resolve("#1/1").unwrap();
        assert_eq!(doc.node(&barrel).unwrap().attr("name"), Some("barrel"));
        assert_eq!(doc.selector(&barrel), "world.xml#1/1");
        assert_eq!(doc.resolve("world.xml#1/1").unwrap(), barrel);
        let well = doc.resolve("world/village.xml#0").unwrap();
        assert_eq!(doc.node(&well).unwrap().attr("name"), Some("well"));
        assert_eq!(doc.resolve("world/village.xml#").unwrap(), NodeRef::root(1));
    }

    #[test]
    fn attribute_selectors_must_be_unique() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = SceneDoc::load(&fixture(tmp.path())).unwrap();
        assert_eq!(doc.resolve("name=well").unwrap(), NodeRef::new(1, vec![0]));
        let ambiguous = doc.resolve("name=crate").unwrap_err().to_string();
        assert!(ambiguous.contains("ambíguo (2 nós)"), "{ambiguous}");
        assert!(doc.resolve("name=ghost").is_err());
    }

    #[test]
    fn bad_selectors_explain_themselves() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = SceneDoc::load(&fixture(tmp.path())).unwrap();
        for bad in ["#9", "#1/x", "nope.xml#0", "hero"] {
            assert!(doc.resolve(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn root_attrs_are_addressable() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = SceneDoc::load(&fixture(tmp.path())).unwrap();
        assert_eq!(doc.attr(&NodeRef::root(0), "sky"), Some("day"));
        assert_eq!(doc.children(&NodeRef::root(0)).unwrap().len(), 3);
    }

    #[test]
    fn missing_include_keeps_world_editable() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(
            tmp.path().join("world.xml"),
            r#"<world><Include src="gone.xml" /><Entity name="a" /></world>"#,
        )
        .unwrap();
        let doc = SceneDoc::load(&tmp.path().join("world.xml")).unwrap();
        assert_eq!(doc.files.len(), 1);
        assert!(doc.include_target(&NodeRef::new(0, vec![0])).is_none());
    }

    #[test]
    fn labels_prefer_name_and_shorten_paths() {
        let node = XmlNode::new("Entity").with_attr("name", "hero");
        assert_eq!(node_label(&node), "Entity \"hero\"");
        let node = XmlNode::new("Include").with_attr("src", "/world/village.xml");
        assert_eq!(node_label(&node), "Include \"village.xml\"");
        assert_eq!(node_label(&XmlNode::new("Sky")), "Sky");
    }
}
