import assert from 'node:assert/strict';
import test from 'node:test';
import { builtinModels } from '@earendil-works/pi-ai/providers/all';
import { buildCompletionOptions } from '../request-options.mjs';

test('Codex options omit temperature for explicit, zero, and missing values', () => {
  const model = { api: 'openai-codex-responses' };
  for (const temperature of [0.3, 0, undefined, null]) {
    const options = buildCompletionOptions(model, { temperature, maxTokens: 64 });
    assert.equal(Object.hasOwn(options, 'temperature'), false);
    assert.equal(options.maxTokens, 64);
  }
});

test('other APIs preserve explicit temperature and the existing default', () => {
  const model = { api: 'openai-completions' };
  assert.equal(
    buildCompletionOptions(model, { temperature: 0.7 }).temperature,
    0.7,
  );
  assert.equal(buildCompletionOptions(model, {}).temperature, 0.3);
  assert.equal(buildCompletionOptions(model, { temperature: 0 }).temperature, 0);
});

test('pi-ai Codex request body omits temperature before any network access', async () => {
  const accountId = 'synthetic-codex-account';
  const payload = Buffer.from(JSON.stringify({
    'https://api.openai.com/auth': { chatgpt_account_id: accountId },
  })).toString('base64url');
  const credential = {
    type: 'oauth',
    access: `header.${payload}.signature`,
    refresh: 'synthetic-refresh-token',
    expires: Date.now() + 60 * 60 * 1000,
    accountId,
  };
  const credentials = {
    async read() { return credential; },
    async list() { return [{ providerId: 'openai-codex', type: 'oauth' }]; },
    async modify(_providerId, update) { return update(credential); },
    async delete() {},
  };
  const models = builtinModels({ credentials });
  const model = models.getModel('openai-codex', 'gpt-6-luna');
  assert.ok(model);

  let requestBody;
  const stopBeforeNetwork = 'test stopped before network';
  const response = await models.complete(model, {
    systemPrompt: '',
    messages: [{ role: 'user', content: 'Reply OK.', timestamp: Date.now() }],
    tools: [],
  }, {
    ...buildCompletionOptions(model, { temperature: 0.3, maxTokens: 64 }),
    transport: 'sse',
    fetch: async () => { throw new Error('unexpected network access'); },
    onPayload(body) {
      requestBody = body;
      throw new Error(stopBeforeNetwork);
    },
  });

  assert.equal(requestBody.model, 'gpt-6-luna');
  assert.equal(Object.hasOwn(requestBody, 'temperature'), false);
  assert.equal(response.stopReason, 'error');
  assert.match(response.errorMessage, new RegExp(stopBeforeNetwork));
  assert.equal(await credentials.read('openai-codex'), credential);
});
