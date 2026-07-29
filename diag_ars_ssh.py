#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ARS SSH 모드 진단 (단독 실행)

"SSH 로 바꿨더니 결과가 안 나온다" 의 원인을 계층별로 좁힌다.
각 단계에서 실패하면 그 지점이 원인이다.

  0) 설정 확인      : access_method / 로그경로 형식(UNC 경로면 SSH 모드에서 실패)
  1) SSH 접속       : whoami
  2) PowerShell     : -EncodedCommand 왕복
  3) 경로 존재      : 오늘자 파일이 실제로 있는가 (Test-Path)
  4) 파일 크기      : file_size
  5) 부분 읽기      : read_range + 인코딩 판정 + 샘플 라인
  6) 패턴 검색      : Select-String (utf8 / default 둘 다 시도)
  7) 인덱스 상태    : 이 서버 인덱스에 콜이 쌓였는가

사용:
    python diag_ars_ssh.py --list
    python diag_ars_ssh.py -s <server_id>
    python diag_ars_ssh.py -s <server_id> -p ERROR      # 패턴 지정
"""

import sys
import argparse
from datetime import datetime

from config_manager import (
    load_config, get_log_paths, get_server_label, get_server_by_id,
    normalize_access_method,
)
from ars_fetcher import ArsLogFetcher
from ars_ssh_fetcher import ArsSshIO


def list_servers():
    cfg = load_config() or {}
    print("등록 서버 (id → 라벨 [type/access_method]):")
    for i, s in enumerate(cfg.get('remote_servers', [])):
        am = normalize_access_method(s.get('access_method'))
        print(f"  {i:>2}  {get_server_label(s):<18} [{s.get('type','AICC')}/{am}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-s', '--server', type=int, help='진단할 서버 id')
    ap.add_argument('-p', '--pattern', default='call_start', help='테스트 패턴')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    if args.list or args.server is None:
        list_servers()
        if args.server is None:
            print("\n-s <id> 로 진단할 서버를 지정하세요.")
        return

    server = get_server_by_id(args.server)
    if not server:
        print(f"[!] server_id={args.server} 없음")
        return list_servers()

    label = get_server_label(server)
    am = normalize_access_method(server.get('access_method'))
    io = ArsSshIO()

    print("=" * 60)
    print(f"ARS SSH 진단: {label} (id={args.server})")
    print("=" * 60)

    # 0) 설정 확인 ------------------------------------------------
    print("\n[0] 설정 확인")
    print(f"    type          : {server.get('type')}")
    print(f"    access_method : {am}")
    if am != 'ssh':
        print("    [!] access_method 가 'ssh' 가 아닙니다 → SSH 페처가 이 서버를 건너뜁니다.")
        print("        서버 관리에서 접근방식을 SSH 로 바꾸세요.")
        return
    target = io.ssh_target(server)
    print(f"    ssh target    : {target}")
    print(f"    ssh_port      : {server.get('ssh_port', 22)}")
    if not target:
        print("    [!] IP/호스트가 비어 SSH 대상 구성 실패")
        return

    paths = get_log_paths(server, 'inbound') or []
    print(f"    inbound paths : {len(paths)}개")
    for p in paths:
        print(f"      - {p}")
        if p.startswith('\\\\'):
            print("        [!] UNC 경로입니다. SSH 모드는 '서버 로컬 경로'여야 합니다.")
            print("            예: D:\\ARSLOG\\IS-IVR-{YYYY-MM-DD}.log")
    if not paths:
        print("    [!] inbound 로그 경로가 없습니다 → 검색/인덱싱 대상 0")
        return

    # 1) SSH 접속 -------------------------------------------------
    print("\n[1] SSH 접속 (whoami)")
    rc, out, err = io._runner(io._ssh_base(server) + [target, 'whoami'])
    print(f"    rc={rc} out={out.decode('utf-8','ignore').strip()[:60]!r}")
    if rc != 0:
        print(f"    [!] SSH 실패: {err[:200]}")
        print("        → 키 인증/방화벽(22)/OpenSSH 서버 활성화 확인")
        return

    # 2) PowerShell -----------------------------------------------
    print("\n[2] PowerShell EncodedCommand")
    rc, out, err = io._run_ps(server, "'PS_OK'")
    got = out.decode('utf-8', 'ignore').strip()
    print(f"    rc={rc} out={got[:40]!r}")
    if 'PS_OK' not in got:
        print(f"    [!] PowerShell 실행 실패: {err[:200]}")
        print("        → 원격 기본 셸이 cmd 인지, powershell 이 PATH 에 있는지 확인")
        return

    # 3~6) 오늘자 파일 대상 ---------------------------------------
    now = datetime.now()
    ds = now.strftime('%Y-%m-%d')
    tmpl = paths[0]
    cur = ArsLogFetcher._expand(tmpl, ds, now.hour) if '{HH}' in tmpl \
        else ArsLogFetcher._expand(tmpl, ds)
    print(f"\n[3] 경로 존재 확인 (오늘자)\n    {cur}")
    exists = io.exists(server, cur)
    print(f"    Test-Path → {exists}")
    if not exists:
        print("    [!] 파일이 없습니다. 원인 후보:")
        print("        - 경로 템플릿이 실제 서버 경로와 다름 (드라이브/폴더명)")
        print("        - {HH} 파일인데 시간대가 안 맞음")
        print("        - 서버에서 dir 로 실제 경로 확인 필요")
        # 폴더 목록 힌트
        import ntpath
        folder = ntpath.dirname(cur)
        rc, out, err = io._run_ps(
            server,
            f"Get-ChildItem -LiteralPath '{folder}' -ErrorAction SilentlyContinue "
            "| Select-Object -First 5 -ExpandProperty Name")
        listing = out.decode('utf-8', 'ignore').strip()
        print(f"    폴더({folder}) 샘플:\n      " + (listing.replace('\n', '\n      ') or '(조회 실패/비어있음)'))
        return

    print("\n[4] 파일 크기")
    size = io.file_size(server, cur)
    print(f"    file_size → {size}")
    if not size:
        print("    [!] 크기 0 또는 조회 실패")
        return

    print("\n[5] 부분 읽기 (앞 2KB)")
    blob = io.read_range(server, cur, 0, min(2048, size))
    if blob is None:
        print("    [!] read_range 실패 (권한/잠금 확인)")
        return
    print(f"    읽은 바이트: {len(blob)}")
    try:
        blob.decode('utf-8'); enc = 'utf-8'
    except UnicodeDecodeError:
        enc = 'cp949'
    print(f"    인코딩 판정: {enc}")
    for i, line in enumerate(blob.decode(enc, 'replace').splitlines()[:3]):
        print(f"      L{i+1}: {line[:90]}")

    print(f"\n[6] 패턴 검색 (Select-String) — 패턴={args.pattern!r}")
    for encoding in ('utf8', 'default'):
        lines, err = io.grep(server, [cur], args.pattern, regex=True, encoding=encoding)
        print(f"    -Encoding {encoding:<8} → {len(lines)}줄" + (f"  err={err[:80]}" if err else ""))
        for l in lines[:2]:
            print(f"        {l[:90]}")
    print("    * utf8 은 0줄인데 default 가 나오면 → 로그가 CP949.")
    print("      ars_ssh_fetcher.py 의 grep(encoding='default') 로 바꾸세요.")

    # 7) 인덱스 상태 ----------------------------------------------
    print("\n[7] 인덱스 상태")
    try:
        from ars_index_store import get_default_store
        store = get_default_store()
        st = store.stats()
        print(f"    전체 인덱스 콜 수: {st.get('total_calls')}")
        rows = store.search(cust_id=None, phone=None, start_date=ds, end_date=ds,
                            servers=[label]) if hasattr(store, 'search') else []
        print(f"    이 서버({label}) 오늘 인덱스 콜: {len(rows)}")
        if not rows:
            print("    [!] 인덱스가 비어 custId 검색은 결과가 안 나옵니다.")
            print("        → 인덱서가 이 서버를 SSH 로 tail 하는 중인지 확인 (앱 재기동 후 수 분 대기)")
    except Exception as e:
        print(f"    (인덱스 조회 생략: {e})")

    print("\n" + "=" * 60)
    print("진단 완료")


if __name__ == '__main__':
    main()
