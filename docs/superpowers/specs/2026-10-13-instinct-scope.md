# voice-instinct: Scope, Limited to What Instinct Does

Decision (2026-10-13): the product does what Instinct does and only that. No coding agent, no general
file or shell work. The earlier coding-task demos were scaffolding for the harness bridge, not the product.

Sources are third-party write-ups (TechCrunch's launch report and review sites such as eesel, Idea Usher,
Layer3 Labs); no official Instinct page was found, so every capability below is "reported", not confirmed.

## In scope: what Instinct is reported to do

| # | Capability (reported) | Our channel and approach | Status |
|---|---|---|---|
| 1 | Text/call interface, no app to learn | Voice in the browser now; Telegram text and phone calls later | voice works; others later |
| 2 | Research and comparison shopping | Delegated agent with web search and fetch, results spoken | partly (web tools prompt) |
| 3 | Travel, restaurant, ticket bookings (online), flight check-in | Browser agent in a dedicated browser profile, propose then book on the user's go-ahead | not built |
| 4 | Purchases (groceries, tickets) and paying bills, using limited spending power (reported: one-time Stripe Link card with user-approved limits, a daily limit) | Spend limits enforced in code before any payment tool exists; start with "agent prepares, user pays" | not built |
| 5 | Cancelling subscriptions | Browser agent plus confirmation of what is cancelled | not built |
| 6 | Concierge phone calls to businesses (booking where there is no online option, cancellation lists, billing problems) | Our strongest fit: the same voice stack over Pipecat's documented Twilio transport, outbound via a Twilio REST call | not built |
| 7 | Proactive follow-ups: reminders, flight check-in, forgotten reservations | Memory and heartbeat from `2026-10-11-memory-and-proactivity-design.md` | spec only |
| 8 | Trusted-person network: your agent coordinates plans with friends' agents | Last; needs other users running agents | deferred |

## Out of scope

Writing or running code, general computer or file tasks, content creation, anything not an errand.
The assistant now says so aloud (system prompt) instead of attempting it.

## Where we aim to be better than Instinct (the reported weak points)

- **Local and private:** your data stays on your machine; no company-run cloud computer with your logins.
- **Open source and bring-your-own model**, no invite list, no cut of purchases.
- **Voice-first**, and phone calls use the same stack.
- **Limits enforced in code, not prompts:** reported Instinct failures include an email sent without
  approval, a seat changed during check-in, data kept after a disconnect, and one hallucinated
  disclosure of someone else's document. Ours: no payment or send tool without a code-enforced cap and
  an action log you can read; deletion from day one.
- **A dedicated browser profile** with its own logins, never your personal profile.
- **Autonomy you can see and tune:** per-capability settings, announced aloud.
- **Task status that is clear**, answering Instinct's reported single-thread confusion.

## Constraints that carry over from what is built

Autonomy stays inside Claude Code's sandbox plus deny rules; anything needing a human (a login code, a
go-ahead, a choice) reaches the user by voice and is answered by voice; yes/no decisions are made by code
from the user's own words after the assistant asked.

## Proposed order (to confirm)

1. **Research and reminders:** web research end to end, plus memory and the heartbeat. No money involved.
2. **Browser bookings with "propose, then you approve":** dedicated profile, no payments.
3. **Payments with enforced limits and an action log.**
4. **Concierge phone calls** (Twilio, our voice stack).
5. **Subscription cancellation**, which reuses the browser agent.
6. Trusted-person network, only if other people run agents.

## Open questions

- Which capability matters most to you first? That decides step 1 versus 4.
- Payments: a virtual card service needs an account (not something I can create). Until then the agent
  prepares and you pay.
- A browser tool for the headless agent (Playwright MCP) runs outside the sandbox; the dedicated
  profile and domain rules are the mitigation, to be designed in step 2.
