const CODEX_RESPONSES_API = 'openai-codex-responses';

/** Build shared SDK options without sending unsupported Codex parameters. */
export function buildCompletionOptions(model, request) {
  const options = { maxTokens: request.maxTokens };
  if (model.api !== CODEX_RESPONSES_API) {
    options.temperature = request.temperature ?? 0.3;
  }
  return options;
}
