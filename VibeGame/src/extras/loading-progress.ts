/**
 * Loading progress tracking for VibeGame asset loading.
 * Provides a simple counter-based progress system for scene loading.
 */

export interface LoadingProgressOptions {
  totalItems: number;
  onProgress?: (loaded: number, total: number, percent: number) => void;
  onComplete?: () => void;
}

export class LoadingProgress {
  private loaded = 0;
  private total: number;
  private onProgress?: (loaded: number, total: number, percent: number) => void;
  private onComplete?: () => void;

  constructor(options: LoadingProgressOptions) {
    this.total = options.totalItems;
    this.onProgress = options.onProgress;
    this.onComplete = options.onComplete;
  }

  get progress(): number {
    return this.total > 0 ? this.loaded / this.total : 0;
  }

  get percent(): number {
    return Math.round(this.progress * 100);
  }

  get isComplete(): boolean {
    return this.loaded >= this.total;
  }

  itemLoaded(): void {
    this.loaded++;
    this.onProgress?.(this.loaded, this.total, this.percent);
    if (this.isComplete) {
      this.onComplete?.();
    }
  }

  reset(total?: number): void {
    this.loaded = 0;
    if (total !== undefined) {
      this.total = total;
    }
  }
}

/**
 * Create a loading progress tracker and wrap a list of async loaders.
 * Each loader receives an `onComplete` callback that advances progress.
 */
export async function loadWithProgress<T>(
  loaders: Array<() => Promise<T>>,
  options?: LoadingProgressOptions
): Promise<T[]> {
  const progress = new LoadingProgress({
    totalItems: loaders.length,
    ...options,
  });

  const results: T[] = [];
  for (const loader of loaders) {
    const result = await loader();
    progress.itemLoaded();
    results.push(result);
  }

  return results;
}
