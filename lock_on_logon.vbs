' lock_on_logon.vbs — НЕМЕДЛЕННАЯ блокировка экрана сразу после входа (в т.ч. автологона).
'
' Почему скрипт, а не голый `rundll32.exe user32.dll,LockWorkStation` прямо в задаче:
' LockWorkStation срабатывает ТОЛЬКО когда интерактивный рабочий стол уже поднят.
' Вызов, пришедший слишком рано, возвращает FALSE и не делает НИЧЕГО — рабочий стол
' остаётся открытым, и нигде это не видно: задача отчитается кодом 0. Поэтому здесь
' цикл с проверкой ФАКТА, а не одиночный выстрел вслепую.
'
' Факт «заперто» = живой процесс LogonUI.exe: он держится на защищённом рабочем столе,
' пока станция заблокирована. Как только факт подтверждён — выходим НЕМЕДЛЕННО, чтобы
' не запереть человека повторно, если он только что разблокировал экран руками.
'
' Запускается задачей Планировщика \tb_lock_on_logon по LogonTrigger (задержка 5с).
' Кириллица здесь живёт только в комментариях (как в rc_remote_control.vbs): код —
' чистый ASCII, поэтому кодировка файла на исполнение не влияет.

Option Explicit

Dim sh, wmi, i

Set sh = CreateObject("WScript.Shell")

On Error Resume Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
On Error Goto 0

For i = 1 To 6                          ' до ~30 секунд попыток
  If Locked(wmi) Then WScript.Quit 0    ' уже заперто — уходим молча
  sh.Run "rundll32.exe user32.dll,LockWorkStation", 0, False
  WScript.Sleep 1500
  If Locked(wmi) Then WScript.Quit 0
  WScript.Sleep 3500
Next

WScript.Quit 0

' Заперта ли станция. Если WMI недоступен (ранний старт) — возвращаем False:
' тогда цикл просто отработает все шесть попыток вслепую, что безопаснее молчания.
Function Locked(w)
  Dim n
  Locked = False
  If IsEmpty(w) Then Exit Function
  If w Is Nothing Then Exit Function
  On Error Resume Next
  n = w.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='LogonUI.exe'").Count
  If Err.Number = 0 Then Locked = (n > 0)
  Err.Clear
  On Error Goto 0
End Function
