/** Subnet geography from the public IC Dashboard API (ic-api.internetcomputer.org). */

const IC_API = 'https://ic-api.internetcomputer.org';

export type GeoRegion = 'USA' | 'EU' | 'MidEast' | 'APAC' | 'Other';

export const GEO_REGION_ORDER: GeoRegion[] = ['USA', 'EU', 'MidEast', 'APAC', 'Other'];

/** Human-readable region labels for the subnet flag matrix. */
export const GEO_REGION_LABELS: Record<GeoRegion, string> = {
  USA: 'Americas',
  EU: 'Europe',
  MidEast: 'Mid East',
  APAC: 'APAC',
  Other: 'Other',
};

export interface RegionFlagGroup {
  region: GeoRegion;
  label: string;
  countries: CountryFlag[];
}

export interface CountryFlag {
  code: string;
  flag: string;
  name: string;
  region: GeoRegion;
}

export type SubnetAuthorization = 'public' | 'authorized_only' | string;

export interface SubnetGeo {
  subnetId: string;
  countryCodes: string[];
  flags: string[];
  countryNames: string[];
  /** Countries sorted USA → EU → MidEast → APAC → Other, then alphabetically within each. */
  orderedCountries: CountryFlag[];
  dataCenters: string[];
  subnetType?: string;
  nodeCount?: number;
  /** IC Dashboard: ``public`` subnets are CMC defaults; ``authorized_only`` need explicit CMC authorization. */
  subnetAuthorization?: SubnetAuthorization;
}

type SubnetApiPayload = {
  subnet_id?: string;
  subnet_type?: string;
  subnet_authorization?: string;
  total_nodes?: number | string;
  data_centers?: Array<{ name?: string; region?: string; owner?: string }>;
};

type SubnetListItem = SubnetApiPayload & { subnet_id?: string };

export interface IcSubnetSummary {
  subnetId: string;
  subnetAuthorization?: SubnetAuthorization;
  subnetType?: string;
}

const geoCache = new Map<string, SubnetGeo>();
const canisterSubnetCache = new Map<string, string>();

const regionNames =
  typeof Intl !== 'undefined' ? new Intl.DisplayNames(['en'], { type: 'region' }) : null;

const USA_CODES = new Set(['US', 'CA', 'MX', 'PR', 'GU', 'VI']);
const EU_CODES = new Set([
  'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT',
  'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE', 'GB', 'CH', 'NO',
  'IS', 'LI', 'IM', 'JE', 'GG', 'AD', 'MC', 'SM', 'VA',
]);
const MIDEAST_CODES = new Set([
  'AE', 'SA', 'IL', 'TR', 'EG', 'JO', 'LB', 'QA', 'BH', 'KW', 'OM', 'IQ', 'IR', 'PS', 'YE',
  'SY', 'CY',
]);
const APAC_CODES = new Set([
  'JP', 'KR', 'CN', 'IN', 'AU', 'NZ', 'SG', 'HK', 'TW', 'TH', 'MY', 'ID', 'PH', 'VN', 'KZ',
  'UZ', 'PK', 'BD', 'LK', 'NP', 'MM', 'KH', 'LA', 'MN', 'MO', 'BN', 'FJ', 'PG', 'NC',
]);

/** Classify an ISO 3166-1 alpha-2 country code into a display region. */
export function classifyCountry(code: string): GeoRegion {
  const c = (code || '').toUpperCase();
  if (!c) return 'Other';
  if (USA_CODES.has(c)) return 'USA';
  if (EU_CODES.has(c)) return 'EU';
  if (MIDEAST_CODES.has(c)) return 'MidEast';
  if (APAC_CODES.has(c)) return 'APAC';
  return 'Other';
}

/** Parse "Europe,RO,Bucharest" → "RO". */
export function countryCodeFromRegion(region: string): string {
  const parts = (region || '').split(',').map((p) => p.trim());
  const code = parts.length >= 2 ? parts[1] : '';
  return /^[A-Za-z]{2}$/.test(code) ? code.toUpperCase() : '';
}

/** ISO 3166-1 alpha-2 → flag emoji (e.g. "RO" → 🇷🇴). */
export function countryCodeToFlag(code: string): string {
  const c = (code || '').toUpperCase();
  if (c.length !== 2) return '';
  return String.fromCodePoint(...[...c].map((ch) => 0x1f1e6 - 65 + ch.charCodeAt(0)));
}

function countryName(code: string): string {
  try {
    return regionNames?.of(code) ?? code;
  } catch {
    return code;
  }
}

function regionRank(region: GeoRegion): number {
  return GEO_REGION_ORDER.indexOf(region);
}

/** Sort country codes: region order, then name within region. */
export function orderCountryCodes(codes: string[]): CountryFlag[] {
  const unique = [...new Set(codes.map((c) => c.toUpperCase()).filter(Boolean))];
  const entries = unique.map((code) => ({
    code,
    flag: countryCodeToFlag(code),
    name: countryName(code),
    region: classifyCountry(code),
  }));
  entries.sort((a, b) => {
    const dr = regionRank(a.region) - regionRank(b.region);
    if (dr !== 0) return dr;
    return a.name.localeCompare(b.name);
  });
  return entries;
}

/** Union of country codes across many subnets, ordered by region then name. */
export function unionCountryColumns(geos: (SubnetGeo | null | undefined)[]): CountryFlag[] {
  const codes = new Set<string>();
  for (const geo of geos) {
    for (const code of geo?.countryCodes ?? []) codes.add(code.toUpperCase());
  }
  return orderCountryCodes([...codes]);
}

/** Group ordered countries by region for the flag matrix header and rows. */
export function buildRegionGroups(columns: CountryFlag[]): RegionFlagGroup[] {
  const groups: RegionFlagGroup[] = [];
  for (const region of GEO_REGION_ORDER) {
    const countries = columns.filter((c) => c.region === region);
    if (countries.length) {
      groups.push({ region, label: GEO_REGION_LABELS[region], countries });
    }
  }
  return groups;
}

function buildGeo(subnetId: string, payload: SubnetApiPayload): SubnetGeo {
  const codes = new Set<string>();
  const dcs: string[] = [];
  for (const dc of payload.data_centers ?? []) {
    const code = countryCodeFromRegion(dc.region ?? '');
    if (code) codes.add(code);
    const label = [dc.name, dc.region?.split(',').slice(2).join(', ')].filter(Boolean).join(', ');
    if (label) dcs.push(label);
  }
  const orderedCountries = orderCountryCodes([...codes]);
  const countryCodes = orderedCountries.map((c) => c.code);
  return {
    subnetId,
    countryCodes,
    flags: orderedCountries.map((c) => c.flag),
    countryNames: orderedCountries.map((c) => c.name),
    orderedCountries,
    dataCenters: dcs,
    subnetType: payload.subnet_type,
    nodeCount: payload.total_nodes != null ? Number(payload.total_nodes) : undefined,
    subnetAuthorization: payload.subnet_authorization,
  };
}

/** Fetch geography for one subnet (cached). */
export async function getSubnetGeo(subnetId: string): Promise<SubnetGeo | null> {
  const id = (subnetId || '').trim();
  if (!id) return null;
  const hit = geoCache.get(id);
  if (hit) return hit;

  const res = await fetch(`${IC_API}/api/v4/subnets/${encodeURIComponent(id)}`);
  if (!res.ok) return null;
  const payload = (await res.json()) as SubnetApiPayload;
  const geo = buildGeo(id, payload);
  geoCache.set(id, geo);
  return geo;
}

/** Resolve a canister's subnet via the IC Dashboard API (cached). */
export async function resolveCanisterSubnet(canisterId: string): Promise<string | null> {
  const id = (canisterId || '').trim();
  if (!id) return null;
  const hit = canisterSubnetCache.get(id);
  if (hit) return hit;

  const res = await fetch(`${IC_API}/api/v3/canisters/${encodeURIComponent(id)}`);
  if (!res.ok) return null;
  const payload = (await res.json()) as { subnet_id?: string };
  const subnet = (payload.subnet_id || '').trim();
  if (subnet) canisterSubnetCache.set(id, subnet);
  return subnet || null;
}

/** List all subnets from the IC Dashboard API (paginated). */
export async function listIcSubnets(): Promise<IcSubnetSummary[]> {
  const rows: IcSubnetSummary[] = [];
  let after: string | undefined;
  for (let page = 0; page < 20; page++) {
    const url = new URL(`${IC_API}/api/v4/subnets`);
    url.searchParams.set('limit', '100');
    if (after) url.searchParams.set('after', after);
    const res = await fetch(url.toString());
    if (!res.ok) break;
    const body = (await res.json()) as { data?: SubnetListItem[]; after?: string };
    for (const row of body.data ?? []) {
      const sid = (row.subnet_id || '').trim();
      if (sid) {
        rows.push({
          subnetId: sid,
          subnetAuthorization: row.subnet_authorization,
          subnetType: row.subnet_type,
        });
      }
    }
    after = body.after;
    if (!after || !(body.data?.length)) break;
  }
  return rows;
}

/** List subnet principals from the IC Dashboard API (paginated). */
export async function listIcSubnetIds(): Promise<string[]> {
  const rows = await listIcSubnets();
  return rows.map((r) => r.subnetId);
}

/** Prefetch geo for many subnet ids (deduped, parallel, cache-friendly). */
export async function warmSubnetGeoCache(subnetIds: string[]): Promise<void> {
  const pending = [...new Set(subnetIds.map((s) => s.trim()).filter(Boolean))].filter(
    (id) => !geoCache.has(id),
  );
  await Promise.all(pending.map((id) => getSubnetGeo(id)));
}

/** Tooltip text summarizing node locations for a subnet. */
export function subnetGeoTitle(geo: SubnetGeo | null | undefined): string {
  if (!geo) return '';
  const parts: string[] = [];
  if (geo.orderedCountries.length) {
    const byRegion = new Map<GeoRegion, string[]>();
    for (const c of geo.orderedCountries) {
      if (!byRegion.has(c.region)) byRegion.set(c.region, []);
      byRegion.get(c.region)!.push(c.name);
    }
    const regionText = GEO_REGION_ORDER.filter((r) => byRegion.has(r))
      .map((r) => `${r}: ${byRegion.get(r)!.join(', ')}`)
      .join(' · ');
    parts.push(regionText);
  }
  if (geo.nodeCount != null) parts.push(`${geo.nodeCount} nodes`);
  if (geo.subnetType) parts.push(`type: ${geo.subnetType}`);
  if (geo.subnetAuthorization === 'authorized_only') {
    parts.push('Authorized only (not a CMC default subnet)');
  }
  if (geo.dataCenters.length) {
    parts.push(`Data centers: ${geo.dataCenters.slice(0, 8).join('; ')}${geo.dataCenters.length > 8 ? '…' : ''}`);
  }
  return parts.join(' · ');
}

export function shortSubnetId(id: string): string {
  const s = (id || '').trim();
  if (s.length <= 13) return s;
  return `${s.slice(0, 5)}…${s.slice(-5)}`;
}

/** First five characters of a subnet principal (e.g. ``4ecnw``). */
export function subnetShortLabel(id: string): string {
  return (id || '').trim().slice(0, 5);
}
