# ABI Reference

Binary-level compatibility contracts are defined in
[`ABI-001-binary-compatibility.md`](ABI-001-binary-compatibility.md):
calling conventions, binary compatibility guarantees, symbol versioning,
and the plugin / driver / runtime / backend ABIs.

- **Status:** Draft. Rules and areas are normative; concrete layouts
  (IPC wire format, header bytes, register conventions) are deferred to
  implementation work — NPS-003 §9 explicitly defers the IPC wire format
  to an ABI document, which is this one.
- **Relation to the API:** the [`API`](../api/API-001-public-api.md) is
  what developers write against; the ABI is what compiled components must
  satisfy. They version together at platform-release granularity
  (API-001 §6.2).
