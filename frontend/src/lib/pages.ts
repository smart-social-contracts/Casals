/** Conductor list ops answer one page per list and `next: { list: id | null }`. */
export type Cursors = Record<string, number | null>;

/** The `<list>_from` args that continue a reply, or null once every list is complete. */
export function nextArgs(next: Cursors | undefined): Record<string, number | null> | null {
  if (!next || !Object.values(next).some((v) => v)) return null;
  return Object.fromEntries(Object.entries(next).map(([k, v]) => [`${k}_from`, v]));
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
