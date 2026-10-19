"use strict";
// Fixture socket -> unmodified observer -> actual worker -> actual local API.
const fs=require('node:fs'),vm=require('node:vm'),readline=require('node:readline');
const {harness,sender,base,now}=require('./check_connector_worker.js');
const [api,token,stamp]=process.argv.slice(2);
const h=harness((url,options)=>fetch(url.replace(base,api),options),{token,marketClock:'po-utc2'},false);
h.advance(Number(stamp)-now);
let pending=[],socket,forwarded=0,outgoing=[];
class Socket {
 constructor(){this.handlers={};}
 addEventListener(name,fn){this.handlers[name]=fn;}
 send(frame){outgoing.push(frame);}
 emit(frame){return this.handlers.message?.({data:frame});}
 close(){return this.handlers.close?.();}
}
const context={window:{WebSocket:Socket,postMessage:value=>{forwarded+=value.events.length;pending.push(h.message({action:'events',events:value.events},sender));}},location:{origin:'https://pocketoption.com'},URL,Blob,ArrayBuffer,TextDecoder,TextEncoder,Date:h.context.Date,crypto:require('node:crypto'),console};
vm.createContext(context);vm.runInContext(fs.readFileSync('connector/observer.js','utf8'),context);
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
 else throw Error('Unknown fixture action');
 await Promise.all(pending);
 while(vm.runInContext('queue.length',h.context))await vm.runInContext('flush()',h.context);
 console.log(JSON.stringify({ok:true,forwarded,outgoing:outgoing.length,lastError:h.storage.lastError||''}));
 }).catch(error=>{console.log(JSON.stringify({error:error.message}));process.exitCode=1;});});
