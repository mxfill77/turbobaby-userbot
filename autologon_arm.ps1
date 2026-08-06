<#
    autologon_arm.ps1 — вооружение автологона Windows для учётки mxfill1.

    ЗАЧЕМ. До входа в Windows на этом ПК не стартует НИ ОДИН процесс контура
    (все четыре задачи Планировщика — LogonType = InteractiveToken, userbot и
    moderation_bot вообще дети pc_agent/оркестратора). Поэтому любая авария
    питания = простой до прихода человека: 7 аварийных обрывов за 3 месяца,
    последний 06.08.2026 в 00:05:33 — 19 ч 32 м простоя.
    Разбор: docs/artifacts/2026-08-06-pc-autostart-without-human.md

    ЧТО ДЕЛАЕТ. Ровно то же, что Sysinternals Autologon.exe, но без скачивания:
      1. кладёт пароль в LSA Secrets (LsaStorePrivateData "DefaultPassword") —
         это ШИФРОВАННОЕ хранилище LSA, а НЕ реестр;
      2. ставит AutoAdminLogon=1, DefaultUserName, DefaultDomainName;
      3. сносит DefaultPassword ИЗ РЕЕСТРА, если он там вдруг есть, — в реестре
         пароль лежал бы ОТКРЫТЫМ ТЕКСТОМ, это запрещено;
      4. сносит AutoLogonCount (если >0, автологон одноразовый: Winlogon после
         первого входа сам удаляет AutoAdminLogon — классическая тихая поломка);
      5. ставит DevicePasswordLessBuildVersion=0 — при значении 2 (дефолт Win11)
         включён «passwordless»-режим, который на части сборок глушит автологон.

    ЧЕГО НЕ ДЕЛАЕТ. Не печатает пароль — ни в консоль, ни в лог, ни в реестр.
    Не запоминает его на диске. Не перезагружает машину.

    ПРОВЕРКА ДО ЗАПИСИ. Введённый пароль сначала проверяется вызовом LogonUser;
    неверный пароль записан НЕ БУДЕТ. Это закрывает класс «автологон сломался
    молча»: без проверки ошибка вылезла бы только на следующей загрузке, когда
    машина уже стоит у экрана входа и до неё никому не дотянуться.

    ЗАПУСК (в обычном окне PowerShell, НЕ в сессии Claude — пароль не должен
    попадать ни в какую переписку):
        powershell -ExecutionPolicy Bypass -File D:\turbobaby-bot\autologon_arm.ps1
    Скрипт сам поднимет себе права администратора.

    ОТКАТ:
        powershell -ExecutionPolicy Bypass -File D:\turbobaby-bot\autologon_arm.ps1 -Disable

    ЦЕНА (владелец знает и принял): пароль учётки лежит на машине. LSA Secrets
    админом извлекается. Физический доступ к машине = доступ к паролю. Смена
    пароля учётки СЛОМАЕТ автологон — после смены запустить этот скрипт заново.
#>

[CmdletBinding()]
param(
    [switch]$Disable
)

$ErrorActionPreference = 'Stop'

$TargetUser   = 'mxfill1'
$TargetDomain = $env:COMPUTERNAME
$WinlogonKey  = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$PwLessKey    = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device'

# ---------------------------------------------------------------- самоподъём прав
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host 'Нужны права администратора — перезапускаюсь с повышением...' -ForegroundColor Yellow
    $argList = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`"")
    if ($Disable) { $argList += '-Disable' }
    Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList $argList -Verb RunAs
    return
}

# ---------------------------------------------------------------- P/Invoke: LSA + LogonUser
$cs = @'
using System;
using System.Runtime.InteropServices;

public static class TbAutologon
{
    [StructLayout(LayoutKind.Sequential)]
    struct LSA_UNICODE_STRING { public ushort Length; public ushort MaximumLength; public IntPtr Buffer; }

    [StructLayout(LayoutKind.Sequential)]
    struct LSA_OBJECT_ATTRIBUTES {
        public int Length; public IntPtr RootDirectory; public IntPtr ObjectName;
        public uint Attributes; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService;
    }

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern uint LsaOpenPolicy(IntPtr SystemName, ref LSA_OBJECT_ATTRIBUTES oa,
                                     uint DesiredAccess, out IntPtr PolicyHandle);

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern uint LsaStorePrivateData(IntPtr PolicyHandle, ref LSA_UNICODE_STRING key,
                                           ref LSA_UNICODE_STRING data);

    [DllImport("advapi32.dll", SetLastError = true, EntryPoint = "LsaStorePrivateData")]
    static extern uint LsaDeletePrivateData(IntPtr PolicyHandle, ref LSA_UNICODE_STRING key, IntPtr nul);

    [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr PolicyHandle);
    [DllImport("advapi32.dll")] static extern int  LsaNtStatusToWinError(uint status);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool LogonUser(string user, string domain, string pass,
                                 int logonType, int provider, out IntPtr token);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CloseHandle(IntPtr h);

    const uint POLICY_CREATE_SECRET = 0x00000020;

    static LSA_UNICODE_STRING Wrap(string s, out IntPtr mem)
    {
        mem = Marshal.StringToHGlobalUni(s);
        LSA_UNICODE_STRING u = new LSA_UNICODE_STRING();
        u.Length        = (ushort)(s.Length * 2);
        u.MaximumLength = (ushort)(s.Length * 2 + 2);
        u.Buffer        = mem;
        return u;
    }

    /// <summary>value == null => секрет удаляется. Возвращает Win32-код (0 = ок).</summary>
    public static int StoreSecret(string key, string value)
    {
        LSA_OBJECT_ATTRIBUTES oa = new LSA_OBJECT_ATTRIBUTES();
        oa.Length = Marshal.SizeOf(typeof(LSA_OBJECT_ATTRIBUTES));
        IntPtr policy;
        uint st = LsaOpenPolicy(IntPtr.Zero, ref oa, POLICY_CREATE_SECRET, out policy);
        if (st != 0) return LsaNtStatusToWinError(st);

        IntPtr kmem = IntPtr.Zero, vmem = IntPtr.Zero;
        try {
            LSA_UNICODE_STRING k = Wrap(key, out kmem);
            if (value == null) {
                st = LsaDeletePrivateData(policy, ref k, IntPtr.Zero);
            } else {
                LSA_UNICODE_STRING v = Wrap(value, out vmem);
                st = LsaStorePrivateData(policy, ref k, ref v);
            }
            return LsaNtStatusToWinError(st);
        } finally {
            if (kmem != IntPtr.Zero) Marshal.ZeroFreeGlobalAllocUnicode(kmem);
            if (vmem != IntPtr.Zero) Marshal.ZeroFreeGlobalAllocUnicode(vmem);  // затираем пароль в памяти
            LsaClose(policy);
        }
    }

    /// <summary>0 = пароль верен; иначе Win32-код (1326 = неверный пароль).</summary>
    public static int CheckPassword(string user, string domain, string pass)
    {
        IntPtr tok = IntPtr.Zero;
        bool ok = LogonUser(user, domain, pass, 2 /*INTERACTIVE*/, 0, out tok);
        int err = Marshal.GetLastWin32Error();
        if (ok) { CloseHandle(tok); return 0; }
        return err;
    }
}
'@
Add-Type -TypeDefinition $cs -Language CSharp | Out-Null

# ---------------------------------------------------------------- откат
if ($Disable) {
    $rc = [TbAutologon]::StoreSecret('DefaultPassword', $null)
    Set-ItemProperty -Path $WinlogonKey -Name 'AutoAdminLogon' -Value '0' -Type String
    if ((Get-ItemProperty $WinlogonKey).PSObject.Properties['DefaultPassword']) {
        Remove-ItemProperty -Path $WinlogonKey -Name 'DefaultPassword'
    }
    if (Test-Path $PwLessKey) {
        Set-ItemProperty -Path $PwLessKey -Name 'DevicePasswordLessBuildVersion' -Value 2 -Type DWord
    }
    Write-Host "ОТКАЧЕНО: AutoAdminLogon=0, секрет LSA удалён (код $rc), passwordless вернул 2." -ForegroundColor Green
    Write-Host 'Задача блокировки \tb_lock_on_logon НЕ трогалась — снимать её отдельно, если нужно.'
    return
}

# ---------------------------------------------------------------- ввод пароля
Write-Host ''
Write-Host "Вооружение автологона для $TargetDomain\$TargetUser" -ForegroundColor Cyan
Write-Host 'Пароль вводится вслепую, нигде не печатается и не сохраняется на диск.'
Write-Host ''
$sec = Read-Host -AsSecureString "Пароль учётной записи $TargetUser"

$bstr  = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$plain = $null
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)

    if ([string]::IsNullOrEmpty($plain)) {
        Write-Host ''
        Write-Host 'СТОП: введён пустой пароль.' -ForegroundColor Red
        Write-Host 'Пустой пароль оставлять нельзя: LimitBlankPasswordUse=1 запрещает этой учётке'
        Write-Host 'любой неконсольный вход, и любая будущая задача с LogonType=Password умрёт.'
        Write-Host 'Сначала задайте непустой пароль (Параметры -> Учётные записи -> Варианты входа),'
        Write-Host 'потом запустите этот скрипт снова.'
        return
    }

    # --- проверка ДО записи: неверный пароль не должен попасть в LSA
    $chk = [TbAutologon]::CheckPassword($TargetUser, $TargetDomain, $plain)
    if ($chk -ne 0) {
        Write-Host ''
        Write-Host "СТОП: пароль не принят системой (код $chk). Ничего не записано." -ForegroundColor Red
        Write-Host '1326 = неверный пароль. 1327 = ограничение учётной записи (в т.ч. пустой пароль).'
        return
    }
    Write-Host 'Пароль проверен системой — верен.' -ForegroundColor Green

    # --- 1. секрет в LSA (НЕ в реестр)
    $rc = [TbAutologon]::StoreSecret('DefaultPassword', $plain)
    if ($rc -ne 0) { throw "LsaStorePrivateData вернул Win32-код $rc" }
    Write-Host 'Пароль положен в LSA Secrets (DefaultPassword).' -ForegroundColor Green
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)   # затираем буфер пароля
    $plain = $null
    [GC]::Collect()
}

# --- 2. ключи Winlogon
Set-ItemProperty -Path $WinlogonKey -Name 'AutoAdminLogon'    -Value '1'          -Type String
Set-ItemProperty -Path $WinlogonKey -Name 'DefaultUserName'   -Value $TargetUser  -Type String
Set-ItemProperty -Path $WinlogonKey -Name 'DefaultDomainName' -Value $TargetDomain -Type String

# --- 3. пароля открытым текстом в реестре быть не должно
$wl = Get-ItemProperty $WinlogonKey
if ($wl.PSObject.Properties['DefaultPassword']) {
    Remove-ItemProperty -Path $WinlogonKey -Name 'DefaultPassword'
    Write-Host 'Убран DefaultPassword из реестра (там он лежал бы открытым текстом).' -ForegroundColor Yellow
}

# --- 4. одноразовый автологон — не наш случай
if ($wl.PSObject.Properties['AutoLogonCount']) {
    Remove-ItemProperty -Path $WinlogonKey -Name 'AutoLogonCount'
    Write-Host 'Убран AutoLogonCount (иначе автологон сработал бы ровно один раз).' -ForegroundColor Yellow
}

# --- 5. passwordless-режим Win11
if (-not (Test-Path $PwLessKey)) { New-Item -Path $PwLessKey -Force | Out-Null }
Set-ItemProperty -Path $PwLessKey -Name 'DevicePasswordLessBuildVersion' -Value 0 -Type DWord

# ---------------------------------------------------------------- отчёт (без пароля)
$wl  = Get-ItemProperty $WinlogonKey
$pwl = Get-ItemProperty $PwLessKey
Write-Host ''
Write-Host 'ИТОГ:' -ForegroundColor Cyan
Write-Host ("  AutoAdminLogon                 = {0}" -f $wl.AutoAdminLogon)
Write-Host ("  DefaultUserName                = {0}" -f $wl.DefaultUserName)
Write-Host ("  DefaultDomainName              = {0}" -f $wl.DefaultDomainName)
Write-Host ("  DefaultPassword в реестре      = {0}" -f $(if ($wl.PSObject.Properties['DefaultPassword']) { 'ЕСТЬ — ПЛОХО' } else { 'нет (правильно)' }))
Write-Host ("  DevicePasswordLessBuildVersion = {0}" -f $pwl.DevicePasswordLessBuildVersion)
Write-Host ("  секрет LSA DefaultPassword     = записан (значение не показывается)")
Write-Host ''
Write-Host 'Блокировка экрана после входа: задача Планировщика \tb_lock_on_logon' -ForegroundColor Cyan
Write-Host '(LogonTrigger + 5с -> wscript //B lock_on_logon.vbs -> LockWorkStation с проверкой факта).'
Write-Host ''
Write-Host 'Осталось: перезагрузить машину и убедиться, что контур поднялся без ввода ПИН-кода.' -ForegroundColor Yellow
Write-Host 'Отдельно и только руками: BIOS -> Settings -> Advanced -> Power Management Setup ->'
Write-Host '"Restore after AC Power Loss" = Power On (из ОС этот параметр не читается и не меняется).'
