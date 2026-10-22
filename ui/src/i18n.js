import catalog from '../../backend/translations.json' with {type:'json'};

export const LANGUAGE_KEY='riotx-language';
export const normalizeLanguage=value=>value==='en'?'en':'ko';
export function readLanguage(storage){
  try{return normalizeLanguage((storage??globalThis.localStorage)?.getItem(LANGUAGE_KEY))}catch{return 'ko'}
}
let language=readLanguage();
const listeners=new Set();
export const getLanguage=()=>language;
export const locale=()=>language==='en'?'en-US':'ko-KR';
export function setLanguage(value,storage){
  language=normalizeLanguage(value);
  try{(storage??globalThis.localStorage)?.setItem(LANGUAGE_KEY,language)}catch{/* Session choice remains available. */}
  if(typeof document!=='undefined')document.documentElement.lang=language;
  for(const listener of listeners)listener();
}
export function subscribeLanguage(listener){listeners.add(listener);return()=>listeners.delete(listener)}
const reverse=Object.fromEntries(Object.entries(catalog).map(([ko,en])=>[en,ko]));
export function t(value){
  if(typeof value!=='string')return value;
  const table=language==='en'?catalog:reverse;
  if(Object.hasOwn(table,value))return table[value];
  const trimmed=value.trim();
  return Object.hasOwn(table,trimmed)?value.replace(trimmed,table[trimmed]):value;
}
// Translate exact messages and known wrappers, preserving dynamic arguments.
export function message(value){
  if(typeof value!=='string')return value;
  const table=language==='en'?catalog:reverse;
  if(Object.hasOwn(table,value))return table[value];
  const newline=value.indexOf('\n');
  if(newline>=0)return message(value.slice(0,newline))+value.slice(newline);
  for(const key of ['RIOTX 프로세스 시작:', '지원하지 않거나 관리되는 옵션:']){
    const prefix=language==='en'?key:catalog[key];
    if(value.startsWith(prefix))return table[prefix]+value.slice(prefix.length);
  }
  const ended=/^(프로세스 종료:|Process ended:)( [A-Z]+ \/ exit -?\d+\. )(.*)$/.exec(value);
  if(ended)return t(ended[1])+ended[2]+message(ended[3]);
  const exception=/^([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception): ?)(.*)$/.exec(value);
  if(exception)return exception[1]+message(exception[2]);
  return value;
}
export function translatedLabels(labels){
  return new Proxy(labels,{get:(target,key)=>t(target[key])});
}
