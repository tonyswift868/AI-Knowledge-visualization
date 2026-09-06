; Included only by the existing MentorFlow.nsi protected-payload branch.
!ifndef ROOT_HELPER
 !error "ROOT_HELPER required"
!endif
!ifndef MANIFEST_SHA
 !error "MANIFEST_SHA required"
!endif
!ifndef SOURCE_SHA
 !error "SOURCE_SHA required"
!endif
!ifndef SOURCE_TREE
 !error "SOURCE_TREE required"
!endif
Name "MentorFlow UAT"
OutFile "${OUTPUT_FILE}"
InstallDir "E:\MentorFlow"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
SetDatablockOptimize on
ManifestSupportedOS "win10"
BrandingText "MentorFlow — protected UAT installation"
!define MUI_WELCOMEPAGE_TITLE "安装 MentorFlow UAT"
!define MUI_WELCOMEPAGE_TEXT "程序将安装到 E:\MentorFlow\App。只在安装时需要管理员权限，不修改学习数据。安装后请通过桌面快捷方式正常启动。"
!define MUI_FINISHPAGE_TEXT "安装和完整校验已完成。请关闭安装器，再双击桌面的 MentorFlow。日常启动不需要管理员权限。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Section "Protected MentorFlow installation"
 ; /D must never redirect this narrowly scoped UAT installer.
 StrCpy $INSTDIR "E:\MentorFlow"
 InitPluginsDir
 SetOutPath "$PLUGINSDIR"
 File /oname=PrepareMentorFlowRoot.exe "${ROOT_HELPER}"
 DetailPrint "正在检查 E: 路径并创建受保护的程序目录…"
 ExecWait '"$PLUGINSDIR\PrepareMentorFlowRoot.exe"' $0
 ${If} $0 != 0
   MessageBox MB_ICONSTOP "无法创建受保护目录。现有 E:\MentorFlow 不会被覆盖；请保留现场并请求修复。"
   SetErrorLevel 3
   Abort
 ${EndIf}
 SetOutPath "$INSTDIR\App"
 SetOverwrite off
 ClearErrors
 File /r /x "Installer" "${PAYLOAD_DIR}\*"
 ${If} ${Errors}
   MessageBox MB_ICONSTOP "程序文件释放失败。未创建启动快捷方式，请保留现场并请求修复。"
   SetErrorLevel 3
   Abort
 ${EndIf}
 DetailPrint "正在锁定文件继承权限…"
 ; Extraction already inherits RX-only user access. Canonicalize each new
 ; file's owner/protected DACL using the existing installer-only primitive.
 ExecWait '"$INSTDIR\App\MentorFlowInstallGate.exe" protect --install-root "$INSTDIR"' $0
 ${If} $0 != 0
   MessageBox MB_ICONSTOP "程序权限检查失败。未创建启动快捷方式，请保留现场并请求修复。"
   SetErrorLevel 3
   Abort
 ${EndIf}
 DetailPrint "正在执行完整文件校验并生成受保护安装回执，请稍候…"
 ; Windowed asInvoker helper inherits this installer's admin token only here.
 ExecWait '"$INSTDIR\App\MentorFlowInstallGate.exe" activate --quiet-install --install-root "$INSTDIR" --manifest-sha256 ${MANIFEST_SHA} --source-sha ${SOURCE_SHA} --tree-sha ${SOURCE_TREE}' $0
 ${If} $0 != 0
   MessageBox MB_ICONSTOP "完整安装校验未通过。未创建启动快捷方式，请保留现场并请求修复。"
   SetErrorLevel 3
   Abort
 ${EndIf}
 IfFileExists "$INSTDIR\installation-receipt.json" +4
   MessageBox MB_ICONSTOP "安装回执缺失，停止发布快捷方式。"
   SetErrorLevel 3
   Abort
 ClearErrors
 ; This is the explicitly requested user's desktop, not an alternate UAC account.
 CreateShortCut "C:\Users\21533\Desktop\MentorFlow.lnk" "$INSTDIR\App\MentorFlow.exe" '--install-root "$INSTDIR" --manifest-sha256 ${MANIFEST_SHA} --source-sha ${SOURCE_SHA} --tree-sha ${SOURCE_TREE}' "$INSTDIR\App\MentorFlow.exe" 0 SW_SHOWNORMAL
 ${If} ${Errors}
   MessageBox MB_ICONSTOP "程序安装已完成，但桌面快捷方式创建失败。请保留安装目录并请求修复。"
   SetErrorLevel 3
   Abort
 ${EndIf}
 ; No elevated finish-page app launch, user-data deletion or legacy uninstaller.
 DetailPrint "安装完成。关闭安装器后从桌面正常启动 MentorFlow。"
SectionEnd
