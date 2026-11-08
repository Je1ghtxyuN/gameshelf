import Foundation

struct AccountLegacyEdition:Decodable {
 let id:String;let canonicalID:String;let candidateID:String;let rootID:String
 let relative:String;let signature:String;let entries:[Candidate];let savePaths:[SharedAdminSaveRule]
}
private struct AccountLegacyEditions:Decodable {let editions:[AccountLegacyEdition]}
private struct AccountLegacyLink:Decodable {let canonicalID:String;let variantIDs:[String]}
private struct AccountLegacyAdded:Decodable {let games:[AccountLibraryGame]}
struct AccountLegacyRegistrationResult {
 let document:AccountLibraryDocument
 let resolutions:[AccountLibraryMigrationResolution]
}

/// Stages exact original-owner source references on the server. This does NOT
/// switch the running client, apply local alias receipts, copy saves, or upload
/// playtime. The caller can commit receipts only after save migration succeeds.
@MainActor final class AccountLegacyRegistration {
 let identity:AccountLibraryIdentity
 private let api:SharedAPI
 private let token:String
 init(identity:AccountLibraryIdentity,api:SharedAPI,token:String){self.identity=identity;self.api=api;self.token=token}
 private func id(_ value:String)->Bool{value.count==32 && value.utf8.allSatisfy{(48...57).contains($0)||(97...102).contains($0)}}
 func stage(plan:AccountLibraryMigrationPlan,mappings:[AccountLibraryMigrationRoot],groups:[[UUID]]=[],stillCurrent:()->Bool={true})async throws->AccountLegacyRegistrationResult {
  guard plan.owner==identity,plan.selections.count<=200 else{throw LibraryError("迁移账号或选择无效。")}
  let localIDs=Set(plan.selections.map(\.localID));var groupedInputs=Set<UUID>()
  for group in groups{guard group.count>=2,group.count<=100,Set(group).count==group.count,group.allSatisfy({localIDs.contains($0) && groupedInputs.insert($0).inserted}) else{throw LibraryError("版本关联选择无效或重复。")}}
  var entriesByFolder:[String:String]=[:]
  for selection in plan.selections{let key=selection.rootID+":"+selection.relative;if let old=entriesByFolder[key],old != selection.entry.path{throw LibraryError("同一目录选择了多个默认启动入口，请先确认。")};entriesByFolder[key]=selection.entry.path}
  let before:AccountLibraryDocument=try await api.value("/api/library",token:token)
  try before.validate(identity:identity)
  guard before.user.admin,stillCurrent() else{throw LibraryError("仅原管理员账号可迁移此游戏库。")}
  let inventory:AccountLegacyEditions=try await api.value("/api/admin/library/editions",token:token)
  for edition in inventory.editions {guard [edition.id,edition.canonicalID,edition.candidateID,edition.rootID].allSatisfy(id) else{throw LibraryError("服务器版本标识无效。")}}
  var records:[UUID:(selection:AccountLibraryMigrationSelection,candidateID:String,variantID:String,canonicalID:String)]=[:]
  for selection in plan.selections {
   guard stillCurrent() else{throw CancellationError()}
   let candidates=mappings.filter{$0.serverID==identity.serverID && $0.rootID==selection.rootID}
   guard candidates.count==1,let mapping=candidates.first else{throw LibraryError("迁移目录映射缺失或存在歧义。")}
   let folder=URL(fileURLWithPath:mapping.localPath).appendingPathComponent(selection.relative).standardizedFileURL
   let fingerprint=try await Task.detached(priority:.userInitiated){try SharedEntryFingerprint.hash(folder:folder,entry:selection.entry)}.value
   guard stillCurrent() else{throw CancellationError()}
   let registered:SharedAdminCandidates=try await api.value("/api/admin/roots/"+selection.rootID+"/candidates",method:"POST",token:token,body:["games":[["relative":selection.relative,"entryPath":selection.entry.path,"entrySHA256":fingerprint]]])
   guard registered.entryFingerprintsVerified==true,registered.candidates.count==1,let candidate=registered.candidates.first,
         id(candidate.id),candidate.rootID==selection.rootID,candidate.relative==selection.relative,candidate.available,
         candidate.entries.contains(where:{$0.path==selection.entry.path && $0.backend==selection.entry.backend}) else{throw LibraryError("迁移入口指纹或目录确认不匹配。")}
   guard stillCurrent() else{throw CancellationError()}
   let oldCanonicals=Set(inventory.editions.filter{($0.candidateID==candidate.id || ($0.rootID==selection.rootID && $0.relative==selection.relative))}.map(\.canonicalID))
   guard oldCanonicals.count<=1 else{throw LibraryError("同一目录关联了多个已有游戏，请先选择保留的游戏身份。")}
   let published:SharedCatalogGame=try await api.value(SharedAdminPath.publish(candidate.id),method:"POST",token:token,body:[:])
   guard id(published.id),published.entries.contains(where:{$0.path==selection.entry.path && $0.backend==selection.entry.backend}) else{throw LibraryError("迁移发布的版本入口不匹配。")}
   guard stillCurrent() else{throw CancellationError()}
   let canonical=oldCanonicals.first ?? published.id
   if canonical != published.id {
    let linked:AccountLegacyLink=try await api.value("/api/admin/library/games/"+canonical+"/variants",method:"POST",token:token,body:["variantIDs":[published.id]])
    guard linked.canonicalID==canonical,linked.variantIDs.contains(published.id) else{throw LibraryError("已有游戏身份关联未确认。")}
   }
   records[selection.localID]=(selection,candidate.id,published.id,canonical)
  }
  // Grouping is an explicit caller-reviewed list of local IDs. Titles never
  // create an association here. All linking happens before preferences/time.
  var grouped=Set<UUID>()
  for group in groups {
   guard stillCurrent() else{throw CancellationError()}
   guard group.count>=2,group.count<=100,Set(group).count==group.count,group.allSatisfy({records[$0] != nil && grouped.insert($0).inserted}),let first=group.first,let target=records[first]?.canonicalID else{throw LibraryError("版本关联选择无效或重复。")}
   let variants=group.compactMap{records[$0]?.variantID}
   let linked:AccountLegacyLink=try await api.value("/api/admin/library/games/"+target+"/variants",method:"POST",token:token,body:["variantIDs":variants])
   guard linked.canonicalID==target,Set(variants).isSubset(of:Set(linked.variantIDs)) else{throw LibraryError("跨平台版本关联未确认。")}
   for local in group{records[local]?.canonicalID=target}
  }
  let sorted=records.values.sorted{$0.selection.localID.uuidString<$1.selection.localID.uuidString}
  for start in stride(from:0,to:sorted.count,by:100) {
   guard stillCurrent() else{throw CancellationError()}
   let batch=Array(sorted[start..<min(start+100,sorted.count)])
   let ids=Array(Set(batch.map(\.candidateID))).sorted()
   var paths:[String:String]=[:]
   for item in batch {
    if let previous=paths[item.candidateID],previous != item.selection.entry.path{throw LibraryError("同一目录选择了多个默认启动入口，请先确认。")}
    paths[item.candidateID]=item.selection.entry.path
   }
   let _:AccountLegacyAdded=try await api.value("/api/admin/library/add",method:"POST",token:token,body:["candidateIDs":ids,"userID":identity.accountID,"entryPaths":paths])
  }
  let result:AccountLibraryDocument=try await api.value("/api/library",token:token)
  try result.validate(identity:identity)
  var receipts:[AccountLibraryMigrationResolution]=[]
  for item in sorted {
   guard let game=result.games.first(where:{$0.id==item.canonicalID}),game.variants.contains(where:{$0.id==item.variantID && $0.entries.contains(where:{$0.path==item.selection.entry.path && $0.backend==item.selection.entry.backend})}) else{throw LibraryError("账号游戏库未确认迁移版本，原记录保留。")}
   receipts.append(AccountLibraryMigrationResolution(localID:item.selection.localID,selectionID:item.selection.selectionID,canonicalID:game.id,variantID:item.variantID))
  }
  guard stillCurrent() else{throw CancellationError()}
  return AccountLegacyRegistrationResult(document:result,resolutions:receipts)
 }
}
