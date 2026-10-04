import { test } from 'node:test';
import assert from 'node:assert/strict';
import { completeRequest } from '../completion.mjs';
import { MemoryCredentials } from '../credentials.mjs';

test('text streams, thinking/tool arguments stay private, final response is intact', async () => {
  const message={content:[{type:'text',text:'hello'}],stopReason:'stop',model:'test'};
  const events=[];
  const stream={async *[Symbol.asyncIterator]() {
    yield {type:'thinking_delta',delta:'private'};
    yield {type:'text_delta',delta:'hello'};
  }, result:async()=>message};
  const models={getModel:()=>({api:'openai-codex-responses'}),getAuth:async()=>{},stream:()=>stream};
  const result=await completeRequest(models,new MemoryCredentials({test:{type:'oauth'}}),
    {provider:'test',model:'test',credential:{type:'oauth'},messages:[]},e=>events.push(e),new AbortController().signal);
  assert.equal(result.content,'hello');
  assert.deepEqual(events.map(e=>e.text),['hello']);
});

test('unchanged credentials are not written; in-place refresh emits exact updated value', async () => {
  const events=[], original={access:'old',refresh:'keep',expires:1};
  const store=new MemoryCredentials({test:original},(id,value)=>events.push(value));
  await store.modify('test',old=>({...old}));
  assert.equal(events.length,0);
  await store.modify('test',old=>{old.access='new';return old;});
  assert.deepEqual(events,[{access:'new',refresh:'keep',expires:1}]);
});
