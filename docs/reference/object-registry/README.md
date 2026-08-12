# Object Registry

The canonical definition of the platform's object model is
[`NPS-025-object-registry.md`](NPS-025-object-registry.md): every object
type (Workspace, Window, Application, Package, Capability, Game, Mod,
Controller, GPU, Notification, AI Conversation, Device, Service — and,
once its own NPS exists, Identity) with fields, lifecycle, permissions,
serialization rules, and relationships.

- **Status:** Draft — the catalogue is proposed against the existing
  specification set and will tighten as implementation begins.
- **Why it exists:** individual specifications reference objects but never
  defined what they *are*; NPS-025 is the single definition, so
  implementation, the public API (API-001), and the ABI (ABI-001) all
  start from the same model.
- **Discipline:** per NPS-025 §1, new object types MUST be added to the
  catalogue (via the normal change process, NPC-001 §6) rather than
  referenced before they exist — the same rule the capability registry
  established (NPS-011 §6).
