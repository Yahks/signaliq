# SignalIQ — Football Analytics Product Position

**Last updated:** 2026-09-18
**Status:** Foundational document — every downstream product decision must be consistent with this.

---

## What SignalIQ Football IS

A **statistical football analytics platform** that:

1. Collects historical match results from public sources
2. Trains a Dixon-Coles Poisson model on that history
3. Generates **probability estimates** for upcoming matches
4. Publishes **analysis articles** that explain:
   - Team form and context
   - Key narratives (injuries, tactics, motivation)
   - The model's probabilistic output
   - Honest confidence levels
5. **Tracks every prediction against the actual result** and publishes the accuracy record

The output is **analysis**, not advice. The user decides what to do with it.

---

## What SignalIQ Football is NOT

SignalIQ Football **does not**:

- Recommend specific bets
- Suggest stake sizes
- Tell users which team to back
- Imply that following our analysis will produce wins
- Guarantee any outcome
- Take a cut of any betting transaction
- Link to specific bookmakers in a referral capacity
- Operate in jurisdictions where prediction services are illegal
- Allow access to underage users

**If a user loses money on a bet based on our analysis, that is their decision, not our advice.**

---

## The legal framework we operate under

### Position
We are a **statistical analysis publisher**, not a **betting tipster service**.

**Key distinction in law (common to most jurisdictions):**
- "Team A will win" = prediction (regulated in some places)
- "Model gives Team A a 62% probability, with 2.4% standard error" = statistical analysis (broadly allowed)

We always use the latter framing.

### Jurisdictions

We accept users from jurisdictions where statistical football analysis is unregulated. We **block** jurisdictions where:

1. Gambling/betting is illegal (predictions become complicit)
2. Football prediction services require a specific license
3. Age of majority plus gambling-specific age requirements conflict

**Blocklist (v1):**
- Saudi Arabia
- United Arab Emirates
- Qatar
- Kuwait
- Bahrain
- Oman
- Iran
- Yemen
- North Korea
- Cuba
- All US states where tipster services are explicitly banned (currently: WA, HI, ID, TN, VA — some restrictions)

**Allowlist (v1):** All other countries, with age gate + disclaimer acceptance.

Enforcement: IP-based at the middleware layer. Precise geography is not always accurate, but reasonable-effort blocking is the standard.

### Age requirements

Minimum age: **18 years** (or 21 where the local gambling age is higher, e.g., some US states).

Enforcement:
- Age confirmation checkbox at signup
- Terms of Service acceptance (which includes age + risk disclaimers)
- No verification of DOB in v1 (users self-attest, backstopped by ToS)

### Disclaimers

**Everywhere the model's output is displayed** (article page, prediction list, API responses), we display:

> ⚠️ **This is statistical analysis, not betting advice.**
> SignalIQ provides probability estimates from a Poisson model trained on historical data.
> Football matches are inherently unpredictable. Model accuracy is publicly tracked — see /performance.
> If you choose to bet, only stake what you can afford to lose. If gambling is causing harm, seek help.

**On signup**, users must accept:

> I understand that:
> - SignalIQ provides statistical analysis, not betting advice.
> - I am 18 years or older (or the applicable age of majority in my jurisdiction).
> - Football matches have inherent uncertainty; no model can guarantee outcomes.
> - I make my own decisions with my own money, and I accept full responsibility.

---

## Model honesty principles

Every published prediction must:

1. Come from a documented model (Dixon-Coles Poisson)
2. Include a **confidence level** (based on sample size + model fit)
3. Be **tracked and scored** against the actual outcome
4. Include a **model performance URL** linking to the accuracy dashboard
5. Never be cherry-picked — all predictions from a given day appear

**Anti-patterns we refuse:**

- Publishing only winning predictions
- Deleting predictions that turned out wrong
- Editing predictions after results are known
- Using vague language to avoid accountability ("could go either way")
- Claiming >60% win rate on 3-way markets without public verification

**Published metrics (publicly visible, updated daily):**

- Total predictions made
- Correct predictions (Win/Draw/Loss)
- Correct predictions (Over/Under 2.5)
- Rolling 30-day accuracy
- Accuracy by league
- Accuracy by confidence band (does "high confidence" actually predict better?)

---

## Data sources

All data is from:

- **football-data.org** — live fixtures, results, standings
- **Football-Data.co.uk** — historical CSVs (public, license-free for personal and commercial use with attribution)

Both are public and permit this use. We do not scrape or use paywalled data.

Attribution to both sources is displayed on the site.

---

## What we will do if requested by a regulator

If any gambling authority in a jurisdiction where we operate requests:

1. **Clarification of our position** → we provide this document + model methodology
2. **Cessation of service in that jurisdiction** → we comply within 30 days
3. **Data on our predictions and accuracy** → we provide (all public anyway)

---

## Open questions (to be resolved by legal counsel)

- Does the Nigerian National Lottery Regulatory Commission treat probability publishers as tipster services? (**Unresolved — need counsel**)
- Does UK Gambling Commission require a license for probability-only content? (**Likely no, but unclear**)
- What is the correct age gate for our African target markets? (**Needs review**)

**Action:** Before launching to paying users, engage a Nigerian lawyer with gambling-regulation experience to review this document.

---

## Consequences for engineering

Every engineering decision must respect this document:

- **No code path** may produce output that says "bet X" or "stake Y"
- **No API endpoint** may return content to blocked jurisdictions
- **No prediction** may be published without its eventual result being tracked
- **No user** may sign up without accepting the disclaimers
- **No article** may display predictions without the disclaimer attached

If a feature request conflicts with this document, the document wins.
