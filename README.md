# Delco Rising Advocacy Calendar

Auto-updating event scraper for the Delco Rising advocacy calendar widget.

## What this does

- Runs **daily at 6 AM Eastern** via GitHub Actions
- Scrapes events from Mobilize.us (DELCO Indivisible), delcoindivisible.org, Philly DSA, and LWV-CDC
- Generates all recurring events (County Council, Delco Indivisible monthly meetings, No Kings Saturdays, etc.)
- Writes everything to `docs/events.json`, which is served publicly via GitHub Pages
- Your Squarespace widget fetches that JSON URL on page load — always fresh

## Sources

| Source | Method |
|---|---|
| DELCO Indivisible (Mobilize.us) | API + RSS fallback |
| delcoindivisible.org | HTML scrape |
| Philly DSA | HTML scrape |
| LWV Central Delaware County | HTML scrape |
| County Council meetings | Generated from schedule |
| DELCO Indivisible monthly meetings | Generated (3rd Thursday) |
| Delco PA Indivisible meetings | Generated (2nd & 4th Tuesday) |
| No Kings Springfield | Generated (every Saturday) |
| Citizens Bank actions | Generated (1st Saturday) |
| PA Election dates | Hardcoded |

## Setup

### 1. Fork / clone this repo to your GitHub account

### 2. Enable GitHub Pages
- Go to repo **Settings → Pages**
- Source: **Deploy from a branch**
- Branch: `main` / folder: `/docs`
- Save — your events.json will be live at:
  `https://[your-username].github.io/delco-calendar/events.json`

### 3. Enable GitHub Actions
- Go to **Actions tab** in your repo
- Click "I understand my workflows, go ahead and enable them"
- Click **Update Advocacy Calendar → Run workflow** to test it manually

### 4. Update your Squarespace widget
Replace the `SEED_EVENTS` array in your widget with a fetch call:

```javascript
// At the top of your <script> block, replace SEED_EVENTS and the allEvents() function with:

const EVENTS_JSON_URL = 'https://[your-username].github.io/delco-calendar/events.json';

let liveEvents = [];
let customEvents = [];
try { customEvents = JSON.parse(localStorage.getItem('delco_custom_events') || '[]'); } catch(e){}

async function loadEvents() {
  try {
    const res = await fetch(EVENTS_JSON_URL);
    const data = await res.json();
    liveEvents = data.events || [];
  } catch(e) {
    console.warn('Could not load live events, using local only');
  }
  render();
  renderCalNav();
}

function allEvents() {
  const now = new Date(); now.setHours(0,0,0,0);
  return [...liveEvents, ...customEvents]
    .filter(e => new Date(e.start.dateTime || e.start.date) >= now)
    .sort((a,b) => new Date(a.start.dateTime||a.start.date) - new Date(b.start.dateTime||b.start.date));
}

// Then change your DOMContentLoaded to:
window.addEventListener('DOMContentLoaded', () => {
  loadEvents();
  renderCalNav();
});
```

## Manual trigger

You can run the scraper manually anytime:
- Go to **Actions → Update Advocacy Calendar → Run workflow**

## Adding new sources

Edit `scraper.py` and add a new fetch function following the same pattern as the existing ones. 
Call it from `main()` with `all_events.extend(your_new_function())`.
