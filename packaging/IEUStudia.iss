; Inno Setup script for the IEUStudia installer (per-user, no admin rights).
;   1. build the app:  uv run pyinstaller packaging\IEUStudia.spec --noconfirm --distpath build\dist --workpath build\work
;   2. build this:     ISCC.exe /DAppVersion=2.1.0 packaging\IEUStudia.iss
; or run scripts\build_release.ps1, which does both. The installer lands in build\installer\.

#define AppName "IEUStudia"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef RepoUrl
  #define RepoUrl "https://github.com/mirzaisi/IEUStudia"
#endif

[Setup]
AppId={{8E1B7D57-5C2A-4F07-9E33-3C9A1D2B7F11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoCompany=IEUStudia contributors
AppPublisher=IEUStudia contributors
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}
DefaultDirName={localappdata}\Programs\IEUStudia
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\build\installer
OutputBaseFilename=IEUStudia-Setup-{#AppVersion}
SetupIconFile=..\src\studia\assets\icon.ico
UninstallDisplayIcon={app}\IEUStudia.exe
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; IEUStudia's window: Setup asks you to close it yourself (its single-instance mutex), so a transcription that is
; running is stopped only if you agree. The helper in the tray is asked to quit (PrepareToInstall); whatever still
; holds the app's files after that is closed.
AppMutex=Local\Studia-SingleInstance
CloseApplications=force
RestartApplications=no
LicenseFile=..\LICENSE
; Turkish on a Turkish Windows, English otherwise, without asking
ShowLanguageDialog=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"

[CustomMessages]
en.NeedWebView2=IEUStudia needs the Microsoft Edge WebView2 Runtime, which was not found on this PC.%n%nIt is free and comes with Windows 11 and current Windows 10. Open the download page now? (You can continue the installation either way.)
tr.NeedWebView2=IEUStudia için Microsoft Edge WebView2 Runtime gerekiyor, bu bilgisayarda bulunamadı.%n%nÜcretsiz ve Windows 11 ile güncel Windows 10'da zaten var. İndirme sayfası şimdi açılsın mı? (Kuruluma her iki durumda da devam edebilirsin.)
en.NotSwapped=The new version of IEUStudia could not take the old one's place, because its files are still in use. The old version stays and still works.%n%nClose IEUStudia (also its icon next to the clock), then run this installer again.
tr.NotSwapped=IEUStudia'nın yeni sürümü eskisinin yerine geçemedi, çünkü eski sürümün dosyaları hâlâ kullanımda. Eski sürüm duruyor ve çalışıyor.%n%nIEUStudia'yı kapat (saatin yanındaki simgesini de), sonra bu kurulum programını yeniden çalıştır.
en.DeleteData=Also delete IEUStudia's data on this PC?%n%nThis removes your settings, the stored (encrypted) IEULearn login, the library and the downloaded models in:%1%n%nYour transcripts and notes in your Documents folder are kept.
tr.DeleteData=IEUStudia'nın bu bilgisayardaki verileri de silinsin mi?%n%nAyarların, saklanan (şifreli) IEULearn girişin, kitaplığın ve indirilen modeller şu klasörlerden silinir:%1%n%nBelgeler klasöründeki ders metinlerin ve notların kalır.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; beside the old copy, which they replace only once they are all there (see update-swap.iss): an install that doesn't
; finish leaves the old copy as it was, and IEUStudia still starts. Nothing of the old version is left over either.
Source: "..\build\dist\IEUStudia\IEUStudia.exe"; DestDir: "{app}"; DestName: "IEUStudia.exe.new"; Flags: ignoreversion
Source: "..\build\dist\IEUStudia\_internal\*"; DestDir: "{app}\_internal.new"; Flags: ignoreversion recursesubdirs createallsubdirs

[UninstallDelete]
; installed under the names above, in use under these
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\_internal.new"
Type: filesandordirs; Name: "{app}\_internal.old"
Type: files; Name: "{app}\IEUStudia.exe"
Type: files; Name: "{app}\IEUStudia.exe.new"
Type: files; Name: "{app}\IEUStudia.exe.old"

[Icons]
Name: "{autoprograms}\IEUStudia"; Filename: "{app}\IEUStudia.exe"; AppUserModelID: "Studia.App"
Name: "{autodesktop}\IEUStudia"; Filename: "{app}\IEUStudia.exe"; Tasks: desktopicon; AppUserModelID: "Studia.App"

[Run]
Filename: "{app}\IEUStudia.exe"; Description: "{cm:LaunchProgram,IEUStudia}"; Flags: nowait postinstall skipifsilent
; an update started from the app (/SILENT /RELAUNCH=1, see updates.py): open the new IEUStudia when it's in place (an
; update that didn't get through opens the old one again, from DeinitializeSetup)
Filename: "{app}\IEUStudia.exe"; Flags: nowait; Check: RelaunchNew

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /f /im IEUStudia.exe"; Flags: runhidden; RunOnceId: "StopIEUStudia"

[Code]
#include "update-swap.iss"

function WebView2Installed(): Boolean;
var
  v: String;
begin
  Result := RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0');
  if not Result then
    Result := RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0');
end;

function InitializeSetup(): Boolean;
var
  code: Integer;
begin
  Result := True;
  if not WebView2Installed() then
    if MsgBox(CustomMessage('NeedWebView2'), mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/#download', '', '', SW_SHOWNORMAL, ewNoWait, code);
end;

{ Before files are replaced: the background helper (the tray icon) keeps IEUStudia's files open, and Windows'
  Restart Manager cannot always close it. Ask it to quit, as its menu's Quit does; it removes its icon. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  wnd: HWND;
  i: Integer;
  asked: Boolean;
begin
  Result := '';
  asked := False;
  for i := 1 to 20 do
  begin
    wnd := FindWindowByClassName('IEUStudiaTray');
    if wnd = 0 then
      Break;
    PostMessage(wnd, 16, 0, 0);      { WM_CLOSE }
    asked := True;
    Sleep(250);
  end;
  if asked then
    Sleep(1000);                     { let it finish what it was doing and exit }
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  data: array[0..2] of String;
  names: String;
  i: Integer;
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'IEUStudia');
  if CurUninstallStep = usPostUninstall then
  begin
    { the data folder, and one under the app's earlier names that was never moved (it has a library) }
    data[0] := ExpandConstant('{localappdata}\IEUStudia');
    data[1] := ExpandConstant('{localappdata}\Studia');
    data[2] := ExpandConstant('{localappdata}\LectureScribe');
    names := '';
    for i := 0 to 2 do
      if DirExists(data[i]) and ((i = 0) or FileExists(data[i] + '\library.db')) then
        names := names + #13#10 + data[i]
      else
        data[i] := '';
    if (names <> '') and not UninstallSilent() then
      if MsgBox(FmtMessage(CustomMessage('DeleteData'), [names]), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        for i := 0 to 2 do
          if data[i] <> '' then
            DelTree(data[i], True, True, True);
  end;
end;
