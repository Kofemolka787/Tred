"use strict";
// Actual worker, with browser APIs as fixtures. No native extension or broker.
const assert=require('node:assert/strict'),vm=require('node:vm');
const {harness,base,now,sender}=require('./check_connector_worker.js');
const feed='11111111-1111-4111-8111-111111111111';
const asset={type:'asset',symbol:'PAIR_otc',market:'otc',is_open:true,payout:.92,expiries:[5,10,15],time:now,feed_id:feed,history_scoped:true};
const plan={version:1,until:now+15,scan_id:null,scanner_active:true,pairs:[],total:23};
const h=harness(url=>({ok:true,status:200,json:async()=>url.endsWith('/api/bridge/scan')?{ok:true,plan}:{ok:true}}));
const browserSession={};
h.context.chrome.storage.session={get:async keys=>Object.fromEntries(keys.map(k=>[k,browserSession[k]])),set:async value=>Object.assign(browserSession,value)};
let next=2,reloads=0;const tabs=new Map([[1,{id:1,url:sender.url+'?private=fixture#old'}]]),created=[],updated=[];
h.context.chrome.tabs={
 query:async()=>[...tabs.values()],get:async id=>{if(!tabs.has(id))throw Error('missing');return tabs.get(id);},
 create:async value=>{created.push(value);const tab={id:next++,url:value.url};tabs.set(tab.id,tab);return tab;},
 update:async(id,value)=>{assert.equal(h.storage.scannerTabId,id,'Register the role before navigation');updated.push(value);tabs.set(id,{id,url:value.url});return tabs.get(id);}
 ,reload:async()=>{reloads++;}
};
h.context.chrome.scripting={executeScript:async()=>[{result:[]}]};
const sync=()=>vm.runInContext('TredScan.sync(post,endpoint)',h.context);
(async()=>{
 await sync();assert.equal(created.length,1);assert.equal(created[0].active,false);assert.equal(created[0].url,'about:blank');
 assert.equal(updated[0].url,'https://pocketoption.com/ru/cabinet/demo-quick-high-low/?tred_scanner=1');
 assert.equal(browserSession.tredCollectorTabId,2,'Record ownership in browser-session storage before navigation');
 assert(!JSON.stringify(updated).includes('private'));
 plan.scan_id=11;h.advance(5);await sync();assert.equal(created.length,1,'A running collector is reused');
 plan.scan_id=12;h.advance(5);await sync();assert.equal(reloads,0,'New analysis must not reload and replace the warm market source');
 await h.message({action:'events',events:[{...asset,source_role:'scanner'}]},sender);
 await vm.runInContext('flush()',h.context);
 const foreground=h.requests.filter(r=>r.url.endsWith('/api/bridge/events')).at(-1).body.events;
 assert.equal(foreground[0].source_role,'terminal','A foreground page cannot claim scanner priority');
 await h.message({action:'events',events:[asset]},{tab:{id:2},url:updated[0].url});
 await vm.runInContext('flush()',h.context);
 assert.equal(h.requests.filter(r=>r.url.endsWith('/api/bridge/events')).at(-1).body.events[0].source_role,'scanner');
 h.context.closedTabId=2;tabs.delete(2);
 await vm.runInContext('TredScan.removed(closedTabId,post,endpoint)',h.context);
 assert.equal(h.storage.scannerPausedJob,12);assert.equal(h.storage.scannerTabId,null);
 assert.equal(browserSession.tredCollectorTabId,null);
 h.advance(5);await sync();assert.equal(created.length,1,'Explicit close does not create an opening loop');
 plan.scan_id=null;h.advance(5);await sync();assert.equal(created.length,1,'A completed candidate cannot reopen a tab the user closed');
 plan.scan_id=13;h.advance(5);await sync();assert.equal(created.length,2,'A new user scan can resume collection');
 const resumed=harness(()=>({ok:true,status:200,json:async()=>({ok:true,plan})}),{...h.storage});
 resumed.context.chrome.storage.session=h.context.chrome.storage.session;
 const stripped={id:3,url:'https://pocketoption.com/ru/cabinet'};let duplicates=0;
 resumed.context.chrome.tabs={query:async()=>[stripped],get:async()=>stripped,
   create:async()=>{duplicates++;return{id:8};},update:async()=>{}};
 resumed.context.chrome.scripting={executeScript:async()=>[{result:[]}]};
 await vm.runInContext('TredScan.sync(post,endpoint)',resumed.context);
 assert.equal(duplicates,0,'A page removing its query must not create more collector tabs after worker restart');
 assert(resumed.storage.scannerStatus.dedicated);
 await resumed.message({action:'events',events:[asset]},{tab:{id:3},url:stripped.url});
 await vm.runInContext('flush()',resumed.context);
 assert.equal(resumed.requests.find(r=>r.url.endsWith('/api/bridge/events')).body.events[0].source_role,'scanner');
 await h.message({action:'events',events:[asset]},{tab:{id:3},url:updated.at(-1).url});
 await vm.runInContext('TredScan.reset()',h.context);
 h.context.closedTabId=3;tabs.delete(3);await vm.runInContext('TredScan.removed(closedTabId,post,endpoint)',h.context);
 assert(h.requests.filter(r=>r.url.endsWith('/api/bridge/events')).at(-1).body.events.some(e=>e.type==='feed'&&e.connected===false&&e.feed_id===feed),'A restarted worker retains enough source identity to invalidate a closed collector');
 assert(!JSON.stringify(updated).includes(h.storage.token));
 const reused=harness(url=>({ok:true,status:200,json:async()=>({ok:true,plan})}),{scannerTabId:1,scannerJobId:11});
 const restored={id:1,url:sender.url};let extraTabs=0,foregroundReloads=0;
 reused.context.chrome.tabs={query:async()=>[restored],get:async()=>restored,
   create:async()=>{extraTabs++;return{id:7};},update:async()=>{},reload:async()=>{foregroundReloads++;}};
 reused.context.chrome.scripting={executeScript:async()=>[{result:[]}]};
 await reused.message({action:'events',events:[{...asset,source_role:'scanner'}]},sender);
 await vm.runInContext('flush()',reused.context);
 assert.equal(reused.requests.find(r=>r.url.endsWith('/api/bridge/events')).body.events[0].source_role,'terminal','A reused tab ID does not grant collector priority');
 assert.equal(extraTabs,1,'A normal terminal with a stale collector ID needs a separate collector');
 assert.equal(foregroundReloads,0,'Do not reload the user terminal as a collector');
 for(const path of ['/ru/cabinet','/ru/cabinet?display=chart','/cabinet','/cabinet/','/en/cabinet/quick-high-low/']){
   const root=harness(()=>({ok:true,status:200,json:async()=>({ok:true,plan})}));let opened=0,navigated='';const injected=[];
   root.context.chrome.tabs={query:async()=>[{id:1,url:'https://pocketoption.com'+path}],
     create:async()=>{opened++;return{id:4};},update:async(id,value)=>{navigated=value.url;}};
   root.context.chrome.scripting={executeScript:async options=>{injected.push(options);return[{result:[]}];}};
   await vm.runInContext('TredScan.sync(post,endpoint)',root.context);
   assert.equal(opened,1,'Recognize the owner terminal address '+path);
   assert.equal(new URL(navigated).search,'?tred_scanner=1');
   assert(root.storage.scannerStatus.dedicated);
   await vm.runInContext('attachOpenTerminals()',root.context);
   assert(injected.some(x=>x.files?.includes('observer.js')),'Restore the observer on '+path);
   assert(injected.some(x=>x.files?.includes('balance-read.js')),'Restore the balance reader on '+path);
 }
 for(const url of ['https://evil.example/ru/cabinet','https://pocketoption.com.evil.example/ru/cabinet','https://pocketoption.com/ru/cabinet-other','https://pocketoption.com/ru/cabinet-other/']){
   const rejected=harness(()=>({ok:true,status:200,json:async()=>({ok:true,plan})}));let opened=0;
   rejected.context.chrome.tabs={query:async()=>[{id:1,url}],create:async()=>{opened++;return{id:4};},update:async()=>{}};
   rejected.context.chrome.scripting={executeScript:async()=>[{result:[]}]};
   await vm.runInContext('TredScan.sync(post,endpoint)',rejected.context);
   assert.equal(opened,0,'Reject an unrelated page '+url);
   assert.equal(rejected.storage.scannerStatus.code,'no_tab');
 }
 console.log(JSON.stringify({ok:true,one_background_tab:true,owner_role_registered_before_navigation:true,
   foreground_cannot_claim_role:true,no_keys_or_old_query_in_tab_url:true,close_pauses_same_job:true,new_scan_resumes:true,reused_tab_id_is_not_a_collector:true,browser_session_ownership_survives_worker_and_query_removal:true,cabinet_root_with_or_without_slash:true,unrelated_paths_rejected:true,idle_collector_opens_before_request:true,new_requests_never_reload_collector:true}));
})().catch(error=>{console.error(error);process.exitCode=1;});
