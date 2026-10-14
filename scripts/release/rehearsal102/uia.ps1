param([int]$TargetPid=0,[string]$Action='list',[string]$Name='',[string]$Value='', [string]$Out='')
$ErrorActionPreference='Stop'
$taskRoot=if($env:MF_UAT_ROOT){$env:MF_UAT_ROOT}else{'D:/MentorFlow/.local/test-tmp/inapp102c'}
$taskRoot=[IO.Path]::GetFullPath($taskRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
$env:LOCALAPPDATA=Join-Path $taskRoot 'uia-local'
$env:TEMP=Join-Path $taskRoot 'temp';$env:TMP=$env:TEMP
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class ScreenNative {
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  public delegate bool EnumCallback(IntPtr h,IntPtr p);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumCallback cb,IntPtr p);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,System.Text.StringBuilder s,int max);
 public static IntPtr Find(int process,string title){IntPtr result=IntPtr.Zero;EnumWindows((h,p)=>{uint id;GetWindowThreadProcessId(h,out id);if(id==process){var s=new System.Text.StringBuilder(512);GetWindowText(h,s,512);if(s.ToString()==title){result=h;return false;}}return true;},IntPtr.Zero);return result;}
 public struct RECT {public int left,top,right,bottom;}
}
"@
if($TargetPid -ne 0){
 $p=Get-CimInstance Win32_Process -Filter "ProcessId=$TargetPid"
 if (-not $p.ExecutablePath.StartsWith($taskRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'not an owned test process'}
}
$cond= if($TargetPid -eq 0){[System.Windows.Automation.Condition]::TrueCondition}else{New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$TargetPid)}
$tops=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$cond)
$result=@()
foreach($top in $tops){
 if($TargetPid -eq 0){
  $p=Get-CimInstance Win32_Process -Filter "ProcessId=$($top.Current.ProcessId)" -ErrorAction SilentlyContinue
  if(-not $p.ExecutablePath -or -not $p.ExecutablePath.StartsWith($taskRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){continue}
 }
 $h=[IntPtr]$top.Current.NativeWindowHandle
 $result+=@{name=$top.Current.Name;pid=$top.Current.ProcessId;hwnd=$h.ToInt64();minimized=[ScreenNative]::IsIconic($h);foreground=([ScreenNative]::GetForegroundWindow() -eq $h)}
 if($Action -eq 'shot'){
  if($Name -and $top.Current.Name -ne $Name){
   $child=$top.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,$Name)))
   $found=[ScreenNative]::Find($TargetPid,$Name)
   if($found -eq [IntPtr]::Zero){continue}
   $h=$found
  }
  $r=New-Object ScreenNative+RECT;[void][ScreenNative]::GetWindowRect($h,[ref]$r)
  $bmp=New-Object Drawing.Bitmap(($r.right-$r.left),($r.bottom-$r.top));$g=[Drawing.Graphics]::FromImage($bmp);$dc=$g.GetHdc()
  [void][ScreenNative]::PrintWindow($h,$dc,2);$g.ReleaseHdc($dc);$bmp.Save($Out,[Drawing.Imaging.ImageFormat]::Png);$g.Dispose();$bmp.Dispose();break
 }
 if($Action -eq 'list') {continue}
 if($Action -eq 'close' -and (-not $Name -or $top.Current.Name -eq $Name)){
  $top.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close();exit
 }
 $all=$top.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
 foreach($e in $all){
   $n=$e.Current.Name
   if($Action -eq 'tree'){
    $row=@{name=$n;type=$e.Current.ControlType.ProgrammaticName;class=$e.Current.ClassName;enabled=$e.Current.IsEnabled;offscreen=$e.Current.IsOffscreen}
    if($e.Current.ClassName -eq 'TNewEdit'){try{$row.value=$e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value}catch{}}
    if($e.Current.ControlType -eq [System.Windows.Automation.ControlType]::CheckBox){try{$row.toggle=$e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Current.ToggleState.ToString()}catch{}}
    $result+=$row;continue
   }
   if($n -ne $Name -or -not $e.Current.IsEnabled -or $e.Current.IsOffscreen){continue}
   if($Action -eq 'toggle'){$e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle();exit}
   if($Action -eq 'patterns'){$e.GetSupportedPatterns() | ForEach-Object {$_.ProgrammaticName};continue}
   if($Action -eq 'invoke'){
    # A freshly exposed Inno button can briefly advertise Invoke before its
    # provider returns the pattern. Retry discovery, never emulate input.
    $pattern=$null
    for($attempt=0;$attempt -lt 20 -and -not $pattern;$attempt++){
      try{$pattern=$e.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)}catch{
       Start-Sleep -Milliseconds 500
       $e=$top.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,$Name)))
      }
    }
    if(-not $pattern){
      # Some VCL buttons have no legacy .NET proxy pattern although native
      # Windows UIA provides Invoke. Stay on UIA, never emulate input/focus.
      if($e.Current.NativeWindowHandle -eq 0){throw "InvokePattern unavailable: $Name"}
      $response=& 'D:/MentorFlow/.venv/Scripts/python.exe' -B (Join-Path $PSScriptRoot 'native_uia.py') --pid $TargetPid --hwnd $e.Current.NativeWindowHandle
      if($LASTEXITCODE -ne 0){throw "Native UIA Invoke failed: $Name"}
      @{invoked=$Name;method='Windows COM UIA InvokePattern';time=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()} | ConvertTo-Json -Compress;exit
    }
    $pattern.Invoke();Start-Sleep -Milliseconds 1500;@{invoked=$Name;time=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()} | ConvertTo-Json -Compress;exit
   }
   if($Action -eq 'value' -and $e.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit){$pattern=$e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern);$pattern.SetValue($Value);@{set=$Name} | ConvertTo-Json -Compress;exit}
 }
}
if($Action -eq 'invoke' -or $Action -eq 'value' -or $Action -eq 'toggle'){throw "owned control not found: $Name"}
ConvertTo-Json -InputObject @($result) -Depth 6 -Compress
