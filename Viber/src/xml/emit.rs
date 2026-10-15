//! Serialização XML — escreve a árvore owned de volta para texto.
//!
//! Contraparte do parser: [`emit_document`] produz XML bem-formado que o
//! próprio `parse_file` relê sem warnings — a fundação do mundo estático
//! do `viber bake` (ler world + includes, transformar, emitir).
//!
//! Contrato de fidelidade: a árvore só contém o que o parser preserva — tags
//! e attrs tal como escritos (a raiz chega lowercased do parse; as tags
//! filhas preservam o caso original), attrs em ordem, texto direto aparado
//! (CDATA entra como texto; **comentários são descartados pelo parse** e por
//! isso não têm representação no emit), filhos em ordem.
//!
//! Formato: declaração `<?xml version="1.0" encoding="UTF-8"?>`, indentação
//! de 2 espaços, self-closing `<Tag />` para nós sem filhos nem texto.
//! Whitespace de controlo que o parser normalizaria (`\r` em texto;
//! `\n`/`\t`/`\r` em attrs — a normalização de atributos do XML converte-o
//! em espaço) sai como referência numérica (`&#13;`…) para o roundtrip ser
//! fiel em valor.
//!
//! Além do emit, este módulo traz a API de edição mínima que o bake precisa
//! (impls de [`XmlNode`]/[`XmlDocument`]): criar nó, duplicar subárvore,
//! inserir/remover filho e set/get attr. O modelo já é todo owned
//! (`String`/`Vec`, sem lifetimes do roxmltree), pelo que não há conversão
//! a fazer — a edição é directa na própria árvore.

use super::{XmlDocument, XmlNode};

// ---------------------------------------------------------------------------
// API de edição (o bake muta a árvore antes de emitir)
// ---------------------------------------------------------------------------

impl XmlNode {
    /// Cria um nó vazio com a tag dada — p.e. o `<Group>` que substitui um
    /// spawner no bake.
    pub fn new(tag: impl Into<String>) -> Self {
        XmlNode {
            tag: tag.into(),
            attrs: Vec::new(),
            text: String::new(),
            children: Vec::new(),
            origin: None,
        }
    }

    /// Builder: acrescenta um attr e devolve o próprio nó, para literais
    /// encadeados (`XmlNode::new("Group").with_attr("name", "trees")`).
    pub fn with_attr(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.attrs.push((name.into(), value.into()));
        self
    }

    /// Define um attr: substitui o valor da primeira ocorrência com este
    /// nome (mantém a posição, preservando a ordem dos restantes) ou
    /// acrescenta-o no fim se ainda não existir.
    pub fn set_attr(&mut self, name: &str, value: impl Into<String>) {
        let value = value.into();
        if let Some(slot) = self.attrs.iter_mut().find(|(k, _)| k == name) {
            slot.1 = value;
        } else {
            self.attrs.push((name.to_string(), value));
        }
    }

    /// Remove o attr com este nome; devolve `true` se existia.
    pub fn remove_attr(&mut self, name: &str) -> bool {
        let before = self.attrs.len();
        self.attrs.retain(|(k, _)| k != name);
        self.attrs.len() != before
    }

    /// Acrescenta um filho ao fim.
    pub fn push_child(&mut self, child: XmlNode) {
        self.children.push(child);
    }

    /// Duplica a subárvore inteira (alias explícito do `Clone` derivado — o
    /// nome existe para o bake ler como intenção, não acidente).
    pub fn duplicate(&self) -> XmlNode {
        self.clone()
    }
}

impl XmlDocument {
    /// Documento novo com a tag raiz dada (o bake usa `"world"`). A raiz é
    /// emitida tal como entregue — o parser lowercases-a ao ler; quem cria a
    /// árvore decide o caso.
    pub fn new(root_tag: impl Into<String>) -> Self {
        XmlDocument {
            root_tag: root_tag.into(),
            root_attrs: Vec::new(),
            root_text: String::new(),
            children: Vec::new(),
        }
    }

    /// Attr da raiz — mesmas semânticas de [`XmlNode::set_attr`].
    pub fn set_root_attr(&mut self, name: &str, value: impl Into<String>) {
        let value = value.into();
        if let Some(slot) = self.root_attrs.iter_mut().find(|(k, _)| k == name) {
            slot.1 = value;
        } else {
            self.root_attrs.push((name.to_string(), value));
        }
    }

    /// Acrescenta um filho direto à raiz.
    pub fn push_child(&mut self, child: XmlNode) {
        self.children.push(child);
    }
}

// ---------------------------------------------------------------------------
// Emit
// ---------------------------------------------------------------------------

/// Serializa o documento para XML textual: declaração, raiz e subárvore,
/// indentado a 2 espaços, terminado em newline.
/// Serializa UM elemento (com a subárvore) — o payload do copy/paste do
/// editor e de qualquer agente que queira transpor um nó.
pub fn emit_node(node: &XmlNode) -> String {
    let mut out = String::new();
    emit_element(
        &mut out,
        &node.tag,
        &node.attrs,
        &node.text,
        &node.children,
        0,
    );
    out
}

pub fn emit_document(doc: &XmlDocument) -> String {
    let mut out = String::with_capacity(4096);
    out.push_str("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
    emit_element(
        &mut out,
        &doc.root_tag,
        &doc.root_attrs,
        &doc.root_text,
        &doc.children,
        0,
    );
    out
}

/// Escreve um elemento (raiz ou filho). Nós sem filhos nem texto saem
/// self-closing; nós só com texto ficam numa linha; nós com filhos abrem
/// bloco, com o texto (se existir) na primeira linha interior — o parser
/// concatena todo o texto direto e apar-o, pelo que a posição relativa ao
/// fim é a que sobrevive ao roundtrip.
fn emit_element(
    out: &mut String,
    tag: &str,
    attrs: &[(String, String)],
    text: &str,
    children: &[XmlNode],
    depth: usize,
) {
    let pad = "  ".repeat(depth);
    out.push_str(&pad);
    out.push('<');
    out.push_str(tag);
    for (name, value) in attrs {
        out.push(' ');
        out.push_str(name);
        out.push_str("=\"");
        escape_attr(out, value);
        out.push('"');
    }
    if children.is_empty() && text.is_empty() {
        out.push_str(" />\n");
        return;
    }
    out.push('>');
    if children.is_empty() {
        escape_text(out, text);
        out.push_str("</");
        out.push_str(tag);
        out.push_str(">\n");
        return;
    }
    out.push('\n');
    if !text.is_empty() {
        out.push_str(&"  ".repeat(depth + 1));
        escape_text(out, text);
        out.push('\n');
    }
    for child in children {
        emit_element(
            out,
            &child.tag,
            &child.attrs,
            &child.text,
            &child.children,
            depth + 1,
        );
    }
    out.push_str(&pad);
    out.push_str("</");
    out.push_str(tag);
    out.push_str(">\n");
}

/// Escapes de conteúdo textual: `&`, `<`, `>` e `\r` (o parser normaliza um
/// `\r` literal para `\n`; a referência numérica sobrevive intacta).
fn escape_text(out: &mut String, s: &str) {
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '\r' => out.push_str("&#13;"),
            _ => out.push(c),
        }
    }
}

/// Escapes de valor de atributo (dentro de aspas): os do texto + `"`/`'` e
/// `\n`/`\t` — a normalização de atributos do XML substitui whitespace
/// literal por espaço, as referências numéricas preservam o carácter.
/// (`\r` já vem de escape_text, partilhado pelo match.)
fn escape_attr(out: &mut String, s: &str) {
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&apos;"),
            '\n' => out.push_str("&#10;"),
            '\t' => out.push_str("&#9;"),
            '\r' => out.push_str("&#13;"),
            _ => out.push(c),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::super::{XmlDocument, XmlNode, include, parse_file};
    use super::emit_document;

    /// Mundo real de teste — a prova de fidelidade exigida pelo bake.
    const SIMPLE_RPG_WORLD: &str =
        concat!(env!("CARGO_MANIFEST_DIR"), "/examples/simple-rpg/world.xml");

    /// Documento aplanado: raiz/texto do parse original + filhos com todos
    /// os `<Include>` expandidos (o mesmo ponto de partida do bake).
    fn flattened_world(path: &Path) -> XmlDocument {
        let base = parse_file(path).unwrap();
        let loaded = include::load_world(path).unwrap();
        XmlDocument {
            root_tag: base.root_tag,
            root_attrs: loaded.root_attrs,
            root_text: base.root_text,
            children: loaded.nodes,
        }
    }

    /// Re-parseia texto emitido através do parser real (ficheiro em tempdir,
    /// como o `parse_file` exige).
    fn reparse(emitted: &str) -> XmlDocument {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("emitted.xml");
        std::fs::write(&path, emitted).unwrap();
        parse_file(&path).unwrap()
    }

    /// Igualdade profunda de documentos: XmlDocument não deriva PartialEq,
    /// mas XmlNode deriva — compara os quatro campos.
    fn assert_docs_equal(a: &XmlDocument, b: &XmlDocument) {
        assert_eq!(a.root_tag, b.root_tag, "root_tag");
        assert_eq!(a.root_attrs, b.root_attrs, "root_attrs");
        assert_eq!(a.root_text, b.root_text, "root_text");
        assert_eq!(a.children, b.children, "children");
    }

    fn count_nodes(nodes: &[XmlNode]) -> usize {
        nodes.len()
            + nodes
                .iter()
                .map(|n| count_nodes(&n.children))
                .sum::<usize>()
    }

    // ---------------------------------------------------------------- emit

    #[test]
    fn test_emit_minimal_document_exact_string() {
        let mut doc = XmlDocument::new("world");
        doc.set_root_attr("clear-color", "#8fc2ef");
        doc.push_child(XmlNode::new("Entity"));
        assert_eq!(
            emit_document(&doc),
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
             <world clear-color=\"#8fc2ef\">\n  \
             <Entity />\n\
             </world>\n"
        );
    }

    #[test]
    fn test_emit_node_forms_self_closing_text_and_block() {
        let mut doc = XmlDocument::new("world");
        doc.push_child(XmlNode::new("Empty"));
        let mut texto = XmlNode::new("UiText");
        texto.text = "100/100".into();
        doc.push_child(texto);
        let mut misto = XmlNode::new("Legend");
        misto.text = "x < y".into();
        misto.push_child(XmlNode::new("Child").with_attr("name", "inner"));
        doc.push_child(misto);
        assert_eq!(
            emit_document(&doc),
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
             <world>\n  \
             <Empty />\n  \
             <UiText>100/100</UiText>\n  \
             <Legend>\n    \
             x &lt; y\n    \
             <Child name=\"inner\" />\n  \
             </Legend>\n\
             </world>\n"
        );
    }

    #[test]
    fn test_escape_attr_values_exact_string() {
        let node = XmlNode::new("E").with_attr("a", "<&\">'");
        let mut doc = XmlDocument::new("world");
        doc.push_child(node);
        let emitted = emit_document(&doc);
        assert!(
            emitted.contains("<E a=\"&lt;&amp;&quot;&gt;&apos;\" />"),
            "{emitted}"
        );
    }

    #[test]
    fn test_escape_text_exact_string() {
        let mut node = XmlNode::new("T");
        // Aspas não precisam de escape em texto (e não o têm); unicode passa.
        node.text = "a<b&c>d\"e'f café ☕".into();
        let mut doc = XmlDocument::new("world");
        doc.push_child(node);
        let emitted = emit_document(&doc);
        assert!(
            emitted.contains("<T>a&lt;b&amp;c&gt;d\"e'f café ☕</T>"),
            "{emitted}"
        );
    }

    // ------------------------------------------------------------- roundtrip

    #[test]
    fn test_roundtrip_synthetic_world() {
        let src = concat!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n",
            "<!-- comentário com <Entity shadows> dentro — o parse descarta -->\n",
            "<world clear-color=\"#8fc2ef\" título=\"céu &amp; mar ☕\">\n",
            "  <PointLight shadows />\n",
            "  <Entity name='single-quoted' ",
            "note=\"a &lt; b &amp;&amp; &quot;q&quot; &gt; c\" ",
            "linha=\"x&#10;y\" tab=\"t&#9;u\" cr=\"v&#13;w\" />\n",
            "  <Estilo><![CDATA[.hud { color: red; } <b>&]]></Estilo>\n",
            "  <Desc>a&#13;b</Desc>\n",
            "  <Ângulo graus=\"90.5\" nó=\"café\" />\n",
            "  <Legend>x &lt; y &amp; z<Child name=\"in-legend\" /></Legend>\n",
            "</world>\n",
        );
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("synthetic.xml");
        std::fs::write(&path, src).unwrap();

        let original = parse_file(&path).unwrap();
        // Comportamento documentado: CDATA vira texto (crú), comentários
        // caem no parse — nenhum dos dois existe no modelo.
        assert_eq!(original.children[2].text, ".hud { color: red; } <b>&");
        assert_eq!(original.children[3].text, "a\rb");
        assert_eq!(original.children[1].attr("linha"), Some("x\ny"));
        assert_eq!(original.children[1].attr("cr"), Some("v\rw"));

        let emitted = emit_document(&original);
        assert_docs_equal(&original, &reparse(&emitted));
    }

    #[test]
    fn test_roundtrip_control_chars_programmatic() {
        let mut doc = XmlDocument::new("world");
        doc.push_child(XmlNode::new("E").with_attr("v", "x\ny\tz\rw"));
        let mut t = XmlNode::new("T");
        t.text = "a\rb\nc".into();
        doc.push_child(t);

        let emitted = emit_document(&doc);
        // Whitespace de controlo em attrs/texto sai como referências
        // numéricas para sobreviver à normalização do parser.
        assert!(emitted.contains("v=\"x&#10;y&#9;z&#13;w\""), "{emitted}");
        let re = reparse(&emitted);
        assert_eq!(re.children[0].attr("v"), Some("x\ny\tz\rw"));
        assert_eq!(re.children[1].text, "a\rb\nc");
        assert_docs_equal(&doc, &re);
    }

    #[test]
    fn test_roundtrip_simple_rpg_real_world() {
        let path = Path::new(SIMPLE_RPG_WORLD);
        let doc = flattened_world(path);
        // Sanidade: o mundo é grande e os includes expandiram mesmo.
        assert!(doc.children.len() > 10, "filhos: {}", doc.children.len());
        let total = count_nodes(&doc.children);
        assert!(total > 100, "nós totais: {total}");

        let emitted = emit_document(&doc);
        assert!(
            !emitted.contains("<Include"),
            "includes deviam estar expandidos"
        );
        assert_docs_equal(&doc, &reparse(&emitted));
    }

    // ---------------------------------------------------------------- edição

    #[test]
    fn test_edit_api_replaces_spawner_and_emits() {
        let mut doc = XmlDocument::new("world");
        let mut terrain = XmlNode::new("Terrain").with_attr("heightmap", "/terrain/heightmap.png");
        terrain.set_attr("heightmap", "/baked/terrain.png"); // reescreve
        let mut pad = XmlNode::new("TerrainPad").with_attr("pos", "10 0 10");
        pad.set_attr("height", "2.5"); // acrescenta novo
        let mut spawner = XmlNode::new("StaticSpawner");
        spawner.push_child(XmlNode::new("Cuboid").with_attr("half-size", "0.5"));
        doc.push_child(terrain);
        doc.push_child(pad);
        doc.push_child(spawner);

        // Transformação do bake: `<StaticSpawner>` → `<Group>` com os filhos
        // duplicados e enriquecidos.
        let idx = doc
            .children
            .iter()
            .position(|n| n.tag.eq_ignore_ascii_case("staticspawner"))
            .unwrap();
        let group = {
            let spawner = &doc.children[idx];
            let mut g = XmlNode::new("Group").with_attr("name", "baked-static");
            for child in &spawner.children {
                let mut dup = child.duplicate();
                dup.set_attr("place", "at: 1 2");
                g.push_child(dup);
            }
            g
        };
        doc.children[idx] = group;
        assert!(doc.children[1].remove_attr("pos"));

        let re = reparse(&emit_document(&doc));
        assert_eq!(re.children.len(), 3);
        assert_eq!(re.children[0].attr("heightmap"), Some("/baked/terrain.png"));
        assert_eq!(re.children[1].attr("height"), Some("2.5"));
        assert_eq!(re.children[1].attr("pos"), None);
        let group = &re.children[2];
        assert_eq!(group.tag, "Group");
        assert_eq!(group.attr("name"), Some("baked-static"));
        assert_eq!(group.children.len(), 1);
        assert_eq!(group.children[0].tag, "Cuboid");
        assert_eq!(group.children[0].attr("half-size"), Some("0.5"));
        assert_eq!(group.children[0].attr("place"), Some("at: 1 2"));
    }

    #[test]
    fn test_set_attr_preserves_order_and_remove_reports() {
        let mut node = XmlNode::new("E").with_attr("a", "1").with_attr("b", "2");
        node.set_attr("a", "9"); // substitui no sítio
        node.set_attr("c", "3"); // acrescenta no fim
        assert_eq!(
            node.attrs,
            vec![
                ("a".to_string(), "9".to_string()),
                ("b".to_string(), "2".to_string()),
                ("c".to_string(), "3".to_string()),
            ]
        );
        assert!(node.remove_attr("b"));
        assert!(!node.remove_attr("b"));
        assert_eq!(node.attr("b"), None);
    }

    #[test]
    fn test_duplicate_is_independent_subtree() {
        let mut original = XmlNode::new("Group");
        original.push_child(XmlNode::new("Child").with_attr("k", "v"));
        let dup = original.duplicate();
        original.children[0].set_attr("k", "mutated");
        original.push_child(XmlNode::new("Extra"));
        assert_eq!(dup.children.len(), 1);
        assert_eq!(dup.children[0].attr("k"), Some("v"));
        assert_eq!(original.children.len(), 2);
    }
}
