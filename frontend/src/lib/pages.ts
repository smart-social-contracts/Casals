/** Conductor list ops answer one page per list and `next: { list: id | null }`. */
export type Cursors = Record<string, number | null>;

/** The `<list>_from` args that continue a reply, or null once every list is complete. */
export function nextArgs(next: Cursors | undefined): Record<string, number | null> | null {
  if (!next || !Object.values(next).some((v) => v)) return null;
  return Object.fromEntries(Object.entries(next).map(([k, v]) => [`${k}_from`, v]));
}

/** Rows per list in one query. The conductor's default (500) is past the IC's instruction limit on a grown
 *  orchestra: each canister row costs tens of millions of instructions. */
export const PAGE = 25;

const overLimit = (e: unknown) => /instruction/i.test(String((e as Error)?.message ?? e));

/** A paged list op read whole through `ask` (one query), from its first reply `got` when the caller has it:
 *  `next` goes back until every list is complete. Pages hold `count` rows (default `PAGE`); one past the
 *  instruction limit is asked again with half. */
export async function readAll<T>(
  ask: (args: Record<string, unknown>) => Promise<any>,
  args: Record<string, unknown> = {},
  got?: any,
): Promise<T> {
  let count = Number(args.count ?? PAGE);
  const page = async (more: Record<string, unknown>) => {
    for (;;) {
      try {
        return await ask({ ...args, ...more, count });
      } catch (e) {
        if (count <= 1 || !overLimit(e)) throw e;
        count = Math.max(1, Math.floor(count / 2));
      }
    }
  };
  got ??= await page({});
  const out = { ...got };
  for (let more = nextArgs(got.next as Cursors); more; more = nextArgs(got.next as Cursors)) {
    got = await page(more);
    appendPage(out, got);
  }
  delete out.next;
  return out as T;
}

/** Every list of a later page appended to the reply so far. */
export function appendPage(out: Record<string, any>, page: Record<string, any>): Record<string, any> {
  for (const [k, v] of Object.entries(page)) if (Array.isArray(v)) out[k] = [...(out[k] ?? []), ...v];
  return out;
}

type ExportSection = { name: string; stands?: unknown[]; [key: string]: unknown };

/** Export sections merged by name, in the order of their full rows; a `{ name, stands }` row carries
 *  stands whose section came on another page. */
export function mergeSections(rows: ExportSection[]): ExportSection[] {
  const full = new Map<string, ExportSection>();
  const stands = new Map<string, unknown[]>();
  for (const row of rows) {
    stands.set(row.name, [...(stands.get(row.name) ?? []), ...(row.stands ?? [])]);
    if (Object.keys(row).some((k) => k !== 'name' && k !== 'stands') && !full.has(row.name)) full.set(row.name, row);
  }
  const names = [...full.keys(), ...[...stands.keys()].filter((n) => !full.has(n))];
  return names.map((n) => ({ ...(full.get(n) ?? { name: n }), stands: stands.get(n) ?? [] }));
}
