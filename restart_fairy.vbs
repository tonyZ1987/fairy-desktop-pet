' Fairy v2 - clean restart: stop any running copy first, then start a fresh one.
' Use this when you want to be sure the latest code is running.
' NOTE: keep this file pure ASCII - wscript decodes .vbs as ANSI.
Option Explicit

Dim sh, fso, base, pyw, script, beat, stopf, ok, i, running
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = FindPy("pythonw.exe")
If pyw = "" Then
    MsgBox "Python not found." & vbCrLf & vbCrLf & _
           "Create a file named python_path.txt next to this script and put" & vbCrLf & _
           "the full path of python.exe (or its folder) in it, then try again.", 16, "Fairy"
    WScript.Quit 1
End If
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
