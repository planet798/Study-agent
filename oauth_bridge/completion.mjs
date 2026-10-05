import { buildCompletionOptions } from './request-options.mjs';

export async function completeRequest(models, credentials, request, emit, signal) {
    const { provider, model: modelId, credential } = request;
    if (!provider || !modelId || !credential) throw new Error('OAuth 配置不完整');
    const model = models.getModel(provider, modelId);
    if (!model) throw new Error(`未找到模型：${provider}/${modelId}`);
    // Resolve/refresh before making the request so a rotated refresh token is
    // handed back to the host even if the subsequent model call fails.
    await models.getAuth(model);
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
    let firstTextMs = null;
    let toolRound = false;
    const started = performance.now();
    // Older hosts send JSON null for an absent session ID; the SDK expects undefined.
    const stream = models.stream(model, context, { ...buildCompletionOptions(model, request), signal, sessionId: request.sessionId ?? undefined });
    for await (const event of stream) {
      if (event.type === 'text_delta' && !toolRound) {
        firstTextMs ??= Math.round(performance.now() - started);
        emit({ event: 'text_delta', text: event.delta });
      } else if (event.type === 'toolcall_start') {
        toolRound = true;
        emit({ event: 'discard_text' });
      }
    }
    const message = await stream.result();
    const text = message.content.filter((block) => block.type === 'text').map((block) => block.text).join('');
    const toolCalls = message.content.filter((block) => block.type === 'toolCall').map((block) => ({
      id: block.id, type: 'function', function: { name: block.name, arguments: JSON.stringify(block.arguments ?? {}) },
    }));
    if (toolCalls.length) emit({ event: "discard_text" });
    return {
      content: text,
      contentTypes: message.content.map((block) => block.type),
      toolCalls,
      finishReason: message.stopReason ?? '',
      errorMessage: message.errorMessage ?? '',
      model: message.model ?? modelId,
      usage: message.usage ?? null,
      timings: { firstTextMs, completionMs: Math.round(performance.now() - started) },
      credential: await credentials.read(provider),
    };
}
