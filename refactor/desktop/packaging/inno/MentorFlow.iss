#if Ver < EncodeVer(7, 1, 0)
  #error Inno Setup 7.1 or newer is required.
#endif
#ifndef PayloadDir
  #error Use scripts/build_learning_installer.py installer with a verified payload.
#endif

[Setup]
AppId={#ProductAppId}
AppName=MentorFlow
AppVersion={#ProductVersion}
AppPublisher=MentorFlow
DefaultDirName={localappdata}\Programs\MentorFlow
DefaultGroupName=MentorFlow
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UsePreviousPrivileges=no
UsePreviousAppDir=no
UsePreviousTasks=no
WizardStyle=modern dynamic windows11 hidebevels includetitlebar
WizardBackColor=#fffdf8
WizardBackColorDynamicDark=#242127
SetupIconFile={#BuildInput}\MentorFlow.ico
WizardSmallImageFile={#BuildInput}\MentorFlow.png
WizardSmallImageFileDynamicDark={#BuildInput}\MentorFlow.png
WizardImageFile=
WizardImageFileDynamicDark=
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableStartupPrompt=yes
DirExistsWarning=no
AllowRootDirectory=no
AllowNetworkDrive=no
AllowUNCPath=no
AllowCancelDuringInstall=yes
UninstallDisplayIcon={app}\App\MentorFlow.exe
UninstallFilesDir={app}\Maintenance
OutputDir={#OutputDir}
OutputBaseFilename=MentorFlow-Setup
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=no
CloseApplications=no
RestartApplications=no
SetupLogging=yes
SetupMutex=MentorFlow.Installer
ShowLanguageDialog=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[LangOptions]
DialogFontName=Segoe UI
DialogFontSize=9

[Messages]
SetupWindowTitle=安装 MentorFlow
WizardInstalling=正在安装 MentorFlow
InstallingLabel=正在准备应用…
FinishedHeadingLabel=MentorFlow 已安装
FinishedLabel=现在可以开始学习。
FinishedLabelNoIcons=现在可以开始学习。
ButtonNext=安装(&I)
ButtonInstall=安装(&I)
ButtonCancel=取消
ButtonFinish=启动 MentorFlow

[Files]
Source: "{#BuildInput}\MentorFlow.png"; Flags: dontcopy
; The signed core alone owns App/Updater/receipts; the shell owns Maintenance.
Source: "{#PayloadDir}\*"; DestDir: "{tmp}\payload"; Flags: dontcopy recursesubdirs createallsubdirs
Source: "{#SignedRelease}"; DestDir: "{tmp}"; DestName: "base-release.json"; Flags: dontcopy
Source: "{#BuildInput}\shell.ini"; DestDir: "{app}\Maintenance"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#ShortcutName}"; Filename: "{app}\Updater\UserRuntime.exe"; Parameters: "launch --root ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\App\MentorFlow.exe"; Check: CanonicalShortcutNeeded
Name: "{autoprograms}\{#ShortcutName} 修复与更新"; Filename: "{app}\Updater\UserRuntime.exe"; Parameters: "repair-update --root ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\App\MentorFlow.exe"; Check: NewInstallationShortcuts
Name: "{autoprograms}\卸载 {#ShortcutName}"; Filename: "{uninstallexe}"; Check: NewInstallationShortcuts
Name: "{app}\{#ShortcutName}"; Filename: "{app}\Updater\UserRuntime.exe"; Parameters: "launch --root ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\App\MentorFlow.exe"; Check: NewInstallationShortcuts

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkablealone
Name: "startup"; Description: "开机时启动 MentorFlow"; Flags: unchecked checkablealone

[Run]
Filename: "{app}\Updater\UserRuntime.exe"; Parameters: "launch --root ""{app}"""; Description: "启动 MentorFlow"; Flags: nowait postinstall skipifsilent runasoriginaluser unchecked; Check: CanLaunchMentorFlow

[UninstallDelete]
Type: files; Name: "{app}\Maintenance\finish-options.ini"

[Code]
var
  LocationPage: TWizardPage;
  SelectedParent: String;
  CoreRunning, CoreCancelRequested: Boolean;
  CoreStatus, CoreCancelFile: String;
  CoreAttempt: Integer;

#include "directory-policy.iss"

#include "finish-options.iss"

#include "branded-pages.iss"
#include "per-user-core.iss"

procedure InitializeWizard;
begin
  LocationPage := CreateCustomPage(wpWelcome, '选择安装位置', '');
  SelectedParent := ExistingKnownParent;
  CreateBrandedPages;
  RefreshLocation(nil);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  ShowBrandedPage(CurPageID);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Inno still constructs its task controls; only the separate page is skipped. }
  Result := PageID = wpSelectTasks;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Target, Problem: String; NeedsAdmin: Boolean;
begin
  Result := True;
  if CurPageID = LocationPage.ID then begin
    Problem := ValidateParent(SelectedParent, Target, NeedsAdmin, True);
    Result := Problem = '';
    if Result then WizardForm.DirEdit.Text := Target else RefreshLocation(nil);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Target: String; NeedsAdmin: Boolean;
begin
  Result := ValidateParent(SelectedParent, Target, NeedsAdmin, True);
  if (Result = '') and (CompareText(Target, WizardDirValue) <> 0) then
    Result := '安装位置已改变，请返回重新选择。';
  if Result = '' then CapturePreviousFinishOptions(Target);
  { Core validates again and creates only the final component before copying. }
  if Result = '' then Result := InstallThroughCore(Target);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    RestorePreviousFinishOptions;
  end;
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  UpdateBrandedProgress(CurProgress, MaxProgress);
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if CoreRunning then begin
    Cancel := False;
    Confirm := False;
    CoreCancelRequested := True;
    SaveStringToFile(CoreCancelFile, 'cancel', False);
    Exit;
  end;
  { Before copying any files, cancelling needs no extra modal. }
  if CurPageID = LocationPage.ID then Confirm := False;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Code: Integer;
begin
  { usUninstall is after the user's confirmation, before Inno removes files. }
  if CurUninstallStep <> usUninstall then Exit;
  { On failure leave Inno registration and shortcuts intact for recovery. }
  if not Exec(ExpandConstant('{app}\Updater\UserRuntime.exe'),
    'uninstall --root ' + AddQuotes(ExpandConstant('{app}')), ExpandConstant('{app}'),
    SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RaiseException('卸载未完成。请先关闭 MentorFlow 后重试；资料不会被删除。');
  RemoveOwnedFinishOptions;
end;
