import { AuthClient } from '@dfinity/auth-client';
import { get, writable } from 'svelte/store';
import type { Identity } from '@dfinity/agent';
import { checkIsCanisterController } from './controllerAccess';
import { checkIsCommander } from './commanderAccess';

export const identity = writable<Identity | null>(null);
export const isAuthenticated = writable(false);
export const principal = writable('');
/** null while checking; true/false once resolved for the current session. */
export const isController = writable<boolean | null>(null);
/** null while checking; true/false once resolved for the current session. */
export const isCommander = writable<boolean | null>(null);
export interface AccessDeniedInfo {
  message: string;
  principal: string;
}

export const accessDenied = writable<AccessDeniedInfo | null>(null);

export function dismissAccessDenied() {
  accessDenied.set(null);
}

let _authClient: AuthClient | null = null;

// Always use the production Internet Identity — it issues delegations that
// work with any replica (local or mainnet). The delegation is scoped to the
// current origin, so local and mainnet get separate identities automatically.
const II_URL = 'https://identity.ic0.app';

function _clearSession() {
  identity.set(null);
  isAuthenticated.set(false);
  principal.set('');
  isController.set(false);
  isCommander.set(false);
}

function _applyIdentity(id: Identity) {
  identity.set(id);
  isAuthenticated.set(true);
  principal.set(id.getPrincipal().toText());
}

async function _verifyLoginAccess(id: Identity, backendCanisterId?: string): Promise<boolean> {
  const caller = id.getPrincipal().toText();
  isCommander.set(null);
  isController.set(null);

  const [commander, controller] = await Promise.all([
    checkIsCommander(caller),
    backendCanisterId ? checkIsCanisterController(id, backendCanisterId) : Promise.resolve(false),
  ]);

  isCommander.set(commander);
  isController.set(controller);

  if (!commander && !controller) {
    accessDenied.set({
      message: 'Log in with a principal listed on the Commanders page.',
      principal: caller,
    });
    if (_authClient) await _authClient.logout();
    _clearSession();
    return false;
  }

  accessDenied.set(null);
  _applyIdentity(id);
  return true;
}

export async function refreshControllerAccess(backendCanisterId?: string) {
  const id = get(identity);
  if (!id || !backendCanisterId) {
    isController.set(false);
    return;
  }
  isController.set(null);
  isController.set(await checkIsCanisterController(id, backendCanisterId));
}

export async function initAuth(backendCanisterId?: string) {
  _authClient = await AuthClient.create();
  const authed = await _authClient.isAuthenticated();
  if (authed) {
    await _verifyLoginAccess(_authClient.getIdentity(), backendCanisterId);
  }
}

export async function login(backendCanisterId?: string): Promise<boolean> {
  accessDenied.set(null);
  if (!_authClient) _authClient = await AuthClient.create();
  return new Promise<boolean>((resolve, reject) => {
    _authClient!.login({
      identityProvider: II_URL,
      maxTimeToLive: BigInt(7 * 24 * 60 * 60 * 1_000_000_000),
      onSuccess: async () => {
        const ok = await _verifyLoginAccess(_authClient!.getIdentity(), backendCanisterId);
        resolve(ok);
      },
      onError: reject,
    });
  });
}

export async function logout() {
  if (!_authClient) return;
  await _authClient.logout();
  _clearSession();
  accessDenied.set(null);
}

/** Internet Identity login without Casals commander/controller gate (Baton / Multisig consoles). */
export async function loginInternetIdentity(): Promise<boolean> {
  accessDenied.set(null);
  if (!_authClient) _authClient = await AuthClient.create();
  return new Promise<boolean>((resolve, reject) => {
    _authClient!.login({
      identityProvider: II_URL,
      maxTimeToLive: BigInt(7 * 24 * 60 * 60 * 1_000_000_000),
      onSuccess: async () => {
        _applyIdentity(_authClient!.getIdentity());
        isCommander.set(null);
        isController.set(null);
        accessDenied.set(null);
        resolve(true);
      },
      onError: reject,
    });
  });
}
