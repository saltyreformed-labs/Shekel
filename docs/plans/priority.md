# The priority rubric

**One copy of how a new card's Priority is suggested.** A session filing a step at the top level or
a question suggests a Priority for it and gives one sentence of reason
(`quill file step|question ... --suggest PRIORITY --why SENTENCE`). The card holds no Priority until
the developer sets one from the board's Inbox view, the open steps and questions holding none,
newest first (rulings plan#2800, plan#3224, plan#3231). The suggestion follows this rubric, which
plan#2797's triage of the open board drafted and the developer's rulings on it left as it stands
here.

Until the developer sets its Priority, the card waits on the board by its suggestion: just under the
High cards when suggested Urgent or High, at the bottom when suggested Medium or Low (ruling
plan#4017). The module docstring of `tools/quill/_regroup.py` says where every card goes on the
board.

A leaf takes no suggestion, since the step it splits ranks it (ruling plan#3229), and a finding
takes none, since its owner step's Priority decides (ruling plan#3226). `check.takes_suggestion` is
the code's one spelling of which cards take one.

## The rubric

- **Urgent:** a hard date, and the work its card waits on (ruling plan#2840: the 2027 tax law,
  without which CI refuses every pull request from December 1); a security hole reachable in
  production now (ruling plan#2841: the sign-in gate and the rate-limit fix); or a wrong amount the
  grid displays (ruling plan#2869). No other wrong money is Urgent by itself (ruling plan#2842):
  where else a balance is off, and how reachable it is, decides Urgent or High (ruling plan#2869).
- **High:** any other wrong money in production, even an error of cents that grows over the years
  (ruling plan#2804); security hardening that reaches production, ranked by its review's own rating
  where a review rated it, so a finding a review rated LOW is Medium at most (ruling plan#2803);
  every step of an outcome milestone, not only the next in each (ruling plan#2843): The flip, Credit
  card arc in production, Paycheck earnings lines, SimpleFIN daily feed, Week and nth-weekday
  cadences, and Loan event stream, but never Fences made unnecessary, whose steps rank on their own
  merits (ruling plan#2802); or a question blocking High work. Two named steps are the exception,
  not a rule: plan#1246 and plan#1247, the two card-interest steps, are Low though both hold an
  outcome milestone, on the option the developer picked ("all 23, which is the 25 minus the two
  card-interest steps", ruling plan#2843; plan#2797's comment of 2026-11-04).
- **Medium:** a real improvement to production or to how work gets done, not pressing. Most of the
  Fences made unnecessary steps land here.
- **Low:** polish, speculative or cosmetic work, tool and process work with no effect on production,
  and rare races between two browser tabs.

On hold is never suggested (`_fields.SUGGESTED_OPTIONS`): it pauses a step (ruling plan#1832), which
is the developer's act alone.

## The reason

The reason is one plain sentence saying what goes wrong for the developer if the card is never
built, or, for a question, what waits on its answer (plan#2798's spec). It shows on the card and its
side panel, not the board's row (ruling plan#3233), and it lasts only while the suggestion is
pending: quill keeps no copy (ruling plan#3236). GitHub caps its length, and `check.REASON_CAP`
holds that cap.

## What the rulings changed in the draft

plan#2797's draft made Urgent "money is wrong in his production now, a security hole is reachable in
his production now, or it blocks most other work". Ruling plan#2842 keeps Urgent for hard dates and
reachable holes, so wrong money is not Urgent by those words alone, and ruling plan#2869 then made a
wrong amount the grid displays Urgent. Ruling plan#3303 dropped "it blocks most other work": a card
is not Urgent because it blocks other cards, Urgent meaning it must be done now, and how many cards
a card holds up is shown apart from its Priority (plan#3302). The draft's High named "the next
prerequisite" of an outcome milestone; ruling plan#2843 made it every step.
