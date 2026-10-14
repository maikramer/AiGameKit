#!/usr/bin/env node
// MCP stdio server `viber` — envolve o CLI `viber` (não reimplementa o cliente BRP:
// a resolução de porta/identidade, request/poll e formatting ficam no CLI).
// Zero dependências: JSON-RPC newline-delimited sobre stdin/stdout (MCP stdio).
//
// Tools: viber_status, viber_screenshot (PNG inline), viber_burst, viber_tree,
// viber_logs, viber_input, viber_lua, viber_scene.
//
// Binário: env VIBER_BIN (default "viber" — userConfig.viberBin do plugin injeta-o).

import { execFile as execFileCb } from "node:child_process";
import { promisify } from "node:util";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const execFile = promisify(execFileCb);
const VIBER_BIN = process.env.VIBER_BIN || "viber";
const WORK_DIR = join(tmpdir(), "viber-mcp");
const SERVER_INFO = { name: "viber", version: "0.1.0" };

const run = async (args, timeoutMs = 30_000) => {
  try {
    const { stdout, stderr } = await execFile(VIBER_BIN, args, {
      timeout: timeoutMs,
      maxBuffer: 32 * 1024 * 1024,
      encoding: "utf8",
      windowsHide: true,
    });
    return { ok: true, stdout: stdout ?? "", stderr: stderr ?? "" };
  } catch (err) {
    return {
      ok: false,
      stdout: err.stdout ?? "",
      stderr: err.stderr ?? "",
      message: err.message ?? String(err),
      code: err.code,
    };
  }
};

// Flags de engine alvo — os subcomandos `viber debug <sub>` aceitam --world/--port.
const targetFlags = (a = {}) => {
  const f = [];
  if (a.world) f.push("--world", String(a.world));
  if (a.port) f.push("--port", String(a.port));
  return f;
};

const num = (v) => (typeof v === "number" ? String(v) : undefined);

const text = (t) => ({ type: "text", text: String(t) });
const errorContent = (r, hint) => [
  text(
    `${VIBER_BIN} falhou (exit ${r.code ?? "?"}): ${(r.stderr || r.stdout || r.message || "").trim()}` +
      (hint ? `\n${hint}` : "")
  ),
];

const pngContent = async (path) => {
  const buf = await readFile(path);
  return { type: "image", data: buf.toString("base64"), mimeType: "image/png" };
};

// ---------- tools ----------

const tools = [
  {
    name: "viber_status",
    description:
      "Estado do debug bridge: engines vivas (`viber debug engines`) + probe (`viber debug probe`). " +
      "Sem engine a correr, o probe falha — o texto explica. Útil antes de qualquer outra tool.",
    inputSchema: {
      type: "object",
      properties: {
        world: { type: "string", description: "Mundo alvo (caminho, nome de ficheiro ou stem) — resolve a porta pelo engine.json da sessão" },
        port: { type: "number", description: "Porta explícita do bridge (default 15702)" },
      },
    },
    handler: async (a) => {
      const engines = await run(["debug", "engines"]);
      const probe = await run(["debug", "probe", ...targetFlags(a)]);
      const out = [
        "== engines ==",
        (engines.stdout || engines.stderr).trim() || "(sem saída)",
        "== probe ==",
        probe.ok ? (probe.stdout || probe.stderr).trim() : `bridge NÃO responde: ${(probe.stderr || probe.message).trim()}`,
      ].join("\n");
      return { content: [text(out)], isError: !engines.ok };
    },
  },
  {
    name: "viber_screenshot",
    description:
      "Captura o ecrã da engine a correr e devolve o PNG INLINE (image content) + caminho em disco. " +
      "Request+poll (~1-3 frames). Janela minimizada não captura (stall 30s → erro).",
    inputSchema: {
      type: "object",
      properties: {
        world: { type: "string" },
        port: { type: "number" },
        output: { type: "string", description: "Caminho do PNG (default: <tmp>/viber-mcp/screenshot-<ts>.png)" },
        timeout_ms: { type: "number", description: "Timeout do CLI em ms (default 10000)" },
      },
    },
    handler: async (a) => {
      await mkdir(WORK_DIR, { recursive: true });
      const output = a.output || join(WORK_DIR, `screenshot-${Date.now()}.png`);
      const timeoutMs = a.timeout_ms || 10_000;
      const r = await run(
        ["debug", "screenshot", "-o", output, "--timeout-ms", String(timeoutMs), ...targetFlags(a)],
        timeoutMs + 15_000
      );
      if (!r.ok) return { content: errorContent(r, "Engine a correr? viber_status primeiro."), isError: true };
      const image = await pngContent(output);
      return { content: [image, text(`screenshot: ${output}`)] };
    },
  },
  {
    name: "viber_burst",
    description:
      "N frames seguidos numa ÚNICA folha (grid √N×√N, 4096 no lado comprido) — QA temporal: flicker não se vê num frame isolado. " +
      "--skip 0 = consecutivos (flicker de 1 frame); skips maiores cobrem caminhada/combate (frames×(skip+1) frames de render). " +
      "stats = veredicto numérico de flicker (luma/frame + oscilação máxima).",
    inputSchema: {
      type: "object",
      properties: {
        world: { type: "string" },
        port: { type: "number" },
        frames: { type: "number", enum: [4, 9, 16], description: "Default 9" },
        skip: { type: "number", description: "Frames renderizados a saltar entre capturas (default 0)" },
        stats: { type: "boolean", description: "Veredicto numérico de flicker no texto de saída" },
        output: { type: "string" },
        timeout_ms: { type: "number", description: "Default 60000" },
      },
    },
    handler: async (a) => {
      await mkdir(WORK_DIR, { recursive: true });
      const output = a.output || join(WORK_DIR, `burst-${Date.now()}.png`);
      const timeoutMs = a.timeout_ms || 60_000;
      const args = ["debug", "burst", "-o", output];
      if (a.frames) args.push("--frames", String(a.frames));
      if (a.skip !== undefined) args.push("--skip", String(a.skip));
      if (a.stats) args.push("--stats");
      const r = await run([...args, ...targetFlags(a)], timeoutMs + 15_000);
      if (!r.ok) return { content: errorContent(r), isError: true };
      const image = await pngContent(output);
      const statsText = (r.stdout || "").trim();
      return { content: [image, text([`burst: ${output}`, statsText].filter(Boolean).join("\n"))] };
    },
  },
  {
    name: "viber_tree",
    description: "Árvore de entidades da engine viva (id/nome/pai/transform/componentes) — como take_snapshot.",
    inputSchema: {
      type: "object",
      properties: { world: { type: "string" }, port: { type: "number" }, json: { type: "boolean" } },
    },
    handler: async (a) => {
      const args = ["debug", "tree"];
      if (a.json) args.push("--json");
      const r = await run([...args, ...targetFlags(a)]);
      return r.ok
        ? { content: [text((r.stdout || r.stderr).trim())] }
        : { content: errorContent(r), isError: true };
    },
  },
  {
    name: "viber_logs",
    description: "Console da engine: ring-buffer de tracing (1000 entradas). Filtros client-side level/grep/limit.",
    inputSchema: {
      type: "object",
      properties: {
        world: { type: "string" },
        port: { type: "number" },
        limit: { type: "number", description: "Default 100" },
        level: { type: "string", enum: ["error", "warn", "info", "debug", "trace"], description: "Nível mínimo" },
        grep: { type: "string", description: "Substring na mensagem/target" },
        json: { type: "boolean" },
      },
    },
    handler: async (a) => {
      const args = ["debug", "logs"];
      if (a.limit) args.push("--limit", String(a.limit));
      if (a.level) args.push("--level", a.level);
      if (a.grep) args.push("--grep", a.grep);
      if (a.json) args.push("--json");
      const r = await run([...args, ...targetFlags(a)]);
      return r.ok
        ? { content: [text((r.stdout || r.stderr).trim() || "(sem logs)")] }
        : { content: errorContent(r), isError: true };
    },
  },
  {
    name: "viber_input",
    description:
      "Input sintético na engine. key é TAP, não hold (repetir para segurar). click/move em pixels lógicos da janela; " +
      "retângulos de UI exatos via viber.debug.ui_tree().",
    inputSchema: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["key", "text", "click", "move"] },
        key: { type: "string", description: "action=key: w, space, enter, esc, up, ctrl, f3…" },
        shift: { type: "boolean", description: "action=key" },
        text: { type: "string", description: "action=text: string completa (typing por char)" },
        x: { type: "number", description: "action=click|move" },
        y: { type: "number", description: "action=click|move" },
        button: { type: "string", description: "action=click: ex. 'right'" },
        world: { type: "string" },
        port: { type: "number" },
      },
      required: ["action"],
    },
    handler: async (a) => {
      let args;
      if (a.action === "key") {
        if (!a.key) return { content: [text("action=key exige `key`")], isError: true };
        args = ["debug", "key", a.key];
        if (a.shift) args.push("--shift");
      } else if (a.action === "text") {
        if (a.text === undefined) return { content: [text("action=text exige `text`")], isError: true };
        args = ["debug", "text", String(a.text)];
      } else if (a.action === "click") {
        if (num(a.x) === undefined || num(a.y) === undefined)
          return { content: [text("action=click exige `x` e `y`")], isError: true };
        args = ["debug", "click", num(a.x), num(a.y)];
        if (a.button) args.push("--button", a.button);
      } else {
        if (num(a.x) === undefined || num(a.y) === undefined)
          return { content: [text("action=move exige `x` e `y`")], isError: true };
        args = ["debug", "move", num(a.x), num(a.y)];
      }
      const r = await run([...args, ...targetFlags(a)]);
      return r.ok
        ? { content: [text((r.stdout || `input ${a.action} enviado`).trim())] }
        : { content: errorContent(r), isError: true };
    },
  },
  {
    name: "viber_lua",
    description:
      "Avalia Luau NA engine (REPL `viber.lua`): toda a API viber.debug.* (VER/MEXER/PROVAR/DESCOBRIR) + viber.* de scripts. " +
      "Globals persistem entre chamadas; `return` devolve valor. Leituras = snapshot do INÍCIO da chamada — escrever e verificar em chamadas SEPARADAS. " +
      "API: `viber debug api` na engine. Ex.: 'viber.debug.tp(0, -20) return viber.debug.player()'",
    inputSchema: {
      type: "object",
      properties: {
        code: { type: "string", description: "Código Luau (vai por ficheiro temp — sem problemas de quoting)" },
        json: { type: "boolean", description: "Default true: resposta completa {ok, result, applied, warnings}" },
        world: { type: "string" },
        port: { type: "number" },
      },
      required: ["code"],
    },
    handler: async (a) => {
      await mkdir(WORK_DIR, { recursive: true });
      const file = join(WORK_DIR, `lua-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.lua`);
      try {
        await writeFile(file, a.code.endsWith("\n") ? a.code : a.code + "\n", "utf8");
        const args = ["debug", "lua", "--file", file];
        if (a.json !== false) args.push("--json");
        const r = await run([...args, ...targetFlags(a)]);
        if (!r.ok) return { content: errorContent(r, "Engine a correr? viber_status primeiro."), isError: true };
        const out = (r.stdout || r.stderr).trim();
        let luaError = false;
        if (a.json !== false) {
          try {
            const parsed = JSON.parse(out);
            if (parsed && parsed.ok === false) luaError = true;
          } catch { /* saída não-JSON: devolver crua */ }
        }
        return { content: [text(out || "(sem saída)")], isError: luaError };
      } finally {
        await rm(file, { force: true });
      }
    },
  },
  {
    name: "viber_scene",
    description:
      "Edição de cena: `viber scene <sub>` offline ou --live (engine a correr; grava + sync ao vivo + undo/redo). " +
      "Subs: ls [SEL] [--depth], get SEL, find --tag X [--attr k=v], set SEL k=v… [--unset k] [--dry-run], " +
      "insert PARENT '<xml>' [--index], rm SEL, mv SEL PARENT, dup SEL, undo|redo (só --live), apply ops.json|-. " +
      "Seletores: name=well ou world/village.xml#3/0. live + terreno/céu/UI/spawners = needs_reload. " +
      "Requer binário viber rebuilt (>=2026-10-13).",
    inputSchema: {
      type: "object",
      properties: {
        sub: { type: "string", description: "Subcomando scene (ls|get|find|set|insert|rm|mv|dup|undo|redo|apply)" },
        args: { type: "array", items: { type: "string" }, description: "Argumentos posicionais do subcomando (strings exatas)" },
        live: { type: "boolean", description: "Default true: engine a correr via bridge" },
        world: { type: "string" },
        port: { type: "number" },
      },
      required: ["sub"],
    },
    handler: async (a) => {
      const args = ["scene"];
      if (a.live !== false) args.push("--live");
      if (a.world) args.push("--world", String(a.world));
      if (a.port) args.push("--port", String(a.port));
      args.push(a.sub, ...((a.args || []).map(String)));
      const r = await run(args, 60_000);
      return r.ok
        ? { content: [text((r.stdout || r.stderr).trim() || "(ok)")] }
        : {
            content: errorContent(
              r,
              "`scene` exige binário rebuilt (./install.sh viber) se o instalado for anterior a 2026-10-13."
            ),
            isError: true,
          };
    },
  },
];

const toolMap = new Map(tools.map((t) => [t.name, t]));

// ---------- JSON-RPC / MCP stdio ----------

const send = (msg) => process.stdout.write(JSON.stringify(msg) + "\n");

let inFlight = 0;
let stdinEnded = false;
const maybeExit = () => {
  if (stdinEnded && inFlight === 0) process.exit(0);
};

const handleRequest = async (req) => {
  const { id, method, params } = req;
  inFlight++;
  try {
    if (method === "initialize") {
      return send({
        jsonrpc: "2.0",
        id,
        result: {
          protocolVersion: params?.protocolVersion || "2024-11-05",
          capabilities: { tools: {} },
          serverInfo: SERVER_INFO,
        },
      });
    }
    if (method === "ping") return send({ jsonrpc: "2.0", id, result: {} });
    if (method === "tools/list") {
      return send({
        jsonrpc: "2.0",
        id,
        result: { tools: tools.map(({ name, description, inputSchema }) => ({ name, description, inputSchema })) },
      });
    }
    if (method === "tools/call") {
      const name = params?.name;
      const tool = toolMap.get(name);
      if (!tool) {
        return send({
          jsonrpc: "2.0",
          id,
          error: { code: -32602, message: `Unknown tool: ${name}` },
        });
      }
      const result = await tool.handler(params?.arguments || {});
      return send({ jsonrpc: "2.0", id, result });
    }
    return send({ jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } });
  } catch (err) {
    return send({
      jsonrpc: "2.0",
      id,
      result: { content: [text(`erro interno: ${err?.message || err}`)], isError: true },
    });
  } finally {
    inFlight--;
    maybeExit();
  }
};

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let idx;
  while ((idx = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, idx).trim();
    buffer = buffer.slice(idx + 1);
    if (!line) continue;
    let req;
    try {
      req = JSON.parse(line);
    } catch {
      send({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
      continue;
    }
    if (req && req.method && req.id === undefined) continue; // notification (ex.: initialized)
    handleRequest(req);
  }
});
process.stdin.on("end", () => {
  stdinEnded = true;
  maybeExit();
});

process.stderr.write(`[viber-mcp] up — binário: ${VIBER_BIN}\n`);
