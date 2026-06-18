# Documentação Text3D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

O Text3D gera meshes 3D a partir de texto em duas fases: **Text2D** (texto → imagem) e **Hunyuan3D-2.1** (imagem → mesh, SDNQ INT4). Ver o [README principal](../README_PT.md) para instalação e licença.

## Índice

- [Instalação](INSTALL.md) — pode estar desatualizado em relação ao fluxo Hunyuan; preferir o README raiz
- [PBR: GLB (Paint 2.1) vs mapas a partir da difusa (Materialize)](PBR_MATERIALIZE.md)
- [API Python](API.md)
- [Paint (custom_rasterizer)](PAINT_SETUP.md)
- [Troubleshooting](TROUBLESHOOTING.md) — conteúdo legado Shap-E
- [Exemplos](EXAMPLES.md) — exemplos antigos; usar API em [API.md](API.md)

## Visão geral

- **Text-to-3D:** `HunyuanTextTo3DGenerator.generate(prompt)`
- **Image-to-3D:** `generate_from_image(...)` (só Hunyuan)
- **Textura (Paint):** mesh + UV + albedo — ver [PAINT_SETUP.md](PAINT_SETUP.md)
- **PBR no GLB:** saída do Hunyuan3D-Paint 2.1 — ver [PBR_MATERIALIZE.md](PBR_MATERIALIZE.md); **Materialize** é para mapas a partir de imagem
- **Pouca VRAM:** hw-auto aplica SDNQ INT4 em GPUs pequenas; descarrega Text2D antes de Hunyuan; `enable_model_cpu_offload` no Hunyuan quando CUDA
