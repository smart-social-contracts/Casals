/**
 * Commander access codes (mirrors `src/access_code.py`).
 *
 * A commander slot can be declared by the checksum of a secret code instead
 * of a principal: `sha256:<hex>`. Whoever redeems the code (`claim_commander`)
 * takes the slot. Only the checksum ever reaches the canister.
 */

export const CODE_CHECKSUM_PREFIX = 'sha256:';

// Crockford-style base32 without look-alikes (0/O, 1/I/L) — same alphabet the
// CLI uses (`casals code new`). 20 symbols ≈ 100 bits of entropy.
const CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTVWXYZ23456789';
const CODE_GROUPS = 4;
const CODE_GROUP_LEN = 5;

/** True when `value` is written in the `sha256:<hex>` slot form. */
export function isCodeChecksum(value: string | undefined | null): boolean {
  return (value ?? '').trim().toLowerCase().startsWith(CODE_CHECKSUM_PREFIX);
}

/** A fresh code such as `K7MQ2-XTR4V-9BCDF-HJ3NP`. */
export function generateAccessCode(): string {
  const bytes = new Uint8Array(CODE_GROUPS * CODE_GROUP_LEN);
  crypto.getRandomValues(bytes);
  const groups: string[] = [];
  for (let g = 0; g < CODE_GROUPS; g++) {
    let s = '';
    for (let i = 0; i < CODE_GROUP_LEN; i++) {
      // 30 symbols do not divide 256 evenly; the bias (≤0.4%) is irrelevant
      // for a one-time code, and keeps this dependency-free.
      s += CODE_ALPHABET[bytes[g * CODE_GROUP_LEN + i] % CODE_ALPHABET.length];
    }
    groups.push(s);
  }
  return groups.join('-');
}

/** `sha256:<hex>` of a code exactly as typed (surrounding whitespace stripped). */
export async function codeChecksum(code: string): Promise<string> {
  const data = new TextEncoder().encode(code.trim());
  const digest = await crypto.subtle.digest('SHA-256', data);
  const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
  return CODE_CHECKSUM_PREFIX + hex;
}

/** Short display form of a checksum: `sha256:2a42…482a`. */
export function shortChecksum(checksum: string): string {
  const hex = checksum.slice(CODE_CHECKSUM_PREFIX.length);
  if (hex.length <= 12) return checksum;
  return `${CODE_CHECKSUM_PREFIX}${hex.slice(0, 4)}…${hex.slice(-4)}`;
}
