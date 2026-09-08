import type { ArrangementStep } from '$lib/api';

export type ArrangementParameterType = 'text' | 'principal' | 'bool' | 'number' | 'sha256';

export interface ArrangementParameterSpec {
  type: ArrangementParameterType;
  label: string;
  description?: string;
  required?: boolean;
}

const PARAM_REF_RE = /^\$([A-Za-z_][A-Za-z0-9_]*)$/;

export function collectParameterRefs(value: unknown, found = new Set<string>()): string[] {
  if (typeof value === 'string') {
    const m = PARAM_REF_RE.exec(value);
    if (m) found.add(m[1]);
  } else if (Array.isArray(value)) {
    for (const item of value) collectParameterRefs(item, found);
  } else if (value && typeof value === 'object') {
    for (const item of Object.values(value as Record<string, unknown>)) {
      collectParameterRefs(item, found);
    }
  }
  return [...found].sort();
}

export function inferParameterSchema(
  schema: Record<string, ArrangementParameterSpec> | undefined,
  steps: ArrangementStep[],
): Record<string, ArrangementParameterSpec> {
  const out: Record<string, ArrangementParameterSpec> = { ...(schema ?? {}) };
  for (const ref of collectParameterRefs(steps)) {
    if (!out[ref]) {
      out[ref] = { type: 'text', label: ref, required: true };
    }
  }
  return out;
}

export async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

export async function buildApplyParameters(
  schema: Record<string, ArrangementParameterSpec>,
  values: Record<string, string | boolean>,
): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(schema)) {
    const raw = values[key];
    if (spec.type === 'bool') {
      out[key] = !!raw;
      continue;
    }
    const text = String(raw ?? '').trim();
    if (!text) continue;
    if (spec.type === 'sha256') {
      out[key] = await sha256Hex(text);
      continue;
    }
    if (spec.type === 'number') {
      out[key] = Number(text);
      continue;
    }
    out[key] = text;
  }
  return out;
}

export function validateApplyForm(
  schema: Record<string, ArrangementParameterSpec>,
  values: Record<string, string | boolean>,
): string | null {
  for (const [key, spec] of Object.entries(schema)) {
    if (!spec.required) continue;
    const raw = values[key];
    if (spec.type === 'bool') continue;
    if (!String(raw ?? '').trim()) {
      return `${spec.label || key} is required`;
    }
  }
  return null;
}
