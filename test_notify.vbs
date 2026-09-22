' Doublick me: send a TEST notification to the fairy desktop pet.
' (Chinese text lives inside fairy_notify.py -- this file stays pure ASCII on purpose,
'  because a .vbs written in another encoding would garble non-ASCII string literals.)
Option Explicit

Dim fso, sh, base, py, args
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
py = FindPy("pythonw.exe")
If Not fso.FileExists(py) Then
If py = "" Then py = FindPy("python.exe")
End If
If Not fso.FileExists(py) Then
  MsgBox "python not found:" & vbCrLf & py, 16, "Fairy"
  WScript.Quit 1
End If

args = """" & py & """ """ & base & "\fairy_notify.py"" --test"
sh.Run args, 0, False

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
