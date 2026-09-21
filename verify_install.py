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
    ("ssh_fetcher.py", "with_filename",
     "AICC 패턴 검색 출처 파일 표시"),
    ("log_searcher.py", "split_filename",
     "AICC 검색 결과의 파일명 분리"),
    ("templates/index.html", "patternFileSummary",
     "패턴 검색 '출처 파일별 건수' 요약"),
    ("templates/index.html", "_pendingKeyPath",
     "등록 폼 초기화/키 경로 전달 — 없으면 이전 서버 값으로 등록된다"),
]


def main():
    print(f"설치 위치 : {BASE}\n")
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
