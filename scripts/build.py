#!/usr/bin/env python3
"""Build profile pages, index and news digest from data/*.yaml.

Usage:
  python scripts/build.py            # rebuild pages and fetch fresh news
  python scripts/build.py --offline  # rebuild pages only, keep existing news

News comes from public Google News RSS search feeds (headline + link + source
only). Runs daily from .github/workflows/daily.yml.
"""
import datetime as dt
import email.utils
import glob
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = yaml.safe_load(open(os.path.join(ROOT, "data", "config.yaml"), encoding="utf-8"))
DATA = yaml.safe_load(open(os.path.join(ROOT, "data", CFG["data_file"]), encoding="utf-8"))
CACHE = os.path.join(ROOT, "data", "news-cache.json")
OUT_DIR = os.path.join(ROOT, CFG["pages_dir"])
UA = {"User-Agent": "Mozilla/5.0 (compatible; RadarBot/1.0; +https://github.com/Subhadeep998)"}
TODAY = dt.date.today()


def slug(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def q(s):
    return urllib.parse.quote_plus(s)


def news_query(c):
    base = c.get("query") or c["name"]
    if " OR " in base:
        return f"({base})"
    return f'"{base}"'


def gnews_url(query, days):
    return ("https://news.google.com/rss/search?q=" + q(f"{query} when:{days}d")
            + "&hl=en-IN&gl=IN&ceid=IN:en")


def gnews_web(query):
    return "https://news.google.com/search?q=" + q(query) + "&hl=en-IN&gl=IN&ceid=IN:en"


def fetch_rss(url, limit):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(" - " + src)]
        try:
            date = email.utils.parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
        except Exception:
            date = ""
        items.append({"title": " ".join(title.split()), "link": it.findtext("link") or "",
                      "source": src, "date": date})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:limit]


def md(s):
    return (s or "").replace("|", "\\|").replace("[", "(").replace("]", ")")


def news_table(items, empty):
    if not items:
        return f"_{empty}_\n"
    rows = ["| Date | Headline | Source |", "|:--|:--|:--|"]
    for n in items:
        rows.append(f"| {n['date']} | [{md(n['title'])}]({n['link']}) | {md(n['source'])} |")
    return "\n".join(rows) + "\n"


def link_row(c, group):
    nq = news_query(c)
    links = [f"[🌐 Website]({c['site']})"]
    if c.get("research"):
        links.append(f"[🔬 Research]({c['research']})")
    for label, tmpl in CFG.get("search_links", []):
        domain = urllib.parse.urlparse(c["site"]).netloc.replace("www.", "")
        clean = re.sub(r"\s*\(.*?\)", "", c["name"])
        links.append(f"[{label}]({tmpl.format(domain=domain, name=q(clean))})")
    if c.get("trials"):
        sponsor = c.get("query") or c["name"]
        links.append(f"[🧪 Clinical trials](https://clinicaltrials.gov/search?spons={q(sponsor)})")
    links.append(f"[📰 Google News]({gnews_web(nq)})")
    links.append(f"[🔔 Set a Google Alert](https://www.google.com/alerts?q={q(c.get('query') or c['name'])})")
    return " · ".join(links)


def page(c, group, news):
    s = slug(c["name"])
    n = news.get(s, {})
    out = [f"# {c['name']}", "",
           f"> **{c['focus']}** · {group['title']}", "",
           f"[← All {CFG['noun_plural']}](../README.md) · [Today's digest](../NEWS.md)", "",
           link_row(c, group), ""]
    if c.get("note"):
        out += [f"> ℹ️ {c['note']}", ""]
    if c.get("tools"):
        out += [f"## 🧰 {CFG['tools_heading']}", ""]
        out += [f"- [{', '.join(t[:-1])}]({t[-1]})" for t in c["tools"]]
        out.append("")
    out += [f"## 🔭 {CFG['plans_heading']}", "",
            f"<sub>{CFG['plans_blurb']} Updated {n.get('updated', '—')}.</sub>", "",
            news_table(n.get("plans", []), "Feed populates on the next daily run."),
            "## 📰 Latest news (last 7 days)", "",
            news_table(n.get("latest", []), "No major coverage this week — or the feed populates on the next daily run."),
            "---",
            "<sub>Headlines and links only, via Google News RSS. Always verify against the organisation's official releases before relying on them.</sub>", ""]
    return "\n".join(out)


def main():
    offline = "--offline" in sys.argv
    news = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    os.makedirs(OUT_DIR, exist_ok=True)
    total, ok = 0, 0
    for g in DATA["groups"]:
        for c in g["companies"]:
            total += 1
            s = slug(c["name"])
            if not offline:
                try:
                    nq = news_query(c)
                    latest = fetch_rss(gnews_url(nq, 7), 10)
                    time.sleep(1.0)
                    plans = fetch_rss(gnews_url(f"{nq} ({CFG['plans_keywords']})", 30), 8)
                    time.sleep(1.0)
                    news[s] = {"latest": latest, "plans": plans, "updated": TODAY.isoformat()}
                    ok += 1
                except Exception as exc:
                    print(f"[news] {c['name']}: {exc}", file=sys.stderr)
            with open(os.path.join(OUT_DIR, f"{s}.md"), "w", encoding="utf-8") as f:
                f.write(page(c, g, news))
    # remove pages for entries deleted from the data file
    keep = {slug(c["name"]) for g in DATA["groups"] for c in g["companies"]}
    for p in glob.glob(os.path.join(OUT_DIR, "*.md")):
        if os.path.basename(p)[:-3] not in keep:
            os.remove(p)
    json.dump(news, open(CACHE, "w"), indent=1, ensure_ascii=False)
    write_index(news)
    write_digest(news)
    print(f"{total} pages built; news refreshed for {ok}.")


def write_index(news):
    tpl = open(os.path.join(ROOT, "data", "README.template.md"), encoding="utf-8").read()
    blocks = []
    for g in DATA["groups"]:
        rows = [f"### {g['title']}", "", f"| {CFG['noun'].title()} | Focus | Profile | Headlines this week |", "|:--|:--|:--|--:|"]
        for c in g["companies"]:
            s = slug(c["name"])
            cnt = len(news.get(s, {}).get("latest", []))
            rows.append(f"| [**{c['name']}**]({c['site']}) | {c['focus']} | [Open →]({CFG['pages_dir']}/{s}.md) | {cnt if cnt else '—'} |")
        blocks.append("\n".join(rows))
    count = sum(len(g["companies"]) for g in DATA["groups"])
    body = tpl.replace("{{INDEX}}", "\n\n".join(blocks)).replace("{{COUNT}}", str(count))
    body = body.replace("{{UPDATED}}", TODAY.strftime("%d %b %Y"))
    open(os.path.join(ROOT, "README.md"), "w", encoding="utf-8").write(body)


def write_digest(news):
    out = [f"# 📰 Daily digest — {TODAY.strftime('%d %B %Y')}", "",
           f"> The top headlines for every {CFG['noun']} in the radar, refreshed every morning (IST).",
           "> Headlines and links only — click through to read the original reporting.", "",
           "[← Back to the radar](README.md)", ""]
    for g in DATA["groups"]:
        out += [f"## {g['title']}", ""]
        for c in g["companies"]:
            s = slug(c["name"])
            items = news.get(s, {}).get("latest", [])[:3]
            out.append(f"**[{c['name']}]({CFG['pages_dir']}/{s}.md)**")
            if items:
                out += [f"- {n['date']} · [{md(n['title'])}]({n['link']}) — _{md(n['source'])}_" for n in items]
            else:
                out.append("- _No major headlines in the last 7 days._")
            out.append("")
    open(os.path.join(ROOT, "NEWS.md"), "w", encoding="utf-8").write("\n".join(out))


if __name__ == "__main__":
    main()
