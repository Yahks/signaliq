"""Chart analysis via the vision role.

Given raw image bytes of a trading chart, returns a structured
text analysis (trend, key levels, patterns, indicators if visible)
suitable for injection into the article generation prompt.

The vision model is chosen by the router: any credential with
'vision' in its roles. Today that's a Gemini key. The system
supports multiple vision credentials and rotates on 429, same as
every other role.

If no vision credential is configured, or the call fails, the
caller gets None. The article pipeline treats chart analysis as
optional — the article still writes without it.
"""
from __future__ import annotations

from packages.core.logging import get_logger

log = get_logger(__name__)


# System directive for the vision model. Asks for structured text
# rather than prose, because the output feeds the article-writer
# LLM as source data.
_SYSTEM = """\
You are a chart analyst. You will see a price chart (equity,
crypto, or index). Describe what the chart shows in structured
text. Do NOT write an article — produce a fact list that another
model will use as source data.

Structure your output as:

  INSTRUMENT: <ticker / symbol if visible, or "unknown">
  TIMEFRAME: <intraday / daily / weekly / unknown>
  TREND: <up / down / sideways> over the visible window
  RANGE: <low> to <high>, current at <price>
  KEY LEVELS: <support and resistance levels, 2-4 values>
  PATTERN: <named pattern if any: ascending triangle, head and
            shoulders, range, breakdown, etc.>
  INDICATORS: <if visible: RSI, MACD, moving averages, volume>

RULES:
- Only describe what you can see. If the chart is unclear or a
  field is not visible, write "unknown" for that field.
- Do not invent prices, tickers, or dates not on the chart.
- Do not speculate on causes or news.
"""


# User prompt. Short — the image carries the content.
_USER = (
    "Describe the chart in the attached image using the structure "
    "above. Output only the structured text, no preamble."
)


async def analyze_chart(image_bytes: bytes) -> str | None:
    """Return a structured chart analysis, or None if unavailable.

    Failures (no vision credential, provider error, empty response)
    return None. The caller decides whether to continue without
    chart context.
    """
    if not image_bytes:
        return None

    from packages.ai.router import complete
    from packages.core.context import current_org_id

    org_id = current_org_id.get()
    if not org_id:
        log.warning("chart_vision.no_org_context")
        return None

    try:
        text = await complete(
            role="vision",
            system=_SYSTEM,
            user=_USER,
            purpose="trading.chart_vision",
            temperature=0.1,
            max_tokens=800,
            json_mode=False,
            use_cache=False,  # ignored for vision anyway; explicit for clarity
            images=[image_bytes],
        )
    except Exception as exc:
        # Common cases: NoCredentialsError (no vision key),
        # ProviderError (bad key, model unavailable). Log and
        # return None — the article pipeline continues without
        # chart context.
        log.warning(
            "chart_vision.failed",
            error=str(exc)[:200],
            error_type=type(exc).__name__,
        )
        return None

    text = (text or "").strip()
    if not text:
        log.warning("chart_vision.empty_response")
        return None

    log.info("chart_vision.ok", chars=len(text))
    return text
