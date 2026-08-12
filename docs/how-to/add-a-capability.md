# How to Add a New Capability to the Registry

*Applies to: contributors adding a permission class the platform doesn't
have yet.*

## When you need this

An application needs to do something no registered capability covers —
e.g. "read the ambient light sensor," a use case no `CAP-*` entry in
[NPS-011](../reference/capability-registry/NPS-011-capability-registry.md)
describes. Per NPC-001 §9.3, no capability may be used by any application
until it has an entry in that document — so the entry comes first.

## Steps

### 1. Draft the entry

Decide the five required fields (NPS-011 §2):

| Field | Your job |
|-------|----------|
| **ID** | Stable identifier, `CAP-<NAME>`. Choose a name that reads as one capability, not a bundle (see step 4 — bundling is a review failure mode). |
| **Description** | What holding this capability allows. One sentence; specific. |
| **Risk Tier** | `Low`, `Medium`, or `High` — informs default prompt behavior (NPS-011 §4). |
| **Android Permission Mapping** | The corresponding Android permission(s) per NPS-008 §5, or `—` if none. |
| **Default Grant** | `Default grant`, `Prompt required`, or `Denied by default`. |

Ask yourself the threat-model question up front: **is this one capability
or several?** The registry split `CAP-MEDIA-LIBRARY` into images/video/
audio because a single coarse capability could over-grant relative to a
narrower Android permission request (`FIND-CAPABILITY-004`, NPS-021 §5.3).
If your draft bundles two independently-requestable things, split it.

### 2. Follow the change process

Per NPS-011 §5, adding a capability goes through the standard change
process (NPC-001 §6): propose the entry in NPS-011, then have it reviewed
by the `security` subsystem owner and by any requesting subsystem's owner.

Practically, that means a draft entry added to NPS-011's table, plus the
document's version bump and revision-history row.

### 3. Update the same-commit index files

NPC-001 §6.5: accepted changes MUST be reflected in
[`SPECIFICATION_INDEX.md`](../00-platform/004-SPECIFICATION_INDEX.md),
[`REPOSITORY_STATE.md`](../00-platform/REPOSITORY_STATE.md), and
[`CHANGE_REQUEST_LOG.md`](../00-platform/CHANGE_REQUEST_LOG.md) in the
same change set.

### 4. Only then may manifests use it

Once the entry is accepted, the capability becomes usable in container
manifests (NPS-010 §4.2). Nothing may reference it as available *before*
that step — including your own tutorial or example code.

## Checklist

- [ ] ID follows `CAP-<NAME>`, no bundles
- [ ] Description says exactly what holding it allows
- [ ] Risk Tier chosen and consistent with §4 behavior
- [ ] Android mapping present or explicitly `—`
- [ ] Default Grant follows the §4 rules (a `Denied by default` tier
      capability can't silently become `Prompt required`)
- [ ] NPS-011 version bumped, revision history updated
- [ ] SPECIFICATION_INDEX / REPOSITORY_STATE / CHANGE_REQUEST_LOG updated
      in the same commit
