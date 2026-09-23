<#
  check_ars_sshd.ps1 - ARS(Windows) sshd public-key auth checker

  Run this ON THE ARS WINDOWS SERVER when the app gets
  "Permission denied" (rc=255) but the event log shows nothing.

  It reports WHICH authorized_keys file sshd actually reads,
  what keys are in it, and whether the ACL is acceptable to sshd.

  NOTE: this file is intentionally ASCII-only so that Windows
  PowerShell 5.1 parses it regardless of file encoding/BOM.

  Usage (admin PowerShell on the ARS server):
      powershell -ExecutionPolicy Bypass -File check_ars_sshd.ps1 -User ivradmin
      powershell -ExecutionPolicy Bypass -File check_ars_sshd.ps1 -User ivradmin -EnableLog

  To register a public key (contents of the .pub file from the app server):
      powershell -ExecutionPolicy Bypass -File check_ars_sshd.ps1 -User ivradmin ^
          -AddKey "ssh-ed25519 AAAAC3Nz... log-analyzer@ARS01"
#>
param(
    [Parameter(Mandatory = $true)][string]$User,
    [switch]$EnableLog,
    # Register a public key into the file sshd actually reads, with the
    # correct ACL. Pass the FULL one-line contents of the .pub file.
    [string]$AddKey
)

$ErrorActionPreference = 'Continue'

function Head([string]$t) {
    Write-Host ""
    Write-Host ("=" * 68)
    Write-Host (" " + $t)
    Write-Host ("=" * 68)
}

# ---------------------------------------------------------------- 1
Head "1. sshd service and version"
$svc = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "  [X] sshd service not found (OpenSSH Server not installed)"
    exit 1
}
Write-Host ("  status      : " + $svc.Status)
Write-Host ("  start type  : " + $svc.StartType)

$sshdExe = Join-Path $env:ProgramFiles "OpenSSH\sshd.exe"
if (Test-Path $sshdExe) {
    $ver = (Get-Item $sshdExe).VersionInfo.FileVersion
    Write-Host ("  sshd.exe    : " + $sshdExe + "  (" + $ver + ")")
}
else {
    Write-Host "  sshd.exe    : not found under Program Files\OpenSSH"
}

$proc = Get-Process sshd -ErrorAction SilentlyContinue | Select-Object -First 1
if ($proc -and (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    $listen = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
              Where-Object { $_.OwningProcess -eq $proc.Id }
    foreach ($l in $listen) {
        Write-Host ("  listening   : " + $l.LocalAddress + ":" + $l.LocalPort)
    }
    if (-not $listen) { Write-Host "  listening   : (could not resolve)" }
}
else {
    # older Windows without Get-NetTCPConnection
    $ns = & netstat -ano 2>$null | Select-String "LISTENING"
    foreach ($line in $ns) {
        if ($proc -and ($line -match ("\s" + $proc.Id + "\s*$"))) {
            Write-Host ("  listening   : " + $line.ToString().Trim())
        }
    }
}

# ---------------------------------------------------------------- 2
Head "2. Target account and group membership"
$isAdmin = $false
try {
    $members = @()
    foreach ($m in (Get-LocalGroupMember -Group "Administrators" -ErrorAction Stop)) {
        $members += ($m.Name -split '\\')[-1]
    }
    Write-Host ("  Administrators: " + ($members -join ", "))
    if ($members -contains $User) { $isAdmin = $true }
}
catch {
    # Get-LocalGroupMember needs PS 5.1+ / newer Windows. Fall back to net.exe,
    # which exists everywhere. This check matters most, so do not skip it.
    Write-Host ("  (Get-LocalGroupMember failed: " + $_.Exception.Message + ")")
    Write-Host "  falling back to 'net localgroup Administrators'"
    $out = & net localgroup Administrators 2>$null
    foreach ($line in $out) {
        $t = ("" + $line).Trim()
        if (-not $t) { continue }
        if ($t -match '^(The command|Alias name|Comment|Members|-----)') { continue }
        $name = ($t -split '\\')[-1]
        if ($name -eq $User) { $isAdmin = $true }
    }
}
Write-Host ("  '" + $User + "' is admin : " + $isAdmin)
if ($isAdmin) {
    Write-Host "  [!] Admin accounts: sshd IGNORES ~\.ssh\authorized_keys and"
    Write-Host "      uses C:\ProgramData\ssh\administrators_authorized_keys only."
}

# ---------------------------------------------------------------- 3
Head "3. sshd_config auth settings"
$cfg = Join-Path $env:ProgramData "ssh\sshd_config"
if (Test-Path $cfg) {
    Write-Host ("  file: " + $cfg)
    $pat = '^\s*#?\s*(PubkeyAuthentication|AuthorizedKeysFile|Match|PasswordAuthentication|LogLevel|SyslogFacility|Port)\b'
    foreach ($line in (Get-Content $cfg)) {
        if ($line -match $pat) { Write-Host ("    " + $line) }
    }
}
else {
    Write-Host ("  [X] sshd_config not found: " + $cfg)
}

# ---------------------------------------------------------------- 4
Head "4. authorized_keys file that applies"
$adminKeys = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
$userKeys = Join-Path "C:\Users" (Join-Path $User ".ssh\authorized_keys")
if ($isAdmin) { $target = $adminKeys } else { $target = $userKeys }
Write-Host ("  applies -> " + $target)
Write-Host ""

foreach ($f in @($adminKeys, $userKeys)) {
    if ($f -eq $target) { $mark = "   <== sshd reads this" } else { $mark = "" }
    if (Test-Path $f) {
        $keys = @()
        foreach ($line in (Get-Content $f)) {
            $t = $line.Trim()
            if ($t -and -not $t.StartsWith("#")) { $keys += $t }
        }
        Write-Host ("  [EXISTS] " + $f + "  (" + $keys.Count + " keys)" + $mark)
    }
    else {
        Write-Host ("  [MISSING] " + $f + $mark)
    }
}

# ---------------------------------------------------------------- 4b
if ($AddKey) {
    Head "4b. Register public key into the file sshd reads"
    $line = $AddKey.Trim()
    if ($line -notmatch '^(ssh-rsa|ssh-ed25519|ecdsa-sha2-\S+)\s+\S+') {
        Write-Host "  [X] Not a valid public key line."
        Write-Host "      Expected: ssh-ed25519 AAAA... comment"
        Write-Host "      (Use the .pub file contents, NOT the private key)"
    }
    else {
        $dir = Split-Path $target
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            Write-Host ("  created dir : " + $dir)
        }
        $existing = @()
        if (Test-Path $target) {
            foreach ($l in (Get-Content $target)) {
                $t = $l.Trim()
                if ($t) { $existing += $t }
            }
        }
        if ($existing -contains $line) {
            Write-Host "  key already present, not duplicated"
        }
        else {
            $existing += $line
        }
        # ASCII, no BOM - sshd will not parse a BOM'd file
        Set-Content -Path $target -Value $existing -Encoding ascii
        Write-Host ("  wrote       : " + $target + "  (" + $existing.Count + " keys)")

        if ($isAdmin) {
            & icacls $target /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F" | Out-Null
            Write-Host "  ACL set     : Administrators:F, SYSTEM:F, inheritance off"
        }
        else {
            & icacls $target /inheritance:r /grant ($User + ":F") /grant "SYSTEM:F" | Out-Null
            Write-Host ("  ACL set     : " + $User + ":F, SYSTEM:F, inheritance off")
        }
        Write-Host "  -> now retry the connection from the app server"
    }
}

# ---------------------------------------------------------------- 5
if (Test-Path $target) {
    Head "5. Fingerprints of registered keys"
    Write-Host "  Compare with the fingerprint printed by diag_ars_auth.py"
    Write-Host ""
    $tmp = [System.IO.Path]::GetTempFileName()
    foreach ($line in (Get-Content $target)) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        Set-Content -Path $tmp -Value $t -Encoding ascii
        $fp = & ssh-keygen -lf $tmp 2>$null
        if ($fp) {
            Write-Host ("    " + $fp)
        }
        else {
            $head = $t
            if ($head.Length -gt 60) { $head = $head.Substring(0, 60) }
            Write-Host ("    (fingerprint failed) " + $head + "...")
        }
    }
    Remove-Item $tmp -ErrorAction SilentlyContinue

    # ------------------------------------------------------------ 6
    Head "6. ACL of that file  (wrong ACL = silent reject)"
    $acl = Get-Acl $target
    Write-Host ("  owner       : " + $acl.Owner)
    $inherited = -not $acl.AreAccessRulesProtected
    Write-Host ("  inheritance : " + $inherited + "   (True = PROBLEM)")
    $bad = @()
    foreach ($r in $acl.Access) {
        $id = $r.IdentityReference.Value
        Write-Host ("    " + $id + " : " + $r.FileSystemRights)
        if ($id -notmatch 'BUILTIN\\Administrators') {
            if ($id -notmatch 'NT AUTHORITY\\SYSTEM') { $bad += $id }
        }
    }
    if ($isAdmin) {
        Write-Host ""
        if ($bad.Count -gt 0 -or $inherited) {
            Write-Host "  [!] administrators_authorized_keys must grant ONLY"
            Write-Host "      Administrators and SYSTEM, with inheritance disabled."
            if ($bad.Count -gt 0) { Write-Host ("      unexpected: " + ($bad -join ", ")) }
            Write-Host ""
            Write-Host "      FIX:"
            Write-Host ('      icacls "' + $target + '" /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"')
        }
        else {
            Write-Host "  -> ACL looks correct"
        }
    }
}

# ---------------------------------------------------------------- 7
if ($EnableLog) {
    Head "7. Enable sshd file logging (DEBUG3)"
    if (-not (Test-Path $cfg)) {
        Write-Host "  [X] sshd_config not found, skipped"
    }
    else {
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $bk = $cfg + ".bak_" + $stamp
        Copy-Item $cfg $bk
        Write-Host ("  backup: " + $bk)

        $keep = @()
        foreach ($line in (Get-Content $cfg)) {
            if ($line -notmatch '^\s*#?\s*(SyslogFacility|LogLevel)\b') { $keep += $line }
        }
        $keep += "SyslogFacility LOCAL0"
        $keep += "LogLevel DEBUG3"
        Set-Content -Path $cfg -Value $keep -Encoding ascii
        Restart-Service sshd
        $logPath = Join-Path $env:ProgramData "ssh\logs\sshd.log"
        Write-Host "  sshd restarted."
        Write-Host "  Now retry the connection from the web app, then run:"
        Write-Host ('    Get-Content "' + $logPath + '" -Tail 80')
        Write-Host ""
        Write-Host "  To revert:"
        Write-Host ('    Copy-Item "' + $bk + '" "' + $cfg + '" -Force; Restart-Service sshd')
    }
}

# ---------------------------------------------------------------- summary
Head "Summary - how to read this"
Write-Host "  Section 4 shows the file sshd actually reads."
Write-Host ""
Write-Host "  [MISSING] on that file      -> key was never registered there"
Write-Host "  fingerprint not in sec 5    -> a DIFFERENT key is registered"
Write-Host "  inheritance True / extra ID -> ACL problem, use the FIX command"
Write-Host "  admin flag differs from"
Write-Host "  what you expect             -> the file sshd reads has CHANGED"
Write-Host "                                 (adding the account to Administrators"
Write-Host "                                  makes ~\.ssh\authorized_keys ignored)"
Write-Host ""
Write-Host "  All of the above look fine? Re-run with -EnableLog and read sshd.log."
Write-Host ""
Write-Host "  Both files [MISSING]? No key is registered at all. Register it:"
Write-Host "    check_ars_sshd.ps1 -User <user> -AddKey \"<contents of .pub>\""
Write-Host ""
