' ============================================================
'  Fairy desktop pet -- remove the "AI bridge"
'
'  Undoes install_agent_bridge.vbs: takes OUR three hooks back
'  out of settings.json (it only removes entries whose command
'  points at fairy_hook.py, so hooks you added yourself stay).
'  A timestamped backup is written next to settings.json.
'
'  NOTE: settings.json is NOT re-read in a running session,
'  so WorkBuddy must be restarted for this to take effect.
'
'  This script is intentionally pure ASCII (CRLF, no BOM) --
'  see pet-v2/_work/check_ascii.py. Chinese text shown to the
'  user comes from agent_bridge_summary.txt (UTF-16).
' ============================================================
Option Explicit

Dim fso, sh, base, pyw, script, rep, sum, txt, oTS, rc

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)

script = base & "\agent_bridge_install.py"
If Not fso.FileExists(script) Then
    MsgBox "agent_bridge_install.py was not found next to this script:" & vbCrLf & _
           script & vbCrLf & vbCrLf & _
           "Keep this .vbs in the same folder as the pet files.", _
           16, "Fairy"
    WScript.Quit 1
End If

pyw = FindPy("pythonw.exe")
If pyw = "" Then
    MsgBox "Python not found." & vbCrLf & vbCrLf & _
           "Create a file named python_path.txt next to this script and put" & vbCrLf & _
           "the full path of python.exe (or its folder) in it, then try again.", _
           16, "Fairy"
    WScript.Quit 1
End If

rep = base & "\agent_bridge_report.txt"
sum = base & "\agent_bridge_summary.txt"
If fso.FileExists(rep) Then fso.DeleteFile rep
If fso.FileExists(sum) Then fso.DeleteFile sum

rc = sh.Run("""" & pyw & """ """ & script & """ --uninstall", 0, True)

txt = ""
If fso.FileExists(sum) Then
    Set oTS = fso.OpenTextFile(sum, 1, False, -1)
    txt = oTS.ReadAll
    oTS.Close
End If
If Len(Trim(txt)) = 0 Then
    txt = "No summary was produced (exit code " & rc & ")." & vbCrLf & vbCrLf & _
          "See agent_bridge_report.txt in this folder."
End If

MsgBox txt, 64, "Fairy - AI bridge"

' ===== Fairy launcher probe (same as start_fairy.vbs) =====
' Locate python.exe / pythonw.exe without hard-coding a machine path.
' Order: %FAIRY_PYTHON% -> python_path.txt beside this script -> WorkBuddy managed venv
'        -> other managed python -> %PATH% -> common install folders.
' python_path.txt may hold a FOLDER or a full path to python.exe - both work.
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
