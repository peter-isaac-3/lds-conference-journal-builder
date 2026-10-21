"""
Download a General Conference from churchofjesuschrist.org into the folder
format used by build_journal.py. For personal study use.

Requires: pip install requests beautifulsoup4
"""
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (personal study-journal builder)"}
MONTHS = {"01": "January", "02": "February", "03": "March", "04": "April", "05": "May",
          "06": "June", "07": "July", "08": "August", "09": "September", "10": "October",
          "11": "November", "12": "December"}
DELAY = 1.0  # seconds between requests, to be polite to the server


STOP = threading.Event()   # set() this from another thread to stop a download


class Cancelled(Exception):
    """Raised when the user presses Stop."""


def _check_stop():
    if STOP.is_set():
        raise Cancelled("Stopped.")


def get_html(url: str, retries: int = 3, timeout: int = 30) -> str:
    last = None
    for attempt in range(retries):               # retry briefly on flaky connections
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            # The site doesn't always declare a charset, and requests then guesses
            # Latin-1, which garbles curly quotes and accents. Pages are always UTF-8.
            return r.content.decode("utf-8", errors="replace")
        except requests.RequestException as e:
            last = e
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (400, 401, 403, 404, 410):
                break
            time.sleep(1.5 * (attempt + 1))
    raise last


def safe_name(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|“”]', "", text)
    return re.sub(r"\s+", " ", text).strip()[:90]


def conference_base(url: str):
    """Return (base_url_without_query, year, month) from a conference URL."""
    p = urlparse(url)
    m = re.search(r"/general-conference/(\d{4})/(\d{2})", p.path)
    if not m:
        raise ValueError("That doesn't look like a general-conference URL, e.g. "
                         "https://www.churchofjesuschrist.org/study/general-conference/2026/10?lang=eng")
    base = urlunparse((p.scheme, p.netloc, p.path[:m.end()], "", "", ""))
    return base, m.group(1), m.group(2)


def list_sessions(index_html: str, base: str):
    """Return [(session_name, [(talk_url), ...]), ...] in page order."""
    soup = BeautifulSoup(index_html, "html.parser")
    base_path = urlparse(base).path
    sessions, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0]
        path = urlparse(urljoin(base, href)).path
        if not path.startswith(base_path + "/"):
            continue
        slug = path[len(base_path) + 1:]
        if not slug or "/" in slug or slug.startswith("_") or path in seen:
            continue
        if slug.endswith("session"):
            name = a.get_text(" ", strip=True)
            # the first occurrence is the short nav link, e.g. "Saturday Morning Session"
            name = re.split(r"\s+The .*session of the", name)[0].strip()
            name = re.sub(r"\s*-{3,}\s*$", "", name).strip()
            seen.add(path)
            sessions.append([name, []])
        else:
            # Recent conferences use slugs like "12gong"; older ones use words like
            # "the-stone-cut-without-hands". Either way, it's a talk.
            if not sessions:                      # talks listed with no session heading
                sessions.append(["Conference Talks", []])
            seen.add(path)
            sessions[-1][1].append(urljoin(base, path) + "?lang=eng")
    return [(n, t) for n, t in sessions if t]


def _inline_text(node) -> str:
    """Text of a tag with footnote links -> [N] and emphasis -> *text*."""
    node = BeautifulSoup(str(node), "html.parser")
    for a in node.find_all("a", class_="note-ref"):
        m = re.search(r"note(\d+)", a.get("href", ""))
        num = m.group(1) if m else ""
        if not num:
            sup = a.find("sup")
            num = (sup.get("data-value") or sup.get_text(strip=True)) if sup else ""
        a.replace_with(f"[{num}]" if num else "")
    for em in node.find_all(["em", "i"]):
        t = em.get_text()
        em.replace_with(f"*{t.strip()}*" if t.strip() else t)
    text = node.get_text()
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("\u00a0", " ")


def parse_talk_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if not h1:
        raise RuntimeError("No title found; the page may require JavaScript or its layout changed.")
    title = h1.get_text(" ", strip=True)
    author = soup.find(class_="author-name")
    role = soup.find(class_="author-role")
    speaker = re.sub(r"^By\s+", "", author.get_text(" ", strip=True)) if author else ""
    role_text = role.get_text(" ", strip=True) if role else ""

    body = soup.find(class_="body-block") or soup.find("article") or soup.find("main") or soup
    notes_el = soup.find("footer", class_="notes") or body.find(class_="notes")

    blocks = []
    for el in body.find_all(["p", "h2", "h3", "h4", "li", "tr"]):
        if notes_el and notes_el in el.parents:
            continue
        if el.name in ("p", "li", "h2", "h3", "h4") and el.find_parent("table") is not None:
            continue  # table cell contents are handled via the row
        if el.name == "tr":
            cells = [_inline_text(c) for c in el.find_all(["th", "td"])]
            row = "  |  ".join(c for c in cells if c)
            if row:
                blocks.append("\u2022 " + row)
            continue
        if el.find_parent("li") is not None and el.name == "p":
            continue  # paragraph inside a list item: handled via the li
        if el.name == "li" and el.find("ul"):
            continue
        text = _inline_text(el)
        if not text:
            continue
        if el.name in ("h2", "h3", "h4"):
            blocks.append("## " + text)
        elif el.name == "li":
            blocks.append("• " + text)
        else:
            blocks.append(text)

    footnotes = []
    if notes_el:
        for i, li in enumerate(notes_el.find_all("li", recursive=True), 1):
            if li.find_parent("li") is not None:
                continue
            footnotes.append(f"{i}. {_inline_text(li)}")
    return {"title": title, "speaker": speaker, "role": role_text,
            "blocks": blocks, "footnotes": footnotes}


def write_talk_file(path: Path, talk: dict):
    lines = [f"Title: {talk['title']}"]
    if talk["speaker"]:
        lines.append(f"Speaker: {talk['speaker']}")
    if talk["role"]:
        lines.append(f"Role: {talk['role']}")
    if talk.get("conference"):
        lines.append(f"Conference: {talk['conference']}")
    text = "\n".join(lines) + "\n\n" + "\n\n".join(talk["blocks"]) + "\n"
    if talk["footnotes"]:
        text += "\n---footnotes---\n" + "\n".join(talk["footnotes"]) + "\n"
    path.write_text(text, encoding="utf-8")


def _download_session_conference(url: str, out_dir: Path = None, name: str = ""):
    """Download a single conference (with its sessions) into out_dir."""
    base, year, month = conference_base(url)
    label = f"{MONTHS.get(month, month)} {year}"
    out_dir = out_dir or Path(f"conference_{year}_{month}")
    out_dir.mkdir(parents=True, exist_ok=True)

    index_url = base + "?lang=eng"
    print(f"Reading contents page: {index_url}")
    sessions = list_sessions(get_html(index_url), base)
    if not sessions:
        raise RuntimeError("Couldn't find any sessions/talks on that page.")
    total = sum(len(t) for _, t in sessions)
    print(f"Found {len(sessions)} sessions and {total} talks.")

    (out_dir / "conference.json").write_text(json.dumps({
        "title": f"{label} General Conference", "subtitle": "Study Journal", "name": name,
    }, indent=2), encoding="utf-8")

    done = 0
    for si, (sname, urls) in enumerate(sessions, 1):
        sdir = out_dir / f"{si:02d} {safe_name(sname)}"
        sdir.mkdir(exist_ok=True)
        for ti, turl in enumerate(urls, 1):
            _check_stop()
            done += 1
            existing = list(sdir.glob(f"{ti:02d} *.txt"))
            if existing:  # already downloaded on an earlier run
                print(f"[{done}/{total}] cached: {existing[0].name}")
                continue
            try:
                talk = parse_talk_html(get_html(turl))
            except Exception as e:  # keep going; report at the end
                print(f"[{done}/{total}] FAILED {turl}: {e}")
                continue
            talk["conference"] = label
            write_talk_file(sdir / f"{ti:02d} {safe_name(talk['title'])}.txt", talk)
            print(f"[{done}/{total}] {talk['title']}")
            time.sleep(DELAY)
    return out_dir


# ----------------------------------------------------------------------------
# Collections: all talks by one speaker, or all talks on one topic
# ----------------------------------------------------------------------------
def classify_url(url: str):
    """Return ("speaker"|"topic"|"conference", slug_or_None)."""
    path = urlparse(url).path
    m = re.search(r"/general-conference/speakers/([^/?#]+)", path)
    if m:
        return "speaker", m.group(1)
    m = re.search(r"/general-conference/topics/([^/?#]+)", path)
    if m:
        return "topic", m.group(1)
    if re.search(r"/general-conference/\d{4}/\d{2}", path):
        return "conference", None
    raise ValueError("Unrecognized URL. Use a conference page (.../general-conference/2026/10), "
                     "a speaker page (.../general-conference/speakers/dallin-h-oaks) or a topic "
                     "page (.../general-conference/topics/atonement).")


def list_collection(html: str, page_url: str):
    """Return (page_heading, [{'url','year','month','slug'}, ...]) in page order."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    heading = h1.get_text(" ", strip=True) if h1 else ""
    pat = re.compile(r"/study/general-conference/(\d{4})/(\d{2})/([^/?#]+)/?$")
    talks, seen = [], set()
    for a in soup.find_all("a", href=True):
        path = urlparse(urljoin(page_url, a["href"])).path
        m = pat.search(path)
        if not m or path in seen:
            continue
        year, month, slug = m.groups()
        if slug.endswith("session"):
            continue
        seen.add(path)
        talks.append({"url": urljoin(page_url, path) + "?lang=eng",
                      "year": year, "month": month, "slug": slug})
    return heading, talks


def _download_collection(url: str, kind: str, slug: str, out_dir: Path = None,
                         owner: str = "", oldest_first: bool = False):
    folder = "speakers" if kind == "speaker" else "topics"
    page_url = f"https://www.churchofjesuschrist.org/study/general-conference/{folder}/{slug}?lang=eng"
    out_dir = out_dir or Path(f"{kind}_{slug}")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading {kind} page: {page_url}")
    heading, talks = list_collection(get_html(page_url), page_url)
    if not talks:
        raise RuntimeError("Couldn't find any talks on that page.")
    heading = heading or slug.replace("-", " ").title()
    if oldest_first:
        talks.reverse()
    total = len(talks)
    print(f"Found {total} talks for \"{heading}\" "
          f"(roughly {int(total * (DELAY + 1) / 60) + 1} minutes to download).")

    (out_dir / "conference.json").write_text(json.dumps({
        "layout": "collection",
        "kind": kind,
        "kicker": "General Conference Talks by" if kind == "speaker" else "General Conference Talks on",
        "title": heading,
        "subtitle": "Study Journal",
        "name": owner,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    for i, t in enumerate(talks, 1):
        _check_stop()
        label = f"{MONTHS.get(t['month'], t['month'])} {t['year']}"
        target = out_dir / f"{i:03d} {t['year']}-{t['month']} {t['slug']}.txt"
        existing = list(out_dir.glob(f"* {t['year']}-{t['month']} {t['slug']}.txt"))
        if existing:                       # downloaded on an earlier run
            if existing[0] != target:      # list order changed: keep the numbering current
                existing[0].rename(target)
            print(f"[{i}/{total}] cached: {label} {t['slug']}")
            continue
        try:
            talk = parse_talk_html(get_html(t["url"]))
        except Exception as e:
            print(f"[{i}/{total}] FAILED {t['url']}: {e}")
            continue
        talk["conference"] = label
        write_talk_file(target, talk)
        print(f"[{i}/{total}] {label}: {talk['title']}")
        time.sleep(DELAY)
    return out_dir


def download_conference(url: str, out_dir: Path = None, name: str = "", oldest_first: bool = False):
    """Download a conference, speaker page, or topic page; return the folder path."""
    kind, slug = classify_url(url)
    if kind == "conference":
        return _download_session_conference(url, out_dir, name)
    return _download_collection(url, kind, slug, out_dir, name, oldest_first)
