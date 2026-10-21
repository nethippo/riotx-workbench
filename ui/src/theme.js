export const THEME_KEY='riotx-theme';
export const normalizeTheme=value=>value==='dark'?'dark':'system';
export const resolveTheme=(preference,systemDark)=>normalizeTheme(preference)==='dark'||systemDark?'dark':'light';
export function readThemePreference(storage){
  try{return normalizeTheme((storage??globalThis.localStorage)?.getItem(THEME_KEY))}catch{return 'system'}
}
export function saveThemePreference(value,storage){
  try{(storage??globalThis.localStorage)?.setItem(THEME_KEY,normalizeTheme(value))}catch{/* Keep the current session usable if storage is unavailable. */}
}
export function bindTheme(preference,root,media){
  const apply=()=>{root.dataset.theme=resolveTheme(preference,media.matches)};
  apply();media.addEventListener('change',apply);
  return ()=>media.removeEventListener('change',apply);
}
export function applyInitialTheme(){
  document.documentElement.dataset.theme=resolveTheme(readThemePreference(),window.matchMedia('(prefers-color-scheme: dark)').matches);
}
