# ============================================================
# CUSTOMER PULSE AGENT  (v2 - a real agent)
#
# Unlike pulse_reader.py (a fixed workflow), this agent has TOOLS
# and DECIDES for itself what to look up, in what order, and when
# it has enough evidence to write its briefing.
# ============================================================

import os
import re
import time
from datetime import datetime

from crewai import Agent, Task, Crew, LLM, Process
from crewai.tools import tool

# Reuse your scoring engine and accounts (also applies the Groq patch)
from pulse_reader import accounts, calculate_health, GROQ_API_KEY

AGENT_MODEL = "groq/openai/gpt-oss-120b"   # change here if tool calling is flaky
MAX_ATTEMPTS = 3

agent_llm = LLM(model=AGENT_MODEL, api_key=GROQ_API_KEY, temperature=0.2)


# ============================================================
# 1. SIMULATED SYSTEMS (stand-ins for your real ticketing / email tools)
# ============================================================

TICKETS = {
    "nova retail co.": [
        "#4412 | 12 days ago | HIGH | CSV export fails above 5,000 rows | OPEN - engineering says fix planned next release",
        "#4471 | 5 days ago | HIGH | Same export failure again; team now exporting manually | OPEN",
    ],
    "orbit learning": [
        "#5120 | 3 days ago | URGENT | Dashboard takes 30+ seconds to load at 9am | OPEN",
        "#5133 | 2 days ago | URGENT | Live training sessions failing due to slow loads | OPEN",
        "#5140 | 1 day ago | URGENT | Reports time out | OPEN",
        "#5141 | 1 day ago | URGENT | API errors during peak hours | OPEN",
        "(5 more lower-priority performance tickets open)",
    ],
    "helix health": [
        "#3302 | 25 days ago | MEDIUM | SSO login fails for new users | OPEN",
    ],
    "crimson energy": [
        "#6010 | 4 days ago | LOW | Billing question about renewal quote | OPEN",
    ],
}

EMAILS = {
    "nova retail co.": [
        "3 weeks ago | FROM Sarah (champion): Our ops team is frustrated with the export problem. We need a date. Also our new VP Operations is reviewing all tool spend this quarter. | NO REPLY LOGGED",
        "2 weeks ago | FROM us: Just checking in, how are things going? | NO RESPONSE",
    ],
    "orbit learning": [
        "4 days ago | FROM champion: Load times are unacceptable. Our trainers cannot run live sessions. If this is not fixed before our board review next month we will have to look at alternatives. | REPLIED: 'We are looking into it.'",
    ],
    "helix health": [
        "3 weeks ago | FROM Finance: We are disputing invoice 8841, the seat count does not match our contract. | NO REPLY LOGGED",
        "2 weeks ago | FROM interim contact: Nobody told us who replaced Dr. Amir. Procurement has asked us to collect pricing comparisons. | NO REPLY LOGGED",
    ],
    "zenith manufacturing": [
        "3 weeks ago | FROM Priya (new contact): I just inherited this tool and I am not sure what we use it for. Can you send an overview? | NO REPLY LOGGED",
    ],
    "pinecrest bank": [
        "5 weeks ago | FROM IT admin: We are waiting on our security review before rolling out to staff. Legal needs your SOC 2 report. | NO REPLY LOGGED",
    ],
    "crimson energy": [
        "6 days ago | FROM Finance: The renewal quote shows an 18% increase. We were told 5% on the sales call. | NO REPLY LOGGED",
    ],
}


# ============================================================
# 2. TOOL LOGIC (plain functions, easy to test)
# ============================================================

def find_account(name: str):
    key = (name or "").strip().lower()
    for a in accounts:
        if a.name.lower() == key:
            return a
    for a in accounts:
        if key and (key in a.name.lower() or a.name.lower() in key):
            return a
    return None


def not_found(name: str) -> str:
    names = ", ".join(a.name for a in accounts)
    return f"No account matches '{name}'. Available accounts: {names}"


def portfolio_text() -> str:
    lines = ["Account | Score | Status | Days to renewal | Expansion signal"]
    for a in accounts:
        h = calculate_health(a)
        lines.append(f"{a.name} | {h.total_score} | {h.status} | "
                     f"{a.days_to_renewal} | {'yes' if a.expansion_signal else 'no'}")
    return "\n".join(lines)


def snapshot_text(name: str) -> str:
    a = find_account(name)
    return a.model_dump_json(indent=2) if a else not_found(name)


def score_text(name: str) -> str:
    a = find_account(name)
    return calculate_health(a).model_dump_json(indent=2) if a else not_found(name)


def history_text(source: dict, label: str, name: str) -> str:
    a = find_account(name)
    if not a:
        return not_found(name)
    items = source.get(a.name.lower())
    if not items:
        return f"No {label} on file for {a.name}."
    return "\n".join(items)


def save_text(title: str, content: str) -> str:
    os.makedirs("briefings", exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:50] or "briefing"
    path = os.path.join("briefings", f"{datetime.now():%Y%m%d_%H%M%S}_{slug}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{content}\n")
    return f"Briefing saved to {path}"


# ============================================================
# 3. THE AGENT'S TOOLS
# ============================================================

@tool("get_portfolio_overview")
def get_portfolio_overview(scope: str = "all") -> str:
    """Lists every account with its health score, status, days to renewal and expansion signal. Use this first when you need to decide which accounts need attention. Input: the word all."""
    return portfolio_text()


@tool("get_account_snapshot")
def get_account_snapshot(account_name: str) -> str:
    """Returns the raw data record for ONE account: usage change, active users, tickets, NPS, renewal date, billing dispute, competitor evaluation and notes. Input: the exact account name."""
    return snapshot_text(account_name)


@tool("get_health_score")
def get_health_score(account_name: str) -> str:
    """Returns the calculated health score for ONE account, with all seven dimension scores and any status guardrails that fired. Input: the exact account name."""
    return score_text(account_name)


@tool("get_ticket_history")
def get_ticket_history(account_name: str) -> str:
    """Returns the individual support tickets for ONE account (what each ticket is about, how old it is, whether it is open). Use it to understand WHY ticket counts are high. Input: the exact account name."""
    return history_text(TICKETS, "ticket history", account_name)


@tool("get_recent_emails")
def get_recent_emails(account_name: str) -> str:
    """Returns recent customer emails for ONE account, including who wrote them and whether anyone replied. Often reveals risks that the numbers hide. Input: the exact account name."""
    return history_text(EMAILS, "email history", account_name)


@tool("save_briefing")
def save_briefing(title: str, markdown_content: str) -> str:
    """Saves the finished briefing to a Markdown file. Call this exactly once, at the very end, with a short title and the full briefing text."""
    return save_text(title, markdown_content)


# ============================================================
# 4. THE AGENT
# ============================================================

investigator = Agent(
    role="Customer Success Investigator",
    goal=(
        "Work out what is really happening in customer accounts by gathering "
        "evidence with your tools, then tell the Customer Success Manager "
        "exactly what to do next."
    ),
    backstory=(
        "You are a senior Customer Success investigator. You never guess: "
        "when you need a fact you call a tool. The raw numbers can hide the "
        "real story, so you always check ticket history and emails before "
        "concluding. If a tool says there is no data, you say so plainly and "
        "never invent facts. Your recommendations are specific, with an "
        "owner, a deadline and a measurable result."
    ),
    tools=[get_portfolio_overview, get_account_snapshot, get_health_score,
           get_ticket_history, get_recent_emails, save_briefing],
    llm=agent_llm,
    verbose=True,
    allow_delegation=False,
    max_iter=15,
    max_rpm=20,
)


# ============================================================
# 5. TASKS
# ============================================================

def account_task(name: str) -> Task:
    return Task(
        description=f"""
Investigate the account "{name}" and decide what the Customer Success Manager should do next.

How to work:
1. Start with get_account_snapshot and get_health_score.
2. Then call get_ticket_history and get_recent_emails. The numbers can hide the real story, so look for what they do not show.
3. If a tool returns no data, say so. Never invent facts.
4. Write a briefing in Markdown with these sections:
### Situation
### What the Numbers Miss
### Evidence   (quote short phrases from tickets or emails)
### Recommended Actions   (the single most urgent action is P0, then P1-P3; each with owner, deadline, success metric)
### Confidence   (High/Medium/Low, and what data is missing)
5. Finally call save_briefing exactly once with the briefing.
""",
        expected_output="The briefing text, followed by confirmation that it was saved.",
        agent=investigator,
    )


def portfolio_task() -> Task:
    return Task(
        description="""
Imagine it is Monday morning. Review the whole customer portfolio.

How to work:
1. Call get_portfolio_overview.
2. Choose the 3 accounts that most need the CSM's attention THIS WEEK. Do not just pick the lowest scores: consider renewal timing and hidden risk.
3. Investigate each chosen account with your tools (snapshot, ticket history, emails).
4. Write a Monday briefing in Markdown:
### Top 3 Priorities   (for each: why it was chosen, the evidence, and the P0 action with owner and deadline)
### Why Not the Others   (one line on the accounts you skipped)
### Quick Wins   (healthy accounts or expansion opportunities worth a short touch)
5. Never invent facts. If a tool returns no data, say so.
6. Finally call save_briefing exactly once.
""",
        expected_output="The Monday briefing, followed by confirmation that it was saved.",
        agent=investigator,
    )


# ============================================================
# 6. RUN
# ============================================================

def run_task(task: Task):
    crew = Crew(agents=[investigator], tasks=[task], process=Process.sequential, verbose=False)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return crew.kickoff()
        except Exception as error:
            if attempt == MAX_ATTEMPTS:
                raise
            wait = 30 * attempt
            print(f"\n[!] {type(error).__name__}: retrying in {wait}s "
                  f"(attempt {attempt}/{MAX_ATTEMPTS})...")
            time.sleep(wait)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("            CUSTOMER PULSE AGENT")



    print("=" * 60)
    print("\n  0. Monday portfolio review (agent chooses the top 3 accounts)")
    for i, a in enumerate(accounts, start=1):
        print(f"  {i}. Investigate {a.name}")

    while True:
        raw = input("\nType a number and press Enter: ").strip()
        if raw == "0":
            task = portfolio_task()
            break
        if raw.isdigit() and 1 <= int(raw) <= len(accounts):
            task = account_task(accounts[int(raw) - 1].name)
            break
        print("That is not on the list, try again.")

    print("\nThe agent is working. Watch it choose its own tools...\n")
    result = run_task(task)
    print("\n" + "=" * 60)
    print("FINAL OUTPUT")
    print("=" * 60)
    print(result)