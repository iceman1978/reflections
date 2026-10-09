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
  // "9:30 am" rather than "9:30 AM": capitals look odd in the handwriting and text.
  const timeOf = (iso) => fmtDate(iso, { hour: "numeric", minute: "2-digit" }).replace(/\b(AM|PM)\b/, (m) => m.toLowerCase());

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

  // ---- Page turns (Older / Newer) ----------------------------------------
  // Like a book: the spine is on the left. Older: the previous page swings
  // down from the left onto the page you were reading. Newer: the page you
  // were reading lifts from its right edge and turns over to the left,
  // uncovering the next one. A copy of the old page plays its part.
  const reducedMotion = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  function oldPageCopy(el) {
    if (el.hidden || reducedMotion()) return null;
    const copy = el.cloneNode(true);
    copy.removeAttribute("id");
    copy.querySelectorAll("[id]").forEach((n) => n.removeAttribute("id"));
    copy.classList.remove("view-in", "turn-older", "turn-newer");
    copy.classList.add("page-ghost");
    copy.setAttribute("aria-hidden", "true");
    copy.inert = true;
    Object.assign(copy.style, {
      left: `${el.offsetLeft}px`, top: `${el.offsetTop}px`,
      width: `${el.offsetWidth}px`, height: `${el.offsetHeight}px`,
    });
    return copy;
  }

  let turnCount = 0;   // so a finished turn's clean-up never touches a newer turn

  function turnPage(el, direction, ghost) {
    const thisTurn = ++turnCount;
    document.querySelectorAll(".page-ghost").forEach((g) => g.remove());   // a quick second turn
    if (!ghost) { animatePage(el, "view-in"); return; }
    ghost.classList.add(direction < 0 ? "ghost-under" : "ghost-turning");
    ghost.style.height = `${el.offsetHeight}px`;   // pages in a book are one size: match the new page
    el.after(ghost);
    el.classList.remove("view-in", "turn-older", "turn-newer");
    if (direction < 0) animatePage(el, "turn-older");          // the new page comes down over it
    // When the turn ends: drop the old page's copy, and give the new page back
    // its stack of pages and cover (hidden only while it was turning).
    const turning = direction < 0 ? el : ghost;
    const done = () => {
      ghost.remove();
      if (thisTurn === turnCount) el.classList.remove("turn-older");
      turning.removeEventListener("animationend", onEnd);
    };
    const onEnd = (e) => { if (e.target === turning) done(); };   // not the page's inner animations
    turning.addEventListener("animationend", onEnd);
    setTimeout(done, 1200);                                     // in case no animation runs
  }

  function showEntry(entry) {
    const switching = !state.current || state.current.id !== entry.id;
    const from = !$("entry-view").hidden ? $("entry-view") : $("write-view");
    const ghost = state.turn ? oldPageCopy(from) : null;   // the page as it was, before it changes
    state.current = entry;
    showView("entry");
    if (state.turn) {
      turnPage($("entry-view"), state.turn, ghost);
      state.turn = null;
    } else if (switching) {
      animatePage($("entry-view"), "view-in");
    }
    $("entry-date").textContent = `${longDate(entry.created_at)} · ${timeOf(entry.created_at)}`;
    showEntryWords(entry);
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
    $("prev-btn").classList.toggle("at-end", !older);
    $("prev-btn").title = older ? "Older entry (← key)" : "This is your first entry";
    $("next-btn").textContent = newer ? "Newer ›" : "New entry ›";
    $("next-btn").title = newer ? "Newer entry (→ key)" : "Start a new entry (→ key)";
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
    if (!target) {
      if (direction < 0) bumpPage($("entry-view"));
      else turnToWritingPage();
      return;
    }
    state.navTarget = target.id;
    state.turn = direction;  // showEntry plays a page turn in this direction
    openEntry(target.id);
  }
  function goOlder() { step(-1); }
  function goNewer() { step(1); }

  // Past the latest entry: turn the page over to a fresh writing page.
  async function turnToWritingPage() {
    const ghost = oldPageCopy($("entry-view"));
    await scrollToTop();
    showWrite();
    turnPage($("write-view"), 1, ghost);
  }

  // Older from the writing page: back to the latest entry, but only if
  // nothing has been written yet (a started entry is never turned away).
  function olderFromWritingPage() {
    if ($("editor").value.trim() || $("title-input").value.trim()) {
      bumpPage($("write-view"));
      showNotice("Finish or clear this entry before turning back.", 3500);
      return;
    }
    const latest = shown().slice(-1)[0];
    if (!latest) { bumpPage($("write-view")); return; }
    state.navTarget = latest.id;
    state.turn = -1;
    openEntry(latest.id);
  }

  // "There's nothing further this way": the page nudges as if to turn, and settles.
  function bumpPage(el) {
    if (reducedMotion()) return;
    el.classList.remove("view-in", "turn-older", "page-bump");
    void el.offsetWidth;
    el.classList.add("page-bump");
    el.addEventListener("animationend", () => el.classList.remove("page-bump"), { once: true });
  }

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
    maybeInviteInstall(s);
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
    const cm = state.countMilestone;
    if (cm && Date.now() < cm.until) {
      const badge = document.createElement("span");
      badge.className = "streak-badge milestone celebrate";
      if (!cm.popped) { badge.classList.add("pop"); cm.popped = true; }   // animate once
      badge.textContent = `✦ Your ${cm.n}th entry`;   // 5th, 10th, 25th, 50th, 100th… all take "th"
      box.append(document.createTextNode(" "), badge);
    }
    const r = state.record;
    if (r && Date.now() < r.until) {
      const rec = document.createElement("span");
      rec.className = "streak-badge milestone celebrate record-badge";
      if (!r.popped) { rec.classList.add("pop"); r.popped = true; }   // animate once
      rec.textContent = `✦ Your longest entry yet · ${plural(r.words, "word")}`;
      box.append(document.createTextNode(" "), rec);
    }
    if (s.longest_streak > n && s.longest_streak > 1) {
      box.append(document.createTextNode(` · best streak ${plural(s.longest_streak, "day")}`));
    }
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

  // Entry-count milestones: 5, 10, 25, 50, then every 50 (100, 150, 200…).
  const isCountMilestone = (n) => n === 5 || n === 10 || n === 25 || (n >= 50 && n % 50 === 0);

  // After Finish & reflect: celebrate whatever this entry achieved (a streak
  // milestone, a new longest entry, an entry-count milestone, or several).
  async function checkMilestone(before, entry) {
    const after = await refreshStats();
    if (!before || !after) return;
    const until = Date.now() + 8000;
    let any = false;
    if (entry && before.shown_entries >= RECORD_FROM && entry.word_count > before.longest_words) {
      state.record = { words: entry.word_count, until };
      any = true;
      if (state.current && state.current.id === entry.id) showEntryWords(state.current);
    }
    const n = after.current_streak;
    if (n > before.current_streak && isMilestone(n)) {
      state.celebrating = { n, until };
      any = true;
    }
    const count = after.total_entries;
    if (count > before.total_entries && isCountMilestone(count)) {
      state.countMilestone = { n: count, until };
      any = true;
    }
    if (any) {
      renderStats(after);
      setTimeout(() => { if (state.lastStats) renderStats(state.lastStats); }, 8100);  // settle back afterwards
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
        // Books of the Bible have no author: "— Romans 8:28".
        li.append(document.createTextNode(`— ${q.author ? `${q.author}, ` : ""}${q.title}${q.location ? ` ${q.location}` : ""} `));
        if (q.kind === "idea") {
          const tag = document.createElement("span");
          tag.className = "cite-kind";
          tag.textContent = "(idea) ";
          tag.title = q.idea ? `Paraphrased: ${q.idea}` : "An idea from this work, paraphrased";
          li.append(tag);
        }
        if (typeof q.url === "string" && q.url.startsWith("https://")) {
          const a = document.createElement("a");
          a.href = q.kind === "quote" ? passageUrl(q.url, q.quote) : q.url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = q.kind === "quote" ? "Read the passage ↗" : "Read the source ↗";
          a.title = `${q.translation}, on ${siteName(q.url)}`;
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

  // A link that opens the source page scrolled to the quote, with it
  // highlighted: a "text fragment" (#:~:text=first words,last words), which
  // Chrome, Edge, Safari and Firefox understand. Quotes are stored in the
  // source's exact wording, so the words match the page. If a browser can't
  // find them, it simply opens the page at the top.
  function siteName(url) {
    const host = new URL(url).hostname.replace(/^www\./, "");
    return { "en.wikisource.org": "Wikisource", "ccel.org": "the Christian Classics Ethereal Library",
             "gutenberg.org": "Project Gutenberg", "biblehub.com": "Bible Hub" }[host] || host;
  }

  function passageUrl(url, quote) {
    const words = String(quote || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return url;
    words[0] = words[0].replace(/^[^\p{L}\p{N}]+/u, "");                      // no leading … or “
    words[words.length - 1] = words[words.length - 1].replace(/[^\p{L}\p{N}]+$/u, "");
    const part = (w) => encodeURIComponent(w.filter(Boolean).join(" ")).replace(/-/g, "%2D");
    const fragment = words.length <= 8 ? part(words) : `${part(words.slice(0, 4))},${part(words.slice(-4))}`;
    return `${url}${url.includes("#") ? ":~:text=" : "#:~:text="}${fragment}`;
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
    // The handwriting's capitals are ornate, so all-capital words (AI, CEO, NYC)
    // are hard to read in it: such titles use the book font instead.
    h.classList.toggle("plain-title", /(^|[^\p{L}])\p{Lu}{2,}(?![\p{L}])/u.test(entry.title || ""));
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
    state.listLimit = LIST_FIRST;
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

  // The list shows the newest 10, and 20 more each time you scroll to its end
  // (or click "Show more"). Older / Newer, swipes and the calendar still reach
  // every entry.
  const LIST_FIRST = 10, LIST_MORE = 20;
  state.listLimit = LIST_FIRST;
  let moreObserver = null;

  function showMoreEntries() {
    state.listLimit += LIST_MORE;
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
      const newestFirst = entries.reverse();
      for (const e of newestFirst.slice(0, state.listLimit)) {
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
      if (moreObserver) { moreObserver.disconnect(); moreObserver = null; }
      const hidden = newestFirst.length - Math.min(state.listLimit, newestFirst.length);
      if (hidden > 0) {
        const li = document.createElement("li");
        li.className = "list-more";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = `Show ${Math.min(LIST_MORE, hidden)} more`;
        btn.title = `${hidden} older ${hidden === 1 ? "entry" : "entries"} not shown yet`;
        btn.addEventListener("click", (ev) => { ev.stopPropagation(); showMoreEntries(); });
        const count = document.createElement("span");
        count.className = "subtle";
        count.textContent = `${hidden} older`;
        li.append(btn, count);
        ul.append(li);
        // Load more by itself when the end of the list scrolls into view.
        if ("IntersectionObserver" in window) {
          moreObserver = new IntersectionObserver((seen) => {
            if (seen.some((s) => s.isIntersecting) && window.scrollY > 0) showMoreEntries();
          }, { rootMargin: "200px" });
          moreObserver.observe(li);
        }
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

  // Settings tabs: Basic, Theme, Security & Data, Admin (admins only).
  const settingsTabs = () => [...document.querySelectorAll('#settings-view [role="tab"]')];

  function selectSettingsTab(name, { focus = false } = {}) {
    for (const tab of settingsTabs()) {
      const on = tab.id === `tab-${name}`;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
      $(tab.getAttribute("aria-controls")).hidden = !on;
      if (on && focus) tab.focus();
    }
    state.settingsTab = name;
  }

  for (const tab of settingsTabs()) {
    tab.addEventListener("click", () => selectSettingsTab(tab.id.slice(4)));
    tab.addEventListener("keydown", (e) => {   // ← / → move between tabs
      const tabs = settingsTabs(), i = tabs.indexOf(tab);
      const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      selectSettingsTab(tabs[(i + step + tabs.length) % tabs.length].id.slice(4), { focus: true });
    });
  }

  async function showSettings() {
    showView("settings");
    selectSettingsTab(state.settingsTab || "basic");   // back to the tab you were on
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
    refreshPasskeys();
    refreshUsage();
    refreshInviteRequests();
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

  // ---- Install as an app ----------------------------------------------------
  // Chrome and Edge (computers, Android) let the page offer their install
  // dialog; on iPhone/iPad the only way is Share → Add to Home Screen, so we
  // show those two steps. Nothing is offered where neither works, or once the
  // app is installed.

  const INSTALL_INVITE_AFTER = 3;                       // entries written
  const INSTALL_SNOOZE_MS = 30 * 24 * 3600 * 1000;      // "Not now" = a month
  const INSTALL_SNOOZE_KEY = "journal.installInviteSnoozed";
  let installPrompt = null;                              // the browser's install dialog, when offered

  const isInstalled = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);   // iPadOS presents as a Mac
  function installMode() {
    if (isInstalled()) return "installed";
    if (installPrompt) return "prompt";
    if (isIOS()) return "ios";
    return "none";
  }

  function renderInstall() {
    const mode = installMode();
    $("install-section").hidden = mode === "installed" || mode === "none";
    $("install-btn").hidden = mode !== "prompt";
    $("install-ios").hidden = mode !== "ios";
    if ((mode === "installed" || mode === "none") && activeNudge && activeNudge.nudge.id === "install") {
      $("install-invite").hidden = true;
    }
  }

  async function installApp() {
    if (!installPrompt) return;
    installPrompt.prompt();
    const { outcome } = await installPrompt.userChoice;
    installPrompt = null;   // the browser offers it only once per page
    if (outcome === "accepted") $("install-invite").hidden = true;
    renderInstall();
  }

  // ---- Suggestion cards ----------------------------------------------------
  // As entries add up, one gentle card at a time (and at most one per visit):
  //   3rd entry      install as an app ("Not now": a month)
  //   5th entry      send feedback, if they never have (once)
  //   10th/20th/40th invite someone, if they never have (each "Not now" waits
  //                  for the next of these, then it stops)
  const INVITE_AT = [10, 20, 40];
  const esc = (text) => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const remember = (key, value) => { try { localStorage.setItem(key, String(value)); } catch (_) {} };
  const recall = (key) => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  let nudgeShownThisVisit = false;

  const NUDGES = [
    {
      id: "install",
      ready: (s) => {
        const mode = installMode();
        return s.total_entries >= INSTALL_INVITE_AFTER && mode !== "installed" && mode !== "none"
          && Date.now() - (Number(recall(INSTALL_SNOOZE_KEY)) || 0) >= INSTALL_SNOOZE_MS;
      },
      text: () => `<strong>Writing here most days?</strong> Install ${esc(CFG.appName)} as an app, and open it straight from your home screen or desktop.`,
      go: () => (installMode() === "ios" ? "Show me how" : "Install"),
      onGo: () => {
        if (installMode() === "ios") { $("invite-ios").hidden = false; $("invite-install-btn").hidden = true; return false; }
        installApp();
        return true;
      },
      later: () => remember(INSTALL_SNOOZE_KEY, Date.now()),
    },
    {
      id: "feedback",
      ready: (s) => CFG.hosted && s.total_entries >= 5 && !s.feedback_sent && !recall("journal.nudge.feedback"),
      text: () => `<strong>How's ${esc(CFG.appName)} working for you?</strong> It's still in beta, and your ideas shape it. `
        + "Tell us what to fix, improve or add, any time, with the speech bubble at the top.",
      go: () => "Send feedback",
      onGo: () => { remember("journal.nudge.feedback", 1); openFeedback(); return true; },
      later: () => remember("journal.nudge.feedback", 1),
    },
    {
      id: "invite",
      ready: (s) => CFG.hosted && !s.invites_sent && INVITE_AT.some((n) => s.total_entries >= n && n > (Number(recall("journal.nudge.invite")) || 0)),
      text: () => `<strong>Know someone who'd enjoy this?</strong> ${esc(CFG.appName)} is invitation-only while it's in beta, `
        + `and grows through people like you. ${esc(CFG.inviteLine)}, any time, with the gift at the top.`,
      go: () => "Invite someone",
      // Opening the form answers this milestone; if no invitation is actually sent,
      // the card comes back at the next one (sent invitations come from the server).
      onGo: (s) => { remember("journal.nudge.invite", Math.max(...INVITE_AT.filter((n) => s.total_entries >= n))); openInvite(); return true; },
      later: (s) => remember("journal.nudge.invite", Math.max(...INVITE_AT.filter((n) => s.total_entries >= n))),
    },
  ];
  let activeNudge = null;

  function maybeInviteInstall(stats) {   // (name kept: called wherever the entry count updates)
    if (!stats || nudgeShownThisVisit || !$("install-invite").hidden) return;
    const nudge = NUDGES.find((n) => n.ready(stats));
    if (!nudge) return;
    activeNudge = { nudge, stats };
    nudgeShownThisVisit = true;
    $("nudge-text").innerHTML = nudge.text();
    $("invite-ios").hidden = true;
    $("invite-install-btn").hidden = false;
    $("invite-install-btn").textContent = nudge.go();
    $("install-invite").hidden = false;
  }

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();          // we show our own button instead of the browser's mini bar
    installPrompt = e;
    renderInstall();
    if (state.lastStats) maybeInviteInstall(state.lastStats);
  });
  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    $("install-invite").hidden = true;
    renderInstall();
    showNotice(`Installed. You'll find ${CFG.appName} with your other apps.`, 5000);
  });
  $("install-btn").addEventListener("click", installApp);
  $("invite-install-btn").addEventListener("click", () => {
    if (!activeNudge) return;
    if (activeNudge.nudge.onGo(activeNudge.stats) !== false) $("install-invite").hidden = true;
  });
  $("invite-later-btn").addEventListener("click", () => {
    if (activeNudge) activeNudge.nudge.later(activeNudge.stats);
    $("install-invite").hidden = true;
  });
  renderInstall();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

  // ---- Invitations (web version) ------------------------------------------

  const INVITE_STATUS = { requested: "Requested", added: "Has access", declined: "Declined" };

  function renderMyInvites(list) {
    $("my-invites-box").hidden = !list.length;
    $("my-invites").replaceChildren(...list.map((i) => {
      const li = document.createElement("li");
      const who = document.createElement("span");
      who.textContent = i.name ? `${i.name} (${i.email})` : i.email;
      const status = document.createElement("span");
      status.className = `invite-status ${i.status}`;
      status.textContent = INVITE_STATUS[i.status] || i.status;
      li.append(who, status);
      return li;
    }));
  }

  async function openInvite() {
    $("invite-error").hidden = true;
    $("invite-dialog").showModal();
    $("invite-email").focus();
    try { renderMyInvites(await api("GET", "/api/invites")); } catch (_) {}
  }

  async function sendInvite(ev) {
    ev.preventDefault();
    const btn = $("invite-send-btn");
    btn.disabled = true;
    $("invite-error").hidden = true;
    try {
      const res = await api("POST", "/api/invites", {
        email: $("invite-email").value, name: $("invite-name").value, note: $("invite-note").value,
      });
      $("invite-email").value = $("invite-name").value = $("invite-note").value = "";
      renderMyInvites(res.invites);
      $("invite-dialog").close();   // the thank-you shows on the page, not behind the dialog
      showNotice("Thanks! Your invitation request has been sent.", 5000);
    } catch (err) {
      $("invite-error").textContent = sentence(err.message);
      $("invite-error").hidden = false;
    } finally {
      btn.disabled = false;
    }
  }

  if ($("invite-btn")) {
    $("invite-btn").addEventListener("click", openInvite);
    $("invite-form").addEventListener("submit", sendInvite);
    $("invite-cancel-btn").addEventListener("click", () => $("invite-dialog").close());
  }

  // Settings → Invitation requests (only for whoever runs the app).
  async function refreshInviteRequests() {
    const ul = $("invite-requests");
    if (!ul) return;
    let items;
    try { items = await api("GET", "/api/admin/invites"); } catch (_) { return; }
    const showAll = $("invites-show-all").checked;
    const shown = items.filter((i) => showAll || i.status === "requested");
    ul.replaceChildren();
    for (const i of shown) {
      const li = document.createElement("li");
      li.className = i.status === "requested" ? "" : "done";
      const meta = document.createElement("div");
      meta.className = "feedback-meta";
      meta.textContent = `${longDate(i.created_at)} · asked by ${i.requester_email || i.requester_name || "someone"}`;
      const body = document.createElement("p");
      body.className = "feedback-body";
      body.textContent = (i.name ? `${i.name}, ${i.email}` : i.email) + (i.note ? `\n“${i.note}”` : "");
      const actions = document.createElement("div");
      actions.className = "invite-actions";
      const status = document.createElement("span");
      status.className = `invite-status ${i.status}`;
      status.textContent = INVITE_STATUS[i.status] || i.status;
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "Copy address";
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(i.email); copy.textContent = "Copied"; } catch (_) { copy.textContent = i.email; }
      });
      actions.append(status, copy);
      if (i.status !== "added") {
        const flip = document.createElement("button");
        flip.type = "button";
        flip.textContent = i.status === "declined" ? "Reopen" : "Decline";
        flip.addEventListener("click", async () => {
          try {
            await api("POST", `/api/admin/invites/${i.id}`, { status: i.status === "declined" ? "requested" : "declined" });
            refreshInviteRequests();
          } catch (err) { state.clientError = `Couldn't update that: ${sentence(err.message)}`; renderBanner(); }
        });
        actions.append(flip);
      }
      li.append(meta, body, actions);
      ul.append(li);
    }
    if (!shown.length) {
      const li = document.createElement("li");
      li.className = "subtle";
      li.textContent = items.length ? "No requests waiting." : "No invitation requests yet.";
      ul.append(li);
    }
  }
  if ($("invites-show-all")) $("invites-show-all").addEventListener("change", refreshInviteRequests);

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
  $("write-older-btn").addEventListener("click", olderFromWritingPage);
  $("next-btn").addEventListener("click", goNewer);

  // Swipe on a past entry (phones and tablets): right = Older, like turning
  // back a page; left = Newer. Only clear, quick, sideways swipes count, so
  // scrolling never turns the page. Swipes from the screen's edge are left to
  // the phone (that's its own "go back" gesture).
  {
    const SWIPE_MIN = 60, EDGE = 24, MAX_MS = 800;
    const onSwipe = (view, older, newer) => {
      let start = null;
      view.addEventListener("touchstart", (e) => {
        const t = e.touches[0];
        const busy = e.touches.length > 1 || !$("entry-editor").hidden || $("dialog").open || state.cal.open
          || t.clientX < EDGE || t.clientX > innerWidth - EDGE;
        start = busy ? null : { x: t.clientX, y: t.clientY, time: Date.now() };
      }, { passive: true });
      view.addEventListener("touchend", (e) => {
        if (!start) return;
        const t = e.changedTouches[0];
        const dx = t.clientX - start.x, dy = t.clientY - start.y, quick = Date.now() - start.time < MAX_MS;
        start = null;
        if (!quick || Math.abs(dx) < SWIPE_MIN || Math.abs(dx) < 2 * Math.abs(dy)) return;
        if (window.getSelection && String(window.getSelection())) return;   // they were selecting text
        if (dx > 0) older(); else if (newer) newer();
      }, { passive: true });
      view.addEventListener("touchcancel", () => { start = null; }, { passive: true });
    };
    onSwipe($("entry-view"), goOlder, goNewer);
    onSwipe($("write-view"), olderFromWritingPage, null);   // nothing is newer than a new entry
  }
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
    if ($("dialog").open || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (!$("write-view").hidden && e.key === "ArrowLeft") { e.preventDefault(); olderFromWritingPage(); return; }
    if ($("entry-view").hidden || !$("entry-editor").hidden) return;
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
    $("vault-quote").hidden = name !== "unlock" && name !== "setup";   // on the Secret Passphrase screens
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

  // ---- Passkeys -----------------------------------------------------------
  // A passkey unlocks the journal through its PRF extension: given a salt, it
  // produces a secret only it can make (after your fingerprint, face or PIN).
  // The server uses that secret to unwrap your journal's key, then forgets it.

  const toB64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const fromB64url = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
  const randomBytes = (n) => crypto.getRandomValues(new Uint8Array(n));
  const passkeysPossible = () => !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext);

  // Plain-language messages for what the browser's passkey prompt can report.
  function passkeyError(err, fallback) {
    if (err && err.name === "NotAllowedError") return "The passkey prompt was cancelled or timed out.";
    if (err && err.name === "InvalidStateError") return "This device already has a passkey for your journal.";
    if (err && err.name === "SecurityError") return "Passkeys don't work on this address.";
    return (err && err.message) || fallback;
  }

  function guessDeviceName() {
    const ua = navigator.userAgent;
    const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android"
      : /Mac OS X/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /CrOS/.test(ua) ? "Chromebook" : "this device";
    const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome"
      : /Safari\//.test(ua) ? "Safari" : "";
    return browser ? `${browser} on ${os}` : os;
  }

  // Ask the passkey for its secret for `salt` (a second prompt, for devices
  // that don't return the secret while creating the passkey).
  async function passkeySecret(rpId, credentialId, salt) {
    const got = await navigator.credentials.get({ publicKey: {
      challenge: randomBytes(32), rpId, timeout: 60000, userVerification: "required",
      allowCredentials: [{ type: "public-key", id: credentialId }],
      extensions: { prf: { eval: { first: salt } } },
    } });
    const prf = got.getClientExtensionResults().prf;
    return prf && prf.results ? prf.results.first : null;
  }

  function renderPasskeys(list) {
    const ul = $("passkey-list");
    ul.replaceChildren(...list.map((pk) => {
      const li = document.createElement("li");
      const name = document.createElement("strong");
      name.textContent = pk.label;
      const when = document.createElement("span");
      when.className = "subtle";
      when.textContent = ` · added ${longDate(pk.created_at)}`
        + (pk.last_used_at ? ` · last used ${longDate(pk.last_used_at)}` : " · not used yet");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "quiet";
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        const sure = await ask("Remove this passkey?", [
          `“${pk.label}” will no longer unlock your journal. Your passphrase still will.`,
          "The passkey itself stays on the device (in its password manager); you can delete it there too.",
        ], [{ label: "Cancel", value: false }, { label: "Remove", value: true, style: "danger" }]);
        if (!sure) return;
        try {
          renderPasskeys((await api("DELETE", `/api/passkeys/${encodeURIComponent(pk.id)}`)).passkeys);
        } catch (err) {
          $("passkey-result").textContent = sentence(err.message);
        }
      });
      li.append(name, when, remove);
      return li;
    }));
    ul.hidden = !list.length;
  }

  async function refreshPasskeys() {
    const possible = passkeysPossible();
    $("passkey-unsupported").hidden = possible;
    $("passkey-form").hidden = !possible;
    if (!$("passkey-label").value) $("passkey-label").value = guessDeviceName();
    try {
      state.passkeyInfo = await api("GET", "/api/passkeys");
      renderPasskeys(state.passkeyInfo.passkeys);
    } catch (_) { /* the rest of Settings still works */ }
  }

  $("passkey-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const out = $("passkey-result");
    const btn = $("passkey-add-btn");
    const password = $("passkey-pw").value;
    btn.disabled = true;
    out.textContent = "";
    try {
      // Check the passphrase first, so a typo doesn't leave a stray passkey on the device.
      await api("POST", "/api/passkeys", { password, check_only: true });
      const info = state.passkeyInfo || await api("GET", "/api/passkeys");
      const salt = randomBytes(32);
      out.textContent = "Follow your device's prompt…";
      const cred = await navigator.credentials.create({ publicKey: {
        rp: { id: info.rp_id, name: info.rp_name },
        user: { id: new TextEncoder().encode(info.user_id), name: info.user_name, displayName: info.user_display },
        challenge: randomBytes(32),
        pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
        authenticatorSelection: { residentKey: "preferred", userVerification: "required" },
        excludeCredentials: info.passkeys.map((pk) => ({ type: "public-key", id: fromB64url(pk.id) })),
        timeout: 60000,
        extensions: { prf: { eval: { first: salt } } },
      } });
      const prf = cred.getClientExtensionResults().prf;
      let secret = prf && prf.results ? prf.results.first : null;
      if (!secret && !(prf && prf.enabled === false)) {
        out.textContent = "One more time, to finish setting it up…";
        secret = await passkeySecret(info.rp_id, cred.rawId, salt);
      }
      if (!secret) {
        throw new Error("This device made a passkey, but it can't be used to unlock journals (it doesn't support "
          + "the feature needed). You can delete it from the device's password manager.");
      }
      const res = await api("POST", "/api/passkeys", {
        password, credential_id: toB64url(cred.rawId), prf_salt: toB64url(salt),
        prf: toB64url(secret), label: $("passkey-label").value,
      });
      $("passkey-pw").value = "";
      state.passkeyInfo = { ...info, passkeys: res.passkeys };
      renderPasskeys(res.passkeys);
      out.textContent = "Passkey added. Next time, choose “Unlock with passkey”.";
      try { localStorage.setItem("journal.passkey", "1"); } catch (_) {}
    } catch (err) {
      out.textContent = sentence(passkeyError(err, "Couldn't add the passkey."));
    } finally {
      btn.disabled = false;
    }
  });

  $("passkey-unlock-btn").addEventListener("click", async () => {
    const btn = $("passkey-unlock-btn");
    btn.disabled = true;
    $("unlock-error").hidden = true;
    try {
      const { rp_id: rpId, credentials } = await api("GET", "/api/vault/passkey-options");
      if (!credentials.length) throw new Error("No passkeys are set up for this journal yet");
      const got = await navigator.credentials.get({ publicKey: {
        challenge: randomBytes(32), rpId, timeout: 60000, userVerification: "required",
        allowCredentials: credentials.map((c) => ({ type: "public-key", id: fromB64url(c.id) })),
        extensions: { prf: { evalByCredential: Object.fromEntries(
          credentials.map((c) => [c.id, { first: fromB64url(c.salt) }])) } },
      } });
      const prf = got.getClientExtensionResults().prf;
      const secret = prf && prf.results ? prf.results.first : null;
      if (!secret) throw new Error("This device's passkey can't unlock the journal. Use your passphrase instead");
      await api("POST", "/api/vault/unlock-passkey", { credential_id: toB64url(got.rawId), prf: toB64url(secret) });
      try { localStorage.setItem("journal.passkey", "1"); } catch (_) {}
      startJournal();
    } catch (err) {
      vaultError("unlock-error", passkeyError(err, "The passkey didn't unlock the journal"));
    } finally {
      btn.disabled = false;
    }
  });

  // ---- Starting up --------------------------------------------------------

  // "Send feedback about the sources" on the Sources page links to /?feedback=sources.
  // Remembered for this tab, so it survives signing in with Google first.
  const FEEDBACK_ON_START = (() => {
    const asked = new URLSearchParams(location.search).get("feedback");
    try {
      if (asked) sessionStorage.setItem("journal.feedbackOnStart", asked);
      return asked || sessionStorage.getItem("journal.feedbackOnStart");
    } catch (_) { return asked; }
  })();

  let started = false;
  function startJournal() {
    state.unlocked = true;
    $("vault").hidden = true;
    $("book").hidden = false;
    if (started) return;
    started = true;
    restartAutosave();
    setInterval(pollStatus, 10000);
    if (FEEDBACK_ON_START === "sources") {
      try { sessionStorage.removeItem("journal.feedbackOnStart"); } catch (_) {}
      history.replaceState(null, "", "/");
      $("feedback-text").value = $("feedback-text").value || "About the sources: ";
      setTimeout(openFeedback, 400);
    }
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
    if (v.passkeys && passkeysPossible()) {
      $("passkey-unlock").hidden = false;
      $("passkey-unlock-btn").focus();   // the quickest way in, when there is one
    }
  })();
})();
