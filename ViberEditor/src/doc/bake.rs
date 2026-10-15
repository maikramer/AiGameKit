//! Serde model of `bake.json` (schema 1) — the baked world document.
//!
//! The editor only ever *reads* bakes, so every field is optional at the serde level
//! (container-level `#[serde(default)]` everywhere): a bake written by a different Viber
//! version must degrade into an emptier panel, never a panic. A `schema` other than 1 is
//! surfaced as a warning through [`BakeDir::warnings`] instead of an error.

use std::path::Path;

use anyhow::Context;
use serde::Deserialize;

use super::ahgt::{self, Ahgt};

/// The schema version this editor understands.
pub const KNOWN_SCHEMA: u32 = 1;

/// The whole `bake.json` document.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct BakeDoc {
    pub schema: u32,
    pub source: BakeSource,
    pub terrain: TerrainSection,
    pub features: Features,
    pub spawn_groups: Vec<SpawnGroup>,
}

impl BakeDoc {
    /// Whether the document was written for a schema this editor knows.
    pub fn schema_known(&self) -> bool {
        self.schema == KNOWN_SCHEMA
    }
}

/// Where the bake came from.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct BakeSource {
    pub world: Option<String>,
    pub viber_version: Option<String>,
}

/// The `terrain` section.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct TerrainSection {
    pub world_size: Option<f32>,
    pub max_height: Option<f32>,
    /// Baked heightmap file name, relative to the bake directory.
    pub heightmap: Option<String>,
    /// The original heightmap the bake was generated from.
    pub source_heightmap: Option<String>,
    pub layers: Vec<String>,
    pub spec: TerrainSpec,
}

/// The terrain chunking/LOD spec snapshot.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct TerrainSpec {
    pub render_distance: Option<f32>,
    pub levels: Option<u32>,
    pub resolution: Option<u32>,
    pub collision_resolution: Option<u32>,
    pub lod_hysteresis: Option<f32>,
    pub lod_distance_ratio: Option<f32>,
    pub shore_width: Option<f32>,
    pub snow_height: Option<f32>,
    pub cliff_angle: Option<f32>,
    pub sharpen: Option<bool>,
}

/// Every carved/placed terrain feature, by kind.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Features {
    pub pads: Vec<Pad>,
    pub lakes: Vec<Lake>,
    pub rivers: Vec<River>,
    pub road_networks: Vec<RoadNetwork>,
    pub roads: Vec<Road>,
    pub cliffs: Vec<Cliff>,
    pub arches: Vec<Arch>,
    pub caves: Vec<Cave>,
    pub exclusions: Vec<Exclusion>,
}

/// A flattened terrain pad (XZ ground position + size in meters).
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Pad {
    pub id: String,
    pub at: [f32; 2],
    pub size: [f32; 2],
    pub falloff: Option<f32>,
    pub corner_radius: Option<f32>,
    pub resolved_height: Option<f32>,
    pub explicit_height: Option<bool>,
    pub source: Option<String>,
}

/// A carved lake; `outline` is the resolved water polygon (XZ meters).
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Lake {
    pub id: String,
    pub at: Option<[f32; 2]>,
    pub radius: Option<f32>,
    pub depth: Option<f32>,
    pub water_level: Option<f32>,
    pub outline: Vec<[f32; 2]>,
    pub source: Option<String>,
}

/// A carved river polyline (XZ meters).
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct River {
    pub id: String,
    pub points: Vec<[f32; 2]>,
    pub width: Option<f32>,
    pub depth: Option<f32>,
    pub water_level: Option<f32>,
    pub source: Option<String>,
}

/// A road network: named waypoints joined by segments.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct RoadNetwork {
    pub id: String,
    pub profile: Option<String>,
    pub segments: Vec<RoadSegment>,
    pub source: Option<String>,
}

/// One segment of a [`RoadNetwork`] between named endpoints.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct RoadSegment {
    pub a: Option<String>,
    pub b: Option<String>,
    pub polyline: Vec<[f32; 2]>,
    pub width: Option<f32>,
}

/// A standalone road polyline (XZ meters).
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Road {
    pub id: String,
    pub points: Vec<[f32; 2]>,
    pub width: Option<f32>,
    pub source: Option<String>,
}

/// A cliff feature.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Cliff {
    pub id: String,
    pub at: [f32; 2],
    pub profile: Option<String>,
    pub source: Option<String>,
}

/// A natural arch.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Arch {
    pub id: String,
    pub at: [f32; 2],
    pub width: Option<f32>,
    pub height: Option<f32>,
    pub yaw: Option<f32>,
    pub source: Option<String>,
}

/// A cave mouth (one radius per entrance chamber).
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Cave {
    pub id: String,
    pub at: [f32; 2],
    pub radius: Vec<f32>,
    pub depth: Option<f32>,
    pub source: Option<String>,
}

/// A spawn exclusion circle (XZ meters).
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Exclusion {
    pub id: String,
    pub at: [f32; 2],
    pub radius: Option<f32>,
    pub source: Option<String>,
}

/// One spawn group with its resolved rules and baked instances.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct SpawnGroup {
    pub id: String,
    pub name: Option<String>,
    pub kind: Option<String>,
    pub source: Option<String>,
    /// SplitMix64 group seed — 64-bit on the engine side (`viber bake`).
    pub seed: Option<u64>,
    pub rules: SpawnRules,
    pub instances: Vec<SpawnInstance>,
}

/// The resolved spawn rules of a group.
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct SpawnRules {
    pub count: Option<u32>,
    pub region_min: Option<[f32; 3]>,
    pub region_max: Option<[f32; 3]>,
    pub avoid_water: Option<bool>,
    pub in_water: Option<bool>,
    pub near_water: Option<bool>,
    pub avoid_road: Option<bool>,
    pub avoid_cliff: Option<bool>,
    pub max_slope_deg: Option<f32>,
}

/// One baked spawn instance (translation/rotation/scale + asset).
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct SpawnInstance {
    pub name: Option<String>,
    pub translation: Option<[f32; 3]>,
    pub rotation: Option<[f32; 4]>,
    pub yaw_deg: Option<f32>,
    pub scale: Option<[f32; 3]>,
    pub asset: Option<String>,
}

/// A bake directory loaded for the World Doc panel: the parsed document, the baked
/// heightmap (when present and readable) and any non-fatal warnings.
#[derive(Debug, Clone, Default)]
pub struct BakeDir {
    /// The bake directory the document was read from.
    pub dir: std::path::PathBuf,
    pub doc: BakeDoc,
    /// The baked `.ahgt` heightmap, when it exists and parses.
    pub heightmap: Option<Ahgt>,
    /// Non-fatal problems the panel should show (schema mismatch, unreadable heightmap…).
    pub warnings: Vec<String>,
}

impl BakeDir {
    /// Loads `bake.json` (required) and the heightmap it references (best effort).
    ///
    /// Missing files and unknown schemas are warnings, not errors: the panel stays
    /// useful with whatever the bake does provide.
    pub fn load(dir: &Path) -> anyhow::Result<Self> {
        let path = dir.join("bake.json");
        let bytes =
            std::fs::read(&path).with_context(|| format!("failed to read {}", path.display()))?;
        let doc: BakeDoc = serde_json::from_slice(&bytes)
            .with_context(|| format!("failed to parse {}", path.display()))?;

        let mut warnings = Vec::new();
        if !doc.schema_known() {
            warnings.push(format!(
                "bake schema {} is newer/different than the known {} — fields may be missing",
                doc.schema, KNOWN_SCHEMA
            ));
        }

        let mut bake = Self {
            dir: dir.to_path_buf(),
            doc,
            heightmap: None,
            warnings,
        };
        bake.load_heightmap();
        Ok(bake)
    }

    /// Best-effort load of `terrain.heightmap`; failures become warnings.
    fn load_heightmap(&mut self) {
        let Some(name) = self.doc.terrain.heightmap.clone() else {
            self.warnings.push("terrain has no baked heightmap".into());
            return;
        };
        let path = self.dir.join(&name);
        match std::fs::read(&path) {
            Ok(bytes) => match ahgt::Ahgt::from_bytes(&bytes) {
                Ok(map) => self.heightmap = Some(map),
                Err(error) => self
                    .warnings
                    .push(format!("heightmap {} failed to parse: {error}", name)),
            },
            Err(error) => self
                .warnings
                .push(format!("heightmap {} unreadable: {error}", name)),
        }
    }

    /// The world extent in meters: the document's declaration, the heightmap's, or a guess.
    pub fn world_size(&self) -> f32 {
        self.doc
            .terrain
            .world_size
            .or(self.heightmap.as_ref().and_then(|map| map.meta.world_size))
            .unwrap_or(4000.0)
            .max(1.0)
    }
}

#[cfg(test)]
mod debug_real_bake {
    use super::*;

    /// Regressão do contrato real: o bake.json do simple-rpg (quando o
    /// checkout o tem) tem de fazer parse — apanha drifts de schema entre o
    /// emissor do motor e este reader (ex.: seed u64 vs u32).
    #[test]
    fn debug_parse_real_simple_rpg_bake() {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../Viber/examples/simple-rpg/world.data/bake.json"
        );
        let Ok(bytes) = std::fs::read(path) else {
            eprintln!("bake real ausente ({path}) — skip");
            return;
        };
        match serde_json::from_slice::<BakeDoc>(&bytes) {
            Ok(doc) => {
                println!(
                    "OK: {} groups, schema {}",
                    doc.spawn_groups.len(),
                    doc.schema
                )
            }
            Err(e) => panic!("ERRO SERDE: {e}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// The full schema-1 contract fixture: every feature kind, one spawn group.
    fn fixture() -> serde_json::Value {
        json!({
            "schema": 1,
            "source": { "world": "examples/simple-rpg/world.xml", "viber_version": "0.1.0" },
            "terrain": {
                "world_size": 4000.0, "max_height": 200.0,
                "heightmap": "terrain.baked.ahgt",
                "source_heightmap": "/assets/terrain/terrain.ahgt",
                "layers": ["grass"],
                "spec": { "render_distance": 950.0, "levels": 5, "resolution": 128,
                          "collision_resolution": 64, "lod_hysteresis": 1.3,
                          "lod_distance_ratio": 1.7, "shore_width": 6.0, "snow_height": 0.92,
                          "cliff_angle": 50.0, "sharpen": true }
            },
            "features": {
                "pads": [ { "id": "pad-0", "at": [0.0, 0.0], "size": [120.0, 120.0],
                            "falloff": 20.0, "corner_radius": 18.0, "resolved_height": 46.3,
                            "explicit_height": false, "source": "world/cities/discordia.xml" } ],
                "lakes": [ { "id": "lake-0", "at": [-80.0, 104.0], "radius": 16.0, "depth": 5.0,
                             "water_level": 43.2, "outline": [[0.0, 0.0], [16.0, 0.0]],
                             "source": "world/water.xml" } ],
                "rivers": [ { "id": "river-0", "points": [[314.0, 125.0], [10.0, 10.0]],
                              "width": 16.0, "depth": 3.7, "water_level": null,
                              "source": "world/water.xml" } ],
                "road_networks": [ { "id": "net-0", "profile": "artery",
                                     "segments": [ { "a": "plaza", "b": "n_gate",
                                                     "polyline": [[0.0, 0.0], [5.0, 5.0]],
                                                     "width": 4.0 } ],
                                     "source": "world/roads.xml" } ],
                "roads": [ { "id": "road-0", "points": [[0.0, 0.0], [9.0, 9.0]], "width": 3.0,
                             "source": "world/roads.xml" } ],
                "cliffs": [ { "id": "cliff-0", "at": [1.0, 2.0], "profile": "columnar",
                              "source": "world/cliffs.xml" } ],
                "arches": [ { "id": "arch-0", "at": [352.0, 96.0], "width": 20.0, "height": 12.0,
                              "yaw": 40.0, "source": "world/arches.xml" } ],
                "caves": [ { "id": "cave-0", "at": [3.0, 4.0], "radius": [2.4], "depth": 10.0,
                             "source": "world/caves.xml" } ],
                "exclusions": [ { "id": "excl-0", "at": [0.0, 0.0], "radius": 52.0,
                                  "source": "world/spawn.xml" } ]
            },
            "spawn_groups": [
                { "id": "grp-0", "name": "forest-pines", "kind": "static",
                  "source": "world/vegetation/forest.xml", "seed": 12,
                  "rules": { "count": 620, "region_min": [-450.0, 0.0, -450.0],
                             "region_max": [450.0, 200.0, 450.0], "avoid_water": true,
                             "in_water": false, "near_water": false, "avoid_road": true,
                             "avoid_cliff": true, "max_slope_deg": 26.0 },
                  "instances": [ { "name": "inst-grp-0-0000", "translation": [1.0, 46.2, 3.0],
                                   "rotation": [0.0, 0.38, 0.0, 0.92], "yaw_deg": 45.0,
                                   "scale": [1.0, 1.1, 1.0],
                                   "asset": "/assets/meshes/forest/pine_a.glb" } ] }
            ]
        })
    }

    #[test]
    fn parses_the_full_contract() {
        let doc: BakeDoc = serde_json::from_value(fixture()).unwrap();
        assert!(doc.schema_known());
        assert_eq!(
            doc.source.world.as_deref(),
            Some("examples/simple-rpg/world.xml")
        );
        assert_eq!(doc.terrain.world_size, Some(4000.0));
        assert_eq!(doc.terrain.heightmap.as_deref(), Some("terrain.baked.ahgt"));
        assert_eq!(doc.terrain.layers, vec!["grass"]);
        assert_eq!(doc.terrain.spec.levels, Some(5));
        assert_eq!(doc.terrain.spec.sharpen, Some(true));

        assert_eq!(doc.features.pads.len(), 1);
        assert_eq!(doc.features.pads[0].size, [120.0, 120.0]);
        assert_eq!(doc.features.pads[0].resolved_height, Some(46.3));
        assert_eq!(doc.features.pads[0].explicit_height, Some(false));

        assert_eq!(doc.features.lakes[0].water_level, Some(43.2));
        assert_eq!(doc.features.lakes[0].outline.len(), 2);
        // JSON `null` maps to `None`, not a parse failure.
        assert_eq!(doc.features.rivers[0].water_level, None);
        assert_eq!(doc.features.rivers[0].points.len(), 2);

        assert_eq!(
            doc.features.road_networks[0].segments[0].a.as_deref(),
            Some("plaza")
        );
        assert_eq!(doc.features.caves[0].radius, vec![2.4]);
        assert_eq!(doc.features.exclusions[0].radius, Some(52.0));

        let group = &doc.spawn_groups[0];
        assert_eq!(group.name.as_deref(), Some("forest-pines"));
        assert_eq!(group.seed, Some(12));
        assert_eq!(group.rules.count, Some(620));
        assert_eq!(group.rules.region_min, Some([-450.0, 0.0, -450.0]));
        assert_eq!(group.instances.len(), 1);
        assert_eq!(
            group.instances[0].asset.as_deref(),
            Some("/assets/meshes/forest/pine_a.glb")
        );
    }

    #[test]
    fn parses_an_empty_object_and_unknown_fields() {
        // A `{}` bake (or one from a different schema with new field names) must not fail.
        let doc: BakeDoc = serde_json::from_value(json!({})).unwrap();
        assert_eq!(doc.schema, 0);
        assert!(!doc.schema_known());
        assert_eq!(doc.features.pads.len(), 0);
        assert_eq!(doc.spawn_groups.len(), 0);

        let doc: BakeDoc = serde_json::from_value(json!({
            "schema": 1,
            "fancy_new_section": { "whatever": 1 },
            "terrain": { "world_size": 1000.0, "future_field": true },
            "spawn_groups": [ { "id": "g", "future": [1, 2] } ]
        }))
        .unwrap();
        assert!(doc.schema_known());
        assert_eq!(doc.terrain.world_size, Some(1000.0));
        assert_eq!(doc.spawn_groups[0].id, "g");
    }

    #[test]
    fn wrong_schema_is_a_warning_not_an_error() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("bake.json"),
            serde_json::to_string(&json!({ "schema": 2 })).unwrap(),
        )
        .unwrap();
        let bake = BakeDir::load(dir.path()).unwrap();
        assert_eq!(bake.doc.schema, 2);
        assert_eq!(bake.warnings.len(), 2);
        assert!(bake.warnings[0].contains("schema 2"));
        assert!(bake.warnings[1].contains("heightmap"));
    }

    #[test]
    fn load_reports_missing_bake_json() {
        let dir = tempfile::tempdir().unwrap();
        assert!(BakeDir::load(dir.path()).is_err());
    }

    #[test]
    fn world_size_falls_back_to_heightmap_then_default() {
        let mut bake = BakeDir::default();
        assert_eq!(bake.world_size(), 4000.0);

        bake.doc.terrain.world_size = Some(2048.0);
        assert_eq!(bake.world_size(), 2048.0);

        bake.doc.terrain.world_size = None;
        bake.heightmap = Some(Ahgt {
            width: 4,
            depth: 4,
            data: vec![0; 16],
            meta: ahgt::AhgtMeta {
                world_size: Some(1024.0),
                max_height: Some(64.0),
            },
        });
        assert_eq!(bake.world_size(), 1024.0);
    }
}
