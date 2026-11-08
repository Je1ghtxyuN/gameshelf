# Stable reviewed-relative Windows launch proof — increment 1

Implemented contract. This increment is **not** the RenPy shared-namespace adapter. The only built-in reviewed program is a harmless deterministic synthetic x64 PE. Its exact source and per-account exported entry are checked; no real game is certified and no cross-variant save file is copied, bound, renamed, merged or migrated.

## Administrator routes

`GET /api/admin/launch-profiles` → `{profiles:[Profile]}`.

Each registry profile contains `id`, `revision`, `entryPath`, `entrySize`, `entrySHA256`, `savePaths:[{path,kind}]`, `writeTargets:[relativePath]`, `rulesSHA256`, `evidenceSHA256`, `platform`, `scope`, `synthetic`, `crossVariantSharing` and `evidence` (review provenance, generator digest, imports and inspected behavior). This is a server-maintained immutable allowlist, not arbitrary administrator assertions or automatically inferred safety.

`POST /api/admin/games/{editionID}/launch-profile {reviewedProfileID:string,enabled:boolean}` → `{gameID,enabled,reviewedProfileID,profileRevision,entryPath,rulesSHA256,evidenceSHA256}`.

Enabled authenticated administrator only. Unknown profile/edition: 404; malformed/extra fields or nonboolean enabled: 400. Enable verifies the published launcher, immutable source snapshot, exact program SHA256, exact relative save rules and confined paths; reauthorizes after filesystem I/O. Edits require ALL accounts' affected canonical leases closed, including opening/expired-active/revoking (409 otherwise). Opt-in persists per edition, pinning the source signature and evidence/rule digests; it grants no account access. Disable removes only the opt-in record and never changes saves or game bytes. Neither route accepts server launch arguments, arbitrary `verified` booleans, host paths, or arbitrary profiles.

## Existing session routes, additive evidence only

Both `POST /api/library/games/{canonicalID}/session {variantID,deviceID}` and legacy `POST /api/games/{editionID}/session {deviceID}` use the same opted-in record and canonical single-writer reservation. Existing IDs, edition overlays, `profileKey` and lease/share fields stay unchanged. No opt-in: `windowsLaunch` is absent.

For an enabled exact profile, only after both source verification and **actual exported account-view** entry/path verification succeed, session adds:

```json
{
  "windowsLaunch": {
    "entryPath": "Fixture.exe",
    "isolation": "reviewed-relative-saves",
    "saveProfileVerified": true,
    "reviewedProfileID": "gameshelf.synthetic-relative-windows.v1",
    "profileRevision": 1,
    "entrySHA256": "23da1a8de2ea9c3aaf4b484b39357c4f41e8c5255f3bddcf5f45610b9003942b",
    "rulesSHA256": "4817ddeea5d8d6b96ac6b9ca8a080e4f3f452d6deef329eb2a112b1f1f0d5f25",
    "evidenceSHA256": "5d86fc7be6ad08c33c2c61fea46fd2e9d03fbd65249ea8ce4478aaac2eda78ec",
    "sourceSignature": "<64 lowercase hex; this published source snapshot>",
    "savePaths": [{"path":"savedata","kind":"directory"}],
    "synthetic": true
  }
}
```

The first three fields match the existing Franklin/C# `WindowsLaunch` record. Additional evidence fields are stable and additive; clients may parse/validate them. The selected executable must exactly match entryPath. The Windows client must start with the reviewed executable's directory as working directory (the current WindowsView already does so). Do not infer support for a different entry, another game, a helper, RenPy or AppData from this proof.

No driver proof capability, stale review, source/entry mismatch, copied-up account entry changes, unsafe save/entry paths, symlinks, case/Unicode ambiguity or unexpected rules → unavailable/error without returning a launch-capable response. Failed exports are revoked; failure to shut down retains the canonical writer as revoking. Source hashes are rechecked even if size/mtime metadata was preserved. An administrator cannot edit the profile during an opening/active/revoking canonical lease. Both APIs stay under the same writer lock.

`reviewed-windows-launch-v1.json` is a synthetic success-evidence fixture with an all-zero example sourceSignature; no private data or real account credentials.

## Built-in synthetic review scope and current limits

ID `gameshelf.synthetic-relative-windows.v1`, revision 1. Deterministic 1536-byte x64 PE from `Fixtures/reviewed-relative-windows/build_fixture.py`. Only imports CreateDirectoryW/CreateFileW/WriteFile/CloseHandle/Sleep/ExitProcess; fixed relative `savedata/progress.txt` output. No user arguments, engine config, managed runtime, dynamic helper/runtime loading, environment profile lookup, registry or network behavior. The code and generator digest are pinned as review evidence. Unrecognized programs or rule sets, including external AppData profiles, cannot obtain this record.

The service tests exercise opt-in, real bounded HTTP, byte fingerprints, exported filesystem view validation, per-account savedata persistence and canonical lease cleanup using temporary fixture sources/FakeDriver. This Mac task does not execute the PE on Windows or mount a real Linux OverlayFS/SMB view. Native Windows and opt-in Linux integration acceptance remain separate; no general runnable Windows/RenPy support is claimed.

## Separate canonical RenPy namespace capability

Canonical RenPy namespaces, the fixed client args/env, explicit session adapter acknowledgement, common native/runtime proof and trusted private registry are defined in `renpy-save-namespace-v1.md`. They are a separate explicitly reviewed capability; the synthetic relative-only PE described here does not acquire RenPy save sharing or certify arbitrary games. The public namespace has exactly six fields. Existing nonempty legacy saves are not migrated by either capability.
