"use strict";
// Fixture socket -> unmodified observer -> actual worker -> actual local API.
const fs=require('node:fs'),vm=require('node:vm'),readline=require('node:readline');
const {harness,sender,base,now}=require('./check_connector_worker.js');
const [api,token,stamp]=process.argv.slice(2);
const h=harness((url,options)=>fetch(url.replace(base,api),options),{token,marketClock:'po-utc2'},false);
h.advance(Number(stamp)-now);
let pending=[],socket,forwarded=0,outgoing=[],timers=[],responses=[],fixtures={};
const automatic=process.argv.includes("--auto-fixture");
class Socket {
 constructor(){this.handlers={};this.readyState=1;this.subscribed=new Set();}
 addEventListener(name,fn){this.handlers[name]=fn;}
 send(frame){outgoing.push(frame);
  if(automatic&&frame.startsWith('42')){const [name,value]=JSON.parse(frame.slice(2));
   if(!['subfor','loadHistoryPeriod'].includes(name))throw Error('Forbidden generated broker request');
   if(name==='subfor'){this.subscribed.add(value);responses.push(this.emit('42'+JSON.stringify(['updateStream',[[value,h.context.Date.now()/1000+7200,106.30]]])));}
   if(name==='loadHistoryPeriod'&&fixtures[value.asset]?.[value.period]){
    responses.push(this.emit('451-["loadHistoryPeriodFast",{"_placeholder":true,"num":0}]'));
    responses.push(this.emit(new TextEncoder().encode(JSON.stringify({asset:value.asset,index:value.index,period:value.period,data:fixtures[value.asset][value.period]})).buffer));
   }
  }
 }
 emit(frame){return this.handlers.message?.({data:frame});}
 close(){return this.handlers.close?.();}
}
const context={window:{WebSocket:Socket,postMessage:value=>{forwarded+=value.events.length;pending.push(h.message({action:'events',events:value.events},sender));}},location:{origin:'https://pocketoption.com'},URL,Blob,ArrayBuffer,TextDecoder,TextEncoder,Date:h.context.Date,crypto:require('node:crypto'),console,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>{}};
vm.createContext(context);if(automatic)vm.runInContext(fs.readFileSync('connector/market-scan.js','utf8'),context);vm.runInContext(fs.readFileSync('connector/observer.js','utf8'),context);
const create=()=>socket=new context.window.WebSocket('wss://demo-api-eu.po.market/socket.io/');create();
const rl=readline.createInterface({input:process.stdin});let chain=Promise.resolve();
console.log(JSON.stringify({ready:true}));
rl.on('line',line=>{chain=chain.then(async()=>{
 const request=JSON.parse(line);pending=[];
 if(request.action==='frame')await socket.emit(request.frame);
 else if(request.action==='advance')h.advance(request.seconds);
 else if(request.action==='close')await socket.close();
 else if(request.action==='new-socket')create();
 else if(request.action==='request')socket.send(request.frame);
 else if(request.action==='fixtures')fixtures=request.histories;
 else if(request.action==='auto'){
  h.context.chrome.tabs={query:async()=>[{id:1,url:sender.url}]};
  h.context.chrome.scripting={executeScript:async options=>{context.planArg=options.args[0];return [{result:vm.runInContext('window.__tredMarketScanner.plan(planArg)',context)}];}};
  await vm.runInContext('TredScan.sync(post,endpoint)',h.context);
 }
 else if(request.action==='tick'){const work=timers;timers=[];for(const fn of work)fn();await Promise.all(responses);responses=[];if(socket.subscribed.size)await socket.emit('42'+JSON.stringify(['updateStream',[...socket.subscribed].map(s=>[s,h.context.Date.now()/1000+7200,106.30])]));}
 else if(request.action==='outgoing'){}

 else throw Error('Unknown fixture action');
 await Promise.all(pending);
 while(vm.runInContext('queue.length',h.context))await vm.runInContext('flush()',h.context);
 console.log(JSON.stringify({ok:true,forwarded,outgoing:outgoing.length,lastError:h.storage.lastError||'',...(request.action==='outgoing'?{frames:outgoing.map(f=>JSON.parse(f.slice(2)))}:{})}));
 }).catch(error=>{console.log(JSON.stringify({error:error.message}));process.exitCode=1;});});
