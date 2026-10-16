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
  // "Thu, Oct 8, 2026": Past entries, where the line also carries the word count and themes.
  const shortDate = (iso) => fmtDate(iso, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
  // "9:30 am" rather than "9:30 AM": capitals look odd in the handwriting and text.
  const timeOf = (iso) => fmtDate(iso, { hour: "numeric", minute: "2-digit" }).replace(/\b(AM|PM)\b/, (m) => m.toLowerCase());

  // passive: timer-driven requests that shouldn't count as activity (so the
  // journal can still lock itself when you've walked away).
  let lockGen = 0;   // goes up each time the book is closed (see forgetJournal)
  async function api(method, url, body, { passive = false } = {}) {
    const gen = lockGen;
    const headers = { "X-Journal-Client": "1" };
    if (body) headers["Content-Type"] = "application/json";
    if (passive) headers["X-Journal-Passive"] = "1";
    const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (gen !== lockGen) {
      // Asked for before the book was closed: whatever came back is dropped,
      // so nothing from the unlocked journal lands in the page afterwards.
      const stale = new Error("The journal is locked");
      stale.status = 423;
      throw stale;
    }
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

  const VIEWS = ["write", "entry", "settings", "kept", ...(document.getElementById("prayers-view") ? ["prayers"] : [])];   // the pages of the book
  function showView(name) {
    const el = $(`${name}-view`);
    const changed = el.hidden;
    for (const v of VIEWS) $(`${v}-view`).hidden = v !== name;
    $("past").hidden = ["settings", "kept", "prayers"].includes(name);
    if (changed) animatePage(el, "view-in");
  }

  // ---- Look: theme and today's photo ------------------------------------

  function applyTheme(theme, palette, textSize) {
    if (theme) document.documentElement.dataset.theme = theme;
    if (palette) document.documentElement.dataset.palette = palette;
    if (textSize) document.documentElement.dataset.textSize = textSize;
  }

  // The look as last saved. Settings previews changes live, and Back without
  // saving returns to this.
  const savedLook = {
    theme: document.documentElement.dataset.theme,
    palette: document.documentElement.dataset.palette,
    textSize: document.documentElement.dataset.textSize,
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
    applyTheme(savedLook.theme, savedLook.palette, savedLook.textSize);
    applyPhotoLook(savedLook.blur, savedLook.visibility);
    document.documentElement.dataset.photo = savedLook.photo;
    $("backdrop").style.visibility = "";  // the saved photo setting decides via loadBackground
  }

  // Show the slider values, and gray the sliders out when the photo is off.
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
    applyTheme(form.elements.theme.value, picked && picked.value, form.elements.text_size.value);
    applyPhotoLook(Number(form.elements.photo_blur.value), Number(form.elements.photo_visibility.value));
    const photoOn = form.elements.background_photo.checked;
    $("backdrop").style.visibility = photoOn ? "" : "hidden";
    document.documentElement.dataset.photo = photoOn ? "on" : "off";  // dark theme-colored surround when off
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

  // keepScroll: stay where the reader is (after Finish & reflect, so a long
  // entry doesn't jump to the top while the reflection is being written).
  function showEntry(entry, { keepScroll = false } = {}) {
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
    $("entry-date").textContent = longDate(entry.created_at);   // the date only: the time is clutter here
    showEntryWords(entry);
    $("entry-text").textContent = entry.text;
    renderEntryThemes(entry);
    renderTitle(entry);
    $("entry-status").textContent = "";
    setEditing(false);
    renderReflection(entry);
    updateNav();
    syncCalendarTo(entry);
    if (!keepScroll) window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---- Older / Newer ----------------------------------------------------

  const when = (e) => Date.parse(e.created_at);
  // Entries passing the favorites filter (what the calendar shades).
  const favShown = () => (state.favOnly
    ? (state.allEntries || []).filter((e) => e.favourite)
    : (state.allEntries || []));
  // Entries passing every filter (the list and Older / Newer).
  const inRange = (e) => !state.range || (e.date >= state.range.start && e.date <= state.range.end);
  const inTheme = (e) => !state.theme || (e.themes || []).includes(state.theme);
  const shown = () => favShown().filter(inRange).filter(inTheme);

  function filterDescription() {
    const parts = [];
    if (state.favOnly) parts.push("favorites");
    if (state.range) parts.push(`${state.range.prep} ${state.range.label}`);
    if (state.theme) parts.push(`about “${state.theme}”`);
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
    $("prev-btn").title = older ? "Older entry (← or j)" : "This is your first entry";
    $("next-btn").textContent = newer ? "Newer ›" : "New entry ›";
    $("next-btn").title = newer ? "Newer entry (→ or k)" : "Start a new entry (→ or k)";
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
    const longestBefore = state.lastStats && state.lastStats.longest_id;
    state.lastStats = s;
    renderStats(s);
    maybeInviteInstall(s);
    if (s.longest_id !== longestBefore) renderList();
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

  // The header line: the entry count (or a milestone in its place) and one
  // streak item. Milestones stay for as long as they're true, not just briefly.
  function renderStats(s) {
    const box = $("stats");
    box.replaceChildren();
    const pop = state.popStats;          // just reached, after Finish & reflect: animate once
    state.popStats = false;
    const badge = (text, title, gold) => {
      const b = document.createElement("span");
      b.className = "streak-badge" + (gold ? " milestone" : "") + (gold && pop ? " pop" : "");
      b.textContent = text;
      b.title = title;
      return b;
    };
    const count = s.total_entries;
    if (isCountMilestone(count)) {
      box.append(badge(`✦ Your ${count}th entry`, `${count} entries`, true));   // 5th, 10th, 25th, 50th…
    } else {
      box.append(document.createTextNode(`${count} ${count === 1 ? "entry" : "entries"}`));
    }
    // One streak item: a milestone day, else a new best, else the plain streak.
    const n = s.current_streak;
    const why = "Days in a row with at least one entry";
    if (n > 0) box.append(document.createTextNode(" · "));
    if (n > 0 && isMilestone(n) && s.wrote_today) {
      box.append(badge(`✦ ${milestoneWords(n)}`, `${n}-day streak`, true));
    } else if (n > 0 && newBestStreak(s)) {
      box.append(badge(`✦ Best streak yet · ${plural(n, "day")}`, `Longer than your previous best (${plural(s.previous_best, "day")})`, true));
    } else if (n >= BADGE_FROM) {
      box.append(badge(`✦ ${n}-day streak`, why, false));
    } else if (n > 0) {
      const plain = document.createElement("span");
      plain.textContent = `${n}-day streak`;
      plain.title = why;
      box.append(plain);
    }
    renderLimits(s);
  }

  // Beating an earlier streak of at least 2 days (a first-ever streak isn't a "best yet").
  const newBestStreak = (s) => s.previous_best >= 2 && s.current_streak > s.previous_best;

  // On the web version: the coin in the header shows the credits left (Shekels
  // in In His Steps), one per reflection. Clicking it opens a small pop-up with
  // the count and when they come back (the place for top-ups later).
  const creditName = (n) => (n === 1 ? CFG.credit.one : CFG.credit.many);

  // Credits come back at midnight in the journal's time zone: that moment, in
  // the reader's own time ("midnight", or "9:00 pm" from further west).
  function creditReset() {
    const now = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
      timeZone: CFG.timezone, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date()).map((p) => [p.type, Number(p.value)]));
    const ms = (24 * 3600 - (now.hour * 3600 + now.minute * 60 + now.second)) * 1000;
    const at = new Date(Date.now() + ms);
    const local = at.getHours() === 0 && at.getMinutes() === 0 ? "midnight"
      : at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).replace(/\b(AM|PM)\b/, (m) => m.toLowerCase());
    const mins = Math.max(1, Math.round(ms / 60000));
    const away = mins < 60 ? `in ${plural(mins, "minute")}` : `in about ${plural(Math.round(mins / 60), "hour")}`;
    return { local, away };
  }

  function renderCreditPop() {
    const s = state.lastStats;
    if (!s || s.reflections_left == null) return;
    const left = s.reflections_left, all = s.reflections_per_day;
    const { local, away } = creditReset();
    $("credit-pop-title").textContent = left === 0 ? `No ${CFG.credit.many} left today` : `${left} ${creditName(left)} left today`;
    $("credit-pop-text").textContent = `Each reflection uses one ${CFG.credit.one}. `
      + (left === all ? `You get ${all} every day; they're topped back up at ${local} (${away}).`
        : `You'll have ${all} again at ${local} (${away}).`)
      + (left === 0 ? " Your entries are still saved, and can be reflected on then." : "");
  }

  function toggleCreditPop(open = $("credit-pop").hidden) {
    if (open) renderCreditPop();
    $("credit-pop").hidden = !open;
    $("credit-coin").setAttribute("aria-expanded", String(open));
    document.querySelector(".book-header").classList.toggle("coin-open", open);
  }
  if ($("credit-coin")) {
    $("credit-coin").addEventListener("click", () => toggleCreditPop());
    document.addEventListener("mousedown", (e) => {
      if (!$("credit-pop").hidden && !e.target.closest(".credit-wrap")) toggleCreditPop(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("credit-pop").hidden) { toggleCreditPop(false); $("credit-coin").focus(); }
    });
  }

  function renderLimits(s) {
    const coin = $("credit-coin");
    if (!coin || s.reflections_left == null) return;
    const left = s.reflections_left;
    const text = left === 0 ? "No reflections left" : `${plural(left, "reflection")} left`;
    const was = coin.dataset.left;
    coin.hidden = false;
    coin.querySelector(".coin").textContent = left;
    coin.title = text;
    coin.setAttribute("aria-label", `${left} ${creditName(left)} left: ${text.toLowerCase()}`);
    if (!$("credit-pop").hidden) renderCreditPop();
    coin.classList.toggle("empty", left === 0);
    if (was !== undefined && Number(was) !== left) {          // a credit used, or a new day: a little flip
      coin.classList.remove("pop"); void coin.offsetWidth; coin.classList.add("pop");
    }
    coin.dataset.left = left;
  }

  // Entry-count milestones: 5, 10, 25, 50, then every 50 (100, 150, 200…).
  const isCountMilestone = (n) => n === 5 || n === 10 || n === 25 || (n >= 50 && n % 50 === 0);
  const RECORD_FROM = 3;  // earlier entries needed before "longest yet" counts

  // After Finish & reflect: if this entry reached a milestone (count, streak
  // or best streak), it pops in once; the header then keeps it while it's true.
  async function checkMilestone(before, entry) {
    if (!before) { await refreshStats(); return; }
    const reached = (s) => [isCountMilestone(s.total_entries), isMilestone(s.current_streak) && s.wrote_today, newBestStreak(s)];
    const was = reached(before);
    let after;
    try { after = await api("GET", "/api/stats", null, {}); } catch (_) { return; }
    state.popStats = reached(after).some((now, i) => now && !was[i]);
    state.lastStats = after;
    renderStats(after);
    maybeInviteInstall(after);
    renderList();                                         // the "✦ longest" mark may have moved
    if (entry && state.current && state.current.id === entry.id) showEntryWords(state.current);
  }

  // ---- Reflection -------------------------------------------------------

  function renderReflection(entry, { working = false } = {}) {
    renderTitle(entry);  // a suggested title arrives with the reflection
    const busy = working || entry.reflecting;
    // No editing or deleting while Claude is writing the reflection: it would
    // answer the old text (or have nowhere to go). Back as soon as it's done,
    // fails, or is refused (e.g. the daily limit).
    for (const id of ["edit-btn", "delete-btn"]) {
      $(id).disabled = busy;
      $(id).title = busy ? "Available once the reflection is ready" : "";
    }
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
      let text = reasons[status] || "This entry doesn't have a reflection yet.";
      let button = status === "Failed" ? "Try reflection again" : "Reflect on this entry";
      // Turned away by a day's limit (perhaps another day): say what's true today.
      const limited = status === "Failed" && (entry.reflection_limited || /used today's/i.test(entry.reflection_error || ""));
      const left = state.lastStats && state.lastStats.reflections_left;
      if (limited && left != null) {
        if (left > 0) {
          text = `This entry doesn't have a reflection yet. You have ${left} ${creditName(left)} left today: `
            + `you can use one to reflect on it now.`;
          button = `Reflect on this entry (uses 1 ${CFG.credit.one})`;
        } else {
          text = `This entry doesn't have a reflection yet. You've used today's ${CFG.credit.many}; `
            + `they come back tomorrow, and you can reflect on it then.`;
        }
      }
      $("reflection-missing-text").textContent = text;
      $("reflect-btn").textContent = button;
      $("reflect-btn").hidden = limited && left === 0;
    }
    $("reflect-again-btn").hidden = busy || !entry.can_reflect_again;
    const TONE_WORDS = { encourage: "Written to encourage", reflect: "Written to reflect", challenge: "Written to challenge" };
    $("reflection-tone").hidden = !done || !TONE_WORDS[entry.reflection_tone];
    $("reflection-tone").textContent = TONE_WORDS[entry.reflection_tone] || "";
    $("reflect-again-btn").textContent = entry.admin_reflect_again ? "Reflect again (admin)" : "Reflect on your edited entry";   // one reflection per entry, unless it's been rewritten
    if (done) {
      renderVignette(entry);
      $("insight").replaceChildren(...entry.insight.split(/\n\s*\n/).map((t) => {
        const p = document.createElement("p"); p.append(formatInsight(t.trim(), entry)); return p;
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
        // Books of the Bible have no author: "— Romans 8:28". A named chapter
        // takes a comma ("…of God, Fourth Conversation"); numbers don't ("Meditations 4.3").
        const where = !q.location ? "" : /^[\p{L}§]/u.test(q.location) ? `, ${q.location}` : ` ${q.location}`;
        li.append(document.createTextNode(`— ${q.author ? `${q.author}, ` : ""}${q.title}${where} `));
        if (typeof q.url === "string" && q.url.startsWith("https://")) {
          const a = document.createElement("a");
          a.href = q.kind === "quote" ? passageUrl(q.url, q.quote, q.location) : inTranslation(q.url, q.location);
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = q.kind === "quote" ? "Read the passage ↗" : "Read the source ↗";
          // "Read the passage" = quoted word for word; "Read the source" = an idea in the reflection's own words.
          a.title = (q.kind === "idea" ? `Paraphrased${q.idea ? `: ${q.idea}` : ""}. ` : "")
            + `${q.translation.charAt(0).toUpperCase()}${q.translation.slice(1)}, on ${siteName(q.url)}`;
          li.append(a);
          if (q.kind === "quote" && q.quote) li.append(document.createTextNode(" · "), shareLink(q));
        }
        ul.append(li);
      }
      const note = $("support-note");
      note.hidden = !entry.support_note;
      if (entry.support_note) note.innerHTML = renderSimpleMarkdown(entry.support_note);
    }
    if (entry.reflecting) pollWhileReflecting(entry.id);
    if ($("question-pray")) $("question-pray").hidden = !done || !entry.question;
    applyHighlights(entry);
    if (done) maybeStartTour(entry);
  }

  // ---- Kept passages -------------------------------------------------------
  // A star after a quotation keeps it; the book with a star in the header opens the page
  // that gathers them by writer.
  const STAR_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="12,2.8 14.8,8.7 21.3,9.6 16.6,14.1 17.8,20.6 12,17.5 6.2,20.6 7.4,14.1 2.7,9.6 9.2,8.7"/></svg>';

  function paintStar(btn, on) {
    btn.setAttribute("aria-pressed", String(on));
    btn.title = on ? "Kept. Click to let it go" : "Keep this passage";
    btn.setAttribute("aria-label", btn.title);
  }

  function quoteStar(entry, index) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "quote-star";
    btn.innerHTML = STAR_SVG;
    paintStar(btn, !!entry.quotes[index].kept);
    btn.addEventListener("click", async () => {
      const q = entry.quotes[index], want = !q.kept;
      q.kept = want;
      paintStar(btn, want);            // straight away; undone if saving fails
      if (want) { btn.classList.remove("pop"); void btn.offsetWidth; btn.classList.add("pop"); }
      try {
        await api("POST", "/api/kept", want ? { keep: true, entry_id: entry.id, index } : { keep: false, key: q.key });
        if (want && !recall("journal.keptHint")) {
          remember("journal.keptHint", 1);
          showNotice("Kept. You'll find your kept passages under the star at the top.", 6000);
        }
      } catch (err) {
        q.kept = !want;
        paintStar(btn, !want);
        state.clientError = `Couldn't ${want ? "keep" : "release"} that passage: ${err.message}`;
        renderBanner();
      }
    });
    return btn;
  }

  async function showKept(sort) {
    showView("kept");
    if (!sort) window.scrollTo({ top: 0 });
    let res;
    try {
      res = await api("GET", `/api/kept${sort ? `?sort=${sort}` : ""}`);
    } catch (err) {
      $("kept-intro").textContent = `Couldn't load your kept passages: ${err.message}`;
      return;
    }
    for (const b of document.querySelectorAll("#kept-sort [data-sort]")) b.setAttribute("aria-pressed", String(b.dataset.sort === res.sort));
    renderKept(res.groups, res.sort);
  }
  for (const b of document.querySelectorAll("#kept-sort [data-sort]")) b.addEventListener("click", () => showKept(b.dataset.sort));

  const KEPT_INTRO = {
    in_order: CFG.hasScripture ? "Scripture in Bible order, then the writers by when they lived." : "The writers by when they lived.",
    recent: "The passages you kept most recently, first.",
    az: CFG.hasScripture ? "Books of the Bible, then the writers, alphabetically." : "The writers, alphabetically by surname.",
    popular: "The books and writers people keep most, first.",
  };

  function renderKept(groups, sort = "in_order") {
    const total = groups.reduce((n, g) => n + g.count, 0);
    $("kept-intro").textContent = total ? `${plural(total, "passage")} you've kept, or that were shared with you. ${KEPT_INTRO[sort]}` : "";
    $("kept-sort").hidden = total < 2;
    const list = $("kept-list");
    list.replaceChildren();
    if (!total) {
      const p = document.createElement("p");
      p.className = "kept-empty";
      p.textContent = "Nothing kept yet. When a reflection quotes something worth holding on to, click the star after the quotation and it will be gathered here.";
      list.append(p);
      return;
    }
    for (const g of groups) {
      const box = document.createElement("section");
      box.className = "kept-group";
      if (g.flat) {                                // Recently kept: one list, each with where it's from
        for (const p of g.sections[0].passages) box.append(keptPassage(p, g));
        list.append(box);
        continue;
      }
      const head = document.createElement("header");
      const h = document.createElement("h2");
      h.textContent = g.name;
      const count = document.createElement("span");
      count.className = "kept-count";
      count.textContent = plural(g.count, "passage");
      head.append(h, count);
      if (g.anchor) {
        const more = document.createElement("a");
        more.className = "kept-more";
        more.href = `/sources#${g.anchor}`;
        more.textContent = `More from ${g.name} →`;
        more.title = "The works this journal draws on, with links to read them in full";
        head.append(more);
      }
      box.append(head);
      for (const s of g.sections) {
        const h3 = document.createElement("h3");
        h3.textContent = s.name;
        box.append(h3);
        for (const p of s.passages) box.append(keptPassage(p, g));
      }
      list.append(box);
    }
  }

  // A passage just let go of: a slim line where it was, with Undo to put it back exactly
  // as it was (kept date, and who shared it). It stays until they leave the page.
  function keptUndo(p, group) {
    const row = document.createElement("p");
    row.className = "kept-undo";
    row.append(`Let go of ${citeText(p)}. `);
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "link-button";
    undo.textContent = "Undo";
    undo.addEventListener("click", async () => {
      undo.disabled = true;
      try {
        await api("POST", "/api/kept", { restore: p });
        if (state.current) for (const q of state.current.quotes || []) if (q.key === p.key) q.kept = true;
        row.replaceWith(keptPassage(p, group));
      } catch (err) {
        undo.disabled = false;
        state.clientError = `Couldn't put that passage back: ${err.message}`;
        renderBanner();
      }
    });
    row.append(undo);
    return row;
  }

  function keptPassage(p, group) {
    const fig = document.createElement("figure");
    fig.className = "kept-passage";
    const quote = document.createElement("blockquote");
    quote.textContent = `“${p.quote}”`;
    const star = document.createElement("button");
    star.type = "button";
    star.className = "quote-star";
    star.innerHTML = STAR_SVG;
    paintStar(star, true);
    star.addEventListener("click", async () => {
      star.disabled = true;
      try {
        await api("POST", "/api/kept", { keep: false, key: p.key });
        if (state.current) for (const q of state.current.quotes || []) if (q.key === p.key) q.kept = false;
        fig.replaceWith(keptUndo(p, group));          // folds to "Let go of … · Undo", until they leave the page
      } catch (err) {
        star.disabled = false;
        state.clientError = `Couldn't release that passage: ${err.message}`;
        renderBanner();
      }
    });
    quote.append(star);
    // Where it's from, on the same line, as the link to read it: "— Romans 8:28 ↗".
    // Scripture: book, chapter and verse. A writer's work is already the heading above, so
    // just the place in it. In one list (Recently kept), the whole citation.
    const place = group.flat ? citeText(p) : group.scripture ? `${p.title}${p.location ? ` ${p.location}` : ""}`
      : p.location || p.title;
    const cite = document.createElement("span");
    cite.className = "kept-cite";
    cite.append("— ");
    if (typeof p.url === "string" && p.url.startsWith("https://")) {
      const a = document.createElement("a");
      a.href = passageUrl(p.url, p.quote, p.location);
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = `${place} ↗`;
      a.title = "Read the passage in context";
      cite.append(a);
    } else {
      cite.append(place);
    }
    quote.append(" ", cite);
    fig.append(quote);
    // Shared with you by a friend (instead of, or as well as, coming up in your reflections).
    for (const gift of p.shared || []) {
      const from = document.createElement("div");
      from.className = "kept-shared";
      const line = document.createElement("p");
      line.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9h18v4H3zM5 13h14v8H5zM12 9v12M12 9c-1.5-3-5-4-6-2s2 2 6 2zM12 9c1.5-3 5-4 6-2s-2 2-6 2z"/></svg>';
      line.append(document.createTextNode(`${p.own === false ? "Shared with you" : "Also shared with you"} by ${gift.by} · `
        + fmtDate(gift.at, { month: "short", day: "numeric", year: "numeric" })));
      from.append(line);
      if (gift.note) {
        const note = document.createElement("p");
        note.className = "kept-shared-note";
        note.textContent = `“${gift.note}”`;
        from.append(note);
      }
      fig.append(from);
    }
    if (p.own === false) fig.classList.add("kept-gift");
    // One line under it: "Came up in …" on the left, Share on the right.
    const foot = document.createElement("div");
    foot.className = "kept-foot";
    const from = document.createElement("p");
    from.className = "kept-entries";
    if (p.entries.length) {
      from.append(document.createTextNode("Came up in "));
      p.entries.forEach((e, i) => {
        if (i) from.append(document.createTextNode(" · "));
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = `${fmtDate(e.created_at, { month: "short", day: "numeric", year: "numeric" })}${e.title ? `, ${e.title}` : ""}`;
        b.title = "Turn to this entry";
        b.addEventListener("click", () => openEntry(e.id));
        from.append(b);
      });
    }
    const share = shareLink(p);
    share.classList.add("share-pill");
    foot.append(from, share);
    fig.append(foot);
    return fig;
  }

  // ---- Highlights (and, in In His Steps, prayers) -----------------------------
  // Selecting words in a saved entry or its reflection shows a little toolbar:
  // Highlight (and Add prayer). A highlight is stored with the entry as its words
  // and which occurrence of them (so it keeps its place through small edits).
  const HL_ROOTS = { entry: () => $("entry-text"), reflection: () => $("insight"), question: () => $("question") };

  function textNodesOf(root) {
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const out = [];
    while (walk.nextNode()) out.push(walk.currentNode);
    return out;
  }

  // Wrap the nth occurrence of `text` in root (across formatting) in <mark>s.
  function markOccurrence(root, text, nth, h) {
    const nodes = textNodesOf(root);
    const starts = [];
    let full = "";
    for (const n of nodes) { starts.push(full.length); full += n.data; }
    let at = -1;
    for (let i = 0; i <= nth; i++) { at = full.indexOf(text, at + 1); if (at < 0) return; }
    const end = at + text.length;
    nodes.forEach((n, i) => {
      const s = starts[i], e = s + n.data.length;
      if (e <= at || s >= end) return;
      const a = Math.max(at, s) - s, b = Math.min(end, e) - s;
      let piece = n;
      if (a > 0) piece = piece.splitText(a);
      if (b - a < piece.data.length) piece.splitText(b - a);
      const mark = document.createElement("mark");
      mark.className = `hl${h.prayer_id ? " prayer" : ""}`;
      mark.dataset.hl = h.id;
      mark.title = h.prayer_id ? "Highlighted, and on your prayer list" : "Highlighted";
      piece.parentNode.insertBefore(mark, piece);
      mark.append(piece);
    });
  }

  function applyHighlights(entry) {
    for (const [where, rootOf] of Object.entries(HL_ROOTS)) {
      const root = rootOf();
      if (!root) continue;
      for (const m of root.querySelectorAll("mark.hl")) m.replaceWith(...m.childNodes);
      root.normalize();
      if (!entry) continue;
      for (const h of entry.highlights || []) if (h.where === where) markOccurrence(root, h.text, h.nth || 0, h);
    }
  }

  // What's selected, if it's words in a saved entry (not being edited) or in one
  // paragraph of its reflection: {where, text, nth, rect}.
  function selectedWords() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount || $("entry-view").hidden || !$("entry-editor").hidden) return null;
    const range = sel.getRangeAt(0);
    for (const [where, rootOf] of Object.entries(HL_ROOTS)) {
      const root = rootOf();
      if (!root || !root.contains(range.commonAncestorContainer)) continue;
      if (where === "reflection") {
        const el = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
        const p = el && el.closest("p");
        if (!p || !p.contains(range.endContainer)) return null;
      }
      const raw = range.toString();
      const text = raw.trim();
      if (text.length < 2) return null;
      const before = document.createRange();
      before.selectNodeContents(root);
      before.setEnd(range.startContainer, range.startOffset);
      const offset = before.toString().length + (raw.length - raw.trimStart().length);
      const full = root.textContent;
      let nth = 0;
      for (let i = full.indexOf(text); i >= 0 && i < offset; i = full.indexOf(text, i + 1)) nth++;
      return { where, text, nth, rect: range.getBoundingClientRect() };
    }
    return null;
  }

  // Place a toolbar over a spot (under it on touch screens, clear of the phone's own menu).
  function placeToolbar(bar, rect) {
    bar.hidden = false;
    const touch = window.matchMedia("(pointer: coarse)").matches;
    const w = bar.offsetWidth, h = bar.offsetHeight;
    const left = Math.min(Math.max(8, rect.left + rect.width / 2 - w / 2), document.documentElement.clientWidth - w - 8);
    const top = touch ? rect.bottom + 12 : rect.top - h - 8;
    bar.style.left = `${left + window.scrollX}px`;
    bar.style.top = `${Math.max(4, top) + window.scrollY}px`;
  }

  function hideToolbars() { $("select-pill").hidden = true; $("highlight-menu").hidden = true; }

  let picked = null;           // the selection the toolbar is for
  let selTimer = null;
  document.addEventListener("selectionchange", () => {
    clearTimeout(selTimer);
    selTimer = setTimeout(() => {
      picked = selectedWords();
      if (picked) { $("highlight-menu").hidden = true; placeToolbar($("select-pill"), picked.rect); }
      else $("select-pill").hidden = true;
    }, 220);
  });
  // Buttons in a toolbar mustn't clear the selection before they act.
  for (const bar of [$("select-pill"), $("highlight-menu")]) bar.addEventListener("mousedown", (e) => e.preventDefault());

  async function highlightWords(words) {
    const res = await api("POST", `/api/entries/${state.current.id}/highlights`,
      { where: words.where, text: words.text, nth: words.nth });
    state.current = res.entry;
    applyHighlights(state.current);
    return res.highlight;
  }

  $("select-pill").addEventListener("click", async (e) => {
    const act = e.target.closest("button")?.dataset.act;
    if (!act || !picked || !state.current) return;
    const words = picked;
    hideToolbars();
    window.getSelection().removeAllRanges();
    try {
      const h = await highlightWords(words);
      if (act === "pray") openPrayerDialog(words.text, state.current.id, h.id);
    } catch (err) {
      state.clientError = `Couldn't highlight that: ${sentence(err.message)}`; renderBanner();
    }
  });

  // Clicking a highlight: Add prayer / On your prayer list / Keep passage / Remove.
  let menuFor = null;
  for (const rootOf of Object.values(HL_ROOTS)) {
    rootOf().addEventListener("click", (e) => {
      const mark = e.target.closest("mark.hl");
      if (!mark || !window.getSelection().isCollapsed || !state.current) return;
      const h = (state.current.highlights || []).find((x) => x.id === mark.dataset.hl);
      if (!h) return;
      menuFor = h;
      const menu = $("highlight-menu");
      const pray = menu.querySelector('[data-act="pray"]'), onList = menu.querySelector('[data-act="prayers"]');
      if (pray) pray.hidden = !!h.prayer_id;
      if (onList) onList.hidden = !h.prayer_id;
      menu.querySelector('[data-act="keep"]').hidden = !quoteFor(h);
      $("select-pill").hidden = true;
      placeToolbar(menu, mark.getBoundingClientRect());
      e.stopPropagation();
    });
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#highlight-menu, #select-pill, mark.hl")) $("highlight-menu").hidden = true;
  });

  // A highlight that is (part of) one of the reflection's checked quotes, not yet kept: offer to keep it.
  function quoteFor(h) {
    if (h.where !== "reflection" || !state.current) return null;
    const norm = (s) => String(s || "").replace(/[“”"]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
    const words = norm(h.text);
    const i = (state.current.quotes || []).findIndex((q) => !q.kept && q.quote
      && (norm(q.quote).includes(words) || words.includes(norm(q.quote))));
    return i < 0 ? null : { index: i, quote: state.current.quotes[i] };
  }

  $("highlight-menu").addEventListener("click", async (e) => {
    const act = e.target.closest("button")?.dataset.act;
    const h = menuFor;
    if (!act || !h || !state.current) return;
    $("highlight-menu").hidden = true;
    try {
      if (act === "remove") {
        const res = await api("DELETE", `/api/entries/${state.current.id}/highlights/${h.id}`);
        state.current = res.entry;
        applyHighlights(state.current);
      } else if (act === "pray") {
        openPrayerDialog(h.text, state.current.id, h.id);
      } else if (act === "prayers") {
        showPrayers();
      } else if (act === "keep") {
        const found = quoteFor(h);
        if (!found) return;
        await api("POST", "/api/kept", { keep: true, entry_id: state.current.id, index: found.index });
        found.quote.kept = true;
        renderReflection(state.current);
        showNotice("Kept. Find it in Kept passages.");
      }
    } catch (err) {
      state.clientError = `Couldn't do that: ${sentence(err.message)}`; renderBanner();
    }
  });

  // ---- The prayer list (In His Steps) -------------------------------------------
  let prayerFor = null;        // {entry_id, highlight_id} the prayer box is adding for
  let loadPrayers = null;      // set below, In His Steps only
  function showPrayers() { if (loadPrayers) loadPrayers(); }
  function openPrayerDialog(text, entryId, highlightId) {
    if (!$("prayer-dialog")) return;
    prayerFor = { entry_id: entryId || null, highlight_id: highlightId || null };
    $("prayer-text").value = text || "";
    $("prayer-error").hidden = true;
    $("prayer-dialog").showModal();
    $("prayer-text").focus();
  }

  if ($("prayer-dialog")) {
    // "To sit with": the reflection's question, a natural thing to pray about.
    $("question-pray").addEventListener("click", () => {
      if (state.current) openPrayerDialog(state.current.question, state.current.id, null);
    });
    $("prayer-cancel").addEventListener("click", () => $("prayer-dialog").close());
    $("prayer-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const res = await api("POST", "/api/prayers", { text: $("prayer-text").value, ...prayerFor });
        $("prayer-dialog").close();
        if (res.entry && state.current && res.entry.id === state.current.id) {
          state.current = res.entry;
          applyHighlights(state.current);
        }
        if (!$("prayers-view").hidden) showPrayers();
        else showNotice("Added to your prayer list.");
      } catch (err) {
        $("prayer-error").textContent = sentence(err.message);
        $("prayer-error").hidden = false;
      }
    });
    $("prayers-btn").addEventListener("click", showPrayers);
    $("prayers-back-btn").addEventListener("click", leaveSettings);
    $("prayer-add-btn").addEventListener("click", () => openPrayerDialog("", null, null));

    let answering = null;
    $("answered-cancel").addEventListener("click", () => $("answered-dialog").close());
    $("answered-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("POST", `/api/prayers/${answering.id}`, { answered: true, note: $("answered-note").value });
        $("answered-dialog").close();
        showPrayers();
      } catch (err) { state.clientError = `Couldn't save that: ${sentence(err.message)}`; renderBanner(); }
    });

    function prayerItem(p) {
      const li = document.createElement("li");
      li.className = `prayer-item${p.answered_at ? " answered" : ""}`;
      const text = document.createElement("p");
      text.className = "prayer-text";
      text.textContent = p.text;
      const meta = document.createElement("p");
      meta.className = "prayer-meta";
      const day = (iso) => fmtDate(iso, { month: "short", day: "numeric", year: "numeric" });
      meta.append(p.answered_at ? `Answered ${day(p.answered_at)} · prayed since ${day(p.added_at)}` : `Added ${day(p.added_at)}`);
      if (p.entry) {
        meta.append(" · from ");
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = p.entry.title || day(p.entry.created_at);
        b.title = "Turn to this entry";
        b.addEventListener("click", () => openEntry(p.entry.id));
        meta.append(b);
      }
      li.append(text, meta);
      if (p.answer_note) {
        const note = document.createElement("p");
        note.className = "prayer-note";
        note.textContent = p.answer_note;
        li.append(note);
      }
      const actions = document.createElement("div");
      actions.className = "prayer-actions";
      const button = (label, fn) => {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = label;
        b.addEventListener("click", fn);
        actions.append(b);
      };
      if (!p.answered_at) {
        button("Answered", () => {
          answering = p;
          $("answered-prayer").textContent = p.text;
          $("answered-note").value = "";
          $("answered-dialog").showModal();
        });
      } else {
        button("Still praying", async () => {
          await api("POST", `/api/prayers/${p.id}`, { answered: false });
          showPrayers();
        });
      }
      button("Remove", async () => {
        if (!confirm("Remove this prayer from your list?")) return;
        await api("DELETE", `/api/prayers/${p.id}`);
        if (state.current && p.entry && p.entry.id === state.current.id) {
          for (const h of state.current.highlights || []) if (h.prayer_id === p.id) h.prayer_id = null;
        }
        showPrayers();
      });
      li.append(actions);
      return li;
    }

    loadPrayers = async () => {
      hideToolbars();
      showView("prayers");
      let list;
      try {
        list = (await api("GET", "/api/prayers")).prayers;
      } catch (err) {
        state.clientError = `Couldn't load your prayer list: ${sentence(err.message)}`; renderBanner();
        return;
      }
      const open = list.filter((p) => !p.answered_at), answered = list.filter((p) => p.answered_at);
      $("prayer-open").replaceChildren(...open.map(prayerItem));
      if (!open.length) {
        const li = document.createElement("li");
        li.className = "subtle";
        li.textContent = answered.length ? "Nothing waiting just now." : "Nothing here yet. Add a prayer, or highlight words in an entry and choose Add prayer.";
        $("prayer-open").append(li);
      }
      $("prayer-answered-heading").hidden = !answered.length;
      $("prayer-answered").replaceChildren(...answered.map(prayerItem));
    };
  }

  // ---- The tour -------------------------------------------------------------------
  // Shown once, when the first reflection arrives (never before the first entry:
  // nothing should stand between someone and writing). Replay from Settings.
  const prayersOn = !!CFG.prayerList;
  const TOUR = [
    { el: () => $("insight").querySelector("p") || $("insight"), title: "Your reflection",
      text: "Wisdom chosen for what you wrote. Every quote is checked word for word: tap the reference beneath it to read it in context." },
    { el: () => $("insight").querySelector(".quote-star") || $("insight"), title: "Keep and highlight",
      text: prayersOn ? "Star a passage to keep it. Select any words, here or in your entry, to highlight them or add them to your prayer list."
                      : "Star a passage to keep it. Select any words, here or in your entry, to highlight them." },
    { el: () => $("question"), title: "To sit with",
      text: prayersOn ? "A question to carry through your day, or to pray about." : "A question to carry through your day." },
    { el: () => $("past"), title: "Your journal",
      text: prayersOn ? "Every entry is kept here. Mark favourites with the ribbon on the page; your kept passages and prayer list are at the top."
                      : "Every entry is kept here. Mark favourites with the ribbon on the page; your kept passages are at the top." },
    { el: () => (isShown($("settings-btn")) ? $("settings-btn") : $("menu-btn")), title: "Make it yours",
      text: "In Settings, choose the tone of your reflections and the writers on your bookshelf." },
  ];
  const isShown = (el) => !!(el && el.offsetParent !== null && el.getBoundingClientRect().width);
  let tourStep = -1, tourShownThisVisit = false;

  function placeTour() {
    const step = TOUR[tourStep];
    const el = step && step.el();
    if (!el) return;
    const r = el.getBoundingClientRect(), pad = 8;
    const spot = $("tour-spot");
    const top = Math.max(4, r.top - pad), height = Math.min(r.height + pad * 2, window.innerHeight - top - 4);
    Object.assign(spot.style, { top: `${top}px`, left: `${Math.max(4, r.left - pad)}px`, width: `${r.width + pad * 2}px`, height: `${height}px` });
    const bubble = $("tour-bubble"), arrow = $("tour-arrow");
    const bw = bubble.offsetWidth, bh = bubble.offsetHeight;
    const below = top + height + 14 + bh < window.innerHeight || top < bh + 24;
    const bTop = below ? Math.min(top + height + 14, window.innerHeight - bh - 8) : top - bh - 14;
    const centre = r.left + r.width / 2;
    const bLeft = Math.min(Math.max(12, centre - bw / 2), window.innerWidth - bw - 12);
    Object.assign(bubble.style, { top: `${Math.max(8, bTop)}px`, left: `${bLeft}px` });
    Object.assign(arrow.style, { left: `${Math.min(Math.max(14, centre - bLeft - 7), bw - 28)}px`,
      top: below ? "-7px" : "", bottom: below ? "" : "-7px" });
  }

  function showTourStep(i) {
    tourStep = i;
    const step = TOUR[i];
    $("tour-title").textContent = step.title;
    $("tour-text").textContent = step.text;
    $("tour-next").textContent = i === TOUR.length - 1 ? "Done" : "Next";
    $("tour-dots").replaceChildren(...TOUR.map((_, j) => { const d = document.createElement("i"); if (j === i) d.className = "on"; return d; }));
    const el = step.el();
    if (el) el.scrollIntoView({ block: el === $("past") ? "start" : "center", behavior: "instant" });
    placeTour();
    setTimeout(placeTour, 400);                          // after the scroll settles
  }

  function startTour() {
    if (!$("tour") || $("entry-view").hidden || $("reflection").hidden) return;
    hideToolbars();
    $("tour").hidden = false;
    showTourStep(0);
    $("tour-next").focus();
  }

  function endTour() {
    $("tour").hidden = true;
    tourStep = -1;
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (!CFG.tourDone) {
      CFG.tourDone = true;
      api("PUT", "/api/settings", { tour_done: true }, { passive: true }).catch(() => {});
    }
  }

  // The first time a finished reflection is on screen (once this visit, and only until they've seen it).
  function maybeStartTour(entry) {
    if (CFG.tourDone || tourShownThisVisit || !entry || entry.reflection_status !== "Done" || !entry.insight) return;
    tourShownThisVisit = true;
    setTimeout(() => { if (!document.querySelector("dialog[open]")) startTour(); else tourShownThisVisit = false; }, 900);
  }

  if ($("tour")) {
    $("tour-next").addEventListener("click", () => (tourStep < TOUR.length - 1 ? showTourStep(tourStep + 1) : endTour()));
    $("tour-skip").addEventListener("click", endTour);
    $("tour").querySelector(".tour-catch").addEventListener("click", endTour);
    document.addEventListener("keydown", (e) => {
      if ($("tour").hidden) return;
      if (e.key === "Escape") endTour();
      else if (e.key === "ArrowRight" || e.key === "Enter") { e.preventDefault(); $("tour-next").click(); }
      else if (e.key === "ArrowLeft" && tourStep > 0) showTourStep(tourStep - 1);
      e.stopPropagation();
    }, true);
    window.addEventListener("resize", () => { if (!$("tour").hidden) placeTour(); });
    window.addEventListener("scroll", () => { if (!$("tour").hidden) placeTour(); }, { passive: true });
    // Settings → Take the tour: on the newest entry that has a reflection.
    $("tour-again-btn").addEventListener("click", async () => {
      const withReflection = [...(state.allEntries || [])].reverse().find((e) => e.reflection_status === "Done");
      if (!withReflection) { showNotice("The tour starts with a reflection: write an entry and choose Finish & reflect first.", 5000); return; }
      await openEntry(withReflection.id);
      setTimeout(startTour, 700);
    });
  }

  // ---- Sharing a passage -----------------------------------------------------
  // By email (the passage and a note, never anything they wrote; the server checks
  // the passage against the library), or on phones through the share sheet.
  const citeText = (q) => {
    const where = !q.location ? "" : /^[\p{L}§]/u.test(q.location) ? `, ${q.location}` : ` ${q.location}`;
    return `${q.author ? `${q.author}, ` : ""}${q.title}${where}`;
  };
  let sharing = null;

  function shareLink(q) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "share-link";
    b.textContent = "Share";
    b.title = "Share this passage with someone";
    b.hidden = !CFG.hosted && !navigator.share;
    b.addEventListener("click", () => openSharePassage(q));
    return b;
  }

  // The passage's own page in the app (/p/<id>) when there is one; the source otherwise (your own computer).
  function sharedText(q, link) {
    const url = link || (q.url ? passageUrl(q.url, q.quote) : "");
    return `“${q.quote}”\n— ${citeText(q)}${url ? `\n${url}` : ""}`;
  }
  let sharingPreview = Promise.resolve({});

  function openSharePassage(q) {
    if (!CFG.hosted) {                                         // on your own computer: the share sheet only
      navigator.share({ text: sharedText(q) }).catch(() => {});
      return;
    }
    sharing = q;
    $("share-passage-quote").textContent = `“${q.quote}”`;
    $("share-passage-cite").textContent = `— ${citeText(q)}`;
    $("share-passage-error").hidden = true;
    $("share-other-btn").hidden = !navigator.share;
    const drawing = $("share-passage-drawing");
    drawing.hidden = true;
    $("share-passage-dialog").showModal();
    $("share-to").focus();
    // The passage's own drawing (its theme is chosen the first time it's shared, then saved), and its link.
    sharingPreview = api("POST", "/api/share/preview", { work: q.work, location: q.location, quote: q.quote }, { passive: true })
      .catch(() => ({}));
    sharingPreview
      .then((res) => {
        if (sharing !== q || !res.drawing) return;
        drawing.style.setProperty("--vignette", `url('${res.drawing}')`);
        drawing.title = res.theme;
        drawing.hidden = false;
      })
      .catch(() => {});
  }

  if ($("share-passage-form")) {
    $("share-passage-cancel").addEventListener("click", () => $("share-passage-dialog").close());
    $("share-other-btn").addEventListener("click", async () => {
      const note = $("share-note").value.trim();
      const { link } = await sharingPreview;
      try {
        await navigator.share({ text: `${note ? `${note}\n\n` : ""}${sharedText(sharing, link)}` });
        $("share-passage-dialog").close();
        if (link) api("POST", "/api/share/other", { link }, { passive: true }).catch(() => {});   // counted, never who
      } catch (_) { /* closed the share sheet */ }
    });
    $("share-passage-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("share-passage-send"), error = $("share-passage-error");
      btn.disabled = true;
      error.hidden = true;
      try {
        await api("POST", "/api/share", {
          to: $("share-to").value, name: $("share-name").value, note: $("share-note").value,
          work: sharing.work, location: sharing.location, quote: sharing.quote,
        });
        $("share-passage-dialog").close();
        showNotice(`Shared with ${$("share-to").value.trim()}.`, 4000);
        $("share-to").value = $("share-name").value = $("share-note").value = "";
      } catch (err) {
        error.textContent = sentence(err.message);
        error.hidden = false;
      } finally {
        btn.disabled = false;
      }
    });
  }

  // A drawing for the entry's themes, if there is one: a pencil sketch painted in the
  // theme's color through a cut-out (see tools/make_vignettes.py).
  // "Grief & loss" -> "grief-and-loss", "God's presence" -> "gods-presence" (the same rule as tools/make_vignettes.py)
  const themeSlug = (name) => name.toLowerCase().replace(/['’]/g, "").replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  // For variety it's one of the entry's top two themes, chosen from the entry's id, so
  // the same entry always shows the same drawing.
  function renderVignette(entry) {
    const box = $("reflection-vignette");
    const drawn = (entry.themes || []).slice(0, 2).filter((name) => (CFG.vignettes || {})[themeSlug(name)]);
    const seed = [...String(entry.id || "")].reduce((n, ch) => (n * 31 + ch.charCodeAt(0)) % 9973, 0);
    const theme = drawn[seed % Math.max(1, drawn.length)];
    const url = theme && CFG.vignettes[themeSlug(theme)];
    box.hidden = !url;
    box.style.setProperty("--vignette", url ? `url("${url}")` : "none");
    box.title = url ? theme : "";
  }

  // A reflection paragraph, easier to scan: quotations in italics, and the
  // works it draws on (from its sources list) in bold where they're named,
  // e.g. "Philippians 4:6" or "Brother Lawrence". Built as nodes, never HTML.
  function formatInsight(text, entry) {
    const names = new Set();
    for (const c of [...(entry.quotes || []), ...(entry.references || [])]) {
      if (c.author) names.add(c.author);
      if (c.title) names.add(c.title);
    }
    const escaped = [...names].filter((n) => n.length > 2).sort((a, b) => b.length - a.length)
      .map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    // A name, optionally followed by chapter and verse ("Psalm 139:23-24").
    const nameRe = escaped.length
      ? new RegExp(`(?<![\\p{L}])(?:${escaped.join("|")})(?:\\s\\d+(?::\\d+(?:[-–]\\d+)?)?)?(?![\\p{L}])`, "gu")
      : null;
    const frag = document.createDocumentFragment();
    const plain = (s) => {
      if (!nameRe || !s) { frag.append(document.createTextNode(s)); return; }
      let at = 0;
      for (const m of s.matchAll(nameRe)) {
        frag.append(document.createTextNode(s.slice(at, m.index)));
        const b = document.createElement("strong");
        b.textContent = m[0];
        frag.append(b);
        at = m.index + m[0].length;
      }
      frag.append(document.createTextNode(s.slice(at)));
    };
    let at = 0;
    for (const m of text.matchAll(/“[^”]+”|"[^"]+"/g)) {         // quotations
      plain(text.slice(at, m.index));
      const em = document.createElement("em");
      em.textContent = m[0];
      frag.append(em);
      // A word-for-word quotation (one the app checked) can be kept: its star follows it.
      const index = (entry.quotes || []).findIndex((q) => q.key && `“${q.quote}”` === m[0]);
      if (index >= 0) frag.append(quoteStar(entry, index));
      at = m.index + m[0].length;
    }
    plain(text.slice(at));
    return frag;
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

  // BibleHub's BSB pages, in the reader's chosen translation (Settings → Basic). The words
  // to scroll to are the BSB's, so for another translation the link opens the chapter.
  // Another translation can't be highlighted with the BSB's words, so a verse goes
  // through /read, which finds that translation's words and opens the chapter with the
  // verse highlighted; a chapter, or a passage without a verse, opens the chapter.
  function inTranslation(url, location) {
    const t = CFG.bibleTranslation || "bsb";
    if (t === "bsb" || !url.includes("://biblehub.com/bsb/")) return url;
    const verse = /^\s*(\d+):(\d+)/.exec(location || "");
    const m = /^(https:\/\/biblehub\.com)\/bsb\/([^/]+)\/(\d+)\.htm/.exec(url);
    if (verse && m && verse[1] === m[3]) return `/read?t=${t}&b=${encodeURIComponent(m[2])}&c=${m[3]}&v=${verse[2]}`;
    return url.replace("://biblehub.com/bsb/", `://biblehub.com/${t}/`).replace(/#.*$/, "");
  }

  function passageUrl(url, quote, location) {
    if ((CFG.bibleTranslation || "bsb") !== "bsb" && url.includes("://biblehub.com/bsb/")) return inTranslation(url, location);
    let words = String(quote || "").trim().split(/\s+/).filter(Boolean);
    // On BibleHub's chapter pages a verse's number runs into its first word ("14Indeed"),
    // and a highlight must start at the start of a word: so start from the second.
    if (words.length > 5) words = words.slice(1);
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
        // A new reflection after an edit failed; the earlier one is still shown.
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
    if (CFG.hosted) refreshStats();  // the credit coin
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
    renderBookmark(!!entry.favourite);
  }

  // ---- Favorites: the cloth bookmark at the top of an entry's page ------

  function renderBookmark(on) {
    const b = $("bookmark-btn");
    b.setAttribute("aria-pressed", String(on));
    const label = on ? "Remove from favorites" : "Add to favorites";
    b.title = label;
    b.setAttribute("aria-label", label);
  }

  async function toggleFavourite() {
    const entry = state.current;
    if (!entry) return;
    const want = !entry.favourite;
    entry.favourite = want;
    renderBookmark(want);  // show it straight away; undo if saving fails
    try {
      const res = await api("POST", `/api/entries/${entry.id}/favourite`, { favourite: want });
      if (state.current && state.current.id === entry.id) state.current.favourite = res.entry.favourite;
      refreshList();
    } catch (err) {
      entry.favourite = !want;
      if (state.current && state.current.id === entry.id) renderBookmark(!want);
      state.clientError = `Couldn't update favorites: ${err.message}`;
      renderBanner();
    }
  }

  // ---- Filters: All · Favorites · Calendar (dates) ----------------------
  // Favorites and a date range can be combined; All clears both.
  // The favorites choice is remembered; a date range lasts until All.

  const FILTER_KEY = "journal.filter";
  state.favOnly = (() => { try { return localStorage.getItem(FILTER_KEY) === "fav"; } catch (_) { return false; } })();

  function showAll() {
    state.favOnly = false;
    state.range = null;
    state.theme = null;
    applyFilters();
  }

  function toggleFavourites() {
    state.favOnly = !state.favOnly;
    applyFilters();
  }

  function applyFilters() {
    try { localStorage.setItem(FILTER_KEY, state.favOnly ? "fav" : "all"); } catch (_) {}
    $("filter-all").setAttribute("aria-pressed", String(!state.favOnly && !state.range && !state.theme));
    if ($("themes-btn")) {
      $("themes-btn").setAttribute("aria-pressed", String(!!state.theme));
      $("themes-btn").textContent = state.theme ? `Theme: ${state.theme}` : "Themes";
    }
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
    $("bookmark-btn").hidden = on;  // bookmarking is for reading, not editing
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
    if (state.search) runSearch();     // results follow edits and deletions
    renderThemes();
    backfillThemes();
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

  // "Thu, Oct 8, 2026 · 60 words" (no time: it only adds clutter).
  function listDate(line, entry) {
    line.append(document.createTextNode(`${shortDate(entry.created_at)} · ${plural(entry.word_count, "word")}`));
  }

  // An entry's themes, quietly, at the end of its date line in Past entries ("· Work, Rest").
  // The theme being filtered on, if any, stands out in the accent color.
  function listThemes(line, entry) {
    const names = (CFG.themes && entry.themes) || [];
    if (!names.length) return;
    const box = document.createElement("span");
    box.className = "list-themes";
    box.append(document.createTextNode(" · "));
    names.forEach((name, i) => {
      if (i) box.append(document.createTextNode(", "));
      const t = document.createElement("span");
      t.textContent = name;
      if (name === state.theme) t.className = "current";
      box.append(t);
    });
    line.append(box);
  }

  // The small ribbon beside a favorite entry's title.
  function favMark() {
    const mark = document.createElement("span");
    mark.className = "fav-mark";
    mark.title = "Favorite";
    return mark;
  }

  function renderList() {
    const all = state.allEntries;
    if (!all) return;
    if (state.search && state.search.results) return renderSearch();
    const entries = shown().slice();
    const n = entries.length;
    const noun = state.favOnly ? (n === 1 ? "favorite" : "favorites") : (n === 1 ? "entry" : "entries");
    $("filter-note").hidden = !state.favOnly && !state.range && !state.theme;
    $("filter-note").textContent = `Showing ${n} ${noun}${state.range ? ` ${state.range.prep} ${state.range.label}` : ""}`
      + (state.theme ? ` about “${state.theme}”` : "");
    {
      const ul = $("entry-list");
      ul.replaceChildren();
      const newestFirst = entries.reverse();
      for (const e of newestFirst.slice(0, state.listLimit)) {
        const li = document.createElement("li");
        const when = document.createElement("span");
        when.className = "when";
        listDate(when, e);
        listThemes(when, e);
        const title = document.createElement("span");
        title.className = e.title ? "title" : "title untitled";
        title.textContent = e.title || "No Title";
        if (e.favourite) {
          title.prepend(favMark());
        }
        const snippet = document.createElement("span");
        snippet.className = "snippet";
        snippet.textContent = e.snippet + (e.snippet.length >= 120 ? "…" : "");
        li.append(when, title, snippet);
        const s = state.lastStats;
        if (s && s.longest_id === e.id && s.shown_entries > RECORD_FROM) {
          const tag = document.createElement("span");
          tag.className = "longest-tag";
          tag.textContent = " ✦ longest";
          tag.title = "Your longest entry so far";
          when.append(tag);
        }
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
          : state.range ? `No ${state.favOnly ? "favorites" : "entries"} ${state.range.prep} ${state.range.label}. Click All to see everything.`
          : "No favorites yet. Open an entry and click the ribbon at the top of its page to add it here.";
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
  // ---- Themes ------------------------------------------------------------
  // Each entry has 1-3 themes from the edition's list (given with its
  // reflection; earlier entries are tagged quietly while the journal is open).

  function setTheme(theme) {
    state.theme = theme;
    applyFilters();
  }

  function themeChip(name, count, onPick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "theme-chip";
    b.setAttribute("aria-pressed", String(state.theme === name));
    b.append(document.createTextNode(name));
    if (count != null) {
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = count;
      b.append(n);
    }
    b.addEventListener("click", onPick);
    return b;
  }

  function renderThemes() {
    if (!$("theme-chips")) return;
    const counts = new Map();
    for (const e of state.allEntries || []) for (const t of e.themes || []) counts.set(t, (counts.get(t) || 0) + 1);
    const names = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b));
    $("theme-chips").replaceChildren(...names.map((name) => themeChip(name, counts.get(name), () => {
      setTheme(state.theme === name ? null : name);
      toggleThemes(false);
    })));
    const untagged = (state.allEntries || []).filter((e) => e.themes == null).length;
    $("themes-status").textContent = !names.length && !untagged ? "Themes appear as your entries are reflected on."
      : untagged ? `Sorting ${untagged} earlier ${untagged === 1 ? "entry" : "entries"} into themes…`
      : state.theme ? "Click the theme again, or All, to see everything." : "";
  }

  function toggleThemes(open = $("themes-pop").hidden) {
    $("themes-pop").hidden = !open;
    $("themes-btn").setAttribute("aria-expanded", String(open));
    if (open) renderThemes();
  }
  if ($("themes-btn")) {
    $("themes-btn").addEventListener("click", () => toggleThemes());
    document.addEventListener("mousedown", (e) => {
      if (!$("themes-pop").hidden && !$("themes-pop").contains(e.target) && !$("themes-btn").contains(e.target)) toggleThemes(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("themes-pop").hidden) { toggleThemes(false); $("themes-btn").focus(); }
    });
  }

  // Under an entry: its themes; clicking one shows every entry with that theme.
  function renderEntryThemes(entry) {
    const box = $("entry-themes");
    const list = entry.themes || [];
    box.hidden = !CFG.themes || !list.length;
    box.replaceChildren(...list.map((name) => themeChip(name, null, () => {
      setTheme(name);
      $("past").scrollIntoView({ behavior: "smooth", block: "start" });
    })));
  }

  // Earlier entries without themes are tagged a few at a time while the journal is open.
  let backfilling = false;
  async function backfillThemes() {
    if (!CFG.themes || backfilling) return;
    if (!(state.allEntries || []).some((e) => e.themes == null)) return;
    backfilling = true;
    try {
      for (let round = 0; round < 60; round++) {
        const res = await api("POST", "/api/themes/backfill", null, { passive: true });
        if (!res.tagged || !res.remaining) break;
        await new Promise((r) => setTimeout(r, 1500));
      }
    } catch (_) { /* try again next time the list loads */ }
    backfilling = false;
    try { state.allEntries = await api("GET", "/api/entries", null, { passive: true }); } catch (_) { return; }
    renderList();
    renderThemes();
    if (state.current) {
      const fresh = state.allEntries.find((e) => e.id === state.current.id);
      if (fresh && fresh.themes) { state.current.themes = fresh.themes; renderEntryThemes(state.current); }
    }
  }

  // ---- Search ------------------------------------------------------------
  // Results replace the list while there's a search; they respect the
  // Favorites and Calendar filters. Clearing the box brings the list back.

  // Text with the given [start, end] ranges wrapped in <mark> (built as nodes, never HTML).
  function highlighted(text, marks) {
    const frag = document.createDocumentFragment();
    let at = 0;
    for (const [s, e] of [...(marks || [])].sort((a, b) => a[0] - b[0])) {
      if (s < at) continue;
      frag.append(document.createTextNode(text.slice(at, s)));
      const m = document.createElement("mark");
      m.textContent = text.slice(s, e);
      frag.append(m);
      at = e;
    }
    frag.append(document.createTextNode(text.slice(at)));
    return frag;
  }

  function renderSearch() {
    const { q, results } = state.search;
    const byId = new Map((state.allEntries || []).map((e) => [e.id, e]));
    const allowed = new Set(shown().map((e) => e.id));
    const hits = results.filter((r) => allowed.has(r.id) && byId.has(r.id));
    const ul = $("entry-list");
    ul.replaceChildren();
    if (moreObserver) { moreObserver.disconnect(); moreObserver = null; }
    const scope = filterDescription();
    $("filter-note").hidden = false;
    $("filter-note").textContent = hits.length
      ? `${hits.length} ${hits.length === 1 ? "entry" : "entries"} matching “${q}”${scope ? ` (${scope})` : ""}${hits.length >= 60 ? ", best first" : ""}`
      : "";
    if (!hits.length) {
      const li = document.createElement("li");
      li.className = "subtle";
      li.textContent = `Nothing found for “${q}”${scope ? ` in ${scope}` : ""}. Try fewer or different words.`;
      ul.append(li);
      return;
    }
    for (const r of hits) {
      const e = byId.get(r.id);
      const li = document.createElement("li");
      const when = document.createElement("span");
      when.className = "when";
      listDate(when, e);
        listThemes(when, e);
      if (r.field === "reflection" || r.field === "question") {
        const where = document.createElement("span");
        where.className = "found-in";
        where.textContent = `in the ${r.field}`;
        when.append(where);
      }
      const title = document.createElement("span");
      title.className = e.title ? "title" : "title untitled";
      title.append(e.title ? highlighted(e.title, r.title_marks) : document.createTextNode("No Title"));
      if (e.favourite) {
        title.prepend(favMark());
      }
      const snippet = document.createElement("span");
      snippet.className = "snippet";
      snippet.append(r.snippet ? highlighted(r.snippet, r.marks) : document.createTextNode(e.snippet));
      li.append(when, title, snippet);
      li.addEventListener("click", () => openEntry(e.id));
      ul.append(li);
    }
  }

  let searchTimer = null, searchSeq = 0;
  function runSearch() {
    clearTimeout(searchTimer);
    const q = $("search-input").value.trim();
    if (q.length < 2) {
      state.search = null;
      renderList();
      return;
    }
    searchTimer = setTimeout(async () => {
      const seq = ++searchSeq;                           // only the latest search's results are shown
      try {
        const res = await api("GET", `/api/search?q=${encodeURIComponent(q)}`);
        if (seq !== searchSeq) return;
        state.search = { q, results: res.results };
        renderList();
      } catch (_) { /* keep what's shown */ }
    }, 220);
  }
  $("search-input").addEventListener("input", runSearch);
  $("search-input").addEventListener("keydown", (e) => {
    if (e.key === "Escape" && e.target.value) { e.preventDefault(); e.target.value = ""; runSearch(); }
  });

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
      showEntry(res.entry, { keepScroll: true });
      refreshList();
      checkMilestone(before, res.entry);
      requestReflection(res.entry.id);  // the entry is already safely saved
      // Once the page has settled (after the turn), bring the "Reflecting…"
      // panel into view if it isn't already, so a long entry still shows it.
      setTimeout(() => {
        const w = $("reflection-working");
        if (w.hidden) return;
        const r = w.getBoundingClientRect();
        if (r.top < 0 || r.bottom > innerHeight) w.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 1100);
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
      if (res.entry.can_reflect_again) {
        $("entry-status").textContent = "Saved. The reflection below is from before your changes; you can ask for one on the edited entry below it.";
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

  // Settings tabs: Basic, Theme, Bookshelf, Security & Data, Admin (admins only).
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
      // Bookshelf: a writer is on unless the saved choices (or the edition's default) say otherwise.
      for (const box of document.querySelectorAll("#panel-library [data-writer]")) {
        const chosen = (settings.writers || {})[box.dataset.writer];
        box.checked = chosen === undefined ? box.dataset.default === "1" : !!chosen;
      }
      syncPhotoControls();
      if ($("admin-digest")) $("admin-digest").checked = settings.admin_digest !== false;
      settingsDirty = false;                            // freshly loaded: nothing changed yet
    } catch (err) {
      $("settings-state").textContent = `Couldn't load settings: ${err.message}`;
    }
    refreshHidden();
    refreshPasskeys();
    refreshUsage();
    refreshInviteRequests();
    refreshFeedbackList();
  }

  // The cover carries the journal's own name if it has one, without the app's tagline.
  function showCoverName(custom) {
    const name = custom || CFG.appName;
    $("cover-name").textContent = name;
    $("cover-name").classList.toggle("long-name", name.length > 14);
    if ($("cover-tagline")) $("cover-tagline").hidden = name !== CFG.appName;
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
    const writers = [...document.querySelectorAll("#panel-library [data-writer]")];
    if (writers.length) out.writers = Object.fromEntries(writers.map((box) => [box.dataset.writer, box.checked]));
    try {
      const res = await api("PUT", "/api/settings", out);
      CFG.userName = res.settings.user_name;
      CFG.bibleTranslation = res.settings.bible_translation || "bsb";
      $("journal-name").textContent = res.settings.journal_name || CFG.appName;
      showCoverName(res.settings.journal_name);
      CFG.autosaveSeconds = res.settings.draft_autosave_seconds;
      savedLook.theme = res.settings.theme;
      savedLook.palette = res.settings.colour_theme;
      savedLook.textSize = res.settings.text_size;
      savedLook.blur = res.settings.photo_blur;
      savedLook.visibility = res.settings.photo_visibility;
      const photoWas = savedLook.photo;
      savedLook.photo = res.settings.background_photo ? "on" : "off";
      restoreSavedLook();
      if (savedLook.photo !== photoWas) loadBackground();  // only when switched on/off; Save alone keeps the photo
      restartAutosave();
      renderGreeting();
      settingsDirty = false;
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
    state.theme = null;
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

  // On a phone, the header's buttons live in a menu behind ☰.
  function setHeaderMenu(open) {
    $("header-actions").classList.toggle("open", open);
    document.querySelector(".book-header").classList.toggle("menu-open", open);   // above the page, for the menu
    $("menu-btn").setAttribute("aria-expanded", String(open));
  }
  $("menu-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    setHeaderMenu(!$("header-actions").classList.contains("open"));
  });
  $("header-actions").addEventListener("click", () => setHeaderMenu(false));   // chose something
  document.addEventListener("click", (e) => {
    if (!$("header-actions").contains(e.target)) setHeaderMenu(false);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setHeaderMenu(false); });
  $("settings-back-btn").addEventListener("click", () => {
    restoreSavedLook();  // Discard: undo any unsaved preview, save nothing
    settingsDirty = false;
    leaveSettings();
  });
  // Preview look changes live (sliders update while dragging).
  const LOOK_FIELDS = ["theme", "colour_theme", "text_size", "background_photo", "photo_blur", "photo_visibility"];
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
  const currentScreen = () => VIEWS.find((n) => !$(`${n}-view`).hidden) || "";

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
        + "Tell us what to fix, improve or add, any time, with the lightbulb at the top.",
      go: () => "Send feedback",
      onGo: () => { remember("journal.nudge.feedback", 1); openFeedback(); return true; },
      later: () => remember("journal.nudge.feedback", 1),
    },
    {
      id: "invite",
      ready: (s) => CFG.hosted && !s.invites_sent && INVITE_AT.some((n) => s.total_entries >= n && n > (Number(recall("journal.nudge.invite")) || 0)),
      text: () => `<strong>Know someone who'd enjoy this?</strong> ${esc(CFG.appName)} is invitation-only while it's in beta, `
        + `and grows through people like you. ${esc(CFG.inviteLine)}, any time, with the giving hand at the top.`,
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
    const waiting = items.filter((i) => i.status === "requested").length;
    $("invite-count").hidden = !waiting;
    $("invite-count").textContent = waiting;
    ul.replaceChildren();
    for (const i of shown) {
      // A card per request: who, who asked and when, their notes, then the buttons.
      const li = document.createElement("li");
      li.className = `invite-card${i.status === "requested" ? "" : " done"}`;
      const who = document.createElement("p");
      who.className = "invite-who";
      const name = document.createElement("strong");
      name.textContent = i.name || i.email;
      who.append(name);
      if (i.name) {
        const addr = document.createElement("span");
        addr.className = "invite-addr";
        addr.textContent = i.email;
        who.append(" ", addr);
      }
      const asked = document.createElement("p");
      asked.className = "invite-asked";
      asked.textContent = `Asked by ${i.requester_name || i.requester_email || "someone"} · ${longDate(i.created_at)}`;
      li.append(who, asked);
      for (const [label, text] of [["For them", i.message], ["To you", i.note]]) {
        if (!text) continue;
        const note = document.createElement("p");
        note.className = "invite-note";
        const tag = document.createElement("span");
        tag.textContent = `${label}: `;
        note.append(tag, `“${text}”`);
        li.append(note);
      }
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
      actions.append(status);
      if (i.emailed_at) {
        const sent = document.createElement("span");
        sent.className = "invite-emailed";
        sent.textContent = `emailed ${longDate(i.emailed_at)}`;
        actions.append(sent);
      }
      // Invite: let them in and email them; once in, the email can be sent again.
      const send = document.createElement("button");
      send.type = "button";
      send.textContent = i.status !== "added" ? "Invite" : i.emailed_at ? "Email again" : "Send email";
      if (i.status === "requested") send.className = "primary";
      send.addEventListener("click", async () => {
        send.disabled = true;
        try {
          const res = await api("POST", `/api/admin/invites/${i.id}/invite`);
          if (res.warning) showNotice(res.warning, 8000);
          else showNotice(`Invitation sent to ${i.email}.`, 4000);
          refreshInviteRequests();
        } catch (err) {
          send.disabled = false;
          state.clientError = `Couldn't invite them: ${sentence(err.message)}`; renderBanner();
        }
      });
      actions.append(send, copy);
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
      li.append(actions);
      ul.append(li);
    }
    if (!shown.length) {
      const li = document.createElement("li");
      li.className = "subtle invite-none";
      li.textContent = items.length ? "No requests waiting." : "No invitation requests yet.";
      ul.append(li);
    }
    refreshUsage();
    refreshThemeGaps();
  }
  if ($("invites-show-all")) $("invites-show-all").addEventListener("change", refreshInviteRequests);

  // Admin → "Email me when there's new work": outside the settings form, so it saves as it's clicked.
  if ($("admin-digest")) {
    $("admin-digest").addEventListener("change", async () => {
      const box = $("admin-digest");
      try {
        await api("PUT", "/api/settings", { admin_digest: box.checked });
        showNotice(box.checked ? "You'll get an email when there's new work." : "No more new-work emails.", 3000);
      } catch (err) {
        box.checked = !box.checked;
        state.clientError = `Couldn't save that: ${sentence(err.message)}`; renderBanner();
      }
    });
  }

  // Settings → Passages that need a theme (only for whoever runs the app).
  async function refreshThemeGaps() {
    const ul = $("theme-gaps");
    if (!ul) return;
    let items;
    try { items = await api("GET", "/api/admin/passages/themes"); } catch (_) { return; }
    ul.replaceChildren();
    for (const p of items) {
      const li = document.createElement("li");
      const meta = document.createElement("div");
      meta.className = "feedback-meta";
      meta.textContent = [p.work + (p.location ? ` ${p.location}` : ""), plural(p.shares, "share"),
        p.theme ? `given “${p.theme}”` : p.theme_problem ? "the pick failed" : "no theme fitted"].join(" · ");
      const body = document.createElement("p");
      body.className = "feedback-body";
      body.textContent = `“${p.quote}”`
        + (p.missing_theme ? `\nA theme that would fit: ${p.missing_theme}` : "")
        + (p.theme_problem ? `\nWhy: ${p.theme_problem}` : "");
      li.append(meta, body);
      ul.append(li);
    }
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "subtle";
      li.textContent = "None yet: every shared passage has a theme that fits.";
      ul.append(li);
    }
  }

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
      // Tokens per call to Claude (what each one costs): average in / out, last 30 days.
      ...(u.tokens || []).map((t) => tile(`${t.label} tokens`, `${t.avg_input.toLocaleString()} / ${t.avg_output.toLocaleString()}`,
        `average in / out over ${plural(t.calls, "call")}, last 30 days`)),
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
    const access = u.rows.some((r) => r.access != null);   // on the web: who can sign in, with Remove
    for (const h of ["Person", "Last visit", "Days active", "Entries (all)", "Reflections", "Joined", ...(access ? [""] : [])]) {
      const th = document.createElement("th"); th.textContent = h; head.append(th);
    }
    const body = table.createTBody();
    for (const r of u.rows) {
      const tr = body.insertRow();
      if (r.access === false) tr.className = "no-access";
      const note = r.invited_only ? " (invited, not joined yet)" : r.access === false ? " (no access)" : r.set_up ? "" : " (not set up)";
      const cells = [
        r.who.replace("@", "@​") + note,  // long emails break after the @
        r.invited_only ? "" : daysAgo(r.days_since),
        r.invited_only ? "" : String(r.active_days),
        r.invited_only ? "" : `${r.entries_recent} (${r.entries_total})`,
        r.invited_only ? "" : String(r.reflections_recent),
        r.joined,
      ];
      for (const c of cells) tr.insertCell().textContent = c;
      if (!access) continue;
      const cell = tr.insertCell();
      if (r.me || !r.email) continue;                        // never yourself
      if (r.access) {
        // Email: what's new for someone who's joined, the invitation for someone who hasn't.
        const mail = document.createElement("button");
        mail.type = "button";
        mail.className = "usage-access-btn";
        mail.textContent = "Email";
        if (r.shared_at) mail.title = `Last emailed ${longDate(r.shared_at)}`;
        mail.addEventListener("click", () => openShare(r));
        cell.append(mail, " ");
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "usage-access-btn";
      btn.textContent = r.access ? "Remove" : "Let back in";
      btn.addEventListener("click", async () => {
        if (r.access && !confirm(`Remove access for ${r.email}? Their journal stays, should you let them back in.`)) return;
        btn.disabled = true;
        try {
          const res = await api(r.access ? "DELETE" : "POST", "/api/admin/access", { email: r.email });
          if (res.warning) showNotice(res.warning, 8000);
          refreshUsage();
          refreshInviteRequests();
        } catch (err) {
          btn.disabled = false;
          state.clientError = `Couldn't change their access: ${sentence(err.message)}`; renderBanner();
        }
      });
      cell.append(btn);
    }
  }

  // Settings → Usage → Email (whoever runs the app): the app's email to one person.
  let shareTo = null;
  function openShare(r) {
    shareTo = r;
    $("share-who").textContent = r.email;
    $("share-what").textContent = (r.invited_only
      ? "They haven't signed in yet, so this sends the invitation email."
      : "They already use the app, so this sends the email as \u201cwhat's new\u201d, without the sign-up steps.")
      + (r.shared_at ? ` Last emailed ${longDate(r.shared_at)}.` : "");
    $("share-message").value = "";
    $("share-error").hidden = true;
    $("share-dialog").showModal();
  }
  if ($("share-form")) {
    $("share-cancel-btn").addEventListener("click", () => $("share-dialog").close());
    $("share-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("share-send-btn");
      btn.disabled = true;
      try {
        await api("POST", "/api/admin/share", { email: shareTo.email, message: $("share-message").value });
        $("share-dialog").close();
        showNotice(`Email sent to ${shareTo.email}.`, 4000);
        refreshUsage();
      } catch (err) {
        $("share-error").textContent = sentence(err.message);
        $("share-error").hidden = false;
      } finally {
        btn.disabled = false;
      }
    });
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
  $("bookmark-btn").addEventListener("click", toggleFavourite);
  $("kept-btn").addEventListener("click", showKept);
  $("kept-back-btn").addEventListener("click", leaveSettings);
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
    // So do j / k, as in Gmail: j = older (down the list), k = newer.
    if (document.querySelector("dialog[open]") || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const back = e.key === "ArrowLeft" || e.key === "j", on = e.key === "ArrowRight" || e.key === "k";
    if (!$("write-view").hidden && back) { e.preventDefault(); olderFromWritingPage(); return; }
    if ($("entry-view").hidden || !$("entry-editor").hidden) return;
    if (back) { e.preventDefault(); goOlder(); }
    if (on) { e.preventDefault(); goNewer(); }
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

  // Signed in but locked (or setting up), the journal shows its cover: the
  // header (its buttons disabled) above a page in the theme's cover color,
  // with the passphrase card on it. Signed out, the card is on the welcome page.
  const HEADER_BUTTONS = "#header-actions button, #menu-btn, #credit-coin";
  function setCover(on) {
    const card = $("vault-card"), foot = $("vault-footer");
    if (on) {
      $("cover-slot").append(card);
      $("cover-foot").append(foot);
    } else {
      $("vault").append(card);
      card.append(foot);
    }
    $("vault").hidden = on;
    $("book").hidden = !on;
    $("book").classList.toggle("locked", on);
    $("cover-view").hidden = !on;
    if (on) {
      for (const v of VIEWS) $(`${v}-view`).hidden = true;
      $("past").hidden = true;
      $("stats").replaceChildren();
      matchCoverToPage();
    }
    for (const b of document.querySelectorAll(HEADER_BUTTONS)) b.disabled = on;
    const coin = $("credit-coin");
    if (coin && on) {                          // shown, but blank: the count comes with the unlocked journal
      toggleCreditPop(false);
      coin.hidden = false;
      coin.classList.add("empty");
      coin.querySelector(".coin").textContent = "";
      coin.title = "Unlock your journal first";
    }
  }

  // The cover is exactly as tall as the writing page beneath it, so unlocking
  // turns it over onto a page of the same size, like a real book.
  function matchCoverToPage() {
    const cover = $("cover-view"), page = $("write-view");
    if (cover.hidden) return;
    const wasHidden = page.hidden;
    page.style.visibility = "hidden";               // measured in place, never painted
    page.hidden = false;
    const h = page.offsetHeight;
    page.hidden = wasHidden;
    page.style.visibility = "";
    cover.style.minHeight = h ? `${h}px` : "";
  }
  window.addEventListener("resize", () => { if (!$("cover-view").hidden) matchCoverToPage(); });
  // Again once the fonts have loaded: until then the writing page is measured in a fallback font.
  if (document.fonts) document.fonts.ready.then(() => matchCoverToPage());

  function showVaultPanel(name) {
    setCover(name !== "signin");
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
  // The welcome page's sample reflection: a page of the journal that turns open
  // from the spine, and turns away again on ×, Esc or a click outside it.
  if ($("sample-btn")) {
    const dialog = $("sample-dialog");
    const page = dialog.querySelector(".sample-page");
    const play = (name, then) => {
      page.classList.remove("opening", "closing");
      void page.offsetWidth;                                    // restart the animation
      page.classList.add(name);
      let finished = false;
      const onEnd = (e) => { if (e.target === page) end(); };   // not the page's inner animations
      const end = () => {
        if (finished) return;
        finished = true;
        page.removeEventListener("animationend", onEnd);
        page.classList.remove(name);
        if (then) then();
      };
      page.addEventListener("animationend", onEnd);
      setTimeout(end, 800);                                     // in case no animation runs (reduced motion)
    };
    const shut = () => { if (dialog.open && !page.classList.contains("closing")) play("closing", () => dialog.close()); };
    $("sample-btn").addEventListener("click", () => { dialog.showModal(); dialog.scrollTop = 0; play("opening"); });
    $("sample-close").addEventListener("click", shut);
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); shut(); });   // Esc
    dialog.addEventListener("click", (e) => { if (e.target === dialog) shut(); });
  }
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
  // ---- Closing the book (the padlock) -------------------------------------
  // Everything the unlocked journal put in this page is cleared here, without
  // reloading it: what's remembered (entries, search results, the draft),
  // what's written on the pages now out of sight, and the timers that fetch more.
  const FORGET = [   // emptied
    "entry-list", "cal-grid", "theme-chips", "entry-themes", "entry-words", "insight", "citations", "hidden-list",
    "passkey-list", "usage-tiles", "usage-chart", "usage-table", "invite-requests", "feedback-list", "my-invites",
    "dialog-title", "dialog-body", "dialog-buttons", "entry-date", "entry-title", "entry-text", "entry-status", "nav-pos",
    "question", "support-note", "reflection-missing-text", "prompt-text", "prompt-source", "prompt-status",
    "draft-state", "filter-note", "themes-status", "nudge-text", "new-recovery-key",
    "new-recovery-result", "password-result", "passkey-result", "import-result", "test-key-result", "usage-since",
    "wipe-count", "wipe-number", "wipe-error", "feedback-error", "invite-error", "settings-state",
    "credit-pop-title", "credit-pop-text", "kept-list", "kept-intro",
  ];
  function forgetJournal() {
    lockGen++;                                    // answers still on their way are dropped (see api)
    openToken++;
    searchSeq++;
    for (const t of [pollTimer, searchTimer, promptNudgeTimer, noticeTimer]) clearTimeout(t);
    for (const t of [autosaveTimer, statusTimer, clockTimer]) clearInterval(t);
    if (moreObserver) { moreObserver.disconnect(); moreObserver = null; }
    wipeChallenge = null;
    activeNudge = null;
    Object.assign(state, {
      prompt: null, promptsShown: [], current: null, draftDirty: false, clientError: null, notice: null,
      allEntries: null, lastStats: null, search: null, navTarget: null, turn: null, olderId: null, newerId: null,
      range: null, theme: null, listLimit: LIST_FIRST, passkeyInfo: null, popStats: false,
    });
    for (const d of document.querySelectorAll("dialog[open]")) d.close();
    restoreSavedLook();                           // an unsaved preview in Settings
    setHeaderMenu(false);
    toggleCalendar(false);
    if ($("themes-btn")) toggleThemes(false);
    for (const id of FORGET) if ($(id)) $(id).replaceChildren();
    // Anything typed: the writing page, an entry being edited, search, passphrases, unsent feedback.
    // (Settings' own boxes are filled in afresh each time it opens.)
    for (const el of document.querySelectorAll("#book textarea, #book input, dialog textarea, dialog input")) {
      if (el.closest("#settings-form") || ["checkbox", "radio", "range", "file", "submit", "button"].includes(el.type)) continue;
      el.value = "";
    }
    for (const id of ["reflection", "support-note", "entry-themes", "new-recovery-box", "my-invites-box", "install-invite"]) {
      if ($(id)) $(id).hidden = true;
    }
    $("word-count").textContent = plural(0, "word");
    if ($("credit-coin")) delete $("credit-coin").dataset.left;
    setEditing(false);
    renderPrompt();
    applyFilters();
    renderBanner();
  }

  $("lock-btn").addEventListener("click", async () => {
    const from = VIEWS.map((v) => $(`${v}-view`)).find((el) => !el.hidden);
    if (state.draftDirty) await saveDraftToServer();          // what's being written is kept, encrypted
    if (!state.draftDirty) localClear();                      // ...so this browser's readable copy can go
    const ghost = from ? oldPageCopy(from) : null;            // the page as it was, for the cover to land on
    let locked = true;
    await Promise.all([api("POST", "/api/vault/lock").catch(() => { locked = false; }), scrollToTop()]);
    window.scrollTo(0, 0);                                    // if the smooth scroll was cut short
    state.unlocked = false;
    if (!locked) return location.reload();                    // couldn't reach the journal: show where things stand
    // The book closes: the cover swings over from the spine onto the page you were on.
    const passkey = state.passkeyUnlock;
    forgetJournal();
    showVaultPanel("unlock");
    $("passkey-unlock").hidden = !passkey;
    if (document.activeElement) document.activeElement.blur();
    turnPage($("cover-view"), -1, ghost);
    setTimeout(() => {
      if (state.unlocked) return;
      state.clientError = null;                               // from a request the lock cut short
      renderBanner();
      (passkey ? $("passkey-unlock-btn") : $("unlock-pw")).focus({ preventScroll: true });
    }, 700);
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
    if (err && err.name === "NotAllowedError") return "The passkey prompt was canceled or timed out.";
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
    state.passkeyUnlock = list.length > 0 && passkeysPossible();   // for the cover, when the book is closed
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
    } catch (err) {
      out.textContent = sentence(passkeyError(err, "Couldn't add the passkey."));
    } finally {
      btn.disabled = false;
    }
  });

  // Only ever started by the "Unlock with passkey" button.
  async function passkeyUnlock() {
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
      startJournal();
    } catch (err) {
      vaultError("unlock-error", passkeyError(err, "The passkey didn't unlock the journal"));
    } finally {
      btn.disabled = false;
    }
  }
  $("passkey-unlock-btn").addEventListener("click", () => passkeyUnlock());

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

  // "Written to …" under a reflection: Settings → Basic, at the Tone switch.
  $("reflection-tone").addEventListener("click", async () => {
    state.settingsTab = "basic";
    await showSettings();
    const tone = document.querySelector(".tone-choice");
    if (!tone) return;
    tone.scrollIntoView({ block: "center", behavior: "smooth" });
    tone.classList.remove("just-shown"); void tone.offsetWidth; tone.classList.add("just-shown");
  });

  // Signing in with an emailed code: any address that's been invited.
  if ($("email-signin-open")) {
    const showError = (msg) => { $("signin-error").textContent = msg; $("signin-error").hidden = !msg; };
    $("email-signin-open").addEventListener("click", () => {
      $("email-signin-open").hidden = true;
      $("email-signin-form").hidden = false;
      $("email-signin-address").focus();
    });
    $("email-signin-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = e.submitter || $("email-signin-form").querySelector("button");
      const address = $("email-signin-address").value.trim();
      btn.disabled = true;
      showError("");
      try {
        await api("POST", "/auth/email/start", { email: address });
        $("email-code-sent").textContent = `If ${address} has been invited, a code is on its way. Check your inbox (and spam). `
          + "Not there? Make sure you used the address you were invited with.";
        $("email-signin-form").hidden = true;
        $("email-code-form").hidden = false;
        $("email-code").value = "";
        $("email-code").focus();
      } catch (err) {
        showError(sentence(err.message));
      } finally {
        btn.disabled = false;
      }
    });
    $("email-code-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("email-code-form").querySelector("button[type=submit]");
      btn.disabled = true;
      showError("");
      try {
        await api("POST", "/auth/email/verify", { email: $("email-signin-address").value.trim(), code: $("email-code").value });
        location.replace("/");
      } catch (err) {
        showError(sentence(err.message));
        btn.disabled = false;
      }
    });
    $("email-code-back").addEventListener("click", () => {
      $("email-code-form").hidden = true;
      $("email-signin-form").hidden = false;
      showError("");
      $("email-signin-address").focus();
    });
  }

  // ---- Home, and not losing unsaved changes --------------------------------------
  // The journal's name goes back to today's page. Anything that leaves the page
  // someone is changing (an entry being edited, Settings not yet saved) asks first.
  // Pop-ups that open on top (Feedback, Invite, the coin) leave the work where it is.
  let settingsDirty = false;
  $("settings-form").addEventListener("input", () => { settingsDirty = true; });
  $("settings-form").addEventListener("change", () => { settingsDirty = true; });

  function unsavedWork() {
    if (!$("entry-view").hidden && !$("entry-editor").hidden && state.current) {
      const e = state.current;
      const { date, time } = localParts(e.created_at);
      if ($("entry-editor").value !== (e.text || "") || $("entry-title-input").value !== (e.title || "")
          || $("entry-date-input").value !== date || $("entry-time-input").value !== time) return "entry";
    }
    if (!$("settings-view").hidden && settingsDirty) return "settings";
    return null;
  }

  function discardUnsaved(kind) {
    if (kind === "entry") setEditing(false);
    if (kind === "settings") { restoreSavedLook(); settingsDirty = false; }
  }

  async function okToLeave() {
    const kind = unsavedWork();
    if (!kind) return true;
    const what = kind === "entry" ? "changes to this entry" : "changes in Settings";
    const leave = await ask("Leave without saving?", [`You have ${what} that haven't been saved. If you leave now, they'll be lost.`], [
      { label: "Keep editing", value: false },
      { label: "Discard and leave", value: true, style: "danger" },
    ]);
    if (leave) discardUnsaved(kind);
    return !!leave;
  }

  // Everything that leaves the current page: checked before it acts.
  const LEAVES = ["#journal-home", "#header-actions button:not(#feedback-btn):not(#invite-btn):not(#menu-btn)", ".nav-btn", "#new-btn",
    "#past li", "#past .cal-day", "#kept-list .kept-entries button", ".prayer-meta button", "a[href='/logout']"].join(", ");
  let letThrough = false;
  document.addEventListener("click", async (e) => {
    if (letThrough) { letThrough = false; return; }
    const el = e.target.closest(LEAVES);
    if (!el || !unsavedWork()) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (await okToLeave()) { letThrough = true; el.click(); }
  }, true);
  window.addEventListener("beforeunload", (e) => {
    if (unsavedWork()) { e.preventDefault(); e.returnValue = ""; }
  });

  function goHome() {
    hideToolbars();
    showWrite();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  $("journal-home").addEventListener("click", goHome);
  $("journal-home").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("journal-home").click(); } });

  // The admin email's "Open Admin" button links to /?settings=admin.
  const SETTINGS_ON_START = (() => {
    const asked = new URLSearchParams(location.search).get("settings");
    try {
      if (asked) sessionStorage.setItem("journal.settingsOnStart", asked);
      return asked || sessionStorage.getItem("journal.settingsOnStart");
    } catch (_) { return asked; }
  })();

  let started = false, statusTimer = null, clockTimer = null;
  function startJournal() {
    state.unlocked = true;
    const fromCover = !$("cover-view").hidden;
    const ghost = fromCover ? oldPageCopy($("cover-view")) : null;   // the cover, to turn away
    $("vault").hidden = true;
    $("book").hidden = false;
    if (fromCover) {
      $("vault").append($("vault-card"));                    // off the cover, out of sight
      $("vault-card").append($("vault-footer"));
      $("cover-view").hidden = true;
      $("book").classList.remove("locked");
      for (const b of document.querySelectorAll(HEADER_BUTTONS)) b.disabled = false;
      if ($("credit-coin")) { $("credit-coin").classList.remove("empty"); $("credit-coin").title = ""; }
      showView("write");
      turnPage($("write-view"), 1, ghost);                   // the cover turns away like a page
    }
    $("today").textContent = longDate(new Date().toISOString());
    restartAutosave();
    clearInterval(statusTimer);
    statusTimer = setInterval(pollStatus, 10000);
    // "Send feedback" links on the Sources and Privacy pages open the form, started for that topic.
    const FEEDBACK_TOPICS = { sources: "About the sources: ", privacy: "About privacy: " };
    if (!started && FEEDBACK_TOPICS[FEEDBACK_ON_START]) {
      try { sessionStorage.removeItem("journal.feedbackOnStart"); } catch (_) {}
      history.replaceState(null, "", "/");
      $("feedback-text").value = $("feedback-text").value || FEEDBACK_TOPICS[FEEDBACK_ON_START];
      setTimeout(openFeedback, 400);
    }
    if (!started && SETTINGS_ON_START && $(`tab-${SETTINGS_ON_START}`)) {
      try { sessionStorage.removeItem("journal.settingsOnStart"); } catch (_) {}
      history.replaceState(null, "", "/");
      state.settingsTab = SETTINGS_ON_START;
      setTimeout(showSettings, 400);
    }
    started = true;
    // Keep the date and streak right if the page stays open past midnight.
    clearInterval(clockTimer);
    clockTimer = setInterval(() => {
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
    state.passkeyUnlock = Boolean(v.passkeys && passkeysPossible());   // for the cover, when the book is closed
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
      $("passkey-unlock-btn").focus();   // the quickest way in, when there is one (never asked for by itself)
    }
  })();
})();
