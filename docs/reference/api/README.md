# API Reference

The public, source-level interfaces of the platform are defined in
[`API-001-public-api.md`](API-001-public-api.md): the API areas
(NyHAL, NyCore, Runtime, Package, Filesystem, Window, AI, Gaming,
Plugin), the naming and versioning rules, and the error model.

- **Status:** Draft. The document fixes the *shape* of the public API —
  areas, layering, conventions — and defers exact signatures to
  implementation work, per the project's rule against publishing
  unverifiable detail (NPC-002 §5.1/§5.2).
- **Relation to the ABI:** API-001 governs source-level interfaces (what
  developers write against, via NySDK); [`ABI-001`](../abi/ABI-001-binary-compatibility.md)
  governs binary-level contracts (what compiled components must satisfy).
