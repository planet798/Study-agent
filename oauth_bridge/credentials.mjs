import { isDeepStrictEqual } from 'node:util';

export class MemoryCredentials {
  constructor(seed = {}, changed = () => {}) { this.entries = new Map(Object.entries(seed)); this.changed = changed; }
  async read(id) { return this.entries.get(id); }
  async list() { return [...this.entries].map(([providerId, credential]) => ({ providerId, type: credential.type })); }
  async modify(id, fn) {
    const previous = structuredClone(this.entries.get(id));
    const updated = await fn(this.entries.get(id));
    if (updated !== undefined) {
      this.entries.set(id, updated);
      if (!isDeepStrictEqual(previous, updated)) this.changed(id, updated);
    }
    return this.entries.get(id);
  }
  async delete(id) { this.entries.delete(id); }
}
