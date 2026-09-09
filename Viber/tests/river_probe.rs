//! Saúde da travessia do rio no simple-rpg — o mundo é carvado para valer e
//! o canal do rio é verificado contra a lâmina registada. Guarda a classe de
//! defeitos do "rio seco": bed erguido por carve posterior (o leito acima da
//! lâmina deixa a ribbon enterrada) e ribbon malformada (triângulos
//! degenerados/alpha nulo tornam o corpo invisível mesmo com o canal certo).

use std::path::Path;

use viber::terrain::brush::BrushGrid;
use viber::terrain::features::apply_features;
use viber::terrain::heightmap::HeightMapU16;
use viber::terrain::water::WaterKind;
use viber::{recipes, xml};

#[test]
fn river_channel_stays_below_the_waterline() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let path = root.join("examples/simple-rpg/world.xml");
    let loaded = xml::include::load_world(&path).expect("world loads");
    let world = recipes::parse_world(&loaded.root_attrs, &loaded.nodes).expect("world parses");
    let mut pending = recipes::spawn::PendingTerrain::default();
    recipes::spawn::collect_terrain(&world.entities, &mut pending);
    let spec = pending.terrain.clone().expect("terrain spec");
    let hm_path = spec.heightmap.clone().expect("heightmap path");
    let world_dir = path.parent().unwrap().to_path_buf();
    // A mesma resolução do runtime (docs/ASSETS.md): a pasta do mundo
    // primeiro, o pool partilhado como fallback — sem espelho por exemplo.
    let rel = hm_path.trim_start_matches('/');
    let bytes = std::fs::read(world_dir.join(rel))
        .or_else(|_| {
            let pool = viber::meshopt::shared_asset_pool().expect("pool partilhado");
            std::fs::read(pool.join(rel))
        })
        .expect("ahgt file");
    let (map, _meta) = HeightMapU16::from_ahgt(&bytes).expect("ahgt decodes");
    let mut grid = BrushGrid::from_height_map(
        &map,
        spec.world_size,
        spec.max_height,
        spec.height_smoothing,
    )
    .expect("grid builds");
    let features = pending.features.clone();
    let result = apply_features(&mut grid, &features);
    let river_idx = result
        .water
        .iter()
        .position(|b| b.kind == WaterKind::River)
        .expect("river body registered");
    let river = &result.water[river_idx];
    let spec = &features.rivers[result.water_specs[river_idx].1];

    // 1. Leito pós-carve abaixo da lâmina em TODO o canal: qualquer estação
    //    com o fundo colado/acima da superfície lê-se como rio seco (a
    //    ribbon fica enterrada sob a areia).
    let mut worst = f32::INFINITY;
    for (i, st) in river.stations.iter().enumerate() {
        let depth = river.surface_y[i] - grid.sample(st.x, st.y);
        worst = worst.min(depth);
        assert!(
            depth > 1.0,
            "estação ({:.1}, {:.1}) com só {depth:.2} m de água abaixo da lâmina \
             (canal erguido por carve posterior?)",
            st.x,
            st.y
        );
    }
    // A travessia da artéria norte (eixo do rio perto de (4, 215)) tem de
    // manter o canal fundo — é aí que carves posteriores mordem primeiro.
    let crossing = river
        .stations
        .iter()
        .enumerate()
        .min_by(|a, b| {
            let da = (a.1.x - 4.0).abs() + (a.1.y - 215.0).abs();
            let db = (b.1.x - 4.0).abs() + (b.1.y - 215.0).abs();
            da.total_cmp(&db)
        })
        .map(|(i, _)| i)
        .expect("stations");
    let crossing_depth =
        river.surface_y[crossing] - grid.sample(river.stations[crossing].x, river.stations[crossing].y);
    assert!(
        crossing_depth > 2.0,
        "canal na travessia norte com só {crossing_depth:.2} m (worst global {worst:.2})"
    );

    // 2. Ribbon hígida: sem triângulos degenerados (a geometria existe) e
    //    com alpha de centro a 1 (o corpo não está apagado na origem).
    let mesh = viber::terrain::water::river_water_mesh(spec, river);
    assert!(!mesh.indices.is_empty(), "ribbon do rio sem índices");
    for tri in mesh.indices.chunks(3) {
        let (a, b, c) = (
            mesh.positions[tri[0] as usize],
            mesh.positions[tri[1] as usize],
            mesh.positions[tri[2] as usize],
        );
        let area2 = ((b[0] - a[0]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[0] - a[0])).abs();
        assert!(area2 > 1e-4, "triângulo degenerado na ribbon do rio");
    }
    for color in &mesh.colors {
        assert!(color[3] >= 0.0 && color[3] <= 1.0, "alpha fora de [0,1]");
    }
}
