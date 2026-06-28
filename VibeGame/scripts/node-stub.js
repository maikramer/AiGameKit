export const existsSync = () => false;
export const mkdirSync = () => {};
export const readFileSync = () => {
  throw new Error('node:fs is not available in the browser');
};
export const writeFileSync = () => {
  throw new Error('node:fs is not available in the browser');
};
export const renameSync = () => {
  throw new Error('node:fs is not available in the browser');
};
export const unlinkSync = () => {};
export const readdirSync = () => [];
export const statSync = () => {
  throw new Error('node:fs is not available in the browser');
};
export const createReadStream = () => {
  throw new Error('node:fs is not available in the browser');
};
export const createWriteStream = () => {
  throw new Error('node:fs is not available in the browser');
};
export const promises = {
  readFile: () =>
    Promise.reject(new Error('node:fs is not available in the browser')),
  writeFile: () =>
    Promise.reject(new Error('node:fs is not available in the browser')),
  mkdir: () => Promise.resolve(),
  stat: () =>
    Promise.reject(new Error('node:fs is not available in the browser')),
  access: () =>
    Promise.reject(new Error('node:fs is not available in the browser')),
};

export const resolve = (...parts) => parts.join('/');
export const join = (...parts) => parts.join('/');
export const dirname = (p) => p.split('/').slice(0, -1).join('/') || '.';
export const basename = (p) => p.split('/').pop() ?? '';
export const extname = (p) => {
  const base = basename(p);
  const dot = base.lastIndexOf('.');
  return dot <= 0 ? '' : base.slice(dot);
};
export const relative = (from, to) => to;
export const parse = (p) => ({
  root: '',
  dir: dirname(p),
  base: basename(p),
  ext: extname(p),
  name: basename(p).replace(/\.[^.]+$/, ''),
});
export const sep = '/';
export const delimiter = ':';
export const isAbsolute = () => false;
export const normalize = (p) => p;
export default {
  resolve,
  join,
  dirname,
  basename,
  extname,
  relative,
  parse,
  sep,
  delimiter,
  isAbsolute,
  normalize,
};
