# Why the Assistant Suggests but Doesn't Act

*(Explanation — see NPS-015, NPS-016 for the normative specs, and
ADR-0011 for the formal decision record.)*

## The Problem

An on-device AI assistant that can see system diagnostics, tune game
performance, and help with code is genuinely useful — but it's also the
single easiest place for a platform to quietly grant itself an exception
to its own rules. "Let the assistant fix it for you" is a natural feature
to build, and it's also exactly the kind of implicit, unaudited authority
NTM-000 §9 and NPC-001 §11 were written to rule out before anyone got far
enough into implementation to be tempted by it.

## The Choice

The assistant runs in ordinary containers (ADR-0011), holding only what's
in the capability registry like anything else — including two capabilities
built specifically for it (`CAP-AI-DIAGNOSTICS-READ`,
`CAP-AI-SUGGEST-ACTION`) that are deliberately narrow: one lets it *see*
system state, the other lets it *propose* a change. Neither lets it make
the change happen. NPS-015 §5 draws that line explicitly: if the assistant
wants to disable Bluetooth, tune a game's resource limits, or change a
setting, the user has to be the one who flips it — not by rubber-stamping
a chat message, but through the same reviewable UI action they'd use
without the assistant involved at all.

Cloud sync (NPS-016) got folded into this milestone for scheduling reasons
but is architecturally unrelated to the assistant — it answers a different
question NPC-001 §10.2 already asked back at Milestone 2 and left
unspecified: what does "opt-in, not required" actually mean per data
class. The answer here is that each of saves, installed-app lists,
settings, themes, and drivers is its own independent grant, not a single
"enable cloud" toggle.

## What This Buys Us

- NTM-000 §9's promise — "the operating system must remain fully
  functional without AI" — isn't just a claim in a philosophy document; it
  follows mechanically from the assistant never holding a capability
  anything else depends on (NPS-015 §6, §7).
- The audit view built back in NPS-010 §8 already answers "what can the
  assistant do right now" without needing AI-specific tooling.
- A user who never enables cloud sync loses nothing, because the local
  NyFS-backed copy (NPS-004, NPS-006) is defined as canonical, not the
  synced copy (NPS-016 §5.1).

## What It Costs Us

- Every genuinely useful "just do it for me" assistant feature has to be
  built as a two-step suggest-then-user-executes flow instead of a
  one-step action, which is real friction weighed against real audit
  guarantees.
- Cloud sync conflict handling has to surface conflicts to the user rather
  than silently picking a winner (NPS-016 §6.1), which is more work than
  a simple last-write-wins policy would be.
