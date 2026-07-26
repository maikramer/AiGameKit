# GLB finish — KTX2 + meshopt

Happy path para compressão de entregáveis GLB (LOD0/1/2).  
Ops / armadilhas: [`findings/MESH_PIPELINE_FINDINGS.md`](findings/MESH_PIPELINE_FINDINGS.md#compressão-entregável).  
Missão: [`mission/02-ease-over-knobs.md`](mission/02-ease-over-knobs.md).

## O quê

Pipeline canónico em `text3d.utils.gltf_finish.gltf_transform_finish`:

```
[ktxdecompress se input já KTX2]
  → shade-smooth + NORMAL + tangents (bpy MikkTSpace)
  → dedup → prune --keep-attributes
  → uastc (KTX2) → meshopt
```

| Passo | Extensão / efeito | Backend |
|-------|-------------------|---------|
| ktxdecompress | KTX2 → PNG (só se input já KTX2) | `@gltf-transform/cli` |
| shade + tangents | `NORMAL` + `TANGENT`; anti V/Tri≈3 | bpy (`smooth_shade_scene`) |
| dedup / prune | buffers/nós órfãos; **keep vertex attrs** | `@gltf-transform/cli` |
| uastc | `KHR_texture_basisu`, `image/ktx2` | gltf-transform **+** CLI `ktx` |
| meshopt | `EXT_meshopt_compression` (+ quantização) | bpy 5.2+ se **sem** KTX2 no input; senão gltf-transform |

Defaults: **KTX2 ON**, **meshopt ON**, **tangents ON**. Collision: só dedup/prune
(sem KTX2/meshopt).

## Dependências (verificar primeiro)

```bash
text3d doctor
```

| Dep | Para quê | Como obter |
|-----|----------|------------|
| `npx` + `@gltf-transform/cli` | dedup/prune/uastc/meshopt fallback | Node.js no PATH |
| `ktx` (KTX-Software) | **obrigatório** para UASTC → KTX2 | `./install.sh text3d` (extras) ou [releases](https://github.com/KhronosGroup/KTX-Software/releases) → `~/.local/opt/KTX-Software` + `~/.local/bin/ktx` |
| `libmeshoptimizer.so` + bpy 5.2 | meshopt nativo (antes do uastc) | `libmeshoptimizer-dev` (Debian/Ubuntu) |

**Armadilha clássica:** doctor/npx OK mas `ktx` ausente → meshopt pode aplicar via bpy, **KTX2 fica PNG/JPEG**. Regras `lod0.yaml` (`texture_format: ktx2`) falham.

`gltf_finish` prepende `~/.local/bin` e `~/.local/opt/KTX-Software/bin` ao PATH do processo.

## Comandos

```bash
# Re-comprimir um GLB (in-place)
text3d finish hero_lod0.glb

# Saída separada / sem tangents
text3d finish hero_lod0.glb -o hero_opt.glb --no-tangents

# Opt-out pontual
text3d finish hero_lod0.glb --no-ktx2
text3d finish hero_lod0.glb --no-meshopt

# bake-master / lod (defaults alinhados: meshopt+ktx2 ON)
text3d bake-master painted.glb -o lod0.glb --target-faces 12000
text3d lod painted.glb -o ./out --basename prop --painted-mesh painted.glb \
  --target-faces 12000 --finish-lod0
```

## GameAssets (master Round 3)

| Path | Compressão |
|------|------------|
| Estático | `text3d lod … --finish-lod0` (CLI meshopt default ON) |
| Rigged / animated | `lod --no-meshopt` (ladder sem compressão) → `_finish_lod_with_rollback` com **uastc+meshopt**; se perder skins/clips → restaura pré-finish |
| Collision | dedup+prune only |

Validação: `gamedev-lab check glb … --category lod0` exige `texture_format: ktx2` + `compression: meshopt`.

## Runtime (VibeGame)

`extras/gltf-bridge` liga `MeshoptDecoder` + `KTX2Loader` (basis transcoder). Sem isso, GLBs comprimidos falham no browser.

## Probe rápido

```bash
python3 - <<'PY'
from pathlib import Path
import struct, json, sys
p = Path(sys.argv[1])
d = p.read_bytes()
jlen = struct.unpack_from("<I", d, 12)[0]
j = json.loads(d[20 : 20 + jlen])
ext = j.get("extensionsUsed") or []
mimes = {i.get("mimeType") for i in j.get("images") or [] if i.get("mimeType")}
attrs=set()
for mesh in j.get("meshes") or []:
    for prim in mesh.get("primitives") or []:
        attrs.update((prim.get("attributes") or {}).keys())
print("meshopt", "EXT_meshopt_compression" in ext)
print("ktx2", "image/ktx2" in mimes)
print("NORMAL", "NORMAL" in attrs, "TANGENT", "TANGENT" in attrs)
print("size_kib", p.stat().st_size // 1024)
PY
# uso: python3 script.py public/assets/meshes/hero_lod0.glb
```

## Aprendizados (2026-07)

1. **Só npx ≠ KTX2.** `@gltf-transform/cli uastc` shell-out para `ktx`; sem binário, warning e GLB fica PNG/JPEG.
2. **Ordem:** uastc → input do meshopt já tem KTX2 → bpy meshopt **recusado** (risco re-encode) → meshopt via gltf-transform.
3. **Disk vs GPU:** UASTC pode **aumentar** ficheiros já JPEG pequenos; em PNG grandes (humanoids/edifícios) costuma cortar muito. Benefício principal = upload/memória GPU, não só bytes em disco.
4. **Batch simple-rpg (2026-07-23):** 162 LODs → ~1139 MiB → ~596 MiB (−542 MiB); 0 rollbacks de skin/anim.
5. **Defaults desalinhados eram regressão:** `apply_meshopt=False` em `gltf_transform_finish` / bake-master + finish GameAssets sem flags → regras YAML falhavam embora deps existissem.
6. **`prune` apaga TANGENT** sem `--keep-attributes true` (gltf-transform 4.x). Finish passa a flag; sem ela o normal map fica “folha amassada”.
7. **Re-finish em KTX2 precisa `ktxdecompress`.** bpy 5.x sem `KHR_texture_basisu` falha o import → shade/tangents no-op (V/Tri fica 3, sem T). Ordem: decompress → bpy → uastc de novo.
8. **`text3d finish` também repara N+T** em LODs legados (não só comprime). Probe: `glb_extract_meta` → `has_tangents` + `v_per_tri`.

## Anti-padrões

- Ensinar `kill` / pkill GPU por causa de finish (CPU/Node — não UMS).
- Comprimir collision com meshopt/KTX2 (physics precisa geometria simples).
- Assumir “doctor verde = KTX2” sem linha `ktx (KTX-Software)`.
- Regenerar shape/paint só para comprimir — usar `text3d finish`.
- Correr finish bpy directo em KTX2 sem `ktxdecompress` (tangents/normais não aplicam).
- `prune` sem `--keep-attributes` em qualquer script manual gltf-transform.
