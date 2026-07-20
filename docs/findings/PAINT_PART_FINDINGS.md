# Paint3D / Part3D — descobertas

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).

---

## Paint3D (Hunyuan3D-Paint 2.1)

### O que o modelo faz

- Multi-view diffusion → bake PBR no GLB (baseColor, normal, ORM).
- Defaults alinhados demo: várias vistas @ ~640 px, **`bake_exp=6`** (menos
  ghosting entre vistas).
- Pós-bake: **bilateral** edge-preserving nas UVs (`texture_smooth`) — remove
  seams sem blur global agressivo.
- Upscale opcional Real-ESRGAN.

### VRAM / UMS

| Modo | Comportamento |
|------|----------------|
| FP16 full | Pico alto (~8 GiB ordem) — 6 GB recusa se UMS não vir quant |
| `memory_efficient=True` | SDNQ uint8 + CFG chunking + ref-UNet offload |
| Payload UMS | **Obrigatório** `memory_efficient` e/ou `sdnq_preset` (`paint3d/ums_payload.py`) |

Hardware profiles: `paint3d/hardware.py` — GPUs pequenas forçam mem-eff.

Runtime budget pós-load: clampa `max_num_view` / `view_resolution` à VRAM livre
(`PAINT3D_AUTO_VRAM_BUDGET=0` desliga).

### Knobs frequentes

| Knob | Nota |
|------|------|
| `bake_exp` | 6 default — nítidez transições vista |
| `--smooth-passes` | Passes bilateral |
| `--quality` / category | Soft via QualityEngine |
| `--gpu-ids` | Multi-GPU (env `PAINT3D_MULTI_GPU` obsoleto) |

### Armadilhas

- Shape Omni clipado / oco → paint não “cura” geometria; só textura.
- Pedir paint sem UMS quant → fila recusa ou OOM.
- GLB painted ≠ LOD0 final se há bake-master / rig / animate a seguir.

---

## Part3D (P3-SAM + X-Part)

- Decomposição semântica de mesh (partes).
- Stack SDNQ; mem-eff em 6 GB.
- Preferir **shape** (ou LOD limpo) como input — painted GLB pode atrapalhar
  segmentação (bench kernel opts: evitar paint GLB como input Part).
- Repair pós-decode: perfil `part_decode` em `gamedev_shared.mesh_repair`.
- UMS backend `part3d` — mesmo contrato peak/quant.

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-19 | Extraído de ops batch + defaults Paint; payload UMS quant |
