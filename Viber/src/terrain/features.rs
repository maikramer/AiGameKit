//! Feature pipeline — applies every declarative ground feature to a
//! [`BrushGrid`] in the VibeGame order and produces the query registries.
//!
//! # Order (VibeGame system chain)
//!
//! 1. **Pads** — flatten first, so water carves the pad rim and roads see
//!    the final plaza plane.
//! 2. **Lakes, then rivers** — lower-only; they cut into the pads' falloff
//!    but never raise it.
//! 3. **Roads** — networks expanded per segment, plain roads first and
//!    **bridges last**, so the corridor lip sees the river banks after the
//!    flatten. Roads skip pad cores and water carve zones.
//!
//! The result carries the registries gameplay queries consume
//! (`avoid-water`, `near-water`, `isPointOnRoad`, `distanceToRoadAt`).

use bevy::math::Vec2;

use super::brush::BrushGrid;
use super::cliffs::CliffSpec;
use super::cut::{CutSpec, carve_cut};
use super::decal::GroundDecalSpec;
use super::plateau::{PlateauSpec, carve_plateau};
use super::roads::{RoadGuards, RoadNetworkSpec, RoadPath, RoadProfile, RoadSpec, carve_road};
use super::sampler::ResolvedPad;
use super::spec::TerrainPadSpec;
use super::water::{
    LakeSpec, RiverSpec, WaterBody, WaterKind, carve_lake, carve_river_with_falls,
    river_cliff_crossings,
};

/// All declarative ground features of a world.
#[derive(Debug, Clone, Default)]
pub struct TerrainFeatures {
    /// Mesas autorais (`<Plateau>`) — RAISE no heightfield + anel de parede
    /// voxel (banda construída no bootstrap). Antes dos pads: um pad sobre
    /// o planalto aplaina o topo por cima do raise.
    pub plateaus: Vec<PlateauSpec>,
    pub pads: Vec<TerrainPadSpec>,
    pub lakes: Vec<LakeSpec>,
    pub rivers: Vec<RiverSpec>,
    /// Trincheiras/cânions secos (`<Cut>`) — LOWER no heightfield + paredes
    /// voxel. Depois da água (a vala pode abrir num vale) e ANTES das
    /// estradas: o survey lê o piso da vala e pina a estrada lá dentro
    /// (o "road cut").
    pub cuts: Vec<CutSpec>,
    /// Cliff walls (`<Cliff>`) — the carve that wants the vertical step.
    pub cliffs: Vec<CliffSpec>,
    /// Tunnels (`<Cave>`) — NOT a carve. These never touch the heightfield;
    /// they become subtractive mods in the voxel field, which is the only
    /// representation that can put rock above the player's head.
    pub caves: Vec<crate::terrain::voxel::CaveSpec>,
    /// Free-standing rock portals (`<Arch>`) — union solids in the voxel
    /// field, never a carve.
    pub arches: Vec<crate::terrain::voxel::ArchSpec>,
    /// Crossings (`<Bridge>`) — union solids in the voxel field. Unlike a
    /// `<Road profile="bridge">` ribbon, the deck exists in the field, so the
    /// column trimesh carries it and the hero can stand on it.
    pub bridges: Vec<crate::terrain::voxel::BridgeSpec>,
    /// Seeding fields (`<RockFeatures>`). Resolved at bootstrap against the
    /// carved grid into plain arch/cave/bridge specs — the feature pass never
    /// sees them, because there is nothing to carve.
    pub rock_fields: Vec<crate::terrain::voxel::RockFeaturesSpec>,
    pub roads: Vec<RoadSpec>,
    pub networks: Vec<RoadNetworkSpec>,
    /// Draped ground patches (`<GroundDecal>`) — plaza floors, market
    /// aprons. Purely visual: they never touch the heightfield.
    pub decals: Vec<GroundDecalSpec>,
}

impl TerrainFeatures {
    /// No features at all — the runtime can skip the feature pass entirely.
    pub fn is_empty(&self) -> bool {
        self.plateaus.is_empty()
            && self.pads.is_empty()
            && self.lakes.is_empty()
            && self.rivers.is_empty()
            && self.cuts.is_empty()
            && self.cliffs.is_empty()
            && self.caves.is_empty()
            && self.arches.is_empty()
            && self.bridges.is_empty()
            && self.rock_fields.is_empty()
            && self.roads.is_empty()
            && self.networks.is_empty()
            && self.decals.is_empty()
    }

    /// Road count including network-expanded segments (for summaries).
    pub fn road_count(&self) -> usize {
        self.roads.len()
            + self
                .networks
                .iter()
                .map(|n| n.segments.len())
                .sum::<usize>()
    }
}

/// Registries produced by [`apply_features`].
#[derive(Debug, Clone, Default)]
pub struct FeatureResult {
    /// Water bodies for `avoid-water` / `near-water` / surface queries.
    pub water: Vec<WaterBody>,
    /// Spec de origem paralela a [`FeatureResult::water`] — `(é_lago,
    /// índice_na_spec)`. Um lago/rio degenerado (carve falhou) não entra em
    /// `water`; sem este alinhamento por identidade, o emparelhamento
    /// posicional em `spawn_water` desalinha TODOS os corpos seguintes.
    pub water_specs: Vec<(bool, usize)>,
    /// Carved roads for `isPointOnRoad` / `distanceToRoadAt`.
    pub roads: Vec<RoadPath>,
    /// The declarative specs parallel to [`FeatureResult::roads`] (ribbon
    /// textures, feather, …).
    pub road_specs: Vec<RoadSpec>,
    /// Pads with the auto height resolved (placement anchors).
    pub pads: Vec<ResolvedPad>,
    /// Fusion discs for multi-arm junctions (VibeGame junctions.ts).
    pub road_junctions: Vec<super::roads::RoadJunction>,
    /// Ground decals to render, in declaration order.
    pub decals: Vec<GroundDecalSpec>,
}

/// Applies all features in the canonical order and returns the registries.
pub fn apply_features(grid: &mut BrushGrid, features: &TerrainFeatures) -> FeatureResult {
    let mut result = FeatureResult::default();

    // 0. Plateaus — RAISE the mesa volume first, so a pad authored on top
    //    flattens the plateau surface and water still cuts lower-only.
    for (i, plateau) in features.plateaus.iter().enumerate() {
        carve_plateau(grid, plateau, i);
    }

    // 1. Pads — flatten (cut and fill), resolve auto heights in order.
    for (i, pad) in features.pads.iter().enumerate() {
        let height = grid.flatten_rect(
            pad.at,
            pad.size,
            pad.falloff,
            pad.corner_radius,
            pad.height,
            &format!("pad:{i}"),
        );
        result.pads.push(ResolvedPad {
            at: pad.at,
            size: pad.size,
            falloff: pad.falloff,
            corner_radius: pad.corner_radius,
            height,
        });
    }

    // 2. Water — lakes then rivers, declaration order. Rivers recebem os
    // lagos JÁ carvados: a confluência sobe as estações à cota do espelho.
    for (i, lake) in features.lakes.iter().enumerate() {
        if let Some(body) = carve_lake(grid, lake, i) {
            result.water.push(body);
            result.water_specs.push((true, i));
        }
    }
    for (i, river) in features.rivers.iter().enumerate() {
        // Cópia dos corpos de lago (poucos) — a confluência usa as cotas do
        // registry, e `result.water` é mutado dentro do loop.
        let lake_bodies: Vec<WaterBody> = result
            .water
            .iter()
            .filter(|b| b.kind == WaterKind::Lake)
            .cloned()
            .collect();
        // Pre-pass cachoeiras de parede: cruza o path do rio com os cliffs
        // autorados (2D, specs) — as bandas de cliff só existem DEPOIS do
        // carve, portanto as travessias têm de vir daqui como hints.
        let falls = river_cliff_crossings(river, &features.cliffs);
        if let Some(body) = carve_river_with_falls(grid, river, i, &lake_bodies, &falls) {
            result.water.push(body);
            result.water_specs.push((false, i));
        }
    }

    // 2.5 Cliffs — NOT carved any more.
    //
    // A cliff is now a 3D solid in the voxel field
    // (`src/terrain/voxel/cliff.rs`), built by the bootstrap from these same
    // specs. Writing it into the height grid as well would be a second source
    // of truth for the same wall, and the grid version is the one that cannot
    // be vertical.
    //
    // Consequence to know about: cliffs used to carve BETWEEN water and roads
    // precisely so a road survey read the finished wall and its `limit_grade`
    // reacted to it. With the wall out of the grid, a road authored across a
    // cliff no longer sees it. No shipped world does that — `simple-rpg` keeps
    // its cliffs a documented ~30 m clear of every arterial — but a world that
    // tried would drive its ribbon under the rock.

    // 2.6 Cuts — dry trenches. Lower-only (a vala morre na encosta em vez
    //     de aterrar o vale), ANTES das estradas: o survey lê o piso da
    //     vala — a estrada entra nela e as paredes (bandas voxel do
    //     bootstrap) ficam-lhe dos lados. As PAREDES são sólidos voxel:
    //     como os cliffs, o survey não as vê (ver a nota 2.5) — mantenha
    //     estradas cruzadas afastadas das margens; o audit avisa.
    for (i, cut) in features.cuts.iter().enumerate() {
        carve_cut(grid, cut, i);
    }

    // 3. Roads — expand networks, plain roads first, bridges last.
    let mut specs: Vec<RoadSpec> = features.roads.clone();
    for network in &features.networks {
        specs.extend(network.expand());
    }
    specs.sort_by_key(|r| matches!(r.profile, RoadProfile::Bridge));
    let pad_cores: Vec<(Vec2, Vec2, f32)> = result
        .pads
        .iter()
        .map(|p| (p.at, p.size * 0.5, p.height))
        .collect();
    let guards = RoadGuards {
        pad_cores: &pad_cores,
        water: &result.water,
    };
    for (i, spec) in specs.iter().enumerate() {
        if let Some(path) = carve_road(grid, spec, i, &guards) {
            result.roads.push(path);
            result.road_specs.push(spec.clone());
        }
    }
    // 4. Ground decals — visual only, so they run after every carve and read
    //    the final heightfield.
    result.decals = features.decals.clone();

    // 5. Fusion discs, minus the ones a decal already hides. Stacking a disc
    //    under a plaza floor put two alpha-blended cobble layers at almost
    //    the same height over the same world-space UVs: the feather bands
    //    double-blend into visible seams, and the pair z-fights. The plaza
    //    floor covers the crossing on its own, so the disc is redundant
    //    there — the discs that survive are the ones out on open ground.
    for network in &features.networks {
        for junction in network.junction_points() {
            if result.decals.iter().any(|d| d.covers(junction.at)) {
                continue;
            }
            result.road_junctions.push(junction);
        }
    }

    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::decal::GroundDecalSpec;
    use crate::terrain::roads::{RoadNetworkSpec, RoadProfile, SegmentSpec, WaySpec};

    /// 128x128 grid, 128 m world (XZ in [-64, 64]), rolling hill.
    fn test_grid() -> BrushGrid {
        let mut grid =
            BrushGrid::new(vec![0; 128 * 128], 128, 128, 128.0, 50.0, 0.0).expect("grid");
        grid.begin_stroke("base");
        for z in 0..128 {
            for x in 0..128 {
                let p = grid.cell_center(x, z);
                let h = 6.0 + 10.0 * (-(p.y * p.y) / 500.0).exp() + 2.0 * (p.x * 0.02).sin();
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        grid
    }

    fn sample_features() -> TerrainFeatures {
        TerrainFeatures {
            decals: Vec::new(),
            caves: Vec::new(),
            arches: Vec::new(),
            bridges: Vec::new(),
            rock_fields: Vec::new(),
            plateaus: Vec::new(),
            cuts: Vec::new(),
            cliffs: Vec::new(),
            pads: vec![TerrainPadSpec {
                at: Vec2::ZERO,
                size: Vec2::splat(24.0),
                falloff: 8.0,
                corner_radius: 4.0,
                height: None,
            }],
            lakes: vec![LakeSpec {
                at: Vec2::new(-40.0, 40.0),
                radius: 10.0,
                ..LakeSpec::default()
            }],
            rivers: vec![RiverSpec {
                path: vec![Vec2::new(10.0, -50.0), Vec2::new(50.0, -50.0)],
                width: 6.0,
                ..RiverSpec::default()
            }],
            roads: vec![RoadSpec {
                name: Some("trail".into()),
                path: vec![Vec2::new(-40.0, 32.0), Vec2::new(40.0, 32.0)],
                width: 4.0,
                ..RoadSpec::default()
            }],
            networks: vec![RoadNetworkSpec {
                name: Some("net".into()),
                ways: vec![
                    WaySpec {
                        id: "a".into(),
                        at: Vec2::new(-20.0, 20.0),
                        width: None,
                    },
                    WaySpec {
                        id: "b".into(),
                        at: Vec2::new(20.0, 20.0),
                        width: None,
                    },
                ],
                segments: vec![SegmentSpec {
                    a: "a".into(),
                    b: "b".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                }],
                ..RoadNetworkSpec::default()
            }],
        }
    }

    #[test]
    fn test_apply_features_full_pipeline() {
        let mut grid = test_grid();
        let features = sample_features();
        let result = apply_features(&mut grid, &features);
        assert_eq!(result.pads.len(), 1);
        assert_eq!(result.water.len(), 2, "lake + river registered");
        assert_eq!(result.roads.len(), 2, "road + network segment registered");
        // Pad core is flat at the resolved height.
        let pad = &result.pads[0];
        assert!(
            (grid.sample(pad.at.x, pad.at.y) - pad.height).abs() < 0.05,
            "pad core flat"
        );
        // Lake carved below its mirror.
        let lake = &result.water[0];
        assert!(
            grid.sample(lake.at.x, lake.at.y) < lake.water_y,
            "bowl below the mirror: {} vs {}",
            grid.sample(lake.at.x, lake.at.y),
            lake.water_y
        );
        // Road beds exist and are registered.
        assert!(result.roads[0].is_on_road(Vec2::new(0.0, 32.0)));
        assert!(result.roads[1].is_on_road(Vec2::new(0.0, 20.0)));
        // Revision moved.
        assert!(grid.revision() > 0);
    }

    #[test]
    fn test_empty_features_is_a_noop() {
        let mut grid = test_grid();
        let before = grid.raw().to_vec();
        let revision = grid.revision();
        let result = apply_features(&mut grid, &TerrainFeatures::default());
        assert!(result.water.is_empty() && result.roads.is_empty() && result.pads.is_empty());
        assert_eq!(grid.raw(), before);
        assert_eq!(grid.revision(), revision);
        assert!(TerrainFeatures::default().is_empty());
        assert_eq!(sample_features().road_count(), 2);
    }

    #[test]
    fn test_road_never_fills_the_lake_in_the_pipeline() {
        // The lake sits exactly on the road path; the mutual guard keeps the
        // bowl carved.
        let mut grid = test_grid();
        let mut features = TerrainFeatures::default();
        features.lakes.push(LakeSpec {
            at: Vec2::new(0.0, 32.0),
            radius: 14.0,
            ..LakeSpec::default()
        });
        features.roads.push(RoadSpec {
            path: vec![Vec2::new(-30.0, 32.0), Vec2::new(50.0, 32.0)],
            width: 4.0,
            ..RoadSpec::default()
        });
        let result = apply_features(&mut grid, &features);
        let lake = &result.water[0];
        let floor = grid.sample(lake.at.x, lake.at.y);
        assert!(floor < lake.water_y, "bowl still carved: {floor}");
        // And the road still registers through the guard zone.
        assert!(result.roads[0].is_on_road(Vec2::new(-20.0, 32.0)));
    }

    #[test]
    fn test_bridges_are_carved_last() {
        // A bridge segment over the river: the banks exist before the bridge
        // ribbon is queried, and the bridge never carves the channel.
        let mut grid = test_grid();
        let mut features = TerrainFeatures::default();
        features.rivers.push(RiverSpec {
            path: vec![Vec2::new(0.0, -40.0), Vec2::new(0.0, 40.0)],
            width: 8.0,
            ..RiverSpec::default()
        });
        features.networks.push(RoadNetworkSpec {
            ways: vec![
                WaySpec {
                    id: "w".into(),
                    at: Vec2::new(-30.0, 0.0),
                    width: None,
                },
                WaySpec {
                    id: "e".into(),
                    at: Vec2::new(30.0, 0.0),
                    width: None,
                },
            ],
            segments: vec![SegmentSpec {
                a: "w".into(),
                b: "e".into(),
                via: Vec::new(),
                width: None,
                profile: Some(RoadProfile::Bridge),
            }],
            ..RoadNetworkSpec::default()
        });
        let result = apply_features(&mut grid, &features);
        let bridge = &result.roads[0];
        assert!(bridge.bridge, "bridge sorted last and flagged");
        assert!(bridge.deck_y.is_some());
        let river = &result.water[0];
        // Channel survived the bridge.
        let floor = grid.sample(0.0, 0.0);
        assert!(floor < river.water_y, "channel intact under the bridge");
    }

    /// A plaza floor and the network's own fusion disc used to stack at the
    /// same crossing: two alpha-blended cobble layers a few centimetres
    /// apart over identical world-space UVs, so their feather bands
    /// double-blended into seams and the pair z-fought. The floor already
    /// hides the crossing, so the disc under it must be dropped.
    #[test]
    fn test_decal_suppresses_the_junction_disc_it_hides() {
        let net = RoadNetworkSpec {
            default_width: 4.0,
            ways: vec![
                WaySpec {
                    id: "hub".into(),
                    at: Vec2::ZERO,
                    width: None,
                },
                WaySpec {
                    id: "n".into(),
                    at: Vec2::new(0.0, 24.0),
                    width: None,
                },
                WaySpec {
                    id: "e".into(),
                    at: Vec2::new(24.0, 0.0),
                    width: None,
                },
                WaySpec {
                    id: "s".into(),
                    at: Vec2::new(0.0, -24.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "hub".into(),
                    b: "n".into(),
                    ..SegmentSpec::default()
                },
                SegmentSpec {
                    a: "hub".into(),
                    b: "e".into(),
                    ..SegmentSpec::default()
                },
                SegmentSpec {
                    a: "hub".into(),
                    b: "s".into(),
                    ..SegmentSpec::default()
                },
            ],
            ..RoadNetworkSpec::default()
        };
        let floor = GroundDecalSpec {
            at: Vec2::ZERO,
            half_extent: Vec2::splat(10.5),
            ..GroundDecalSpec::default()
        };

        let mut grid = test_grid();
        let bare = apply_features(
            &mut grid,
            &TerrainFeatures {
                networks: vec![net.clone()],
                ..TerrainFeatures::default()
            },
        );
        assert_eq!(bare.road_junctions.len(), 1, "sem decal o disco fica");

        let mut grid = test_grid();
        let covered = apply_features(
            &mut grid,
            &TerrainFeatures {
                networks: vec![net],
                decals: vec![floor],
                ..TerrainFeatures::default()
            },
        );
        assert!(
            covered.road_junctions.is_empty(),
            "o chão da praça já cobre o cruzamento: {:?}",
            covered.road_junctions
        );
        assert_eq!(
            covered.decals.len(),
            1,
            "o decal continua a ser renderizado"
        );
    }

    /// A decal far from the crossing must not swallow its disc.
    #[test]
    fn test_distant_decal_leaves_the_disc_alone() {
        let net = RoadNetworkSpec {
            default_width: 4.0,
            ways: vec![
                WaySpec {
                    id: "hub".into(),
                    at: Vec2::ZERO,
                    width: None,
                },
                WaySpec {
                    id: "n".into(),
                    at: Vec2::new(0.0, 24.0),
                    width: None,
                },
                WaySpec {
                    id: "e".into(),
                    at: Vec2::new(24.0, 0.0),
                    width: None,
                },
                WaySpec {
                    id: "s".into(),
                    at: Vec2::new(0.0, -24.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "hub".into(),
                    b: "n".into(),
                    ..SegmentSpec::default()
                },
                SegmentSpec {
                    a: "hub".into(),
                    b: "e".into(),
                    ..SegmentSpec::default()
                },
                SegmentSpec {
                    a: "hub".into(),
                    b: "s".into(),
                    ..SegmentSpec::default()
                },
            ],
            ..RoadNetworkSpec::default()
        };
        let mut grid = test_grid();
        let result = apply_features(
            &mut grid,
            &TerrainFeatures {
                networks: vec![net],
                decals: vec![GroundDecalSpec {
                    at: Vec2::new(40.0, 40.0),
                    half_extent: Vec2::splat(6.0),
                    ..GroundDecalSpec::default()
                }],
                ..TerrainFeatures::default()
            },
        );
        assert_eq!(result.road_junctions.len(), 1);
    }

    /// Decals are visual only — they must never move the heightfield.
    #[test]
    fn test_decals_never_carve_the_terrain() {
        let mut grid = test_grid();
        let before = grid.raw().to_vec();
        let result = apply_features(
            &mut grid,
            &TerrainFeatures {
                decals: vec![GroundDecalSpec {
                    at: Vec2::ZERO,
                    half_extent: Vec2::splat(12.0),
                    ..GroundDecalSpec::default()
                }],
                ..TerrainFeatures::default()
            },
        );
        assert_eq!(grid.raw(), before.as_slice(), "decals são só visuais");
        assert_eq!(result.decals.len(), 1);
    }

    #[test]
    fn test_is_empty_accounts_for_decals() {
        assert!(TerrainFeatures::default().is_empty());
        assert!(
            !TerrainFeatures {
                decals: vec![GroundDecalSpec::default()],
                ..TerrainFeatures::default()
            }
            .is_empty()
        );
    }
}
