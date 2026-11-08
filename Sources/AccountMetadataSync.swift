import Foundation

enum AccountMetadataField: String, Codable, CaseIterable {
 case title, favorite, collectionIDs
}

enum AccountMetadataConflictChoice {
 case keepLocal, useServer
}

/// Only account-editable fields. Membership is a set, serialized in UUID order.
struct AccountMetadataValues: Codable, Equatable {
 var title: String
 var favorite: Bool
 var collectionIDs: [UUID]

 init(title: String, favorite: Bool, collectionIDs: [UUID]) {
  self.title = title
  self.favorite = favorite
  self.collectionIDs = collectionIDs.sorted { $0.uuidString < $1.uuidString }
 }
 init(game: AccountLibraryGame) {
  self.init(title: game.title, favorite: game.favorite, collectionIDs: game.collectionIDs)
 }
 fileprivate func checked() throws -> Self {
  guard !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
        title.utf8.count <= 1024,
        !title.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains),
        collectionIDs.count <= 200, Set(collectionIDs).count == collectionIDs.count else {
   throw LibraryError("账号游戏资料无效，未同步。")
  }
  return Self(title: title, favorite: favorite, collectionIDs: collectionIDs)
 }
 fileprivate func matches(_ field: AccountMetadataField, _ other: Self) -> Bool {
  switch field {
  case .title: return title == other.title
  case .favorite: return favorite == other.favorite
  case .collectionIDs: return Set(collectionIDs) == Set(other.collectionIDs)
  }
 }
 fileprivate mutating func copy(_ field: AccountMetadataField, from other: Self) {
  switch field {
  case .title: title = other.title
  case .favorite: favorite = other.favorite
  case .collectionIDs: collectionIDs = other.collectionIDs
  }
 }
}

/// Local and remote can differ after ACK: an untouched local field may still
/// show an older remote value. Keeping both prevents that value becoming a new edit.
struct AccountMetadataBaseline: Codable, Equatable {
 let identity: AccountLibraryIdentity
 let gameID: String
 var revision: Int
 var local: AccountMetadataValues
 var remote: AccountMetadataValues
}

struct AccountMetadataPatch {
 let revision: Int
 fileprivate(set) var title: String?
 fileprivate(set) var favorite: Bool?
 fileprivate(set) var collectionIDs: [UUID]?
 var isEmpty: Bool { title == nil && favorite == nil && collectionIDs == nil }
 var body: [String: Any] {
  var result: [String: Any] = ["revision": revision]
  if let title { result["title"] = title }
  if let favorite { result["favorite"] = favorite }
  if let collectionIDs { result["collectionIDs"] = collectionIDs.map(\.uuidString) }
  return result
 }
}

struct AccountMetadataReconciliation {
 let gameID: String
 let local: AccountMetadataValues
 let remote: AccountMetadataValues
 /// Safe merged view; conflicting fields retain remote. `local` retains intent.
 let merged: AccountMetadataValues
 /// Stable order: title, favorite, collectionIDs. These fields are never patched.
 let conflicts: [AccountMetadataField]
 let patch: AccountMetadataPatch
 fileprivate let baseline: AccountMetadataBaseline
}

enum AccountMetadataReconciler {
 /// Pure three-way comparison. Collections are one field, not silently unioned.
 /// A 409 caller refetches and reconciles again; it must not just change revision.
 static func reconcile(baseline: AccountMetadataBaseline, local: AccountMetadataValues,
                       remote game: AccountLibraryGame) throws -> AccountMetadataReconciliation {
  guard game.id == baseline.gameID, baseline.revision >= 0, game.revision >= baseline.revision else {
   throw LibraryError("游戏资料身份或版本过旧，请重新获取。")
  }
  let local = try local.checked(), remote = try AccountMetadataValues(game: game).checked()
  let oldLocal = try baseline.local.checked(), oldRemote = try baseline.remote.checked()
  var merged = remote, conflicts: [AccountMetadataField] = []
  var patch = AccountMetadataPatch(revision: game.revision)
  for field in AccountMetadataField.allCases {
   guard !local.matches(field, oldLocal) else { continue }
   if local.matches(field, remote) { continue } // Already accepted, including a lost ACK.
   guard remote.matches(field, oldRemote) else { conflicts.append(field); continue }
   merged.copy(field, from: local)
   switch field {
   case .title: patch.title = local.title
   case .favorite: patch.favorite = local.favorite
   case .collectionIDs: patch.collectionIDs = local.collectionIDs
   }
  }
  return AccountMetadataReconciliation(gameID: game.id, local: local, remote: remote,
   merged: merged, conflicts: conflicts, patch: patch, baseline: baseline)
 }
}

/// One instance per account, used on the caller's serialized queue (e.g. MainActor).
/// No networking or LibraryStore mutations. Persist each ACK before sending the
/// next game. Apply `merged` only after a successful response (or an empty-patch
/// observation); preserve `local` intent for all reported conflicts.
final class AccountMetadataSync {
 private struct Record: Codable {
  var version = 1
  var identity: AccountLibraryIdentity
  var games: [String: AccountMetadataBaseline] = [:]
 }
 private let file: URL
 private let identity: AccountLibraryIdentity
 private var state: Record

 init(root: URL, identity: AccountLibraryIdentity) throws {
  _ = try AccountLibraryCache(root: root, identity: identity)
  self.identity = identity
  file = root.appendingPathComponent("metadata-baselines.json")
  if let bytes = try AccountLibraryDisk.regularData(file, limit: 4 * 1024 * 1024) {
   state = try JSONDecoder().decode(Record.self, from: bytes)
  } else { state = Record(identity: identity) }
  guard state.version == 1, state.identity == identity, state.games.count <= 1000 else {
   throw LibraryError("同步基线归属或格式无效，原件已保留。")
  }
  for (key, baseline) in state.games {
   guard Self.validID(key), key == baseline.gameID, baseline.identity == identity, baseline.revision >= 0 else {
    throw LibraryError("同步基线身份无效，原件已保留。")
   }
   _ = try baseline.local.checked()
   _ = try baseline.remote.checked()
  }
 }

 func baseline(for gameID: String) -> AccountMetadataBaseline? { state.games[gameID] }

 /// Seed only previously unknown games from a trusted pre-edit snapshot. Repeated
 /// seed calls never replace ACKs or resolve outstanding conflicts implicitly.
 func seed(_ document: AccountLibraryDocument) throws {
  try document.validate(identity: identity)
  var next = state
  for game in document.games where next.games[game.id] == nil {
   let values = try AccountMetadataValues(game: game).checked()
   next.games[game.id] = AccountMetadataBaseline(identity: identity, gameID: game.id,
    revision: game.revision, local: values, remote: values)
  }
  if next.games != state.games { try commit(next) }
 }

 /// `remote` must be a fresh identity-validated response after a CAS conflict.
 /// Upsert new collections first and include them in the fetched document.
 func reconcile(gameID: String, local: AccountMetadataValues,
                remote: AccountLibraryDocument) throws -> AccountMetadataReconciliation {
  try remote.validate(identity: identity)
  guard let game = remote.games.first(where: { $0.id == gameID }), let baseline = state.games[gameID],
        Set(local.collectionIDs).isSubset(of: Set(remote.collections.map(\.id))) else {
   throw LibraryError("缺少游戏同步基线、游戏权限或分类，请重新获取。")
  }
  return try AccountMetadataReconciler.reconcile(baseline: baseline, local: local, remote: game)
 }

 /// Stage an explicit per-game decision for exactly the revision/values shown
 /// in a confirmation dialog. No disk or network mutation occurs here. Back up
 /// the draft before choosing server; retain newer UI edits if the dialog raced.
 /// After a safe partial ACK, reconcile again before asking for a decision.
 /// A later POST 409 invalidates this choice: refetch and ask again, never replay
 /// a confirmed local overwrite against a different revision automatically.
 func resolve(_ plan: AccountMetadataReconciliation, choice: AccountMetadataConflictChoice,
              confirmedRevision: Int, local: AccountMetadataValues,
              remote: AccountLibraryDocument) throws -> AccountMetadataReconciliation {
  guard plan.baseline.identity == identity, state.games[plan.gameID] == plan.baseline,
        !plan.conflicts.isEmpty, confirmedRevision == plan.patch.revision,
        try local.checked() == plan.local else {
   throw LibraryError("冲突确认已过期或本地资料已变化，请重新确认。")
  }
  let current = try reconcile(gameID: plan.gameID, local: local, remote: remote)
  guard current.patch.revision == confirmedRevision, current.remote == plan.remote else {
   throw LibraryError("服务端资料在确认期间已变化，请重新获取并确认。")
  }
  var patch = current.patch, merged = current.merged
  if choice == .keepLocal {
   for field in current.conflicts {
    merged.copy(field, from: current.local)
    switch field {
    case .title: patch.title = current.local.title
    case .favorite: patch.favorite = current.local.favorite
    case .collectionIDs: patch.collectionIDs = current.local.collectionIDs
    }
   }
  }
  // Preserve the captured local draft as the local ACK anchor even for server
  // choice. Until projection adopts remote values, that rejected draft must not
  // look like a brand-new edit and resurrect itself on a retry/restart.
  return AccountMetadataReconciliation(gameID: current.gameID, local: current.local,
   remote: current.remote, merged: merged, conflicts: [], patch: patch, baseline: current.baseline)
 }

 /// For a nonempty patch use the successful mutation response. For an empty
 /// patch use the exact fetched game to checkpoint an already accepted edit.
 /// Keep the default when local storage still contains the captured local values.
 /// Set applyingRemoteFields only if local storage has adopted the acknowledged
 /// remote values for nonconflicting fields; this prevents adopted values from
 /// being misread as local edits later. Do not discard newer in-flight UI edits.
 /// Conflicting fields retain their older comparison anchors even if safe fields
 /// are acknowledged, so a subsequent retry cannot overwrite the conflict.
 func acknowledge(_ plan: AccountMetadataReconciliation, response: AccountLibraryGame,
                  applyingRemoteFields: Bool = false) throws {
  guard plan.baseline.identity == identity, state.games[plan.gameID] == plan.baseline,
        response.id == plan.gameID else {
   throw LibraryError("同步确认归属不匹配或已过期，未修改基线。")
  }
  let values = try AccountMetadataValues(game: response).checked()
  if plan.patch.isEmpty {
   guard response.revision == plan.patch.revision, values == plan.remote else {
    throw LibraryError("同步观察已过期，请重新获取。")
   }
  } else {
   guard response.revision > plan.patch.revision,
         plan.patch.title.map({ $0 == values.title }) ?? true,
         plan.patch.favorite.map({ $0 == values.favorite }) ?? true,
         plan.patch.collectionIDs.map({ Set($0) == Set(values.collectionIDs) }) ?? true else {
    throw LibraryError("服务端未确认所提交的资料，未修改基线。")
   }
  }
  var baseline = plan.baseline
  baseline.revision = response.revision
  for field in AccountMetadataField.allCases where !plan.conflicts.contains(field) {
   baseline.local.copy(field, from: applyingRemoteFields ? values : plan.local)
   baseline.remote.copy(field, from: values)
  }
  var next = state
  next.games[plan.gameID] = baseline
  try commit(next)
 }

 private static func validID(_ value: String) -> Bool {
  value.utf8.count == 32 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
 }
 private func commit(_ next: Record) throws {
  let encoder = JSONEncoder()
  encoder.outputFormatting = [.sortedKeys]
  let data = try encoder.encode(next)
  guard next.games.count <= 1000, data.count <= 4 * 1024 * 1024 else {
   throw LibraryError("同步基线过大，原记录已保留。")
  }
  try AccountLibraryDisk.write(data, to: file)
  state = next
 }
}
