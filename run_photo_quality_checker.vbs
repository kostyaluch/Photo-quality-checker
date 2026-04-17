Option Explicit

Dim shell, fso, scriptDir, command, pythonwCheckExitCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwCheckExitCode = shell.Run("cmd /c where pythonw >nul 2>&1", 0, True)

If pythonwCheckExitCode <> 0 Then
    MsgBox "Не знайдено pythonw.exe у PATH. Запустіть через pythonw або встановіть Python Launcher.", vbExclamation, "Photo Quality Checker"
    WScript.Quit 1
End If

command = "pythonw """ & scriptDir & "\main_app.py"""
shell.Run command, 0, False
