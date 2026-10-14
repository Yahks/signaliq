"""Templates for market_analysis personal blog posts.

Each template is one complete shape for one kind of personal blog
post a systematic trader might write. The generate() function
picks one at random per run.

Right now there is one template. The TEMPLATES list is built so
that adding a second is a one-line append - no other code change.

Every template receives two format arguments:
  {source_block} - the labeled reports (dashboard + events)
  {cta_text}     - the CTA sentence, supplied by generate()
"""

import random


_TEMPLATE_OBSERVATION = """You are writing a personal blog post about what you found in the
market this week. You are a systematic trader. You have five
components of information in front of you.

Return your response in exactly this format:

TITLE: <one line - the post title>
BODY:
<the blog post body>

RULES:
- Start with the literal line 'TITLE: ' (nothing before it).
- Then a line with exactly 'BODY:' (nothing else on that line).
- The body is continuous prose. No headings. No bold labels. No
  bullets. No numbered lists. No markdown tables.
- Length: 850 to 1230 words. Aim for 1000.
- First person throughout.

Here are your components. Everything below this line is your
source data. Every number in your piece must come from here.

{source_block}

THE SHAPE - thirteen beats. Do NOT print beat names. Do NOT skip
any.

  1. THE SCREEN - what you pulled from the reports. One or two
     sentences. 'I pulled my five dashboard readings and the last
     24 hours of session data.'

  2. THE LIST - the surface reading. What the headline numbers
     say at first glance. State the dashboard readings and the
     headline market state in a short paragraph. Do NOT enumerate
     them as a bullet list. Write them as prose.

  3. THE TURN - the moment you decided to dig deeper. 'I almost
     stopped here, but when I looked at the segments and the
     persistent movers, things got interesting.'

  4. COMPONENT - the trader's own readings. Write about what the
     readings say, not what they are.

  5. COMPONENT - the segments. Large cap, mid cap, small cap.
     Say what the split means.

  6. COMPONENT - the VIX trajectory. Opened, closed, range. Say
     what the option market is telling you that the tape isn't.

  7. COMPONENT - the asset classes. Equities, rates, commodities,
     credit, currencies. Say which ones are telling the same
     story and which one is the odd one out.

  8. COMPONENT - the persistent movers. Names that appeared
     repeatedly. Say what the story is: sector, theme, or
     individual event.

  9. THE RE-RANK - the same picture, re-evaluated with all five
     components together. 'When I put all five side by side, the
     picture reshuffles.'

 10. THE CONTRADICTION - the sharpest disagreement in the piece,
     named plainly. 'The clearest contradiction is X: the surface
     says A, the underneath says B.'

 11. THE REFRAME - one sentence. The finding, restated as a
     distinction. Not a definition. Not a repeat of beat 10. Do
     NOT use a fixed formula. Do NOT write 'X isn't Y, it's Z'
     unless that shape genuinely fits.

 12. THE CHECKLIST - five questions you ask yourself. Present
     tense. Honest. Questions this article actually raises, not
     generic ones.

 13. CTA - verbatim from the payload.

COMPONENT PARAGRAPHS - beats 4 through 8.

Each paragraph WRITES ABOUT what the component says, not what
the component IS. Every component paragraph must connect back to
the finding. The reader finishes each paragraph having learned
something about the market, not having just read a number. Do
NOT write 'Component 1: the readings showed X.' That is a
report. Write analysis.

NEGATIVE EXAMPLES - do NOT write like this:

  WRONG (report, not analysis):
    'The segments showed large cap down 0.01%, mid cap up 0.12%,
     and small cap down 0.43%.'

  RIGHT (analysis):
    'The index was flat, but small caps were down for the fourth
     session in a row. That is not calm - that is a market where
     the large names are holding the tape up while the small
     ones bleed.'

  WRONG:
    'The persistent movers were AMAT down 6.69%, JNJ down 5.73%,
     LCID down 9.37%, MU down 5.03%, and C down 5.60%.'

  RIGHT:
    'AMAT and JNJ both appeared in the top movers for seven
     sessions in a row. That is not a one-day event - that is two
     names the market keeps punishing. Two of the four biggest
     movers were semiconductors. That is a sector story, not a
     name story.'

WORD BUDGET - 850 to 1230 words. Aim for 1000. If you are under
850, extend the component paragraphs (beats 4-8), not the
opening and not the CTA. If you are over 1230, cut the
component paragraphs.

VOICE:
  - First person.
  - Past tense for what you did ('I pulled', 'I checked').
  - Present tense for what is true ('The VIX sits at 15.27').
  - Short sentences. Contractions. Personal.
  - No 'the data suggests.' No 'it is worth noting.'
  - Never name a paper.

SAFETY:
  - No predictions about price direction.
  - No price targets.
  - No buy/sell signals.
  - No invented numbers. Every figure traces to the components
    above.

End with this CTA:

{cta_text}
"""


TEMPLATES = [
    _TEMPLATE_OBSERVATION,
]


def pick_template() -> str:
    """Return one template at random.

    With one template this returns the same string each time.
    With multiple, random.choice rotates unpredictably so
    consecutive runs don't produce identical articles even when
    the source data is unchanged.
    """
    return random.choice(TEMPLATES)
