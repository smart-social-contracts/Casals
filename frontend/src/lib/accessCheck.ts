import type { CommanderAccess } from './commanderAccess';

/**
 * Runs an access read. A failed read (network, certificate, instruction limit)
 * comes back as `error`: it says nothing about whether the caller is a commander.
 */
export async function settleAccess(read: () => Promise<CommanderAccess>): Promise<CommanderAccess> {
  try {
    return await read();
  } catch (e) {
    return { commander: false, permissions: new Set(), error: e instanceof Error ? e.message : String(e) };
  }
}
