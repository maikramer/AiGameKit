# CLI API Reference

## Sintaxe

```
materialize [OPTIONS] [INPUT] [COMMAND]
```

`INPUT` pode ser um ficheiro, um diretório ou um padrão glob (`png`, `jpg`, `tga`, `exr`). É opcional apenas para `--list-presets`, `--list-maps`, `--generate-completions` e para os subcomandos (`info`, `skill`).

## Argumentos

### Posicionais

| Argumento | Obrigatório | Descrição |
|-----------|-------------|-----------|
| `INPUT` | Sim* | Caminho para a imagem/dir/glob de entrada. *Opcional só para `--list-*`, `--generate-completions` e subcomandos. |

### Flags e Options

| Opção | Curta | Tipo | Padrão | Descrição |
|-------|-------|------|--------|-----------|
| `--output` | `-o` | path | `.` | Diretório de saída |
| `--format` | `-f` | enum | `png` | Formato dos arquivos: `png`, `jpg`, `tga`, `exr` |
| `--preset` | `-p` | enum | `default` | Preset de material (ver seção Presets) |
| `--quality` | `-q` | int | `95` | Qualidade JPEG 1–100 (ignorada noutros formatos); `0` é clampado a `1` |
| `--verbose` | `-v` | flag | — | Progresso, tempos por estágio e info de auto-detect |
| `--quiet` | — | flag | — | Não listar arquivos gerados no sucesso |
| `--include-curvature` | — | flag | — | Gerar `texture_curvature.png` (7.º mapa) |
| `--roughness` | — | flag | — | Gerar `texture_roughness.png` (= `1 - smoothness`) em vez de `texture_smoothness.png` |
| `--normal-format` | — | enum | `opengl` | Eixo Y da normal: `opengl` (Y-up) ou `directx` (Y-down) |
| `--only` | — | lista | — | Whitelist: `height,normal,metallic,smoothness,edge,ao,curvature` |
| `--skip` | — | lista | — | Blacklist (mutuamente exclusivo com `--only`) |
| `--seamless` / `--no-seamless` | — | flag | auto | Forçar wrap ou clamp na amostragem das bordas |
| `--jobs` | — | int | `1` | Paralelismo CPU no batch (GPU fica serial) |
| `--skip-existing` | — | flag | — | Saltar imagens cujo height já existe (resume) |
| `--progress` | — | flag | — | Mostrar `[i/N]` por imagem no batch |
| `--list-presets` | — | flag | — | Listar todos os presets e sair |
| `--list-maps` | — | flag | — | Listar todos os nomes de mapas gerados e sair |
| `--generate-completions` | — | enum | — | Gerar conclusão de shell: `bash`, `zsh`, `fish`, `elvish`, `powershell` |
| `--help` | `-h` | — | — | Mostrar ajuda |
| `--version` | `-V` | — | — | Mostrar versão |

#### Overrides inline (aplicados por cima do preset)

Cada um é `Option<f32>` aplicado por cima do preset selecionado:

`--height-contrast`, `--height-blur`, `--normal-strength`, `--metallic-scale`,
`--metallic-local-variance`, `--smoothness-base`, `--smoothness-boost`,
`--smoothness-roughness`, `--edge-contrast`, `--ao-depth-scale`.

## Subcomandos

### `materialize info <imagem>`

Analisa uma textura sem gerar mapas. Imprime o preset detetado, a pontuação de confiança e o vetor de features completo (luminância, saturação, histograma de matiz, densidade de edges, variância de contraste local, tile MSE, cobertura alpha).

```bash
materialize info texture.png
```

### `materialize skill install`

Instala a skill do Cursor do Materialize CLI no projeto atual, em `.cursor/skills/materialize-cli/`.

```bash
materialize skill install
```

## Enums

### Preset de Material (`--preset`)

19 presets de material mais `auto`. Use `-p` / `--preset` para escolher.

| Valor | Descrição | Uso ideal |
|-------|-----------|-----------|
| `default` | Configurações balanceadas | Qualquer textura (comportamento original) |
| `skin` | Metallic zero, smoothness médio-alto, normals suaves | Pele humana, personagens |
| `floor` | Height pronunciado, AO forte, superfície áspera | Chão, piso, azulejo, terra |
| `metal` | Metallic boost, edges nítidos, polido | Aço, ouro, cobre, alumínio |
| `fabric` | Matte, sem metallic, edges suaves | Tecido, roupa, cortina |
| `wood` | Sem metallic, detalhe de grão moderado | Madeira, móveis, piso de madeira |
| `stone` | Muito áspero, AO profundo, normals fortes | Pedra, rocha |
| `concrete` | Áspero, cinzento, ruído denso de superfície | Betão, cimento |
| `leather` | Granulado, semi-liso, tons quentes | Couro, sofás, selas |
| `marble` | Polido, veios, liso | Mármore, superfícies polidas |
| `sand` | Grão fino, muito áspero | Areia, desertos, praias |
| `foliage` | Orgânico, metallic baixo, detalhe médio | Folhas, erva, plantas |
| `plaster` | Plano, normals suaves | Reboco, stucco, paredes |
| `asphalt` | Escuro, áspero, edges densos | Asfalto, estradas |
| `brick` | Edges nítidos, superfície áspera | Tijolo, alvenaria |
| `ice` | Muito liso, detalhe ligeiro | Gelo, cristais |
| `snow` | Suave, difuso | Neve, superfícies geladas |
| `lava` | Fundida, semi-metálica | Lava, rocha incandescente |
| `water` | Muito lisa, fluída | Água, líquidos |
| `auto` | Análise automática (CPU) + escolha do melhor preset | Textura desconhecida |

### Formato de Saída (`--format`)

| Valor | Extensão | Características |
|-------|----------|-----------------|
| `png` | .png | Lossless, bom geral |
| `jpg` | .jpg | Lossy, compacto |
| `jpeg` | .jpeg | Alias para jpg |
| `tga` | .tga | Uncompressed, games |
| `exr` | .exr | HDR, alta precisão |

## Convenção de Nomenclatura

### Padrão

Input: `texture.png`

Output:
- `texture_height.png`
- `texture_normal.png`
- `texture_metallic.png`
- `texture_smoothness.png` (ou `texture_roughness.png` com `--roughness`)
- `texture_edge.png`
- `texture_ao.png`
- `texture_curvature.png` (só com `--include-curvature`)

## Códigos de Saída

| Código | Significado |
|--------|-------------|
| `0` | Sucesso |
| `1` | Erro genérico |
| `2` | Input file não encontrado |
| `3` | Formato de input não suportado |
| `4` | Erro de GPU (adapter não encontrado) |
| `5` | Erro de I/O (permissão, disco cheio, etc.) |
| `6` | Imagem muito grande para GPU |

## Mensagens de Erro

### Input não encontrado

```
Error: Input file 'texture.png' not found
```

### Formato não suportado

```
Error: Unsupported image format 'texture.bmp'
       Supported formats: png, jpg, tga, exr
```

### GPU não disponível

```
Error: No GPU adapter available
       Ensure you have Vulkan (Linux), Metal (macOS), or DirectX 12 (Windows) drivers installed
```

### Out of memory

```
Error: Image too large (16384x16384 requires 2GB GPU memory)
       Try using a smaller image or enabling tiled processing (--tiled)
```

## Modo Verbose

Quando `-v` ou `--verbose` é usado, o CLI imprime informações de progresso:

```bash
$ materialize texture.png -v
[1/5] Loading texture.png... 2048x2048 RGBA8 (16.7 MB)
[2/5] Initializing GPU... Vulkan adapter: NVIDIA GeForce RTX 3060
[3/5] Processing height map... done (45ms)
[4/5] Processing normal map... done (12ms)
[4/5] Processing metallic map... done (18ms)
[5/5] Saving outputs... done

Output files:
  - texture_height.png (2048x2048, 4.2 MB)
  - texture_normal.png (2048x2048, 12.5 MB)
  - texture_metallic.png (2048x2048, 1.1 MB)

Total time: 89ms
```

## Exemplos Completos

### Exemplo 1: Uso básico

```bash
materialize brick.png
```

Gera na pasta atual os seis mapas (height, normal, metallic, smoothness, edge, ao).

### Exemplo 2: Diretório de saída com preset

```bash
materialize brick.png -o ./materials/brick/ -p floor
```

Gera em `./materials/brick/` os seis mapas otimizados para textura de chão.

### Exemplo 3: Textura de personagem

```bash
materialize character_skin.png -p skin -o ./character/
```

Gera mapas otimizados para pele humana (sem metallic, normals suaves).

### Exemplo 4: Pipeline em script

```bash
#!/bin/bash

INPUT_DIR="./raw_textures"
OUTPUT_DIR="./processed"

mkdir -p "$OUTPUT_DIR"

for file in "$INPUT_DIR"/*.png; do
    name=$(basename "$file" .png)
    echo "Processing $name..."
    materialize "$file" -o "$OUTPUT_DIR/$name/" -p "$name"
done

echo "Done! Processed $(ls "$INPUT_DIR"/*.png | wc -l) textures"
```

### Exemplo 4: Formato específico por tipo de mapa

(Nota: Versão futura, não suportado em MVP)

```bash
# Height em EXR (precisão), outros em PNG
materialize texture.png --height-format=exr --normal-format=png --metallic-format=png
```

### Exemplo 5: Batch nativo

```bash
# Diretório inteiro; --jobs controla o paralelismo CPU, --progress mostra [i/N]
materialize ./textures/ -o ./output/ --jobs 4 --progress

# Retomar após interrupção
materialize ./textures/ -o ./output/ --skip-existing

# Padrão glob
materialize "./textures/bricks/*.png" -o ./output/
```

## Integração com Scripts

### Verificação de sucesso

```bash
if materialize texture.png; then
    echo "Success!"
else
    echo "Failed with exit code $?"
fi
```

### Captura de output

```bash
# Com --quiet não imprime a lista; sem --quiet imprime "Generated:" e os 6 paths
materialize texture.png -o ./out/
materialize texture.png -o ./out/ --quiet
```

## Variáveis de Ambiente

| Variável | Valores | Descrição |
|----------|---------|-----------|
| `MATERIALIZE_GPU_BACKEND` | `vulkan` · `metal` · `dx12` · `gl` · `primary` | Forçar um backend wgpu específico (padrão: `primary`) |
| `MATERIALIZE_LOG` | `error` · `warn` · `info` · `debug` · `trace` | Nível de log (padrão: `warn`) |

### Exemplo

```bash
MATERIALIZE_GPU_BACKEND=vulkan materialize texture.png
MATERIALIZE_LOG=debug materialize texture.png -v
```

## Auto-completion

Gere o script de conclusão com `--generate-completions <shell>` e avalie-o ou grave-o no local apropriado da sua shell. Suporta `bash`, `zsh`, `fish`, `elvish` e `powershell`.

### Bash

```bash
materialize --generate-completions bash > /etc/bash_completion.d/materialize
```

### Zsh

```bash
materialize --generate-completions zsh > "${fpath[1]}/_materialize"
```

### Fish

```bash
materialize --generate-completions fish > ~/.config/fish/completions/materialize.fish
```

### Elvish

```bash
materialize --generate-completions elvish >> ~/.config/elvish/rc.elv
```

### PowerShell

```powershell
materialize --generate-completions powershell | Out-String | Invoke-Expression
```
