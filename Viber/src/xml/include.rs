//! Recursive `<Include src="…">` expansion for Viber world files.
//!
//! Semantics (inherited from the original engine, tightened where it was
//! sloppy): depth limit of 8, cycle detection on canonical paths, fragments
//! unwrapped (a `<world>`/`<scene>` root contributes its children, any other
//! root element is used as-is), paths starting with `/` resolve against the
//! root file's directory, other paths against the including file's directory.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};

use super::XmlNode;

/// Maximum `<Include>` nesting depth (the root file does not count, so 8
/// nested includes are allowed — same semantics as the original engine).
pub const MAX_INCLUDE_DEPTH: usize = 8;

/// A fully expanded world: root attributes plus include-free children.
#[derive(Debug, Clone)]
pub struct LoadedWorld {
    pub root_attrs: Vec<(String, String)>,
    pub nodes: Vec<XmlNode>,
}

/// Load a world file and expand every `<Include>`.
pub fn load_world(path: &Path) -> Result<LoadedWorld> {
    let path = path
        .canonicalize()
        .with_context(|| format!("world file not found: {}", path.display()))?;
    let root_dir = path
        .parent()
        .expect("canonicalized file path has a parent")
        .to_path_buf();
    let doc = super::parse_file(&path)?;
    if doc.root_tag != "world" && doc.root_tag != "scene" {
        bail!(
            "{}: root element must be <world> (got <{}>)",
            path.display(),
            doc.root_tag
        );
    }
    let mut stack = vec![path];
    let nodes = expand(doc.children, &root_dir, &root_dir, &mut stack)?;
    Ok(LoadedWorld {
        root_attrs: doc.root_attrs,
        nodes,
    })
}

fn expand(
    nodes: Vec<XmlNode>,
    dir: &Path,
    root_dir: &Path,
    stack: &mut Vec<PathBuf>,
) -> Result<Vec<XmlNode>> {
    // stack[0] is the root file; only nested includes count toward the limit.
    if stack.len() > MAX_INCLUDE_DEPTH + 1 {
        bail!(
            "include depth exceeds {MAX_INCLUDE_DEPTH}: {}",
            chain(stack)
        );
    }
    let mut out = Vec::with_capacity(nodes.len());
    for node in nodes {
        if !node.tag.eq_ignore_ascii_case("include") {
            let inner_dir = dir.to_path_buf();
            out.push(recurse_into_children(node, &inner_dir, root_dir, stack)?);
            continue;
        }
        let src = node
            .attr("src")
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .context("<Include> requires a src attribute")?;
        let resolved = if src.starts_with('/') {
            root_dir.join(src.trim_start_matches('/'))
        } else {
            dir.join(src)
        };
        let resolved = resolved
            .canonicalize()
            .with_context(|| format!("include not found: {src} (from {})", chain(stack)))?;
        if stack.contains(&resolved) {
            bail!("include cycle detected: {} → {}", chain(stack), src);
        }
        stack.push(resolved.clone());
        let doc = super::parse_file(&resolved)?;
        let file_dir = resolved
            .parent()
            .expect("canonicalized file path has a parent")
            .to_path_buf();
        // Unwrap: a <world>/<scene> root contributes its children; any other
        // single-root fragment is used as-is.
        let fragment: Vec<XmlNode> = if doc.root_tag == "world" || doc.root_tag == "scene" {
            doc.children
        } else {
            vec![XmlNode {
                tag: doc.root_tag,
                attrs: doc.root_attrs,
                children: doc.children,
            }]
        };
        out.extend(expand(fragment, &file_dir, root_dir, stack)?);
        stack.pop();
    }
    Ok(out)
}

/// Recurse into an element's children so includes nested anywhere expand.
fn recurse_into_children(
    mut node: XmlNode,
    dir: &Path,
    root_dir: &Path,
    stack: &mut Vec<PathBuf>,
) -> Result<XmlNode> {
    node.children = expand(node.children, dir, root_dir, stack)?;
    Ok(node)
}

fn chain(stack: &[PathBuf]) -> String {
    stack
        .iter()
        .map(|p| {
            p.file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_else(|| p.display().to_string())
        })
        .collect::<Vec<_>>()
        .join(" → ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn write(path: &Path, content: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, content).unwrap();
    }

    fn load_at(dir: &Path, rel: &str) -> Result<LoadedWorld> {
        load_world(&dir.join(rel))
    }

    #[test]
    fn test_include_expands_children() {
        let dir = tempfile::tempdir().unwrap();
        write(
            &dir.path().join("child.xml"),
            "<Entity name=\"from-child\" />",
        );
        write(
            &dir.path().join("main.xml"),
            "<world><Entity name=\"root\" /><Include src=\"child.xml\" /></world>",
        );
        let world = load_at(dir.path(), "main.xml").unwrap();
        assert_eq!(world.nodes.len(), 2);
        assert_eq!(world.nodes[1].attr("name"), Some("from-child"));
    }

    #[test]
    fn test_include_accepts_lowercase_tag() {
        let dir = tempfile::tempdir().unwrap();
        write(&dir.path().join("c.xml"), "<world><Entity /></world>");
        write(
            &dir.path().join("m.xml"),
            "<world><include src=\"c.xml\" /></world>",
        );
        let world = load_at(dir.path(), "m.xml").unwrap();
        assert_eq!(world.nodes.len(), 1);
    }

    #[test]
    fn test_include_unwraps_world_root_and_bare_fragments() {
        let dir = tempfile::tempdir().unwrap();
        write(
            &dir.path().join("wrapped.xml"),
            "<world><Entity name=\"a\" /><Entity name=\"b\" /></world>",
        );
        write(&dir.path().join("bare.xml"), "<Group name=\"bare-root\" />");
        write(
            &dir.path().join("m.xml"),
            "<world><Include src=\"wrapped.xml\" /><Include src=\"bare.xml\" /></world>",
        );
        let world = load_at(dir.path(), "m.xml").unwrap();
        assert_eq!(world.nodes.len(), 3);
        // Bare-fragment roots come through the document root, which the
        // parser lowercases; recipe matching is case-insensitive anyway.
        assert!(world.nodes[2].tag.eq_ignore_ascii_case("Group"));
    }

    #[test]
    fn test_include_resolves_relative_to_including_file() {
        let dir = tempfile::tempdir().unwrap();
        write(
            &dir.path().join("sub/leaf.xml"),
            "<world><Entity name=\"leaf\" /></world>",
        );
        write(
            &dir.path().join("sub/mid.xml"),
            "<world><Include src=\"leaf.xml\" /></world>",
        );
        write(
            &dir.path().join("m.xml"),
            "<world><Include src=\"sub/mid.xml\" /></world>",
        );
        let world = load_at(dir.path(), "m.xml").unwrap();
        assert_eq!(world.nodes[0].attr("name"), Some("leaf"));
    }

    #[test]
    fn test_include_absolute_path_resolves_against_root_dir() {
        let dir = tempfile::tempdir().unwrap();
        // "/leaf.xml" resolves against the ROOT file's dir (sub/), not CWD.
        write(
            &dir.path().join("sub/assets/leaf.xml"),
            "<Entity name=\"abs\" />",
        );
        write(
            &dir.path().join("sub/m.xml"),
            "<world><Include src=\"/assets/leaf.xml\" /></world>",
        );
        let world = load_at(dir.path(), "sub/m.xml").unwrap();
        assert_eq!(world.nodes[0].attr("name"), Some("abs"));
    }

    #[test]
    fn test_include_cycle_fails_fast() {
        let dir = tempfile::tempdir().unwrap();
        write(
            &dir.path().join("a.xml"),
            "<world><Include src=\"b.xml\" /></world>",
        );
        write(
            &dir.path().join("b.xml"),
            "<world><Include src=\"a.xml\" /></world>",
        );
        write(
            &dir.path().join("m.xml"),
            "<world><Include src=\"a.xml\" /></world>",
        );
        let err = load_at(dir.path(), "m.xml").unwrap_err();
        assert!(err.to_string().contains("cycle detected"), "{err}");
    }

    #[test]
    fn test_include_depth_limit() {
        let dir = tempfile::tempdir().unwrap();
        for i in 0..=MAX_INCLUDE_DEPTH + 1 {
            let next = i + 1;
            write(
                &dir.path().join(format!("n{i}.xml")),
                &format!("<world><Include src=\"n{next}.xml\" /></world>"),
            );
        }
        write(
            &dir.path().join(format!("n{}.xml", MAX_INCLUDE_DEPTH + 1)),
            "<world />",
        );
        let err = load_at(dir.path(), "n0.xml").unwrap_err();
        assert!(
            err.to_string().contains("depth exceeds 8"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn test_include_at_max_depth_is_allowed() {
        let dir = tempfile::tempdir().unwrap();
        for i in 0..MAX_INCLUDE_DEPTH {
            write(
                &dir.path().join(format!("d{i}.xml")),
                &format!("<world><Include src=\"d{}.xml\" /></world>", i + 1),
            );
        }
        write(
            &dir.path().join(format!("d{MAX_INCLUDE_DEPTH}.xml")),
            "<world><Entity name=\"deep\" /></world>",
        );
        let world = load_at(dir.path(), "d0.xml").unwrap();
        assert_eq!(world.nodes[0].attr("name"), Some("deep"));
    }

    #[test]
    fn test_include_requires_src() {
        let dir = tempfile::tempdir().unwrap();
        write(&dir.path().join("m.xml"), "<world><Include /></world>");
        let err = load_at(dir.path(), "m.xml").unwrap_err();
        assert!(err.to_string().contains("requires a src"), "{err}");
    }

    #[test]
    fn test_include_missing_file_errors() {
        let dir = tempfile::tempdir().unwrap();
        write(
            &dir.path().join("m.xml"),
            "<world><Include src=\"nope.xml\" /></world>",
        );
        let err = load_at(dir.path(), "m.xml").unwrap_err();
        assert!(err.to_string().contains("include not found"), "{err}");
    }

    #[test]
    fn test_load_world_rejects_non_world_root() {
        let dir = tempfile::tempdir().unwrap();
        write(&dir.path().join("m.xml"), "<Entity />");
        let err = load_at(dir.path(), "m.xml").unwrap_err();
        assert!(
            err.to_string().contains("root element must be <world>"),
            "{err}"
        );
    }
}
