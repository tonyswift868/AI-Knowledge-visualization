// Stable, installed control binary. No arbitrary command/plugin or UAC retry.
using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

[assembly:System.Reflection.AssemblyTitle("MentorFlow Update and Restart")]
[assembly:System.Reflection.AssemblyProduct("MentorFlow Update and Restart")]
[assembly:System.Reflection.AssemblyVersion("2.0.0.0")]

internal static class UpdateBridge {
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true,EntryPoint="CreateFileW")]
    static extern SafeFileHandle Open(string path,uint access,uint share,IntPtr sa,uint create,uint flags,IntPtr template);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandleEx(SafeFileHandle h,int kind,byte[] data,uint size);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFinalPathNameByHandle(SafeFileHandle h,StringBuilder path,uint size,uint flags);
    [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
    [DllImport("advapi32.dll",SetLastError=true)] static extern bool GetTokenInformation(IntPtr token,int kind,IntPtr data,int size,out int needed);
    [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
    [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthority(IntPtr sid,uint index);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string text,uint revision,out IntPtr sd,out uint size);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr pointer);
    [StructLayout(LayoutKind.Sequential)] struct SA {public int size;public IntPtr descriptor;public int inherit;}
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateDirectory(string path,ref SA sa);

    static string Hash(string path) {
        using(var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read))
        using(var hash=SHA256.Create())return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-","").ToLowerInvariant();
    }
    static void VerifyAcl(string path,bool directory) {
        if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException("Reparse control rejected");
        FileSystemSecurity acl=directory?(FileSystemSecurity)Directory.GetAccessControl(path):File.GetAccessControl(path);
        string owner=acl.GetOwner(typeof(SecurityIdentifier)).Value;
        if((owner!="S-1-5-32-544"&&owner!="S-1-5-18")||!acl.AreAccessRulesProtected)throw new InvalidOperationException("Unprotected control authority");
        var seen=new HashSet<string>();
        foreach(FileSystemAccessRule rule in acl.GetAccessRules(true,true,typeof(SecurityIdentifier))) {
            string sid=rule.IdentityReference.Value;
            int rights=sid=="S-1-5-32-545"?0x1200a9:0x1f01ff;
            if((sid!="S-1-5-32-544"&&sid!="S-1-5-18"&&sid!="S-1-5-32-545")||
               rule.AccessControlType!=AccessControlType.Allow||(int)rule.FileSystemRights!=rights||!seen.Add(sid))
                throw new InvalidOperationException("Unexpected control access rule");
        }
        if(seen.Count!=3)throw new InvalidOperationException("Incomplete control access rules");
    }
    static void NewProtectedDirectory(string path) {
        if(Directory.Exists(path)){VerifyAcl(path,true);return;}
        IntPtr sd;uint size;
        if(!ConvertStringSecurityDescriptorToSecurityDescriptor("O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)",1,out sd,out size))
            throw new System.ComponentModel.Win32Exception();
        try {
            SA sa=new SA {size=Marshal.SizeOf(typeof(SA)),descriptor=sd,inherit=0};
            if(!CreateDirectory(path,ref sa))throw new System.ComponentModel.Win32Exception();
        }finally{LocalFree(sd);}
        VerifyAcl(path,true);
    }
    static void RequireToken(bool high) {
        IntPtr token;if(!OpenProcessToken(Process.GetCurrentProcess().Handle,8,out token))throw new System.ComponentModel.Win32Exception();
        try {
            int size;IntPtr data=Marshal.AllocHGlobal(4);bool elevated;
            try {if(!GetTokenInformation(token,20,data,4,out size))throw new System.ComponentModel.Win32Exception();elevated=Marshal.ReadInt32(data)!=0;}
            finally{Marshal.FreeHGlobal(data);}
            GetTokenInformation(token,25,IntPtr.Zero,0,out size);data=Marshal.AllocHGlobal(size);
            try {
                if(!GetTokenInformation(token,25,data,size,out size))throw new System.ComponentModel.Win32Exception();
                IntPtr sid=Marshal.ReadIntPtr(data);byte count=Marshal.ReadByte(GetSidSubAuthorityCount(sid));
                int rid=Marshal.ReadInt32(GetSidSubAuthority(sid,(uint)(count-1)));
                if(rid!=(high?12288:8192)||elevated!=high)throw new InvalidOperationException("Actual token does not match bridge role");
            }finally{Marshal.FreeHGlobal(data);}
        }finally{CloseHandle(token);}
    }
    static string Quote(string value) {
        var result=new StringBuilder("\"");int slashes=0;
        foreach(char ch in value) {
            if(ch=='\\'){++slashes;continue;}
            result.Append('\\',slashes*(ch=='"'?2:1));slashes=0;
            if(ch=='"')result.Append('\\');result.Append(ch);
        }
        result.Append('\\',slashes*2);return result.Append('"').ToString();
    }
    static Dictionary<string,object> Object(object value){return (Dictionary<string,object>)value;}
    static int Main(string[] args) {
        var pins=new List<SafeFileHandle>();
        try {
            if(args.Length<1 || (args[0]!="--coordinate"&&args[0]!="--apply"&&args[0]!="--initial-health"))return 87;
            bool high=args[0]!="--coordinate";
            if(args.Length!=(args[0]=="--initial-health"?1:3))return 87;
            if(args.Length==3&&!System.Text.RegularExpressions.Regex.IsMatch(args[2],@"\A[0-9a-f]{64}\z"))return 87;
            RequireToken(high);
            string self=typeof(UpdateBridge).Assembly.Location,root=Path.GetDirectoryName(self);
            if(Path.GetFileName(self)!="UpdateBridge.exe")throw new InvalidOperationException("Installed stable bridge required");
            // Pin ancestors before inspecting/loading any adjacent runtime.
            var ancestors=new Stack<string>();string next=root;
            while(next!=null){ancestors.Push(next);next=Path.GetDirectoryName(next);}
            while(ancestors.Count>0) {
                string dir=ancestors.Pop();
                if((File.GetAttributes(dir)&FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException("Aliased bridge ancestor");
                var handle=Open(dir,0x80,3,IntPtr.Zero,3,0x02200000,IntPtr.Zero);
                if(handle.IsInvalid)throw new System.ComponentModel.Win32Exception();
                pins.Add(handle);var final=new StringBuilder(32768);
                uint n=GetFinalPathNameByHandle(handle,final,32768,0);
                if(n==0||n>=32768||!String.Equals(final.ToString().Replace(@"\\?\",""),dir,StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Bridge ancestor resolved elsewhere");
            }
            VerifyAcl(root,true);VerifyAcl(self,false);
            string receiptPath=Path.Combine(root,"installation-receipt.json");VerifyAcl(receiptPath,false);
            if(new FileInfo(receiptPath).Length>16*1024*1024)throw new InvalidOperationException("Oversized receipt");
            var json=new JavaScriptSerializer {MaxJsonLength=16*1024*1024};
            var receipt=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(receiptPath));
            var binding=Object(receipt["binding"]);var controls=Object(receipt["control_hashes"]);
            if((string)binding["schema"]!="mentorflow.admin-installation.v2"||(string)binding["root"]!=root||
               (string)controls["UpdateBridge.exe"]!=Hash(self))throw new InvalidOperationException("Stable bridge receipt mismatch");
            ulong volume;byte[] id;WindowsIdentityWire.Decode(Object(binding["root_identity"]),out volume,out id);
            byte[] actual=new byte[24];
            if(!GetFileInformationByHandleEx(pins[pins.Count-1],18,actual,24)||BitConverter.ToUInt64(actual,0)!=volume)
                throw new InvalidOperationException("Bridge volume mismatch");
            for(int i=0;i<16;++i)if(actual[i+8]!=id[i])throw new InvalidOperationException("Bridge FileId mismatch");
            string runtimeDir=Path.Combine(root,"UpdateRuntime");VerifyAcl(runtimeDir,true);
            var expected=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach(var pair in controls)if(pair.Key.StartsWith("UpdateRuntime/",StringComparison.Ordinal)) {
                foreach(string part in pair.Key.Split('/'))
                    if(part.Length==0||part=="."||part==".."||part.IndexOfAny(Path.GetInvalidFileNameChars())>=0||part.EndsWith(".")||part.EndsWith(" "))
                        throw new InvalidOperationException("Invalid runtime control path");
                string relative=pair.Key.Replace('/','\\');
                string path=Path.GetFullPath(Path.Combine(root,relative));
                if(!path.StartsWith(runtimeDir+"\\",StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("Runtime escape");
                if(!expected.Add(path))throw new InvalidOperationException("Duplicate runtime control path");
            }
            // Verify each parent before descending: recursive enumeration must
            // never follow a junction first and check it afterwards.
            var pending=new Stack<string>();pending.Push(runtimeDir);
            while(pending.Count>0) {
                string parent=pending.Pop();VerifyAcl(parent,true);
                foreach(string entry in Directory.GetFileSystemEntries(parent)) {
                    FileAttributes attrs=File.GetAttributes(entry);
                    if((attrs&FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException("Aliased runtime entry");
                    if((attrs&FileAttributes.Directory)!=0){VerifyAcl(entry,true);pending.Push(entry);continue;}
                    if(!expected.Remove(entry))throw new InvalidOperationException("Unknown control runtime file");
                    VerifyAcl(entry,false);
                    string key=entry.Substring(root.Length+1).Replace('\\','/');
                    if(Hash(entry)!=(string)controls[key])throw new InvalidOperationException("Control runtime tampered");
                }
            }
            if(expected.Count!=0)throw new InvalidOperationException("Missing control runtime file");
            string runtime=Path.Combine(runtimeDir,"UpdateRuntime.exe");
            if(!controls.ContainsKey("UpdateRuntime/UpdateRuntime.exe"))throw new InvalidOperationException("Runtime not pinned");
            string temp;
            if(high) {
                string stage=Path.Combine(root,".install-staging");NewProtectedDirectory(stage);
                string metadata=Path.Combine(stage,"update-metadata");NewProtectedDirectory(metadata);
                temp=Path.Combine(metadata,"native-temp");NewProtectedDirectory(temp);
            }else temp=Path.GetDirectoryName(Path.GetFullPath(args[1]));
            var command=new StringBuilder(args[0]+" --install-root "+Quote(root));
            if(args.Length==3)command.Append(" --request "+Quote(Path.GetFullPath(args[1]))+" --request-sha256 "+args[2]);
            var start=new ProcessStartInfo(runtime,command.ToString()) {UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=runtimeDir};
            start.EnvironmentVariables.Clear();
            foreach(string key in new[]{"SystemRoot","WINDIR","USERPROFILE","LOCALAPPDATA","APPDATA","PROGRAMDATA"})
                if(Environment.GetEnvironmentVariable(key)!=null)start.EnvironmentVariables[key]=Environment.GetEnvironmentVariable(key);
            start.EnvironmentVariables["PATH"]=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),"System32");
            start.EnvironmentVariables["TEMP"]=temp;start.EnvironmentVariables["TMP"]=temp;
            using(var child=Process.Start(start)) {
                if(!child.WaitForExit(900000)){child.Kill();child.WaitForExit(5000);return 124;}
                return child.ExitCode;
            }
        }catch(Exception error){Console.Error.WriteLine(error.GetType().Name+": "+error.Message);return 3;}
        finally{for(int i=pins.Count-1;i>=0;--i)pins[i].Dispose();}
    }
}
