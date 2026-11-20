import assert from 'node:assert/strict';
import test from 'node:test';
import {refreshKritaCloudCanvas} from '../web-ui/kritaCloudSync.ts';

function fixture(){
  const state={revision:3,payload:'saved',saved:'saved',busy:false};
  const outcomes=[];let active=true,reads=0;
  return {state,outcomes,setActive:value=>{active=value;},options:{snapshot:()=>({...state}),active:()=>active,
    read:async()=>{reads++;return {revision:4,nodes:['remote']};},apply:value=>outcomes.push(value),conflict:()=>outcomes.push('conflict')},get reads(){return reads;}};
}
test('a clean canvas accepts a newer cloud revision',async()=>{
  const f=fixture();assert.equal(await refreshKritaCloudCanvas(f.options),'applied');assert.equal(f.outcomes[0].revision,4);
});
test('unsaved work is preserved and raises a conflict',async()=>{
  const f=fixture();f.state.payload='local changes';assert.equal(await refreshKritaCloudCanvas(f.options),'conflict');assert.deepEqual(f.outcomes,['conflict']);
});
test('edits made while the response is in flight are never replaced',async()=>{
  const f=fixture();f.options.read=async()=>{f.state.payload='typed while loading';return {revision:4};};
  assert.equal(await refreshKritaCloudCanvas(f.options),'stale');assert.deepEqual(f.outcomes,[]);
});
test('saving a newer local revision invalidates an old in-flight response',async()=>{
  const f=fixture();f.options.read=async()=>{f.state.revision=5;return {revision:4};};
  assert.equal(await refreshKritaCloudCanvas(f.options),'stale');assert.deepEqual(f.outcomes,[]);
});
test('logout or navigation cancels an in-flight refresh',async()=>{
  const f=fixture();f.options.read=async()=>{f.setActive(false);return {revision:4};};
  assert.equal(await refreshKritaCloudCanvas(f.options),'stale');assert.deepEqual(f.outcomes,[]);
});
test('drawing, pending imports and saves skip cloud reads',async()=>{
  const f=fixture();f.state.busy=true;assert.equal(await refreshKritaCloudCanvas(f.options),'idle');assert.equal(f.reads,0);
});
test('a task that starts while loading prevents replacement',async()=>{
  const f=fixture();f.options.read=async()=>{f.state.busy=true;return {revision:4};};
  assert.equal(await refreshKritaCloudCanvas(f.options),'stale');assert.deepEqual(f.outcomes,[]);
});
test('unchanged or older remote revisions are ignored',async()=>{
  for(const revision of [2,3]){const f=fixture();f.options.read=async()=>({revision});assert.equal(await refreshKritaCloudCanvas(f.options),'unchanged');assert.deepEqual(f.outcomes,[]);}
});
test('an invalid remote revision cannot replace the canvas',async()=>{
  const f=fixture();f.options.read=async()=>({revision:-1});await assert.rejects(()=>refreshKritaCloudCanvas(f.options),/版本无效/);assert.deepEqual(f.outcomes,[]);
});
