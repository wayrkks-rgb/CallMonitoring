#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VGW 채널 모니터링 — telnet(mon) 스트림 수집/파싱

구조 개요
---------
- VGW 서버의 채널 모니터링 포트는 그 서버의 127.0.0.1(로컬호스트) 전용이라,
  로그수집 서버(이 앱)에서 직접 접근할 수 없다. 그래서 이미 쓰는 OpenSSH 로
  VGW 에 붙어 원격 bash 의 /dev/tcp 로 해당 포트에 연결하고 `mon start N` 을
  1회 전송한 뒤, 밀려오는 텍스트 스트림을 ssh stdout 으로 그대로 읽는다.
      ssh <host> bash -c 'exec 3<>/dev/tcp/127.0.0.1/PORT;
                          printf "mon start N\r\n" >&3; cat <&3'
- 연결은 엔드포인트(방향)당 1개만 상시 유지한다(재접속 없이 스트림만 읽음).
  → SSH 인증 핸드셰이크가 반복되지 않아 VGW 부하는 무시할 수준.
- 끊기면 백오프 후 자동 재연결.

이 모듈은 파서(VgwStreamParser)와 수집기(VgwCollector)를 제공한다.
수집기는 방향별 '최신 스냅샷'을 메모리에 보관하고, 스냅샷마다 on_snapshot
콜백을 호출한다(통계 적재는 상위에서 콜백으로 연결).
"""

import re
import time
import logging
import threading
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# ── telnet 출력 파서 ───────────────────────────────────────
class VgwStreamParser:
    """
    mon 스트림 텍스트를 받아 완성된 스냅샷 블록을 파싱한다.

    한 블록 예시:
        =====...=====
        Time[2026/07/09 09:41:44]
        SessionID[1402159854114561782905318974]
        ChannelNumber[Status:W(Wait)|E(Error)|R(Rining)|B(Busy):calltime]
             5416[W]  ...  5515[B:259]  ...
        BusyCH[1] RegisterCH[40] TotalCH[40]
        =====...=====
    """

    # 채널 토큰: 5515[B:259] / 5416[W]
    CH_RE = re.compile(r'(\d+)\[([WERB])(?::(\d+))?\]')
    # 한 블록: Time[..] ~ TotalCH[..] 까지 (non-greedy, DOTALL)
    BLOCK_RE = re.compile(
        r'Time\[(?P<time>[^\]]*)\]'
        r'.*?SessionID\[(?P<sid>[^\]]*)\]'
        r'.*?ChannelNumber\[[^\]]*\]'
        r'(?P<body>.*?)'
        r'BusyCH\[(?P<busy>\d+)\]\s*RegisterCH\[(?P<reg>\d+)\]\s*TotalCH\[(?P<total>\d+)\]',
        re.S
    )
    # telnet IAC(0xFF) 등 제어바이트 제거용
    _CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]')

    def __init__(self):
        self._buf = ''

    def feed(self, text):
        """스트림 조각을 받아 완성된 스냅샷 리스트를 반환(없으면 [])."""
        if not text:
            return []
        self._buf += text
        snapshots = []
        while True:
            m = self.BLOCK_RE.search(self._buf)
            if not m:
                break
            snapshots.append(self._build(m))
            self._buf = self._buf[m.end():]
        # 버퍼 무한증가 방지 (미완성 꼬리만 유지)
        if len(self._buf) > 200_000:
            self._buf = self._buf[-50_000:]
        return snapshots

    def parse_all(self, text):
        """일회성 파싱(진단용): 텍스트 전체에서 모든 블록 추출."""
        return [self._build(m) for m in self.BLOCK_RE.finditer(text or '')]

    def _build(self, m):
        body = self._CTRL_RE.sub('', m.group('body'))
        channels = []
        for num, st, ct in self.CH_RE.findall(body):
            channels.append({
                'num': int(num),
                'status': st,
                'calltime': int(ct) if ct else None,
            })
        return {
            'time': (m.group('time') or '').strip(),
            'session_id': (m.group('sid') or '').strip(),
            'busy': int(m.group('busy')),
            'register': int(m.group('reg')),
            'total': int(m.group('total')),
            'channels': channels,
            'parsed_at': time.time(),
        }


# 상태 코드 → 사람이 읽는 라벨 (원문 코드 기준)
STATUS_LABELS = {
    'W': '대기',    # Wait
    'R': '인입',    # Ringing (발신/링잉)
    'B': '통화중',  # Busy (calltime 초)
    'E': '에러',    # Error
}


def channel_kind(status, calltime):
    """
    원문 상태 + calltime 유무로 세분화된 종류 반환.

    아웃바운드 구분(사용자 확인):
      W            → 대기(wait)
      R(+초)       → 발신(dial), calltime = 발신경과초
      B(초 없음)   → 로그인(login)  ※ 통화 아님
      B:초         → 통화중(talk),  calltime = 통화경과초
      E            → 에러(error)
    """
    if status == 'B':
        return 'talk' if calltime is not None else 'login'
    if status == 'R':
        return 'dial'
    if status == 'E':
        return 'error'
    return 'wait'


KIND_LABELS = {
    'wait': '대기', 'dial': '발신', 'talk': '통화중',
    'login': '로그인', 'error': '에러',
}


# ── SSH 스트림 수집기 ──────────────────────────────────────
class VgwCollector:
    """
    설정된 VGW 엔드포인트(방향별)마다 상시 ssh 스트림을 유지하며
    최신 스냅샷을 메모리에 보관한다.

    config_provider(): 아래 형태의 dict 를 반환하는 콜러블
        {
          "enabled": bool,
          "poll_interval": 5,
          "endpoints": [
             {"name":"VGW1", "server_id":0,
              "inbound_port":54001, "outbound_port":54002},
             ...
          ]
        }
    server_resolver(server_id): server_id → 서버설정 dict (ssh 접속용)
    on_snapshot(ep_name, direction, snapshot): 스냅샷마다 호출(선택)
    """

    def __init__(self, config_provider, server_resolver, on_snapshot=None):
        self._config_provider = config_provider
        self._server_resolver = server_resolver
        self._on_snapshot = on_snapshot

        self._threads = {}          # key -> Thread
        self._procs = {}            # key -> Popen
        self._latest = {}           # key -> snapshot dict(+meta)
        self._errors = {}           # key -> last error str
        self._last_data = {}        # key -> 마지막 스냅샷 수신 시각(watchdog용)
        self._interval = 5
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._running = False
        self._watchdog = None

    # 키: "VGW1/inbound"
    @staticmethod
    def _key(name, direction):
        return f"{name}/{direction}"

    def start(self):
        cfg = self._config_provider() or {}
        if not cfg.get('enabled'):
            logger.info("VGW 모니터: 비활성화(enabled=false) — 수집기 미기동")
            return
        interval = int(cfg.get('poll_interval', 5) or 5)
        self._interval = interval
        self._stop.clear()
        self._running = True

        for ep in cfg.get('endpoints', []):
            name = ep.get('name') or 'VGW'
            sid = ep.get('server_id')
            for direction, port_key in (('inbound', 'inbound_port'),
                                        ('outbound', 'outbound_port')):
                port = ep.get(port_key)
                if sid is None or not port:
                    continue
                key = self._key(name, direction)
                t = threading.Thread(
                    target=self._reader_loop,
                    args=(key, name, direction, sid, int(port), interval),
                    daemon=True,
                )
                self._threads[key] = t
                t.start()

        # 스트림 정체 감시(watchdog): VGW 재기동 등으로 스트림이 조용히 멈추면
        #   SSH 프로세스를 강제 종료 → 리더 루프가 자동 재연결한다.
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()

        logger.info(f"VGW 모니터: 수집기 기동 (스트림 {len(self._threads)}개, 주기 {interval}s)")

    def stop(self):
        self._stop.set()
        self._running = False
        for key, p in list(self._procs.items()):
            try:
                p.terminate()
            except Exception:
                pass
        self._procs.clear()
        self._last_data.clear()
        logger.info("VGW 모니터: 수집기 종료")

    def is_running(self):
        return self._running and any(t.is_alive() for t in self._threads.values())

    def restart(self):
        """설정 변경 후 재기동."""
        self.stop()
        # 스레드 정리 대기(짧게)
        time.sleep(0.5)
        self._threads.clear()
        self._latest.clear()
        self.start()

    def _watchdog_loop(self):
        """스트림 정체 감시. 일정 시간 스냅샷이 없으면 SSH 프로세스를 강제 종료
        → 리더 루프가 자동 재연결(VGW 재기동/텔넷 세션 사망 자동 복구)."""
        while not self._stop.is_set():
            self._watchdog_tick()
            if self._stop.wait(5):
                break

    def _watchdog_tick(self):
        # 유예: 갱신주기의 3배(최소 20초) 동안 데이터 없으면 정체로 판단
        stale_after = max(20, self._interval * 3)
        now = time.time()
        for key, proc in list(self._procs.items()):
            last = self._last_data.get(key, now)
            if now - last > stale_after:
                logger.warning(
                    f"VGW 모니터[{key}]: {int(now-last)}초 무응답 → 재연결")
                self._set_error(key, f"{int(now-last)}초 무응답 감지 — 재연결 시도")
                try:
                    proc.terminate()   # 리더 루프가 EOF 감지 후 재접속
                except Exception:
                    pass
                with self._lock:
                    self._last_data[key] = now   # 재판정 방지

    # ── 원격 명령 구성 ─────────────────────────────────────
    def _build_ssh_cmd(self, server, port, interval):
        """모니터 전용 접속 옵션으로 ssh 명령 리스트 생성.

        세션 안전 옵션:
          - ControlMaster=no / ControlPath=none : 검색용 SSH 와 제어소켓을 공유·점유
            하지 않도록 완전 분리(멀티플렉싱 간섭·세션 홀딩 방지)
          - TCPKeepAlive + ServerAliveInterval : 죽은 연결 조기 감지
          - ConnectionAttempts=1 / BatchMode : 매달리지 않고 실패 시 즉시 종료→재연결
        """
        target = self._ssh_target(server)
        if not target:
            return None
        cmd = ['ssh',
               '-o', 'ControlMaster=no',
               '-o', 'ControlPath=none',
               '-o', 'ConnectTimeout=10',
               '-o', 'ConnectionAttempts=1',
               '-o', 'ServerAliveInterval=10',
               '-o', 'ServerAliveCountMax=3',
               '-o', 'TCPKeepAlive=yes',
               '-o', 'StrictHostKeyChecking=accept-new',
               '-o', 'BatchMode=yes']
        key_path = server.get('ssh_key_path')
        if key_path and Path(key_path).exists():
            cmd += ['-i', str(key_path)]
        ssh_port = server.get('ssh_port', 22)
        if ssh_port and int(ssh_port) != 22:
            cmd += ['-p', str(ssh_port)]
        cmd.append(target)
        # 원격 bash 의 /dev/tcp 로 모니터 포트 접속 → mon start N 전송 → 스트림 relay
        remote = (
            "bash -c 'exec 3<>/dev/tcp/127.0.0.1/%d; "
            "printf \"mon start %d\\r\\n\" >&3; cat <&3'" % (port, interval)
        )
        cmd.append(remote)
        return cmd

    @staticmethod
    def _ssh_target(server):
        hostname = server.get('hostname')
        if hostname and not server.get('ip'):
            return hostname
        ip = server.get('ip')
        user = server.get('user', 'loguser')
        return f"{user}@{ip}" if ip else (hostname or None)

    # ── 리더 루프(엔드포인트/방향 1개) ─────────────────────
    def _reader_loop(self, key, name, direction, server_id, port, interval):
        backoff = 2
        while not self._stop.is_set():
            server = None
            try:
                server = self._server_resolver(server_id)
            except Exception as e:
                self._set_error(key, f"서버 조회 실패: {e}")
            if not server:
                self._set_error(key, f"server_id={server_id} 서버 설정 없음")
                if self._stop.wait(10):
                    break
                continue

            cmd = self._build_ssh_cmd(server, port, interval)
            if not cmd:
                self._set_error(key, "SSH 대상 구성 실패(호스트/IP 확인)")
                if self._stop.wait(10):
                    break
                continue

            parser = VgwStreamParser()
            try:
                logger.info(f"VGW 모니터[{key}]: 연결 시도 (port {port})")
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    text=True, encoding='utf-8', errors='ignore', bufsize=1,
                )
                self._procs[key] = proc
                self._set_error(key, None)
                with self._lock:
                    self._last_data[key] = time.time()   # 연결 직후 유예 시작
                backoff = 2

                for line in proc.stdout:
                    if self._stop.is_set():
                        break
                    for snap in parser.feed(line):
                        self._store_snapshot(key, name, direction, port, snap)

                # 스트림 종료 → stderr 수거
                err = ''
                try:
                    err = (proc.stderr.read() or '').strip()
                except Exception:
                    pass
                rc = proc.poll()
                if not self._stop.is_set():
                    self._set_error(key, f"스트림 종료(rc={rc}) {err[:200]}")
            except FileNotFoundError:
                self._set_error(key, "OpenSSH 미설치")
                if self._stop.wait(30):
                    break
                continue
            except Exception as e:
                self._set_error(key, f"수집 오류: {e}")
            finally:
                p = self._procs.pop(key, None)
                if p:
                    try:
                        p.terminate()
                    except Exception:
                        pass

            # 재연결 백오프
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 30)

    # ── 상태 저장/조회 ─────────────────────────────────────
    def _store_snapshot(self, key, name, direction, port, snap):
        # 세분화 종류별 카운트 (대기/발신/통화중/로그인/에러)
        counts = {'wait': 0, 'dial': 0, 'talk': 0, 'login': 0, 'error': 0}
        for ch in snap['channels']:
            counts[channel_kind(ch['status'], ch['calltime'])] += 1
        record = {
            'vgw': name,
            'direction': direction,
            'port': port,
            'time': snap['time'],
            'session_id': snap['session_id'],
            'busy': snap['busy'],            # telnet 요약값(BusyCH = R+B)
            'register': snap['register'],
            'total': snap['total'],
            'counts': counts,                # 세분화 카운트
            'channels': snap['channels'],
            'updated_at': time.time(),
        }
        with self._lock:
            self._latest[key] = record
            self._last_data[key] = time.time()   # watchdog: 정상 수신 갱신
        if self._on_snapshot:
            try:
                self._on_snapshot(name, direction, snap)
            except Exception as e:
                logger.debug(f"on_snapshot 콜백 오류(무시): {e}")

    def _set_error(self, key, msg):
        with self._lock:
            if msg is None:
                self._errors.pop(key, None)
            else:
                self._errors[key] = msg
                logger.warning(f"VGW 모니터[{key}]: {msg}")

    def snapshot_all(self):
        """현재 최신 스냅샷 + 스트림 상태 전체 반환(라우트용)."""
        with self._lock:
            endpoints = list(self._latest.values())
            errors = dict(self._errors)
        # 스트림별 alive 여부
        streams = []
        for key, t in self._threads.items():
            streams.append({
                'key': key,
                'alive': bool(t.is_alive()),
                'error': errors.get(key),
            })
        return {
            'running': self.is_running(),
            'endpoints': endpoints,
            'streams': streams,
        }


# ── 콜 이벤트 트래커 (채널 상태 전이 → 콜 이벤트) ─────────────
class VgwCallTracker:
    """
    연속 스냅샷에서 채널별 상태 전이를 추적해 '완료된 콜'을 emit 한다.

    상태 정의(사용자 확인):
      - 통화중(talk) = B 이면서 calltime 이 있음(B:초)
      - 로그인(login) = B 인데 calltime 없음           ※ 통화 아님
      - 발신(dial)   = R (아웃바운드 발신)
      - 대기(wait)   = W

    이벤트:
      - answered : 통화중 진입 후 종료. duration = 마지막 통화 calltime(초)
      - noanswer : 발신(R) 이 통화중에 도달하지 못하고 대기/에러로 종료
                   (아웃바운드 통화무응답)

    판정 원칙(중복 방지):
      - 발신이 시작되면 '미해결 발신(pending_dial)' 표시.
      - 통화중에 진입하면 pending_dial 해제(= 응답으로 이어짐).
      - 통화중 종료 시 answered emit(duration = 마지막 calltime).
      - 대기(W)/에러(E) 로 돌아갈 때 pending_dial 이 남아있으면 noanswer emit.
        (로그인 B 로 잠깐 바뀌는 것은 미해결 상태로 보류 → 오탐 방지)
    """

    def __init__(self, on_call):
        self._on_call = on_call
        # (key, channel) -> {'phase','calltime','talk_since','pending_dial','dial_since'}
        self._state = {}

    def process(self, vgw, direction, snapshot):
        key = f"{vgw}/{direction}"
        now = snapshot.get('parsed_at') or time.time()
        for ch in snapshot.get('channels', []):
            self._step(key, vgw, direction, ch['num'],
                       ch['status'], ch.get('calltime'), now)

    def _emit(self, vgw, direction, channel, start_ts, end_ts, duration, outcome):
        if self._on_call:
            try:
                self._on_call({
                    'vgw': vgw, 'direction': direction, 'channel': channel,
                    'start_ts': start_ts, 'end_ts': end_ts,
                    'duration_sec': int(duration or 0), 'outcome': outcome,
                })
            except Exception as e:
                logger.debug(f"on_call 콜백 오류(무시): {e}")

    @staticmethod
    def _phase(status, calltime):
        if status == 'B':
            return 'talk' if calltime is not None else 'login'
        if status == 'R':
            return 'dial'
        if status == 'E':
            return 'error'
        return 'wait'

    def _step(self, key, vgw, direction, channel, status, calltime, now):
        sk = (key, channel)
        st = self._state.get(sk) or {
            'phase': None, 'calltime': None, 'talk_since': None,
            'pending_dial': False, 'dial_since': None,
        }
        prev = st['phase']
        cur = self._phase(status, calltime)

        if cur == 'talk':
            if prev == 'talk':
                # 연속콜: calltime 급감 → 직전 통화 종료 + 새 통화 시작
                if calltime is not None and st['calltime'] is not None and calltime < st['calltime']:
                    self._emit(vgw, direction, channel, st['talk_since'], now,
                               st['calltime'], 'answered')
                    st['talk_since'] = now
                st['calltime'] = calltime
            else:
                # 통화 시작 (발신이 응답으로 이어짐)
                st['talk_since'] = now
                st['calltime'] = calltime
                st['pending_dial'] = False
                st['dial_since'] = None
        else:
            if prev == 'talk':
                # 통화 종료 → 응답콜 확정
                self._emit(vgw, direction, channel, st['talk_since'], now,
                           st['calltime'], 'answered')
                st['talk_since'] = None
                st['calltime'] = None
                st['pending_dial'] = False

            if cur == 'dial':
                if not st['pending_dial']:
                    st['pending_dial'] = True
                    st['dial_since'] = now
            elif cur in ('wait', 'error'):
                # 진짜 유휴로 복귀 → 미해결 발신이면 무응답 확정
                if st['pending_dial']:
                    self._emit(vgw, direction, channel, st['dial_since'], now, 0, 'noanswer')
                st['pending_dial'] = False
                st['dial_since'] = None
            # cur == 'login' : 미해결 상태 보류(그대로 유지)

        st['phase'] = cur
        self._state[sk] = st
