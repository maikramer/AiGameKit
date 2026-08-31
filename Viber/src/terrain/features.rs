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
use super::roads::{RoadGuards, RoadNetworkSpec, RoadPath, RoadProfile, RoadSpec, carve_road};
use super::sampler::ResolvedPad;
use super::spec::TerrainPadSpec;
use super::water::{LakeSpec, RiverSpec, WaterBody, carve_lake, carve_river};

/// All declarative ground features of a world.
#[derive(Debug, Clone, Default)]
pub struct TerrainFeatures {
    pub pads: Vec<TerrainPadSpec>,
    pub lakes: Vec<LakeSpec>,
    pub rivers: Vec<RiverSpec>,
    pub roads: Vec<RoadSpec>,
    pub networks: Vec<RoadNetworkSpec>,
}

impl TerrainFeatures {
    /// No features at all — the runtime can skip the feature pass entirely.
    pub fn is_empty(&self) -> bool {
        self.pads.is_empty()
            && self.lakes.is_empty()
            && self.rivers.is_empty()
            && self.roads.is_empty()
            && self.networks.is_empty()
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
    /// Carved roads for `isPointOnRoad` / `distanceToRoadAt`.
    pub roads: Vec<RoadPath>,
    /// The declarative specs parallel to [`FeatureResult::roads`] (ribbon
    /// textures, feather, …).
    pub road_specs: Vec<RoadSpec>,
    /// Pads with the auto height resolved (placement anchors).
    pub pads: Vec<ResolvedPad>,
}

/// Applies all features in the canonical order and returns the registries.
pub fn apply_features(grid: &mut BrushGrid, features: &TerrainFeatures) -> FeatureResult {
    let mut result = FeatureResult::default();

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

    // 2. Water — lakes then rivers, declaration order (lower-only).
    for (i, lake) in features.lakes.iter().enumerate() {
        if let Some(body) = carve_lake(grid, lake, i) {
            result.water.push(body);
        }
    }
    for (i, river) in features.rivers.iter().enumerate() {
        if let Some(body) = carve_river(grid, river, i) {
            result.water.push(body);
        }
    }

    // 3. Roads — expand networks, plain roads first, bridges last.
    let mut specs: Vec<RoadSpec> = features.roads.clone();
    for network in &features.networks {
        specs.extend(network.expand());
    }
    specs.sort_by_key(|r| matches!(r.profile, RoadProfile::Bridge));
    let pad_cores: Vec<(Vec2, Vec2)> = result.pads.iter().map(|p| (p.at, p.size * 0.5)).collect();
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

    result
}

#[cfg(test)]
mod tests {
    use super::*;
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
}
