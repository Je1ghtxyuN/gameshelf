import Foundation

enum Wrapper {
 static var defaultSource:URL {
  FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications/Steambuild 32 64bit DXVK.app")
 }
 static func isManagedSource(source:URL)->Bool {
  let fm=FileManager.default
  guard let data=try? Data(contentsOf:source.appendingPathComponent("Contents/Info.plist")),
        let info=try? PropertyListSerialization.propertyList(from:data,format:nil) as? [String:Any],
        info["GameShelfManagedWine"] as? Bool==true else{return false}
  return ["wine64","wineserver"].allSatisfy{fm.isExecutableFile(atPath:source.appendingPathComponent("Contents/SharedSupport/wine/bin/"+$0).path)}
 }
 static func prepare(game:Game,source:URL,destination:URL,shareEngine:Bool=false,freshPrefix:Bool=false) throws -> URL {
  try EntryPolicy.validate(game.entry)
  let fm=FileManager.default
  let managed=isManagedSource(source:source)
  let emptyPrefix=freshPrefix || managed
  guard !managed || shareEngine else{throw LibraryError("托管运行环境需要独立 prefix。")}
  guard !freshPrefix || shareEngine else{throw LibraryError("全新共享环境需要独立 prefix 与共享引擎。")}
  let existed=fm.fileExists(atPath:destination.path)
  if existed && freshPrefix {
   let info=destination.appendingPathComponent("Contents/Info.plist")
   guard let data=try? Data(contentsOf:info),let values=try? PropertyListSerialization.propertyList(from:data,format:nil) as? [String:Any],values["GameShelfFreshPrefix"] as? Bool==true else{throw LibraryError("此共享环境由旧版模板复制生成，未继续使用。请保留旧环境并创建全新的账号运行目录。")}
  }
  let executable=URL(fileURLWithPath:game.folder).appendingPathComponent(game.entry.path)
  guard fm.fileExists(atPath:executable.path) else {throw LibraryError("游戏文件不可用。请连接 NAS，或在游戏菜单中重新定位。")}
  guard managed || fm.fileExists(atPath:source.appendingPathComponent("Contents/MacOS/wineskinlauncher").path) else {throw LibraryError("找不到可用的 Wine 运行环境。")}
  let cloneRoot=destination.resolvingSymlinksInPath().standardizedFileURL.path
  let sourceRoot=source.resolvingSymlinksInPath().standardizedFileURL.path
  guard !containsPath(cloneRoot,in:sourceRoot),!containsPath(sourceRoot,in:cloneRoot) else {throw LibraryError("运行副本路径与原环境重叠。")}
  var sourcePrefixes=[source.appendingPathComponent("Contents/SharedSupport/prefix")]
  // An existing shared runtime may still contain links into its previous source.
  let engineLink=destination.appendingPathComponent("Contents/SharedSupport/wine")
  if let old=try? fm.destinationOfSymbolicLink(atPath:engineLink.path) {
   let target=URL(fileURLWithPath:old,relativeTo:engineLink.deletingLastPathComponent()).standardizedFileURL
   sourcePrefixes.append(target.deletingLastPathComponent().appendingPathComponent("prefix"))
  }
  if !fm.fileExists(atPath:destination.path) {
   try fm.createDirectory(at:destination.deletingLastPathComponent(),withIntermediateDirectories:true)
   let staging=destination.deletingLastPathComponent().appendingPathComponent(".preparing-"+UUID().uuidString+".app")
   defer {try? fm.removeItem(at:staging)}
   if shareEngine {
    let contents=staging.appendingPathComponent("Contents"),support=staging.appendingPathComponent("Contents/SharedSupport")
    try fm.createDirectory(at:support,withIntermediateDirectories:true)
    try fm.copyItem(at:source.appendingPathComponent("Contents/Info.plist"),to:contents.appendingPathComponent("Info.plist"))
    for (relative,name) in [("Contents/SharedSupport/wine","wine"),("Contents/Frameworks","Frameworks")] {
     let target=source.appendingPathComponent(relative)
     guard fm.fileExists(atPath:target.path) else{throw LibraryError("已有运行环境缺少组件："+name)}
     let link=name=="wine" ? support.appendingPathComponent(name):contents.appendingPathComponent(name)
     try fm.createSymbolicLink(atPath:link.path,withDestinationPath:target.path)
    }
    if emptyPrefix {
     let prefix=support.appendingPathComponent("prefix")
     try fm.createDirectory(at:prefix.appendingPathComponent("drive_c"),withIntermediateDirectories:true)
     try fm.createDirectory(at:prefix.appendingPathComponent("dosdevices"),withIntermediateDirectories:true)
     // Adding G: before wineboot makes dosdevices nonempty, so Wine no longer
     // supplies these default drive links itself. Seed only empty drive layout.
     try fm.createSymbolicLink(atPath:prefix.appendingPathComponent("dosdevices/c:").path,withDestinationPath:"../drive_c")
     try fm.createSymbolicLink(atPath:prefix.appendingPathComponent("dosdevices/z:").path,withDestinationPath:"/")
    }
    else{try fm.copyItem(at:source.appendingPathComponent("Contents/SharedSupport/prefix"),to:support.appendingPathComponent("prefix"))}
   } else {
    let process=Process();process.executableURL=URL(fileURLWithPath:"/bin/cp");process.arguments=["-cR",source.path,staging.path]
    let err=Pipe();process.standardError=err
    try PreparationTasks.shared.run(process)
  defer{PreparationTasks.shared.finish(process)}
    let errorData=err.fileHandleForReading.readDataToEndOfFile();process.waitUntilExit()
    guard process.terminationStatus==0 else {throw LibraryError("无法创建独立运行环境："+(String(data:errorData,encoding:.utf8) ?? "复制失败"))}
   }
   try validateWritableLinks(runtime:staging,sourcePrefixes:sourcePrefixes)
   try fm.moveItem(at:staging,to:destination)
  }
  let prefix=destination.appendingPathComponent("Contents/SharedSupport/prefix")
  try validateWritableLinks(runtime:destination,sourcePrefixes:sourcePrefixes)
  if shareEngine {try refreshSharedEngine(source:source,runtime:destination)}
  let dos=prefix.appendingPathComponent("dosdevices")
  try fm.createDirectory(at:dos,withIntermediateDirectories:true)
  let mapping=dos.appendingPathComponent("g:")
  if (try? fm.destinationOfSymbolicLink(atPath:mapping.path)) != nil {try fm.removeItem(at:mapping)}
  else if fm.fileExists(atPath:mapping.path) {throw LibraryError("独立环境的 G: 已被占用，未覆盖。")}
  try fm.createSymbolicLink(atPath:mapping.path,withDestinationPath:game.folder)
  let plistURL=destination.appendingPathComponent("Contents/Info.plist")
  guard var plist=try PropertyListSerialization.propertyList(from:Data(contentsOf:plistURL),format:nil) as? [String:Any] else {throw LibraryError("运行环境配置损坏。")}
  plist["Program Name and Path"]="/windows/system32/cmd.exe"
  plist["Program Flags"]="/c exit"
  plist["CFBundleIdentifier"]="local.gameshelf.runtime."+game.id.uuidString.lowercased()
  plist["CFBundleName"]="GameShelf Runtime"
  plist["Fullscreen"]=false;plist["Use XQuartz"]=false;plist["Debug Mode"]=true
  plist["WINEDEBUG"]="-all,+err"
  if freshPrefix || (managed && !existed){plist["GameShelfFreshPrefix"]=true}
  // Logs aid troubleshooting, but detected engine does not imply a verified playable game.
  let data=try PropertyListSerialization.data(fromPropertyList:plist,format:.xml,options:0)
  try data.write(to:plistURL,options:.atomic)
  return destination
 }

 private static func containsPath(_ path:String,in root:String)->Bool {
  path==root || path.hasPrefix(root+"/")
 }

 private static func resolvedWritablePath(_ url:URL)->String {
  // Foundation normalizes existing /private/tmp paths to /tmp, but leaves a
  // not-yet-created child unchanged. Resolve the nearest existing ancestor
  // first so containment checks use the same spelling for both.
  let fm=FileManager.default
  var ancestor=url,tail:[String]=[]
  while !fm.fileExists(atPath:ancestor.path),
        (try? fm.destinationOfSymbolicLink(atPath:ancestor.path)) == nil,
        ancestor.path != "/" {
   tail.insert(ancestor.lastPathComponent,at:0)
   ancestor=ancestor.deletingLastPathComponent()
  }
  let base=ancestor.resolvingSymlinksInPath().standardizedFileURL.path
  return ([base]+tail).joined(separator:"/")
 }

 private static func validateWritableLinks(runtime:URL,sourcePrefixes:[URL]) throws {
  let fm=FileManager.default
  let root=resolvedWritablePath(runtime)
  let prefix=runtime.appendingPathComponent("Contents/SharedSupport/prefix")
  // These directories/files are written by GameShelf or Wine itself. Host drive and
  // user-folder mappings below the prefix are intentionally handled separately.
  for relative in ["Contents/Info.plist","Contents/SharedSupport/Cache","Contents/SharedSupport/Logs","Contents/SharedSupport/prefix","Contents/SharedSupport/prefix/dosdevices","Contents/SharedSupport/prefix/drive_c","Contents/SharedSupport/prefix/drive_c/GameShelf","Contents/SharedSupport/prefix/drive_c/GameShelf/launch.bat"] {
   let resolved=resolvedWritablePath(runtime.appendingPathComponent(relative))
   guard resolved.hasPrefix(root+"/") else {throw LibraryError("运行副本包含指向外部的可写链接，已停止以保护原环境："+relative)}
  }
  let prefixRoot=resolvedWritablePath(prefix)
  for relative in ["dosdevices/c:","user.reg","system.reg","userdef.reg"] {
   let resolved=resolvedWritablePath(prefix.appendingPathComponent(relative))
   guard containsPath(resolved,in:prefixRoot) else {throw LibraryError("运行副本的 C: 或注册表指向外部："+relative)}
  }
  let originals=sourcePrefixes.flatMap{[$0.standardizedFileURL.path,$0.resolvingSymlinksInPath().standardizedFileURL.path]}
  var traversalError:Error?
  guard let walker=fm.enumerator(at:prefix,includingPropertiesForKeys:[.isSymbolicLinkKey],options:[],errorHandler:{_,error in traversalError=error;return false}) else {throw LibraryError("无法检查运行副本的 prefix。")}
  for case let item as URL in walker {
   // Use prefetched metadata to avoid readlink on every regular file over SMB.
   // Unknown metadata still takes the existing readlink/all-hop validation path.
   if (try? item.resourceValues(forKeys:[.isSymbolicLinkKey]).isSymbolicLink)==false {continue}
   guard let link=try? fm.destinationOfSymbolicLink(atPath:item.path) else {continue}
   walker.skipDescendants()
   try rejectSourceLink(target:link,parent:item.deletingLastPathComponent(),originals:originals)
  }
  if let traversalError {throw traversalError}
 }

 private static func rejectSourceLink(target:String,parent:URL,originals:[String]) throws {
  // Inspect each symlink hop rather than only the final resolved destination:
  // an alias can enter the source prefix and then leave via its Documents link.
  // Process .. after symlinks, matching filesystem semantics.
  var pending=(target.hasPrefix("/") ? []:parent.pathComponents.filter{$0 != "/"})+target.split(separator:"/").map(String.init)
  var current=URL(fileURLWithPath:"/"),hops=0
  while !pending.isEmpty {
   let component=pending.removeFirst()
   if component=="." {continue}
   if component==".." {current=current.deletingLastPathComponent();continue}
   let next=current.appendingPathComponent(component)
   // Foundation normalizes /private/var back to /var on macOS; match both forms.
   guard !originals.contains(where:{containsPath(next.path,in:$0) || containsPath(next.standardizedFileURL.path,in:$0)}) else {
    throw LibraryError("运行副本仍包含指向原 prefix 的链接，已停止以保护存档："+next.path)
   }
   if let link=try? FileManager.default.destinationOfSymbolicLink(atPath:next.path) {
    hops+=1
    guard hops<=40 else {throw LibraryError("运行副本包含循环或过深的符号链接。")}
    if link.hasPrefix("/") {current=URL(fileURLWithPath:"/")}
    pending=link.split(separator:"/").map(String.init)+pending
   } else {current=next}
  }
 }

 private static func refreshSharedEngine(source:URL,runtime:URL) throws {
  let fm=FileManager.default
  let paths=["Contents/SharedSupport/wine","Contents/Frameworks"]
  let independent=paths.allSatisfy {path in
   let item=runtime.appendingPathComponent(path)
   var directory:ObjCBool=false
   return (try? fm.destinationOfSymbolicLink(atPath:item.path))==nil && fm.fileExists(atPath:item.path,isDirectory:&directory) && directory.boolValue
  }
  if independent {return}
  // Validate both components before touching either existing link. Never replace a
  // real directory (e.g. a previously independent clone) with a shared engine.
  var changes:[(link:URL,target:URL,previous:String?)]=[]
  for path in paths {
   let target=source.appendingPathComponent(path),link=runtime.appendingPathComponent(path)
   guard fm.fileExists(atPath:target.path) else {throw LibraryError("已有运行环境缺少组件："+path)}
   let previous=try? fm.destinationOfSymbolicLink(atPath:link.path)
   guard previous != nil || !fm.fileExists(atPath:link.path) else {throw LibraryError("运行副本含独立引擎目录，未覆盖："+path)}
   if previous != target.path {changes.append((link,target,previous))}
  }
  var changed:[(URL,String?)]=[]
  do {
   for change in changes {
    if change.previous != nil {try fm.removeItem(at:change.link)}
    changed.append((change.link,change.previous))
    try fm.createSymbolicLink(atPath:change.link.path,withDestinationPath:change.target.path)
   }
  } catch {
   for (link,previous) in changed.reversed() {
    if (try? fm.destinationOfSymbolicLink(atPath:link.path)) != nil {try? fm.removeItem(at:link)}
    if let previous {try? fm.createSymbolicLink(atPath:link.path,withDestinationPath:previous)}
   }
   throw error
  }
 }
}
struct LaunchSpec {var executable:URL;var arguments:[String];var directory:URL;var environment:[String:String]}
extension Wrapper {
 // Darwin limits POSIX shared-memory names; this engine appends "-tid" to an inode-based name.
 static func msyncEnabled(inode:UInt64)->Bool {("/wine-"+String(inode,radix:16)+"-msync-tid").utf8.count <= 31}
 static func syncSettings(inode:UInt64?) -> [String:String] {
  let msync=inode.map{msyncEnabled(inode:$0)} ?? false
  // On this engine, ESync alone spins Wine services on the tested NAS prefix.
  // Keep classic Wine waits when MSync's Darwin name cannot represent the inode.
  return ["WINEMSYNC":msync ? "1":"0","WINEESYNC":msync ? "1":"0"]
 }
 static func launchSpec(game:Game,runtime:URL,seedRenderCache:Bool=true,directNAS:Bool=false) throws -> LaunchSpec {
  try EntryPolicy.validate(game.entry)
  let directory=URL(fileURLWithPath:game.folder)
  var environment=ProcessInfo.processInfo.environment
  environment["WINEPREFIX"]=runtime.appendingPathComponent("Contents/SharedSupport/prefix").path
  environment["DYLD_FALLBACK_LIBRARY_PATH"]=runtime.appendingPathComponent("Contents/Frameworks").path
  let cacheRoot=directNAS ? runtime.deletingLastPathComponent().deletingLastPathComponent() : WineRenderCachePolicy.defaultRoot
  environment.merge(try WineRenderCachePolicy.prepare(game:game,runtime:runtime,root:cacheRoot,migrateLegacy:seedRenderCache && !directNAS)){_,new in new}
  environment["GAMESHELF_PROFILE"]=game.showPerformanceHUD==true ? "1":"0"
  environment["DXVK_HUD"]=game.showPerformanceHUD==true ? "fps,frametimes,gpuload,compiler":"0"
  environment["WINEDEBUG"]="-all,err+all,+msgbox"
  environment["WINEDLLOVERRIDES"]="winemenubuilder.exe=d"
  let graphics=inheritedGraphicsOverrides(runtime:runtime)
  if !graphics.isEmpty{environment["WINEDLLOVERRIDES"]! += ";"+graphics}
  if game.shared != nil {
   // Wine-side Unix helpers also receive an account-private home. Caches remain
   // local; no environment variable points them at another account's user data.
   environment["HOME"]=(directNAS ? runtime.appendingPathComponent("Contents/SharedSupport/DirectNASProfile/UnixHome"):directory.appendingPathComponent(".gameshelf-profile/UnixHome")).path
  }
  environment.removeValue(forKey:"GAMESHELF_WINDOW_BOUNDS")
  let prefix=runtime.appendingPathComponent("Contents/SharedSupport/prefix")
  let inode=(try? FileManager.default.attributesOfItem(atPath:prefix.path)[.systemFileNumber] as? NSNumber)?.uint64Value
  environment.merge(syncSettings(inode:inode)){_,new in new}
  var arguments=[directNAS ? "G:\\"+game.entry.path.replacingOccurrences(of:"/",with:"\\"):directory.appendingPathComponent(game.entry.path).path]
  if let size=game.windowSize {
   guard ["1280x720","1024x768"].contains(size) else{throw LibraryError("窗口区域设置无效。")}
   let preparedRGSS=game.entry.engine=="RPG Maker VX Ace" &&
    ["GameShelfAdapter.rb","GameShelfBootstrap.rvdata2"].allSatisfy{FileManager.default.fileExists(atPath:directory.appendingPathComponent($0).path)}
   if preparedRGSS {
    // This adapter sizes its own game window after scripts initialize. A
    // virtual desktop instead changes the game's size list and saved index.
    environment["GAMESHELF_WINDOW_BOUNDS"]=size
   } else {arguments=["explorer","/desktop=GameShelf"+game.id.uuidString+","+size]+arguments}
  }
  return LaunchSpec(executable:runtime.appendingPathComponent("Contents/SharedSupport/wine/bin/wine64"),arguments:arguments,directory:directory,environment:environment)
 }
 // Construct alongside launchSpec; execute after the wine64 process exits. A
 // successful wait means all Wine processes in THIS prefix have ended. Do not
 // release the app's running/copy guard merely because the initial EXE exited.
 static func stopSpec(game:Game,runtime:URL,directNAS:Bool=false) throws -> LaunchSpec {
  var spec=try waitSpec(game:game,runtime:runtime,directNAS:directNAS)
  spec.arguments=["-k"]
  return spec
 }
 static func waitSpec(game:Game,runtime:URL,directNAS:Bool=false) throws -> LaunchSpec {
  let launch=try launchSpec(game:game,runtime:runtime,seedRenderCache:false,directNAS:directNAS)
  return LaunchSpec(executable:runtime.appendingPathComponent("Contents/SharedSupport/wine/bin/wineserver"),arguments:["-w"],directory:runtime.appendingPathComponent("Contents/SharedSupport/prefix"),environment:launch.environment)
 }
}

extension Wrapper {
 // Carry only explicitly configured native graphics libraries from the chosen
 // engine into a fresh account prefix. Never inherit its user registry/data.
 static func inheritNativeGraphics(runtime:URL)throws {
  let fm=FileManager.default,support=runtime.appendingPathComponent("Contents/SharedSupport")
  let link=support.appendingPathComponent("wine")
  let receipt=support.appendingPathComponent("native-graphics.json")
  func clearReceipt()throws {
   if let type=try? fm.attributesOfItem(atPath:receipt.path)[.type] as? FileAttributeType {
    guard type == .typeRegular || type == .typeSymbolicLink else{throw LibraryError("Wine 图形组件记录位置异常。")}
    try fm.removeItem(at:receipt)
   }
  }
  guard let raw=try? fm.destinationOfSymbolicLink(atPath:link.path) else{try clearReceipt();return}
  let engine=URL(fileURLWithPath:raw,relativeTo:support).standardizedFileURL.deletingLastPathComponent()
  let source=engine.appendingPathComponent("prefix")
  guard let registry=try? String(contentsOf:source.appendingPathComponent("user.reg"),encoding:.utf8) else{try clearReceipt();return}
  let allowed:Set<String>=["d3d8","d3d9","d3d10core","d3d11","dxgi","d3dcompiler_43","d3dcompiler_47"]
  var active=false,modes:[String:String]=[:]
  for line in registry.components(separatedBy:.newlines) {
   if line.hasPrefix("["){active=line.components(separatedBy:"]").first=="[Software\\\\Wine\\\\DllOverrides";continue}
   guard active else{continue}
   let fields=line.components(separatedBy:"=")
   guard fields.count==2 else{continue}
   let name=fields[0].trimmingCharacters(in:CharacterSet(charactersIn:"\"* ")).lowercased()
   let mode=fields[1].trimmingCharacters(in:CharacterSet(charactersIn:"\" ")).lowercased()
   if allowed.contains(name),["native","native,builtin","n","n,b"].contains(mode){modes[name]=["native","n"].contains(mode) ? "n":"n,b"}
  }
  guard !modes.isEmpty else{try clearReceipt();return}
  let prefix=support.appendingPathComponent("prefix")
  var inherited:[String:String]=[:]
  for name in modes.keys.sorted() {
   var copied=false
   for arch in ["system32","syswow64"] {
    let relative="drive_c/windows/"+arch+"/"+name+".dll"
    let input=source.appendingPathComponent(relative)
    guard (try? fm.attributesOfItem(atPath:input.path)[.type] as? FileAttributeType) == .typeRegular else{continue}
    let bytes=try Data(contentsOf:input)
    guard bytes.starts(with:[0x4d,0x5a]) else{throw LibraryError("Wine 图形组件不是有效的 Windows 程序："+name)}
    var directory=prefix
    for part in ["drive_c","windows",arch] {
     directory.appendPathComponent(part)
     if !fm.fileExists(atPath:directory.path){try fm.createDirectory(at:directory,withIntermediateDirectories:false)}
     guard try fm.attributesOfItem(atPath:directory.path)[.type] as? FileAttributeType == .typeDirectory,
           directory.resolvingSymlinksInPath().path.hasPrefix(prefix.resolvingSymlinksInPath().path+"/") else{throw LibraryError("Wine 图形组件目录包含外部链接。")}
    }
    let output=prefix.appendingPathComponent(relative)
    if (try? fm.destinationOfSymbolicLink(atPath:output.path)) != nil{throw LibraryError("Wine 图形组件包含外部链接。")}
    if (try? Data(contentsOf:output)) != bytes {
     if fm.fileExists(atPath:output.path) {
      var backupDirectory=support
      for part in ["GraphicsDependencyBackup",UUID().uuidString,"drive_c","windows",arch] {
       backupDirectory.appendPathComponent(part)
       if !fm.fileExists(atPath:backupDirectory.path){try fm.createDirectory(at:backupDirectory,withIntermediateDirectories:false)}
       guard try fm.attributesOfItem(atPath:backupDirectory.path)[.type] as? FileAttributeType == .typeDirectory,
             backupDirectory.resolvingSymlinksInPath().path.hasPrefix(support.resolvingSymlinksInPath().path+"/") else{throw LibraryError("Wine 图形组件备份目录包含外部链接。")}
      }
      try fm.copyItem(at:output,to:backupDirectory.appendingPathComponent(name+".dll"))
     }
     try bytes.write(to:output,options:.atomic)
    }
    copied=true
   }
   if copied{inherited[name]=modes[name]}
  }
  if (try? fm.destinationOfSymbolicLink(atPath:receipt.path)) != nil{throw LibraryError("Wine 图形组件记录包含外部链接。")}
  try JSONEncoder().encode(inherited).write(to:receipt,options:.atomic)
 }
 static func inheritedGraphicsOverrides(runtime:URL)->String {
  let receipt=runtime.appendingPathComponent("Contents/SharedSupport/native-graphics.json")
  guard let data=try? Data(contentsOf:receipt),let modes=try? JSONDecoder().decode([String:String].self,from:data) else{return ""}
  let allowed:Set<String>=["d3d8","d3d9","d3d10core","d3d11","dxgi","d3dcompiler_43","d3dcompiler_47"]
  return modes.keys.sorted().filter{allowed.contains($0) && ["n","n,b"].contains(modes[$0] ?? "")}.map{$0+"="+modes[$0]!}.joined(separator:";")
 }
}
