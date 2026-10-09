// Journal front end. Plain JavaScript, no frameworks.
(() => {
  const $ = (id) => document.getElementById(id);
  const CFG = window.JOURNAL;
  // The draft copy kept in this browser; on the web, one per account (set at boot).
  let LOCAL_DRAFT_KEY = "journal.draft";
  const offlineMessage = CFG.hosted
    ? "Can't reach the journal right now. Your writing is kept in this browser in the meantime."
    : "Can't reach the journal app. Is the start.bat window still open? Your writing is kept in this browser in the meantime.";

  const state = {
    prompt: null,         // writing prompt in use: {prompt, based_on}
    promptsShown: [],     // prompts suggested this time, so "Another one" doesn't repeat
    current: null,        // entry being viewed
    draftDirty: false,    // typed since last server draft save
    clientError: null,    // banner message from a failed request
    unlocked: false,      // this page has unlocked the journal
  };

  // ---- Helpers ----------------------------------------------------------

  // End a message with exactly one full stop.
  const sentence = (msg) => String(msg).replace(/[.\s]+$/, "") + ".";
  const wordCount = (t) => (t.trim().match(/\S+/g) || []).length;
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

  function fmtDate(iso, opts) {
    return new Date(iso).toLocaleString(undefined, { timeZone: CFG.timezone, ...opts });
  }
  const longDate = (iso) => fmtDate(iso, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  const timeOf = (iso) => fmtDate(iso, { hour: "numeric", minute: "2-digit" });

  // passive: timer-driven requests that shouldn't count as activity (so the
  // journal can still lock itself when you've walked away).
  async function api(method, url, body, { passive = false } = {}) {
    const headers = { "X-Journal-Client": "1" };
    if (body) headers["Content-Type"] = "application/json";
    if (passive) headers["X-Journal-Passive"] = "1";
    const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if ((res.status === 423 || res.status === 401) && state.unlocked) {
      // Locked (timed out, or locked elsewhere): reload to the lock screen,
      // which also clears everything decrypted off the page.
      state.unlocked = false;
      location.reload();
    }
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status})`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function localGet() {
    try { return JSON.parse(localStorage.getItem(LOCAL_DRAFT_KEY) || "null"); } catch (_) { return null; }
  }
  function localSet(draft) {
    try {
      localStorage.setItem(LOCAL_DRAFT_KEY, JSON.stringify({ ...draft, saved_at: new Date().toISOString() }));
    } catch (_) {}
  }
  // What's on the writing page right now: text, title and any chosen prompt.
  const currentDraft = () => ({
    text: $("editor").value,
    title: $("title-input").value,
    prompt: state.prompt ? state.prompt.prompt : null,
  });
  function localClear() {
    try { localStorage.removeItem(LOCAL_DRAFT_KEY); } catch (_) {}
  }

  // ---- Banner -----------------------------------------------------------

  function renderBanner() {
    const b = $("banner");
    let msg = null, isError = false, isNotice = false;
    if (state.clientError) {
      msg = state.clientError; isError = true;
    } else if (state.notice) {
      msg = state.notice; isNotice = true;
    }
    b.hidden = !msg;
    b.textContent = msg || "";
    b.classList.toggle("error", isError);
    b.classList.toggle("notice", isNotice);
  }

  // A short, friendly confirmation at the top that fades on its own.
  let noticeTimer = null;
  function showNotice(text, ms = 3500) {
    state.notice = text;
    renderBanner();
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { state.notice = null; renderBanner(); }, ms);
  }

  async function pollStatus() {
    try {
      const s = await api("GET", "/api/status", null, { passive: true });
      if (state.unlocked && !s.unlocked) { state.unlocked = false; location.reload(); return; }
      if (state.clientError && state.clientError.startsWith("Can't reach")) state.clientError = null;
    } catch (_) {
      state.clientError = offlineMessage;
    }
    renderBanner();
  }

  // ---- Views ------------------------------------------------------------

  // Restart one of the page animations (fade in, or a page turn) on an element.
  function animatePage(el, cls) {
    el.classList.remove("view-in", "turn-older", "turn-newer");
    void el.offsetWidth;  // lets the same animation play again
    el.classList.add(cls);
  }

  function showView(name) {
    const el = $(`${name}-view`);
    const changed = el.hidden;
    for (const v of ["write", "entry", "settings"]) $(`${v}-view`).hidden = v !== name;
    $("past").hidden = name === "settings";
    if (changed) animatePage(el, "view-in");
  }

  // ---- Look: theme and today's photo ------------------------------------

  function applyTheme(theme, palette) {
    if (theme) document.documentElement.dataset.theme = theme;
    if (palette) document.documentElement.dataset.palette = palette;
  }

  // The look as last saved. Settings previews changes live, and Back without
  // saving returns to this.
  const savedLook = {
    theme: document.documentElement.dataset.theme,
    palette: document.documentElement.dataset.palette,
    blur: CFG.photoBlur,
    visibility: CFG.photoVisibility,
    photo: document.documentElement.dataset.photo,  // "on" / "off"
  };

  function applyPhotoLook(blur, visibility) {
    const root = document.documentElement.style;
    root.setProperty("--photo-blur", `${blur}px`);
    root.setProperty("--veil-strength", String((100 - visibility) / 100));
  }

  function restoreSavedLook() {
    applyTheme(savedLook.theme, savedLook.palette);
    applyPhotoLook(savedLook.blur, savedLook.visibility);
    document.documentElement.dataset.photo = savedLook.photo;
    $("backdrop").style.visibility = "";  // the saved photo setting decides via loadBackground
  }

  // Show the slider values, and grey the sliders out when the photo is off.
  function syncPhotoControls() {
    const f = $("settings-form").elements;
    $("photo-blur-value").textContent = f.photo_blur.value === "0" ? "sharp" : `${f.photo_blur.value} px`;
    $("photo-visibility-value").textContent = `${f.photo_visibility.value}%`;
    const off = !f.background_photo.checked;
    $("photo-sliders").classList.toggle("off", off);
    f.photo_blur.disabled = off;
    f.photo_visibility.disabled = off;
  }

  function previewLook() {
    const form = $("settings-form");
    const picked = form.querySelector('input[name="colour_theme"]:checked');
    applyTheme(form.elements.theme.value, picked && picked.value);
    applyPhotoLook(Number(form.elements.photo_blur.value), Number(form.elements.photo_visibility.value));
    const photoOn = form.elements.background_photo.checked;
    $("backdrop").style.visibility = photoOn ? "" : "hidden";
    document.documentElement.dataset.photo = photoOn ? "on" : "off";  // dark theme-coloured surround when off
    syncPhotoControls();
  }

  // Shows the current photo (it moves on with each new entry). When it
  // changes, the old one fades out and the new one fades in.
  // A random photo on every page load (and after each new entry), never the
  // one shown last time, even across refreshes.
  const LAST_PHOTO_KEY = "journal.lastPhoto";
  let shownPhoto = null;
  async function loadBackground() {
    const el = $("backdrop");
    let last = null;
    try { last = localStorage.getItem(LAST_PHOTO_KEY); } catch (_) {}
    let res;
    try {
      res = await api("GET", `/api/background${last ? `?avoid=${encodeURIComponent(last)}` : ""}`);
    } catch (_) { return; }
    if (!res.url) {
      el.classList.remove("loaded");
      el.style.backgroundImage = "";
      shownPhoto = null;
      return;
    }
    if (res.url === shownPhoto) return;
    try { localStorage.setItem(LAST_PHOTO_KEY, res.name); } catch (_) {}
    const img = new Image();
    img.onload = async () => {
      if (shownPhoto && el.classList.contains("loaded")) {
        el.classList.remove("loaded");
        await new Promise((r) => setTimeout(r, 700));  // let the old photo fade
      }
      el.style.backgroundImage = `url("${res.url}")`;
      el.classList.add("loaded");
      shownPhoto = res.url;
    };
    img.src = res.url;
  }

  function renderGreeting() {
    const name = (CFG.userName || "").trim();
    $("greeting").textContent = name ? `Hi ${name}, what's on your mind?` : "What's on your mind?";
  }

  function showWrite() {
    state.current = null;
    showView("write");
    $("editor").focus();
    schedulePromptNudge();
  }

  // "612 words", plus "✦ longest" on the entry that holds the record.
  function showEntryWords(entry) {
    const s = state.lastStats;
    const record = s && s.longest_id === entry.id && s.shown_entries > RECORD_FROM;
    $("entry-words").replaceChildren(document.createTextNode(plural(entry.word_count, "word")));
    if (record) {
      const tag = document.createElement("span");
      tag.className = "longest-tag";
      tag.textContent = " ✦ longest";
      tag.title = "Your longest entry so far";
      $("entry-words").append(tag);
    }
  }

  function showEntry(entry) {
    const switching = !state.current || state.current.id !== entry.id;
    state.current = entry;
    showView("entry");
    if (state.turn) {
      animatePage($("entry-view"), state.turn < 0 ? "turn-older" : "turn-newer");
      state.turn = null;
    } else if (switching) {
      animatePage($("entry-view"), "view-in");
    }
    $("entry-date").textContent = `${longDate(entry.created_at)} · ${timeOf(entry.created_at)}`;
    showEntryWords(entry);
    $("entry-prompt").hidden = !entry.prompt;
    $("entry-prompt").textContent = entry.prompt ? `Prompt: ${entry.prompt}` : "";
    $("entry-text").textContent = entry.text;
    renderTitle(entry);
    $("entry-status").textContent = "";
    setEditing(false);
    renderReflection(entry);
    updateNav();
    syncCalendarTo(entry);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---- Older / Newer ----------------------------------------------------

  const when = (e) => Date.parse(e.created_at);
  // Entries passing the favourites filter (what the calendar shades).
  const favShown = () => (state.favOnly
    ? (state.allEntries || []).filter((e) => e.favourite)
    : (state.allEntries || []));
  // Entries passing every filter (the list and Older / Newer).
  const inRange = (e) => !state.range || (e.date >= state.range.start && e.date <= state.range.end);
  const shown = () => favShown().filter(inRange);

  function filterDescription() {
    const parts = [];
    if (state.favOnly) parts.push("favourites");
    if (state.range) parts.push(`${state.range.prep} ${state.range.label}`);
    return parts.join(" ");
  }

  function updateNav() {
    if (!state.current || $("entry-view").hidden) return;
    const list = shown();
    const t = when(state.current);
    const older = list.filter((e) => when(e) < t).pop();
    const newer = list.find((e) => when(e) > t);
    state.olderId = older && older.id;
    state.newerId = newer && newer.id;
    $("prev-btn").disabled = !older;
    $("next-btn").disabled = !newer;
    const i = list.findIndex((e) => e.id === state.current.id);
    const what = filterDescription();
    $("nav-pos").textContent = i >= 0 ? `${i + 1} of ${list.length}${what ? ` ${what}` : ""}` : "";
  }

  // Step from where the last press was heading, so quick presses don't get lost.
  function step(direction) {
    const list = shown();
    const baseId = state.navTarget || (state.current && state.current.id);
    const base = list.find((e) => e.id === baseId) || state.current;
    if (!base) return;
    const t = when(base);
    const target = direction < 0
      ? list.filter((e) => when(e) < t).pop()
      : list.find((e) => when(e) > t);
    if (!target) return;
    state.navTarget = target.id;
    state.turn = direction;  // showEntry plays a page turn in this direction
    openEntry(target.id);
  }
  function goOlder() { step(-1); }
  function goNewer() { step(1); }

  // ---- Calendar ---------------------------------------------------------

  const pad = (n) => String(n).padStart(2, "0");
  // The journal's calendar day (Toronto time) for an instant, as YYYY-MM-DD.
  const dayKey = (d) => new Intl.DateTimeFormat("en-CA",
    { timeZone: CFG.timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  state.cal = (() => {
    const [y, m] = dayKey(new Date()).split("-").map(Number);
    return { year: y, month: m - 1, open: false, pending: null };
  })();
  // The date filter: null, or {start, end, label} with dates as "YYYY-MM-DD".
  state.range = null;

  // A "YYYY-MM-DD" date in words (read as a plain calendar date, no time zone).
  const dayWords = (key, opts) => new Date(`${key}T12:00:00Z`)
    .toLocaleDateString(undefined, { timeZone: "UTC", ...opts });
  const monthName = (y, m) => new Date(Date.UTC(y, m, 1, 12))
    .toLocaleDateString(undefined, { timeZone: "UTC", month: "long", year: "numeric" });

  function rangeLabel(start, end) {
    if (start === end) return dayWords(start, { weekday: "short", month: "short", day: "numeric" });
    const [sy, sm] = start.split("-");
    const [ey, em] = end.split("-");
    const startWords = dayWords(start, { month: "short", day: "numeric" });
    if (sy === ey && sm === em) return `${startWords}–${Number(end.slice(8))}`;
    return `${startWords} – ${dayWords(end, { month: "short", day: "numeric", year: sy === ey ? undefined : "numeric" })}`;
  }

  // The calendar is a pop-up: it opens from its button and closes on a
  // finished pick, a click elsewhere, Esc, or the button again.
  function toggleCalendar(open = !state.cal.open) {
    state.cal.open = open;
    state.cal.pending = null;
    $("calendar").hidden = !open;
    $("calendar-btn").setAttribute("aria-expanded", String(open));
    if (open) {
      renderCalendar();
      const target = $("calendar").querySelector(".cal-day.range-start, .cal-day.today, .cal-day.has-entry");
      (target || $("cal-next")).focus({ preventScroll: true });
    }
  }

  // Open the calendar on the month of the entry being read.
  function syncCalendarTo(entry) {
    if (state.cal.open) return;
    const [y, m] = dayKey(new Date(entry.created_at)).split("-").map(Number);
    state.cal.year = y;
    state.cal.month = m - 1;
  }

  function shiftMonth(delta) {
    const d = new Date(state.cal.year, state.cal.month + delta, 1);
    state.cal.year = d.getFullYear();
    state.cal.month = d.getMonth();
    renderCalendar();
  }

  function setRange(start, end, label) {
    // "on Mon, Sep 14", "in September 2026", "from Sep 8–14"
    const prep = label ? "in" : start === end ? "on" : "from";
    state.range = start ? { start, end, label: label || rangeLabel(start, end), prep } : null;
    applyFilters();
  }

  function pickWholeMonth() {
    const { year, month } = state.cal;
    const last = new Date(year, month + 1, 0).getDate();
    setRange(`${year}-${pad(month + 1)}-01`, `${year}-${pad(month + 1)}-${pad(last)}`, monthName(year, month));
    toggleCalendar(false);
  }

  // First click: that day. Second click on another day: the range between.
  function pickDay(key) {
    const first = state.cal.pending;
    if (first && first !== key) {
      const [start, end] = [first, key].sort();
      setRange(start, end);
      toggleCalendar(false);
    } else if (first === key) {
      toggleCalendar(false);  // that single day it is
    } else {
      state.cal.pending = key;
      setRange(key, key);
      renderCalendar();
    }
  }

  function renderCalendar() {
    if (!state.cal.open) return;
    const { year, month } = state.cal;
    const byDay = {};
    for (const e of favShown()) (byDay[e.date] = byDay[e.date] || []).push(e);

    $("cal-label").textContent = monthName(year, month);
    $("cal-hint").textContent = state.cal.pending
      ? "Click another day to make a range, or click the same day again to finish."
      : "Click a day, then another to choose a range. Click the month for all of it.";
    const grid = $("cal-grid");
    grid.replaceChildren();
    for (const name of ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]) {
      const h = document.createElement("div");
      h.className = "cal-dow";
      h.textContent = name;
      grid.append(h);
    }
    for (let i = 0; i < new Date(year, month, 1).getDay(); i++) grid.append(document.createElement("div"));

    const today = dayKey(new Date());
    const days = new Date(year, month + 1, 0).getDate();
    for (let d = 1; d <= days; d++) {
      const key = `${year}-${pad(month + 1)}-${pad(d)}`;
      const list = byDay[key] || [];
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "cal-day";
      cell.textContent = d;
      const r = state.range;
      cell.classList.toggle("has-entry", list.length > 0);
      cell.classList.toggle("has-fav", list.some((e) => e.favourite));
      cell.classList.toggle("today", key === today);
      cell.classList.toggle("in-range", !!r && key >= r.start && key <= r.end);
      cell.classList.toggle("range-start", !!r && key === r.start);
      cell.classList.toggle("range-end", !!r && key === r.end);
      cell.title = list.length === 0 ? "No entries"
        : list.length === 1 ? (list[0].title || "No Title") : `${list.length} entries`;
      cell.addEventListener("click", () => pickDay(key));
      grid.append(cell);
    }
  }

  // ---- Stats ------------------------------------------------------------

  async function refreshStats({ passive = false } = {}) {
    let s;
    try { s = await api("GET", "/api/stats", null, { passive }); } catch (_) { return null; }
    state.lastStats = s;
    renderStats(s);
    return s;
  }

  // ---- Streak milestones: 3 days, 7 days, then every 7 days -------------

  const BADGE_FROM = 3;  // from here on the streak shows as a badge
  const isMilestone = (n) => n === 3 || (n >= 7 && n % 7 === 0);
  const NUMBER_WORDS = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
    "Nine", "Ten", "Eleven", "Twelve"];

  function milestoneWords(n) {
    if (n === 3) return "Three days in a row";
    if (n === 7) return "A full week of writing";
    const weeks = n / 7;
    return `${NUMBER_WORDS[weeks] || weeks} weeks of writing`;
  }

  function renderStats(s) {
    const box = $("stats");
    box.replaceChildren();
    box.append(document.createTextNode(`${s.total_entries} ${s.total_entries === 1 ? "entry" : "entries"}`));
    const n = s.current_streak;
    if (n >= BADGE_FROM) {
      const c = state.celebrating;
      const fresh = c && c.n === n && Date.now() < c.until;
      const badge = document.createElement("span");
      // Gold on the day a milestone is reached; brighter while it's being celebrated.
      const milestoneToday = isMilestone(n) && s.wrote_today;
      badge.className = "streak-badge" + (milestoneToday || fresh ? " milestone" : "") + (fresh ? " celebrate" : "");
      if (fresh && !c.popped) { badge.classList.add("pop"); c.popped = true; }  // animate once
      badge.textContent = fresh ? `✦ ${milestoneWords(n)}` : `✦ ${n}-day streak`;
      badge.title = fresh ? `${n}-day streak` : "Days in a row with at least one entry";
      box.append(document.createTextNode(" · "), badge);
    } else if (n > 0) {
      box.append(document.createTextNode(` · ${n}-day streak`));
    }
    const r = state.record;
    if (r && Date.now() < r.until) {
      const rec = document.createElement("span");
      rec.className = "streak-badge milestone celebrate record-badge";
      if (!r.popped) { rec.classList.add("pop"); r.popped = true; }   // animate once
      rec.textContent = `✦ Your longest entry yet · ${plural(r.words, "word")}`;
      box.append(document.createTextNode(" "), rec);
    }
    if (s.longest_streak > n && s.longest_streak > 1) box.append(document.createTextNode(` · best ${s.longest_streak}`));
    if (n > 0 && !s.wrote_today) {
      const nudge = document.createElement("span");
      nudge.className = "nudge";
      nudge.textContent = " · write today to keep it going";
      box.append(nudge);
    }
    renderLimits(s);
  }

  // On the web version: how many reflections are left today.
  function renderLimits(s) {
    if (s.reflections_left == null) return;
    const left = s.reflections_left;
    const text = left === 0 ? "No reflections left today"
      : `${plural(left, "reflection")} left today`;
    $("finish-btn").title = `${text} (${s.reflections_per_day} a day). Entries are always saved.`;
    const note = $("limits-note");
    if (note) {
      note.textContent = `${text}. Each person gets ${s.reflections_per_day} reflections a day; `
        + "entries are always saved, and an entry without one can be reflected on another day.";
    }
  }

  // Called after Finish & reflect: celebrate if this entry reached a milestone.
  const RECORD_FROM = 3;  // earlier entries needed before "longest yet" counts

  async function checkMilestone(before, entry) {
    const after = await refreshStats();
    if (!before || !after) return;
    if (entry && before.shown_entries >= RECORD_FROM && entry.word_count > before.longest_words) {
      state.record = { words: entry.word_count, until: Date.now() + 8000 };
      renderStats(after);
      setTimeout(() => { if (state.lastStats) renderStats(state.lastStats); }, 8100);
      if (state.current && state.current.id === entry.id) showEntryWords(state.current);
    }
    const n = after.current_streak;
    if (n > before.current_streak && isMilestone(n)) {
      state.celebrating = { n, until: Date.now() + 8000 };
      renderStats(after);
      setTimeout(() => { if (state.lastStats) renderStats(state.lastStats); }, 8100);  // settle to "✦ N-day streak"
    }
  }

  // ---- Reflection -------------------------------------------------------

  function renderReflection(entry, { working = false } = {}) {
    renderTitle(entry);  // a suggested title arrives with the reflection
    const busy = working || entry.reflecting;
    const status = entry.reflection_status;
    const done = status === "Done" && entry.insight;
    $("reflection").hidden = false;
    $("reflection-working").hidden = !busy;
    $("reflection-body").hidden = busy || !done;
    $("reflection-missing").hidden = busy || done;

    if (!busy && !done) {
      const reasons = {
        "Failed": entry.reflection_error || "The reflection didn't come through.",
        "Pending": "This entry doesn't have a reflection yet.",
        "Not requested": "This entry doesn't have a reflection yet.",
      };
      $("reflection-missing-text").textContent =
        reasons[status] || "This entry doesn't have a reflection yet.";
      $("reflect-btn").textContent = status === "Failed" ? "Try reflection again" : "Reflect on this entry";
    }
    if (done) {
      $("insight").replaceChildren(...entry.insight.split(/\n\s*\n/).map((t) => {
        const p = document.createElement("p"); p.textContent = t.trim(); return p;
      }));
      $("question").textContent = entry.question || "";
      const ul = $("citations");
      ul.replaceChildren();
      // Verified quotes link to "the passage"; ideas attributed without quoting
      // link to "the source" (the app checks the chapter exists, not the words).
      const sourcesList = [
        ...(entry.quotes || []).map((q) => ({ ...q, kind: "quote" })),
        ...(entry.references || []).map((r) => ({ ...r, kind: "idea" })),
      ];
      for (const q of sourcesList) {
        const li = document.createElement("li");
        li.append(document.createTextNode(`— ${q.author}, ${q.title}${q.location ? ` ${q.location}` : ""} `));
        if (q.kind === "idea") {
          const tag = document.createElement("span");
          tag.className = "cite-kind";
          tag.textContent = "(idea) ";
          tag.title = q.idea ? `Paraphrased: ${q.idea}` : "An idea from this work, paraphrased";
          li.append(tag);
        }
        if (typeof q.url === "string" && q.url.startsWith("https://")) {
          const a = document.createElement("a");
          a.href = q.url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = q.kind === "quote" ? "Read the passage ↗" : "Read the source ↗";
          a.title = `${q.translation}, on Wikisource`;
          li.append(a);
        }
        ul.append(li);
      }
      const note = $("support-note");
      note.hidden = !entry.support_note;
      if (entry.support_note) note.innerHTML = renderSimpleMarkdown(entry.support_note);
    }
    if (entry.reflecting) pollWhileReflecting(entry.id);
  }

  // Only **bold** is supported; everything else is escaped.
  function renderSimpleMarkdown(text) {
    const esc = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return esc.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  async function requestReflection(id) {
    if (state.current && state.current.id === id) renderReflection(state.current, { working: true });
    let entry;
    try {
      entry = (await api("POST", `/api/entries/${id}/reflect`)).entry;
    } catch (err) {
      if (err.status === 409) { pollWhileReflecting(id); return; }
      entry = err.data && err.data.entry;
      if (entry && entry.reflection_status === "Done") {
        // Reflect again failed; the earlier reflection is still shown.
        state.clientError = `Couldn't get a new reflection: ${err.message}`;
        renderBanner();
      }
      if (!entry) {
        // Couldn't reach the app at all; show that on the reflection itself.
        entry = { ...(state.current || {}), id, reflection_status: "Failed",
          reflection_error: "Couldn't reach the journal app. Your entry is saved; try again once it's running." };
      }
    }
    if (state.current && state.current.id === id) {
      state.current = { ...state.current, ...entry };
      renderReflection(state.current);
    }
    refreshList();
    if (CFG.hosted) refreshStats();  // reflections left today
  }

  let pollTimer = null;
  function pollWhileReflecting(id) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      if (!state.current || state.current.id !== id) return;
      try {
        const entry = await api("GET", `/api/entries/${id}`);
        if (state.current && state.current.id === id) {
          state.current = entry;
          renderReflection(entry);
          if (!entry.reflecting) refreshList();
        }
      } catch (_) { pollWhileReflecting(id); }
    }, 3000);
  }

  // An instant as the journal's local date ("2026-09-14") and time ("12:42").
  function localParts(iso) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
      timeZone: CFG.timezone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
    return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
  }

  function renderTitle(entry) {
    const h = $("entry-title");
    h.textContent = entry.title || "No Title";
    h.classList.toggle("untitled", !entry.title);
    renderStar(!!entry.favourite);
  }

  // ---- Favourites -------------------------------------------------------

  function renderStar(on) {
    const b = $("star-btn");
    b.textContent = on ? "★" : "☆";
    b.setAttribute("aria-pressed", String(on));
    const label = on ? "Remove from favourites" : "Add to favourites";
    b.title = label;
    b.setAttribute("aria-label", label);
  }

  async function toggleFavourite() {
    const entry = state.current;
    if (!entry) return;
    const want = !entry.favourite;
    entry.favourite = want;
    renderStar(want);  // show it straight away; undo if saving fails
    try {
      const res = await api("POST", `/api/entries/${entry.id}/favourite`, { favourite: want });
      if (state.current && state.current.id === entry.id) state.current.favourite = res.entry.favourite;
      refreshList();
    } catch (err) {
      entry.favourite = !want;
      if (state.current && state.current.id === entry.id) renderStar(!want);
      state.clientError = `Couldn't update favourites: ${err.message}`;
      renderBanner();
    }
  }

  // ---- Filters: All · Favourites · Calendar (dates) ----------------------
  // Favourites and a date range can be combined; All clears both.
  // The favourites choice is remembered; a date range lasts until All.

  const FILTER_KEY = "journal.filter";
  state.favOnly = (() => { try { return localStorage.getItem(FILTER_KEY) === "fav"; } catch (_) { return false; } })();

  function showAll() {
    state.favOnly = false;
    state.range = null;
    applyFilters();
  }

  function toggleFavourites() {
    state.favOnly = !state.favOnly;
    applyFilters();
  }

  function applyFilters() {
    try { localStorage.setItem(FILTER_KEY, state.favOnly ? "fav" : "all"); } catch (_) {}
    $("filter-all").setAttribute("aria-pressed", String(!state.favOnly && !state.range));
    $("filter-fav").setAttribute("aria-pressed", String(state.favOnly));
    $("calendar-btn").setAttribute("aria-pressed", String(!!state.range));
    $("calendar-btn").textContent = state.range ? `Calendar: ${state.range.label}` : "Calendar";
    renderList();
    updateNav();
    renderCalendar();
  }

  function setEditing(on) {
    $("entry-text").hidden = on;
    $("entry-editor").hidden = !on;
    $("entry-title").hidden = on;
    $("star-btn").hidden = on;  // starring is for reading, not editing
    $("entry-title-input").hidden = !on;
    $("entry-when-edit").hidden = !on;
    if (on) {
      $("entry-title-input").value = state.current.title || "";
      const { date, time } = localParts(state.current.created_at);
      $("entry-date-input").value = date;
      $("entry-time-input").value = time;
      $("entry-date-input").max = localParts(new Date().toISOString()).date;
    }
    $("edit-btn").hidden = on;
    $("new-btn").hidden = on;
    $("delete-btn").hidden = on;
    $("cancel-edit-btn").hidden = !on;
    $("save-edit-btn").hidden = !on;
    if (on) {
      $("entry-editor").value = state.current.text;
      $("entry-editor").focus();
    }
  }

  async function refreshList() {
    try {
      state.allEntries = await api("GET", "/api/entries");  // oldest first
    } catch (_) { return; /* status poll reports connectivity */ }
    updateNav();
    renderCalendar();
    refreshStats();
    renderList();
  }

  function renderList() {
    const all = state.allEntries;
    if (!all) return;
    const entries = shown().slice();
    const n = entries.length;
    const noun = state.favOnly ? (n === 1 ? "favourite" : "favourites") : (n === 1 ? "entry" : "entries");
    $("filter-note").hidden = !state.favOnly && !state.range;
    $("filter-note").textContent = `Showing ${n} ${noun}${state.range ? ` ${state.range.prep} ${state.range.label}` : ""}`;
    {
      const ul = $("entry-list");
      ul.replaceChildren();
      for (const e of entries.reverse()) {
        const li = document.createElement("li");
        const when = document.createElement("span");
        when.className = "when";
        when.textContent = `${longDate(e.created_at)} · ${timeOf(e.created_at)} · ${plural(e.word_count, "word")}`;
        const title = document.createElement("span");
        title.className = e.title ? "title" : "title untitled";
        title.textContent = e.title || "No Title";
        if (e.favourite) {
          const star = document.createElement("span");
          star.className = "fav-mark";
          star.textContent = "★";
          star.title = "Favourite";
          title.prepend(star);
        }
        const snippet = document.createElement("span");
        snippet.className = "snippet";
        snippet.textContent = e.snippet + (e.snippet.length >= 120 ? "…" : "");
        li.append(when, title, snippet);
        if (e.reflection_status !== "Done") {
          const badge = document.createElement("span");
          badge.className = "badge";
          badge.textContent = "no reflection yet";
          when.append(badge);
        }
        li.addEventListener("click", () => openEntry(e.id));
        ul.append(li);
      }
      if (!entries.length) {
        const li = document.createElement("li");
        li.className = "subtle";
        li.textContent = !all.length ? "No entries yet."
          : state.range ? `No ${state.favOnly ? "favourites" : "entries"} ${state.range.prep} ${state.range.label}. Click All to see everything.`
          : "No favourites yet. Open an entry and click ☆ to add it here.";
        ul.append(li);
      }
    }
  }

  // Smoothly scroll to the top; resolves once the scroll has finished.
  function scrollToTop() {
    return new Promise((resolve) => {
      if (window.scrollY < 4) return resolve();
      const instant = matchMedia("(prefers-reduced-motion: reduce)").matches;
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      window.scrollTo({ top: 0, behavior: instant ? "auto" : "smooth" });
      // Watch the actual position (a "scroll ended" event from an earlier
      // scroll could otherwise end the wait too soon).
      const check = () => { if (window.scrollY < 4) finish(); else if (!done) requestAnimationFrame(check); };
      requestAnimationFrame(check);
      setTimeout(finish, 1200);  // never wait longer than this, e.g. if the scroll is interrupted
    });
  }

  let openToken = 0;
  async function openEntry(id) {
    const token = ++openToken;
    try {
      // Scroll up while the entry loads, then turn the page in plain view.
      const [entry] = await Promise.all([api("GET", `/api/entries/${id}`), scrollToTop()]);
      if (token !== openToken) return;  // a newer request superseded this one
      showEntry(entry);
    } catch (err) {
      if (token !== openToken) return;
      state.clientError = err.message; renderBanner();
    } finally {
      if (token === openToken) { state.navTarget = null; state.turn = null; }
    }
  }

  // ---- Writing prompts --------------------------------------------------

  function renderPrompt() {
    const p = state.prompt;
    $("prompt-card").hidden = !p;
    $("prompt-btn").hidden = !!p;
    if (!p) return;
    $("prompt-text").textContent = p.prompt;
    const src = p.based_on;
    const verb = p.approach === "follow_up_question" ? "Following up on" : "Returning to";
    $("prompt-source").textContent = !src ? ""
      : src.title ? `${verb} “${src.title}”, ${src.date}`
      : `${verb} your entry from ${src.date}`;
  }

  function setPromptStatus(text) {
    $("prompt-status").hidden = !text;
    $("prompt-status").textContent = text || "";
  }

  async function getPrompt() {
    const buttons = [$("prompt-btn"), $("another-prompt-btn")];
    clearTimeout(promptNudgeTimer);
    $("prompt-btn").classList.remove("nudge-glow");
    buttons.forEach((b) => { b.disabled = true; });
    setPromptStatus("Thinking of a prompt…");
    try {
      const res = await api("POST", "/api/writing-prompt", { already_suggested: state.promptsShown });
      state.prompt = res;
      state.promptsShown.push(res.prompt);
      setPromptStatus("");
      renderPrompt();
      onType();  // keep the prompt with the draft
      $("editor").focus();
    } catch (err) {
      setPromptStatus(`Couldn't get a prompt: ${err.message}`);
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  function dismissPrompt() {
    state.prompt = null;
    setPromptStatus("");
    renderPrompt();
    onType();
    $("editor").focus();
  }

  // ---- Writing & drafts -------------------------------------------------

  function onType() {
    const text = $("editor").value;
    $("word-count").textContent = plural(wordCount(text), "word");
    localSet(currentDraft());  // instant, survives a closed tab
    state.draftDirty = true;   // server copy follows on the next tick
    schedulePromptNudge();
  }

  // After 30 seconds on a blank page without typing, gently highlight
  // "Give me a prompt" so it's clear help is there. Typing clears it.
  const PROMPT_NUDGE_MS = 30000;
  let promptNudgeTimer = null;
  function schedulePromptNudge() {
    clearTimeout(promptNudgeTimer);
    $("prompt-btn").classList.remove("nudge-glow");
    promptNudgeTimer = setTimeout(() => {
      const blank = !$("editor").value.trim() && !$("title-input").value.trim();
      if (!blank || $("write-view").hidden || state.prompt || $("prompt-btn").disabled) return;
      if (document.hidden) { schedulePromptNudge(); return; }  // wait until they're looking
      $("prompt-btn").classList.add("nudge-glow");
    }, PROMPT_NUDGE_MS);
  }

  async function saveDraftToServer() {
    if (!state.draftDirty) return;
    state.draftDirty = false;
    try {
      await api("POST", "/api/draft", currentDraft());
      $("draft-state").textContent = $("editor").value.trim() ? "Draft saved" : "";
    } catch (_) {
      state.draftDirty = true;
      $("draft-state").textContent = "Draft kept in this browser";
    }
  }

  async function restoreDraft() {
    let server = {};
    try { server = await api("GET", "/api/draft"); } catch (_) {}
    const local = localGet();
    // Only the writing comes back: a prompt shows only when asked for with
    // "Give me a prompt", never on its own after a refresh.
    const pick = [server, local]
      .filter((d) => d && ((d.text && d.text.trim()) || (d.title && d.title.trim())))
      .sort((a, b) => (b.saved_at || "").localeCompare(a.saved_at || ""))[0];
    if (pick) {
      $("editor").value = pick.text || "";
      $("title-input").value = pick.title || "";
      $("draft-state").textContent = "Draft restored";
    }
    // Re-save the draft as it is now, so an old prompt doesn't linger in it.
    if (pick || [server, local].some((d) => d && d.prompt)) onType();
  }

  async function finish() {
    const text = $("editor").value;
    if (!text.trim()) { $("editor").focus(); return; }
    const btn = $("finish-btn");
    btn.disabled = true;
    btn.textContent = "Saving…";
    let before = null;  // the streak before this entry, to spot a milestone
    try { before = await api("GET", "/api/stats"); } catch (_) {}
    try {
      const res = await api("POST", "/api/entries", { ...currentDraft(), reflect: true });
      localClear();
      state.draftDirty = false;
      $("editor").value = "";
      $("title-input").value = "";
      state.prompt = null;
      state.promptsShown = [];
      renderPrompt();
      $("draft-state").textContent = "";
      onType();
      localClear();
      state.clientError = null;
      renderBanner();
      showEntry(res.entry);
      refreshList();
      checkMilestone(before, res.entry);
      requestReflection(res.entry.id);  // the entry is already safely saved
      loadBackground();                 // a new entry brings the next photo
    } catch (err) {
      state.clientError = `Couldn't save the entry: ${sentence(err.message)} ` +
        "Your text is still here and kept in this browser. Try again in a moment.";
      renderBanner();
    } finally {
      btn.disabled = false;
      btn.textContent = "Finish & reflect";
    }
  }

  async function saveEdit() {
    const text = $("entry-editor").value;
    if (!text.trim()) return;
    const btn = $("save-edit-btn");
    btn.disabled = true;
    try {
      const body = { text, title: $("entry-title-input").value };
      const was = localParts(state.current.created_at);
      const date = $("entry-date-input").value, time = $("entry-time-input").value;
      if (date && time && (date !== was.date || time !== was.time)) {
        body.created_at_local = `${date}T${time}`;  // only sent when you changed it
      }
      const res = await api("PUT", `/api/entries/${state.current.id}`, body);
      state.clientError = null;
      renderBanner();
      showEntry(res.entry);
      if (res.entry.reflection_status === "Done") {
        $("entry-status").textContent = "Saved. The reflection below is from before your edit; use Reflect again to update it.";
      }
      refreshList();
    } catch (err) {
      state.clientError = `Couldn't save your changes: ${sentence(err.message)} They're still in the editor.`;
      renderBanner();
    } finally {
      btn.disabled = false;
    }
  }

  // ---- Dialog -----------------------------------------------------------

  // Shows a modal and resolves with the chosen button's value (null if dismissed).
  function ask(title, paragraphs, buttons) {
    const dlg = $("dialog");
    $("dialog-title").textContent = title;
    $("dialog-body").replaceChildren(...paragraphs.map((t) => {
      const p = document.createElement("p"); p.textContent = t; return p;
    }));
    return new Promise((resolve) => {
      const row = $("dialog-buttons");
      row.replaceChildren();
      for (const b of buttons) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = b.label;
        if (b.style) btn.className = b.style;
        btn.addEventListener("click", () => { dlg.close(); resolve(b.value); });
        row.append(btn);
      }
      dlg.addEventListener("cancel", () => resolve(null), { once: true });
      dlg.showModal();
      row.firstChild.focus();  // Cancel is first, so Enter never deletes by accident
    });
  }

  // ---- Deleting ---------------------------------------------------------

  async function deleteCurrent() {
    const entry = state.current;
    if (!entry) return;
    const sure = await ask("Are you sure?",
      [`This removes the entry from ${longDate(entry.created_at)} from your journal.`],
      [{ label: "Cancel", value: null }, { label: "Yes", value: "yes", style: "primary" }]);
    if (sure !== "yes") return;

    const how = await ask("Hide the entry, or delete it permanently from the record?", [
      "Hide: it no longer appears in the app, but stays in your journal (and in exports). You can restore it from Settings.",
      "Delete permanently: it's removed from your journal and its backups. This can't be undone.",
    ], [
      { label: "Cancel", value: null },
      { label: "Hide", value: "hide", style: "primary" },
      { label: "Delete permanently", value: "delete", style: "danger" },
    ]);
    if (!how) return;

    try {
      const res = how === "hide"
        ? await api("POST", `/api/entries/${entry.id}/hide`)
        : await api("DELETE", `/api/entries/${entry.id}`);
      state.clientError = res.failed_backups && res.failed_backups.length
        ? `The entry was deleted, but couldn't be removed from these backups (are they open in another program?):${res.failed_backups.join(", ")}`
        : null;
      renderBanner();
      showWrite();
      refreshList();
    } catch (err) {
      state.clientError = `Couldn't ${how === "hide" ? "hide" : "delete"} the entry: ${err.message}`;
      renderBanner();
    }
  }

  // ---- Settings ---------------------------------------------------------

  async function showSettings() {
    showView("settings");
    $("settings-state").textContent = "";
    try {
      const { settings } = await api("GET", "/api/settings");
      const form = $("settings-form");
      for (const el of form.elements) {
        if (!el.name || !(el.name in settings)) continue;
        if (el.type === "checkbox") el.checked = !!settings[el.name];
        else if (el.type === "radio") el.checked = el.value === settings[el.name];
        else el.value = settings[el.name];
      }
      syncPhotoControls();
    } catch (err) {
      $("settings-state").textContent = `Couldn't load settings: ${err.message}`;
    }
    refreshHidden();
    refreshUsage();
    refreshFeedbackList();
  }

  async function saveSettings(ev) {
    ev.preventDefault();
    const out = {};
    for (const el of $("settings-form").elements) {
      if (!el.name) continue;
      if (el.type === "checkbox") out[el.name] = el.checked;
      else if (el.type === "radio") { if (el.checked) out[el.name] = el.value; }
      else if (el.type === "number" || el.type === "range") out[el.name] = Number(el.value);
      else out[el.name] = el.value;
    }
    try {
      const res = await api("PUT", "/api/settings", out);
      CFG.userName = res.settings.user_name;
      $("journal-name").textContent = res.settings.journal_name || CFG.appName;
      CFG.autosaveSeconds = res.settings.draft_autosave_seconds;
      savedLook.theme = res.settings.theme;
      savedLook.palette = res.settings.colour_theme;
      savedLook.blur = res.settings.photo_blur;
      savedLook.visibility = res.settings.photo_visibility;
      const photoWas = savedLook.photo;
      savedLook.photo = res.settings.background_photo ? "on" : "off";
      restoreSavedLook();
      if (savedLook.photo !== photoWas) loadBackground();  // only when switched on/off; Save alone keeps the photo
      restartAutosave();
      renderGreeting();
      leaveSettings();
      if (res.needs_restart.length) {
        showNotice("Settings saved. Restart the app (close the start.bat window and open it again) "
          + "for the time zone, port or browser setting to apply.", 9000);
      } else {
        showNotice("Settings saved.");
      }
    } catch (err) {
      // Stay on the page so nothing typed is lost.
      $("settings-state").textContent = sentence(err.message);
    }
  }

  // ---- Wiping the journal (guarded by a typed number) -------------------

  let wipeChallenge = null;

  async function newWipeChallenge() {
    wipeChallenge = await api("POST", "/api/wipe/challenge");
    $("wipe-number").textContent = wipeChallenge.number;
    $("wipe-answer").value = "";
    $("wipe-confirm-btn").disabled = true;
  }

  async function openWipe() {
    try {
      const s = await api("GET", "/api/stats");
      $("wipe-count").textContent = `${s.total_entries} ${s.total_entries === 1 ? "entry" : "entries"}`;
      await newWipeChallenge();
    } catch (err) {
      state.clientError = `Couldn't start the wipe: ${sentence(err.message)}`;
      renderBanner();
      return;
    }
    $("wipe-error").hidden = true;
    $("wipe-dialog").showModal();
    $("wipe-answer").focus();
  }

  async function confirmWipe(ev) {
    ev.preventDefault();
    if (!wipeChallenge) return;
    const btn = $("wipe-confirm-btn");
    btn.disabled = true;
    let res;
    try {
      res = await api("POST", "/api/wipe", { id: wipeChallenge.id, answer: $("wipe-answer").value });
    } catch (err) {
      // Wrong answer (or expired): nothing deleted. Offer a fresh number.
      $("wipe-error").textContent = `${sentence(err.message)} Here's a new number if you still want to go ahead.`;
      $("wipe-error").hidden = false;
      try { await newWipeChallenge(); } catch (_) { $("wipe-dialog").close(); }
      $("wipe-answer").focus();
      return;
    }
    wipeChallenge = null;
    $("wipe-dialog").close();
    // Start fresh on the writing page.
    localClear();
    state.draftDirty = false;
    $("editor").value = "";
    $("title-input").value = "";
    state.prompt = null;
    state.promptsShown = [];
    renderPrompt();
    onType();
    state.current = null;
    state.range = null;
    state.favOnly = false;
    applyFilters();
    showWrite();
    refreshList();
    loadBackground();
    const leftovers = res.failed_backups && res.failed_backups.length
      ? ` These backups couldn't be removed (are they open?): ${res.failed_backups.join(", ")}.` : "";
    showNotice(res.message + leftovers, 7000);
  }

  // Back to the journal: the entry being read, or the writing page.
  function leaveSettings() {
    if (state.current) showEntry(state.current);
    else showWrite();
  }

  async function refreshHidden() {
    const ul = $("hidden-list");
    try {
      const hidden = await api("GET", "/api/entries/hidden");
      ul.replaceChildren();
      for (const e of hidden.reverse()) {
        const li = document.createElement("li");
        const label = document.createElement("span");
        label.textContent = `${longDate(e.created_at)} · ${e.snippet.slice(0, 60)}${e.snippet.length > 60 ? "…" : ""}`;
        const btn = document.createElement("button");
        btn.textContent = "Restore";
        btn.addEventListener("click", async () => {
          try {
            await api("POST", `/api/entries/${e.id}/unhide`);
            refreshHidden();
            refreshList();
          } catch (err) {
            state.clientError = `Couldn't restore the entry: ${err.message}`; renderBanner();
          }
        });
        li.append(label, btn);
        ul.append(li);
      }
      if (!hidden.length) {
        const li = document.createElement("li");
        li.className = "subtle";
        li.textContent = "No hidden entries.";
        ul.append(li);
      }
    } catch (_) { /* status poll reports connectivity */ }
  }

  // ---- Wire up ----------------------------------------------------------

  let autosaveTimer = null;
  function restartAutosave() {
    clearInterval(autosaveTimer);
    autosaveTimer = setInterval(saveDraftToServer, Math.max(1, CFG.autosaveSeconds) * 1000);
  }

  renderGreeting();
  loadBackground();
  $("settings-btn").addEventListener("click", showSettings);
  $("settings-back-btn").addEventListener("click", () => {
    restoreSavedLook();  // Discard: undo any unsaved preview, save nothing
    leaveSettings();
  });
  // Preview look changes live (sliders update while dragging).
  const LOOK_FIELDS = ["theme", "colour_theme", "background_photo", "photo_blur", "photo_visibility"];
  for (const type of ["change", "input"]) {
    $("settings-form").addEventListener(type, (e) => {
      if (LOOK_FIELDS.includes(e.target.name)) previewLook();
    });
  }
  $("settings-form").addEventListener("submit", saveSettings);
  $("wipe-btn").addEventListener("click", openWipe);
  $("wipe-form").addEventListener("submit", confirmWipe);
  $("wipe-cancel-btn").addEventListener("click", () => { wipeChallenge = null; $("wipe-dialog").close(); });
  $("wipe-answer").addEventListener("input", () => {
    $("wipe-confirm-btn").disabled = !$("wipe-answer").value.trim();
  });
  $("delete-btn").addEventListener("click", deleteCurrent);
  $("reflect-btn").addEventListener("click", () => state.current && requestReflection(state.current.id));
  $("reflect-again-btn").addEventListener("click", () => state.current && requestReflection(state.current.id));
  if ($("test-key-btn")) $("test-key-btn").addEventListener("click", async () => {
    const btn = $("test-key-btn"), out = $("test-key-result");
    btn.disabled = true;
    out.textContent = "Testing…";
    try {
      out.textContent = (await api("POST", "/api/test-key")).message;
    } catch (err) {
      out.textContent = `Couldn't run the test: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });

  // ---- Feedback -----------------------------------------------------------

  const FEEDBACK_HINTS = {
    fix: "What happened, or what could be better? Where in the app?",
    feature: "What would you like it to do, and how would it help?",
  };
  const feedbackKind = () => document.querySelector('input[name="feedback-kind"]:checked').value;
  const currentScreen = () => ["write", "entry", "settings"].find((n) => !$(`${n}-view`).hidden) || "";

  function openFeedback() {
    $("feedback-error").hidden = true;
    $("feedback-text").placeholder = FEEDBACK_HINTS[feedbackKind()];
    $("feedback-dialog").showModal();
    $("feedback-text").focus();
  }

  async function sendFeedback(ev) {
    ev.preventDefault();
    const btn = $("feedback-send-btn");
    btn.disabled = true;
    $("feedback-error").hidden = true;
    try {
      await api("POST", "/api/feedback", { kind: feedbackKind(), text: $("feedback-text").value, screen: currentScreen() });
      $("feedback-text").value = "";  // kept if sending fails, or on Cancel
      $("feedback-dialog").close();
      showNotice("Thank you! Your feedback has been sent.", 5000);
      if (!$("settings-view").hidden) refreshFeedbackList();
    } catch (err) {
      $("feedback-error").textContent = sentence(err.message);
      $("feedback-error").hidden = false;
    } finally {
      btn.disabled = false;
    }
  }

  $("feedback-btn").addEventListener("click", openFeedback);
  $("feedback-form").addEventListener("submit", sendFeedback);
  $("feedback-cancel-btn").addEventListener("click", () => $("feedback-dialog").close());
  for (const r of document.querySelectorAll('input[name="feedback-kind"]')) {
    r.addEventListener("change", () => { $("feedback-text").placeholder = FEEDBACK_HINTS[feedbackKind()]; });
  }

  // Settings → Feedback received (only on the page for whoever reads feedback).
  async function refreshFeedbackList() {
    const ul = $("feedback-list");
    if (!ul) return;
    let items;
    try { items = await api("GET", "/api/feedback"); } catch (_) { return; }
    const showDone = $("feedback-show-done").checked;
    const shown = items.filter((f) => showDone || !f.done);
    ul.replaceChildren();
    for (const f of shown) {
      const li = document.createElement("li");
      li.className = f.done ? "done" : "";
      const meta = document.createElement("div");
      meta.className = "feedback-meta";
      const who = f.email || f.name || "You";
      meta.textContent = `${longDate(f.created_at)}, ${timeOf(f.created_at)} · ${who} · ${f.kind_label}`
        + (f.screen ? ` · on the ${f.screen} page` : "");
      const text = document.createElement("p");
      text.className = "feedback-body";
      text.textContent = f.text;
      const done = document.createElement("label");
      done.className = "checkbox";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = f.done;
      box.addEventListener("change", async () => {
        try {
          await api("POST", `/api/feedback/${f.id}/done`, { done: box.checked });
          refreshFeedbackList();
        } catch (err) {
          box.checked = !box.checked;
          state.clientError = `Couldn't update that: ${sentence(err.message)}`; renderBanner();
        }
      });
      done.append(box, document.createTextNode(" Done"));
      li.append(meta, text, done);
      ul.append(li);
    }
    if (!shown.length) {
      const li = document.createElement("li");
      li.className = "subtle";
      li.textContent = items.length ? "Everything is marked done." : "No feedback yet.";
      ul.append(li);
    }
  }
  if ($("feedback-show-done")) $("feedback-show-done").addEventListener("change", refreshFeedbackList);

  // Settings → Usage (only on the page for whoever runs the app).
  function daysAgo(n) {
    if (n == null) return "never";
    return n === 0 ? "today" : n === 1 ? "yesterday" : `${n} days ago`;
  }

  async function refreshUsage() {
    if (!$("usage-tiles")) return;
    let u;
    try { u = await api("GET", "/api/admin/usage"); } catch (_) { return; }
    const tile = (label, value, note) => {
      const d = document.createElement("div");
      d.className = "usage-tile";
      const v = document.createElement("div"); v.className = "usage-value"; v.textContent = value;
      const l = document.createElement("div"); l.className = "usage-label"; l.textContent = label;
      d.append(v, l);
      if (note) { const n = document.createElement("div"); n.className = "usage-note"; n.textContent = note; d.append(n); }
      return d;
    };
    $("usage-tiles").replaceChildren(
      tile("People", u.people, u.invited != null ? `${u.set_up} set up · ${u.invited} invited` : `${u.set_up} set up`),
      tile("Active today", u.active_today),
      tile("This week", u.active_week, "last 7 days"),
      tile("This month", u.active_month, "last 30 days"),
      tile("Coming back", u.returning, "on 2+ different days"),
      tile("Days active", u.avg_active_days, "average per active person"),
      tile("Entries", u.entries_recent, "last 30 days"),
      tile("Reflections", u.reflections_recent, "last 30 days"),
    );
    $("usage-since").textContent = u.tracking_since
      ? `Visits counted since ${longDate(u.tracking_since + "T12:00:00")}.` : "No visits counted yet.";

    // People active each day: one bar per day.
    const max = Math.max(1, ...u.series.map((d) => d.users));
    const chart = $("usage-chart");
    chart.replaceChildren();
    for (const d of u.series) {
      const bar = document.createElement("div");
      bar.className = "usage-bar" + (d.day === u.today ? " today" : "");
      bar.style.height = `${Math.max(d.users ? 8 : 2, (d.users / max) * 100)}%`;
      bar.title = `${longDate(d.day + "T12:00:00")}: ${plural(d.users, "person").replace("persons", "people")}`;
      chart.append(bar);
    }

    // One row per person.
    const table = $("usage-table");
    table.replaceChildren();
    const head = table.createTHead().insertRow();
    for (const h of ["Person", "Last visit", "Days active", "Entries (all)", "Reflections", "Joined"]) {
      const th = document.createElement("th"); th.textContent = h; head.append(th);
    }
    const body = table.createTBody();
    for (const r of u.rows) {
      const tr = body.insertRow();
      const cells = [
        r.who.replace("@", "@​") + (r.set_up ? "" : " (not set up)"),  // long emails break after the @
        daysAgo(r.days_since),
        String(r.active_days),
        `${r.entries_recent} (${r.entries_total})`,
        String(r.reflections_recent),
        r.joined,
      ];
      for (const c of cells) tr.insertCell().textContent = c;
    }
  }

  // Import entries from a CSV made with Export.
  $("import-btn").addEventListener("click", () => $("import-file").click());
  $("import-file").addEventListener("change", async () => {
    const file = $("import-file").files[0];
    if (!file) return;
    const out = $("import-result");
    out.textContent = "Importing…";
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch("/api/import", { method: "POST", headers: { "X-Journal-Client": "1" }, body: form });
      let data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
      out.textContent = `Imported ${plural(data.imported, "entry").replace("entrys", "entries")}`
        + (data.skipped ? `; ${data.skipped} already in the journal were skipped.` : ".");
      refreshList();
      refreshHidden();
    } catch (err) {
      out.textContent = `Couldn't import: ${sentence(err.message)}`;
    } finally {
      $("import-file").value = "";
    }
  });

  $("today").textContent = longDate(new Date().toISOString());
  $("editor").addEventListener("input", onType);
  $("title-input").addEventListener("input", onType);
  $("star-btn").addEventListener("click", toggleFavourite);
  $("prompt-btn").addEventListener("click", getPrompt);
  $("another-prompt-btn").addEventListener("click", getPrompt);
  $("dismiss-prompt-btn").addEventListener("click", dismissPrompt);
  $("filter-all").addEventListener("click", showAll);
  $("filter-fav").addEventListener("click", toggleFavourites);
  applyFilters();
  $("prev-btn").addEventListener("click", goOlder);
  $("next-btn").addEventListener("click", goNewer);
  $("calendar-btn").addEventListener("click", () => toggleCalendar());
  $("cal-prev").addEventListener("click", () => shiftMonth(-1));
  $("cal-next").addEventListener("click", () => shiftMonth(1));
  $("cal-label").addEventListener("click", pickWholeMonth);
  $("cal-month-btn").addEventListener("click", pickWholeMonth);
  $("cal-clear-btn").addEventListener("click", () => { setRange(null); toggleCalendar(false); });
  toggleCalendar(false);
  document.addEventListener("mousedown", (e) => {
    if (state.cal.open && !$("calendar").contains(e.target) && !$("calendar-btn").contains(e.target)) {
      toggleCalendar(false);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.cal.open) {
      toggleCalendar(false);
      $("calendar-btn").focus();
    }
  });
  document.addEventListener("keydown", (e) => {
    // ← / → page through entries while reading (not while typing or in a dialog).
    if ($("entry-view").hidden || !$("entry-editor").hidden || $("dialog").open) return;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); goOlder(); }
    if (e.key === "ArrowRight") { e.preventDefault(); goNewer(); }
  });
  $("title-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); $("editor").focus(); }
  });
  $("finish-btn").addEventListener("click", finish);
  $("new-btn").addEventListener("click", showWrite);
  $("edit-btn").addEventListener("click", () => setEditing(true));
  $("cancel-edit-btn").addEventListener("click", () => setEditing(false));
  $("save-edit-btn").addEventListener("click", saveEdit);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !$("write-view").hidden) finish();
  });
  window.addEventListener("pagehide", () => {
    if (state.draftDirty && $("editor").value.trim()) {
      // keepalive lets the request finish after the tab closes
      fetch("/api/draft", {
        method: "POST",
        keepalive: true,
        headers: { "X-Journal-Client": "1", "Content-Type": "application/json" },
        body: JSON.stringify(currentDraft()),
      }).catch(() => {});
    }
  });

  // ---- The lock screen: set up a password, unlock, or recover -----------

  // The heading on each lock-screen card.
  const VAULT_TITLES = {
    signin: "Welcome",
    setup: "Secret Passphrase",
    recovery: "Your Recovery Key",
    unlock: "Secret Passphrase",
    recover: "Forgot Your Passphrase?",
  };

  function showVaultPanel(name) {
    $("vault").hidden = false;
    $("book").hidden = true;
    for (const p of ["signin", "setup", "recovery", "unlock", "recover"]) {
      if ($(`vault-${p}`)) $(`vault-${p}`).hidden = p !== name;
    }
    if ($("vault-account")) $("vault-account").hidden = name === "signin";
    // Signed out on the web: the welcome page, with what the app is on the left.
    const welcome = name === "signin";
    $("vault").classList.toggle("landing", welcome);
    if ($("landing")) $("landing").hidden = !welcome;
    $("vault-title").textContent = VAULT_TITLES[name] || CFG.appName;
    for (const el of document.querySelectorAll(".vault-error")) el.hidden = true;
    const first = $(`vault-${name}`).querySelector("input");
    if (first) first.focus();
  }

  function vaultError(id, msg) {
    $(id).textContent = sentence(msg);
    $(id).hidden = false;
  }

  async function vaultSubmit(ev, formId, errorId, run) {
    ev.preventDefault();
    const btn = $(formId).querySelector("button[type=submit]");
    btn.disabled = true;
    $(errorId).hidden = true;
    try {
      await run();
    } catch (err) {
      vaultError(errorId, err.message);
    } finally {
      btn.disabled = false;
    }
  }

  function showRecoveryKey(res) {
    $("recovery-key").textContent = res.recovery_key;
    const notes = [];
    if (res.imported) notes.push(`Your ${res.imported} existing ${res.imported === 1 ? "entry has" : "entries have"} been encrypted.`);
    if (res.import_problem) notes.push(res.import_problem);
    if (res.cleanup_failed && res.cleanup_failed.length) {
      notes.push("Some old unencrypted files couldn't be deleted yet (probably open in another program); "
        + "the app will keep trying.");
    } else if (res.imported) {
      notes.push("The old unencrypted copies have been deleted.");
    }
    $("recovery-import-note").textContent = notes.join(" ");
    $("recovery-saved").checked = false;
    $("recovery-done").disabled = true;
    showVaultPanel("recovery");
  }

  $("vault-setup").addEventListener("submit", (ev) => vaultSubmit(ev, "vault-setup", "setup-error", async () => {
    if ($("setup-pw").value !== $("setup-pw2").value) throw new Error("The two passphrases don't match");
    showRecoveryKey(await api("POST", "/api/vault/setup", { password: $("setup-pw").value }));
    $("setup-pw").value = $("setup-pw2").value = "";
  }));
  $("vault-unlock").addEventListener("submit", (ev) => vaultSubmit(ev, "vault-unlock", "unlock-error", async () => {
    const res = await api("POST", "/api/vault/unlock", { password: $("unlock-pw").value });
    $("unlock-pw").value = "";
    startJournal();
    if (res.imported) showNotice(`Your ${res.imported} older entries have been encrypted and imported.`, 7000);
  }));
  $("vault-recover").addEventListener("submit", (ev) => vaultSubmit(ev, "vault-recover", "recover-error", async () => {
    if ($("recover-pw").value !== $("recover-pw2").value) throw new Error("The two passphrases don't match");
    await api("POST", "/api/vault/recover", { recovery_key: $("recover-key").value, new_password: $("recover-pw").value });
    $("recover-key").value = $("recover-pw").value = $("recover-pw2").value = "";
    startJournal();
    showNotice("Your new passphrase is set. Your recovery key still works too.", 7000);
  }));
  $("show-recover").addEventListener("click", () => showVaultPanel("recover"));
  $("back-to-unlock").addEventListener("click", () => showVaultPanel("unlock"));
  $("recovery-saved").addEventListener("change", () => { $("recovery-done").disabled = !$("recovery-saved").checked; });
  $("copy-recovery").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("recovery-key").textContent);
      $("copy-recovery").textContent = "Copied";
    } catch (_) {
      $("copy-recovery").textContent = "Select and copy it by hand";
    }
  });
  $("recovery-done").addEventListener("click", () => startJournal());
  $("lock-btn").addEventListener("click", async () => {
    try { await api("POST", "/api/vault/lock"); } catch (_) {}
    state.unlocked = false;
    location.reload();
  });

  // ---- Password settings ------------------------------------------------

  $("password-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const out = $("password-result");
    if ($("pw-new").value !== $("pw-new2").value) { out.textContent = "The two new passphrases don't match."; return; }
    try {
      await api("POST", "/api/vault/password", { current: $("pw-current").value, new: $("pw-new").value });
      $("pw-current").value = $("pw-new").value = $("pw-new2").value = "";
      out.textContent = "Passphrase changed.";
    } catch (err) {
      out.textContent = sentence(err.message);
    }
  });
  $("new-recovery-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const res = await api("POST", "/api/vault/recovery-key", { password: $("recovery-pw").value });
      $("recovery-pw").value = "";
      $("new-recovery-key").textContent = res.recovery_key;
      $("new-recovery-box").hidden = false;
      $("new-recovery-result").textContent = "";
    } catch (err) {
      $("new-recovery-result").textContent = sentence(err.message);
    }
  });

  // ---- Starting up --------------------------------------------------------

  let started = false;
  function startJournal() {
    state.unlocked = true;
    $("vault").hidden = true;
    $("book").hidden = false;
    if (started) return;
    started = true;
    restartAutosave();
    setInterval(pollStatus, 10000);
    // Keep the date and streak right if the page stays open past midnight.
    setInterval(() => {
      $("today").textContent = longDate(new Date().toISOString());
      refreshStats({ passive: true });
      renderCalendar();
    }, 60000);
    restoreDraft();
    refreshList();
    pollStatus();
    $("editor").focus();
    schedulePromptNudge();
  }

  (async function boot() {
    let v;
    try {
      v = await api("GET", "/api/vault");
    } catch (_) {
      $("vault").hidden = false;
      $("vault-offline").hidden = false;
      return;
    }
    if (v.mode === "hosted") {
      if (!v.signed_in) {
        const why = new URLSearchParams(location.search).get("signin");
        if (why) {
          vaultError("signin-error", why === "not-allowed"
            ? "That Google account isn't on the list for this journal. Ask whoever invited you to add it"
            : "Signing in with Google didn't work. Please try again");
          history.replaceState(null, "", "/");
        }
        showVaultPanel("signin");
        if (why) $("signin-error").hidden = false;
        return;
      }
      LOCAL_DRAFT_KEY = `journal.draft.${v.email}`;
      $("vault-email").textContent = v.email;
      $("account-email").textContent = v.email;
      if (location.search) history.replaceState(null, "", "/");
    }
    if (v.unlocked) return startJournal();
    if (!v.set_up) {
      $("legacy-note").hidden = !v.legacy_entries;
      $("legacy-note").textContent = v.legacy_entries
        ? `Your ${v.legacy_entries} existing ${v.legacy_entries === 1 ? "entry" : "entries"} will be encrypted, `
          + "and the old unencrypted copies (including journal.xlsx and its backups) deleted."
        : "";
      return showVaultPanel("setup");
    }
    showVaultPanel("unlock");
  })();
})();
