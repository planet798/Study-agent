import { randomUUID } from 'node:crypto';
import { builtinModels } from '@earendil-works/pi-ai/providers/all';
import { MemoryCredentials } from './credentials.mjs';
import { completeRequest } from './completion.mjs';
import { serve } from './protocol.mjs';

const createCollection = (credentials) => builtinModels({ credentials });
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

async function run(request, emit, signal, prompt) {
  if (!request || typeof request !== 'object') throw new Error('请求格式错误');
  const credentials = new MemoryCredentials(request.credential ? { [request.provider]: request.credential } : (request.credentials ?? {}), (provider, credential) => emit({ event: "credential", provider, credential }));
  const models = createCollection(credentials);
  if (request.action === 'catalog') return { providers: getProviderInfo(models) };
  if (request.action === 'login') {
    const provider = models.getProvider(request.provider);
    if (!provider?.auth?.oauth) throw new Error(`提供商不支持 OAuth 登录：${request.provider}`);
    await models.login(request.provider, 'oauth', {
      signal,
      prompt,
      notify: (event) => emit({ event: 'auth', authEvent: event }),
    }, { getDeviceId: () => request.deviceId || randomUUID() });
    return { credential: await credentials.read(request.provider) };
  }
  if (request.action === 'logout') {
    await models.logout(request.provider);
    return { ok: true };
  }
  if (request.action === 'complete') return completeRequest(models, credentials, request, emit, signal);
  throw new Error(`未知操作：${request.action}`);
}

await serve(run, process.argv.includes('--server'));
