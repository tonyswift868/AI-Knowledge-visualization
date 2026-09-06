; MentorFlow per-user offline installer.
; The build wrapper supplies PAYLOAD_DIR, PRODUCT_VERSION and OUTPUT_FILE.
; This script deliberately has no network or elevation surface.
Unicode True

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WinMessages.nsh"
!include "FileFunc.nsh"
!include "StrFunc.nsh"
!include "nsDialogs.nsh"

${Using:StrFunc} UnStrStr

!ifndef PAYLOAD_DIR
  !error "PAYLOAD_DIR is required (the verified MentorFlow payload root)."
!endif
; Also protect direct makensis invocation, not only the PowerShell wrapper.
; This legacy entry cannot establish the new admin-owned installation boundary.
!if /FileExists "${PAYLOAD_DIR}\MentorFlowInstallGate.exe"
  !error "PROTECTED_INSTALLER_REQUIRED: native-gate payload cannot use the legacy per-user installer."
!endif
!ifndef PRODUCT_VERSION
  !define PRODUCT_VERSION "0.0.0-dev"
!endif
!ifndef OUTPUT_FILE
  !define OUTPUT_FILE "MentorFlow-Setup.exe"
!endif

!define PRODUCT_NAME "MentorFlow"
; All installer-owned identities have compile-time defaults so fixture builds
; can never reuse the production HKCU subtree or Start Menu links.
!ifndef PRODUCT_KEY
  !define PRODUCT_KEY "Software\MentorFlow"
!endif
!ifndef UNINSTALL_KEY
  !define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\MentorFlow"
!endif
!ifndef SHORTCUT_FOLDER
  !define SHORTCUT_FOLDER "MentorFlow"
!endif
!ifndef SHORTCUT_NAME
  !define SHORTCUT_NAME "MentorFlow.lnk"
!endif
!ifndef UNINSTALL_SHORTCUT_NAME
  !define UNINSTALL_SHORTCUT_NAME "Uninstall MentorFlow.lnk"
!endif
!ifndef DESKTOP_SHORTCUT_NAME
  !define DESKTOP_SHORTCUT_NAME "MentorFlow.lnk"
!endif
!ifndef DATA_ROOT_CANONICAL
  !define DATA_ROOT_CANONICAL "E:\MentorFlowData"
!endif
!ifndef DATA_ROOT_LOCALAPPDATA
  !define DATA_ROOT_LOCALAPPDATA "$LOCALAPPDATA\MentorFlow"
!endif
!ifndef INSTALL_DIR
  !define INSTALL_DIR "$LOCALAPPDATA\Programs\MentorFlow"
!endif
; The build wrapper derives this from the longest verified payload-relative
; path.  Direct fixture builds keep a conservative default.
!ifndef MAX_INSTALL_DIR_CHARS
  !define MAX_INSTALL_DIR_CHARS 80
!endif
!ifndef FILE_VERSION
  !define FILE_VERSION "${PRODUCT_VERSION}.0"
!endif

Name "${PRODUCT_NAME}"
Caption "${PRODUCT_NAME} ${PRODUCT_VERSION} Setup"
OutFile "${OUTPUT_FILE}"
InstallDir "${INSTALL_DIR}"
InstallDirRegKey HKCU "${PRODUCT_KEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetDatablockOptimize on
ManifestSupportedOS "win10"
BrandingText "MentorFlow (unsigned offline installer)"
VIProductVersion "${FILE_VERSION}"
VIAddVersionKey ProductName "${PRODUCT_NAME}"
VIAddVersionKey ProductVersion "${PRODUCT_VERSION}"
VIAddVersionKey FileVersion "${PRODUCT_VERSION}"
VIAddVersionKey FileDescription "MentorFlow offline installer"
VIAddVersionKey LegalCopyright "MentorFlow"
; No Authenticode certificate is available; this build is intentionally unsigned.

!define MUI_ABORTWARNING
; Keep the installer icon independent of the payload so isolated fixture builds
; (whose executable is intentionally a tiny placeholder) remain verifiable.
!define MUI_WELCOMEPAGE_TITLE "欢迎使用 MentorFlow ${PRODUCT_VERSION}"
!define MUI_WELCOMEPAGE_TEXT "这是一个离线的按用户安装程序。不需要管理员权限或网络连接。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\MentorFlow.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动 MentorFlow"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\使用说明.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "打开 MentorFlow 使用说明"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.DataPage un.DataPageLeave
!insertmacro MUI_UNPAGE_INSTFILES
; Simplified Chinese is the first/default language; English remains available
; as an explicit fallback for systems without Chinese UI support.
LangString UN_DATA_LABEL 2052 "MentorFlow 通常会在卸载时保留本地学习数据。"
LangString UN_DATA_LABEL 1033 "MentorFlow normally preserves your local learning data when the program is removed."
LangString UN_DATA_CHECKBOX 2052 "永久删除本地学习数据（可选；此操作无法撤销）"
LangString UN_DATA_CHECKBOX 1033 "Permanently delete local learning data (optional; cannot be undone)"
LangString UN_DATA_ROOTS 2052 "如果选中，第二次确认将只列出以下根目录：\r\n  ${DATA_ROOT_CANONICAL}\r\n  ${DATA_ROOT_LOCALAPPDATA}\r\n不会删除自定义或导入的根目录。"
LangString UN_DATA_ROOTS 1033 "If selected, the second confirmation will list exactly:\r\n  ${DATA_ROOT_CANONICAL}\r\n  ${DATA_ROOT_LOCALAPPDATA}\r\nCustom or imported roots are not deleted."
LangString UN_DATA_CONFIRM 2052 "如果继续，将永久删除以下确切根目录（如存在）：\r\n\r\n${DATA_ROOT_CANONICAL}\r\n${DATA_ROOT_LOCALAPPDATA}\r\n\r\n此操作无法撤销。是否继续？"
LangString UN_DATA_CONFIRM 1033 "The following exact roots will be permanently deleted if they exist:\r\n\r\n${DATA_ROOT_CANONICAL}\r\n${DATA_ROOT_LOCALAPPDATA}\r\n\r\nThis cannot be undone. Continue?"
LangString UN_PROGRAM_TREE_UNSAFE 2052 "检测到程序目录包含符号链接、重解析点或设备节点。为避免误删其他位置，卸载已安全停止。请移除异常链接后重试。"
LangString UN_PROGRAM_TREE_UNSAFE 1033 "The program directory contains a symbolic link, reparse point, or device entry. Uninstall stopped safely to avoid deleting another location. Remove the unsafe link and try again."
LangString INSTALL_DIR_TOO_LONG 2052 "所选程序目录过长。当前安装包要求目录不超过 ${MAX_INSTALL_DIR_CHARS} 个字符，请选择更短的目录。"
LangString INSTALL_DIR_TOO_LONG 1033 "The selected program directory is too long. This package requires at most ${MAX_INSTALL_DIR_CHARS} characters; choose a shorter directory."
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; Keep a stable, per-user Start Menu location.  The desktop shortcut section is
; optional and intentionally not selected by default.
Section "MentorFlow" SEC_MAIN
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD_DIR}\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "${PRODUCT_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${PRODUCT_KEY}" "Version" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "MentorFlow"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1

  CreateDirectory "$SMPROGRAMS\${SHORTCUT_FOLDER}"
  CreateShortCut "$SMPROGRAMS\${SHORTCUT_FOLDER}\${SHORTCUT_NAME}" "$INSTDIR\MentorFlow.exe" "" "$INSTDIR\MentorFlow.exe" 0
  CreateShortCut "$SMPROGRAMS\${SHORTCUT_FOLDER}\${UNINSTALL_SHORTCUT_NAME}" "$INSTDIR\Uninstall.exe"
SectionEnd

Section /o "Desktop shortcut" SEC_DESKTOP
  SetShellVarContext current
  CreateShortCut "$DESKTOP\${DESKTOP_SHORTCUT_NAME}" "$INSTDIR\MentorFlow.exe" "" "$INSTDIR\MentorFlow.exe" 0
SectionEnd

Function .onInit
  ; Silent installs do not show the directory page, so enforce the same
  ; protected-root boundary before any payload or registry write.
  Call .onVerifyInstDir
FunctionEnd

Function .onVerifyInstDir
  ; Never let an install path overlap either approved user-data root.  A
  ; custom E: path is supported, but data roots remain outside the payload.
  StrLen $4 "$INSTDIR"
  IntCmp $4 ${MAX_INSTALL_DIR_CHARS} 0 0 path_too_long
  ${GetRoot} "$INSTDIR" $0
  StrCmp "$INSTDIR" "$0" bad_path
  StrCmp "$INSTDIR" "${DATA_ROOT_CANONICAL}" bad_path
  StrCmp "$INSTDIR" "${DATA_ROOT_LOCALAPPDATA}" bad_path

  ; Reject descendants of either data root.  An otherwise normal uninstall
  ; recursively removes $INSTDIR, so installing below a data root would turn
  ; program removal into user-data deletion.
  StrLen $0 "${DATA_ROOT_CANONICAL}"
  StrCpy $1 "$INSTDIR" $0
  StrCmp "$1" "${DATA_ROOT_CANONICAL}" 0 +3
  StrCpy $2 "$INSTDIR" 1 $0
  StrCmp "$2" "\" bad_path
  StrLen $0 "${DATA_ROOT_LOCALAPPDATA}"
  StrCpy $1 "$INSTDIR" $0
  StrCmp "$1" "${DATA_ROOT_LOCALAPPDATA}" 0 +3
  StrCpy $2 "$INSTDIR" 1 $0
  StrCmp "$2" "\" bad_path

  ; Reject ancestors of either data root as well (for example E:\ or the
  ; LocalAppData parent).  A broad program directory must never contain a
  ; protected data root that RMDir /r could reach.
  StrLen $0 "$INSTDIR"
  StrCpy $1 "${DATA_ROOT_CANONICAL}" $0
  StrCmp "$1" "$INSTDIR" 0 +3
  StrCpy $2 "${DATA_ROOT_CANONICAL}" 1 $0
  StrCmp "$2" "\" bad_path
  StrLen $0 "$INSTDIR"
  StrCpy $1 "${DATA_ROOT_LOCALAPPDATA}" $0
  StrCmp "$1" "$INSTDIR" 0 +3
  StrCpy $2 "${DATA_ROOT_LOCALAPPDATA}" 1 $0
  StrCmp "$2" "\" bad_path
  Return
path_too_long:
  IfSilent path_too_long_silent
  MessageBox MB_ICONSTOP "$(INSTALL_DIR_TOO_LONG)"
path_too_long_silent:
  ; NSIS may materialize an empty InstallDirRegKey while resolving /D before
  ; .onInit.  Remove only that empty key; existing preferences stay intact.
  DeleteRegKey /ifempty HKCU "${PRODUCT_KEY}"
  SetErrorLevel 2
  Abort
bad_path:
  IfSilent bad_path_silent
  MessageBox MB_ICONSTOP "Choose a program directory, not a MentorFlow data directory ($INSTDIR)."
bad_path_silent:
  DeleteRegKey /ifempty HKCU "${PRODUCT_KEY}"
  SetErrorLevel 2
  Abort
FunctionEnd

Var UnDeleteData
Var UnDataDialog
Var UnDataCheckbox
Var UnDataState
Var UnLocalDataRoot
Var UnTreeResult
Var UnTreeFailurePath
Var UnFindHandle
Var UnFindName
Var UnChildPath
Var UnAttributes
Var UnMatch

Function un.onInit
  StrCpy $UnDeleteData "0"
  StrCpy $UnLocalDataRoot "${DATA_ROOT_LOCALAPPDATA}"
  ; /S and /SDDL silent modes never opt in to data deletion.  The custom page
  ; is skipped by NSIS in silent mode; this explicit reset is defense in depth.
  IfSilent un.silent_init
  Return
un.silent_init:
  StrCpy $UnDeleteData "0"
FunctionEnd

Function un.DataPage
  IfSilent un.data_page_done
  nsDialogs::Create 1018
  Pop $UnDataDialog
  ${If} $UnDataDialog == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 42u "$(UN_DATA_LABEL)"
  Pop $UnMatch
  ${NSD_CreateCheckbox} 0 50u 100% 26u "$(UN_DATA_CHECKBOX)"
  Pop $UnDataCheckbox
  ${NSD_SetState} $UnDataCheckbox ${BST_UNCHECKED}
  ${NSD_CreateLabel} 0 86u 100% 62u "$(UN_DATA_ROOTS)"
  Pop $UnMatch
  nsDialogs::Show
un.data_page_done:
FunctionEnd

Function un.DataPageLeave
  IfSilent un.data_leave_done
  ${NSD_GetState} $UnDataCheckbox $UnDataState
  StrCpy $UnDeleteData "0"
  ${If} $UnDataState == ${BST_CHECKED}
    MessageBox MB_ICONEXCLAMATION|MB_YESNO|MB_DEFBUTTON2 "$(UN_DATA_CONFIRM)" IDYES un.data_confirmed
    ${NSD_SetState} $UnDataCheckbox ${BST_UNCHECKED}
  ${EndIf}
  Return
un.data_confirmed:
  StrCpy $UnDeleteData "1"
un.data_leave_done:
FunctionEnd

; Return UnTreeResult=1 only when every entry in an allowed root is a normal
; file/directory.  Reparse points, devices and enumeration errors fail closed.
Function un.CheckSafeTree
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  ; Recursive calls must preserve each directory enumeration register.  A
  ; reparse check that loses its parent handle would otherwise fail open.
  Push $UnFindHandle
  Push $UnFindName
  Push $UnChildPath
  Push $UnAttributes
  Push $UnMatch
  StrCpy $UnTreeResult "0"
  ${GetFileAttributes} "$0" "ALL" $UnAttributes
  ${If} ${Errors}
    System::Call 'kernel32::GetLastError() i .r1'
    StrCpy $UnTreeFailurePath "$0|getattrs=$1"
    Goto un.safe_tree_done
  ${EndIf}
  ${UnStrStr} $UnMatch "$UnAttributes" "REPARSE_POINT"
  ${If} $UnMatch != ""
    StrCpy $UnTreeFailurePath "$0"
    Goto un.safe_tree_done
  ${EndIf}
  ${UnStrStr} $UnMatch "$UnAttributes" "DEVICE"
  ${If} $UnMatch != ""
    StrCpy $UnTreeFailurePath "$0"
    Goto un.safe_tree_done
  ${EndIf}
  ${UnStrStr} $UnMatch "$UnAttributes" "DIRECTORY"
  ${If} $UnMatch == ""
    StrCpy $UnTreeResult "1"
    Goto un.safe_tree_done
  ${EndIf}

  ClearErrors
  FindFirst $UnFindHandle $UnFindName "$0\*"
  ${If} ${Errors}
    ; ERROR_FILE_NOT_FOUND means this is an ordinary empty directory.  Other
    ; errors (notably access denied) remain fail-closed.
    System::Call 'kernel32::GetLastError() i .r1'
    StrCmp $1 "2" un.safe_tree_empty
    StrCmp $1 "18" un.safe_tree_empty
    StrCpy $UnTreeFailurePath "$0|findfirst=$1"
    Goto un.safe_tree_done
  ${EndIf}
un.safe_tree_loop:
  StrCmp "$UnFindName" "" un.safe_tree_close
  StrCmp "$UnFindName" "." un.safe_tree_next
  StrCmp "$UnFindName" ".." un.safe_tree_next
  StrCpy $UnChildPath "$0\$UnFindName"
  Push "$UnChildPath"
  Call un.CheckSafeTree
  ${If} $UnTreeResult == "0"
    FindClose $UnFindHandle
    Goto un.safe_tree_done
  ${EndIf}
un.safe_tree_next:
  ClearErrors
  FindNext $UnFindHandle $UnFindName
  ${If} ${Errors}
    FindClose $UnFindHandle
    ; NSIS reports the ordinary end of FindNext through its error flag, but
    ; does not preserve a reliable Win32 last-error value (an empty directory
    ; can surface an unrelated stale value).  FindFirst failures remain
    ; fail-closed; after enumeration has started, this is the normal terminus.
    StrCpy $UnTreeResult "1"
    Goto un.safe_tree_done
  ${EndIf}
  Goto un.safe_tree_loop
un.safe_tree_empty:
  StrCpy $UnTreeResult "1"
  Goto un.safe_tree_done
un.safe_tree_close:
  FindClose $UnFindHandle
  StrCpy $UnTreeResult "1"
un.safe_tree_done:
  Pop $UnMatch
  Pop $UnAttributes
  Pop $UnChildPath
  Pop $UnFindName
  Pop $UnFindHandle
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Only the two literal roots below can ever be passed to this helper.  The
; caller does not accept command-line paths, so arbitrary imported roots stay.
Function un.DeleteAllowedRoot
  Exch $0
  Push $1
  StrCmp "$0" "${DATA_ROOT_CANONICAL}" un.allowed_root_ok
  StrCmp "$0" "$UnLocalDataRoot" un.allowed_root_ok
  Goto un.allowed_root_done
un.allowed_root_ok:
  Push "$0"
  Call un.CheckSafeTree
  ${If} $UnTreeResult == "1"
    RMDir /r "$0"
  ${EndIf}
un.allowed_root_done:
  Pop $1
  Pop $0
FunctionEnd

; Remove the product key only when the installer-owned values were its entire
; contents.  Existing preferences or subkeys are deliberately preserved.
Function un.DeleteEmptyProductKey
  ClearErrors
  EnumRegValue $0 HKCU "${PRODUCT_KEY}" 0
  ${IfNot} ${Errors}
    Return
  ${EndIf}
  ClearErrors
  EnumRegKey $0 HKCU "${PRODUCT_KEY}" 0
  ${IfNot} ${Errors}
    Return
  ${EndIf}
  DeleteRegKey /ifempty HKCU "${PRODUCT_KEY}"
FunctionEnd

Function un.onUninstSuccess
  HideWindow
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  ; Fail closed before any installer-owned state is removed.  This blocks an
  ; installed child junction/reparse point from redirecting recursive program
  ; cleanup outside the selected install directory.
  Push "$INSTDIR"
  Call un.CheckSafeTree
  ${If} $UnTreeResult != "1"
    IfSilent un.program_tree_unsafe_silent
    MessageBox MB_ICONSTOP "$(UN_PROGRAM_TREE_UNSAFE)"
un.program_tree_unsafe_silent:
    SetErrorLevel 2
    System::Call 'kernel32::ExitProcess(i 2)'
  ${EndIf}
  ; Data deletion is opt-in and can only be reached through the interactive
  ; second confirmation.  Silent uninstall always has UnDeleteData=0.
  ${If} $UnDeleteData == "1"
    Push "${DATA_ROOT_CANONICAL}"
    Call un.DeleteAllowedRoot
    Push "$UnLocalDataRoot"
    Call un.DeleteAllowedRoot
  ${EndIf}
  Delete "$SMPROGRAMS\${SHORTCUT_FOLDER}\${SHORTCUT_NAME}"
  Delete "$SMPROGRAMS\${SHORTCUT_FOLDER}\${UNINSTALL_SHORTCUT_NAME}"
  RMDir "$SMPROGRAMS\${SHORTCUT_FOLDER}"
  Delete "$DESKTOP\${DESKTOP_SHORTCUT_NAME}"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  ; The stable product key may contain app preferences.  Remove only the
  ; installer-owned values; never erase unrelated product/user state.
  DeleteRegValue HKCU "${PRODUCT_KEY}" "InstallDir"
  DeleteRegValue HKCU "${PRODUCT_KEY}" "Version"
  Call un.DeleteEmptyProductKey
  ; A second direct empty-key attempt accommodates registry providers that
  ; defer the first close until the uninstall section unwinds.
  DeleteRegKey /ifempty HKCU "${PRODUCT_KEY}"
  RMDir /r "$INSTDIR"
SectionEnd
