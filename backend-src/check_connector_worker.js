"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const base="https://fixture-route.trycloudflare.com";
const now=Math.floor(Date.now()/1000);
const token="fixture-token-not-a-real-account-0000000";
const sender={tab:{id:1},url:"https://pocketoption.com/ru/cabinet/demo-quick-high-low/"};
const response=(status,body)=>({ok:status>=200&&status<300,status,json:async()=>body});
const copy=x=>JSON.parse(JSON.stringify(x));
const account={type:"account",mode:"demo",account_id:"456",currency:"USD",balance:"100.00",time:now};
const asset={type:"asset",symbol:"EURUSD",market:"normal",is_open:true,payout:.8,expiries:[5],time:now};
const quote={type:"quote",symbol:"EURUSD",price:1.1,time:now};
function harness(fetcher,initial={}){
  const storage={base,token,...initial},requests=[];let listener;
  const context={URL,Date:class extends Date{static now(){return now*1000;}},AbortSignal,console,setTimeout:()=>1,
    chrome:{storage:{local:{setAccessLevel:()=>{},get:async keys=>Object.fromEntries(keys.map(k=>[k,storage[k]])),set:async value=>Object.assign(storage,value),clear:async()=>{for(const k in storage)delete storage[k];}}},runtime:{onMessage:{addListener:fn=>listener=fn}}},
    fetch:async(url,options)=>{requests.push({url,options,body:JSON.parse(options.body)});return fetcher?fetcher(url,options):response(200,{ok:true});}
  };
  vm.createContext(context);
  context.importScripts=name=>vm.runInContext(fs.readFileSync("connector/"+name,"utf8"),context);
  vm.runInContext(fs.readFileSync("connector/worker.js","utf8"),context);
  return {storage,requests,context,message:(message,from={})=>new Promise(resolve=>listener(message,from,resolve)),
    async events(events){await this.message({action:"events",events},sender);await vm.runInContext("flush()",context);}};
}
async function checks(){
  const h=harness();
  await h.events([account,asset,quote,{...quote,time:now+30}]);
  assert.deepEqual(h.requests.map(r=>r.body.events[0].type),["account","asset","quote"]);
  assert.equal(h.requests[2].body.events.length,1);
  assert.equal(h.requests[2].body.events[0].time,now,"No timestamp rewriting");
  let status=await h.message({action:"status"});
  assert(status.paired&&!status.needsPairing&&status.lastSeen);
  assert.match(status.diagnostic,/quote|Котировка/);assert.match(status.diagnostic,/30 с/);
  assert(!JSON.stringify(status).includes(token));
  assert(!JSON.stringify(h.storage).includes('"balance"'));
  await h.events([quote]);
  assert.equal((await h.message({action:"status"})).diagnostic,"");

  const badClaim=harness(()=>response(401,{message:"Код связи неверен или истёк."}));
  assert((await badClaim.message({action:"pair",base,code:"fixture-expired-code"})).error);
  assert.equal(badClaim.storage.token,token);
  assert((await badClaim.message({action:"status"})).paired);

  const expired=harness(()=>response(401,{message:"Связь истекла или отключена."}));
  await expired.events([account,asset]);
  assert.equal(expired.requests.length,1);
  assert((await expired.message({action:"status"})).needsPairing);
  await expired.events([account]);assert.equal(expired.requests.length,1,"Rejected binding is not retried until paired again");

  const serverTime=harness((url,options)=>JSON.parse(options.body).events[0].type==="quote"?response(400,{message:"Некорректное время данных."}):response(200,{ok:true}));
  await serverTime.events([quote,account]);
  assert(serverTime.storage.receivedKinds.includes("account"));
  assert.match((await serverTime.message({action:"status"})).diagnostic,/сервер отклонил время/);

  const t=h.context.TredTime;
  assert.equal(t.problem({type:"trade",time:now,opened_at:now-300,expires_at:now+300,status:"open"},now),null);
  assert.equal(t.problem({type:"candle",time:now-86400},now),null);
  for(const value of [null,NaN,Infinity,0,1.1,now*1000,now+10,now-61])assert(t.problem({...quote,time:value},now));
  const allBad=harness();await allBad.events([{...quote,time:now+10}]);
  assert.equal(allBad.requests.length,0);assert(!allBad.storage.lastSeen);
  assert((await allBad.message({action:"status"})).paired);

  const secret=harness();await secret.events([{...account,session:"fixture-secret-never-forward"},quote]);
  assert.equal(secret.requests.length,1);
  assert(!JSON.stringify(secret.requests).includes("fixture-secret"));
  assert(!JSON.stringify(secret.storage).includes("fixture-secret"));

  let release,started;
  const waiting=new Promise(resolve=>started=resolve);
  const racing=harness(()=>{started();return new Promise(resolve=>release=resolve);});
  const pending=racing.events([account]);await waiting;
  await racing.message({action:"disconnect"});release(response(200,{ok:true}));await pending;
  assert.deepEqual(racing.storage,{});
  assert.equal((await h.message({action:"events",events:[quote]},{tab:{id:2},url:"https://evil.example/"})).error,"Действие недоступно.");
  console.log(JSON.stringify({ok:true,worker_scenarios:9,timestamps_not_rewritten:true,bad_quote_does_not_block_account:true,failed_claim_preserves_binding:true,status_excludes_token:true,unknown_credential_fields_not_sent:true,late_response_after_disconnect_ignored:true}));
}
async function realHttp(){
  const api=process.argv[process.argv.indexOf("--api")+1],fixtureToken=process.argv[process.argv.indexOf("--fixture-token")+1];
  assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(api));
  const h=harness((url,options)=>fetch(url.replace(base,api),options),{token:fixtureToken});
  await h.events([account,asset,quote,{...quote,time:now+30}]);
  const s=await h.message({action:"status"});
  assert.equal(s.lastError,"");assert(s.paired&&s.lastSeen);
  assert.deepEqual(copy(s.receivedKinds),["account","asset","quote"]);
  assert.match(s.diagnostic,/30 с/);
  console.log(JSON.stringify({ok:true,actual_backend_http:true,good_types_accepted:3,bad_future_quote_not_sent:true}));
}
(process.argv.includes("--api")?realHttp():checks()).catch(error=>{console.error(error);process.exitCode=1;});
