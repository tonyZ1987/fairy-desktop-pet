' Fairy v2 desktop pet - launcher.  Double-click to start.
' NOTE: keep this file pure ASCII - wscript decodes .vbs as ANSI.
Option Explicit

Dim sh, fso, base, pyw, script, beat, ok, i
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
