import Foundation
@main struct SharedWineRuntimeTests {
 static func main()throws {
  struct Failure:Error{let message:String}
  func check(_ condition:Bool,_ message:String)throws{if !condition{throw Failure(message:message)}}
  let fm=FileManager.default,root=URL(fileURLWithPath:"/private/tmp").appendingPathComponent("SharedWine-"+UUID().uuidString)
  try fm.createDirectory(at:root,withIntermediateDirectories:true);defer{try? fm.removeItem(at:root)}
  let source=root.appendingPathComponent("Source.app"),destination=root.appendingPathComponent("Private.app"),folder=root.appendingPathComponent("Game")
  func put(_ url:URL,_ text:String)throws{try fm.createDirectory(at:url.deletingLastPathComponent(),withIntermediateDirectories:true);try Data(text.utf8).write(to:url)}
  try put(source.appendingPathComponent("Contents/MacOS/wineskinlauncher"),"")
  try put(source.appendingPathComponent("Contents/SharedSupport/wine/bin/wine64"),"")
  try fm.createDirectory(at:source.appendingPathComponent("Contents/Frameworks"),withIntermediateDirectories:true)
  let seed=source.appendingPathComponent("Contents/SharedSupport/prefix")
  try put(seed.appendingPathComponent("drive_c/ProgramData/Owner/progress.sav"),"owner progress")
  try put(seed.appendingPathComponent("user.reg"),"[Software\\\\Wine\\\\DllOverrides]\n\"*d3d11\"=\"native,builtin\"\n")
  for arch in ["system32","syswow64"] {try put(seed.appendingPathComponent("drive_c/windows/"+arch+"/d3d11.dll"),"MZ fixture graphics "+arch)}
  try PropertyListSerialization.data(fromPropertyList:["CFBundleName":"Fixture"],format:.xml,options:0).write(to:source.appendingPathComponent("Contents/Info.plist"))
  try put(folder.appendingPathComponent("Game.exe"),"MZ fixture")
  let ref=SharedGameReference(connectionID:UUID(),serverID:"server",accountID:"alice",gameID:"story")
  let game=Game(title:"Story",folder:folder.path,originalFolder:"shared",entry:Candidate(path:"Game.exe",backend:.wine,engine:"Windows"),shared:ref)
  _ = try Wrapper.prepare(game:game,source:source,destination:destination,shareEngine:true,freshPrefix:true)
  let prefix=destination.appendingPathComponent("Contents/SharedSupport/prefix")
  try check(!fm.fileExists(atPath:prefix.appendingPathComponent("drive_c/ProgramData/Owner/progress.sav").path),"template ProgramData was copied into a shared account")
  try check(!fm.fileExists(atPath:prefix.appendingPathComponent("user.reg").path),"template registry copied")
  try check(fm.fileExists(atPath:prefix.appendingPathComponent("drive_c").path),"fresh C drive missing")
  try check(try fm.destinationOfSymbolicLink(atPath:prefix.appendingPathComponent("dosdevices/c:").path)=="../drive_c","fresh C mapping missing")
  try check(try fm.destinationOfSymbolicLink(atPath:prefix.appendingPathComponent("dosdevices/z:").path)=="/","fresh host-path mapping missing")
  var calls=0
  try SharedWineBootstrap.prepare(game:game,runtime:destination){spec,_ in
   calls+=1
   try check(spec.environment["WINEPREFIX"]==prefix.path,"wrong prefix")
   if spec.arguments.first=="wineboot" {
    try put(prefix.appendingPathComponent("user.reg"),"fresh registry")
    try fm.createDirectory(at:prefix.appendingPathComponent("drive_c/users/engine-user/Documents"),withIntermediateDirectories:true)
   }
  }
  for arch in ["system32","syswow64"] {
   let dll=prefix.appendingPathComponent("drive_c/windows/"+arch+"/d3d11.dll")
   try check(fm.fileExists(atPath:dll.path),"fresh account lost native graphics DLLs from its selected engine")
   try check(try Data(contentsOf:dll)==Data(contentsOf:seed.appendingPathComponent("drive_c/windows/"+arch+"/d3d11.dll")),"graphics DLL did not come from selected engine")
  }
  let graphicsSpec=try Wrapper.launchSpec(game:game,runtime:destination)
  try check(graphicsSpec.environment["WINEDLLOVERRIDES"]?.contains("d3d11=n,b")==true,"native graphics override absent from account launch")
  try check(calls>0,"first launch skipped fresh Wine initialization")
  try check(try String(contentsOf:prefix.appendingPathComponent("user.reg"),encoding:.utf8)=="fresh registry","fresh registry was erased and would trigger reinitialization")
  let link=try fm.destinationOfSymbolicLink(atPath:prefix.appendingPathComponent("drive_c/users/engine-user").path)
  try check(link.hasSuffix("/WindowsUsers/Player"),"engine-generated user was not isolated")
  calls=0
  try SharedWineBootstrap.prepare(game:game,runtime:destination){_,_ in calls+=1}
  try check(calls==0,"repeated launch recreated account prefix")
  let originalRegistry=try Data(contentsOf:seed.appendingPathComponent("user.reg"))
  try put(seed.appendingPathComponent("user.reg"),"[Software\\\\Wine\\\\AppDefaults\\\\other.exe\\\\DllOverrides]\n\"*d3d11\"=\"native,builtin\"\n")
  try SharedWineBootstrap.prepare(game:game,runtime:destination){_,_ in calls+=1}
  let noGlobal=try Wrapper.launchSpec(game:game,runtime:destination)
  try check(noGlobal.environment["WINEDLLOVERRIDES"]?.contains("d3d11=")==false,"application-specific graphics setting leaked globally")
  try put(seed.appendingPathComponent("user.reg"),"[Software\\\\Wine\\\\DllOverrides]\n")
  try SharedWineBootstrap.prepare(game:game,runtime:destination){_,_ in calls+=1}
  let disabled=try Wrapper.launchSpec(game:game,runtime:destination)
  try check(disabled.environment["WINEDLLOVERRIDES"]?.contains("d3d11=")==false,"disabled engine graphics retained stale override")
  try originalRegistry.write(to:seed.appendingPathComponent("user.reg"))
  let outside=root.appendingPathComponent("OutsideGraphicsBackup")
  try fm.createDirectory(at:outside,withIntermediateDirectories:true)
  let backupLink=destination.appendingPathComponent("Contents/SharedSupport/GraphicsDependencyBackup")
  try fm.createSymbolicLink(at:backupLink,withDestinationURL:outside)
  try put(prefix.appendingPathComponent("drive_c/windows/system32/d3d11.dll"),"MZ old graphics")
  var refused=false
  do{try SharedWineBootstrap.prepare(game:game,runtime:destination){_,_ in calls+=1}}catch{refused=true}
  try check(refused,"external graphics backup symlink was followed")
  try check(try fm.contentsOfDirectory(atPath:outside.path).isEmpty,"graphics backup wrote outside account runtime")
  try fm.removeItem(at:backupLink)
  try SharedWineBootstrap.prepare(game:game,runtime:destination){_,_ in calls+=1}

  try check(try String(contentsOf:seed.appendingPathComponent("drive_c/ProgramData/Owner/progress.sav"),encoding:.utf8)=="owner progress","source progress changed")
  let directRoot=root.appendingPathComponent("DirectRuntime")
  let direct=directRoot.appendingPathComponent("Runtimes/Direct.app")
  let directFolder=root.appendingPathComponent("MountedFixture")
  var rebound=game
  rebound.folder=directFolder.path
  try put(directFolder.appendingPathComponent("Game.exe"),"MZ fixture")
  try put(directFolder.appendingPathComponent("existing.sav"),"preserved")
  _ = try Wrapper.prepare(game:rebound,source:source,destination:direct,shareEngine:true,freshPrefix:true)
  let directPrefix=direct.appendingPathComponent("Contents/SharedSupport/prefix")
  let localProfile=direct.appendingPathComponent("Contents/SharedSupport/DirectNASProfile")
  try SharedWineBootstrap.prepare(game:rebound,runtime:direct,directNAS:true){spec,_ in
   try check(spec.environment["HOME"]==localProfile.appendingPathComponent("UnixHome").path,"direct bootstrap used NAS home")
   if spec.arguments.first=="wineboot" {try put(directPrefix.appendingPathComponent("user.reg"),"direct registry")}
  }
  let spec=try Wrapper.launchSpec(game:rebound,runtime:direct,directNAS:true)
  try check(spec.arguments==["G:\\Game.exe"] && spec.directory.path==directFolder.path,"direct launch must address the NAS executable via G:")
  try check(try fm.destinationOfSymbolicLink(atPath:directPrefix.appendingPathComponent("dosdevices/g:").path)==directFolder.path,"direct G: must reference the chosen folder")
  try check(spec.environment["XDG_CACHE_HOME"]?.hasPrefix(directRoot.path+"/")==true,"preview cache escaped direct runtime root")
  let profileLink=try fm.destinationOfSymbolicLink(atPath:directPrefix.appendingPathComponent("drive_c/users/"+NSUserName()).path)
  try check(profileLink==localProfile.appendingPathComponent("WindowsUsers/Player").path,"direct Windows user directories were not host-local")
  try check(Set(try fm.contentsOfDirectory(atPath:directFolder.path))==Set(["Game.exe","existing.sav"]),"preparation wrote to the NAS fixture")
  try check(try String(contentsOf:directFolder.appendingPathComponent("existing.sav"),encoding:.utf8)=="preserved","preparation changed the existing save")
  let mountedRuntime=root.appendingPathComponent("MountedRuntime.app")
  var first=game
  first.folder=root.appendingPathComponent("FirstLease").path
  try put(URL(fileURLWithPath:first.folder).appendingPathComponent("Game.exe"),"MZ fixture")
  _ = try Wrapper.prepare(game:first,source:source,destination:mountedRuntime,shareEngine:true,freshPrefix:true)
  try SharedProfile.prepareWine(game:first,runtime:mountedRuntime,mountedWorkspace:true)
  let support=mountedRuntime.appendingPathComponent("Contents/SharedSupport")
  let user=support.appendingPathComponent("prefix/drive_c/users/"+NSUserName())
  let firstTarget=try fm.destinationOfSymbolicLink(atPath:user.path)
  try put(user.appendingPathComponent("AppData/Local/progress.sav"),"first lease")
  var second=game
  second.folder=root.appendingPathComponent("SecondLease").path
  try put(URL(fileURLWithPath:second.folder).appendingPathComponent("Game.exe"),"MZ fixture")
  try SharedProfile.prepareWine(game:second,runtime:mountedRuntime,mountedWorkspace:true)
  try check(try fm.destinationOfSymbolicLink(atPath:user.path)==firstTarget,"user mapping changed when lease rotated")
  let stored=URL(fileURLWithPath:first.folder).appendingPathComponent(".gameshelf-profile/WindowsUsers/Player/AppData/Local/progress.sav")
  try check(try String(contentsOf:stored,encoding:.utf8)=="first lease","first lease data changed")
  try check(!fm.fileExists(atPath:user.appendingPathComponent("AppData/Local/progress.sav").path),"second lease still points to first lease")
  print("PASS SharedWineRuntime: engine-only fresh prefix, no ProgramData/user-registry inheritance, initialized profile mapping and repeat launch")
 }
}
