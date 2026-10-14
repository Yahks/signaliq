"""Seed the three relocated prompts (S.6.1a).

These replace inline strings that existed in cluster.py and subblocks/base.py.
The template text is copied verbatim from those call sites — no behavior change.
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

CLUSTER_TOPIC_LABEL = """You are a news analyst. Below are {{count}} examples from a cluster of related records.

Give the cluster a SHORT title (max 8 words) and a 1-sentence summary.

Return ONLY valid JSON: {"title": "...", "summary": "..."}

Examples:
{{examples}}

JSON:"""

TRADING_VERIFY = """You are a fact-checker for a trading article.

SOURCE DATA (the only reference):
{{payload_str}}

CLAIMS TO CHECK:
{{claims_block}}

──────────────────────────────────────────────────────────────
WHAT COUNTS AS A CLAIM

A claim is a statement about the market — a number, a
percentage, a point value, a named condition, a mechanism, a
fact about current conditions. Check these against SOURCE
DATA.

A claim is NOT:

  - A statement of the trader's own process. Any sentence
    containing "I used to...", "I now...", "Now I...", or
    describing the trader's past or present behavior, is a
    process statement, not a market claim. The source data
    does not record the trader's history. Do NOT flag these.

  - A question. Any sentence ending in "?" is a
    self-interrogation, not a claim. Do NOT flag these.

  - First-person narrative. Sentences describing the
    trader's reaction, mood, or memory ("I stared at the
    screen", "my heart rate rose", "I felt tension") are
    narrative. Do NOT flag these.

  - A statement about the article itself ("the piece
    argues", "the reading forced me to rethink"). Do NOT
    flag these.

  - A roadmap sentence. A sentence that announces the
    structure of what follows ("The regime changes three
    core aspects", "There are three observations", "Here
    is what changes") is not a claim about the market.
    Do NOT flag it. Check the sentences that follow it
    instead.

──────────────────────────────────────────────────────────────
WHAT IS SUPPORTED

A claim is SUPPORTED if:

  - It quotes or paraphrases a value from SOURCE DATA.
  - It restates a value with different rounding (0.51 vs
    0.514 is fine).
  - It draws a conclusion that follows directly from one
    or more values in SOURCE DATA.
  - It draws on a mechanism that is named and cited in the
    SOURCE DATA's RESEARCH SOURCES block.
  - It states that a historical comparison is unavailable, or
    that the source provides no regime-duration entry for this
    metric. This is a register-permitted disclaimer, not a
    claim. Do NOT flag it.
  - It states a framing sentence that previews the shape of
    the following sentences (e.g. "The diagnostics confirm
    the regime's characteristics."). This is not a claim
    about the market. Do NOT flag it.
  - The absence of a metric-source paragraph is not a claim.
    The payload carries no metric source; the article is
    correct to omit it. Do NOT flag the omission.

MECHANISM SENTENCES — DO NOT FLAG.

When the SOURCE DATA contains a RESEARCH SOURCES entry that
names a mechanism (for example, common-factor dominance in
a downturn, cited to Longin & Solnik 2001), do NOT flag
sentences of the following kinds. They are all expansions of
the cited mechanism, not new claims:

  - A sentence that restates the mechanism in other words
    ("correlations rise during downturns", "a common factor
    dominates", "returns move in lockstep").
  - A sentence that names a pathway the mechanism travels
    ("hedgers shift from relative-value to broad-based
    positioning", "factor signals lose their independence").
  - A sentence that states a consequence for portfolio
    behaviour ("diversification benefits collapse",
    "systemic risk rises", "the portfolio behaves like a
    single bet").
  - A sentence that compares calm and stressed regimes.

When a matching mechanism is present in RESEARCH SOURCES,
these sentences are supported. Do NOT flag them.

  - It restates a threshold in different units. If SOURCE
    DATA says "Pearson > 0.50 over a rolling 15-minute
    window" and the article says "above 0.50 for three
    consecutive 5-minute bars", the two are the same
    statement. Do NOT flag it.

A claim is UNSUPPORTED if:

  - It names a number, percentage, or dollar figure that
    does not appear in SOURCE DATA.
  - It names a market condition (e.g. VIX-NQ spread, ATR,
    breadth count, a specific bar count) where the
    corresponding value is not in SOURCE DATA — even if it
    hedges with "not listed" or "not provided."
  - It attributes a mechanism to a paper whose citation is
    not in the RESEARCH SOURCES section of SOURCE DATA.
  - It states a historical precedent (a duration, a past
    regime, a prior reading) not present in SOURCE DATA.

──────────────────────────────────────────────────────────────
OUTPUT

Reply only with:
  UNSUPPORTED: <comma-separated list of claim numbers>
or, if every claim is supported:
  UNSUPPORTED: NONE

Do not explain. Do not add context. Do not write anything
else."""

TRADING_SUBBLOCK_GENERATE = """{{user}}

Respond using ONLY this format:
TITLE: <one line>
BODY:
<markdown>"""


PROMPTS = [
    ("cluster.topic_label", "fast", CLUSTER_TOPIC_LABEL, ["count", "examples"]),
    ("trading.verify", "reasoning", TRADING_VERIFY, ["payload_str", "claims_block"]),
    ("trading.subblock_generate", "reasoning", TRADING_SUBBLOCK_GENERATE,
     ["user", "target_words"]),
]


# ─────────────────────────────────────────────────────────────────────
# S.6.1b.1 — base prompts for living enrichment + fixed chain.verify@2
# ─────────────────────────────────────────────────────────────────────

CHAIN_IMPACTS_BASE = """You are a market analyst. Given the events in this chain, identify what could be affected.

EVENTS:
{{events}}

Produce a ranked list of impacts. For each:
- label: a short name (e.g. "LNG prices", "US technology equities", "Credit spreads")
- category: sector | commodity | asset_class | currency | credit | region | company
- direction: up | down | mixed | uncertain
- strength: strong | moderate | weak | unknown
- reasoning: one sentence explaining the linkage

Rules:
- Only list impacts that plausibly follow from the events. Do not invent actors.
- If the direction is unclear, use "uncertain".
- Rank by importance: strongest/most certain first.
- Include 5-12 impacts. No fewer, no more.

Return ONLY valid JSON:
{
  "impacts": [
    {
      "label": "...",
      "category": "...",
      "direction": "...",
      "strength": "...",
      "reasoning": "..."
    }
  ]
}

JSON:"""

CHAIN_SCENARIOS_BASE = """You are a scenario analyst. Given the events and impacts, describe 2-4 conditional futures.

EVENTS:
{{events}}

IMPACTS:
{{impacts}}

For each scenario, produce:
- name: short, evocative (e.g. "Supply shock intensifies", "Mild normalization")
- condition: a concrete conditional statement (If X happens, then Y)
- reasoning: 1-2 sentences explaining the mechanism
- direction: "up" | "down" | "mixed" | "uncertain"

Rules:
- Scenarios must be MUTUALLY EXCLUSIVE and cover the plausible space.
- Use concrete conditions, not vague ones.
- Do NOT invent statistics.
- 2-4 scenarios total.

Return ONLY valid JSON:
{
  "scenarios": [
    {
      "name": "...",
      "condition": "...",
      "reasoning": "...",
      "direction": "up"
    }
  ]
}

JSON:"""

CHAIN_WATCHLIST_BASE = """You are a research editor. Given the events and impacts, suggest what a reader should monitor to track this story over time.

EVENTS:
{{events}}

IMPACTS:
{{impacts}}

For each item:
- label: what to watch (e.g. "Natural gas futures", "VIX", "US energy sector")
- reasoning: one sentence on why this is a leading indicator

Rules:
- 4-8 items total.
- Prefer real, liquid, monitorable indicators over vague categories.

Return ONLY valid JSON:
{
  "watchlist": [
    {"label": "...", "reasoning": "..."}
  ]
}

JSON:"""

CHAIN_VERIFY_V2 = """You are an evidence auditor.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

For each sentence in the body:
1. Classify as: fact | inference | hypothesis | recommendation
2. If fact: cite the event id(s) that support it. If none, mark "unsupported": true.
3. If inference: list the event ids it is inferred FROM.
4. If a claim of type "fact" has empty supported_by_event_ids, set "unsupported": true.

Return ONLY valid JSON in this exact shape:
[
  {
    "claim_text": "...",
    "claim_type": "fact",
    "supported_by_event_ids": ["event_id_1", "event_id_2"],
    "confidence": 0.0,
    "unsupported": false
  }
]

JSON:"""


def seed_s61b_1() -> None:
    """Seed the 3 base prompts + the fixed chain.verify@2."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    prompts = [
        # (name, version, role, template, variables)
        ("chain.impacts",   1, "reasoning", CHAIN_IMPACTS_BASE,   ["events"]),
        ("chain.scenarios", 1, "reasoning", CHAIN_SCENARIOS_BASE, ["events", "impacts"]),
        ("chain.watchlist", 1, "reasoning", CHAIN_WATCHLIST_BASE, ["events", "impacts"]),
        # Bump verify to v2
        ("chain.verify",    2, "fast",      CHAIN_VERIFY_V2,      ["body", "evidence"]),
    ]

    with Session() as session:
        for name, version, role, template, variables in prompts:
            # Deactivate all versions of this name, then activate the target.
            session.execute(
                sql_text("UPDATE prompt_versions SET is_active = false WHERE name = :name"),
                {"name": name},
            )
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:name, :version, :role, :template, CAST(:vars AS jsonb), true)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        variables = EXCLUDED.variables,
                        role = EXCLUDED.role,
                        is_active = true
                """),
                {
                    "name": name, "version": version, "role": role,
                    "template": template, "vars": json.dumps(variables),
                },
            )
        session.commit()
    print(f"  seeded {len(prompts)} prompts")


def seed_trading_verify_v2() -> None:
    """Bump trading.verify to version 2 with the scope-rules prompt."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'trading.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('trading.verify', 2, 'reasoning', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {"t": TRADING_VERIFY, "v": json.dumps(["payload_str", "claims_block"])},
        )
        session.commit()
    print("  seeded trading.verify@2")


def seed_trading_verify_v3() -> None:
    """Bump trading.verify to version 3 — permits inferences from
    a cited mechanism (the implications beat)."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'trading.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('trading.verify', 3, 'reasoning', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {"t": TRADING_VERIFY, "v": json.dumps(["payload_str", "claims_block"])},
        )
        session.commit()
    print("  seeded trading.verify@3")


def seed_trading_verify_v3() -> None:
    """Bump trading.verify to version 3 — permits inferences from
    a cited mechanism (the implications beat)."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'trading.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('trading.verify', 3, 'reasoning', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {"t": TRADING_VERIFY, "v": json.dumps(["payload_str", "claims_block"])},
        )
        session.commit()
    print("  seeded trading.verify@3")


def seed_trading_verify_v4() -> None:
    """Bump trading.verify to version 4 — permits roadmap sentences."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'trading.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('trading.verify', 4, 'reasoning', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {"t": TRADING_VERIFY, "v": json.dumps(["payload_str", "claims_block"])},
        )
        session.commit()
    print("  seeded trading.verify@4")


def seed_trading_verify_v5() -> None:
    """Bump trading.verify to v5 — broader mechanism-inference rule."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'trading.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('trading.verify', 5, 'reasoning', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {"t": TRADING_VERIFY, "v": json.dumps(["payload_str", "claims_block"])},
        )
        session.commit()
    print("  seeded trading.verify@5")


def seed_trading_verify_v6() -> None:
    """Bump trading.verify to v6 — directive mechanism rule."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'trading.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('trading.verify', 6, 'reasoning', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {"t": TRADING_VERIFY, "v": json.dumps(["payload_str", "claims_block"])},
        )
        session.commit()
    print("  seeded trading.verify@6")


def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        for name, role, template, variables in PROMPTS:
            session.execute(
                sql_text("UPDATE prompt_versions SET is_active = false WHERE name = :name"),
                {"name": name},
            )
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:name, 1, :role, :template, CAST(:vars AS jsonb), true)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        variables = EXCLUDED.variables,
                        role = EXCLUDED.role,
                        is_active = true
                """),
                {"name": name, "role": role, "template": template,
                 "vars": json.dumps(variables)},
            )
        session.commit()
        print(f"  seeded {len(PROMPTS)} prompts")

    # S.6.1b.1 — the living-enrichment base prompts + fixed chain.verify@2
    seed_s61b_1()


if __name__ == "__main__":
    seed()
    seed_trading_verify_v2()

# ─────────────────────────────────────────────────────────────────────
# S.6.1b.1b — chain.verify@3 with 20-claim cap
# ─────────────────────────────────────────────────────────────────────

CHAIN_VERIFY_V3 = """You are an evidence auditor.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

For each sentence in the body:
1. Classify as: fact | inference | hypothesis | recommendation
2. If fact: cite the event id(s) that support it. If none, mark "unsupported": true.
3. If inference: list the event ids it is inferred FROM.

Rules:
- Return AT MOST 20 claims. If the body has more sentences than that,
  prioritise in this order and stop once you have 20:
    1. Claims of type "fact" that contain numbers, dates, or named entities
    2. Other claims of type "fact"
    3. Claims of type "inference"
- Skip "recommendation" and "teaching" claims entirely if you have already
  reached 20 with higher-priority claims.
- Every claim object must contain all five fields, even when
  supported_by_event_ids is empty. Do not omit "unsupported".

Return ONLY valid JSON in this exact shape:
[
  {
    "claim_text": "...",
    "claim_type": "fact",
    "supported_by_event_ids": ["event_id_1"],
    "confidence": 0.0,
    "unsupported": false
  }
]

JSON:"""


def seed_s61b_1b() -> None:
    """Activate chain.verify@3 with the 20-claim cap."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        # Deactivate all versions of chain.verify
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.verify', 3, 'fast', :template, CAST(:vars AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {
                "template": CHAIN_VERIFY_V3,
                "vars": json.dumps(["body", "evidence"]),
            },
        )
        session.commit()
    print("  seeded chain.verify@3")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()

# ─────────────────────────────────────────────────────────────────────
# S.6.1b.1b.1 — chain.verify@4 (cap actually enforced in the prompt)
# ─────────────────────────────────────────────────────────────────────

CHAIN_VERIFY_V4 = """You are an evidence auditor.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

TASK: Extract and audit the TOP 20 most important factual claims in the body.

Do NOT audit every sentence — you will run out of output space and the
response will be rejected. A partial audit of 20 claims is better than a
truncated audit of 40.

Prioritise in this exact order, stopping at 20:
  1. Claims of type "fact" that contain specific numbers, dates, or named entities
  2. Other claims of type "fact"
  3. Claims of type "inference"

Skip claims of type "recommendation" and "teaching" entirely.

For each extracted claim:
- Classify as: fact | inference | hypothesis | recommendation
- If fact: list the event id(s) that support it. If none, set "unsupported": true.
- If inference: list the event ids it is inferred FROM.
- Set confidence between 0.0 and 1.0.

HARD LIMIT: Return at most 20 claim objects. If you would produce more,
stop at 20 and omit the rest. Do not exceed 20.

Return ONLY valid JSON in this exact shape:
[
  {
    "claim_text": "...",
    "claim_type": "fact",
    "supported_by_event_ids": ["event_id_1"],
    "confidence": 0.0,
    "unsupported": false
  }
]

JSON:"""


def seed_s61b_1b_1() -> None:
    """Activate chain.verify@4 with the corrected 20-claim cap."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.verify', 4, 'fast', :template, CAST(:vars AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {
                "template": CHAIN_VERIFY_V4,
                "vars": json.dumps(["body", "evidence"]),
            },
        )
        session.commit()
    print("  seeded chain.verify@4")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()
    seed_s61b_1b_1()

# ─────────────────────────────────────────────────────────────────────
# S.6.1b.2 — chain.extract@1 (paste-anything event extraction)
# ─────────────────────────────────────────────────────────────────────

CHAIN_EXTRACT_V1 = """You are a news event extractor. Below is text a user pasted — it may be an
article, a tweet thread, a paragraph, or a partial sentence. Your job is to
extract the discrete news events the text describes.

TEXT:
{{text}}

Rules:
- Extract between 2 and 10 events. Never fewer than 2. If the text contains
  only one clear event, extract that one and one closely-related fact as a
  second event; the caller will enrich with related events from the database.
- An event is: something that happened, at a specific time, involving specific
  actors. Do not extract opinions, general statements, or background.
- Use only the facts in the text. Do NOT invent dates, numbers, or names.
- If a field is not stated, use null (for occurred_at) or an empty array
  (for actors) rather than guessing.
- event_type must be one of: statement | economic | price_move | analyst_call |
  sentiment_shift | report | policy | conflict | other
- status must be one of: fact | claim | rumor
- confidence is a float between 0.0 and 1.0 reflecting how clearly the text
  states the event.

Return ONLY valid JSON in this exact shape:
[
  {
    "headline": "concise, action-oriented (max 150 chars)",
    "summary": "2-4 sentence factual summary (max 2000 chars)",
    "occurred_at": "2026-09-15T12:00:00Z",
    "actors": ["Fed", "Powell"],
    "event_type": "statement",
    "status": "fact",
    "confidence": 0.9
  }
]

JSON:"""


def seed_s61b_2() -> None:
    """Activate chain.extract@1."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.extract'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.extract', 1, 'reasoning', :template, CAST(:vars AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {
                "template": CHAIN_EXTRACT_V1,
                "vars": json.dumps(["text"]),
            },
        )
        session.commit()
    print("  seeded chain.extract@1")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()
    seed_s61b_1b_1()
    seed_s61b_2()

# ─────────────────────────────────────────────────────────────────────
# S.6.1b.2.1 — chain.extract@2 (actor rule corrected)
# ─────────────────────────────────────────────────────────────────────

CHAIN_EXTRACT_V2 = """You are a news event extractor. Below is text a user pasted — it may be an
article, a tweet thread, a paragraph, or a partial sentence. Your job is to
extract the discrete news events the text describes.

TEXT:
{{text}}

Rules:
- Extract between 2 and 10 events. Never fewer than 2. If the text describes
  only one clear event, extract that one and one closely-related fact as a
  second event; the caller will enrich with related events from the database.
- An event is: something that happened, at a specific time, involving specific
  actors. Do not extract opinions, general statements, or background.
- Use only the facts in the text. Do NOT invent dates, numbers, or names.
- actors must contain AT LEAST ONE name. If the text does not name anyone
  specific, use the primary subject of the event (e.g. "S&P 500" for a market
  move, "Treasury" for a yield move, "Federal Reserve" for a policy action).
  Never return an empty actors array — the downstream validator requires at
  least one actor per event.
- If occurred_at is not stated, use null. Do not guess.
- event_type must be one of: statement | economic | price_move | analyst_call |
  sentiment_shift | report | policy | conflict | other
- status must be one of: fact | claim | rumor
- confidence is a float between 0.0 and 1.0 reflecting how clearly the text
  states the event.

Return ONLY valid JSON in this exact shape:
[
  {
    "headline": "concise, action-oriented (max 150 chars)",
    "summary": "2-4 sentence factual summary (max 2000 chars)",
    "occurred_at": "2026-09-15T12:00:00Z",
    "actors": ["Fed", "Powell"],
    "event_type": "statement",
    "status": "fact",
    "confidence": 0.9
  }
]

JSON:"""


def seed_s61b_2_1() -> None:
    """Activate chain.extract@2."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.extract'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.extract', 2, 'reasoning', :template, CAST(:vars AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {
                "template": CHAIN_EXTRACT_V2,
                "vars": json.dumps(["text"]),
            },
        )
        session.commit()
    print("  seeded chain.extract@2")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()
    seed_s61b_1b_1()
    seed_s61b_2()
    seed_s61b_2_1()

# ─────────────────────────────────────────────────────────────────────
# S.6.2.1 — bump chain.order / chain.link to v2 (model role change)
# ─────────────────────────────────────────────────────────────────────

def seed_s621() -> None:
    """Re-activate chain.order and chain.link at @2 with the same text.

    The text didn't change; the version bump marks the boundary where the
    model moved from reasoning to fast, so a future query can compare
    pre/post outputs and success rates.
    """
    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        for name, role in [("chain.order", "fast"), ("chain.link", "fast")]:
            # Fetch the current active template
            row = session.execute(
                sql_text("SELECT template FROM prompt_versions "
                         "WHERE name = :n AND is_active = true LIMIT 1"),
                {"n": name},
            ).first()
            if not row:
                print(f"  ⚠ no active {name} — skipping")
                continue
            tpl = row[0]

            session.execute(
                sql_text("UPDATE prompt_versions SET is_active = false WHERE name = :n"),
                {"n": name},
            )
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:n, 2, :r, :t, '[]'::jsonb, true)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        role = EXCLUDED.role,
                        is_active = true
                """),
                {"n": name, "r": role, "t": tpl},
            )
        session.commit()
    print("  seeded chain.order@2 and chain.link@2 (role=fast)")


if __name__ == "__main__":
    # NOTE: this __main__ block is overwritten by the previous seeding
    # scripts' pattern — kept here so seed_s621() runs when imported.
    pass

# ─────────────────────────────────────────────────────────────────────
# S.6.2.3a — chain.link@3 (explicit UUID-only values)
# ─────────────────────────────────────────────────────────────────────

CHAIN_LINK_V3 = """For each consecutive event pair, determine the relationship.

Link types:
- causes:      A directly caused B
- precedes:    A happened before B (temporal only)
- contradicts: A and B cannot both be fully true
- escalates:   B raises the stakes of A
- contextualizes: B explains or frames A
- responds_to: B is a response to A
- corroborates: B confirms A

PAIRS:
{{pairs}}

Rules:
- `from` and `to` must be the raw event id string, exactly as shown in
  the PAIRS block above. Do NOT add a prefix like "id=" or "uuid:", do
  NOT add quotes beyond the JSON string delimiters, do NOT reformat.
- One link object per pair, in the same order as PAIRS.
- `link_type` must be exactly one of the seven names listed above.
- `rationale` is one sentence, no more.
- `confidence` is a number between 0.0 and 1.0.

Return ONLY valid JSON in this exact shape:
[
  {"from": "<event_id>", "to": "<event_id>", "link_type": "causes", "rationale": "one sentence", "confidence": 0.8}
]

JSON:"""


def seed_s623a() -> None:
    """Activate chain.link@3."""
    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.link'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.link', 3, 'fast', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {"t": CHAIN_LINK_V3, "v": '["pairs"]'},
        )
        session.commit()
    print("  seeded chain.link@3")

# ─────────────────────────────────────────────────────────────────────
# S.6.3.5 — chain.verify@5 (thesis claim_type + unsupported rule)
# ─────────────────────────────────────────────────────────────────────

CHAIN_VERIFY_V5 = """You are an evidence auditor.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

TASK: Extract and audit the TOP 20 most important claims in the body.

Do NOT audit every sentence — you will run out of output space and the
response will be rejected. A partial audit of 20 claims is better than a
truncated audit of 40.

CLAIM CLASSIFICATION

For each extracted claim, choose ONE of:
  - "fact":        a specific, verifiable statement about what happened,
                   at a specific time, involving specific actors. Cites
                   one or more event ids. This is the strongest category.
  - "thesis":      the article's framing, its opening thesis, its closing
                   summary, or a topic sentence that summarises rather
                   than reports. It may be TRUE but it is not a fact —
                   it is framing. Do NOT mark a thesis as "fact".
  - "inference":   a conclusion drawn from one or more facts. May cite
                   event ids. It is not itself an event.
  - "hypothesis":  a speculative forecast about what might happen.
  - "recommendation": advice to the reader.

PRIORITISE in this exact order, stopping at 20:
  1. The article's opening thesis sentence (usually the first sentence
     of the body) — classify it as "thesis".
  2. The article's closing thesis sentence (usually the last sentence)
     — classify it as "thesis".
  3. Claims of type "fact" that contain specific numbers, dates, or
     named entities.
  4. Other claims of type "fact".
  5. Claims of type "inference".

SUPPORT AND UNSUPPORTED

For every claim of type "fact" or "inference":
  - List the event ids that support it in supported_by_event_ids.
  - If the claim has NO supporting event ids, OR its key nouns (people,
    places, numbers, entities) do not appear anywhere in the EVIDENCE
    block, set "unsupported": true.
  - A claim that references something the events don't mention is
    UNSUPPORTED even if it sounds plausible.

A claim of type "thesis" is never "unsupported" — it's framing.

HARD LIMIT: Return at most 20 claim objects. Do not exceed 20.

Return ONLY valid JSON in this exact shape:
[
  {
    "claim_text": "...",
    "claim_type": "fact",
    "supported_by_event_ids": ["event_id_1"],
    "confidence": 0.0,
    "unsupported": false
  }
]

JSON:"""


def seed_s635() -> None:
    """Activate chain.verify@5."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.verify', 5, 'fast', :t, CAST(:v AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {"t": CHAIN_VERIFY_V5, "v": json.dumps(["body", "evidence"])},
        )
        session.commit()
    print("  seeded chain.verify@5")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()
    seed_s61b_1b_1()
    seed_s61b_2()
    seed_s61b_2_1()
    seed_s623a()
    seed_s635()
