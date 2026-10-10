# A Life Well Lived and In His Steps

A private journal that runs on your own computer. You write in your browser, and every entry is saved, encrypted, in a database on your machine that only your passphrase can open.

It can also be put on the web for a few people (family, friends), each signing in with Google and keeping their own encrypted journal: see [Hosting on Render](#hosting-on-render).

## Starting the app

**Double-click `start.bat`** in this folder. (Or, in a terminal in this folder, run `start.bat`.)

- The first start takes a minute while it installs what it needs. Later starts take a couple of seconds.
- Your browser opens at <http://localhost:5050>.
- Keep the black `start.bat` window open while you write. Close it to stop the app.
- If the app is already running, `start.bat` closes that copy and starts a fresh one, so you always get the latest version. Your entries and draft are kept.

The only thing it needs installed is **uv** (`winget install astral-sh.uv`). uv downloads the right Python version on its own.

Shortcut: **Ctrl+Enter** does the same as **Finish & reflect**.

## Your passphrase and the lock

Your journal is encrypted. Nothing you write can be read from the disk without your passphrase.

- **First start:** you choose a passphrase (at least 8 characters; a short phrase works well). The app then shows a **recovery key** like `NW4Y-6D2B-A76F-EMNN-9PBZ-GSDZ`. **Write it down or save it in a password manager.** It's the only way back in if you forget your passphrase; nobody else can recover your journal. (If you had entries from before encryption, they're imported, checked, and the old unencrypted copies deleted at this point.)
- **Unlocking:** each time you start the app, it opens on the lock screen. Enter your passphrase.
- **Locking:** click the **padlock** next to ⚙ to lock straight away. The journal also locks after a period without activity (*Settings → Lock the journal after*, 30 minutes by default) and whenever the app is closed. Locking clears everything from the page.
- **Forgot your passphrase?** On the lock screen, click **Forgot your passphrase?**, enter your recovery key (capitals and dashes don't matter) and choose a new passphrase.
- **Changing your passphrase, or getting a new recovery key:** *Settings → Passphrase*. A new recovery key replaces the old one.
- After 10 wrong tries in a row, unlocking pauses for 15 minutes.
- **Passkeys (fingerprint, face or device PIN):** *Settings → Security & Data → Passkeys → Add a passkey* (you confirm with your passphrase). From then on the lock screen offers **Unlock with passkey**. Add one per device, or one that syncs (Apple, Google or a password manager). Your passphrase and recovery key keep working; **Remove** stops a passkey unlocking the journal. It needs a browser and device that support the passkey *PRF* feature (recent Chrome, Edge, Safari or Firefox, and most phones); if one doesn't, the app says so and you carry on with the passphrase. Passkeys belong to the site's address, so they only work on that domain (welllived.app, inhissteps.app, or `localhost` for test copies).

How it works, briefly: a random key encrypts every entry (AES-256-GCM); that key is itself stored only encrypted, once with your passphrase (via scrypt), once with your recovery key, and once for each passkey (with a secret only that passkey can produce, through the WebAuthn PRF extension; the server never stores the secret). So changing your passphrase is instant. On the web version, each person has their own key, locked by their own passphrase.

## Writing prompts

Stuck? Click **Give me a prompt**. Claude reads your last eight entries (and the reflection questions you were given) and suggests one prompt: it might return to a recurring theme, follow up on a question you haven't answered, or, with little history, offer something fresh from the philosophical tradition. A small note says which entry it builds on. **Another one** gives a different suggestion; **×** dismisses it. A prompt takes a few seconds and costs well under a cent.

If you write with a prompt showing, it's saved with the entry (in the *Writing Prompt* column of exports; it isn't shown on the entry page once you finish) and kept with your draft until then. Hidden entries are never used for prompts. To tune the suggestions, edit `editions/philosophy/prompts/writing_prompt.md`; changes apply straight away.

## Titles

Type a title above your entry if you like. If you leave it blank, Claude suggests a short one (about five words, e.g. "On wit and teasing") along with the reflection. Entries without a title show *No Title*. To change a title later, open the entry and click **Edit**. A title you've written or changed is never replaced by a suggested one.

## Browsing past entries

- **Older / Newer** at the top of an entry steps through your entries in date order. While reading, the **←** and **→** keys do the same, and on a phone or tablet you can **swipe**: right for Older, left for Newer. Going Older from your first entry gives a little bump (there's nothing before it); going Newer from your latest entry turns to a fresh writing page. From an empty writing page, **‹ Older** (or swiping right) turns back to your latest entry; once you've started writing, it won't turn away from your draft.
- Above the list of past entries are three filters: **All**, **★ Favorites** and **Calendar**.
  - **★ Favorites** shows only favorites (click it again to turn it off).
  - **Calendar** pops up a month view that shades the days you wrote. Use it to filter by date:
    - click the **month name** (or **Whole month**) for that whole month;
    - click a **day** for just that day, then click a **second day** to stretch it into a range (e.g. a week);
    - **‹ ›** changes month; **Clear dates** removes the date filter.
    The button then shows the dates (e.g. *Calendar: Sep 8–14*) and a line above the list says what's showing.
  - Favorites and dates combine (e.g. favorites from September).
  - **All** clears every filter.
- Older / Newer only steps through the entries the filters are showing.
- The list shows your 10 newest entries (or newest matches for a filter); 20 more load as you scroll to its end, or with **Show more**. Older / Newer, swiping and the calendar still reach every entry.

## Entry count and streak

The header shows your total entries, your current streak (consecutive days with at least one entry), and your best streak. They're worked out fresh from your entries' dates every time, never stored, so they stay right after edits, deletions or restores.

- A day counts once toward the streak, however many entries you write; each entry counts toward the total.
- If you wrote yesterday but not yet today, the streak still shows, with a gentle *write today to keep it going*. It only resets after a full day with no entry.
- Hidden entries still count. Permanently deleted ones don't.
- Days follow your time zone setting (Toronto by default), so an entry at 11:30 pm counts for that day.
- From 3 days on, the streak shows as a small badge (**✦ 9-day streak**). When **Finish & reflect** brings you to a milestone (3 days, 1 week, then every further week: 2 weeks, 3 weeks, …), the badge gently pops and reads the milestone for a few seconds (*Three days in a row*, *A full week of writing*, *Two weeks of writing*…) before settling back. It only celebrates the entry that reaches the milestone, not later entries the same day.

### Entry milestones

Finishing your **5th, 10th, 25th and 50th** entry, and every 50th after that (100th, 150th, 200th…), brings up a gold **"✦ Your 50th entry"** badge beside your streak for a few seconds. Hidden entries count too, matching the entry count in the header.

### Longest entry yet

From your fourth entry on, finishing an entry with more words than any you've written before brings up a gold **"✦ Your longest entry yet · 612 words"** badge beside your streak. That entry then shows **"✦ longest"** next to its word count, until another entry beats it (a tie keeps the record where it is; hidden entries don't count).

## Favorites

While reading a past entry, click the **☆** next to its title to make it a favorite (★); click again to remove it. The star is hidden while you're editing. Above the list of past entries, choose **★ Favorites** to see only your favorites, or **All** to see everything; the app remembers your choice. Favorites are marked in the *Favorite* column of exports. Starring doesn't change an entry's *Last Updated* time.

## Reflections

When you click **Finish & reflect**, the entry is saved first, and only then is it sent to Claude. A reflection (an insight plus a question to sit with) appears under the entry, usually within 20–40 seconds.

If it fails (no internet, an API problem, Claude busy), your entry is still saved. You'll see the reason and a **Try reflection again** button. Entries without a reflection are marked *no reflection yet* in the list, and can be retried any time. In exports, the *Reflection Status* column shows *Done*, *Failed*, *Pending* or *Not requested*.

After editing an entry, the old reflection stays. Click **Reflect again** if you want a new one. If that fails, the earlier reflection is kept.

**Tuning the reflections:** edit `editions/philosophy/prompts/reflection.md` in Notepad. Changes apply to the next reflection; no restart is needed. The note shown when an entry suggests real distress is in `prompts/support_note.md`.

**Cost:** each reflection costs roughly 2–5 cents of API credit at the default *Deep* setting. *Settings → Reflection depth* trades depth for speed and cost.

### Quotes and links

Reflections draw on the whole Western tradition: Socrates and Plato, Aristotle, Epicurus, Cicero, the Stoics, Augustine, Boethius, Anselm, Aquinas and Dante, Descartes, Pascal, Spinoza, Locke and Hume, Kant, Kierkegaard, and Mill. Claude picks whichever thinker best fits each entry; to change that emphasis, edit `editions/philosophy/prompts/reflection.md`.

Claude may quote only the 34 works listed in `editions/philosophy/sources.json`, each in a specific public-domain translation (or original English edition) on Wikisource. Claude never writes links. For each quote, the app:

1. searches its local copy of that translation (`sources/texts/`) for the words,
2. if found, shows the **source's exact wording**, cites where it actually appears (correcting the book or chapter if Claude got it wrong), and links to that page (**Read the passage**),
3. if not found, replaces that sentence with Claude's attributed paraphrase (no quotation marks), and lists the work as an idea (below).

Ideas Claude attributes to one of these works *without* quoting (e.g. "Seneca, in *On the Shortness of Life*, argued that…") are listed under the reflection too, with a **Read the source** link (hovering over it shows the idea) to the book, chapter or letter Claude named. The app checks that the location exists in that work; if it doesn't, the link goes to the work's main page instead. (It can't check that the idea is in that chapter, since there are no exact words to look for, hence "source" rather than "passage".) Reflections written before this feature have no idea links; **Reflect again** adds them.

To add a work, or after editing `editions/philosophy/sources.json`, run this in the journal folder. It checks every link and downloads the texts (a few minutes):

```
uv run python tools/build_sources.py
```

Add `--check` to only check the links.

After re-downloading the sources, you can bring quotes in earlier reflections up to date (corrected wording, location and link). Close the app first; you'll be asked for your journal password. The first command only shows what would change; the second makes the changes (the daily backup keeps the previous version):

```
uv run python tools/recheck_quotes.py
uv run python tools/recheck_quotes.py --apply
```

### The quotable works

The **Sources** page (footer, between the version number and *Privacy*; also at `/sources`) lists every quotable work of the edition, grouped by tradition, with its translation and a link to the full text. Its groups, introduction and (for In His Steps) the writers who are only ever paraphrased live in `editions/<edition>/edition.json` (`sources_intro`, `source_groups`, `paraphrase_only`). A test checks that every work in `sources.json` appears in exactly one group. Its *Send feedback about the sources* link opens the feedback form once the journal is unlocked.

All texts are public domain and come from [Wikisource](https://en.wikisource.org).

| Thinker | Works (translation) |
|---|---|
| Socrates & Plato | *Apology*, *Crito*, *Phaedo*, *Symposium* (Jowett, 1892); *Republic* (Jowett, 1901) |
| Aristotle | *Nicomachean Ethics* (Chase, 1847) |
| Epicurus | *Letters* and *Principal Doctrines*, in Diogenes Laërtius' *Lives*, Book X (Hicks, 1925) |
| Cicero | *On Old Age* (Peabody, 1884); *Tusculan Disputations* (Yonge, 1888) |
| Marcus Aurelius | *Meditations* (Long, 1862) |
| Epictetus | *Enchiridion*, *Discourses* (Long, 1877) |
| Seneca | *Letters to Lucilius* (Gummere, 1917–25); *On the Shortness of Life* (Basore, 1932); *On Anger* (Stewart, 1900) |
| Augustine | *Confessions* (Pilkington, 1886) |
| Boethius | *The Consolation of Philosophy* (James, 1897) |
| Anselm of Canterbury | *Proslogium* (Deane, 1903) |
| Thomas Aquinas | *Summa Theologiae*, moral parts I-II and II-II (English Dominican Fathers, 1917) |
| Dante | *Inferno*, *Purgatorio*, *Paradiso* (Longfellow, 1867) |
| Descartes | *Meditations on First Philosophy*, *Discourse on the Method* (Veitch, 1853) |
| Pascal | *Pensées*, Sections I–III, thoughts 1–241 (Trotter, 1910); the rest isn't transcribed on Wikisource yet |
| Spinoza | *Ethics* (Elwes, 1883) |
| Locke | *An Essay Concerning Human Understanding* (1853 printing) |
| Hume | *A Treatise of Human Nature* (1888 ed.); *An Enquiry Concerning Human Understanding* (1748 first ed.) |
| Kant | *Groundwork of the Metaphysics of Morals*, *Critique of Practical Reason* (Abbott, 1873) |
| Kierkegaard | *Selections from the Writings of Kierkegaard* (Hollander, 1923) |
| John Stuart Mill | *On Liberty* (1859); *Utilitarianism* (1863), original English editions |

## Where your data lives

Everything is in the `data` folder, and all of it is encrypted:

| What | Where |
|---|---|
| Your journal: entries, reflections, titles, dates, favorites, draft | `data/journal.db` (an encrypted SQLite database) |
| Daily backups | `data/backups/journal-YYYY-MM-DD.db` (keeps the last 14; also encrypted) |
| Error log, to share when debugging | `logs/journal.log` (IDs, counts and errors only, never what you write) |

The database shows nothing about your entries except how many there are: even their dates are encrypted. Entry IDs are random.

**The one unencrypted copy** your browser keeps is of the draft you're currently typing (in its local storage), as a safety net if the app can't be reached. It's replaced as you type and cleared when you finish the entry.

**To back up your journal**, copy `data/journal.db` (or the whole `data` folder) somewhere safe. A backup opens with the password (or recovery key) that was in use when it was made.

**Changing an entry's date or time:** open the entry, click **Edit**, and change the *Date* and *Time* fields above the title. The entry moves to its new place in the list, calendar and Older / Newer order, and the streak is recalculated. (Future dates aren't allowed.)

### Export and wipe

In **⚙ Settings → Your data**:

- **Export (CSV)** downloads your whole journal (e.g. `journal-2026-10-07.csv`) to your Downloads folder. It opens directly in Excel, with accents and line breaks intact; hidden entries are included and marked. **The export is not encrypted**, so keep it somewhere safe, or delete it when you're done.
- **Import (CSV)…** adds the entries from a file made with Export: to restore entries after a wipe, or to move your journal to the web version. Titles, favorites, hidden entries, reflections and source links come back too. Entries already in the journal (same date, time and text) are skipped, so importing the same file twice is harmless.
- **Wipe journal…** permanently deletes all entries (hidden ones too, with their reflections), your draft, and your entries in every backup in `data/backups/`. Your passphrase, settings, API key, photos and quote sources are kept. To confirm, you're shown a random three-digit number and must type it in words, digit by digit (352 → *three five two*). A wrong answer deletes nothing; each number allows one try. **There's no undo**, so export first if you might want your entries back.

## Deleting an entry

Open the entry and click **Delete**. You'll be asked to confirm, then to choose:

- **Hide:** the entry disappears from the app but stays in your journal (and in exports, marked *Hidden*). You can bring it back from **Settings → Hidden entries → Restore**.
- **Delete permanently:** the entry is removed from your journal and from every backup in `data/backups/`. This can't be undone.

## Settings

Click the **⚙** in the top-right corner. Settings has tabs: **Basic** (your account, name, journal name, draft saving, auto-lock; on your own computer also the app-wide settings under *This computer*), **Theme** (day or evening, color, background photo), **Security & Data** (passphrase, recovery key, export, import, wipe, hidden entries) and, for admins, **Admin** (usage, invitation requests, feedback). **Save** saves and returns to your journal; **Discard** returns without saving anything. The ones under *Applied after restarting the app* take effect the next time you start `start.bat`.

Your personal choices (name, journal name, day/evening, color, photo, draft saving, lock time) are saved with your journal in `data/journal.db`. The app-wide settings (port, time zone, backups, model…) are saved to `config/settings.json`, which you can also edit in Notepad and then restart the app. On the web version, each person only sees their personal choices; the app-wide ones come from the server's settings.

| Setting | What it does | Default |
|---|---|---|
| `user_name` | Your name in the greeting ("Hi …, what's on your mind?") | *(empty)* |
| `journal_name` | The title at the top of your journal, in the theme color (blank = the app name, e.g. *A Life Well Lived*) | *(empty)* |
| `port` | The number in the browser address | `5050` |
| `open_browser_on_start` | Open the browser automatically | `true` |
| `timezone` | Time zone for dates and streaks | `America/Toronto` |
| `backups_to_keep` | How many daily backups to keep | `14` |
| `draft_autosave_seconds` | How often your draft is saved while typing | `3` |
| `lock_minutes` | Lock after this many minutes without activity: `5`, `15`, `30`, `60`, `120`, or `0` (only when the app closes) | `30` |
| `model` | Which Claude model writes reflections | `claude-sonnet-5-5` |
| `reflection_effort` | Reflection depth: `low`, `medium` or `high` | `high` |
| `api_timeout_seconds` | How long to wait for Claude before giving up | `60` |
| `api_retries` | Automatic retries when a Claude request fails | `2` |
| `theme` | Day or evening: `day`, `evening` or `auto` | `day` |
| `colour_theme` | `lake`, `walnut`, `sage`, `amber`, `claret`, `lavender`, `sea-glass` or `graphite` | `lake` |
| `background_photo` | Background photo behind the journal (off: a deep shade of the color theme) | `true` |
| `photo_blur` | Photo blur in pixels, 0–30 | `3` |
| `photo_visibility` | How clearly the photo shows through, 0–100 (%) | `50` |

## Claude API key

1. Copy `.env.example` and name the copy `.env` (in this same folder).
2. Open `.env` in Notepad and paste your key after `ANTHROPIC_API_KEY=`.
3. Check it with **Settings → Test my API key**.

The key is read fresh each time, so no restart is needed after changing it. `.env` is never shown in the app, and `.gitignore` keeps it out of version control.

## Look and feel

- **Day or evening:** *Settings → Day or evening* chooses **Day** (the default), **Evening**, or **Automatic** (follows your device's light/dark setting).
- **Color:** *Settings → Color* picks one of eight color themes: **Lake** (cool blue, the default), **Walnut** (warm brown), **Sage** (green), **Amber** (orange), **Claret** (deep red), **Lavender** (purple), **Sea glass** (teal) and **Graphite** (neutral gray). The color tints the buttons, links, paper, margin notes, desk and the veil over the photo, and works in both day and evening. Clicking a swatch previews it straight away; **Save** keeps it, **Discard** returns to what you had.
- **Adding a color:** in `static/style.css`, copy one line from the *Color themes* block (e.g. `[data-palette="lake"] { --hue: 212; ... }`), give it a new name and hue (0–360 around the color wheel: 0 red, 30 orange, 60 yellow, 120 green, 200 blue, 270 purple), then add the name to `COLOUR_THEMES` in `journal/config.py`.
- **Background photo:** a softly blurred photo sits behind the journal. Every time the page loads or refreshes, a random photo from `static/backgrounds/` appears (never the one you just saw), and finishing an entry fades in another. Turn it off with *Settings → Background photo*; the journal then sits on a deep shade of your color theme (e.g. navy for Lake), the same in Day and Evening. Day / Evening only changes the journal itself.
- **Photo blur and visibility:** two sliders under that setting. *Photo blur* goes from 0 (sharp) to 30 (very soft); *Photo visibility* from 0% (almost hidden behind a paper-coloured veil) to 100% (no veil). They preview as you drag; **Save** keeps them. In evening mode the veil is a little stronger, so the photo never overpowers the dark page.
- **Your own photos:** drop any `.jpg`, `.png` or `.webp` into `static/backgrounds/`; no restart needed. Photos there are shared by both apps; to show a photo in only one, put it in `static/backgrounds/philosophy/` (Well Lived) or `static/backgrounds/christian/` (In His Steps). Landscape, about 1920 × 1080 (or 1920 × 1280 / 1440), under ~2 MB each works best. Portrait photos work but lose some of their top and bottom.
- **Motion:** Older / Newer turn the page gently; other screens fade in. If your computer is set to reduce motion (Windows: *Settings → Accessibility → Visual effects → Animation effects*), the app keeps still.

### Credits

Background photos are your own. (The journal originally shipped with five public-domain / CC0 photos from Wikimedia Commons by W.carter, George Chernilevsky and Jebulon; they've since been replaced.)

Fonts, stored in `static/fonts/` (nothing loads from the internet): [EB Garamond](https://github.com/googlefonts/ebgaramond) (SIL Open Font License) for the text, and [Homemade Apple](https://fonts.google.com/specimen/Homemade+Apple) by Font Diner (Apache License 2.0) for the handwriting. Copies of the licenses are alongside the fonts.

## Tests

The streak rules, the wipe confirmation, the encryption (nothing readable on disk, locking, passphrases, recovery key, tamper detection, backups, import, export), and accounts (each person sees only their own journal, daily limits, the upgrade from the single-person database) have automated tests. The encryption tests need a throwaway folder, so they never touch your journal:

```
set JOURNAL_DATA_DIR=%TEMP%\journal-tests
uv run python -m unittest discover tests
```

## Hosting on Render

This puts the journal at **https://reflections-j7o5.onrender.com** (Render added -j7o5 because the plain name was taken) for a few people you invite. Each person signs in with Google, then sets their own passphrase. Their entries are encrypted with it, so neither you nor Render can read them. Reflections use *your* Claude API key, with a limit of **3 reflections and 10 writing prompts per person per day**.

**Cost:** Render's *Starter* plan with a 1 GB disk, about US$7/month (the free plan has no disk, so entries would vanish on every restart). Claude usage is billed to your Anthropic account as usual.

### 1. Put the code on GitHub (private)

1. Install Git (`winget install Git.Git`) and make a free account at github.com.
2. On GitHub, create a **private** repository named `reflections`, with no files.
3. In a terminal in this folder:
   ```
   git init
   git add .
   git commit -m "Journal"
   git branch -M main
   git remote add origin https://github.com/YOUR-NAME/reflections.git
   git push -u origin main
   ```
   `.gitignore` keeps `.env` (your API key), `data/` (your journal), `logs/` and `config/settings.json` out of it. Your background photos **are** included, which is why the repository should be private.

### 2. Create the Google sign-in

1. Go to <https://console.cloud.google.com>, create a project (e.g. *A Life Well Lived*).
2. **APIs & Services → OAuth consent screen** (or *Google Auth Platform*): choose **External**, app name *A Life Well Lived*, your email as support and developer contact. Scopes: just the defaults (`openid`, `email`, `profile`).
3. Leave it in **Testing** mode and add each family member's Gmail address under **Test users** (up to 100). Nobody else can sign in, and Google doesn't need to review the app.
4. **Credentials → Create credentials → OAuth client ID** → *Web application*.
   - Authorised JavaScript origin: `https://reflections.onrender.com`
   - Authorised redirect URI: `https://reflections.onrender.com/auth/callback`
5. Copy the **Client ID** and **Client secret**.

### 3. Create the service on Render

1. Sign up at <https://render.com> with your GitHub account.
2. **New → Blueprint**, pick the `reflections` repository. Render reads `render.yaml` and creates a web service named *reflections* with a disk.
3. Fill in the values it asks for:
   | Variable | Value |
   |---|---|
   | `GOOGLE_CLIENT_ID` | from step 2 |
   | `GOOGLE_CLIENT_SECRET` | from step 2 |
   | `ANTHROPIC_API_KEY` | your Claude API key |
   | `ALLOWED_EMAILS` | the invited addresses, comma-separated: `you@gmail.com, mum@gmail.com` |
   | `ADMIN_EMAILS` | your own address: who can read feedback (Settings → Feedback received) |

   `SECRET_KEY` is generated for you. The time zone and daily limits are set in `render.yaml` (change them there or in Render's *Environment* tab).
4. Wait for the first deploy (a few minutes), then open https://reflections.onrender.com.

If the name `reflections` is taken on Render, it will give the service a slightly different address (e.g. `reflections-ab12.onrender.com`). Use that address instead in Google's two URLs and in `PUBLIC_HOST` in `render.yaml`.

### The welcome page, logo and name

Signed-out visitors see a welcome page: what the app is on the left, and *Continue with Google* on the right. Signing in for the first time creates that person's journal (if their address is invited). The page has a **Privacy** link, and so does every journal page (at the bottom, and in *Settings → Account*).

- **Logo and app icons:** the master is `brand-source/<edition>/logo-original.png` (square, 1024 px or larger). After replacing it, run `uv run --with pillow python tools/make_icons.py <edition>` and push: that makes the welcome-page logo, the browser-tab icon, the iPhone home-screen icon, the "Install app" icons, and `logo-mask.png` (a cut-out of the logo's dark color, which the header fills with the current color theme, so its logo matches the theme) in `static/brands/<edition>/`. The cut-out assumes a logo in one dark color on white, like both current ones.
- **Installing:** the app offers to install itself where it can. **Settings → Basic → Install as an app** always has it; and from the 3rd entry on, a small invitation appears below the header (**Not now** hides it for a month; it never shows once installed). On Chrome or Edge (Windows, Mac, Android) the **Install** button opens the browser's install dialog; on iPhone/iPad, where websites can't install themselves, it shows the two steps (*Share → Add to Home Screen*). Nothing is offered in browsers that can't install (e.g. Firefox on a computer). A tiny service worker (`static/sw.js`) makes the site installable; it stores nothing on the device.
- **Picture behind the left side (optional):** `static/brands/<edition>/hero.jpg`, about 1600 × 1200, under 1 MB. It's darkened so the words stay readable.
- **Name:** comes from the edition (`app_name` in `editions/<edition>/edition.json`): *A Life Well Lived* for philosophy, *In His Steps* for the Christian edition. It shows in the browser tab, welcome page, privacy page and installed app. `APP_NAME` in `.env` or on Render overrides it, but is best left unset.

Files in `static/brands/<edition>/` are public (the welcome page is visible to anyone), unlike `static/backgrounds/`, which only signed-in people can see.

### Inviting someone, or removing them

Add (or remove) their address in **both** places: Google's *Test users*, and `ALLOWED_EMAILS` on Render (saving it restarts the app in about a minute). Someone removed from `ALLOWED_EMAILS` is signed out on their next click; their encrypted entries stay on the disk.

### Feedback

The **speech bubble** at the top (left of the padlock) opens a short form: choose **Fix or improve something** or **Suggest a new feature**, write a few lines, and **Send**. Feedback is saved with the sender's email, the date, the screen they were on and their browser type. It is **not encrypted** (it's meant for you to read), and the form says so.

You read it in **Settings → Feedback received**: newest first, with a **Done** tick-box to clear things you've handled, and **Download (CSV)** for a spreadsheet. Only addresses in `ADMIN_EMAILS` on Render see that section (on your own computer, you always do). Each person can send up to 20 a day.

### Suggestion cards

As someone's entries add up, a small card under the header suggests one thing at a time (never more than one per visit):

| Entry | Card | Until |
|---|---|---|
| 3rd | Install as an app | installed, or *Not now* (back in a month) |
| 5th | Send feedback (web version) | they've sent feedback, or answered the card once |
| 10th, 20th, 40th | Invite someone (web version) | they've sent an invitation; *Not now* (or opening the form without sending) waits for the next of these, and it stops after the 40th |

Whether someone has sent feedback or an invitation comes from the server; dismissals are remembered in that browser.

### Invitations

On the web version, the **gift icon** (between Feedback and the padlock) lets anyone ask for a friend to be invited: their Google address, plus an optional name and note. Below the form they see their own requests: *Requested*, *Has access* or *Declined*.

You see the requests in **Settings → Invitation requests** (admins only), with **Copy address** and **Decline**. To let someone in, add them as before (Google *Test users* if the sign-in is still in Testing, and `ALLOWED_EMAILS` on Render); the request then shows *Has access* by itself, for you and for whoever asked. Each request records who asked, ready for rewards for successful invitations later. Up to 10 requests per person per day.

### Usage stats

**Settings → Usage** (only for addresses in `ADMIN_EMAILS`; on your own computer, you always see it) shows how Reflections is being used:

- **Tiles:** people (signed up / set up / invited), active today, this week, this month, how many are *coming back* (used it on 2 or more different days in the last 30), average days active, and entries and reflections in the last 30 days.
- **Chart:** how many people used it each day for the last 30 days.
- **Table:** one row per person: last visit (*today*, *3 days ago*…), days active in the last 30, entries (last 30 days and all time), reflections, and when they joined.

Only counts are kept, per person per day: that they opened their journal, how many entries they wrote, and how many reflections and prompts they asked for. Nothing anyone writes, no titles, no times of day. The privacy page says so. Counting started when this feature was added, so earlier days show as empty.

### Trying changes before they go live: `test-web.bat`

Double-click **`test-web.bat`** to run a test copy of the *web* version on your computer at <http://localhost:5061> (it opens in your browser). It's marked **Test copy** in the corner and **[TEST]** in the browser tab.

- Instead of Google, a **Sign in as** box: pick `you@example.com`, `mum@example.com` or `dad@example.com` to try it as different family members (each gets their own journal and passphrase), or type any other address to see the "not invited" message.
- Its data is in `data\test-web`, separate from your journal and from the live site. Delete that folder to start over.
- Reflections and prompts are real (they use the key in `.env`), with the web's daily limits.
- The sign-in box only exists in this test copy; on Render it's switched off.

`start.bat` (your own journal, port 5050) and `test-web.bat` (port 5061) can run at the same time.

### Version number

The footer shows the version, e.g. **Beta v0.1**. It's the one line in the **`VERSION`** file at the top of the project: change it (say to `Beta v0.2` for a minor improvement, `v1.0` for a major one), then push. It's read when the app starts, so on your own computer restart `start.bat` to see it.

### Updating the web version

After changing the code here: `git add .`, `git commit -m "what changed"`, `git push`. Render rebuilds and restarts by itself. People who were writing have to unlock again after a restart; their drafts are kept.

### Moving your own journal to the web

Locally: *Settings → Export (CSV)*. On the web: sign in, set your passphrase, then *Settings → Import (CSV)…*. Delete the exported file afterwards; it isn't encrypted.

### Good to know

- **Forgotten passphrases can't be reset**, by you or anyone. Make sure everyone keeps their recovery key.
- Unlocked journals are held in the server's memory, so a restart (an update, or Render's maintenance) locks everyone; they just unlock again.
- The server keeps 14 daily backups of the (encrypted) database on the same disk. For an extra copy, Render's disk snapshots can be restored from the dashboard.
- `logs/journal.log` on the disk, and Render's *Logs* tab, contain only IDs, counts and errors, never anyone's writing.

## Terms

The **Terms** page (`/terms`, linked from the footer, the lock screen, Settings → Account, and the Privacy and Sources pages) is the plain-language terms of use: beta and invitation-only, your writing is yours (and unrecoverable without passphrase or recovery key), reflections are AI-written and not professional advice, fair use and the daily limits (read from the settings), leaving, no guarantees, and changes. It's in `templates/terms.html`; when you change it, update `TERMS_UPDATED` in `app.py` so the date at the top is right. Have it reviewed by a lawyer before relying on it, especially before charging.

## Privacy

- Your journal is encrypted on disk; only your passphrase or recovery key opens it.
- The app only listens on your own computer (`127.0.0.1`), and only the browser tab that unlocked the journal can read it.
- Nothing leaves your computer except the entry text (with its date, title and prompt) sent to Claude, over an encrypted connection, for reflections and writing prompts.
- Exports are the only unencrypted copies the app makes, and only when you ask.
- For extra protection of the whole computer, turn on Windows device encryption (*Settings → Privacy & security → Device encryption*).
