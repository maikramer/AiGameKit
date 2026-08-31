//! XML layer: element tree model, tolerant preprocessing and file parsing.
//!
//! The world format is strict XML, but keeps two small tolerances from the
//! original engine: boolean attributes may be written bare (`<PointLight
//! shadows>`) and tag names are matched case-insensitively downstream.

pub mod include;
pub mod values;

use std::path::Path;

use anyhow::{Context, Result};

/// A parsed XML element with raw string attributes.
///
/// Tag and attribute names are preserved exactly as written; recipe matching
/// lowercases them. Attribute values stay strings until a recipe interprets
/// them.
#[derive(Debug, Clone, PartialEq)]
pub struct XmlNode {
    pub tag: String,
    pub attrs: Vec<(String, String)>,
    pub children: Vec<XmlNode>,
}

impl XmlNode {
    /// First attribute value with this exact name (case-sensitive, kebab-case).
    pub fn attr(&self, name: &str) -> Option<&str> {
        self.attrs
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.as_str())
    }
}

/// A parsed document: the root element plus its expanded children.
#[derive(Debug, Clone)]
pub struct XmlDocument {
    /// Root tag, lowercased (`world`, `scene`, …).
    pub root_tag: String,
    pub root_attrs: Vec<(String, String)>,
    pub children: Vec<XmlNode>,
}

/// Read and parse an XML file.
pub fn parse_file(path: &Path) -> Result<XmlDocument> {
    let raw =
        std::fs::read_to_string(path).with_context(|| format!("reading {}", path.display()))?;
    parse_str_with_context(&normalize_bare_bools(&raw), path)
}

fn parse_str_with_context(src: &str, path: &Path) -> Result<XmlDocument> {
    let doc =
        roxmltree::Document::parse(src).with_context(|| format!("parsing {}", path.display()))?;
    let root = doc.root_element();
    Ok(XmlDocument {
        root_tag: root.tag_name().name().to_ascii_lowercase(),
        root_attrs: collect_attrs(root),
        children: collect_children(root),
    })
}

fn collect_attrs(node: roxmltree::Node) -> Vec<(String, String)> {
    node.attributes()
        .map(|a| (a.name().to_string(), a.value().to_string()))
        .collect()
}

fn collect_children(node: roxmltree::Node) -> Vec<XmlNode> {
    node.children()
        .filter(|n| n.is_element())
        .map(|n| XmlNode {
            tag: n.tag_name().name().to_string(),
            attrs: collect_attrs(n),
            children: collect_children(n),
        })
        .collect()
}

/// Rewrite bare boolean attributes (`<Fog enabled>`) as `enabled="true"` so
/// the strict XML parser accepts HTML-ish worlds. Quoted values pass through
/// untouched.
pub fn normalize_bare_bools(src: &str) -> String {
    let b = src.as_bytes();
    let mut out = String::with_capacity(src.len() + 16);
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'<' && i + 1 < b.len() && b[i + 1].is_ascii_alphabetic() {
            i += 1;
            let name_start = i;
            while i < b.len() && !b[i].is_ascii_whitespace() && b[i] != b'>' && b[i] != b'/' {
                i += 1;
            }
            out.push('<');
            out.push_str(&src[name_start..i]);
            while i < b.len() && b[i] != b'>' {
                let ws_start = i;
                while i < b.len() && b[i].is_ascii_whitespace() {
                    i += 1;
                }
                out.push_str(&src[ws_start..i]);
                if i >= b.len() || b[i] == b'>' {
                    break;
                }
                if b[i] == b'/' {
                    out.push('/');
                    i += 1;
                    continue;
                }
                let attr_start = i;
                while i < b.len()
                    && !b[i].is_ascii_whitespace()
                    && b[i] != b'='
                    && b[i] != b'>'
                    && b[i] != b'/'
                {
                    i += 1;
                }
                let attr = &src[attr_start..i];
                let ws2 = i;
                while i < b.len() && b[i].is_ascii_whitespace() {
                    i += 1;
                }
                if i < b.len() && b[i] == b'=' {
                    out.push_str(attr);
                    out.push('=');
                    i += 1;
                    while i < b.len() && b[i].is_ascii_whitespace() {
                        out.push(b[i] as char); // ASCII whitespace: byte-safe
                        i += 1;
                    }
                    if i < b.len() && (b[i] == b'"' || b[i] == b'\'') {
                        let q = b[i];
                        let v_start = i;
                        i += 1;
                        while i < b.len() && b[i] != q {
                            i += 1;
                        }
                        if i < b.len() {
                            i += 1; // include the closing quote
                        }
                        out.push_str(&src[v_start..i]);
                    } else {
                        let v_start = i;
                        while i < b.len()
                            && !b[i].is_ascii_whitespace()
                            && b[i] != b'>'
                            && b[i] != b'/'
                        {
                            i += 1;
                        }
                        out.push_str(&src[v_start..i]);
                    }
                } else {
                    out.push_str(attr);
                    out.push_str("=\"true\"");
                    i = ws2; // re-emit the whitespace before the next token
                }
            }
            if i < b.len() {
                out.push('>');
                i += 1;
            }
        } else {
            // Not a tag start (`<!--`, `</`, `<?`, stray `<`): copy verbatim.
            // Step past this '<' first or a leading one would loop forever.
            let start = i;
            i += 1;
            while i < b.len() && b[i] != b'<' {
                i += 1;
            }
            out.push_str(&src[start..i]);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn roundtrip(src: &str) -> String {
        normalize_bare_bools(src)
    }

    #[test]
    fn test_normalize_bare_bool_attr() {
        assert_eq!(
            roundtrip("<PointLight shadows>"),
            "<PointLight shadows=\"true\">"
        );
    }

    #[test]
    fn test_normalize_bare_bool_self_closing() {
        assert_eq!(roundtrip("<Fog enabled />"), "<Fog enabled=\"true\" />");
    }

    #[test]
    fn test_normalize_keeps_quoted_values() {
        assert_eq!(
            roundtrip("<Entity name=\"café\" shadows><Entity visible=true /></Entity></Entity>"),
            "<Entity name=\"café\" shadows=\"true\"><Entity visible=true /></Entity></Entity>"
        );
    }

    #[test]
    fn test_normalize_preserves_text_and_multibyte() {
        let src = "<!-- comentário café -->\n<world><Entity /></world>";
        assert_eq!(roundtrip(src), src);
    }

    #[test]
    fn test_normalize_multiple_bare_attrs() {
        assert_eq!(
            roundtrip("<Light a b=\"2\" c>"),
            "<Light a=\"true\" b=\"2\" c=\"true\">"
        );
    }

    #[test]
    fn test_normalize_handles_leading_comment_and_declaration() {
        // Regression: a '<' that is not a tag start must advance the scanner.
        let src = "<?xml version=\"1.0\"?>\n<!-- comentário -->\n<world><Entity /></world>";
        assert_eq!(roundtrip(src), src);
    }

    #[test]
    fn test_parse_file_returns_root_tag() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bad.xml");
        std::fs::write(&path, "<Entity />").unwrap();
        let doc = parse_file(&path).unwrap();
        assert_eq!(doc.root_tag, "entity");
    }

    #[test]
    fn test_parse_file_collects_tree() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("w.xml");
        std::fs::write(
            &path,
            "<World clear-color=\"#000\"><Group name=\"a\"><Entity /></Group></World>",
        )
        .unwrap();
        let doc = parse_file(&path).unwrap();
        assert_eq!(doc.root_tag, "world");
        assert_eq!(doc.root_attrs, vec![("clear-color".into(), "#000".into())]);
        assert_eq!(doc.children.len(), 1);
        assert_eq!(doc.children[0].children.len(), 1);
    }

    #[test]
    fn test_parse_file_rejects_malformed_xml() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("broken.xml");
        std::fs::write(&path, "<World><Entity></World>").unwrap();
        assert!(parse_file(&path).is_err());
    }

    #[test]
    fn test_parse_file_accepts_bare_bool_attrs_end_to_end() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("w.xml");
        std::fs::write(&path, "<world><PointLight shadows /></world>").unwrap();
        let doc = parse_file(&path).unwrap();
        assert_eq!(doc.children[0].attr("shadows"), Some("true"));
    }

    #[test]
    fn test_attr_lookup() {
        let node = XmlNode {
            tag: "Entity".into(),
            attrs: vec![("name".into(), "hero".into())],
            children: vec![],
        };
        assert_eq!(node.attr("name"), Some("hero"));
        assert_eq!(node.attr("missing"), None);
    }
}
