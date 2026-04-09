import { defineComponent, Types } from 'bitecs';

/** Marca zona de navegação (recipe `nav-mesh`). */
export const NavMesh = defineComponent({
  loaded: Types.ui8,
});

/** Agente que segue caminho no navmesh. */
export const NavAgent = defineComponent({
  targetX: Types.f32,
  targetY: Types.f32,
  targetZ: Types.f32,
  speed: Types.f32,
  tolerance: Types.f32,
  /** 0 idle, 1 moving, 2 arrived, 3 stuck */
  status: Types.ui8,
});
