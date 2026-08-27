# -*- coding: utf-8 -*-
r"""
diag_ars_auth.py — SSH 'Permission denied (rc=255)' 원인 진단

rc=255 는 ssh 자신의 종료코드로 '원격 명령 실패'가 아니라 '접속/인증 실패'다.
포트가 열려 있고 OpenSSH 가 설치돼 있어도 인증에서 막히면 이 값이 나온다.
ssh -v 출력을 파싱해 어느 단계에서 끊겼는지 짚어준다.

사용법:
    python diag_ars_auth.py --list
    python diag_ars_auth.py -s <server_id>
    python diag_ars_auth.py -s <server_id> --raw     # ssh -vvv 원문도 출력
"""
import os
import sys
import argparse
import subprocess
from pathlib import Path

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from config_manager import load_config, get_server_label, normalize_access_method  # noqa: E402
from ars_ssh_fetcher import ArsSshIO  # noqa: E402


def _hr(t=""):
    print("\n" + "=" * 72)
    if t:
        print(f" {t}")
        print("=" * 72)


def list_servers():
    cfg = load_config() or {}
    print("등록 서버 (id → 라벨 [type/access]  SSH대상:포트):")
    for i, s in enumerate(cfg.get("remote_servers", [])):
        am = normalize_access_method(s.get("access_method"))
        tgt = ArsSshIO.ssh_target(s) if am == "ssh" else "-"
        print(f"  {i:>2}  {get_server_label(s):<18} [{s.get('type','AICC')}/{am}]"
              f"  {tgt}:{s.get('ssh_port', 22)}")


def check_key(server):
    _hr("1. 클라이언트 키 파일")
    kp = server.get("ssh_key_path")
    if not kp:
        print("  ssh_key_path 미설정 → 키 인증을 시도하지 않습니다.")
        print("  ★ BatchMode=yes 라 비밀번호 입력도 못 하므로 반드시 실패합니다.")
        print("    → 서버 관리에서 'SSH 키 등록'을 다시 수행하세요.")
        return None
    p = Path(kp)
    print(f"  경로 : {kp}")
    print(f"  존재 : {p.exists()}")
    if not p.exists():
        print("  ★ 키 파일이 없습니다. 이게 원인입니다.")
        print("    - 파일이 삭제/이동됐거나")
        print("    - 웹앱이 다른 계정(서비스 계정)으로 돌아 그 경로가 안 보이거나")
        print("      (키는 보통 등록을 수행한 계정의 %USERPROFILE%\\.ssh 에 생성됩니다)")
        return None
    try:
        print(f"  크기 : {p.stat().st_size:,} bytes")
        head = p.read_text(errors="ignore").splitlines()[:1]
        print(f"  헤더 : {head[0] if head else '(빈 파일)'}")
        if p.stat().st_size == 0:
            print("  ★ 빈 파일입니다.")
    except OSError as e:
        print(f"  ★ 읽기 실패: {e}  (실행 계정 권한 확인)")
        return None
    pub = Path(str(p) + ".pub")
    print(f"  공개키({pub.name}) 존재 : {pub.exists()}")
    if pub.exists():
        try:
            line = pub.read_text(errors="ignore").strip()
            print(f"    {line[:90]}...")
            fp = _fingerprint(line)
            if fp:
                print(f"  ★ 지문 : {fp}")
                print("    → 서버의 authorized_keys 에 '이 지문'이 있는지 대조하세요.")
        except OSError:
            pass
    else:
        print("    (.pub 이 없으면 지문 대조를 못 합니다 — 서버 등록 여부 확인 필요)")
    return str(p)


def _fingerprint(pubkey_line):
    """'ssh-rsa AAAA... comment' → 'SHA256:xxxx' (ssh-keygen -lf 와 동일 형식)."""
    import base64
    import hashlib
    parts = (pubkey_line or "").split()
    blob = next((s for s in parts if len(s) > 40 and s.startswith("AAAA")), None)
    if not blob:
        return None
    try:
        raw = base64.b64decode(blob)
    except Exception:
        return None
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    keytype = parts[0] if parts else "?"
    return f"SHA256:{digest}   (형식 {keytype})"


def probe(server, key_path, raw=False):
    _hr("2. SSH 접속 시도 (ssh -v)")
    target = ArsSshIO.ssh_target(server)
    port = server.get("ssh_port", 22)
    if not target:
        print("  ★ SSH 대상을 만들 수 없습니다 (hostname/ip 확인)")
        return
    cmd = ["ssh", "-vvv" if raw else "-v",
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=accept-new",
           "-o", "ConnectionAttempts=1"]
    if key_path:
        cmd += ["-i", key_path, "-o", "IdentitiesOnly=yes"]
    if port and int(port) != 22:
        cmd += ["-p", str(port)]
    cmd += [target, "echo __OK__"]
    print(f"  실행 : ssh ... -p {port} {target} \"echo __OK__\"")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=40)
    except subprocess.TimeoutExpired:
        print("  ★ 타임아웃 — 방화벽/포트 문제 (인증 이전 단계)")
        return
    except FileNotFoundError:
        print("  ★ ssh 실행 파일을 찾을 수 없습니다 (OpenSSH 클라이언트 미설치)")
        return

    err = r.stderr or ""
    print(f"  결과 : rc={r.returncode}  stdout={r.stdout.strip()!r}")
    if raw:
        print("\n----- ssh -vvv 원문 -----")
        print(err)
        print("-------------------------\n")

    if r.returncode == 0 and "__OK__" in (r.stdout or ""):
        print("  → 접속·인증 정상입니다. 문제는 다른 단계에 있습니다.")
        return

    _hr("3. 판정")
    low = err.lower()

    def has(*subs):
        return any(s.lower() in low for s in subs)

    # 서버가 제시한 인증 방식 / 클라이언트가 제시한 키 (중복 줄 제거)
    seen = set()

    def once(label, value):
        v = (label, value)
        if value and v not in seen:
            seen.add(v)
            print(f"  {label} : {value}")

    for line in err.splitlines():
        s = line.strip()
        low_s = s.lower()
        if "authentications that can continue" in low_s:
            once("서버가 허용하는 인증", s.rsplit(":", 1)[-1].strip())
        elif "offering public key" in low_s:
            once("클라이언트가 제시한 키", s.split("Offering public key:", 1)[-1].strip())
        elif "server accepts key" in low_s:
            once("서버 반응", "키를 수락함 (이후 단계에서 실패)")
        elif "we sent a publickey packet, wait for reply" in low_s:
            once("서버 반응", "키 제시 후 거부됨 (등록되지 않은 키)")

    if has("connection refused"):
        print("  ★ 포트에서 거부 — sshd 미기동 또는 포트 불일치")
    elif has("no route to host", "network is unreachable", "connection timed out"):
        print("  ★ 네트워크 도달 불가 — 방화벽/라우팅")
    elif has("host key verification failed"):
        print("  ★ 호스트 키 불일치 — 서버 재설치/교체 가능성")
        print("    → known_hosts 에서 해당 호스트 줄을 지우고 재시도")
    elif has("no such identity", "could not open"):
        print("  ★ 키 파일을 열 수 없음 — 경로/권한 확인")
    elif has("bad permissions", "unprotected private key"):
        print("  ★ 개인키 파일 권한이 너무 열려 있어 ssh 가 거부")
        print("    → icacls 로 실행 계정만 읽기 권한이 되도록 조정")
    elif has("permission denied"):
        print("  ★ 인증 거부 — 서버가 이 키를 받아주지 않습니다.")
        print()
        print("    [어느 순간부터 막혔다면 가장 흔한 원인]")
        print("    1) 계정이 Administrators 그룹에 들어갔다/빠졌다")
        print("       Windows OpenSSH 는 관리자 계정의 경우")
        print("       ~/.ssh/authorized_keys 를 '무시'하고")
        print("       C:\\ProgramData\\ssh\\administrators_authorized_keys 만 봅니다.")
        print("       그룹이 바뀌면 잘 되던 키가 그날부터 무시됩니다.")
        print("    2) administrators_authorized_keys 의 ACL 이 초기화됨")
        print("       (상속 켜짐/일반 사용자 권한 추가) → sshd 가 조용히 거부")
        print("       복구:")
        print("         icacls C:\\ProgramData\\ssh\\administrators_authorized_keys"
              " /inheritance:r /grant Administrators:F /grant SYSTEM:F")
        print("    3) sshd_config 변경 — PubkeyAuthentication no,")
        print("       AuthorizedKeysFile 경로 변경, Match 블록 추가")
        print("    4) 키가 실제로 등록돼 있지 않음(계정 재생성/프로필 초기화)")
        print()
        print("    [서버에서 확인할 것]")
        print("      · 대상 계정이 Administrators 인지")
        print("      · 위 두 파일 중 '어느 쪽'에 공개키가 들어 있는지")
        print("      · sshd 로그: 이벤트 뷰어 > 응용 프로그램 및 서비스 로그 > OpenSSH")
        print("        (여기에 거부 사유가 명시됩니다 — 가장 확실한 근거)")
    else:
        print("  ★ 분류되지 않은 실패 — --raw 로 ssh -vvv 원문을 확인하세요")
        print("  stderr 마지막 줄:")
        for line in [l for l in err.splitlines() if l.strip()][-5:]:
            print(f"    {line}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--server", type=int, help="진단할 서버 id")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--raw", action="store_true", help="ssh -vvv 원문 출력")
    args = ap.parse_args()

    if args.list or args.server is None:
        list_servers()
        if args.server is None:
            print("\n-s <id> 로 진단할 서버를 지정하세요.")
        return

    cfg = load_config() or {}
    servers = cfg.get("remote_servers", [])
    if not (0 <= args.server < len(servers)):
        print(f"[!] server_id={args.server} 없음")
        return list_servers()
    server = servers[args.server]

    print(f"대상 : {get_server_label(server)} "
          f"[{server.get('type','?')}/{normalize_access_method(server.get('access_method'))}]")
    key = check_key(server)
    probe(server, key, raw=args.raw)


if __name__ == "__main__":
    main()
