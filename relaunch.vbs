' Capybara Monitor - delayed relaunch after install
' Why delay: AV software (e.g. 360) scans the freshly written exe and
' may lock it briefly; starting immediately can make PyInstaller fail
' with "Failed to load Python DLL" (interrupted extraction).
' Wait 2.5s, then retry up to 3 times while the exe is still locked.
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
exe = appDir & "\DeepSeekTokenMonitor.exe"
WScript.Sleep 2500
Set shell = CreateObject("WScript.Shell")
For i = 1 To 3
    On Error Resume Next
    Set f = fso.OpenTextFile(exe, 1, False)
    If Err.Number = 0 Then
        f.Close
        On Error GoTo 0
        Exit For
    End If
    On Error GoTo 0
    WScript.Sleep 1000
Next
shell.CurrentDirectory = appDir
shell.Run """" & exe & """", 1, False
