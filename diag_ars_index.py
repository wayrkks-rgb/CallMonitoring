# -*- coding: utf-8 -*-
"""
diag_ars_index.py — "ARS 인바운드 오늘자 로그가 안 나온다" 원인 진단

웹 서버를 띄우지 않고 단독 실행한다. 인덱서가 오늘 파일을 어디까지 읽었는지,
파일이 실제로 존재/증가하는지, 색인 DB 에 오늘 콜이 들어왔는지를 한 번에 본다.

사용법 (config.json 과 같은 폴더에서):
    python diag_ars_index.py
    python diag_ars_index.py --hours 3        # 최근 3시간 파일까지 확인
    python diag_ars_index.py --phone 01012345678
    python diag_ars_index.py --unseal         # 잘못 확정된 오늘 파일 해제(복구)
"""
import os
import sys
import argparse
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)


def _hr(t=""):
    print("\n" + "=" * 72)
    if t:
        print(f" {t}")
        print("=" * 72)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=2, help="확인할 최근 시간 수 (기본 2)")
    ap.add_argument("--phone", default=None, help="이 번호로 오늘 검색까지 시도")
    ap.add_argument("--cust-id", default=None)
    ap.add_argument("--unseal", action="store_true",
                    help="오늘자 파일의 확정(sealed) 플래그 해제")
    args = ap.parse_args()

    from config_manager import get_enabled_servers, get_log_paths, get_server_label
    from ars_fetcher import ArsLogFetcher
    from ars_index_store import get_default_store, DEFAULT_DB_PATH

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    print(f"진단 시각 : {now:%Y-%m-%d %H:%M:%S}  (오늘 = {today})")
    print(f"색인 DB   : {DEFAULT_DB_PATH}"
          f"  {'있음' if os.path.exists(DEFAULT_DB_PATH) else '★ 없음 ★'}")

    store = get_default_store()

    # ── 1) 대상 서버 / 경로 ────────────────────────────────
    # 색인기는 get_enabled_servers(purpose='inbound') 로 대상을 고른다.
    # 즉 '인바운드' 경로가 비어 있는 서버는 조용히 제외된다 — 서버마다
    # 되고 안 되고가 갈리는 가장 흔한 원인이라 전체를 나열해 사유를 보여준다.
    _hr("1. ARS 서버 전체와 색인 대상 여부")
    all_ars = get_enabled_servers(server_type="ARS")
    if not all_ars:
        print("★ 활성화된 ARS 서버가 없습니다 (type=ARS · enabled=true 확인)")
        return

    targets = []
    for idx, srv in all_ars:
        label = get_server_label(srv)
        am = (srv.get("access_method") or "unc")
        inb = get_log_paths(srv, "inbound")
        outb = get_log_paths(srv, "outbound")
        mark = "색인 대상" if inb else "★ 색인 제외 ★"
        print(f"\n  [{idx}] {label}   접근={am}   {mark}")
        if am == "ssh":
            from ars_ssh_fetcher import ArsSshIO
            tgt = ArsSshIO.ssh_target(srv)
            print(f"        SSH  {tgt}  포트={srv.get('ssh_port', 22)}  "
                  f"키={srv.get('ssh_key_path') or '(없음)'}")
        print(f"        인바운드 경로 {len(inb)}개 / 아웃바운드 {len(outb)}개")
        for p in inb:
            print(f"          IN  {p}   [{'시간별' if '{HH}' in p else '일별'}]")
        if not inb:
            print("        ↑ 인바운드 경로가 비어 있어 색인기가 이 서버를 건너뜁니다.")
            if outb:
                print("          아웃바운드에만 등록돼 있습니다:")
                for p in outb:
                    print(f"          OUT {p}")
                print("          → 서버 관리 > 로그 경로에서 '인바운드'로도 등록하세요.")
        else:
            targets.append((idx, srv))

    if not targets:
        print("\n★ 인바운드 경로가 등록된 ARS 서버가 하나도 없습니다.")
        return

    # ── 2) 오늘 파일 존재/크기 vs 인덱서가 읽은 위치 ────────
    _hr("2. 오늘 파일 상태 (존재/크기) vs 인덱서 진행 위치")
    print("  size  = 지금 파일 크기,  last  = 인덱서가 마지막으로 읽은 위치")
    print("  뒤처짐 = size - last  (계속 0 근처여야 정상)")
    print("  sealed=1 이면 인덱서가 이 파일을 더 이상 읽지 않습니다 ★\n")

    conn = None
    any_today_file = False
    today_paths = []          # 5번(복구)에서 재사용 — 파일명 패턴 추측 없이 정확히
    for idx, srv in targets:
        label = get_server_label(srv)
        is_ssh = (srv.get("access_method") or "unc") == "ssh"
        if not is_ssh and conn is None:
            from ars_fetcher import ArsConnectionManager
            conn = ArsConnectionManager()

        for tmpl in get_log_paths(srv, "inbound"):
            has_hh = "{HH}" in tmpl
            slots = []
            if has_hh:
                for k in range(args.hours):
                    t = now - timedelta(hours=k)
                    slots.append((ArsLogFetcher._expand(tmpl, t.strftime("%Y-%m-%d"), t.hour),
                                  f"{t:%m-%d %H}시"))
            else:
                slots.append((ArsLogFetcher._expand(tmpl, today), "오늘(일별)"))

            today_paths.extend(p for p, _ in slots)
            for path, tag in slots:
                if not is_ssh:
                    try:
                        res = conn.connect_for_paths([path])
                        bad = [e for ok, e in res.values() if not ok]
                        if bad:
                            print(f"  {label} {tag}: ★ UNC 연결 실패 — {bad[0]}")
                            continue
                    except Exception as e:
                        print(f"  {label} {tag}: ★ UNC 연결 예외 — {e}")
                        continue
                    size, why = None, None
                    try:
                        size = os.path.getsize(path)
                    except OSError as e:
                        why = f"{type(e).__name__}: {e}"
                else:
                    # file_size 는 실패 사유를 삼키므로 원시 호출로 rc/stderr 를 본다
                    from ars_ssh_fetcher import ArsSshIO
                    io = ArsSshIO()
                    rc, out, serr = io._run_ps(
                        srv, f"$ErrorActionPreference='Stop';"
                             f"(Get-Item -LiteralPath '{path}').Length")
                    txt = out.decode("ascii", "ignore").strip()
                    size = int(txt) if txt.isdigit() else None
                    why = None if size is not None else \
                        f"rc={rc} {(serr or txt or '응답 없음').strip()[:160]}"

                st = store.get_scan_state(path)
                name = os.path.basename(path)
                if size is None:
                    print(f"  {label} {tag}  {name}: ★ 읽기 실패")
                    print(f"        경로={path}")
                    print(f"        사유={why}")
                    continue
                any_today_file = True
                if st is None:
                    print(f"  {label} {tag}  {name}: size={size:,}  "
                          f"★ 인덱서가 한 번도 읽지 않음(scan_state 없음)")
                else:
                    lag = size - (st.get("last_offset") or 0)
                    flag = " ★ sealed(더 이상 안 읽음)" if st.get("sealed") else ""
                    print(f"  {label} {tag}  {name}: size={size:,} "
                          f"last={st.get('last_offset'):,} 뒤처짐={lag:,}"
                          f"  갱신={st.get('updated_at')}{flag}")

    if conn:
        try:
            conn.disconnect_all()
        except Exception:
            pass
    if not any_today_file:
        print("\n  ★ 오늘 파일을 하나도 찾지 못했습니다 → 경로 템플릿/권한 문제일 가능성이 큽니다.")

    # ── 3) 색인 DB 에 오늘 콜이 있는지 ──────────────────────
    _hr("3. 색인 DB 현황")
    stt = store.stats()
    for k, v in stt.items():
        print(f"  {k:18} = {v}")
    # ★ 서버별 색인 현황 — '이 서버 콜이 DB 에 들어와 있는가'가 수집/조회 문제를 가른다
    with store._lock:
        per = store._conn.execute(
            "SELECT server, COUNT(*) n, "
            "       SUM(CASE WHEN start_time >= ? THEN 1 ELSE 0 END) today_n, "
            "       MAX(start_time) last_call "
            "FROM calls GROUP BY server ORDER BY server",
            (f"{today} 00:00:00",)).fetchall()
    print("\n  서버별 색인 현황:")
    print(f"    {'서버':<20} {'전체':>8} {'오늘':>7}   마지막 콜")
    indexed = set()
    for r in per:
        indexed.add(r["server"])
        flag = "" if r["today_n"] else "   ★ 오늘 0건"
        print(f"    {r['server']:<20} {r['n']:>8,} {r['today_n']:>7,}   {r['last_call']}{flag}")
    # 색인 대상인데 DB 에 한 줄도 없는 서버 = 수집 단계에서 막힌 서버
    missing = [get_server_label(s) for _, s in targets if get_server_label(s) not in indexed]
    if missing:
        print(f"\n    ★ 색인 대상이지만 DB 에 콜이 하나도 없는 서버: {', '.join(missing)}")
        print("      → 조회가 아니라 '수집'에서 막힌 것입니다. 위 2번의 사유를 보세요.")

    with store._lock:
        rows = store._conn.execute(
            "SELECT server, start_time, ucid, phone, cust_id FROM calls "
            "WHERE start_time >= ? ORDER BY start_time DESC LIMIT 5",
            (f"{today} 00:00:00",)).fetchall()
        unsealed = store._conn.execute(
            "SELECT file_path, last_offset, updated_at FROM scan_state "
            "WHERE sealed = 0 ORDER BY updated_at DESC LIMIT 5").fetchall()
    print(f"\n  오늘 색인된 콜 최신 {len(rows)}건:")
    for r in rows:
        print(f"    {r['start_time']}  {r['server']}  ucid={r['ucid']} "
              f"phone={r['phone']} cust={r['cust_id']}")
    if not rows:
        print("    (없음) ★ 오늘 콜이 하나도 색인되지 않았습니다")
    print(f"\n  현재 추적 중(sealed=0) 파일 {len(unsealed)}건:")
    for r in unsealed:
        print(f"    {os.path.basename(r['file_path'])}  last={r['last_offset']:,} "
              f"갱신={r['updated_at']}")
    if not unsealed:
        print("    (없음) ★ 라이브 추적 중인 파일이 없습니다 — 인덱서가 멈췄거나")
        print("           오늘 파일이 전부 확정(sealed)된 상태입니다")

    # ── 4) 실제 검색 재현 ──────────────────────────────────
    if args.phone or args.cust_id:
        _hr("4. 오늘 날짜로 실제 검색 재현")
        res = store.search(phone=args.phone, cust_id=args.cust_id,
                           start_date=today, end_date=today)
        print(f"  store.search(오늘) → {len(res)}건")
        for r in res[:5]:
            print(f"    {r['start_time']}  {r['server']}  ucid={r['ucid']}")
        allres = store.search(phone=args.phone, cust_id=args.cust_id)
        print(f"  store.search(전체기간) → {len(allres)}건")
        if allres and not res:
            print("  ★ 전체기간엔 있는데 오늘만 없음 → 색인 지연/중단 쪽을 보세요")
            print(f"    가장 최근 콜: {allres[-1]['start_time']}")

    # ── 5) 복구 ────────────────────────────────────────────
    if args.unseal:
        _hr("5. 오늘자 파일 확정 해제")
        # 파일명 규칙({YYYY}-{MMDD}-{HH} 등)이 설정마다 다르므로 문자열 추측 대신
        # 2번에서 설정으로 계산한 실제 경로만 대상으로 한다.
        n = 0
        for path in dict.fromkeys(today_paths):
            st = store.get_scan_state(path)
            if not st or not st.get("sealed"):
                continue
            store.set_scan_state(path, st.get("last_offset") or 0,
                                 st.get("pending_offset") or 0, sealed=0)
            print(f"  해제: {os.path.basename(path)} "
                  f"(last={st.get('last_offset')} 유지 — 처음부터 다시 읽지 않음)")
            n += 1
        if n:
            print(f"  총 {n}건 해제 — 웹 서버를 재기동하면 다시 읽기 시작합니다.")
        else:
            print("  해제할 파일 없음 (오늘자 파일 중 확정된 것이 없습니다)")

    _hr("판정 가이드")
    print("""  · 1번 '★ 색인 제외'        → 인바운드 경로 미등록. 서버 관리에서 등록
  · 2번 '읽기 실패' + 사유    → 경로 템플릿 / 권한 / SSH 접속 문제 (사유 참고)
  · 2번에서 'sealed' 표시    → 잘못 확정됨. --unseal 후 재기동
  · 2번 '뒤처짐'이 계속 커짐 → 인덱서 스레드 정지/오류 (logs/app.log 확인)
  · 2번 정상인데 3번이 0건   → 콜 경계 미검출 (call_start/WaitCall 패턴 불일치)
  · 3번에 콜이 있는데 화면 X → 검색 조건(서버 선택/날짜) 또는 조회 경로 문제

  ※ 서버마다 되고 안 되는 경우, 1번에서 되는 서버와 안 되는 서버의
    '접근' 방식과 '인바운드 경로' 줄을 나란히 비교해 보세요.

  ── 수집 문제인가, 조회 문제인가 ──────────────────────────
   3번 '서버별 색인 현황'에서 그 서버의 오늘 건수를 봅니다.
     오늘 0건  → 수집(색인)에서 막힘. 2번의 '사유=' 를 보세요.
     오늘 N건  → 수집은 정상. 화면에 안 나오면 조회 조건 문제이므로
                 --phone/--cust-id 로 4번 검색 재현을 돌려 비교하세요.""")


if __name__ == "__main__":
    main()
