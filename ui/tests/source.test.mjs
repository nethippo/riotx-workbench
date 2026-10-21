import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceContext} from '../src/source.js';
const state={files:[{id:'f',name:'orders.csv'}],profiles:[{id:'live',name:'Live DB'}],telemetry:{live:{keys:10000}},time:'2026-10-20'};
test('file import counts the configured file despite a legacy Redis source ID',()=>{const s=sourceContext({kind:'file-import',source_id:'live',file_id:'f',source_status:{records:2,stable:true}},state);assert.equal(s.telemetry.keys,2);assert.equal(s.profile,null);assert.equal(s.name,'orders.csv');});
test('unknown file count never falls back to live database count',()=>assert.equal(sourceContext({kind:'file-import',source_id:'live'},state).telemetry.keys,undefined));
test('replication keeps selected Redis database live totals',()=>assert.equal(sourceContext({kind:'replicate',source_id:'live'},state).telemetry.keys,10000));
test('generator reads configured count rather than Redis total',()=>assert.equal(sourceContext({kind:'gen',source_id:'live',options:{count:70}},state).telemetry.keys,70));
