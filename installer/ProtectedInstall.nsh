; Transactional installer: adjacent payload bytes are pinned by manifest.
!ifndef TRANSACTION_HELPER
 !error "TRANSACTION_HELPER required"
!endif
!ifndef MANIFEST_SHA
 !error "MANIFEST_SHA required"
!endif
Name "MentorFlow"
OutFile "${OUTPUT_FILE}"
InstallDir "E:\MentorFlow"
RequestExecutionLevel admin
SetCompressor lzma
ShowInstDetails hide
ShowUninstDetails hide
ManifestSupportedOS "win10"
BrandingText "MentorFlow"
Var PayloadSource
Var Installing
Var StatusFile
Var CancelFile
Var HelperArgs
Var PreviousRoot
!define MUI_CUSTOMFUNCTION_ABORT RequestCancel
!define MUI_WELCOMEPAGE_TITLE "安装 MentorFlow"
!define MUI_WELCOMEPAGE_TEXT "请选择程序安装目录。安装器需与上一级 MentorFlow UAT 程序文件一起保留。安装不修改学习或账号数据。"
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE ValidateDirectory
!insertmacro MUI_PAGE_DIRECTORY
!define MUI_PAGE_CUSTOMFUNCTION_SHOW InstallShow
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TEXT "程序安装与完整校验已完成。请关闭安装器后从桌面启动 MentorFlow，日常启动无需管理员权限。"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Function .onInit
 InitPluginsDir
 SetOutPath "$PLUGINSDIR"
 File /oname=MentorFlowInstallGate.exe "${TRANSACTION_HELPER}"
 WriteUninstaller "$PLUGINSDIR\Uninstall.exe"
 ${GetParent} "$EXEDIR" $PayloadSource
 StrCpy $StatusFile "$PLUGINSDIR\install-status.ini"
 StrCpy $CancelFile "$PLUGINSDIR\cancel.request"
 StrCpy $Installing 0
 SetRegView 64
 ReadRegStr $PreviousRoot HKLM "Software\MentorFlow" "InstallLocation"
 ${If} $PreviousRoot != ""
   ExecWait '"$PLUGINSDIR\MentorFlowInstallGate.exe" recognise --install-root "$PreviousRoot"' $0
   ${If} $0 == 0
     StrCpy $INSTDIR $PreviousRoot
   ${EndIf}
 ${EndIf}
FunctionEnd

Function ValidateDirectory
 StrCpy $HelperArgs '--install-root "$INSTDIR" --payload-source "$PayloadSource" --manifest-sha256 ${MANIFEST_SHA} --source-sha ${SOURCE_SHA} --tree-sha ${SOURCE_TREE} --uninstaller-source "$PLUGINSDIR\Uninstall.exe" --status-file "$StatusFile"'
 ExecWait '"$PLUGINSDIR\MentorFlowInstallGate.exe" preflight $HelperArgs' $0
 ${If} $0 != 0
   ReadINIStr $1 "$StatusFile" "install" "error"
   MessageBox MB_ICONSTOP "安装前检查未通过：$1"
   Abort
 ${EndIf}
 ReadINIStr $1 "$StatusFile" "install" "stage"
 ReadINIStr $2 "$StatusFile" "install" "percent"
 MessageBox MB_OKCANCEL "MentorFlow 将安装到：$INSTDIR$\r$\n所需新增空间（字节）：$1$\r$\n可用空间（字节）：$2" IDOK accepted
 Abort
 accepted:
FunctionEnd

Function InstallShow
 GetDlgItem $0 $HWNDPARENT 2
 EnableWindow $0 1
FunctionEnd

Function RequestCancel
 ${If} $Installing == 1
   FileOpen $0 "$CancelFile" w
   FileWrite $0 "cancel"
   FileClose $0
   DetailPrint "已请求取消，正在安全回滚，请稍候…"
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
 StrCpy $Installing 1
 GetDlgItem $0 $HWNDPARENT 2
 EnableWindow $0 1
 GetFunctionAddress $0 UpdateStatus
 nsDialogs::CreateTimer $0 250
 DetailPrint "正在安装 MentorFlow…"
 ExecWait '"$PLUGINSDIR\MentorFlowInstallGate.exe" transaction $HelperArgs --cancel-file "$CancelFile" --shortcut-path "$DESKTOP\MentorFlow.lnk"' $4
 GetFunctionAddress $0 UpdateStatus
 nsDialogs::KillTimer $0
 StrCpy $Installing 0
 ${If} $4 != 0
   ReadINIStr $1 "$StatusFile" "install" "error"
   DetailPrint "$1"
   MessageBox MB_ICONINFORMATION "安装未完成：$1"
   SetErrorLevel $4
   Abort
 ${EndIf}
 DetailPrint "完整验证通过，安装完成。"
SectionEnd

Section "Uninstall"
 InitPluginsDir
 SetOutPath "$PLUGINSDIR"
 File /oname=MentorFlowInstallGate.exe "${TRANSACTION_HELPER}"
 ExecWait '"$PLUGINSDIR\MentorFlowInstallGate.exe" uninstall-transaction --install-root "$INSTDIR" --shortcut-path "$DESKTOP\MentorFlow.lnk"' $0
 ${If} $0 != 0
   MessageBox MB_ICONSTOP "卸载未完成，程序或恢复事务已保留。"
   SetErrorLevel $0
   Abort
 ${EndIf}
SectionEnd
