' Fairy v2 - clean restart: stop any running copy first, then start a fresh one.
' Use this when you want to be sure the latest code is running.
' NOTE: keep this file pure ASCII - wscript decodes .vbs as ANSI.
Option Explicit

Dim sh, fso, base, pyw, script, beat, stopf, ok, i, running
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = FindPy("pythonw.exe")
script = base & "\fairy_pet.py"
beat = base & "\heartbeat.txt"
stopf = base & "\stop.txt"

sh.CurrentDirectory = base

' A fresh heartbeat means a copy is really running. If nothing is running we skip the
' whole stop dance - otherwise the launcher would sit there doing nothing for seconds,
' which feels exactly like "double-clicked and nothing happened".
running = False
If fso.FileExists(beat) Then
    If DateDiff("s", fso.GetFile(beat).DateLastModified, Now()) <= 3 Then running = True
End If

If running Then
    ' The running copy checks for stop.txt every 30 frames and deletes it on the way out.
    If fso.FileExists(stopf) Then fso.DeleteFile stopf
    fso.CreateTextFile(stopf, True).Close
    For i = 1 To 20
        WScript.Sleep 250
        If Not fso.FileExists(stopf) Then Exit For
    Next
End If

' Never leave a stale stop flag behind: the copy we are about to start would exit at once.
If fso.FileExists(stopf) Then fso.DeleteFile stopf

WScript.Sleep 200
sh.Run """" & pyw & """ """ & script & """", 1, False

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
           "2) Or open  pet_error.log  in this folder.", 48, "Fairy"
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
