/* Stream-owned ribbons: no innerHTML, eval, CSS input, or user-supplied URLs.
 * Static content updates on change; clock fields at one-second resolution;
 * tickers use compositor transforms with server-clock phase correction.
 */
(function (global) {
  'use strict';
  var mediaPath = /^\/media\/[0-9]{1,12}\/(?:[0-9a-f]{32}\/)?[0-9]{3}(_t)?\.(png|gif|jpg)$/;
  function finite(n, low, high, fallback) { return typeof n === 'number' && isFinite(n) && n >= low && n <= high ? n : fallback; }
  function choose(value, values, fallback) { return values.indexOf(value) >= 0 ? value : fallback; }
  function color(value, fallback) { return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value : fallback; }
  function text(value, max) { return typeof value === 'string' ? value.slice(0, max) : ''; }
  function timestamp(n) { return typeof n === 'number' && isFinite(n) && n >= 0 && n <= 253402300799999 ? n : null; }
  function url(value) { return typeof value === 'string' && mediaPath.test(value) ? value : null; }
  function ribbon(raw) {
    raw = raw || {};
    return {enabled:raw.enabled === true, mode:choose(raw.mode,['static','ticker','clock','countdown'],'static'),
      text:text(raw.text,1000), background:color(raw.background,'#17212b'), foreground:color(raw.foreground,'#ffffff'),
      opacity:finite(raw.opacity,0,100,100), height_pct:finite(raw.height_pct,3,24,8), font_pct:finite(raw.font_pct,1,14,3.6),
      align:choose(raw.align,['left','center','right'],'left'), speed:finite(raw.speed,15,240,70), direction:raw.direction==='right'?'right':'left',
      timezone:text(raw.timezone,80)||'UTC', hour24:raw.hour24!==false, seconds:raw.seconds===true, show_date:raw.show_date===true,
      target_at:timestamp(raw.target_at), zero:choose(raw.zero,['hold','message','hide'],'message'), done_text:text(raw.done_text,160),
      start_at:timestamp(raw.start_at), end_at:timestamp(raw.end_at), asset_url:url(raw.asset_url), poster_url:url(raw.poster_url),
      asset_side:raw.asset_side==='right'?'right':'left', entry:choose(raw.entry,['none','fade','slide'],'fade'), entry_ms:finite(raw.entry_ms,0,1500,250)};
  }
  function normalize(raw) {
    raw=raw||{}; var transition=raw.transition||{}, art=raw.screensaver||{};
    return {top:ribbon(raw.top),bottom:ribbon(raw.bottom),reserve_space:raw.reserve_space===true,reduced_motion:raw.reduced_motion===true,
      transition:{effect:choose(transition.effect,['cut','fade','slide-left','slide-up'],'fade'),duration_ms:finite(transition.duration_ms,0,2000,500)},
      screensaver:{timezone:text(art.timezone,80)||null,asset_url:url(art.asset_url),poster_url:url(art.poster_url),width_pct:finite(art.width_pct,5,60,22),
        opacity:finite(art.opacity,0,100,100),position:choose(art.position,['center','top-left','top-right','bottom-left','bottom-right'],'center')}};
  }
  function pad(n) { return (n<10?'0':'')+n; }
  function active(r, now) {
    return r.enabled && (r.start_at===null || now>=r.start_at) && (r.end_at===null || now<r.end_at) &&
      !(r.mode==='countdown' && (r.target_at===null || (now>=r.target_at && r.zero==='hide')));
  }
  function format(r, now) {
    var suffix='';
    if(r.mode==='clock') {
      var options={timeZone:r.timezone,hour:'2-digit',minute:'2-digit',hour12:!r.hour24};
      if(r.seconds) options.second='2-digit';
      try {
        suffix=new Intl.DateTimeFormat('en-US',options).format(new Date(now));
        if(r.show_date) suffix+=' · '+new Intl.DateTimeFormat('en-US',{timeZone:r.timezone,month:'short',day:'numeric'}).format(new Date(now));
      } catch(_) { suffix=new Date(now).toISOString().slice(11,r.seconds?19:16)+' UTC'; }
    } else if(r.mode==='countdown' && r.target_at!==null) {
      if(now>=r.target_at && r.zero==='message') suffix=r.done_text;
      else { var seconds=Math.max(0,Math.ceil((r.target_at-now)/1000)),days=Math.floor(seconds/86400);
        suffix=(days?days+'d ':'')+pad(Math.floor(seconds/3600)%24)+':'+pad(Math.floor(seconds/60)%60)+':'+pad(seconds%60); }
    }
    return r.text+(suffix?(r.text?'  ·  ':'')+suffix:'');
  }
  function element(tag, cls) { var el=document.createElement(tag);el.className=cls;return el; }
  function create(root, nowFn, onInsets) {
    nowFn=nowFn||Date.now;onInsets=onInsets||function(){};
    root.classList.add('signage-overlay-root');
    var settings=null, mode='off', fingerprint='', nodes={}, lastSecond=-1, lastInsets='', destroyed=false;
    var mq=global.matchMedia?global.matchMedia('(prefers-reduced-motion: reduce)'):null;
    var art=element('img','signage-screensaver-art');art.alt='';art.hidden=true;root.appendChild(art);
    function reduced() { return !!((settings&&settings.reduced_motion)||(mq&&mq.matches)); }
    function stop(node) { if(node.animation){node.animation.cancel();node.animation=null;} if(node.entryAnimation){node.entryAnimation.cancel();node.entryAnimation=null;} }
    function make(side) {
      var box=element('div','signage-ribbon signage-ribbon-'+side),bg=element('div','signage-ribbon-bg');
      var image=element('img','signage-ribbon-image'),win=element('div','signage-ribbon-window'),content=element('span','signage-ribbon-text');
      image.alt='';image.hidden=true;win.appendChild(content);box.appendChild(bg);box.appendChild(image);box.appendChild(win);box.hidden=true;root.appendChild(box);
      image.addEventListener('error',function(){image.hidden=true;});
      return {box:box,bg:bg,image:image,win:win,content:content,animation:null,entryAnimation:null,width:0,duration:0};
    }
    nodes.top=make('top');nodes.bottom=make('bottom');
    function syncTicker(side,force) {
      if(!settings)return;var n=nodes[side],r=settings[side];
      if(r.mode!=='ticker'||reduced()||n.box.hidden||!n.content.animate) { if(n.animation){n.animation.cancel();n.animation=null;}n.content.classList.remove('is-ticker');return; }
      n.content.classList.add('is-ticker');
      var width=n.content.scrollWidth,viewport=n.win.clientWidth;
      if(!viewport||!width)return;
      if(force||n.width!==width||n.viewport!==viewport||!n.animation){
        if(n.animation)n.animation.cancel();
        n.width=width;n.viewport=viewport;
        var a=viewport,b=-width;
        if(r.direction==='right'){a=-width;b=viewport;}
        n.duration=(viewport+width)/(r.speed*Math.max(.05,root.clientHeight/1080))*1000;
        n.animation=n.content.animate([{transform:'translateX('+a+'px)'},{transform:'translateX('+b+'px)'}],{duration:n.duration,iterations:Infinity,easing:'linear'});
        n.animation.currentTime=((nowFn()%n.duration)+n.duration)%n.duration;
      } else {
        var desired=((nowFn()%n.duration)+n.duration)%n.duration,current=n.animation.currentTime%n.duration;
        var difference=Math.abs(desired-current);difference=Math.min(difference,n.duration-difference);
        if(difference>250)n.animation.currentTime=desired;
      }
    }
    art.addEventListener('error',function(){art._failed=true;art.hidden=true;});
    function setImage(image,source) {
      if(!source){image.hidden=true;image.removeAttribute('src');return;}
      if(image.getAttribute('src')!==source){image._failed=false;image.hidden=false;image.src=source;}
    }
    function layout() {
      if(!settings)return;var H=root.clientHeight;
      ['top','bottom'].forEach(function(side){var r=settings[side],n=nodes[side];
        n.box.style.height=r.height_pct+'%';n.box.style.color=r.foreground;
        n.box.style.fontSize=Math.min(r.font_pct,r.height_pct*.8)/100*H+'px';
        n.bg.style.backgroundColor=r.background;n.bg.style.opacity=r.opacity/100;
        n.content.style.textAlign=r.align;n.image.style.order=r.asset_side==='right'?'2':'0';
        setImage(n.image,reduced()&&r.asset_url&&/\.gif$/.test(r.asset_url)?r.poster_url:r.asset_url);
      });
      var g=settings.screensaver;
      art.style.width=g.width_pct+'%';art.style.opacity=g.opacity/100;
      art.style.left='';art.style.right='';art.style.top='';art.style.bottom='';art.style.transform='';
      if(g.position==='center'){art.style.left='50%';art.style.top='31%';art.style.transform='translate(-50%,-50%)';}
      else {art.style[g.position.indexOf('left')>=0?'left':'right']='4%';
        art.style[g.position.indexOf('top')>=0?'top':'bottom']=(4+(g.position.indexOf('top')>=0?settings.top.height_pct:settings.bottom.height_pct))+'%';}
      setImage(art,reduced()&&g.asset_url&&/\.gif$/.test(g.asset_url)?g.poster_url:g.asset_url);
      tick(true);
    }
    function tick(force) {
      if(destroyed||!settings)return;
      var now=nowFn(),second=Math.floor(now/1000),insets={top:0,bottom:0};
      ['top','bottom'].forEach(function(side){var r=settings[side],n=nodes[side],visible=mode!=='off'&&active(r,now),wasHidden=n.box.hidden;
        n.box.hidden=!visible;
        if(!visible){stop(n);return;}
        if(wasHidden&&!reduced()&&r.entry!=='none'&&r.entry_ms&&n.box.animate){
          n.entryAnimation=n.box.animate(r.entry==='fade'?[{opacity:0},{opacity:1}]:[{transform:'translateY('+(side==='top'?'-100%':'100%')+')'},{transform:'translateY(0)'}],{duration:r.entry_ms,easing:'ease-out'});
        }
        if(force||second!==lastSecond||wasHidden){var value=format(r,now);if(n.content.textContent!==value)n.content.textContent=value;syncTicker(side,force||wasHidden);}
        if(settings.reserve_space)insets[side]=r.height_pct;
      });
      art.hidden=mode!=='clock'||!art.getAttribute('src')||art._failed===true;
      var key=insets.top+':'+insets.bottom;if(key!==lastInsets){lastInsets=key;onInsets(insets);}
      lastSecond=second;
    }
    function set(raw,newMode) {
      mode=newMode||'rotation';
      if(!raw||mode==='off'){settings=null;fingerprint='';['top','bottom'].forEach(function(side){stop(nodes[side]);nodes[side].box.hidden=true;});art.hidden=true;art.removeAttribute('src');lastInsets='';onInsets({top:0,bottom:0});return;}
      var clean=normalize(raw),key=JSON.stringify(clean);
      if(key!==fingerprint){settings=clean;fingerprint=key;layout();}else tick(false);
    }
    function resize(){layout();}
    var observer=typeof ResizeObserver!=='undefined'?new ResizeObserver(resize):null;
    if(observer)observer.observe(root);else global.addEventListener('resize',resize);
    function motionChange(){layout();}
    if(mq){if(mq.addEventListener)mq.addEventListener('change',motionChange);else if(mq.addListener)mq.addListener(motionChange);}
    var timer=setInterval(function(){tick(false);},250);
    return {set:set,tick:tick,reduced:reduced,settings:function(){return settings;},destroy:function(){destroyed=true;clearInterval(timer);if(observer)observer.disconnect();global.removeEventListener('resize',resize);
      if(mq){if(mq.removeEventListener)mq.removeEventListener('change',motionChange);else if(mq.removeListener)mq.removeListener(motionChange);}
      ['top','bottom'].forEach(function(side){stop(nodes[side]);});while(root.firstChild)root.removeChild(root.firstChild);}};
  }
  global.SignageOverlay={create:create,normalize:normalize,format:format,active:active};
})(window);
