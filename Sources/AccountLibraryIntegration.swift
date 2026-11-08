import AppKit

private struct AccountLibraryMutation:Decodable {let game:AccountLibraryGame}
private struct AccountTimeAcknowledgment:Decodable {let playtimeSeconds:Double;let lastPlayed:Double?}
private struct AccountLibraryOK:Decodable {}

/// One coordinator per authenticated account. Captured requests and stores never
/// change identity while in flight; old completions cannot populate a new user.
@MainActor
final class AccountLibraryController {
 weak var app:App?
 let connection:SharedConnection
 let token:String
 let identity:AccountLibraryIdentity
 let api:SharedAPI
 private(set) var document:AccountLibraryDocument?
 private(set) var profileRoot:URL?
 private(set) var active=false
 private var closed=false
 private var refreshing=false
 private var refreshQueued=false
 private var mutationRevision:UInt64=0
 private var applying=false
 private var refreshTimer:Timer?
 private var pendingTimer:Timer?
 private var outbox:AccountPlaytimeOutbox?
 private var metadataSync:AccountMetadataSync?
 private var localToCanonical:[UUID:String]=[:]
 private var unavailable=Set<UUID>()
 private var totals:[String:Double]=[:]
 private var discovery:AccountDiscoveryWindowController?
 private var aliases:[String:String]=[:]
 private var coverAttempts=Set<String>()
 private let verifyOwnerHistory:AccountOwnerTransitionGate.VerifyHistoricalReceipt?

 init(app:App,connection:SharedConnection,token:String,verifyOwnerHistory:AccountOwnerTransitionGate.VerifyHistoricalReceipt?=nil)throws {
  self.verifyOwnerHistory=verifyOwnerHistory
  self.app=app;self.connection=connection;self.token=token
  identity=try AccountLibraryIdentity(serverID:connection.serverID,accountID:connection.accountID)
  api=try SharedAPI(address:connection.address,certificateSHA256:connection.certificateSHA256)
 }
 deinit{refreshTimer?.invalidate();pendingTimer?.invalidate()}
 private var current:Bool{!closed && app?.accountLibrary === self}
 var canonicalGames:[AccountLibraryGame]{document?.games ?? []}
 func canonicalID(for localID:UUID)->String?{localToCanonical[localID]}
 func game(for localID:UUID)->AccountLibraryGame?{canonicalID(for:localID).flatMap{id in document?.games.first{$0.id==id}}}
 func launchIssue(for localID:UUID)->String?{unavailable.contains(localID) ? "没有适用于当前系统的可用版本。":nil}
 func load(remember:Bool){
  Task { [weak self] in
   guard let self,let app=self.app else{return}
   do {
    let remote:AccountLibraryDocument=try await self.api.value("/api/library",token:self.token)
    try remote.validate(identity:self.identity)
    guard self.current else{return}
    let binding=(try PersonalLibraryBindingStore(root:app.root)).binding
    let owner=try binding.map{try AccountLibraryIdentity(serverID:$0.serverID,accountID:$0.accountID)}
    let profile=try AccountLibraryProfile.open(under:app.root,identity:self.identity,legacyOwner:owner)
    let cached=try AccountLibraryCache(root:profile,identity:self.identity)
    let approved=try AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:self.identity,savedBinding:binding,verifyHistoricalReceipt:self.verifyOwnerHistory)
    self.aliases=Dictionary(uniqueKeysWithValues:approved.map{($0.key.uuidString,$0.value)})
    let local=try LibraryStore(root:profile)
    try AccountLibraryProjection.recover(store:local,identity:self.identity)
    self.profileRoot=profile
    self.metadataSync=try AccountMetadataSync(root:profile,identity:self.identity)
    if let before=try cached.load(){
     self.document=before
     try await self.pushMetadata(store:local,baseline:before)
    }
    let latest:AccountLibraryDocument=try await self.api.value("/api/library",token:self.token)
    try latest.validate(identity:self.identity)
    guard self.current else{return}
    try self.metadataSync?.seed(latest)
    try self.apply(latest,to:local)
    let history=try PlaytimeStore(root:profile)
    let queue=try AccountPlaytimeOutbox(root:profile,identity:self.identity,deviceID:self.deviceID)
    if remember{try AccountLoginStore(root:app.root).save(AccountLoginRecord(connectionID:self.connection.id,identity:self.identity,token:self.token))}
    else{try AccountLoginStore(root:app.root).forgetRememberedLogin()}
    await MainActor.run {
     guard self.current else{return}
     self.outbox=queue;app.store=local;app.playtime=history;app.playtimeRecorder=PlaytimeRecorder(store:history)
     local.onChange={[weak self] in self?.scheduleSync()}
     app.filter="all";app.search.stringValue="";self.active=true
     self.applying=true;app.showLibrary();self.applying=false
     app.window.makeKeyAndOrderFront(nil);app.sharedLibraryWindow?.close();app.sharedLibraryWindow=nil
     app.settingsWindow?.close();app.settingsWindow=nil
     let timer=Timer(timeInterval:60,repeats:true){[weak self] _ in Task{@MainActor in self?.refresh()}};self.refreshTimer=timer;RunLoop.main.add(timer,forMode:.common)
     self.queueClosedSessions();self.refresh()
    }
   }catch{
    await MainActor.run {
     guard self.current else{return}
     self.active=false
     if (error as? SharedRequestError)?.statusCode==401 {try? AccountLoginStore(root:app.root).clear(identity:self.identity)}
     app.sharedLibraryWindow?.accountLibraryLoadFailed(error)
    }
   }
  }
 }
 private var deviceID:String {
  if let id=UserDefaults.standard.string(forKey:"SharedDeviceID"){return id}
  let id=UUID().uuidString;UserDefaults.standard.set(id,forKey:"SharedDeviceID");return id
 }
 func scheduleSync(){
  guard active,current,!applying else{return}
  pendingTimer?.invalidate()
  let timer=Timer(timeInterval:0.6,repeats:false){[weak self] _ in Task{@MainActor in self?.refresh()}};pendingTimer=timer;RunLoop.main.add(timer,forMode:.common)
 }
 func showDiscovery(){
  guard document?.user.admin==true,let app else{return}
  do{if discovery==nil{discovery=try AccountDiscoveryWindowController(app:app,connection:connection,changed:{[weak self] in self?.serverDidChange()})};discovery?.showWindow(nil);discovery?.window?.makeKeyAndOrderFront(nil)}catch{app.errorAlert(error)}
 }
 func close(){closed=true;refreshTimer?.invalidate();pendingTimer?.invalidate();discovery?.close();active=false}
 func serverDidChange(){mutationRevision &+= 1;refresh()}
 private func finishRefresh(){
  refreshing=false
  if refreshQueued{refreshQueued=false;scheduleSync()}
 }
 func refresh(){
  guard active,current,let app,let document else{return}
  guard !refreshing,!app.accountGameIsRunning else{refreshQueued=true;return}
  refreshing=true;queueClosedSessions()
  let storeRevision=app.store.changeRevision,serverRevision=mutationRevision
  Task { [weak self] in
   guard let self else{return}
   do {
    try await self.pushMetadata(store:app.store,baseline:document)
    var uploadFailure:Error?
    if let outbox=self.outbox {
     for event in outbox.pending {
      guard self.current else{return}
      do {
       // Persist uncertainty BEFORE the request: a lost ACK must not cause the
       // displayed authoritative total to count the same pending duration twice.
       try outbox.markAttempted(eventID:event.eventID)
       let ack:AccountTimeAcknowledgment=try await self.api.value("/api/library/games/"+event.canonicalID+"/playtime",method:"POST",token:self.token,body:event.body)
       guard ack.playtimeSeconds.isFinite,ack.playtimeSeconds>=0 else{throw LibraryError("时长同步返回无效。")}
       try outbox.acknowledge(eventID:event.eventID);self.totals[event.canonicalID]=ack.playtimeSeconds
      }catch{uploadFailure=error}
     }
    }
    let latest:AccountLibraryDocument=try await self.api.value("/api/library",token:self.token)
    try latest.validate(identity:self.identity)
    await MainActor.run {
     defer{self.finishRefresh()}
     guard self.current else{return}
     guard !app.accountGameIsRunning,app.store.changeRevision==storeRevision,self.mutationRevision==serverRevision else{self.refreshQueued=true;return}
     do{try self.apply(latest,to:app.store);self.applying=true;app.showLibrary();self.applying=false;self.fetchCovers();if let uploadFailure{app.showStatus("时长等待同步："+uploadFailure.localizedDescription)}}catch{app.showStatus(error.localizedDescription)}
    }
   }catch{await MainActor.run{self.finishRefresh();if self.current{app.showStatus(error.localizedDescription)}}}
  }
 }
 func chooseEntry(_ id:UUID,platform:String,variant:AccountLibraryVariant?,entry:Candidate?){
  guard active,current,let app,let canonical=canonicalID(for:id) else{return}
  guard !app.accountGameIsRunning else{app.errorAlert(LibraryError("请先保存并退出正在运行的游戏。"));return}
  if let variant,let entry {
   guard variant.platform==platform,AccountLibraryGame.safeEntry(entry,platform:platform),game(for:id)?.variants.contains(where:{$0.id==variant.id && $0.platform==platform && $0.entries.contains(entry)})==true else{return}
  }
  let body:[String:Any]=["platform":platform,"variantID":variant.map{$0.id as Any} ?? NSNull(),"entryPath":entry.map{$0.path as Any} ?? NSNull()]
  Task { [weak self] in
   guard let self else{return}
   do{let response:AccountLibraryMutation=try await self.api.value("/api/library/games/"+canonical+"/launch-preferences",method:"POST",token:self.token,body:body);guard response.game.id==canonical else{throw LibraryError("游戏版本确认不匹配。")};if self.current{self.serverDidChange()}}
   catch{if self.current{app.errorAlert(error)}}
  }
 }
 func publishDefaultCover(_ game:Game,file:URL){
  guard active,current,connection.isAdministrator,let variant=game.shared?.gameID else{return}
  Task { [weak self] in
   guard let self else{return}
   do{
    let payload=try LibraryCoverPayload.thumbnail(at:file)
    let result:LibraryCoverPayload=try await self.api.value("/api/admin/games/"+variant+"/cover",method:"POST",token:self.token,body:["data":payload.data])
    _ = try result.checkedBytes(expectedHash:payload.sha256)
    if self.current{self.serverDidChange()}
   }catch{if self.current{self.app?.showStatus("封面尚未同步："+error.localizedDescription)}}
  }
 }
 func removeGame(_ id:UUID,completed:@escaping()->Void){
  guard active,current,let app,let canonical=canonicalID(for:id) else{return}
  guard !app.accountGameIsRunning else{app.errorAlert(LibraryError("请先保存并退出正在运行的游戏。"));return}
  Task { [weak self] in
   guard let self else{return}
   do{
    let _:AccountLibraryOK=try await self.api.value("/api/library/games/"+canonical,method:"DELETE",token:self.token)
    guard self.current else{return}
    completed();self.serverDidChange()
   }catch{if self.current{app.errorAlert(error)}}
  }
 }
 func queueClosedSessions(){
  guard active,current,let app,let outbox,let document else{return}
  let history=AccountLibraryHistoryLinks.make(visible:localToCanonical,aliases:aliases,document:document)
  do{for (id,canonical) in history{for session in app.playtime?.records(gameID:id) ?? []{try outbox.enqueue(session:session,canonicalID:canonical)}}}catch{app.showStatus(error.localizedDescription)}
 }
 func displayedSeconds(for id:UUID)->Double? {
  guard let canonical=localToCanonical[id],let app else{return nil}
  let base=totals[canonical] ?? game(for:id)?.playtimeSeconds ?? 0
  let pending=outbox?.unsubmitted.filter{$0.canonicalID==canonical}.reduce(0){$0+$1.elapsedSeconds} ?? 0
  let live=(app.playtime?.records(gameID:id) ?? []).filter{$0.endedAt==nil}.reduce(0){$0+$1.durationSeconds}
  return base+pending+live
 }
 private func apply(_ remote:AccountLibraryDocument,to store:LibraryStore)throws {
  guard let app,profileRoot != nil else{throw LibraryError("账号资料尚未准备。")}
  try remote.validate(identity:identity)
  let old=store.games;var next:[Game]=[],mapping:[UUID:String]=[:],unsupported=Set<UUID>(),used=Set<UUID>()
  for item in remote.games {
   let editionIDs=Set(item.variants.map(\.id))
   let previous=old.first{aliases[$0.id.uuidString]==item.id || ($0.shared?.serverID==identity.serverID && $0.shared?.accountID==identity.accountID && editionIDs.contains($0.shared?.gameID ?? ""))}
   let selected=try? item.selection(platform:"macos")
   let variant=selected?.variant ?? item.variants.first
   let entry=selected?.entry ?? Candidate(path:".",backend:.native,engine:"未提供 macOS 版本")
   let ref=SharedGameReference(connectionID:connection.id,serverID:identity.serverID,accountID:identity.accountID,gameID:variant?.id ?? item.id)
   var value=previous ?? Game(id:identity.gameIdentity(item.id),title:item.title,folder:"",originalFolder:"gameshelf://"+identity.storageKey+"/"+item.id,entry:entry)
   value.title=item.title;value.favorite=item.favorite;value.lastPlayed=item.lastPlayed.map(Date.init(timeIntervalSince1970:));value.entry=entry;value.shared=ref
   value.folder=app.root.appendingPathComponent("NetworkMounts/"+ref.storageKey).path
   next.append(value);mapping[value.id]=item.id;used.insert(value.id)
   if selected==nil{unsupported.insert(value.id)}
  }
  // Owner-only legacy entries remain available until their exact NAS root/entry
  // has been registered and linked. Foreign/revoked shared records never leak.
  next += old.filter{!used.contains($0.id) && $0.shared==nil && aliases[$0.id.uuidString]==nil}
  var groups=remote.collections.map{c in GameCollection(id:c.id,name:c.name,gameIDs:remote.games.filter{$0.collectionIDs.contains(c.id)}.compactMap{r in mapping.first{$0.value==r.id}?.key})}
  let retained=Set(next.map(\.id)).subtracting(Set(mapping.keys))
  for oldGroup in store.collections {
   let members=oldGroup.gameIDs.filter{retained.contains($0)}
   guard !members.isEmpty else{continue}
   if let i=groups.firstIndex(where:{$0.id==oldGroup.id || $0.name.caseInsensitiveCompare(oldGroup.name) == .orderedSame}){groups[i].gameIDs=Array(Set(groups[i].gameIDs+members)).sorted{$0.uuidString<$1.uuidString}}
   else{groups.append(GameCollection(id:oldGroup.id,name:oldGroup.name,gameIDs:members))}
  }
  applying=true;defer{applying=false}
  try AccountLibraryProjection.commit(remote,games:next,collections:groups,store:store,identity:identity)
  try acknowledgeProjection(remote,store:store)
  document=remote;localToCanonical=mapping;unavailable=unsupported;totals=Dictionary(uniqueKeysWithValues:remote.games.map{($0.id,$0.playtimeSeconds)})
 }
 private func pushMetadata(store:LibraryStore,baseline:AccountLibraryDocument)async throws {
  guard let sync=metadataSync else{return}
  try sync.seed(baseline)
  let local=store.games,groups=store.collections
  var fresh:AccountLibraryDocument=try await api.value("/api/library",token:token)
  try fresh.validate(identity:identity)
  for group in groups {
   let previous=baseline.collections.first(where:{$0.id==group.id})
   let current=fresh.collections.first(where:{$0.id==group.id})
   if current?.name==group.name{continue}
   if previous?.name==group.name{continue} // A remote rename is not a local edit.
   guard current?.name==previous?.name else{throw LibraryError("分类在其他设备上也已修改："+group.name)}
   let _:AccountLibraryOK=try await api.value("/api/library/collections",method:"POST",token:token,body:["id":group.id.uuidString,"name":group.name,"expectedName":current.map{$0.name as Any} ?? NSNull()])
  }
  fresh=try await api.value("/api/library",token:token)
  try fresh.validate(identity:identity);try sync.seed(fresh)
  for remote in fresh.games {
   let ids=Set(remote.variants.map(\.id))
   guard let game=local.first(where:{aliases[$0.id.uuidString]==remote.id || localToCanonical[$0.id]==remote.id || ($0.shared?.serverID==identity.serverID && $0.shared?.accountID==identity.accountID && ids.contains($0.shared?.gameID ?? ""))}) else{continue}
   let values=AccountMetadataValues(title:game.title,favorite:game.favorite,collectionIDs:groups.filter{$0.gameIDs.contains(game.id)}.map(\.id))
   var completed=false
   for attempt in 0..<3 {
    var plan=try sync.reconcile(gameID:remote.id,local:values,remote:fresh)
    if !plan.conflicts.isEmpty {
     let choice=try confirmConflict(plan,title:game.title,store:store)
     let confirmed:AccountLibraryDocument=try await api.value("/api/library",token:token)
     try confirmed.validate(identity:identity)
     guard self.current,let latestLocal=store.games.first(where:{$0.id==game.id}) else{throw CancellationError()}
     let currentValues=AccountMetadataValues(title:latestLocal.title,favorite:latestLocal.favorite,collectionIDs:store.collections.filter{$0.gameIDs.contains(game.id)}.map(\.id))
     plan=try sync.resolve(plan,choice:choice,confirmedRevision:plan.patch.revision,local:currentValues,remote:confirmed)
     fresh=confirmed
    }
    guard let current=fresh.games.first(where:{$0.id==remote.id}) else{break}
    do {
     let acknowledged:AccountLibraryGame
     if plan.patch.isEmpty{acknowledged=current}
     else{let response:AccountLibraryMutation=try await api.value("/api/library/games/"+remote.id+"/metadata",method:"POST",token:token,body:plan.patch.body);acknowledged=response.game}
     try sync.acknowledge(plan,response:acknowledged)
     if !plan.conflicts.isEmpty{throw LibraryError("游戏资料在其他设备上也已修改，请处理冲突后重试："+game.title)}
     completed=true;break
    }catch let error as SharedRequestError where error.statusCode==409 && attempt<2 {
     fresh=try await api.value("/api/library",token:token);try fresh.validate(identity:identity)
    }
   }
   guard completed else{throw LibraryError("游戏资料已变化，请刷新后重试。")}
  }
  for collection in baseline.collections where !groups.contains(where:{$0.id==collection.id}) {
   if let current=fresh.collections.first(where:{$0.id==collection.id}),current.name != collection.name{throw LibraryError("待移除分类在其他设备上已修改："+collection.name)}
   let _:AccountLibraryOK=try await api.value("/api/library/collections/"+collection.id.uuidString,method:"DELETE",token:token)
  }
 }
 private func confirmConflict(_ plan:AccountMetadataReconciliation,title:String,store:LibraryStore)throws->AccountMetadataConflictChoice {
  guard current,let app,!app.accountGameIsRunning,let profile=profileRoot else{throw CancellationError()}
  let backup=profile.deletingLastPathComponent().appendingPathComponent("MigrationBackup/MetadataConflicts")
  try AccountLibraryDisk.directory(backup)
  let encoder=JSONEncoder();encoder.outputFormatting=[.sortedKeys]
  try AccountLibraryDisk.write(encoder.encode(plan.local),to:backup.appendingPathComponent(plan.gameID+"-"+UUID().uuidString+".json"))
  let fields=plan.conflicts.map{field in field == .title ? "名称":field == .favorite ? "收藏":"分类"}.joined(separator:"、")
  let alert=NSAlert();alert.messageText="「"+title+"」的"+fields+"有冲突"
  alert.addButton(withTitle:"保留此设备");alert.addButton(withTitle:"使用服务器");alert.addButton(withTitle:"取消同步")
  switch alert.runModal(){case .alertFirstButtonReturn:return .keepLocal;case .alertSecondButtonReturn:return .useServer;default:throw CancellationError()}
 }
 private func acknowledgeProjection(_ remote:AccountLibraryDocument,store:LibraryStore)throws {
  guard let sync=metadataSync else{return}
  try sync.seed(remote)
  for game in remote.games {
   let values=AccountMetadataValues(game:game)
   let plan=try sync.reconcile(gameID:game.id,local:values,remote:remote)
   guard plan.patch.isEmpty,plan.conflicts.isEmpty else{continue}
   try sync.acknowledge(plan,response:game,applyingRemoteFields:true)
  }
 }
 private func fetchCovers(){
  guard let app,let document,let profile=profileRoot else{return}
  for remote in document.games {
   guard let expected=remote.coverSHA256,let localID=localToCanonical.first(where:{$0.value==remote.id})?.key,let local=app.store.games.first(where:{$0.id==localID}),local.defaultCoverSHA256 != expected || local.cover==nil else{continue}
   Task { [weak self] in
    guard let self else{return}
    do{
     let payload:LibraryCoverPayload=try await self.api.value("/api/library/games/"+remote.id+"/cover",token:self.token)
     let bytes=try payload.checkedBytes(expectedHash:expected),folder=profile.appendingPathComponent("Covers")
     try AccountLibraryDisk.directory(folder);let target=folder.appendingPathComponent(remote.id+"-"+expected+".jpg");try AccountLibraryDisk.write(bytes,to:target)
     await MainActor.run{guard self.current,self.document?.games.first(where:{$0.id==remote.id})?.coverSHA256==expected,var game=app.store.games.first(where:{$0.id==localID}),game.cover==local.cover,game.defaultCoverSHA256==local.defaultCoverSHA256 else{return};game.cover=target.path;game.coverOrigin="shared-default";game.defaultCoverSHA256=expected;do{try app.store.update(game);app.renderCards()}catch{app.showStatus(error.localizedDescription)}}
    }catch{}
   }
  }
  if connection.isAdministrator,UserDefaults.standard.object(forKey:"AutoOnlineCover") as? Bool != false {
   for remote in document.games where remote.coverSHA256==nil {
    guard coverAttempts.insert(remote.id).inserted,let localID=localToCanonical.first(where:{$0.value==remote.id})?.key,let local=app.store.games.first(where:{$0.id==localID}) else{continue}
    if let cover=local.cover{publishDefaultCover(local,file:URL(fileURLWithPath:cover))}
    else{app.findCover(local,automatic:true)}
   }
  }
 }
}

extension App {
 var activeLibraryConnection:SharedConnection?{accountLibrary?.active==true ? accountLibrary?.connection:nil}
 var accountGameIsRunning:Bool {
  window?.attachedSheet != nil || busy || active != nil || pendingWebLaunch != nil || !wineSessions.isEmpty || running.values.contains{$0.isRunning} || nativeRunning.values.contains{$0.isRunning} || nativeApplications.values.contains{!$0.isTerminated} || !nativeLaunching.isEmpty || !sharedSessions.isEmpty
 }
 func beginAccountLibrary(){
  do{
   let connections=try SharedConnectionStore(root:root)
   if connections.connections.isEmpty {
    let choice=(try? Data(contentsOf:root.appendingPathComponent("onboarding.json"))).flatMap{try? JSONSerialization.jsonObject(with:$0) as? [String:Any]}
    if choice?["choice"] as? String=="local" {try activateLocalLibrary();return}
    if FileManager.default.fileExists(atPath:root.appendingPathComponent("library.json").path),choice==nil {try activateLocalLibrary();return}
    showOnboardingIfNeeded()
    if onboardingWindow != nil{return}
   }
   showAccountLogin()
   if let saved=try AccountLoginStore(root:root).load(),let connection=connections.connections.first(where:{$0.id==saved.connectionID && $0.serverID==saved.identity.serverID && $0.accountID==saved.identity.accountID}) {
    sharedAccessTokens[connection.id]=saved.token;didAuthenticateAccount(connection:connection,token:saved.token,rememberLogin:true)
   }
  }catch{showAccountLogin();sharedLibraryWindow?.accountLibraryLoadFailed(error)}
 }
 func activateLocalLibrary()throws {
  accountLibrary?.close();accountLibrary=nil
  store=try LibraryStore(root:root);playtime=try PlaytimeStore(root:root);playtimeRecorder=playtime.map(PlaytimeRecorder.init)
  for game in store.games{_ = try? applyKnownMetadata(game)}
  if !FileManager.default.fileExists(atPath:root.appendingPathComponent("collections.json").path){for name in ["SLG","RPG","视觉小说"]{_ = try store.createCollection(name:name)}}
  showLibrary();window.makeKeyAndOrderFront(nil)
 }
 func didAuthenticateAccount(connection:SharedConnection,token:String,rememberLogin:Bool){
  guard !accountGameIsRunning else{errorAlert(LibraryError("请先保存并退出正在运行的游戏。"));return}
  do{accountLibrary?.close();let controller=try AccountLibraryController(app:self,connection:connection,token:token);accountLibrary=controller;sharedAccessTokens[connection.id]=token;controller.load(remember:rememberLogin)}catch{sharedLibraryWindow?.accountLibraryLoadFailed(error)}
 }
 func requestAccountSwitch(){
  guard !accountGameIsRunning else{errorAlert(LibraryError("请先保存并退出正在运行的游戏。"));return}
  if let current=accountLibrary {
   current.queueClosedSessions()
   do{try AccountLoginStore(root:root).clear(identity:current.identity)}catch{errorAlert(error);return}
   let api=current.api,token=current.token;current.close()
   api.request("/api/logout",method:"POST",token:token){(_:Result<AccountLibraryOK,Error>) in}
   sharedAccessTokens.removeValue(forKey:current.connection.id)
  }
  accountLibrary=nil
  do{store=try LibraryStore(root:root.appendingPathComponent("SignedOut"));playtime=nil;playtimeRecorder=nil;showAccountLogin()}catch{errorAlert(error)}
 }
 func refreshAccountLibrary(){accountLibrary?.refresh()}
 func showAccountDiscovery(){if let account=accountLibrary,account.active{account.showDiscovery()}else if store.root==root{showDiscovery()}}
}
