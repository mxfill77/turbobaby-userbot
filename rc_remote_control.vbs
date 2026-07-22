' rc_remote_control.vbs — СКРЫТЫЙ запуск супервизора Remote Control (rc_supervisor.py).
'
' WshShell.Run(cmd, 0, True): процесс получает СВОЮ консоль (интерактивной сессии
' `claude --remote-control` нужен живой TTY), но окно не показывается — на ПК владельца
' ничего не мигает. Именно поэтому здесь НЕ pythonw.exe и НЕ CREATE_NO_WINDOW: они убирают
' саму консоль, а вместе с ней TTY, без которого интерактивная сессия не поднимется.
'
' Задача Планировщика pc_remote_control зовёт:
'   wscript.exe //B //Nologo "D:\turbobaby-bot\rc_remote_control.vbs"
Option Explicit
Dim sh, cmd
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\turbobaby-bot"
cmd = """D:\turbobaby-bot\venv\Scripts\python.exe"" ""D:\turbobaby-bot\rc_supervisor.py"""
sh.Run cmd, 0, True
