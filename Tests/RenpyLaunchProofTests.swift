import Foundation
import CryptoKit
import Darwin

@main struct RenpyLaunchProofTests {
 static func main()throws {
  struct Failure:Error{let text:String}
  func check(_ value:Bool,_ text:String)throws{if !value{throw Failure(text:text)}}
  func rejects(_ label:String,_ body:()throws->Void)throws{do{try body()}catch{return};throw Failure(text:"Accepted invalid proof: "+label)}
  func sha(_ data:Data)->String{SHA256.hash(data:data).map{String(format:"%02x",$0)}.joined()}
  let fm=FileManager.default,root=URL(fileURLWithPath:"/private/tmp/RenpyLaunchProof-"+UUID().uuidString)
  try fm.createDirectory(at:root,withIntermediateDirectories:true);defer{try? fm.removeItem(at:root)}
  let view=root.appendingPathComponent("View")
  try fm.createDirectory(at:view,withIntermediateDirectories:true)
  let original=try Data(contentsOf:URL(fileURLWithPath:"Contracts/Fixtures/renpy-8.3.7/savelocation.py"))
  let patched=try RenpyAccountAdapter.patchSaveLocation(original)
  let namespace=SharedSaveNamespace(key:"server/owner/"+String(repeating:"a",count:32)+"/"+String(repeating:"b",count:64),relativePath:RenpyAccountAdapter.relativePath,adapter:"renpy-savedir-v1",reviewedProfileID:"reviewed-fixture",revision:3,rulesSHA256:String(repeating:"c",count:64))
  let entry=Candidate(path:"Game.exe",backend:.renpy,engine:"Ren’Py")
  let rolePaths=[("entry","Game.exe"),("bootstrap","renpy/bootstrap.py"),("arguments","renpy/arguments.py"),("main","renpy/main.py"),("locations","renpy/savelocation.py"),("persistent","renpy/persistent.py"),("tokens","renpy/savetoken.py"),("config","game/options.rpy")]
  var fingerprints:[[String:Any]]=[]
  for (role,path) in rolePaths {
   let data=role=="locations" ? patched:Data(("fixture-only "+role+"\n").utf8)
   let file=view.appendingPathComponent(path);try fm.createDirectory(at:file.deletingLastPathComponent(),withIntermediateDirectories:true);try data.write(to:file)
   fingerprints.append(["path":path,"role":role,"sourceSHA256":role=="locations" ? sha(original):sha(data),"exportSHA256":sha(data)])
  }
  let payload:[String:Any]=["adapter":namespace.adapter,"platform":"windows","entryPath":entry.path,"relativeSavePath":namespace.relativePath,"reviewedProfileID":namespace.reviewedProfileID,"revision":namespace.revision,"rulesSHA256":namespace.rulesSHA256,"evidenceSHA256":String(repeating:"d",count:64),"sourceSignature":String(repeating:"e",count:64),"fingerprints":fingerprints]
  func decode(_ value:[String:Any])throws->RenpyLaunchProof{try JSONDecoder().decode(RenpyLaunchProof.self,from:JSONSerialization.data(withJSONObject:value))}
  func validate(_ value:[String:Any])throws{try decode(value).validate(namespace:namespace,entry:entry,platform:"windows")}
  let proof=try decode(payload)
  try proof.validate(namespace:namespace,entry:entry,platform:"windows")
  try proof.verify(view:view)
  for (key,value) in [("adapter","other-adapter"),("platform","macos"),("entryPath","Other.exe"),("relativeSavePath","../other"),("reviewedProfileID","another-profile"),("rulesSHA256",String(repeating:"f",count:64))] {
   var bad=payload;bad[key]=value;try rejects(key){try validate(bad)}
  }
  var bad=payload;bad["revision"]=2;try rejects("revision mismatch"){try validate(bad)}
  try rejects("backend mismatch"){try proof.validate(namespace:namespace,entry:Candidate(path:entry.path,backend:.wine,engine:entry.engine),platform:"windows")}
  try rejects("platform mismatch"){try proof.validate(namespace:namespace,entry:entry,platform:"linux")}
  for key in ["rulesSHA256","evidenceSHA256","sourceSignature"] {
   bad=payload;bad[key]=String(repeating:"A",count:64);try rejects("uppercase "+key){try validate(bad)}
  }
  for role in rolePaths.map(\.0){bad=payload;bad["fingerprints"]=fingerprints.filter{$0["role"] as? String != role};try rejects("missing "+role){try validate(bad)}}
  for key in ["sourceSHA256","exportSHA256"] {
   var rows=fingerprints;rows[0][key]="not-a-sha";bad=payload;bad["fingerprints"]=rows;try rejects("invalid fingerprint "+key){try validate(bad)}
  }
  for path in ["../escape.py","/absolute.py","renpy/../main.py","renpy//main.py","renpy/./main.py","renpy\\main.py","C:main.py",".gameshelf-profile/token",".GAMESHELF-PROFILE/other","renpy/.gameshelf-profile/private","renpy/control\n.py"] {
   var rows=fingerprints;rows[1]["path"]=path;bad=payload;bad["fingerprints"]=rows;try rejects("unsafe path "+path){try validate(bad)}
  }
  var rows=fingerprints;var duplicate=rows[1];duplicate["path"]="RENPY/BOOTSTRAP.PY";duplicate["role"]="metadata";rows.append(duplicate)
  bad=payload;bad["fingerprints"]=rows;try rejects("case-fold duplicate"){try validate(bad)}
  rows=fingerprints
  for path in ["renpy/Straße.py","RENPY/STRASSE.PY"]{rows.append(["path":path,"role":"metadata","sourceSHA256":sha(Data()),"exportSHA256":sha(Data())])}
  bad=payload;bad["fingerprints"]=rows;try rejects("Unicode case-fold duplicate"){try validate(bad)}
  rows=fingerprints;rows[1]["role"]="unreviewed-role";bad=payload;bad["fingerprints"]=rows;try rejects("unknown role"){try validate(bad)}
  rows=fingerprints;var secondEntry=rows[0];secondEntry["path"]="Other.exe";rows.append(secondEntry);bad=payload;bad["fingerprints"]=rows;try rejects("duplicate entry role"){try validate(bad)}
  for (source,target) in [(sha(patched),sha(patched)),(String(repeating:"f",count:64),sha(patched)),(sha(original),sha(original))] {
   rows=fingerprints;rows[4]["sourceSHA256"]=source;rows[4]["exportSHA256"]=target;bad=payload;bad["fingerprints"]=rows;try rejects("unknown or unpatched locations"){try validate(bad)}
  }
  rows=fingerprints
  for index in 0..<120{rows.append(["path":"metadata/\(index).json","role":"metadata","sourceSHA256":sha(Data()),"exportSHA256":sha(Data())])}
  bad=payload;bad["fingerprints"]=rows;try validate(bad)
  rows.append(["path":"metadata/overflow.json","role":"metadata","sourceSHA256":sha(Data()),"exportSHA256":sha(Data())]);bad["fingerprints"]=rows;try rejects("129 fingerprints"){try validate(bad)}
  // Every one of the eight exported role files must be hashed, not just entry/locations.
  for (_,path) in rolePaths {
   let file=view.appendingPathComponent(path),bytes=try Data(contentsOf:file)
   try (bytes+Data("changed".utf8)).write(to:file)
   try rejects("mutated "+path){try proof.verify(view:view)}
   try bytes.write(to:file)
  }
  let config=view.appendingPathComponent("game/options.rpy"),configBytes=try Data(contentsOf:config)
  try fm.removeItem(at:config);try fm.createSymbolicLink(at:config,withDestinationURL:view.appendingPathComponent("Game.exe"))
  try rejects("leaf symlink"){try proof.verify(view:view)}
  try fm.removeItem(at:config);try configBytes.write(to:config)
  let gameDirectory=view.appendingPathComponent("game"),moved=root.appendingPathComponent("Moved")
  try fm.moveItem(at:gameDirectory,to:moved);try fm.createSymbolicLink(at:gameDirectory,withDestinationURL:moved)
  try rejects("parent symlink"){try proof.verify(view:view)}
  try fm.removeItem(at:gameDirectory);try fm.moveItem(at:moved,to:gameDirectory)
  let linkedView=root.appendingPathComponent("LinkedView")
  try fm.createSymbolicLink(at:linkedView,withDestinationURL:view)
  try rejects("view symlink"){try proof.verify(view:linkedView)}
  try fm.removeItem(at:config);try fm.createDirectory(at:config,withIntermediateDirectories:false)
  try rejects("directory instead of regular file"){try proof.verify(view:view)}
  try fm.removeItem(at:config);try check(mkfifo(config.path,0o600)==0,"fixture FIFO creation")
  try rejects("FIFO without blocking"){try proof.verify(view:view)}
  try fm.removeItem(at:config);try configBytes.write(to:config)
  for (path,size) in [("Game.exe",128*1024*1024+1),("game/options.rpy",8*1024*1024+1)] {
   let file=view.appendingPathComponent(path),bytes=try Data(contentsOf:file),handle=try FileHandle(forWritingTo:file)
   try handle.truncate(atOffset:UInt64(size));try handle.close()
   try rejects("oversized "+path){try proof.verify(view:view)}
   try bytes.write(to:file)
  }
  try proof.verify(view:view)
  // Native entry is an app path; its entry fingerprint is a regular launcher file.
  var native=payload;native["platform"]="macos";native["entryPath"]="Game.app"
  var nativeRows=fingerprints
  nativeRows[0]["path"]="Game.app/Contents/MacOS/Game"
  let nativeFile=view.appendingPathComponent("Game.app/Contents/MacOS/Game")
  try fm.createDirectory(at:nativeFile.deletingLastPathComponent(),withIntermediateDirectories:true)
  try Data(contentsOf:view.appendingPathComponent("Game.exe")).write(to:nativeFile)
  let infoBytes=try PropertyListSerialization.data(fromPropertyList:["CFBundleExecutable":"Game","CFBundlePackageType":"APPL"],format:.xml,options:0)
  let sourceInfo=view.appendingPathComponent("Game.app/Contents/Info.plist")
  try infoBytes.write(to:sourceInfo)
  nativeRows.append(["path":"Game.app/Contents/Info.plist","role":"metadata","sourceSHA256":sha(infoBytes),"exportSHA256":sha(infoBytes)])
  native["fingerprints"]=nativeRows
  let nativeProof=try decode(native)
  try nativeProof.validate(namespace:namespace,entry:Candidate(path:"Game.app",backend:.native,engine:"renpy"),platform:"macos")
  try nativeProof.verify(view:view)
  native["entryPath"]=".";nativeRows[0]["path"]="Contents/MacOS/Game"
  nativeRows[nativeRows.count-1]["path"]="Contents/Info.plist";native["fingerprints"]=nativeRows
  let dotProof=try decode(native)
  try dotProof.validate(namespace:namespace,entry:Candidate(path:".",backend:.native,engine:"macOS"),platform:"macos")
  for (_,path) in rolePaths where path != "Game.exe" {
   let destination=view.appendingPathComponent("Game.app").appendingPathComponent(path)
   try fm.createDirectory(at:destination.deletingLastPathComponent(),withIntermediateDirectories:true)
   try Data(contentsOf:view.appendingPathComponent(path)).write(to:destination)
  }
  try dotProof.verify(view:view.appendingPathComponent("Game.app"))
  // All eight roles must survive copying a nested app to a renamed private cache.
  var cachedPayload=native
  cachedPayload["entryPath"]="Game.app"
  cachedPayload["fingerprints"]=nativeRows.map{row -> [String:Any] in var row=row;row["path"]="Game.app/"+(row["path"] as! String);return row}
  let cachedProof=try decode(cachedPayload)
  try cachedProof.validate(namespace:namespace,entry:Candidate(path:"Game.app",backend:.native,engine:"renpy"),platform:"macos")
  try cachedProof.verify(view:view)
  let cache=root.appendingPathComponent("PrivateCache/NewName.app")
  try fm.createDirectory(at:cache.deletingLastPathComponent(),withIntermediateDirectories:true)
  try fm.copyItem(at:view.appendingPathComponent("Game.app"),to:cache)
  try cachedProof.verifyCachedApp(cache,entryPath:"Game.app")
  try dotProof.verifyCachedApp(cache,entryPath:".")
  try rejects("cached entry path mismatch"){try cachedProof.verifyCachedApp(cache,entryPath:"Different.app")}
  try rejects("outside-app privacy roles cannot be skipped"){try nativeProof.verifyCachedApp(cache,entryPath:"Game.app")}
  try rejects("Windows proof cannot validate native cache"){try proof.verifyCachedApp(cache,entryPath:entry.path)}
  let cachedPersistent=cache.appendingPathComponent("renpy/persistent.py"),cachedBytes=try Data(contentsOf:cachedPersistent)
  try (cachedBytes+Data("cache mutation".utf8)).write(to:cachedPersistent)
  try rejects("mutated renamed private cache"){try cachedProof.verifyCachedApp(cache,entryPath:"Game.app")}
  try cachedBytes.write(to:cachedPersistent)
  try fm.removeItem(at:cachedPersistent)
  try fm.createSymbolicLink(at:cachedPersistent,withDestinationURL:view.appendingPathComponent("renpy/persistent.py"))
  try rejects("cached file symlink"){try cachedProof.verifyCachedApp(cache,entryPath:"Game.app")}
  try fm.removeItem(at:cachedPersistent);try cachedBytes.write(to:cachedPersistent)
  try cachedProof.verifyCachedApp(cache,entryPath:"Game.app")
  // Regression: all reviewed hashes can match while plist selects unreviewed B.
  let mismatchInfo=try PropertyListSerialization.data(fromPropertyList:["CFBundleExecutable":"B"],format:.xml,options:0)
  try mismatchInfo.write(to:sourceInfo)
  try Data("reviewed A".utf8).write(to:view.appendingPathComponent("Game.app/Contents/MacOS/A"))
  try Data("unreviewed B".utf8).write(to:view.appendingPathComponent("Game.app/Contents/MacOS/B"))
  var mismatchRows=cachedPayload["fingerprints"] as! [[String:Any]]
  let entryIndex=mismatchRows.firstIndex{$0["role"] as? String=="entry"}!
  let metadataIndex=mismatchRows.firstIndex{$0["role"] as? String=="metadata"}!
  mismatchRows[entryIndex]["path"]="Game.app/Contents/MacOS/A"
  mismatchRows[entryIndex]["sourceSHA256"]=sha(Data("reviewed A".utf8));mismatchRows[entryIndex]["exportSHA256"]=sha(Data("reviewed A".utf8))
  mismatchRows[metadataIndex]["sourceSHA256"]=sha(mismatchInfo);mismatchRows[metadataIndex]["exportSHA256"]=sha(mismatchInfo)
  var mismatchJSON=cachedPayload;mismatchJSON["fingerprints"]=mismatchRows
  let mismatchProof=try decode(mismatchJSON)
  let mismatchedStage=root.appendingPathComponent("MismatchStage.app")
  try fm.copyItem(at:view.appendingPathComponent("Game.app"),to:mismatchedStage)
  try rejects("proof hashes A but source Info.plist launches B"){try mismatchProof.verify(view:view)}
  try rejects("proof hashes A but cached Info.plist launches B"){try mismatchProof.verifyCachedApp(mismatchedStage,entryPath:"Game.app")}
  try rejects("pretransform cache/source A-B mismatch"){try mismatchProof.verifyCachedApp(mismatchedStage,entryPath:"Game.app",sourceView:view)}
  try infoBytes.write(to:sourceInfo)
  var noMetadata=cachedPayload
  noMetadata["fingerprints"]=(cachedPayload["fingerprints"] as! [[String:Any]]).filter{$0["role"] as? String != "metadata"}
  try rejects("native Info.plist must have exact metadata proof"){try decode(noMetadata).verify(view:view)}
  // Production cache layout: only config may live beneath the exact payload link.
  let payloadRelative="Contents/Resources/autorun/game"
  let sourcePayload=view.appendingPathComponent("Game.app/"+payloadRelative)
  try fm.createDirectory(at:sourcePayload,withIntermediateDirectories:true)
  let sourceConfig=sourcePayload.appendingPathComponent("options.rpy")
  try Data(contentsOf:view.appendingPathComponent("game/options.rpy")).write(to:sourceConfig)
  var payloadProofJSON=cachedPayload
  var payloadRows=cachedPayload["fingerprints"] as! [[String:Any]]
  let configIndex=payloadRows.firstIndex{$0["role"] as? String=="config"}!
  payloadRows[configIndex]["path"]="Game.app/"+payloadRelative+"/options.rpy"
  payloadProofJSON["fingerprints"]=payloadRows
  let payloadProof=try decode(payloadProofJSON)
  try payloadProof.validate(namespace:namespace,entry:Candidate(path:"Game.app",backend:.native,engine:"renpy"),platform:"macos")
  let stage=root.appendingPathComponent("FreshStageRenamed.app")
  try fm.copyItem(at:view.appendingPathComponent("Game.app"),to:stage)
  let stagePayload=stage.appendingPathComponent(payloadRelative)
  try fm.removeItem(at:stagePayload);try fm.createSymbolicLink(at:stagePayload,withDestinationURL:sourcePayload)
  try payloadProof.verifyCachedApp(stage,entryPath:"Game.app",sourceView:view)
  var sourceDot=payloadProofJSON;sourceDot["entryPath"]="."
  sourceDot["fingerprints"]=payloadRows.map{row -> [String:Any] in var row=row;row["path"]=String((row["path"] as! String).dropFirst("Game.app/".count));return row}
  try rejects("generic source verification must still reject payload links"){try decode(sourceDot).verify(view:stage)}
  // Do not exempt all config or metadata just because one config lives in payload.
  let extraFiles=[("config","outside-config.rpy")]
  var completeRows=payloadRows
  for (role,path) in extraFiles {
   let bytes=Data(("reviewed fixture "+role).utf8)
   for bundle in [view.appendingPathComponent("Game.app"),stage]{try bytes.write(to:bundle.appendingPathComponent(path))}
   completeRows.append(["path":"Game.app/"+path,"role":role,"sourceSHA256":sha(bytes),"exportSHA256":sha(bytes)])
  }
  var completeJSON=payloadProofJSON;completeJSON["fingerprints"]=completeRows
  let completeProof=try decode(completeJSON)
  try completeProof.verifyCachedApp(stage,entryPath:"Game.app",sourceView:view)
  for path in ["outside-config.rpy","Contents/Info.plist"] {
   let file=stage.appendingPathComponent(path),bytes=try Data(contentsOf:file)
   try (bytes+Data("stage mutation".utf8)).write(to:file)
   try rejects("cached config/metadata outside payload must not be skipped"){try completeProof.verifyCachedApp(stage,entryPath:"Game.app",sourceView:view)}
   try bytes.write(to:file)
  }
  let foreignPayload=root.appendingPathComponent("ForeignPayload")
  try fm.copyItem(at:sourcePayload,to:foreignPayload)
  try fm.removeItem(at:stagePayload);try fm.createSymbolicLink(at:stagePayload,withDestinationURL:foreignPayload)
  try rejects("same bytes but replaced payload target"){try payloadProof.verifyCachedApp(stage,entryPath:"Game.app",sourceView:view)}
  try fm.removeItem(at:stagePayload);try fm.createSymbolicLink(at:stagePayload,withDestinationURL:sourcePayload)
  let configOriginal=try Data(contentsOf:sourceConfig)
  try (configOriginal+Data("source mutation".utf8)).write(to:sourceConfig)
  try rejects("config cannot be skipped behind the allowed link"){try payloadProof.verifyCachedApp(stage,entryPath:"Game.app",sourceView:view)}
  try configOriginal.write(to:sourceConfig)
  let cachedBootstrap=stage.appendingPathComponent("renpy/bootstrap.py"),bootstrapBytes=try Data(contentsOf:cachedBootstrap)
  try (bootstrapBytes+Data("unreviewed local rewrite".utf8)).write(to:cachedBootstrap)
  try rejects("cached code still checked before identity/signing transforms"){try payloadProof.verifyCachedApp(stage,entryPath:"Game.app",sourceView:view)}
  try bootstrapBytes.write(to:cachedBootstrap)
  let sourceBootstrap=view.appendingPathComponent("Game.app/renpy/bootstrap.py")
  try (bootstrapBytes+Data("source runtime mutation".utf8)).write(to:sourceBootstrap)
  try rejects("every source proof must be verified again"){try payloadProof.verifyCachedApp(stage,entryPath:"Game.app",sourceView:view)}
  try bootstrapBytes.write(to:sourceBootstrap)
  var wrongRoles=payloadRows
  wrongRoles.append(["path":"Game.app/"+payloadRelative+"/code.py","role":"bootstrap","sourceSHA256":sha(bootstrapBytes),"exportSHA256":sha(bootstrapBytes)])
  try bootstrapBytes.write(to:sourcePayload.appendingPathComponent("code.py"))
  var unsupportedPayload=payloadProofJSON;unsupportedPayload["fingerprints"]=wrongRoles
  let unsupported=try decode(unsupportedPayload)
  try rejects("non-config code beneath payload is unsupported"){try unsupported.verifyCachedApp(stage,entryPath:"Game.app",sourceView:view)}
  try payloadProof.verifyCachedApp(stage,entryPath:"Game.app",sourceView:view)
  print("PASS RenpyLaunchProof: namespace/entry binding, eight roles, pinned locations patch, bounded export hashing, mutation/symlink/nonregular/size rejection, complete renamed native-cache verification")
 }
}
