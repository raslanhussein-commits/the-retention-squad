# ============================================================
# CUSTOMER PULSE MONITOR  -  an autonomous agent loop
#
# Runs on its own. Every cycle it:
#   1. Reads new events dropped into the "inbox" folder
#   2. Scores every account with Python (free and instant)
#   3. Decides which accounts need attention (triage)
#   4. Sends ONLY those to the tool-using agent (pulse_agent.py)
#   5. Remembers what it did, so it never repeats itself
#
# Start:  python pulse_monitor.py        (loops until Ctrl+C)
# Once:   python pulse_monitor.py --once (one cycle, for schedulers)
# ============================================================

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta

from crewai import Task

import pulse_reader as pr
import pulse_agent as pa
from slack_alert import send_slack

# ---------------- Settings (change these) ----------------
CHECK_INTERVAL_MINUTES = 5     # how often it wakes up
COOLDOWN_HOURS = 24            # do not re-investigate an account sooner than this
RETRY_MINUTES = 30             # wait this long after a failed investigation
RENEWAL_WINDOW_DAYS = 45       # renewals closer than this get investigated
MAX_PER_CYCLE = 2              # protects the free-tier rate limit
PAUSE_BETWEEN_ACCOUNTS = 20    # seconds

INBOX = "inbox"
PROCESSED = os.path.join(INBOX, "processed")
STATE_FILE = "monitor_state.json"
LOG_FILE = "monitor_log.md"
RISKY = ("AT_RISK", "CRITICAL")
LONG_AGO = "2000-01-01T00:00:00"

CAPTURE = {"saved": ""}

# Remember where the agent saves its briefing
_original_save_text = pa.save_text


def _capturing_save_text(title, content):
    message = _original_save_text(title, content)
    CAPTURE["saved"] = message
    return message


pa.save_text = _capturing_save_text


# ---------------- Logging and memory ----------------
def log(message):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log("Could not read monitor_state.json, starting with fresh memory.")
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------------- Events (new emails / tickets) ----------------
def parse_event(text):
    """Event file format:  ACCOUNT: name / TYPE: email or ticket / message text"""
    account_match = re.search(r"ACCOUNT:\s*(.+)", text, re.I)
    if not account_match:
        return None
    account = pa.find_account(account_match.group(1).strip())
    if not account:
        return None
    type_match = re.search(r"TYPE:\s*(\w+)", text, re.I)
    kind = type_match.group(1).lower() if type_match else "email"
    start = type_match.end() if type_match else account_match.end()
    body = " ".join(text[start:].split())
    return account, kind, body


def apply_event(account, kind, body):
    target = pa.TICKETS if kind == "ticket" else pa.EMAILS
    target.setdefault(account.name.lower(), []).insert(0, f"RECENT | {body} | NO REPLY LOGGED")


def load_processed_events():
    """Re-learn old events after a restart (no investigation is triggered)."""
    for name in sorted(os.listdir(PROCESSED)):
        if name.endswith(".txt"):
            with open(os.path.join(PROCESSED, name), encoding="utf-8") as f:
                parsed = parse_event(f.read())
            if parsed:
                apply_event(*parsed)


def check_inbox():
    """Read new event files. Returns {account name: [file names]}."""
    found = {}
    for name in sorted(os.listdir(INBOX)):
        path = os.path.join(INBOX, name)
        if not (os.path.isfile(path) and name.endswith(".txt")):
            continue
        with open(path, encoding="utf-8") as f:
            parsed = parse_event(f.read())
        if not parsed:
            log(f"Could not understand inbox file '{name}' (needs an ACCOUNT: line with a known account). Renamed to .bad")
            os.replace(path, path + ".bad")
            continue
        account, kind, body = parsed
        apply_event(account, kind, body)
        destination = os.path.join(PROCESSED, f"{datetime.now():%Y%m%d_%H%M%S}_{name}")
        shutil.move(path, destination)
        found.setdefault(account.name, []).append(name)
        log(f"NEW EVENT ({kind}) for {account.name}: {body[:90]}")
    return found


# ---------------- Triage: who needs attention? ----------------
def triage(state, new_events):
    picks = []
    for account in pr.accounts:
        health = pr.calculate_health(account)
        entry = state.get(account.name, {})
        reasons = []
        priority = 3

        if account.name in new_events:
            reasons.append("new event: " + ", ".join(new_events[account.name]))
            priority = 0
        else:
            eligible_at = datetime.fromisoformat(entry.get("next_eligible", LONG_AGO))
            if eligible_at > datetime.now():
                continue
            if health.status in RISKY:
                reasons.append(f"status {health.status} (score {health.total_score})")
                priority = 1 if health.status == "CRITICAL" else 2
            if account.days_to_renewal <= RENEWAL_WINDOW_DAYS:
                reasons.append(f"renewal in {account.days_to_renewal} days")
                priority = min(priority, 2)
            for earlier in entry.get("pending", []):     # failed last time, try again
                if earlier not in reasons:
                    reasons.append(earlier)
                if earlier.startswith("new event"):
                    priority = 0

        if reasons:
            picks.append((priority, health.total_score, account, reasons))

    picks.sort(key=lambda p: (p[0], p[1]))
    return picks


# ---------------- Investigation (hands over to the agent) ----------------
def investigate(account, reasons):
    base = pa.account_task(account.name)
    note = (f"\nYou were triggered automatically because: {'; '.join(reasons)}. "
            "Make sure your briefing addresses this trigger first.\n")
    task = Task(description=base.description + note,
                expected_output=base.expected_output, agent=pa.investigator)
    CAPTURE["saved"] = ""
    result = pa.run_task(task)
    return CAPTURE["saved"], str(result)


def run_cycle(state):
    new_events = check_inbox()
    picks = triage(state, new_events)
    if not picks:
        log("Cycle complete: nothing needs attention right now.")
        return

    log(f"{len(picks)} account(s) need attention. Investigating up to {MAX_PER_CYCLE} this cycle.")
    chosen = picks[:MAX_PER_CYCLE]
    for index, (_, _, account, reasons) in enumerate(chosen):
        log(f"INVESTIGATING {account.name} - {'; '.join(reasons)}")
        health = pr.calculate_health(account)
        try:
            saved, _answer = investigate(account, reasons)
            state[account.name] = {
                "next_eligible": (datetime.now() + timedelta(hours=COOLDOWN_HOURS)).isoformat(),
                "last_status": health.status, "last_score": health.total_score,
                "last_result": "ok", "last_briefing": saved, "pending": [],
            }
            log(f"DONE {account.name} - {saved or 'agent finished but did not save a briefing'}")
            send_slack(f"*{account.name}* - {health.status} (score {health.total_score}) | Why I looked: {'; '.join(reasons)} | {saved or 'No briefing saved.'}")
        except Exception as error:
            state[account.name] = {
                "next_eligible": (datetime.now() + timedelta(minutes=RETRY_MINUTES)).isoformat(),
                "last_status": health.status, "last_score": health.total_score,
                "last_result": f"error: {type(error).__name__}", "pending": reasons,
            }
            log(f"ERROR {account.name} - {type(error).__name__}: {str(error)[:200]}. "
                f"Will retry in {RETRY_MINUTES} minutes.")
        save_state(state)
        if index < len(chosen) - 1:
            time.sleep(PAUSE_BETWEEN_ACCOUNTS)

    waiting = len(picks) - len(chosen)
    if waiting > 0:
        log(f"{waiting} more account(s) are queued for the next cycle.")


# ---------------- Main loop ----------------
def main():
    once = "--once" in sys.argv
    os.makedirs(PROCESSED, exist_ok=True)
    load_processed_events()
    state = load_state()

    print("\n" + "=" * 60)
    print("          CUSTOMER PULSE MONITOR (autonomous)")
    print("=" * 60)
    log(f"Started. Wakes every {CHECK_INTERVAL_MINUTES} min, cooldown {COOLDOWN_HOURS}h, "
        f"watching the '{INBOX}' folder. Press Ctrl+C to stop.")
    try:
        while True:
            run_cycle(state)
            if once:
                break
            log(f"Sleeping {CHECK_INTERVAL_MINUTES} minutes...")
            time.sleep(CHECK_INTERVAL_MINUTES * 60)
    except KeyboardInterrupt:
        log("Monitor stopped by user.")


if __name__ == "__main__":
    main()
