import type { Tree } from './api';

/** Prefer a non-empty cached list; otherwise the live fetch. Empty `[]` is missing. */
export function coalesceControllers(cached?: string[], live?: string[]): string[] | undefined {
  if (cached?.length) return cached;
  if (live?.length) return live;
  return cached ?? live;
}

export function missingControllerIds(tree: Tree | null | undefined, extra: string[] = []): string[] {
  const ids = new Set<string>();
  if (tree) {
    for (const sec of tree.sections) {
      for (const stand of sec.stands) {
        for (const c of stand.canisters) {
          if (c.canister_id && !(c.controllers?.length)) ids.add(c.canister_id);
        }
      }
    }
  }
  for (const id of extra) {
    if (id) ids.add(id);
  }
  return [...ids];
}

export function allControllerLookupIds(tree: Tree | null | undefined, extra: string[] = []): string[] {
  const ids = new Set<string>();
  if (tree) {
    for (const sec of tree.sections) {
      for (const stand of sec.stands) {
        for (const c of stand.canisters) {
          if (c.canister_id) ids.add(c.canister_id);
        }
      }
    }
  }
  for (const id of extra) {
    if (id) ids.add(id);
  }
  return [...ids];
}

export function applyControllerMapToTree(
  tree: Tree,
  byId: Map<string, string[]>,
  opts: { force?: boolean } = {},
): Tree {
  const force = Boolean(opts.force);
  return {
    ...tree,
    sections: tree.sections.map((sec) => ({
      ...sec,
      stands: sec.stands.map((stand) => ({
        ...stand,
        canisters: stand.canisters.map((c) => {
          if (!c.canister_id) return c;
          if (!force && c.controllers?.length) return c;
          const live = byId.get(c.canister_id);
          return live?.length ? { ...c, controllers: live } : c;
        }),
      })),
    })),
  };
}
