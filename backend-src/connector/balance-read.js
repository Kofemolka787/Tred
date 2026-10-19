"use strict";
// Read only the displayed account header, never forms, credentials or orders.
globalThis.TredTerminalBalance=(()=>{
  const label=/^(?:QT\s*)?(Demo|Real)(?:\s*[A-Z]{3})?$/i;
  const header=/^(?:QT\s*)?(Demo|Real)\s*([A-Z]{3})\s*((?:\d{1,3}(?:[ ,\u00a0\u202f]\d{3})+|\d{1,8})(?:[.,]\d{2})?)$/i;
  const clean=s=>String(s||"").replace(/[\u200b-\u200d\ufeff]/g,"").trim();
  function money(value){
    let s=value.replace(/[ \u00a0\u202f]/g,"");
    if(/^\d{1,3}(,\d{3})+(\.\d{2})?$/.test(s))s=s.replace(/,/g,"");
    else if(/^\d+,\d{2}$/.test(s))s=s.replace(",",".");
    if(!/^\d{1,8}(\.\d{2})?$/.test(s)||Number(s)>10000000)return null;
    return s.includes(".")?s:s+".00";
  }
  const visible=node=>node?.isConnected&&node.getClientRects().length>0&&getComputedStyle(node).visibility!=="hidden";
  function read(){
    if(!/\/(?:[a-z]{2}\/)?cabinet\//i.test(location.pathname))return {code:"not_terminal"};
    const found=new Map();
    for(const node of Array.from(document.querySelectorAll("span,div,button,a,strong")).slice(0,10000)){
      const text=clean(node.textContent);
      if(text.length>40||!label.test(text)||!visible(node))continue;
      let parent=node;
      for(let depth=0;parent&&depth<5;depth++,parent=parent.parentElement){
        if(!visible(parent))break;
        const rendered=clean(parent.innerText).replace(/\s+/g," ");
        if(rendered.length>120)break;
        const match=header.exec(rendered);if(!match)continue;
        const balance=money(match[3]);if(balance===null)continue;
        const value={mode:match[1].toLowerCase()==="demo"?"demo":"real",currency:match[2].toUpperCase(),balance};
        found.set(JSON.stringify(value),value);break;
      }
    }
    if(found.size!==1)return {code:found.size?"ambiguous_header":"header_not_found"};
    const snapshot=found.values().next().value;
    if(snapshot.currency!=="USD")return {code:"unsupported_currency"};
    return {code:"ready",snapshot};
  }
  return {read};
})();
