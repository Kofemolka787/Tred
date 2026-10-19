"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const saved=[];
const context={window:{},URL,AbortSignal,AbortController,setTimeout,clearTimeout,document:{baseURI:'https://fixture.invalid/'},localStorage:{setItem:(...x)=>saved.push(x)}};
vm.createContext(context);vm.runInContext(fs.readFileSync('web/data-client.js','utf8'),context);
const tg={initData:'signed-fixture-only',initDataUnsafe:{user:{id:101}}},url='https://fixture-route.trycloudflare.com';
const health={ok:true,service:'tred-backend',api_version:1};
function device(userId,shared,initial={},options={}){
  const local=new Map(Object.entries(initial)),calls=[],writes=[];
  const cloud={
    getItem:(key,callback)=>{if(options.cloudError)throw Error('fixture unsupported');callback(null,shared.get(userId+':'+key)||'');},
    setItem:(key,value,callback)=>{writes.push({key,value});shared.set(userId+':'+key,value);callback(null,true);}
  };
  const telegram={initData:'signed-fixture-user-'+userId,initDataUnsafe:{user:{id:userId}},CloudStorage:cloud};
  const ctx={window:{},URL,Date,AbortSignal,AbortController,setTimeout,clearTimeout,document:{baseURI:'https://fixture.invalid/'},localStorage:{getItem:key=>local.get(key)||null,setItem:(key,value)=>local.set(key,value)}};
  ctx.fetch=async(path,request)=>{
    calls.push({path:String(path),request});
    if(String(path).endsWith('app-config.json'))return {ok:true,json:async()=>({mode:'device'})};
    if(options.reject) return {ok:false,json:async()=>({message:'Rejected identity'})};
    return {ok:true,json:async()=>String(path).endsWith('/healthz')?health:{mode:'api',user:{id:options.responseUser||userId},accounts:[{id:'456',mode:'demo',balance:'205.20'}]}};
  };
  vm.createContext(ctx);vm.runInContext(fs.readFileSync('web/data-client.js','utf8'),ctx);
  return {api:ctx.window.TredData,tg:telegram,local,shared,calls,writes,cloud,ctx};
}
async function sharedChecks(){
  const shared=new Map(),cloudKey='tred_backend_v1',localKey='tred.backend.v1.101';
  const desktop=device(101,shared,{[localKey]:url});
  const pc=await desktop.api.create(desktop.tg);
  assert.equal(pc.initialState.accounts[0].balance,'205.20');assert.equal(pc.sync.state,'synced');
  const record=JSON.parse(shared.get('101:'+cloudKey));
  assert.deepEqual(record,{v:1,owner:101,base:url});
  assert(!JSON.stringify(desktop.writes).includes('signed-fixture'));
  assert(!JSON.stringify(desktop.writes).includes('205.20'));
  const phone=device(101,shared),mobile=await phone.api.create(phone.tg);
  assert.equal(mobile.base,url);assert.equal(mobile.initialState.accounts[0].balance,'205.20');
  assert.equal(phone.local.get(localKey),url);assert.equal(phone.writes.length,0,'Restoring another device must not rewrite shared settings');
  const stranger=device(202,shared),empty=await stranger.api.create(stranger.tg);
  assert.equal(empty.mode,'device');assert.equal((await empty.request('/api/state')).accounts.length,0);
  shared.set('202:'+cloudKey,shared.get('101:'+cloudKey));
  const forged=device(202,shared),untrusted=await forged.api.create(forged.tg);
  assert.equal(untrusted.mode,'device');assert(!forged.calls.some(x=>x.path.includes('/api/state')),'A copied record for another owner is not restored');
  const wrong=device(101,new Map(),{}, {responseUser:202});
  await assert.rejects(wrong.api.connect(wrong.tg,url),/Telegram-аккаунт/);
  assert.equal(wrong.writes.length,0);assert.equal(wrong.local.size,0);
  const failed=device(101,new Map(),{}, {reject:true});
  await assert.rejects(failed.api.connect(failed.tg,url));assert.equal(failed.writes.length,0);
  const staged=device(101,new Map()),pending=await staged.api.connect(staged.tg,url,{persist:false});
  assert.equal(staged.local.size,0);assert.equal(staged.writes.length,0);
  await pending.commit();assert.equal(staged.writes.length,1);
  const newer='https://new-fixture.trycloudflare.com';
  shared.set('101:'+cloudKey,JSON.stringify({v:1,owner:101,base:newer}));
  const stale=device(101,shared,{[localKey]:url}),restored=await stale.api.create(stale.tg);
  assert.equal(restored.base,newer);assert.equal(stale.local.get(localKey),newer);assert.equal(stale.writes.length,0);
  const switched=await mobile.checkCloud(true);assert.equal(switched.client.base,newer);await switched.commit();
  assert.equal(phone.local.get(localKey),newer);assert.equal(phone.writes.length,0,'An open client can discover a changed shared route without pairing');
  const waitingShared=new Map(),waiting=device(101,waitingShared),offline=await waiting.api.create(waiting.tg);
  assert.equal(offline.mode,'device');waitingShared.set('101:'+cloudKey,JSON.stringify(record));
  const discovered=await offline.checkCloud(true);assert.equal(discovered.client.base,url);await discovered.commit();
  const unavailable=device(101,new Map(),{[localKey]:url},{cloudError:true}),fallback=await unavailable.api.create(unavailable.tg);
  assert.equal(fallback.initialState.accounts[0].balance,'205.20');assert.equal(fallback.sync.state,'local');
  assert.equal(unavailable.writes.length,0,'A failed cloud read must not be interpreted as an empty record');
  const raceShared=new Map(),race=device(101,raceShared,{[localKey]:url});let reads=0;
  race.cloud.getItem=(key,callback)=>{if(++reads===2)raceShared.set('101:'+key,JSON.stringify({v:1,owner:101,base:newer}));callback(null,raceShared.get('101:'+key)||'');};
  const raced=await race.api.create(race.tg);assert.equal(race.writes.length,0);assert.equal(raced.sync.state,'changed');
  const malformed=new Map([['101:'+cloudKey,JSON.stringify({v:1,owner:101,base:'https://evil.example/'})]]),bad=device(101,malformed);
  assert.equal((await bad.api.create(bad.tg)).mode,'device');assert(!bad.calls.some(x=>x.path.includes('evil.example')));
  const unsigned=device(101,shared);unsigned.tg.initData='';
  assert.equal((await unsigned.api.create(unsigned.tg)).mode,'device');
  const stalled=device(101,new Map(),{[localKey]:url});let lateReply;
  stalled.cloud.getItem=(_key,callback)=>lateReply=callback;
  const started=Date.now(),stillWorking=await stalled.api.create(stalled.tg);
  assert(Date.now()-started<7000,'A missing SDK callback must not block the working local cabinet indefinitely');
  assert.equal(stillWorking.initialState.accounts[0].balance,'205.20');
  lateReply(null,JSON.stringify({v:1,owner:101,base:newer}));
  assert.equal(stalled.writes.length,0);assert.equal(stalled.local.get(localKey),url);
  const writeFailure=device(101,new Map());writeFailure.cloud.setItem=(_key,_value,callback)=>callback('fixture failed');
  const connected=await writeFailure.api.connect(writeFailure.tg,url);
  assert.equal(connected.client.sync.state,'local','Failed writes must not claim cross-device sync');
  return 15;
}
(async()=>{
  context.fetch=async()=>{throw new TypeError('Failed to fetch');};
  await assert.rejects(context.window.TredData.connect(tg,url),/мобильный интернет/);
  assert.equal(saved.length,0);
  context.fetch=async()=>({ok:true,json:async()=>({service:'tred-backend',api_version:1})});
  await assert.rejects(context.window.TredData.connect(tg,url),/не найден сервер/);
  context.fetch=async path=>({ok:path.endsWith('/healthz'),json:async()=>path.endsWith('/healthz')?health:{message:'Rejected identity'}});
  await assert.rejects(context.window.TredData.connect(tg,url),/Rejected identity/);assert.equal(saved.length,0);
  context.fetch=async path=>({ok:true,json:async()=>path.endsWith('/healthz')?health:{user:{id:101}}});
  const result=await context.window.TredData.connect(tg,url,{persist:false});
  assert.equal(saved.length,0,'Uncommitted connection must not replace saved server');
  result.commit();assert.equal(saved.length,1);assert.equal(saved[0][1],url);
  await context.window.TredData.connect(tg,url);assert.equal(saved.length,2,'Existing direct callers still persist');
  const shared_checks=await sharedChecks();
  console.log(JSON.stringify({ok:true,connection_checks:5,shared_connection_checks:shared_checks,failed_auth_does_not_persist:true,deferred_commit_prevents_stale_connection_overwrite:true,legacy_desktop_route_migrated_after_verified_state:true,phone_restores_without_local_storage_or_pairing:true,other_user_and_unsigned_session_not_restored:true,cloud_record_excludes_credentials_and_balances:true,open_client_discovers_updated_route:true,failed_cloud_read_does_not_overwrite:true}));
})().catch(error=>{console.error(error);process.exitCode=1;});
