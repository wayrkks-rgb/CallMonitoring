# -*- coding: utf-8 -*-
"""
ars_indexer.py — ARS 로그 증분 인덱서 (백그라운드 스레드)

동작:
  - 라이브 테일: 현재 시각(+직전 시각) 파일을 5초마다 증분 스캔 → 종료된 콜만 인덱싱
  - 백필: 과거 로그를 최신순으로 1파일씩 1회 색인 (백그라운드, 라이브 방해 안 함)
  - 정리: 하루 1회 30일 경과분 삭제

핵심:
  - append-only 전제 → 파일별 pending_offset 부터 이어읽기 (매번 6GB 풀스캔 X)
  - 콜 조립은 검증된 _ChannelStateMachine 재사용 (key=None → 전량 인덱싱)
  - 바이트 offset 추적 → 검색 시 그 구간만 읽어 원본 그대로(디버그 포함) 복원
  - '종료된 콜'만 저장. 아직 안 끝난 콜은 pending 으로 남겨 다음 사이클에 확정
  - 디렉터리 리스팅 안 함(파일 1000개 무관) — 파일명은 패턴으로 계산
"""

import os
import time
import logging
import threading
from datetime import datetime, timedelta
from collections import deque

from ars_fetcher import _ChannelStateMachine, ArsConnectionManager, extract_host, ArsLogFetcher
from ars_ssh_fetcher import ArsSshIO
from config_manager import get_enabled_servers, get_log_paths, get_server_label

logger = logging.getLogger(__name__)

# 백필 버스트: 이 개수만큼 연속 처리 후 라이브 테일 1회 끼워넣음(최신 콜 신선도 유지)
_BACKFILL_BURST = 10

# 한 파일이 접속 오류로 계속 실패할 때 재시도 상한 (무한 루프 방지)
_BACKFILL_MAX_RETRY = 5


def _detect_encoding(sample):
    """UTF-8 우선, 실패 시 CP949."""
    try:
        sample.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError as e:
        if e.start >= len(sample) - 3:
            return 'utf-8'
        return 'cp949'


class ArsIndexer:
    BACKFILL_MAX_RETRY = _BACKFILL_MAX_RETRY

    def __init__(self, store, server_ids=None, poll_interval=5, max_interval=10,
                 conn_manager=None, auth_path=None, retention_days=30,
                 backfill_days=1):
        """
        store: ArsIndexStore
        backfill_days: 시작 시 과거 며칠치를 1회 색인할지 (최신순). 0이면 백필 안 함.
        라이브 테일과 백필은 별도 스레드로 동작 → 백필이 오늘 실시간 색인을 막지 않음.
        """
        self.store = store
        self.server_ids = server_ids
        self.poll = poll_interval
        self.max_interval = max_interval
        # 라이브/백필 각자 연결 매니저 (같은 계정으로 각자 세션; 이미 연결 시 성공 처리)
        if conn_manager is not None:
            self.conn = conn_manager        # 주입(테스트) 시 공유
            self.conn_bf = conn_manager
        else:
            self.conn = ArsConnectionManager(auth_path=auth_path)
            self.conn_bf = ArsConnectionManager(auth_path=auth_path)
        self.retention_days = retention_days
        self.backfill_days = backfill_days

        # SSH 방식 ARS 서버용 IO (access_method=='ssh')
        self._ssh_io = ArsSshIO()

        self._stop = threading.Event()
        self._t_live = None
        self._t_bf = None
        self._backfill = deque()
        self._bf_retry = {}          # path -> 재시도 횟수 (무한 재시도 방지)
        self._backfill_inited = False
        self._backfill_done = False
        self._last_purge = None
        self.last_error = None

    # ── 스레드 제어 ───────────────────────────────────────
    def start(self):
        self._stop.clear()
        if not (self._t_live and self._t_live.is_alive()):
            self._t_live = threading.Thread(target=self._run_live, name='ars-live', daemon=True)
            self._t_live.start()
        if self.backfill_days and not (self._t_bf and self._t_bf.is_alive()):
            self._t_bf = threading.Thread(target=self._run_backfill, name='ars-backfill', daemon=True)
            self._t_bf.start()
        logger.info("ARS 인덱서 시작 (라이브 %ss + 백필 %s일 별도 스레드)",
                    self.poll, self.backfill_days)

    def stop(self):
        self._stop.set()
        for t in (self._t_live, self._t_bf):
            if t:
                t.join(timeout=self.poll + 2)
        for c in (self.conn, self.conn_bf):
            try:
                c.disconnect_all()
            except Exception:
                pass
        logger.info("ARS 인덱서 정지")

    # ── 라이브 테일 루프 (항상 5초, 백필과 독립) ───────────
    def _run_live(self):
        while not self._stop.is_set():
            worked = False
            try:
                worked = self._live_pass()
                self._maybe_purge(datetime.now())
            except Exception as e:
                self.last_error = str(e)
                logger.exception("라이브 테일 오류: %s", e)
            interval = self.poll if worked else self.max_interval
            self._stop.wait(interval)

    # ── 백필 루프 (별도 스레드, 최신순 연속 처리) ──────────
    def _run_backfill(self):
        # 큐 초기화 (최초 1회)
        try:
            self._init_backfill(datetime.now())
        except Exception as e:
            self.last_error = str(e)
            logger.exception("백필 초기화 오류: %s", e)
            return
        # 남은 파일을 연속 처리 (대기 없음). 완료되면 스레드 종료.
        while not self._stop.is_set() and self._backfill:
            try:
                r = self._do_one_backfill()
                if r == 'retry':
                    self._stop.wait(self.poll)  # 연결 실패 → 잠깐 쉬고 재시도
            except Exception as e:
                self.last_error = str(e)
                logger.exception("백필 처리 오류: %s", e)
                self._stop.wait(self.poll)  # 오류 시 잠깐 쉼
        self._backfill_done = True
        if not self._stop.is_set():
            logger.info("ARS 백필 완료 (%d일치)", self.backfill_days)

    def _targets(self):
        return get_enabled_servers(server_type='ARS', purpose='inbound',
                                   server_ids=self.server_ids)

    @staticmethod
    def _is_ssh(server):
        return (server.get('access_method') or 'unc') == 'ssh'

    def _live_pass(self):
        """라이브 테일 1회 — 모든 대상 서버의 현재/직전 시각 파일 증분 스캔."""
        targets = self._targets()
        if not targets:
            return False

        worked = False
        now = datetime.now()

        for _idx, server in targets:
            label = get_server_label(server)
            paths = get_log_paths(server, 'inbound')
            if not paths:
                continue
            if self._is_ssh(server):
                # SSH 모드: UNC 연결 불필요. 파일별 file_size/read_range 로 tail.
                for tmpl in paths:
                    worked |= self._tail_source(server, label, tmpl, now)
            else:
                # UNC 모드: 호스트 연결 보장 (연결은 캐시되어 재호출 저렴)
                ok_map = self.conn.connect_for_paths(paths)
                if any(not ok for ok, _ in ok_map.values()):
                    continue  # 이 서버는 이번 패스 스킵
                for tmpl in paths:
                    worked |= self._tail_source(server, label, tmpl, now)

        return worked

    # ── 라이브 테일 ───────────────────────────────────────
    def _tail_source(self, server, label, tmpl, now):
        worked = False
        cur_date = now.strftime('%Y-%m-%d')
        cur_path = ArsLogFetcher._expand(tmpl, cur_date, now.hour) if '{HH}' in tmpl \
            else ArsLogFetcher._expand(tmpl, cur_date)

        # 라이브 대상 파일은 정의상 확정될 수 없다. 과거 버그/비정상 종료로
        # 확정돼 있으면 여기서 풀어준다(확정 상태면 _scan_and_store 가 곧바로
        # 빠져나가 오늘 로그가 영영 색인되지 않으므로 자가복구가 필요).
        st = self.store.get_scan_state(cur_path)
        if st and st.get('sealed'):
            logger.warning("확정된 라이브 파일 해제: %s", cur_path)
            self.store.set_scan_state(cur_path, st.get('last_offset') or 0,
                                      st.get('pending_offset') or 0, sealed=0)

        # 현재 시각 파일: 절대 seal 안 함
        worked |= self._scan_and_store(server, label, cur_path, cur_date, seal=False)

        # 직전 시각 파일: 정각 직후 늦게 쓰이는 로그를 흡수하다가, 2분 지나면 seal
        if '{HH}' in tmpl:
            prev = now - timedelta(hours=1)
            prev_path = ArsLogFetcher._expand(tmpl, prev.strftime('%Y-%m-%d'), prev.hour)
            st = self.store.get_scan_state(prev_path)
            if not st or not st.get('sealed'):
                seal = (now.minute >= 2)  # 정각+2분 지나면 확정
                worked |= self._scan_and_store(server, label, prev_path,
                                               prev.strftime('%Y-%m-%d'), seal=seal)
        else:
            # 일별 파일: 어제자를 자정 직후까지 마저 흡수한다(시간별의 '직전 시각'과 동일).
            # 이게 없으면 날짜가 바뀌는 순간 어제 파일은 라이브 테일 대상에서 빠지고
            # 백필 큐에도 없어(기동 시점의 '오늘'이라 제외됨) 마지막 구간이 유실된다.
            prev = now - timedelta(days=1)
            pds = prev.strftime('%Y-%m-%d')
            prev_path = ArsLogFetcher._expand(tmpl, pds)
            st = self.store.get_scan_state(prev_path)
            if not st or not st.get('sealed'):
                seal = (now.hour > 0 or now.minute >= 5)  # 자정+5분 지나면 확정
                worked |= self._scan_and_store(server, label, prev_path, pds, seal=seal)
        return worked

    def _scan_and_store(self, server, label, path, file_date, seal):
        st = self.store.get_scan_state(path)
        if st and st.get('sealed'):
            return False
        start = st['pending_offset'] if st else 0
        last = st['last_offset'] if st else 0

        # 파일 크기 확인(1 stat/원격조회). 안 커졌고 seal 아니면 스킵
        size, status = self._stat(server, path)
        if status == 'error':
            # 접속/권한 오류 — 확정하지 않는다. 확정해 버리면 복구 후에도
            # 이 파일을 영영 다시 읽지 않는다.
            return False
        if size is None:                 # status == 'nofile'
            if seal and st:              # 사라진 파일 → 확정 처리
                self.store.set_scan_state(path, last, start, sealed=1)
            return False
        if not seal and size <= last and (st is not None):
            return False  # 신규 데이터 없음

        res = self._scan_file(server, path, label, file_date, start, seal)
        if res is None:
            return False
        completed, pending, eof = res
        n = self.store.upsert_calls(completed) if completed else 0
        self.store.set_scan_state(path, last_offset=eof, pending_offset=pending,
                                  sealed=1 if seal else 0)
        if n:
            logger.debug("인덱싱 %s: %d콜 (%s)", os.path.basename(path), n, label)
        return bool(n)

    def _stat(self, server, path):
        """
        (size, status) — status: 'ok' | 'nofile' | 'error'

        '파일 없음'과 '접속/권한 오류'를 구분한다. 구분하지 않으면 SSH 인증이
        끊긴 동안 멀쩡한 파일이 '없는 파일'로 확정(sealed)되어, 인증을 복구해도
        그 구간이 영영 색인되지 않는다.
        """
        if self._is_ssh(server):
            return self._ssh_io.stat(server, path)
        try:
            return os.path.getsize(path), 'ok'
        except FileNotFoundError:
            return None, 'nofile'
        except OSError as e:
            logger.warning("파일 확인 실패 %s: %s", path, e)
            return None, 'error'

    def _size_of(self, server, path):
        """파일 크기 (없거나 오류면 None). 구분이 필요하면 _stat() 사용."""
        return self._stat(server, path)[0]

    def _scan_file(self, server, path, label, file_date, start_offset, seal):
        """[start_offset, EOF) 를 읽어 종료된 콜 추출. returns (calls, pending, eof) | None"""
        sm = _ChannelStateMachine(None)  # key 없음 → 전량
        offset = start_offset

        if self._is_ssh(server):
            # SSH 모드: [start_offset, EOF) 를 청크로 끊어 읽는다.
            # 한 번에 받으면 대용량 시간대에서 SSH 타임아웃이 나고, 이후 매
            # 폴링마다 같은 구간을 재시도하다 실패해 색인이 영구히 멈춘다.
            size = self._ssh_io.file_size(server, path)
            if size is None:
                return None
            enc = None
            carry = b''          # 청크 경계에서 잘린 마지막 줄
            src = os.path.basename(path)
            for _pos, chunk in self._ssh_io.read_chunks(server, path, start_offset, size):
                if enc is None:
                    enc = _detect_encoding(chunk[:65536])
                buf = carry + chunk
                nl = buf.rfind(b'\n')
                if nl < 0:                    # 아직 개행이 없음 → 다음 청크로 이월
                    carry = buf
                    continue
                body, carry = buf[:nl + 1], buf[nl + 1:]
                for raw in body.splitlines(keepends=True):
                    ls = offset
                    offset += len(raw)
                    sm.feed(raw.decode(enc, errors='replace'), source=src,
                            start_offset=ls, end_offset=offset)
            # 남은 꼬리(개행 없는 마지막 줄): 확정 파일이면 소비하고,
            # 아직 쓰이는 중이면 offset 을 전진시키지 않아 다음 회차에 다시 읽는다.
            if carry and seal:
                ls = offset
                offset += len(carry)
                sm.feed(carry.decode(enc or 'utf-8', errors='replace'), source=src,
                        start_offset=ls, end_offset=offset)
        else:
            # UNC 모드: 로컬/네트워크 파일 순차 읽기
            try:
                with open(path, 'rb') as f:
                    f.seek(start_offset)
                    sample = f.read(65536)
                    enc = _detect_encoding(sample) if sample else 'utf-8'
                    f.seek(start_offset)
                    for raw in f:
                        ls = offset
                        offset += len(raw)
                        line = raw.decode(enc, errors='replace')
                        sm.feed(line, source=os.path.basename(path),
                                start_offset=ls, end_offset=offset)
            except FileNotFoundError:
                return None
            except OSError as e:
                logger.warning("파일 읽기 오류 %s: %s", path, e)
                return None

        eof = offset
        if seal:
            sm.flush()  # 완료 파일: 남은 열린 콜 강제 마감

        completed = sm.emitted
        # pending = 아직 열린 콜의 최소 시작 offset, 없으면 EOF
        open_starts = [c['start_offset'] for c in sm.open_calls.values()
                       if c.get('start_offset') is not None]
        pending = min(open_starts) if open_starts else eof

        for c in completed:
            c['server'] = label
            c['file_path'] = path
            c['start_time'] = f"{file_date} {c['start_time']}" if c.get('start_time') else None
            c['end_time'] = f"{file_date} {c['end_time']}" if c.get('end_time') else None
        return completed, pending, eof

    # ── 백필 (과거 로그 최신순 1회) ───────────────────────
    def _init_backfill(self, now):
        self._backfill_inited = True
        targets = self._targets()
        items = []
        for _idx, server in targets:
            label = get_server_label(server)
            for tmpl in get_log_paths(server, 'inbound'):
                for d in range(self.backfill_days + 1):  # 오늘 포함 과거
                    day = (now - timedelta(days=d))
                    ds = day.strftime('%Y-%m-%d')
                    if '{HH}' in tmpl:
                        hours = range(now.hour, -1, -1) if d == 0 else range(23, -1, -1)
                        for hh in hours:
                            # 현재/직전 시각은 라이브 테일이 담당 → 백필 제외
                            if d == 0 and hh >= now.hour - 1:
                                continue
                            items.append((server, label, ArsLogFetcher._expand(tmpl, ds, hh), ds))
                    else:
                        # 일별 파일: 오늘자는 하루 종일 append 되므로 백필 대상이 아니다.
                        # 백필은 seal=1 로 확정하는데, 확정된 파일은 _scan_and_store 가
                        # 곧바로 return False 하므로 라이브 테일이 오늘 파일을 더 이상
                        # 읽지 않게 된다 → 기동 이후의 오늘 콜이 통째로 색인되지 않음.
                        if d == 0:
                            continue
                        items.append((server, label, ArsLogFetcher._expand(tmpl, ds), ds))
        # 최신순(리스트가 이미 최신→과거) 유지
        self._backfill = deque(items)
        logger.info("백필 대기: %d 파일", len(self._backfill))

    def _do_one_backfill(self):
        """returns: True(진행) | 'retry'(연결실패, 재시도 대기 필요)"""
        server, label, path, ds = self._backfill.popleft()
        st = self.store.get_scan_state(path)
        if st and st.get('sealed'):
            return True  # 이미 완료 → 진행(대기 불필요)

        if not self._is_ssh(server):
            # UNC 모드만 호스트 연결 보장
            ok_map = self.conn_bf.connect_for_paths([path])
            if any(not ok for ok, _ in ok_map.values()):
                self._backfill.append((server, label, path, ds))  # 뒤로 미뤄 재시도
                return 'retry'

        # 확정(seal) 전에 존재 여부를 먼저 판정한다.
        # 접속 오류를 '없는 파일'로 오인해 확정하면, 인증/네트워크를 복구해도
        # 그 구간이 영영 색인되지 않는다 (실제로 SSH 인증이 끊긴 동안 백필이
        # 지나간 파일들이 전부 sealed=1, offset=0 으로 박제됐다).
        def _retry():
            """확정하지 않고 뒤로 미룸. 계속 실패하면 큐에서 내려놓되 sealed 는 안 한다
            (다음 기동 때 다시 시도된다)."""
            n = self._bf_retry.get(path, 0) + 1
            self._bf_retry[path] = n
            if n <= self.BACKFILL_MAX_RETRY:
                self._backfill.append((server, label, path, ds))
            else:
                logger.warning("백필 포기(미확정, 다음 기동 시 재시도): %s", path)
            return 'retry'

        _size, status = self._stat(server, path)
        if status == 'error':
            return _retry()
        if status == 'nofile':
            self.store.set_scan_state(path, 0, 0, sealed=1)
            return True

        res = self._scan_file(server, path, label, ds, 0, seal=True)
        if res is None:      # 읽기 도중 실패 — 확정하지 않고 재시도
            return _retry()
        completed, pending, eof = res
        n = self.store.upsert_calls(completed) if completed else 0
        self.store.set_scan_state(path, last_offset=eof, pending_offset=eof, sealed=1)
        logger.debug("백필 %s: %d콜", os.path.basename(path), n)
        return True

    # ── 정리 ──────────────────────────────────────────────
    def _maybe_purge(self, now):
        today = now.strftime('%Y-%m-%d')
        if self._last_purge == today:
            return
        self._last_purge = today
        try:
            removed = self.store.purge_older_than(self.retention_days)
            if removed:
                logger.info("인덱스 정리: %d콜 삭제(%d일 경과)", removed, self.retention_days)
        except Exception as e:
            logger.warning("정리 오류: %s", e)
