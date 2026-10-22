import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {LANGUAGE_KEY,readLanguage,setLanguage,getLanguage,locale,subscribeLanguage,t,message,translatedLabels} from '../src/i18n.js';
import {sourceContext} from '../src/source.js';
const storage={getItem:()=>null,setItem(){}};
test('Korean default and invalid or unavailable storage',()=>{
 for(const value of [null,'invalid','ko'])assert.equal(readLanguage({getItem:()=>value}),'ko');
 assert.equal(readLanguage({getItem:()=>{throw Error()}}),'ko');
 assert.equal(readLanguage({getItem:()=> 'en'}),'en');
});
test('language persistence, immediate subscriptions and locale formatting',()=>{
 const saved=new Map(),store={setItem:(k,v)=>saved.set(k,v)};let updates=0;
 const stop=subscribeLanguage(()=>updates++);
 setLanguage('en',store);assert.equal(getLanguage(),'en');assert.equal(locale(),'en-US');assert.equal(saved.get(LANGUAGE_KEY),'en');
 setLanguage('ko',store);assert.equal(locale(),'ko-KR');assert.equal(updates,2);
 stop();setLanguage('en',storage);assert.equal(updates,2);
 setLanguage('ko',storage);
});
test('labels and source counts respond to language changes without rewriting data',()=>{
 const labels=translatedLabels({RUNNING:'실행 중'});
 const state={profiles:[{id:'s',name:'고객 소스'}],telemetry:{s:{keys:1250}},files:[],time:'today'};
 const job={kind:'replicate',source_id:'s'},before=JSON.stringify(state);
 setLanguage('en',storage);
 assert.equal(labels.RUNNING,'Running');assert.equal(sourceContext(job,state).label,'Source keys');
 assert.equal(sourceContext(job,state).name,'고객 소스');assert.equal(JSON.stringify(state),before);
 assert.equal(t('file.json'),'file.json');assert.equal(t('redis://localhost:6401/0'),'redis://localhost:6401/0');
 assert.equal(message('ValueError: 지원하지 않거나 관리되는 옵션: --password'),'ValueError: Unsupported or managed option: --password');
 assert.equal(message('RIOTX 프로세스 시작: riotx --key-pattern 소스:키:*'),'RIOTX process started: riotx --key-pattern 소스:키:*');
 assert.equal(message('설정 검증\nriotx --key-pattern 소스:키:*'),'Validate settings\nriotx --key-pattern 소스:키:*');
 setLanguage('ko',storage);assert.equal(message('Unsupported or managed option: --소스'),'지원하지 않거나 관리되는 옵션: --소스');assert.equal(labels.RUNNING,'실행 중');assert.equal(sourceContext(job,state).label,'소스 키 수');
});
test('all Korean UI literals and backend system messages have catalog entries',()=>{
 const catalog=JSON.parse(readFileSync(new URL('../../backend/translations.json',import.meta.url)));
 const app=readFileSync(new URL('../src/App.jsx',import.meta.url),'utf8');
 const source=readFileSync(new URL('../src/source.js',import.meta.url),'utf8');
 for(const match of (app+'\n'+source).matchAll(/\b(?:tr|t)\((['"])([^'"\n]*[가-힣][^'"\n]*)\1\)/g))assert.ok(Object.hasOwn(catalog,match[2].trim()),match[2]);
 for(const [ko,en]of Object.entries(catalog)){assert.ok(en.trim(),ko);assert.doesNotMatch(en,/[가-힣]/,ko)}
 // Locale changes must never translate arbitrary names or raw RIOTX log lines.
 assert.match(app,/l\.origin==='workbench'\?message\(l\.message\):l\.message/);
 assert.doesNotMatch(app,/tr\((?:job|p|file)\.(?:name|url)\)/);
});
