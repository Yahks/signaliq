"""Seed the initial prompts. Idempotent — safe to run repeatedly."""
from __future__ import annotations

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import sessionmaker

from packages.core.config import get_settings

PROMPTS: list[dict] = [
    {
        "name": "enrich.combined",
        "version": 1,
        "role": "fast",
        "template": (
            "You are a text analysis engine.\n"
            "Analyze the following text and return ONLY valid JSON matching this schema:\n"
            "{\n"
            '  "sentiment": {"label": "positive|negative|neutral|mixed", "score": -1.0 to 1.0, "confidence": 0.0 to 1.0},\n'
            '  "emotions": {"anger": 0.0 to 1.0, "frustration": 0.0 to 1.0, "trust": 0.0 to 1.0, "fear": 0.0 to 1.0, "joy": 0.0 to 1.0},\n'
            '  "intent": {"label": "complaint|praise|question|request|statement", "confidence": 0.0 to 1.0},\n'
            '  "entities": [{"text": "...", "type": "person|org|location|product|other", "confidence": 0.0 to 1.0}],\n'
            '  "topics": ["short topic label", "..."],\n'
            '  "pain_point_signals": ["..."],\n'
            '  "feature_request_signals": ["..."]\n'
            "}\n"
            "\n"
            "Rules:\n"
            "- Return ONLY the JSON, no other text.\n"
            "- Use empty arrays/objects if nothing found.\n"
            "- Confidence is your certainty (0.0 to 1.0).\n"
            "\n"
            "TEXT:\n"
            "{{text}}\n"
            "\n"
            "JSON:"
        ),
        "variables": ["text"],
        "is_active": True,
    },
]


def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        for p in PROMPTS:
            # Set old active versions to inactive
            session.execute(
                sql_text(
                    "UPDATE prompt_versions SET is_active = false "
                    "WHERE name = :name AND is_active = true"
                ),
                {"name": p["name"]},
            )
            # Insert (idempotent on name+version)
            session.execute(
                sql_text("""
                    INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                    VALUES (:name, :version, :role, :template, :variables, :is_active)
                    ON CONFLICT (name, version) DO UPDATE
                    SET template = EXCLUDED.template,
                        role = EXCLUDED.role,
                        variables = EXCLUDED.variables,
                        is_active = EXCLUDED.is_active
                """),
                {
                    "name": p["name"],
                    "version": p["version"],
                    "role": p["role"],
                    "template": p["template"],
                    "variables": __import__("json").dumps(p["variables"]),
                    "is_active": p["is_active"],
                },
            )
        session.commit()
    print(f"Seeded {len(PROMPTS)} prompt(s)")


if __name__ == "__main__":
    seed()
