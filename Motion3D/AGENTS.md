# Motion3D — agent notes

Text-to-motion (**SprongMotion-14M**, CPU). Runtime C11 vendorizado + GGUF de
33 MB; sem torch, sem GPU, sem vramd. GLB export via **bpy** (padrões Animator3D /
Text3D Shared).

## Hard rules

- Happy path: `motion3d generate` → NPZ (`joints` + `offsets` + códigos) →
  `motion3d apply-rigged` (perfil `sprong22`). Multi-clip:
  `motion3d pack-rigged … -m name=path.npz` (export ACTIONS).
- **Nunca** reintroduzir torch, `vramd` backend, `--sdnq-preset` ou
  `--model lite|full` — a inferência é o runtime C (`motion3d/sprong/`).
- O esqueleto é o do modelo: 22 joints (`pelvis, l_hip, …, l_wrist`), topologia
  igual ao HML22 em índices, nomes diferentes. `motion3d/skeleton.py` é o
  contrato; o perfil de retarget é `sprong22.yaml`.
- Vendor: `src/motion3d/vendor/sprongmotion/` (v1.1.0, Apache-2.0) — **não
  editar**; ver `VENDORED.md`. Compila para a cache no primeiro uso
  (`sprong/runtime.py`).
- Pesos: HF `SegerEnd/SprongMotion-14M` → cache HF; SHA-256 verificado
  (`weights.py`). `SPRONG_MODEL` aponta para um `.gguf` local.
- O bind do pelvis em `export_joints_glb` usa a altura de pé do esqueleto.
  A diferença até à altura do primeiro frame também entra na animação, para
  que um personagem já sentado comece à altura do assento após o retarget.
- O neutral de braços (`NEUTRAL_AIM`) só vale para clips de hang (walk/idle).
  `export-glb` **e** `apply-rigged` filtram-no com `--arm-neutral auto`: a
  decisão tem de ser medida nos joints **estabilizados** que o bake amostra
  (`in_place`), nunca nos crus — senão um gesto que vira durante o clip leva o
  hang aplicado e as mãos ficam dentro do tronco.
- Depois de editar código: nada para reiniciar (in-process). `motion3d doctor`.

## Qualidade

- O modelo varia muito entre seeds: o CLI gera `--attempts` (default 3) e
  escolhe pelo score mecânico (`quality.py`). Não remover sem substituto.
- Opções nativas preferidas a pós-processamento: `--loop`, `--foot-lock`,
  `--in-place`.

## Docs

[`docs/findings/MOTION3D_FINDINGS.md`](../docs/findings/MOTION3D_FINDINGS.md)
