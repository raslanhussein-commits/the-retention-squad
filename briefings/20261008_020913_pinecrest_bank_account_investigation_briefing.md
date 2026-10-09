# Pinecrest Bank Account Investigation Briefing

### Situation
- **Account:** Pinecrest Bank
- **Days to renewal:** 290 days
- **Health Score:** 53.6 (AT_RISK)
- **Key dimensions:** Adoption 40.8, Engagement 10, Implementation 30, Support 100 (no tickets), Sentiment 60, Commercial 80, Relationship 60.
- **Onboarding:** 30% complete; only the IT administrator has logged in, no end‑users active.
- **Usage change:** 0% (flat)
- **Active user ratio:** 5% (very low)
- **Champion response rate:** 10%
- **Payment dispute / competitor eval / expansion signal:** None.
- **Recent email (5 weeks ago):** IT admin says they are “waiting on our security review before rolling out to staff. Legal needs your SOC 2 report.” No reply logged.

### What the Numbers Miss
- The health score flags risk, but the underlying cause is not a support issue (no tickets) – it’s a **security/compliance bottleneck** preventing broader rollout.
- Adoption and engagement are low because the product cannot be deployed to staff until the SOC 2 report is provided and the security review is cleared.
- The single active user (IT admin) is a **false positive** for support health; the organization is effectively **blocked** from using the platform.
- No insight into end‑user sentiment, NPS, or potential competitor evaluation, which could hide additional churn risk.

### Evidence
- **Email excerpt:** “We are waiting on our security review before rolling out to staff. Legal needs your SOC 2 report.”
- **Snapshot note:** “Only the IT administrator has logged in. No end users have logged in.”
- **Health score guardrails:** AT_RISK status driven by low adoption, engagement, and implementation scores.

### Recommended Actions
| Priority | Action | Owner | Deadline | Success Metric |
|----------|--------|-------|----------|----------------|
| **P0** | Send the latest SOC 2 compliance report to the IT admin and follow up to confirm receipt and next steps for the security review. | Customer Success Manager (CSM) | **3 business days** | Report delivered; IT admin acknowledges receipt and provides timeline for security review completion. |
| **P1** | Accelerate onboarding for end‑users: schedule a kickoff training, assign a product champion, and set up a pilot group. | Implementation Specialist | **2 weeks** | Onboarding completion ≥ 70%; active user ratio ↑ to ≥ 30%. |
| **P2** | Conduct a health‑check call with the IT admin and key stakeholders (Legal, Security, Business unit leads) to surface any additional compliance or functional concerns. | CSM | **1 week** | Call held; documented action items and stakeholder map updated. |
| **P3** | After adoption improves, evaluate expansion opportunities (additional seats or modules). | Account Manager | **60 days** (or by next renewal cycle) | Identify at least one upsell prospect and create a pipeline entry. |

### Confidence
- **Confidence Level:** **Medium** – core risk drivers (security review delay) are confirmed by email, but we lack:
  - Detailed NPS or sentiment data.
  - Competitor evaluation information.
  - Full usage metrics beyond the 0% change.
  - Confirmation of executive sponsor involvement.
- **Missing Data:** NPS score, detailed usage logs, stakeholder map, any internal escalation notes.

*Next step:* Execute the P0 action immediately to unblock the rollout and prevent churn.

