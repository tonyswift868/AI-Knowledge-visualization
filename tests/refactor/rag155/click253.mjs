// CDP click helper: resolve the selector afresh on every sample so React
// replacement during layout cannot leave us with a stale element reference.
export async function click253(evaluate, dispatch, selector, {timeoutMs=12000, pollMs=25, stableSamples=3, onSample=()=>{}}={}) {
  const started=performance.now();let previous=null,stable=0,last={selector};
  while(performance.now()-started<timeoutMs){
    last=await evaluate(`(()=>{
      const e=document.querySelector(${JSON.stringify(selector)});
      if(!e)return {found:false};
      e.scrollIntoView({block:'nearest',inline:'nearest'});
      const r=e.getBoundingClientRect(),s=getComputedStyle(e),x=r.left+r.width/2,y=r.top+r.height/2;
      e.__click253Identity??=(window.__click253NextId=(window.__click253NextId||0)+1);
      const hit=x>=0&&y>=0&&x<innerWidth&&y<innerHeight?document.elementFromPoint(x,y):null;
      const cssVisible=e.checkVisibility?e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}):s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)>0;
      const visible=!!(e.isConnected&&cssVisible&&!e.matches(':disabled'));
      const scrollAncestors=[];for(let a=e.parentElement;a;a=a.parentElement){const q=a.getBoundingClientRect();if(a.scrollHeight>a.clientHeight+1||a.scrollWidth>a.clientWidth+1)scrollAncestors.push({tag:a.tagName,className:String(a.className||''),scrollTop:a.scrollTop,clientHeight:a.clientHeight,scrollHeight:a.scrollHeight,rect:{left:q.left,top:q.top,right:q.right,bottom:q.bottom}});}
      return {found:true,connected:e.isConnected,clientRects:e.getClientRects().length,readyState:document.readyState,
        streaming:!!document.querySelector('[data-testid="rag-streaming-reply"]'),pending:!!document.querySelector('[data-testid="rag-send"]:disabled'),
        scrollAncestors,visible,width:r.width,height:r.height,x,y,rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom},
        viewport:{width:innerWidth,height:innerHeight},identity:e.__click253Identity,text:e.textContent,stableKey:e.tagName+'|'+e.getAttribute('data-testid')+'|'+e.textContent,
        ready:visible&&r.width>0&&r.height>0&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&(hit===e||e.contains(hit)),
        hit:hit?.outerHTML?.slice(0,180)||null};
    })()`);
    await onSample(last);
    if(last.ready){const key=JSON.stringify([last.rect,last.identity,last.stableKey]);stable=key===previous?stable+1:1;previous=key;
      if(stable>=stableSamples){
        // Re-resolve and check the exact point immediately before dispatch.
        const final=await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;const r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,h=x>=0&&y>=0&&x<innerWidth&&y<innerHeight?document.elementFromPoint(x,y):null,s=getComputedStyle(e),v=e.checkVisibility?e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}):s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)>0;e.__click253Identity??=(window.__click253NextId=(window.__click253NextId||0)+1);return {x,y,width:r.width,height:r.height,top:r.top,left:r.left,identity:e.__click253Identity,text:e.textContent,ready:e.isConnected&&v&&!e.matches(':disabled')&&r.width>0&&r.height>0&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&(h===e||e.contains(h)),hit:h?.outerHTML?.slice(0,180)||null}})()`);
        const samePosition=Math.abs(final?.x-(last.x??NaN))<=.5&&Math.abs(final?.y-(last.y??NaN))<=.5;
        if(final?.identity!==last.identity)last={...last,final,reason:'target identity changed'};
        if(final?.ready&&final.identity===last.identity&&samePosition){await dispatch('mousePressed',final.x,final.y);await dispatch('mouseReleased',final.x,final.y);return final;}
        last={...last,final};stable=0;previous=null;
      }
    }else{stable=0;previous=null;}
    await new Promise(r=>setTimeout(r,pollMs));
  }
  throw Error(`click253 timeout selector=${selector} elapsedMs=${Math.round(performance.now()-started)} state=${JSON.stringify(last)}`);
}
