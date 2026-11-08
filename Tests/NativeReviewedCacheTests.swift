import Foundation
@main struct NativeReviewedCacheTests {
 static func main()throws {
  let fm=FileManager.default,base=URL(fileURLWithPath:"/private/tmp/NativeReviewedCache-"+UUID().uuidString)
  defer{try? fm.removeItem(at:base)}
  let source=base.appendingPathComponent("Reviewed.app"),root=base.appendingPathComponent("Local")
  func write(_ path:String,_ data:Data)throws{let p=source.appendingPathComponent(path);try fm.createDirectory(at:p.deletingLastPathComponent(),withIntermediateDirectories:true);try data.write(to:p)}
  try write("Contents/Info.plist",PropertyListSerialization.data(fromPropertyList:["CFBundleExecutable":"engine","CFBundleIdentifier":"org.fixture.original"],format:.xml,options:0))
  try write("Contents/MacOS/engine",Data("inert fixture".utf8))
  try write("Contents/Resources/autorun/renpy/vc_version.py",Data("version='8.3.7'".utf8))
  try write("Contents/Resources/autorun/game/config.rpy",Data("inert fixture".utf8))
  let game=Game(title:"Reviewed fixture",folder:source.path,originalFolder:source.path,entry:Candidate(path:".",backend:.native,engine:"fixture"),shared:SharedGameReference(connectionID:UUID(),serverID:"server",accountID:"owner",gameID:String(repeating:"a",count:32)))
  let original=try Data(contentsOf:source.appendingPathComponent("Contents/Info.plist"))
  let profile="renpy-savedir-v1/"+String(repeating:"b",count:64);var verified=0
  let verify:(URL)throws->Void={stage in
   verified+=1
   guard try Data(contentsOf:stage.appendingPathComponent("Contents/Info.plist"))==original else{throw LibraryError("Verifier ran after metadata rewrite")}
  }
  let first=try NativeAppCache.prepare(game:game,root:root,reviewedRuntimeProfile:profile,verifyReviewedRuntime:verify)
  guard verified==1,first.cached,first.app != source else{throw LibraryError("Fresh private copy was not reviewed")}
  let second=try NativeAppCache.prepare(game:game,root:root,reviewedRuntimeProfile:profile,verifyReviewedRuntime:verify)
  guard verified==1,second.cached else{throw LibraryError("Reviewed cache receipt did not survive reuse")}
  let third=try NativeAppCache.prepare(game:game,root:root,reviewedRuntimeProfile:"renpy-savedir-v1/"+String(repeating:"c",count:64),verifyReviewedRuntime:verify)
  guard verified==2,third.cached else{throw LibraryError("Changed review reused an old cache")}
  let published=try Data(contentsOf:third.app.appendingPathComponent("Contents/Info.plist"))
  var refused=false
  do{_ = try NativeAppCache.prepare(game:game,root:root,reviewedRuntimeProfile:"renpy-savedir-v1/"+String(repeating:"d",count:64),verifyReviewedRuntime:{_ in throw LibraryError("fixture review refusal")})}catch{refused=true}
  guard refused,try Data(contentsOf:third.app.appendingPathComponent("Contents/Info.plist"))==published else{throw LibraryError("Failed review replaced the existing private copy")}
  let children=try fm.contentsOfDirectory(atPath:root.appendingPathComponent("NativeApps").path)
  guard !children.contains(where:{$0.hasPrefix(".preparing-")}) else{throw LibraryError("Failed review left an unpublished stage")}
  guard try Data(contentsOf:source.appendingPathComponent("Contents/Info.plist"))==original else{throw LibraryError("Source was modified")}
  print("PASS NativeReviewedCache: verify before identity/signing, pinned receipt, reuse, review change rebuild, failure preserves published cache and cleans stage, source unchanged")
 }
}
