# -*- coding: utf-8 -*-
"""
ars_indexer.py — ARS 로그 증분 인덱서 (백그라운드 스레드)

동작:
  - 라이브 테일: 현재 시각(+직전 시각) 파일을 5초마다 증분 스캔 → 종료된 콜만 인덱싱
      · 회차 사이에 상태머신을 유지해 '새로 붙은 바이트'만 읽는다
      · 처음 붙을 때 많이 뒤처져 있으면 끝부분(최신)부터 읽고, 건너뛴 앞부분은
        우선 작업으로 따로 채운다 → 오늘 · 실시간 콜이 가장 먼저 보인다
  - 백필: 과거 로그를 최신순으로 1파일씩 1회 색인 (라이브 첫 회차가 끝난 뒤 시작)
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

from concurrent.futures import ThreadPoolExecutor

from ars_fetcher import _ChannelStateMachine, ArsConnectionManager, extract_host, ArsLogFetcher
from ars_fetcher import LazyDecoder, detect_encoding, _channel_of, RE_START
from ars_ssh_fetcher import ArsSshIO, ArsReadError
from config_manager import get_enabled_servers, get_log_paths, get_server_label

logger = logging.getLogger(__name__)

# 백필 버스트: 이 개수만큼 연속 처리 후 라이브 테일 1회 끼워넣음(최신 콜 신선도 유지)
_BACKFILL_BURST = 10

# 한 파일이 접속 오류로 계속 실패할 때 재시도 상한 (무한 루프 방지)
_BACKFILL_MAX_RETRY = 5

# 라이브 테일이 파일에 처음 붙을 때 이보다 뒤처져 있으면 끝부분부터 읽는다.
# (오늘자 일별 파일은 수백 MB — 앞에서부터 다 읽어야 최신 콜이 보이면 늦다)
LIVE_JUMP = 16 * 1024 * 1024

# 열린 콜 자동 마감 (초). 'WaitCall Success!' 없이 끝난 콜이 채널이 한동안
# 안 쓰이는 사이 몇 시간씩 열려 있으면, 색인에도 안 보이고 다시 읽을
# 시작점(pending)도 그 자리에 묶여 매 회차 몇 시간치를 다시 읽게 된다.
IDLE_CLOSE_ENDED = 120       # call_end 가 찍힌 뒤 조용하면
IDLE_CLOSE_OPEN = 1800       # call_end 도 없이 조용하면

# 백필은 라이브 첫 회차(오늘 최신 구간)가 끝난 뒤 시작한다. 라이브가 고장 나도
# 백필이 영영 멈추지 않도록 상한을 둔다.
LIVE_FIRST_WAIT = 600

# UNC 파일 읽기 단위 (SSH 의 READ_CHUNK 와 맞춘다 — 단위마다 색인 · 진행 저장)
UNC_BLOCK = 8 * 1024 * 1024

# 앞부분 채우기에서 경계에 걸친 콜을 마무리하려고 경계 뒤를 더 읽는 상한.
# 닫는 줄이 끝내 안 찍힌 콜이 있으면 파일 끝까지 읽게 되므로 상한을 둔다
# (보통 통화는 이 안에서 끝난다. 남은 콜은 그 시점까지로 마감).
DRAIN_MAX = 8 * 1024 * 1024


def _detect_encoding(sample):
    """UTF-8 우선, 실패 시 CP949. (처음 나오는 비-ASCII 바이트부터 판정)"""
    return detect_encoding(sample)


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
        self._prio = deque()         # 우선 작업: 라이브가 건너뛴 오늘 파일 앞부분
        self._live = {}              # path -> 라이브 상태 (상태머신 · 읽은 위치 · 남은 앞부분)
        self._live_ready = threading.Event()   # 라이브 첫 회차 완료
        self._bf_retry = {}          # path -> 재시도 횟수 (무한 재시도 방지)
        self._backfill_inited = False
        self._backfill_done = False
        self._last_purge = None
        self.last_error = None

    # ── 스레드 제어 ───────────────────────────────────────
    def start(self):
        self._stop.clear()
        self._live_ready.clear()
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
            finally:
                # 첫 회차가 끝나야(오늘 최신 구간 확보) 백필이 시작된다
                if not self._live_ready.is_set():
                    self._live_ready.set()
                    logger.info("ARS 라이브 첫 회차 완료 — 백필 시작")
            interval = self.poll if worked else self.max_interval
            self._stop.wait(interval)

    # ── 백필 루프 (별도 스레드) ────────────────────────────
    def _run_backfill(self):
        # 오늘 · 실시간 구간이 먼저다. 예전에는 기동하자마자 둘이 같이 돌아
        # 30일치 백필이 원격 읽기를 차지하는 동안 오늘 로그가 한참 늦게 들어왔다.
        self._live_ready.wait(timeout=LIVE_FIRST_WAIT)
        if self._stop.is_set():
            return
        try:
            self._init_backfill(datetime.now())
        except Exception as e:
            self.last_error = str(e)
            logger.exception("백필 초기화 오류: %s", e)
            return
        # 30일치면 파일이 수천 개고 원격 읽기는 한 번에 하나씩이라 몇 시간이
        # 걸린다. 진척을 남기지 않으면 '멈춘 건지 도는 건지' 알 수가 없다.
        # 오늘 파일 앞부분(우선 작업)이 있으면 언제나 그것부터 한다.
        total = len(self._backfill)
        done = 0
        last_report = time.time()
        while not self._stop.is_set():
            if not self._prio and not self._backfill:
                if not self._backfill_done:
                    self._backfill_done = True
                    logger.info("ARS 백필 완료 (%d일치)", self.backfill_days)
                self._stop.wait(self.max_interval)   # 우선 작업이 새로 생길 수 있다
                continue
            try:
                r = self._do_one_gap() if self._prio else self._do_one_backfill()
                if r == 'retry':
                    self._stop.wait(self.poll)  # 연결 실패 → 잠깐 쉬고 재시도
                else:
                    done += 1
                if time.time() - last_report >= 60:
                    last_report = time.time()
                    logger.info("ARS 백필 진행: %d/%d 처리, %d개 남음 (오늘 앞부분 %d건)",
                                done, total, len(self._backfill), len(self._prio))
            except Exception as e:
                self.last_error = str(e)
                logger.exception("백필 처리 오류: %s", e)
                self._stop.wait(self.poll)  # 오류 시 잠깐 쉼

    def _targets(self):
        return get_enabled_servers(server_type='ARS', purpose='inbound',
                                   server_ids=self.server_ids)

    @staticmethod
    def _is_ssh(server):
        return (server.get('access_method') or 'unc') == 'ssh'

    def _live_pass(self):
        """라이브 테일 1회 — 모든 대상 서버의 현재/직전 시각 파일 증분 스캔.

        서버마다 별개의 접속이라 동시에 돈다. 순차로 돌면 한 서버의 큰 구간을
        읽는 동안 나머지 서버의 오늘 로그가 전부 기다린다.
        """
        targets = self._targets()
        if not targets:
            return False
        now = datetime.now()

        def one(server):
            label = get_server_label(server)
            paths = get_log_paths(server, 'inbound')
            if not paths:
                return False
            if not self._is_ssh(server):
                # UNC 모드: 호스트 연결 보장 (연결은 캐시되어 재호출 저렴)
                ok_map = self.conn.connect_for_paths(paths)
                if any(not ok for ok, _ in ok_map.values()):
                    return False  # 이 서버는 이번 패스 스킵
            worked = False
            for tmpl in paths:
                try:
                    worked |= self._tail_source(server, label, tmpl, now)
                except Exception as e:
                    self.last_error = str(e)
                    logger.exception("라이브 테일 오류 %s: %s", label, e)
            return worked

        servers = [s for _, s in targets]
        with ThreadPoolExecutor(max_workers=max(1, min(len(servers), 8))) as ex:
            return any(list(ex.map(one, servers)))

    # ── 라이브 테일 ───────────────────────────────────────
    def _tail_source(self, server, label, tmpl, now):
        worked = False
        cur_date = now.strftime('%Y-%m-%d')
        cur_path = ArsLogFetcher._expand(tmpl, cur_date, now.hour) if '{HH}' in tmpl \
            else ArsLogFetcher._expand(tmpl, cur_date)

        # 라이브 대상 파일은 정의상 확정될 수 없다. 과거 버그/비정상 종료로
        # 확정돼 있으면 여기서 풀어준다(확정 상태면 곧바로 빠져나가 오늘 로그가
        # 영영 색인되지 않으므로 자가복구가 필요).
        st = self.store.get_scan_state(cur_path)
        if st and st.get('sealed'):
            logger.warning("확정된 라이브 파일 해제: %s", cur_path)
            self.store.set_scan_state(cur_path, st.get('last_offset') or 0,
                                      st.get('pending_offset') or 0, sealed=0,
                                      server=label)

        # 현재 시각 파일: 절대 seal 안 함
        worked |= self._live_scan(server, label, cur_path, cur_date, seal=False)

        # 직전 시각 파일: 정각 직후 늦게 쓰이는 로그를 흡수하다가, 2분 지나면 seal
        if '{HH}' in tmpl:
            prev = now - timedelta(hours=1)
            prev_path = ArsLogFetcher._expand(tmpl, prev.strftime('%Y-%m-%d'), prev.hour)
            st = self.store.get_scan_state(prev_path)
            if not st or not st.get('sealed'):
                seal = (now.minute >= 2)  # 정각+2분 지나면 확정
                worked |= self._live_scan(server, label, prev_path,
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
                worked |= self._live_scan(server, label, prev_path, pds, seal=seal)
        return worked

    def _live_scan(self, server, label, path, file_date, seal):
        """실시간 파일 1개 증분 색인.

        예전에는 회차마다 상태머신을 새로 만들어 pending(가장 오래된 열린 콜의
        시작점)부터 다시 읽었다. 'WaitCall Success!' 없이 끝난 콜 하나가 몇 시간
        열려 있으면 5초마다 몇 시간치를 다시 읽었고, 다 읽기 전에는 아무것도
        저장하지 않아 오늘 로그가 한참 동안(또는 계속) 안 보였다.
        이제는 상태머신을 회차 사이에 유지해 새로 붙은 바이트만 읽는다.
        """
        st = self.store.get_scan_state(path)
        if st and st.get('sealed'):
            if not self._unseal_if_grown(server, label, path, st):
                self._live.pop(path, None)
                return False
            st = self.store.get_scan_state(path)

        size, status = self._stat(server, path)
        if status == 'error':
            # 접속/권한 오류 — 확정하지 않는다. 확정해 버리면 복구 후에도
            # 이 파일을 영영 다시 읽지 않는다.
            return False
        if size is None:                 # status == 'nofile'
            if seal and st:              # 사라진 파일 → 확정 처리
                self.store.set_scan_state(path, st.get('last_offset') or 0,
                                          st.get('pending_offset') or 0,
                                          sealed=1, server=label)
                self._live.pop(path, None)
            return False

        lv = self._live.get(path)
        if lv is None:
            lv = self._live_attach(server, label, path, file_date, st, size)
            if lv is None:
                return False
        lv['touched'] = time.monotonic()
        sm = lv['sm']

        if size > lv['offset']:
            def on_batch(off):
                lv['offset'] = off
                self._live_save(path, label, file_date, lv, sealed=False)
            res = self._consume(server, path, lv['offset'], sm, end=size,
                                take_tail=seal, on_batch=on_batch)
            if res is None:
                return False
            lv['offset'], complete = res
        else:
            complete = True

        # 오래 조용한 콜 마감 (비정상 종료로 닫는 줄이 안 찍힌 콜)
        sm.close_idle(IDLE_CLOSE_ENDED, IDLE_CLOSE_OPEN)
        # 앞부분(우선 작업)을 다 채우기 전에는 확정하지 않는다
        done = seal and complete and not lv.get('gap')
        if done:
            sm.flush()  # 완료 파일: 남은 열린 콜 강제 마감
        n = self._live_save(path, label, file_date, lv, sealed=done)
        if done:
            self._live.pop(path, None)
        return bool(n)

    def _live_attach(self, server, label, path, file_date, st, size):
        """기동 후 이 파일을 처음 볼 때. 많이 뒤처져 있으면 끝부분부터 붙는다."""
        resume = (st.get('pending_offset') or 0) if st else 0
        start, gap = resume, None
        if size - resume > LIVE_JUMP:
            start = self._line_start(server, path, size - LIVE_JUMP)
            if start is None:
                return None
            if start > resume:
                gap = (resume, start)
                self._prio.append((server, label, path, file_date, resume, start))
                logger.info("라이브: %s 끝부분부터 색인 (앞부분 %.0fMB 는 우선 작업으로)",
                            os.path.basename(path), (start - resume) / 1048576)
            else:
                start = resume
        lv = {'sm': _ChannelStateMachine(None), 'offset': start, 'gap': gap,
              'touched': time.monotonic()}
        self._live[path] = lv
        return lv

    def _live_save(self, path, label, file_date, lv, sealed):
        """라이브 상태머신이 마감한 콜을 저장하고 읽은 위치를 남긴다."""
        sm = lv['sm']
        calls = self._stamp(sm.emitted, label, path, file_date)
        sm.emitted = []
        n = self.store.upsert_calls(calls) if calls else 0
        opens = [c['start_offset'] for c in sm.open_calls.values()
                 if c.get('start_offset') is not None]
        pending = min(opens) if opens else lv['offset']
        if lv.get('gap'):
            # 앞부분을 다 채우기 전에 재기동하면 거기서부터 다시 하도록
            pending = min(pending, lv['gap'][0])
        self.store.set_scan_state(path, last_offset=lv['offset'], pending_offset=pending,
                                  sealed=1 if sealed else 0, server=label)
        if n:
            logger.debug("인덱싱 %s: %d콜 (%s)", os.path.basename(path), n, label)
        return n

    def _line_start(self, server, path, pos):
        """pos 이후 첫 줄의 시작 위치 (줄 중간에서 읽기 시작하지 않도록)."""
        want = 65536
        try:
            if self._is_ssh(server):
                data = self._ssh_io.read_range(server, path, pos, want)
            else:
                with open(path, 'rb') as f:
                    f.seek(pos)
                    data = f.read(want)
        except OSError as e:
            logger.warning("파일 읽기 오류 %s: %s", path, e)
            return None
        if data is None:
            return None
        nl = data.find(b'\n')
        return pos + nl + 1 if nl >= 0 else pos

    def _unseal_if_grown(self, server, label, path, st):
        """확정된 파일이 실제로는 더 크면 확정을 풀고 이어 읽게 한다.

        확정(sealed)은 '끝까지 읽었다'는 뜻이어야 하는데, 예전 버전은 읽기가
        중간에 끊겨도 확정해 버렸다(그때 박제된 상태가 지금도 DB 에 남아 있다).
        그런 파일은 스크립트로 일일이 풀어 주지 않으면 영영 복구되지 않으므로,
        색인기가 만날 때마다 스스로 검사해서 되살린다.

        returns: True 면 확정을 풀었으니 계속 읽어도 된다
        """
        last = st.get('last_offset') or 0
        size, status = self._stat(server, path)
        if status != 'ok' or size is None or size <= last:
            return False          # 없는 파일이거나 정말로 다 읽은 파일
        logger.warning("확정 해제(덜 읽힌 파일): %s  %s/%s 바이트",
                       os.path.basename(path), last, size)
        self.store.set_scan_state(path, last, st.get('pending_offset') or 0,
                                  sealed=0, server=label)
        return True

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

    # ── 읽기 공통 ─────────────────────────────────────────
    def _read_blocks(self, server, path, start, end):
        """[start, end) 를 큰 단위로 차례로 돌려준다 (SSH · UNC 공통).
        end 까지 못 가면 ArsReadError — 호출측이 확정하지 않도록."""
        if self._is_ssh(server):
            # 한 번에 받으면 대용량 시간대에서 SSH 타임아웃이 나고, 이후 매
            # 폴링마다 같은 구간을 재시도하다 실패해 색인이 영구히 멈춘다.
            for _pos, chunk in self._ssh_io.read_chunks(server, path, start, end):
                yield chunk
            return
        with open(path, 'rb') as f:
            f.seek(start)
            pos = start
            while pos < end:
                b = f.read(min(UNC_BLOCK, end - pos))
                if not b:
                    raise ArsReadError(f'짧게 읽힘 offset={pos} (파일이 줄었음)')
                pos += len(b)
                yield b

    def _consume(self, server, path, start, sm, end, take_tail=False,
                 on_batch=None, drain_after=None):
        """[start, end) 를 줄 단위로 sm 에 먹인다.

        take_tail  : 개행 없는 마지막 줄도 소비 (확정할 파일). 아니면 그 줄은
                     다음 회차에 다시 읽도록 위치를 전진시키지 않는다.
        on_batch   : 읽기 단위마다 on_batch(지금까지 읽은 위치) — 그때그때 저장
        drain_after: 이 위치부터는 이미 열린 콜의 나머지 줄만 받고, 열린 콜이
                     다 닫히면 멈춘다 (앞부분 채우기에서 경계에 걸친 콜 마무리)
        returns (읽은 위치, 끝까지 갔는지) | None(파일 오류)
        """
        src = os.path.basename(path)
        dec = LazyDecoder()  # 영문 줄만 나오는 동안은 인코딩 판정을 미룬다
        offset = start
        carry = b''          # 읽기 단위 경계에서 잘린 마지막 줄
        complete = True

        def put(line, ls, le):
            """False 를 돌려주면 그만 읽는다 (드레인 끝)."""
            if drain_after is None or ls < drain_after:
                sm.feed(line, source=src, start_offset=ls, end_offset=le)
                return True
            if not sm.open_calls:
                return False
            ch = _channel_of(line)
            if not ch or ch not in sm.open_calls:
                return True
            if RE_START.search(line):        # 그 채널의 다음 콜 시작 = 이전 콜 끝
                sm._close(ch, line_for_time=None, reason='다음 call_start(최후)')
            else:
                sm.feed(line, source=src, start_offset=ls, end_offset=le)
            return bool(sm.open_calls)

        try:
            for chunk in self._read_blocks(server, path, start, end):
                buf = carry + chunk
                nl = buf.rfind(b'\n')
                if nl < 0:                    # 아직 개행이 없음 → 다음 단위로 이월
                    carry = buf
                    continue
                body, carry = buf[:nl + 1], buf[nl + 1:]
                rel = 0
                for raw in body.splitlines(keepends=True):
                    ls = offset
                    offset += len(raw)
                    rel += len(raw)
                    line = (dec.decode(raw, body[rel:rel + 65536])
                            if dec.enc is None else
                            raw.decode(dec.enc, errors='replace'))
                    if not put(line, ls, offset):
                        if on_batch:
                            on_batch(offset)
                        return offset, True
                if on_batch:
                    on_batch(offset)
        except ArsReadError as e:
            # 여기까지 읽은 건 유효하다 → 진행분은 살리고 확정만 막는다
            complete = False
            logger.warning("원격 읽기 중단 %s: %s", src, e)
        except FileNotFoundError:
            return None
        except OSError as e:
            logger.warning("파일 읽기 오류 %s: %s", path, e)
            return None
        # 남은 꼬리(개행 없는 마지막 줄): 끝까지 읽은 확정 파일이면 소비하고,
        # 아직 쓰이는 중이면 offset 을 전진시키지 않아 다음 회차에 다시 읽는다.
        if carry and take_tail and complete:
            ls = offset
            offset += len(carry)
            put(dec.decode(carry), ls, offset)
            if on_batch:
                on_batch(offset)
        return offset, complete

    @staticmethod
    def _stamp(calls, label, path, file_date):
        for c in calls:
            c['server'] = label
            c['file_path'] = path
            c['start_time'] = f"{file_date} {c['start_time']}" if c.get('start_time') else None
            c['end_time'] = f"{file_date} {c['end_time']}" if c.get('end_time') else None
        return calls

    def _scan_file(self, server, path, label, file_date, start_offset, seal):
        """[start_offset, EOF) 를 읽어 종료된 콜을 색인 (백필용).

        읽기 단위(8MB)마다 저장 · 진행 위치를 남긴다. 큰 파일을 다 읽은 뒤
        한꺼번에 저장하면 그동안 아무것도 안 보이고, 중간에 끊기면 처음부터다.

        returns (저장한 콜 수, pending, eof, complete) | None
          complete=False 면 EOF 까지 못 갔다는 뜻 — 호출측은 절대 확정(seal)하면
          안 된다. 읽은 데이터 자체는 유효하므로 진행분은 그대로 반영한다.
        """
        size, status = self._stat(server, path)
        if size is None:
            return None
        sm = _ChannelStateMachine(None)  # key 없음 → 전량
        saved = [0]

        def pending_of(off):
            opens = [c['start_offset'] for c in sm.open_calls.values()
                     if c.get('start_offset') is not None]
            return min(opens) if opens else off

        def on_batch(off):
            calls = self._stamp(sm.emitted, label, path, file_date)
            sm.emitted = []
            if calls:
                saved[0] += self.store.upsert_calls(calls)
            self.store.set_scan_state(path, last_offset=off, pending_offset=pending_of(off),
                                      sealed=0, server=label)

        res = self._consume(server, path, start_offset, sm, end=size,
                            take_tail=seal, on_batch=on_batch)
        if res is None:
            return None
        eof, complete = res
        if seal and complete:
            sm.flush()  # 완료 파일: 남은 열린 콜 강제 마감
            # (끝까지 못 읽었으면 flush 하면 안 된다 — 아직 이어질 콜이
            #  '끝난 콜'로 박제돼 뒷부분이 통째로 사라진다)
        calls = self._stamp(sm.emitted, label, path, file_date)
        sm.emitted = []
        if calls:
            saved[0] += self.store.upsert_calls(calls)
        return saved[0], pending_of(eof), eof, complete

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
            # 확정돼 있어도 실제 파일이 더 크면 덜 읽은 것 → 이어서 읽는다
            if not self._unseal_if_grown(server, label, path, st):
                return True  # 이미 완료 → 진행(대기 불필요)
            st = self.store.get_scan_state(path)

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
            self.store.set_scan_state(path, 0, 0, sealed=1, server=label)
            return True

        # 이전 시도가 중간에 끊겼으면 그 지점부터 이어 읽는다 (매번 0부터 다시
        # 읽으면 큰 파일에서 같은 실패를 반복하며 영원히 진도가 안 나간다)
        resume = (st.get('pending_offset') or 0) if st else 0
        res = self._scan_file(server, path, label, ds, resume, seal=True)
        if res is None:      # 읽기 도중 실패 — 확정하지 않고 재시도
            return _retry()
        n, pending, eof, done = res      # 콜은 읽기 단위마다 이미 저장됐다
        if not done:
            # 진행분만 저장하고 확정은 보류 — 다음 시도가 여기서 이어간다
            self.store.set_scan_state(path, last_offset=eof, pending_offset=pending,
                                      sealed=0, server=label)
            logger.warning("백필 중단(확정 보류) %s: 진행=%s", os.path.basename(path), eof)
            return _retry()
        self.store.set_scan_state(path, last_offset=eof, pending_offset=eof, sealed=1,
                                  server=label)
        logger.debug("백필 %s: %d콜", os.path.basename(path), n)
        return True

    def _do_one_gap(self):
        """우선 작업 1건 — 라이브가 끝부분부터 붙으면서 건너뛴 오늘 파일 앞부분.

        [gs, ge) 를 읽고, ge 이후로는 경계에 걸친(이미 열린) 콜의 나머지 줄만
        받아 마무리한다. ge 이후에 시작한 콜은 라이브가 맡으므로 중복되지 않는다.
        returns: True | 'retry'
        """
        server, label, path, ds, gs, ge = self._prio.popleft()
        key = ('gap', path, ge)

        def _retry(resume=gs):
            n = self._bf_retry.get(key, 0) + 1
            self._bf_retry[key] = n
            if n <= self.BACKFILL_MAX_RETRY:
                self._prio.append((server, label, path, ds, resume, ge))
            else:
                # 라이브 상태의 gap 은 남겨 둔다 → pending 이 앞부분에 머물러
                # 다음 기동 때 다시 채운다
                logger.warning("오늘 앞부분 채우기 포기(다음 기동 시 재시도): %s", path)
            return 'retry'

        if not self._is_ssh(server):
            ok_map = self.conn_bf.connect_for_paths([path])
            if any(not ok for ok, _ in ok_map.values()):
                return _retry()
        size, status = self._stat(server, path)
        if status == 'error':
            return _retry()
        if size is not None:
            sm = _ChannelStateMachine(None)
            saved = [0]

            def on_batch(off):
                calls = self._stamp(sm.emitted, label, path, ds)
                sm.emitted = []
                if calls:
                    saved[0] += self.store.upsert_calls(calls)

            res = self._consume(server, path, gs, sm, end=min(size, ge + DRAIN_MAX),
                                on_batch=on_batch, drain_after=ge)
            if res is None:
                return _retry()
            off, complete = res
            if not complete and off < ge:
                opens = [c['start_offset'] for c in sm.open_calls.values()
                         if c.get('start_offset') is not None]
                on_batch(off)
                return _retry(min(opens) if opens else off)
            sm.flush()   # 경계 뒤로 길게 이어진 콜 — 라이브는 시작 줄을 못 봤으니 여기서 마감
            on_batch(off)
            logger.info("오늘 앞부분 채움 완료 %s: %d콜 (%.0fMB)",
                        os.path.basename(path), saved[0], (ge - gs) / 1048576)
        lv = self._live.get(path)
        if lv and lv.get('gap') and lv['gap'][1] == ge:
            lv['gap'] = None
        return True

    # ── 정리 ──────────────────────────────────────────────
    def _maybe_purge(self, now):
        today = now.strftime('%Y-%m-%d')
        if self._last_purge == today:
            return
        self._last_purge = today
        # 하루 넘게 손대지 않은 라이브 상태(지난 파일) 정리
        old = time.monotonic() - 86400
        for pth, lv in list(self._live.items()):
            if lv.get('touched', 0) < old and not lv.get('gap'):
                self._live.pop(pth, None)
        try:
            removed = self.store.purge_older_than(self.retention_days)
            if removed:
                logger.info("인덱스 정리: %d콜 삭제(%d일 경과)", removed, self.retention_days)
        except Exception as e:
            logger.warning("정리 오류: %s", e)
