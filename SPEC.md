# Personal Journal App — Spec

## Instructions for Claude Code

- Read this whole spec, then **summarize your plan and ask me any questions before writing code**.
- I have an engineering background, but I haven't coded in years. Explain setup steps plainly, and give me a single command to start the app.
- Build it in the phases listed at the end, and check with me after each phase.
- Write a short `README.md` that covers how to start the app, where my data lives, and how to change the settings.

---

## 1. What this is

This is a private, personal journal web app that runs on my own computer. I open it in my browser and quickly write about whatever is on my mind. A typical entry is about 500 words. When I finish, Claude reflects on the entry, offering a philosophical insight and a thought-provoking question. Everything is saved to an Excel file that I can open myself.

The app has one user (me). There are no accounts, no login, and no cloud hosting.

## 2. Reliability is the top priority

My previous version of this app failed often. That's the main reason I'm rebuilding it. These rules come first:

- **Never lose an entry.** Save the entry to disk *before* calling Claude. If the Claude call fails, the entry is still safe.
- **Reflection failures are recoverable.** If the API call fails or times out, show a clear, friendly message and a **"Try reflection again"** button. Entries that are missing a reflection should be marked as such and retryable later.
- **Handle a locked Excel file.** On Windows, Excel locks a file while it's open. If the journal file can't be written, don't crash and don't drop data. Keep the entry in a local backup, tell me to close Excel, and write it automatically once the file is available.
- **Autosave drafts** every few seconds while I'm typing, so a closed tab or a crash doesn't lose work in progress.
- Use sensible timeouts and one or two automatic retries on API calls, and log errors to a file I can share for debugging.

## 3. Tech approach (suggested, open to your recommendation)

- A small local web app: a Python backend (e.g. Flask or FastAPI) with a plain HTML/CSS/JavaScript frontend. Avoid heavy frameworks unless there's a clear reason.
- `openpyxl` for the Excel file.
- The Anthropic Python SDK for Claude calls. The API key goes in a `.env` file, which must never be committed or shown in the UI.
- The model name lives in config so I can change it. Default to a current Claude Sonnet model.
- It runs at `http://localhost:<port>` and starts with one command or script.

## 4. Features

### 4.1 Writing an entry
- The home screen shows today's date and a gentle prompt: **"What's on your mind?"**
- There's a large, distraction-free writing area. Show a live word count, kept subtle.
- A **"Give me a prompt"** button helps when I'm stuck (see 4.2).
- A **"Finish & reflect"** button saves the entry and requests the reflection.

### 4.2 Writing prompts
- When I ask for a prompt, generate one with Claude, informed by my recent entries (e.g. the last 5–10 entries, or their reflection questions). Good prompts might:
  - revisit a theme or unresolved question from earlier entries,
  - follow up on a reflection question I was given,
  - or offer a fresh Stoic-inspired prompt if there's little history.
- Show one prompt at a time, with an option for "another one."
- If I wrote from a prompt, save the prompt with the entry.

### 4.3 Reflection from Claude
After I submit an entry, Claude returns two things:

1. **Insight** (roughly 120–250 words). This connects my entry to philosophy, especially **Stoicism** (Marcus Aurelius, Epictetus, Seneca). Other traditions are fine when they fit better.
2. **One thought-provoking question** for me to sit with.

The tone and intent of the reflection:
- **Challenge me to grow. Don't just agree with me.** It should gently question my assumptions, point out what's in my control and what isn't, and suggest another way to see the situation. It should be encouraging but honest. No flattery, and no simply restating what I wrote.
- Be specific to what I actually wrote, not generic.
- Use Stoic ideas concretely where relevant: the dichotomy of control, judgments versus events, the view from above, amor fati, premeditatio malorum, and so on.
- **Don't invent quotes.** If quoting a philosopher, use only well-known, accurate quotes. Otherwise paraphrase and attribute ("Epictetus argued that…").
- **Every direct quote must come with a link to the source passage** so I can read more and verify it (see 4.3.1).

#### 4.3.1 Quote sources and links
AI models can make up plausible-looking URLs, so **don't let Claude write the URLs itself.** Do this instead:
- Have Claude return the reflection as structured output (JSON). For each quote, include: `quote`, `author`, `work`, and a precise `location` (e.g. *Meditations* Book 4, §3; Epictetus, *Enchiridion* ch. 5; Seneca, *Letters* 13).
- The app builds the link itself from a **curated source map** (e.g. `config/sources.json`). The map links each supported work to a reputable, free, public-domain online text, such as Wikisource, Project Gutenberg, or the MIT Internet Classics Archive. Where the site allows it, link to the specific book, chapter, or letter. Otherwise link to the work.
- Limit quotes to works in the source map. If Claude cites something outside it, show it as a paraphrase with no link and no quotation marks.
- Check each URL in the source map when the map is built or updated, and fix any broken links.
- Show the source under the reflection as a small citation, e.g. *— Marcus Aurelius, Meditations 4.3 [Read the passage]*, opening in a new tab.
- Starter works for the map: Marcus Aurelius, *Meditations*; Epictetus, *Enchiridion* and *Discourses*; Seneca, *Letters to Lucilius*, *On the Shortness of Life*, and *On Anger*. Add others as needed.
- It's a reflection, not therapy. If an entry suggests I'm in real distress, respond with care and gently encourage me to talk to someone I trust or a professional.

Put the system prompt in a separate, editable file (e.g. `prompts/reflection.md`) so I can tune it without touching code. Do the same for the writing-prompt generator (`prompts/writing_prompt.md`).

Display the reflection beneath the entry, styled differently, like a note in the margin or a facing page.

### 4.4 Saving to Excel
- Use one workbook, e.g. `data/journal.xlsx`. The location should be configurable, and the file should open cleanly in Excel.
- Each entry is one row, with these columns:
  `Entry ID | Date | Time | Writing Prompt (if any) | Entry | Word Count | Insight | Question | Quote Sources (citation + link) | Reflection Status | Last Updated`
- Use the local time zone (America/Toronto). Use readable date and time formats, with wrapped text and sensible column widths.
- Back up the workbook automatically (e.g. a dated copy, keeping the last N copies).

### 4.5 Browsing past entries
- **Previous / Next** buttons step through entries in date order.
- A **date picker / calendar** highlights the dates that have entries. Selecting one opens that day's entry. If a day has more than one entry, list them.
- Past entries show the entry text, insight, and question.
- Allow editing a past entry. Editing should *not* automatically regenerate the reflection; offer a "Reflect again" button instead.

### 4.6 Entry count and streak
- Show the **total entry count**, and a **current streak**: the number of consecutive calendar days, in local time, with at least one entry.
- A streak stays alive through today until midnight. For example, if I wrote yesterday but not yet today, the streak still shows yesterday's count, perhaps with a subtle "write today to keep it going." It resets to 0 only after a full day is missed.
- Multiple entries on one day count once toward the streak but each count toward the total.
- Also track the **longest streak** ever, as a nice-to-have.
- Calculate these from the entries' dates, not a stored counter, so they stay correct after edits or restores.
- Display them subtly, e.g. in the header or on the book's cover or edge. They should feel encouraging, not gamified or nagging.

## 5. Look and feel

It should feel calm, attractive, and personal, like writing in a real journal.
- The writing area should look like a page in a book or notebook: a subtle paper texture, a warm off-white page, and a serif or soft handwriting-style font that's still easy to read.
- Optionally, show a calming nature photo in the background, softly blurred or dimmed behind the "book." Keep the images in a local folder (e.g. `static/backgrounds/`) so I can add my own photos. Rotate through them by day or by entry. If you include starter images, use only royalty-free ones and note their sources in the README.
- Use gentle transitions. A page-turn effect when navigating entries would be nice, but keep it subtle.
- Keep it minimal, with no clutter. It should work well on a laptop screen, and a dark or "evening" mode is a nice-to-have.

## 6. Privacy

- All data stays on my computer, except the text sent to the Claude API for reflections and prompts.
- No analytics, tracking, or third-party scripts beyond what's strictly needed.
- Keep `.env`, `data/`, and logs out of version control (`.gitignore`).

## 7. Out of scope for now (possible later)

- Answering the reflection question as a follow-up entry
- Search across entries, and tags or mood tracking
- Weekly or monthly summaries of themes
- Mobile access or syncing across devices

## 8. Build phases

1. **Core loop:** write an entry, save it to Excel with a timestamp, and view it. No AI yet.
2. **Reflection:** the Claude insight and question, with all the error handling in section 2.
3. **Navigation and stats:** Previous / Next, the date picker, the entry count, and the streak.
4. **Writing prompts** based on previous entries.
5. **Look and feel:** the journal styling, backgrounds, and polish.

## 9. Done means

- I can start the app with one command and write an entry in under 10 seconds from launch.
- No entry is ever lost, even with the internet off, a bad API key, or Excel open.
- Every entry, insight, and question appears in `journal.xlsx` with its date and time.
- I can jump to any past entry by date or with Previous / Next.
- Reflections consistently challenge my thinking and reference Stoic ideas specifically, not generically.
- Every direct quote shows a citation with a working link to the source text, and no link is generated by the AI itself.
- The total entry count and current streak are always visible and correct.
