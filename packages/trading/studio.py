"""Studio generation — the on-demand chat path.

A user prompt arrives; one LLM call returns either an article
(parsed to title + body, verified, persisted as a trading_post with
source_kind='studio') or a quick answer (returned as-is, no post).

The model self-classifies with a trailing marker:
    KIND: article
    KIND: question

One call, one parse. No separate classification step.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


# ── Studio-specific system directive ──────────────────────────────
# Appended after the composed voice/form/tone/block guide. Tells the
# model to self-classify and produce one of two response shapes.

# Symbols shown in the snapshot line at the top of every
# news-adjacent article. Kept small so the line is scannable.
_SNAPSHOT_SYMBOLS = [
    "^GSPC",   # S&P 500
    "^DJI",    # Dow Jones
    "^IXIC",   # Nasdaq
    "^RUT",    # Russell 2000
    "^VIX",    # VIX
    "GC=F",    # Gold futures
    "CL=F",    # Crude oil futures
    "EURUSD=X",# EUR/USD
    "BTC-USD", # Bitcoin
    "ETH-USD", # Ethereum
]

# Compact display names for the snapshot line.
_SNAPSHOT_LABELS = {
    "^GSPC": "S&P 500",
    "^DJI": "DOW",
    "^IXIC": "NASDAQ",
    "^RUT": "RUSSELL 2K",
    "^VIX": "VIX",
    "GC=F": "GOLD",
    "CL=F": "CRUDE OIL",
    "EURUSD=X": "EUR/USD",
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
}


async def _market_snapshot_line() -> str:
    """Return one line like:

        S&P 500 7,683.69 ▼ -0.8%  DOW 51,481.51 ▼ -0.7%  ...

    Used at the top of every news-adjacent article. Returns an empty
    string if the fetch fails, so the article can still be written
    without the snapshot.
    """
    from .subblocks._yahoo_shared import fetch_quotes, fmt_num

    try:
        quotes = await fetch_quotes(_SNAPSHOT_SYMBOLS)
    except Exception as exc:
        log.warning("studio.snapshot_fetch_failed", error=str(exc)[:200])
        return ""

    by_symbol = {q.get("symbol"): q for q in quotes}
    parts: list[str] = []
    for sym in _SNAPSHOT_SYMBOLS:
        q = by_symbol.get(sym)
        if not q or q.get("price") is None:
            continue
        label = _SNAPSHOT_LABELS.get(sym, sym)
        price = fmt_num(q.get("price"), decimals=2)
        change = q.get("change_pct")
        if change is None:
            parts.append(f"{label} {price}")
            continue
        arrow = "\u25b2" if change >= 0 else "\u25bc"
        parts.append(f"{label} {price} {arrow} {change:+.2f}%")

    return "  ".join(parts)


# Directive appended when the prompt names an entity (news-adjacent).
# Shape adapted from Read Treasury and Switzer: snapshot, headline,
# sourced lead, 2-3 subheads, portfolio implication. Every factual
# claim cites a source.
_NEWS_ADJACENT_DIRECTIVE = """\
This is a news-adjacent piece about a specific event. Write it in
this shape:

1. Open with the market snapshot line already provided at the top
   of the input data. Use it as-is. Do not modify the numbers.

2. Then a headline. State what happened and the consequence, the
   way Switzer or Read Treasury would:
     "Trump rejects Iran's Hormuz proposal as oil futures rise
      in early Asia"
     "Why are Tesla and Nvidia climbing while the S&P slips"
   Do not open the headline with an index percentage unless the
   index move is the story.

3. Then a lead paragraph (2 sentences). What happened, with a
   source. Cite it explicitly: "according to <source>," "<source>
   reported," "per <source>." When the only source is the market
   data itself, say so: "per Yahoo Finance."

4. Then 2-3 subhead sections. Each is a short question or phrase:
     "What did Iran propose?"
     "Why does the Strait of Hormuz matter?"
     "What to watch next"
   Each section is 2-3 short paragraphs.

Premise check:

The user's prompt may assume a direction that the data does not
support. If they ask "why is Tesla up today?" but Tesla is
actually down, the article must lead with the correction — not
answer the false premise and not silently pivot.

When the premise contradicts the data:
  - Open the lead with the correction, plainly:
    "Tesla is not up today — it's down 3.94% to $357.45, per
    Yahoo Finance."
  - Then write the article about the actual move. What is Tesla
    doing today? What does the data show?
  - Do not speculate on causes for a move that did not happen.

When the premise matches the data:
  - Answer the user's question directly.

Throughout:

- Cite every factual claim. "Tesla closed at $357.45, up 2.49%,
  per Yahoo Finance" not "Tesla had a strong day."
- If a "Recent headlines" block is present in the input, USE IT.
  Draw at least one subhead section from those headlines and cite
  the source by name: "Reuters reported that...", "per CNBC...",
  "according to Bloomberg...". When a headline explains the cause
  of a move, that's the "why" — lead the relevant section with it.
- Distinguish what was said from what is true. "Trump said he
  rejected the proposal" — not "the rejection caused oil to rise."
- Use cautious causal language: "followed," "as," "amid" rather
  than "because of," "due to."
- Do not speculate on causes when you have neither price data nor
  headlines. Describe the move; do not explain it.
- Short paragraphs. Plain English. Assume the reader follows the
  market but is not a professional.
"""


_TRACK_RETAIL = """\
This is a retail-track article. Write for ordinary retail investors:
mainstream stock market news in plain English. Define technical terms
in one short phrase when you use them. The reader follows the market
but is not a professional. The goal is that a reader can understand
and share what you wrote.
"""

_TRACK_QUANT = """\
This is a quant-track article. Write for quantitative traders. Use
case studies with mechanisms: show how the trade or pattern works,
step by step, with the reasoning behind each step. Assume the reader
knows what beta, duration, and factor exposure mean. The goal is
that a professional finds the piece rigorous.
"""


def _track_directive(track: str | None) -> str:
    """Return the prose directive for a track. Empty string if none."""
    if track == "quant":
        return _TRACK_QUANT
    if track == "retail":
        return _TRACK_RETAIL
    return ""


_STUDIO_DIRECTIVE = """\
You are responding to a user request inside a trading chat interface.

Write an ARTICLE when the request names a specific entity — a
ticker, a company, a market event, a named person, a sector — and
asks for an explanation, analysis, or write-up. These are article
requests even when phrased as questions, because the answer is a
report on a specific event. Examples:
  "Why is Tesla down today?"
  "What happened with AAPL?"
  "Explain the Fed's decision yesterday"
  "Write about the recent AI stock rally"
  "How did NVDA's earnings move the sector?"

Write a QUESTION-style response (2-4 sentences) only when the
request is purely conceptual and names no specific entity.
Examples:
  "How does margin trading work?"
  "What is a K-line chart?"
  "Define beta"
  "Why do interest rates affect stocks?"

Format:

If writing an ARTICLE:
  TITLE: <the article title>
  BODY:
  <the article body in markdown>

If answering a QUESTION:
  <2-4 sentences directly>

In all cases, end your response with a single line, either:
  KIND: article
  or
  KIND: question
"""


async def generate_article_content(
    session: AsyncSession,
    *,
    org_id: str,
    prompt: str,
    form: str | None = None,
    tone: str | None = None,
    track: str | None = None,
    persist: bool = True,
    subblock_slug: str = "studio",
    image_url: str | None = None,
    register: str | None = None,
    tier: str | None = None,
) -> dict:
    """Generate an article or question.

    Returns a dict with kind, content, post_id, tickers,
    source_payload. S.8.11.7 adds image_url — when set, the pipeline
    reads the image, runs vision analysis, and injects the analysis
    into the article prompt.
    """
    from packages.ai.router import complete

    from .editorial import compose_system_prompt, get_org_voice
    from .forms import DEFAULT_FORM, DEFAULT_TONE
    from .subblocks.base import TradingPost, load_subblock, persist_post, verify_post

    subblock = await load_subblock(session, slug=subblock_slug, org_id=org_id)
    if not subblock:
        raise RuntimeError(
            f"{subblock_slug} subblock row missing — run alembic upgrade head"
        )

    form = form or DEFAULT_FORM
    tone = tone or DEFAULT_TONE
    voice = await get_org_voice(session, org_id)

    # Extract tickers, fetch today's quotes for context.
    from .studio_entities import extract_tickers
    from .subblocks._yahoo_shared import fetch_quotes, fmt_pct

    tickers = extract_tickers(prompt)
    market_context_md = ""
    if tickers:
        try:
            quotes = await fetch_quotes(tickers)
            rows = [
                f"| {q['symbol']} | {q.get('name') or q['symbol']} | "
                f"{q.get('price')} | {fmt_pct(q.get('change_pct'))} |"
                for q in quotes
                if q.get("price") is not None
            ]
            if rows:
                market_context_md = (
                    "\n\nToday's market data for the tickers mentioned:\n\n"
                    "| Symbol | Name | Price | Change |\n"
                    "|--------|------|-------|--------|\n"
                    + "\n".join(rows)
                    + "\n\nIf the user's question is about *why* the move "
                    "happened and you only have price data, describe the "
                    "move (size, direction, sector context) without "
                    "speculating on the cause."
                )
        except Exception as exc:
            log.warning("studio.quote_fetch_failed", error=str(exc)[:200])

    # Fetch news headlines for the primary ticker.
    headlines_md = ""
    if tickers:
        try:
            from .news import fetch_news, render_headlines_md
            from .studio_entities import COMPANY_TO_TICKER
            primary = tickers[0]
            query = primary
            for name, sym in COMPANY_TO_TICKER.items():
                if sym == primary:
                    query = name
                    break
            headlines = await fetch_news(query, ticker=primary)
            headlines_md = render_headlines_md(headlines)
        except Exception as exc:
            log.warning("studio.news_failed", error=str(exc)[:200])

    # S.8.11.7 — analyze an uploaded chart image via the vision role.
    chart_analysis: str | None = None
    if image_url:
        try:
            from .chart_vision import analyze_chart
            from pathlib import Path as _Path
            name = image_url.rsplit("/", 1)[-1]
            img_path = _Path("apps/api/static/uploads") / name
            if img_path.exists():
                chart_analysis = await analyze_chart(img_path.read_bytes())
                log.info(
                    "studio.chart_analysis",
                    image_url=image_url,
                    chars=len(chart_analysis) if chart_analysis else 0,
                )
            else:
                log.warning("studio.chart_image_missing", image_url=image_url)
        except Exception as exc:
            log.warning(
                "studio.chart_analysis_failed",
                image_url=image_url,
                error=str(exc)[:200],
            )

    # S.8.6 — snapshot line. Image alone also counts as news-adjacent.
    snapshot_line = ""
    is_news_adjacent = bool(tickers) or bool(image_url)
    if tickers:
        snapshot_line = await _market_snapshot_line()

    # Compose the system prompt.
    track_directive = _track_directive(track)
    system_parts = [
        compose_system_prompt(
            voice,
            subblock.get("style_guide"),
            "You write trading content for a user request.",
            form=form,
            tone=tone,
            slug=subblock_slug,
            register=register,
            tier=tier,
        ),
    ]
    # S.8.51 — render the register target so the model has a
    # length directive when a register is set. The subblock
    # path does this in call_llm_json_verified; the Studio
    # path needs it explicitly.
    if register:
        # S.8.57 — use target_words_for so the SUBBLOCK_TARGET_WORDS
        # override wins. REGISTER_TARGET_WORDS.get(register) ignored
        # the subblock override, so movers saw 700 instead of 1000.
        from .registers import target_words_for
        from .subblocks.base import _MIN_WORDS, _MAX_WORDS
        _tw = target_words_for(subblock_slug, register, tier)
        if _tw:
            system_parts.append(
                f"- Target length: {_tw} words "
                f"(acceptable range {_MIN_WORDS}-{_MAX_WORDS} words).\n"
            )
    if track_directive:
        system_parts.append(track_directive)
    if is_news_adjacent:
        system_parts.append(_NEWS_ADJACENT_DIRECTIVE)
    system_parts.append(_STUDIO_DIRECTIVE)
    system = "\n\n".join(system_parts)

    # Compose the user prompt.
    user_parts = []
    if snapshot_line:
        user_parts.append(
            "Market snapshot line (use verbatim at the top):\n" + snapshot_line
        )
    if chart_analysis:
        user_parts.append(
            "CHART ANALYSIS (from the attached chart image — treat as "
            "source data; every factual claim about the chart must trace "
            "to this block):\n" + chart_analysis
        )
    if headlines_md:
        user_parts.append(headlines_md)
    user_parts.append(prompt + market_context_md)
    user = "\n\n".join(user_parts)

    log.info(
        "studio.generate_start",
        org_id=org_id[:8],
        prompt_len=len(prompt),
        form=form,
        tone=tone,
        track=track,
        slug=subblock_slug,
        has_image=bool(image_url),
    )

    raw = await complete(
        role="reasoning",
        system=system,
        user=user,
        org_id=org_id,
        prompt_version_id=None,
        purpose=f"trading.{subblock_slug}",
        temperature=0.3,
        max_tokens=9000,
        json_mode=False,
    )
    raw = (raw or "").strip()

    kind, title, body, content = _parse_studio_response(raw)

    log.info(
        "studio.generate_done",
        org_id=org_id[:8],
        kind=kind,
        text_len=len(content),
    )

    source_payload: dict = {
        "user_prompt": prompt,
        "tickers": tickers,
        "market_context": market_context_md,
        "snapshot_line": snapshot_line,
        "news_adjacent": is_news_adjacent,
        "headlines_md": headlines_md,
        "image_url": image_url,
        "chart_analysis": chart_analysis,
        "form": form,
        "tone": tone,
        "voice": voice,
        "track": track,
        "generator": subblock_slug,
    }
    if register:
        source_payload["register"] = register
    if tier:
        source_payload["tier"] = tier

    result: dict = {
        "kind": kind,
        "content": content,
        "post_id": None,
        "tickers": tickers,
        "source_payload": source_payload,
    }

    if kind != "article":
        return result

    if not persist:
        return result

    # S.8.10 — attach a price chart image for the primary ticker.
    if tickers and is_news_adjacent:
        try:
            from .chart import render_price_chart
            chart_url = await render_price_chart(tickers[0])
            if chart_url:
                chart_md = f"![{tickers[0]} 30-day price chart]({chart_url})"
                lines = body.split("\n", 1)
                if len(lines) == 2:
                    body = lines[0] + "\n\n" + chart_md + "\n\n" + lines[1]
                else:
                    body = chart_md + "\n\n" + body
        except Exception as exc:
            log.warning("studio.chart_failed", error=str(exc)[:200])

    # S.8.12 — humanize the structured draft before verify + persist.
    # The structured body goes into source_payload["structured_draft"]
    # for the audit trail; the humanized version is what persists.
    # Verify runs on the humanized version so that the fail-safe
    # applies to what ships.
    structured_body = body
    humanized: str | None = None
    try:
        from .humanize import humanize
        humanized = await humanize(
            session,
            org_id=org_id,
            structured_body=structured_body,
            voice=voice,
        )
    except Exception as exc:
        log.warning("studio.humanize_failed", error=str(exc)[:200])

    if humanized:
        source_payload["structured_draft"] = structured_body
        body = humanized
        # S.8.12 — the API response must reflect the humanized prose.
        # `content` and `result["content"]` were both built from the
        # structured draft. Update both so the browser and the
        # persistence path see prose, not markdown.
        content = f"{title}\n\n{body}" if title else body
        result["content"] = content
        log.info(
            "studio.humanize_applied",
            structured_words=len(structured_body.split()),
            humanized_words=len(humanized.split()),
        )
    else:
        log.info("studio.humanize_skipped")

    post = TradingPost(title=title, body=body, word_count=len(body.split()))
    post.form = form
    post.tone = tone
    post.voice = voice

    # S.8.57 — enforce the register's length band on the Studio
    # path. The subblock path has this check in
    # _call_llm_json_verified_impl; the Studio path was bypassing
    # it, so 390-word movers articles published against a 630 floor.
    from .registers import target_words_for as _tw_for
    from .subblocks.base import _MIN_WORDS as _FLOOR, _MAX_WORDS as _CEIL
    _target = _tw_for(subblock_slug, register, tier)
    _floor = _FLOOR
    _ceiling = _CEIL
    if post.word_count < _floor or post.word_count > _ceiling:
        log.info(
            "studio.length_rejected",
            words=post.word_count,
            floor=_floor,
            target=_target,
            ceiling=_ceiling,
            slug=subblock_slug,
        )
        post.verification_ok = False
        post.unsupported_claims = (list(post.unsupported_claims or []) + [
            f"__length__: {post.word_count} words "
            f"(floor {_floor}, target {_target}, ceiling {_ceiling})"
        ])

    try:
        ok, unsupported = await verify_post(
            session, org_id=org_id, post=post, source_payload=source_payload,
        )
        post.verification_ok = ok
        if not ok:
            post.unsupported_claims = unsupported
    except Exception as exc:
        log.warning("studio.verify_failed", error=str(exc)[:200])
        post.verification_ok = False
        post.unsupported_claims = [f"verification error: {exc}"]

    post_id = await persist_post(
        session, org_id=org_id, subblock=subblock, post=post,
        source_payload=source_payload,
        source_urls=[],
        symbols=tickers,
    )
    result["post_id"] = post_id
    return result

async def generate_studio_response(
    session: AsyncSession,
    *,
    org_id: str,
    user_id: str,
    prompt: str,
    form: str | None = None,
    tone: str | None = None,
    track: str | None = None,
    image_url: str | None = None,
) -> tuple[str, str, str | None]:
    """Studio chat entry point.

    Calls generate_article_content; the studio_messages row is
    inserted by the API handler, not here.
    """
    result = await generate_article_content(
        session,
        org_id=org_id,
        prompt=prompt,
        form=form,
        tone=tone,
        track=track,
        persist=True,
        subblock_slug="studio",
        image_url=image_url,
    )
    return (result["kind"], result["content"], result["post_id"])
def _parse_studio_response(raw: str) -> tuple[str, str, str, str]:
    """Return (kind, title, body, content).

    kind:    'article' | 'question'
    title:   article title, or '' for questions
    body:    article body, or '' for questions
    content: the message text the user sees (title + body for
             articles, the question text for questions)

    Title conventions, in priority order:
      1. TITLE: <text>      — the format we ask for
      2. ## <text>          — a markdown heading
      3. bare opening line

    KIND marker conventions:
      KIND: article         — the format we ask for
      **KIND:** article     — markdown emphasis
      KIND: ARTICLE         — uppercase

    If no KIND marker is found, the kind is inferred from the
    content: TITLE+/-BODY markers or a long structured response
    is treated as an article; anything else as a question.
    """
    import re as _re

    if not raw:
        return ("question", "", "", "")

    text = raw.strip()

    def _strip_md(line: str) -> str:
        """Remove leading #, *, _ and trailing * or _ from a line."""
        s = line.strip()
        s = _re.sub(r"^#+\s*", "", s)
        s = _re.sub(r"^\*+\s*", "", s)
        s = _re.sub(r"^_+\s*", "", s)
        s = _re.sub(r"\*+\s*$", "", s)
        s = _re.sub(r"_+\s*$", "", s)
        return s.strip()

    # 1. Look for the trailing KIND marker. Strip markdown from the
    #    line before matching, so `**KIND:** article` resolves.
    kind: str | None = None
    lines = text.splitlines()
    kind_idx = None
    for i in range(len(lines) - 1, -1, -1):
        line = _strip_md(lines[i]).upper()
        if line.startswith("KIND:"):
            value = line.split(":", 1)[1].strip()
            if value.startswith("ARTICLE"):
                kind = "article"
            elif value.startswith("QUESTION"):
                kind = "question"
            kind_idx = i
            break
    if kind_idx is not None:
        lines = lines[:kind_idx] + lines[kind_idx + 1:]

    # 2. If no marker, infer from content shape.
    if kind is None:
        joined = "\n".join(lines)
        joined_upper = joined.upper()
        has_title = (
            "TITLE:" in joined_upper
            or "BODY:" in joined_upper
        )
        # Long response with structure → article.
        if has_title or len(joined) > 800:
            kind = "article"
        else:
            kind = "question"

    # 3. Question path — return the visible text as-is.
    if kind == "question":
        visible = "\n".join(lines).strip()
        return ("question", "", "", visible)

    # 4. Article path. Find the title by trying each convention.
    title = ""
    title_idx = None
    body_marker_idx = None
    body = ""

    for i, line in enumerate(lines):
        stripped = _strip_md(line)
        upper = stripped.upper()
        if not stripped:
            continue

        if title_idx is None and body_marker_idx is None:
            if upper.startswith("TITLE:"):
                title = stripped.split(":", 1)[1].strip()
                title = _strip_md(title)
                title_idx = i
                continue
            title = stripped
            title_idx = i
            continue

        if body_marker_idx is None and upper.startswith("BODY:"):
            after = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            body_marker_idx = i
            if after:
                body = after + "\n" + "\n".join(lines[i + 1:]).strip()
                body = body.strip()
                content = f"## {title}\n\n{body}" if title else body
                return ("article", title, body, content)
            break

    # 5. Collect the body.
    if body_marker_idx is not None:
        body = "\n".join(lines[body_marker_idx + 1:]).strip()
    elif title_idx is not None:
        body = "\n".join(lines[title_idx + 1:]).strip()
    else:
        body = "\n".join(lines).strip()

    if not body:
        body = ""
    if not title:
        first = (body.splitlines() or ["Untitled"])[0]
        title = _strip_md(first)[:120]

    content = f"## {title}\n\n{body}" if title else body
    return ("article", title, body, content)
