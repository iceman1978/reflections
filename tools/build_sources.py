"""Check every link in editions/<edition>/sources.json and download the texts used to verify quotes.

Run after editing the source map:

    uv run python tools/build_sources.py            # check links + download texts
    uv run python tools/build_sources.py --check    # only check that links work

Choose the edition with the EDITION setting (default: philosophy), e.g. in PowerShell:

    $env:EDITION = "christian"; uv run python tools/build_sources.py

Texts are saved to sources/texts/<work>.json. Takes a few minutes, because
it pauses between requests to be polite to the sites.

Where texts come from (the 'pages' field of each work):
    Wikisource                 most works (see the notes in sources.json)
    {"bible": "Romans", "slug": "romans"}
                               the Berean Standard Bible (public domain), one
                               page per chapter, linked to that chapter on Bible Hub
    {"ccel": "author/book"}    the Christian Classics Ethereal Library's XML
                               edition, one page per chapter, linked to CCEL
    {"gutenberg": 25141, "split": "h2" or "split_text": regex}
                               a Project Gutenberg book, split at each heading
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal import sources  # noqa: E402

API = "https://en.wikisource.org/w/api.php"
RENDER = "https://en.wikisource.org/w/index.php"
HEADERS = {"User-Agent": "PersonalJournalApp/0.2 (personal, non-commercial; quote source checker)"}
PAUSE = 1.0

sys.stdout.reconfigure(encoding="utf-8")


def fetch(url):
    for attempt in range(6):
        time.sleep(PAUSE)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503):
                raise
            wait = int(e.headers.get("Retry-After") or 10 * (attempt + 1))
            print(f"    (Wikisource asked us to slow down; waiting {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"Gave up after repeated rate limiting: {url}")


# ---- Link check ---------------------------------------------------------

def missing_titles(titles):
    """Ask Wikisource which of these page titles don't exist (50 at a time)."""
    missing = []
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        q = urllib.parse.urlencode({"action": "query", "titles": "|".join(batch),
                                    "redirects": 1, "format": "json"})
        data = json.loads(fetch(f"{API}?{q}"))
        for page in data["query"]["pages"].values():
            if "missing" in page or "invalid" in page:
                missing.append(page.get("title", "?"))
    return missing


# ---- Text download ------------------------------------------------------

class Paragraphs(HTMLParser):
    """Collects the text of every block (paragraph, div, list item, ...) on a page.

    Skips Wikisource's navigation header, page numbers, footnote markers and
    references, and margin notes / running heads from the printed book (which
    would otherwise land mid-sentence, e.g. "I neither Apology. Socrates. know").
    """
    SKIP_TAGS = {"style", "script", "sup"}
    SKIP_CLASSES = ("wst-header", "ws-noexport", "mw-references", "reference", "noprint",
                    "sidenote", "wst-rh", "running-header", "pagenum")
    BLOCKS = {"p", "div", "li", "dd", "dt", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
              "td", "th", "center", "table", "tr", "ul", "ol", "dl", "pre"}
    VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "col", "area", "source"}

    def __init__(self):
        super().__init__()
        self.paras, self.buf, self.stack = [], [], []  # stack of (tag, skips)

    def _skipping(self):
        return any(skip for _, skip in self.stack)

    def _flush(self):
        text = " ".join("".join(self.buf).replace("​", "").split())
        if text and text != "Layout 1":
            self.paras.append(text)
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            if tag == "br":
                self.buf.append(" ")
            return
        cls = dict(attrs).get("class") or ""
        skip = tag in self.SKIP_TAGS or any(c in cls for c in self.SKIP_CLASSES)
        if tag in self.BLOCKS and not self._skipping():
            self._flush()
        self.stack.append((tag, skip))

    def handle_endtag(self, tag):
        if not any(t == tag for t, _ in self.stack):
            return  # stray closing tag
        while self.stack:
            t, _ = self.stack.pop()
            if t == tag:
                break
        if tag in self.BLOCKS and not self._skipping():
            self._flush()

    def handle_data(self, data):
        if not self._skipping():
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


ROMAN_HEADING = re.compile(r"^([IVXLC]+)\.$")
NUMBERED = re.compile(r"^(\d+)\.\s")
NUMBER_ALONE = re.compile(r"^(\d+[a-z]?)\.?$")   # "146." or just "146" on its own line (Pascal)


def assign_sections(paras, style):
    out = []
    if style == "numbered_paragraphs":
        section = None
        for p in paras:
            heading = ROMAN_HEADING.match(p) or (len(p) < 80 and (
                re.match(r"(BOOK|CHAPTER|PART)\b", p.upper()) or
                re.match(r"(FIRST|SECOND|THIRD) ESSAY\b", p.upper())))
            if heading:
                continue
            alone = NUMBER_ALONE.match(p)
            if alone:  # e.g. "146." on its own line, before the aphorism
                section = alone.group(1)
                continue
            m = NUMBERED.match(p)
            if m:
                section = m.group(1)
            elif section is None:
                section = "1"  # first passage of a book is often unnumbered
            out.append({"section": section, "text": p})
    elif style == "roman_headings":
        section = None
        for p in paras:
            m = ROMAN_HEADING.match(p)
            if m:
                section = str(sources.roman_to_int(m.group(1)))
            elif section is not None:
                out.append({"section": section, "text": p})
    else:
        out = [{"section": None, "text": p} for p in paras]
    return out


def list_prefix_pages(spec):
    """Every subpage under spec['prefix'] whose remaining title matches spec['pattern']."""
    pattern = re.compile(spec["pattern"])
    titles, cont = [], {}
    while True:
        q = urllib.parse.urlencode({"action": "query", "list": "allpages", "apprefix": spec["prefix"],
                                    "aplimit": 500, "format": "json", **cont})
        data = json.loads(fetch(f"{API}?{q}"))
        titles += [p["title"] for p in data["query"]["allpages"]]
        if "continue" not in data:
            break
        cont = {"apcontinue": data["continue"]["apcontinue"]}
    pages = {}
    for title in titles:
        m = pattern.match(title[len(spec["prefix"]):])
        if m:
            key = ".".join(sources.key_part(g, spec.get("aliases")) for g in m.groups())
            pages.setdefault(key, title)  # first wins if a page is listed twice
    def order(kv):  # numbers in order; named pages (e.g. Prologue) first
        return [(1, int(p), "") if p.isdigit() else (0, 0, p) for p in kv[0].split(".")]
    return sorted(pages.items(), key=order)


# ---- Books from outside Wikisource ----------------------------------------

BSB_URL = "https://bereanbible.com/bsb.txt"
BIBLE_HUB = "https://biblehub.com/bsb/{slug}/{chapter}.htm"
_bsb = {}


def bsb_verses(book):
    """{chapter: [(verse, text), ...]} for one book of the BSB (downloaded once)."""
    if not _bsb:
        print("    downloading the Berean Standard Bible...")
        for line in fetch(BSB_URL).splitlines():
            m = re.match(r"^(.+?) (\d+):(\d+)\t(.*)$", line.strip("﻿"))
            if m:
                _bsb.setdefault(m.group(1), {}).setdefault(int(m.group(2)), []).append((m.group(3), m.group(4).strip()))
    if book not in _bsb:
        raise SystemExit(f"No book called '{book}' in the BSB. Names are like 'Psalm', '1 John', 'Song of Solomon'.")
    return _bsb[book]


def bible_pages(spec):
    return [{"key": str(ch), "title": BIBLE_HUB.format(slug=spec["slug"], chapter=ch),
             "paragraphs": [{"section": v, "text": text} for v, text in verses if text]}
            for ch, verses in sorted(bsb_verses(spec["bible"]).items())]


SKIP_TITLES = re.compile(r"^(title page|contents|table of contents|indexes|index\b|outline|prefatory|"
                         r"publisher|foreword|footnotes|notes\.?$|appendix to (the )?electronic)", re.I)


def short_label(div):
    """How a chapter is named in a citation: 'Book 1', 'Chapter IV', 'Third Letter',
    'Morning, January 1', 'IV. The Fight'."""
    kind, n, title = div.get("type") or "", div.get("n") or "", " ".join((div.get("title") or "").split())
    if title.isupper():
        title = title.title()
    if kind.lower() in ("book", "part", "chapter") and sources.key_part(n).isdigit():
        return f"{kind.title()} {sources.key_part(n)}"
    first = re.match(r"^(.{1,28}?)\.\s", title + " ")
    if first:
        if re.fullmatch(r"[IVXLC]+|\d+", first.group(1)):   # "IV. The Fight": keep a few words of the name
            rest = title[first.end():]
            return f"{first.group(1)}. " + (rest if len(rest) <= 40 else rest[:38].rsplit(" ", 1)[0] + "…")
        return first.group(1)
    return title if len(title) <= 40 else title[:38].rsplit(" ", 1)[0] + "…"


def label_of(div):
    """A division's heading, for matching locations: 'Book ONE. Thoughts Helpful...'."""
    kind, n, title = div.get("type") or "", div.get("n") or "", div.get("title") or ""
    if kind.lower() in ("book", "part", "chapter", "section") and n:
        return f"{kind} {n}. {title}"
    return title


class CcelParser(Paragraphs):
    """Paragraphs of a CCEL ThML book, each tagged with the division (chapter) it's in."""
    SKIP_TAGS = Paragraphs.SKIP_TAGS | {"note", "thml.head"}
    DIVS = {f"div{i}" for i in range(1, 7)}
    BLOCKS = Paragraphs.BLOCKS | DIVS

    def __init__(self):
        super().__init__()
        self.divs, self.info, self.blocks = [], {}, []

    def _flush(self):
        text = " ".join("".join(self.buf).replace("​", "").split())
        if text:
            self.blocks.append((self.divs[-1] if self.divs else None, text))
        self.buf = []

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag in self.DIVS:
            a = dict(attrs)
            div_id = a.get("id") or f"_{len(self.info)}"
            self.info[div_id] = {**a, "parent": self.divs[-1] if self.divs else None}
            self.divs.append(div_id)

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag in self.DIVS and self.divs:
            self.divs.pop()


def ccel_pages(work):
    spec = work["pages"]
    author, book = spec["ccel"].split("/")
    xml = fetch(f"https://ccel.org/ccel/{author[0]}/{author}/{book}.xml")
    rights = re.search(r"<DC.Rights>([^<]*)", xml)
    print(f"    CCEL rights: {rights.group(1).strip() if rights else '(not stated)'}")
    parser = CcelParser()
    parser.feed(xml)
    parser.close()
    levels = spec.get("levels", 1)
    only = re.compile(spec["only"]) if spec.get("only") else None
    paras, order = {}, []
    for div_id, text in parser.blocks:
        if div_id is None:
            continue
        if div_id not in paras:
            paras[div_id] = []
            order.append(div_id)
        paras[div_id].append(text)
    pages = []
    for div_id in order:
        path, d = [], div_id
        while d:
            path.insert(0, d)
            d = parser.info[d]["parent"]
        divs = [parser.info[d] for d in path]
        if any(SKIP_TITLES.match((x.get("title") or "").strip()) for x in divs):
            continue
        if only and not only.search(div_id):
            continue
        texts = paras[div_id]
        if sum(len(x.split()) for x in texts) < 80:      # a title page, or a heading with nothing under it
            continue
        pages.append({
            "key": div_id,
            "title": f"https://ccel.org/ccel/{author}/{book}/{book}.{div_id}.html",
            "label": " / ".join(label_of(x) for x in divs),
            "location": ", ".join(short_label(x) for x in divs[-levels:]) if levels else "",
            "paragraphs": assign_sections(texts, work.get("sections")),
        })
    return pages


class BlockParser(Paragraphs):
    """Paragraphs of an HTML book, with each block's tag and anchor (for Gutenberg)."""
    def __init__(self):
        super().__init__()
        self.blocks, self.cur = [], [None, None]

    def _flush(self):
        text = " ".join("".join(self.buf).replace("​", "").split())
        if text:
            self.blocks.append((self.cur[0], self.cur[1], text))
        self.buf = []
        self.cur = [None, None]

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        a = dict(attrs)
        if tag in self.BLOCKS and not self._skipping():
            self.cur = [tag, a.get("id")]
        elif a.get("id") and self.cur[1] is None:
            self.cur[1] = a["id"]


def gutenberg_pages(work):
    spec = work["pages"]
    n = spec["gutenberg"]
    url = f"https://www.gutenberg.org/cache/epub/{n}/pg{n}-images.html"
    html = fetch(url)
    start = html.find("</header>", html.find('id="pg-header"')) + len("</header>")
    end = html.rfind("<", 0, html.find('id="pg-footer"'))
    parser = BlockParser()
    parser.feed(html[start:end])
    parser.close()
    split_tag = spec.get("split")
    split_text = re.compile(spec["split_text"]) if spec.get("split_text") else None
    skip = re.compile(spec["skip"], re.I) if spec.get("skip") else SKIP_TITLES
    pages, current, before = [], None, ""
    for tag, anchor, text in parser.blocks:
        if (split_tag and tag == split_tag) or (split_text and split_text.match(text)):
            label = re.sub(r"^([IVXLC]+)\.?\s+", r"\1. ", text.rstrip("."))   # "I  Following" -> "I. Following"
            if re.fullmatch(r"[IVXLC]+\.?", before):        # the chapter's numeral, on the line above
                label = f"{before.rstrip('.')}. {label}"
            current = None if skip.match(label) else {
                "key": anchor or label, "title": f"{url}#{anchor}" if anchor else url,
                "label": label, "location": short_label({"title": label}), "texts": []}
            if current:
                pages.append(current)
        elif current and not re.fullmatch(r"[IVXLC]+\.?", text):
            current["texts"].append(text)
        before = text
    for page in pages:
        page["paragraphs"] = assign_sections(page.pop("texts"), work.get("sections"))
    return [p for p in pages if sum(len(x["text"].split()) for x in p["paragraphs"]) >= 40]


def outside_pages(work):
    """Pages (with their text) of a work that isn't on Wikisource."""
    spec = work["pages"]
    if "bible" in spec:
        return bible_pages(spec)
    if "ccel" in spec:
        return ccel_pages(work)
    return gutenberg_pages(work)


def link_works(url):
    try:
        fetch(url)
        return True
    except Exception as e:
        print(f"    BROKEN: {url}  ({e})")
        return False


def pages_of(work):
    if "prefix" in work["pages"]:
        return list_prefix_pages(work["pages"])
    return list(sources.enumerate_pages(work))


def download_work(work_id, work, page_list):
    pages = []
    for key, title in page_list:
        q = urllib.parse.urlencode({"title": title, "action": "render"})
        parser = Paragraphs()
        parser.feed(fetch(f"{RENDER}?{q}"))
        parser.close()
        paras = assign_sections(parser.paras, work.get("sections"))
        pages.append({"key": key, "title": title, "paragraphs": paras})
        print(f"    {title}  ({sum(len(p['text'].split()) for p in paras)} words)")
    save_text(work_id, work, pages)


def save_text(work_id, work, pages):
    sources.TEXTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {"work": work_id, "translation": work["translation"],
           "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "pages": pages}
    target = sources.TEXTS_DIR / f"{work_id}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)  # atomic: the app never sees a half-written file


def main():
    check_only = "--check" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    works = sources.works()
    problems = 0

    for work_id, work in works.items():
        if only and work_id not in only:
            continue
        if any(k in work["pages"] for k in ("bible", "ccel", "gutenberg")):
            print(f"\n{work['author'] or 'Bible'}, {work['title']}")
            pages = outside_pages(work)
            ok = bool(pages) and link_works(sources.page_url(work["work_page"])) and link_works(pages[0]["title"])
            if not pages:
                print(f"    BROKEN: no pages found for {work_id}; check 'pages' in sources.json")
            if not ok:
                problems += 1
                continue
            words = sum(len(x["text"].split()) for p in pages for x in p["paragraphs"])
            print(f"    {len(pages)} pages, {words:,} words; first: {pages[0].get('location') or pages[0]['key']}"
                  f", last: {pages[-1].get('location') or pages[-1]['key']}")
            if not check_only:
                save_text(work_id, work, pages)
            continue
        page_list = pages_of(work)
        titles = [work["work_page"]] + [t for _, t in page_list]
        print(f"\n{work['author']}, {work['title']}: checking {len(titles)} links")
        missing = missing_titles(titles)
        if len(page_list) == 0:
            missing.append(f"(no pages found for {work_id}; check 'pages' in sources.json)")
        for t in missing:
            print(f"    BROKEN: {sources.page_url(t)}")
        problems += len(missing)
        if not missing:
            print("    all links work")
        if not check_only and not missing:
            print("    downloading text...")
            download_work(work_id, work, page_list)

    print()
    if problems:
        print(f"{problems} broken link(s). Fix them in editions/<edition>/sources.json and run this again.")
        sys.exit(1)
    print("All source links work." + ("" if check_only else " Texts saved to sources/texts/."))


if __name__ == "__main__":
    main()
