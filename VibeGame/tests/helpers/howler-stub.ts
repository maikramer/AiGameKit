/**
 * Stub completo do global `Howler` para `mock.module('howler')`.
 *
 * O registo de mocks do bun test é global ao processo: o factory de um
 * ficheiro vaz para todos os que correm depois dele. Um factory que só
 * define `Howl` deixa `Howler` undefined nos ficheiros seguintes e rebenta
 * suites alheias (ex.: `TypeError: Howler.pos is not a function` no
 * default-entities). Espalhe este stub e sobreponha só o que o teste precisa:
 *
 *   mock.module('howler', () => ({ Howl: MockHowl, Howler: { ...HOWLER_GLOBAL_STUB } }));
 *   // variante suspended:
 *   mock.module('howler', () => ({
 *     Howl: MockHowl,
 *     Howler: { ...HOWLER_GLOBAL_STUB, ctx: { state: 'suspended', resume: () => {} } },
 *   }));
 */
export const HOWLER_GLOBAL_STUB = {
  pos: () => {},
  ctx: { state: 'running', resume: () => {} },
  autoSuspend: false,
  unload: () => {},
} as const;
