#!/usr/bin/env node
/**
 * CLI `vibegame` — ponto de entrada estável para o instalador unificado do monorepo.
 * `vibegame run`: antes do build da engine, `bun install` na engine se faltar alguma dependência
 * declarada em package.json (evita erros tipo Rollup não resolver `howler`). `--install` força install
 * mesmo com node_modules completo. `--skip-engine-install` pula esse passo. Em apps, instala deps em
 * falta (bun, com fallback npm).
 */
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, normalize, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');

/** @param {string} text */
function stripBom(text) {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
}

/**
 * Lê JSON de ficheiro; remove BOM UTF-8 (comum no Windows) para não falhar JSON.parse.
 * @param {string} filePath
 */
function readJsonFile(filePath) {
  return JSON.parse(stripBom(readFileSync(filePath, 'utf8')));
}

const pkg = readJsonFile(join(root, 'package.json'));
const createScript = join(root, 'create-vibegame', 'index.js');

/** @param {string} a @param {string} b */
function sameResolvedPath(a, b) {
  return normalize(resolve(a)) === normalize(resolve(b));
}

/** @param {string} child @param {string} parent */
function isDescendantDir(child, parent) {
  const rel = relative(resolve(parent), resolve(child));
  return (
    rel !== '' && !rel.startsWith('..') && !normalize(rel).startsWith('..')
  );
}

/**
 * Sobe diretórios a partir de `fromDir` e devolve a raiz do pacote `vibegame` (engine).
 * @param {string} fromDir
 * @returns {string | null}
 */
function findEngineRoot(fromDir) {
  let dir = resolve(fromDir);
  let guard = 0;
  while (guard++ < 24) {
    const pkgPath = join(dir, 'package.json');
    if (existsSync(pkgPath)) {
      try {
        const j = readJsonFile(pkgPath);
        if (
          j.name === 'vibegame' &&
          existsSync(join(dir, 'scripts', 'vibegame-cli.mjs'))
        ) {
          return dir;
        }
      } catch {
        /* ignore */
      }
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

/**
 * @param {string} cwd
 * @returns {string | null}
 */
function resolveEngineFromFileVibegameDep(cwd) {
  const pkgPath = join(cwd, 'package.json');
  if (!existsSync(pkgPath)) return null;
  let j;
  try {
    j = readJsonFile(pkgPath);
  } catch {
    return null;
  }
  const dep =
    (typeof j.dependencies?.vibegame === 'string' && j.dependencies.vibegame) ||
    (typeof j.devDependencies?.vibegame === 'string' &&
      j.devDependencies.vibegame) ||
    null;
  if (!dep || !dep.startsWith('file:')) return null;
  const raw = dep.slice('file:'.length).trim();
  return resolve(dirname(pkgPath), raw);
}

/**
 * @param {string} cwd
 * @returns {string | null}
 */
function resolveEngineRootForRun(cwd) {
  const fromWalk = findEngineRoot(cwd);
  if (fromWalk) return fromWalk;
  const fromFile = resolveEngineFromFileVibegameDep(cwd);
  if (
    fromFile &&
    existsSync(join(fromFile, 'package.json')) &&
    existsSync(join(fromFile, 'scripts', 'vibegame-cli.mjs'))
  ) {
    return fromFile;
  }
  return null;
}

/**
 * @param {string[]} args rest after `vibegame run`
 * @returns {{ install: boolean, skipBuild: boolean, skipEngineInstall: boolean, skipAppInstall: boolean, devArgs: string[] }}
 */
function parseRunArgs(args) {
  let install = false;
  let skipBuild = false;
  let skipEngineInstall = false;
  let skipAppInstall = false;
  /** @type {string[]} */
  let devArgs = [];
  const dash = args.indexOf('--');
  const flagsPart = dash >= 0 ? args.slice(0, dash) : args;
  if (dash >= 0) devArgs = args.slice(dash + 1);
  for (const f of flagsPart) {
    if (f === '--skip-install' || f === '--skip-engine-install')
      skipEngineInstall = true;
    else if (f === '--install' || f === '-i' || f === '--sync') install = true;
    else if (f === '--skip-build') skipBuild = true;
    else if (f === '--skip-app-install') skipAppInstall = true;
  }
  return { install, skipBuild, skipEngineInstall, skipAppInstall, devArgs };
}

/**
 * Executa `bun …` com stdio herdado.
 * No Windows, `shell: true` com Bun costuma deixar `bun install` sem saída ou sem encerrar após migrar lockfile.
 * @param {string} cwd
 * @param {string[]} args argumentos após `bun`
 * @returns {number}
 */
function runBun(cwd, args) {
  const base = {
    cwd,
    stdio: 'inherit',
    env: process.env,
    windowsHide: true,
  };
  let r = spawnSync('bun', args, { ...base, shell: false });
  if (r.error) {
    r = spawnSync('bun', args, { ...base, shell: true });
  }
  if (r.error) {
    console.error(
      `[vibegame run] Não foi possível executar bun: ${r.error.message}`
    );
    return 127;
  }
  return r.status ?? 1;
}

/**
 * @param {string} root
 * @param {string} pkgName npm package name (e.g. "howler", "@types/howler")
 * @returns {boolean}
 */
function depPackageJsonExists(root, pkgName) {
  const parts = pkgName.split('/');
  let rel;
  if (pkgName.startsWith('@')) {
    if (parts.length < 2) return false;
    rel = join('node_modules', parts[0], parts[1], 'package.json');
  } else {
    rel = join('node_modules', pkgName, 'package.json');
  }
  return existsSync(join(root, rel));
}

/**
 * Verifica se cada dependência declarada em package.json existe em node_modules.
 * Não inclui peerDependencies (podem vir de hoisting em monorepos).
 * @param {string} projectRoot
 * @param {Record<string, unknown>} pkg conteúdo de package.json
 * @returns {boolean}
 */
function declaredDepsSatisfied(projectRoot, pkg) {
  if (!existsSync(join(projectRoot, 'node_modules'))) return false;
  const groups = [
    pkg.dependencies,
    pkg.devDependencies,
    pkg.optionalDependencies,
  ];
  for (const g of groups) {
    if (!g || typeof g !== 'object') continue;
    for (const name of Object.keys(g)) {
      if (!depPackageJsonExists(projectRoot, name)) return false;
    }
  }
  return true;
}

/**
 * @param {string} engineRoot
 * @returns {boolean}
 */
function engineNodeModulesLookReady(engineRoot) {
  const pkgPath = join(engineRoot, 'package.json');
  if (!existsSync(pkgPath)) return false;
  let pkg;
  try {
    pkg = readJsonFile(pkgPath);
  } catch {
    return false;
  }
  return declaredDepsSatisfied(engineRoot, pkg);
}

/**
 * @param {string} cwd
 * @returns {boolean}
 */
function appNodeModulesLookReady(cwd) {
  const pkgPath = join(cwd, 'package.json');
  if (!existsSync(pkgPath)) return true;
  let pkg;
  try {
    pkg = readJsonFile(pkgPath);
  } catch {
    return false;
  }
  return declaredDepsSatisfied(cwd, pkg);
}

/**
 * @param {string} cwd
 * @returns {number}
 */
function runNpmInstall(cwd) {
  const base = {
    cwd,
    stdio: 'inherit',
    env: process.env,
    windowsHide: true,
  };
  let r = spawnSync('npm', ['install'], { ...base, shell: false });
  if (r.error) {
    r = spawnSync('npm', ['install'], { ...base, shell: true });
  }
  if (r.error) {
    console.error(
      `[vibegame run] npm install também falhou: ${r.error.message}`
    );
    return 127;
  }
  return r.status ?? 1;
}

/**
 * @param {string} cwd
 * @returns {number}
 */
function runAppPackageInstall(cwd) {
  console.log(`[vibegame run] bun install (app) → ${cwd}`);
  let code = runBun(cwd, ['install']);
  if (code !== 0) {
    console.warn(
      '[vibegame run] bun install falhou no app; a tentar npm install (útil no Windows com dependências file:).'
    );
    code = runNpmInstall(cwd);
  }
  return code;
}

/** @returns {void} */
function assertBunOnPath() {
  let r = spawnSync('bun', ['--version'], {
    stdio: 'pipe',
    shell: false,
    encoding: 'utf8',
    windowsHide: true,
  });
  if (r.error) {
    r = spawnSync('bun', ['--version'], {
      stdio: 'pipe',
      shell: true,
      encoding: 'utf8',
      windowsHide: true,
    });
  }
  if (r.error || r.status !== 0) {
    console.error(
      '[vibegame run] O comando `bun` não está disponível no PATH. Instale Bun: https://bun.sh'
    );
    process.exit(127);
  }
}

/**
 * @param {string} engineRoot
 * @param {{ install: boolean, skipBuild: boolean, skipEngineInstall: boolean, skipAppInstall: boolean, devArgs: string[] }} opts
 * @returns {never}
 */
function runVibegameRun(engineRoot, opts) {
  assertBunOnPath();
  const cwd = resolve(process.cwd());
  const localPkgPath = join(cwd, 'package.json');
  const localPkg =
    existsSync(localPkgPath) &&
    (() => {
      try {
        return readJsonFile(localPkgPath);
      } catch {
        return null;
      }
    })();

  const isEngineRoot = sameResolvedPath(cwd, engineRoot);
  const fileEngine = resolveEngineFromFileVibegameDep(cwd);
  const linksToThisEngine =
    fileEngine != null && sameResolvedPath(fileEngine, engineRoot);
  const hasDevScript = Boolean(localPkg?.scripts?.dev);
  const isApp =
    !isEngineRoot &&
    hasDevScript &&
    (isDescendantDir(cwd, engineRoot) || linksToThisEngine);

  if (opts.install) {
    console.log(
      `[vibegame run] bun install (engine, --install) → ${engineRoot}`
    );
    const c0 = runBun(engineRoot, ['install']);
    if (c0 !== 0) process.exit(c0);
  } else if (
    !opts.skipEngineInstall &&
    !engineNodeModulesLookReady(engineRoot)
  ) {
    console.log(
      `[vibegame run] Dependências da engine em falta ou incompletas — bun install (engine) → ${engineRoot}`
    );
    const c0 = runBun(engineRoot, ['install']);
    if (c0 !== 0) process.exit(c0);
  } else if (
    opts.skipEngineInstall &&
    !engineNodeModulesLookReady(engineRoot)
  ) {
    console.warn(
      '[vibegame run] Aviso: --skip-engine-install / --skip-install ativo mas node_modules da engine parece incompleto; o build pode falhar.'
    );
  }

  if (!opts.skipBuild) {
    console.log('[vibegame run] bun run build (engine)');
    const c1 = runBun(engineRoot, ['run', 'build']);
    if (c1 !== 0) process.exit(c1);
  }

  if (isApp) {
    const needsAppInstall =
      !opts.skipAppInstall && !appNodeModulesLookReady(cwd);
    if (needsAppInstall) {
      const c2 = runAppPackageInstall(cwd);
      if (c2 !== 0) process.exit(c2);
    } else if (opts.skipAppInstall) {
      console.log(
        '[vibegame run] Pulando install no app (--skip-app-install).'
      );
    } else {
      console.log(
        '[vibegame run] node_modules do app parece completo — pulando install.'
      );
    }
    console.log('[vibegame run] bun run dev (app)');
    const c3 = runBun(cwd, ['run', 'dev', ...opts.devArgs]);
    process.exit(c3);
  }

  if (isEngineRoot) {
    console.log('[vibegame run] bun run dev (engine — vite build --watch)');
    const c4 = runBun(engineRoot, ['run', 'dev', ...opts.devArgs]);
    process.exit(c4);
  }

  console.error(
    '[vibegame run] Não foi possível decidir o alvo: não está na raiz da engine nem num projeto com script `dev` ligado a essa engine.'
  );
  console.error(`  cwd: ${cwd}`);
  console.error(`  engine: ${engineRoot}`);
  console.error(
    '  Dica: rode dentro de `examples/...` ou da raiz do pacote `vibegame`, ou use dependência `file:` para a engine.'
  );
  process.exit(1);
}

function help() {
  console.log(`vibegame ${pkg.version} — VibeGame (GameDev monorepo)`);
  console.log('');
  console.log('  vibegame create <name>   Create a new project (scaffold)');
  console.log(
    '  vibegame run [opts] [-- …]  Build da engine + dev (bun install na engine se faltar deps)'
  );
  console.log('  vibegame playwright …    Run Playwright CLI (alias: pw)');
  console.log('  vibegame --version       Show version');
  console.log('  vibegame help            This message');
  console.log('');
  console.log(
    '  vibegame run --install / -i / --sync   Força bun install na engine antes do build'
  );
  console.log(
    '  vibegame run --skip-engine-install   Não roda bun install na engine (node_modules já ok)'
  );
  console.log('  vibegame run --skip-install   Alias de --skip-engine-install');
  console.log(
    '  vibegame run --skip-build     Pula build da engine (só sobe dev)'
  );
  console.log(
    '  vibegame run --skip-app-install   Não roda bun install na pasta do app (node_modules já ok)'
  );
  console.log(
    '  vibegame run -- --port 5174       Repassa argumentos ao `bun run dev`'
  );
  console.log('');
  console.log(
    'Playwright examples (from monorepo root with devDependencies installed):'
  );
  console.log('  vibegame pw test');
  console.log('  vibegame pw test tests/playwright/simple-rpg-smoke.spec.ts');
  console.log('  vibegame pw test --ui');
  console.log('  vibegame pw install chromium');
  console.log('');
  console.log(
    'See playwright.config.ts for PLAYWRIGHT_CDP_WS, PLAYWRIGHT_CDP_URL, PLAYWRIGHT_BASE_URL.'
  );
  console.log('');
  console.log(`Library root: ${root}`);
}

/** @returns {string | null} */
function getLocalPlaywrightBin() {
  const binDir = join(root, 'node_modules', '.bin');
  if (process.platform === 'win32') {
    const cmd = join(binDir, 'playwright.cmd');
    return existsSync(cmd) ? cmd : null;
  }
  const sh = join(binDir, 'playwright');
  return existsSync(sh) ? sh : null;
}

function hasPlaywrightConfig() {
  return existsSync(join(root, 'playwright.config.ts'));
}

/**
 * @param {string[]} pwArgs arguments passed to the playwright CLI
 * @returns {never}
 */
function runPlaywright(pwArgs) {
  const localBin = getLocalPlaywrightBin();
  const hasConfig = hasPlaywrightConfig();

  if (!localBin && !hasConfig) {
    console.error(
      'vibegame playwright: no playwright.config.ts in the package root and no local Playwright CLI.'
    );
    console.error(`  Root: ${root}`);
    console.error(
      '  Install dev dependencies in the VibeGame monorepo folder (e.g. bun install), then:'
    );
    console.error('    bun run playwright:install');
    process.exit(1);
  }

  const opts = {
    cwd: root,
    stdio: 'inherit',
    env: process.env,
  };

  if (localBin) {
    const useShell = process.platform === 'win32' && localBin.endsWith('.cmd');
    const r = spawnSync(
      localBin,
      pwArgs,
      useShell ? { ...opts, shell: true } : opts
    );
    process.exit(r.status ?? 1);
  }

  const tryBunx = spawnSync('bunx', ['playwright', ...pwArgs], {
    ...opts,
    shell: true,
  });
  if (!tryBunx.error) {
    process.exit(tryBunx.status ?? 1);
  }

  const tryNpx = spawnSync('npx', ['--yes', 'playwright', ...pwArgs], {
    ...opts,
    shell: true,
  });
  if (!tryNpx.error) {
    process.exit(tryNpx.status ?? 1);
  }

  console.error(
    'vibegame playwright: could not run Playwright (no local CLI, bunx, or npx).'
  );
  console.error(
    '  In the VibeGame repo: bun install && bun run playwright:install'
  );
  process.exit(1);
}

const argv = process.argv.slice(2);
const first = argv[0];

if (first === 'create' || first === '-c') {
  const rest = argv.slice(1);
  const r = spawnSync(process.execPath, [createScript, ...rest], {
    stdio: 'inherit',
    cwd: process.cwd(),
  });
  process.exit(r.status ?? 1);
}

if (first === 'playwright' || first === 'pw') {
  const pwArgs = argv.slice(1);
  runPlaywright(pwArgs);
}

if (first === 'run' || first === 'r') {
  const runArgv = argv.slice(1);
  const opts = parseRunArgs(runArgv);
  const engineRoot = resolveEngineRootForRun(process.cwd());
  if (!engineRoot) {
    console.error(
      '[vibegame run] Não encontrei a engine VibeGame (pacote `vibegame` no disco).'
    );
    console.error(
      '  Rode a partir da pasta do repositório da engine, de `examples/...`, ou de um projeto com `"vibegame": "file:…"` no package.json.'
    );
    process.exit(1);
  }
  runVibegameRun(engineRoot, opts);
}

if (first === '--version' || first === '-v') {
  console.log(pkg.version);
  process.exit(0);
}

if (
  first === 'help' ||
  first === '--help' ||
  first === '-h' ||
  first === undefined
) {
  help();
  process.exit(first === undefined ? 0 : 0);
}

console.error(`Unknown command: ${first}`);
console.error('Try: vibegame help');
process.exit(1);
