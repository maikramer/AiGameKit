"""Templates YAML/CSV para gameassets init."""

GAME_YAML = """# Perfil do jogo — edita os campos e escolhe style_preset
# (ver GameAssets/src/gameassets/data/presets.yaml)

title: "Meu Jogo"
genre: "roguelike de ação"
tone: "colorido e acessível"

# Um dos presets embutidos: lowpoly, pixel_art, painterly, realistic_stylized
# Se usares um preset só no teu presets-local.yaml, o batch DEVE incluir --presets-local ficheiro.yaml
style_preset: lowpoly

# Palavras ou frases a evitar (além do preset)
negative_keywords:
  - "gore explícito"
  - "logotipos de marcas"

# Raiz dos assets no projeto do jogo (defeito: diretório atual → ./images e ./meshes, sem pasta outputs/)
output_dir: .
# split: images_subdir/ + meshes_subdir/ (cada id pode ter subpastas, ex. Cat/item.png)
# flat: PNG e GLB na mesma pasta por categoria — id recomendado "Categoria/nome" → output_dir/Categoria/nome.png e .glb
path_layout: split
images_subdir: images
meshes_subdir: meshes
image_ext: png

# Opcional: seed base para reprodutibilidade (inteiro)
# seed_base: 42

# Fonte de imagens 2D: text2d (defeito, FLUX Klein) ou texture2d (texturas seamless HF).
# image_source: text2d

# Text2D em batch: low_vram=true recomendado em GPUs ~6 GB (CPU offload do FLUX).
# Se der OOM, fecha o Godot/outros que usem a GPU antes do batch; em último caso cpu: true (lento).
text2d:
  low_vram: true
  width: 768
  height: 768

# Texture2D (HF API) — descomenta image_source + bloco abaixo para seamless em vez de Text2D.
# Combo PBR: materialize: true gera mapas (normal, height, metallic) com o CLI materialize.
# image_source: texture2d
# texture2d:
#   width: 1024
#   height: 1024
#   materialize: true
#   materialize_maps_subdir: pbr_maps

# Skymap2D (HF API) — descomenta image_source + bloco abaixo para skymaps equirectangular 360°.
# image_source: skymap2d
# skymap2d:
#   width: 2048
#   height: 1024

# Text2Sound com generate_audio no CSV: áudio por linha (Stable Audio Open); audio_subdir + bloco opcional.
# audio_subdir: audio
# text2sound:
#   duration: 10
#   steps: 100
#   cfg_scale: 4.5
#   audio_format: wav

# Text3D with --with-3d: preset fast balances time/VRAM.
# texture=true => paint3d texture after shape (Hunyuan3D-Paint 2.1; GLB já PBR).
# low_vram=true => Hunyuan shape on CPU (much worse quality).
# phased_batch: true (with texture) => batch em 2 passos (shape → paint).
# Requires PAINT3D_BIN or paint3d in PATH when texture is true.
# PBR a partir de PNG (não GLB): texture2d.materialize — ver bloco texture2d.
text3d:
  preset: fast
  low_vram: false
  texture: true
  # feet = base do modelo em Y=0 (recomendado para a maioria dos props/personagens).
  # export_origin: center  # só para excepções (pivô no centro da caixa)
  export_origin: feet
  paint_preserve_origin: true
  # phased_batch: false

# Rigging3D após Text3D (--with-rig + coluna generate_rig=true no CSV). Requer bash, GPU CUDA; ver Rigging3D/README.md.
# rigging3d:
#   output_suffix: "_rigged"

# Part3D (Hunyuan3D-Part) após Text3D: --with-parts + coluna generate_parts=true. Requer PART3D_BIN ou part3d no PATH.
# part3d:
#   steps: 50
#   octree_resolution: 256
#   segment_only: false
#   parts_suffix: "_parts"
#   segmented_suffix: "_segmented"

# LOD (3 níveis de detalhe via text3d lod): pipeline "lod" no manifest.
# lod:
#   lod1_ratio: 0.42
#   lod2_ratio: 0.14
#   min_faces_lod1: 500
#   min_faces_lod2: 150
"""

MANIFEST_CSV = """id,idea,kind,generate_3d,generate_audio,generate_rig,generate_animate,generate_parts,image_source
chest_01,"baú de madeira com ferrolhos dourados",prop,false,false,false,false,false,
hero_sword,"espada longa com gema azul no punho",prop,true,false,false,false,false,
forest_bg,"floresta densa ao entardecer",environment,false,false,false,false,false,
sky_sunset,"pôr do sol sobre montanhas, nuvens douradas",skymap,false,false,false,false,false,skymap2d
"""

MANIFEST_YAML = """# Manifest — assets do jogo (gameassets batch)
# pipeline: combinações de [3d, audio, rig, animate, parts]

assets:
  - id: chest_01
    idea: "baú de madeira com ferrolhos dourados"
    kind: prop
    pipeline: []

  - id: hero_sword
    idea: "espada longa com gema azul no punho"
    kind: prop
    pipeline: [3d, lod]

  - id: forest_bg
    idea: "floresta densa ao entardecer"
    kind: environment
    pipeline: []
"""
