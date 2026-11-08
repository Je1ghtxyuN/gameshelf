# Reviewed canonical RenPy save namespace v1

Only explicitly opted-in, fingerprint-reviewed exports qualify. Unknown engines, unsupported runtime layouts, unreviewed global SDKs and other games receive no RenPy namespace capability. No game/source/save migration occurs implicitly.

## Requests and activation

`GET /api/admin/save-profiles` returns `{profiles:[{id,revision,adapter,compatibilityKey,synthetic,rulesSHA256,evidenceSHA256}]}` to administrators only.

`POST /api/admin/library/games/{canonicalID}/save-compatibility {variantIDs:[editionID],compatibilityKey,reviewedProfileID}` explicitly activates reviewed bindings. All editions must already be explicitly linked to that canonical. The selected key must equal the trusted profile's save-format key. Each edition must uniquely match the published entry/backend, source/bootstrap/runtime/config file hashes and reviewed source save exclusions. Root-native `.app` entry `.` is supported only for a registered native source folder whose suffix is `.app`, with Info.plist and actual launcher/bootstrap/config evidence. Windows needs its exact published RenPy executable and privacy-critical runtime/config evidence.

Returns `{canonicalID,saveCompatibilityKey,adapter:"renpy-savedir-v1",relativeSavePath:".gameshelf-profile/CanonicalSaves/renpy"}`. Active/opening/revoking leases or migration barriers across affected accounts block profile changes. Existing namespace identity/revision/evidence is not silently replaced. New reviewed edition bindings may be added under the same immutable namespace record. This route neither grants accounts nor imports saves. It accepts no arbitrary reviewed boolean, replacement code, source host path, argv or environment dictionary.

Both session routes accept the optional request `adapter` field:

- `POST /api/library/games/{canonicalID}/session {variantID,deviceID,adapter:"renpy-savedir-v1",saveCompatibilityKey?}`
- `POST /api/games/{editionID}/session {deviceID,adapter:"renpy-savedir-v1"}`

Once a canonical namespace is active, BOTH routes require this explicit adapter capability acknowledgement. Unaware old clients receive 409 before export. A supplied compatibility key must match the authoritative mapping; omitting it does not create an isolated fallback. An unlisted or stale edition is 409, even through the legacy route. All variants/keys remain under the same per-account canonical writer, independent of the compatibility-key string. Ordinary games without a namespace preserve existing request/response behavior.

Library variants with a current reviewed binding advertise their existing optional `saveCompatibilityKey`. Clients must still validate the session and its exact namespace/proof; an engine name or generic saveIsolation text is never certification.

## Final public namespace — exactly six fields

```json
{
  "saveNamespace": {
    "key": "serverID/userID/canonicalID/SHA256(compatibilityKey UTF-8)",
    "relativePath": ".gameshelf-profile/CanonicalSaves/renpy",
    "adapter": "renpy-savedir-v1",
    "reviewedProfileID": "opaque-reviewed-profile-id",
    "revision": 1,
    "rulesSHA256": "64-lowercase-hex"
  }
}
```

The key has exactly four slash-separated components: existing server, authenticated account, logical canonical ID, and lowercase SHA256 of the advertised compatibility key. It is opaque identity, NOT a host filesystem location. The complete relativePath and adapter strings above are fixed, case-sensitive values; clients reject any other spelling/path. No internal variant record is included. Edition `profileKey`, game IDs, old workspaces, and share/lease fields remain unchanged.

## Native/common hash proof and Windows proof

Only after source verification, canonical namespace bind and actual exported-view verification succeed, the lease additionally carries:

`renpyLaunch:{adapter,platform,entryPath,relativeSavePath,reviewedProfileID,revision,rulesSHA256,evidenceSHA256,sourceSignature,fingerprints:[{path,role,sourceSHA256,exportSHA256}]}`.

All fingerprint paths are relative to the leased game view. `sourceSHA256` pins audited source provenance; `exportSHA256` is the required actual exported/private-cache byte hash, including the expected private runtime patch. No patch contents, reviewer's private identity or host paths are returned. A native `.` proof includes `Contents/Info.plist` plus launcher/bootstrap and privacy-critical runtime/config fingerprints. For a nested `.app`, the paths include its published entry prefix; map that prefix correctly when copying the app bundle into a private cache.

The proof adapter/profile/revision/rules must equal the six-field namespace, and source/entry identity must match the lease/selected variant. Cache copied code only after verifying export hashes, then reverify the private copy. An already correctly patched file is accepted idempotently. Do not accept an original hash where the lease requires a patched export hash.

A reviewed Windows entry also receives the existing `windowsLaunch` proof fields plus `adapter:"renpy-savedir-v1"` and `relativeSavePath` equal to the same fixed target. Existing entryPath/isolation/saveProfileVerified/profile/fingerprint fields remain. No arbitrary argument array is returned. A global/unpatched client SDK cannot inherit this capability; unsupported client runtimes must refuse or use a separately reviewed private runtime route.

## Required fixed client adapter

Resolve the fixed target only beneath the authenticated account view, rejecting symlinks/reparse ancestors and path escapes. Validate full lease, namespace key, proof and expected module hashes before launch. Generate exactly the known option and fixed environment locally:

- arguments: `--savedir`, resolved namespace root;
- `RENPY_PATH_TO_SAVES`: namespace root + `/Auxiliary`;
- `RENPY_MULTIPERSISTENT`: namespace root + `/MultiPersistent`;
- `PYTHONDONTWRITEBYTECODE`: `1`.

Use the client/runtime's correct path representation (including Wine paths where applicable). Do not replace the OS user's AppData/home roots or accept arbitrary server arguments/environment. The namespace root and Auxiliary/MultiPersistent directories are private account data, never shared across accounts.

`--savedir` alone does NOT establish isolation for stock RenPy 8.3.7: its secondary game-local/extra locations, token path and multipersistent fallbacks also require the reviewed mitigation. This adapter requires the single-primary-location policy plus the fixed environment. The known original `savelocation.py` SHA `7a84aea6ddccab67c7e004d94cd38092e4a910c15ccb6fabb44aecbde1108427` is refused without the exact approved private replacement SHA `ba48a4c044ec8d557bbc2f42aab441caaf52480ebeb29f2a9208594b6dc09596`. The reviewed replacement removes the block from `    # 2. Game-local savedir.\n` up to `    # Scan the location once.\n`, replacing it with `    # GameShelf account adapter v1: use only the supplied account savedir.\n\n`. The MIT fixture/provenance is under `Fixtures/renpy-8.3.7`.

The server patches only the private overlay, only from a pinned original to a digest-verified trusted replacement, and verifies the resulting exported hashes. Source files are untouched. Unknown/tampered private runtime files and associated unreviewed compiled caches are preserved and rejected, never silently overwritten/deleted. `PYTHONDONTWRITEBYTECODE=1` is not permission to skip existing-cache review. No other engine/runtime version is inferred safe from its name.

## Trusted private review registry

The optional gateway-private `reviewed-renpy-profiles.json` is loaded at service start. It must be a regular, unlinked, no-follow file, owned by the server effective UID (root in the production Linux gateway), exact mode 0600, bounded to 2 MiB. It is not writable through HTTP. Missing file means no reviewed RenPy profiles. Invalid or changed-on-read review data fails closed.

Shape:

```text
{schemaVersion:1, profiles:[{
  id, revision, adapter:"renpy-savedir-v1", compatibilityKey, synthetic:boolean,
  isolationPolicy:"single-primary-location-and-fixed-env-v1",
  review:{by,basis},
  variants:[{
    id, platform:"macos"|"windows", entryPath, entryBackend:"native"|"renpy",
    savePaths:[{path,kind:"directory"}],
    legacySavePaths:[{path,kind:"directory"}],
    fingerprints:[{path,role,sha256}],
    patches?:[{path,sourceSHA256,targetSHA256,replacementBase64}]
  }]
}]}
```

`savePaths` must exactly match the edition's reviewed published source exclusions; publish an explicit save-rule edition and link it to the prior canonical when necessary, rather than weakening or rewriting an existing workspace's privacy manifest. `legacySavePaths` describe all known prior account-view save directories, may include specific reserved-profile legacy directories, and cannot overlap the canonical reserved target. All paths are confined relative paths, with no dot/walk-up/drive/alias paths except the strictly validated native entry `.`. Overlapping directory rules are rejected.

Required fingerprint roles: entry, bootstrap, arguments, main, locations, persistent, tokens, config; native app Info.plist requires an additional metadata proof. Exactly one executable entry proof is required. Include all additional files needed by the actual audited bootstrap/loader/config; this minimum list is not an automatic engine detector or an audit of arbitrary scripts. A profile must uniquely match the edition; no newest/first match is chosen.

Optional private replacement bytes are allowed only for fingerprint-bound, non-entry Python runtime/config files. Source and replacement digests must match; patches are fixed file replacements, not commands or executed server code. Known unsafe stock 8.3.7 location code only accepts the approved patched digest. Server evidenceSHA256 binds the normalized full record; rulesSHA256 binds the adapter, fixed path, format key and reviewed per-variant directory rules. New revisions/evidence require explicit migration/review when an existing namespace is pinned. Production records require an actual source/bootstrap/loader/save-path audit; synthetic API/filesystem tests certify no real game automatically.

## Storage, cleanup and legacy data

Physical data is partitioned by server/account/canonical/compatibility hash beneath private gateway state. Root-owned manifests pin namespace identity/profile/revision/rules/evidence; account data directories are mode 0700. No physical host path is exposed. Each original edition retains its lower/upper/work/merged overlay. Only the fixed reserved subtree is bound to canonical data before SMB credentials/export become usable.

Before opening, inspect the relevant existing private save directories of every reviewed canonical edition and the reserved target. Nonempty legacy data is 409, preserved for explicit backup/adoption. Unknown historical editions with existing private workspace content are also 409; do not assume their progress is empty or merge an unknown profile. Source seed saves are masked by reviewed exclusions and are never imported into a new account implicitly. Existing namespace data is reused only with an exact manifest match and safe directory/file structure. Links, special/hardlinked files, wrong owners/modes and uninitialized/conflicting state fail closed.

Close order: revoke SMB credentials/share/open handles, normally unmount reserved child bind, then merged overlay and lower bind. No lazy/forced unmount. Child-bind or shutdown failure retains the canonical lease as revoking and prevents another platform writer. Recovery uses persisted minimal cleanup identity without requiring client cooperation. Real OverlayFS/SMB behavior still requires isolated Linux acceptance; command-recording tests do not substitute for it.

## Owner import barrier (internal coordinator only)

No HTTP endpoint accepts arbitrary import/source/backup host paths. A trusted owner coordinator must verify the exact stored owner/server/account/canonical binding and freeze source/destination. Reserve before grant, namespace import or receipt commit; do not grant then rely on an idle check.

Internal service calls are `reserve_migration(authenticatedAdminSelf, canonicalID, operationID)` and `commit_migration_receipt(authenticatedAdminSelf, canonicalID, operationID, {status:"applied"|"rolled-back",digest:SHA256})`. The barrier operationID is a stable 32-lowercase-hex coordinator UUID; a copy/backup plan's own SHA256 operation digest can be recorded in the receipt digest. These are internal primitives, not an authorization bypass or proof that filesystem import has occurred.

Both session routes check the durable account/canonical barrier before source I/O and again before reserving the play writer. A barrier also blocks identity/profile changes, own removal and entry-preference changes. It survives token logout/expiry, reap and restart; another account is independent. Only after verified import or completed rollback may the trusted coordinator atomically commit its receipt and release the matching barrier in one SQLite transaction. Receipt failures keep the barrier; repeat operations/receipts must match exactly. Source-owner validation, backup/copy/recovery orchestration and adoption of legacy nonempty state remain the coordinator's responsibility; no automatic import is performed by namespace activation or launch.
