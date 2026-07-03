import type { HeightSampler } from './height-sampler';
import { createHeightmapSampler, loadHeightmapFromUrl } from './height-sampler';
import { AHGT_MAGIC, parseAhgt } from './ahgt-format';

function extOf(name: string | undefined): string {
  if (!name) return '';
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : '';
}

/**
 * Extension of a heightfield URL. The hash fragment wins when it carries one
 * (`data:...#terrain.ahgt` — data: URLs have no pathname to inspect), then the
 * last path segment of the URL without query/hash.
 */
function heightfieldExtension(url: string): string {
  const [base, hash] = url.split('#');
  const clean = base!.split('?')[0]!;
  return extOf(hash) || extOf(clean.split('/').pop());
}

/**
 * Load a heightfield from a URL, dispatching on the file extension:
 *  - `.ahgt` → binary uint16+deflate format (parseAhgt)
 *  - `.png`/`.jpg`/`.jpeg`/`.webp` → legacy luminance decode (loadHeightmapFromUrl)
 *
 * PNG is the default for retrocompat: existing `<Terrain url="...png">` markup
 * keeps working unchanged. `.ahgt` is opt-in for higher precision (3mm vs 0.78m).
 *
 * For `.ahgt`, the URL is fetched as a binary blob and parsed; worldSize /
 * maxHeight come from the file's metadata block. For images, the caller still
 * supplies worldSize/maxHeight (matching the existing API). An extensionless
 * URL is sniffed by magic: AHGT bytes → ahgt, anything else → image decode.
 */
export async function loadHeightfield(
  url: string,
  fallbackWorldSize?: number,
  fallbackMaxHeight?: number
): Promise<HeightSampler> {
  const ext = heightfieldExtension(url);

  if (ext === 'ahgt') {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`AHGT fetch ${response.status}: ${url}`);
    }
    const buf = new Uint8Array(await response.arrayBuffer());
    return parseAhgt(buf).sampler;
  }

  const isImage =
    ext === 'png' || ext === 'jpg' || ext === 'jpeg' || ext === 'webp';
  if (isImage || ext === '') {
    if (ext === '') {
      // No extension: sniff the first bytes for the AHGT magic before
      // falling back to the image decoder.
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`Heightfield fetch ${response.status}: ${url}`);
      }
      const buf = new Uint8Array(await response.arrayBuffer());
      if (
        buf.byteLength >= 4 &&
        new DataView(buf.buffer, buf.byteOffset).getUint32(0, true) ===
          AHGT_MAGIC
      ) {
        return parseAhgt(buf).sampler;
      }
    }
    if (fallbackWorldSize === undefined || fallbackMaxHeight === undefined) {
      throw new Error(
        `Image heightmap requires worldSize and maxHeight, got: ${fallbackWorldSize}, ${fallbackMaxHeight}`
      );
    }
    const imgData = await loadHeightmapFromUrl(url);
    return createHeightmapSampler(
      fallbackWorldSize,
      fallbackMaxHeight,
      imgData
    );
  }

  throw new Error(
    `Unknown heightmap format (extension "${ext || '(none)'}") for: ${url}`
  );
}
