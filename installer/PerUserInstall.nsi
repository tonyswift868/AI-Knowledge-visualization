; Default consumer mode. No runas, machine registration or protected ACLs.
Unicode True
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "nsDialogs.nsh"
!include "WinMessages.nsh"
Name "MentorFlow"
OutFile "${OUTPUT_FILE}"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\MentorFlow"
ShowInstDetails hide
ShowUninstDetails hide
SetCompressor lzma
Var DirectoryEdit
Var Parent
Var Payload
Var Installing
Var StatusFile
Var CancelFile
!define MUI_ABORTWARNING
!define MUI_CUSTOMFUNCTION_ABORT CancelInstall
!insertmacro MUI_PAGE_WELCOME
Page custom DirectoryCreate DirectoryLeave
!define MUI_PAGE_CUSTOMFUNCTION_SHOW InstallShow
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
 SetShellVarContext current
 InitPluginsDir
 SetOutPath "$PLUGINSDIR\UserRuntime"
 File /r "${USER_RUNTIME}\*.*"
 SetOutPath "$PLUGINSDIR"
 File /oname=release.json "${SIGNED_RELEASE}"
 ${GetParent} "$EXEDIR" $Payload
 StrCpy $StatusFile "$PLUGINSDIR\status.ini"
 StrCpy $CancelFile "$PLUGINSDIR\cancel"
 StrCpy $Installing 0
 ReadRegStr $0 HKCU "Software\${APP_ID}" "InstallLocation"
 ${If} $0 != ""
  ExecWait '"$PLUGINSDIR\UserRuntime\UserRuntime.exe" preflight --root "$0"' $1
  ${If} $1 == 0
   StrCpy $INSTDIR $0
  ${EndIf}
 ${EndIf}
FunctionEnd

Function DirectoryCreate
 !insertmacro MUI_HEADER_TEXT "安装位置" "仅为当前用户安装，无需管理员权限。"
 nsDialogs::Create 1018
 Pop $0
 ${NSD_CreateLabel} 0 0 100% 28u "请选择这台电脑上已有且可写的文件夹。MentorFlow 将安装到其中的 MentorFlow 子文件夹。"
 Pop $0
 ${NSD_CreateText} 0 35u 78% 14u "$INSTDIR"
 Pop $DirectoryEdit
 ${NSD_CreateBrowseButton} 80% 35u 20% 14u "浏览…"
 Pop $0
 ${NSD_OnClick} $0 BrowseParent
 ${NSD_CreateLabel} 0 60u 100% 28u "需要空间：约 ${SPACE_MB} MB。若要为所有用户安装，请明确使用管理员安装模式。"
 Pop $0
 nsDialogs::Show
FunctionEnd
Function BrowseParent
 Pop $0
 ${NSD_GetText} $DirectoryEdit $0
 ${GetParent} "$0" $Parent
 nsDialogs::SelectFolderDialog "选择已有的文件夹" "$Parent"
 Pop $Parent
 ${If} $Parent == "error"
  Return
 ${EndIf}
 System::Call 'kernel32::GetFileAttributesW(w "$Parent")i.r0'
 IntOp $1 $0 & 0x10
 ${If} $0 == -1
 ${OrIf} $1 == 0
  MessageBox MB_ICONSTOP "所选安装位置不存在。请选择一个已经存在的文件夹。"
  Return
 ${EndIf}
 StrCpy $0 $Parent 1 -1
 ${If} $0 == "\"
  StrCpy $Parent $Parent -1
 ${EndIf}
 ${NSD_SetText} $DirectoryEdit "$Parent\MentorFlow"
FunctionEnd
Function DirectoryLeave
 ${NSD_GetText} $DirectoryEdit $INSTDIR
 ExecWait '"$PLUGINSDIR\UserRuntime\UserRuntime.exe" preflight --root "$INSTDIR" --status-file "$StatusFile"' $0
 ${If} $0 != 0
  ReadINIStr $1 "$StatusFile" "install" "error"
  MessageBox MB_ICONSTOP "$1"
  Abort
 ${EndIf}
FunctionEnd
Function InstallShow
 GetDlgItem $0 $HWNDPARENT 2
 EnableWindow $0 1
FunctionEnd
Function CancelInstall
 ${If} $Installing == 1
  FileOpen $0 "$CancelFile" w
  FileClose $0
  Abort
 ${EndIf}
FunctionEnd
Function UpdateStatus
 ReadINIStr $0 "$StatusFile" "install" "stage"
 ReadINIStr $1 "$StatusFile" "install" "percent"
 FindWindow $2 "#32770" "" $HWNDPARENT
 GetDlgItem $3 $2 1006
 SendMessage $3 ${WM_SETTEXT} 0 "STR:$0"
 GetDlgItem $3 $2 1004
 SendMessage $3 ${PBM_SETPOS} $1 0
FunctionEnd
Section "MentorFlow"
 AddSize ${SPACE_KIB}
 StrCpy $Installing 1
 GetFunctionAddress $0 UpdateStatus
 nsDialogs::CreateTimer $0 250
 ExecWait '"$PLUGINSDIR\UserRuntime\UserRuntime.exe" install --root "$INSTDIR" --source "$Payload" --release "$PLUGINSDIR\release.json" --status-file "$StatusFile" --cancel-file "$CancelFile"' $4
 GetFunctionAddress $0 UpdateStatus
 nsDialogs::KillTimer $0
 StrCpy $Installing 0
 ${If} $4 != 0
  ReadINIStr $1 "$StatusFile" "install" "error"
  MessageBox MB_ICONSTOP "安装未完成：$1"
  SetErrorLevel $4
  Abort
 ${EndIf}
 WriteRegStr HKCU "Software\${APP_ID}" "InstallLocation" "$INSTDIR"
 WriteRegStr HKCU "Software\${APP_ID}" "Version" "${VERSION}"
 CreateShortCut "$DESKTOP\${APP_ID}.lnk" "$INSTDIR\App\MentorFlow.exe" '--install-mode per_user --install-root "$INSTDIR"'
 CreateShortCut "$SMPROGRAMS\${APP_ID}.lnk" "$INSTDIR\App\MentorFlow.exe" '--install-mode per_user --install-root "$INSTDIR"'
 DetailPrint "安装成功。"
SectionEnd
