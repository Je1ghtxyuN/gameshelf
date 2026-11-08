import Foundation

@main struct AccountMetadataSyncTests {
 struct Failure: Error { let message: String }
 static func check(_ value: Bool, _ message: String) throws {
  if !value { throw Failure(message: message) }
 }
 static func rejects(_ operation: () throws -> Void) throws {
  do { try operation() } catch is Failure { throw Failure(message: "unexpected test failure") } catch { return }
  throw Failure(message: "expected rejection")
 }
 static let gameID = String(repeating: "a", count: 32)
 static let secondID = String(repeating: "b", count: 32)
 static func game(_ id: String = gameID, title: String = "Original", revision: Int = 0,
                  favorite: Bool = false, collections: [UUID] = []) -> AccountLibraryGame {
  AccountLibraryGame(id: id, title: title, revision: revision, favorite: favorite,
   collectionIDs: collections, playtimeSeconds: 0, lastPlayed: nil, coverSHA256: nil, variants: [])
 }
 static func document(_ identity: AccountLibraryIdentity, _ games: [AccountLibraryGame],
                      collections: [UUID] = []) -> AccountLibraryDocument {
  AccountLibraryDocument(schemaVersion: 2, serverID: identity.serverID,
   user: AccountLibraryUser(id: identity.accountID, username: "Fixture", admin: false), games: games,
   collections: collections.map { AccountLibraryCollection(id: $0, name: "Fixture") })
 }
 static func main() throws {
  let fm = FileManager.default
  let root = fm.temporaryDirectory.appendingPathComponent("AccountMetadataSync-" + UUID().uuidString)
  defer { try? fm.removeItem(at: root) }
  let identity = try AccountLibraryIdentity(serverID: "fixture-server", accountID: "alice")
  func makeSync(_ name: String) throws -> (AccountMetadataSync, URL) {
   let profile = try AccountLibraryProfile.open(under: root.appendingPathComponent(name), identity: identity, legacyOwner: nil)
   let sync = try AccountMetadataSync(root: profile, identity: identity)
   try sync.seed(document(identity, [game(), game(secondID)]))
   return (sync, profile)
  }

  // Catches overwriting a remotely changed favorite while uploading a local title.
  do {
   let (sync, _) = try makeSync("other-field")
   let local = AccountMetadataValues(game: game(title: "Local title"))
   let remote = document(identity, [game(revision: 1, favorite: true)])
   let plan = try sync.reconcile(gameID: gameID, local: local, remote: remote)
   try check(plan.conflicts.isEmpty && plan.patch.revision == 1, "independent edit conflicted or used stale CAS")
   try check(plan.patch.title == "Local title" && plan.patch.favorite == nil, "remote favorite was sent back")
   try check(plan.merged.title == "Local title" && plan.merged.favorite, "merge lost either edit")
   try sync.acknowledge(plan, response: game(title: "Local title", revision: 2, favorite: true))
   let retry = try sync.reconcile(gameID: gameID, local: local,
    remote: document(identity, [game(title: "Local title", revision: 2, favorite: true)]))
   try check(retry.patch.isEmpty && retry.merged.favorite, "unchanged local favorite reverted remote after ACK")
  }

  // A safe field can ACK independently without erasing a pending title conflict.
  do {
   let (sync, _) = try makeSync("same-field")
   let local = AccountMetadataValues(game: game(title: "Mine", favorite: true))
   let remote = document(identity, [game(title: "Theirs", revision: 1)])
   let plan = try sync.reconcile(gameID: gameID, local: local, remote: remote)
   try check(plan.conflicts == [.title], "same-field title conflict was hidden")
   try check(plan.patch.title == nil && plan.patch.favorite == true, "conflict overwrote remote or blocked safe field")
   try check(plan.merged.title == "Theirs" && plan.local.title == "Mine", "conflict discarded one side")
   try sync.acknowledge(plan, response: game(title: "Theirs", revision: 2, favorite: true))
   let retry = try sync.reconcile(gameID: gameID, local: local,
    remote: document(identity, [game(title: "Theirs", revision: 2, favorite: true)]))
   try check(retry.conflicts == [.title] && retry.patch.isEmpty, "partial ACK forgot conflict or requeued favorite")
  }

  // Simulates game one ACK followed by game two failure/restart, then a new edit.
  do {
   let (sync, profile) = try makeSync("partial-ack")
   let initial = document(identity, [game(), game(secondID)])
   let first = try sync.reconcile(gameID: gameID, local: AccountMetadataValues(game: game(title: "First")), remote: initial)
   try sync.acknowledge(first, response: game(title: "First", revision: 1))
   let reloaded = try AccountMetadataSync(root: profile, identity: identity)
   try reloaded.seed(initial) // A stale cache must not replace an acknowledged baseline.
   try check(reloaded.baseline(for: gameID)?.revision == 1 && reloaded.baseline(for: secondID)?.revision == 0,
    "partial success was lost or falsely acknowledged another game")
   let current = document(identity, [game(title: "First", revision: 1), game(secondID)])
   let retry = try reloaded.reconcile(gameID: gameID, local: AccountMetadataValues(game: game(title: "Second")), remote: current)
   try check(retry.patch.revision == 1 && retry.patch.title == "Second", "retry uses pre-ACK revision")
   try rejects { _ = try reloaded.reconcile(gameID: gameID, local: retry.local, remote: initial) }
   let attrs = try fm.attributesOfItem(atPath: profile.appendingPathComponent("metadata-baselines.json").path)
   try check((attrs[.posixPermissions] as? NSNumber)?.intValue == 0o600, "baselines not private")
  }

  // Neither another account nor the same account name on another server can reuse state/plans.
  do {
   let (sync, profile) = try makeSync("identity")
   let plan = try sync.reconcile(gameID: gameID, local: AccountMetadataValues(game: game(title: "Alice")),
    remote: document(identity, [game()]))
   for other in [try AccountLibraryIdentity(serverID: identity.serverID, accountID: "bob"),
                 try AccountLibraryIdentity(serverID: "other-server", accountID: identity.accountID)] {
    try rejects { _ = try AccountMetadataSync(root: profile, identity: other) }
    try rejects { _ = try sync.reconcile(gameID: gameID, local: plan.local, remote: document(other, [game()])) }
    let separate = try AccountLibraryProfile.open(under: root.appendingPathComponent("identity"), identity: other, legacyOwner: nil)
    let otherSync = try AccountMetadataSync(root: separate, identity: other)
    try otherSync.seed(document(other, [game()]))
    try rejects { try otherSync.acknowledge(plan, response: game(title: "Alice", revision: 1)) }
    try check(otherSync.baseline(for: gameID)?.remote.title == "Original", "foreign ACK contaminated account")
   }
  }

  // Set order is not an edit; competing membership replacements are explicit conflicts.
  do {
   let (sync, _) = try makeSync("collections")
   let a = UUID(uuidString: "00000000-0000-0000-0000-000000000001")!
   let b = UUID(uuidString: "00000000-0000-0000-0000-000000000002")!
   let remote = document(identity, [game(revision: 1, collections: [b, a])], collections: [a, b])
   let same = try sync.reconcile(gameID: gameID, local: AccountMetadataValues(game: game(collections: [a, b])), remote: remote)
   try check(same.patch.isEmpty && same.conflicts.isEmpty, "equivalent memberships conflicted")
   try check(same.merged.collectionIDs == [a, b], "membership ordering is nondeterministic")
   let conflict = try sync.reconcile(gameID: gameID, local: AccountMetadataValues(game: game(collections: [a])), remote: remote)
   try check(conflict.conflicts == [.collectionIDs] && conflict.patch.collectionIDs == nil, "membership conflict silently overwrote")
   let safe = try sync.reconcile(gameID: gameID, local: AccountMetadataValues(game: game(collections: [b, a])),
    remote: document(identity, [game()], collections: [a, b]))
   try check(safe.patch.collectionIDs == [a, b], "patch membership ordering is nondeterministic")
   let body = safe.patch.body
   try check(body["revision"] as? Int == 0 && body["collectionIDs"] as? [String] == [a.uuidString, b.uuidString]
    && body["title"] == nil && body["favorite"] == nil, "wire patch contains unrelated fields")
  }

  // Lost response followed by a refetch matching the intent must not upload twice.
  do {
   let (sync, _) = try makeSync("lost-response")
   let local = AccountMetadataValues(game: game(title: "Saved"))
   let response = game(title: "Saved", revision: 1)
   let plan = try sync.reconcile(gameID: gameID, local: local, remote: document(identity, [response]))
   try check(plan.patch.isEmpty && plan.conflicts.isEmpty, "already applied edit was queued/conflicted")
   try sync.acknowledge(plan, response: response)
   try check(sync.baseline(for: gameID)?.revision == 1, "observed success did not checkpoint")
  }

  // An ACK only confirms the captured local value, not later UI edits.
  do {
   let (sync, _) = try makeSync("in-flight")
   let plan = try sync.reconcile(gameID: gameID, local: AccountMetadataValues(game: game(title: "Sent")),
    remote: document(identity, [game()]))
   try rejects { try sync.acknowledge(plan, response: game(title: "Wrong", revision: 1)) }
   try check(sync.baseline(for: gameID)?.revision == 0, "invalid ACK modified baseline")
   try sync.acknowledge(plan, response: game(title: "Sent", revision: 1))
   let next = try sync.reconcile(gameID: gameID, local: AccountMetadataValues(game: game(title: "Edited while waiting")),
    remote: document(identity, [game(title: "Sent", revision: 1)]))
   try check(next.patch.title == "Edited while waiting" && next.patch.revision == 1, "in-flight local edit was acknowledged unsent")
   try rejects { try sync.acknowledge(plan, response: game(title: "Sent", revision: 1)) }
  }

  // Applying the merged view must not make an adopted remote title a local edit.
  do {
   let (sync, _) = try makeSync("adopt-remote")
   let plan = try sync.reconcile(gameID: gameID, local: AccountMetadataValues(game: game(favorite: true)),
    remote: document(identity, [game(title: "Remote title", revision: 1)]))
   try check(plan.merged.title == "Remote title" && plan.patch.title == nil, "merge lost remote title")
   try sync.acknowledge(plan, response: game(title: "Remote title", revision: 2, favorite: true), applyingRemoteFields: true)
   let next = try sync.reconcile(gameID: gameID, local: plan.merged,
    remote: document(identity, [game(title: "New remote title", revision: 3, favorite: true)]))
   try check(next.conflicts.isEmpty && next.patch.isEmpty && next.merged.title == "New remote title",
    "adopting a remote field created a false conflict on the next edit")
  }

  // Explicit local choice patches only conflicts and preserves unrelated remote edits.
  do {
   let (sync, profile) = try makeSync("resolve-local")
   let local = AccountMetadataValues(game: game(title: "Mine"))
   let fresh = document(identity, [game(title: "Theirs", revision: 1, favorite: true)])
   let conflict = try sync.reconcile(gameID: gameID, local: local, remote: fresh)
   let resolved = try sync.resolve(conflict, choice: .keepLocal, confirmedRevision: 1, local: local, remote: fresh)
   try check(resolved.conflicts.isEmpty && resolved.patch.title == "Mine" && resolved.patch.favorite == nil,
    "local resolution overwrote an unrelated remote field")
   try check(resolved.patch.revision == 1 && resolved.merged.favorite, "local resolution lost exact CAS or remote edit")
   try sync.acknowledge(resolved, response: game(title: "Mine", revision: 2, favorite: true))
   let reload = try AccountMetadataSync(root: profile, identity: identity)
   let retry = try reload.reconcile(gameID: gameID, local: local,
    remote: document(identity, [game(title: "Mine", revision: 2, favorite: true)]))
   try check(retry.patch.isEmpty && retry.conflicts.isEmpty, "resolved local ACK was not durable")
  }

  // Server choice drops only conflicting intent; a safe favorite still uploads.
  do {
   let (sync, profile) = try makeSync("resolve-server")
   let local = AccountMetadataValues(game: game(title: "Mine", favorite: true))
   let fresh = document(identity, [game(title: "Theirs", revision: 1)])
   let conflict = try sync.reconcile(gameID: gameID, local: local, remote: fresh)
   let resolved = try sync.resolve(conflict, choice: .useServer, confirmedRevision: 1, local: local, remote: fresh)
   try check(resolved.conflicts.isEmpty && resolved.patch.title == nil && resolved.patch.favorite == true,
    "server resolution discarded an independent local field")
   try check(resolved.merged.title == "Theirs" && resolved.merged.favorite, "server resolution merged wrong values")
   try sync.acknowledge(resolved, response: game(title: "Theirs", revision: 2, favorite: true))
   let reload = try AccountMetadataSync(root: profile, identity: identity)
   let retry = try reload.reconcile(gameID: gameID, local: local,
    remote: document(identity, [game(title: "Theirs", revision: 2, favorite: true)]))
   try check(retry.conflicts.isEmpty && retry.patch.isEmpty, "old unprojected draft resurrected after server choice")
  }

  // Dialog confirmation binds both revision and displayed fields, plus captured local intent.
  do {
   let (sync, profile) = try makeSync("resolve-stale")
   let local = AccountMetadataValues(game: game(title: "Mine"))
   let fresh = document(identity, [game(title: "Theirs", revision: 1)])
   let conflict = try sync.reconcile(gameID: gameID, local: local, remote: fresh)
   let file = profile.appendingPathComponent("metadata-baselines.json")
   let before = try Data(contentsOf: file)
   try rejects { _ = try sync.resolve(conflict, choice: .keepLocal, confirmedRevision: 0, local: local, remote: fresh) }
   try rejects { _ = try sync.resolve(conflict, choice: .keepLocal, confirmedRevision: 1, local: local,
    remote: document(identity, [game(title: "Theirs", revision: 2)])) }
   try rejects { _ = try sync.resolve(conflict, choice: .keepLocal, confirmedRevision: 1, local: local,
    remote: document(identity, [game(title: "New title at same revision", revision: 1)])) }
   try rejects { _ = try sync.resolve(conflict, choice: .useServer, confirmedRevision: 1,
    local: AccountMetadataValues(game: game(title: "Edited during dialog")), remote: fresh) }
   let foreign = try AccountLibraryIdentity(serverID: identity.serverID, accountID: "bob")
   try rejects { _ = try sync.resolve(conflict, choice: .useServer, confirmedRevision: 1, local: local,
    remote: document(foreign, [game(title: "Theirs", revision: 1)])) }
   try check(try Data(contentsOf: file) == before, "rejected/staged choice mutated durable baselines")
  }

  // After safe fields ACK, replan before resolving; server-only choice needs no POST.
  do {
   let (sync, _) = try makeSync("resolve-after-partial")
   let local = AccountMetadataValues(game: game(title: "Mine", favorite: true))
   let fresh = document(identity, [game(title: "Theirs", revision: 1)])
   let conflict = try sync.reconcile(gameID: gameID, local: local, remote: fresh)
   try sync.acknowledge(conflict, response: game(title: "Theirs", revision: 2, favorite: true))
   try rejects { _ = try sync.resolve(conflict, choice: .useServer, confirmedRevision: 1, local: local, remote: fresh) }
   let current = document(identity, [game(title: "Theirs", revision: 2, favorite: true)])
   let replanned = try sync.reconcile(gameID: gameID, local: local, remote: current)
   let resolved = try sync.resolve(replanned, choice: .useServer, confirmedRevision: 2, local: local, remote: current)
   try check(resolved.patch.isEmpty && resolved.conflicts.isEmpty, "server-only choice generated a mutation")
   try sync.acknowledge(resolved, response: current.games[0])
   let retry = try sync.reconcile(gameID: gameID, local: local, remote: current)
   try check(retry.patch.isEmpty && retry.conflicts.isEmpty, "server-only choice was not checkpointed")
  }

  // Unreadable persisted state is not treated as a fresh account.
  do {
   let (_, profile) = try makeSync("corrupt")
   let file = profile.appendingPathComponent("metadata-baselines.json")
   let corrupt = Data("corrupt".utf8)
   try corrupt.write(to: file)
   try rejects { _ = try AccountMetadataSync(root: profile, identity: identity) }
   try check(try Data(contentsOf: file) == corrupt, "corrupt baseline was overwritten")
  }
  print("PASS AccountMetadataSync: independent edits, explicit conflicts, durable partial ACK retries, identity isolation, deterministic memberships, lost ACK and in-flight edits")
 }
}
