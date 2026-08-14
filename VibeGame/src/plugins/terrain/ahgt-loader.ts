import type { HeightSampler } from './height-sampler';
import {
  createHeightmapSampler,
  decodeHeightmapBlob,
  loadHeightmapFromUrl,
} from './height-sampler';
import { AHGT_MAGIC, parseAhgt } from './ahgt-format';
import { fetchBytesResilient } from '../../core/utils/resilient-net';

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

function isAhgt(buf: Uint8Array): boolean {
  return (
    buf.byteLength >= 4 &&
    new DataView(buf.buffer, buf.byteOffset, 4).getUint32(0, true) ===
      AHGT_MAGIC
  );
}

/**
 * Content type for a sniffed heightfield buffer. Extensionless URLs are
 * re-decoded as images from the bytes we already hold; Firefox's
 * `createImageBitmap` trusts the blob type, so derive it from magic instead of
 * shipping an empty one.
 */
function imageTypeFromMagic(b: Uint8Array): string {
  if (
    b.length >= 8 &&
    b[0] === 0x89 &&
    b[1] === 0x50 &&
    b[2] === 0x4e &&
    b[3] === 0x47
  ) {
    return 'image/png';
  }
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) {
    return 'image/jpeg';
  }
  if (
    b.length >= 12 &&
    b[8] === 0x57 &&
    b[9] === 0x45 &&
    b[10] === 0x42 &&
    b[11] === 0x50
  ) {
    return 'image/webp';
  }
  return 'application/octet-stream';
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
 * URL is sniffed by magic: AHGT bytes → ahgt, anything else → image decode of
 * the already-downloaded buffer (no second request).
 */
export async function loadHeightfield(
  url: string,
  fallbackWorldSize?: number,
  fallbackMaxHeight?: number
): Promise<HeightSampler> {
  const ext = heightfieldExtension(url);

  if (ext === 'ahgt') {
    let buf: Uint8Array;
    try {
      buf = await fetchBytesResilient(url);
    } catch (e) {
      throw new Error(`AHGT fetch failed: ${url} — ${e}`, { cause: e });
    }
    return parseAhgt(buf).sampler;
  }

  const isImage =
    ext === 'png' || ext === 'jpg' || ext === 'jpeg' || ext === 'webp';
  if (isImage || ext === '') {
    let sniffed: Blob | null = null;
    if (ext === '') {
      // No extension: sniff the first bytes for the AHGT magic before
      // falling back to the image decoder.
      let buf: Uint8Array;
      try {
        buf = await fetchBytesResilient(url);
      } catch (e) {
        throw new Error(`Heightfield fetch failed: ${url} — ${e}`, {
          cause: e,
        });
      }
      if (isAhgt(buf)) return parseAhgt(buf).sampler;
      // Uint8Array.from allocates a plain ArrayBuffer (BlobPart requires it).
      sniffed = new Blob([Uint8Array.from(buf)], {
        type: imageTypeFromMagic(buf),
      });
    }
    if (
      fallbackWorldSize === undefined ||
      fallbackMaxHeight === undefined ||
      !Number.isFinite(fallbackWorldSize) ||
      !Number.isFinite(fallbackMaxHeight) ||
      fallbackWorldSize <= 0 ||
      fallbackMaxHeight < 0
    ) {
      throw new Error(
        `Image heightmap requires finite worldSize > 0 and maxHeight >= 0, got: ${fallbackWorldSize}, ${fallbackMaxHeight}`
      );
    }
    const imgData = sniffed
      ? await decodeHeightmapBlob(sniffed, url)
      : await loadHeightmapFromUrl(url);
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
