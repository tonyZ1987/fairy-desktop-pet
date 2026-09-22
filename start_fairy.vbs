' Fairy v2 desktop pet - launcher.  Double-click to start.
' NOTE: keep this file pure ASCII - wscript decodes .vbs as ANSI.
Option Explicit

Dim sh, fso, base, pyw, script, beat, ok, i
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = FindPy("pythonw.exe")
script = base & "\fairy_pet.py"
beat = base & "\heartbeat.txt"

If Not fso.FileExists(pyw) Then
    MsgBox "pythonw.exe not found:" & vbCrLf & pyw, 16, "Fairy"
    WScript.Quit 1
End If
If Not fso.FileExists(script) Then
    MsgBox "fairy_pet.py not found:" & vbCrLf & script, 16, "Fairy"
    WScript.Quit 1
End If

sh.CurrentDirectory = base

' Run style 1 (normal). Style 0 would set STARTUPINFO.wShowWindow = SW_HIDE; that was
' tested and does NOT actually hide the pet window, but there is no reason to rely on it.
' pythonw.exe has no console, so nothing flashes either way.
sh.Run """" & pyw & """ """ & script & """", 1, False

' Wait for a fresh heartbeat (a new copy writes it; an already-running copy keeps
' refreshing it after being asked to come to front).
ok = False
For i = 1 To 24
    WScript.Sleep 500
    If fso.FileExists(beat) Then
        If DateDiff("s", fso.GetFile(beat).DateLastModified, Now()) <= 3 Then
            ok = True
            Exit For
        End If
    End If
Next

If Not ok Then
    MsgBox "Fairy did not come up within 12 seconds." & vbCrLf & vbCrLf & _
           "1) Double-click  check_fairy.vbs  for a full diagnosis." & vbCrLf & _
           "2) Or open  pet_error.log  in this folder." & vbCrLf & vbCrLf & _
           "(If a copy was already running, this launcher only asks it to come to" & vbCrLf & _
           "front instead of starting a second one.)", 48, "Fairy"
End If

' ---------------------------------------------------------------------------
' FindPy: locate python.exe / pythonw.exe WITHOUT hard-coding any path.
'
' Order:  %FAIRY_PYTHON%  ->  python_path.txt next to this script  ->  %PATH%
'         ->  the standard install locations.
'
' Kept in a Function (rather than inline) on purpose: every variable here is
' function-local, so it cannot clash with the caller's names under
' "Option Explicit" (inline Dim of an already-declared name is a hard error).
'
' If your Python is in a non-standard place (e.g. a portable build), either
' set the environment variable FAIRY_PYTHON to the full path of pythonw.exe,
' or drop a one-line python_path.txt next to this script containing the path.
' ---------------------------------------------------------------------------
Function FindPy(exeName)
    Dim fso, sh, c, r, roots, s, here, tf, ts
    Set fso = CreateObject("Scripting.FileSystemObject")
    Set sh = CreateObject("WScript.Shell")

    ' 1) explicit override via environment variable
    c = sh.ExpandEnvironmentStrings("%FAIRY_PYTHON%")
    If c <> "%FAIRY_PYTHON%" And fso.FileExists(c) Then
        FindPy = c
        Exit Function
    End If

    ' 2) a one-line python_path.txt sitting next to this script
    here = fso.GetParentFolderName(WScript.ScriptFullName)
    tf = here & "\python_path.txt"
    If fso.FileExists(tf) Then
        Set ts = fso.OpenTextFile(tf, 1)
        c = Trim(ts.ReadLine)
        ts.Close
        If Len(c) > 0 And fso.FileExists(c) Then
            FindPy = c
            Exit Function
        End If
    End If

    ' 3) anywhere on PATH
    For Each c In Split(sh.ExpandEnvironmentStrings("%PATH%"), ";")
        c = Trim(c)
        If Len(c) > 1 Then
            If Right(c, 1) = "\" Then c = Left(c, Len(c) - 1)
            If fso.FileExists(c & "\" & exeName) Then
                FindPy = c & "\" & exeName
                Exit Function
            End If
        End If
    Next

    ' 4) the usual install locations (only dirs whose name contains "Python")
    roots = Array(sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python"), _
                  sh.ExpandEnvironmentStrings("%ProgramFiles%"), _
                  sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%"))
    For Each r In roots
        If fso.FolderExists(r) Then
            For Each s In fso.GetFolder(r).SubFolders
                If InStr(1, s.Name, "Python", 1) > 0 Then
                    If fso.FileExists(s.Path & "\" & exeName) Then
                        FindPy = s.Path & "\" & exeName
                        Exit Function
                    End If
                End If
            Next
        End If
    Next

    FindPy = ""
End Function
