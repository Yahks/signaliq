# Studio — design

## What Studio is

Studio is a chat interface inside the trading block where a user
writes a free-form prompt and receives a professional trading
article. It is the on-demand counterpart to the block's scheduled
subblocks: where `market_analysis` fires on a cron and writes about
whatever the day's data shows, Studio responds to what a specific
user asks for.

Examples of prompts Studio handles:

  - "Write an article on today's move in NVDA"
  - "Analyse this chart" (image)
  - "Rewrite this article for a retail audience"
  - "What failed in this trade setup?"

Studio sits under the trading block. If trading is not enabled for an
org, Studio is not available. It has its own sidebar entry, its own
routes, and its own tables — but conceptually it is a trading
feature, gated by the same block check.

## Decisions made

| Decision | Choice | Why |
|---|---|---|
| Knowledge layer | RAG (scrape, embed, retrieve) | Fine-tuning is a 6-12 month project; RAG delivers most of the same benefit in weeks. |
| Own model training | Deferred | Not in scope for any current phase. Revisit once RAG has collected a corpus of approved articles. |
| Studio placement | Under trading | Gated by `is_block_enabled(org, "trading")`. If trading is off, Studio is off. |
| Chat style | One-shot | Each message is independent. Conversation history is stored but not sent to the model. Threading is a later phase. |
| Session model | One session per user | Simplest shape. Named multi-session threads are a later phase. |
| Article persistence | Articles become `trading_posts` | The review, trash, dedup, and publish flows already exist. No new content tables. |
| Chat history | Separate table `studio_messages` | Chat noise does not pollute the drafts page. Only article-length responses create posts. |
| Message to post link | `trading_post_id` FK on the message | The full text lives once, in `trading_posts`. Chat rows point at it. |
| Vision model | Gemini 2.0 Flash | Free tier, cheap, has vision, has long context. |
| Text model | Existing pool (gpt-oss-120b via Groq, Gemini fallback) | No new text provider needed for Phase 1. |

## Deferred decisions

Named so future work has a home; not designed yet.

- **Fine-tuning a model.** Deferred until RAG has run for long enough
  to produce a corpus of approved articles. If done, this is Phase 8
  or later.
- **Multiple named sessions.** One session per user for Phase 1.
- **Conversation threading.** One-shot for Phase 1.
- **Crypto-specific chart vision.** Gemini Flash handles most charts.
  Crypto-specific indicators (on-chain, funding) may need a
  specialised prompt; not a separate model.
- **SEO keyword source.** Where do ranking keywords come from? Not
  decided. Phase 6 open question.
- **Stock/crypto as user setting vs session setting.** Not decided.
  Phase 7 open question.
- **Retail positioning: voice preset or directive layer.** The
  "mainstream retail investor" framing is either a sixth voice or a
  content-positioning directive that sits above voice. Not decided.

## The two-model pipeline

A user prompt with an image goes through two models:
user message (prompt + optional image)
|
+-- if image:
| |
| +-- Gemini Flash (vision)
| | input: image bytes
| | output: structured chart analysis (text)
| | e.g. "trend=up, key_support=180.5,
| | key_resistance=195.2, pattern=ascending_triangle"
| |
| +-- analysis is passed to the text model as part of context
|
+-- text model (gpt-oss-120b, Gemini fallback)
input: user prompt + market context + chart analysis
output: article (title + body)
The vision model never writes prose. It returns structured facts.
The text model never sees the image. It sees the vision model's
output as source data, exactly like a subblock sees Yahoo quotes.

This keeps verification workable: the article's chart claims are
checked against the vision model's structured output, which is
in `source_payload` alongside the market context.

## Data model — Phase 1

One new table.

```sql
CREATE TABLE studio_messages (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role               varchar(16) NOT NULL,       -- 'user' | 'assistant'
    content            text NOT NULL,              -- the message text
    prompt_kind        varchar(32),                -- NULL on user rows
                                                   -- 'article' | 'quick_answer'
                                                   -- on assistant rows
    trading_post_id    uuid REFERENCES trading_posts(id) ON DELETE SET NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_studio_messages_org_user_time
    ON studio_messages (org_id, user_id, created_at DESC);Phase 1 columns only. attachments, embeddings, citations
come in later phases.

Why one session per user: the Phase 1 UI is a single scrollback.
Multiple sessions would need a studio_sessions table and a
session_id FK; deferred.

Why prompt_kind on assistant rows: the model classifies its
own response as article (a full post) or quick_answer (a short
reply). Only article responses create a trading_posts row.
When prompt_kind='article', trading_post_id is populated;
when 'quick_answer', it is NULL.

API surface — Phase 1
Three endpoints.POST   /api/studio/message
       body: { "prompt": "..." }
       returns: {
         "message_id": "...",
         "kind": "article" | "quick_answer",
         "content": "...",                    (the assistant text)
         "trading_post_id": "..." | null
       }

GET    /api/studio/messages?limit=50&before=<iso>
       returns: list of past messages (user + assistant) for display

DELETE /api/studio/messages/{id}
       deletes a message and, if it produced a post, soft-deletes
       the post tooNo POST /api/studio/upload yet. No image handling. That is Phase 2.

## UI plan

Sidebar: a "Chat" entry under the trading section. It links to
`/trading/chat`.

Page `/trading/chat`:

  - A scrollback of the session.
  - A single text input at the bottom with a Send button.
  - A small settings strip above the input: voice (org-level), form
    override, tone override. These default to the org's current
    settings but the user can change them per message.
  - Each assistant message: the text, plus (if `prompt_kind='article'`)
    a "Saved as draft" badge linking to the draft.

The inline settings are the "everything the user needs to fill"
from the original request — but limited to what actually varies per
message (voice is per-org, set once; form and tone can be overridden
per prompt if the user wants).

## The generation path

When a prompt arrives:
Block gate: is_block_enabled(org, "trading") — same as subblocks.

Budget gate: can_generate(user) — same as subblocks.

Rate limit: check_rate_limit(namespace="org",
route="studio-message",
limit=...) — new route key.

Build source_payload:
{
"user_prompt": "...",
"market_context": { <if we infer a ticker, fetch a small quote block> },
"form": <chosen form>,
"tone": <chosen tone>,
"voice": <org voice>
}

Compose the system prompt with compose_system_prompt():
voice + form + tone + block style_guide + studio fallback.
The studio fallback says:
"You are a trading writer responding to a user request.
If the user asks for an article, produce one. If the user
asks a quick question, answer briefly. Classify your own
response as 'article' or 'quick_answer' at the end using the
marker ARTICLE: or QUICK_ANSWER:."

Call the reasoning LLM via call_llm_json_verified.
The verifier checks the response against source_payload —
the prompt, the market context, and (Phase 2) the chart analysis.

If the response is classified as ARTICLE:
persist_post(..., source_kind="studio")
-> sets status='draft' if verification failed, 'published' otherwise
-> returns the post id
If classified as QUICK_ANSWER:
no post. Just the message row.

Insert into studio_messages:

one row for the user prompt

one row for the assistant response, with prompt_kind and
trading_post_id (or NULL)
Steps 1-3 (gates) are already implemented; they just need a new
route key for the rate limit.

Steps 4-6 reuse `compose_system_prompt`, `call_llm_json_verified`,
and `persist_post` from the trading subblock machinery.

Steps 7-8 are new.

## Verification

For a Studio article, "source data" is:

  - the user's prompt (verbatim)
  - the market context we injected (quote block if a ticker was inferred)
  - the chart analysis (Phase 2, from Gemini Flash)

The existing verifier checks each claim against these. Claims that
trace to one of the three sources pass. Claims that don't — e.g. a
statement about a company event not in the context — get flagged and
the post is written as a draft.

The `_VERIFICATION_INCOMPLETE` sentinel path also applies: if the
verifier can't complete, the post is written as a draft.

## Phase 1 scope

In:

  - `studio_messages` table
  - `POST /api/studio/message`, `GET /api/studio/messages`, `DELETE`
  - sidebar entry
  - `/trading/chat` page with scrollback + input
  - prompt -> LLM -> article or quick answer
  - article responses become `trading_posts` with `source_kind='studio'`
  - inline voice/form/tone settings

Out (later phases):

  - image upload (Phase 2)
  - article/document upload (Phase 3)
  - scrapers (Phase 4)
  - RAG (Phase 5)
  - SEO (Phase 6)
  - stock/crypto split (Phase 7)
  - fine-tuning (Phase 8+, deferred)

## Phases 2-7 (outline only)

**Phase 2 — image upload.** Add `POST /api/studio/upload`. Store
images in MinIO (already in the compose stack). Call Gemini Flash
with the image and a "chart analysis" system prompt. The output
becomes `source_payload["chart_analysis"]`. The image bytes are
not stored permanently; only the analysis text is.

**Phase 3 — article/document upload.** Text paste or file upload.
Long-context input passed as-is into the prompt. Token counting
before the call so we can reject inputs above a limit.

**Phase 4 — scrapers.** Reddit, Quora, LinkedIn, trading sites.
Each a source module in `packages/trading/sources/`. Fetch and
store raw in a `studio_sources` table. No retrieval yet.

**Phase 5 — RAG.** Embed the scraped content (Gemini's embedding
model, or a local one). Store vectors in pgvector (already enabled
in the DB image). On a Studio message, embed the prompt, retrieve
top-K passages, inject them into `source_payload["context"]`.

**Phase 6 — SEO.** Keyword extraction from the prompt and the
retrieved context. Inject a "target keywords" list into the
system prompt. Optionally generate meta description and slug.

**Phase 7 — stock/crypto split.** Two checkboxes in the Studio
settings strip. `source_payload["markets"] = ["stock", "crypto"]`
or one of each. The system prompt and the market context branch on
this field.

## Open questions

  - Where do SEO keywords come from? (Phase 6)
  - Are `markets` and the user's other Studio preferences stored
    per-org, per-user, or per-message? (Phase 7)
  - Does the retail-investor positioning become a sixth voice, or
    a directive that sits above voice for all Studio prompts?
  - What is the token budget per Studio message? The reasoning model
    is 8000 TPM. A long prompt + long context + a 900-word article
    is roughly 3000-4000 tokens total. Within budget, but a user
    who sends ten messages in a minute will hit the cap.
  - Should Studio articles be excluded from the trading block's
    dedup check? The dedup compares title+body within 24h. Two
    prompts that produce very similar articles would be blocked.
    For Studio, a unique prompt should produce a unique article
    even if it resembles an earlier one.

## What is not in this document

  - The vision model's exact prompt (Phase 2).
  - The scraper targets (Phase 4).
  - The embedding model choice (Phase 5).
  - The SEO keyword API or list (Phase 6).
  - Any crypto-specific analysis path (Phase 7).

These are each a design decision for their own phase.
