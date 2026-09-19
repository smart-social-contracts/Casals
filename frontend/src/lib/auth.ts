import { AuthClient } from '@dfinity/auth-client';
import { derived, get, writable, type Readable } from 'svelte/store';
import type { Identity } from '@dfinity/agent';
import { checkIsCanisterController } from './controllerAccess';
import { checkCommanderAccess, holdsPermission } from './commanderAccess';
import { claimCommander, type ClaimedSlot } from './api';

export const identity = writable<Identity | null>(null);
export const isAuthenticated = writable(false);
export const principal = writable('');
/** null while checking; true/false once resolved for the current session. */
export const isController = writable<boolean | null>(null);
/** null while checking; true/false once resolved for the current session. */
export const isCommander = writable<boolean | null>(null);
/** Permission keys the session's commander grants add up to (`'*'` = all);
 * null while checking. Controllers are not commanders: check `isController` too. */
export const myPermissions = writable<Set<string> | '*' | null>(null);
/** True when the session may call an endpoint guarded by `key` — a controller,
 * or a commander whose grants include it. null while access is still resolving. */
export function canDo(key: string): Readable<boolean | null> {
  return derived([isController, myPermissions], ([ctrl, perms]) => {
    if (ctrl === true) return true;
    if (holdsPermission(perms, key)) return true;
    return ctrl === null || perms === null ? null : false;
  });
}
export interface AccessDeniedInfo {
  message: string;
  principal: string;
}

export const accessDenied = writable<AccessDeniedInfo | null>(null);

let _authClient: AuthClient | null = null;

// The identity that just logged in but is not (yet) a commander. It is kept
// alive while the Access Denied dialog is open so the user can redeem an
// access code with it; dismissing the dialog logs it out.
let _pendingIdentity: Identity | null = null;
let _pendingBackendCanisterId: string | undefined;

export async function dismissAccessDenied() {
  accessDenied.set(null);
  _pendingIdentity = null;
  _pendingBackendCanisterId = undefined;
  if (_authClient && (await _authClient.isAuthenticated()) && !get(isAuthenticated)) {
    await _authClient.logout();
  }
}

/**
 * Redeem a commander access code with the identity that was just denied
 * access. On success the session opens as usual. Returns the claimed slots.
 */
export async function claimAccessCode(code: string): Promise<ClaimedSlot[]> {
  const id = _pendingIdentity;
  if (!id) throw new Error('Log in first, then enter your access code.');
  const res = await claimCommander(code, id);
  const claimed = res.claimed ?? [];
  const ok = await _verifyLoginAccess(id, _pendingBackendCanisterId);
  if (!ok) throw new Error('Code accepted, but access could not be verified. Please log in again.');
  return claimed;
}

// Always use the production Internet Identity — it issues delegations that
// work with any replica (local or mainnet). The delegation is scoped to the
// current origin, so local and mainnet get separate identities automatically.
const II_URL = 'https://identity.ic0.app';

/** Internet Identity delegation lifetime (nanoseconds). */
const SESSION_MAX_TTL_NS = BigInt(7 * 24 * 60 * 60 * 1_000_000_000); // 1 week

/** AuthClient idle manager logs out after 10m by default; disable so TTL governs session length. */
const AUTH_CLIENT_OPTIONS = {
  idleOptions: { disableIdle: true },
} as const;

async function _getAuthClient(): Promise<AuthClient> {
  if (!_authClient) _authClient = await AuthClient.create(AUTH_CLIENT_OPTIONS);
  return _authClient;
}

function _clearSession() {
  identity.set(null);
  isAuthenticated.set(false);
  principal.set('');
  isController.set(false);
  isCommander.set(false);
  myPermissions.set(new Set());
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
  myPermissions.set(null);

  const [access, controller] = await Promise.all([
    checkCommanderAccess(caller),
    backendCanisterId ? checkIsCanisterController(id, backendCanisterId) : Promise.resolve(false),
  ]);
  const commander = access.commander;

  isCommander.set(commander);
  isController.set(controller);
  myPermissions.set(access.permissions);

  if (!commander && !controller) {
    // Keep the II delegation alive (no logout yet): the dialog lets the user
    // redeem an access code with this identity. Closing the dialog logs out.
    _pendingIdentity = id;
    _pendingBackendCanisterId = backendCanisterId;
    accessDenied.set({
      message:
        "You don't have commander access to this orchestra. Enter an access code if you were given one, or send your principal to the platform administrator so they can grant you commander access.",
      principal: caller,
    });
    _clearSession();
    return false;
  }

  _pendingIdentity = null;
  _pendingBackendCanisterId = undefined;
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
  _authClient = await _getAuthClient();
  const authed = await _authClient.isAuthenticated();
  if (authed) {
    await _verifyLoginAccess(_authClient.getIdentity(), backendCanisterId);
  }
}

export async function login(backendCanisterId?: string): Promise<boolean> {
  accessDenied.set(null);
  const client = await _getAuthClient();
  return new Promise<boolean>((resolve, reject) => {
    client.login({
      identityProvider: II_URL,
      maxTimeToLive: SESSION_MAX_TTL_NS,
      onSuccess: async () => {
        const ok = await _verifyLoginAccess(client.getIdentity(), backendCanisterId);
        resolve(ok);
      },
      onError: reject,
    });
  });
}

export async function logout() {
  _pendingIdentity = null;
  _pendingBackendCanisterId = undefined;
  if (!_authClient) return;
  await _authClient.logout();
  _clearSession();
  accessDenied.set(null);
}

/** Internet Identity login without Casals commander/controller gate (Baton / Multisig consoles). */
export async function loginInternetIdentity(): Promise<boolean> {
  accessDenied.set(null);
  const client = await _getAuthClient();
  return new Promise<boolean>((resolve, reject) => {
    client.login({
      identityProvider: II_URL,
      maxTimeToLive: SESSION_MAX_TTL_NS,
      onSuccess: async () => {
        _applyIdentity(client.getIdentity());
        isCommander.set(null);
        isController.set(null);
        myPermissions.set(null);
        accessDenied.set(null);
        resolve(true);
      },
      onError: reject,
    });
  });
}
