' Fairy v2 - health check. Double-click and a window pops up with the diagnosis.
' NOTE: keep this file pure ASCII - wscript decodes .vbs as ANSI.
Option Explicit

Dim sh, fso, base, py, script, rep, f, txt
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
py = FindPy("python.exe")
If py = "" Then
    MsgBox "Python not found." & vbCrLf & vbCrLf & _
           "Create a file named python_path.txt next to this script and put" & vbCrLf & _
           "the full path of python.exe (or its folder) in it, then try again.", 16, "Fairy"
    WScript.Quit 1
End If
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

' === Fairy launcher probe (managed by pet-v2/_work/fix_launchers.py) ===
' Locate python.exe / pythonw.exe without hard-coding a machine path.
' Order: %FAIRY_PYTHON% -> python_path.txt beside this script -> WorkBuddy managed venv
'        -> other managed python -> %PATH% -> common install folders.
' python_path.txt may hold a FOLDER or a full path to python.exe - both work.
'
Function PyFromPath(sFrom, sWant)
    Dim sD, sN
    PyFromPath = ""
    If sFrom = "" Then Exit Function
    sD = sFrom
    If Right(sD, 1) = "\" Then sD = Left(sD, Len(sD) - 1)
    If fso.FolderExists(sD) Then
        If fso.FileExists(sD & "\" & sWant) Then PyFromPath = sD & "\" & sWant
        Exit Function
    End If
    sN = fso.GetParentFolderName(sD)
    If sN <> "" Then
        If fso.FileExists(sN & "\" & sWant) Then PyFromPath = sN & "\" & sWant
    End If
End Function

Function FindPyIn(aRoots, sWant, bScriptsOnly)
    Dim sRoot, oSub, sHit
    FindPyIn = ""
    For Each sRoot In aRoots
        If fso.FolderExists(sRoot) Then
            For Each oSub In fso.GetFolder(sRoot).SubFolders
                If bScriptsOnly Then
                    sHit = oSub.Path & "\Scripts\" & sWant
                    If fso.FileExists(sHit) Then
                        FindPyIn = sHit
                        Exit Function
                    End If
                Else
                    sHit = oSub.Path & "\" & sWant
                    If fso.FileExists(sHit) Then
                        FindPyIn = sHit
                        Exit Function
                    End If
                    sHit = oSub.Path & "\Scripts\" & sWant
                    If fso.FileExists(sHit) Then
                        FindPyIn = sHit
                        Exit Function
                    End If
                End If
            Next
        End If
    Next
End Function

Function FindPy(sWant)
    Dim sEnv, sCand, sTxt, oTS, aParts, aRoots, sDir, iPy
    FindPy = ""
    sEnv = sh.ExpandEnvironmentStrings("%FAIRY_PYTHON%")
    If sEnv <> "%FAIRY_PYTHON%" Then
        sCand = PyFromPath(sEnv, sWant)
        If sCand <> "" Then
            FindPy = sCand
            Exit Function
        End If
    End If
    sTxt = base & "\python_path.txt"
    If Not fso.FileExists(sTxt) Then sTxt = fso.GetParentFolderName(base) & "\python_path.txt"
    If fso.FileExists(sTxt) Then
        Set oTS = fso.OpenTextFile(sTxt, 1)
        If Not oTS.AtEndOfStream Then sCand = Trim(oTS.ReadLine)
        oTS.Close
        sCand = PyFromPath(sCand, sWant)
        If sCand <> "" Then
            FindPy = sCand
            Exit Function
        End If
    End If
    aRoots = Array( _
        sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.workbuddy\binaries\python\envs", _
        sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.workbuddy\binaries\python\versions")
    sCand = FindPyIn(aRoots, sWant, True)
    If sCand = "" Then sCand = FindPyIn(aRoots, sWant, False)
    If sCand <> "" Then
        FindPy = sCand
        Exit Function
    End If
    aParts = Split(sh.ExpandEnvironmentStrings("%PATH%"), ";")
    For iPy = 0 To UBound(aParts)
        sDir = Trim(aParts(iPy))
        If sDir <> "" Then
            If Right(sDir, 1) = "\" Then sDir = Left(sDir, Len(sDir) - 1)
            If fso.FileExists(sDir & "\" & sWant) Then
                FindPy = sDir & "\" & sWant
                Exit Function
            End If
        End If
    Next
    aRoots = Array( _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python", _
        sh.ExpandEnvironmentStrings("%ProgramFiles%"), _
        sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%"))
    sCand = FindPyIn(aRoots, sWant, False)
    If sCand <> "" Then FindPy = sCand
End Function
