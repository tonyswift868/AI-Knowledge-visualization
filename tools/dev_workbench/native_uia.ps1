<# Process-bound TEST UIA driver. No OS-wide keyboard/mouse injection.
   References: Microsoft UIA patterns; pywinauto Application/WindowSpecification;
   FlaUI process-bound UIA abstraction. Uses installed .NET, no runtime dependency.
#>
param(
    [Parameter(Mandatory)][int]$TargetPid,
    [Parameter(Mandatory)][string]$ExePath,
    [Parameter(Mandatory)][string]$Sha256,
    [long]$Hwnd = 0,
    [ValidateSet('Windows','Inspect','Invoke','SetValue','Select','Toggle','Close','ClickTarget','ClickWindowPoint')][string]$Action = 'Inspect',
    [string]$AutomationId = '', [string]$Name = '', [string]$ControlType = '',
    [string]$RuntimeId = '',
    [int]$PointX = -1, [int]$PointY = -1,
    [string]$FrameEvidence = '',
    [string]$Value = '', [string]$ValueFile = '', [string]$ValueKey = '',
    [Parameter(Mandatory)][string]$EvidenceRoot
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class MFGuiIdentity {
 [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
 [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr h, ref POINT p);
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SendMessageTimeout(IntPtr h,uint m,IntPtr w,IntPtr l,uint flags,uint timeout,out IntPtr result);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
}
'@
$allowed = 'D:\CodexScratch\MentorFlow\MF-MAIN3-02-LONGPATH-AB-FINAL-20260916-35\02'
function Require-OwnedPath([string]$path) {
    $full = [IO.Path]::GetFullPath($path)
    if (-not $full.StartsWith($allowed+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'OUTSIDE_ASSIGNED_SCRATCH' }
    $probe = $full
    while ($probe) {
        $entry = Get-Item -LiteralPath $probe -Force
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'REPARSE_REJECTED' }
        $probe = [IO.Path]::GetDirectoryName($probe)
    }
    return $full
}
$EvidenceRoot = Require-OwnedPath $EvidenceRoot
$ExePath = Require-OwnedPath $ExePath
function Assert-Process {
    $p = Get-Process -Id $TargetPid
    if ($p.Path -ne $ExePath) { throw 'PROCESS_PATH_MISMATCH' }
    if ((Get-FileHash -LiteralPath $p.Path -Algorithm SHA256).Hash -ne $Sha256) { throw 'PROCESS_HASH_MISMATCH' }
}
function Foreground {
    $h=[MFGuiIdentity]::GetForegroundWindow(); [uint32]$owner=0
    [void][MFGuiIdentity]::GetWindowThreadProcessId($h,[ref]$owner)
    return @{hwnd=$h.ToInt64();pid=$owner;target=($owner -eq $TargetPid)}
}
Assert-Process
$before=Foreground
$condition=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ProcessIdProperty,$TargetPid)
$root=[Windows.Automation.AutomationElement]::RootElement
if ($Action -eq 'Windows') {
    $windows=@($root.FindAll([Windows.Automation.TreeScope]::Children,$condition) | ForEach-Object {
        @{pid=$_.Current.ProcessId;hwnd=$_.Current.NativeWindowHandle;title=$_.Current.Name;automation_id=$_.Current.AutomationId;visible=[MFGuiIdentity]::IsWindowVisible([IntPtr]$_.Current.NativeWindowHandle)}
    })
    @{windows=$windows;foreground=$before} | ConvertTo-Json -Depth 6
    exit
}
if (-not $Hwnd) { throw 'EXACT_HWND_REQUIRED' }
$window=[Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
if ($window.Current.ProcessId -ne $TargetPid) { throw 'HWND_PROCESS_MISMATCH' }
$all=@($window)+@($window.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition))
if ($Action -eq 'ClickWindowPoint') {
    $framePath=Require-OwnedPath $FrameEvidence
    $frame=Get-Content -LiteralPath $framePath -Raw | ConvertFrom-Json
    $png=Require-OwnedPath $frame.path
    $age=([DateTimeOffset]::UtcNow-[DateTimeOffset]::Parse($frame.captured_at)).TotalSeconds
    if($frame.pid -ne $TargetPid -or $frame.hwnd -ne $Hwnd -or $frame.artifact_sha256 -ne $Sha256 -or
       (Get-FileHash -LiteralPath $png -Algorithm SHA256).Hash -ne $frame.image_sha256 -or
       $age -lt 0 -or $age -gt 120){throw 'STALE_OR_WRONG_FRAME'}
    if(-not $Name -or $PointX -lt 0 -or $PointY -lt 0 -or $PointX -ge $frame.width -or $PointY -ge $frame.height){throw 'OBSERVED_WINDOW_POINT_REQUIRED'}
    # Explicit visual fallback for Qt item widgets omitted from its UIA tree.
    # No OS input: the observed point is sent only to this hash-bound HWND.
    $r=$window.Current.BoundingRectangle
    if([Math]::Abs($r.X-$frame.uia_rect[0]) -gt 1 -or [Math]::Abs($r.Y-$frame.uia_rect[1]) -gt 1 -or
       [Math]::Abs($r.Width-$frame.uia_rect[2]) -gt 1 -or [Math]::Abs($r.Height-$frame.uia_rect[3]) -gt 1){throw 'WINDOW_MOVED_SINCE_FRAME'}
    $point=[MFGuiIdentity+POINT]::new();$point.X=$frame.window_rect[0]+$PointX;$point.Y=$frame.window_rect[1]+$PointY
    if(-not [MFGuiIdentity]::ScreenToClient([IntPtr]$Hwnd,[ref]$point)){throw 'COORDINATE_CONVERSION_FAILED'}
    $position=[IntPtr](($point.Y -shl 16) -bor ($point.X -band 65535));[IntPtr]$result=0
    foreach($message in @(0x201,0x202)) {
        Assert-Process
        if($window.Current.ProcessId -ne $TargetPid){throw 'STALE_PROCESS_IDENTITY'}
        $buttons=if($message -eq 0x201){1}else{0}
        if([MFGuiIdentity]::SendMessageTimeout([IntPtr]$Hwnd,$message,[IntPtr]$buttons,$position,2,2000,[ref]$result) -eq [IntPtr]::Zero){throw 'TARGET_MESSAGE_FAILED'}
    }
    $event=@{action=$Action;pattern='FRAME_BOUND_HWND_WM_LBUTTON';pid=$TargetPid;hwnd=$Hwnd;observed_name=$Name;frame_sha256=$frame.image_sha256;foreground_before=$before;foreground_after=(Foreground);os_sendinput_count=0;value_recorded=$false;success=$true}
    $line=$event|ConvertTo-Json -Depth 6 -Compress
    [IO.File]::AppendAllText((Join-Path $EvidenceRoot 'uia-events.jsonl'),$line+[Environment]::NewLine)
    $line;exit
}
if ($PointX -ge 0 -or $PointY -ge 0) {
    throw 'POINT_REQUIRES_FRAME_BOUND_ACTION'
}
if ($Action -eq 'Inspect') {
    $items=@($all | ForEach-Object {
        $c=$_.Current
        if ($c.ProcessId -eq $TargetPid) {
            $displayValue=$null
            # Only this test-owned plan editor's goal/interests are observable.
            # Never inspect login, provider or credential field values.
            if (-not $c.IsPassword -and $c.ControlType -eq [Windows.Automation.ControlType]::Edit -and $c.AutomationId -like 'QApplication.MainWindow.*.PlansPage.*.QLineEdit') {
                $displayValue=$_.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value
            }
            @{name=$(if($c.IsPassword){'[PASSWORD_CONTROL]'}else{$c.Name});runtime_id=($_.GetRuntimeId() -join '.');plan_display_value=$displayValue;automation_id=$c.AutomationId;type=$c.ControlType.ProgrammaticName;enabled=$c.IsEnabled;offscreen=$c.IsOffscreen;focused=$c.HasKeyboardFocus;hwnd=$c.NativeWindowHandle;patterns=@($_.GetSupportedPatterns() | ForEach-Object {$_.ProgrammaticName})}
        }
    })
    $r=$window.Current.BoundingRectangle
    $result=@{pid=$TargetPid;hwnd=$Hwnd;title=$window.Current.Name;window_rect=@($r.X,$r.Y,$r.Width,$r.Height);foreground=$before;elements=$items}
    $result | ConvertTo-Json -Depth 8
    exit
}
if ($Action -eq 'Close') {$selected=@($window)} else {
    if (-not $AutomationId -and -not $Name -and -not $RuntimeId) { throw 'ELEMENT_IDENTITY_REQUIRED' }
    $selected=@($all | Where-Object {
        $_.Current.ProcessId -eq $TargetPid -and
        (-not $AutomationId -or $_.Current.AutomationId -eq $AutomationId) -and
        (-not $Name -or $_.Current.Name -eq $Name) -and
        (-not $RuntimeId -or ($_.GetRuntimeId() -join '.') -eq $RuntimeId) -and
        (-not $ControlType -or $_.Current.ControlType.ProgrammaticName -eq ('ControlType.'+$ControlType))
    })
}
if ($selected.Count -ne 1) { throw 'ELEMENT_IDENTITY_NOT_UNIQUE' }
$element=$selected[0]
Assert-Process
if ($window.Current.ProcessId -ne $TargetPid -or $element.Current.ProcessId -ne $TargetPid) { throw 'STALE_PROCESS_IDENTITY' }
if ($ValueFile) {
    if ($Action -ne 'SetValue' -or -not $element.Current.IsPassword) { throw 'SECRET_TARGET_NOT_PASSWORD_CONTROL' }
    # Existing UAT33 private fixture is an explicitly authorized retained runtime.
    $privateRoot='D:\CodexScratch\MentorFlow\MF-MAIN3-02-FROZEN-GUI-AB-CLOSEOUT-20260916-33\02\uat\MF-02-UAT-33-1494a290'
    if ([IO.Path]::GetFullPath($ValueFile) -ne ($privateRoot+'\fixture-credentials.json') -or $ValueKey -ne 'password') { throw 'PRIVATE_REFERENCE_NOT_AUTHORIZED' }
    $Value=(Get-Content -LiteralPath $ValueFile -Raw | ConvertFrom-Json).$ValueKey
}
if ($Action -eq 'ClickTarget') {
    if (-not $element.Current.IsEnabled -or $element.Current.IsOffscreen) { throw 'TARGET_NOT_INTERACTABLE' }
    $bounds=$element.Current.BoundingRectangle
    $point=[MFGuiIdentity+POINT]::new();$point.X=[int]($bounds.X+$bounds.Width/2);$point.Y=[int]($bounds.Y+$bounds.Height/2)
    if(-not [MFGuiIdentity]::ScreenToClient([IntPtr]$Hwnd,[ref]$point)){throw 'COORDINATE_CONVERSION_FAILED'}
    $position=[IntPtr](($point.Y -shl 16) -bor ($point.X -band 65535))
    [IntPtr]$result=0
    foreach($message in @(0x201,0x202)) {
        Assert-Process
        if($window.Current.ProcessId -ne $TargetPid -or $element.Current.ProcessId -ne $TargetPid){throw 'STALE_PROCESS_IDENTITY'}
        $buttons=if($message -eq 0x201){1}else{0}
        if([MFGuiIdentity]::SendMessageTimeout([IntPtr]$Hwnd,$message,[IntPtr]$buttons,$position,2,2000,[ref]$result) -eq [IntPtr]::Zero){throw 'TARGET_MESSAGE_FAILED'}
    }
    $event=@{action=$Action;pattern='HWND_WM_LBUTTON';pid=$TargetPid;hwnd=$Hwnd;automation_id=$AutomationId;foreground_before=$before;foreground_after=(Foreground);os_sendinput_count=0;value_recorded=$false;success=$true}
    $line=$event|ConvertTo-Json -Depth 6 -Compress
    [IO.File]::AppendAllText((Join-Path $EvidenceRoot 'uia-events.jsonl'),$line+[Environment]::NewLine)
    $line
    exit
}
$patternName=@{Invoke='InvokePattern';SetValue='ValuePattern';Select='SelectionItemPattern';Toggle='TogglePattern';Close='WindowPattern'}[$Action]
$patternType=[Type]('System.Windows.Automation.'+$patternName)
$patternId=$patternType::Pattern
$pattern=$element.GetCurrentPattern($patternId)
switch ($Action) {
    Invoke { $pattern.Invoke() }
    SetValue { $pattern.SetValue($Value); $Value=$null }
    Select { $pattern.Select() }
    Toggle { $pattern.Toggle() }
    Close { $pattern.Close() }
}
$event=@{action=$Action;pattern=$patternName;pid=$TargetPid;hwnd=$Hwnd;automation_id=$AutomationId;runtime_id=($element.GetRuntimeId() -join '.');foreground_before=$before;foreground_after=(Foreground);os_sendinput_count=0;value_recorded=$false;success=$true}
$line=$event | ConvertTo-Json -Depth 6 -Compress
[IO.File]::AppendAllText((Join-Path $EvidenceRoot 'uia-events.jsonl'),$line+[Environment]::NewLine)
$line
