import { defineComponent, Types } from 'bitecs';

/** Marca zona de navegação (recipe `nav-mesh`). */
export const NavMeshSurface = defineComponent({
  loaded: Types.ui8,
  /** 0 = use fallback PlaneGeometry zone, 1 = build from scene collidable meshes. */
  buildFromScene: Types.ui8,
});

/** Agente que segue caminho no navmesh. */
export const NavMeshAgent = defineComponent({
  targetX: Types.f32,
  targetY: Types.f32,
  targetZ: Types.f32,
  speed: Types.f32,
  tolerance: Types.f32,
  /** 0 idle, 1 moving, 2 arrived, 3 stuck */
  status: Types.ui8,
});
