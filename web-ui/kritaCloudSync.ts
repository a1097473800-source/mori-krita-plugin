/** Refresh only a clean, idle canvas; never replace work edited during a read. */
export type SyncSnapshot={revision:number;payload:string;saved:string;busy:boolean};
export async function refreshKritaCloudCanvas<T extends {revision?:number}>(options:{
  snapshot:()=>SyncSnapshot;active:()=>boolean;read:()=>Promise<T>;
  apply:(value:T)=>void;conflict:()=>void;
}):Promise<'idle'|'unchanged'|'stale'|'conflict'|'applied'>{
  const before=options.snapshot();
  if(!options.active()||before.busy)return 'idle';
  const remote=await options.read(),after=options.snapshot();
  if(!options.active()||after.busy||after.revision!==before.revision||after.payload!==before.payload)return 'stale';
  const revision=remote.revision??0;
  if(!Number.isSafeInteger(revision)||revision<0)throw new Error('云端画布版本无效');
  if(revision<=after.revision)return 'unchanged';
  if(after.payload!==after.saved){options.conflict();return 'conflict';}
  options.apply(remote);return 'applied';
}
