import feedparser
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# ── Sources ───────────────────────────────────────────────────────────────────

SOURCES = [
    {
        "label": "Delco Rising",
        "cat":   "delco",
        "rss":   "https://delcorising.substack.com/feed",
        "always": True,
    },
    {
        "label": "Daily Times",
        "cat":   "delco",
        "rss":   "https://www.delcotimes.com/feed",
        "always": False,
    },
    {
        "label": "Patch Delco",
        "cat":   "delco",
        "rss":   "https://patch.com/pennsylvania/media/rss.xml",
        "always": False,
    },
    {
        "label": "Spotlight PA",
        "cat":   "pa",
        "rss":   "https://www.spotlightpa.org/feeds/full.xml",
        "always": True,
    },
    {
        "label": "Capital-Star",
        "cat":   "pa",
        "rss":   "https://penncapital-star.com/feed",
        "always": True,
    },
    {
        "label": "Keystone Report",
        "cat":   "pa",
        "rss":   "https://keystonereport.com/feed",
        "always": True,
    },
    {
        "label": "Billy Penn",
        "cat":   "regional",
        "rss":   "https://billypenn.com/feed",
        "always": False,
    },
    {
        "label": "WHYY Politics",
        "cat":   "regional",
        "rss":   "https://whyy.org/categories/politics/feed",
        "always": True,
    },
    {
        "label": "6abc",
        "cat":   "regional",
        "rss":   "https://6abc.com/feed",
        "always": False,
    },
]

# ── Political keyword filter ───────────────────────────────────────────────────

ALWAYS_INCLUDE = {"Delco Rising", "Spotlight PA", "Capital-Star", "Keystone Report", "WHYY Politics"}

KEYWORDS = re.compile(
    r"election|vot(e|ing|er)|ballot|primary|candidate|campaign|"
    r"council|commissioner|mayor|governor|senator|senate|representative|"
    r"congress|legislat|state house|state senate|general assembly|"
    r"democrat|republican|gop|progressive|dsa|union|labor|"
    r"policy|budget|tax|zoning|housing|affordable|police|prison|"
    r"immigr|ice\b|deport|healthcare|health care|medicaid|medicare|"
    r"education|school board|school district|environment|climate|"
    r"infrastructure|transit|septa|shapiro|fetterman|"
    r"delaware county|delco|crozer|springfield|upper darby|haverford|"
    r"media borough|reuther|kearney|harrisburg|pa house|pa senate|"
    r"politics|political|government|partisan|reform|protest|activist|"
    r"ordinance|hearing|testimony|indictment|lawsuit|verdict",
    re.IGNORECASE,
)


def is_political(title, summary):
    return bool(KEYWORDS.search((title or "") + " " + (summary or "")))


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text, n=200):
    text = strip_html(text)
    return text[:n].rstrip() + "\u2026" if len(text) > n else text


def parse_date(entry):
    for field in ("published", "updated"):
        raw = getattr(entry, field, None)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt.astimezone(timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def fetch_source(source):
    articles = []
    try:
        feed = feedparser.parse(
            source["rss"],
            agent="Mozilla/5.0 (compatible; DelcoRisingBot/1.0)",
        )
        for entry in feed.entries[:25]:
            title   = strip_html(getattr(entry, "title", "") or "")
            summary = truncate(
                getattr(entry, "summary", "") or
                getattr(entry, "description", "") or ""
            )
            link = getattr(entry, "link", "") or ""

            if not title:
                continue
            if source["label"] not in ALWAYS_INCLUDE and not is_political(title, summary):
                continue

            articles.append({
                "title":   title,
                "summary": summary,
                "url":     link,
                "source":  source["label"],
                "cat":     source["cat"],
                "date":    parse_date(entry),
            })

    except Exception as e:
        print(f"  ERROR: {e}")

    return articles


def main():
    all_articles = []

    for source in SOURCES:
        print(f"Fetching {source['label']}...")
        articles = fetch_source(source)
        print(f"  -> {len(articles)} articles")
        all_articles.extend(articles)

    # Sort newest first, cap at 200
    all_articles.sort(key=lambda a: a["date"], reverse=True)
    all_articles = all_articles[:200]

    output = {
        "updated":  datetime.now(timezone.utc).isoformat(),
        "count":    len(all_articles),
        "articles": all_articles,
    }

    import os
    os.makedirs("docs", exist_ok=True)

    with open("docs/news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nDone. {len(all_articles)} articles written to docs/news.json")


if __name__ == "__main__":
    main()
