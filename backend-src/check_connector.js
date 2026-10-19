"use strict";
// Protocol fixtures only; this does not certify the live broker's current schema.
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
let sockets=[],sent=[],forwarded=[];
class Socket {
  constructor(url){this.url=url;this.handlers={};sockets.push(this);}
  addEventListener(name,fn){this.handlers[name]=fn;}
  send(value){sent.push(value);}
  emit(data){this.handlers.message?.({data});}
}
const sandbox={window:{WebSocket:Socket,postMessage:(value)=>forwarded.push(value)},location:{origin:"https://pocketoption.com"},URL,Blob,ArrayBuffer,TextDecoder,Date,Promise,Number,JSON,Object,console};
vm.runInNewContext(fs.readFileSync("connector/observer.js","utf8"),sandbox);
const socket=new sandbox.window.WebSocket("wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket");
socket.emit('42["auth",{"session":"fixture-secret-never-forward","uid":456,"isDemo":1}]');
socket.emit('42["successauth",{"session":"fixture-secret-never-forward","uid":456,"isDemo":1,"currency":"USD","balance":100}]');
socket.emit('42["successupdateBalance",{"balance":108,"isDemo":1}]');
socket.emit('451-["updateStream",{"_placeholder":true,"num":0}]');
socket.emit(new TextEncoder().encode(JSON.stringify([["EURUSD",Date.now()/1000,1.1]])).buffer);
socket.emit('42["successcloseOrder",{"session":"fixture-secret-never-forward","uid":456}]'); // Incomplete result must be ignored.
setImmediate(()=>{
  const events=forwarded.flatMap(x=>x.events);
  assert.equal(sent.length,0,"Observer must never send an order or subscription");
  assert.equal(events.filter(x=>x.type==="account").length,2);
  assert.equal(events.find(x=>x.type==="account").mode,"demo");
  assert.equal(events.filter(x=>x.type==="quote").length,1);
  assert.equal(events.filter(x=>x.type==="trade").length,0);
  assert(!JSON.stringify(forwarded).includes("fixture-secret"));
  assert(events.every(x=>!Object.hasOwn(x,"session") && !Object.hasOwn(x,"password")));
  console.log(JSON.stringify({ok:true,fixture_protocol:true,auth_fields_not_exported:true,no_outbound_commands:true,binary_quotes_decoded:true,incomplete_settlements_ignored:true}));
});
