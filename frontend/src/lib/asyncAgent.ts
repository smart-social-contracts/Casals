import {
  HttpAgent,
  type HttpAgentOptions,
  type Identity,
  type Nonce,
  type SubmitResponse,
} from '@dfinity/agent';
import type { Principal } from '@dfinity/principal';

// Sync /api/v3/call can return HTTP 200 with request_status "processing" when
// execution outlives the boundary wait window; the actor layer then throws even
// though the call succeeds on-chain. Async v2 (callSync: false) polls correctly.
class AsyncHttpAgent extends HttpAgent {
  override call(
    canisterId: Principal | string,
    options: {
      methodName: string;
      arg: ArrayBuffer;
      effectiveCanisterId?: Principal | string;
      callSync?: boolean;
      nonce?: Uint8Array | Nonce;
    },
    identity?: Identity | Promise<Identity>,
  ): Promise<SubmitResponse> {
    return super.call(canisterId, { ...options, callSync: false }, identity);
  }
}

export function createHttpAgent(options: HttpAgentOptions = {}): HttpAgent {
  return new AsyncHttpAgent(options);
}
