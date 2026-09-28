/*
 * Signage player — runs on each Display Cast Pro in Web mode.
 *
 * Every player on a stream computes the current item from the shared clock:
 *     pos = (serverNow - anchor) mod loopLength
 * so displays on the same stream stay frame-close without talking to each other.
 * The server clock offset is estimated from the lowest-latency recent poll.
 *
 * Written in conservative JS so older embedded browsers are fine.
 */
(function () {
  "use strict";

  var path = location.pathname.replace(/\/+$/, "");
  var m = path.match(/^\/display\/([^/]+)$/);
  var p = path.match(/^\/preview\/(\d+)$/);
  var c = path.match(/^\/stream\/([0-9a-f]{32})$/);
  var endpoint = c ? "/api/player/channel/" + c[1] : m ? "/api/player/display/" + encodeURIComponent(m[1]) : p ? "/api/player/stream/" + p[1] : null;
  var cacheKey = "lc_state:" + path;
  var params = new URLSearchParams(location.search);
  var withSound = params.get("sound") === "1";

  var stage = document.getElementById("stage");
  var msgEl = document.getElementById("msg");
  var clockEl = document.getElementById("clock");
  var clock24 = true;
  var clockMoved = 0;
  var lastClock = 0, inFlight = false, lastSuccess = 0;

  var haveNetworkState = false;
  var state = null;          // { items, anchor_ms, version, ... }
  var total = 0;
  var starts = [];           // cumulative start offset of each item
  var offset = 0;            // serverTime - Date.now()
  var samples = [];          // [{rtt, offset}]
  var pollMs = 5000;
  var fadeMs = 500;
  var pollTimer = null;
  var edgeTimer = null;

  var cur = null;            // { key, slideId, el, kind }
  var prepared = {};         // key -> element (next item, preloaded)
  var lastSeek = 0;

  function serverNow() { return Date.now() + offset; }
  var overlay = window.SignageOverlay ? window.SignageOverlay.create(document.getElementById('overlays'), serverNow, function(insets) {
    stage.style.top = insets.top + '%'; stage.style.bottom = insets.bottom + '%';
  }) : null;
  var transitionEffect = 'fade';
  function updatePresentation(s) {
    var settings = s && s.presentation && s.presentation.settings;
    if (overlay) overlay.set(settings || null, s ? s.mode : 'off');
    var clean = overlay && overlay.settings();
    var transition = clean && clean.transition;
    transitionEffect = transition ? transition.effect : 'fade';
    fadeMs = transition ? transition.duration_ms : Math.max(0, Math.min(2000, Number(s && s.fade_ms) || 0));
    if (transitionEffect === 'cut' || (overlay && overlay.reduced())) fadeMs = 0;
    document.documentElement.style.setProperty('--fade', fadeMs + 'ms');
  }

  function message(text) {
    msgEl.textContent = text || "";
    msgEl.style.display = text ? "block" : "none";
  }

  // ---- state / polling ---------------------------------------------------
  function approvedURL(value, origins) {
    try {
      var url = new URL(value);
      return (url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password &&
        !url.hash && Array.isArray(origins) && origins.indexOf(url.origin) !== -1;
    } catch (_) { return false; }
  }
  function validState(s) {
    if (!s || typeof s.version !== "string" || !Array.isArray(s.items) || s.items.length > 10000 || !Number.isFinite(s.anchor_ms)) return false;
    for (var i = 0; i < s.items.length; i++) {
      var item = s.items[i];
      if (!item || !Number.isFinite(item.duration_ms) || item.duration_ms <= 0 || item.duration_ms > 14400000) return false;
      if (/^(image|video)$/.test(item.kind)) {
        if (!/^\/media\/[0-9]{1,12}\/(?:[0-9a-f]{32}\/)?[0-9]{3}\.(jpg|mp4|png|gif)$/.test(item.url)) return false;
      } else if (/^(hls|remote_video|remote_image|web)$/.test(item.kind)) {
        if (!approvedURL(item.url, item.kind === "web" ? s.frame_origins : s.media_origins)) return false;
      } else return false;
    }
    return true;
  }
  function release(el) {
    if (!el) return;
    el._released = true;
    if (el._transition) { el._transition.cancel(); el._transition = null; }
    clearTimeout(el._loadTimer);
    if (el._hls) { try { el._hls.destroy(); } catch (_) {} el._hls = null; }
    if (el.tagName === "VIDEO") { try { el.pause(); el.removeAttribute("src"); el.load(); } catch (_) {} }
    else el.removeAttribute("src");
    if (el.parentNode) el.parentNode.removeChild(el);
  }
  function clearPrepared() {
    for (var key in prepared) if (Object.prototype.hasOwnProperty.call(prepared, key)) release(prepared[key]);
    prepared = {};
  }
  function applyState(s, fromCache) {
    if (!validState(s)) return;
    if (state && haveNetworkState && !fromCache && JSON.stringify([state.media_origins, state.frame_origins]) !== JSON.stringify([s.media_origins, s.frame_origins])) {
      // Source policy changes also require a fresh response CSP. Save before
      // reload to avoid a stale-cache reload loop; ordinary content edits do not reload.
      try { localStorage.setItem(cacheKey, JSON.stringify(s)); } catch (_) {}
      location.reload(); return;
    }
    if (!fromCache) haveNetworkState = true;
    if (s.poll_seconds) pollMs = Math.max(2, Math.min(60, s.poll_seconds)) * 1000;
    if (s.clock_24h !== undefined) clock24 = !!s.clock_24h;
    updatePresentation(s);
    // Appearance has its own version: save edits even when playlist identity is unchanged.
    if (!fromCache && (!state || state.version !== s.version || JSON.stringify(state.presentation) !== JSON.stringify(s.presentation) || state.mode !== s.mode || state.next_change_ms !== s.next_change_ms)) {
      try { localStorage.setItem(cacheKey, JSON.stringify(s)); } catch (_) {}
    }
    if (state && state.version === s.version) { state = s; scheduleEdge(); return; }
    state = s;
    starts = []; total = 0;
    for (var i = 0; i < s.items.length; i++) { starts.push(total); total += s.items[i].duration_ms; }
    clearPrepared();
    if (!fromCache) { try { localStorage.setItem(cacheKey, JSON.stringify(s)); } catch (e) { /* ignore */ } }
    scheduleEdge();
    tick(true);
  }

  function scheduleEdge() {
    // Refresh right on the next scheduled start/end so every display flips together.
    if (edgeTimer) { clearTimeout(edgeTimer); edgeTimer = null; }
    if (!state) return;
    var edges = [state.next_change_ms, state.presentation_change_ms].filter(function(t) { return typeof t === 'number' && t > serverNow(); });
    if (!edges.length) return;
    var wait = Math.min.apply(Math, edges) - serverNow() + 150;
    if (wait > 0 && wait < 24 * 3600e3) edgeTimer = setTimeout(poll, wait);
  }

  function poll() {
    if (!endpoint || inFlight) return;
    inFlight = true;
    clearTimeout(pollTimer);
    var url = endpoint + (cur && m ? "?now=" + cur.slideId : "");
    var t0 = Date.now();
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timeout = null;
    // XHR gives old embedded browsers a real timeout even without AbortController.
    function fetchState() {
      if (controller) {
        timeout = setTimeout(function () { controller.abort(); }, 15000);
        return fetch(url, { cache: "no-store", signal: controller.signal });
      }
      return new Promise(function (resolve, reject) {
        var xhr = new XMLHttpRequest(); xhr.open("GET", url); xhr.timeout = 15000;
        xhr.onload = function () { resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, json: function () { return Promise.resolve(JSON.parse(xhr.responseText)); } }); };
        xhr.onerror = xhr.ontimeout = reject; xhr.send();
      });
    }
    fetchState()
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, d: d }; }); })
      .then(function (res) {
        var t1 = Date.now();
        var d = res.d;
        if (d.server_time_ms) {
          var rtt = t1 - t0;
          samples.push({ rtt: rtt, offset: d.server_time_ms + rtt / 2 - t1 });
          if (samples.length > 12) samples.shift();
          var best = samples[0];
          for (var i = 1; i < samples.length; i++) if (samples[i].rtt < best.rtt) best = samples[i];
          offset = best.offset;
        }
        if (res.status === 404) {
          message(d.unknown_display ? "Display is not registered" : "Stream is no longer available");
          clearStage(); state = null; updatePresentation(null); clockEl.style.display = "none";
          try { localStorage.removeItem(cacheKey); } catch (_) {}
        } else if (res.ok && validState(d)) {
          lastSuccess = Date.now();
          if (!cur || !cur.el._failedAt) message("");
          applyState(d, false);
        }
      })
      .catch(function () { /* server unreachable: keep playing what we have */ })
      .then(function () { clearTimeout(timeout); inFlight = false; pollTimer = setTimeout(poll, pollMs); });
  }

  // ---- rendering ---------------------------------------------------------
  var hlsLoading = null;
  function loadHls() {
    if (window.Hls) return Promise.resolve(window.Hls);
    if (!hlsLoading) hlsLoading = new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      var timer = setTimeout(function () { script.remove(); reject(new Error("Player support unavailable")); }, 15000);
      script.src = "/static/vendor/hls.min.js";
      script.onload = function () { clearTimeout(timer); window.Hls ? resolve(window.Hls) : reject(new Error("HLS unavailable")); };
      script.onerror = function () { clearTimeout(timer); script.remove(); reject(new Error("HLS unavailable")); };
      document.head.appendChild(script);
    }).catch(function (error) { hlsLoading = null; throw error; });
    return hlsLoading;
  }
  function resumeVideo(el) {
    if (el._released) return;
    var promise = el.play();
    if (promise && promise.catch) promise.catch(function () {
      if (el._released) return;
      el.muted = true;
      var retry = el.play(); if (retry && retry.catch) retry.catch(function () {});
    });
  }
  function failSource(el) {
    if (el._released || el._failedAt) return;
    el._failedAt = Date.now(); el.classList.remove("on");
    if (el._hls) { el._hls.destroy(); el._hls = null; }
    if (el.tagName === "VIDEO") { el.pause(); el.removeAttribute("src"); el.load(); }
    if (cur && cur.el === el) message("Source unavailable · reconnecting");
  }
  function displayedURL(item) {
    if (item.kind === 'image' && /\.gif$/.test(item.url) && overlay && overlay.reduced()) {
      if (typeof item.thumb === 'string' && /^\/media\/[0-9]{1,12}\/(?:[0-9a-f]{32}\/)?[0-9]{3}_t\.jpg$/.test(item.thumb)) return item.thumb;
    }
    return item.url;
  }
  function makeEl(item) {
    var el, external = !/^(image|video)$/.test(item.kind);
    if (/^(video|remote_video|hls)$/.test(item.kind)) {
      el = document.createElement("video");
      el.muted = !withSound; el.playsInline = true;
      el.setAttribute("playsinline", ""); el.setAttribute("muted", "");
      el.preload = "auto"; el.loop = item.kind === "remote_video";
      if (external) {
        el._lastProgress = Date.now();
        el.addEventListener("timeupdate", function () {
          if (el._time !== el.currentTime) { el._lastProgress = Date.now(); el._time = el.currentTime; }
        });
        el.addEventListener("canplay", function () { clearTimeout(el._loadTimer); resumeVideo(el); });
      }
      if (item.kind === "hls" && !el.canPlayType("application/vnd.apple.mpegurl")) {
        // No CDN requests at runtime. Docker installs the checksum-pinned local build.
        loadHls().then(function (Hls) {
          if (el._released || el._failedAt) return;
          if (!Hls.isSupported()) { failSource(el); return; }
          var allowed = (state.media_origins || []).slice();
          var engine = new Hls({ enableWorker: true, maxBufferLength: 20, maxMaxBufferLength: 40,
            backBufferLength: 10, liveSyncDurationCount: 3, liveMaxLatencyDurationCount: 6,
            maxBufferSize: 30000000,
            xhrSetup: function (xhr, url) {
              // Enforce policy on child playlists, segments and key requests, not just the first URL.
              if (!approvedURL(url, allowed)) throw new Error("Unapproved media origin");
              xhr.withCredentials = false;
            }
          });
          el._hls = engine;
          engine.on(Hls.Events.ERROR, function (_, data) { if (data.fatal) failSource(el); });
          engine.on(Hls.Events.MANIFEST_PARSED, function () { resumeVideo(el); });
          engine.loadSource(item.url); engine.attachMedia(el);
        }).catch(function () { failSource(el); });
      } else el.src = item.url;
    } else if (item.kind === "web") {
      el = document.createElement("iframe");
      el.title = item.title || "Display content";
      el.setAttribute("sandbox", "allow-scripts");
      el.setAttribute("referrerpolicy", "no-referrer");
      el.setAttribute("tabindex", "-1"); el.src = item.url;
    } else {
      el = document.createElement("img"); el.decoding = "async";
      el.referrerPolicy = "no-referrer"; el.src = displayedURL(item);
    }
    if (external) {
      el._loadTimer = setTimeout(function () { failSource(el); }, 30000);
      el.addEventListener("load", function () { clearTimeout(el._loadTimer); });
      el.addEventListener("error", function () { failSource(el); });
    }
    el._displayURL = displayedURL(item);
    el.setAttribute("data-slide", item.slide_id);
    return el;
  }

  function switchMedia(el, prev, item) {
    var ms = Math.min(fadeMs, Math.floor(item.duration_ms / 2));
    if (overlay && overlay.reduced()) ms = 0;
    el.style.transition = 'none';
    el.classList.add('on');
    if (prev) { prev.el.style.transition = 'none'; prev.el.style.zIndex = '0'; }
    el.style.zIndex = '1';
    if (ms && el.animate) {
      var frames = [{opacity:0},{opacity:1}];
      if (transitionEffect === 'slide-left') frames = [{transform:'translateX(100%)'},{transform:'translateX(0)'}];
      if (transitionEffect === 'slide-up') frames = [{transform:'translateY(100%)'},{transform:'translateY(0)'}];
      el._transition = el.animate(frames, {duration:ms, easing:transitionEffect==='fade'?'linear':'ease-out'});
    }
    if (prev) {
      // Keep the outgoing frame under the incoming frame until completion.
      // No black-frame fade-through, and only one outgoing element survives.
      if (prev.el.tagName === 'VIDEO') { try { prev.el.pause(); } catch (_) {} }
      setTimeout(function () { release(prev.el); }, ms + 30);
    }
  }

  function clearStage() {
    clearPrepared();
    while (stage.firstChild) release(stage.firstChild);
    cur = null;
  }

  function locate(now) {
    var pos = ((now - state.anchor_ms) % total + total) % total;
    var cycle = Math.floor((now - state.anchor_ms) / total);
    var lo = 0, hi = starts.length - 1;
    while (lo < hi) {  // binary search for the item containing pos
      var mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= pos) lo = mid; else hi = mid - 1;
    }
    return { idx: lo, into: pos - starts[lo], cycle: cycle };
  }

  function keyFor(cycle, idx) { return state.version + ":" + cycle + ":" + idx; }

  function playVideo(el, seconds) {
    try { if (Math.abs(el.currentTime - seconds) > 0.05) el.currentTime = seconds; } catch (e) { /* not seekable yet */ }
    var pr = el.play();
    if (pr && pr.catch) pr.catch(function () {
      // Autoplay with sound blocked: fall back to muted.
      el.muted = true; el.play().catch(function () {});
    });
  }

  // ---- built-in screensaver clock (used when the screensaver stream has no content) ----
  function renderClock() {
    if (Date.now() - lastClock < 1000) return;
    lastClock = Date.now();
    var d = new Date(serverNow());
    var appearance = overlay && overlay.settings();
    var zone = appearance && appearance.screensaver.timezone;
    var h = d.getHours(), mi = d.getMinutes();
    var time;
    if (clock24) time = (h < 10 ? "0" : "") + h + ":" + (mi < 10 ? "0" : "") + mi;
    else time = ((h % 12) || 12) + ":" + (mi < 10 ? "0" : "") + mi + (h < 12 ? " AM" : " PM");
    var dateOptions = { weekday: "long", day: "numeric", month: "long", year: "numeric" };
    if (zone) dateOptions.timeZone = zone;
    var date;
    try {
      if (zone) time = new Intl.DateTimeFormat('en-US',{timeZone:zone,hour:'2-digit',minute:'2-digit',hour12:!clock24}).format(d);
      date = d.toLocaleDateString('en-US',dateOptions);
    } catch (_) { time=d.toISOString().slice(11,16)+' UTC';date=d.toISOString().slice(0,10); }
    clockEl.firstChild.textContent = time;
    clockEl.lastChild.textContent = date;
    if (clockEl.style.display !== "block") { clockEl.style.display = "block"; clockMoved = 0; }
    clockEl.style.textAlign = appearance && appearance.screensaver.asset_url ? 'center' : 'left';
    if (appearance && appearance.screensaver.asset_url && state && state.mode === 'clock') {
      clockEl.style.left = Math.max(20, (window.innerWidth - clockEl.offsetWidth) / 2) + 'px';
      clockEl.style.top = (window.innerHeight * .62) + 'px';
      return;
    }
    // Small periodic position changes reduce static dwell; they do not guarantee burn-in prevention.
    if (Date.now() - clockMoved > 60000) {
      clockMoved = Date.now();
      var maxX = Math.max(0, window.innerWidth - clockEl.offsetWidth - 40);
      var topSafe = appearance && appearance.top.enabled ? appearance.top.height_pct / 100 * window.innerHeight : 0;
      var bottomSafe = appearance && appearance.bottom.enabled ? appearance.bottom.height_pct / 100 * window.innerHeight : 0;
      var maxY = Math.max(0, window.innerHeight - clockEl.offsetHeight - 40 - topSafe - bottomSafe);
      clockEl.style.left = (20 + Math.random() * maxX) + "px";
      clockEl.style.top = (20 + topSafe + Math.random() * maxY) + "px";
    }
  }

  function tick(force) {
    // Do not show a cached override beyond its known schedule boundary offline.
    // Rotation caches are best effort; a manifest is not an offline media store.
    if (state && state.mode === "override" && state.next_change_ms && serverNow() > state.next_change_ms + 15000 && Date.now() - lastSuccess > 15000) {
      clearStage(); state = null; updatePresentation(null); clockEl.style.display = "none"; return;
    }
    if (state && (state.mode === "clock" || (cur && cur.el._failedAt))) renderClock();
    else if (clockEl.style.display === "block") clockEl.style.display = "none";
    if (!state || !state.items.length || !total) {
      if (cur) {
        var old = cur.el; old.classList.remove("on");
        setTimeout(function () { release(old); }, fadeMs + 50);
        cur = null;
      }
      return;
    }
    var now = serverNow();
    var loc = locate(now);
    var item = state.items[loc.idx];
    var key = keyFor(loc.cycle, loc.idx);

    if (cur && cur.key === key && cur.el._failedAt && Date.now() - cur.el._failedAt > 20000) {
      release(cur.el); cur = null; message("");
    }
    if (cur && /^(remote_video|hls)$/.test(cur.kind) && !cur.el._failedAt && Date.now() - cur.el._lastProgress > 30000) failSource(cur.el);
    if (!cur || cur.key !== key || force || cur.el._displayURL !== displayedURL(item)) {
      if (cur && cur.slideId === item.slide_id && cur.url === item.url && cur.el._displayURL === displayedURL(item) && cur.kind === item.kind && /^(image|remote_image|web|hls|remote_video)$/.test(cur.kind)) {
        cur.key = key;                    // same still image (e.g. single-item loop): nothing to change
      } else if (cur && cur.slideId === item.slide_id && cur.url === item.url && cur.kind === item.kind && cur.kind === "video") {
        cur.key = key;                    // same video looping: rewind in place
        playVideo(cur.el, loc.into / 1000);
      } else {
        if (prepared[key] && prepared[key]._displayURL !== displayedURL(item)) { release(prepared[key]); delete prepared[key]; }
        var el = prepared[key] || makeEl(item);
        delete prepared[key];
        if (!el.parentNode) stage.appendChild(el);
        if (item.kind === "video") playVideo(el, loc.into / 1000);
        else if (/^(hls|remote_video)$/.test(item.kind)) resumeVideo(el);
        var prev = cur;
        cur = { key: key, slideId: item.slide_id, url: item.url, el: el, kind: item.kind };
        switchMedia(el, prev, item);
      }
      prepareNext(loc);
    } else if (cur.kind === "video") {
      // keep synced videos from drifting apart
      var expected = loc.into / 1000;
      var el2 = cur.el;
      if (el2.readyState >= 2 && Date.now() - lastSeek > 2000 && Math.abs(el2.currentTime - expected) > 0.35) {
        lastSeek = Date.now();
        playVideo(el2, expected);
      } else if (el2.paused && el2.readyState >= 2) {
        playVideo(el2, expected);
      }
    }
  }

  function prepareNext(loc) {
    var n = state.items.length;
    var nextIdx = (loc.idx + 1) % n;
    var nextCycle = nextIdx === 0 ? loc.cycle + 1 : loc.cycle;
    var nextKey = keyFor(nextCycle, nextIdx);
    // drop stale preloads
    for (var k in prepared) {
      if (k !== nextKey && prepared.hasOwnProperty(k)) {
        var old = prepared[k];
        release(old);
        delete prepared[k];
      }
    }
    var item = state.items[nextIdx];
    // Do not open an extra live connection or run a hidden website ahead of its slot.
    if (!/^(image|video)$/.test(item.kind)) return;
    if (prepared[nextKey] || (cur && cur.slideId === item.slide_id && cur.url === item.url)) return;
    var el = makeEl(item);
    if (item.kind === "video") {
      // Park the video in the (invisible) stage so it buffers the opening.
      stage.appendChild(el);
      try { el.currentTime = 0; } catch (e) {}
    }
    prepared[nextKey] = el;
  }

  // ---- housekeeping ------------------------------------------------------
  function nightlyReload() {
    // Signage runs for weeks; a daily reload at 03:00 clears any browser leaks.
    var now = new Date();
    var next = new Date(now); next.setHours(3, 0, 0, 0);
    if (next <= now) next.setDate(next.getDate() + 1);
    setTimeout(function () {
      fetch("/healthz", { cache: "no-store" })
        .then(function (r) { if (r.ok) location.reload(); else nightlyReload(); })
        .catch(nightlyReload);
    }, next - now);
  }

  if (!endpoint) { message("Unknown player URL"); return; }

  // Restore the last manifest. Playback still requires reachable or browser-cached media.
  try {
    var cached = localStorage.getItem(cacheKey);
    if (cached && cached.length < 4 * 1024 * 1024) applyState(JSON.parse(cached), true);
  } catch (e) { /* ignore */ }

  poll();
  setInterval(function () { tick(false); }, 100);
  nightlyReload();
})();
