import test from 'node:test';
import assert from 'node:assert/strict';
import { operatorConfig } from '../src/logic.ts';
const form = { providerEnabled:false, providerId:'', kind:'openai_compatible', baseUrl:'', credentialRef:'', allowInsecureLoopback:false, supportsTools:false, supportsJsonSchema:false, routerModel:'', contextLimit:'64000', tokenLimit:'', concurrencyLimit:'1', hosts:'' };
test('explicit disabled default serializes nullable cap without inventing one',()=>{
  const result=operatorConfig(form,{enforced:false,savedLimit:null});
  assert.equal(result.model_token_limit,null);assert.equal(result.enforce_token_budgets,false);
  assert.equal(result.router_context_limit,64000);assert.equal(result.model_concurrency_limit,1);
});
test('unrelated edits retain explicit active policy and exact numeric cap including zero',()=>{
  for(const limit of [0,12345]){
    const result=operatorConfig({...form,tokenLimit:String(limit),hosts:'example.invalid'},{enforced:true,savedLimit:limit});
    assert.equal(result.model_token_limit,limit);assert.equal(result.enforce_token_budgets,true);assert.deepEqual(result.network_hosts,['example.invalid']);
  }
});
test('disabled configured amount remains recorded without enabling it',()=>{
  const result=operatorConfig({...form,tokenLimit:'12345'},{enforced:false,savedLimit:12345});
  assert.equal(result.model_token_limit,12345);assert.equal(result.enforce_token_budgets,false);
});
test('cannot silently clear a known cap or submit enabled policy without limit',()=>{
  assert.throws(()=>operatorConfig(form,{enforced:false,savedLimit:12345}),/Token 上限/);
  assert.throws(()=>operatorConfig(form,{enforced:true,savedLimit:null}),/Token 上限/);
});
test('unknown policy cannot silently disable an existing operator opt-in',()=>{
  assert.throws(()=>operatorConfig({...form,tokenLimit:'12345'}),/策略未知/);
  assert.throws(()=>operatorConfig({...form,tokenLimit:'12345'},{enforced:null,savedLimit:12345}),/策略未知/);
});
