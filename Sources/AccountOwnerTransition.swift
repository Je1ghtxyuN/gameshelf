import Foundation
import CryptoKit
import Darwin

/// Frozen coordinator bindings. mappingSHA256/evidenceSHA256 are externally
/// audited opaque digests, NOT hashes invented from a title or engine label.
struct AccountOwnerTransitionBinding:Codable,Equatable {
 var serverID:String;var accountID:String;var localID:String;var selectionID:String
 var canonicalID:String;var variantID:String;var candidateID:String;var rootID:String;var relative:String
 var sourceSignature:String;var reviewedProfileID:String;var profileVariantID:String;var profileRevision:Int
 var rulesSHA256:String;var evidenceSHA256:String;var sourceInventorySHA256:String;var mappingSHA256:String;var namespaceKey:String
 fileprivate func validate(owner:AccountLibraryIdentity)throws {
  guard serverID==owner.serverID,accountID==owner.accountID,let uuid=UUID(uuidString:localID),uuid.uuidString==localID,
        [canonicalID,variantID,candidateID,rootID].allSatisfy({OwnerTransitionCheck.hex($0,32)}),
        [selectionID,sourceSignature,rulesSHA256,evidenceSHA256,sourceInventorySHA256,mappingSHA256].allSatisfy({OwnerTransitionCheck.hex($0,64)}),
        OwnerTransitionCheck.tag(reviewedProfileID),OwnerTransitionCheck.tag(profileVariantID),profileRevision>0,
        relative=="." || OwnerTransitionCheck.relative(relative) else{throw LibraryError("Owner transition binding is incomplete or belongs to another owner.")}
  let prefix=serverID+"/"+accountID+"/"+canonicalID+"/"
  guard namespaceKey.hasPrefix(prefix),OwnerTransitionCheck.hex(String(namespaceKey.dropFirst(prefix.count)),64) else{throw LibraryError("Owner transition namespace does not match the exact account/game.")}
 }
}
/// Flat payload fields match the server coordinator envelope exactly.
struct AccountOwnerTransitionPayload:Codable,Equatable {
 var binding:AccountOwnerTransitionBinding
 var schemaVersion:Int;var kind:String;var status:String;var operationID:String
 var targetInventorySHA256:String;var importOperationID:String
 private enum CodingKeys:String,CodingKey{case schemaVersion,kind,status,operationID,targetInventorySHA256,importOperationID}
 init(binding:AccountOwnerTransitionBinding,schemaVersion:Int,kind:String,status:String,operationID:String,targetInventorySHA256:String,importOperationID:String){
  self.binding=binding;self.schemaVersion=schemaVersion;self.kind=kind;self.status=status;self.operationID=operationID;self.targetInventorySHA256=targetInventorySHA256;self.importOperationID=importOperationID
 }
 init(from decoder:Decoder)throws {
  binding=try AccountOwnerTransitionBinding(from:decoder)
  let values=try decoder.container(keyedBy:CodingKeys.self)
  schemaVersion=try values.decode(Int.self,forKey:.schemaVersion);kind=try values.decode(String.self,forKey:.kind);status=try values.decode(String.self,forKey:.status)
  operationID=try values.decode(String.self,forKey:.operationID);targetInventorySHA256=try values.decode(String.self,forKey:.targetInventorySHA256);importOperationID=try values.decode(String.self,forKey:.importOperationID)
 }
 func encode(to encoder:Encoder)throws {
  try binding.encode(to:encoder)
  var values=encoder.container(keyedBy:CodingKeys.self)
  try values.encode(schemaVersion,forKey:.schemaVersion);try values.encode(kind,forKey:.kind);try values.encode(status,forKey:.status);try values.encode(operationID,forKey:.operationID)
  try values.encode(targetInventorySHA256,forKey:.targetInventorySHA256);try values.encode(importOperationID,forKey:.importOperationID)
 }
}
/// Authoritative output of the TRUSTED internal coordinator verifier only.
/// Its implementation must verify HMAC, committed SQLite operation/receipt and
/// live target inventory, and return the digest of the exact envelope bytes.
/// The injected bridge calls verify_transition(operationID); no host path or
/// signing key is passed to a client-facing import endpoint.
/// A client-parsed envelope or caller-supplied `verified: true` is not this trust
/// boundary. No HMAC key is sent to this model; there is no signing API here.
struct AccountOwnerTransitionConfirmation:Codable,Equatable {
 let payload:AccountOwnerTransitionPayload
 let receiptSHA256:String
 let envelopeSHA256:String
}
/// Not Codable: only this gate can mint a write capability, after live verification.
struct AccountOwnerTransitionPermit {
 let localID:UUID
 let canonicalID:String
 let variantID:String
 let receiptSHA256:String
 fileprivate let binding:AccountOwnerTransitionBinding
 fileprivate let confirmation:AccountOwnerTransitionConfirmation
 fileprivate let root:URL
 fileprivate let historical:Bool
 fileprivate let revalidate:()throws->Void
 fileprivate init(root:URL,confirmation:AccountOwnerTransitionConfirmation,historical:Bool=false,revalidate:@escaping()throws->Void){
  self.root=root.standardizedFileURL;self.confirmation=confirmation;self.historical=historical;self.revalidate=revalidate
  binding=confirmation.payload.binding;localID=UUID(uuidString:binding.localID)!;canonicalID=binding.canonicalID;variantID=binding.variantID;receiptSHA256=confirmation.receiptSHA256
 }
}

/// One serialized owner. Initial verification and alias commit require the same
/// operator freeze. Historical authentication never compares/replays live saves.
final class AccountOwnerTransitionGate {
 typealias VerifyCommittedReceipt=(String)throws->AccountOwnerTransitionConfirmation
 /// Calls coordinator verify_history_transition, NOT verify_transition.
 typealias VerifyHistoricalReceipt=(String)throws->AccountOwnerTransitionConfirmation
 private struct Acceptance:Codable {var owner:AccountLibraryIdentity;var accepted:Bool;var confirmations:[AccountOwnerTransitionConfirmation]}
 private struct SealedAcceptance:Codable {var payload:Data;var mac:Data}
 private static let acceptanceName="owner-transition-accepted.json"
 private static let keyName="owner-transition-acceptance.key"
 private struct Stage:Codable {var owner:AccountLibraryIdentity;var original:Game;var expected:AccountOwnerTransitionBinding?}
 private struct Stages:Codable {var version=1;var owner:AccountLibraryIdentity;var records:[Stage]=[]}
 private struct Envelope:Decodable {
  struct Signature:Decodable{let algorithm:String;let keyID:String;let mac:String}
  let payload:AccountOwnerTransitionPayload;let receiptSHA256:String;let signature:Signature
 }
 private let root:URL,owner:AccountLibraryIdentity,binding:PersonalLibraryBinding
 private let verifyCommittedReceipt:VerifyCommittedReceipt?
 private let verifyHistoricalReceipt:VerifyHistoricalReceipt?
 private var stagesURL:URL{root.appendingPathComponent("owner-transition-staging.json")}
 init(profileRoot:URL,owner:AccountLibraryIdentity,savedBinding:PersonalLibraryBinding?,verifyCommittedReceipt:VerifyCommittedReceipt?=nil,verifyHistoricalReceipt:VerifyHistoricalReceipt?=nil)throws {
  guard let savedBinding,savedBinding.serverID==owner.serverID,savedBinding.accountID==owner.accountID else{throw LibraryError("Only the exact saved owner may transition original records.")}
  self.root=profileRoot;self.owner=owner;self.binding=savedBinding;self.verifyCommittedReceipt=verifyCommittedReceipt;self.verifyHistoricalReceipt=verifyHistoricalReceipt
  _ = try AccountLibraryCache(root:profileRoot,identity:owner)
 }
 func stage(original:Game,expected:AccountOwnerTransitionBinding?)throws {
  _ = try AccountLibraryCache(root:root,identity:owner)
  guard original.shared==nil else{throw LibraryError("Only an unchanged original record can be staged.")}
  if let expected{try expected.validate(owner:owner);guard expected.localID==original.id.uuidString else{throw LibraryError("Staged local UUID does not match.")}}
  var state=try loadStages();state.records.removeAll{$0.original.id==original.id}
  state.records.append(Stage(owner:owner,original:original,expected:expected))
  guard state.records.count<=1000 else{throw LibraryError("Too many staged owner transitions.")}
  let bytes=try JSONEncoder().encode(state);guard bytes.count<=4*1024*1024 else{throw LibraryError("Owner staging is too large.")}
  try AccountLibraryDisk.write(bytes,to:stagesURL)
 }
 /// Prepared file, SHA alone, rollback, missing audit or missing trusted bridge
 /// all throw. The caller retains the ORIGINAL record/path on every refusal.
 func authorize(original:Game,receiptURL:URL)throws->AccountOwnerTransitionPermit {
  _ = try AccountLibraryCache(root:root,identity:owner)
  guard let record=try loadStages().records.first(where:{$0.original.id==original.id}),let expected=record.expected,
        original.shared==nil,record.original.folder==original.folder,record.original.originalFolder==original.originalFolder,
        record.original.entry.path==original.entry.path,record.original.entry.backend==original.entry.backend,
        let verifier=verifyCommittedReceipt else{throw LibraryError("Original path retained: no exact audit and trusted committed-receipt verifier.")}
  try expected.validate(owner:owner)
  guard expected.localID==original.id.uuidString else{throw LibraryError("Staged receipt targets a different local UUID.")}
  let bytes=try readReceipt(receiptURL),envelope=try JSONDecoder().decode(Envelope.self,from:bytes)
  let payload=envelope.payload
  guard envelope.signature.algorithm=="hmac-sha256",OwnerTransitionCheck.tag(envelope.signature.keyID),OwnerTransitionCheck.hex(envelope.signature.mac,64),
        OwnerTransitionCheck.hex(envelope.receiptSHA256,64),payload.schemaVersion==1,payload.kind=="owner-save-migration",payload.status=="applied",
        OwnerTransitionCheck.hex(payload.operationID,32),OwnerTransitionCheck.hex(payload.targetInventorySHA256,64),OwnerTransitionCheck.hex(payload.importOperationID,64),payload.binding==expected else{throw LibraryError("Original path retained: receipt does not bind the complete expected owner/source/profile.")}
  let verified=try verifier(payload.operationID)
  guard verified.payload==payload,verified.receiptSHA256==envelope.receiptSHA256,verified.envelopeSHA256==OwnerTransitionCheck.sha(bytes),
        try readReceipt(receiptURL)==bytes else{throw LibraryError("Original path retained: committed server confirmation or durable receipt changed.")}
  return AccountOwnerTransitionPermit(root:root,confirmation:verified,revalidate:{
   guard try verifier(payload.operationID)==verified,try self.readReceipt(receiptURL)==bytes else{throw LibraryError("Adoption changed before local alias commit.")}
  })
 }
 /// New links require frozen live verification. Existing links may use the
 /// distinct history bridge and cannot be created/reassigned by that authority.
 @discardableResult func applyApproved(plan:AccountLibraryMigrationPlan,resolutions:[AccountLibraryMigrationResolution],document:AccountLibraryDocument,originals:[Game],receiptURLs:[UUID:URL])throws->[UUID:String] {
  guard plan.owner==owner,Set(originals.map(\.id)).count==originals.count else{throw LibraryError("Owner transition inputs changed.")}
  let manifest=try AccountLibraryMigrationManifest(profileRoot:root,owner:owner,savedBinding:binding)
  let next=try manifest.validate(plan:plan,resolutions:resolutions,document:document)
  let existing=try manifest.load()
  let permits=try next.keys.map{id -> AccountOwnerTransitionPermit in
   if let canonical=existing[id],let verifier=verifyHistoricalReceipt {
    let baseline=try Self.accepted(root:root,owner:owner,links:existing)
    guard let confirmation=baseline.confirmations.first(where:{$0.payload.binding.localID==id.uuidString}),confirmation.payload.binding.canonicalID==canonical,
          try verifier(confirmation.payload.operationID)==confirmation else{throw LibraryError("Existing adoption history does not match.")}
    return AccountOwnerTransitionPermit(root:root,confirmation:confirmation,historical:true,revalidate:{
     guard try verifier(confirmation.payload.operationID)==confirmation else{throw LibraryError("Existing adoption history changed before commit.")}
    })
   }
   guard let game=originals.first(where:{$0.id==id}),let url=receiptURLs[id] else{throw LibraryError("Original transition selection is missing.")}
   return try authorize(original:game,receiptURL:url)
  }
  return try manifest.apply(plan:plan,resolutions:resolutions,document:document,permits:permits)
 }
 /// Structural loading alone is never activation authority. Empty profiles need
 /// no bridge. Nonempty profiles require a private accepted baseline AND trusted
 /// immutable server history authentication; later gameplay is left untouched.
 static func loadApprovedAliases(profileRoot:URL,owner:AccountLibraryIdentity,savedBinding:PersonalLibraryBinding?,verifyHistoricalReceipt:VerifyHistoricalReceipt?=nil)throws->[UUID:String] {
  _ = try AccountLibraryCache(root:profileRoot,identity:owner)
  guard let bytes=try AccountLibraryDisk.regularData(profileRoot.appendingPathComponent("game-links.json"),limit:512*1024) else{return [:]}
  let raw=try JSONDecoder().decode([String:String].self,from:bytes)
  if raw.isEmpty{return [:]}
  let manifest=try AccountLibraryMigrationManifest(profileRoot:profileRoot,owner:owner,savedBinding:savedBinding)
  let links=try manifest.load()
  guard let verifier=verifyHistoricalReceipt else{throw LibraryError("Original profile retained: trusted historical adoption verifier is unavailable.")}
  let baseline=try accepted(root:profileRoot,owner:owner,links:links)
  for confirmation in baseline.confirmations {
   guard try verifier(confirmation.payload.operationID)==confirmation else{throw LibraryError("Accepted adoption history changed.")}
  }
  return links
 }
 /// Called only by the typed Manifest mutator, on one serialized owner.
 static func commitAliases(root:URL,owner:AccountLibraryIdentity,current:[UUID:String],next:[UUID:String],plan:AccountLibraryMigrationPlan,resolutions:[AccountLibraryMigrationResolution],permits:[AccountOwnerTransitionPermit])throws {
  guard permits.count==next.count,Set(permits.map(\.localID)).count==permits.count else{throw LibraryError("Every alias requires an exact gate-issued permit.")}
  let old=current.isEmpty ? nil : try accepted(root:root,owner:owner,links:current)
  for permit in permits {
   try permit.binding.validate(owner:owner)
   guard permit.root==root.standardizedFileURL,next[permit.localID]==permit.canonicalID else{throw LibraryError("Permit belongs to another profile or alias.")}
   if let resolution=resolutions.first(where:{$0.localID==permit.localID}) {
    guard let selection=plan.selections.first(where:{$0.localID==permit.localID}),permit.binding.selectionID==resolution.selectionID,
          permit.variantID==resolution.variantID,permit.binding.rootID==selection.rootID,permit.binding.relative==selection.relative else{throw LibraryError("Permit does not bind the exact selected variant/root/directory.")}
   }
   if permit.historical && current[permit.localID] != permit.canonicalID{throw LibraryError("Historical authority cannot create a new alias.")}
   if current[permit.localID] != nil {
    guard old?.confirmations.contains(permit.confirmation)==true else{throw LibraryError("Existing alias has no matching accepted adoption.")}
   }
   try permit.revalidate()
  }
  if next==current{return}
  let keyURL=root.appendingPathComponent(keyName)
  let key:Data
  if let existing=try AccountLibraryDisk.regularData(keyURL,limit:32){key=existing}
  else{key=Data((0..<32).map{_ in UInt8.random(in:0...255)});try AccountLibraryDisk.write(key,to:keyURL)}
  guard key.count==32 else{throw LibraryError("Invalid local acceptance key.")}
  var baseline=Acceptance(owner:owner,accepted:false,confirmations:permits.map(\.confirmation))
  func seal()throws {
   let payload=try JSONEncoder().encode(baseline)
   let mac=Data(HMAC<SHA256>.authenticationCode(for:payload,using:SymmetricKey(data:key)))
   let sealed=try JSONEncoder().encode(SealedAcceptance(payload:payload,mac:mac))
   guard sealed.count<=4*1024*1024 else{throw LibraryError("Adoption baseline is too large.")}
   try AccountLibraryDisk.write(sealed,to:root.appendingPathComponent(acceptanceName))
  }
  // Interrupted commits deny activation; history must never promote pending work.
  try seal()
  let encoder=JSONEncoder();encoder.outputFormatting=[.sortedKeys]
  try AccountLibraryDisk.write(encoder.encode(Dictionary(uniqueKeysWithValues:next.map{($0.key.uuidString,$0.value)})),to:root.appendingPathComponent("game-links.json"))
  baseline.accepted=true;try seal()
 }
 private static func accepted(root:URL,owner:AccountLibraryIdentity,links:[UUID:String])throws->Acceptance {
  guard let key=try AccountLibraryDisk.regularData(root.appendingPathComponent(keyName),limit:32),key.count==32,
        let bytes=try AccountLibraryDisk.regularData(root.appendingPathComponent(acceptanceName),limit:4*1024*1024) else{throw LibraryError("No durable accepted adoption baseline; originals retained.")}
  let sealed=try JSONDecoder().decode(SealedAcceptance.self,from:bytes)
  guard HMAC<SHA256>.isValidAuthenticationCode(sealed.mac,authenticating:sealed.payload,using:SymmetricKey(data:key)) else{throw LibraryError("Adoption acceptance baseline was modified.")}
  let baseline=try JSONDecoder().decode(Acceptance.self,from:sealed.payload)
  guard baseline.accepted,baseline.owner==owner,baseline.confirmations.count==links.count,
        Set(baseline.confirmations.map{$0.payload.binding.localID}).count==links.count else{throw LibraryError("Adoption acceptance baseline is incomplete.")}
  for confirmation in baseline.confirmations {
   let binding=confirmation.payload.binding;try binding.validate(owner:owner)
   guard let id=UUID(uuidString:binding.localID),links[id]==binding.canonicalID else{throw LibraryError("Alias differs from accepted adoption.")}
  }
  return baseline
 }
 private func loadStages()throws->Stages {
  guard let bytes=try AccountLibraryDisk.regularData(stagesURL,limit:4*1024*1024) else{return Stages(owner:owner)}
  let state=try JSONDecoder().decode(Stages.self,from:bytes)
  guard state.version==1,state.owner==owner,state.records.count<=1000,Set(state.records.map{$0.original.id}).count==state.records.count,state.records.allSatisfy({$0.owner==owner}) else{throw LibraryError("Staged owner identity changed.")}
  return state
 }
 private func readReceipt(_ url:URL)throws->Data {
  guard url.isFileURL else{throw LibraryError("Adoption receipt must be a durable private file.")}
  let fd=open(url.path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC)
  guard fd>=0 else{throw LibraryError("Cannot read adoption receipt without following links.")}
  defer{Darwin.close(fd)}
  var before=stat(),after=stat()
  guard fstat(fd,&before)==0,(before.st_mode&S_IFMT)==S_IFREG,before.st_uid==geteuid(),before.st_nlink==1,(before.st_mode&0o777)==0o600,before.st_size>0,before.st_size<=1024*1024 else{throw LibraryError("Adoption receipt owner, mode, type or size is unsafe.")}
  let handle=FileHandle(fileDescriptor:fd,closeOnDealloc:false),bytes=try handle.read(upToCount:1024*1024+1) ?? Data()
  guard fstat(fd,&after)==0,bytes.count==before.st_size,before.st_dev==after.st_dev,before.st_ino==after.st_ino,before.st_size==after.st_size,
        before.st_mtimespec.tv_sec==after.st_mtimespec.tv_sec,before.st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec,
        before.st_ctimespec.tv_sec==after.st_ctimespec.tv_sec,before.st_ctimespec.tv_nsec==after.st_ctimespec.tv_nsec else{throw LibraryError("Adoption receipt changed while reading.")}
  return bytes
 }
}
private enum OwnerTransitionCheck {
 static func sha(_ bytes:Data)->String{SHA256.hash(data:bytes).map{String(format:"%02x",$0)}.joined()}
 static func hex(_ value:String,_ count:Int)->Bool{value.utf8.count==count && value.utf8.allSatisfy{(48...57).contains($0)||(97...102).contains($0)}}
 static func tag(_ value:String)->Bool{!value.isEmpty && value.utf8.count<=128 && value.utf8.allSatisfy{(48...57).contains($0)||(65...90).contains($0)||(97...122).contains($0)||[45,46,95].contains($0)}}
 static func relative(_ value:String)->Bool{!value.isEmpty && value.utf8.count<=4096 && !value.contains("\\") && !value.contains(":") && !value.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains) && value.split(separator:"/",omittingEmptySubsequences:false).allSatisfy{!$0.isEmpty && $0 != "." && $0 != ".." && $0 != ".gameshelf-profile"}}
}
