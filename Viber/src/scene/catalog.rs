//! Catálogo "Add Node" (tags conhecidas com snippets válidos) e o drag-drop de `.glb` para o
//! viewport — Fase 3 do editor, com paridade: o diálogo do editor e um agente usam os MESMOS
//! builders (a op `insert` que daqui sai é a que `viber scene insert` aplicaria).

use std::path::Path;

use super::ops::EditOp;
use super::pose;

/// Grupo do diálogo Add Node (ordem de apresentação).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TemplateGroup {
    Content,
    Shape,
    Light,
    Water,
    Feature,
}

impl TemplateGroup {
    pub fn as_str(self) -> &'static str {
        match self {
            TemplateGroup::Content => "content",
            TemplateGroup::Shape => "shape",
            TemplateGroup::Light => "light",
            TemplateGroup::Water => "water",
            TemplateGroup::Feature => "feature",
        }
    }
}

/// Uma tag do catálogo com um snippet mínimo válido (parsa e spawna tal qual).
pub struct NodeTemplate {
    pub group: TemplateGroup,
    pub tag: &'static str,
    pub label: &'static str,
    pub snippet: &'static str,
}

/// O catálogo: só tags cujo snippet passa pelo parser (garantido por teste). Tags conhecidas
/// fora do catálogo continuam a inserir-se com o snippet genérico `<Tag />`.
pub const TEMPLATES: &[NodeTemplate] = &[
    NodeTemplate {
        group: TemplateGroup::Content,
        tag: "entity",
        label: "Entity (contentor de props)",
        snippet: r#"<Entity name="prop" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Content,
        tag: "group",
        label: "Group (agrupar nós)",
        snippet: r#"<Group name="group" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Content,
        tag: "gltfscene",
        label: "GLB (GltfScene)",
        snippet: r#"<GltfScene url="/assets/meshes/model.glb" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Shape,
        tag: "cuboid",
        label: "Cuboid",
        snippet: r#"<Cuboid half-size="0.5 0.5 0.5" translation="0 0.5 0" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Shape,
        tag: "sphere",
        label: "Sphere",
        snippet: r#"<Sphere radius="0.6" translation="0 0.6 0" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Shape,
        tag: "cylinder",
        label: "Cylinder",
        snippet: r#"<Cylinder radius="0.4" half-height="0.8" translation="0 0.8 0" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Shape,
        tag: "plane",
        label: "Plane",
        snippet: r#"<Plane translation="0 0.01 0" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Shape,
        tag: "capsule",
        label: "Capsule",
        snippet: r#"<Capsule translation="0 0.8 0" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Light,
        tag: "pointlight",
        label: "PointLight",
        snippet: r#"<PointLight translation="0 3 0" intensity="1500" shadows="true" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Light,
        tag: "directionallight",
        label: "DirectionalLight",
        snippet: r#"<DirectionalLight />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Light,
        tag: "ambientlight",
        label: "AmbientLight",
        snippet: r#"<AmbientLight color="0xc8d8e8" brightness="110" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Water,
        tag: "lake",
        label: "Lake",
        snippet: r#"<Lake at="0 0" radius="18" depth="2" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Water,
        tag: "river",
        label: "River",
        snippet: r#"<River path="0 0 30 4 60 2" width="12" depth="2.5" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Feature,
        tag: "terrainpad",
        label: "TerrainPad (achatar terreno)",
        snippet: r#"<TerrainPad at="0 0" size="40 40" falloff="20" corner-radius="12" />"#,
    },
    NodeTemplate {
        group: TemplateGroup::Feature,
        tag: "road",
        label: "Road",
        snippet: r#"<Road path="0 0 20 6 40 8" width="6" />"#,
    },
];

/// O catálogo completo (dialog Add Node).
pub fn templates() -> &'static [NodeTemplate] {
    TEMPLATES
}

/// O template de uma tag (sem distinguir maiúsculas), se estiver no catálogo.
pub fn template_for(tag: &str) -> Option<&'static NodeTemplate> {
    TEMPLATES
        .iter()
        .find(|template| template.tag.eq_ignore_ascii_case(tag))
}

/// O snippet a inserir para uma tag: o do catálogo, ou `<Tag />` genérico (tags desconhecidas
/// do parser degradam para no-op com aviso no `analyze` — nunca rebentam o mundo).
pub fn snippet_for(tag: &str) -> String {
    template_for(tag)
        .map(|template| template.snippet.to_string())
        .unwrap_or_else(|| format!("<{tag} />"))
}

/// A op do diálogo Add Node: insere o snippet da tag em `parent`.
pub fn add_node(parent: &str, tag: &str, index: Option<usize>) -> EditOp {
    EditOp::Insert {
        parent: parent.to_string(),
        index,
        xml: snippet_for(tag),
    }
}

/// A op de largar um `.glb` no mundo: `Entity` com o nome do ficheiro e o `GltfScene` filho,
/// posta em `world_pos` (coordenadas MUNDO — o raycast do terreno, ou o que um agente pedir)
/// convertidas para o referencial de `parent` ([`pose::world_frame`]). Pose canónica.
pub fn glb_drop(
    doc: &super::doc::SceneDoc,
    parent: &str,
    glb_path: &str,
    world_pos: [f32; 3],
) -> anyhow::Result<EditOp> {
    let parent_ref = doc.resolve(parent)?;
    let name = Path::new(glb_path)
        .file_stem()
        .and_then(|stem| stem.to_str())
        .unwrap_or("dropped");
    let url = suggest_url(glb_path);
    let local = pose::world_to_local(
        doc,
        &parent_ref,
        pose::Pose {
            translation: bevy::math::Vec3::from(world_pos),
            ..pose::Pose::default()
        },
    );
    let translation = pose::format_vec3(local.translation);
    Ok(EditOp::Insert {
        parent: parent.to_string(),
        index: None,
        xml: format!(
            r#"<Entity name="{name}" translation="{translation}"><GltfScene url="{url}" /></Entity>"#
        ),
    })
}

/// O catálogo em JSON (o "menu" Add Node de um agente).
pub fn to_json() -> serde_json::Value {
    serde_json::Value::Array(
        TEMPLATES
            .iter()
            .map(|t| serde_json::json!({ "group": t.group.as_str(), "tag": t.tag, "label": t.label, "snippet": t.snippet }))
            .collect(),
    )
}

/// URL a escrever no XML para um caminho arrastado do sistema: se vive sob uma pasta
/// `public/` (o pool partilhado), vira o caminho `/assets/…` habitual dos mundos; senão
/// usa-se o caminho tal como arrived (absoluto ou relativo ao mundo).
pub fn suggest_url(glb_path: &str) -> String {
    let path = glb_path.replace('\\', "/");
    match path.rfind("/public/") {
        Some(index) => path[index + "/public".len()..].to_string(),
        None => path,
    }
}
