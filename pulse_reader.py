# ============================================================
# CUSTOMER PULSE AI  -  Customer Success Intelligence Engine
# v1.1
#
# Pipeline per account:
#   1. Structured customer data
#   2. Deterministic health score (Python rules)
#   3. Guardrails that escalate status when the score hides risk
#   4. Health Analyst  ->  Risk Investigator  ->  CS Strategist
#   5. Markdown report saved to /reports
# ============================================================

import os
import time
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel

from crewai import Agent, Task, Crew, LLM, Process

load_dotenv()

# ------------------------------------------------------------
# Compatibility patch: CrewAI adds a "cache_breakpoint" marker
# to messages and Groq rejects it. Strip it before sending.
# ------------------------------------------------------------
import litellm

_original_completion = litellm.completion


def _completion_without_cache_marker(*args, **kwargs):
    messages = kwargs.get("messages")
    if messages:
        kwargs["messages"] = [
            {k: v for k, v in m.items() if k != "cache_breakpoint"}
            if isinstance(m, dict) else m
            for m in messages
        ]
    return _original_completion(*args, **kwargs)


litellm.completion = _completion_without_cache_marker


# ============================================================
# 1. SETTINGS
# ============================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Add it to your .env file.")

MODEL_NAME = "groq/openai/gpt-oss-120b"
PAUSE_BETWEEN_ACCOUNTS = 20   # seconds, protects the free-tier rate limit
MAX_ATTEMPTS = 3              # retries per account if the API errors out

llm = LLM(model=MODEL_NAME, api_key=GROQ_API_KEY, temperature=0.2)


# ============================================================
# 2. DATA MODELS
# ============================================================

class AccountSnapshot(BaseModel):
    name: str
    usage_change_pct: float
    active_user_ratio_pct: float
    open_support_tickets: int
    urgent_support_tickets: int
    champion_response_rate_pct: Optional[float] = None
    executive_sponsor_active: Optional[bool] = None
    nps: Optional[int] = None
    days_to_renewal: int
    payment_dispute: bool = False
    competitor_evaluation: bool = False
    expansion_signal: bool = False
    onboarding_completion_pct: Optional[float] = None
    notes: str = ""


class HealthProfile(BaseModel):
    adoption: float
    engagement: float
    support: float
    sentiment: float
    commercial: float
    relationship: float
    implementation: float
    total_score: float
    score_status: str            # status from the score alone
    status: str                  # final status after guardrails
    overrides: List[str] = []    # why the status was escalated


# ============================================================
# 3. DETERMINISTIC HEALTH ENGINE
# ============================================================
# Python calculates the score. The AI only explains it.

STATUS_ORDER = ["HEALTHY", "WATCH", "AT_RISK", "CRITICAL"]


def clamp(value: float, minimum: float = 0, maximum: float = 100) -> float:
    return max(minimum, min(value, maximum))


def status_from_score(score: float) -> str:
    if score >= 80:
        return "HEALTHY"
    if score >= 65:
        return "WATCH"
    if score >= 45:
        return "AT_RISK"
    return "CRITICAL"


def apply_guardrails(account: AccountSnapshot, status: str):
    """Escalate the status when the weighted score hides a clear danger."""
    reasons: List[str] = []

    def escalate(minimum: str, why: str):
        nonlocal status
        if STATUS_ORDER.index(status) < STATUS_ORDER.index(minimum):
            reasons.append(f"{status} -> {minimum}: {why}")
            status = minimum

    if account.urgent_support_tickets >= 3:
        escalate("AT_RISK", f"{account.urgent_support_tickets} urgent support tickets open")
    if account.nps is not None and account.nps <= 4:
        escalate("AT_RISK", f"NPS of {account.nps} signals an unhappy customer")
    if account.executive_sponsor_active is False and account.days_to_renewal <= 90:
        escalate("AT_RISK", "no active executive sponsor with renewal inside 90 days")
    if account.active_user_ratio_pct <= 15:
        escalate("AT_RISK", f"only {account.active_user_ratio_pct:g}% of licensed users are active")
    if account.payment_dispute and account.competitor_evaluation:
        escalate("CRITICAL", "billing dispute combined with a competitor evaluation")

    return status, reasons


def calculate_health(account: AccountSnapshot) -> HealthProfile:
    # Adoption
    usage_score = clamp(70 + account.usage_change_pct * 1.5)
    active_user_score = clamp(account.active_user_ratio_pct)
    adoption = usage_score * 0.55 + active_user_score * 0.45

    # Engagement
    if account.champion_response_rate_pct is None:
        engagement = 60
    else:
        engagement = clamp(account.champion_response_rate_pct)

    # Support
    support = clamp(
        100 - account.open_support_tickets * 8 - account.urgent_support_tickets * 15
    )

    # Sentiment
    sentiment = 60 if account.nps is None else clamp(account.nps * 10)

    # Commercial
    commercial = 80
    if account.days_to_renewal <= 90:
        commercial -= 10
    if account.days_to_renewal <= 60:
        commercial -= 10
    if account.days_to_renewal <= 30:
        commercial -= 10
    if account.payment_dispute:
        commercial -= 30
    if account.competitor_evaluation:
        commercial -= 30
    if account.expansion_signal:
        commercial += 10
    commercial = clamp(commercial)

    # Relationship
    relationship = 60
    if account.executive_sponsor_active is True:
        relationship += 30
    elif account.executive_sponsor_active is False:
        relationship -= 30
    relationship = clamp(relationship)

    # Implementation
    if account.onboarding_completion_pct is None:
        implementation = 60
    else:
        implementation = clamp(account.onboarding_completion_pct)

    total = round(
        adoption * 0.20
        + engagement * 0.15
        + support * 0.10
        + sentiment * 0.10
        + commercial * 0.20
        + relationship * 0.15
        + implementation * 0.10,
        1,
    )

    score_status = status_from_score(total)
    final_status, overrides = apply_guardrails(account, score_status)

    return HealthProfile(
        adoption=round(adoption, 1),
        engagement=round(engagement, 1),
        support=round(support, 1),
        sentiment=round(sentiment, 1),
        commercial=round(commercial, 1),
        relationship=round(relationship, 1),
        implementation=round(implementation, 1),
        total_score=total,
        score_status=score_status,
        status=final_status,
        overrides=overrides,
    )


# ============================================================
# 4. AGENTS
# ============================================================

health_analyst = Agent(
    role="Customer Health Analyst",
    goal=(
        "Analyze the calculated customer health profile and identify the "
        "strongest evidence explaining the customer's current state. "
        "Separate facts from assumptions and never invent evidence."
    ),
    backstory=(
        "You are an experienced B2B Customer Success analyst specializing in "
        "adoption, engagement, sentiment, commercial risk, stakeholder "
        "relationships and onboarding. You are analytical, direct and "
        "evidence-driven. You never sugarcoat customer risk."
    ),
    llm=llm,
    verbose=False,
    allow_delegation=False,
)

risk_investigator = Agent(
    role="Customer Risk Investigator",
    goal=(
        "Investigate the customer profile for hidden churn risks, combinations "
        "of signals, stakeholder risks, commercial risks and missing information."
    ),
    backstory=(
        "You are a senior Customer Success risk specialist. You look beyond "
        "obvious signals, challenge assumptions, and search for combinations "
        "of signals that predict future churn."
    ),
    llm=llm,
    verbose=False,
    allow_delegation=False,
)

cs_strategist = Agent(
    role="Customer Success Strategist",
    goal=(
        "Convert the health analysis and risk findings into specific, "
        "prioritized and measurable next-best actions."
    ),
    backstory=(
        "You are a strategic Customer Success leader responsible for retention, "
        "adoption, customer value and expansion. You never recommend vague "
        "actions such as 'follow up with the customer'. Every recommendation "
        "has a reason, an owner, a timeframe and a measurable success condition."
    ),
    llm=llm,
    verbose=False,
    allow_delegation=False,
)


# ============================================================
# 5. ANALYZE ONE ACCOUNT
# ============================================================

def build_crew(account: AccountSnapshot, health: HealthProfile):
    data_json = account.model_dump_json(indent=2)
    health_json = health.model_dump_json(indent=2)

    override_note = ""
    if health.overrides:
        override_note = (
            "\nIMPORTANT: The raw score said "
            f"{health.score_status}, but guardrail rules escalated the status to "
            f"{health.status}: {'; '.join(health.overrides)}. "
            "Explain why the raw score understated the real risk.\n"
        )

    health_task = Task(
        description=f"""
Analyze this customer account.

CUSTOMER: {account.name}

RAW CUSTOMER DATA:
{data_json}

CALCULATED HEALTH PROFILE:
{health_json}
{override_note}
Use only the provided evidence. Do not invent facts, names or numbers.
Distinguish facts from reasonable inference.

Respond in Markdown with exactly these sections:
### Verdict
(2-3 sentences, evidence-based)
### Key Signals
(bullets: signal - evidence - business impact)
### Hidden Risks
(bullets)
### Data Gaps
(bullets: what is missing that would improve confidence)
### Priority Focus
(one sentence)
""",
        expected_output="A Markdown health analysis with the five requested sections.",
        agent=health_analyst,
    )

    risk_task = Task(
        description=f"""
Investigate churn risk for {account.name}.

CUSTOMER DATA:
{data_json}

HEALTH PROFILE:
{health_json}
{override_note}
Look for: combinations of signals, champion or sponsor dependency,
adoption problems, commercial and renewal risk, support-related risk,
second-order effects, and missing information.
Think beyond the headline score. Do not invent facts.

Respond in Markdown with exactly these sections:
### Churn Risk Level
(LOW / MEDIUM / HIGH / CRITICAL, plus one line explaining why)
### Dangerous Signal Combinations
(bullets)
### Second-Order Effects
(bullets)
### What We Do Not Know
(bullets)
""",
        expected_output="A Markdown risk investigation with the four requested sections.",
        agent=risk_investigator,
        context=[health_task],
    )

    strategy_task = Task(
        description=f"""
Create a Customer Success action plan for {account.name}.

HEALTH PROFILE:
{health_json}
{override_note}
Use the findings of the previous analysts.
Answer: "What should the Customer Success Manager do next?"

Every action must have: priority, exact action, reason, owner,
timeframe, and a measurable success metric.
The single most urgent action MUST be labeled P0. Other actions use P1-P3.
Avoid vague actions.

Bad: "Follow up with customer."
Good: "Schedule a 30-minute recovery call with the champion within 48 hours
and include the support lead to agree a documented fix plan for the bug."

Respond in Markdown with exactly these sections:
### Primary Action (P0)
(Action / Reason / Owner / Timeframe / Success metric)
### Secondary Actions
(numbered list, each with priority, action, owner, timeframe, success metric)
### 30-Day Outlook
(2-3 sentences: what happens if the plan works, and if it does not)
""",
        expected_output="A Markdown action plan with the three requested sections.",
        agent=cs_strategist,
        context=[health_task, risk_task],
    )

    crew = Crew(
        agents=[health_analyst, risk_investigator, cs_strategist],
        tasks=[health_task, risk_task, strategy_task],
        process=Process.sequential,
        verbose=False,
    )
    return crew, health_task, risk_task, strategy_task


def task_text(task) -> str:
    output = getattr(task, "output", None)
    raw = getattr(output, "raw", None)
    return (raw or "(no output captured)").strip()


def analyze_account(account: AccountSnapshot) -> dict:
    print("\n" + "=" * 70)
    print(f"CUSTOMER PULSE AI  ->  {account.name}")
    print("=" * 70)

    health = calculate_health(account)

    print("\n[HEALTH ENGINE]")
    print(f"Overall Score : {health.total_score}/100")
    print(f"Score Status  : {health.score_status}")
    print(f"Final Status  : {health.status}")
    for line in health.overrides:
        print(f"  Guardrail   : {line}")
    print("\nDimensions:")
    for dim in ("adoption", "engagement", "support", "sentiment",
                "commercial", "relationship", "implementation"):
        print(f"  {dim.capitalize():<15}: {getattr(health, dim)}")

    crew, health_task, risk_task, strategy_task = build_crew(account, health)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            crew.kickoff()
            break
        except Exception as error:
            if attempt == MAX_ATTEMPTS:
                raise
            wait = 30 * attempt
            print(f"\n[!] API error ({type(error).__name__}). "
                  f"Retrying in {wait}s (attempt {attempt}/{MAX_ATTEMPTS})...")
            time.sleep(wait)

    result = {
        "account": account,
        "health": health,
        "health_analysis": task_text(health_task),
        "risk_analysis": task_text(risk_task),
        "action_plan": task_text(strategy_task),
    }

    print("\n[HEALTH ANALYST]\n" + result["health_analysis"])
    print("\n[RISK INVESTIGATOR]\n" + result["risk_analysis"])
    print("\n[CS STRATEGIST]\n" + result["action_plan"])
    print("\n" + "=" * 70)
    return result


# ============================================================
# 6. REPORT WRITER
# ============================================================

def write_report(results: List[dict], path: str):
    ordered = sorted(results, key=lambda r: r["health"].total_score)
    counts = {s: 0 for s in STATUS_ORDER}
    for r in results:
        counts[r["health"].status] += 1

    lines = [
        "# Customer Pulse AI - Portfolio Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"Accounts analyzed: {len(results)}  ",
        f"Model: {MODEL_NAME}",
        "",
        "## Portfolio Summary",
        "",
        "| Healthy | Watch | At Risk | Critical |",
        "|---|---|---|---|",
        f"| {counts['HEALTHY']} | {counts['WATCH']} | {counts['AT_RISK']} | {counts['CRITICAL']} |",
        "",
        "### Accounts ranked by risk (worst first)",
        "",
        "| Account | Score | Status | Guardrail triggered |",
        "|---|---|---|---|",
    ]
    for r in ordered:
        h = r["health"]
        guard = "; ".join(h.overrides) if h.overrides else "-"
        lines.append(f"| {r['account'].name} | {h.total_score} | {h.status} | {guard} |")

    for r in ordered:
        a, h = r["account"], r["health"]
        lines += [
            "",
            "---",
            "",
            f"## {a.name}",
            "",
            f"**Score:** {h.total_score}/100  |  **Status:** {h.status}  |  "
            f"**Renewal in:** {a.days_to_renewal} days",
            "",
        ]
        if h.overrides:
            lines.append("**Guardrails:** " + "; ".join(h.overrides))
            lines.append("")
        lines += [
            "| Adoption | Engagement | Support | Sentiment | Commercial | Relationship | Implementation |",
            "|---|---|---|---|---|---|---|",
            f"| {h.adoption} | {h.engagement} | {h.support} | {h.sentiment} | "
            f"{h.commercial} | {h.relationship} | {h.implementation} |",
            "",
            "### Health Analyst",
            "",
            r["health_analysis"],
            "",
            "### Risk Investigator",
            "",
            r["risk_analysis"],
            "",
            "### Action Plan",
            "",
            r["action_plan"],
        ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ============================================================
# 7. SAMPLE ACCOUNTS
# ============================================================

accounts = [
    AccountSnapshot(
        name="Nova Retail Co.",
        usage_change_pct=-40, active_user_ratio_pct=45,
        open_support_tickets=2, urgent_support_tickets=1,
        champion_response_rate_pct=15, executive_sponsor_active=True,
        nps=6, days_to_renewal=45,
        onboarding_completion_pct=85,
        notes="Two support tickets are related to the same unresolved bug. "
              "Main champion Sarah has not opened the last three emails.",
    ),
    AccountSnapshot(
        name="BrightPath Logistics",
        usage_change_pct=35, active_user_ratio_pct=85,
        open_support_tickets=0, urgent_support_tickets=0,
        champion_response_rate_pct=95, executive_sponsor_active=True,
        nps=9, days_to_renewal=200, expansion_signal=True,
        onboarding_completion_pct=95,
        notes="Three new teams were onboarded. Executive sponsor asked about the premium tier.",
    ),
    AccountSnapshot(
        name="Helix Health",
        usage_change_pct=0, active_user_ratio_pct=13,
        open_support_tickets=1, urgent_support_tickets=0,
        champion_response_rate_pct=30, executive_sponsor_active=False,
        nps=5, days_to_renewal=60,
        payment_dispute=True, competitor_evaluation=True,
        onboarding_completion_pct=70,
        notes="Executive sponsor left six weeks ago. Finance disputed the last invoice. "
              "Customer said they are evaluating other vendors.",
    ),
    AccountSnapshot(
        name="Orbit Learning",
        usage_change_pct=20, active_user_ratio_pct=90,
        open_support_tickets=9, urgent_support_tickets=4,
        champion_response_rate_pct=100, executive_sponsor_active=True,
        nps=4, days_to_renewal=90,
        onboarding_completion_pct=95,
        notes="Champion is highly engaged but frustrated. Four support tickets are marked urgent.",
    ),
    AccountSnapshot(
        name="Pinecrest Bank",
        usage_change_pct=0, active_user_ratio_pct=5,
        open_support_tickets=0, urgent_support_tickets=0,
        champion_response_rate_pct=10, executive_sponsor_active=None,
        nps=None, days_to_renewal=290,
        onboarding_completion_pct=30,
        notes="Signed 75 days ago but onboarding is only 30% complete. "
              "Only the IT administrator has logged in. No end users have logged in.",
    ),
    AccountSnapshot(
        name="Zenith Manufacturing",
        usage_change_pct=-10, active_user_ratio_pct=55,
        open_support_tickets=0, urgent_support_tickets=0,
        champion_response_rate_pct=30, executive_sponsor_active=False,
        nps=7, days_to_renewal=120,
        onboarding_completion_pct=100,
        notes="Our champion changed roles two months ago and the new contact is hard "
              "to reach. No complaints, but no engagement either.",
    ),
    AccountSnapshot(
        name="Stellar Fintech",
        usage_change_pct=10, active_user_ratio_pct=82,
        open_support_tickets=0, urgent_support_tickets=0,
        champion_response_rate_pct=90, executive_sponsor_active=True,
        nps=9, days_to_renewal=25, expansion_signal=True,
        onboarding_completion_pct=100,
        notes="Renewal is in 25 days, but the customer is happy and has asked about "
              "adding more seats.",
    ),
    AccountSnapshot(
        name="Redwood Telecom",
        usage_change_pct=0, active_user_ratio_pct=30,
        open_support_tickets=0, urgent_support_tickets=0,
        champion_response_rate_pct=None, executive_sponsor_active=None,
        nps=None, days_to_renewal=150,
        onboarding_completion_pct=None,
        notes="New account, we know very little about it. No survey, no sponsor "
              "identified, no onboarding data.",
    ),
    AccountSnapshot(
        name="Crimson Energy",
        usage_change_pct=25, active_user_ratio_pct=90,
        open_support_tickets=1, urgent_support_tickets=0,
        champion_response_rate_pct=80, executive_sponsor_active=True,
        nps=8, days_to_renewal=40, payment_dispute=True,
        onboarding_completion_pct=100,
        notes="Heavy usage and happy users, but finance is disputing a price increase "
              "on the renewal quote.",
    ),
]


# ============================================================
# 8. MENU + RUN
# ============================================================

def choose_accounts() -> List[AccountSnapshot]:
    print("\n  0. Run ALL accounts (full portfolio report)")
    for i, a in enumerate(accounts, start=1):
        print(f"  {i}. {a.name}")

    while True:
        raw = input("\nType a number and press Enter: ").strip()
        if raw == "0":
            return accounts
        if raw.isdigit() and 1 <= int(raw) <= len(accounts):
            return [accounts[int(raw) - 1]]
        print("That is not on the list, try again.")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("               CUSTOMER PULSE AI")
    print("      CUSTOMER SUCCESS INTELLIGENCE ENGINE")
    print("=" * 60)

    selected = choose_accounts()

    os.makedirs("reports", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join("reports", f"pulse_report_{stamp}.md")

    results: List[dict] = []
    for index, account in enumerate(selected):
        results.append(analyze_account(account))
        write_report(results, report_path)   # saved after every account
        if index < len(selected) - 1:
            print(f"\nPausing {PAUSE_BETWEEN_ACCOUNTS}s to respect rate limits...")
            time.sleep(PAUSE_BETWEEN_ACCOUNTS)

    print(f"\nDone. Full report saved to: {report_path}")