; The Windows installer of Tracen Replay: the built folder (desktop/
; build-windows.ps1) into Program Files, a Start-menu entry, an uninstaller.
; Built with makensis /DVERSION=... /DBUNDLE=<folder> /DOUT=<setup.exe>.
Unicode true
!include "MUI2.nsh"

Name "Tracen Replay"
OutFile "${OUT}"
InstallDir "$PROGRAMFILES64\Tracen Replay"
InstallDirRegKey HKLM "Software\Tracen Replay" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define MUI_ICON "${__FILEDIR__}\icon\icon.ico"
!define MUI_UNICON "${__FILEDIR__}\icon\icon.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\Tracen Replay.exe"
!insertmacro MUI_PAGE_LICENSE "${BUNDLE}\LICENSE.md"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Tracen Replay" SecMain
  SetOutPath "$INSTDIR"
  File /r "${BUNDLE}\*"
  WriteRegStr HKLM "Software\Tracen Replay" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\TracenReplay" "DisplayName" "Tracen Replay"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\TracenReplay" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\TracenReplay" "Publisher" "Andy Dam"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\TracenReplay" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\TracenReplay" "NoModify" 1
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\Tracen Replay"
  CreateShortcut "$SMPROGRAMS\Tracen Replay\Tracen Replay.lnk" "$INSTDIR\Tracen Replay.exe"
  CreateShortcut "$SMPROGRAMS\Tracen Replay\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
  ; The program goes; what the person made (%APPDATA%\TracenReplay) stays.
  RMDir /r "$INSTDIR"
  RMDir /r "$SMPROGRAMS\Tracen Replay"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\TracenReplay"
  DeleteRegKey HKLM "Software\Tracen Replay"
SectionEnd
