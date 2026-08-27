<#
  check_ars_sshd.ps1 — ARS(Windows) 서버에서 실행하는 sshd 인증 점검

  '인증 거부(Permission denied)' 인데 이벤트 로그에 아무것도 안 남을 때,
  sshd 가 '실제로 어느 파일을 보는지' 와 '그 파일 권한이 맞는지' 를 확인한다.

  실행 (ARS 서버에서 관리자 PowerShell):
      powershell -ExecutionPolicy Bypass -File check_ars_sshd.ps1 -User ivradmin
      powershell -ExecutionPolicy Bypass -File check_ars_sshd.ps1 -User ivradmin -EnableLog

  -EnableLog 를 주면 sshd 파일 로깅(DEBUG3)을 켜고 sshd 를 재시작한다.
  (이벤트 로그가 안 쌓일 때 원인을 보는 가장 확실한 방법)
#>
param(
  [Parameter(Mandatory=$true)][string]$User,
  [switch]$EnableLog
)

function Head($t) { Write-Host ""; Write-Host ("=" * 68); Write-Host " $t"; Write-Host ("=" * 68) }

Head "1. sshd 서비스 / 버전"
$svc = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $svc) { Write-Host "  X sshd 서비스가 없습니다 (OpenSSH Server 미설치)"; exit 1 }
Write-Host "  상태 : $($svc.Status)  시작유형 : $($svc.StartType)"
& ssh -V 2>&1 | ForEach-Object { Write-Host "  클라이언트 : $_" }
$sshdExe = "$env:ProgramFiles\OpenSSH\sshd.exe"
if (Test-Path $sshdExe) { & $sshdExe -? 2>&1 | Select-Object -First 1 | ForEach-Object { Write-Host "  sshd : $_" } }
Write-Host "  수신 포트 :"
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.OwningProcess -eq (Get-Process sshd -ErrorAction SilentlyContinue | Select-Object -First 1).Id } |
  ForEach-Object { Write-Host "    $($_.LocalAddress):$($_.LocalPort)" }

Head "2. 대상 계정과 그룹"
$isAdmin = $false
try {
  $members = (Get-LocalGroupMember -Group "Administrators" -ErrorAction Stop |
              ForEach-Object { $_.Name.Split('\')[-1] })
  $isAdmin = $members -contains $User
  Write-Host "  Administrators 구성원 : $($members -join ', ')"
} catch { Write-Host "  (그룹 조회 실패: $_)" }
Write-Host "  '$User' 가 관리자인가 : $isAdmin"
if ($isAdmin) {
  Write-Host "  ★ 관리자 계정이면 sshd 는 ~\.ssh\authorized_keys 를 '무시'하고"
  Write-Host "    C:\ProgramData\ssh\administrators_authorized_keys 만 봅니다."
}

Head "3. sshd_config 의 인증 설정"
$cfg = "$env:ProgramData\ssh\sshd_config"
if (Test-Path $cfg) {
  Get-Content $cfg | Where-Object {
    $_ -match '^\s*(PubkeyAuthentication|AuthorizedKeysFile|Match|PasswordAuthentication|LogLevel|SyslogFacility)'
  } | ForEach-Object { Write-Host "    $_" }
} else { Write-Host "  X sshd_config 없음: $cfg" }

Head "4. sshd 가 참조하는 authorized_keys"
$adminKeys = "$env:ProgramData\ssh\administrators_authorized_keys"
$userKeys  = "C:\Users\$User\.ssh\authorized_keys"
$target = if ($isAdmin) { $adminKeys } else { $userKeys }
Write-Host "  적용 대상 : $target"

foreach ($f in @($adminKeys, $userKeys)) {
  $mark = if ($f -eq $target) { " <= sshd 가 보는 파일" } else { "" }
  if (Test-Path $f) {
    $n = (Get-Content $f | Where-Object { $_.Trim() -and -not $_.StartsWith('#') }).Count
    Write-Host "  [있음] $f  ($n 개 키)$mark"
  } else {
    Write-Host "  [없음] $f$mark"
  }
}

if (Test-Path $target) {
  Head "5. 등록된 키 지문 (웹앱 진단의 '지문' 과 대조)"
  $tmp = [IO.Path]::GetTempFileName()
  Get-Content $target | Where-Object { $_.Trim() -and -not $_.StartsWith('#') } | ForEach-Object {
    Set-Content -Path $tmp -Value $_ -Encoding ascii
    $fp = & ssh-keygen -lf $tmp 2>$null
    if ($fp) { Write-Host "    $fp" } else { Write-Host "    (지문 계산 실패) $($_.Substring(0,[Math]::Min(60,$_.Length)))..." }
  }
  Remove-Item $tmp -ErrorAction SilentlyContinue

  Head "6. 파일 권한 (ACL) — 여기가 틀리면 sshd 가 조용히 거부"
  $acl = Get-Acl $target
  Write-Host "  소유자 : $($acl.Owner)"
  Write-Host "  상속    : $(-not $acl.AreAccessRulesProtected)  (True 면 ★ 문제)"
  $bad = @()
  foreach ($r in $acl.Access) {
    $id = $r.IdentityReference.Value
    Write-Host "    $id : $($r.FileSystemRights)"
    if ($id -notmatch 'BUILTIN\\Administrators|NT AUTHORITY\\SYSTEM') { $bad += $id }
  }
  if ($isAdmin) {
    if ($bad.Count -or -not $acl.AreAccessRulesProtected) {
      Write-Host ""
      Write-Host "  ★ administrators_authorized_keys 는 Administrators 와 SYSTEM 만"
      Write-Host "    권한을 가져야 하고 상속이 꺼져 있어야 합니다."
      Write-Host "    허용 외 주체: $($bad -join ', ')"
      Write-Host "    복구:"
      Write-Host "      icacls `"$target`" /inheritance:r /grant `"Administrators:F`" /grant `"SYSTEM:F`""
    } else {
      Write-Host "  → ACL 정상"
    }
  }
}

if ($EnableLog) {
  Head "7. sshd 파일 로깅 활성화"
  $bk = "$cfg.bak_$(Get-Date -Format yyyyMMdd_HHmmss)"
  Copy-Item $cfg $bk
  Write-Host "  백업 : $bk"
  $c = Get-Content $cfg
  $c = $c | Where-Object { $_ -notmatch '^\s*#?\s*(SyslogFacility|LogLevel)\s' }
  $c += "SyslogFacility LOCAL0"
  $c += "LogLevel DEBUG3"
  Set-Content -Path $cfg -Value $c -Encoding ascii
  Restart-Service sshd
  Write-Host "  sshd 재시작 완료. 이제 웹앱에서 다시 접속을 시도한 뒤:"
  Write-Host "    Get-Content `"$env:ProgramData\ssh\logs\sshd.log`" -Tail 80"
  Write-Host "  거부 사유가 여기에 그대로 남습니다."
  Write-Host "  확인 후 원복 : Copy-Item `"$bk`" `"$cfg`" -Force; Restart-Service sshd"
}

Head "판정 요약"
Write-Host @"
  · 4번에서 'sshd 가 보는 파일' 이 [없음] 이면        → 키가 등록돼 있지 않음
  · 5번 지문에 웹앱 진단의 지문이 없으면              → 다른 키가 등록돼 있음
  · 6번에서 상속 True 이거나 허용 외 주체가 있으면    → ACL 문제 (복구 명령 참고)
  · 2번에서 관리자 여부가 예상과 다르면               → 참조 파일이 바뀐 것
    (관리자 그룹에 들어가면 ~\.ssh\authorized_keys 는 무시됩니다)
  · 위가 다 정상인데도 거부되면 -EnableLog 로 sshd 로그를 켜세요
"@
