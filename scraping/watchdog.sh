#!/bin/bash
# Supervises scrape_rym_charts.py -> scrape_reviews.py -> scrape_covers.py for
# the genre in config.py. Relaunches on death, detects genuine completion vs.
# process death, and flags real stalls (progress metric unchanged across
# consecutive relaunches) rather than blindly retrying forever. See
# scraping/README.md "Running unattended".
set -uo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate

progress() {
  python3 -c "
import sqlite3, config
conn = sqlite3.connect(config.DB_PATH)
albums = conn.execute('SELECT COUNT(*) FROM albums WHERE genre=?', (config.GENRE,)).fetchone()[0]
pages = conn.execute('SELECT COUNT(*) FROM scraped_pages').fetchone()[0]
reviews = conn.execute('SELECT COUNT(*) FROM user_reviews').fetchone()[0]
complete = conn.execute('SELECT COUNT(*) FROM album_details ad JOIN albums a ON a.id=ad.album_id WHERE a.genre=? AND ad.reviews_complete=1', (config.GENRE,)).fetchone()[0]
covers = conn.execute('SELECT COUNT(*) FROM albums WHERE genre=? AND cover_url IS NOT NULL', (config.GENRE,)).fetchone()[0]
print(f'PROGRESS albums={albums} chart_pages_scraped={pages} reviews={reviews} albums_reviews_complete={complete}/{albums} covers={covers}/{albums}')
"
}

metric() {
  # $1: sqlite query returning one number, used to detect stalls across relaunches
  python3 -c "
import sqlite3, config
conn = sqlite3.connect(config.DB_PATH)
print(conn.execute(\"\"\"$1\"\"\").fetchone()[0])
"
}

probe() {
  python3 -c "
from scrapling.fetchers import StealthySession
try:
    with StealthySession(headless=True, max_pages=1) as s:
        r = s.fetch('https://rateyourmusic.com/', solve_cloudflare=True, network_idle=False, wait_selector='body', timeout=60000)
        print('PROBE_STATUS', r.status)
except Exception as e:
    print('PROBE_ERROR', repr(e))
"
}

charts_complete() {
  # $1: byte offset into charts.log before this attempt started
  tail -c +"$(($1 + 1))" charts.log 2>/dev/null | grep -q "reached end of chart, stopping"
}

reviews_complete() {
  python3 -c "
import sqlite3, config
conn = sqlite3.connect(config.DB_PATH)
total = conn.execute('SELECT COUNT(*) FROM albums WHERE genre=?', (config.GENRE,)).fetchone()[0]
done = conn.execute('''SELECT COUNT(*) FROM albums a JOIN album_details ad ON ad.album_id=a.id
                        WHERE a.genre=? AND ad.reviews_complete=1''', (config.GENRE,)).fetchone()[0]
import sys
sys.exit(0 if (total > 0 and done == total) else 1)
"
}

covers_complete() {
  python3 -c "
import sqlite3, config
conn = sqlite3.connect(config.DB_PATH)
total = conn.execute('SELECT COUNT(*) FROM albums WHERE genre=?', (config.GENRE,)).fetchone()[0]
done = conn.execute('SELECT COUNT(*) FROM albums WHERE genre=? AND cover_url IS NOT NULL', (config.GENRE,)).fetchone()[0]
import sys
sys.exit(0 if (total > 0 and done == total) else 1)
"
}

# run_stage <script> <log> <is_complete_fn> <metric_query>
run_stage() {
  local script=$1 log=$2 is_complete_fn=$3 metric_query=$4
  local last_metric="" stall_count=0

  while true; do
    local offset
    offset=$( [ -f "$log" ] && wc -c < "$log" || echo 0 )

    echo "STAGE_LAUNCH $script"
    python "$script" >> "$log" 2>&1
    local exit_code=$?
    echo "STAGE_EXITED $script code=$exit_code"
    progress

    if "$is_complete_fn" "$offset"; then
      echo "STAGE_COMPLETE $script"
      return 0
    fi

    local m
    m=$(metric "$metric_query")
    if [ "$m" == "$last_metric" ]; then
      stall_count=$((stall_count + 1))
    else
      stall_count=0
    fi
    last_metric=$m

    if [ "$stall_count" -ge 2 ]; then
      echo "STALL_ALERT $script metric_unchanged=$m across $stall_count relaunches - probing connectivity"
      probe
      echo "STALL_ALERT_ACTION_NEEDED $script - if PROBE_STATUS is not 200, switch network (wifi/hotspot/VPN) per README, then this will keep retrying and self-heal"
      stall_count=0
    fi

    echo "RELAUNCHING $script in 10s"
    sleep 10
  done
}

echo "WATCHDOG_START"
run_stage scrape_rym_charts.py charts.log charts_complete \
  "SELECT COUNT(*) FROM scraped_pages"

echo "WATCHDOG_STAGE_TRANSITION charts_done_starting_reviews"
run_stage scrape_reviews.py reviews.log reviews_complete \
  "SELECT COUNT(*) FROM user_reviews"

echo "WATCHDOG_STAGE_TRANSITION reviews_done_starting_covers"
run_stage scrape_covers.py covers.log covers_complete \
  "SELECT COUNT(*) FROM albums WHERE cover_url IS NOT NULL"

echo "WATCHDOG_ALL_DONE"
