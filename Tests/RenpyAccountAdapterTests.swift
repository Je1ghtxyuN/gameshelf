import Foundation
import CryptoKit

@main struct RenpyAccountAdapterTests {
 static func main()throws {
  func check(_ value:Bool,_ text:String)throws{if !value{throw LibraryError(text)}}
  func reject(_ body:()throws->Void)throws{do{try body()}catch{return};throw LibraryError("Expected adapter rejection")}
  let original=try Data(contentsOf:URL(fileURLWithPath:"Contracts/Fixtures/renpy-8.3.7/savelocation.py"))
  let patched=try RenpyAccountAdapter.patchSaveLocation(original)
  let compatibility="fixture-save-v1",compatibilityHash=SHA256.hash(data:Data(compatibility.utf8)).map{String(format:"%02x",$0)}.joined()
  let namespace=SharedSaveNamespace(key:"server/owner/"+String(repeating:"a",count:32)+"/"+compatibilityHash,relativePath:RenpyAccountAdapter.relativePath,adapter:"renpy-savedir-v1",reviewedProfileID:"fixture-renpy",revision:1,rulesSHA256:String(repeating:"c",count:64))
  try namespace.validate(serverID:"server",accountID:"owner",canonicalID:String(repeating:"a",count:32),compatibilityKey:compatibility)
  try reject{try namespace.validate(serverID:"server",accountID:"owner",canonicalID:String(repeating:"a",count:32),compatibilityKey:"incompatible-v2")}
  try reject{try namespace.validate(serverID:"server",accountID:"guest",canonicalID:String(repeating:"a",count:32))}
  try reject{try namespace.validate(serverID:"server",accountID:"owner",canonicalID:String(repeating:"d",count:32))}
  try check(patched != original,"Account adapter did not remove extra save locations")
  try check(try RenpyAccountAdapter.patchSaveLocation(patched)==patched,"Adapter is not idempotent")
  let code=String(decoding:patched,as:UTF8.self)
  let initBody=code.components(separatedBy:"def init():")[1].components(separatedBy:"def zip_saves")[0]
  try check(initBody.components(separatedBy:"location.add(FileLocation(").count==2,"Adapter permits multiple save locations")
  try check(!initBody.contains("extra_savedirs") && !initBody.contains("gamedir"),"Account adapter still opens game/global extra save paths")
  try reject{_ = try RenpyAccountAdapter.patchSaveLocation(original+Data("# changed".utf8))}
  let fm=FileManager.default,base=URL(fileURLWithPath:"/private/tmp/RenpyAccountAdapter-"+UUID().uuidString)
  defer{try? fm.removeItem(at:base)}
  let view=base.appendingPathComponent("PrivateView")
  try fm.createDirectory(at:view.appendingPathComponent(".gameshelf-profile/CanonicalSaves/renpy"),withIntermediateDirectories:true)
  let module=view.appendingPathComponent("savelocation.py")
  try patched.write(to:module)
  try RenpyAccountAdapter.verifyPatchedModule(at:module)
  try original.write(to:module)
  try reject{try RenpyAccountAdapter.verifyPatchedModule(at:module)}
  let location=try RenpyAccountAdapter.locations(view:view,relativePath:".gameshelf-profile/CanonicalSaves/renpy")
  try check(location.arguments==["--savedir",location.root.path],"Native savedir argv is not fixed")
  try check(location.environment["RENPY_PATH_TO_SAVES"]==location.root.appendingPathComponent("Auxiliary").path,"Tokens/default path not account-private")
  try check(location.environment["RENPY_MULTIPERSISTENT"]==location.root.appendingPathComponent("MultiPersistent").path,"MultiPersistent can read the OS-wide profile")
  try check(location.environment["HOME"]==nil && location.environment["APPDATA"]==nil,"Adapter replaced the user's OS profile")
  let wine=location.wine
  try check(wine.arguments.count==2 && wine.arguments[1].hasPrefix("Z:\\"),"Wine received a POSIX path instead of an absolute Windows path")
  try reject{_ = try RenpyAccountAdapter.locations(view:view,relativePath:"../OtherAccount")}
  let namespacePath=view.appendingPathComponent(".gameshelf-profile/CanonicalSaves/renpy")
  try fm.removeItem(at:namespacePath)
  try fm.createDirectory(at:base.appendingPathComponent("ForeignAccount"),withIntermediateDirectories:true)
  try fm.createSymbolicLink(atPath:namespacePath.path,withDestinationPath:base.appendingPathComponent("ForeignAccount").path)
  try reject{_ = try RenpyAccountAdapter.locations(view:view,relativePath:".gameshelf-profile/CanonicalSaves/renpy")}
  print("PASS RenPy account adapter: pinned deterministic single-location patch, no global persistence fallback, fixed native/Wine args and private namespace path confinement")
 }
}
