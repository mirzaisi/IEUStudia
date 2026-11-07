{ How a new version takes the place of the old one: Pascal code included inside the Code section of IEUStudia.iss
  (a test installer includes it too). It needs the custom message NotSwapped.

  The Files section writes the new version beside the old one: _internal.new and IEUStudia.exe.new in the app's
  folder. Only once all of it is there (ssPostInstall) do the two swap places, in four renames; the old copy is then
  deleted. An install that is cancelled, fails (a full disk, an antivirus taking a file) or is cut off (an antivirus
  ending it, a power cut) leaves the old copy as it was, and IEUStudia still starts. (Before, InstallDelete removed
  the old _internal first, and an install that didn't finish left a copy that could not start at all.) An update
  started from the app (/RELAUNCH=1) opens IEUStudia again either way: the new version, or the old one, which then
  says the update didn't finish. }

var
  Swapped: Boolean;        { the new version took the old one's place }

function Relaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function RelaunchNew(): Boolean;
begin
  Result := Relaunch() and Swapped;
end;

{ Restart Manager closes whatever runs from the old copy (the window, the helper next to the clock) before the install:
  the new files are written beside its files, so they have to be named here to be checked. }
procedure RegisterExtraCloseApplicationsResources;
begin
  RegisterExtraCloseApplicationsResource(False, ExpandConstant('{app}\IEUStudia.exe'));
end;

{ End only the programs that run from this folder (Why: what the log says first). }
procedure EndProgramsRunningHere(const Why: String);
var
  app: String;
  code: Integer;
begin
  app := ExpandConstant('{app}');
  StringChangeEx(app, '''', '''''', True);
  Log(Why + ': ending what runs from ' + ExpandConstant('{app}'));
  { the native (64-bit) PowerShell also when the installer runs as 32-bit: a 32-bit one can't read a 64-bit program's
    path, and so ended nothing }
  Exec(ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-Process -ErrorAction SilentlyContinue | ' +
    'Where-Object { $_.Path -and $_.Path.StartsWith(''' + app + '\'', [StringComparison]::OrdinalIgnoreCase) } | ' +
    'Stop-Process -Force -PassThru -ErrorAction SilentlyContinue | Wait-Process -Timeout 10 -ErrorAction ' +
    'SilentlyContinue"', '', SW_HIDE, ewWaitUntilTerminated, code);
  { (ended for good before the renames: a program still closing holds its files, and _internal.old stayed behind) }
end;

{ RenameFile, tried again for up to 10 s: an antivirus scanning a file just written lets go of it a moment later }
function RenameRetry(const OldName, NewName: String): Boolean;
var
  i: Integer;
begin
  Result := True;
  for i := 1 to 20 do
  begin
    if RenameFile(OldName, NewName) then
      Exit;
    Sleep(500);
  end;
  Log('Could not rename ' + OldName + ' to ' + NewName);
  Result := False;
end;

{ What an install that was cut off left (it never runs: the copy in use is always _internal and IEUStudia.exe). A swap
  cut off halfway, the old copy set aside and not yet replaced, is put back first. }
procedure TidyLeftovers();
var
  app: String;
begin
  app := ExpandConstant('{app}');
  { cut off between the swap's last two renames (the new _internal in place, its IEUStudia.exe not yet): finished }
  if DirExists(app + '\_internal') and not DirExists(app + '\_internal.new') and
     not FileExists(app + '\IEUStudia.exe') and FileExists(app + '\IEUStudia.exe.new') then
    RenameRetry(app + '\IEUStudia.exe.new', app + '\IEUStudia.exe');
  if DirExists(app + '\_internal.old') then
    if DirExists(app + '\_internal') then
      DelTree(app + '\_internal.old', True, True, True)
    else
      RenameRetry(app + '\_internal.old', app + '\_internal');
  if FileExists(app + '\IEUStudia.exe.old') then
    if FileExists(app + '\IEUStudia.exe') then
      DeleteFile(app + '\IEUStudia.exe.old')
    else
      RenameRetry(app + '\IEUStudia.exe.old', app + '\IEUStudia.exe');
  DelTree(app + '\_internal.new', True, True, True);
  DeleteFile(app + '\IEUStudia.exe.new');
end;

{ The new version in place of the old one; False (and the old one as it was) when that can't be done. }
function SwapIn(): Boolean;
var
  app: String;
  i: Integer;
  hadOld, hadExe: Boolean;
begin
  Result := False;
  app := ExpandConstant('{app}');
  if not (DirExists(app + '\_internal.new') and FileExists(app + '\IEUStudia.exe.new')) then
  begin
    Log('The new version is not all there: nothing is swapped');
    Exit;
  end;
  hadOld := DirExists(app + '\_internal');
  if hadOld then
  begin
    { what runs from the old copy now was started during the install (a click on the icon while the files were
      written; Restart Manager closed the rest before): renaming doesn't stop it, and it would go on as the old version
      while the new one's [Run] only brings that window to the front }
    EndProgramsRunningHere('Before the swap');
    { a program that just closed lets go of its files a moment later }
    for i := 1 to 20 do
    begin
      if RenameFile(app + '\_internal', app + '\_internal.old') then
        Break;
      if i = 10 then
        EndProgramsRunningHere('The old copy is still in use');
      Sleep(500);
    end;
    if DirExists(app + '\_internal') then
    begin
      Log('The old copy stays: its files are in use');
      TidyLeftovers();
      Exit;
    end;
  end;
  hadExe := FileExists(app + '\IEUStudia.exe');
  if hadExe and not RenameRetry(app + '\IEUStudia.exe', app + '\IEUStudia.exe.old') then
  begin
    Log('The old copy stays: IEUStudia.exe can''t be set aside');
    TidyLeftovers();                     { (puts _internal back) }
    Exit;
  end;
  if not RenameRetry(app + '\_internal.new', app + '\_internal') then
  begin
    Log('The new version could not be moved into place: the old copy is put back');
    TidyLeftovers();
    Exit;
  end;
  if not RenameRetry(app + '\IEUStudia.exe.new', app + '\IEUStudia.exe') then
  begin
    Log('The new IEUStudia.exe could not be moved into place: the old copy is put back');
    { never the old IEUStudia.exe with the new _internal: the new one goes, whatever it takes }
    if not RenameRetry(app + '\_internal', app + '\_internal.new') then
      DelTree(app + '\_internal', True, True, True);
    TidyLeftovers();
    Exit;
  end;
  DelTree(app + '\_internal.old', True, True, True);
  DeleteFile(app + '\IEUStudia.exe.old');
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    TidyLeftovers()
  else if CurStep = ssPostInstall then
  begin
    Swapped := SwapIn();
    if Swapped then
      Log('The new version is in place')
    else if not WizardSilent() then
      MsgBox(CustomMessage('NotSwapped'), mbError, MB_OK);
  end;
end;

{ An update from the app that didn't get through (cancelled, failed, or the old copy kept): IEUStudia opens again all
  the same, and says the update didn't finish ([Run] opens the new version when it did). }
procedure DeinitializeSetup();
var
  code: Integer;
begin
  if Relaunch() and not Swapped then
    try
      if FileExists(ExpandConstant('{app}\IEUStudia.exe')) then
      begin
        Log('The update did not get through: opening the old copy of IEUStudia again');
        Exec(ExpandConstant('{app}\IEUStudia.exe'), '', '', SW_SHOWNORMAL, ewNoWait, code);
      end;
    except
      Log('IEUStudia could not be opened again');
    end;
end;
