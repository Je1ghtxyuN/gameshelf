# Internal single-game owner migration v1

Scope: trusted local Python caller only, no HTTP, CLI accepting remote paths,
UI, automatic publishing/linking, production invocation, or conversion of all
legacy records. Existing source paths and local UUIDs are never renamed. The
caller must stop/freeze every old writer and keep the selected source and private
target quiescent until commit; a server barrier cannot stop external OS writers.

`OwnerMigrationCoordinator(service)` reads the fixed gateway-owned mode-0600
`state/owner-migration-owner.json` containing `{serverID,accountID}`. This must be
installed only by a trusted local owner audit; neither HTTP nor login creates it.
The account must still exist, be enabled and be an administrator. A private
mode-0600 local manifest binds the operation to the original owner, local UUID,
selection digest, original source identity (device/inode/UID), published edition,
candidate/root/relative/signature, canonical, trusted profile and its evidence.
Paths in this private manifest never become public receipt fields.

First implementation accepts exactly one mapping `{source:<reviewed legacy
directory>,target:"."}`; its source must be listed in the selected trusted
profile variant's legacySavePaths. Root is an explicitly pinned, gateway-owned
local directory, disjoint from gateway state/public source. It does not infer
AppData/home/engine rules or save compatibility. Canonical/edition must already
exist and be explicitly associated; ambiguous matching and new publication/link
are deferred. Real-source verification uses existing profile checks. Existing
nonempty/unknown server uppers remain refused by unchanged namespace checks.

API:

1. `reserve(manifest_path)` validates owner, identities, live source/profile,
   all affected leases and operation uniqueness. A SQLite transaction writes the
   immutable journal and reserves the existing canonical barrier before any grant
   or namespace activation. Reusing an operation with different manifest fails.
2. `apply(operation_id)` reloads that journal under a private per-operation OS
   lock. Only that journal/barrier pair authorizes internal profile staging and
   an own-account grant. Public profile/link/preference barrier checks stay intact.
   It builds a private bounded clone with legacy_saves, normalizes the reviewed
   path to `data/`, then uses legacy_saves again to import into the canonical
   namespace base without opening/exporting a driver view. Immutable plans and
   primitive ledgers are retained for restart/retry; conflicts fail closed.
   A signed receipt file is written after content verification. This is PREPARED,
   not alias authority: barrier remains held.
3. `commit(operation_id)` verifies the signed receipt, exact journal/owner/profile,
   primitive committed ledger, original source and actual target inventory again.
   One SQLite transaction records the receipt, marks the journal committed and
   removes only its matching barrier. Any error retains the barrier. Generic
   `commit_migration_receipt` cannot release coordinator-owned operations.
4. `verify_committed_receipt(operation_id)` is the trusted local verification
   boundary for a later caller/bridge. It requires committed SQLite evidence,
   private file ownership/mode/no-follow, HMAC, all identity bindings and matching
   target inventory. Prepared/copied/edited receipts are rejected. It returns a
   verified value for a caller to bind to its exact local selection before alias
   changes. SHA alone is never authority. Verification after gameplay may fail
   because target progress changed; consume before the first new launch.

`verify_transition(operation_id)` is the concrete trusted bridge boundary. Its
exact result is `{payload,receiptSHA256,envelopeSHA256}`. The envelope digest is
SHA256 of the exact private envelope bytes checked, not JSON reserialized by a
client. Swift must compare this authoritative result with its expected original
selection before committing aliases; a public transport bridge is not included.
`mappingSHA256` hashes canonical `{source:<reviewed legacy directory>,target:"."}`.
Runtime/config pins are bound separately by evidenceSHA256/rulesSHA256 and
sourceSignature; mappingSHA256 alone does not certify runtime compatibility.

`authenticate_committed_receipt(operation_id)` has a different, historical
purpose: authenticate the immutable signed receipt against the committed journal,
owner/selection and original plans without reading current saves. It returns the
envelope, not a transition confirmation. Use only for an alias ALREADY accepted
against a trusted local baseline; it cannot authorize a new alias. It rejects
prepared/partial/tampered receipts. Committed apply/commit retries likewise return
historical authentication without reimporting data. Legitimate gameplay changes
cause the one-time live transition verifier to refuse, while newer bytes remain
untouched and historical authentication remains usable.

`verify_history_transition(operation_id)` is the typed historical bridge. Exact
result: `{payload,receiptSHA256,envelopeSHA256}`, matching initial confirmation's
shape but using a DISTINCT trusted verifier. It verifies HMAC, committed operation
and receipt rows, immutable owner/selection/plan bindings and exact envelope bytes.
It never calls live source/profile verification, live inventory capture or
legacy_saves.apply_import. Only use it to load an alias already recorded in a
private accepted-alias baseline; match that alias and all receipt bindings. It
cannot mint a permit for a new alias. Prepared, partial, tampered and foreign-owner
history is denied. The server does not receive/interpret the client's raw aliases.

There is no distributed activation handoff. `commit` releases the server barrier
before the client writes aliases. The operator MUST keep all old/new devices and
writers frozen through `verify_transition` AND the local alias commit. Do not
start a new game between these steps. This is a manual single-game migration
candidate; existing libraries require explicit, reviewed migration. Existing
Swift Manifest.apply/load need the controller's permit integration; this server
module alone does not enforce their mutation/activation boundary.

Receipt envelope is `{payload,receiptSHA256,signature:{algorithm:"hmac-sha256",
keyID,mac}}`; canonical JSON uses sorted keys, compact separators, ensure_ascii.
receiptSHA256 hashes canonical payload; HMAC hashes canonical
`{payload,receiptSHA256}` with a gateway-owned private key never exported. Payload:
`schemaVersion:1,kind:"owner-save-migration",status:"applied",operationID,serverID,
accountID,localID,selectionID,canonicalID,variantID,candidateID,rootID,relative,
sourceSignature,reviewedProfileID,profileVariantID,profileRevision,rulesSHA256,
evidenceSHA256,sourceInventorySHA256,targetInventorySHA256,mappingSHA256,
importOperationID,namespaceKey`.

Recovery: journal phases reserved/staged/planned/applied/committed. Plans
are persisted BEFORE primitive application. Restart retries the same plans and
primitive ledgers; it does not regenerate a plan over a partially changed target.
Incomplete temporary normalized clones are retained and ignored in favor of a
new fully verified private clone. Crash after receipt file but before SQL commit
does not authorize aliases. Crash after SQL commit can reverify the same receipt.
No automatic cancellation, TTL release, deleting old saves, or forced rollback.

Implementation sequence: regression tests RED; owner/manifest/journal authority;
profile/grant staging; durable clone/remap/import; signed prepared receipt and
atomic commit; interrupted-recovery/adversarial focused GREEN; full Server suite.
Tests use temporary synthetic reviewed RenPy trees and FakeDriver only.
