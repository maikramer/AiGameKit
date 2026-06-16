const noop = () => undefined;
const empty = {};

export default empty;
export const readFileSync = () => '';
export const statSync = () => ({ isDirectory: () => false });
export const readdirSync = () => [];
export const existsSync = () => false;
export const realpathSync = () => '';
export const promises = empty;
export {
  noop as accessSync,
  noop as mkdirSync,
  noop as writeFileSync,
  noop as rmSync,
  noop as copyFileSync,
  noop as watch,
};
