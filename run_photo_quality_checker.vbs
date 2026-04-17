Option Explicit

Dim shell, fso, scriptDir, pythonwPath, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwPath = """" & Replace(shell.ExpandEnvironmentStrings("%LOCALAPPDATA%"), """", """""") & "\Programs\Python\Python312\pythonw.exe"""

If Not fso.FileExists(Replace(pythonwPath, """", "")) Then
    pythonwPath = "pythonw"
End If

command = pythonwPath & " """ & scriptDir & "\main_app.py"""
shell.Run command, 0, False
