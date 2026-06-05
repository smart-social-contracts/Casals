import { AuthClient } from '@dfinity/auth-client';
import { writable } from 'svelte/store';
import type { Identity } from '@dfinity/agent';

export const identity = writable<Identity | null>(null);
export const isAuthenticated = writable(false);
export const principal = writable('');

let _authClient: AuthClient | null = null;

// Always use the production Internet Identity — it issues delegations that
// work with any replica (local or mainnet). The delegation is scoped to the
// current origin, so local and mainnet get separate identities automatically.
const II_URL = 'https://identity.ic0.app';

function _applyIdentity(id: Identity) {
  identity.set(id);
  isAuthenticated.set(true);
  principal.set(id.getPrincipal().toText());
}

export async function initAuth() {
  _authClient = await AuthClient.create();
  const authed = await _authClient.isAuthenticated();
  if (authed) {
    _applyIdentity(_authClient.getIdentity());
  }
}

export async function login() {
  if (!_authClient) _authClient = await AuthClient.create();
  return new Promise<void>((resolve, reject) => {
    _authClient!.login({
      identityProvider: II_URL,
      maxTimeToLive: BigInt(7 * 24 * 60 * 60 * 1_000_000_000),
      onSuccess: () => {
        _applyIdentity(_authClient!.getIdentity());
        resolve();
      },
      onError: reject,
    });
  });
}

export async function logout() {
  if (!_authClient) return;
  await _authClient.logout();
  identity.set(null);
  isAuthenticated.set(false);
  principal.set('');
}
