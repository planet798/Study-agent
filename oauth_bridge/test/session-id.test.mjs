import assert from 'node:assert/strict';
import test from 'node:test';
import { builtinModels } from '@earendil-works/pi-ai/providers/all';
import { completeRequest } from '../completion.mjs';
import { MemoryCredentials } from '../credentials.mjs';

for (const [label, fields] of [
  ['omitted', {}],
  ['null', { sessionId: null }],
  ['valid string', { sessionId: 'synthetic-session' }],
]) {
  test(`bridge builds a real Codex payload with ${label} session ID`, async () => {
    const accountId = 'synthetic-codex-account';
    const payload = Buffer.from(JSON.stringify({
      'https://api.openai.com/auth': { chatgpt_account_id: accountId },
    })).toString('base64url');
    const credential = {
      type: 'oauth', access: `header.${payload}.signature`,
      refresh: 'synthetic-refresh-token', expires: Date.now() + 3600000, accountId,
    };
    const credentials = new MemoryCredentials({ 'openai-codex': credential });
    const models = builtinModels({ credentials });
    const sdkStream = models.stream.bind(models);
    let requestBody;
    let forwardedSessionId;
    let networkCalls = 0;
    const stopBeforeNetwork = 'test stopped before network';

    // Exercise the bridge and actual SDK, stopping at the payload boundary.
    models.stream = (model, context, options) => {
      forwardedSessionId = options.sessionId;
      return sdkStream(model, context, {
        ...options,
        transport: 'sse',
        fetch: async () => {
          networkCalls++;
          throw new Error('unexpected network access');
        },
        onPayload(body) {
          requestBody = body;
          throw new Error(stopBeforeNetwork);
        },
      });
    };

    const events = [];
    const response = await completeRequest(models, credentials, {
      provider: 'openai-codex', model: 'gpt-6-luna', credential,
      messages: [{ role: 'user', content: 'Reply with OK.' }],
      temperature: 0.3, maxTokens: 64, ...fields,
    }, event => events.push(event), new AbortController().signal);

    assert.ok(requestBody, 'SDK must reach payload construction');
    assert.equal(forwardedSessionId, fields.sessionId ?? undefined);
    assert.equal(requestBody.model, 'gpt-6-luna');
    assert.equal(requestBody.prompt_cache_key, fields.sessionId ?? undefined);
    assert.equal(Object.hasOwn(requestBody, 'temperature'), false);
    assert.equal(response.finishReason, 'error');
    assert.equal(response.errorMessage, stopBeforeNetwork);
    assert.equal(networkCalls, 0);
    assert.deepEqual(events, []);
    assert.deepEqual(response.credential, credential);
  });
}
