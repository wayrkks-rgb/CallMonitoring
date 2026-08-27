# -*- coding: utf-8 -*-
r"""
setup_ssh_keys.py — 모든 서버에 SSH 키를 생성/등록하고 검증 (일괄)

paramiko 없이 동작한다. 표준 ssh / ssh-keygen 만 사용하며, 등록 단계에서
서버 비밀번호를 '대화식으로' 한 번 입력받는다(ssh-copy-id 와 같은 방식).

대상: AICC 서버 전체 + ARS 서버 중 access_method='ssh'

각 서버마다
  1) 키가 없으면 ed25519 키쌍 생성 (앱 실행 계정의 ~/.ssh 아래)
  2) 공개키를 서버에 등록  ← 여기서 비밀번호 1회 입력
       Windows(ARS): 관리자면 administrators_authorized_keys, 아니면 ~\.ssh
                     + icacls 로 ACL 설정 (BOM 없는 ASCII 로 기록)
       Linux(AICC) : ~/.ssh/authorized_keys + chmod 600
  3) config.json 의 ssh_key_path 갱신
  4) 키 인증으로 실제 접속 검증 (BatchMode)

사용법:
    python setup_ssh_keys.py --list
    python setup_ssh_keys.py                 # 전체
    python setup_ssh_keys.py -s 2            # 서버 하나만
    python setup_ssh_keys.py --verify-only   # 등록 없이 현재 상태만 검증
"""
import os
import sys
import base64
import argparse
import subprocess
from pathlib import Path

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from config_manager import (  # noqa: E402
    load_config, save_config, get_server_label, normalize_access_method,
)

KEY_DIR = Path.home() / ".ssh"


def _hr(t=""):
    print("\n" + "=" * 72)
    if t:
        print(f" {t}")
        print("=" * 72)


def ssh_target(srv):
    host = srv.get("hostname")
    ip = srv.get("ip")
    user = srv.get("user") or "loguser"
    if host and not ip:
        return host
    return f"{user}@{ip}" if ip else (host or None)


def is_windows_target(srv):
    """ARS = Windows, AICC = Linux (코드 전반의 전제와 동일)."""
    return (srv.get("type") or "AICC").upper() == "ARS"


def needs_ssh(srv):
    t = (srv.get("type") or "AICC").upper()
    if t == "AICC":
        return True
    return normalize_access_method(srv.get("access_method")) == "ssh"


def _safe(name):
    """키 파일명용 ASCII 이름. 한글이 들어가면 ssh 명령행 인코딩에서 꼬일 수 있다."""
    out = "".join(c if (c.isascii() and (c.isalnum() or c in "-_")) else "_"
                  for c in (name or "srv"))
    return out.strip("_") or "srv"


def _port_args(srv):
    p = srv.get("ssh_port", 22)
    try:
        p = int(p)
    except (TypeError, ValueError):
        p = 22
    return (["-p", str(p)] if p != 22 else []), p


def ensure_key(srv, label):
    """키쌍 확보. (private_path, public_line) 반환. 실패 시 (None, None)."""
    kp = srv.get("ssh_key_path")
    if kp and Path(kp).exists():
        priv = Path(kp)
    else:
        KEY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        priv = KEY_DIR / f"id_ed25519_{_safe(label)}"
        if not priv.exists():
            print(f"  키 생성 : {priv}")
            try:
                r = subprocess.run(
                    ["ssh-keygen", "-t", "ed25519", "-N", "", "-C",
                     f"log-analyzer@{_safe(label)}", "-f", str(priv)],
                    capture_output=True, text=True)
            except FileNotFoundError:
                print("  [X] ssh-keygen 을 찾을 수 없습니다.")
                print("      Windows: 설정 > 앱 > 선택적 기능 에서 "
                      "'OpenSSH 클라이언트' 설치")
                return None, None
            if r.returncode != 0:
                print(f"  [X] ssh-keygen 실패: {(r.stderr or r.stdout).strip()[:200]}")
                return None, None
        else:
            print(f"  기존 키 사용 : {priv}")
    pub = Path(str(priv) + ".pub")
    if not pub.exists():
        print(f"  [X] 공개키 없음: {pub}")
        return None, None
    return str(priv), pub.read_text(encoding="utf-8", errors="ignore").strip()


def _ps_payload(pub_line):
    """Windows 등록용 PowerShell (따옴표 꼬임 방지를 위해 EncodedCommand 로 전달)."""
    key = pub_line.replace("'", "''")
    return (
        "$ErrorActionPreference='Stop';"
        # 관리자 여부는 그룹 SID(S-1-5-32-544)로 판정한다.
        # IsInRole 은 UAC 필터링된 토큰에서 False 가 나올 수 있다.
        "$sid=New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544');"
        "$adm=([Security.Principal.WindowsIdentity]::GetCurrent()).Groups -contains $sid;"
        "if($adm){$f=Join-Path $env:ProgramData 'ssh\\administrators_authorized_keys'}"
        "else{$f=Join-Path $env:USERPROFILE '.ssh\\authorized_keys'};"
        "$d=Split-Path $f;"
        "if(-not(Test-Path $d)){New-Item -ItemType Directory -Force -Path $d|Out-Null};"
        f"$k='{key}';"
        "$cur=@();"
        "if(Test-Path $f){$cur=@(Get-Content $f|ForEach-Object{$_.Trim()}|Where-Object{$_})};"
        "if($cur -notcontains $k){$cur+=$k};"
        # BOM 이 붙으면 sshd 가 파싱하지 못한다 → ascii 로 기록
        "Set-Content -Path $f -Value $cur -Encoding ascii;"
        "if($adm){icacls $f /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F'|Out-Null}"
        "else{icacls $f /inheritance:r /grant ($env:USERNAME+':F') /grant 'SYSTEM:F'|Out-Null};"
        "Write-Output ('REG_OK ' + $f + ' admin=' + $adm)"
    )


def _sh_payload(pub_line):
    """Linux 등록용 셸 명령 (공개키에는 작은따옴표가 들어가지 않는다)."""
    return (
        "umask 077; mkdir -p ~/.ssh; "
        f"grep -qxF '{pub_line}' ~/.ssh/authorized_keys 2>/dev/null || "
        f"echo '{pub_line}' >> ~/.ssh/authorized_keys; "
        "chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; "
        "echo REG_OK"
    )


def register(srv, pub_line):
    """비밀번호 대화식 입력으로 공개키 등록. 성공 여부 반환."""
    target = ssh_target(srv)
    port_args, port = _port_args(srv)
    if is_windows_target(srv):
        enc = base64.b64encode(_ps_payload(pub_line).encode("utf-16-le")).decode("ascii")
        remote = f"powershell -NoProfile -NonInteractive -EncodedCommand {enc}"
    else:
        remote = _sh_payload(pub_line)

    cmd = (["ssh", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15", "-o", "PubkeyAuthentication=no"]
           + port_args + [target, remote])
    print(f"  등록 접속 : {target} (포트 {port})")
    print("  ★ 서버 비밀번호를 입력하세요 (입력해도 화면에 표시되지 않습니다)")
    try:
        # capture 하지 않아야 비밀번호 프롬프트가 콘솔에 보인다
        r = subprocess.run(cmd, timeout=180)
    except FileNotFoundError:
        print("  [X] ssh 실행 파일을 찾을 수 없습니다 (OpenSSH 클라이언트 확인)")
        return False
    except subprocess.TimeoutExpired:
        print("  [X] 시간 초과")
        return False
    if r.returncode != 0:
        print(f"  [X] 등록 실패 (rc={r.returncode})")
        print("      · 계정/비밀번호/포트 확인")
        print("      · 서버가 'PasswordAuthentication no' 이면 이 방식으로 등록할 수")
        print("        없습니다 → 서버에서 check_ars_sshd.ps1 -AddKey 로 직접 등록")
        return False
    print("  등록 완료")
    return True


def verify(srv, priv):
    """키 인증으로 실제 접속되는지 확인."""
    target = ssh_target(srv)
    port_args, _ = _port_args(srv)
    cmd = (["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "IdentitiesOnly=yes", "-i", priv]
           + port_args + [target, "echo __OK__"])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=40)
    except Exception as e:
        print(f"  검증 : 실패 ({e})")
        return False
    if r.returncode == 0 and "__OK__" in (r.stdout or ""):
        print("  검증 : 키 인증 성공")
        return True
    tail = [l for l in (r.stderr or "").splitlines() if l.strip()][-2:]
    print(f"  검증 : 실패 (rc={r.returncode}) {' / '.join(tail)[:180]}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--server", type=int, help="이 서버만 처리")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify-only", action="store_true",
                    help="등록하지 않고 현재 키로 접속만 검증")
    args = ap.parse_args()

    cfg = load_config()
    if not cfg:
        print("config.json 을 읽을 수 없습니다.")
        return
    servers = cfg.get("remote_servers", [])

    print(f"키 저장 위치 : {KEY_DIR}")
    import getpass
    try:
        who = getpass.getuser()
    except Exception:
        who = os.environ.get("USERNAME") or os.environ.get("USER") or "(알 수 없음)"
    print(f"실행 계정    : {who}")
    print("★ 웹앱을 실행하는 계정과 같은 계정에서 실행해야 합니다.")
    print("  (다른 계정에서 만들면 앱이 그 키를 못 찾습니다)")

    if args.list:
        _hr("서버 목록")
        for i, s in enumerate(servers):
            t = (s.get("type") or "AICC").upper()
            am = normalize_access_method(s.get("access_method"))
            use = "대상" if needs_ssh(s) else "-"
            print(f"  {i:>2}  {get_server_label(s):<18} [{t}/{am}] "
                  f"{ssh_target(s) or '-':<24} 포트={s.get('ssh_port', 22):<5} {use}")
        return

    idxs = [args.server] if args.server is not None else list(range(len(servers)))
    done, failed, skipped = [], [], []

    for i in idxs:
        if not (0 <= i < len(servers)):
            print(f"[!] server_id={i} 없음")
            continue
        srv = servers[i]
        label = get_server_label(srv)
        if not needs_ssh(srv):
            skipped.append(f"{i}:{label}(UNC)")
            continue
        if not ssh_target(srv):
            failed.append(f"{i}:{label}(대상없음)")
            continue

        _hr(f"[{i}] {label}  ({(srv.get('type') or 'AICC').upper()} / "
            f"{'Windows' if is_windows_target(srv) else 'Linux'})")

        priv, pub = ensure_key(srv, label)
        if not priv:
            failed.append(f"{i}:{label}(키생성)")
            continue

        if not args.verify_only:
            if not register(srv, pub):
                failed.append(f"{i}:{label}(등록)")
                continue

        if verify(srv, priv):
            if srv.get("ssh_key_path") != priv:
                srv["ssh_key_path"] = priv
                cfg["remote_servers"][i] = srv
                save_config(cfg)
                print(f"  config.json 갱신 : ssh_key_path = {priv}")
            done.append(f"{i}:{label}")
        else:
            failed.append(f"{i}:{label}(검증)")

    _hr("결과")
    print(f"  성공 {len(done)}건 : {', '.join(done) or '-'}")
    print(f"  실패 {len(failed)}건 : {', '.join(failed) or '-'}")
    if skipped:
        print(f"  건너뜀      : {', '.join(skipped)}  (UNC 모드는 키가 필요 없습니다)")
    if done and not args.verify_only:
        print("\n  다음 단계:")
        print("    1) 웹 서버 정지")
        print("    2) python diag_ars_index.py --reset-failed")
        print("    3) 웹 서버 시작")
        print("    4) python diag_ars_index.py --hours 3   (오늘 건수 증가 확인)")


if __name__ == "__main__":
    main()
