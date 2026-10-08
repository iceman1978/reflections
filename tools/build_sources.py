"""Check every link in config/sources.json and download the texts used to verify quotes.

Run after editing the source map:

    uv run python tools/build_sources.py            # check links + download texts
    uv run python tools/build_sources.py --check    # only check that links work

Texts are saved to sources/texts/<work>.json. Takes a few minutes, because
it pauses between requests to be polite to Wikisource.
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
NUMBER_ALONE = re.compile(r"^(\d+[a-z]?)\.$")


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
        print(f"{problems} broken link(s). Fix them in config/sources.json and run this again.")
        sys.exit(1)
    print("All source links work." + ("" if check_only else " Texts saved to sources/texts/."))


if __name__ == "__main__":
    main()
