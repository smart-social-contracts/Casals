/** Browser snapshot of the last Orchestra tree (scoped per Casals backend). */

export type CachedOrchestraTree = {
  sections: unknown[];
  principal_aliases?: Record<string, string>;
};

export type TreeCacheStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

/** Open-Orchestra plan: paint cache immediately, fetch live in the background. */
export type OrchestraOpenPlan<T extends CachedOrchestraTree = CachedOrchestraTree> = {
  tree: T | null;
  /** True when the page must block on a live get_tree before first paint. */
  waitForLive: boolean;
  /** Always true — live get_tree still refreshes after paint (or is the first load). */
  fetchLive: boolean;
};

export function treeCacheKey(backendId: string): string {
  return `casals.orchestraTree.${backendId || 'default'}`;
}

export function parseCachedTree(raw: string | null | undefined): CachedOrchestraTree | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.sections)) return null;
    return parsed as CachedOrchestraTree;
  } catch {
    return null;
  }
}

export function readCachedTree(
  backendId: string,
  storage?: TreeCacheStorage | null,
): CachedOrchestraTree | null {
  if (!storage) return null;
  try {
    return parseCachedTree(storage.getItem(treeCacheKey(backendId)));
  } catch {
    return null;
  }
}

export function writeCachedTree(
  backendId: string,
  tree: CachedOrchestraTree | null | undefined,
  storage?: TreeCacheStorage | null,
): void {
  if (!storage || !tree || !Array.isArray(tree.sections)) return;
  try {
    storage.setItem(treeCacheKey(backendId), JSON.stringify(tree));
  } catch {
    /* quota / private mode */
  }
}

export function orchestraOpenPlan<T extends CachedOrchestraTree>(
  cached: T | null,
): OrchestraOpenPlan<T> {
  if (cached && Array.isArray(cached.sections)) {
    return { tree: cached, waitForLive: false, fetchLive: true };
  }
  return { tree: null, waitForLive: true, fetchLive: true };
}

export function browserTreeStorage(): TreeCacheStorage | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    return localStorage;
  } catch {
    return null;
  }
}
