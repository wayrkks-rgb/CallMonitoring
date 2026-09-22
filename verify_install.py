# -*- coding: utf-8 -*-
"""
verify_install.py — 반입한 파일이 실제로 적용됐는지 확인

폐쇄망으로 파일을 옮기다 보면 일부 폴더(특히 routes/)를 덮어쓰지 못하고
예전 파일이 그대로 남는 일이 생긴다. 그러면 '분명히 고쳤는데 증상이 그대로'
가 된다. 이 스크립트는 파일마다 '이 수정이 들어갔다면 반드시 있어야 할 표식'
을 찾아서, 어떤 파일이 옛날 것인지 짚어 준다.

사용법:
    python verify_install.py
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

# (파일, 있어야 할 표식, 없으면 뭐가 안 고쳐진 건지)
CHECKS = [
    ("routes/servers.py", "'updated': True",
     "서버 재등록이 갱신(upsert)으로 동작 — 없으면 '중복 호스트명'으로 막힌다"),
    ("routes/servers.py", "no-store",
     "서버 목록 캐시 금지 — 없으면 삭제 직후에도 옛 목록이 보인다"),
    ("routes/servers.py", "'stale': True",
     "낡은 화면 목록으로 엉뚱한 서버가 삭제되는 것 방지"),
    ("templates/index.html", "로그 경로가 없어 검색",
     "경로 없는 항목 경고 표시 — 없으면 유령 항목이 정상 서버처럼 보인다"),
    ("config_manager.py", "os.replace",
     "config.json 원자적 저장 — 없으면 동시 접근 시 설정이 깨진다"),
    ("config_manager.py", "_recover_from_backup",
     "config.json 자동 복구 — 없으면 한 번 깨지면 복구되지 않는다"),
    ("routes/search.py", "diag_servers.py",
     "설정 손상 시 검색이 원인 표시 — 없으면 조용히 '결과 없음'만 뜬다"),
    ("ars_ssh_fetcher.py", "ArsReadError",
     "원격 읽기 중단 감지 — 없으면 부분만 읽고 파일을 확정해 버린다"),
    ("ars_ssh_fetcher.py", "GREP_FILE_SEP",
     "패턴 검색 결과에 출처 파일 표시"),
    ("ars_indexer.py", "확정 보류",
     "끝까지 읽은 경우에만 확정 — 없으면 로그 뒷부분이 통째로 유실된다"),
    ("ars_indexer.py", "_unseal_if_grown",
     "확정된 파일이 더 커졌으면 자동 재개 — 없으면 수동 복구가 필요하다"),
    ("ars_index_store.py", "sealed_files",
     "확정분 전수 조회 — 없으면 --reset-partial 이 오늘자만 본다"),
    ("ars_index_store.py", "_ADD_COLUMNS",
     "scan_state.server 컬럼 보강"),
    ("vgw_monitor.py", "stop_ev",
     "VGW 모니터 정지 신호 분리 — 없으면 중단해도 계속 접속을 시도한다"),
    ("scenario_deploy.py", "ssh_identity",
     "시나리오 배포가 등록된 SSH 키를 사용 — 없으면 비밀번호를 묻고 멈춘다"),
    ("scenario_fetcher.py", "BatchMode=yes",
     "시나리오 수집이 비밀번호 대기로 멈추지 않도록"),
    ("scenario_deploy_diff.py", "_btn_key",
     "메뉴 버튼(BTNM) 개별 비교 — 없으면 버튼 변경이 하나만 잡힌다"),
    ("scenario_deploy_diff.py", "_WV_SEND",
     "szSendMenuData 직접 호출 화면도 비교 대상에 포함"),
    ("templates/deploy_diff.html", "EXPAND_ALL",
     "DIFF 파일을 기본 펼침 — 없으면 앞 3개만 보이고 나머지는 개수만 보인다"),
    ("ssh_fetcher.py", "with_filename",
     "AICC 패턴 검색 출처 파일 표시"),
    ("log_searcher.py", "split_filename",
     "AICC 검색 결과의 파일명 분리"),
    ("templates/index.html", "patternFileSummary",
     "패턴 검색 '출처 파일별 건수' 요약"),
    ("templates/index.html", "_pendingKeyPath",
     "등록 폼 초기화/키 경로 전달 — 없으면 이전 서버 값으로 등록된다"),
]


def find_running_apps():
    """실행 중인 웹앱(app.py)의 폴더를 찾는다.

    '파일은 덮어썼는데 증상 그대로'의 가장 흔한 원인은, 지금 돌고 있는 앱이
    파일을 덮어쓴 폴더가 '아닌' 다른 폴더에서 실행 중인 경우다. 스크립트를
    둔 폴더만 검사해서는 절대 알 수 없으므로 프로세스를 직접 찾는다.
    """
    try:
        import psutil
    except ImportError:
        return None            # psutil 없음 → 판정 불가
    found = []
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = p.info.get('cmdline') or []
            if not any('app.py' in str(c) for c in cmd):
                continue
            if not any('python' in str(c).lower() for c in cmd[:1] + [p.info.get('name') or '']):
                continue
            # app.py 의 실제 경로를 인자에서 뽑는다(상대경로면 cwd 기준)
            target = next((str(c) for c in cmd if 'app.py' in str(c)), '')
            try:
                cwd = p.cwd()
            except Exception:
                cwd = ''
            path = target if os.path.isabs(target) else os.path.join(cwd, target)
            found.append({'pid': p.info['pid'], 'dir': os.path.dirname(os.path.abspath(path)),
                          'cwd': cwd})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def main():
    print(f"이 스크립트가 검사하는 폴더 : {BASE}")

    running = find_running_apps()
    if running is None:
        print("실행 중인 앱 : (psutil 이 없어 프로세스 확인 불가)")
        print("               → 대신 list_routes.py 를 실행하세요")
    elif not running:
        print("실행 중인 앱 : 없음 (웹 서버가 꺼져 있습니다)")
    else:
        for r in running:
            same = os.path.normcase(os.path.abspath(r['dir'])) == \
                   os.path.normcase(os.path.abspath(BASE))
            mark = "같은 폴더" if same else "★ 다른 폴더 ★"
            print(f"실행 중인 앱 : PID {r['pid']}  {r['dir']}   [{mark}]")
            if not same:
                print("               ↑ 여기에 파일을 덮어써야 합니다!")
                print(f"               (지금 덮어쓴 곳: {BASE})")
    print()
    missing_files, stale = [], []

    by_file = {}
    for path, marker, why in CHECKS:
        by_file.setdefault(path, []).append((marker, why))

    for path, items in by_file.items():
        full = os.path.join(BASE, path.replace("/", os.sep))
        if not os.path.exists(full):
            print(f"  ★ 없음  {path}")
            missing_files.append(path)
            continue
        try:
            text = open(full, "r", encoding="utf-8", errors="replace").read()
        except OSError as e:
            print(f"  ★ 읽기 실패  {path} — {e}")
            missing_files.append(path)
            continue

        bad = [(m, w) for m, w in items if m not in text]
        mtime = os.path.getmtime(full)
        import datetime
        stamp = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        if bad:
            stale.append(path)
            print(f"  ★ 옛날 파일  {path}   (수정시각 {stamp})")
            for m, w in bad:
                print(f"        빠짐: {w}")
        else:
            print(f"     최신      {path}   (수정시각 {stamp})")

    # __pycache__ 가 소스보다 새것이면 혼동의 원인이 될 수 있어 같이 알려준다
    stale_pyc = []
    for root, dirs, files in os.walk(BASE):
        if os.path.basename(root) != "__pycache__":
            continue
        for f in files:
            if not f.endswith(".pyc"):
                continue
            src = os.path.join(os.path.dirname(root), f.split(".")[0] + ".py")
            pyc = os.path.join(root, f)
            if os.path.exists(src) and os.path.getmtime(pyc) > os.path.getmtime(src) + 1:
                stale_pyc.append(os.path.relpath(pyc, BASE))

    print()
    if not stale and not missing_files:
        print("  ▶ 모든 파일이 최신입니다.")
    else:
        print("  ▶ 아래 파일을 다시 덮어쓰세요:")
        for p in stale + missing_files:
            print(f"      {p}")
        print("\n    ※ zip 을 풀 때 하위 폴더(routes/, templates/, static/)까지")
        print("       통째로 덮어쓰는지 확인하세요. 최상위 .py 만 복사하면")
        print("       routes/ 안의 수정이 반영되지 않습니다.")

    if stale_pyc:
        print(f"\n  ※ 소스보다 새로운 .pyc 가 {len(stale_pyc)}개 있습니다.")
        print("     보통은 문제가 없지만, 증상이 계속되면 __pycache__ 폴더를")
        print("     통째로 지우고 다시 기동해 보세요.")

    return 1 if (stale or missing_files) else 0


if __name__ == "__main__":
    sys.exit(main())
