"use strict";
/* Signage admin UI — plain JS, no build step. */

let S = null;            // latest /api/overview payload
let view = "streams";
let dragging = false;

import { $, h, toast, modal, icon, applyTheme } from "/static/js/ui.js";
import { api, request } from "/static/js/api.js";
import { presentationEditor } from "/static/js/presentation-editor.js";

async function act(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg);
    await refresh(true);
    return r;
  } catch (e) {
    toast(e.message, true);
    return null;
  }
}

const pad = n => String(n).padStart(2, "0");
function fmtDate(ms) {
  if (ms === null || ms === undefined) return "";
  const d = new Date(ms), now = new Date();
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.toDateString() === now.toDateString()) return `today ${time}`;
  const tmr = new Date(now); tmr.setDate(now.getDate() + 1);
  if (d.toDateString() === tmr.toDateString()) return `tomorrow ${time}`;
  const day = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  return `${day} ${time}`;
}
function fmtAgo(ms) {
  if (!ms) return "never";
  const s = Math.round((Date.now() - ms) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return fmtDate(ms);
}
function fmtDur(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}
function toLocalInput(ms) {
  if (ms === null || ms === undefined) return "";
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fromLocalInput(v) { return v ? new Date(v).getTime() : null; }

function streamColor(id) {
  const st = S.streams.find(s => s.id === id);
  if (!st) return "var(--line)";
  if (st.kind === "screensaver") return "var(--ss)";
  const i = S.streams.filter(s => s.kind !== "screensaver").indexOf(st);
  return `var(--s${(i % 6) + 1})`;
}
function streamName(id) {
  const s = S.streams.find(s => s.id === id);
  return s ? s.name : "—";
}
function uploadById(id) { return S.uploads.find(u => u.id === id); }

function isLive(p, now) {
  return p.enabled && (p.start_at === null || p.start_at <= now) && (p.end_at === null || now < p.end_at);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }, text);
    document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("Copied");
}

// ---------------------------------------------------------------------------
// modals
// ---------------------------------------------------------------------------
function confirmBox(title, text, okLabel = "Delete") {
  return new Promise(resolve => {
    modal({
      title, body: h("div", null, text),
      onClose: () => resolve(false),
      actions: [
        c => h("button", { class: "btn", onclick: () => c() }, "Cancel"),
        c => h("button", { class: "btn danger", onclick: () => { resolve(true); c(); } }, okLabel),
      ],
    });
  });
}

function promptBox(title, label, value = "", opts = {}) {
  return new Promise(resolve => {
    const input = h("input", { type: opts.type || "text", value, maxlength: opts.max || 120 });
    let done = false;
    const m = modal({
      title,
      body: h("div", { class: "field" }, h("label", null, label), input),
      onClose: () => { if (!done) resolve(null); },
      actions: [
        c => h("button", { class: "btn", onclick: () => c() }, "Cancel"),
        c => h("button", { class: "btn primary", onclick: () => { done = true; resolve(input.value); c(); } }, "Save"),
      ],
    });
    input.addEventListener("keydown", e => { if (e.key === "Enter") { done = true; resolve(input.value); m.close(); } });
  });
}

// ---------------------------------------------------------------------------
// place dialog: put an upload on streams (rotation or override)
// ---------------------------------------------------------------------------
function placeDialog(upload, preset = {}) {
  let mode = preset.mode || "rotation";
  let all = !!preset.all;
  const chosen = new Set(preset.streamIds || []);
  const seg = h("div", { class: "seg" });
  const modeBtn = (m, label) => h("button", { type: "button", class: mode === m ? "on" : "", onclick: () => { mode = m; sync(); } }, label);

  const checks = h("div", { class: "checks" });
  const secs = h("input", { type: "number", min: 2, max: 3600, value: S.default_slide_seconds, style: { width: "90px" } });
  const start = h("input", { type: "datetime-local", value: toLocalInput(preset.start || null) });
  const end = h("input", { type: "datetime-local" });
  const err = h("div", { class: "err-text" });

  const hasStills = upload.kind !== "video";
  const secsField = h("div", { class: "field" },
    h("label", null, upload.kind === "deck" ? "Seconds per slide" : "Seconds on screen"), secs);

  const quickEnd = (label, fn) => h("button", {
    type: "button", class: "btn small",
    onclick: () => { const base = fromLocalInput(start.value) || Date.now(); end.value = toLocalInput(fn(base)); },
  }, label);
  const eod = b => { const d = new Date(b); d.setHours(23, 59, 0, 0); return d.getTime(); };
  const quick = h("div", { class: "quick" },
    quickEnd("+1 hour", b => b + 3600e3),
    quickEnd("+4 hours", b => b + 4 * 3600e3),
    quickEnd("End of day", eod),
    quickEnd("+1 day", b => b + 86400e3),
    quickEnd("+1 week", b => b + 7 * 86400e3),
    h("button", { type: "button", class: "btn small", onclick: () => { end.value = ""; } }, "Clear"));

  const endLabel = h("label", null, "End");
  const startLabel = h("label", null, "Start");

  function sync() {
    seg.replaceChildren(modeBtn("rotation", "Rotation"), modeBtn("override", "Override"));
    checks.replaceChildren(
      h("label", { class: all ? "on" : "" },
        h("input", { type: "checkbox", checked: all, onchange: () => { all = !all; sync(); } }), "All streams"),
      ...S.streams.map(s => h("label", {
        class: !all && chosen.has(s.id) ? "on" : "", style: { "--sc": streamColor(s.id) },
      },
        h("input", {
          type: "checkbox", checked: chosen.has(s.id),
          onchange: () => { all = false; chosen.has(s.id) ? chosen.delete(s.id) : chosen.add(s.id); sync(); },
        }), s.name)));
    endLabel.textContent = mode === "override" ? "End (required)" : "End (optional)";
    startLabel.textContent = mode === "override" ? "Start (blank = now)" : "Start (optional)";
  }
  sync();

  const body = h("div", { style: { display: "grid", gap: "14px" } },
    h("div", { class: "strip" }, (upload.slides.length ? upload.slides : [null]).slice(0, 24).map(s =>
      s ? h("img", { src: s.thumb, alt: "" }) : h("div", { class: "m" }, "Converting…"))),
    h("div", { class: "field" }, h("label", null, "Mode",
      h("span", { class: "i", "data-tip": "Rotation adds it to the stream's loop. Override replaces the stream's loop until the end time, then the loop resumes where it would have been." }, "i")), seg),
    h("div", { class: "field" }, h("label", null, "Streams"), checks),
    hasStills ? secsField : null,
    upload.kind === "feed" ? h("p", { class: "note" }, "Seconds on screen controls its slot in a mixed rotation. A live feed placed alone keeps playing at the live edge; it is not frame-synchronized with other displays.") : null,
    h("div", { class: "row" },
      h("div", { class: "field" }, startLabel, start),
      h("div", { class: "field" }, endLabel, end)),
    quick, err);

  modal({
    title: `Place “${upload.title}”`, body,
    actions: [
      c => h("button", { class: "btn", onclick: () => c() }, "Cancel"),
      c => h("button", {
        class: "btn primary",
        onclick: async () => {
          err.textContent = "";
          const payload = {
            upload_id: upload.id, mode, all_streams: all, stream_ids: [...chosen],
            slide_seconds: parseInt(secs.value, 10) || S.default_slide_seconds,
            start_at: fromLocalInput(start.value), end_at: fromLocalInput(end.value),
          };
          if (!all && !chosen.size) { err.textContent = "Pick at least one stream."; return; }
          if (mode === "override" && !payload.end_at) { err.textContent = "Overrides need an end time."; return; }
          try {
            await api("POST", "/api/placements", payload);
            c(); toast(mode === "override" ? "Override scheduled" : "Added to rotation");
            refresh(true);
          } catch (e) { err.textContent = e.message; }
        },
      }, "Place"),
    ],
  });
}

function editPlacementDialog(p) {
  const up = uploadById(p.upload_id);
  const secs = h("input", { type: "number", min: 2, max: 3600, value: p.slide_seconds, style: { width: "90px" } });
  const start = h("input", { type: "datetime-local", value: toLocalInput(p.start_at) });
  const end = h("input", { type: "datetime-local", value: toLocalInput(p.end_at) });
  const enabled = h("input", { type: "checkbox", checked: !!p.enabled });
  const err = h("div", { class: "err-text" });
  const where = p.stream_id === null ? "All streams" : streamName(p.stream_id);

  modal({
    title: `${p.mode === "override" ? "Override" : "Rotation item"} · ${up ? up.title : ""}`,
    body: h("div", { style: { display: "grid", gap: "14px" } },
      h("div", { class: "m" }, where),
      up && up.kind !== "video" ? h("div", { class: "field" }, h("label", null, "Seconds per slide"), secs) : null,
      h("div", { class: "row" },
        h("div", { class: "field" }, h("label", null, "Start"), start),
        h("div", { class: "field" }, h("label", null, "End"), end)),
      p.mode === "rotation" ? h("label", { class: "row" }, enabled, "Enabled") : null,
      err),
    actions: [
      c => h("button", {
        class: "btn danger left",
        onclick: async () => { c(); if (await confirmBox("Remove", `Remove “${up ? up.title : "item"}” from ${where}?`, "Remove")) act(() => api("DELETE", `/api/placements/${p.id}`), "Removed"); },
      }, "Remove"),
      c => h("button", { class: "btn", onclick: () => c() }, "Cancel"),
      c => h("button", {
        class: "btn primary",
        onclick: async () => {
          const s = fromLocalInput(start.value), e = fromLocalInput(end.value);
          const body = {
            slide_seconds: parseInt(secs.value, 10) || p.slide_seconds,
            enabled: enabled.checked,
          };
          if (s) body.start_at = s; else body.clear_start = true;
          if (e) body.end_at = e; else body.clear_end = true;
          try { await api("PATCH", `/api/placements/${p.id}`, body); c(); refresh(true); }
          catch (x) { err.textContent = x.message; }
        },
      }, "Save"),
    ],
  });
}

function pickUploadDialog(title, onPick) {
  const ready = S.uploads.filter(u => u.status !== "error");
  const body = ready.length
    ? h("div", { class: "pick" }, ready.map(u => h("button", {
      type: "button", onclick: () => { m.close(); onPick(u); },
    }, u.slides[0] ? h("img", { src: u.slides[0].thumb, alt: "" }) : h("img", { alt: "" }), h("span", null, u.title))))
    : h("div", { class: "none" }, "Library is empty.");
  const m = modal({ title, body, wide: true, actions: [c => h("button", { class: "btn", onclick: () => c() }, "Close")] });
}

// ---------------------------------------------------------------------------
// Streams view
// ---------------------------------------------------------------------------
function displayChip(d) {
  return h("button", {
    onclick: () => assignDisplay(d), "aria-label": `${d.name}: ${d.online ? "online" : "offline"}. Change stream`,
    class: "chip" + (d.online ? "" : " off"), draggable: "true", "data-display": d.id,
    title: d.online ? "Online" : `Offline · last seen ${fmtAgo(d.last_seen)}`,
  }, h("span", { class: "led" }), d.name);
}

function nowPreview(s) {
  if (s.mode === "clock") {
    return h("div", { class: "clockprev" }, h("div", { class: "t" }, clockText(new Date(S.server_time_ms))), h("div", { class: "d" },
      new Date(S.server_time_ms).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })));
  }
  if (s.now_thumb) return h("img", { src: s.now_thumb, alt: "" });
  return h("div", { class: "empty" }, s.mode === "off" ? "Off" : "Nothing scheduled");
}

function clockText(d) {
  const hh = d.getHours(), mm = pad(d.getMinutes());
  return S.clock_24h === false ? `${(hh % 12) || 12}:${mm}` : `${pad(hh)}:${mm}`;
}

function renderStreams() {
  const now = S.server_time_ms;

  // "Off" tray: displays with no stream show black.
  const off = S.displays.filter(d => d.stream_id === null);
  $("#tray").replaceChildren(
    h("span", { class: "tray-label" }, "Unassigned"),
    h("span", { class: "i", "data-tip": "Drag a display onto a stream to assign it. Displays dropped here show a black screen." }, "i"),
    h("div", { class: "chips", "data-drop": "" }, off.map(displayChip)), h("span", { class: "tray-help" }, "Drop a display here to turn its content off"));

  $("#streams").replaceChildren(...S.streams.map(s => {
    const isSS = s.kind === "screensaver";
    const sc = streamColor(s.id);
    const displays = S.displays.filter(d => d.stream_id === s.id);
    const rot = S.placements.filter(p => p.stream_id === s.id && p.mode === "rotation");
    const ovs = S.placements.filter(p => p.mode === "override" && (p.stream_id === s.id || p.stream_id === null) && p.end_at > now)
      .sort((a, b) => (a.start_at || a.created_at) - (b.start_at || b.created_at));

    let tag = null;
    if (s.mode === "override") tag = h("span", { class: "tag override" }, "OVERRIDE");
    else if (s.source === "screensaver" && !isSS) tag = h("span", { class: "tag ss" }, "SCREENSAVER");
    else if (s.mode === "clock") tag = h("span", { class: "tag ss" }, "CLOCK");
    else if (s.mode === "rotation") tag = h("span", { class: "tag" }, "ROTATION");

    const list = h("ul", { class: "plist", "data-stream": s.id });
    for (const p of rot) list.append(rotationRow(p, now));

    const fallback = isSS ? null : h("div", { class: "fallback" },
      h("span", null, "If empty"),
      h("div", { class: "seg small" },
        ["screensaver", "blank"].map(f => h("button", {
          type: "button", class: s.fallback === f ? "on" : "",
          onclick: () => { if (s.fallback !== f) act(() => api("PATCH", `/api/streams/${s.id}`, { fallback: f })); },
        }, f === "screensaver" ? "Screensaver" : "Blank"))));

    return h("div", { class: "stream" + (isSS ? " is-ss" : ""), style: { "--sc": sc }, "data-drop": s.id },
      h("div", { class: "stream-head" },
        h("h2", null, h("button", { class: "stream-title", "aria-label": `Rename ${s.name}`, onclick: () => renameStream(s) }, s.name)),
        isSS ? h("span", { class: "i", "data-tip": "Shown on displays assigned here, and on any stream set to fall back to the screensaver when it has nothing scheduled. With no content it shows a clock." }, "i") : null,
        h("div", { class: "tools" },
          h("a", { class: "link", href: (S.player_base_url || location.origin) + (s.player_path || `/preview/${s.id}`), target: "_blank", rel: "noopener" }, "Preview"),
          h("button", { class: "link", onclick: () => copyText((S.player_base_url || location.origin) + s.player_path) }, "Copy URL"),
          isSS ? null : h("button", { class: "link danger", onclick: () => deleteStream(s) }, "Delete"))),
      h("div", { class: "chips" }, displays.length ? displays.map(displayChip) : h("span", { class: "m" }, "No displays assigned")),
      h("div", { class: "now" },
        nowPreview(s), tag,
        s.loop_ms ? h("span", { class: "loop" }, `${s.item_count} item${s.item_count === 1 ? "" : "s"} · ${fmtDur(s.loop_ms)} loop`) : null),
      fallback,
      h('div', {class:'stream-presentation'},h('button',{class:'link',onclick:()=>presentationEditor(s,S,()=>refresh(true))},'Banners & motion'),h('span',null,'Stream overlay')),

      ovs.length ? h("div", null,
        h("div", { class: "sub" }, "Overrides"),
        h("ul", { class: "plist" }, ovs.map(p => overrideRow(p, now)))) : null,

      h("div", { class: "sub" }, "Rotation", h("span", { class: "spacer" }),
        h("button", { class: "link", onclick: () => pickUploadDialog(`Add to ${s.name}`, u => placeDialog(u, { streamIds: [s.id], mode: "rotation" })) }, "Add"),
        h("button", { class: "link", onclick: () => pickUploadDialog(`Override ${s.name}`, u => placeDialog(u, { streamIds: [s.id], mode: "override" })) }, "Override")),
      rot.length ? list : h("div", { class: "none" }, isSS ? "The clock plays when this rotation is empty." : "Add content to start this rotation."));
  }));
  wireDrag($("#streams"));
  wireDisplayDrag();
}

// Drag display chips between streams (and the Off tray).
function wireDisplayDrag() {
  const board = $("#view-streams");
  board.querySelectorAll("[data-display]").forEach(chip => {
    chip.addEventListener("dragstart", e => {
      dragging = true;
      chip.classList.add("lifted");
      board.classList.add("moving-display");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("application/x-display", chip.dataset.display);
    });
    chip.addEventListener("dragend", () => {
      dragging = false;
      chip.classList.remove("lifted");
      board.classList.remove("moving-display");
      board.querySelectorAll(".drop-hot").forEach(x => x.classList.remove("drop-hot"));
    });
  });
}

// Drop targets are wired once (delegated), since the tray element persists across renders.
function wireDisplayDrop() {
  const board = $("#view-streams");
  const target = e => e.target.closest(".stream[data-drop], #tray");
  const isDisplay = e => e.dataTransfer && e.dataTransfer.types.includes("application/x-display");
  board.addEventListener("dragover", e => {
    const t = target(e);
    if (!t || !isDisplay(e)) return;
    e.preventDefault();
    board.querySelectorAll(".drop-hot").forEach(x => { if (x !== t) x.classList.remove("drop-hot"); });
    t.classList.add("drop-hot");
  });
  board.addEventListener("dragleave", e => {
    const t = target(e);
    if (t && !t.contains(e.relatedTarget)) t.classList.remove("drop-hot");
  });
  board.addEventListener("drop", e => {
    const t = target(e);
    if (!t || !isDisplay(e)) return;
    e.preventDefault();
    t.classList.remove("drop-hot");
    board.classList.remove("moving-display");
    dragging = false;
    const id = parseInt(e.dataTransfer.getData("application/x-display"), 10);
    const sid = t.id === "tray" ? null : parseInt(t.dataset.drop, 10);
    const d = S.displays.find(x => x.id === id);
    if (!d || d.stream_id === sid) return;
    act(() => api("PATCH", `/api/displays/${d.id}`, sid === null ? { clear_stream: true } : { stream_id: sid }),
      `${d.name} → ${sid === null ? "Off" : streamName(sid)}`);
  });
}

function schedText(p, now) {
  if (!p.enabled) return "Disabled";
  if (p.start_at && p.start_at > now) return `Starts ${fmtDate(p.start_at)}` + (p.end_at ? ` · ends ${fmtDate(p.end_at)}` : "");
  if (p.end_at && p.end_at <= now) return `Ended ${fmtDate(p.end_at)}`;
  if (p.end_at) return `Until ${fmtDate(p.end_at)}`;
  return "";
}

function itemMeta(up, p) {
  if (!up) return "";
  if (up.status === "processing") return "Converting…";
  if (up.status === "error") return "Conversion failed";
  if (up.kind === "video") return `Video · ${fmtDur(up.slides[0] ? up.slides[0].duration_ms : 0)}`;
  if (up.kind === "deck") return `${up.slide_count} slides × ${p.slide_seconds}s`;
  return `${p.slide_seconds}s`;
}

function rotationRow(p, now) {
  const up = uploadById(p.upload_id);
  const sched = schedText(p, now);
  const li = h("li", { class: "pitem" + (isLive(p, now) && up && up.status === "ready" ? "" : " inactive"), draggable: "true", "data-id": p.id },
    h("span", { class: "grip", title: "Drag to reorder" }, "⋮⋮"),
    up && up.slides[0] ? h("img", { src: up.slides[0].thumb, alt: "" }) : h("img", { alt: "" }),
    h("div", { style: { minWidth: 0 } },
      h("div", { class: "t" }, up ? up.title : "?"),
      h("div", { class: "m" }, itemMeta(up, p), sched ? " · " : "", sched ? h("span", { class: "sched" }, sched) : null)),
    h("div", { class: "acts" },
      h("div", { class: "order-controls" }, [-1, 1].map(direction => {
        const order = S.placements.filter(x => x.stream_id === p.stream_id && x.mode === "rotation");
        const index = order.findIndex(x => x.id === p.id);
        return h("button", { "aria-label": `Move ${up?.title || "item"} ${direction < 0 ? "up" : "down"}`, disabled: index + direction < 0 || index + direction >= order.length,
          onclick: () => movePlacement(p, direction) }, direction < 0 ? "↑" : "↓");
      })), h("button", { class: "link", "aria-label": `Edit ${up?.title || "rotation item"}`, onclick: () => editPlacementDialog(p) }, "Edit")));
  return li;
}

function overrideRow(p, now) {
  const up = uploadById(p.upload_id);
  const live = isLive(p, now);
  const scope = p.stream_id === null ? "All streams · " : "";
  return h("li", { class: "pitem ov" + (live ? " live" : "") },
    up && up.slides[0] ? h("img", { src: up.slides[0].thumb, alt: "" }) : h("img", { alt: "" }),
    h("div", { style: { minWidth: 0 } },
      h("div", { class: "t" }, (live ? "● " : "") + (up ? up.title : "?")),
      h("div", { class: "m" }, scope, live ? `Live until ${fmtDate(p.end_at)}` : `${fmtDate(p.start_at)} → ${fmtDate(p.end_at)}`)),
    h("div", { class: "acts" },
      live ? h("button", { class: "link danger", onclick: () => act(() => api("PATCH", `/api/placements/${p.id}`, { end_at: Date.now() }), "Override ended") }, "End now") : null,
      h("button", { class: "link", onclick: () => editPlacementDialog(p) }, "Edit")));
}

function wireDrag(root) {
  let src = null;
  root.querySelectorAll(".plist[data-stream] .pitem").forEach(li => {
    li.addEventListener("dragstart", e => {
      src = li; dragging = true; li.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", li.dataset.id);
    });
    li.addEventListener("dragend", () => {
      dragging = false; src = null; li.classList.remove("dragging");
      root.querySelectorAll(".over").forEach(x => x.classList.remove("over"));
    });
    li.addEventListener("dragover", e => {
      if (!src || src.parentNode !== li.parentNode || src === li) return;
      e.stopPropagation();
      e.preventDefault(); li.classList.add("over");
    });
    li.addEventListener("dragleave", () => li.classList.remove("over"));
    li.addEventListener("drop", e => {
      e.preventDefault(); li.classList.remove("over");
      if (!src || src.parentNode !== li.parentNode || src === li) return;
      const list = li.parentNode;
      const items = [...list.children];
      if (items.indexOf(src) < items.indexOf(li)) li.after(src); else li.before(src);
      const ids = [...list.children].map(x => parseInt(x.dataset.id, 10));
      dragging = false;
      act(() => api("POST", `/api/streams/${list.dataset.stream}/order`, { placement_ids: ids }));
    });
  });
}

async function renameStream(s) {
  const name = await promptBox("Rename stream", "Name", s.name, { max: 60 });
  if (name && name.trim() && name.trim() !== s.name) act(() => api("PATCH", `/api/streams/${s.id}`, { name: name.trim() }));
}
async function deleteStream(s) {
  const n = S.displays.filter(d => d.stream_id === s.id).length;
  const ok = await confirmBox("Delete stream", `Delete ${s.name}? Its rotation and overrides are removed${n ? ` and ${n} display${n > 1 ? "s" : ""} will go blank until reassigned` : ""}. Library files are kept.`);
  if (ok) act(() => api("DELETE", `/api/streams/${s.id}`), "Stream deleted");
}

// ---------------------------------------------------------------------------
// Library view
// ---------------------------------------------------------------------------
function renderLibrary() {
  const q = $("#lib-filter").value.trim().toLowerCase();
  const kind = $("#lib-kind").value;
  const list = S.uploads.filter(u => (!kind || u.kind === kind) && (!q || u.title.toLowerCase().includes(q) || u.original_name.toLowerCase().includes(q)));
  const root = $("#library");
  if (!list.length) { root.replaceChildren(h("div", { class: "none", style: { gridColumn: "1 / -1" } }, q || kind ? "No content matches these filters." : "Your library is ready. Upload the first piece of content above.")); return; }

  root.replaceChildren(...list.map(u => {
    const usedOn = [...new Set(S.placements.filter(p => p.upload_id === u.id).map(p => p.stream_id))];
    let inner;
    if (u.status === "processing") inner = h("div", { class: "state" }, S.worker_alive ? "Converting…" : "Waiting for converter");
    else if (u.status === "error") inner = h("div", { class: "state err" }, u.error || "Conversion failed");
    else inner = u.slides[0] ? h("img", { src: u.slides[0].thumb, alt: "", loading: "lazy" }) : null;

    const badge = u.kind === "feed" ? ({ hls: "Live HLS", remote_video: "Video URL", remote_image: "Image URL", web: "Web page" }[u.feed_kind] || "External source") : u.kind === "deck" ? `${u.slide_count} slides` : u.kind === "video"
      ? `Video ${u.slides[0] ? fmtDur(u.slides[0].duration_ms) : ""}` : u.kind === "graphic" ? "Graphic · alpha / animation" : "Image";

    return h("div", { class: "card" },
      h("div", { class: "thumb" }, inner, u.status === "ready" ? h("span", { class: "badge" }, badge) : null),
      h("div", { class: "t", title: u.original_name }, u.title),
      h("div", { class: "m" }, `${u.created_by || ""} · ${fmtDate(u.created_at)}`),
      usedOn.length ? h("div", { class: "dots", title: usedOn.map(id => id === null ? "All streams" : streamName(id)).join(", ") },
        usedOn.map(id => h("span", { style: { "--sc": id === null ? "var(--warn)" : streamColor(id) } }))) : null,
      h("div", { class: "acts" },
        u.status !== "error" ? h("button", { class: "link", onclick: () => placeDialog(u) }, "Place") : null,
        u.status === "error" ? h("button", { class: "link", onclick: () => act(() => api("POST", `/api/uploads/${u.id}/retry`), "Retrying") }, "Retry") : null,
        u.kind === "feed" ? h("button", { class: "link", onclick: () => sourceDialog(u) }, "Edit source") : h("button", { class: "link", onclick: () => renameUpload(u) }, "Rename"),
        u.kind !== "feed" ? h("button", { class: "link", onclick: () => sharedFolders(u) }, "Export") : null,
        h("button", { class: "link danger", onclick: () => deleteUpload(u, usedOn.length) }, "Delete")));
  }));
}

// External addresses are server-approved sources, never arbitrary server fetches.
function sourceDialog(source = null) {
  const title = h("input", { maxlength: 120, value: source?.title || "", placeholder: "Office broadcast" });
  const url = h("input", { type: "url", maxlength: 2048, value: source?.source_url || "", placeholder: "https://video.example.org/office/index.m3u8" });
  const kind = h("select", null, ...Object.entries({ hls: "Live video · HLS", remote_video: "Video URL · MP4", remote_image: "Image URL", web: "Web page · sandboxed" }).map(([value, label]) => h("option", { value, selected: value === (source?.feed_kind || "hls") }, label)));
  const note = h("p", { class: "note" });
  const err = h("div", { class: "err-text" });
  function policy() {
    const origins = kind.value === "web" ? S.source_frame_origins : S.source_media_origins;
    note.textContent = origins?.length ? `Approved origins: ${origins.join(", ")}. Playback URLs are visible to viewers. Do not include passwords or private access tokens.` : "No origins approved yet. Your operator must add this source to the server allowlist first.";
  }
  kind.addEventListener("change", policy); policy();
  modal({ title: source ? "Edit source" : "Add URL or live feed", wide: true,
    body: h("div", { class: "source-form" },
      h("div", { class: "field" }, h("label", null, "Title"), title),
      h("div", { class: "field" }, h("label", null, "Source type"), kind),
      h("div", { class: "field" }, h("label", null, "Source URL"), url), note,
      h("p", { class: "note" }, "Use a direct media URL, not a video-service watch page. Web pages must permit embedding. RTSP cameras need a separate HLS relay."), err),
    actions: [close => h("button", { class: "btn", onclick: close }, "Cancel"),
      close => h("button", { class: "btn primary", onclick: async () => {
        if (!title.value.trim() || !url.value.trim()) { err.textContent = "Enter a title and a source URL."; return; }
        try {
          await api(source ? "PUT" : "POST", source ? `/api/feeds/${source.id}` : "/api/feeds", { title: title.value.trim(), url: url.value.trim(), kind: kind.value });
          close(); await refresh(true); toast(source ? "Source updated. Playback addresses unchanged." : "Source added. Place it on a stream from the library.");
        } catch (error) { err.textContent = error.message; }
      } }, source ? "Save source" : "Add source")]
  });
}

let transferBusy = false, transferSignature = "";
async function refreshTransfers() {
  if (transferBusy || document.hidden || !S) return;
  transferBusy = true;
  try {
    const jobs = await api("GET", "/api/transfers");
    const signature = JSON.stringify([jobs, S.uploads.filter(u => u.status === "ready").map(u => u.id)]);
    if (signature === transferSignature) return;
    transferSignature = signature;
    const root = $("#transfers"); root.hidden = !jobs.length;
    root.replaceChildren(h("h2", null, "Recent transfers"), ...jobs.slice(0, 10).map(job => {
      const content = job.result?.id ? uploadById(job.result.id) : null;
      const state = job.status === "completed" && job.direction === "import" ? "Imported · conversion queued or complete" : ({ queued: "Queued", running: "Transferring", error: "Needs attention", completed: "Exported" }[job.status] || job.status);
      return h("div", { class: "transfer-row" }, h("div", null,
        h("strong", null, job.direction === "import" ? job.relative_path.split("/").pop() : (job.output_name || "Export")),
        h("span", { class: job.error ? "err-text" : "m" }, job.error || state)),
        job.status === "error" ? h("button", { class: "link", onclick: async () => { await api("POST", `/api/transfers/${job.id}/retry`); transferSignature = ""; await refreshTransfers(); } }, "Retry") : null,
        content?.status === "ready" ? h("button", { class: "link", onclick: () => placeDialog(content) }, "Place") : null);
    }));
  } catch (error) {
    const root = $("#transfers"); root.hidden = false; transferSignature = "";
    root.replaceChildren(h("p", { class: "note" }, `Transfer status unavailable. ${error.message}`), h("button", { class: "link", onclick: refreshTransfers }, "Retry"));
  } finally { transferBusy = false; }
}

async function sharedFolders(exportUpload = null) {
  const folder = h("select", { "aria-label": "Shared folder" });
  const contents = h("div", { class: "share-list" });
  const breadcrumb = h("span", { class: "share-path" }, "/");
  const status = h("div", { class: "err-text" });
  let path = "", offset = 0, nextOffset = null, busy = false, open = true, configured = [];
  const exportButton = h("button", { class: "btn primary", disabled: true, onclick: async () => {
    if (busy || !folder.value) return;
    try {
      await api("POST", `/api/shares/${folder.value}/export`, { upload_id: exportUpload.id, path });
      toast("Export queued. The original file will be saved under a unique name."); dialog.close(); refreshTransfers();
    } catch (error) { status.textContent = error.message; }
  } }, "Export here");
  async function load(reset = true) {
    if (busy || !folder.value || !open) return;
    busy = true; if (reset) offset = 0;
    folder.disabled = true; exportButton.disabled = true; status.textContent = "";
    contents.replaceChildren(h("p", { class: "note" }, "Opening shared folder…"));
    breadcrumb.textContent = path ? "/" + path : "/";
    try {
      const data = await api("GET", `/api/shares/${folder.value}/browse?path=${encodeURIComponent(path)}&offset=${offset}`);
      if (!open) return;
      nextOffset = data.next_offset;
      contents.replaceChildren(...data.entries.map(entry => h("div", { class: "share-entry" },
        entry.directory ? h("button", { class: "link share-name", onclick: () => { path = [path, entry.name].filter(Boolean).join("/"); return load(); } }, "▸ " + entry.name) : h("span", { class: "share-name" }, entry.name),
        !entry.directory && !exportUpload ? h('div', {class:'row'},
          !/\.svg$/i.test(entry.name) ? h("button", { class: "btn small", onclick: async () => {
            await api("POST", `/api/shares/${folder.value}/import`, { path: [path, entry.name].filter(Boolean).join("/") });
            toast(`${entry.name} queued for import`); refreshTransfers();
          } }, "Import") : null,
          /\.(png|gif|svg)$/i.test(entry.name) ? h('button', {class:'btn small',onclick:async()=>{
            await api('POST', `/api/shares/${folder.value}/import`, {path:[path,entry.name].filter(Boolean).join('/'),as_graphic:true});
            toast(`${entry.name} queued as a graphic`);refreshTransfers();
          }},'As graphic') : null) : null)));
      if (!data.entries.length) contents.append(h("p", { class: "note" }, "No supported files or folders here."));
      if (offset || nextOffset !== null) contents.append(h("div", { class: "row" },
        h("button", { class: "btn small", disabled: !offset, onclick: () => { offset = Math.max(0, offset - 100); return load(false); } }, "Previous"),
        h("button", { class: "btn small", disabled: nextOffset === null, onclick: () => { offset = nextOffset; return load(false); } }, "Next")));
      exportButton.disabled = !exportUpload || data.read_only;
    } catch (error) {
      status.textContent = error.message;
      contents.replaceChildren(h("button", { class: "btn", onclick: () => load() }, "Retry connection"));
    } finally { busy = false; folder.disabled = false; }
  }
  folder.addEventListener("change", () => { path = ""; load(); });
  const dialog = modal({ title: exportUpload ? `Export “${exportUpload.title}”` : "Shared folders", wide: true,
    body: h("div", { class: "source-form" }, h("div", { class: "field" }, h("label", null, "Approved share"), folder),
      h("div", { class: "row" }, h("button", { class: "btn small", onclick: () => { if (busy) return; path = path.split("/").slice(0, -1).join("/"); return load(); } }, "Up"), breadcrumb),
      contents, status, h("p", { class: "note" }, exportUpload ? "Exports copy the original, never overwrite existing files, and are tracked across restarts." : "Import makes a library snapshot and runs the normal converter. Later changes on the NAS do not automatically publish to displays.")),
    onClose: () => { open = false; },
    actions: [close => h("button", { class: "btn", onclick: close }, "Close"), ...(exportUpload ? [() => exportButton] : [])]
  });
  try {
    configured = await api("GET", "/api/shares");
    if (!open) return;
    if (exportUpload) configured = configured.filter(share => !share.read_only);
    folder.replaceChildren(...configured.map(share => h("option", { value: share.id }, share.name + (share.read_only ? " · read only" : ""))));
    if (!configured.length) { status.textContent = "No approved " + (exportUpload ? "writable " : "") + "shared folders. Mount the NAS on the host and configure shares.json first."; return; }
    await load();
  } catch (error) { if (open) status.textContent = error.message; }
}

async function renameUpload(u) {
  const t = await promptBox("Rename", "Title", u.title);
  if (t && t.trim() && t.trim() !== u.title) act(() => api("PATCH", `/api/uploads/${u.id}`, { title: t.trim() }));
}
async function deleteUpload(u, used) {
  const ok = await confirmBox("Delete", `Delete “${u.title}”${used ? " and remove it from every stream" : ""}? This can't be undone.`);
  if (ok) act(() => api("DELETE", `/api/uploads/${u.id}`), "Deleted");
}

const uploadQueue = [];
let uploading = false;
function uploadFiles(files, graphic = false) {
  if (!S) return;
  for (const file of [...files]) {
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!(graphic ? [".png", ".svg", ".gif"] : S.accept).includes(ext)) { toast(`${file.name}: unsupported file type`, true); continue; }
    const maxMB = graphic ? (ext === ".svg" ? 2 : 32) : S.max_upload_mb;
    if (!file.size || file.size > maxMB * 1048576) { toast(`${file.name}: empty or exceeds ${maxMB} MB`, true); continue; }
    if (uploadQueue.length >= 50) { toast("Upload queue is full. Let it finish before adding more files.", true); break; }
    const progress = h("progress", { max: 100, value: 0, "aria-label": `Upload ${file.name}` });
    const status = h("span", { class: "mono" }, "Queued");
    const item = { file, graphic, progress, status, canceled: false, xhr: null };
    const cancel = h("button", { class: "link", "aria-label": `Cancel ${file.name}`, onclick: () => {
      item.canceled = true; if (item.xhr) item.xhr.abort(); item.row.remove();
    } }, "Cancel");
    item.row = h("div", { class: "q" }, h("span", null, file.name), progress, status, cancel);
    $("#upload-queue").append(item.row); uploadQueue.push(item);
  }
  drainUploads();
}
async function drainUploads() {
  if (uploading) return;
  uploading = true;
  try {
    while (uploadQueue.length) {
      const item = uploadQueue.shift(); if (item.canceled) continue;
      try {
        await new Promise((resolve, reject) => {
          const xhr = item.xhr = new XMLHttpRequest();
          xhr.open("POST", item.graphic ? "/api/graphics" : "/api/uploads"); xhr.setRequestHeader("X-Signage", "1");
          xhr.timeout = 30 * 60 * 1000;
          item.status.textContent = "Uploading";
          xhr.upload.onprogress = e => { if (e.lengthComputable) {
            item.progress.value = Math.round(e.loaded / e.total * 100);
            item.status.textContent = item.progress.value === 100 ? "Finishing…" : `${item.progress.value}%`;
          } };
          xhr.onload = () => {
            let data = {}; try { data = JSON.parse(xhr.responseText); } catch (_) {}
            if (xhr.status >= 200 && xhr.status < 300) resolve(data);
            else { if (xhr.status === 401) location.assign("/"); reject(new Error(typeof data.detail === "string" ? data.detail : `Upload failed (${xhr.status})`)); }
          };
          xhr.onerror = () => reject(new Error("Connection lost. The upload may need to be retried."));
          xhr.ontimeout = () => reject(new Error("Upload timed out. Check your connection."));
          xhr.onabort = () => reject(new Error("Upload canceled"));
          const form = new FormData(); form.append("file", item.file); form.append("title", item.file.name.replace(/\.[^.]+$/, "").slice(0, 120));
          xhr.send(form);
        });
        item.row.remove(); toast(`${item.file.name} uploaded. Conversion is queued.`);
        await refresh(true);
      } catch (error) {
        if (!item.canceled) {
          item.status.textContent = "Failed";
          item.row.replaceChildren(h("span", null, `${item.file.name}: ${error.message}`),
            h("button", { class: "link", onclick: () => { item.row.remove(); uploadFiles([item.file], item.graphic); } }, "Retry"),
            h("button", { class: "link", onclick: () => item.row.remove() }, "Dismiss"));
          toast(error.message, true);
        }
      } finally { item.xhr = null; }
    }
  } finally { uploading = false; }
}

// ---------------------------------------------------------------------------
// Displays view
// ---------------------------------------------------------------------------
function displaySelect(d) {
  return h("select", { "aria-label": `Stream for ${d.name}`, onchange: event => {
    const value = event.target.value;
    return act(() => api("PATCH", `/api/displays/${d.id}`, value ? { stream_id: Number(value) } : { clear_stream: true }), "Display assignment saved");
  } }, h("option", { value: "", selected: d.stream_id === null }, "Off · blank screen"),
    S.streams.map(s => h("option", { value: s.id, selected: s.id === d.stream_id }, s.name)));
}
function assignDisplay(d) {
  modal({ title: d.name, body: h("div", { class: "field" }, h("label", null, "Assigned stream"), displaySelect(d)),
    actions: [close => h("button", { class: "btn", onclick: () => { close(); render(true); } }, "Done")] });
}
async function movePlacement(p, direction) {
  const ids = S.placements.filter(x => x.stream_id === p.stream_id && x.mode === "rotation").map(x => x.id);
  const index = ids.indexOf(p.id), next = index + direction;
  if (next < 0 || next >= ids.length) return;
  [ids[index], ids[next]] = [ids[next], ids[index]];
  await act(() => api("POST", `/api/streams/${p.stream_id}/order`, { placement_ids: ids }), "Rotation order updated");
}
function renderDisplays() {
  const origin = S.player_base_url || location.origin;
  $("#displays").replaceChildren(...S.displays.map(d => {
    const url = `${origin}/display/${d.slug}`;
    const stream = S.streams.find(s => s.id === d.stream_id);
    return h("article", { class: "display-card" },
      h("div", { class: "display-head" }, h("h2", null, d.name),
        h("span", { class: "display-status" }, h("span", { class: "led" + (d.online ? "" : " offline") }), d.online ? "Online" : "Offline")),
      h("div", { class: "display-preview" }, d.now_thumb ? h("img", { src: d.now_thumb, alt: `Reported content on ${d.name}`, loading: "lazy" }) : !d.online ? "No current heartbeat" : stream?.mode === "clock" ? nowPreview(stream) : "No content reported"),
      h("div", { class: "display-body" }, h("div", { class: "field" }, h("label", { for: `assign-${d.id}` }, "ASSIGNED STREAM"), Object.assign(displaySelect(d), { id: `assign-${d.id}` })),
        h("div", { class: "display-meta" }, h("span", null, `Seen ${fmtAgo(d.last_seen)}`), h("span", { class: "mono" }, d.last_ip || "No connection yet")),
        h("div", { class: "display-url" }, h("a", { href: url, target: "_blank", rel: "noopener", title: url }, url), h("button", { class: "btn small", onclick: () => copyText(url) }, "Copy URL"))),
      h("div", { class: "display-foot" }, h("button", { class: "link", onclick: () => renameDisplay(d) }, "Rename"), h("button", { class: "link danger", onclick: () => deleteDisplay(d) }, "Remove")));
  }));
  if (!S.displays.length) $("#displays").append(h("div", { class: "none" }, "No displays yet. Add a display to get its player URL."));
}

async function renameDisplay(d) {
  const n = await promptBox("Rename display", "Name", d.name, { max: 60 });
  if (n && n.trim() && n.trim() !== d.name) act(() => api("PATCH", `/api/displays/${d.id}`, { name: n.trim() }));
}
async function deleteDisplay(d) {
  if (await confirmBox("Remove display", `Remove ${d.name}? Its player URL will stop showing content.`, "Remove"))
    act(() => api("DELETE", `/api/displays/${d.id}`), "Display removed");
}
function newDisplayDialog() {
  const name = h("input", { type: "text", maxlength: 60, placeholder: "Lobby 4" });
  const slug = h("input", { type: "text", maxlength: 40, placeholder: "lobby-4", class: "mono" });
  const stream = h("select", null, h("option", { value: "" }, "Off"), S.streams.map(s => h("option", { value: s.id }, s.name)));
  const err = h("div", { class: "err-text" });
  let slugTouched = false;
  slug.addEventListener("input", () => { slugTouched = true; });
  name.addEventListener("input", () => {
    if (!slugTouched) slug.value = name.value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  });
  modal({
    title: "Add display",
    body: h("div", { style: { display: "grid", gap: "14px" } },
      h("div", { class: "field" }, h("label", null, "Name"), name),
      h("div", { class: "field" }, h("label", null, "Display ID",
        h("span", { class: "i", "data-tip": "Becomes the player URL: /display/<id>" }, "i")), slug),
      h("div", { class: "field" }, h("label", null, "Stream"), stream), err),
    actions: [
      c => h("button", { class: "btn", onclick: () => c() }, "Cancel"),
      c => h("button", {
        class: "btn primary", onclick: async () => {
          try {
            await api("POST", "/api/displays", { name: name.value.trim() || slug.value, slug: slug.value.trim(), stream_id: stream.value ? parseInt(stream.value, 10) : null });
            c(); refresh(true); toast("Display added");
          } catch (e) { err.textContent = e.message; }
        },
      }, "Add"),
    ],
  });
}

// ---------------------------------------------------------------------------
// Activity + Users
// ---------------------------------------------------------------------------
async function renderActivity() {
  const rows = await api("GET", "/api/audit?limit=300");
  $("#activity").replaceChildren(
    h("thead", null, h("tr", null, h("th", null, "When"), h("th", null, "Who"), h("th", null, "What"))),
    h("tbody", null, rows.map(r => h("tr", null,
      h("td", { class: "mono", style: { whiteSpace: "nowrap" } }, new Date(r.at).toLocaleString("en-US")),
      h("td", null, r.who || ""), h("td", null, r.what)))));
}

async function renderUsers() {
  const rows = await api("GET", "/api/users");
  $("#users").replaceChildren(
    h("thead", null, h("tr", null, h("th", null, "Username"), h("th", null, "Created"), h("th", null, ""))),
    h("tbody", null, rows.map(u => h("tr", null,
      h("td", null, u.username + (u.username.toLowerCase() === S.me.username.toLowerCase() ? " (you)" : "")),
      h("td", null, new Date(u.created_at).toLocaleDateString("en-US")),
      h("td", { style: { textAlign: "right" } },
        h("button", { class: "link", onclick: () => resetPassword(u) }, "Set password"), "  ",
        h("button", { class: "link danger", onclick: () => deleteUser(u) }, "Delete"))))));
}

async function resetPassword(u) {
  const pw = await promptBox(`Set password · ${u.username}`, "New password (12+ characters)", "", { type: "password", max: 128 });
  if (!pw) return;
  const r = await act(() => api("PUT", `/api/users/${u.id}/password`, { password: pw }), "Password updated");
  if (r && r.self) location.href = "/login";
}
async function deleteUser(u) {
  if (await confirmBox("Delete user", `Delete ${u.username}?`)) { await act(() => api("DELETE", `/api/users/${u.id}`), "User deleted"); renderUsers(); }
}
function newUserDialog() {
  const user = h("input", { type: "text", maxlength: 64, autocomplete: "off" });
  const pw = h("input", { type: "password", maxlength: 128, autocomplete: "new-password" });
  const err = h("div", { class: "err-text" });
  modal({
    title: "Add user",
    body: h("div", { style: { display: "grid", gap: "14px" } },
      h("div", { class: "field" }, h("label", null, "Username"), user),
      h("div", { class: "field" }, h("label", null, "Password (12+ characters)"), pw), err),
    actions: [
      c => h("button", { class: "btn", onclick: () => c() }, "Cancel"),
      c => h("button", {
        class: "btn primary", onclick: async () => {
          try { await api("POST", "/api/users", { username: user.value.trim(), password: pw.value }); c(); toast("User added"); renderUsers(); }
          catch (e) { err.textContent = e.message; }
        },
      }, "Add"),
    ],
  });
}

// ---------------------------------------------------------------------------
// plumbing
// ---------------------------------------------------------------------------
let etag = null, refreshing = null, pollTimer = null, failures = 0;
const rendered = new Map();
function status() {
  if (!S) return;
  $("#me").textContent = S.me.username;
  $("#avatar").textContent = S.me.username.slice(0, 2).toUpperCase();
  $("#account-menu summary").setAttribute("aria-label", `Account: ${S.me.username}`);
  $("#account-menu summary").title = S.me.username;
  $("#auth-provider").textContent = S.me.provider === "local" ? "Local account" : "Access account";
  $("#site-name").textContent = S.site_name;
  document.title = `${view[0].toUpperCase() + view.slice(1)} · ${S.site_name}`;
  $("#worker-down").hidden = S.worker_alive;
  $("#tab-users").hidden = !S.manages_users;
  $("#library-count").textContent = S.uploads.length;
  const online = S.displays.filter(d => d.online).length;
  $("#display-summary").textContent = `${online} of ${S.displays.length} displays online`;
  $("#display-summary").classList.toggle("incomplete", online < S.displays.length || online === 0);
  $("#stream-summary").textContent = `${S.streams.filter(s => s.kind !== "screensaver").length} streams · ${S.uploads.length} library items`;
  $("#upload-limit").textContent = `${S.max_upload_mb} MB / file`;
  $("#file-input").accept = S.accept.join(",");
  $("#workspace-date").textContent = new Date(S.server_time_ms).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
function render(force = false) {
  if (!S) return;
  status();
  const root = $(`#view-${view}`);
  if (dragging || document.querySelector("dialog[open]") || (!force && root.contains(document.activeElement))) return;
  let signature;
  if (view === "streams") signature = JSON.stringify([S.streams, S.placements, S.uploads.map(u => [u.id,u.title,u.status,u.slide_count,u.slides]), S.displays.map(d => [d.id,d.name,d.online,d.stream_id]), Math.floor(S.server_time_ms / 60000)]);
  else if (view === "library") signature = JSON.stringify([S.worker_alive,S.uploads,S.placements,S.streams.map(s => [s.id,s.name])]);
  else if (view === "displays") signature = JSON.stringify([S.streams, S.displays.map(d => [d.id,d.name,d.slug,d.online,d.stream_id,d.now_thumb,d.last_ip]), Math.floor(S.server_time_ms / 60000)]);
  if (!force && rendered.get(view) === signature) return;
  if (view === "streams") renderStreams();
  else if (view === "library") renderLibrary();
  else if (view === "displays") renderDisplays();
  rendered.set(view, signature);
}
let lastRefreshAt = 0;
function workspaceNotice(offline = false) {
  const title = offline ? "Browser is offline" : "Workspace updates unavailable";
  const last = lastRefreshAt ? ` Last successful refresh: ${new Date(lastRefreshAt).toLocaleTimeString()}.` : "";
  const detail = (S ? "Showing the last received workspace data; it may be out of date." : "The workspace has not loaded yet.")
    + last + (offline ? " The browser reports no network connection. Reconnect or retry." : " Retrying automatically; you can also retry now.")
    + " This warning does not report display playback health.";
  // Avoid repeatedly announcing identical failures on every retry.
  if ($("#workspace-notice-title").textContent !== title) $("#workspace-notice-title").textContent = title;
  if ($("#workspace-notice-detail").textContent !== detail) $("#workspace-notice-detail").textContent = detail;
  $("#connection-banner").hidden = false;
}
function workspaceFresh() {
  lastRefreshAt = Date.now();
  const refreshButton = $("#refresh-now");
  refreshButton.title = `Refresh workspace. Last successful refresh: ${new Date(lastRefreshAt).toLocaleTimeString()}`;
  // Do not leave keyboard focus inside a warning that just disappeared.
  if ($("#connection-banner").contains(document.activeElement)) refreshButton.focus({ preventScroll: true });
  $("#connection-banner").hidden = true;
}
function isOverview(data) {
  return data && typeof data === "object" && typeof data.me?.username === "string"
    && ["streams", "displays", "uploads", "placements"].every(key => Array.isArray(data[key]))
    && Number.isFinite(data.server_time_ms);
}
function schedulePoll() {
  clearTimeout(pollTimer);
  if (!document.hidden) pollTimer = setTimeout(() => refresh(false), Math.min(30000, 4000 * 2 ** Math.min(failures, 3)));
}
async function refresh(force = false) {
  if (refreshing) {
    await refreshing;
    // A mutation may finish while an older poll is in flight. Fetch fresh state
    // after that poll settles, rather than repainting its stale response.
    if (force) return refresh(true);
    return;
  }
  clearTimeout(pollTimer);
  $("#refresh-now").setAttribute("aria-busy", "true");
  refreshing = (async () => {
    try {
      const result = await request("GET", "/api/overview", undefined, { etag: force ? null : etag });
      if (result.response.status === 304) {
        if (!S) throw new Error("No previous workspace data is available.");
        S.server_time_ms = Number(result.response.headers.get("X-Server-Time")) || Date.now();
      } else {
        // An HTML proxy error with status 200 is not a successful data refresh.
        if (!isOverview(result.data)) throw new Error("The server did not return valid workspace data.");
        S = result.data;
      }
      etag = result.response.headers.get("ETag") || etag;
      render(force);
      failures = 0;
      workspaceFresh();
      if (view === "library") refreshTransfers();
    } catch (error) {
      failures++;
      workspaceNotice(!navigator.onLine);
    }
  })();
  try { await refreshing; } finally {
    refreshing = null;
    $("#refresh-now").setAttribute("aria-busy", "false");
    schedulePoll();
  }
}
function show(v) {
  if (!["streams", "library", "displays", "activity", "users"].includes(v)) return;
  if (v === "users" && S && !S.manages_users) v = "streams";
  view = v;
  document.querySelectorAll("#tabs button").forEach(button => {
    button.classList.toggle("active", button.dataset.view === v);
    if (button.dataset.view === v) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  document.querySelectorAll(".view").forEach(section => section.classList.toggle("active", section.id === `view-${v}`));
  const label = v[0].toUpperCase() + v.slice(1);
  $("#view-label").textContent = label;
  $("#nav-toggle").setAttribute("aria-label", `Workspace navigation, current page: ${label}`);
  try { localStorage.setItem("sp_view", v); } catch (_) {}
  if (!S) return;
  if (v === "activity") renderActivity().catch(error => toast(error.message, true));
  else if (v === "users") renderUsers().catch(error => toast(error.message, true));
  render(true);
  if (v === "library") refreshTransfers();
}
function wireTips() {
  const tip = $("#tip");
  function showTip(event) {
    const el = event.target.closest?.("[data-tip]");
    if (!el) { tip.style.display = "none"; return; }
    tip.textContent = el.dataset.tip; tip.style.display = "block";
    const rect = el.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(window.innerWidth - tip.offsetWidth - 8, rect.left)) + "px";
    tip.style.top = Math.min(window.innerHeight - tip.offsetHeight - 8, rect.bottom + 8) + "px";
  }
  document.addEventListener("mouseover", showTip); document.addEventListener("focusin", showTip);
  document.addEventListener("focusout", () => { tip.style.display = "none"; });
  document.addEventListener("keydown", e => { if (e.key === "Escape") tip.style.display = "none"; });
}
// A disclosure on small screens; the same navigation stays inline on desktop.
// No duplicate controls, focus trap, global shortcuts or extra polling loop.
function setNavigationOpen(open, restoreFocus = false) {
  $("#app-header").classList.toggle("nav-open", open);
  $("#nav-toggle").setAttribute("aria-expanded", String(open));
  if (open) $("#account-menu").open = false;
  if (restoreFocus) $("#nav-toggle").focus({ preventScroll: true });
}
function wireNavigation() {
  const header = $("#app-header"), toggle = $("#nav-toggle"), tabs = $("#tabs"), account = $("#account-menu");
  let lastFocused = document.activeElement;
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    setNavigationOpen(open);
    if (open) (tabs.querySelector("button[aria-current=page]:not([hidden])") || tabs.querySelector("button:not([hidden])"))?.focus();
  });
  account.addEventListener("toggle", () => { if (account.open) setNavigationOpen(false); });
  document.addEventListener("click", event => {
    if (!tabs.contains(event.target) && !toggle.contains(event.target)) setNavigationOpen(false);
    if (!account.contains(event.target)) account.open = false;
  });
  document.addEventListener("focusin", event => {
    lastFocused = event.target;
    if (!tabs.contains(event.target) && !toggle.contains(event.target)) setNavigationOpen(false);
    if (!account.contains(event.target)) account.open = false;
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    if (header.classList.contains("nav-open")) { event.preventDefault(); setNavigationOpen(false, true); }
    else if (account.open) { event.preventDefault(); account.open = false; account.querySelector("summary").focus(); }
  });
  const breakpoint = matchMedia("(max-width: 900px)");
  const resize = () => {
    // Hiding a focused control can move focus to body before matchMedia fires.
    const focused = document.activeElement === document.body ? lastFocused : document.activeElement;
    const focusInNav = tabs.contains(focused);
    const focusOnToggle = focused === toggle;
    setNavigationOpen(false);
    if (breakpoint.matches && focusInNav) toggle.focus({ preventScroll: true });
    else if (!breakpoint.matches && focusOnToggle) (tabs.querySelector("button[aria-current=page]:not([hidden])") || tabs.querySelector("button:not([hidden])"))?.focus();
  };
  breakpoint.addEventListener("change", resize);
}

function init() {
  applyTheme();
  document.querySelectorAll("#tabs button").forEach(button => {
    button.addEventListener("click", () => {
      show(button.dataset.view);
      if ($("#app-header").classList.contains("nav-open")) {
        setNavigationOpen(false);
        $("#main").focus({ preventScroll: true });
      }
    });
  });
  $("#theme-toggle").append(icon("theme"));
  const toggleTheme = () => {
    const current = applyTheme(), next = ({ system: "light", light: "dark", dark: "system" })[current];
    try { localStorage.setItem("sp_theme", next); } catch (_) {}
    applyTheme(); toast(`Theme: ${next}`);
  };
  $("#theme-toggle").addEventListener("click", toggleTheme);
  const logout = async () => { try { const result = await api("POST", "/auth/logout"); location.assign(result.redirect); } catch (error) { toast(error.message, true); } };
  $("#signout").addEventListener("click", logout);
  wireNavigation();
  $("#refresh-now").append(icon("refresh"));
  $("#refresh-now").addEventListener("click", () => refresh(true));
  $("#retry-workspace").addEventListener("click", () => refresh(true));
  $("#refresh-activity").addEventListener("click", () => renderActivity().catch(e => toast(e.message, true)));
  $("#manage-displays").addEventListener("click", () => show("displays"));
  $("#btn-new-stream").addEventListener("click", async () => {
    if (!S) return;
    const name = await promptBox("New stream", "Name", "", { max: 60 });
    if (name?.trim()) act(() => api("POST", "/api/streams", { name: name.trim() }), "Stream created");
  });
  $("#btn-override-all").addEventListener("click", () => {
    if (S) pickUploadDialog("Override all streams", u => placeDialog(u, { mode: "override", all: true }));
  });
  $("#btn-new-display").addEventListener("click", () => { if (S) newDisplayDialog(); });
  $("#btn-new-user").addEventListener("click", () => { if (S) newUserDialog(); });
  const input = $("#file-input"), zone = $("#dropzone");
  $('#btn-graphic').addEventListener('click',()=>{if(S)$('#graphic-input').click();});
  $('#graphic-input').addEventListener('change',e=>{uploadFiles(e.target.files,true);e.target.value='';});
  $("#btn-source").addEventListener("click", () => { if (S) sourceDialog(); });
  $("#btn-shares").addEventListener("click", () => { if (S) sharedFolders(); });
  $("#btn-upload").addEventListener("click", () => { if (S) input.click(); });
  zone.addEventListener("click", () => { if (S) input.click(); });
  input.addEventListener("change", () => { uploadFiles(input.files); input.value = ""; });
  $("#lib-filter").addEventListener("input", () => { if (S) renderLibrary(); });
  $("#lib-kind").addEventListener("change", () => { if (S) renderLibrary(); });
  const library = $("#view-library");
  library.addEventListener("dragover", e => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); zone.classList.add("hot"); } });
  library.addEventListener("dragleave", e => { if (!library.contains(e.relatedTarget)) zone.classList.remove("hot"); });
  library.addEventListener("drop", e => { if (e.dataTransfer.files.length) { e.preventDefault(); zone.classList.remove("hot"); uploadFiles(e.dataTransfer.files); } });
  wireTips(); wireDisplayDrop();
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearTimeout(pollTimer); else refresh(false); });
  window.addEventListener("online", () => refresh(false));
  window.addEventListener("offline", () => workspaceNotice(true));
  let saved = "streams"; try { saved = localStorage.getItem("sp_view") || "streams"; } catch (_) {}
  refresh(true).then(() => show(saved));
}
init();
