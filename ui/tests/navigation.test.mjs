import test from 'node:test';
import assert from 'node:assert/strict';
import {comparisonParent} from '../src/navigation.js';
const parent={id:'p',kind:'replicate',workspace:'demo',source_id:'s',target_id:'t',options:{'key-pattern':'*'}};
const compare={id:'c',kind:'compare',workspace:'demo',source_id:'s',target_id:'t',options:{'key-pattern':'demo:*'}};
test('legacy comparison nests under unique endpoint pair without changing scope',()=>{
 const jobs=[parent,compare];assert.equal(comparisonParent(compare,jobs),parent);assert.equal(compare.options['key-pattern'],'demo:*');
});
test('ambiguous legacy comparisons remain visible independently',()=>{
 assert.equal(comparisonParent(compare,[parent,{...parent,id:'p2'},compare]),null);
});
test('explicit relationship resolves multiple matching replications',()=>{
 assert.equal(comparisonParent({...compare,parent_job_id:'p'},[parent,{...parent,id:'p2'}]),parent);
});
test('missing parent stays standalone; explicit parent survives endpoint changes',()=>{
 assert.equal(comparisonParent({...compare,parent_job_id:'deleted'},[parent]),null);
 assert.equal(comparisonParent({...compare,parent_job_id:'p',source_id:'other',workspace:'moved'},[parent]),parent);
});
test('workspace, direction and kind are respected',()=>{
 assert.equal(comparisonParent({...compare,workspace:'other'},[parent]),null);
 assert.equal(comparisonParent({...compare,source_id:'t',target_id:'s'},[parent]),null);
 assert.equal(comparisonParent(parent,[parent]),null);
});
