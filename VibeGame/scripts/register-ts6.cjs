/**
 * Redirect `require('typescript')` / `import 'typescript'` to `@typescript/typescript6`.
 *
 * TypeScript 7 ships a native compiler without the JS Compiler API that
 * typescript-eslint still needs. Keep `typescript@7` for `tsc`; use this
 * preload only for ESLint.
 */
const Module = require('module');
const ts6 = require.resolve('@typescript/typescript6');

const originalResolveFilename = Module._resolveFilename;
Module._resolveFilename = function (request, parent, isMain, options) {
  if (request === 'typescript') {
    return ts6;
  }
  return originalResolveFilename.call(this, request, parent, isMain, options);
};
