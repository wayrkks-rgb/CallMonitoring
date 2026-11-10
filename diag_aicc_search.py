# -*- coding: utf-8 -*-
r"""
diag_aicc_search.py — AICC(리눅스) 로그가 검색되지 않는 원인 진단

AICC 는 ARS 와 달리 색인(DB)을 쓰지 않는다. 검색할 때마다 서버에 붙어
원격 grep 을 돌린다. 따라서 인증이 정상인데도 결과가 없다면 원인은
'인증 이후' 단계다:

  1) 로그 경로에 날짜 플레이스홀더가 없어 경로가 통째로 스킵되는가
  2) 전개된 파일 경로가 서버에 실제로 존재하는가  (여기가 가장 흔함)
  3) grep 이 실제로 몇 줄을 반환하는가 / 종료코드는 무엇인가
  4) 인코딩·정규식 방언 때문에 매칭이 안 되는가

사용법:
    python diag_aicc_search.py --list
    python diag_aicc_search.py -s 0
    python diag_aicc_search.py -s 0 -p "custId"          # 패턴 지정
    python diag_aicc_search.py -s 0 --date 2026-08-18    # 날짜 지정
"""
import os
import sys
import argparse
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from config_manager import (  # noqa: E402
    load_config, get_log_paths, get_server_label, DATE_PLACEHOLDERS,
)
from ssh_fetcher import OpenSSHLogFetcher  # noqa: E402


def _hr(t=""):
    print("\n" + "=" * 72)
    if t:
        print(f" {t}")
        print("=" * 72)


def list_servers(servers):
    print("서버 목록 (id → 라벨 [type]):")
    for i, s in enumerate(servers):
        t = (s.get("type") or "AICC").upper()
        en = "" if s.get("enabled", True) else "  (비활성)"
        inb = len(get_log_paths(s, "inbound"))
        outb = len(get_log_paths(s, "outbound"))
        print(f"  {i:>2}  {get_server_label(s):<18} [{t}] "
              f"IN={inb} OUT={outb}{en}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--server", type=int)
    ap.add_argument("-p", "--pattern", default="custId", help="테스트 패턴")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (기본 오늘)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    # 경로 스킵 경고는 2번 항목에서 ★ 로 직접 표시하므로 로거 중복 출력을 막는다
    import logging
    logging.getLogger("ssh_fetcher").setLevel(logging.ERROR)

    cfg = load_config()
    if not cfg:
        print("config.json 을 읽을 수 없습니다.")
        return
    servers = cfg.get("remote_servers", [])

    if args.list or args.server is None:
        list_servers(servers)
        if args.server is None:
            print("\n-s <id> 로 진단할 서버를 지정하세요.")
        return
    if not (0 <= args.server < len(servers)):
        print(f"[!] server_id={args.server} 없음")
        return list_servers(servers)

    srv = servers[args.server]
    label = get_server_label(srv)
    date = args.date or datetime.now().strftime("%Y-%m-%d")
    f = OpenSSHLogFetcher()

    print(f"대상 : {label} [{(srv.get('type') or 'AICC').upper()}]  날짜 : {date}")
    target = f._build_ssh_target(srv)
    print(f"SSH  : {target}  포트={srv.get('ssh_port', 22)}")
    if not target:
        print("★ SSH 대상을 만들 수 없습니다 (hostname/ip 확인)")
        return

    # ── 1) 접속 확인 ───────────────────────────────────────
    _hr("1. SSH 접속")
    ok, msg = f.test_connection(srv)
    print(f"  {'정상' if ok else '★ 실패'} — {msg}")
    if not ok:
        if "미설치" in str(msg):
            print("  → 이 PC 에 OpenSSH 클라이언트가 없습니다.")
            print("     설정 > 앱 > 선택적 기능 에서 'OpenSSH 클라이언트' 설치")
        else:
            print("  → 인증/접속 문제입니다. setup_ssh_keys.py 로 키를 등록하세요.")
        return

    # ── 2) 경로 전개 ───────────────────────────────────────
    _hr("2. 로그 경로 전개")
    for purpose in ("inbound", "outbound"):
        paths = get_log_paths(srv, purpose)
        print(f"\n  [{purpose}] 등록 {len(paths)}개")
        if not paths:
            print("    (없음)")
            continue
        for p in paths:
            has = any(tok in p for tok in DATE_PLACEHOLDERS)
            mark = "" if has else "   ★ 날짜 플레이스홀더 없음 → 검색에서 제외됨"
            print(f"    {p}{mark}")
        pats = f._build_file_patterns(paths, [date])
        print(f"    → 전개된 파일 {len(pats)}개")
        for x in pats:
            print(f"       {x}")

    # ── 3) 서버에 실제로 존재하는가 ─────────────────────────
    _hr("3. 서버의 실제 파일 존재 여부  (가장 흔한 원인)")
    allpaths = get_log_paths(srv, "inbound") + get_log_paths(srv, "outbound")
    pats = f._build_file_patterns(list(dict.fromkeys(allpaths)), [date])
    if not pats:
        print("  ★ 전개된 경로가 없습니다 — 2번의 플레이스홀더 문제입니다.")
        return
    cmd = f._build_ssh_cmd(srv, target)
    listing = "ls -la " + " ".join(pats) + " 2>&1 | head -40"
    cmd = cmd + [listing]
    lines, err = f._execute_ssh(cmd, timeout=40, ok_codes=(0, 1, 2))
    if err:
        print(f"  ★ 실행 실패: {err}")
    else:
        found = 0
        for l in lines or []:
            print(f"    {l}")
            if "No such file" not in l and l.strip().startswith("-"):
                found += 1
        print(f"\n  존재하는 파일 {found}개 / 기대 {len(pats)}개")
        if found == 0:
            print("  ★ 하나도 없습니다. 원인 후보:")
            print("    · 파일명 규칙이 다름 (로그 로테이션 형식 변경)")
            print("    · 경로가 다름 / 권한 없음")
            print("    · 그 날짜에 로그가 생성되지 않음")
            print("  → 서버에서 실제 파일명을 확인해 경로 설정을 맞추세요:")
            base = os.path.dirname(pats[0]) or "/"
            print(f"       ssh {target} \"ls -la {base} | tail -20\"")

    # ── 4) 실제 grep 수행 ──────────────────────────────────
    _hr("4. 실제 grep 결과")
    remote = f._build_grep_command(args.pattern, pats, use_extended=True)
    print(f"  패턴 : {args.pattern!r}")
    print(f"  명령 : {remote[:150]}{'...' if len(remote) > 150 else ''}")
    cmd2 = f._build_ssh_cmd(srv, target) + [remote]
    lines2, err2 = f._execute_ssh(cmd2, timeout=60, ok_codes=(0, 1, 2))
    if err2:
        print(f"  ★ 실패: {err2}")
    else:
        n = len(lines2 or [])
        print(f"  매칭 {n}줄")
        for l in (lines2 or [])[:3]:
            print(f"    {l[:160]}")
        if n == 0:
            print("\n  매칭 0줄 — 3번에서 파일이 존재한다면 다음을 의심하세요:")
            print("    · 정규식 방언: AICC 는 grep -E(POSIX ERE) 입니다.")
            print("      \\d \\s (?:...) 는 동작하지 않습니다 → [0-9] [[:space:]] 사용")
            print("    · 패턴이 실제 로그 문자열과 다름")
            print(f"      확인: ssh {target} \"head -3 {pats[0]}\"")

    # ── 5) 검색 경로 재현 ──────────────────────────────────
    _hr("5. 앱과 동일한 경로로 재현")
    lines3, errs3 = f.grep_remote(srv, list(dict.fromkeys(allpaths)), [date],
                                  args.pattern, use_extended=True)
    print(f"  grep_remote() → {len(lines3)}줄, 오류 {len(errs3)}건")
    for e in errs3:
        print(f"    {e}")

    _hr("판정")
    print("""  · 1번 실패            → 인증 문제 (setup_ssh_keys.py)
  · 2번 '플레이스홀더 없음' → 그 경로는 검색에서 빠집니다. 경로 설정 수정
  · 3번 존재 0개         → 경로/파일명 규칙 불일치 (가장 흔함)
  · 3번 정상, 4번 0줄    → 패턴 문제 (POSIX ERE 방언 확인)
  · 4번 정상, 5번 0줄    → 앱 코드 경로 문제이니 결과를 알려주세요""")


if __name__ == "__main__":
    main()
