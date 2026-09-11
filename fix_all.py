# -*- coding: utf-8 -*-
r"""
fix_all.py — ARS + AICC 로그 수집 전체 점검 · 자동 조치 · 리포트 (단일 실행)

폐쇄망 왕복을 줄이기 위해 한 번 실행으로 아래를 전부 수행한다.
paramiko 불필요 (표준 ssh / ssh-keygen 만 사용).

  [1] 실행 환경 점검 (ssh/ssh-keygen, 실행 계정)
  [2] 서버별 처리
        - SSH 서버(AICC 전체 + ARS access_method=ssh)
            키 확보(없으면 ed25519 생성) → 키 인증 테스트
            → 실패 시 비밀번호 1회 입력받아 공개키 등록 → 재검증
            → config.json 의 ssh_key_path 갱신
        - UNC 서버(ARS access_method=unc)
            ars_auth.json 자격증명으로 공유 연결 확인
  [3] 실제 로그 접근 검증 (오늘자 파일 존재 + 테스트 grep)
  [4] ARS 색인 복구 (접속 장애 중 '없는 파일'로 오인 확정된 구간 초기화)
  [5] 요약 리포트 (파일로도 저장 → 그대로 가져오시면 됩니다)

사용법 (웹앱을 실행하는 계정으로):
    python fix_all.py                  # 점검 + 자동 조치
    python fix_all.py --check-only     # 조치 없이 점검만
    python fix_all.py --no-index-reset # 색인 초기화는 건너뜀
    python fix_all.py -s 2             # 특정 서버만
"""
import os
import sys
import base64
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

KEY_DIR = Path.home() / ".ssh"
NOW = datetime.now()
TODAY = NOW.strftime("%Y-%m-%d")


# ── 출력: 화면 + 파일 동시 기록 ────────────────────────────
class Tee:
    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")
        self.out = sys.stdout

    def write(self, s):
        self.out.write(s)
        self.f.write(s)

    def flush(self):
        self.out.flush()
        self.f.flush()

    def close(self):
        self.f.close()


def hr(t=""):
    print("\n" + "=" * 74)
    if t:
        print(f" {t}")
        print("=" * 74)


def sub(t):
    print("\n  " + "-" * 68)
    print(f"  {t}")
    print("  " + "-" * 68)


# ── 공통 헬퍼 ─────────────────────────────────────────────
def ssh_target(srv):
    host, ip = srv.get("hostname"), srv.get("ip")
    user = srv.get("user") or "loguser"
    if host and not ip:
        return host
    return f"{user}@{ip}" if ip else (host or None)


def ssh_port(srv):
    try:
        return int(srv.get("ssh_port") or 22)
    except (TypeError, ValueError):
        return 22


def port_args(srv):
    p = ssh_port(srv)
    return ["-p", str(p)] if p != 22 else []


def is_ars(srv):
    return (srv.get("type") or "AICC").upper() == "ARS"


def ascii_name(s):
    out = "".join(c if (c.isascii() and (c.isalnum() or c in "-_")) else "_"
                  for c in (s or "srv"))
    return out.strip("_") or "srv"


def run(cmd, timeout=60, interactive=False):
    """(rc, stdout, stderr). interactive=True 면 화면을 그대로 넘긴다(비밀번호 입력)."""
    try:
        if interactive:
            r = subprocess.run(cmd, timeout=timeout)
            return r.returncode, "", ""
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=timeout)
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return 127, "", "실행 파일 없음"
    except subprocess.TimeoutExpired:
        return 124, "", f"시간 초과({timeout}s)"
    except Exception as e:                     # noqa: BLE001
        return 1, "", str(e)


def ssh_run(srv, remote, timeout=60, batch=True, force_password=False, key=None):
    opts = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]
    if batch:
        opts += ["-o", "BatchMode=yes"]
    if force_password:
        opts += ["-o", "PubkeyAuthentication=no"]
    elif key:
        opts += ["-o", "IdentitiesOnly=yes", "-i", key]
    cmd = ["ssh"] + opts + port_args(srv) + [ssh_target(srv), remote]
    return run(cmd, timeout=timeout, interactive=force_password)


# ── 키 확보 / 등록 ────────────────────────────────────────
def ensure_key(srv, label, idx):
    kp = srv.get("ssh_key_path")
    if kp and Path(kp).exists():
        priv = Path(kp)
    else:
        KEY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        priv = KEY_DIR / f"id_ed25519_{idx}_{ascii_name(label)}"
        if not priv.exists():
            print(f"    키 생성 : {priv}")
            rc, out, err = run(["ssh-keygen", "-t", "ed25519", "-N", "",
                                "-C", f"log-analyzer@{ascii_name(label)}",
                                "-f", str(priv)], timeout=60)
            if rc != 0:
                print(f"    [X] ssh-keygen 실패 ({rc}) {(err or out).strip()[:150]}")
                return None, None
        else:
            print(f"    기존 키 : {priv}")
    pub = Path(str(priv) + ".pub")
    if not pub.exists():
        print(f"    [X] 공개키 없음 : {pub}")
        return None, None
    return str(priv), pub.read_text(encoding="utf-8", errors="ignore").strip()


def _ps_register(pub):
    key = pub.replace("'", "''")
    return (
        "$ErrorActionPreference='Stop';"
        "$sid=New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544');"
        "$adm=([Security.Principal.WindowsIdentity]::GetCurrent()).Groups -contains $sid;"
        "if($adm){$f=Join-Path $env:ProgramData 'ssh\\administrators_authorized_keys'}"
        "else{$f=Join-Path $env:USERPROFILE '.ssh\\authorized_keys'};"
        "$d=Split-Path $f;"
        "if(-not(Test-Path $d)){New-Item -ItemType Directory -Force -Path $d|Out-Null};"
        f"$k='{key}';$cur=@();"
        "if(Test-Path $f){$cur=@(Get-Content $f|ForEach-Object{$_.Trim()}|Where-Object{$_})};"
        "if($cur -notcontains $k){$cur+=$k};"
        "Set-Content -Path $f -Value $cur -Encoding ascii;"
        "if($adm){icacls $f /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F'|Out-Null}"
        "else{icacls $f /inheritance:r /grant ($env:USERNAME+':F') /grant 'SYSTEM:F'|Out-Null};"
        "Write-Output ('REG_OK ' + $f)"
    )


def _sh_register(pub):
    return ("umask 077; mkdir -p ~/.ssh; "
            f"grep -qxF '{pub}' ~/.ssh/authorized_keys 2>/dev/null || "
            f"echo '{pub}' >> ~/.ssh/authorized_keys; "
            "chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; echo REG_OK")


def register_key(srv, pub):
    if is_ars(srv):
        enc = base64.b64encode(_ps_register(pub).encode("utf-16-le")).decode("ascii")
        remote = f"powershell -NoProfile -NonInteractive -EncodedCommand {enc}"
    else:
        remote = _sh_register(pub)
    print(f"    등록 접속 : {ssh_target(srv)}:{ssh_port(srv)}")
    print("    >>> 서버 비밀번호를 입력하세요 (화면에 표시되지 않습니다)")
    rc, _, _ = ssh_run(srv, remote, timeout=180, batch=False, force_password=True)
    if rc != 0:
        print(f"    [X] 등록 실패 (rc={rc})")
        print("        · 계정/비밀번호/포트 확인")
        print("        · 서버가 PasswordAuthentication no 이면 이 방식 불가")
        return False
    print("    등록 완료")
    return True


def verify_key(srv, key):
    rc, out, err = ssh_run(srv, "echo __OK__", timeout=40, key=key)
    if rc == 0 and "__OK__" in out:
        return True, "키 인증 성공"
    tail = [l for l in err.splitlines() if l.strip()][-1:] or [f"rc={rc}"]
    return False, tail[0][:140]


# ── 로그 접근 검증 ────────────────────────────────────────
def check_logs_ssh_ars(srv):
    """ARS(SSH): 오늘 현재/직전 시각 파일 존재 확인."""
    from ars_fetcher import ArsLogFetcher
    paths = []
    from config_manager import get_log_paths
    for tmpl in get_log_paths(srv, "inbound"):
        if "{HH}" in tmpl:
            for k in (0, 1):
                t = NOW.replace(minute=0, second=0)
                hh = (NOW.hour - k) % 24
                paths.append(ArsLogFetcher._expand(tmpl, TODAY, hh))
        else:
            paths.append(ArsLogFetcher._expand(tmpl, TODAY))
    if not paths:
        return 0, 0, "인바운드 경로 없음 (★ 색인 대상에서 제외됩니다)"
    quoted = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    script = (f"@({quoted}) | ForEach-Object {{ "
              "if(Test-Path -LiteralPath $_){"
              "Write-Output ('OK ' + (Get-Item -LiteralPath $_).Length + ' ' + $_)}"
              "else{Write-Output ('NO 0 ' + $_)} }")
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    rc, out, err = ssh_run(srv, f"powershell -NoProfile -NonInteractive -EncodedCommand {enc}",
                           timeout=60, key=srv.get("ssh_key_path"))
    if rc != 0:
        return 0, len(paths), f"확인 실패 rc={rc} {err.strip()[:100]}"
    found = 0
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        print(f"      {line}")
        if line.startswith("OK "):
            found += 1
    return found, len(paths), None


def check_logs_unc_ars(srv, conn):
    """ARS(UNC): 공유 연결 + 오늘 파일 존재."""
    from ars_fetcher import ArsLogFetcher
    from config_manager import get_log_paths
    paths = []
    for tmpl in get_log_paths(srv, "inbound"):
        if "{HH}" in tmpl:
            for k in (0, 1):
                paths.append(ArsLogFetcher._expand(tmpl, TODAY, (NOW.hour - k) % 24))
        else:
            paths.append(ArsLogFetcher._expand(tmpl, TODAY))
    if not paths:
        return 0, 0, "인바운드 경로 없음 (★ 색인 대상에서 제외됩니다)"
    try:
        res = conn.connect_for_paths(paths)
        bad = [e for ok, e in res.values() if not ok]
        if bad:
            return 0, len(paths), f"UNC 연결 실패 {bad[0]}"
    except Exception as e:                      # noqa: BLE001
        return 0, len(paths), f"UNC 연결 예외 {e}"
    found = 0
    for p in paths:
        try:
            sz = os.path.getsize(p)
            print(f"      OK {sz} {p}")
            found += 1
        except OSError as e:
            print(f"      NO 0 {p}  ({type(e).__name__})")
    return found, len(paths), None


def check_logs_aicc(srv, pattern):
    """AICC: 오늘 파일 존재 + 테스트 grep."""
    from ssh_fetcher import OpenSSHLogFetcher
    from config_manager import get_log_paths, DATE_PLACEHOLDERS
    f = OpenSSHLogFetcher.__new__(OpenSSHLogFetcher)
    f.openssh_available = True
    raw = list(dict.fromkeys(get_log_paths(srv, "inbound") + get_log_paths(srv, "outbound")))
    skipped = [p for p in raw if not any(t in p for t in DATE_PLACEHOLDERS)]
    for p in skipped:
        print(f"      ★ 날짜 플레이스홀더 없음 → 검색 제외 : {p}")
    pats = f._build_file_patterns(raw, [TODAY])
    if not pats:
        return 0, 0, 0, "전개된 경로 없음 (경로 설정 확인)"
    listing = "ls -la " + " ".join(pats) + " 2>&1 | head -30"
    rc, out, err = ssh_run(srv, listing, timeout=45, key=srv.get("ssh_key_path"))
    found = 0
    for line in out.splitlines():
        line = line.rstrip()
        if not line:
            continue
        print(f"      {line[:110]}")
        if line.startswith("-"):
            found += 1
    grep_cmd = f._build_grep_command(pattern, pats, use_extended=True)
    rc2, out2, err2 = ssh_run(srv, grep_cmd, timeout=60, key=srv.get("ssh_key_path"))
    hits = len([l for l in out2.splitlines() if l.strip()])
    note = None
    if rc2 not in (0, 1, 2):
        note = f"grep 실행 오류 rc={rc2} {err2.strip()[:100]}"
    elif err2.strip() and hits == 0:
        note = f"grep stderr: {err2.strip()[:120]}"
    return found, len(pats), hits, note


# ── 메인 ──────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--server", type=int, help="이 서버만")
    ap.add_argument("-p", "--pattern", default="custId", help="AICC 테스트 패턴")
    ap.add_argument("--check-only", action="store_true", help="조치 없이 점검만")
    ap.add_argument("--no-index-reset", action="store_true")
    ap.add_argument("--report", default=None, help="리포트 파일 경로")
    args = ap.parse_args()

    report = args.report or os.path.join(
        BASE, f"fix_all_report_{NOW:%Y%m%d_%H%M%S}.txt")
    tee = Tee(report)
    sys.stdout = tee

    import logging
    logging.disable(logging.WARNING)

    try:
        _main(args, report)
    finally:
        sys.stdout = tee.out
        tee.close()
        print(f"\n리포트 저장 : {report}")
        print("이 파일을 그대로 가져오시면 됩니다.")


def _main(args, report):
    from config_manager import (load_config, save_config, get_server_label,
                                normalize_access_method, get_log_paths)

    hr("0. 실행 환경")
    import getpass
    try:
        who = getpass.getuser()
    except Exception:                            # noqa: BLE001
        who = os.environ.get("USERNAME") or "(알 수 없음)"
    print(f"  시각      : {NOW:%Y-%m-%d %H:%M:%S}")
    print(f"  실행 계정 : {who}   ★ 웹앱 실행 계정과 같아야 합니다")
    print(f"  키 위치   : {KEY_DIR}")
    import shutil
    missing = []
    for tool in ("ssh", "ssh-keygen"):
        path = shutil.which(tool)
        print(f"  {tool:<11}: {path or '★ 없음'}")
        if not path:
            missing.append(tool)
    if missing:
        print("\n★ OpenSSH 클라이언트가 없습니다: " + ", ".join(missing))
        print("  Windows: 설정 > 앱 > 선택적 기능 > 기능 추가 > 'OpenSSH 클라이언트'")
        print("  설치 후 다시 실행하세요. (이 상태로는 아무 서버도 점검할 수 없습니다)")
        return
    rc, out, err = run(["ssh", "-V"], timeout=15)
    v = (err or out).strip().splitlines()
    if v:
        print(f"  버전      : {v[0][:70]}")

    cfg = load_config()
    if not cfg:
        print("\n★ config.json 을 읽을 수 없습니다. 중단합니다.")
        return
    servers = cfg.get("remote_servers", [])
    idxs = [args.server] if args.server is not None else range(len(servers))

    results = []
    conn = None
    changed = False

    for i in idxs:
        if not (0 <= i < len(servers)):
            continue
        srv = servers[i]
        label = get_server_label(srv)
        if not srv.get("enabled", True):
            results.append((i, label, "비활성", "-", None))
            continue

        typ = (srv.get("type") or "AICC").upper()
        am = normalize_access_method(srv.get("access_method"))
        use_ssh = (typ == "AICC") or (typ == "ARS" and am == "ssh")

        hr(f"서버 [{i}] {label}   {typ} / {'SSH' if use_ssh else 'UNC'}")

        # ── 접속 계층 ──────────────────────────────────────
        auth_ok, auth_msg, key = False, "", None
        if use_ssh:
            if not ssh_target(srv):
                results.append((i, label, "설정오류", "hostname/ip 없음", None))
                print("  ★ SSH 대상을 만들 수 없습니다 (hostname/ip 확인)")
                continue
            sub("1) 키 확보 및 인증")
            key, pub = ensure_key(srv, label, i)
            if not key:
                results.append((i, label, "실패", "키 생성 불가", None))
                continue
            auth_ok, auth_msg = verify_key(srv, key)
            print(f"    인증 : {'정상' if auth_ok else '실패 — ' + auth_msg}")
            if not auth_ok and not args.check_only:
                print("    → 공개키를 등록합니다")
                if register_key(srv, pub):
                    auth_ok, auth_msg = verify_key(srv, key)
                    print(f"    재검증 : {'정상' if auth_ok else '실패 — ' + auth_msg}")
            if auth_ok and srv.get("ssh_key_path") != key:
                srv["ssh_key_path"] = key
                changed = True
                print(f"    config 갱신 : ssh_key_path = {key}")
        else:
            sub("1) UNC 연결")
            if conn is None:
                from ars_fetcher import ArsConnectionManager
                conn = ArsConnectionManager()
            auth_ok = True      # 실제 판정은 파일 확인 단계에서

        if use_ssh and not auth_ok:
            results.append((i, label, "인증실패", auth_msg[:60], key))
            continue

        # ── 로그 접근 계층 ─────────────────────────────────
        sub("2) 오늘자 로그 접근")
        try:
            if typ == "ARS" and use_ssh:
                found, total, note = check_logs_ssh_ars(srv)
                hits = None
            elif typ == "ARS":
                found, total, note = check_logs_unc_ars(srv, conn)
                hits = None
            else:
                found, total, hits, note = check_logs_aicc(srv, args.pattern)
        except Exception as e:                   # noqa: BLE001
            found, total, hits, note = 0, 0, None, f"예외 {type(e).__name__}: {e}"

        if note:
            print(f"    ! {note}")
        print(f"    파일 {found}/{total} 존재"
              + (f" · 테스트 grep {hits}줄" if hits is not None else ""))

        if total == 0:
            verdict, detail = "경로문제", note or "경로 없음"
        elif found == 0:
            verdict, detail = "파일없음", "경로/파일명 규칙 불일치 또는 권한"
        elif hits is not None and hits == 0:
            verdict, detail = "매칭0", "파일은 있으나 패턴 불일치(POSIX ERE 확인)"
        else:
            verdict, detail = "정상", f"{found}/{total} 파일"
        results.append((i, label, verdict, detail, key))

    if conn:
        try:
            conn.disconnect_all()
        except Exception:                        # noqa: BLE001
            pass

    if changed and not args.check_only:
        save_config(cfg)
        print("\nconfig.json 저장 완료 (ssh_key_path 갱신)")

    # ── ARS 색인 복구 ─────────────────────────────────────
    if not args.no_index_reset and not args.check_only:
        hr("3. ARS 색인 복구")
        try:
            from ars_index_store import get_default_store
            store = get_default_store()
            with store._lock:
                n = store._conn.execute(
                    "SELECT COUNT(*) FROM scan_state "
                    "WHERE sealed=1 AND last_offset=0").fetchone()[0]
                store._conn.execute(
                    "DELETE FROM scan_state WHERE sealed=1 AND last_offset=0")
                store._conn.commit()
            print(f"  오인 확정(0바이트 sealed) {n}건 초기화")
            print("  → 웹 서버를 재기동하면 해당 구간을 다시 색인합니다")
            st = store.stats()
            print(f"  색인 현황 : 전체 {st.get('indexed_calls')}건 / "
                  f"오늘 {st.get('today_calls')}건 / "
                  f"마지막 콜 {st.get('today_last_call')}")
            with store._lock:
                per = store._conn.execute(
                    "SELECT server, COUNT(*) n, "
                    "SUM(CASE WHEN start_time>=? THEN 1 ELSE 0 END) t, "
                    "MAX(start_time) last FROM calls GROUP BY server",
                    (f"{TODAY} 00:00:00",)).fetchall()
            for r in per:
                print(f"    {r['server']:<18} 전체 {r['n']:>7,}  오늘 {r['t']:>5,}  "
                      f"마지막 {r['last']}")
        except Exception as e:                   # noqa: BLE001
            print(f"  ! 색인 복구 건너뜀: {type(e).__name__}: {e}")

    # ── 요약 ──────────────────────────────────────────────
    hr("4. 요약")
    print(f"  {'id':<4}{'서버':<20}{'상태':<10}{'내용'}")
    for i, label, verdict, detail, _k in results:
        print(f"  {i:<4}{label:<20}{verdict:<10}{detail}")

    bad = [r for r in results if r[2] not in ("정상", "비활성")]
    hr("5. 다음 조치")
    if not bad:
        print("  모든 서버 정상입니다.")
        print("  1) 웹 서버 재기동")
        print("  2) 몇 분 뒤 인바운드 검색 / 패턴 검색 확인")
    else:
        for i, label, verdict, detail, keypath in bad:
            print(f"\n  [{i}] {label} — {verdict}")
            if verdict == "인증실패":
                print("    · 서버가 PasswordAuthentication no 이면 자동 등록 불가")
                print("      → 서버에서 직접 공개키 등록 필요")
                print(f"      → 공개키: {keypath}.pub" if keypath
                      else "      → 키 경로를 확인할 수 없습니다")
            elif verdict == "파일없음":
                print("    · 서버의 실제 파일명과 등록 경로가 다릅니다")
                print("      → 서버에서 실제 파일명 확인 후 로그 경로 수정")
            elif verdict == "경로문제":
                print("    · 로그 경로 미등록 또는 날짜 플레이스홀더 없음")
                print("      → 서버 관리 > 로그 경로에서 확인")
                print("        (ARS 인바운드는 '인바운드' 칸에 등록해야 색인됩니다)")
            elif verdict == "매칭0":
                print("    · AICC 는 grep -E(POSIX ERE) 입니다")
                print("      \\d → [0-9], \\s → [[:space:]], (?:...) → (...)")
    print("\n  위 내용은 리포트 파일에도 그대로 저장돼 있습니다.")


if __name__ == "__main__":
    main()
