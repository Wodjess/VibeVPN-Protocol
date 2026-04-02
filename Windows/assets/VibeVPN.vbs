Set objShell = CreateObject("Shell.Application")
objShell.ShellExecute "VibeVPN.exe", "", CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName), "runas", 1
