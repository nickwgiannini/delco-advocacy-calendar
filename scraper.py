"""
Delco Rising Advocacy Calendar Scraper
Runs daily via GitHub Actions. Fetches events from all available sources,
merges with hardcoded recurring/fixed events, and writes docs/events.json.
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ET_ZONE = ZoneInfo("America/New_York")
TODAY = date.today()
CUTOFF = TODAY + timedelta(days=180)  # fetch 6 months ahead

# ── HELPERS ───────────────────────────────────────────────────────────────────

def safe_get(url, **kwargs):
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "DelcoRisingBot/1.0"}, **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  ⚠ Failed to fetch {url}: {e}", file=sys.stderr)
        return None

def iso(dt):
    """Return ISO 8601 string from datetime or date."""
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt.isoformat()

def future(dt_str):
    """Return True if the date string is today or in the future."""
    try:
        d = datetime.fromisoformat(dt_str).date() if "T" in dt_str else date.fromisoformat(dt_str)
        return d >= TODAY
    except Exception:
        return False

def make_event(id_, org, summary, start_dt, end_dt=None, location="", description="", url="", recurring=""):
    start_str = iso(start_dt)
    end_str = iso(end_dt) if end_dt else ""
    return {
        "id": id_,
        "_org": org,
        "summary": summary,
        "start": {"dateTime": start_str} if "T" in start_str else {"date": start_str},
        "end": {"dateTime": end_str} if end_str and "T" in end_str else ({"date": end_str} if end_str else {}),
        "location": location,
        "description": description,
        "htmlLink": url,
        "recurring": recurring,
    }


# ── SOURCE 1: MOBILIZE.US — DELCO INDIVISIBLE ─────────────────────────────────
# Mobilize has a public API endpoint for organization events

def fetch_mobilize(org_slug="delcoindivisible", org_id=None):
    """
    Fetch events from Mobilize.us public API.
    org_slug: the slug used in mobilize.us/[slug]/
    """
    events = []
    print(f"  Fetching Mobilize.us ({org_slug})...")

    # Try the public events endpoint (no auth required for public orgs)
    # Mobilize API v1 - public events feed
    params = {
        "timeslot_start": f"gte_{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}",
        "per_page": 100,
        "visibility": "PUBLIC",
    }

    # Try slug-based URL first
    url = f"https://api.mobilize.us/v1/organizations/{org_slug}/events"
    r = safe_get(url, params=params)

    if r is None:
        # Try RSS fallback
        rss_url = f"https://www.mobilize.us/{org_slug}/rss/"
        r = safe_get(rss_url)
        if r:
            events.extend(_parse_mobilize_rss(r.text, org_slug))
        return events

    try:
        data = r.json()
        items = data.get("data", [])
        for item in items:
            title = item.get("title", "")
            loc_data = item.get("location") or {}
            location = loc_data.get("venue") or loc_data.get("address_lines", [""])[0] or ""
            if loc_data.get("city"):
                location += f", {loc_data['city']}, {loc_data.get('state', 'PA')}"
            description = BeautifulSoup(item.get("description", ""), "html.parser").get_text()
            event_url = f"https://www.mobilize.us/{org_slug}/event/{item.get('id', '')}/"

            for ts in item.get("timeslots", [])[:3]:  # take up to 3 timeslots per event
                start_ts = ts.get("start_date")
                end_ts = ts.get("end_date")
                if not start_ts:
                    continue
                start_dt = datetime.fromtimestamp(start_ts, tz=ET_ZONE).replace(tzinfo=None)
                end_dt = datetime.fromtimestamp(end_ts, tz=ET_ZONE).replace(tzinfo=None) if end_ts else None
                if start_dt.date() < TODAY or start_dt.date() > CUTOFF:
                    continue
                ev_id = f"mobilize-{org_slug}-{item['id']}-{start_ts}"
                events.append(make_event(ev_id, "DELCO Indivisible", title, start_dt, end_dt, location, description, event_url))

    except Exception as e:
        print(f"  ⚠ Mobilize API parse error: {e}", file=sys.stderr)
        # Try RSS fallback
        rss_url = f"https://www.mobilize.us/{org_slug}/rss/"
        r2 = safe_get(rss_url)
        if r2:
            events.extend(_parse_mobilize_rss(r2.text, org_slug))

    print(f"    → {len(events)} events from Mobilize")
    return events


def _parse_mobilize_rss(xml_text, org_slug):
    """Parse Mobilize RSS feed as fallback."""
    events = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc_raw = item.findtext("description", "") or ""
            desc = BeautifulSoup(desc_raw, "html.parser").get_text().strip()[:500]
            pub_date_str = item.findtext("pubDate", "")
            try:
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub_date_str).replace(tzinfo=None)
                if pub_dt.date() < TODAY or pub_dt.date() > CUTOFF:
                    continue
                ev_id = f"mobilize-rss-{org_slug}-{hash(link)}"
                events.append(make_event(ev_id, "DELCO Indivisible", title, pub_dt, None, "", desc, link))
            except Exception:
                continue
    except Exception as e:
        print(f"  ⚠ Mobilize RSS parse error: {e}", file=sys.stderr)
    return events


# ── SOURCE 2: DELCO INDIVISIBLE WEBSITE ───────────────────────────────────────

def fetch_delco_indivisible_site():
    """Scrape upcoming events from delcoindivisible.org homepage."""
    events = []
    print("  Fetching delcoindivisible.org...")
    r = safe_get("https://delcoindivisible.org/")
    if not r:
        return events

    soup = BeautifulSoup(r.text, "html.parser")
    # The site uses GoDaddy builder — events are in heading/paragraph blocks
    # We look for date patterns like "Mar 28th", "Thurs, Mar 19th" etc.
    date_pattern = re.compile(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?",
        re.IGNORECASE,
    )
    month_map = {m: i+1 for i, m in enumerate(
        ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    )}

    seen = set()
    for tag in soup.find_all(["h3","h4","h2"]):
        text = tag.get_text(" ", strip=True)
        m = date_pattern.search(text)
        if not m:
            continue
        month_str = m.group(1)[:3].capitalize()
        day = int(m.group(2))
        month_num = month_map.get(month_str)
        if not month_num:
            continue
        year = TODAY.year if month_num >= TODAY.month else TODAY.year + 1
        try:
            ev_date = date(year, month_num, day)
        except ValueError:
            continue
        if ev_date < TODAY or ev_date > CUTOFF:
            continue

        # Get sibling content for title and details
        title = ""
        desc = ""
        nxt = tag.find_next_sibling()
        if nxt:
            inner = nxt.find(["h4","h3","h2","strong"])
            title = inner.get_text(strip=True) if inner else nxt.get_text(" ", strip=True)[:80]
        if not title:
            title = text[:80]

        key = f"{ev_date}-{title[:30]}"
        if key in seen:
            continue
        seen.add(key)

        start_dt = datetime(ev_date.year, ev_date.month, ev_date.day, 19, 0)
        ev_id = f"di-site-{hash(key)}"
        events.append(make_event(ev_id, "DELCO Indivisible", title, start_dt, None,
                                 "Media VFW Post, 11 Hilltop Rd, Media, PA", desc,
                                 "https://delcoindivisible.org/"))

    print(f"    → {len(events)} events from delcoindivisible.org")
    return events


# ── SOURCE 3: PHILLY DSA ──────────────────────────────────────────────────────

def fetch_philly_dsa():
    """Fetch events from Philly DSA's public events page / Action Network."""
    events = []
    print("  Fetching Philly DSA...")

    # Try Action Network API (phillydsa uses it for public events)
    r = safe_get("https://actionnetwork.org/api/v2/events?filter[organization_id]=phillydsa",
                 headers={"OSDI-API-Token": ""})  # public endpoint, no token needed for listed events

    # Fallback: scrape phillydsa.org/events
    r2 = safe_get("https://www.phillydsa.org/events")
    if r2:
        soup = BeautifulSoup(r2.text, "html.parser")
        # Squarespace-based site — events are in structured blocks
        for block in soup.find_all(class_=re.compile(r"eventlist-event|event-item|summary-item")):
            title_tag = block.find(class_=re.compile(r"eventlist-title|summary-title"))
            date_tag = block.find(class_=re.compile(r"event-date|dt-start|summary-thumbnail-date"))
            link_tag = block.find("a", href=True)
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            ev_url = ""
            if link_tag:
                href = link_tag["href"]
                ev_url = href if href.startswith("http") else "https://www.phillydsa.org" + href

            date_str = date_tag.get_text(" ", strip=True) if date_tag else ""
            # Try to parse the date
            start_dt = _parse_dsa_date(date_str)
            if not start_dt or start_dt.date() < TODAY or start_dt.date() > CUTOFF:
                continue
            ev_id = f"dsa-{hash(title+str(start_dt.date()))}"
            events.append(make_event(ev_id, "Philly DSA", title, start_dt, None,
                                     "Philadelphia, PA – see phillydsa.org/events for location",
                                     "", ev_url))

    print(f"    → {len(events)} events from Philly DSA")
    return events


def _parse_dsa_date(s):
    """Attempt to parse various date string formats."""
    s = s.strip()
    formats = [
        "%B %d, %Y", "%b %d, %Y", "%B %d", "%b %d",
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=TODAY.year)
                if dt.date() < TODAY:
                    dt = dt.replace(year=TODAY.year + 1)
            return dt
        except ValueError:
            continue
    return None


# ── SOURCE 4: LWV CENTRAL DELCO ───────────────────────────────────────────────

def fetch_lwv():
    """Scrape LWV-CDC events calendar page."""
    events = []
    print("  Fetching LWV Central Delaware County...")
    r = safe_get("https://my.lwv.org/pennsylvania/central-delaware-county/calendar")
    if not r:
        return events

    soup = BeautifulSoup(r.text, "html.parser")
    # LWV MyLO platform uses Drupal — events are in .views-row or article tags
    for row in soup.find_all(class_=re.compile(r"views-row|event-item")):
        title_tag = row.find(class_=re.compile(r"views-field-title|field-title")) or row.find("h3") or row.find("h2")
        date_tag = row.find(class_=re.compile(r"date-display|field-date|views-field-field-date"))
        link_tag = row.find("a", href=True)
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        date_str = date_tag.get_text(" ", strip=True) if date_tag else ""
        ev_url = ""
        if link_tag:
            href = link_tag["href"]
            ev_url = href if href.startswith("http") else "https://my.lwv.org" + href
        start_dt = _parse_dsa_date(date_str)
        if not start_dt or start_dt.date() < TODAY or start_dt.date() > CUTOFF:
            continue
        ev_id = f"lwv-{hash(title+str(start_dt.date()))}"
        events.append(make_event(ev_id, "LWV", title, start_dt, None,
                                 "See LWV-CDC website for location", "", ev_url))

    print(f"    → {len(events)} events from LWV")
    return events


# ── SOURCE 5: HARDCODED / CALCULATED EVENTS ───────────────────────────────────
# These are events with fixed schedules that don't need scraping

def generate_recurring_events():
    """Generate all recurring events with known schedules."""
    events = []

    # ── COUNTY COUNCIL: 1st & 3rd Wednesday + prior Tuesday ──────────────────
    def nth_weekday(year, month, weekday, n):
        d = date(year, month, 1)
        count = 0
        while True:
            if d.weekday() == weekday:
                count += 1
                if count == n:
                    return d
            d += timedelta(days=1)

    WED, TUE = 2, 1
    for month in range(1, 13):
        for week in [1, 3]:
            reg_date = nth_weekday(TODAY.year, month, WED, week)
            prelim_date = reg_date - timedelta(days=1)
            reg_time = 18 if month <= 5 else 13  # 6pm Jan–May, 1pm Jun–Dec

            for d, title, t, eid_suffix in [
                (prelim_date, "County Council Preliminary Agenda Meeting", 13, f"p-{reg_date.isoformat()}"),
                (reg_date, "County Council Regular Public Meeting", reg_time, f"r-{reg_date.isoformat()}"),
            ]:
                if d < TODAY or d > CUTOFF:
                    continue
                start_dt = datetime(d.year, d.month, d.day, t, 0)
                end_dt = datetime(d.year, d.month, d.day, t + 2, 0)
                is_prelim = "Preliminary" in title
                desc = (
                    "Preliminary Agenda Meeting to familiarize Council and the public on upcoming agenda items. Open to the public. Recorded and posted by 5:00 PM same day."
                    if is_prelim else
                    "Regular Public Meeting. Open to the public. Residents may speak during Public Comment. Meetings typically conclude around 8:00 PM (or 3:00 PM Jun–Dec)."
                )
                events.append(make_event(
                    f"cc-{eid_suffix}", "County Council", title, start_dt, end_dt,
                    "Government Center, 201 W Front St, Media, PA 19063" + (" (Room 200)" if is_prelim else " (1st Floor)"),
                    desc, "https://www.delcopa.gov/council/meetings",
                    "1st & 3rd Wednesday" if not is_prelim else "Tuesday before each Council meeting"
                ))

    # ── DELCO INDIVISIBLE: Monthly meeting (3rd Thursday) ────────────────────
    for month in range(1, 13):
        d = nth_weekday(TODAY.year, month, 3, 3)  # 3rd Thursday
        if d < TODAY or d > CUTOFF:
            continue
        start_dt = datetime(d.year, d.month, d.day, 19, 0)
        end_dt = datetime(d.year, d.month, d.day, 20, 30)
        events.append(make_event(
            f"di-monthly-{d.isoformat()}", "DELCO Indivisible",
            "DELCO Indivisible Monthly Meeting", start_dt, end_dt,
            "Media VFW Post, 11 Hilltop Rd, Media, PA",
            "Delco Indivisible meets on the third Thursday each month at the Media VFW post at 7:00 PM. Open to all.",
            "https://www.mobilize.us/delcoindivisible/event/790082/",
            "3rd Thursday each month"
        ))

    # ── DELCO PA INDIVISIBLE: 2nd & 4th Tuesdays ─────────────────────────────
    for month in range(1, 13):
        for week in [2, 4]:
            d = nth_weekday(TODAY.year, month, TUE, week)
            if d < TODAY or d > CUTOFF:
                continue
            start_dt = datetime(d.year, d.month, d.day, 19, 0)
            end_dt = datetime(d.year, d.month, d.day, 21, 0)
            events.append(make_event(
                f"dpi-{week}-{d.isoformat()}", "Delco PA Indivisible",
                "Delco PA Indivisible General Meeting", start_dt, end_dt,
                "Media VFW Post 3460, 11 Hilltop Rd, Media, PA 19063",
                "General meetings on the 2nd and 4th Tuesdays of each month. Open to all.",
                "https://sites.google.com/view/delcopaindivisible",
                "2nd & 4th Tuesdays 7–9pm"
            ))

    # ── NO KINGS SPRINGFIELD: Every Saturday ─────────────────────────────────
    d = TODAY
    while d.weekday() != 5:  # find next Saturday
        d += timedelta(days=1)
    while d <= CUTOFF:
        start_dt = datetime(d.year, d.month, d.day, 13, 0)
        end_dt = datetime(d.year, d.month, d.day, 14, 0)
        events.append(make_event(
            f"nk-springfield-{d.isoformat()}", "No Kings",
            "No Kings: Springfield – Weekly Saturday Demo", start_dt, end_dt,
            "Springfield Mall, 1198 Baltimore Pike, Springfield, PA",
            "Weekly demonstration against the Trump administration. Every Saturday, rain or shine.",
            "https://www.mobilize.us/delcoindivisible/",
            "Every Saturday 1–2pm"
        ))
        d += timedelta(days=7)

    # ── DELCO INDIVISIBLE: Citizens Bank — 1st Saturday monthly ─────────────
    for month in range(1, 13):
        d = nth_weekday(TODAY.year, month, 5, 1)  # 1st Saturday
        if d < TODAY or d > CUTOFF:
            continue
        start_dt = datetime(d.year, d.month, d.day, 11, 0)
        end_dt = datetime(d.year, d.month, d.day, 12, 0)
        events.append(make_event(
            f"di-citizens-{d.isoformat()}", "DELCO Indivisible",
            "Citizens Bank: Stop Funding Cruelty", start_dt, end_dt,
            "Citizens Bank branch – location changes monthly, check Mobilize",
            "Monthly action raising awareness about Citizens Bank's support for GEO Group, which runs the Moshannon Valley ICE facility in PA.",
            "https://www.mobilize.us/delcoindivisible/",
            "1st Saturday each month 11am–noon"
        ))

    # ── LWV: Key election dates ───────────────────────────────────────────────
    election_dates = [
        (date(TODAY.year, 5, 19), "PA Primary Election Day",
         "Pennsylvania Primary Election. Register by April 18. Mail-in deadline: May 12. Polls open 7AM–8PM.",
         "https://my.lwv.org/pennsylvania/central-delaware-county/voter-toolkit/election-calendar-0"),
        (date(TODAY.year, 11, 3), "PA General Election Day",
         "Pennsylvania General Election — U.S. Senate and all U.S. House seats on ballot. Register by Oct. 13. Mail-in deadline: Oct. 27. Polls open 7AM–8PM.",
         "https://my.lwv.org/pennsylvania/central-delaware-county/voter-toolkit/election-calendar-0"),
    ]
    for ed, title, desc, url in election_dates:
        if ed >= TODAY:
            events.append(make_event(f"lwv-election-{ed.isoformat()}", "LWV", title, ed,
                                     None, "Polls open 7AM–8PM across Pennsylvania", desc, url))

    print(f"    → {len(events)} recurring/hardcoded events generated")
    return events


# ── DEDUP & MERGE ─────────────────────────────────────────────────────────────

def dedup(events):
    """Remove duplicate events by matching org + date + similar title."""
    seen = {}
    result = []
    for e in events:
        start_str = e["start"].get("dateTime") or e["start"].get("date", "")
        day = start_str[:10]
        title_key = re.sub(r"[^a-z0-9]", "", e["summary"].lower())[:30]
        key = f"{e['_org']}-{day}-{title_key}"
        if key not in seen:
            seen[key] = True
            result.append(e)
    return result


def sort_events(events):
    def sort_key(e):
        s = e["start"].get("dateTime") or e["start"].get("date", "")
        return s
    return sorted(events, key=sort_key)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("🗓  Delco Rising Calendar Scraper")
    print(f"   Running for: {TODAY} → {CUTOFF}\n")

    all_events = []

    # Scraped sources
    print("Fetching live sources...")
    all_events.extend(fetch_mobilize("delcoindivisible"))
    all_events.extend(fetch_delco_indivisible_site())
    all_events.extend(fetch_philly_dsa())
    all_events.extend(fetch_lwv())

    # Generated recurring events (always fresh, no scraping needed)
    print("\nGenerating recurring events...")
    all_events.extend(generate_recurring_events())

    # Clean up
    all_events = dedup(all_events)
    all_events = sort_events(all_events)

    print(f"\n✅ Total events: {len(all_events)}")

    # Write output
    out_path = Path(__file__).parent / "docs" / "events.json"
    out_path.parent.mkdir(exist_ok=True)
    payload = {
        "updated": datetime.now().isoformat(),
        "count": len(all_events),
        "events": all_events,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"✅ Written to {out_path}")


if __name__ == "__main__":
    main()
