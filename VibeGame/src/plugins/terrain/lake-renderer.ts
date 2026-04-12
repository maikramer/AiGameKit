import type { TerrainData } from './terrain-data-loader';

export function createLakeWaterEntities(data: TerrainData): string {
  const planes = data.lake_planes || [];
  if (planes.length === 0) return '';

  return planes
    .map((plane) => {
      // Water só usa um `size` para malha + colisor; sem isto o default (256) cobre quase o mapa.
      const size = Math.max(plane.size_x, plane.size_z);
      // `water-level` deve coincidir com a superfície em Y (material / submersão / reflexão).
      const y = plane.pos_y;
      return `<Water pos="${plane.pos_x} ${y} ${plane.pos_z}" size="${size}" water-level="${y}" size-x="${plane.size_x}" size-z="${plane.size_z}"></Water>`;
    })
    .join('\n');
}

export function createRiverWaterEntities(data: TerrainData): string {
  const rivers = data.rivers || [];
  if (rivers.length === 0) return '';

  return rivers
    .map((river) => {
      if (river.path.length < 2) return '';
      const [sx, sz] = river.source;
      return `<Water pos="${sx} 0 ${sz}" size-x="2" size-z="2"></Water>`;
    })
    .filter(Boolean)
    .join('\n');
}
