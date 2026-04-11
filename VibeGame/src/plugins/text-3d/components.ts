import { defineComponent, Types } from 'bitecs';

/**
 * Entidade que carrega um modelo GLTF/GLB gerado pelo Text3D (pipeline Hunyuan).
 * O modelo é tratado como uma mesh estática — sem colisão por padrão.
 */
export const TextMesh = defineComponent({
  /** URL do arquivo .glb/.gltf (Text3D output). */
  url: Types.eid, // reutilizamos eid como placeholder string; a URL real fica no contexto
  /** 1 = carregamento pendente, 0 = carregado ou pronto. */
  pending: Types.ui8,
  /** Escala uniforme aplicada ao modelo importado (default 1). */
  scale: Types.f32,
  /** Cor de tinta (overrides material do GLB). 0 = não aplicar. */
  tint: Types.ui32,
});

export const Text3dContext = defineComponent({
  /** Reserved — armazena estado interno do plugin. */
  _loaded: Types.ui8,
});
