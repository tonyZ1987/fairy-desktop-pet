' Fairy v2 - health check. Double-click and a window pops up with the diagnosis.
' NOTE: keep this file pure ASCII - wscript decodes .vbs as ANSI.
Option Explicit

Dim sh, fso, base, py, script, rep, f, txt
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
py = FindPy("python.exe")
script = base & "\fairy_status.py"
rep = base & "\fairy_status.txt"

If Not fso.FileExists(py) Then
    MsgBox "python.exe not found:" & vbCrLf & py, 16, "Fairy"
    WScript.Quit 1
End If
If Not fso.FileExists(script) Then
    MsgBox "fairy_status.py not found:" & vbCrLf & script, 16, "Fairy"
    WScript.Quit 1
End If

' Drop the old report so we can never show a stale one.
If fso.FileExists(rep) Then fso.DeleteFile rep

sh.CurrentDirectory = base
' 0 = hidden console, True = wait for it to finish.
sh.Run """" & py & """ """ & script & """", 0, True

If fso.FileExists(rep) Then
    ' -1 = TristateTrue -> read as Unicode (the python side writes UTF-16).
    Set f = fso.OpenTextFile(rep, 1, False, -1)
    txt = f.ReadAll
    f.Close
Else
    txt = "The status script did not produce a report." & vbCrLf & _
          "See pet_error.log in this folder."
End If

MsgBox txt, 64, "Fairy status"

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
