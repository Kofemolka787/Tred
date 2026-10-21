"use strict";
const assert=require('node:assert/strict'),vm=require('node:vm');
const {harness,base,now,sender}=require('./check_connector_worker.js');
const local='http://127.0.0.1:8790';
const plan={version:1,until:now+15,scan_id:null,scanner_active:false,pairs:[],total:0};
const response=(status,body)=>({ok:status===200,status,json:async()=>body});
(async()=>{
 const h=harness(url=>url.startsWith(local)?response(200,{ok:true,plan}):response(503,{message:'Old public route closed'}));
 h.context.chrome.tabs={query:async()=>[]};h.context.chrome.scripting={executeScript:async()=>[]};
 const saved=h.storage.token;
 await vm.runInContext('TredScan.sync(post,endpoint)',h.context);
 assert.equal(h.storage.base,local);assert.equal(h.storage.token,saved);
 assert.equal(h.requests.length,1,'Recover through the saved token without the old public hostname');
 assert(h.requests[0].options.headers.Authorization.endsWith(saved));
 const bad=harness(url=>url.startsWith(local)?response(401,{message:'Different local server'}):response(200,{ok:true,plan}));
 bad.context.chrome.tabs=h.context.chrome.tabs;bad.context.chrome.scripting=h.context.chrome.scripting;
 await vm.runInContext('TredScan.sync(post,endpoint)',bad.context);
 assert.equal(bad.storage.base,base,'Never adopt a local server which did not authorize the existing token');
 assert.equal(bad.requests.length,2);
 for(const url of ['http://localhost:8790','http://127.0.0.1:8791','http://192.168.1.1:8790','http://127.0.0.1:8790/other','http://user:pass@127.0.0.1:8790/']){
   h.context.candidate=url;assert.throws(()=>vm.runInContext('endpoint(candidate)',h.context));
 }
 console.log(JSON.stringify({ok:true,old_route_recovers_without_new_code:true,token_unchanged:true,foreign_local_server_rejected:true,only_fixed_loopback_allowed:true}));
})().catch(error=>{console.error(error);process.exitCode=1;});
