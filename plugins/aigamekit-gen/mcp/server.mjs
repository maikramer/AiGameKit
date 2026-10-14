#!/usr/bin/env node
// MCP server (stdio) do plugin aigamekit-gen — expõe os CLIs do AiGameKit como tools.
// Sem dependências externas. stdout = protocolo (newline-delimited JSON-RPC);
// logs de debug só em stderr com AIGAMEKIT_MCP_DEBUG=1.

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const SERVER = { name: "aigamekit", version: "0.2.0" };

const REPO = process.env.AIGAMEKIT_ROOT || join(homedir(), "GitClones", "AiGameKit");
const VENV_PACKAGE = {
  text2d: "Text2D",
  text3d: "Text3D",
  paint3d: "Paint3D",
  rigging3d: "Rigging3D",
  animator3d: "Animator3D",
  gameassets: "GameAssets",
};

function venvBins() {
  return Object.values(VENV_PACKAGE).map((pkg) => join(REPO, pkg, ".venv", "bin"));
}

function extraPathDirs() {
  return [join(homedir(), ".local", "bin"), ...venvBins()];
}

function resolveBin(name) {
  const pathDirs = (process.env.PATH || "").split(":").filter(Boolean);
  for (const dir of [...pathDirs, ...extraPathDirs()]) {
    const p = join(dir, name);
    try {
      if (existsSync(p)) return p;
    } catch {
      /* ignora */
    }
  }
  return null;
}

function childEnv() {
  const path = [...extraPathDirs(), process.env.PATH || ""].join(":");
  return { ...process.env, PATH: path };
}

const GPU_NOTE =
  "Jobs GPU delegam no vramd automaticamente (admit/queue/eviction) e podem demorar minutos; se exceder o timeout da tool, corre o mesmo comando via Bash ou reduz o scope.";

function str(v) {
  return String(v);
}

// ---------- definição das tools (declarativa) ----------

const TOOLS = [
  {
    name: "text2d_generate",
    description:
      "Gera uma imagem a partir de texto (FLUX Klein SDNQ). Defaults da tool seguem o CLI: 1024², 4 steps, guidance 1.0. " + GPU_NOTE,
    bin: "text2d",
    timeout: 900,
    inputSchema: {
      type: "object",
      properties: {
        prompt: { type: "string", description: "Prompt da imagem" },
        output: { type: "string", description: "Ficheiro de saída (.png/.jpg)" },
        width: { type: "integer" },
        height: { type: "integer" },
        steps: { type: "integer" },
        seed: { type: "integer" },
        quality: { type: "string", enum: ["fast", "low", "medium", "high", "highest"] },
        category: { type: "string", enum: ["icon"], description: "Modo ícone: 512², 2 steps" },
        transparent: { type: "boolean", description: "Remover fundo (rembg; exige .png)" },
      },
      required: ["prompt", "output"],
    },
    build: (a) => [
      "generate", a.prompt, "-o", a.output,
      ...(a.width != null ? ["-W", str(a.width)] : []),
      ...(a.height != null ? ["-H", str(a.height)] : []),
      ...(a.steps != null ? ["-s", str(a.steps)] : []),
      ...(a.seed != null ? ["--seed", str(a.seed)] : []),
      ...(a.quality ? ["--quality", a.quality] : []),
      ...(a.category ? ["--category", a.category] : []),
      ...(a.transparent ? ["--transparent"] : []),
    ],
  },
  {
    name: "text3d_generate",
    description:
      "Gera uma mesh 3D a partir de texto (Text2D → Hunyuan3D-Omni) ou de uma imagem existente (from_image, preferir vista eye-level 3/4). " + GPU_NOTE,
    bin: "text3d",
    timeout: 1800,
    inputSchema: {
      type: "object",
      properties: {
        prompt: { type: "string", description: "Prompt (ignorado com from_image)" },
        from_image: { type: "string", description: "Imagem de referência: corre só o Hunyuan3D" },
        output: { type: "string", description: "Ficheiro de saída (.glb/.ply)" },
        preset: { type: "string", enum: ["fast", "balanced", "hq"] },
        seed: { type: "integer" },
        format: { type: "string", enum: ["glb", "ply"] },
      },
      required: ["output"],
    },
    build: (a) => {
      if (!a.prompt && !a.from_image) throw new Error("Indica 'prompt' ou 'from_image'.");
      return [
        "generate",
        ...(a.from_image ? ["-i", a.from_image] : [a.prompt]),
        "-o", a.output,
        ...(a.preset ? ["--preset", a.preset] : []),
        ...(a.seed != null ? ["--seed", str(a.seed)] : []),
        ...(a.format ? ["-f", a.format] : []),
      ];
    },
  },
  {
    name: "text3d_mesh",
    description:
      "Operações de mesh do Text3D (dono único das operações de mesh): topology-fix (Stage 2, sempre antes de paint/rig), simplify, lod (ladder LOD0/1/2), collision, finish (KTX2+meshopt), bake-master, remesh-textured, align-plus-z, convert, split-at-height, patch-pbr. output é opcional onde o subcomando suporta -o; para 'lod' é o diretório de saída (com basename).",
    bin: "text3d",
    timeout: 1800,
    inputSchema: {
      type: "object",
      properties: {
        op: { type: "string", enum: ["topology-fix", "simplify", "lod", "collision", "finish", "bake-master", "remesh-textured", "align-plus-z", "convert", "split-at-height", "patch-pbr"] },
        input: { type: "string", description: "GLB/OBJ/PLY de entrada" },
        output: { type: "string", description: "Saída (ficheiro; diretório+basename para lod)" },
        basename: { type: "string", description: "Apenas para lod: prefixo dos ficheiros" },
        target_faces: { type: "integer", description: "Apenas simplify/remesh-textured" },
        engine: { type: "string", enum: ["arrays", "bpy", "auto"], description: "Apenas topology-fix (default arrays)" },
        extra_args: { type: "array", items: { type: "string" }, description: "Flags extra passadas tal qual ao subcomando" },
      },
      required: ["op", "input"],
    },
    build: (a) => {
      const argv = [a.op, a.input];
      if (a.output) argv.push("-o", a.output);
      if (a.basename) argv.push("--basename", a.basename);
      if (a.target_faces != null) argv.push("--target-faces", str(a.target_faces));
      if (a.engine) argv.push("--engine", a.engine);
      return [...argv, ...(a.extra_args || [])];
    },
  },
  {
    name: "paint3d_texture",
    description:
      "Texturiza uma mesh com Hunyuan3D-Paint 2.1 → GLB com PBR completo (albedo + metallicRoughness + occlusion via Materialize). A imagem tem de estar alinhada com a mesh. " + GPU_NOTE,
    bin: "paint3d",
    timeout: 1800,
    inputSchema: {
      type: "object",
      properties: {
        mesh: { type: "string", description: "GLB de entrada (idealmente pós topology-fix)" },
        image: { type: "string", description: "Imagem de referência alinhada" },
        output: { type: "string", description: "GLB de saída com textura embutida" },
        upscale: { type: "boolean", description: "Real-ESRGAN (requer spandrel)" },
        upscale_factor: { type: "integer", enum: [2, 4] },
        texture_size: { type: "integer", description: "Resolução do atlas UV (default 4096)" },
        pbr_preset: { type: "string", enum: ["default", "skin", "floor", "metal", "fabric", "wood", "stone"] },
      },
      required: ["mesh", "image"],
    },
    build: (a) => [
      "texture", a.mesh, "-i", a.image,
      ...(a.output ? ["-o", a.output] : []),
      ...(a.upscale ? ["--upscale"] : []),
      ...(a.upscale && a.upscale_factor ? ["--upscale-factor", str(a.upscale_factor)] : []),
      ...(a.texture_size != null ? ["--texture-size", str(a.texture_size)] : []),
      ...(a.pbr_preset ? ["--pbr-preset", a.pbr_preset] : []),
    ],
  },
  {
    name: "paint3d_quick",
    description: "Textura rápida sem IA (CPU, segundos): cor sólida ou ruído perlin — útil para placeholders.",
    bin: "paint3d",
    timeout: 300,
    inputSchema: {
      type: "object",
      properties: {
        mesh: { type: "string" },
        output: { type: "string" },
        style: { type: "string", enum: ["solid", "perlin"] },
        color: { type: "string", description: "#RGB ou #RRGGBB (style=solid)" },
        seed: { type: "integer" },
      },
      required: ["mesh", "output"],
    },
    build: (a) => [
      "quick", a.mesh, "--style", a.style || "solid", "-o", a.output,
      ...(a.color ? ["--color", a.color] : []),
      ...(a.seed != null ? ["--seed", str(a.seed)] : []),
    ],
  },
  {
    name: "rigging3d_pipeline",
    description:
      "Auto-rigging SkinTokens (skeleton + skin numa passada) sobre um GLB (idealmente _painted). O rig herda origem/pivô/orientação do LOD0. " + GPU_NOTE,
    bin: "rigging3d",
    timeout: 1800,
    inputSchema: {
      type: "object",
      properties: {
        input: { type: "string" },
        output: { type: "string" },
        quality: { type: "string", enum: ["fast", "low", "medium", "high", "highest"] },
        seed: { type: "integer" },
        use_existing_skeleton: { type: "boolean", description: "Entrada já tem skeleton; gerar só o skin" },
      },
      required: ["input", "output"],
    },
    build: (a) => [
      "pipeline", "-i", a.input, "-o", a.output,
      ...(a.quality ? ["--quality", a.quality] : []),
      ...(a.seed != null ? ["--seed", str(a.seed)] : []),
      ...(a.use_existing_skeleton ? ["--use-existing-skeleton"] : []),
    ],
  },
  {
    name: "animator3d_game_pack",
    description:
      "Gera todas as animações de um rig num comando: retarget Quaternius (CC0) para humanoides; presets procedurais para criaturas/voadores. Catálogo de clips: animator3d_list_animations. " + GPU_NOTE,
    bin: "animator3d",
    timeout: 1800,
    inputSchema: {
      type: "object",
      properties: {
        input: { type: "string", description: "GLB rigado" },
        output: { type: "string", description: "GLB animado" },
        preset: { type: "string", enum: ["humanoid", "creature", "flying"] },
        clips: { type: "string", description: 'Subconjunto por vírgulas, ex: "idle,walk,run"' },
        anim_pack: { type: "string", enum: ["quaternius", "quaternius2", "villager", "both", "all"] },
        facing: { type: "string", enum: ["auto", "+x", "-x", "+z", "-z"], description: "Eixo do focinho (gait para trás = forçar)" },
        force_preset: { type: "boolean", description: "Desativar auto-deteção humanoid vs creature" },
      },
      required: ["input", "output"],
    },
    build: (a) => [
      "game-pack", a.input, a.output,
      ...(a.preset ? ["--preset", a.preset] : []),
      ...(a.clips ? ["--clips", a.clips] : []),
      ...(a.anim_pack ? ["--anim-pack", a.anim_pack] : []),
      ...(a.facing ? ["--facing", a.facing] : []),
      ...(a.force_preset ? ["--force-preset"] : []),
    ],
  },
  {
    name: "animator3d_list_animations",
    description: "Catálogo de animações dos packs (UAL1/UAL2/villager) em JSON — leve, sem bpy/GPU.",
    bin: "animator3d",
    timeout: 60,
    inputSchema: {
      type: "object",
      properties: {
        pack: { type: "string", enum: ["quaternius", "quaternius2", "villager", "both", "all"] },
      },
    },
    build: (a) => ["list-animations", "--json", ...(a.pack ? ["--pack", a.pack] : [])],
  },
  {
    name: "animator3d_list_clips",
    description: "Lista clips (Actions/NLA) dentro de um GLB, em JSON — útil para pipelines. CPU only.",
    bin: "animator3d",
    timeout: 300,
    inputSchema: {
      type: "object",
      properties: { input: { type: "string", description: "GLB/GLTF/FBX" } },
      required: ["input"],
    },
    build: (a) => ["list-clips", a.input],
  },
  {
    name: "animator3d_inspect",
    description: "Inspeciona um GLB: armatures, bones (amostra) e ações. CPU only.",
    bin: "animator3d",
    timeout: 300,
    inputSchema: {
      type: "object",
      properties: { input: { type: "string" } },
      required: ["input"],
    },
    build: (a) => ["inspect", a.input],
  },
  {
    name: "animator3d_screenshot",
    description: "Screenshots multi-ângulo de um modelo 3D (QA para agentes). CPU only.",
    bin: "animator3d",
    timeout: 600,
    inputSchema: {
      type: "object",
      properties: {
        input: { type: "string" },
        output_dir: { type: "string" },
      },
      required: ["input"],
    },
    build: (a) => ["screenshot", a.input, ...(a.output_dir ? ["-o", a.output_dir] : [])],
  },
  {
    name: "gameassets_batch",
    description:
      "Batch de assets por manifest (game.yaml + manifest.yaml): imagens → 3D → paint → rig → animação → LOD → collision → validate. Stages auto-detectadas; corre com --plain (texto simples). " + GPU_NOTE,
    bin: "gameassets",
    timeout: 3600,
    inputSchema: {
      type: "object",
      properties: {
        profile: { type: "string", description: "game.yaml" },
        manifest: { type: "string", description: "manifest.yaml" },
        dry_run: { type: "boolean", description: "Mostra comandos sem executar" },
        force: { type: "boolean" },
        fail_fast: { type: "boolean" },
        no_3d: { type: "boolean" },
        no_rig: { type: "boolean" },
        no_animate: { type: "boolean" },
        no_lod: { type: "boolean" },
        no_collision: { type: "boolean" },
        gpu_ids: { type: "string", description: "Ex.: '0,1' (auto-deteta quando omitido)" },
      },
    },
    build: (a) => [
      "batch", "--plain",
      ...(a.profile ? ["--profile", a.profile] : []),
      ...(a.manifest ? ["--manifest", a.manifest] : []),
      ...(a.dry_run ? ["--dry-run"] : []),
      ...(a.force ? ["--force"] : []),
      ...(a.fail_fast ? ["--fail-fast"] : []),
      ...(a.no_3d ? ["--no-3d"] : []),
      ...(a.no_rig ? ["--no-rig"] : []),
      ...(a.no_animate ? ["--no-animate"] : []),
      ...(a.no_lod ? ["--no-lod"] : []),
      ...(a.no_collision ? ["--no-collision"] : []),
      ...(a.gpu_ids ? ["--gpu-ids", a.gpu_ids] : []),
    ],
  },
  {
    name: "gameassets_resume",
    description:
      "Batch inteligente: analisa o estado de cada asset e executa apenas as fases pendentes (preferir a batch --force com trabalho parcial). " + GPU_NOTE,
    bin: "gameassets",
    timeout: 3600,
    inputSchema: {
      type: "object",
      properties: {
        profile: { type: "string" },
        manifest: { type: "string" },
        dry_run: { type: "boolean" },
        force: { type: "boolean" },
        redo_split: { type: "boolean", description: "Refaz stump/top/LOD/collision a partir do painted" },
        gpu_ids: { type: "string" },
      },
    },
    build: (a) => [
      "resume", "--no-dashboard",
      ...(a.profile ? ["--profile", a.profile] : []),
      ...(a.manifest ? ["--manifest", a.manifest] : []),
      ...(a.dry_run ? ["--dry-run"] : []),
      ...(a.force ? ["--force"] : []),
      ...(a.redo_split ? ["--redo-split"] : []),
      ...(a.gpu_ids ? ["--gpu-ids", a.gpu_ids] : []),
    ],
  },
  {
    name: "gameassets_dream_create",
    description:
      "Ideia → jogo: plano LLM → game.yaml/manifest/world.xml → projeto Vite jogável. --dry-run gera ficheiros sem GPU; ollama = provider local sem API key. " + GPU_NOTE,
    bin: "gameassets",
    timeout: 3600,
    inputSchema: {
      type: "object",
      properties: {
        description: { type: "string", description: "Descrição do jogo em linguagem natural" },
        output_dir: { type: "string" },
        llm_provider: { type: "string", enum: ["openai", "huggingface", "ollama", "stdin"] },
        llm_model: { type: "string" },
        seed: { type: "integer" },
        max_assets: { type: "integer" },
        dry_run: { type: "boolean" },
        no_audio: { type: "boolean" },
        no_sky: { type: "boolean" },
        no_terrain: { type: "boolean" },
        replan: { type: "boolean", description: "Ignora cache de planos" },
      },
      required: ["description"],
    },
    build: (a) => [
      "dream", "create", a.description,
      ...(a.output_dir ? ["--output-dir", a.output_dir] : []),
      ...(a.llm_provider ? ["--llm-provider", a.llm_provider] : []),
      ...(a.llm_model ? ["--llm-model", a.llm_model] : []),
      ...(a.seed != null ? ["--seed", str(a.seed)] : []),
      ...(a.max_assets != null ? ["--max-assets", str(a.max_assets)] : []),
      ...(a.dry_run ? ["--dry-run"] : []),
      ...(a.no_audio ? ["--no-audio"] : []),
      ...(a.no_sky ? ["--no-sky"] : []),
      ...(a.no_terrain ? ["--no-terrain"] : []),
      ...(a.replan ? ["--replan"] : []),
    ],
  },
  {
    name: "gameassets_handoff",
    description: "Copia GLB/áudio do output_dir do perfil para public/assets de um projeto Vite e grava gameassets_handoff.json.",
    bin: "gameassets",
    timeout: 300,
    inputSchema: {
      type: "object",
      properties: {
        public_dir: { type: "string", description: "Pasta public/ do projeto" },
        profile: { type: "string" },
        manifest: { type: "string" },
        symlink: { type: "boolean", description: "Symlinks em vez de cópias" },
      },
      required: ["public_dir"],
    },
    build: (a) => [
      "handoff", "--public-dir", a.public_dir,
      ...(a.profile ? ["--profile", a.profile] : []),
      ...(a.manifest ? ["--manifest", a.manifest] : []),
      ...(a.symlink ? ["--symlink"] : []),
    ],
  },
  {
    name: "gameassets_validate",
    description: "Valida assets gerados contra thresholds de qualidade (faces por mesh, tamanho de ficheiro).",
    bin: "gameassets",
    timeout: 600,
    inputSchema: {
      type: "object",
      properties: {
        profile: { type: "string" },
        manifest: { type: "string" },
        max_poly_count: { type: "integer" },
        max_file_size_mb: { type: "number" },
      },
    },
    build: (a) => [
      "validate",
      ...(a.profile ? ["--profile", a.profile] : []),
      ...(a.manifest ? ["--manifest", a.manifest] : []),
      ...(a.max_poly_count != null ? ["--max-poly-count", str(a.max_poly_count)] : []),
      ...(a.max_file_size_mb != null ? ["--max-file-size-mb", str(a.max_file_size_mb)] : []),
    ],
  },
  {
    name: "gameassets_doctor",
    description: "Primeiro uso: valida tools do perfil, vramd/GPU, compressão GLB, LLM do dream e disco.",
    bin: "gameassets",
    timeout: 300,
    inputSchema: { type: "object", properties: {} },
    build: () => ["doctor"],
  },
  {
    name: "aigamekit_run",
    description:
      "Escape hatch total: corre um comando arbitrário de um CLI AiGameKit permitido (ex.: args ['--version'] ou ['generate','x'...]). Para flags/subcomandos não cobertos pelas tools específicas.",
    bin: null,
    timeout: 900,
    inputSchema: {
      type: "object",
      properties: {
        tool: { type: "string", enum: Object.keys(VENV_PACKAGE) },
        args: { type: "array", items: { type: "string" }, description: "Argumentos após o binário" },
        timeout_s: { type: "integer", description: "Timeout em segundos (5–3600; default 900)" },
      },
      required: ["tool", "args"],
    },
    build: (a) => a.args.map(str),
    resolveBinName: (a) => a.tool,
  },
];

const TOOL_MAP = new Map(TOOLS.map((t) => [t.name, t]));

// ---------- execução ----------

const MAX_OUT = 24000;

function truncate(text) {
  if (text.length <= MAX_OUT) return text;
  const head = text.slice(0, 16000);
  const tail = text.slice(-6000);
  return `${head}\n…[truncado ${text.length - head.length - tail.length} chars]…\n${tail}`;
}

function runCli(binName, argv, timeoutSec) {
  return new Promise((resolve) => {
    const bin = resolveBin(binName);
    if (!bin) {
      resolve({
        code: -1,
        killed: false,
        out:
          `ERRO: binário '${binName}' não encontrado no PATH nem em ${join(homedir(), ".local", "bin")}` +
          ` ou nos venvs de ${REPO}. Instala com ./install.sh <tool> ou define AIGAMEKIT_ROOT.`,
      });
      return;
    }
    const started = Date.now();
    const child = spawn(bin, argv.map(str), { env: childEnv() });
    let out = "";
    let killed = false;
    const timer = setTimeout(() => {
      killed = true;
      child.kill("SIGKILL");
    }, Math.min(3600, Math.max(5, timeoutSec)) * 1000);
    const feed = (d) => {
      out += d;
      if (out.length > 2_000_000) out = out.slice(-1_000_000); // proteger memória em jobs longos
    };
    child.stdout.on("data", feed);
    child.stderr.on("data", feed);
    child.on("error", (e) => {
      clearTimeout(timer);
      resolve({ code: -1, killed, out: `ERRO spawn ${bin}: ${e.message}` });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      const secs = ((Date.now() - started) / 1000).toFixed(1);
      const header = `$ ${binName} ${argv.map(str).join(" ")}\n(exit ${code ?? "?"}, ${secs}s${killed ? ", TIMEOUT — processo morto; usa Bash para jobs longos" : ""})\n`;
      resolve({ code: code ?? -1, killed, out: header + truncate(out.trim()) });
    });
  });
}

// ---------- protocolo MCP (stdio, newline-delimited JSON-RPC) ----------

function reply(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}

function replyError(id, code, message) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n");
}

async function handleRequest(msg) {
  const { id, method, params } = msg;
  switch (method) {
    case "initialize":
      reply(id, {
        protocolVersion: params?.protocolVersion || "2024-11-05",
        capabilities: { tools: {} },
        serverInfo: SERVER,
      });
      return;
    case "ping":
      reply(id, {});
      return;
    case "tools/list":
      reply(id, {
        tools: TOOLS.map(({ name, description, inputSchema }) => ({ name, description, inputSchema })),
      });
      return;
    case "tools/call": {
      const name = params?.name;
      const tool = TOOL_MAP.get(name);
      if (!tool) {
        reply(id, {
          content: [{ type: "text", text: `Tool desconhecida: ${name}` }],
          isError: true,
        });
        return;
      }
      let argv;
      try {
        argv = tool.build(params?.arguments || {});
      } catch (e) {
        reply(id, { content: [{ type: "text", text: `Argumentos inválidos: ${e.message}` }], isError: true });
        return;
      }
      const binName = tool.resolveBinName ? tool.resolveBinName(params?.arguments || {}) : tool.bin;
      const timeout = params?.arguments?.timeout_s || tool.timeout;
      const res = await runCli(binName, argv, timeout);
      reply(id, { content: [{ type: "text", text: res.out }], isError: res.code !== 0 });
      return;
    }
    default:
      replyError(id, -32601, `Método não suportado: ${method}`);
  }
}

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  let nl;
  while ((nl = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, nl).trim();
    buf = buf.slice(nl + 1);
    if (!line) continue;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      if (process.env.AIGAMEKIT_MCP_DEBUG) console.error(`linha não-JSON ignorada: ${line.slice(0, 120)}`);
      continue;
    }
    if (msg && msg.method && msg.id != null) {
      handleRequest(msg).catch((e) =>
        replyError(msg.id, -32603, `Erro interno: ${e?.message || e}`)
      );
    }
    // notificações (sem id) não recebem resposta
  }
});
process.stdin.on("end", () => process.exit(0));
