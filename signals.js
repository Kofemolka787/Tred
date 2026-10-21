"use strict";
// Explainable rules and user-reported outcomes. No order submission or ML claims.
window.TredSignals=(()=>{
  let hooks,chosen=null,offset=0,working=false;
  const $=s=>document.querySelector(s);
  const node=(name,cls,value)=>{const e=document.createElement(name);if(cls)e.className=cls;if(value!=null)e.textContent=value;return e;};
  const price=v=>Number.isFinite(v)?Number(v).toLocaleString("en-US",{maximumFractionDigits:6}):"—";
  const now=()=>Date.now()/1000+offset;
  const clock=v=>new Date(v*1000).toLocaleTimeString("ru-RU");
  const duration=s=>{s=Math.max(0,Math.ceil(s));return `${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`;};
  function svg(name,attrs={}){const e=document.createElementNS("http://www.w3.org/2000/svg",name);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e;}
  function chart(host,setup,session=null){
    host.replaceChildren();
    const image=svg("svg",{viewBox:"0 0 360 190",role:"img","aria-label":session?"Движение полученной цены после подтверждения входа":"Закрытые свечи, зоны и границы сценария"});
    const candles=session?[]:(setup.candles||[]);
    const points=session?[{time:session.opened_at,price:session.entry_price},...(session.series||[])]:[];
    if(!candles.length&&!points.length){host.append(node("p","quiet-text","История для графика ещё не получена."));return;}
    const levels=[...candles.flatMap(c=>[c.high,c.low]),...points.map(p=>p.price),...(!session?(setup.zones||[]).flatMap(z=>[z.low,z.high]):[])];
    if(session)levels.push(session.entry_price);else if(setup.invalidation)levels.push(setup.invalidation);
    const lo=Math.min(...levels),hi=Math.max(...levels),padding=Math.max((hi-lo)*.12,Math.abs(hi)*.00001),bottom=lo-padding,top=hi+padding;
    const start=session?session.opened_at:candles[0].time;
    const end=session?Math.max(start+30,Math.min(now(),session.expires_at),...points.map(p=>p.time)):candles[candles.length-1].time+(setup.timeframes?.[0]||60);
    const x=t=>12+Math.max(0,Math.min(1,(t-start)/(end-start)))*278;
    const y=p=>160-(p-bottom)/(top-bottom)*146;
    for(let i=0;i<4;i++){const p=bottom+(top-bottom)*i/3;image.append(svg("line",{x1:12,y1:y(p),x2:292,y2:y(p),stroke:"#ffffff12"}));const label=svg("text",{x:298,y:y(p)+3,fill:"#b4a7c7","font-size":8,stroke:"none"});label.textContent=price(p);image.append(label);}
    if(!session){
      for(const z of setup.zones||[]){image.append(svg("rect",{x:x(Math.max(start,z.time)),y:y(z.high),width:292-x(Math.max(start,z.time)),height:Math.max(1,y(z.low)-y(z.high)),fill:z.kind==="fvg"?"#20bddb1f":"#9c54ff25",stroke:z.kind==="fvg"?"#20bddb66":"#9c54ff66"}));}
      const barWidth=Math.max(2,Math.min(8,220/candles.length));
      for(const c of candles){const color=c.close>=c.open?"#21d2ac":"#ff6d8d",cx=x(c.time);image.append(svg("line",{x1:cx,y1:y(c.high),x2:cx,y2:y(c.low),stroke:color}));image.append(svg("rect",{x:cx-barWidth/2,y:y(Math.max(c.open,c.close)),width:barWidth,height:Math.max(1,Math.abs(y(c.open)-y(c.close))),fill:color,stroke:"none"}));}
      const ch=setup.channel;
      if(ch)for(const side of ["low","high"]){const slope=ch["slope_"+side];image.append(svg("line",{x1:x(start),y1:y(ch[side]+slope*(start-ch.time)),x2:292,y2:y(ch[side]+slope*(end-ch.time)),stroke:"#a9d75c","stroke-width":1}));}
      for(const event of new Map((setup.events||[]).map(e=>[e.kind,e])).values()){const label=svg("text",{x:x(event.time),y:Math.max(14,Math.min(150,y(event.level)-5)),fill:"#c8b0ff","font-size":8,stroke:"none"});label.textContent=event.kind==="bos"?"BOS":"sweep";image.append(label);}
    }else{
      let path="",previous=null;
      for(const p of points){path+=(previous&&p.time-previous.time<=15?" L":" M")+x(p.time)+","+y(p.price);previous=p;}
      image.append(svg("path",{d:path,fill:"none",stroke:"#37d6e4","stroke-width":2}));
      const last=points.at(-1);image.append(svg("circle",{cx:x(last.time),cy:y(last.price),r:3,fill:"#37d6e4",stroke:"none"}));
      image.append(svg("line",{x1:12,y1:y(session.entry_price),x2:292,y2:y(session.entry_price),stroke:"#d0adff","stroke-dasharray":"5 4"}));
    }
    const caption=svg("text",{x:12,y:182,fill:"#a197b2","font-size":8,stroke:"none"});caption.textContent=clock(start)+" → "+clock(end);image.append(caption);
    host.append(image);
  }
  function why(setup){
    const detail=node("details","signal-why");detail.append(node("summary","","Почему этот сценарий"));
    if(setup.strategy_name)detail.append(node("p","",`Стратегия: ${setup.strategy_name}. Таймфреймы: ${(setup.timeframes||[]).map(p=>"M"+p/60).join(" + ")}.`));
    if(setup.strategy_risk)detail.append(node("p","","Слабость стратегии: "+setup.strategy_risk));
    if(setup.ai)detail.append(node("p","",`${setup.ai.provider} · ${setup.ai.model}: ${setup.ai.summary}`),node("p","","Что учитывать: "+setup.ai.risk));
    const list=node("ol");for(const line of setup.evidence||[])list.append(node("li","",line));detail.append(list);
    detail.append(node("p","","BOS — закрытие за подтверждённым экстремумом. FVG — разрыв между первой и третьей свечой импульса. ATR — средний истинный диапазон свечей. Sweep — вынос экстремума с возвратом."));
    detail.append(node("p","",`Зона входа ${price(setup.entry_low)}–${price(setup.entry_high)}. Отмена сценария: ${setup.direction==="up"?"цена ниже":"цена выше"} ${price(setup.invalidation)}. Это условие пропуска нового входа; сделку у брокера оно не закрывает.`));
    detail.append(node("p","","На OTC видны цены брокера. Order block, FVG и sweep — модели свечей; реальные заявки и стопы участников этим не подтверждаются. Более длинная экспирация сама по себе не гарантирует надёжность."));return detail;
  }
  function timer(){
    for(const el of document.querySelectorAll("[data-until]"))el.textContent=duration(Number(el.dataset.until)-now());
    const button=$("#watch-entry");
    if(chosen&&$("#close-preview"))$("#close-preview").textContent=`Вход в ближайшие ${duration(chosen.valid_until-now())}, пока цена в зоне. Если войти сейчас — завершение примерно ${clock(now()+chosen.expiry*60)}. У брокера выбери длительность ${chosen.expiry} мин и сверь время закрытия.`;
    if(button){
      const state=hooks.getState(),q=state?.feed?.pairs?.find(p=>p.symbol===chosen?.symbol);
      const expired=now()>chosen?.valid_until;
      const changed=!q?.quote_live||q.quote_time==null||now()-q.quote_time>10||!q.eligible||q.price<chosen.entry_low||q.price>chosen.entry_high;
      button.disabled=working||hooks.getBusy()||expired||changed||!state?.accounts?.length||!!state?.stale;
      $("#entry-feedback").textContent=expired?"Окно закончилось. Запроси новый анализ.":changed?"Цена или доступность изменились. Вход сейчас не подтверждён; запроси новый анализ.":"Зона входа актуальна по последней полученной цене.";
    }
  }
  function show(result){
    chosen=result.status==="candidate"?result:null;
    const host=$("#signal-result");host.replaceChildren();host.hidden=false;
    if(!chosen){
      const invalidReply=/^(decision_|model_)/.test(result.ai_error_code||"");
      const title=result.status==="loading"?(result.phase==="ai"?"ИИ анализирует рынок":result.phase==="entry_quote"?"Проверяем цену входа":result.phase==="quotes"?"Получаем текущие цены":"Загружаем данные всех пар"):result.status==="setup_required"?"Подключи ИИ один раз":result.status==="ai_unavailable"?(invalidReply?"Ответ ИИ не прошёл проверку":"ИИ пока недоступен"):result.status==="cancelled"?"Проверка остановлена":result.waiting_data?"Не хватает данных для анализа":"ИИ не подтвердил вход";
      host.append(node("h3","",title),node("p","",result.reason||"Ждём подтверждённые условия."));
      if(result.status==="ai_unavailable"&&/^[a-z_]{3,48}$/.test(result.ai_error_code||""))host.append(node("p","quiet-text","Код проверки: "+result.ai_error_code));
      if(result.progress)host.append(node("p","",`Графики для ИИ: ${result.progress.analysis_ready??result.progress.history_ready} / ${result.progress.total} · свежие котировки: ${result.progress.quoted}`));
      if(result.status==="loading"){const b=node("button","primary-small","Остановить проверку");b.id="cancel-scan";b.type="button";host.append(b);}
      if(result.ai){host.append(node("p","",`${result.ai.provider} · ${result.ai.model} · в списке: ${result.ai.reviewed_pairs}${Number.isInteger(result.ai.analyzed_charts)?" · графиков в анализе: "+result.ai.analyzed_charts:""}${Number.isInteger(result.ai.ready_pairs)?" · со свежей ценой на момент анализа: "+result.ai.ready_pairs:""}${Number.isInteger(result.ai.qualified_setups)?" · найдено сценариев: "+result.ai.qualified_setups:""}`),node("p","","Риск/ограничение: "+result.ai.risk));}
      return;
    }
    host.append(node("p","eyebrow",`${result.market==="otc"?"OTC":"ОБЫЧНЫЙ РЫНОК"} · ${result.ai?"ИИ-СЦЕНАРИЙ":"СЦЕНАРИЙ ПО СТРУКТУРЕ"}`),node("h2","",`${result.symbol} ${result.direction==="up"?"↑ ВВЕРХ":"↓ ВНИЗ"}`),node("p","",`${result.expiry} мин · выплата ${Math.round(result.payout*100)}% · анализ M${result.timeframes[0]/60} + M${result.timeframes[1]/60}`));
    if(result.strategy_name)host.append(node("p","entry-window",result.strategy_name));
    if(result.ai)host.append(node("p","",`${result.ai.provider} · ${result.ai.model} · рассмотрено пар: ${result.ai.reviewed_pairs} · графиков в анализе: ${result.ai.analyzed_charts??result.ai.ready_pairs} / ${result.total_pairs}`));
    const preview=node("p","entry-window");preview.id="close-preview";host.append(preview);
    const canvas=node("div","market-chart");chart(canvas,result);host.append(canvas);
    const status=node("p","entry-window","Окно входа: "),time=node("strong");time.dataset.until=result.valid_until;status.append(time);host.append(status);
    const feedback=node("p","quiet-text");feedback.id="entry-feedback";host.append(feedback);
    host.append(node("p","",`Вход только в зоне ${price(result.entry_low)}–${price(result.entry_high)}. Если цена ушла или время истекло, запроси новый анализ.`),why(result));
    const form=node("form","watch-form");form.id="watch-form";
    const modeLabel=node("label","","Счёт, на котором ты открыл сделку"),mode=node("select");mode.id="watch-mode";
    for(const a of hooks.getState().accounts||[]){const option=node("option","",a.mode==="real"?"Реальный счёт":"Демосчёт");option.value=a.mode;if(a.mode==="real")option.selected=true;mode.append(option);}modeLabel.append(mode);
    const label=node("label","","Фактическая цена входа · необязательно"),input=node("input");input.id="watch-price";input.type="number";input.step="any";input.min="0";input.inputMode="decimal";input.placeholder=price(result.price);label.append(input);
    const button=node("button","primary-small","Я открыл сделку");button.id="watch-entry";button.type="submit";
    form.append(modeLabel,label,node("small","","Кнопка отмечает твой вход. Если цену не указать, график начнётся с последней полученной котировки. Отсчёт идёт от нажатия."),button);host.append(form);timer();
  }
  async function action(path,value){
    if(working||hooks.getBusy())return;working=true;hooks.setBusy(true);timer();
    try{const state=await hooks.api(path,value);hooks.setState(state);hooks.render();return true;}
    catch(error){hooks.notify(error.message||"Не удалось сохранить действие.");return false;}
    finally{working=false;hooks.setBusy(false);timer();}
  }
  function render(state){
    if(Number.isFinite(state.server_time))offset=state.server_time-Date.now()/1000;
    const prefs=state.preferences;
    if(chosen&&(chosen.expiry!==prefs.expiry||chosen.market!==prefs.market||chosen.payout<(prefs.min_payout||85)/100||!(state.recent_requests||[]).some(r=>r.analysis_id===chosen.analysis_id&&r.valid_until===chosen.valid_until))){chosen=null;$("#signal-result").hidden=true;}
    const feed=state.feed;
    const metadata=feed?.metadata_eligible??feed?.pairs?.filter(p=>!["closed","payout","expiry"].includes(p.stage)).length??feed?.eligible;
    $("#feed-coverage").textContent=feed?`Каталог: ${feed.catalog} · выплата и время подходят: ${metadata} · свежая цена: ${feed.quoted} · готовы к анализу: ${feed.analysis_ready??feed.ready}`:"Для анализа нужен обновлённый сервер TRED.";
    const list=$("#coverage-list");list.replaceChildren();
    for(const p of feed?.pairs||[]){
      const row=node("p","",`${p.symbol} · ${Math.round(p.payout*100)}% · ${p.quote_live?"цена поступает":"нет свежей цены"} · ${(p.timeframes||feed.timeframes).map((t,i)=>`M${t/60}: ${p.bars[i]}`).join(" / ")} · ${p.reason}`);
      if(Array.isArray(p.available_history))row.append(node("small","",p.available_history.length?"На сервере, непрерывные закрытые свечи: "+p.available_history.map(h=>`M${h.period/60}: ${h.bars} · возраст ${h.age_seconds} с`).join("; "):"В этом канале пока нет сохранённых закрытых свечей."));
      if(p.regimes?.length){const labels={trend:"тренд",range:"боковик",transition:"переходный"};row.append(node("small","","Режим: "+p.regimes.map((r,i)=>`M${(p.timeframes||feed.timeframes)[i]/60} — ${labels[r]||r}`).join(" / ")));}
      const d=p.history_status;
      if(d){const labels={ohlc:"распознаны OHLC",ticks:"свечи по тикам",empty:"пустой ответ",period_missing:"в ответе нет таймфрейма",unsupported_period:"нужен поддерживаемый период свечи",format_unrecognized:"формат пока не распознан"};row.append(node("small","",`История: ${d.event} · ${d.period?"M"+d.period/60:"период не указан"} · строк ${d.rows} · распознано ${d.bars} · ${labels[d.code]||"ожидаем данные"}`));}
      list.append(row);
    }
    const suggestions=$(".pair-suggestions");
    if(feed?.pairs?.length){suggestions.replaceChildren();for(const p of feed.pairs){const b=node("button","",`${p.symbol} · ${Math.round(p.payout*100)}%`);b.type="button";b.dataset.symbol=p.symbol;suggestions.append(b);}}
    $("#min-payout").value=state.preferences.min_payout||85;
    const strategies=$("#strategy-list"),strategyHelp=$("#strategy-help");
    if(strategies&&strategyHelp){
      strategyHelp.hidden=!feed?.strategy_catalog?.length;strategies.replaceChildren();
      for(const s of feed?.strategy_catalog||[]){const row=node("p");row.append(node("strong","",s.name),node("span",""," — "+s.rule),node("small","","Слабость: "+s.weakness));strategies.append(row);}
    }
    $("#signals-upgrade").hidden=state.mode!=="api"||state.market_data_version===5&&!!feed?.strategy_catalog?.length;
    $("#ai-status").textContent=state.mode!=="api"?"ИИ подключается к твоему серверу TRED.":state.ai?.configured?`ИИ: ${state.ai.provider} · ${state.ai.model}. Ключ настроен; решение проверяется по свежим данным.`:"ИИ ещё не настроен. Запусти SETUP-LUNAROUTE.cmd на компьютере и введи ключ LunaRoute локально.";
    const help=$("#feed-help");
    if(feed?.blocked){
      const b=feed.blocked;
      const matching=(feed.pairs||[]).filter(p=>!["closed","payout","expiry"].includes(p.stage));
      const missingHistory=matching.filter(p=>!p.history_ready).length,missingQuotes=matching.filter(p=>!p.quote_live).length;
      help.textContent=matching.length&&(missingHistory||missingQuotes)?`Сбор данных: историю ожидают ${missingHistory} пар, свежую цену — ${missingQuotes}. Расширение обновляет их автоматически; листать графики вручную не нужно. Оставь терминал и сервер открытыми.`:b.catalog?`Каталог канала недоступен: ${b.catalog}. После обновления расширения перезагрузи вкладку Pocket один раз.`:feed.ready?`Данные готовы по ${feed.ready} парам. ИИ сравнит доступные структуры и выберет сценарий либо объяснит пропуск.`:"В каталоге показаны все полученные пары выбранного рынка, включая закрытые и неподходящие по выплате.";
    }
    const sessions=state.signal_sessions||[],pending=sessions.filter(s=>!s.result),container=$("#live-sessions");
    const opened=new Set([...container.querySelectorAll("details[open]")].map(d=>d.dataset.session));container.replaceChildren();
    for(const s of pending){
      const card=node("article","session-card");card.dataset.session=s.id;
      card.append(node("p","eyebrow",`${s.mode==="real"?"РЕАЛЬНЫЙ СЧЁТ":"ДЕМО"} · ТВОЯ ОТМЕТКА ВХОДА`),node("h3","",`${s.symbol} ${s.direction==="up"?"↑":"↓"} · ${s.expiry} мин`));
      const line=node("p","",s.status==="open"?"До завершения: ":"Время закончилось. Укажи результат из Pocket Option.");
      if(s.status==="open"){const counter=node("strong");counter.dataset.until=s.expires_at;line.append(counter);}card.append(line);
      card.append(node("p",s.live&&!state.stale?"positive":"negative",s.live&&!state.stale?`Цена ${price(s.current_price)} · ${clock(s.quote_time)}`:"Котировки не поступают. Последние точки сохранены; ждём связь."));
      card.append(node("p","quiet-text",`Вход ${price(s.entry_price)} · ${s.entry_source==="user"?"цена указана тобой":"котировка при нажатии, не подтверждённая цена исполнения"}`));
      const canvas=node("div","market-chart");chart(canvas,s.setup,s);card.append(canvas);
      const details=why(s.setup);details.dataset.session=String(s.id);details.open=opened.has(String(s.id));card.append(details);
      if(s.status==="awaiting_result"){
        const controls=node("div","result-controls");
        for(const [result,label] of [["win","Плюс"],["loss","Минус"],["draw","Возврат"]]){const b=node("button",result,label);b.type="button";b.disabled=working||hooks.getBusy();b.dataset.result=result;b.dataset.session=s.id;controls.append(b);}card.append(controls,node("small","","Запишем выбранный тобой результат. Итоговую выплату подтверждает брокер."));
      }
      container.append(card);
    }
    const history=$("#signal-trade-list");history.replaceChildren();
    for(const s of sessions.filter(s=>s.result)){
      const row=node("article","trade-entry");row.append(node("strong","",`${s.symbol} ${s.direction==="up"?"↑":"↓"} · ${s.result==="win"?"Плюс":s.result==="loss"?"Минус":"Возврат"}`),node("p","",`${s.mode==="real"?"Реальный счёт":"Демо"} · ${s.expiry} мин · ${new Date(s.opened_at*1000).toLocaleString()} · результат указан тобой`),why(s.setup));history.append(row);
    }
    $("#signal-history-section").hidden=!sessions.some(s=>s.result);
    const recorded=sessions.filter(s=>s.result),wins=recorded.filter(s=>s.result==="win").length,losses=recorded.filter(s=>s.result==="loss").length;
    $("#signal-journal-stats").textContent=`Отмечено: ${recorded.length} · плюс ${wins} / минус ${losses}. Результаты со слов пользователя; отдельно от полученных сделок брокера.`;
    if(chosen&&sessions.some(s=>s.analysis_id===chosen.analysis_id)){$("#signal-result").hidden=true;chosen=null;}
    timer();
  }
  function init(value){hooks=value;
    document.addEventListener("submit",async event=>{
      if(event.target.id!=="watch-form")return;event.preventDefault();
      if(!chosen||working)return;
      const body={analysis_id:chosen.analysis_id,mode:$("#watch-mode").value},value=$("#watch-price").value;
      if(value)body.entry_price=Number(value);
      if(await action("/api/watch",body)){hooks.notify("Вход отмечен. Теперь можно следить с телефона.");$("#live-sessions").scrollIntoView({block:"start",behavior:"smooth"});}
    });
    document.addEventListener("click",async event=>{const b=event.target.closest("[data-result]");if(b&&!working)await action("/api/result",{session_id:Number(b.dataset.session),result:b.dataset.result});});
    $("#min-payout").addEventListener("change",()=>hooks.update({min_payout:Number($("#min-payout").value)}));
    setInterval(timer,1000);
  }
  return {init,render,show};
})();
