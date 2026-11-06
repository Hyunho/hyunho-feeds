#!/usr/bin/env python3
"""Shared RSS hosting on Cloudflare Pages (stdlib only). See README.md (Korean guide for bots).

Usage (works from any working directory; all paths are relative to this script):
  # Valley AI (single channel valley.xml + unified feed.xml). Briefings & study posts both go here.
  python3 publish.py add --channel valley --title "2026-10-03 아침 브리핑" --file briefing.md [--date ISO] [--link URL] [--no-deploy]
  # (omit --channel or use --channel valley; both write to items.json -> valley.xml)
  # Bot channels (each bot has its own feed <secret>/<slug>.xml, last 60 items)
  python3 publish.py channel create --slug deals --name "할인정보" [--description "..."] [--link URL]
  python3 publish.py channel list
  python3 publish.py channel remove --slug deals --yes        # unregister + delete its stored items
  python3 publish.py add --channel deals --title "..." --file post.md [--date ISO] [--link URL] [--no-deploy]
  python3 publish.py deploy | build | resplit [--no-deploy]
  python3 publish.py progress [--date YYYY-MM-DD] [--channel slug]  # D1 done/skip/todo (default: yesterday KST)

Outputs (under public/<secret>/):
  feed.xml        UNIFIED feed "통합 브리핑": Valley AI items + every bot channel post, date desc, max 100.
                  Valley items keep their title/guid; bot items get title "[<channel name>] <title>" and the
                  same guid as in their channel feed. Rebuilt on every add/create/remove/build.
  valley.xml      Valley AI single channel (briefings + study posts) -- slug "valley"
  <slug>.xml      bot channel feeds (registered in channels.json, items in items/<slug>.json)
  channels.opml   every feed for one-shot import (unified + Valley AI + bot channels)
  index.html      private channel index (only under the secret path; site root index.html stays blank)

Topic split (legacy, storage-only): at `add`/`resplit` time, briefing markdown may still be classified into
  market/insights/news/community and stored on item["channels"], but those topic XML files are NOT written
  and are NOT listed in OPML/index. Valley AI is one channel: valley.xml only.
  --channel-file CH=path still overrides the stored split for that topic key.

Concurrency: every mutating command (add/resplit/build/deploy/channel create|remove) holds an exclusive
fcntl lock on .publish.lock for the whole read-modify-write + build + deploy.

Env: CLOUDFLARE_API_TOKEN (required for deploy), CLOUDFLARE_ACCOUNT_ID (optional;
otherwise read from account_id.txt or discovered via GET /accounts).
"""
import argparse, contextlib, unicodedata, fcntl, html, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error, uuid
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ITEMS = os.path.join(BASE, "items.json")
PUBLIC = os.path.join(BASE, "public")
CHANNELS_JSON = os.path.join(BASE, "channels.json")
CHAN_ITEMS_DIR = os.path.join(BASE, "items")
LOCK_FILE = os.path.join(BASE, ".publish.lock")
DOCS_DIR = os.path.join(BASE, "docs")
LOCK_TIMEOUT = int(os.environ.get("PUBLISH_LOCK_TIMEOUT", "900"))  # seconds
KST = timezone(timedelta(hours=9))
MAX_ITEMS = 60
UNIFIED_MAX = 100
UNIFIED_TITLE = "통합 브리핑"
UNIFIED_DESC = "valley.town + 봇 채널 통합 피드"
WRANGLER = os.environ.get("WRANGLER_PKG", "wrangler@3")  # v3 runs on Node 20; v4 needs Node >= 22


def read_or_create(name, factory):
    p = os.path.join(BASE, name)
    if os.path.exists(p):
        v = open(p).read().strip()
        if v:
            return v
    v = factory()
    open(p, "w").write(v + "\n")
    return v


def secret():
    return read_or_create("secret.txt", lambda: subprocess.check_output(["openssl", "rand", "-hex", "16"], text=True).strip())


def project():
    return read_or_create("project.txt", lambda: "hyunho-feeds-" + os.urandom(3).hex())


# ---------------- minimal markdown -> html ----------------
def link_attrs(url):
    """Add target=_blank for external http(s); keep app schemes / same-site plain."""
    u = (url or "").strip()
    low = u.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return ""
    try:
        host = (urllib.parse.urlparse(u).hostname or "").lower()
    except Exception:
        host = ""
    if host == "hyunho-feeds.pages.dev" or host.endswith(".hyunho-feeds.pages.dev"):
        return ""
    return ' target="_blank" rel="noopener"'

_A_HREF_RE = re.compile(r'<a\s+([^>]*?)href=("([^"]*)"|\'([^\']*)\')([^>]*)>', re.I)

def rewrite_external_links(html_str):
    """Ensure external http(s) anchors in stored HTML open in a new tab."""
    def repl(m):
        pre, href_tok, dqh, sqh, post = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        url = dqh if dqh is not None else sqh
        attrs = (pre or "") + (post or "")
        if re.search(r"\btarget\s*=", attrs, re.I):
            return m.group(0)
        extra = link_attrs(url or "")
        if not extra:
            return m.group(0)
        return "<a %shref=%s%s%s>" % (pre, href_tok, post, extra)
    return _A_HREF_RE.sub(repl, html_str or "")

def inline(text):
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "[":
            # balanced bracket label, then (url)
            depth, j = 0, i
            while j < n:
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j < n and j + 1 < n and text[j + 1] == "(":
                k = text.find(")", j + 2)
                if k != -1:
                    label, url = text[i + 1:j], text[j + 2:k].strip()
                    out.append('<a href="%s"%s>%s</a>' % (html.escape(url, quote=True), link_attrs(url), inline(label)))
                    i = k + 1
                    continue
            out.append(html.escape(c)); i += 1; continue
        if text.startswith("**", i):
            k = text.find("**", i + 2)
            if k != -1:
                out.append("<strong>%s</strong>" % inline(text[i + 2:k])); i = k + 2; continue
        if c == "<" or c == ">" or c == "&" or c == '"':
            out.append(html.escape(c)); i += 1; continue
        # bare URLs
        m = re.match(r"https?://[^\s<>()]+", text[i:])
        if m and (i == 0 or not text[i - 1].isalnum()):
            u = m.group(0)
            out.append('<a href="%s"%s>%s</a>' % (html.escape(u, quote=True), link_attrs(u), html.escape(u))); i += len(u); continue
        out.append(c); i += 1
    return "".join(out)


BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")


def md_to_html(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out, para, stack = [], [], []  # stack of indent levels with open <ul>

    def flush_para():
        if para:
            out.append("<p>%s</p>" % "<br/>".join(inline(l.strip()) for l in para)); para.clear()

    def close_lists(to_depth=0):
        while len(stack) > to_depth:
            stack.pop(); out.append("</li></ul>")

    for line in lines:
        m = BULLET.match(line)
        if m:
            flush_para()
            indent = len(m.group(1).expandtabs(4))
            level = indent // 2
            if not stack or level > stack[-1]:
                out.append("<ul><li>"); stack.append(level)
            else:
                while stack and level < stack[-1]:
                    stack.pop(); out.append("</li></ul>")
                if not stack:
                    out.append("<ul><li>"); stack.append(level)
                else:
                    out.append("</li><li>")
            out.append(inline(m.group(2).strip()))
        elif not line.strip():
            flush_para(); close_lists()
        else:
            if stack and line.startswith(" "):  # continuation of list item
                out.append(" " + inline(line.strip())); continue
            close_lists()
            h = re.match(r"^(#{1,6})\s+(.*)$", line)
            if h:
                flush_para(); lvl = len(h.group(1))
                out.append("<h%d>%s</h%d>" % (lvl, inline(h.group(2).strip()), lvl))
            else:
                para.append(line)
    flush_para(); close_lists()
    return "\n".join(out)



# ---------------- channels ----------------
CHANNELS = [  # (key, Korean name, description)
    ("market", "시장 흐름", "프리미엄 분석팀 글, 시황 digest, 거시/금리"),
    ("insights", "투자가설", "Valley Insights 예측대회 투자가설"),
    ("news", "뉴스룸", "valley.town 뉴스룸 기사"),
    ("community", "커뮤니티", "커뮤니티·밸리 톡 글과 공지"),
]
WRITE_TOPIC_FEEDS = False  # Valley AI is one channel; topic XMLs disabled (split may still be stored)
CHANNEL_NAMES = {k: n for k, n, _ in CHANNELS}
URL_RULES = [  # checked in order against each URL
    ("/competitions/valley-insights/", "insights"),
    ("/premium/", "market"),
    ("/newsroom/", "news"),
    ("/space/", "community"),
    ("/community/", "community"),
]
HEADER_RULES = [
    ("market", ["시장", "프리미엄", "흐름", "시황", "거시", "금리", "매크로"]),
    ("insights", ["투자가설", "예측대회", "인사이트", "insight"]),
    ("news", ["뉴스룸", "뉴스"]),
    ("community", ["커뮤니티", "공지", "톡", "스페이스"]),
]
HEADER_LINE = re.compile(r"^\s*\*\*(.+?)\*\*\s*:?\s*$")
URL_RE = re.compile(r"https?://[^\s<>()\]]+")


def header_channel(text):
    t = text.lower()
    hits = {ch for ch, kws in HEADER_RULES if any(k.lower() in t for k in kws)}
    return hits.pop() if len(hits) == 1 else None


def url_channel(block):
    for u in URL_RE.findall(block):
        for pat, ch in URL_RULES:
            if pat in u:
                return ch
        # first link unrecognised -> keep looking at later links
    return None


def split_channels(md):
    """Split briefing markdown into {channel: markdown}. Only channels with >=1 bullet are returned."""
    lines = md.replace("\r\n", "\n").split("\n")
    intro, blocks = [], []  # blocks: (kind, header_text, header_ch, text)
    cur_header, cur_hch, seen_struct, i = None, None, False, 0
    while i < len(lines):
        line = lines[i]
        h = HEADER_LINE.match(line)
        m = BULLET.match(line)
        if h:
            seen_struct = True
            cur_header, cur_hch = line.strip(), header_channel(h.group(1)); i += 1
        elif m and len(m.group(1).expandtabs(4)) < 2:
            seen_struct = True
            blk = [line]; i += 1
            while i < len(lines) and lines[i].strip() and (lines[i][:1] in (" ", "\t")) :
                blk.append(lines[i]); i += 1
            blocks.append(("bullet", cur_header, cur_hch, "\n".join(blk)))
        elif not line.strip():
            i += 1
        else:
            if not seen_struct:
                intro.append(line)
            else:
                blocks.append(("para", cur_header, cur_hch, line))
            i += 1
    out = {}  # ch -> list of md chunks
    last_header = {}
    has_bullet = set()
    for kind, header, hch, text in blocks:
        ch = (url_channel(text) if kind == "bullet" else None) or hch or "market"
        chunks = out.setdefault(ch, [])
        if header and last_header.get(ch) != header:
            chunks.append(("h", header)); last_header[ch] = header
        chunks.append((kind, text))
        if kind == "bullet":
            has_bullet.add(ch)
    result = {}
    for ch, _, _ in CHANNELS:
        if ch not in has_bullet:
            continue
        parts = []
        if ch == "market" and intro:
            parts.append("\n".join(intro)); parts.append("")
        prev = None
        for kind, text in out[ch]:
            if kind != "bullet" or prev != "bullet":
                if parts and parts[-1] != "":
                    parts.append("")
            parts.append(text); prev = kind
        result[ch] = "\n".join(parts).strip() + "\n"
    return result


def apply_split(item, overrides=None):
    """(Re)compute item['channels'] from item['md'], keeping manual overrides."""
    overrides = dict(overrides or {})
    old = item.get("channels") or {}
    for ch in item.get("channel_overrides") or []:
        if ch not in overrides and ch in old:
            overrides[ch] = old[ch]
    chans = split_channels(item["md"]) if item.get("md") else {}
    chans.update({k: v for k, v in overrides.items() if v.strip()})
    item["channels"] = {ch: chans[ch] for ch, _, _ in CHANNELS if ch in chans}
    if overrides:
        item["channel_overrides"] = sorted(overrides)
    return item


# ---------------- items / storage ----------------
def write_atomic(path, text):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


@contextlib.contextmanager
def publish_lock():
    """Exclusive inter-process lock for read-modify-write + build + deploy."""
    f = open(LOCK_FILE, "a+")
    start, warned = time.time(), False
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if not warned:
                print("Another publish is in progress; waiting for lock...", file=sys.stderr); warned = True
            if time.time() - start > LOCK_TIMEOUT:
                f.close(); sys.exit("ERROR: could not acquire publish lock within %ds (%s)" % (LOCK_TIMEOUT, LOCK_FILE))
            time.sleep(1)
    try:
        yield
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN); f.close()


def load_items():
    return load_json(ITEMS, [])


def save_items(items):
    save_json(ITEMS, items)


# ---------------- bot channel registry ----------------
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_SLUGS = {"feed", "market", "insights", "news", "community", "channels", "valley", "index"}
VALLEY = {"name": "Valley AI", "description": "valley.town 브리핑 · 공부 일정", "file": "valley.xml",
          "builtin": True, "link": "https://www.valley.town/",
          "agent_id": "31c9ced1-decf-43ca-aee1-a59e15c564a9", "agent_name": "valley ai 학습"}
AGENT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def agent_footer(meta):
    """Footer linking to the Grok Bot agent that owns a channel (added at build time)."""
    aid = (meta or {}).get("agent_id")
    if not aid:
        return ""
    name = meta.get("agent_name") or meta.get("name") or "봇"
    return '<p><a href="grokbot://app/v1/agent?id=%s">🤖 %s 열기</a></p>' % (html.escape(aid), html.escape(name, quote=False))


def validate_agent_id(aid):
    if not AGENT_RE.match(aid or ""):
        sys.exit("ERROR: --agent-id must be a lowercase UUID like 63e168c7-5838-4b10-a2f9-65e7fa9c53f7")


def load_channels():
    """{slug: meta}. 'valley' is always present as a builtin alias of valley.xml."""
    data = load_json(CHANNELS_JSON, {})
    data.setdefault("channels", {})
    data["channels"]["valley"] = dict(VALLEY, **{k: v for k, v in data["channels"].get("valley", {}).items()
                                                 if k in ("created", "agent_id", "agent_name")})
    return data


def save_channels(data):
    save_json(CHANNELS_JSON, data)


def bot_slugs(data):
    return [s for s, m in data["channels"].items() if not m.get("builtin")]


def chan_items_path(slug):
    return os.path.join(CHAN_ITEMS_DIR, slug + ".json")


def load_chan_items(slug):
    return load_json(chan_items_path(slug), [])


def by_date(items):
    return sorted(items, key=lambda it: datetime.fromisoformat(it["pubDate"]), reverse=True)


def validate_slug(slug):
    if not slug or len(slug) > 40 or not SLUG_RE.match(slug):
        sys.exit("ERROR: invalid slug %r: use 1-40 chars of [a-z0-9-] (no leading/trailing/double '-')" % slug)
    if slug in RESERVED_SLUGS:
        sys.exit("ERROR: slug %r is reserved (%s)" % (slug, ", ".join(sorted(RESERVED_SLUGS))))


def base_url():
    return "https://%s.pages.dev/%s/" % (project(), secret())


def feed_url(slug, data=None):
    data = data or load_channels()
    m = data["channels"].get(slug) or {}
    return base_url() + m.get("file", slug + ".xml")



# ---------------- today_routine frontmatter v1 ----------------
# Spec: https://github.com/Hyunho/hyunho-feeds/blob/main/docs/today-routine-frontmatter-v1.md
FM_RE = re.compile(r"\A(?:\ufeff)?---[ \t]*\r?\n(.*?)(?:\r?\n)?^---[ \t]*(?:\r?\n|\Z)", re.S | re.M)
START_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
ROUTINE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")


def split_frontmatter(md):
    """Return (yaml_text or None, body)."""
    m = FM_RE.match(md)
    if not m:
        return None, md
    return m.group(1), md[m.end():]


def _strict_yaml_load(text):
    try:
        import yaml
    except ImportError:
        sys.exit("ERROR: PyYAML needed for frontmatter validation: pip install --user pyyaml")

    class StrictLoader(yaml.SafeLoader):
        pass

    def no_alias(self, *a, **k):
        raise yaml.YAMLError("anchors/aliases are not allowed")

    def construct_mapping(loader, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise yaml.YAMLError("expected a mapping")
        seen = set()
        for k, _ in node.value:
            if k.tag == "tag:yaml.org,2002:merge":
                raise yaml.YAMLError("merge keys (<<) are not allowed (line %d)" % (k.start_mark.line + 1))
            key = loader.construct_object(k, deep=deep)
            if key in seen:
                raise yaml.YAMLError("duplicate key %r (line %d)" % (key, k.start_mark.line + 1))
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    # reject anchors / aliases / custom (non-implicit) tags by scanning events
    for ev in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(ev, yaml.AliasEvent) or getattr(ev, "anchor", None):
            raise yaml.YAMLError("anchors/aliases are not allowed (line %d)" % (ev.start_mark.line + 1))
        tag = getattr(ev, "tag", None)
        implicit = getattr(ev, "implicit", True)
        if tag and tag not in ("!", None) and not (implicit is True or (isinstance(implicit, tuple) and any(implicit))):
            raise yaml.YAMLError("explicit/custom tags are not allowed: %s (line %d)" % (tag, ev.start_mark.line + 1))
    return yaml.load(text, Loader=StrictLoader)


RFC3339 = re.compile(r"^(\d{4}-\d{2}-\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})\Z")


def _valid_date(v):
    if not isinstance(v, str) or not re.match(r"^\d{4}-\d{2}-\d{2}\Z", v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%d"); return True
    except ValueError:
        return False


def _valid_rfc3339(v):
    m = isinstance(v, str) and RFC3339.match(v)
    if not m or not _valid_date(m.group(1)):
        return False
    hh, mm, ss = int(m.group(2)), int(m.group(3)), int(m.group(4))
    off = m.group(6)
    if off not in ("Z", "z") and (int(off[1:3]) > 23 or int(off[4:6]) > 59):
        return False
    return hh <= 23 and mm <= 59 and ss <= 60


def validate_calendar_link(cl):
    w = "today_routine.calendar_link"
    if not isinstance(cl, dict):
        return ["%s: must be a mapping (omit it for no link; null/empty not allowed)" % w]
    errs = []

    def req_str(k):
        if k not in cl:
            errs.append("%s.%s: required" % (w, k)); return None
        v = cl[k]
        if not isinstance(v, str) or not v.strip():
            errs.append("%s.%s: must be a non-empty string (got %r)" % (w, k, v)); return None
        return v

    prov = req_str("provider")
    if prov is not None and prov != "google":
        errs.append("%s.provider: only \"google\" is supported (got %r)" % (w, prov))
    cid = req_str("calendar_id")
    if cid is not None:
        if cid.strip().lower() == "primary":
            errs.append("%s.calendar_id: use the real calendar id, not \"primary\"" % w)
        elif re.match(r"^https?://", cid.strip(), re.I):
            errs.append("%s.calendar_id: must be the calendar id, not a URL" % w)
    eid = req_str("event_id")
    if eid is not None and (re.match(r"^https?://", eid.strip(), re.I) or "@" in eid):
        errs.append("%s.event_id: must be the Google API event id (not a URL or iCalUID)" % w)
    scope = req_str("scope")
    if scope is not None and scope not in ("event", "series", "occurrence"):
        errs.append("%s.scope: must be event, series or occurrence (got %r)" % (w, scope)); scope = None
    ost = cl.get("original_start_time", None)
    has = "original_start_time" in cl
    if scope in ("event", "series") and has:
        errs.append("%s.original_start_time: not allowed for scope %s" % (w, scope))
    elif scope == "occurrence":
        if not has:
            errs.append("%s.original_start_time: required for scope occurrence" % w)
        elif not isinstance(ost, dict):
            errs.append("%s.original_start_time: must be a mapping with exactly one of date_time / date" % w)
        else:
            keys = [k for k in ("date_time", "date") if k in ost]
            extra = [k for k in ost if k not in ("date_time", "date")]
            if len(keys) != 1:
                errs.append("%s.original_start_time: exactly one of date_time / date required" % w)
            if extra:
                errs.append("%s.original_start_time: unknown field(s) %s" % (w, ", ".join(map(str, extra))))
            if "date_time" in ost and not _valid_rfc3339(ost["date_time"]):
                errs.append("%s.original_start_time.date_time: must be a quoted RFC 3339 time with explicit offset "
                            "or Z, e.g. \"2026-10-21T09:00:00+09:00\" (got %r)" % (w, ost["date_time"]))
            if "date" in ost and not _valid_date(ost["date"]):
                errs.append("%s.original_start_time.date: must be a quoted valid \"YYYY-MM-DD\" (got %r)" % (w, ost["date"]))
    return errs


def validate_routine_md(md):
    """Validate frontmatter. Returns (errors, frontmatter dict or None)."""
    fm_text, _ = split_frontmatter(md)
    if fm_text is None:
        if re.match(r"\A(?:\ufeff)?---[ \t]*\r?\n", md):
            return ["frontmatter: opening '---' without a closing '---' line"], None
        return [], None
    try:
        fm = _strict_yaml_load(fm_text)
    except Exception as e:  # yaml.YAMLError
        return ["frontmatter YAML: %s" % e], None
    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        return ["frontmatter must be a YAML mapping"], None
    errs = []
    if "title" in fm and not isinstance(fm["title"], str):
        errs.append("title: must be a string")
    if "date" in fm:
        if not isinstance(fm["date"], str):
            errs.append("date: must be a quoted string \"YYYY-MM-DD\" (unquoted YAML dates are converted)")
        elif not re.match(r"^\d{4}-\d{2}-\d{2}\Z", fm["date"]):
            errs.append("date: must be YYYY-MM-DD")
    if "today_routine" not in fm:
        return errs, fm
    r = fm["today_routine"]
    if not isinstance(r, dict):
        return errs + ["today_routine: must be a mapping"], fm

    def is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    def chk_id(v, where):
        if not isinstance(v, str) or not ROUTINE_ID.match(v):
            errs.append("%s: must be a string matching ^[a-z0-9][a-z0-9_-]*$ (got %r)" % (where, v))
            return False
        return True

    def chk_title(v, where):
        if not isinstance(v, str) or not v.strip():
            errs.append("%s: required non-blank string" % where)

    if "version" not in r:
        errs.append("today_routine.version: required (1)")
    elif not is_int(r["version"]) or r["version"] != 1:
        errs.append("today_routine.version: must be integer 1 (got %r)" % (r["version"],))
    if "id" not in r:
        errs.append("today_routine.id: required")
    else:
        chk_id(r["id"], "today_routine.id")
    if "start_time" in r:
        stt = r["start_time"]
        unquoted = re.search(r"^[ \t]*start_time[ \t]*:[ \t]*(?![\"'\s])", fm_text, re.M)
        if unquoted or not isinstance(stt, str) or not START_TIME.match(stt):
            errs.append('today_routine.start_time: must be a quoted 24h "HH:mm" string 00:00-23:59 (got %r; '
                        'unquoted 09:00 parses as a number)' % (stt,))
    if "calendar_link" in r:
        errs.extend(validate_calendar_link(r["calendar_link"]))
    tasks = r.get("tasks")
    if "tasks" not in r:
        errs.append("today_routine.tasks: required (array of >= 1 task)")
    elif not isinstance(tasks, list) or not tasks:
        errs.append("today_routine.tasks: must be a non-empty array")
    else:
        tids = set()
        for i, t in enumerate(tasks):
            w = "tasks[%d]" % i
            if not isinstance(t, dict):
                errs.append("%s: must be a mapping" % w); continue
            if "id" not in t:
                errs.append("%s.id: required" % w)
            elif chk_id(t["id"], w + ".id"):
                if t["id"] in tids:
                    errs.append("%s.id: duplicate task id %r" % (w, t["id"]))
                tids.add(t["id"])
            chk_title(t.get("title"), w + ".title")
            if "duration_minutes" in t and not (is_int(t["duration_minutes"]) and t["duration_minutes"] >= 1):
                errs.append("%s.duration_minutes: must be an integer >= 1 (got %r)" % (w, t["duration_minutes"]))
            if "steps" in t:
                steps = t["steps"]
                if steps is None:
                    steps = []
                if not isinstance(steps, list):
                    errs.append("%s.steps: must be an array" % w); continue
                sids = set()
                for j, st in enumerate(steps):
                    sw = "%s.steps[%d]" % (w, j)
                    if not isinstance(st, dict):
                        errs.append("%s: must be a mapping" % sw); continue
                    if "steps" in st:
                        errs.append("%s.steps: nested steps are not supported (one level only)" % sw)
                    if "id" not in st:
                        errs.append("%s.id: required" % sw)
                    elif chk_id(st["id"], sw + ".id"):
                        if st["id"] in sids:
                            errs.append("%s.id: duplicate step id %r within task" % (sw, st["id"]))
                        sids.add(st["id"])
                    chk_title(st.get("title"), sw + ".title")
                    for bad in ("done", "completed", "status", "checked"):
                        if bad in st:
                            errs.append("%s.%s: completion/progress state must not be in frontmatter" % (sw, bad))
            for bad in ("done", "completed", "status", "checked", "progress"):
                if bad in t:
                    errs.append("%s.%s: completion/progress state must not be in frontmatter" % (w, bad))
    return errs, fm


def check_md_or_exit(md, label="--file"):
    errs, fm = validate_routine_md(md)
    if errs:
        sys.exit("ERROR: %s frontmatter is invalid (today_routine v1):\n  - %s" % (label, "\n  - ".join(errs)))
    return fm


def has_frontmatter(md):
    return bool(md) and split_frontmatter(md)[0] is not None


def body_html(md):
    """HTML for readers: frontmatter is never rendered into the HTML body."""
    return md_to_html(split_frontmatter(md)[1])


def raw_md_for(it):
    """Original Markdown to carry in content:encoded (only for frontmatter docs)."""
    md = it.get("md")
    return md if has_frontmatter(md) else None


# ---------------- feed rendering ----------------
def cdata(s):
    return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def rss_doc(title, description, entries, link="https://www.valley.town/"):
    """entries: list of dicts with title, guid, pubDate (iso), html, optional link."""
    esc = html.escape
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">', "<channel>",
             "<title>%s</title>" % esc(title, quote=False),
             "<link>%s</link>" % esc(link, quote=False),
             "<description>%s</description>" % esc(description, quote=False),
             "<language>ko</language>",
             "<lastBuildDate>%s</lastBuildDate>" % format_datetime(datetime.now(KST))]
    for e in entries:
        parts.append("<item>")
        parts.append("<title>%s</title>" % esc(e["title"], quote=False))
        if e.get("link"):
            parts.append("<link>%s</link>" % esc(e["link"]))
        parts.append('<guid isPermaLink="false">%s</guid>' % esc(e["guid"]))
        parts.append("<pubDate>%s</pubDate>" % format_datetime(datetime.fromisoformat(e["pubDate"])))
        parts.append("<description>%s</description>" % cdata(e["html"]))
        if e.get("md"):  # full original Markdown incl. frontmatter (today_routine v1)
            parts.append("<content:encoded>%s</content:encoded>" % cdata(e["md"]))
        parts.append("</item>")
    parts += ["</channel>", "</rss>", ""]
    return "\n".join(parts)


def opml_doc(burl, groups):
    """groups: list of (group title, [(fname, title, htmlUrl)])."""
    esc = lambda v: html.escape(v, quote=True)
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<opml version="2.0">', "<head>",
           "<title>Valley AI 피드</title>",
           "<dateCreated>%s</dateCreated>" % format_datetime(datetime.now(KST)), "</head>", "<body>"]
    for gtitle, feeds in groups:  # gtitle None -> feeds at top level (no folder)
        if not feeds:
            continue
        if gtitle:
            out.append('<outline text="%s" title="%s">' % (esc(gtitle), esc(gtitle)))
        for fname, title, hurl in feeds:
            out.append('<outline type="rss" text="%s" title="%s" xmlUrl="%s" htmlUrl="%s"/>'
                       % (esc(title), esc(title), esc(burl + fname), esc(hurl)))
        if gtitle:
            out.append("</outline>")
    out += ["</body>", "</opml>", ""]
    return "\n".join(out)


def private_index(groups):
    """Landing page under /<secret>/ — channel home for the unified feed."""
    esc = html.escape
    rows = []
    for gtitle, feeds in groups:
        if not feeds:
            continue
        rows.append(("<h2>%s</h2>" % esc(gtitle) if gtitle else "") + "<ul>")
        for fname, title, _ in feeds:
            rows.append('<li><a href="%s">%s</a> <code>%s</code></li>' % (esc(fname), esc(title), esc(fname)))
        rows.append("</ul>")
    body = "".join(rows)
    return (
        "<!doctype html>\n"
        '<html lang="ko"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        '<meta name="referrer" content="no-referrer">\n'
        "<title>Valley Feed · RSS</title>\n"
        "<style>\n"
        ":root{color-scheme:light dark}\n"
        "body{font-family:system-ui,-apple-system,sans-serif;max-width:40rem;"
        "margin:2rem auto;padding:0 1.25rem;line-height:1.55}\n"
        "h1{font-size:1.35rem;margin:0 0 .2rem}\n"
        ".sub{opacity:.7;font-size:.95rem;margin:0 0 1.25rem}\n"
        "h2{font-size:1.05rem;margin:1.4rem 0 .45rem;padding-bottom:.25rem;"
        "border-bottom:1px solid #ccc}\n"
        "ul{padding-left:1.2rem;margin:.35rem 0}\n"
        "li{margin:.35rem 0}\n"
        "a{color:#0b57d0}\n"
        "@media (prefers-color-scheme:dark){a{color:#8ab4f8} h2{border-bottom-color:#444}}\n"
        "code{font-size:.82em;opacity:.65;margin-left:.35rem}\n"
        ".opml{margin:1rem 0 .25rem}\n"
        ".note{opacity:.65;font-size:.88rem;margin-top:1.75rem}\n"
        "</style></head><body>\n"
        "<h1>Valley Feed</h1>\n"
        '<p class="sub">통합 브리핑 · 채널별 RSS / Unified &amp; per-channel feeds</p>\n'
        '<p class="opml"><a href="today">📋 오늘 루틴 타임라인</a></p>\n'
        '<p class="opml"><a href="sns">📱 SNS 구독 타임라인</a></p>\n'
        '<p class="opml"><a href="channels.opml">channels.opml</a>'
        " — 한 번에 구독 / import all</p>\n"
        '<p class="opml">할 일 Inbox API 문서 / Todo inbox API docs: '
        '<a href="/api/inbox/docs.md">docs.md</a> · <a href="/api/inbox/openapi.json">openapi.json</a></p>\n'
        + body + "\n"
        '<p class="note">이 주소는 비공개 경로입니다. 공유에 주의하세요.'
        " / Private path — do not share publicly.</p>\n"
        "</body></html>\n"
    )



def item_page(e, d, burl):
    """Write a standalone HTML page for an item without its own link; return its URL."""
    esc = html.escape
    slug = re.sub(r"[^A-Za-z0-9-]", "-", e["guid"])
    pdir = os.path.join(d, "p")
    os.makedirs(pdir, exist_ok=True)
    when = datetime.fromisoformat(e["pubDate"]).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    page = ("<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta name=\"robots\" content=\"noindex, nofollow\"><meta name=\"referrer\" content=\"no-referrer\">"
            "<title>%s</title><style>:root{color-scheme:light dark}body{font-family:system-ui,-apple-system,sans-serif;"
            "max-width:40rem;margin:2rem auto;padding:0 1.25rem;line-height:1.65}h1{font-size:1.35rem}"
            ".meta{opacity:.65;font-size:.9rem}a{color:#0b57d0}@media (prefers-color-scheme:dark){a{color:#8ab4f8}}"
            "</style></head><body><h1>%s</h1><p class=\"meta\">%s</p>%s</body></html>\n"
            % (esc(e["title"]), esc(e["title"]), when, rewrite_external_links(e["html"])))
    open(os.path.join(pdir, slug + ".html"), "w", encoding="utf-8").write(page)
    return burl + "p/" + slug + ".html"


def with_pages(entries, d, burl):
    return [e if e.get("link") else dict(e, link=item_page(e, d, burl)) for e in entries]


def routine_entries(it, chan_name, agent_id=None):
    md = it.get("md")
    if not has_frontmatter(md):
        return []
    errs, fm = validate_routine_md(md)
    if errs or not fm or "today_routine" not in fm:
        return []
    r = fm["today_routine"]
    body = split_frontmatter(md)[1]
    links = [{"t": t, "u": u} for t, u in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", body)]
    day = fm.get("date") or datetime.fromisoformat(it["pubDate"]).astimezone(KST).strftime("%Y-%m-%d")
    return [{"rid": r["id"], "date": day, "start": r.get("start_time"), "cal": r.get("calendar_link"), "pub": it["pubDate"], "title": fm.get("title") or it["title"],
             "channel": chan_name, "agent": agent_id, "links": links,
             "tasks": [{"id": t["id"], "title": t["title"], "min": t.get("duration_minutes"),
                        "steps": [{"id": x["id"], "title": x["title"]} for x in (t.get("steps") or [])]}
                       for t in r["tasks"]]}]


TODAY_HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow"><meta name="referrer" content="no-referrer">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>오늘 루틴</title>
<style>
:root{color-scheme:light dark;--bg:#f7f7f9;--card:#fff;--fg:#1c1c1e;--sub:#6b6b72;--line:#d0d0d6;--acc:#0b57d0;--dot:#0b57d0;--ol-bullet:#a0a0a8;--ol-hover:rgba(127,127,127,.12)}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--fg:#f2f2f7;--sub:#9a9aa2;--line:#3a3a3c;--acc:#8ab4f8;--dot:#8ab4f8;--ol-bullet:#6e6e76;--ol-hover:rgba(255,255,255,.08)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,system-ui,sans-serif;
padding:max(1rem,env(safe-area-inset-top)) 1rem calc(2rem + env(safe-area-inset-bottom))}
main{max-width:40rem;margin:0 auto}h1{font-size:1.5rem;margin:.2rem 0}.sub{color:var(--sub);font-size:.9rem;margin:0 0 1rem}
.hdr{display:flex;align-items:center;justify-content:space-between;gap:.75rem;margin:.2rem 0}
.hdr h1{margin:0;flex:1 1 auto}
#refresh-btn{flex:0 0 auto;width:2.5rem;height:2.5rem;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg);font-size:1.25rem;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center;-webkit-tap-highlight-color:transparent;box-shadow:0 1px 2px rgba(0,0,0,.06)}
#refresh-btn:active,#refresh-btn.busy{opacity:.6}
#refresh-btn.busy{animation:spin .8s linear infinite}
@keyframes spin{to{-webkit-transform:rotate(360deg);transform:rotate(360deg)}}

.ctl{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin:.5rem 0 1rem;font-size:.95rem}
.ctl input{font-size:1rem;padding:.3rem .4rem;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.ctl button{font-size:.9rem;padding:.35rem .7rem;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.vtog{display:inline-flex;gap:0;margin:0 0 1rem;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--card)}
.vtog button{font-size:.88rem;padding:.4rem .85rem;border:0;background:transparent;color:var(--sub);cursor:pointer;font-weight:500}
.vtog button+button{border-left:1px solid var(--line)}
.vtog button.on{background:var(--acc);color:#fff}
@media (prefers-color-scheme:dark){.vtog button.on{color:#0b0b0f}}
.card{background:var(--card);border-radius:14px;padding:1rem 1rem .6rem;margin:0 0 1rem;box-shadow:0 1px 3px rgba(0,0,0,.08);transition:opacity .15s,box-shadow .15s}
.card.cal-ev{position:relative;padding-left:calc(1rem + .35rem)}
.card.cal-ev::before{content:"";position:absolute;left:0;top:0;bottom:0;width:.35rem;border-radius:14px 0 0 14px;background:var(--cal,#888)}
.card.done.cal-ev{padding-left:calc(.85rem + .35rem)}
.ol-b.cal-ev>.ol-row{position:relative;padding-left:.7rem}
.ol-b.cal-ev>.ol-row::before{content:"";position:absolute;left:.15rem;top:.35rem;bottom:.35rem;width:.28rem;border-radius:4px;background:var(--cal,#888)}
.ol-b.cal-ev>.ol-row .ol-dot{background:var(--cal,#888)}
#toc a.cal-ev,#toc-sheet-list a.cal-ev{box-shadow:inset .28rem 0 0 var(--cal,#888)}
.card.done,.card.skip{opacity:.7;padding:.45rem .85rem;margin:0 0 .45rem;box-shadow:none}
.card.done .card-title,.card.skip .card-title{text-decoration:line-through;color:var(--sub);font-weight:500;font-size:1rem}
.card.done .card-time,.card.skip .card-time{color:var(--sub)}
.card.done .meta,.card.done .tm,.card.done .cal,.card.done ol.tl,.card.done .tot,.card.done .subtasks,
.card.skip .meta,.card.skip .tm,.card.skip .cal,.card.skip ol.tl,.card.skip .tot,.card.skip .subtasks{display:none}
.card.done .ch,.card.skip .ch{display:block;margin:0 0 .2rem;font-size:.72rem;opacity:.9}
.card.done .card-title-row,.card.skip .card-title-row{margin:0}
.card.skip{opacity:.62}
.card.skip .card-title{color:#8b9198}
.done-cb{flex:0 0 auto;width:1.15rem;height:1.15rem;margin:0;padding:0;border:2px solid var(--line);border-radius:4px;background:var(--card);color:transparent;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;font-size:.85rem;line-height:1;font-weight:700;-webkit-tap-highlight-color:transparent;box-sizing:border-box}
.done-cb[data-state="done"]{background:var(--acc);border-color:var(--acc);color:#fff}
.done-cb[data-state="done"]::before{content:"✓"}
.done-cb[data-state="skip"]{background:#9aa0a6;border-color:#9aa0a6;color:#fff}
.done-cb[data-state="skip"]::before{content:"—"}
.ol-b.done>.ol-row .ol-title,.ol-b.skip>.ol-row .ol-title{text-decoration:line-through;color:var(--sub)}
.ol-b.skip>.ol-row .ol-title{color:#8b9198}
.ol-b.done>.ol-row .ol-dot{background:var(--acc)}
.ol-b.skip>.ol-row .ol-dot{background:#9aa0a6}
.ol-row .done-cb{margin:.35rem .1rem 0 0}
.card-title-row{display:flex;align-items:center;gap:.45rem;margin:.15rem 0 .2rem}
.card-time{flex:0 0 auto;color:var(--sub);font-variant-numeric:tabular-nums;font-size:.95rem;min-width:2.7rem}
.card-title{font-size:1.15rem;font-weight:600;margin:0;line-height:1.3;flex:1 1 auto}
.ch{font-size:.8rem;color:var(--sub)}.ch a{color:var(--acc);text-decoration:none}
h2{font-size:1.15rem;margin:.15rem 0 .2rem}.meta{font-size:.85rem;color:var(--sub);margin-bottom:.6rem}
.tag{display:inline-block;font-size:.75rem;padding:.05rem .45rem;border-radius:999px;border:1px solid var(--line);margin-left:.3rem}
ol.tl{list-style:none;margin:0;padding:0 0 0 1.1rem;border-left:2px solid var(--line)}
ol.tl>li{position:relative;padding:0 0 .9rem .6rem}
ol.tl>li:before{content:"";position:absolute;left:-1.55rem;top:.4rem;width:.7rem;height:.7rem;border-radius:50%;background:var(--dot);border:2px solid var(--card)}
.tt{font-weight:600;display:flex;align-items:flex-start;gap:0}.tt a{color:var(--acc)}.tt-text{flex:1 1 auto}.tt.done-task .tt-text{text-decoration:line-through;color:var(--sub)}
.tm{font-size:.82rem;color:var(--sub)}
ul.st{margin:.25rem 0 0;padding-left:1.1rem;font-size:.92rem}ul.st li{margin:.1rem 0}
.task-cb{flex:0 0 auto;width:1.05rem;height:1.05rem;margin:.2rem .35rem 0 0;accent-color:var(--acc);cursor:pointer}
.ol-row .task-cb{margin:.4rem .15rem 0 0}
.ol-title.done-task{text-decoration:line-through;color:var(--sub)}
.tot{font-size:.9rem;font-weight:600;padding:.2rem 0 .4rem}.empty{color:var(--sub);text-align:center;padding:2rem 0}
.cal{margin:-.3rem 0 .5rem}h3{font-size:.95rem;color:var(--sub);margin:1.4rem 0 .5rem}
/* Logseq-like outline */
#list.ol-mode{background:var(--card);border-radius:14px;padding:.6rem .4rem .8rem;box-shadow:0 1px 3px rgba(0,0,0,.06)}
ul.ol,ul.ol ul{list-style:none;margin:0;padding:0}
ul.ol ul{padding-left:1.35rem;border-left:1px solid var(--line);margin-left:.55rem}
.ol-b{position:relative}
.ol-row{display:flex;align-items:flex-start;gap:.35rem;padding:.22rem .4rem;border-radius:6px;min-height:1.7rem;line-height:1.45}
.ol-row:hover{background:var(--ol-hover)}
.ol-tog{flex:0 0 1.1rem;width:1.1rem;height:1.4rem;padding:0;margin:0;border:0;background:transparent;color:var(--sub);cursor:pointer;font-size:.65rem;line-height:1.4rem;text-align:center;border-radius:4px}
.ol-tog:hover{background:var(--ol-hover);color:var(--fg)}
.ol-tog.leaf{visibility:hidden;pointer-events:none}
.ol-dot{flex:0 0 .55rem;width:.55rem;height:.55rem;margin:.45rem .15rem 0 0;border-radius:50%;background:var(--ol-bullet)}
.ol-time{flex:0 0 auto;color:var(--sub);font-variant-numeric:tabular-nums;font-size:.88rem;min-width:2.7rem}
.ol-title{flex:1 1 auto;word-break:break-word}.ol-title a{color:var(--acc);text-decoration:none}
.ol-title .tag{vertical-align:middle}
.ol-ch{flex:0 0 auto;font-size:.75rem;color:var(--sub);max-width:9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ol-b.collapsed>ul{display:none}.ol-b.collapsed>.subtasks{display:none}
.ol-b.collapsed>.ol-row .ol-tog{transform:rotate(-90deg)}
@media(max-width:520px){.ol-ch{display:none}ul.ol ul{padding-left:1.05rem;margin-left:.4rem}}

/* User subtasks (세부 작업) — compact, per card */
.subtasks{margin:.3rem 0 .5rem;padding-top:.25rem;border-top:1px dashed var(--line)}
.st-tog{display:inline-flex;align-items:center;gap:.2rem;border:0;background:transparent;color:var(--sub);font:inherit;font-size:.85rem;padding:.15rem 0;cursor:pointer;-webkit-tap-highlight-color:transparent}
.st-tog:hover{color:var(--fg)}
.st-body{margin-top:.3rem}
.st-list{list-style:none;margin:0;padding:0}
.st-list li{display:flex;align-items:flex-start;gap:.3rem;padding:.18rem 0;font-size:.92rem}
.st-list li.done > label span{text-decoration:line-through;color:var(--sub)}
.st-list label{flex:1 1 auto;display:flex;align-items:flex-start;gap:.4rem;cursor:pointer;min-width:0}
.st-list label span{word-break:break-word}
.st-list input{flex:0 0 auto;margin:.2rem 0 0;width:1.05rem;height:1.05rem;accent-color:var(--acc)}
.st-del{flex:0 0 auto;border:0;background:transparent;color:var(--sub);font-size:1.15rem;line-height:1;padding:.1rem .35rem;border-radius:6px;cursor:pointer}
.st-del:hover{background:var(--ol-hover);color:var(--fg)}
.st-add{display:flex;gap:.35rem;margin-top:.35rem}
.st-add input{flex:1 1 auto;min-width:0;font-size:.92rem;padding:.35rem .5rem;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.st-add button{flex:0 0 auto;font-size:.85rem;padding:.35rem .7rem;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.ol-b > .subtasks{margin:.15rem 0 .35rem 1.55rem;border-top:0;padding-top:0}
@media(max-width:520px){.ol-b > .subtasks{margin-left:1.1rem}}
#toc{display:none}@media(min-width:1100px){#toc{display:block;position:fixed;top:2rem;left:max(1rem,calc(50% - 20rem - 17rem));width:15rem;max-height:calc(100vh - 4rem);overflow:auto;font-size:.88rem}#toc h3{font-size:.95rem;margin:0 0 .5rem}#toc a{display:flex;gap:.5rem;padding:.3rem .4rem;border-radius:6px;text-decoration:none;color:inherit}#toc a:hover{background:rgba(127,127,127,.15)}#toc .t{color:var(--sub);font-variant-numeric:tabular-nums;min-width:2.8rem}.card,.ol-b{scroll-margin-top:1rem}}
/* Mobile bottom-sheet TOC (<1100px) */
#toc-fab,#toc-sheet,#toc-backdrop{display:none}
@media(max-width:1099.98px){
#toc-fab{display:flex;position:fixed;right:max(1rem,env(safe-area-inset-right));bottom:max(1rem,calc(.75rem + env(safe-area-inset-bottom)));z-index:40;align-items:center;justify-content:center;gap:.35rem;min-height:2.75rem;padding:.65rem 1.1rem;border:0;border-radius:999px;background:var(--acc);color:#fff;font:600 .95rem/1 -apple-system,system-ui,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.22);cursor:pointer}
@media (prefers-color-scheme:dark){#toc-fab{color:#0b0b0f}}
#toc-backdrop{display:block;position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.4);opacity:0;pointer-events:none;transition:opacity .2s}
#toc-sheet{display:flex;flex-direction:column;position:fixed;left:0;right:0;bottom:0;z-index:51;max-height:min(78vh,34rem);background:var(--card);color:var(--fg);border-radius:16px 16px 0 0;box-shadow:0 -8px 28px rgba(0,0,0,.2);transform:translateY(110%);transition:transform .25s ease;padding:0 0 calc(.5rem + env(safe-area-inset-bottom));touch-action:none}
body.toc-open #toc-backdrop{opacity:1;pointer-events:auto}
body.toc-open #toc-sheet{transform:translateY(0)}
body.toc-open{overflow:hidden}
.toc-handle{display:flex;justify-content:center;padding:.55rem 0 .2rem;cursor:grab}
.toc-handle span{width:2.4rem;height:.28rem;border-radius:99px;background:var(--line)}
.toc-sheet-hd{display:flex;align-items:center;justify-content:space-between;padding:.2rem 1rem .55rem;border-bottom:1px solid var(--line)}
.toc-sheet-hd h3{margin:0;font-size:1rem}
.toc-close{border:0;background:transparent;color:var(--sub);font-size:1.4rem;line-height:1;padding:.25rem .4rem;cursor:pointer;border-radius:8px}
.toc-close:hover{background:var(--ol-hover);color:var(--fg)}
#toc-sheet-list{overflow:auto;-webkit-overflow-scrolling:touch;padding:.4rem .55rem .8rem;flex:1 1 auto}
#toc-sheet-list a,#toc a{display:flex;gap:.5rem;padding:.45rem .55rem;border-radius:8px;text-decoration:none;color:inherit;align-items:flex-start}
#toc-sheet-list a:hover{background:var(--ol-hover)}
#toc-sheet-list .t,#toc .t{color:var(--sub);font-variant-numeric:tabular-nums;min-width:2.8rem;flex:0 0 auto}
#toc-sheet-list a.toc-done,#toc a.toc-done,#toc-sheet-list a.toc-skip,#toc a.toc-skip{opacity:.62}
#toc-sheet-list a.toc-done span:last-child,#toc a.toc-done span:last-child,
#toc-sheet-list a.toc-skip span:last-child,#toc a.toc-skip span:last-child{text-decoration:line-through}
#toc-sheet-list a.toc-skip,#toc a.toc-skip{color:#8b9198}
#toc-sheet-list a.toc-now,#toc a.toc-now{background:rgba(11,87,208,.12);font-weight:600}
@media (prefers-color-scheme:dark){#toc-sheet-list a.toc-now,#toc a.toc-now{background:rgba(138,180,248,.16)}}
#toc-sheet-list a.toc-next,#toc a.toc-next{box-shadow:inset 3px 0 0 var(--acc)}
}
</style></head><body><nav id="toc"></nav><main>
<div class="hdr"><h1>오늘 루틴</h1><button type="button" id="refresh-btn" aria-label="새로고침" title="새로고침">↻</button></div><p class="sub" id="today"></p>
<p class="sub" id="prog"></p>
<div class="vtog" id="vtog" role="group" aria-label="보기 전환"><button type="button" data-v="card" class="on">카드</button><button type="button" data-v="outline">아웃라인</button></div>
<div id="list"></div><div id="up"></div>
<p class="sub" style="margin-top:2rem"><a href="./" style="color:var(--acc)">← 피드 목록</a></p>
</main>
<button type="button" id="toc-fab" aria-controls="toc-sheet" aria-expanded="false">목차</button>
<div id="toc-backdrop" hidden></div>
<div id="toc-sheet" role="dialog" aria-modal="true" aria-labelledby="toc-sheet-title" hidden>
 <div class="toc-handle" aria-hidden="true"><span></span></div>
 <div class="toc-sheet-hd"><h3 id="toc-sheet-title">루틴 / 일정</h3><button type="button" class="toc-close" id="toc-close" aria-label="닫기">×</button></div>
 <nav id="toc-sheet-list"></nav>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
var D=JSON.parse(document.getElementById('data').textContent);
function kstToday(){var f=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'});return f.format(new Date());}
var today=kstToday(),W=['일','월','화','수','목','금','토'];
function label(d){var p=d.split('-');var dt=new Date(Date.UTC(+p[0],p[1]-1,+p[2]));return (+p[1])+'월 '+(+p[2])+'일 ('+W[dt.getUTCDay()]+')';}
document.getElementById('today').textContent='오늘 '+label(today)+' · 한국 시간 기준';
var best={},next={};
D.forEach(function(r){
 if(r.date==today){var b=best[r.rid];if(!b||r.date>b.date||(r.date==b.date&&r.pub>b.pub))best[r.rid]=r;}
 else if(r.date>today){var n=next[r.rid];if(!n||r.date<n.date||(r.date==n.date&&r.pub>n.pub))next[r.rid]=r;}
});
var KEY='today-routine-start';
var VIEW_KEY='today-view-mode';
if(!window.CSS)window.CSS={};if(typeof CSS.escape!=='function'){CSS.escape=function(s){return String(s).replace(/[^a-zA-Z0-9_-]/g,function(c){return '\\'+c;});};}
var COL_KEY='today-outline-collapsed';
var TASK_KEY='today-routine-task-done';
var TASKS_DONE={};
function loadTasksDone(){try{TASKS_DONE=JSON.parse(localStorage.getItem(TASK_KEY)||'{}')||{};}catch(e){TASKS_DONE={};}}
function saveTasksDone(){try{localStorage.setItem(TASK_KEY,JSON.stringify(TASKS_DONE));}catch(e){}}
function tk(rid,date,tid){return rid+'|'+date+'|'+tid;}
function setTaskDone(rid,date,tid,done){var k=tk(rid,date,tid);if(done)TASKS_DONE[k]=1;else delete TASKS_DONE[k];saveTasksDone();}
function userStart(rid){return '';}
function startOf(r){return userStart(r.rid)||r.start||'';}
// Fixed detailed routines for calendar cards (no feed required). Matched by Google event id, then title.
var FIXED_ROUTINES=[
 {eventId:'hepgfaqcark07inovjan8tq6ec',title:'일과 시작',calendar:'Routine',tasks:[
  {id:'power-on',title:'컴퓨터 켜기',min:1},
  {id:'start-teatree',title:'티트리 시작',min:1},
  {id:'coffee',title:'텀블러에 커피',min:4},
  {id:'instrumental',title:'가사 없는 음악 듣기',min:1},
  {id:'email',title:'이메일 확인',min:10},
  {id:'todo-list',title:'투두리스트 확인',min:10},
  {id:'prioritize',title:'오늘 할 일 우선순위 정하기',min:5},
  {id:'meetings',title:'회의 일정 확인',min:3}
 ]}
];
function matchFixedRoutine(r){
 if(!r)return null;
 for(var i=0;i<FIXED_ROUTINES.length;i++){
  var f=FIXED_ROUTINES[i];
  if(f.eventId&&r.eventId&&f.eventId===r.eventId)return f;
 }
 for(var j=0;j<FIXED_ROUTINES.length;j++){
  var g=FIXED_ROUTINES[j];
  if(g.title&&r.title===g.title&&(!g.calendar||g.calendar===r.calendar))return g;
 }
 return null;
}
function applyFixedRoutine(r){
 var f=matchFixedRoutine(r);
 if(!f)return r;
 r.tasks=f.tasks.map(function(t){return {id:t.id,title:t.title,min:t.min||null,steps:[]};});
 r.fixedRoutine=true;
 return r;
}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
// Banksalad: custom scheme best guess (unverified); App Store id1195804784 verified.
var BANKSALAD_STORE='https://apps.apple.com/kr/app/id1195804784';
var BANKSALAD_SCHEME='banksalad://';
// Telegram: tg:// opens app; App Store id686449807 verified (301).
var TELEGRAM_STORE='https://apps.apple.com/app/id686449807';
var TELEGRAM_SCHEME='tg://';
// Forge Master – Idle RPG (Lessmore GmbH); App Store id6746636289 verified 200.
var FORGE_MASTER_STORE='https://apps.apple.com/kr/app/forge-master-idle-rpg/id6746636289';
// Calendar colors: Google Calendar MCP list_calendars omits backgroundColor (2026-10-30).
// Palette = sampled from user's Google Calendar screenshot 2026-10-30; keyed by summary + today-cal aliases (개인=hyunho).
var BOT_NAMES={'063518fa-34a7-43f1-8c62-a7acc644346d':'forge master game','0b622bec-1489-4d8f-abfc-ada3b5970014':'독서','208ee518-3647-41ec-91aa-3fe9677c0226':'아침 글쓰기','23c15155-d45e-482d-8314-7b5f537c2b19':'모닝 자동차','2b263b22-bc54-46b0-80ff-fb44f1589caa':'영어 공부','31c9ced1-decf-43ca-aee1-a59e15c564a9':'valley ai 학습','804ba387-766e-4d91-8ffa-367b8b26bff9':'Workout Bot','9201700e-49c1-4258-875f-f05dd903d1e0':'주식 종목 발굴','994e94af-3a82-43f2-9e40-dd7af2bd6497':'SNS 구독 봇','a93df8d3-7d51-4003-896c-b0bb383d0b73':'노마드 코더 학습','b1e73f00-c05f-4eab-a638-6786d2097b1b':'일상 루틴','b6e1b52e-c873-4159-b9b0-8f9c6ae9f30b':'예약 담당','cd781941-bfb4-417e-90f8-86f95ce8ac2d':'문화생활','e93ed2b9-4bd2-447d-9cdc-1d3aae3fb1c0':'할인정보/구매 봇','e974de70-8823-469d-a3ca-9fb162136d39':'육아봇','ef85bcc5-ebff-47e6-a79c-ba4966b4be62':'일정/루틴 관리','f8187410-d04d-4f45-9677-38859b3dd17d':'가족','e693cb93-3e09-4f82-a815-fb45b210ffc0':'claude academe 학습봇'};
function botLabel(r){var n=BOT_NAMES[r.agent];return '🤖 '+(n||'봇')+' 열기';}
var CAL_COLORS={
 'hyunho':'#55ab30',
 '개인':'#55ab30',
 'Tasks':'#139688',
 'Workout':'#ca6119',
 'Routine':'#785549',
 '가족':'#1e83c4',
 '가족 one':'#f4511e',
 'Runna':'#be4130',
 '대한민국의 휴일':'#616161'
};
function calColorOf(r){
 if(!r||!r.calendar)return '';
 var n=String(r.calendar);
 return CAL_COLORS[n]||'';
}
function calStyleAttr(r){
 var c=calColorOf(r);
 return c?' style="--cal:'+esc(c)+'"':'';
}
function calClass(r){return calColorOf(r)?' cal-ev':'';}
function isExternalHttp(u){
 if(!u)return false;
 var s=String(u);
 if(!/^https?:\/\//i.test(s))return false;
 try{
  var h=new URL(s, location.href).hostname.toLowerCase();
  if(h==='hyunho-feeds.pages.dev'||/\.hyunho-feeds\.pages\.dev$/.test(h))return false;
 }catch(e){return true;}
 return true;
}
function blankAttrs(u){return isExternalHttp(u)?' target="_blank" rel="noopener"':'';}
function markExternalLinks(root){
 var as=(root||document).querySelectorAll('a[href]');
 for(var i=0;i<as.length;i++){
  var a=as[i], href=a.getAttribute('href')||'';
  if(isExternalHttp(href)){a.setAttribute('target','_blank');a.setAttribute('rel','noopener');}
 }
}
function displayTitle(r){
 if(!r)return '';
 // Page-side title mapping (source feed titles unchanged).
 if(r.rid==='nomad-daily')return '노마드 코더 공부';
 var t=String(r.title||'');
 // Vague dated portion titles e.g. "10/30 오늘 분량" → "<channel> · 오늘 분량"
 if(/^\d{1,2}\/\d{1,2}\b/.test(t) && /오늘\s*분량/.test(t)){
  var rest=t.replace(/^\d{1,2}\/\d{1,2}\s*/,'').trim()||'오늘 분량';
  if(r.channel && t.indexOf(String(r.channel))<0)return String(r.channel)+' · '+rest;
  return rest;
 }
 return t;
}
function enrichExt(r){
 if(!r||r.extLabel)return r;
 if(/가계부/.test(String(r.title||''))){
  r.extLabel='🥗 뱅크샐러드';
  r.extScheme=BANKSALAD_SCHEME;
  r.extStore=BANKSALAD_STORE;
 }else if(/가희\s*연락/.test(String(r.title||''))){
  r.extLabel='✈️ 텔레그램';
  r.extScheme=TELEGRAM_SCHEME;
  r.extStore=TELEGRAM_STORE;
 }else if(r.rid==='forge-master-daily'||r.channel==='Forge Master'||/Forge\s*Master/.test(String(r.title||''))){
  // iOS App Store (Lessmore GmbH id6746636289); no public custom URL scheme known.
  r.extLabel='⚔️ Forge Master';
  r.extStore=FORGE_MASTER_STORE;
 }
 return r;
}
function extLinkHtml(r){
 if(!r.extLabel)return '';
 var href=esc(r.extStore||r.extScheme||'#');
 return ' · <a class="ext-app" href="'+href+'"'+(r.extScheme?' data-scheme="'+esc(r.extScheme)+'"':'')+(r.extStore?' data-store="'+esc(r.extStore)+'"':'')+'>'+esc(r.extLabel)+'</a>';
}

function fmtMin(m){var h=Math.floor(m/60),x=m%60;return h?(h+'시간'+(x?' '+x+'분':'')):(x+'분');}
function clock(base,m){if(base==null)return '';var t=(base+m)%1440;return String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0');}
function link(r,t){var tl=t.title.toLowerCase();for(var i=0;i<r.links.length;i++){var l=r.links[i].t.toLowerCase();if(l.indexOf(tl)>=0||tl.indexOf(l)>=0)return r.links[i].u;}return null;}
function card(r,future){
 var sv=startOf(r),own=!!userStart(r.rid),base=null;if(sv){var p=sv.split(':');base=(+p[0])*60+(+p[1]);}
 var st=itemState(r.rid),isDone=st==='done',isSkip=st==='skip';
 var tasks=r.tasks||[];
 // Calendar stub: one synthetic task that only echoes the card title (+ time range).
 var stub=tasks.length===1 && !tasks[0].steps.length && (function(){
  var tt=String(tasks[0].title||''), ti=String(r.title||'');
  return !tt || tt===ti || tt.indexOf(ti+' (')===0 || tt.indexOf(ti+'(')===0;
 })();
 enrichExt(r);
 var h='<div class="card'+(isDone?' done':'')+(isSkip?' skip':'')+calClass(r)+'" id="c-'+esc(r.rid)+'" data-rid="'+esc(r.rid)+'"'+calStyleAttr(r)+'><div class="ch">'+esc(r.channel)+(r.agent?' · <a href="grokbot://app/v1/agent?id='+encodeURIComponent(r.agent)+'">'+esc(botLabel(r))+'</a>':'')+extLinkHtml(r)+'</div>';
 h+='<div class="card-title-row">'+stateBtnHtml(r.rid,st)+'<span class="card-time">'+esc(sv||'--:--')+'</span><h2 class="card-title">'+esc(displayTitle(r))+'</h2></div>';
 // Meta only when not "today" (page header already says today) or when tagged.
 if(r.date!==today||future||r.date<today||r.fixedRoutine){
  h+='<div class="meta">'+label(r.date)+(future?' 예정':' 루틴')+(r.date<today?'<span class="tag">이전 날짜</span>':'')+(future?'<span class="tag">예정</span>':'')+(r.fixedRoutine?'<span class="tag">고정 세부 루틴</span>':'')+'</div>';
 }
 if(r.cal){var c=r.cal,SC={event:'단일 일정',series:'반복 일정 전체',occurrence:'반복 일정 특정 회차'},o=c.original_start_time||{},ot='';
  if(o.date)ot=o.date+' (종일)';else if(o.date_time){var d=new Date(o.date_time);ot=isNaN(d)?o.date_time:new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit'}).format(d)+' KST';}
  h+='<div class="tm cal" title="'+esc(c.calendar_id+' / '+c.event_id)+'">📅 Google 캘린더 연결 · '+(SC[c.scope]||esc(c.scope))+(ot?' · '+esc(ot):'')+'</div>';}
 else if(r.fixedRoutine){h+='<div class="tm cal">고정 세부 루틴 · 캘린더 일정</div>';}
 // No separate "시작 HH:MM" — already in card-time.
 var cum=0,unk=false;
 if(stub){
  var t0=tasks[0];
  if(t0.min){cum=t0.min;h+='<div class="tm">'+(base!=null?esc(sv)+'–'+clock(base,cum)+' · ':'')+fmtMin(cum)+'</div>';}
  else if(!sv){h+='<div class="tm">시작 시간 없음</div>';}
 }else{
  h+='<ol class="tl">';
  tasks.forEach(function(t){
   var u=link(r,t),s0=cum;
   // Skip a task heading that only repeats the card title (keep its steps under a neutral label if any).
   var sameTitle=String(t.title||'')===String(r.title||'');
   var tDone=!!TASKS_DONE[tk(r.rid,r.date,t.id)];
   var cbAttrs=' type="checkbox" class="task-cb" aria-label="작업 완료" data-rid="'+esc(r.rid)+'" data-date="'+esc(r.date)+'" data-tid="'+esc(t.id)+'"'+(tDone?' checked':'');
   h+='<li data-tid="'+esc(t.id)+'">';
   if(!sameTitle){h+='<div class="tt'+(tDone?' done-task':'')+'"><input'+cbAttrs+'/><span class="tt-text">'+(u?'<a href="'+esc(u)+'"'+blankAttrs(u)+'>'+esc(t.title)+'</a>':esc(t.title))+'</span></div>';}
   else{h+='<div class="tt'+(tDone?' done-task':'')+'"><input'+cbAttrs+'/>'+(u?'<span class="tt-text"><a href="'+esc(u)+'">열기</a></span>':'<span class="tt-text">작업</span>')+'</div>';}
   h+='<div class="tm">';
   if(t.min){cum+=t.min;h+=(base!=null&&!unk?clock(base,s0)+'–'+clock(base,cum)+' · ':'')+fmtMin(t.min);}else{unk=true;h+='소요 시간 미지정';}
   h+='</div>';
   if(t.steps&&t.steps.length){h+='<ul class="st">';t.steps.forEach(function(x){h+='<li>'+esc(x.title)+'</li>';});h+='</ul>';}
   h+='</li>';
  });
  h+='</ol>';
  if(tasks.length){h+='<div class="tot">총 '+fmtMin(cum)+(unk?' + 미지정':'')+(base!=null?' · ~'+clock(base,cum)+' 종료':'')+'</div>';}
 }
 h+=subtasksHtml(r.rid);
 h+='</div>';
 return h;
}
function loadCollapsed(){try{return JSON.parse(localStorage.getItem(COL_KEY)||'{}')||{};}catch(e){return {};}}
function saveCollapsed(m){try{localStorage.setItem(COL_KEY,JSON.stringify(m));}catch(e){}}
function outlineBlock(r,future){
 enrichExt(r);
 var sv=startOf(r),base=null;if(sv){var p=sv.split(':');base=(+p[0])*60+(+p[1]);}
 var col=loadCollapsed(),cid='c-'+r.rid,isCol=!!col[cid];
 var kids='',cum=0,unk=false,hasKids=false;
 (r.tasks||[]).forEach(function(t,ti){
  var u=link(r,t),s0=cum,tm='';
  if(t.min){cum+=t.min;tm=(base!=null&&!unk?clock(base,s0)+'–'+clock(base,cum)+' · ':'')+fmtMin(t.min);}else{unk=true;}
  var steps='',hasSteps=t.steps&&t.steps.length;
  var tDone=!!TASKS_DONE[tk(r.rid,r.date,t.id)];
  if(hasSteps){t.steps.forEach(function(x){steps+='<li class="ol-b"><div class="ol-row"><button type="button" class="ol-tog leaf" tabindex="-1" aria-hidden="true">▼</button><span class="ol-dot"></span><span class="ol-title">'+esc(x.title)+'</span></div></li>';});}
  hasKids=true;
  var tid=cid+'-t'+ti,tCol=!!col[tid];
  var tStart=(base!=null&&t.min&&!unk)?clock(base,s0):'';
  kids+='<li class="ol-b'+(tCol?' collapsed':'')+'" data-oid="'+esc(tid)+'"><div class="ol-row"><button type="button" class="ol-tog'+(hasSteps?'':' leaf')+'" aria-expanded="'+(hasSteps&&!tCol?'true':'false')+'">▼</button><input type="checkbox" class="task-cb" aria-label="작업 완료" data-rid="'+esc(r.rid)+'" data-date="'+esc(r.date)+'" data-tid="'+esc(t.id)+'"'+(tDone?' checked':'')+'/>'
   +(tStart?'<span class="ol-time">'+esc(tStart)+'</span>':'')
   +'<span class="ol-title'+(tDone?' done-task':'')+'">'+(u?'<a href="'+esc(u)+'"'+blankAttrs(u)+'>'+esc(t.title)+'</a>':esc(t.title))+(tm?' <span class="tm">'+esc(tm)+'</span>':'')+'</span></div>'
   +(hasSteps?'<ul class="ol-kids">'+steps+'</ul>':'')+'</li>';
 });
 var tags=(r.date<today?'<span class="tag">이전 날짜</span>':'')+(future?'<span class="tag">예정</span>':'');
 var st=itemState(r.rid);
 return '<li class="ol-b'+(isCol?' collapsed':'')+(st==='done'?' done':'')+(st==='skip'?' skip':'')+calClass(r)+'" id="'+esc(cid)+'" data-oid="'+esc(cid)+'"'+calStyleAttr(r)+'><div class="ol-row"><button type="button" class="ol-tog'+(hasKids?'':' leaf')+'" aria-expanded="'+(hasKids&&!isCol?'true':'false')+'">▼</button>'+stateBtnHtml(r.rid,st)+'<span class="ol-dot"></span>'
  +'<span class="ol-time">'+esc(sv||'--:--')+'</span><span class="ol-title">'+esc(displayTitle(r))+tags+'</span><span class="ol-ch">'+esc(r.channel)+(r.agent?' · <a href="grokbot://app/v1/agent?id='+encodeURIComponent(r.agent)+'">'+esc(botLabel(r))+'</a>':'')+extLinkHtml(r)+'</span></div>'
  +(hasKids?'<ul class="ol-kids">'+kids+'</ul>':'')+subtasksHtml(r.rid)+'</li>';
}
function outline(items){
 return items.length?'<ul class="ol">'+items.map(function(o){return outlineBlock(o.r,o.f);}).join('')+'</ul>':'<div class="empty">오늘 루틴이 없습니다.</div>';
}
function eff(r){return userStart(r.rid)||r.start||'';}
function byTime(x,y){return (eff(x)||'99:99').localeCompare(eff(y)||'99:99')||x.channel.localeCompare(y.channel)||x.title.localeCompare(y.title);}
function getView(){try{var v=localStorage.getItem(VIEW_KEY);return v==='outline'?'outline':'card';}catch(e){return 'card';}}
function setView(v){try{localStorage.setItem(VIEW_KEY,v);}catch(e){} updateToggle(v);}
function updateToggle(v){document.querySelectorAll('#vtog button').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-v')===v);});}
function collect(){
 var a=Object.keys(best).map(function(k){return best[k];}).map(function(r){return {r:r,f:false};});
 Object.keys(next).filter(function(k){return !best[k];}).forEach(function(k){a.push({r:next[k],f:true});});
 // Drop a calendar event only when a routine card at the SAME start time already covers it
 // (first-word title/channel match). Same-name events at other times (e.g. Forge Master 21:00
 // vs morning Forge card) must still show.
 CAL.forEach(function(r){
  var w=(r.title.split(/[:(（]/)[0].trim().split(/\s+/)[0]||'').toLowerCase();
  var st=r.start||'';
  var dup=w.length>=2&&!!st&&a.some(function(o){
   if(o.f)return false;
   var ost=eff(o.r)||'';
   if(!ost||ost!==st)return false;
   var hay=(o.r.channel+' '+o.r.title).toLowerCase().replace(/\s+/g,'');
   return hay.indexOf(w)>=0;
  });
  if(dup)return;
  a.push({r:r,f:false});
 });
 a.sort(function(x,y){return byTime(x.r,y.r)||String(x.r.date||'').localeCompare(String(y.r.date||''));});
 return a;
}
function render(){
 var a=collect(),v=getView(),list=document.getElementById('list');
 updateToggle(v);
 list.classList.toggle('ol-mode',v==='outline');
 if(v==='outline'){list.innerHTML=outline(a);}
 else{list.innerHTML=a.length?a.map(function(o){return card(o.r,o.f);}).join(''):'<div class="empty">오늘 루틴이 없습니다.</div>';}
 document.getElementById('up').innerHTML='';
 markExternalLinks(list);
 fillToc(a);
 updateProg(a);
}
function kstNowHM(){
 var f=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
 var parts=f.formatToParts(new Date()),o={};parts.forEach(function(p){o[p.type]=p.value;});
 return (o.hour||'00')+':'+(o.minute||'00');
}
function tocHighlight(a){
 var now=kstNowHM(),cur=null,nxt=null;
 for(var i=0;i<a.length;i++){
  var t=eff(a[i].r)||'';
  if(!t)continue;
  if(t<=now)cur=a[i].r.rid;
  if(t>now&&nxt==null)nxt=a[i].r.rid;
 }
 return {cur:cur,nxt:nxt};
}
function tocLinksHtml(a){
 if(!a.length)return '<div class="empty" style="padding:1rem 0">오늘 루틴이 없습니다.</div>';
 var hl=tocHighlight(a);
 return a.map(function(o){
  var r=o.r,cls=[];
  var st=itemState(r.rid);if(st==='done')cls.push('toc-done');else if(st==='skip')cls.push('toc-skip');
  if(hl.cur&&r.rid===hl.cur)cls.push('toc-now');
  else if(hl.nxt&&r.rid===hl.nxt)cls.push('toc-next');
  if(calColorOf(r))cls.push('cal-ev');
  return '<a href="#c-'+esc(r.rid)+'" data-rid="'+esc(r.rid)+'"'+(cls.length?' class="'+cls.join(' ')+'"':'')+calStyleAttr(r)+'><span class="t">'+esc(r.start||'--:--')+'</span><span>'+esc(displayTitle(r))+'</span></a>';
 }).join('');
}
function fillToc(a){
 document.getElementById('toc').innerHTML=a.length?'<h3>루틴 / 일정</h3>'+tocLinksHtml(a):'';
 var sheetList=document.getElementById('toc-sheet-list');
 if(sheetList)sheetList.innerHTML=tocLinksHtml(a);
 var fab=document.getElementById('toc-fab');
 if(fab)fab.style.display=a.length?'':'none';
}
function openTocSheet(){
 var sheet=document.getElementById('toc-sheet'),bd=document.getElementById('toc-backdrop'),fab=document.getElementById('toc-fab');
 sheet.hidden=false;bd.hidden=false;
 requestAnimationFrame(function(){document.body.classList.add('toc-open');});
 if(fab)fab.setAttribute('aria-expanded','true');
 var nowEl=sheet.querySelector('a.toc-now')||sheet.querySelector('a.toc-next');
 if(nowEl)nowEl.scrollIntoView({block:'nearest'});
}
function closeTocSheet(){
 document.body.classList.remove('toc-open');
 var fab=document.getElementById('toc-fab');
 if(fab)fab.setAttribute('aria-expanded','false');
 var sheet=document.getElementById('toc-sheet');
 setTimeout(function(){if(!document.body.classList.contains('toc-open')){sheet.hidden=true;document.getElementById('toc-backdrop').hidden=true;}},260);
}
(function bindTocSheet(){
 var fab=document.getElementById('toc-fab'),bd=document.getElementById('toc-backdrop'),sheet=document.getElementById('toc-sheet'),closeBtn=document.getElementById('toc-close');
 if(!fab||!sheet)return;
 fab.addEventListener('click',function(){if(document.body.classList.contains('toc-open'))closeTocSheet();else openTocSheet();});
 bd.addEventListener('click',closeTocSheet);
 closeBtn.addEventListener('click',closeTocSheet);
 document.getElementById('toc-sheet-list').addEventListener('click',function(e){
  var a=e.target.closest('a[href^="#c-"]');if(!a)return;
  e.preventDefault();
  var id=a.getAttribute('href').slice(1),el=document.getElementById(id);
  closeTocSheet();
  if(el)setTimeout(function(){el.scrollIntoView({behavior:'smooth',block:'start'});},50);
 });
 // swipe down to close
 var startY=null,dragging=false;
 function onStart(ev){var t=ev.touches?ev.touches[0]:ev;startY=t.clientY;dragging=true;}
 function onMove(ev){
  if(!dragging||startY==null)return;
  var t=ev.touches?ev.touches[0]:ev,dy=t.clientY-startY;
  if(dy>0){sheet.style.transform='translateY('+dy+'px)';sheet.style.transition='none';if(ev.cancelable)ev.preventDefault();}
 }
 function onEnd(ev){
  if(!dragging)return;dragging=false;
  var t=(ev.changedTouches&&ev.changedTouches[0])||ev,dy=(t.clientY||0)-startY;startY=null;
  sheet.style.transition='';sheet.style.transform='';
  if(dy>72)closeTocSheet();
 }
 var handle=sheet.querySelector('.toc-handle');
 ['touchstart','mousedown'].forEach(function(ev){handle.addEventListener(ev,onStart,{passive:true});});
 ['touchmove','mousemove'].forEach(function(ev){sheet.addEventListener(ev,onMove,{passive:false});});
 ['touchend','mouseup','mouseleave'].forEach(function(ev){sheet.addEventListener(ev,onEnd);});
 document.addEventListener('keydown',function(e){if(e.key==='Escape'&&document.body.classList.contains('toc-open'))closeTocSheet();});
})();
document.getElementById('vtog').addEventListener('click',function(e){
 var b=e.target.closest('button[data-v]');if(!b)return;
 setView(b.getAttribute('data-v'));render();
});
document.getElementById('list').addEventListener('click',function(e){
 var t=e.target.closest('.ol-tog');if(!t||t.classList.contains('leaf'))return;
 e.preventDefault();
 var li=t.closest('.ol-b');if(!li)return;
 var oid=li.getAttribute('data-oid');if(!oid)return;
 li.classList.toggle('collapsed');
 var open=!li.classList.contains('collapsed');
 t.setAttribute('aria-expanded',open?'true':'false');
 var m=loadCollapsed();if(open)delete m[oid];else m[oid]=1;saveCollapsed(m);
});

var SUBTASKS={}; // card_id -> [{id,title,done}]
var SUB_OPEN={};
var SUB_OPEN_KEY='today-subtasks-open';
function loadSubOpen(){try{SUB_OPEN=JSON.parse(localStorage.getItem(SUB_OPEN_KEY)||'{}')||{};}catch(e){SUB_OPEN={};}}
function saveSubOpen(){try{localStorage.setItem(SUB_OPEN_KEY,JSON.stringify(SUB_OPEN));}catch(e){}}
function subtasksOf(rid){return SUBTASKS[rid]||[];}
function newSubId(){try{if(crypto&&crypto.randomUUID)return crypto.randomUUID().replace(/-/g,'').slice(0,16);}catch(e){}return Date.now().toString(36)+Math.random().toString(36).slice(2,8);}
function subtasksHtml(rid){
 var list=subtasksOf(rid),nDone=0;list.forEach(function(x){if(x.done)nDone++;});
 var open=!!SUB_OPEN[rid];
 var label=list.length?('세부 작업 '+nDone+'/'+list.length):(open?'세부 작업':'세부 작업 추가');
 var h='<div class="subtasks" data-card="'+esc(rid)+'">';
 h+='<button type="button" class="st-tog" aria-expanded="'+(open?'true':'false')+'">'+esc(label)+(open?' ▾':' ▸')+'</button>';
 h+='<div class="st-body"'+(open?'':' hidden')+'>';
 h+='<ul class="st-list">';
 list.forEach(function(x){
  h+='<li class="'+(x.done?'done':'')+'" data-sid="'+esc(x.id)+'"><label><input type="checkbox" class="st-cb"'+(x.done?' checked':'')+'/> <span>'+esc(x.title)+'</span></label>';
  h+='<button type="button" class="st-del" aria-label="삭제">×</button></li>';
 });
 h+='</ul>';
 h+='<form class="st-add" action="#"><input type="text" maxlength="200" placeholder="세부 작업 입력" aria-label="세부 작업 입력" autocomplete="off"/><button type="submit">추가</button></form>';
 h+='</div></div>';
 return h;
}
function loadSubtasks(){
 return fetch('api/subtasks?date='+encodeURIComponent(today)+'&_='+Date.now(),{cache:'no-store'}).then(function(r){return r.ok?r.json():null;}).then(function(j){
  SUBTASKS={};
  if(!j||!Array.isArray(j.items))return;
  j.items.forEach(function(it){
   if(!it||!it.card_id||!it.id)return;
   if(!SUBTASKS[it.card_id])SUBTASKS[it.card_id]=[];
   SUBTASKS[it.card_id].push({id:String(it.id),title:String(it.title||''),done:!!it.done});
  });
 }).catch(function(){});
}
function addSubtask(rid,title){
 title=String(title||'').trim();if(!title||!rid)return Promise.resolve();
 var id=newSubId();
 if(!SUBTASKS[rid])SUBTASKS[rid]=[];
 SUBTASKS[rid].push({id:id,title:title,done:false});
 SUB_OPEN[rid]=1;saveSubOpen();
 render();
 return fetch('api/subtasks',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({date:today,card_id:rid,title:title,new_id:id})}).then(function(r){
  if(!r.ok)throw new Error('http '+r.status);
  return r.json();
 }).then(function(j){
  if(j&&j.id&&j.id!==id){
   var list=SUBTASKS[rid]||[];
   for(var i=0;i<list.length;i++){if(list[i].id===id){list[i].id=j.id;break;}}
   render();
  }
 }).catch(function(){
  SUBTASKS[rid]=(SUBTASKS[rid]||[]).filter(function(x){return x.id!==id;});
  render();
  alert('세부 작업 저장에 실패했습니다. 잠시 후 다시 시도하세요.');
 });
}
function setSubtaskDone(rid,sid,done){
 var list=SUBTASKS[rid]||[],prev=null;
 for(var i=0;i<list.length;i++){if(list[i].id===sid){prev=!!list[i].done;list[i].done=!!done;break;}}
 render();
 return fetch('api/subtasks',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({date:today,card_id:rid,id:sid,done:!!done})}).then(function(r){
  if(!r.ok)throw new Error('http '+r.status);
 }).catch(function(){
  for(var i=0;i<list.length;i++){if(list[i].id===sid&&prev!==null)list[i].done=prev;}
  render();
  alert('세부 작업 상태 저장에 실패했습니다.');
 });
}
function deleteSubtask(rid,sid){
 var list=SUBTASKS[rid]||[],kept=[],removed=null;
 list.forEach(function(x){if(x.id===sid)removed=x;else kept.push(x);});
 SUBTASKS[rid]=kept;render();
 return fetch('api/subtasks',{method:'DELETE',cache:'no-store',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({date:today,card_id:rid,id:sid})}).then(function(r){
  if(!r.ok)throw new Error('http '+r.status);
 }).catch(function(){
  if(removed){kept.push(removed);SUBTASKS[rid]=kept;}
  render();
  alert('세부 작업 삭제에 실패했습니다.');
 });
}

var CAL=[];
var DONE={}; // rid -> 'done' | 'skip'
function itemState(rid){return DONE[rid]||'open';}
function stateBtnHtml(rid,st){
 st=st||'open';
 var label=st==='done'?'완료':(st==='skip'?'스킵':'미완료');
 var ac=st==='done'?'true':(st==='skip'?'mixed':'false');
 return '<button type="button" class="done-cb" data-rid="'+esc(rid)+'" data-state="'+st+'" aria-label="상태: '+label+' (탭하여 변경)" aria-checked="'+ac+'" title="미완료 → 완료 → 스킵"></button>';
}
function nextState(st){return st==='open'?'done':(st==='done'?'skip':'open');}
function applyStateDom(rid,st){
 var nodes=document.querySelectorAll('[data-rid="'+CSS.escape(rid)+'"], #c-'+CSS.escape(rid)+', [data-oid="c-'+CSS.escape(rid)+'"]');
 // update buttons
 document.querySelectorAll('.done-cb[data-rid="'+CSS.escape(rid)+'"]').forEach(function(btn){
  btn.setAttribute('data-state',st);
  btn.setAttribute('aria-checked',st==='done'?'true':(st==='skip'?'mixed':'false'));
  btn.setAttribute('aria-label','상태: '+(st==='done'?'완료':(st==='skip'?'스킵':'미완료'))+' (탭하여 변경)');
 });
 var card=document.getElementById('c-'+rid);
 if(card){card.classList.toggle('done',st==='done');card.classList.toggle('skip',st==='skip');}
 var ol=document.querySelector('.ol-b[data-oid="c-'+CSS.escape(rid)+'"]');
 if(ol){ol.classList.toggle('done',st==='done');ol.classList.toggle('skip',st==='skip');}
}
function updateProg(a){
 var el=document.getElementById('prog');if(!el)return;
 var nDone=0,nSkip=0,nOpen=0;
 (a||collect()).forEach(function(o){
  if(o.f)return;
  var s=itemState(o.r.rid);
  if(s==='done')nDone++; else if(s==='skip')nSkip++; else nOpen++;
 });
 el.textContent='완료 '+nDone+' · 스킵 '+nSkip+' · 남음 '+nOpen;
}
function loadDone(){
 return fetch('api/done?date='+encodeURIComponent(today)+'&_='+Date.now(),{cache:'no-store'}).then(function(r){return r.ok?r.json():null;}).then(function(j){
  DONE={};
  if(j&&Array.isArray(j.done))j.done.forEach(function(id){DONE[id]='done';});
  if(j&&Array.isArray(j.skip))j.skip.forEach(function(id){DONE[id]='skip';});
 }).catch(function(){});
}
function setState(rid,state){
 var prev=itemState(rid);
 DONE[rid]=state==='open'?undefined:state;
 if(state==='open')delete DONE[rid];
 applyStateDom(rid,state);
 updateProg();
 fetch('api/done',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({date:today,item_id:rid,state:state})}).then(function(r){
  if(!r.ok)throw new Error('http '+r.status);
  return r.json();
 }).catch(function(){
  if(prev==='open')delete DONE[rid]; else DONE[rid]=prev;
  applyStateDom(rid,prev);
  updateProg();
  alert('완료 상태 저장에 실패했습니다. 잠시 후 다시 시도하세요.');
 });
}
function cycleState(rid){setState(rid,nextState(itemState(rid)));}
document.getElementById('list').addEventListener('click',function(e){
 var cb=e.target.closest('.done-cb');
 if(cb){
  e.preventDefault();
  var rid=cb.getAttribute('data-rid');if(!rid)return;
  cycleState(rid);
  return;
 }
 var tog=e.target.closest('.st-tog');
 if(tog){
  e.preventDefault();
  var box=tog.closest('.subtasks');if(!box)return;
  var rid=box.getAttribute('data-card');if(!rid)return;
  if(SUB_OPEN[rid])delete SUB_OPEN[rid]; else SUB_OPEN[rid]=1;
  saveSubOpen();
  render();
  return;
 }
 var del=e.target.closest('.st-del');
 if(del){
  e.preventDefault();
  var li=del.closest('li[data-sid]');var box=del.closest('.subtasks');
  if(!li||!box)return;
  var rid=box.getAttribute('data-card'),sid=li.getAttribute('data-sid');
  if(!rid||!sid)return;
  deleteSubtask(rid,sid);
  return;
 }
});
document.getElementById('list').addEventListener('submit',function(e){
 var form=e.target.closest('form.st-add');if(!form)return;
 e.preventDefault();
 var box=form.closest('.subtasks');if(!box)return;
 var rid=box.getAttribute('data-card');if(!rid)return;
 var inp=form.querySelector('input');if(!inp)return;
 var title=inp.value;inp.value='';
 addSubtask(rid,title);
});
document.getElementById('list').addEventListener('change',function(e){
 var tcb=e.target.closest('.task-cb');
 if(tcb){
  var rid=tcb.getAttribute('data-rid'),date=tcb.getAttribute('data-date'),tid=tcb.getAttribute('data-tid');
  if(!rid||!date||!tid)return;
  setTaskDone(rid,date,tid,tcb.checked);
  render();
  return;
 }
 var scb=e.target.closest('.st-cb');
 if(scb){
  var li=scb.closest('li[data-sid]');var box=scb.closest('.subtasks');
  if(!li||!box)return;
  var rid=box.getAttribute('data-card'),sid=li.getAttribute('data-sid');
  if(!rid||!sid)return;
  setSubtaskDone(rid,sid,scb.checked);
 }
});
function openExtApp(a){
 var scheme=a.getAttribute('data-scheme'), store=a.getAttribute('data-store')||a.getAttribute('href');
 if(!scheme){if(store)location.href=store;return;}
 var gone=false;
 function mark(){gone=true;cleanup();}
 function cleanup(){
  document.removeEventListener('visibilitychange',onVis);
  window.removeEventListener('pagehide',mark);
 }
 function onVis(){if(document.hidden)mark();}
 document.addEventListener('visibilitychange',onVis);
 window.addEventListener('pagehide',mark);
 try{location.href=scheme;}catch(err){}
 setTimeout(function(){cleanup();if(!gone&&store)location.href=store;},1250);
}
document.addEventListener('click',function(e){
 var a=e.target.closest('a.ext-app');if(!a)return;
 if(!a.getAttribute('data-scheme'))return;
 e.preventDefault();
 openExtApp(a);
});

loadTasksDone();
loadSubOpen();
function calRid(e){
 var raw=String(e&&e.id||'');
 if(!raw) raw=String((e&&e.calendar)||'')+'|'+(e&&e.start||'')+'|'+(e&&e.title||'');
 if(!/^[\w.:@+\-]{1,190}$/.test(raw)){
  raw=raw.replace(/[^\w.:@+\-]+/g,'_').replace(/^_+|_+$/g,'').slice(0,190);
  if(!raw) raw='x';
 }
 return 'cal-'+raw;
}
function loadCal(){
 return fetch('api/today-cal?date='+encodeURIComponent(today)+'&_='+Date.now(),{cache:'no-store'}).then(function(r){return r.ok?r.json():null;}).then(function(j){
  if(!j||!j.events){CAL=[];return;}
  CAL=j.events.map(function(e){var span=e.allDay?'종일':(e.start+'–'+e.end);var o={rid:calRid(e),date:today,start:e.allDay?'종일':e.start,title:e.title,channel:'캘린더 · '+e.calendar,calendar:e.calendar,links:[],pub:'',
   tasks:[{id:'t',title:e.title+' ('+span+')',min:e.duration||null,steps:[]}],
   eventId:e.eventId||''};
   if(e.calendar==='Workout'){o.agent='804ba387-766e-4d91-8ffa-367b8b26bff9';}
   return applyFixedRoutine(o);});
 }).catch(function(){CAL=[];});
}
var refreshing=false;
function refreshToday(opts){
 opts=opts||{};
 if(refreshing)return;
 // Day rolled over → full reload so embedded routine JSON updates too.
 if(kstToday()!==today){location.reload();return;}
 refreshing=true;
 var btn=document.getElementById('refresh-btn');
 if(btn)btn.classList.add('busy');
 return Promise.all([loadDone(),loadCal(),loadSubtasks()]).then(function(){
  render();
 }).catch(function(){
  if(!opts.silent)alert('새로고침에 실패했습니다. 다시 시도해 주세요.');
 }).then(function(){
  refreshing=false;
  if(btn)btn.classList.remove('busy');
 });
}
render();
refreshToday({silent:true});
var refreshBtn=document.getElementById('refresh-btn');
 if(refreshBtn)refreshBtn.addEventListener('click',function(){refreshToday();});
document.addEventListener('visibilitychange',function(){
 if(document.hidden)return;
 if(kstToday()!==today){location.reload();return;}
 refreshToday({silent:true});
});
})();
</script></body></html>
"""



def today_page(routines):
    data = json.dumps(routines, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return TODAY_HTML.replace("__DATA__", data)



def sync_secret_functions():
    """Refresh live functions/<secret>/ from tracked functions/_secret_/ (no-op if missing)."""
    script = os.path.join(BASE, "scripts", "sync-secret-functions.sh")
    if not os.path.isfile(script):
        return
    subprocess.run(["bash", script], check=False)

def build():
    sync_secret_functions()
    sec = secret()
    chans = load_channels()
    items = by_date(load_items())[:MAX_ITEMS]
    vfoot = agent_footer(chans["channels"]["valley"])
    full = [dict(title=it["title"], guid=it["id"], pubDate=it["pubDate"], html=it["html"] + vfoot,
                 link=it.get("link"), md=raw_md_for(it))
            for it in items]
    routines = []
    for it in load_items():
        routines += routine_entries(it, VALLEY["name"], chans["channels"]["valley"].get("agent_id"))
    for _slug in bot_slugs(chans):
        _m = chans["channels"][_slug]
        for it in load_chan_items(_slug):
            routines += routine_entries(it, _m["name"], _m.get("agent_id"))
    stage = os.path.join(BASE, ".public-build")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    d = os.path.join(stage, sec)
    os.makedirs(d)
    burl = "https://%s.pages.dev/%s/" % (project(), sec)
    full = with_pages(full, d, burl)
    open(os.path.join(d, "valley.xml"), "w", encoding="utf-8").write(
        rss_doc(VALLEY["name"], VALLEY["description"], full))
    valley_feeds = [("valley.xml", VALLEY["name"], "https://www.valley.town/")]
    unified = list(full)
    counts = {}
    for ch, name, desc in CHANNELS:
        entries = []
        for it in items:
            cmd = (it.get("channels") or {}).get(ch)
            if not cmd or not any(BULLET.match(l) for l in cmd.split("\n")):  # skip channel w/o bullets
                continue
            entries.append(dict(title="%s · %s" % (it["title"], name), guid="%s#%s" % (it["id"], ch),
                                pubDate=it["pubDate"], html=md_to_html(cmd) + vfoot, link=it.get("link")))
        counts[ch] = len(entries)
        if WRITE_TOPIC_FEEDS:
            title = "Valley AI · %s" % name
            open(os.path.join(d, ch + ".xml"), "w", encoding="utf-8").write(rss_doc(title, desc, entries))
            valley_feeds.append((ch + ".xml", title, "https://www.valley.town/"))
    burl = "https://%s.pages.dev/%s/" % (project(), sec)
    bot_feeds = []
    for slug in sorted(bot_slugs(chans)):
        m = chans["channels"][slug]
        citems = by_date(load_chan_items(slug))[:MAX_ITEMS]
        foot = agent_footer(m)
        entries = [dict(title=it["title"], guid=it["id"], pubDate=it["pubDate"], html=it["html"] + foot,
                        link=it.get("link"), md=raw_md_for(it)) for it in citems]
        entries = with_pages(entries, d, burl)
        unified += [dict(e, title="[%s] %s" % (m["name"], e["title"])) for e in entries]
        fname = slug + ".xml"
        link = m.get("link") or burl + fname
        open(os.path.join(d, fname), "w", encoding="utf-8").write(
            rss_doc(m["name"], m.get("description") or m["name"], entries, link=link))
        counts[slug] = len(entries)
        bot_feeds.append((fname, m["name"], link))
    unified = sorted(unified, key=lambda e: datetime.fromisoformat(e["pubDate"]), reverse=True)[:UNIFIED_MAX]
    # Channel home for Reeder/etc.: private index under /<secret>/ (not valley.town)
    index_url = burl  # https://...pages.dev/<secret>/
    open(os.path.join(d, "feed.xml"), "w", encoding="utf-8").write(
        rss_doc(UNIFIED_TITLE, UNIFIED_DESC, unified, link=index_url))
    counts["feed(unified)"] = len(unified)
    unified_feeds = [("feed.xml", UNIFIED_TITLE, index_url)]
    groups = [(None, unified_feeds), ("Valley AI", valley_feeds), ("봇 채널", bot_feeds)]
    open(os.path.join(d, "channels.opml"), "w", encoding="utf-8").write(opml_doc(burl, groups))
    open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(private_index(groups))
    open(os.path.join(d, "today.html"), "w", encoding="utf-8").write(today_page(routines))
    # Calendar overlays for today-cal (Runna / 가족 / …) — same-origin ICS under /<secret>/cal/
    cal_src = os.path.join(BASE, "cal-overlays")
    if os.path.isdir(cal_src):
        cal_dst = os.path.join(d, "cal")
        os.makedirs(cal_dst, exist_ok=True)
        for fn in os.listdir(cal_src):
            if fn.endswith(".ics"):
                shutil.copyfile(os.path.join(cal_src, fn), os.path.join(cal_dst, fn))
    # SNS timeline page (client-side, reads sns.xml from same origin -> auto-updates on every add)
    if os.path.exists(os.path.join(DOCS_DIR, "sns.html")):
        shutil.copyfile(os.path.join(DOCS_DIR, "sns.html"), os.path.join(d, "sns.html"))
    blank = ('<!doctype html><html><head><meta charset="utf-8">'
             '<meta name="robots" content="noindex, nofollow"><title></title></head><body></body></html>\n')
    open(os.path.join(stage, "index.html"), "w", encoding="utf-8").write(blank)
    # Cloudflare Pages serves 404.html with HTTP 404 for missing assets (old topic/valley-study URLs).
    open(os.path.join(stage, "404.html"), "w", encoding="utf-8").write(blank)
    open(os.path.join(stage, "robots.txt"), "w").write("User-agent: *\nDisallow: /\n")
    # public inbox API docs (no token / no feed secret inside) -> /api/inbox/{openapi.json,docs.md,llms.txt}
    ddir = os.path.join(stage, "api", "inbox")
    os.makedirs(ddir)
    for src, dst in (("openapi.json", "openapi.json"), ("docs.md", "docs.md"), ("docs.md", "llms.txt")):
        shutil.copyfile(os.path.join(DOCS_DIR, src), os.path.join(ddir, dst))
    hdr = ["/*", "  X-Robots-Tag: noindex, nofollow", ""]
    for fname, _, _ in unified_feeds + valley_feeds + bot_feeds:
        hdr += ["/%s/%s" % (sec, fname), "  Content-Type: application/rss+xml; charset=utf-8",
                "  Cache-Control: public, max-age=300", ""]
    hdr += ["/%s/channels.opml" % sec, "  Content-Type: text/x-opml; charset=utf-8",
            "  Cache-Control: public, max-age=300", ""]
    for _p in ("today", "today.html", "sns", "sns.html"):
        hdr += ["/%s/%s" % (sec, _p), "  Cache-Control: no-cache", ""]
    hdr += ["/%s/cal/*" % sec, "  Content-Type: text/calendar; charset=utf-8",
            "  Cache-Control: public, max-age=60", ""]
    hdr += ["/%s/*" % sec, "  Referrer-Policy: no-referrer", ""]
    for f, ct in (("openapi.json", "application/json"), ("docs.md", "text/markdown"), ("llms.txt", "text/plain")):
        hdr += ["/api/inbox/" + f, "  Content-Type: %s; charset=utf-8" % ct, "  Access-Control-Allow-Origin: *",
                "  Cache-Control: public, max-age=300", ""]
    open(os.path.join(stage, "_headers"), "w").write("\n".join(hdr))
    old = PUBLIC + ".old"
    if os.path.isdir(old):
        shutil.rmtree(old)
    if os.path.isdir(PUBLIC):
        os.rename(PUBLIC, old)
    os.rename(stage, PUBLIC)
    if os.path.isdir(old):
        shutil.rmtree(old)
    print("Built %d valley items -> public/<secret>/valley.xml; feeds: %s; + channels.opml"
          % (len(items), ", ".join("%s=%d" % kv for kv in counts.items())))
    return sec


def account_id():
    v = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if v:
        return v
    p = os.path.join(BASE, "account_id.txt")
    if os.path.exists(p) and open(p).read().strip():
        return open(p).read().strip()
    tok = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not tok:
        sys.exit("ERROR: CLOUDFLARE_API_TOKEN not set")
    req = urllib.request.Request("https://api.cloudflare.com/client/v4/accounts",
                                 headers={"Authorization": "Bearer " + tok})
    d = json.load(urllib.request.urlopen(req))
    res = d.get("result") or []
    if not d.get("success") or not res:
        sys.exit("ERROR: GET /accounts returned no accounts (success=%s, errors=%s). "
                 "Set CLOUDFLARE_ACCOUNT_ID or write it to account_id.txt." % (d.get("success"), d.get("errors")))
    aid = res[0]["id"]
    open(p, "w").write(aid + "\n")
    return aid


def wrangler(args, env, check=True):
    local = os.path.join(BASE, "node_modules", ".bin", "wrangler")
    cmd = ([local] if os.path.exists(local) else ["npx", "-y", WRANGLER]) + ["pages"] + args
    r = subprocess.run(cmd, env=env, cwd=BASE, capture_output=True, text=True)
    out = (r.stdout + r.stderr)
    tok = env.get("CLOUDFLARE_API_TOKEN", "")
    if tok:
        out = out.replace(tok, "***")
    if check and r.returncode != 0:
        print(out); sys.exit("ERROR: wrangler %s failed (exit %d)" % (" ".join(args[:2]), r.returncode))
    return r.returncode, out


def deploy(sec):
    sync_secret_functions()
    cal_fn = os.path.join(BASE, "functions", sec, "api", "today-cal.js")
    if not os.path.isfile(cal_fn):
        sys.exit("ERROR: missing %s — run scripts/sync-secret-functions.sh before deploy" % cal_fn)
    name = project()
    env = dict(os.environ, CLOUDFLARE_ACCOUNT_ID=account_id(), WRANGLER_SEND_METRICS="false")
    rc, out = wrangler(["project", "list", "--json"], env, check=False)
    if rc != 0 or name not in out:
        rc2, out2 = wrangler(["project", "create", name, "--production-branch", "main"], env, check=False)
        if rc2 != 0 and "already exists" not in out2:
            print(out2); sys.exit("ERROR: project create failed")
        if rc2 == 0:
            print("Created Pages project", name)
    rc, out = wrangler(["deploy", PUBLIC, "--project-name", name, "--branch", "main", "--commit-dirty=true"], env)
    print("\n".join(l for l in out.splitlines() if "pages.dev" in l or "Success" in l or "Uploaded" in l))
    url = "https://%s.pages.dev/%s/feed.xml" % (name, sec)
    print("UNIFIED FEED URL:", url)
    # Smoke: today-cal must be reachable or calendar cards (일과 시작 FIXED_ROUTINES) stay empty.
    smoke = "https://%s.pages.dev/%s/api/today-cal" % (name, sec)
    try:
        req = urllib.request.Request(smoke, headers={"User-Agent": "hyunho-feeds-deploy-smoke"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            if resp.status != 200:
                sys.exit("ERROR: deploy smoke today-cal HTTP %s" % resp.status)
            data = json.loads(body.decode("utf-8"))
            if "events" not in data:
                sys.exit("ERROR: deploy smoke today-cal missing events key")
            print("SMOKE OK: today-cal (%d events)" % len(data.get("events") or []))
    except Exception as e:
        sys.exit("ERROR: deploy smoke today-cal failed: %s\nURL: %s" % (e, smoke))
    return url


def parse_date(s):
    if not s:
        return datetime.now(KST).replace(microsecond=0)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        sys.exit("ERROR: --date must be ISO 8601, e.g. 2026-10-03T07:30:00+09:00 (got %r)" % s)
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt


def read_md(path):
    if path == "-":
        md = sys.stdin.read()
    else:
        if not os.path.isfile(path):
            sys.exit("ERROR: --file not found: %s" % path)
        md = open(path, encoding="utf-8", newline="").read()
    if not md.strip():
        sys.exit("ERROR: markdown content is empty")
    return md


def finish(no_deploy, url):
    sec = build()
    if no_deploy:
        print("FEED URL (after deploy):", url)
    else:
        deploy(sec)
        print("CHANNEL FEED URL:", url)
    return sec


def cmd_add_valley(args, md):
    dt = parse_date(args.date)
    overrides = {}
    for spec in args.channel_file:
        ch, sep, path = spec.partition("=")
        if not sep or ch not in CHANNEL_NAMES:
            sys.exit("ERROR: --channel-file expects CHANNEL=PATH with CHANNEL in %s" % ",".join(CHANNEL_NAMES))
        overrides[ch] = open(path, encoding="utf-8").read()
    item = {"id": str(uuid.uuid4()), "title": args.title, "pubDate": dt.isoformat(), "html": body_html(md), "md": md}
    if args.link:
        item["link"] = args.link
    apply_split(item, overrides)
    print("Channels:", ", ".join(item["channels"]) or "(none)")
    with publish_lock():
        items = load_items(); items.append(item); save_items(items)
        print("Added item:", args.title, item["pubDate"])
        finish(args.no_deploy, feed_url("valley"))


def cmd_add_channel(args, md):
    slug = args.channel
    if args.channel_file:
        sys.exit("ERROR: --channel-file only applies to the valley briefing (no --channel)")
    dt = parse_date(args.date)
    item = {"id": str(uuid.uuid4()), "title": args.title, "pubDate": dt.isoformat(), "html": body_html(md), "md": md}
    if args.link:
        item["link"] = args.link
    with publish_lock():
        chans = load_channels()
        if slug not in chans["channels"]:
            sys.exit("ERROR: unknown channel %r. Register it first:\n  python3 %s channel create --slug %s --name \"<표시 이름>\"\n"
                     "(existing: %s)" % (slug, os.path.abspath(__file__), slug, ", ".join(sorted(chans["channels"]))))
        items = by_date(load_chan_items(slug) + [item])[:MAX_ITEMS]
        save_json(chan_items_path(slug), items)
        print("Added item to channel %s: %s %s (%d stored)" % (slug, args.title, item["pubDate"], len(items)))
        finish(args.no_deploy, feed_url(slug, chans))


ROUTINE_TEMPLATE = """---
title: "{title}"
date: "{date}"
today_routine:
  version: 1
  id: {rid}
{start}{cal}  tasks:
{tasks}---

# {title}

(읽기용 본문 — 여기에 설명을 쓰세요. 피드 리더에는 이 본문만 보이고, 앱은 위 frontmatter에서 작업을 읽습니다.)
"""


def cmd_routine(args):
    if args.rcmd == "validate":
        md = read_md(args.file)
        errs, fm = validate_routine_md(md)
        if errs:
            print("INVALID:\n  - " + "\n  - ".join(errs)); sys.exit(1)
        if not fm or "today_routine" not in fm:
            print("OK (no today_routine frontmatter — plain reading item)")
            return
        r = fm["today_routine"]
        print("OK today_routine v1 id=%s, %d task(s)%s" % (r["id"], len(r["tasks"]),
              ", start_time=%s" % r["start_time"] if "start_time" in r else ""))
        if "calendar_link" in r:
            cl = r["calendar_link"]
            print("  calendar_link: %s %s scope=%s%s" % (cl["calendar_id"], cl["event_id"], cl["scope"],
                  " original=%s" % list(cl["original_start_time"].values())[0] if "original_start_time" in cl else ""))
        for t in r["tasks"]:
            dur = " (%d분)" % t["duration_minutes"] if "duration_minutes" in t else ""
            print("  - [%s] %s%s" % (t["id"], t["title"], dur))
            for st in t.get("steps") or []:
                print("      · [%s] %s" % (st["id"], st["title"]))
        return
    # new
    if args.start_time and not START_TIME.match(args.start_time):
        sys.exit("ERROR: --start-time must be HH:mm (00:00-23:59)")
    cal = ""
    if args.calendar_id or args.event_id or args.scope or args.original_start:
        if not (args.calendar_id and args.event_id and args.scope):
            sys.exit("ERROR: calendar link needs --calendar-id, --event-id and --scope")
        cal = ("  calendar_link:\n    provider: \"google\"\n    calendar_id: %s\n    event_id: %s\n    scope: \"%s\"\n"
               % (json.dumps(args.calendar_id), json.dumps(args.event_id), args.scope))
        if args.original_start:
            k = "date" if re.match(r"^\d{4}-\d{2}-\d{2}\Z", args.original_start) else "date_time"
            cal += "    original_start_time:\n      %s: %s\n" % (k, json.dumps(args.original_start))
        errs = validate_calendar_link({"provider": "google", "calendar_id": args.calendar_id, "event_id": args.event_id,
                                       "scope": args.scope, **({"original_start_time": {k: args.original_start}}
                                                               if args.original_start else {})})
        if errs:
            sys.exit("ERROR:\n  - " + "\n  - ".join(errs))
    if not ROUTINE_ID.match(args.id):
        sys.exit("ERROR: --id must match ^[a-z0-9][a-z0-9_-]*$")
    q = lambda v: json.dumps(v, ensure_ascii=False)  # JSON string == valid YAML double-quoted scalar
    lines = []
    specs = args.task or ["task-1=첫 번째 작업"]
    for n, spec in enumerate(specs, 1):
        tid, sep, title = spec.partition("=")
        if not sep:
            tid, title = "task-%d" % n, spec
        lines.append("    - id: %s\n      title: %s\n" % (tid, q(title)))
        if args.with_steps:
            lines.append("      duration_minutes: 10\n      steps:\n"
                         "        - id: step-1\n          title: \"세부 작업 1\"\n"
                         "        - id: step-2\n          title: \"세부 작업 2\"\n")
    title = args.title or args.id
    out = ROUTINE_TEMPLATE.format(title=title.replace('"', "'"), date=datetime.now(KST).strftime("%Y-%m-%d"),
                                  rid=args.id, start=('  start_time: "%s"\n' % args.start_time if args.start_time else ""), cal=cal, tasks="".join(lines))
    errs, _ = validate_routine_md(out)
    if errs:
        sys.exit("ERROR: generated template invalid: %s" % errs)
    if args.out and args.out != "-":
        if os.path.exists(args.out) and not args.force:
            sys.exit("ERROR: %s exists (use --force)" % args.out)
        open(args.out, "w", encoding="utf-8").write(out)
        print("Wrote", args.out)
    else:
        sys.stdout.write(out)


def cmd_channel(args):
    if args.ccmd == "list":
        chans = load_channels()
        rows = []
        for slug in ["valley"] + sorted(bot_slugs(chans)):
            m = chans["channels"][slug]
            n = len(load_items()) if slug == "valley" else len(load_chan_items(slug))
            rows.append((slug, m["name"], str(min(n, MAX_ITEMS)), feed_url(slug, chans)))
        dw = lambda t: sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in t)
        pad = lambda t, n: t + " " * (n - dw(t))
        allr = [("SLUG", "NAME", "ITEMS", "FEED URL")] + rows
        w = [max(dw(r[i]) for r in allr) for i in range(3)]
        for r in allr:
            print("  ".join([pad(r[0], w[0]), pad(r[1], w[1]), " " * (w[2] - dw(r[2])) + r[2], r[3]]))
        print("(통합 피드 feed.xml = valley + 모든 봇 채널: %sfeed.xml)" % base_url())
        print("(valley = Valley AI 단일 채널 valley.xml; OPML: %schannels.opml)"
              % base_url())
        return
    slug = args.slug
    if args.ccmd == "create":
        validate_slug(slug)
        if not args.name.strip():
            sys.exit("ERROR: --name must not be empty")
        with publish_lock():
            chans = load_channels()
            if slug in chans["channels"]:
                sys.exit("ERROR: channel %r already exists (%s)" % (slug, chans["channels"][slug]["name"]))
            meta = {"name": args.name.strip(), "description": (args.description or "").strip(),
                    "created": datetime.now(KST).replace(microsecond=0).isoformat()}
            if args.link:
                meta["link"] = args.link
            if args.agent_id:
                validate_agent_id(args.agent_id)
                meta["agent_id"] = args.agent_id
            if args.agent_name:
                meta["agent_name"] = args.agent_name.strip()
            chans["channels"][slug] = meta
            save_channels(chans)
            if not os.path.exists(chan_items_path(slug)):
                save_json(chan_items_path(slug), [])
            print("Created channel %s (%s)" % (slug, meta["name"]))
            finish(args.no_deploy, feed_url(slug, chans))
    elif args.ccmd == "update":
        with publish_lock():
            chans = load_channels()
            if slug not in chans["channels"]:
                sys.exit("ERROR: unknown channel %r" % slug)
            m = chans["channels"][slug]
            if args.name is not None and slug != "valley":
                m["name"] = args.name.strip()
            if args.description is not None and slug != "valley":
                m["description"] = args.description.strip()
            if args.link is not None and slug != "valley":
                m["link"] = args.link
            if args.agent_id is not None:
                validate_agent_id(args.agent_id)
                m["agent_id"] = args.agent_id
            if args.agent_name is not None:
                m["agent_name"] = args.agent_name.strip()
            save_channels(chans)
            print("Updated channel %s (agent_id=%s)" % (slug, m.get("agent_id")))
            sec = build()
            if not args.no_deploy:
                deploy(sec)
    elif args.ccmd == "remove":
        if slug == "valley" or slug in RESERVED_SLUGS:
            sys.exit("ERROR: %r is a builtin/reserved channel and cannot be removed" % slug)
        if not args.yes:
            sys.exit("ERROR: add --yes to confirm removing channel %r and deleting its stored items" % slug)
        with publish_lock():
            chans = load_channels()
            if slug not in chans["channels"]:
                sys.exit("ERROR: unknown channel %r" % slug)
            del chans["channels"][slug]
            save_channels(chans)
            if os.path.exists(chan_items_path(slug)):
                os.remove(chan_items_path(slug))
            print("Removed channel", slug)
            sec = build()
            if not args.no_deploy:
                deploy(sec)


# ---------------- todo inbox (Pages Function /api/inbox, KV-backed) ----------------
INBOX_TOKEN_FILE = os.path.join(BASE, "inbox_token.txt")


def inbox_url():
    return os.environ.get("INBOX_URL") or "https://%s.pages.dev/api/inbox" % project()


def inbox_request(method, body=None, query=""):
    if not os.path.exists(INBOX_TOKEN_FILE):
        sys.exit("ERROR: %s not found" % INBOX_TOKEN_FILE)
    tok = open(INBOX_TOKEN_FILE).read().strip()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(inbox_url() + query, data=data, method=method, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "valley-feed-inbox/1"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e)
        except Exception:
            msg = {"error": {"code": "http_%d" % e.code, "message": e.reason}}
        sys.exit("ERROR: inbox %s -> HTTP %d: %s" % (method, e.code, json.dumps(msg, ensure_ascii=False)))


def cmd_inbox(args):
    if args.icmd == "add":
        body = {"title": args.title}
        for k in ("note", "due", "priority", "source"):
            v = getattr(args, k)
            if v:
                body[k] = v
        if args.tags:
            body["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
        if args.note_file:
            body["note"] = sys.stdin.read() if args.note_file == "-" else open(args.note_file, encoding="utf-8").read()
        _, d = inbox_request("POST", body)
        print(json.dumps(d["item"], ensure_ascii=False, indent=None if args.json else 2))
    elif args.icmd == "list":
        _, d = inbox_request("GET", query="?limit=%d" % args.limit)
        if args.json:
            print(json.dumps(d, ensure_ascii=False)); return
        if not d["items"]:
            print("(inbox empty)"); return
        for it in d["items"]:
            extra = " ".join(x for x in [
                "due=" + it["due"] if it.get("due") else "", "pri=" + it["priority"] if it.get("priority") else "",
                "src=" + it["source"] if it.get("source") else "",
                "#" + ",#".join(it["tags"]) if it.get("tags") else ""] if x)
            print("%s  %s  %s%s" % (it["id"], it["created_at"], it["title"], ("  [" + extra + "]") if extra else ""))
        print("%d item(s)%s" % (d["count"], " (more pending)" if d.get("has_more") else ""))
    elif args.icmd == "bind":
        # One-time setup: bind an existing KV namespace as INBOX on the Pages project, then redeploy.
        nsid = args.namespace_id.strip()
        if not re.fullmatch(r"[0-9a-f]{32}", nsid):
            sys.exit("ERROR: --namespace-id must be the 32-hex KV namespace id")
        tok = os.environ.get("CLOUDFLARE_API_TOKEN") or sys.exit("ERROR: CLOUDFLARE_API_TOKEN not set")
        url = "https://api.cloudflare.com/client/v4/accounts/%s/pages/projects/%s" % (account_id(), project())
        cfg = {"kv_namespaces": {"INBOX": {"namespace_id": nsid}}}
        body = json.dumps({"deployment_configs": {"production": cfg, "preview": cfg}}).encode()
        req = urllib.request.Request(url, data=body, method="PATCH", headers={
            "Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            sys.exit("ERROR: PATCH project -> HTTP %d: %s" % (e.code, e.read().decode()[:500]))
        prod = d["result"]["deployment_configs"]["production"]
        print("bound INBOX ->", (prod.get("kv_namespaces") or {}).get("INBOX"))
        print("INBOX_TOKEN secret present:", "INBOX_TOKEN" in (prod.get("env_vars") or {}))
        open(os.path.join(BASE, "inbox_kv_id.txt"), "w").write(nsid + "\n")
        with publish_lock():
            deploy(build())  # bindings apply to new deployments only
    elif args.icmd == "clear":
        if args.all:
            if not args.yes:
                sys.exit("ERROR: --all deletes every pending todo; add --yes (prefer --ids)")
            _, d = inbox_request("DELETE", {"all": True})
        else:
            if not args.ids:
                sys.exit("ERROR: give --ids ID [ID ...] (or --all --yes)")
            _, d = inbox_request("DELETE", {"ids": args.ids})
        print(json.dumps(d, ensure_ascii=False))



def _progress_task_status(done_ids, skip_ids, rid, tid):
    """Resolve sub-task status from D1 done/skip lists.

    Prefers exact task keys if present (future: rid|tid, rid:tid, rid/tid);
    otherwise inherits the parent card's state.
    """
    for key in (f"{rid}|{tid}", f"{rid}:{tid}", f"{rid}/{tid}", tid):
        if key in done_ids:
            return "done"
        if key in skip_ids:
            return "skip"
    if rid in done_ids:
        return "done"
    if rid in skip_ids:
        return "skip"
    return "todo"


def _progress_items_for_slug(slug):
    """Valley uses items.json; other channels use items/<slug>.json."""
    if slug == "valley":
        return load_items()
    return load_chan_items(slug)


def cmd_progress(args):
    """Read D1 done-state for a date and print JSON for learning bots.

    GET /<secret>/api/done?date=YYYY-MM-DD (path is the gate; no extra token).
    Never prints the secret. Default --date is yesterday KST.
    Optional --channel filters to one slug.
    """
    date = args.date or (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise SystemExit(f"--date must be YYYY-MM-DD, got {date!r}")

    # Build URL with secret; never print url/secret.
    url = "https://%s.pages.dev/%s/api/done?date=%s" % (project(), secret(), date)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "valley-feed-progress/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit("done API HTTP %s" % e.code)
    except Exception as e:
        raise SystemExit("done API failed: %s" % e)

    done_ids = set(payload.get("done") or [])
    skip_ids = set(payload.get("skip") or [])

    data = load_channels()
    channel_map = data.get("channels") or {}
    if args.channel:
        if args.channel not in channel_map:
            raise SystemExit("unknown channel: %s" % args.channel)
        slugs = [args.channel]
    else:
        slugs = list(channel_map.keys())

    out_channels = []
    sum_done = sum_skip = sum_todo = sum_items = 0

    for slug in slugs:
        meta = channel_map[slug]
        items_out = []
        seen_rids = set()
        for it in _progress_items_for_slug(slug):
            md = it.get("md") or ""
            if not md.startswith("---"):
                continue
            m = re.match(r"^---\n(.*?)\n---", md, re.S)
            if not m:
                continue
            try:
                import yaml
                fm = yaml.safe_load(m.group(1)) or {}
            except Exception:
                continue
            day = fm.get("date")
            if not day:
                try:
                    day = datetime.fromisoformat(it["pubDate"]).astimezone(KST).strftime("%Y-%m-%d")
                except Exception:
                    continue
            if str(day) != date:
                continue
            tr = fm.get("today_routine")
            if not isinstance(tr, dict) or not tr.get("id"):
                continue
            rid = tr["id"]
            if rid in seen_rids:
                continue
            seen_rids.add(rid)
            if rid in done_ids:
                card_status = "done"
            elif rid in skip_ids:
                card_status = "skip"
            else:
                card_status = "todo"
            tasks_out = []
            for t in tr.get("tasks") or []:
                if not isinstance(t, dict) or not t.get("id"):
                    continue
                tid = t["id"]
                st = _progress_task_status(done_ids, skip_ids, rid, tid)
                tasks_out.append({
                    "id": tid,
                    "title": t.get("title") or tid,
                    "status": st,
                })
                if st == "done":
                    sum_done += 1
                elif st == "skip":
                    sum_skip += 1
                else:
                    sum_todo += 1
            items_out.append({
                "rid": rid,
                "title": it.get("title") or tr.get("title") or rid,
                "status": card_status,
                "start_time": tr.get("start_time") or "",
                "category": tr.get("category") or "",
                "tasks": tasks_out,
            })
            sum_items += 1
        if items_out or args.channel:
            out_channels.append({
                "slug": slug,
                "name": meta.get("name") or slug,
                "items": items_out,
            })

    result = {
        "date": date,
        "channels": out_channels,
        "summary": {
            "items": sum_items,
            "tasks_done": sum_done,
            "tasks_skip": sum_skip,
            "tasks_todo": sum_todo,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="Shared RSS publisher (see README.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="publish an item (valley briefing by default, or --channel <slug>)")
    a.add_argument("--channel", help="bot channel slug (omit for the valley briefing)")
    a.add_argument("--title", required=True)
    a.add_argument("--file", required=True, help="markdown file ('-' for stdin)")
    a.add_argument("--date", help="ISO datetime (default: now KST)")
    a.add_argument("--link")
    a.add_argument("--no-deploy", action="store_true")
    a.add_argument("--channel-file", action="append", default=[], metavar="CHANNEL=PATH",
                   help="valley only: override auto split for a topic (market|insights|news|community); repeatable")
    c = sub.add_parser("channel", help="manage bot channels")
    csub = c.add_subparsers(dest="ccmd", required=True)
    cc = csub.add_parser("create")
    cc.add_argument("--slug", required=True)
    cc.add_argument("--name", required=True, help="display name (feed title)")
    cc.add_argument("--description")
    cc.add_argument("--link", help="optional website link for the feed's <link>")
    cc.add_argument("--agent-id", help="your Grok Bot agent id (UUID); adds an 'open bot' link to every item")
    cc.add_argument("--agent-name", help="bot name for the link text (default: --name)")
    cc.add_argument("--no-deploy", action="store_true")
    cu = csub.add_parser("update")
    cu.add_argument("--slug", required=True)
    cu.add_argument("--name"); cu.add_argument("--description"); cu.add_argument("--link")
    cu.add_argument("--agent-id"); cu.add_argument("--agent-name")
    cu.add_argument("--no-deploy", action="store_true")
    csub.add_parser("list")
    cr = csub.add_parser("remove")
    cr.add_argument("--slug", required=True)
    cr.add_argument("--yes", action="store_true")
    cr.add_argument("--no-deploy", action="store_true")
    r = sub.add_parser("resplit")
    r.add_argument("--no-deploy", action="store_true")
    ib = sub.add_parser("inbox", help="todo inbox for the user's app (AI adds, app fetches + clears)")
    isub = ib.add_subparsers(dest="icmd", required=True)
    ia = isub.add_parser("add", help="add a todo")
    ia.add_argument("--title", required=True)
    ia.add_argument("--note")
    ia.add_argument("--note-file", help="read note from file ('-' = stdin)")
    ia.add_argument("--due", help="YYYY-MM-DD or YYYY-MM-DDTHH:MM (KST if no offset)")
    ia.add_argument("--priority", choices=["low", "normal", "high", "urgent"])
    ia.add_argument("--source", help="which bot/AI added it, e.g. deals-bot")
    ia.add_argument("--tags", help="comma-separated")
    ia.add_argument("--json", action="store_true")
    il = isub.add_parser("list", help="list pending todos (oldest first)")
    il.add_argument("--limit", type=int, default=100)
    il.add_argument("--json", action="store_true")
    ibd = isub.add_parser("bind", help="one-time: bind KV namespace INBOX to the Pages project + redeploy")
    ibd.add_argument("--namespace-id", required=True)
    ic = isub.add_parser("clear", help="delete todos by id (what the app does after saving them)")
    ic.add_argument("--ids", nargs="+")
    ic.add_argument("--all", action="store_true")
    ic.add_argument("--yes", action="store_true")
    ro = sub.add_parser("routine", help="today_routine v1 frontmatter helpers (template / validate)")
    rsub = ro.add_subparsers(dest="rcmd", required=True)
    rn = rsub.add_parser("new", help="print/write a Markdown template with today_routine frontmatter")
    rn.add_argument("--id", required=True, help="stable routine id, ^[a-z0-9][a-z0-9_-]*$")
    rn.add_argument("--title")
    rn.add_argument("--task", action="append", metavar="ID=TITLE", help="repeatable")
    rn.add_argument("--start-time", help='suggested routine start, "HH:mm" (24h, local time)')
    rn.add_argument("--calendar-id", help="Google calendar id (real id, not 'primary')")
    rn.add_argument("--event-id", help="Google event id (series/occurrence: recurring master id)")
    rn.add_argument("--scope", choices=["event", "series", "occurrence"])
    rn.add_argument("--original-start", help="occurrence only: RFC3339 with offset (date_time) or YYYY-MM-DD (date)")
    rn.add_argument("--with-steps", action="store_true", help="add example duration + steps to each task")
    rn.add_argument("--out", help="write to file (default stdout)")
    rn.add_argument("--force", action="store_true")
    rv = rsub.add_parser("validate", help="validate a Markdown file's frontmatter")
    rv.add_argument("--file", required=True)
    sub.add_parser("deploy")
    sub.add_parser("build")
    p_prog = sub.add_parser("progress", help="print D1 done/skip/todo JSON for a date (learning bots)")
    p_prog.add_argument("--date", default=None, help="YYYY-MM-DD (default: yesterday KST)")
    p_prog.add_argument("--channel", default=None, help="optional channel slug filter")
    args = ap.parse_args()
    if args.cmd == "add":
        md = read_md(args.file)
        check_md_or_exit(md)
        if args.channel and args.channel != "valley":
            cmd_add_channel(args, md)
        else:
            cmd_add_valley(args, md)
    elif args.cmd == "channel":
        cmd_channel(args)
    elif args.cmd == "inbox":
        cmd_inbox(args)
    elif args.cmd == "routine":
        cmd_routine(args)
    elif args.cmd == "resplit":
        with publish_lock():
            items = load_items()
            for it in items:
                if it.get("md"):
                    apply_split(it)
                    print("%s -> %s" % (it["title"], ", ".join(it["channels"]) or "(none)"))
                else:
                    print("%s -> skipped (no stored markdown)" % it["title"])
            save_items(items)
            sec = build()
            if not args.no_deploy:
                deploy(sec)
    elif args.cmd == "build":
        with publish_lock():
            build(); print("FEED URL (after deploy):", feed_url("valley"))
    elif args.cmd == "progress":
        cmd_progress(args)
    else:
        with publish_lock():
            deploy(build())


if __name__ == "__main__":
    main()
