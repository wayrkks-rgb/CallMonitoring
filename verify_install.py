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
    ("scenario_deploy_diff.py", "_var_changes",
     "변수 단위 변경 비교 — 없으면 줄 diff 만 보여 의도를 읽기 어렵다"),
    ("scenario_deploy_diff.py", "CACHE_VERSION",
     "스냅샷 캐시 버전 — 없으면 파서를 고쳐도 예전 캐시가 재사용된다"),
    ("templates/deploy_diff.html", "블록 전문 보기",
     "블록 전문/연관 시나리오 표시"),
    ("scenario_deploy.py", "REPORT_VERSION",
     "리포트 캐시 버전 — 없으면 분석기를 고쳐도 예전 리포트가 나온다"),
    ("scenario_deploy_routes.py", "no-store",
     "DIFF 화면 캐시 금지 + templates 경로 우선순위"),
    ("routes/search.py", "PATTERN_BUDGET_SEC",
     "패턴 검색 제한 시간/병렬 — 없으면 서버 수만큼 시간이 곱해진다"),
    ("templates/index.html", "PATTERN_BUDGET",
     "패턴 검색 경과 시간 표시 + 시간 초과 안내"),
    ("templates/deploy_diff.html", "PAGE_VERSION",
     "DIFF 화면 버전 표시 — 브라우저가 예전 화면을 쓰는지 확인용"),
    ("scenario_deploy_routes.py", "selftest",
     "DIFF 자가진단 (/api/deploy/selftest)"),
    ("scenario_deploy_diff.py", "_strip_ns",
     "XML 네임스페이스/구조 관용 파싱 — 없으면 '블록 0개'가 된다"),
    ("scenario_deploy_routes.py", "xmlprobe",
     "시나리오 XML 구조 확인 (/api/deploy/xmlprobe)"),
    ("scenario_deploy.py", "scn_exts",
     "비교 대상 폴더/확장자 설정 — 없으면 OUTPUT 의 .dxml 만 본다"),
    ("scenario_deploy_diff.py", "_NODE_TAGS",
     "<scenario><block> 형식 지원 — 없으면 블록이 0개로 나온다"),
    ("ssh_fetcher.py", "with_filename",
     "AICC 패턴 검색 출처 파일 표시"),
    ("log_searcher.py", "split_filename",
     "AICC 검색 결과의 파일명 분리"),
    ("templates/index.html", "patternFileSummary",
     "패턴 검색 '출처 파일별 건수' 요약"),
    ("templates/index.html", "_pendingKeyPath",
     "등록 폼 초기화/키 경로 전달 — 없으면 이전 서버 값으로 등록된다"),
    ("routes/precheck.py", "_SCR_CODE",
     "보이는ARS 화면코드 접두사 제한 해제 — 없으면 화면이 하나도 안 잡힌다"),
    ("routes/precheck.py", "_topo_link",
     "로그 → FLOW 뷰어 딥링크(시작/종료 단계)"),
    ("templates/index.html", "inRange",
     "구간별 '화면으로 보기'를 시각 범위로 매칭 — 없으면 같은 화면만 되풀이된다"),
    ("templates/topology.html", "_deepParams",
     "FLOW 뷰어 딥링크 수신 — 없으면 링크를 눌러도 첫 화면만 열린다"),
    ("templates/topology.html", "deepHighlight",
     "연결된 블록으로 이동·표시"),
    ("scenario_store.py", '"entry": entry',
     "블록 위치에 진입점 포함 — 없으면 어느 업무 트리인지 못 찾는다"),
    ("templates/deploy_diff.html", "flowLink",
     "DIFF 에서 변경 블록을 FLOW 뷰어로 연결"),
    ("scenario_deploy.py", '"viewer_env": cfg.get("viewer_env")',
     "DIFF 리포트에 뷰어 환경 이름 포함 — 없으면 FLOW 링크가 안 생긴다"),
    ("scenario_boot.py", "구성도 준비 시작",
     "구성도 준비 진행 로그 — 없으면 멈춘 건지 만드는 중인지 알 수 없다"),
    ("scenario_boot.py", "_keylock",
     "같은 구성도를 동시에 두 번 만들지 않도록 — 없으면 첫 로딩이 두 배로 걸린다"),
    ("routes/topology.py", "api_warm",
     "구성도 준비 상태 API (/api/topology/warm)"),
    ("templates/topology.html", "warmMsg",
     "FLOW 뷰어에 '구성도 준비 중 %' 표시 + 완료 시 자동 갱신"),
    ("scenario_deploy_diff.py", "find_broken_refs",
     "끊어진 이동 검출 — 없으면 존재하지 않는 시나리오 · 블록 호출을 못 잡는다"),
    ("templates/deploy_diff.html", "brokenHtml",
     "DIFF 상단 '끊어진 이동 — 배포 전 수정 필요' 경고"),
    ("diag_broken_links.py", "find_broken_refs",
     "끊어진 이동 전수 점검 스크립트"),
    ("diag_call_volume.py", "영향 통화",
     "시간당 콜 수 · 장애 1건당 영향 통화 계산 스크립트"),
    ("scenario_store.py", "def locator_index",
     "업무 위치 인덱스 1회 생성 — 없으면 과거 폴더가 많이 다를 때 비교가 수십 분"),
    ("scenario_deploy.py", "업무 위치 인덱스",
     "배포 비교에서 위치 인덱스 재사용 + 단계별 소요 시간 로그"),
    ("scenario_deploy_diff.py", "inbound = {}",
     "연관 관계 역방향 인덱스 — 없으면 변경 파일 수 x 전체 블록 만큼 반복"),
    ("ars_ssh_fetcher.py", "_ENC_DETECT_PS",
     "패턴 검색 파일별 인코딩 판정 — 없으면 CP949 로그의 한글이 '�' 로 깨진다"),
    ("ars_fetcher.py", "class LazyDecoder",
     "앞부분이 영문뿐인 CP949 로그도 한글을 바르게 읽기"),
    ("ars_indexer.py", "LazyDecoder()",
     "색인기 인코딩 지연 판정 — 없으면 뒤쪽 한글이 깨진 채 색인된다"),
    ("ars_indexer.py", "def _live_attach",
     "오늘 로그 우선 — 라이브가 끝부분부터 붙고 앞부분은 우선 작업으로 채움"),
    ("ars_indexer.py", "_live_ready.wait",
     "백필은 라이브 첫 회차 뒤에 시작 — 없으면 과거 로그가 오늘 로그를 밀어낸다"),
    ("ars_fetcher.py", "def close_idle",
     "닫는 줄 없이 끝난 콜 자동 마감 — 없으면 매 회차 몇 시간치를 다시 읽는다"),
    ("app.py", "today_gap_remaining",
     "/ars-index-status 에 라이브 · 오늘 앞부분 진행 표시"),
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
