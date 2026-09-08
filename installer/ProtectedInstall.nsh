; Transactional installer: adjacent payload bytes are pinned by manifest.
!ifndef TRANSACTION_HELPER
 !error "TRANSACTION_HELPER required"
!endif
!ifndef TRANSACTION_RUNTIME
 !error "Verified onedir TRANSACTION_RUNTIME required; elevated onefile execution prohibited"
!endif
!ifndef MANIFEST_SHA
 !error "MANIFEST_SHA required"
!endif
!ifndef INSTALL_SPACE_KIB
 !error "INSTALL_SPACE_KIB required from verified payload inventory"
!endif
!if ${INSTALL_SPACE_KIB} <= 0
 !error "INSTALL_SPACE_KIB must be positive"
!endif
!if ${INSTALL_SPACE_KIB} > 2147483647
 !error "INSTALL_SPACE_KIB exceeds NSIS section-size range"
!endif
!ifndef PROTECTED_APP_ID
 !define PROTECTED_APP_ID "MentorFlow"
!endif
!ifndef PROTECTED_SHORTCUT_NAME
 !define PROTECTED_SHORTCUT_NAME "MentorFlow.lnk"
!endif
Name "MentorFlow"
OutFile "${OUTPUT_FILE}"
InstallDir "$PROGRAMFILES64\MentorFlow"
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
Var DirectoryDialog
Var DirectoryEdit
Var BrowseButton
Var SelectedParent
Var DirectoryPreview
!define MUI_CUSTOMFUNCTION_ABORT RequestCancel
!define MUI_WELCOMEPAGE_TITLE "安装 MentorFlow"
!define MUI_WELCOMEPAGE_TEXT "欢迎安装 MentorFlow。请选择这台电脑上已有的安装位置。安装不会修改您的学习资料或账号数据。"
!insertmacro MUI_PAGE_WELCOME
Page custom DirectoryCreate ValidateDirectory
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
 ; NSIS 3.12 creates a restricted plugin directory. Embed the complete onedir
 ; runtime; never run a High onefile bootloader using inherited user TEMP.
 SetOutPath "$PLUGINSDIR\InstallRuntime"
 File /r "${TRANSACTION_RUNTIME}\*.*"
 SetOutPath "$PLUGINSDIR"
 WriteUninstaller "$PLUGINSDIR\Uninstall.exe"
 ${GetParent} "$EXEDIR" $PayloadSource
 StrCpy $StatusFile "$PLUGINSDIR\install-status.ini"
 StrCpy $CancelFile "$PLUGINSDIR\cancel.request"
 StrCpy $Installing 0
 ; Resolve current-machine system parent at runtime; never compile a drive.
 StrCpy $SelectedParent "$PROGRAMFILES64"
 IfFileExists "$SelectedParent\*.*" default_parent_ok
 StrCpy $SelectedParent "$PROGRAMFILES"
 IfFileExists "$SelectedParent\*.*" default_parent_ok
 StrCpy $SelectedParent "$LOCALAPPDATA"
 IfFileExists "$SelectedParent\*.*" default_parent_ok
 MessageBox MB_ICONSTOP "找不到有效的默认安装位置，请检查这台电脑的系统目录。"
 Abort
 default_parent_ok:
 StrCpy $INSTDIR "$SelectedParent\MentorFlow"
 SetRegView 64
 ReadRegStr $PreviousRoot HKLM "Software\${PROTECTED_APP_ID}" "InstallLocation"
 ${If} $PreviousRoot != ""
   ExecWait '"$PLUGINSDIR\InstallRuntime\MentorFlowInstallGate.exe" recognise --install-root "$PreviousRoot"' $0
   ${If} $0 == 0
     StrCpy $INSTDIR $PreviousRoot
   ${EndIf}
 ${EndIf}
FunctionEnd

Function DirectoryCreate
 !insertmacro MUI_HEADER_TEXT "安装位置" "选择这台电脑上已有的文件夹。"
 nsDialogs::Create 1018
 Pop $DirectoryDialog
 ${NSD_CreateLabel} 0 0 100% 28u "MentorFlow 将安装到所选文件夹中的 MentorFlow 子文件夹。"
 Pop $0
 ${NSD_CreateText} 0 35u 78% 14u "$INSTDIR"
 Pop $DirectoryEdit
 ${NSD_CreateBrowseButton} 80% 35u 20% 14u "浏览…"
 Pop $BrowseButton
 ${NSD_OnClick} $BrowseButton BrowseExistingParent
 ${NSD_CreateLabel} 0 63u 100% 30u "需要空间：约 ${INSTALL_SPACE_MB} MB。安装前会自动检查可用空间。"
 Pop $DirectoryPreview
 nsDialogs::Show
FunctionEnd

Function BrowseExistingParent
 Pop $0
 ${NSD_GetText} $DirectoryEdit $1
 ${GetParent} "$1" $SelectedParent
 nsDialogs::SelectFolderDialog "选择已有的安装位置" "$SelectedParent"
 Pop $SelectedParent
 ${If} $SelectedParent == "error"
   Return
 ${EndIf}
 ; The native picker result is untrusted until an actual directory check.
 System::Call 'kernel32::GetFileAttributesW(w "$SelectedParent")i.r0'
 IntOp $1 $0 & 0x10
 ${If} $0 == -1
 ${OrIf} $1 == 0
   MessageBox MB_ICONSTOP "所选安装位置不存在。请选择一个已经存在的文件夹。"
   Return
 ${EndIf}
 StrCpy $1 $SelectedParent 1 -1
 ${If} $1 == "\"
   StrCpy $SelectedParent $SelectedParent -1
 ${EndIf}
 StrCpy $INSTDIR "$SelectedParent\MentorFlow"
 ${NSD_SetText} $DirectoryEdit "$INSTDIR"
FunctionEnd

Function ValidateDirectory
 ${NSD_GetText} $DirectoryEdit $INSTDIR
 !ifdef MAX_INSTALL_DIR_CHARS
 StrLen $0 $INSTDIR
 ${If} $0 > ${MAX_INSTALL_DIR_CHARS}
   MessageBox MB_ICONSTOP "安装位置路径过长，请选择更靠近磁盘根目录的位置。"
   Abort
 ${EndIf}
 !endif
 StrCpy $HelperArgs '--install-root "$INSTDIR" --payload-source "$PayloadSource" --manifest-sha256 ${MANIFEST_SHA} --source-sha ${SOURCE_SHA} --tree-sha ${SOURCE_TREE} --uninstaller-source "$PLUGINSDIR\Uninstall.exe" --status-file "$StatusFile"'
 ExecWait '"$PLUGINSDIR\InstallRuntime\MentorFlowInstallGate.exe" preflight $HelperArgs' $0
 ${If} $0 != 0
   ReadINIStr $1 "$StatusFile" "install" "error"
   MessageBox MB_ICONSTOP "安装前检查未通过：$1"
   Abort
 ${EndIf}
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
 ; Payload copying occurs in the external helper, not NSIS File instructions.
 ; Populate the native directory-page estimate without disabling disk checks.
 AddSize ${INSTALL_SPACE_KIB}
 StrCpy $Installing 1
 GetDlgItem $0 $HWNDPARENT 2
 EnableWindow $0 1
 GetFunctionAddress $0 UpdateStatus
 nsDialogs::CreateTimer $0 250
 DetailPrint "正在安装 MentorFlow…"
 ExecWait '"$PLUGINSDIR\InstallRuntime\MentorFlowInstallGate.exe" transaction $HelperArgs --cancel-file "$CancelFile" --shortcut-path "$DESKTOP\${PROTECTED_SHORTCUT_NAME}"' $4
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
 DetailPrint "安装成功。"
 IfFileExists "$INSTDIR\UpdateBridge.exe" 0 +2
   Exec '"$INSTDIR\UpdateBridge.exe" --initial-health'
SectionEnd

Section "Uninstall"
 InitPluginsDir
 SetOutPath "$PLUGINSDIR\InstallRuntime"
 File /r "${TRANSACTION_RUNTIME}\*.*"
 SetOutPath "$PLUGINSDIR"
 ExecWait '"$PLUGINSDIR\InstallRuntime\MentorFlowInstallGate.exe" uninstall-transaction --install-root "$INSTDIR" --shortcut-path "$DESKTOP\${PROTECTED_SHORTCUT_NAME}"' $0
 ${If} $0 != 0
   MessageBox MB_ICONSTOP "卸载未完成，程序或恢复事务已保留。"
   SetErrorLevel $0
   Abort
 ${EndIf}
SectionEnd
