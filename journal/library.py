"""Each reader's own library: which of the edition's writers their reflections draw on.

The edition lists its writers in edition.json ("writers"): a name, an era, a line
on what they bring (shown in Settings → Library, and given to Claude), and
whether they're on by default. A reader can switch writers on or off; their
choices (only where they differ from the defaults) are saved with their
preferences as {"name": true/false}. Scripture, in an edition that has it, is
always included: books of the Bible have no writer to switch off.

Everything a reflection is told about the library comes through here, so a
writer who is off is simply never mentioned to Claude: not their notes, not
their works, not their name among the voices to choose from.
"""
from . import sources
from .config import MIN_WRITERS, WRITERS

__all__ = ["MIN_WRITERS", "WRITERS", "Library"]


def _works_by(name):
    """The quotable works by this writer: {work id: work}."""
    return {wid: w for wid, w in sources.works().items() if w["author"] == name}


class Library:
    """One reader's library: the writers that are on, given their saved choices."""

    def __init__(self, choices=None):
        choices = choices or {}
        self.writers = [w for w in WRITERS if choices.get(w["name"], w.get("default", True))]
        self.off = [w for w in WRITERS if w not in self.writers]

    @property
    def narrowed(self):
        """True if anything is switched off (by choice or by default)."""
        return bool(self.off)

    def voices(self):
        """The names a reflection can say it drew on (Socrates comes with Plato)."""
        return [v for w in self.writers for v in w.get("voices", [w["name"]])]

    def works(self):
        """The works that may be quoted or referred to: the writers' own, and Scripture."""
        on = {w["name"] for w in self.writers}
        return {wid: w for wid, w in sources.works().items() if not w["author"] or w["author"] in on}

    def unquotable(self):
        """Writers who are on but have no quotable work here (still in copyright)."""
        return [w for w in self.writers if not _works_by(w["name"])]

    def guide(self):
        """The notes on each writer, for Claude's instructions."""
        lines = []
        for w in self.writers:
            line = f"  - **{w.get('label', w['name'])}:** {w['guide']}"
            if w.get("caution"):
                line += f" {w['caution']}"
            if w.get("works") and not _works_by(w["name"]):
                line += f" (Books: {w['works']}.)"
            lines.append(line)
        return "\n".join(lines)

    def off_note(self, brief=False):
        """A plain instruction about the writers this reader isn't drawing on, or None.
        brief: for writing prompts, whose instructions don't describe the writers."""
        if not self.off:
            return None
        names = ", ".join(w.get("label", w["name"]) for w in self.off)
        note = ("## This reader's library\n\n"
                f"This reader's library does not include: {names}. Never name, quote, paraphrase or allude to "
                "them, even where these instructions mention one of them as an example.")
        if brief:
            return note
        scripture = any(not w["author"] for w in sources.works().values())
        return note + " Draw only on the writers described above" + (" and on Scripture." if scripture else ".")


def for_settings(choices=None):
    """The writers by era, for the Library tab: name, what they bring, their works, and whether they're on."""
    on = {w["name"] for w in Library(choices).writers}
    eras = []
    for w in WRITERS:
        if not eras or eras[-1]["name"] != w["era"]:
            eras.append({"name": w["era"], "writers": []})
        titles = [work["title"] for work in _works_by(w["name"]).values()]
        eras[-1]["writers"].append({
            "name": w["name"], "label": w.get("label", w["name"]), "about": w["guide"],
            "works": ", ".join(titles) or w.get("works", ""), "quotable": bool(titles),
            "default": w.get("default", True), "on": w["name"] in on,
            # why a writer with nothing quotable isn't quoted: still in copyright, or no checked text here yet
            "unquoted": "" if titles else "no checked text here yet" if w.get("no_text") else "in copyright",
        })
    return eras
