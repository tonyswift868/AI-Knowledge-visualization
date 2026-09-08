#if Ver < EncodeVer(7, 0, 0)
  #error Inno Setup 7 or newer is required.
#endif
#ifndef PayloadDir
  #error Use scripts/build_inno_installer.py with a verified payload.
#endif

[Setup]
AppId=MentorFlow.Inno.v1
AppName=MentorFlow
AppVersion={#ProductVersion}
AppPublisher=MentorFlow
DefaultDirName={localappdata}\MentorFlow
DefaultGroupName=MentorFlow
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsePreviousPrivileges=no
UsePreviousAppDir=no
WizardStyle=modern {#Appearance} windows11 hidebevels includetitlebar
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
UninstallDisplayIcon={app}\MentorFlow.exe
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=no
CloseApplications=no
RestartApplications=no
SetupLogging=yes
SetupMutex=MentorFlow.Installer
ShowLanguageDialog=no
#if Int(FixtureBuild)
Uninstallable=no
CreateUninstallRegKey=no
#endif

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
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BuildInput}\ownership.ini"; DestDir: "{app}"; DestName: ".mentorflow-install.ini"; Flags: ignoreversion
Source: "{#BuildInput}\MentorFlow.png"; Flags: dontcopy

#if !Int(FixtureBuild)
[Icons]
Name: "{autoprograms}\MentorFlow"; Filename: "{app}\MentorFlow.exe"
#endif

[Run]
Filename: "{app}\MentorFlow.exe"; Description: "启动 MentorFlow"; Flags: nowait postinstall skipifsilent runasoriginaluser unchecked

[Code]
var
  LocationPage: TWizardPage;
  SelectedParent: String;

#include "directory-policy.iss"

#include "branded-pages.iss"

procedure InitializeWizard;
begin
  LocationPage := CreateCustomPage(wpWelcome, '选择安装位置', '');
  SelectedParent := ExpandConstant('{param:PARENT|{localappdata}}');
  CreateBrandedPages;
  RefreshLocation(nil);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  ShowBrandedPage(CurPageID);
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
#if Int(FixtureBuild)
  if (Result = '') and (Pos('e:\codex\temp\', Lowercase(Target)) <> 1) then
    Result := '预览安装请选择 E:\Codex\Temp 中已有的文件夹。';
#endif
  if (Result = '') and (CompareText(Target, WizardDirValue) <> 0) then
    Result := '安装位置已改变，请返回重新选择。';
  { The only application directory creation is this final single component. }
  if (Result = '') and not DirExists(Target) then
    if not CreateDir(Target) then Result := '无法创建 MentorFlow 文件夹，请选择其他位置。';
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  UpdateBrandedProgress(CurProgress, MaxProgress);
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  { Before copying any files, cancelling needs no extra modal. }
  if CurPageID = LocationPage.ID then Confirm := False;
end;

#if Int(FixtureBuild)
#include "probe.iss"
#endif
