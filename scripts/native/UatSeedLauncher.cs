// Isolated one-shot UAT setup. No product, registry, network, service or update code.
using System;
using System.IO.Compression;
using System.Reflection;
using System.IO;
using System.Text;
using System.Diagnostics;
using System.Threading;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;
[assembly:System.Reflection.AssemblyTitle("MentorFlow UAT BASE Seed")]
[assembly:System.Reflection.AssemblyDescription("MentorFlow isolated UAT BASE Seed")]
[assembly:System.Reflection.AssemblyProduct("MentorFlow UAT BASE Seed")]
[assembly:System.Reflection.AssemblyVersion("1.0.0.0")]

class Bootstrap {
 const string Root=@"D:\MFUAC";
 const string Dacl="D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)";
 const string Admin="S-1-5-32-544", SystemSid="S-1-5-18", Users="S-1-5-32-545";
 const string Dispatch="MF-E107-UPDATER-RECEIPT-STATE-MACHINE-20260907-13";
 static readonly JavaScriptSerializer Json=new JavaScriptSerializer();
 static readonly string Evidence=Path.GetDirectoryName(typeof(Bootstrap).Assembly.Location);
 [StructLayout(LayoutKind.Sequential)] struct SA { public int size; public IntPtr descriptor; public int inherit; }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct SEI {
  public int size; public uint mask; public IntPtr hwnd;
  [MarshalAs(UnmanagedType.LPWStr)] public string verb,file,parameters,directory;
  public int show; public IntPtr instance,idlist;
  [MarshalAs(UnmanagedType.LPWStr)] public string className;
  public IntPtr classKey; public uint hotkey; public IntPtr icon,process;
 }
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string s,uint revision,out IntPtr sd,out uint size);
 [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr p);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateDirectory(string path,ref SA attributes);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern SafeFileHandle CreateFile(string path,uint access,uint sharing,ref SA attributes,uint disposition,uint flags,IntPtr template);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
 [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
 [DllImport("advapi32.dll",SetLastError=true)] static extern bool GetTokenInformation(IntPtr token,int kind,IntPtr data,int size,out int needed);
 [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
 [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthority(IntPtr sid,uint index);
 [DllImport("shell32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool ShellExecuteEx(ref SEI info);
 [DllImport("ole32.dll")] static extern int CoInitializeEx(IntPtr reserved,uint flags);
 [DllImport("ole32.dll")] static extern void CoUninitialize();
 [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint ms);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr handle,out uint code);
 static Dictionary<string,object> Obj(params object[] pairs) {
  var d=new Dictionary<string,object>(); for(int i=0;i<pairs.Length;i+=2)d[(string)pairs[i]]=pairs[i+1]; return d;
 }
 static long Now() { return (long)(DateTime.UtcNow-new DateTime(1970,1,1)).TotalSeconds; }
 static string DefaultOwner() {
  IntPtr token; if(!OpenProcessToken(Process.GetCurrentProcess().Handle,8,out token))throw new Win32Exception();
  try {
   int needed; GetTokenInformation(token,4,IntPtr.Zero,0,out needed); IntPtr buffer=Marshal.AllocHGlobal(needed);
   try {
    if(!GetTokenInformation(token,4,buffer,needed,out needed))throw new Win32Exception();
    return new SecurityIdentifier(Marshal.ReadIntPtr(buffer)).Value;
   } finally {Marshal.FreeHGlobal(buffer);}
  } finally {CloseHandle(token);}
 }
 static Dictionary<string,object> Token() {
  IntPtr h; if(!OpenProcessToken(Process.GetCurrentProcess().Handle,8,out h))throw new Win32Exception();
  try {
   int n; IntPtr b=Marshal.AllocHGlobal(4); bool elevated;
   try { if(!GetTokenInformation(h,20,b,4,out n))throw new Win32Exception(); elevated=Marshal.ReadInt32(b)!=0; }
   finally { Marshal.FreeHGlobal(b); }
   GetTokenInformation(h,25,IntPtr.Zero,0,out n); b=Marshal.AllocHGlobal(n);
   try {
    if(!GetTokenInformation(h,25,b,n,out n))throw new Win32Exception();
    IntPtr sid=Marshal.ReadIntPtr(b); byte count=Marshal.ReadByte(GetSidSubAuthorityCount(sid));
    int rid=Marshal.ReadInt32(GetSidSubAuthority(sid,(uint)(count-1)));
    return Obj("elevated",elevated,"integrity_rid",rid,"integrity",rid==8192?"MEDIUM":rid==12288?"HIGH":"OTHER");
   } finally { Marshal.FreeHGlobal(b); }
  } finally { CloseHandle(h); }
 }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true,EntryPoint="CreateFileW")] static extern SafeFileHandle OpenRead(string p,uint a,uint s,IntPtr sa,uint d,uint flags,IntPtr t);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandleEx(SafeFileHandle h,int kind,byte[] data,uint size);
 static string Hash(byte[] bytes) { using(var hash=SHA256.Create())return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-","").ToLowerInvariant(); }
 static void Authority() {
  Verify(Root,true);Verify(Root+@"\App",true);Verify(Root+@"\.uat-control",true);
  Verify(Root+@"\.uat-control\acl-bootstrap-result.json",false);
  if(Hash(File.ReadAllBytes(Root+@"\.uat-control\acl-bootstrap-result.json"))!=SeedPins.BootstrapSha)throw new InvalidOperationException("Bootstrap identity mismatch");
  using(var h=OpenRead(Root,0x80,3,IntPtr.Zero,3,0x02200000,IntPtr.Zero)) {
   byte[] b=new byte[24];
   if(h.IsInvalid||!GetFileInformationByHandleEx(h,18,b,24)||BitConverter.ToUInt64(b,0)!=SeedPins.Volume||BitConverter.ToUInt64(b,8)!=SeedPins.RootId||BitConverter.ToUInt64(b,16)!=0)throw new InvalidOperationException("Protected root identity mismatch");
  }
  if(File.Exists(Root+@"\installation-receipt.json"))throw new InvalidOperationException("Existing canonical install is not a seed target");
  string[] files=Directory.GetFileSystemEntries(Root+@"\App");
  if(files.Length!=1||files[0]!=Root+@"\App\payload-sentinel.txt"||File.ReadAllText(files[0])!="MentorFlow UAT elevated controlled write PASS\n")throw new InvalidOperationException("Not ACL_READY_NO_BASE");
  if(File.Exists(Root+@"\.install-staging\native-seed-once.claim"))throw new InvalidOperationException("Seed already attempted");
 }
 static Dictionary<string,object> Inspect() {
  var token=Token();if((bool)token["elevated"]||(int)token["integrity_rid"]!=8192)throw new InvalidOperationException("Medium token required");
  Authority();
  return Obj("PRE_UAC_READY","YES","token",token,"root",Root,"state","ACL_READY_NO_BASE");
 }
 static string Verify(string path,bool directory) {
  if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException("Reparse rejected");
  FileSystemSecurity acl=directory?(FileSystemSecurity)Directory.GetAccessControl(path):File.GetAccessControl(path);
  string owner=acl.GetOwner(typeof(SecurityIdentifier)).Value;
  if(owner!=Admin && owner!=SystemSid)throw new InvalidOperationException("Non-admin owner");
  if(!acl.AreAccessRulesProtected)throw new InvalidOperationException("Unprotected DACL");
  var seen=new HashSet<string>();
  foreach(FileSystemAccessRule rule in acl.GetAccessRules(true,true,typeof(SecurityIdentifier))) {
   string sid=rule.IdentityReference.Value; int expected=sid==Users?0x1200a9:0x1f01ff;
   if((sid!=Admin&&sid!=SystemSid&&sid!=Users)||rule.AccessControlType!=AccessControlType.Allow||(int)rule.FileSystemRights!=expected||!seen.Add(sid))
    throw new InvalidOperationException("Unexpected access rule");
  }
  if(seen.Count!=3)throw new InvalidOperationException("Incomplete DACL");
  return acl.GetSecurityDescriptorSddlForm(AccessControlSections.Owner|AccessControlSections.Access);
 }
 static bool OwnerIfNeeded(string path,bool directory) {
  FileSystemSecurity acl=directory?(FileSystemSecurity)Directory.GetAccessControl(path):File.GetAccessControl(path);
  string owner=acl.GetOwner(typeof(SecurityIdentifier)).Value;
  if(owner==Admin||owner==SystemSid)return false;
  // Only after elevated creation, only this newly-created exact object, and
  // only when the OS default owner is not the required admin/system owner.
  acl.SetOwner(new SecurityIdentifier(Admin));
  if(directory)Directory.SetAccessControl(path,(DirectorySecurity)acl); else File.SetAccessControl(path,(FileSecurity)acl);
  return true;
 }
 static void NewObject(string path,bool directory,byte[] bytes,List<string> ownerCorrections) {
  if(path!=Root && !path.StartsWith(Root+"\\",StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("Root escape");
  IntPtr sd; uint size;
  // Prefer the elevated token's admin/system default owner. Only if it is
  // unsuitable, assign BA atomically at creation, avoiding a user-owned window.
  string defaultOwner=DefaultOwner(); bool ownerNecessary=defaultOwner!=Admin&&defaultOwner!=SystemSid;
  if(!ConvertStringSecurityDescriptorToSecurityDescriptor((ownerNecessary?"O:BA":"")+Dacl,1,out sd,out size))throw new Win32Exception();
  try {
   SA sa=new SA {size=Marshal.SizeOf(typeof(SA)),descriptor=sd,inherit=0};
   if(directory) { if(!CreateDirectory(path,ref sa))throw new Win32Exception(); }
   else {
    using(SafeFileHandle handle=CreateFile(path,0x40000000,0,ref sa,1,0x80,IntPtr.Zero)) {
     if(handle.IsInvalid)throw new Win32Exception();
     using(var stream=new FileStream(handle,FileAccess.Write)) { stream.Write(bytes,0,bytes.Length); stream.Flush(true); }
    }
   }
  } finally {LocalFree(sd);}
  if(ownerNecessary)ownerCorrections.Add("AT_CREATE_DEFAULT_OWNER_NOT_ADMIN: "+path);
  if(OwnerIfNeeded(path,directory))ownerCorrections.Add(path);
  Verify(path,directory);
 }
 static int Initialize(string expiryText,string nonce) {
  long expiry;if(!long.TryParse(expiryText,out expiry)||expiry<Now()||expiry>Now()+120||!System.Text.RegularExpressions.Regex.IsMatch(nonce,"\\A[0-9a-f]{32}\\z"))return 87;
  var token=Token();if(!(bool)token["elevated"]||(int)token["integrity_rid"]!=12288)return 740;
  Authority();var corrected=new List<string>();
  string staging=Root+@"\.install-staging";
  if(!Directory.Exists(staging))NewObject(staging,true,null,corrected);
  Verify(staging,true);
  NewObject(staging+@"\native-seed-once.claim",false,Encoding.UTF8.GetBytes(nonce),corrected);
  string runtime=staging+@"\runtime-"+nonce;
  NewObject(runtime,true,null,corrected);
  var result=Obj("schema_version",1,"record_type","mentorflow-base-seed-launch-result","HELPER_INTEGRITY",token,
    "UAT_BASE_SEED_UAC_COUNT",1,"UPDATE_UAC_PROMPT_COUNT",0,"attempt_nonce",nonce,"BASE_SEED_RESULT","FAIL");
  try {
   byte[] zipBytes;
   using(var input=Assembly.GetExecutingAssembly().GetManifestResourceStream("seed-payload.zip"))
   using(var memory=new MemoryStream()){input.CopyTo(memory);zipBytes=memory.ToArray();}
   if(Hash(zipBytes)!=SeedPins.PayloadSha)throw new InvalidOperationException("Embedded runtime digest mismatch");
   var dirs=new HashSet<string>();dirs.Add(runtime);
   using(var zip=new ZipArchive(new MemoryStream(zipBytes),ZipArchiveMode.Read)) {
    foreach(var entry in zip.Entries) {
     string key=entry.FullName;
     if(key.StartsWith("/")||key.Contains("\\")||key.Contains(":")||Array.Exists(key.Split('/'),part=>part==""||part=="."||part==".."))throw new InvalidOperationException("Unsafe embedded entry");
     string dest=runtime+"\\"+key.Replace('/','\\');
     string parent=Path.GetDirectoryName(dest);
     var parents=new Stack<string>();
     while(!dirs.Contains(parent)){parents.Push(parent);parent=Path.GetDirectoryName(parent);}
     while(parents.Count>0){string d=parents.Pop();NewObject(d,true,null,corrected);dirs.Add(d);}
     byte[] bytes;using(var input=entry.Open())using(var memory=new MemoryStream()){input.CopyTo(memory);bytes=memory.ToArray();}
     NewObject(dest,false,bytes,corrected);
    }
   }
   string temp=runtime+@"\temp";NewObject(temp,true,null,corrected);
   var psi=new ProcessStartInfo(runtime+@"\SeedRuntime.exe","--seed");
   psi.UseShellExecute=false;psi.CreateNoWindow=true;psi.WorkingDirectory=runtime;
   psi.RedirectStandardOutput=true;psi.RedirectStandardError=true;
   psi.EnvironmentVariables.Clear();
   string windows=Environment.GetFolderPath(Environment.SpecialFolder.Windows);
   psi.EnvironmentVariables["SystemRoot"]=windows;psi.EnvironmentVariables["WINDIR"]=windows;
   psi.EnvironmentVariables["PATH"]=Path.Combine(windows,"System32");
   psi.EnvironmentVariables["TEMP"]=temp;psi.EnvironmentVariables["TMP"]=temp;
   using(var child=Process.Start(psi)) {
    if(!child.WaitForExit(840000)){child.Kill();child.WaitForExit(5000);throw new TimeoutException("Owned seed runtime timeout");}
    result["runtime_exit_code"]=child.ExitCode;result["runtime_stdout"]=child.StandardOutput.ReadToEnd();result["runtime_stderr"]=child.StandardError.ReadToEnd();
    result["BASE_SEED_RESULT"]=child.ExitCode==0?"PASS_REAL_PROTECTED_UAT":"FAIL";
    return child.ExitCode;
   }
  } catch(Exception error){result["error"]=error.GetType().Name+": "+error.Message;return 1;}
  finally {NewObject(staging+@"\native-seed-result.json",false,Encoding.UTF8.GetBytes(Json.Serialize(result)),corrected);}
 }
 static void ExternalResult(Dictionary<string,object> result) {
  using(var file=new FileStream(Path.Combine(Evidence,"base-seed-launch-result.json"),FileMode.CreateNew,FileAccess.Write)) {
   byte[] bytes=Encoding.UTF8.GetBytes(Json.Serialize(result)); file.Write(bytes,0,bytes.Length); file.Flush(true);
  }
 }
 static int Launch() {
  var preflight=Inspect();
  string lockPath=Path.Combine(Evidence,"base-seed-once.claim");
  using(var claim=new FileStream(lockPath,FileMode.CreateNew,FileAccess.Write)) { claim.WriteByte(1); claim.Flush(true); }
  long expiry=Now()+120; string nonce=Guid.NewGuid().ToString("N");
  IntPtr process=IntPtr.Zero; int error=0; bool launched=false;
  var done=new ManualResetEvent(false);
  var thread=new Thread(delegate(){
   bool com=false;
   try {
    int hr=CoInitializeEx(IntPtr.Zero,2); if(hr<0) {error=hr;return;} com=true;
    SEI info=new SEI {size=Marshal.SizeOf(typeof(SEI)),mask=0x40|0x100,verb="runas",file=typeof(Bootstrap).Assembly.Location,
      parameters="--seed "+expiry+" "+nonce,directory=Evidence,show=0};
    launched=ShellExecuteEx(ref info); error=launched?0:Marshal.GetLastWin32Error(); process=info.process;
   } catch(Exception failure) {error=failure.HResult;}
   finally {if(com)CoUninitialize();done.Set();}
  }); thread.IsBackground=true; thread.SetApartmentState(ApartmentState.STA); thread.Start();
  var result=Obj("DISPATCH_ID",Dispatch,"PRE_UAC_READY","YES","UAT_BASE_SEED_RUNAS_CALLS",1,
    "UPDATE_UAC_PROMPT_COUNT",0,"COORDINATOR_TOKEN",Token(),"expiry_unix",expiry,"root",Root,"attempt_nonce",nonce);
  if(!done.WaitOne(120000)) {result["status"]="CONSENT_SESSION_EXPIRED"; ExternalResult(result); return 124;}
  if(!launched) {result["status"]="UAC_NOT_ACCEPTED_OR_LAUNCH_FAILED"; result["win32_error"]=error; ExternalResult(result); return error;}
  try {
   uint remaining=900000;
   uint waited=WaitForSingleObject(process,remaining); uint code;
   if(waited!=0||!GetExitCodeProcess(process,out code)) {result["status"]="BOUNDED_HELPER_WAIT_EXPIRED"; ExternalResult(result); return 124;}
   result["status"]=code==0?"BASE_SEED_COMPLETED_AWAITING_MEDIUM_VALIDATION":"BOOTSTRAP_FAILED";
   result["helper_exit_code"]=code; ExternalResult(result); return (int)code;
  } finally {if(process!=IntPtr.Zero)CloseHandle(process);}
 }
 static int Main(string[] args) {
  try {
   if(args.Length==1&&args[0]=="--inspect") {Console.WriteLine(Json.Serialize(Inspect()));return 0;}
   if(args.Length==1&&args[0]=="--launch")return Launch();
   if(args.Length==3&&args[0]=="--seed")return Initialize(args[1],args[2]);
   return 87;
  } catch(Exception error) {Console.Error.WriteLine(error.GetType().Name+": "+error.Message); return error is Win32Exception?((Win32Exception)error).NativeErrorCode:1;}
 }
}
