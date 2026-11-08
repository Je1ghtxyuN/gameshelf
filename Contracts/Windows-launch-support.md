# Windows launch support — current server contract

The client consumes v2 `POST /api/library/games/{canonicalID}/session` with `{variantID,deviceID,adapter:"renpy-savedir-v1"}`. Adapter is an explicit supported capability, not an arbitrary command. The v1 lease response remains the base, including `game.id`, account/server identity and expiry, `share.host`, exact `share.port`, `share.name`/`share.share`, and ephemeral share credentials. Credentials stay in memory; time `endedAt` remains finite Unix seconds.

## Reviewed proof and namespace

The server now issues `windowsLaunch` only for its reviewed registry profiles. The existing proof binds `entryPath`, `isolation:"reviewed-relative-saves"`, `saveProfileVerified`, `reviewedProfileID`, `profileRevision`, `entrySHA256`, `rulesSHA256`, `evidenceSHA256`, `sourceSignature`, `savePaths`, and `synthetic`. Unknown/unreviewed games do not gain proof from detected save paths or an administrator's assertion.

Reviewed Ren'Py canonical sessions additionally return exactly six public `saveNamespace` fields:

```
{key, relativePath, adapter, reviewedProfileID, revision, rulesSHA256}
```

`key` is `serverID/userID/canonicalID/SHA256(compatibilityKey)`. The only supported adapter is `renpy-savedir-v1`; the path is exactly `.gameshelf-profile/CanonicalSaves/renpy`. Its `windowsLaunch` adds `adapter` and `relativeSavePath`. `renpyLaunch` binds platform, entry, profile revision/rules/evidence/source signature and all reviewed runtime/config fingerprints with source/export hashes. Server driver verifies the exported pinned module after applying the reviewed patch; source files are not modified by this client.

Windows validates bound identities/key, six-field shape, mandatory advertised compatibility key (nonempty, ≤128) and its computed digest, known adapter/path, matching review ID/revision/rules/evidence/source and entry, complete unique critical fingerprint roles at the known Windows Ren’Py module paths, all exported file hashes and the known mitigated Ren'Py 8.3.7 locations module. Unknown fields carrying raw environment maps or arguments are rejected. It requires an already provisioned namespace directory and refuses links, changed files, missing proofs or expired launch leases. It may create only Auxiliary and MultiPersistent within the validated account namespace; it never opens a blank fallback or adopts old saves.

After successful validation, the native suspended/Job launch gets exactly:

```
--savedir <account-view>/.gameshelf-profile/CanonicalSaves/renpy
RENPY_PATH_TO_SAVES=<savedir>/Auxiliary
RENPY_MULTIPERSISTENT=<savedir>/MultiPersistent
PYTHONDONTWRITEBYTECODE=1
```

These three overrides are built locally into the child process environment. No server-supplied arbitrary arguments/variables, APPDATA/LOCALAPPDATA/USERPROFILE replacement, Wine or administrator setup is permitted. Ordinary standalone local launches retain usual original Windows save behavior and are never labelled account-isolated.

## SMB and acceptance boundary

Windows 11 24H2 `New-SmbMapping -TcpPort` capability is checked. The exact API port is used, with no fallback to 445 and no NAS administrator share. Mapping ownership starts only after a correlated successful operation receipt; cleanup verifies its provenance, current port and network credential identity. Failed creation cannot remove another caller's mapping.

Real Windows build, process lifetime, fixture account views, fixed adapter args/environment and local-time tests are separate from actual NAS verification. No real NAS/game, 1445/445 coexistence, real Ren'Py export compatibility, migration/adoption or cross-device save merge is certified by those fixtures. Missing/unsupported review proof fails closed and releases an unused lease. An isolated synthetic live TLS/SMB2445 attempt reached session issuance but failed in client mapping creation before native launch; it did not pass acceptance. Normal cleanup was separately confirmed. No live NAS acceptance or production install/release is claimed.

Official SMB capability references: https://learn.microsoft.com/en-us/windows-server/storage/file-server/smb-ports ; https://learn.microsoft.com/en-us/powershell/module/smbshare/new-smbmapping
