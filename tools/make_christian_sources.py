"""Writes editions/christian/sources.json (the Bible books are listed here once,
rather than typed out 21 times). Run it again after changing the lists below:

    uv run python tools/make_christian_sources.py

then download the texts:

    $env:EDITION = "christian"; uv run python tools/build_sources.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "editions" / "christian" / "sources.json"
PHILOSOPHY = json.loads((ROOT / "editions" / "philosophy" / "sources.json").read_text(encoding="utf-8"))

# (id, name in the BSB file, Bible Hub slug, how it's cited, guidance for Claude)
BIBLE = [
    ("psalms", "Psalm", "psalms", "Psalm",
     "Prayer in every mood: lament, confession (32, 51), trust (23, 27, 46, 62, 91), longing (42, 63, 84), "
     "searching the heart (139), praise. Give the speaker's honest feelings their due before turning to hope."),
    ("proverbs", "Proverbs", "proverbs", "Proverbs",
     "Wisdom for daily conduct: speech, work, friendship, money, pride and humility, the fear of the LORD (1, 3, 4, 16)."),
    ("ecclesiastes", "Ecclesiastes", "ecclesiastes", "Ecclesiastes",
     "The Teacher on vanity, toil, time and seasons (3), enjoying God's gifts, and what endures (12). "
     "Call the author 'the Teacher' (the book's own name for him, traditionally Solomon): "
     "'the Teacher of Ecclesiastes' on first mention, then just 'the Teacher' or 'he'."),
    ("isaiah", "Isaiah", "isaiah", "Isaiah",
     "Comfort and calling: the vision of holiness (6), comfort for the weary (40), the Servant (42, 53), invitation (55)."),
    ("matthew", "Matthew", "matthew", "Matthew",
     "The Sermon on the Mount (5-7): the Beatitudes, anger, lust, enemies, giving and prayer in secret, treasure, "
     "worry, judging, the narrow gate. Parables (13, 18, 20, 25). The weary invited to rest (11:28-30)."),
    ("mark", "Mark", "mark", "Mark",
     "Jesus in action: the cost of following (8:34-38), service (10:42-45), faith and doubt (9:24), Gethsemane (14)."),
    ("luke", "Luke", "luke", "Luke",
     "Parables of grace: the Good Samaritan (10), Mary and Martha (10), the rich fool (12), the lost sheep, "
     "coin and son (15), the Pharisee and the tax collector (18). Zacchaeus (19)."),
    ("john", "John", "john", "John",
     "The Word made flesh (1), new birth (3), living water (4), the 'I am' sayings, washing feet (13), "
     "abiding in the vine (15), the high-priestly prayer (17), Peter restored (21)."),
    ("romans", "Romans", "romans", "Romans",
     "Paul's fullest gospel: all have sinned (3), justified by faith (5), dead to sin (6), the divided self (7), "
     "no condemnation and life in the Spirit (8), living sacrifices and transformed minds (12)."),
    ("1_corinthians", "1 Corinthians", "1_corinthians", "1 Corinthians",
     "The foolishness of the cross (1), the body (12), love (13), resurrection hope (15)."),
    ("2_corinthians", "2 Corinthians", "2_corinthians", "2 Corinthians",
     "Comfort in affliction (1), treasure in jars of clay (4), new creation (5), strength in weakness (12)."),
    ("galatians", "Galatians", "galatians", "Galatians",
     "Freedom in Christ, not by works of the law (2:20, 5:1), the fruit of the Spirit against the works of the flesh (5), "
     "bearing one another's burdens (6)."),
    ("ephesians", "Ephesians", "ephesians", "Ephesians",
     "Saved by grace through faith (2), rooted in love (3), the old self and the new (4), speech and anger (4), "
     "walking as children of light (5), the armour of God (6)."),
    ("philippians", "Philippians", "philippians", "Philippians",
     "The mind of Christ and his humility (2), counting all as loss (3), pressing on (3), anxiety and peace, "
     "whatever is true, contentment in all circumstances (4)."),
    ("colossians", "Colossians", "colossians", "Colossians",
     "Setting your mind on things above, putting off and putting on (3), work done as for the Lord (3)."),
    ("1_thessalonians", "1 Thessalonians", "1_thessalonians", "1 Thessalonians",
     "A quiet life (4), grief with hope (4), rejoice always, pray without ceasing, give thanks (5)."),
    ("2_timothy", "2 Timothy", "2_timothy", "2 Timothy",
     "Not a spirit of fear (1), endurance (2), Scripture (3), finishing the race (4)."),
    ("hebrews", "Hebrews", "hebrews", "Hebrews",
     "A high priest who sympathises with weakness (4), faith (11), running the race and discipline (12), contentment (13)."),
    ("james", "James", "james", "James",
     "Trials and wisdom (1), doers of the word (1), favouritism (2), faith and works (2), the tongue (3), "
     "humility and planning (4), patience (5)."),
    ("1_peter", "1 Peter", "1_peter", "1 Peter",
     "Living hope (1), suffering for doing good (2-4), casting your anxieties on him (5:7), humility (5)."),
    ("1_john", "1 John", "1_john", "1 John",
     "Walking in the light, confession and forgiveness (1), love for one another, God is love (4), assurance (5)."),
]

# Shared with Reflections: same texts, so the downloaded copies are shared too.
SHARED = {
    "confessions": "The restless heart (1.1), disordered loves, the divided will (8), conversion in the garden (8.12), "
                   "memory and longing for God (10). Augustine speaks to God throughout: quote it as prayer.",
    "summa": "Use it for grace (I-II.109-114), the theological virtues of faith, hope and charity (II-II.1-46), "
             "humility and pride (II-II.161-162), sloth (II-II.35). Quote the body of an article or a reply, "
             "not the objections, which state views Aquinas rejects.",
}

WRITERS = {
    "luther_liberty": {
        "author": "Martin Luther", "title": "Concerning Christian Liberty",
        "translation": "R. S. Grignon (Harvard Classics, 1910)",
        "location_format": "leave empty",
        "note": "Also known as The Freedom of a Christian (1520): the Christian is a free lord of all, subject to "
                "none, and a dutiful servant of all, subject to all; faith alone justifies, and good works flow "
                "from a heart already set free.",
        "work_page": "https://ccel.org/ccel/luther/christianliberty",
        "pages": {"ccel": "luther/christianliberty", "levels": 0}, "sections": None},
    "calvin_christian_life": {
        "author": "John Calvin", "title": "On the Christian Life",
        "translation": "Henry Beveridge (1845)",
        "location_format": "chapter, e.g. Chapter 2",
        "note": "Institutes III.6-10, often printed as the Golden Booklet: self-denial ('we are not our own'), "
                "bearing the cross, meditating on the life to come, using this life's gifts rightly.",
        "work_page": "https://ccel.org/ccel/calvin/chr_life",
        "pages": {"ccel": "calvin/chr_life"}, "sections": None},
    "bunyan_pilgrim": {
        "author": "John Bunyan", "title": "The Pilgrim's Progress",
        "translation": "original English (1678)",
        "location_format": "part and stage, e.g. Part I, The Third Stage",
        "note": "Christian's journey: the burden at the cross, the Slough of Despond, the Interpreter's house, "
                "Vanity Fair, Doubting Castle and Giant Despair, the River. Characters speak the lines; name them.",
        "work_page": "https://ccel.org/ccel/bunyan/pilgrim",
        "pages": {"ccel": "bunyan/pilgrim", "levels": 2}, "sections": None},
    "bunyan_grace": {
        "author": "John Bunyan", "title": "Grace Abounding to the Chief of Sinners",
        "translation": "original English (1666)",
        "location_format": "paragraph number, e.g. 37",
        "note": "Bunyan's own story of doubt, temptation, despair and assurance: honest about a troubled conscience.",
        "work_page": "https://ccel.org/ccel/bunyan/grace",
        "pages": {"ccel": "bunyan/grace"}, "sections": "numbered_paragraphs",
        "location_from_section": True, "section_prefix": "§"},
    "edwards_affections": {
        "author": "Jonathan Edwards", "title": "Religious Affections",
        "translation": "original English (1746)",
        "location_format": "part and numbered sign, e.g. Part III, XII (or just Part I)",
        "note": "Telling true religion from its imitations: 'true religion, in great part, consists in holy "
                "affections'; feelings are no proof either way; the sign of real grace is a changed life and practice.",
        "work_page": "https://ccel.org/ccel/edwards/affections",
        "pages": {"ccel": "edwards/affections", "levels": 2}, "sections": None},
    "lawrence_practice": {
        "author": "Brother Lawrence", "title": "The Practice of the Presence of God",
        "translation": "the classic anonymous English translation",
        "location_format": "conversation or letter, e.g. Fourth Conversation or Second Letter",
        "note": "A kitchen worker's habit of constant, simple conversation with God among the pots and pans; "
                "returning without fuss after distraction.",
        "work_page": "https://ccel.org/ccel/lawrence/practice",
        "pages": {"ccel": "lawrence/practice"}, "sections": None},
    "murray_prayer": {
        "author": "Andrew Murray", "title": "With Christ in the School of Prayer",
        "translation": "original English (1885)",
        "location_format": "lesson, e.g. Third Lesson",
        "note": "Thirty-one lessons on prayer from Jesus' own words: praying in secret, asking with confidence, "
                "persevering, the Father's love.",
        "work_page": "https://ccel.org/ccel/murray/prayer",
        "pages": {"ccel": "murray/prayer"}, "sections": None},
    "murray_humility": {
        "author": "Andrew Murray", "title": "Humility: The Beauty of Holiness",
        "translation": "original English (1895)",
        "location_format": "chapter, e.g. Chapter VI",
        "note": "Humility as the creature's proper place before God and the root of every virtue; pride as the root "
                "of sin; humility tested in daily life, among other people, more than in prayer.",
        "work_page": "https://www.gutenberg.org/ebooks/57121",
        "pages": {"gutenberg": 57121,
                  "split_text": r"^(Notes|Humility: The [A-Za-z ]{1,30}|The Humility of Jesus|Humility (in|and) [A-Za-z ]{1,25})\.?$"},
        "sections": None},
    "spurgeon_morning_evening": {
        "author": "Charles Spurgeon", "title": "Morning and Evening",
        "translation": "original English (1865-1868)",
        "location_format": "reading, e.g. Morning, January 14 or Evening, March 3",
        "note": "Daily devotional readings, each on one verse: warm, vivid, Christ-centred.",
        "work_page": "https://ccel.org/ccel/spurgeon/morneve",
        "pages": {"ccel": "spurgeon/morneve"}, "sections": None},
    "ryle_holiness": {
        "author": "J. C. Ryle", "title": "Holiness",
        "translation": "original English (1879)",
        "location_format": "chapter, e.g. IV (The Fight)",
        "note": "Plain, searching chapters: sin, sanctification, the fight, the cost, growth, assurance, "
                "'Lot: a Beacon', 'Remember Lot's Wife', 'Christ is All'.",
        "work_page": "https://ccel.org/ccel/ryle/holiness",
        "pages": {"ccel": "ryle/holiness"}, "sections": None},
    "owen_mortification": {
        "author": "John Owen", "title": "Of the Mortification of Sin in Believers",
        "translation": "original English (1656)",
        "location_format": "chapter, e.g. Chapter II",
        "note": "Putting sin to death by the Spirit (Romans 8:13): 'be killing sin or it will be killing you'; "
                "dealing with the root, not just the symptom; looking to Christ.",
        "work_page": "https://ccel.org/ccel/owen/mort",
        "pages": {"ccel": "owen/mort"}, "sections": None},
    "tozer_pursuit": {
        "author": "A. W. Tozer", "title": "The Pursuit of God",
        "translation": "original English (1948)",
        "location_format": "chapter, e.g. Chapter II",
        "note": "Hungering for God himself rather than for religion: possessing nothing, removing the veil, "
                "the gaze of the soul, the sacrament of ordinary living.",
        "work_page": "https://www.gutenberg.org/ebooks/25141",
        "pages": {"gutenberg": 25141, "split": "h2", "skip": r"^(contents|introduction)"}, "sections": None},
    "baxter_rest": {
        "author": "Richard Baxter", "title": "The Saints' Everlasting Rest",
        "translation": "original English (1650), abridged by Benjamin Fawcett",
        "location_format": "chapter, e.g. Chapter 9",
        "note": "Heaven as the believer's rest, and the habit of heavenly meditation as medicine for a troubled, "
                "earth-bound heart.",
        "work_page": "https://ccel.org/ccel/baxter/saints_rest",
        "pages": {"ccel": "baxter/saints_rest"}, "sections": None},
    "kempis_imitation": {
        "author": "Thomas à Kempis", "title": "The Imitation of Christ",
        "translation": "Aloysius Croft and Harold Bolton (1940)",
        "location_format": "book and chapter, e.g. Book 1, Chapter 3",
        "note": "Short chapters on humility, inner peace, self-knowledge, bearing others' faults, and following Christ. "
                "Book 4 is on the Sacrament: rarely the right lens here.",
        "work_page": "https://ccel.org/ccel/kempis/imitation",
        "pages": {"ccel": "kempis/imitation", "levels": 2}, "sections": None},
}


def main():
    works = {}
    for work_id, book, slug, cited, note in BIBLE:
        works[work_id] = {
            "author": "", "title": "Psalms" if book == "Psalm" else book, "cite_title": cited,
            "translation": "Berean Standard Bible",
            "location_format": "chapter:verse, e.g. 3:16 (or chapter:verse-verse for a short run, e.g. 3:16-17)",
            "note": note,
            "work_page": f"https://biblehub.com/bsb/{slug}/1.htm",
            "pages": {"bible": book, "slug": slug}, "sections": "verses",
            "location_separator": ":",
        }
    for work_id, note in SHARED.items():
        works[work_id] = {**PHILOSOPHY["works"][work_id], "note": note}
    works.update(WRITERS)
    out = {
        "_about": [
            "In His Steps' library: the only works Claude may quote, and the app (never Claude) builds",
            "every link from this file. MADE BY tools/make_christian_sources.py: edit that, not this.",
            "Then download the texts:  $env:EDITION = \"christian\"; uv run python tools/build_sources.py",
            "",
            "Scripture is the Berean Standard Bible (public domain since 2023), linked to Bible Hub.",
            "Writers come from the Christian Classics Ethereal Library (CCEL), Project Gutenberg, or",
            "Wikisource (Augustine and Aquinas, shared with Reflections). Writers still in copyright",
            "(C. S. Lewis, Bonhoeffer, Dallas Willard, Tim Keller, Oswald Chambers) are not listed:",
            "Claude may paraphrase and name them, but never quote them.",
            "See editions/philosophy/sources.json for what each field means.",
        ],
        "site": PHILOSOPHY["site"],
        "works": works,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(works)} works")


if __name__ == "__main__":
    main()
