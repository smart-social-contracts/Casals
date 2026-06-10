import { Principal } from '@dfinity/principal';

const ACCOUNT_DOMAIN = new TextEncoder().encode('\x0Aaccount-id');
const DEFAULT_SUBACCOUNT = new Uint8Array(32);

/** CRC-32 (IEEE) for IC ledger AccountIdentifier checksum. */
function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const b of bytes) {
    crc ^= b;
    for (let i = 0; i < 8; i++) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function hex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Derive the 64-char hex ICP ledger account id for a canister's default account. */
export async function ledgerAccountIdFromCanister(canisterId: string): Promise<string> {
  const principal = Principal.fromText(canisterId.trim());
  const payload = new Uint8Array([
    ...ACCOUNT_DOMAIN,
    ...principal.toUint8Array(),
    ...DEFAULT_SUBACCOUNT,
  ]);
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-224', payload));
  const checksum = crc32(digest);
  const out = new Uint8Array(32);
  out[0] = (checksum >>> 24) & 0xff;
  out[1] = (checksum >>> 16) & 0xff;
  out[2] = (checksum >>> 8) & 0xff;
  out[3] = checksum & 0xff;
  out.set(digest, 4);
  return hex(out);
}
