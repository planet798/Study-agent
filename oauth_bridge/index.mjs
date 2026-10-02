import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';
import { builtinModels } from '@earendil-works/pi-ai/providers/all';

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
let requestResolve;
const firstLine = new Promise((resolve) => { requestResolve = resolve; });
const promptWaiters = new Map();
let nextPromptId = 1;
input.on('line', (line) => {
  let value;
  try { value = JSON.parse(line); } catch { return; }
  if (value?.type === 'prompt_result') {
    const resolve = promptWaiters.get(value.id);
    if (resolve) {
      promptWaiters.delete(value.id);
      resolve(value.cancelled ? new Error('登录已取消') : (value.value ?? ''));
    }
    return;
  }
  requestResolve(value);
  requestResolve = () => {};
});
const emit = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);

class MemoryCredentials {
  constructor(seed = {}) { this.entries = new Map(Object.entries(seed)); }
  async read(id) { return this.entries.get(id); }
  async list() { return [...this.entries].map(([providerId, credential]) => ({ providerId, type: credential.type })); }
  async modify(id, fn) {
    const updated = await fn(this.entries.get(id));
    if (updated !== undefined) this.entries.set(id, updated);
    return this.entries.get(id);
  }
  async delete(id) { this.entries.delete(id); }
}

function createCollection(credentials) {
  return builtinModels({ credentials });
}

function getProviderInfo(models) {
  return models.getProviders()
    .filter((provider) => provider.auth?.oauth)
    .map((provider) => ({
      id: provider.id,
      name: provider.name,
      loginLabel: provider.auth.oauth.loginLabel ?? provider.auth.oauth.name,
      isSubscription: provider.auth.oauth.isSubscription ?? false,
      models: models.getModels(provider.id).map((model) => ({ id: model.id, name: model.name ?? model.id })),
    }));
}

async function run(request) {
  if (!request || typeof request !== 'object') throw new Error('请求格式错误');
  const credentials = new MemoryCredentials(request.credentials ?? {});
  const models = createCollection(credentials);
  if (request.action === 'catalog') return { providers: getProviderInfo(models) };
  if (request.action === 'login') {
    const provider = models.getProvider(request.provider);
    if (!provider?.auth?.oauth) throw new Error(`提供商不支持 OAuth 登录：${request.provider}`);
    await models.login(request.provider, 'oauth', {
      signal: new AbortController().signal,
      prompt: (prompt) => new Promise((resolve, reject) => {
        const id = nextPromptId++;
        const cleanup = () => prompt.signal?.removeEventListener('abort', onAbort);
        const onAbort = () => {
          promptWaiters.delete(id);
          cleanup();
          reject(new Error('登录提示已取消'));
        };
        promptWaiters.set(id, (value) => {
          cleanup();
          value instanceof Error ? reject(value) : resolve(value);
        });
        prompt.signal?.addEventListener('abort', onAbort, { once: true });
        emit({ event: 'prompt', id, prompt: { type: prompt.type, message: prompt.message, placeholder: prompt.placeholder, options: prompt.options } });
      }),
      notify: (event) => emit({ event: 'auth', authEvent: event }),
    }, { getDeviceId: () => request.deviceId || randomUUID() });
    return { credential: await credentials.read(request.provider) };
  }
  if (request.action === 'logout') {
    await models.logout(request.provider);
    return { ok: true };
  }
  if (request.action === 'complete') {
    const { provider, model: modelId, credential } = request;
    if (!provider || !modelId || !credential) throw new Error('OAuth 配置不完整');
    await credentials.modify(provider, async () => credential);
    const model = models.getModel(provider, modelId);
    if (!model) throw new Error(`未找到模型：${provider}/${modelId}`);
    // Resolve/refresh before making the request so a rotated refresh token is
    // handed back to the host even if the subsequent model call fails.
    await models.getAuth(model);
    emit({ event: 'credential', provider, credential: await credentials.read(provider) });
    let systemPrompt = request.systemPrompt ?? '';
    const messages = [];
    for (const source of request.messages ?? []) {
      if (source.role === 'system') {
        systemPrompt = [systemPrompt, source.content ?? ''].filter(Boolean).join('\\n\\n');
        continue;
      }
      const timestamp = Date.now();
      if (source.role === 'tool') {
        messages.push({
          role: 'toolResult', toolCallId: source.tool_call_id ?? source.toolCallId ?? '',
          toolName: source.name ?? '', content: [{ type: 'text', text: source.content ?? '' }],
          isError: false, timestamp,
        });
        continue;
      }
      const content = [];
      if (source.content) content.push({ type: 'text', text: source.content });
      for (const call of source.tool_calls ?? source.toolCalls ?? []) {
        const fn = call.function ?? call;
        let args = fn.arguments ?? {};
        if (typeof args === 'string') {
          try { args = JSON.parse(args); } catch { args = {}; }
        }
        content.push({ type: 'toolCall', id: call.id, name: fn.name, arguments: args });
      }
      messages.push({ role: source.role, content, timestamp });
    }
    const context = {
      systemPrompt,
      messages,
      tools: (request.tools ?? []).map((source) => {
        const tool = source.function ?? source;
        return {
          name: tool.name,
          description: tool.description ?? '',
          parameters: tool.parameters,
        };
      }),
    };
    const message = await models.complete(model, context, {
      temperature: request.temperature ?? 0.3,
      maxTokens: request.maxTokens,
    });
    const text = message.content.filter((block) => block.type === 'text').map((block) => block.text).join('');
    const toolCalls = message.content.filter((block) => block.type === 'toolCall').map((block) => ({
      id: block.id, type: 'function', function: { name: block.name, arguments: JSON.stringify(block.arguments ?? {}) },
    }));
    return {
      content: text,
      contentTypes: message.content.map((block) => block.type),
      toolCalls,
      finishReason: message.stopReason ?? '',
      errorMessage: message.errorMessage ?? '',
      model: message.model ?? modelId,
      usage: message.usage ?? null,
      credential: await credentials.read(provider),
    };
  }
  throw new Error(`未知操作：${request.action}`);
}

try {
  const request = await firstLine;
  if (!request) throw new Error('未收到请求');
  const response = await run(request);
  emit({ result: response });
} catch (error) {
  emit({ error: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
} finally {
  input.close();
}
