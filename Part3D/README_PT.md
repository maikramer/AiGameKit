# Part3D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

Decomposição semântica de meshes 3D via **Hunyuan3D-Part** (P3-SAM + X-Part): segmentação e geração de partes. Alinhado aos padrões Shared (QualityEngine, SDNQ, hw-auto, `ensure_vram` / exclusive GPU, UMS).

## Requisitos

- Python **3.13**
- GPU NVIDIA com CUDA (~6 GB VRAM com offload + SDNQ)
- Instalação: `./install.sh part3d` (registo em `tools.yaml`)

## Instalação

```bash
./install.sh part3d
# ou:
cd Shared && pip install -e . && cd ../Part3D && pip install -e .
```

## Uso

```bash
part3d --help
part3d decompose mesh.glb -o partes.glb -v
part3d decompose mesh.glb --quality fast
part3d decompose mesh.glb -q int4          # SDNQ int4
part3d decompose mesh.glb --gpu-ids 0,1    # DiT / auxiliares

# UMS (fila + modelo quente) — ver ModelServer/README.md
gamedev-model-server start
part3d decompose mesh.glb -o partes.glb --ums-stream
part3d decompose mesh.glb -o partes.glb --ums-priority batch
part3d decompose mesh.glb -o partes.glb --no-ums   # forçar in-process
```

Flags UMS partilhadas: `--ums-priority`, `--no-ums`, `--ums-stream`.

### Segmentação fina e meshes high-poly

P3-SAM amostra um orçamento fixo de pontos (até 56 mil nos presets atuais).
Uma mesh com centenas de milhares de faces não fornece informação
proporcionalmente maior; rachas, micro-ilhas e projeção serrilhada de labels
podem piorar o resultado.

Para meshes acima de aproximadamente 200 mil faces, recomenda-se um
**remesh isotrópico alinhado de 50–120 mil faces** como proxy:

```bash
text3d remesh chapel_high.glb -o chapel_proxy.glb --target-faces 100000
part3d decompose chapel_high.glb \
  --segmentation-proxy chapel_proxy.glb \
  --fine-parts --quality highest
```

Part3D segmenta o proxy, transfere as labels por superfície mais próxima e
concordância de normais, refina as fronteiras e exporta a topologia high-poly
original. Proxy e alvo devem partilhar espaço e bounds, com tolerância de 5%.
Prefere `text3d remesh`; um LOD decimado só serve se continuar
topologicamente conectado.

Controles finos:

- `--mask-nms-iou`: supressão de máscaras primárias redundantes.
- `--secondary-mask-iou`: overlap mínimo na fusão secundária.
- `--min-cluster-support` e `--min-predicted-iou`: preservação de clusters
  pequenos mas confiáveis.
- `--prompt-batch-size`: micro-batch dos prompts P3-SAM.
- `--detail-levels`: passes hierárquicos locais em regiões grandes.
- `--refine-labels`: fronteiras ancoradas em vincos e componentes conexos.

Hw-auto (default): GPUs &lt;8 GiB → memory-efficient (SDNQ uint8 + CPU offload). Kill-switch: `PART3D_HW_AUTO=0`.

Documentação completa: [README.md](README.md).
