"use strict";
// Actual worker + MAIN scanner: a worker restart must not need a new catalog
// or a new quote just to discover an already-open, attested market socket.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const {harness,now,sender}=require('./check_connector_worker.js');
const feed='11111111-1111-4111-8111-111111111111';
const other='22222222-2222-4222-8222-222222222222';
const plan={version:1,until:now+15,pairs:[{symbol:'AAA',feed_id:feed,quote:true,history:[{period:60,bars:160},{period:300,bars:160}]}]};
function mainPage(id=feed){
 const frames=[],context={window:{},Date:class extends Date{static now(){return now*1000;}},setTimeout:()=>1,clearTimeout:()=>{}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('connector/market-scan.js','utf8'),context);
 const socket={readyState:1},state={feed:id,catalog:new Map([['AAA',{is_open:true}]]),requests:new Map()};
 context.window.__tredMarketScanner.attach(socket,state,frame=>frames.push(frame));
 return {context,socket,state,frames};
}
function worker(pages,extra=[]){
 const h=harness(url=>({ok:true,status:200,json:async()=>url.endsWith('/api/bridge/scan')?{ok:true,plan}:{ok:true}}));
 const injections=[];
 h.context.chrome.tabs={query:async()=>[...pages.keys()].map(id=>({id,url:sender.url})).concat(extra)};
 h.context.chrome.scripting={executeScript:async options=>{
  injections.push(options);const page=pages.get(options.target.tabId);assert(page);
  page.context.args=options.args;
  return [{result:vm.runInContext('('+options.func.toString()+')(...args)',page.context)}];
 }};
 return {h,injections,sync:()=>vm.runInContext('TredScan.sync(post,endpoint)',h.context)};
}
(async()=>{
 const page=mainPage();let w=worker(new Map([[1,page]]));
 await w.sync();assert.equal(w.h.storage.scannerStatus.code,'ready');assert.equal(w.h.storage.scannerStatus.pairs,1);
 assert.equal(w.injections.length,2,'Discovery then a bounded plan, without a new broker event');
 assert.equal(page.frames.length,0,'Discovery does not generate market or order requests');
 const serialized=JSON.stringify(w.injections);
 assert(!serialized.includes('fixture-token'),'Pairing token never enters MAIN arguments');
 const firstInjections=w.injections.length;w.h.advance(5);await w.sync();
 assert.equal(w.injections.length,firstInjections+1,'Known mappings need no extra discovery');
 w=worker(new Map([[1,page]]));await w.sync();
 assert.equal(w.h.storage.scannerStatus.code,'ready','A fresh worker recovers the live page mapping');
 page.socket.readyState=3;w=worker(new Map([[1,page]]));await w.sync();assert.equal(w.h.storage.scannerStatus.code,'no_socket');
 assert.equal(w.injections.length,1,'Closed socket cannot receive a plan');
 w=worker(new Map([[1,mainPage(other)]]));await w.sync();assert.equal(w.h.storage.scannerStatus.code,'no_socket');
 w=worker(new Map([[1,mainPage()],[2,mainPage()]]));await w.sync();assert.equal(w.h.storage.scannerStatus.code,'no_socket','Ambiguous tabs are not selected arbitrarily');
 assert.equal(w.injections.length,2,'Only discovery runs for ambiguous IDs');
 w=worker(new Map(),[{id:3,url:'https://evil.example/ru/cabinet/'}]);await w.sync();
 assert.equal(w.h.storage.scannerStatus.code,'no_tab');assert.equal(w.injections.length,0);
 const legacy=mainPage();delete legacy.context.window.__tredMarketScanner.connections;
 w=worker(new Map([[1,legacy]]));await w.sync();assert.equal(w.h.storage.scannerStatus.code,'no_socket');
 const status=await w.h.message({action:'status'});assert.equal(status.scannerStatus.code,'no_socket');
 assert(!JSON.stringify(status).includes('fixture-token'));
 console.log(JSON.stringify({ok:true,worker_restart_recovers_live_socket:true,no_catalog_or_time_replay:true,closed_unknown_ambiguous_tabs_rejected:true,no_credentials_in_main:true,scanner_status_visible:true}));
})().catch(error=>{console.error(error);process.exitCode=1;});
