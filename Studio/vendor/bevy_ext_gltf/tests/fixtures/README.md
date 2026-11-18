# Fixtures do decoder GLB

Dois GLBs pequenos do pool do Vael, usados pelos testes quando `VAEL_ROOT`
não está definido:

- `assets/meshes/bomb_lod2.glb` (44 KB): `EXT_meshopt_compression` +
  `KHR_mesh_quantization` + `KHR_texture_basisu` (KTX2).
- `assets/meshes/kart_wheel_lod0.glb` (15 KB): meshopt + quantização, sem
  texturas.

Cobrem o caminho real do `MeshoptAssetReader`/`decode_glb` no CI: reescrita do
buffer, de-quantização para `f32`, lifting de `KHR_texture_basisu` e remoção
das extensões de `extensionsRequired`. O teste de UASTC `RRR` continua a usar
a fixture sintética de `ktx2_with_channel` (o pool atual não tem uma textura
RRR pequena) e o teste de assets `desert/cactus_lod0.glb` continua opt-in por
`VAEL_ROOT`.

Proveniência: `assets/public/assets/meshes/{props/bomb_lod2,vehicles/kart_wheel_lod0}.glb`
do checkout do Vael. Para regenerar, copia os dois ficheiros do pool; não são
editados à mão.
