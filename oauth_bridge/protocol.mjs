import { createInterface } from 'node:readline';

export async function serve(run, server) {
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  const controllers = new Map(), prompts = new Map();
  let sequence = 0, tail = Promise.resolve(), handled = false;
  const output = (packet) => process.stdout.write(JSON.stringify(packet) + '\n');
  const handle = async (request) => {
    const requestId = request.requestId || 'login';
    const controller = controllers.get(requestId);
    const emit = (packet) => output({ ...packet, requestId });
    const prompt = (details) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const onAbort = () => { prompts.delete(id); reject(new Error('登录已取消')); };
      controller.signal.addEventListener('abort', onAbort, { once: true });
      prompts.set(id, (reply) => {
        controller.signal.removeEventListener('abort', onAbort);
        reply.cancelled ? reject(new Error('登录已取消')) : resolve(reply.value ?? '');
      });
      emit({ event: 'prompt', id, prompt: { type: details.type, message: details.message, placeholder: details.placeholder, options: details.options } });
    });
    try {
      if (controller.signal.aborted) throw new Error('请求已取消');
      emit({ result: await run(request, emit, controller.signal, prompt) });
    } catch (error) {
      emit({ error: error instanceof Error ? error.message : String(error) });
      if (!server) process.exitCode = 1;
    } finally {
      controllers.delete(requestId);
      if (!server) input.close();
    }
  };
  input.on('line', (line) => {
    let request;
    try { request = JSON.parse(line); } catch { return; }
    if (request.type === 'prompt_result') {
      const waiter = prompts.get(request.id);
      if (waiter) { prompts.delete(request.id); waiter(request); }
      return;
    }
    if (request.action === 'cancel') { controllers.get(request.requestId)?.abort(); return; }
    if (!server && handled) return;
    handled = true;
    const id = request.requestId || 'login';
    if (controllers.has(id)) return;
    controllers.set(id, new AbortController());
    tail = tail.then(() => handle(request));
  });
  input.on('close', () => {
    if (server) for (const controller of controllers.values()) controller.abort();
  });
  if (server) output({ event: 'ready' });
}
