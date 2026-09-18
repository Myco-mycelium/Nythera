# Ship a Release (the ISO release driver's short version)

The v0.29.25 release burned five rounds before it shipped — every
failure mode below was met once and is now either fixed in the
pipeline or self-diagnosing. This page is the operator's short
version; deep background lives in
[`REPOSITORY_STATE.md`](https://github.com/Myco-mycelium/Nythera/blob/main/docs/00-platform/REPOSITORY_STATE.md)
(Documentation Hygiene Notes, Sep 17–18).

## The pipeline (what runs, in order)

On a `v*` tag push, **both** ISO workflows build, boot-smoke, and ship:

| Workflow | Build → smoke → ship |
|---|---|
| `live-iso.yml` (amd64) | build → direct QEMU smoke → menu-boot job → attach step |
| `live-iso-arm64.yml` (arm64) | same shape, cross-built under TCG (~45 min) |

The attach logic (race-safe create, bounded-curl upload, draft
self-heal) lives in **one script** —
`scripts/attach_release_asset.sh` — shared by both attach steps, both
`re-attach` dispatch jobs, and the race harness. Do not inline it.

## Cutting a release

```bash
# 1. Bump the version in the SAME commit as the newest CHANGELOG
#    heading (tools/check_version_drift.py enforces this).
# 2. Tag the commit (annotated) and push it:
git tag -a vX.Y.Z -m "Nyrqis vX.Y.Z" && git push origin vX.Y.Z
# 3. Watch BOTH workflows to completion (~50 min worst case):
scripts/check_scheduled_runs.sh   # or the Actions tab
# 4. Verify the release is PUBLIC and carries BOTH assets:
curl -s https://api.github.com/repos/Myco-mycelium/Nythera/releases/tags/vX.Y.Z \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('name'), d.get('draft')); [print(' ', a['name'], a['size'], a['digest']) for a in d.get('assets',[])]"
```

Anonymous `404` on that last call means **draft release** — see
failure mode 3.

## The failure modes, and what handles them now

1. **`gh release upload` stalls on a ~250 MB asset.** It hung twice
   (28.5 min to a job-budget kill, zero evidence). Fixed: uploads go
   through `curl` against the release `upload_url` with `--max-time`,
   3 bounded retries, and a 20-min **step-level** timeout — a hang
   dies as a named step, never a silent cancellation.
2. **`--generate-notes` can stall server-side.** Fixed: deterministic
   `--notes` in `attach_release_asset.sh`.
3. **Force-moving a tag converts its release to a DRAFT** — invisible
   to anonymous clients while `gh` still sees it, so CI "succeeds" into
   a release nobody can download. Fixed: the script runs
   `gh release edit --draft=false` after the upload gate. Never
   force-move a tag while its release exists; if you must, expect the
   draft and let the next attach heal it.
4. **The virt machine's default NIC ROM (`efi-virtio.rom`) is absent
   under `--no-install-recommends` apt** — QEMU exits 1 in 2 s. Fixed:
   both smokes run `-net none`.
5. **TCG emulation is slow; budgets must reflect measurement.** The
   arm64 smoke budget is 1680 s, pinned in `TestJobTimeoutContract`;
   keep the arithmetic (build 90 + apt 5 + smoke 29 + overhead ≤ 150)
   when touching the workflow.
6. **`uploads.github.com` can have a sustained 5xx window** (v0.29.25:
   `500/500/hang + 500/500/502`, githubstatus green throughout). The
   in-step retries ride it out; if they don't, use the recovery paths
   below.

## Recovery paths (no rebuild needed)

- **Re-run just the failed jobs**: Actions tab → the run →
  "Re-run failed jobs" (needs a token with `actions:write` — the
  workflow's own `GITHUB_TOKEN` cannot).
- **Re-attach jobs**: dispatch on `main` with `release-tag=vX.Y.Z` —
  they re-attach the already-built ISO artifact. (Equivalent
  self-serve: download the `nyrqis-live-iso[-arm64]` artifact, then
  run `scripts/attach_release_asset.sh` with `GH_TOKEN`, `RELEASE_TAG`,
  `ISO_PATH` set — the exact thing the drill did against the live
  v0.29.25 release; the replacement asset's server-side digest proved
  the round-trip integrity-preserving.)

## After the release

- Confirm the boot smokes went green on the **tag** runs (both jobs,
  both arches) — not only the pushes.
- If a digest matters (reproducibility audit), pin it at publish time:
  asset `digest` fields are server-computed; a local
  `sha256sum` of a downloaded asset must match.
- Credentials: the push token is a fine-grained PAT with a hard expiry
  (`pat-expiry-watch.yml` fails red at ≤7 days of runway). Rotate with
  `scripts/rotate_push_pat.sh`, verify grants with
  `scripts/verify_pat_grants.sh` (`--drill` re-runs the release
  round-trip at a tag).
