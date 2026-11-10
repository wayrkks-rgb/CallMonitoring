# -*- coding: utf-8 -*-
r"""
diag_today.py — '오늘 · 방금 발생한 콜이 조회되지 않는다' 원인 진단

ARS · AICC 서버마다 세 가지를 나란히 보여 준다.
  1) 설정된 경로 템플릿으로 계산한 '오늘 파일' 이름과, 실제로 있는지 · 크기 · 수정시각
  2) 그 폴더에서 최근에 쓰인 파일 5개 (이름 규칙이 다르면 여기서 바로 보인다)
  3) ARS 는 색인 상태 — 오늘 파일을 어디까지 읽었는지, 오늘 색인된 콜 수 · 마지막 콜

판정 예
  - 오늘 파일 '없음' + 최근 파일 목록에 날짜 없는 이름(api.log 등)이 최신
        → 오늘 로그는 날짜 없는 이름에 쓰이고 자정에 넘어간다 (경로 템플릿 확인)
  - 오늘 파일은 있는데 색인 '읽은 위치'가 크기보다 한참 뒤
        → 색인이 밀림 (조회는 최근 32MB 를 직접 읽어 보충하지만 원인 확인 필요)
  - 오늘 색인 콜 0 + 색인 상태 '없음'
        → 색인기가 오늘 파일을 아예 못 봄 (앱 재기동 · verify_install.py 확인)

사용법:
    python diag_today.py            # 활성 서버 전체
    python diag_today.py -s 0       # 서버 하나만
"""
import os
import sys
import argparse
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from config_manager import load_config, get_log_paths, get_server_label  # noqa: E402
from ars_fetcher import ArsLogFetcher  # noqa: E402


def _fmt_size(n):
    if n is None:
        return '-'
    return f'{n / 1048576:.1f}MB' if n >= 1048576 else f'{n / 1024:.0f}KB'


def _today_paths(tmpl, now):
    today = now.strftime('%Y-%m-%d')
    if '{HH}' in tmpl:
        out = [ArsLogFetcher._expand(tmpl, today, now.hour)]
        prev = now - timedelta(hours=1)
        if prev.date() == now.date():
            out.append(ArsLogFetcher._expand(tmpl, today, prev.hour))
        return out
    return [ArsLogFetcher._expand(tmpl, today)]


# ── ARS (UNC) ─────────────────────────────────────────────
def _ars_unc(server, paths, now):
    from ars_fetcher import ArsConnectionManager
    conn = ArsConnectionManager()
    try:
        res = conn.connect_for_paths(paths)
        for h, (ok, err) in res.items():
            print(f"  UNC 연결 {h}: {'OK' if ok else '실패'} {'' if ok else err}")
        for tmpl in paths:
            print(f"  템플릿: {tmpl}")
            for p in _today_paths(tmpl, now):
                try:
                    st = os.stat(p)
                    print(f"    오늘 파일 {os.path.basename(p)} : 있음 {_fmt_size(st.st_size)} "
                          f"수정 {datetime.fromtimestamp(st.st_mtime):%H:%M:%S}")
                except OSError as e:
                    print(f"    오늘 파일 {os.path.basename(p)} : 없음 ({e.__class__.__name__})")
            d = os.path.dirname(tmpl.replace('{HH}', '00'))
            d = ArsLogFetcher._expand(d, now.strftime('%Y-%m-%d'))
            try:
                ents = sorted((e for e in os.scandir(d) if e.is_file()),
                              key=lambda e: e.stat().st_mtime, reverse=True)[:5]
                print(f"    폴더 최근 파일 ({d}):")
                for e in ents:
                    st = e.stat()
                    print(f"      {datetime.fromtimestamp(st.st_mtime):%m-%d %H:%M:%S}  "
                          f"{_fmt_size(st.st_size):>8}  {e.name}")
            except OSError as e:
                print(f"    폴더 목록 실패: {d} ({e})")
    finally:
        conn.disconnect_all()


# ── ARS (SSH) ─────────────────────────────────────────────
def _ars_ssh(server, paths, now):
    from ars_ssh_fetcher import ArsSshIO
    io = ArsSshIO()
    for tmpl in paths:
        print(f"  템플릿: {tmpl}")
        for p in _today_paths(tmpl, now):
            size, status = io.stat(server, p)
            print(f"    오늘 파일 {p.split(chr(92))[-1]} : "
                  f"{'있음 ' + _fmt_size(size) if status == 'ok' else status}")
        d = tmpl.rsplit('\\', 1)[0]
        d = ArsLogFetcher._expand(d, now.strftime('%Y-%m-%d'))
        script = (f"Get-ChildItem -LiteralPath '{d}' -File | Sort-Object LastWriteTime "
                  f"-Descending | Select-Object -First 5 | ForEach-Object {{ "
                  f"[Console]::Out.WriteLine($_.LastWriteTime.ToString('MM-dd HH:mm:ss') + "
                  f"'  ' + $_.Length + '  ' + $_.Name) }}")
        rc, out, err = io._run_ps(server, script)
        print(f"    폴더 최근 파일 ({d}):")
        if rc != 0:
            print(f"      목록 실패: {(err or '').strip()[:160]}")
        for ln in out.decode('utf-8', 'replace').splitlines():
            if ln.strip():
                print(f"      {ln}")


# ── AICC (SSH, 리눅스) ────────────────────────────────────
def _aicc(server, now):
    from ssh_fetcher import OpenSSHLogFetcher, active_log_variant
    f = OpenSSHLogFetcher()
    target = f._build_ssh_target(server)
    today = now.strftime('%Y-%m-%d')
    for purpose in ('inbound', 'outbound'):
        for tmpl in get_log_paths(server, purpose):
            files = f._build_file_patterns([tmpl], [today])
            act = active_log_variant(tmpl)
            print(f"  [{purpose}] 템플릿: {tmpl}")
            d = os.path.dirname(f._expand_placeholders(tmpl, today).replace('{HH}', '*'))
            cmd = f._build_ssh_cmd(server, target)
            listing = ' ; '.join(
                [f"ls -l --time-style=+%m-%d_%H:%M:%S {p} 2>/dev/null || echo '없음 {p}'"
                 for p in files]
                + [f"echo '--- 폴더 최근 파일 ({d})'", f"ls -lt --time-style=+%m-%d_%H:%M:%S {d} 2>&1 | head -6"])
            cmd.append(listing)
            lines, err = f._execute_ssh(cmd, timeout=20, ok_codes=(0, 1, 2))
            if err:
                print(f"    접속/실행 오류: {err}")
                continue
            for ln in lines:
                print(f"    {ln}")
            if act:
                print(f"    ※ 오늘 쓰이는 중일 수 있는 날짜 없는 이름도 검색함: {act}")


def _index_state(paths, now):
    try:
        from ars_index_store import get_default_store
        store = get_default_store()
    except Exception as e:
        print(f"  색인 DB 열기 실패: {e}")
        return
    for tmpl in paths:
        for p in _today_paths(tmpl, now):
            st = store.get_scan_state(p)
            if not st:
                print(f"    색인 상태 {os.path.basename(p)}: 없음 (색인기가 이 파일을 읽은 적 없음)")
            else:
                print(f"    색인 상태 {os.path.basename(p)}: 읽은 위치 {_fmt_size(st.get('last_offset'))} · "
                      f"미완료 시작 {_fmt_size(st.get('pending_offset'))} · "
                      f"확정={st.get('sealed')} · 갱신 {st.get('updated_at')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-s', '--server', type=int, default=None)
    args = ap.parse_args()
    cfg = load_config()
    if not cfg:
        print('config.json 을 읽을 수 없습니다.')
        return 1
    now = datetime.now()
    print(f"기준 시각 : {now:%Y-%m-%d %H:%M:%S} (이 PC 시계 — 서버 시계와 다르면 오늘 파일 계산이 어긋남)")
    try:
        from ars_index_store import get_default_store
        s = get_default_store().stats()
        print(f"ARS 색인 : 전체 {s['indexed_calls']}콜 · 오늘 {s['today_calls']}콜 "
              f"(마지막 콜 {s['today_last_call']}) · 마지막 색인 {s['last_indexed_at']}")
    except Exception as e:
        print(f"ARS 색인 DB 확인 실패: {e}")

    for i, srv in enumerate(cfg.get('remote_servers', [])):
        if args.server is not None and i != args.server:
            continue
        if not srv.get('enabled', True):
            continue
        typ = (srv.get('type') or 'AICC').upper()
        label = get_server_label(srv)
        print('\n' + '=' * 72)
        if typ == 'ARS':
            paths = get_log_paths(srv, 'inbound')
            how = (srv.get('access_method') or 'unc').lower()
            print(f"[{i}] {label}  ARS ({how})")
            if not paths:
                print('  인바운드 로그 경로 없음')
                continue
            try:
                (_ars_ssh if how == 'ssh' else _ars_unc)(srv, paths, now)
            except Exception as e:
                print(f"  확인 실패: {e}")
            _index_state(paths, now)
        else:
            print(f"[{i}] {label}  AICC")
            try:
                _aicc(srv, now)
            except Exception as e:
                print(f"  확인 실패: {e}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
