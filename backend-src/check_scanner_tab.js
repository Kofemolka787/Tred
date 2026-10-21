"use strict";
// Actual worker, with browser APIs as fixtures. No native extension or broker.
const assert=require('node:assert/strict'),vm=require('node:vm');
const {harness,base,now,sender}=require('./check_connector_worker.js');
const feed='11111111-1111-4111-8111-111111111111';
const asset={type:'asset',symbol:'PAIR_otc',market:'otc',is_open:true,payout:.92,expiries:[5,10,15],time:now,feed_id:feed,history_scoped:true};
const plan={version:1,until:now+15,scan_id:11,scanner_active:true,pairs:[],total:23};
const h=harness(url=>({ok:true,status:200,json:async()=>url.endsWith('/api/bridge/scan')?{ok:true,plan}:{ok:true}}));
let next=2;const tabs=new Map([[1,{id:1,url:sender.url+'?private=fixture#old'}]]),created=[],updated=[];
h.context.chrome.tabs={
 query:async()=>[...tabs.values()],get:async id=>{if(!tabs.has(id))throw Error('missing');return tabs.get(id);},
 create:async value=>{created.push(value);const tab={id:next++,url:value.url};tabs.set(tab.id,tab);return tab;},
 update:async(id,value)=>{assert.equal(h.storage.scannerTabId,id,'Register the role before navigation');updated.push(value);tabs.set(id,{id,url:value.url});return tabs.get(id);}
};
h.context.chrome.scripting={executeScript:async()=>[{result:[]}]};
const sync=()=>vm.runInContext('TredScan.sync(post,endpoint)',h.context);
(async()=>{
 await sync();assert.equal(created.length,1);assert.equal(created[0].active,false);assert.equal(created[0].url,'about:blank');
 assert.equal(updated[0].url,'https://pocketoption.com/ru/cabinet/demo-quick-high-low/?tred_scanner=1');
 assert(!JSON.stringify(updated).includes('private'));
 h.advance(5);await sync();assert.equal(created.length,1,'A running collector is reused');
 await h.message({action:'events',events:[{...asset,source_role:'scanner'}]},sender);
 await vm.runInContext('flush()',h.context);
 const foreground=h.requests.filter(r=>r.url.endsWith('/api/bridge/events')).at(-1).body.events;
 assert.equal(foreground[0].source_role,'terminal','A foreground page cannot claim scanner priority');
 await h.message({action:'events',events:[asset]},{tab:{id:2},url:updated[0].url});
 await vm.runInContext('flush()',h.context);
 assert.equal(h.requests.filter(r=>r.url.endsWith('/api/bridge/events')).at(-1).body.events[0].source_role,'scanner');
 h.context.closedTabId=2;tabs.delete(2);
 await vm.runInContext('TredScan.removed(closedTabId,post,endpoint)',h.context);
 assert.equal(h.storage.scannerPausedJob,11);assert.equal(h.storage.scannerTabId,null);
 h.advance(5);await sync();assert.equal(created.length,1,'Explicit close does not create an opening loop');
 plan.scan_id=null;h.advance(5);await sync();assert.equal(created.length,1,'A completed candidate cannot reopen a tab the user closed');
 plan.scan_id=12;h.advance(5);await sync();assert.equal(created.length,2,'A new user scan can resume collection');
 await h.message({action:'events',events:[asset]},{tab:{id:3},url:updated.at(-1).url});
 await vm.runInContext('TredScan.reset()',h.context);
 h.context.closedTabId=3;tabs.delete(3);await vm.runInContext('TredScan.removed(closedTabId,post,endpoint)',h.context);
 assert(h.requests.filter(r=>r.url.endsWith('/api/bridge/events')).at(-1).body.events.some(e=>e.type==='feed'&&e.connected===false&&e.feed_id===feed),'A restarted worker retains enough source identity to invalidate a closed collector');
 assert(!JSON.stringify(updated).includes(h.storage.token));
 console.log(JSON.stringify({ok:true,one_background_tab:true,owner_role_registered_before_navigation:true,
   foreground_cannot_claim_role:true,no_keys_or_old_query_in_tab_url:true,close_pauses_same_job:true,new_scan_resumes:true}));
})().catch(error=>{console.error(error);process.exitCode=1;});
