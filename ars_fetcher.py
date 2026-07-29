#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ARS(Windows) 로그 수집 모듈 — 인증/연결부 (4-①)

- ARS 서버는 네트워크 드라이브(UNC)로 접근 (SSH 아님)
- 워크그룹/로컬계정 환경이라, 서비스 세션에서 런타임 인증이 필요:
    WNetAddConnection2(\\host\IPC$, user, password)
- 계정은 ARS 전대 공통(ivradmin) → ars_auth.json(DPAPI) 에서 1회 로드
- 호스트 단위로 1회 연결 후 캐싱, 부분 실패 격리, 사용하는 호스트만 lazy 연결
- 드라이브 문자 매핑 안 함 (서비스 세션에서 안 보임) → UNC 직접 사용

검색부(mmap 2단계: 채널 경계/종료 3단/캐리오버)는 4-② 에서 이 매니저 위에 올림.
"""

import logging
from threading import Lock

import ars_auth_store as store

logger = logging.getLogger(__name__)

try:
    import win32wnet
    import win32netcon
    import pywintypes
    _HAS_PYWIN32 = True
except ImportError:
    win32wnet = None
    win32netcon = None
    pywintypes = None
    _HAS_PYWIN32 = False


# Windows 네트워크 오류코드 → 사용자 메시지
_WNET_ERRORS = {
    5:    '접근 거부 (권한 확인)',
    53:   '네트워크 경로를 찾을 수 없음 (호스트/공유명 확인)',
    64:   '네트워크 이름이 더 이상 사용 불가',
    67:   '네트워크 이름을 찾을 수 없음 (공유명 확인)',
    86:   '비밀번호 오류',
    1219: '자격증명 충돌 (해당 서버에 다른 계정으로 이미 연결됨)',
    1231: '네트워크 위치에 도달할 수 없음',
    1326: '로그온 실패 (계정/비밀번호 확인)',
}
_ERR_ALREADY_ASSIGNED = 85  # 이미 연결됨 → 성공으로 간주
_ERR_CREDENTIAL_CONFLICT = 1219  # 자격증명 충돌 → 강제 해제 후 재시도


def extract_host(unc_path):
    """
    UNC 경로에서 호스트명 추출.
      \\\\ARS01\\share\\dir\\file.log  → 'ARS01'
      //10.19.20.11/share/...          → '10.19.20.11'
    UNC 가 아니면 None.
    """
    if not unc_path:
        return None
    s = unc_path.strip().replace('/', '\\')
    if not s.startswith('\\\\'):
        return None
    parts = s.lstrip('\\').split('\\')
    return parts[0] if parts and parts[0] else None


class ArsConnectionManager:
    """
    ARS UNC 세션 관리자.

    사용:
        with ArsConnectionManager() as cm:
            results = cm.connect_for_paths(unc_paths)   # {host: (ok, err)}
            # 이후 ok 인 호스트의 UNC 경로를 일반 파일처럼 읽기
        # with 블록 종료 시 연결 자동 해제
    """

    def __init__(self, auth=None, auth_path=None):
        """
        Args:
            auth: {'user':..., 'password':...} 사전 주입 (없으면 ars_auth.json 에서 lazy 로드)
            auth_path: ars_auth.json 경로 (테스트/커스텀용)
        """
        self._auth = auth
        self._auth_path = auth_path
        self._connected = {}   # host -> True (성공만 캐싱)
        self._lock = Lock()

    # ── 인증 ───────────────────────────────────────────────
    def _ensure_auth(self):
        if self._auth is None:
            self._auth = store.get_ars_credentials(path=self._auth_path)
        return self._auth

    # ── 연결 ───────────────────────────────────────────────
    def ensure_connected(self, host):
        """
        호스트에 UNC 세션 보장 (lazy, 캐싱, 스레드 안전).
        Returns: (ok: bool, error: dict|None)
        """
        if not host:
            return False, {'error': 'ARS 호스트 없음'}

        with self._lock:
            if self._connected.get(host) is True:
                return True, None

            if not _HAS_PYWIN32:
                return False, {
                    'server': host,
                    'error': 'pywin32 미설치',
                    'details': 'win32wnet 없음 (pywin32 반입 필요)'
                }

            try:
                creds = self._ensure_auth()
            except store.ArsAuthError as e:
                return False, {'server': host, 'error': 'ARS 인증정보 오류', 'details': str(e)}

            remote = f'\\\\{host}\\IPC$'
            nr = win32wnet.NETRESOURCE()
            nr.dwType = win32netcon.RESOURCETYPE_ANY
            nr.lpRemoteName = remote

            try:
                win32wnet.WNetAddConnection2(nr, creds['password'], creds['user'], 0)
                self._connected[host] = True
                logger.info(f"ARS 연결 성공: {host}")
                return True, None
            except pywintypes.error as e:
                code = getattr(e, 'winerror', None)
                if code == _ERR_ALREADY_ASSIGNED:
                    self._connected[host] = True
                    logger.info(f"ARS 이미 연결됨: {host}")
                    return True, None

                # 1219: 기존(탐색기/net use) 연결과 자격증명 충돌 → 강제 해제 후 1회 재시도
                if code == _ERR_CREDENTIAL_CONFLICT:
                    cancelled = self._force_disconnect_host(host)
                    logger.warning(
                        f"ARS 1219 자격증명 충돌 → 기존 연결 {len(cancelled)}건 강제 해제 후 재시도: {host}"
                    )
                    try:
                        win32wnet.WNetAddConnection2(nr, creds['password'], creds['user'], 0)
                        self._connected[host] = True
                        logger.info(f"ARS 재연결 성공(1219 복구): {host}")
                        return True, None
                    except pywintypes.error as e2:
                        code2 = getattr(e2, 'winerror', None)
                        if code2 == _ERR_ALREADY_ASSIGNED:
                            self._connected[host] = True
                            return True, None
                        msg2 = _WNET_ERRORS.get(code2, getattr(e2, 'strerror', str(e2)))
                        logger.error(f"ARS 1219 복구 실패: {host} (코드 {code2}) {msg2}")
                        return False, {
                            'server': host,
                            'error': 'ARS 연결 실패 (1219 자동복구 실패)',
                            'code': code2,
                            'details': f'{msg2} — 탐색기의 네트워크 드라이브를 수동 해제 후 재시도하세요.',
                        }

                msg = _WNET_ERRORS.get(code, getattr(e, 'strerror', str(e)))
                logger.error(f"ARS 연결 실패: {host} (코드 {code}) {msg}")
                return False, {
                    'server': host,
                    'error': 'ARS 연결 실패',
                    'code': code,
                    'details': msg,
                }
            except Exception as e:
                logger.exception(f"ARS 연결 오류: {host}")
                return False, {'server': host, 'error': 'ARS 연결 오류', 'details': str(e)}

    def _force_disconnect_host(self, host):
        """
        해당 호스트로 열려 있는 모든 UNC 연결을 강제(force) 해제.
        탐색기 매핑이 IPC$ 가 아닌 다른 공유(\\\\host\\share)일 수 있어
        현재 연결을 열거하여 그 호스트 대상 전부 끊는다.
        Returns: 해제된 원격 리소스명 리스트
        """
        cancelled = []
        prefix = f'\\\\{host}'.lower()
        # 1) 현재 연결된 리소스 열거 → 이 호스트 대상 강제 해제
        try:
            handle = win32wnet.WNetOpenEnum(
                win32netcon.RESOURCE_CONNECTED, win32netcon.RESOURCETYPE_DISK, 0, None
            )
            try:
                while True:
                    try:
                        items = win32wnet.WNetEnumResource(handle, 64)
                    except Exception:
                        break
                    if not items:
                        break
                    for it in items:
                        rn = (getattr(it, 'lpRemoteName', '') or '')
                        if rn.lower().startswith(prefix):
                            try:
                                win32wnet.WNetCancelConnection2(rn, 0, True)  # bForce=True
                                cancelled.append(rn)
                            except Exception as ce:
                                logger.debug(f"연결 해제 실패(무시): {rn} — {ce}")
            finally:
                win32wnet.WNetCloseEnum(handle)
        except Exception as e:
            logger.debug(f"연결 열거 실패(무시): {host} — {e}")

        # 2) IPC$ / 서버 루트도 강제 해제 시도 (열거에 안 잡히는 경우 대비)
        for target in (f'\\\\{host}\\IPC$', f'\\\\{host}'):
            try:
                win32wnet.WNetCancelConnection2(target, 0, True)
                cancelled.append(target)
            except Exception:
                pass

        self._connected.pop(host, None)
        return cancelled

    def connect_for_paths(self, unc_paths):
        """
        경로 리스트에서 고유 호스트를 뽑아 각 호스트에 연결.
        Returns: {host: (ok, error)}  — 부분 실패는 격리되어 개별 결과로 반환.
        """
        hosts = []
        seen = set()
        for p in unc_paths:
            h = extract_host(p)
            if h and h not in seen:
                seen.add(h)
                hosts.append(h)

        results = {}
        for h in hosts:
            results[h] = self.ensure_connected(h)
        return results

    # ── 해제 ───────────────────────────────────────────────
    def disconnect(self, host):
        """단일 호스트 연결 해제"""
        if not _HAS_PYWIN32:
            return
        try:
            win32wnet.WNetCancelConnection2(f'\\\\{host}\\IPC$', 0, False)
            logger.info(f"ARS 연결 해제: {host}")
        except Exception as e:
            logger.debug(f"ARS 연결 해제 실패(무시): {host} — {e}")
        finally:
            self._connected.pop(host, None)

    def disconnect_all(self):
        """매니저가 맺은 모든 연결 해제 (서비스 중지 훅 등에서 호출)"""
        for host in list(self._connected.keys()):
            self.disconnect(host)

    # ── 컨텍스트 매니저 ────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.disconnect_all()
        return False


# ═════════════════════════════════════════════════════════
# 4-② ArsLogFetcher — mmap 2단계 검색 + 채널 경계 상태머신
# ═════════════════════════════════════════════════════════

import os
import re
import glob
import mmap
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config_manager import get_enabled_servers, get_log_paths, get_server_label

# ── ARS 로그 파싱 정규식 (실데이터 ars_2_.txt 로 검증됨) ──
RE_CHANNEL   = re.compile(r'\w+@\d+\s+\[(\d{4})\]')
RE_START     = re.compile(r'\[UniqueCallID=(\d+)\]\s+send_call_start_event\s*->\s*call_start')
RE_END_EVENT = re.compile(r'\[UniqueCallID=(\d+)\]\s+send_call_end_event\s*->\s*call_end')
RE_WAITOK    = re.compile(r'\[WAITCALL\]\s+WaitCall\s+Success!')
RE_CUSTID    = re.compile(r'app\.CustID\s*:?\s*(\d+)')
RE_PHONE     = re.compile(r'(?:ani\[|ANI\[|call_ani\()(\d{9,12})')
RE_TIME      = re.compile(r'(\d{2}:\d{2}:\d{2})')
RE_DATETIME  = re.compile(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})')


def _channel_of(line):
    m = RE_CHANNEL.search(line)
    return m.group(1) if m else None


def _time_of(line):
    """HH:MM:SS (콜 경계 시각 — 날짜는 UCID 앞 8자리에서 구함)"""
    m = RE_TIME.search(line)
    return m.group(1) if m else None


def _datetime_of(line):
    """'YYYY-MM-DD HH:MM:SS' (표시용). 날짜 접두부가 없으면 HH:MM:SS 만."""
    m = RE_DATETIME.search(line)
    if m:
        return f'{m.group(1)} {m.group(2)}'
    return _time_of(line)


class _ChannelStateMachine:
    """
    채널별 콜 경계 상태머신 (파일 경계를 넘어 연속 feed 가능 → 크로스파일 캐리오버).

    종료 우선순위:
      1) WaitCall Success!  (주)
      2) send_call_end_event -> call_end  (보조, 동일 UCID)
      3) 같은 채널 다음 call_start 직전  (최후)
    """

    def __init__(self, key_needle):
        self.key = key_needle          # 이 문자열을 포함한 콜만 emit
        self.open_calls = {}           # channel -> call dict
        self.emitted = []

    def has_open(self):
        return bool(self.open_calls)

    def _new_call(self, ucid, line, source, start_offset=None, end_offset=None):
        return {
            'ucid': ucid,
            'channel': _channel_of(line),
            'start_time': _time_of(line),
            'lines': [line],
            'end_event_line': None,     # call_end 감지 여부(보조)
            # key 가 없으면(인덱서 모드) 모든 콜을 emit, 있으면 키 포함 콜만
            'key_seen': True if not self.key else (self.key in line),
            'sources': {source},
            'start_offset': start_offset,
            'end_offset': end_offset,
        }

    def feed(self, line, source, start_offset=None, end_offset=None):
        ch = _channel_of(line)

        # 1) call_start
        ms = RE_START.search(line)
        if ms:
            c = _channel_of(line)
            if c in self.open_calls:
                # 종료 마커 없이 재시작 → 이전 콜을 최후수단으로 마감
                self._close(c, line_for_time=None, reason='다음 call_start(최후)')
            self.open_calls[c] = self._new_call(ms.group(1), line, source,
                                                start_offset, end_offset)
            return

        # 열린 콜에 라인 적재
        if ch and ch in self.open_calls:
            call = self.open_calls[ch]
            call['lines'].append(line)
            call['sources'].add(source)
            if end_offset is not None:
                call['end_offset'] = end_offset
            if self.key and self.key in line:
                call['key_seen'] = True

            # 2) call_end (보조) — 위치만 기록, 닫지 않음
            if RE_END_EVENT.search(line):
                call['end_event_line'] = len(call['lines'])

            # 3) WaitCall Success! (주 종료)
            if RE_WAITOK.search(line):
                self._close(ch, line_for_time=line, reason='WaitCall Success!')

    def _close(self, ch, line_for_time, reason):
        call = self.open_calls.pop(ch, None)
        if not call:
            return
        body = call['lines']
        end_time = _time_of(line_for_time) if line_for_time else None
        if not end_time:
            # 보조/최후 마감 시: 뒤에서부터 타임스탬프 탐색
            for l in reversed(body):
                t = _time_of(l)
                if t:
                    end_time = t
                    break

        custids = RE_CUSTID.findall(''.join(body))
        phones = RE_PHONE.findall(''.join(body))

        call.update({
            'end_time': end_time,
            'end_by': reason,
            'cust_id': custids[-1] if custids else None,   # 마지막 CustID = VGW 연결키
            'all_cust_ids': custids,
            'phone': phones[0] if phones else None,
            'line_count': len(body),
            'source_files': sorted(call['sources']),
            'start_offset': call.get('start_offset'),
            'end_offset': call.get('end_offset'),
        })
        if call.get('key_seen'):
            self.emitted.append(call)

    def flush(self):
        """스트림 종료 시 남은 열린 콜을 보조/최후 마감"""
        for ch in list(self.open_calls.keys()):
            call = self.open_calls[ch]
            reason = 'call_end(보조)' if call.get('end_event_line') else 'EOF(미마감)'
            self._close(ch, line_for_time=None, reason=reason)


class ArsLogFetcher:
    """
    ARS 인바운드 로그 검색.

    흐름:
      1) 대상 ARS 서버(inbound) → 날짜×시(hour) 후보 파일 목록
      2) 호스트 연결 보장(ArsConnectionManager)
      3) mmap 바이트검색으로 키(핸드폰/고객ID) 포함 파일 선별 (1단계)
      4) 선별 파일 + 캐리오버 구간만 디코드하며 채널 경계 상태머신 (2단계)
      5) 콜 구간에서 마지막 CustID 추출 → (5단계에서 VGW 세션키 로직에 전달)

    파일 IO 는 메서드로 분리(_path_exists/_mmap_contains/_iter_lines)해
    테스트에서 로컬 픽스처로 대체 가능.
    """

    def __init__(self, start_date=None, end_date=None, server_ids=None,
                 conn_manager=None, auth_path=None, index_store=None):
        self.start_date = start_date
        self.end_date = end_date
        self.server_ids = server_ids
        self.conn = conn_manager if conn_manager is not None else ArsConnectionManager(auth_path=auth_path)
        self.index_store = index_store  # None 이면 진입점에서 기본 싱글톤 사용
        self.errors = []

    # ── 날짜/파일 목록 ─────────────────────────────────────
    def _date_range(self):
        if not self.start_date and not self.end_date:
            return [datetime.now().strftime('%Y-%m-%d')]
        fmt = '%Y-%m-%d'
        start = datetime.strptime(self.start_date, fmt) if self.start_date else None
        end = datetime.strptime(self.end_date, fmt) if self.end_date else None
        if start and not end:
            end = start
        if end and not start:
            start = end
        if start > end:
            start, end = end, start
        out, cur = [], start
        while cur <= end:
            out.append(cur.strftime(fmt))
            cur += timedelta(days=1)
        return out

    @staticmethod
    def _expand(path, date_str, hour=None):
        y, m, d = date_str.split('-')
        res = path
        res = res.replace('{YYYY-MM-DD}', date_str)
        res = res.replace('{YYYYMMDD}', y + m + d)
        res = res.replace('{YYYY}', y).replace('{MM}', m).replace('{DD}', d)
        res = res.replace('{MMDD}', m + d)
        if hour is not None:
            res = res.replace('{HH}', f'{hour:02d}')
        return res

    def _candidate_files(self, paths, dates, dead_hosts=frozenset()):
        """
        (정렬키, 경로) 후보 리스트 생성 — 순수 문자열 계산, 네트워크 I/O 없음.
        {HH} 패턴은 0~23시 경로를 모두 생성한다. 존재 여부는 읽기 단계에서
        판정(없으면 read 가 None 반환)하므로 별도 stat/glob 왕복을 하지 않는다.
        dead_hosts 경로는 제외.
        """
        out, seen = [], set()
        for path in paths:
            if extract_host(path) in dead_hosts:
                continue
            has_hh = '{HH}' in path
            for ds in dates:
                if has_hh:
                    for hh in range(24):
                        p = self._expand(path, ds, hh)
                        if p not in seen:
                            seen.add(p)
                            out.append(((ds, f'{hh:02d}'), p))
                else:
                    p = self._expand(path, ds)
                    if p not in seen:
                        seen.add(p)
                        out.append(((ds, ''), p))
        # 파일명에 0-패딩된 시(HH)가 들어가므로 문자열 정렬 = 시간 정렬
        out.sort(key=lambda x: (x[0][0], x[0][1]))
        return out

    def _glob(self, pattern):
        """디렉터리 리스팅(존재 파일만). 테스트에서 대체 가능."""
        try:
            return glob.glob(pattern)
        except OSError:
            return []

    def _read_file_bytes(self, path):
        """
        파일을 한 번에 통째로 읽어 bytes 반환 (없거나 접근불가면 None).
        UNC(네트워크 공유)에서 mmap 의 페이지폴트 왕복을 피하려고 단일 순차읽기 사용.
        """
        try:
            with open(path, 'rb') as f:
                return f.read()
        except FileNotFoundError:
            return None
        except OSError as e:
            self.errors.append({'error': 'ARS 파일 접근 오류', 'details': f'{path}: {e}'})
            return None

    def _detect_encoding_bytes(self, data):
        """이미 읽은 bytes 로 인코딩 판정 (UTF-8 우선, 실패 시 CP949)."""
        sample = data[:65536]
        try:
            sample.decode('utf-8')
            return 'utf-8'
        except UnicodeDecodeError as e:
            if e.start >= len(sample) - 3:   # 경계에서 잘린 멀티바이트
                return 'utf-8'
            return 'cp949'

    def _iter_lines_from_bytes(self, data):
        """읽어둔 bytes 를 인코딩 판정 후 줄 단위로 (재-네트워크 읽기 없음)."""
        enc = self._detect_encoding_bytes(data)
        text = data.decode(enc, errors='replace')
        for line in text.splitlines(keepends=True):
            yield line

    # ── 파일 IO (테스트에서 대체 가능) ─────────────────────
    def _path_exists(self, path):
        return os.path.exists(path)

    def _mmap_contains(self, path, needle_bytes):
        try:
            with open(path, 'rb') as f:
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    return mm.find(needle_bytes) != -1
                finally:
                    mm.close()
        except ValueError:
            return False  # 빈 파일
        except OSError as e:
            self.errors.append({'error': 'ARS 파일 접근 오류', 'details': f'{path}: {e}'})
            return False

    def _detect_encoding(self, path):
        """
        파일 인코딩 감지. UTF-8 우선, 실패 시 CP949(한국 윈도우) 폴백.
        (샘플 64KB만 검사, 경계에서 잘린 멀티바이트는 UTF-8로 간주)
        """
        try:
            with open(path, 'rb') as f:
                sample = f.read(65536)
        except OSError:
            return 'utf-8'
        try:
            sample.decode('utf-8')
            return 'utf-8'
        except UnicodeDecodeError as e:
            if e.start >= len(sample) - 3:   # 경계에서 잘린 멀티바이트 문자
                return 'utf-8'
            return 'cp949'

    def _iter_lines(self, path, encoding=None):
        enc = encoding or self._detect_encoding(path)
        with open(path, 'r', encoding=enc, errors='replace') as f:
            for line in f:
                yield line

    # ── 서버 1대 처리 ──────────────────────────────────────
    def _process_server(self, server, needle):
        label = get_server_label(server)
        paths = get_log_paths(server, 'inbound')
        if not paths:
            return []

        # 1) 먼저 호스트 연결 (경로 템플릿에서 호스트 추출 — 플레이스홀더 영향 없음)
        #    연결 전에 UNC stat/glob 을 날리면 암시적 연결 시도로 매우 느려지므로 순서가 중요.
        conn_results = self.conn.connect_for_paths(paths)
        dead_hosts = {h for h, (ok, err) in conn_results.items() if not ok}
        for h, (ok, err) in conn_results.items():
            if not ok and err:
                self.errors.append(err)

        # 2) 연결 후 후보 파일 경로 생성 (네트워크 I/O 없음)
        candidates = self._candidate_files(paths, self._date_range(), dead_hosts)
        if not candidates:
            return []

        needle_bytes = needle.encode('utf-8', 'ignore')

        # ── 1단계: 파일을 각각 1회 통째로 읽어 (a)존재/(b)키 포함 판정.
        #    읽은 bytes 는 캐시해 2단계 디코드에 재사용 → 파일당 네트워크 읽기 1회.
        file_bytes = [None] * len(candidates)
        hit_flags = [False] * len(candidates)
        with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as ex:
            futs = {ex.submit(self._read_file_bytes, p): i
                    for i, (_, p) in enumerate(candidates)}
            for f in as_completed(futs):
                i = futs[f]
                data = f.result()
                file_bytes[i] = data
                if data is not None:
                    hit_flags[i] = (data.find(needle_bytes) != -1)

        hit_idxs = [i for i, h in enumerate(hit_flags) if h]
        if not hit_idxs:
            return []
        earliest, last_hit = hit_idxs[0], hit_idxs[-1]

        # ── 2단계: (earliest-1)부터 캐시된 bytes 로 디코드 + 채널 경계 상태머신 ──
        sm = _ChannelStateMachine(needle)
        start_idx = max(0, earliest - 1)
        for i in range(start_idx, len(candidates)):
            data = file_bytes[i]
            must_decode = hit_flags[i] or sm.has_open() or (i == start_idx and i < earliest)
            if must_decode and data is not None:
                fname = os.path.basename(candidates[i][1])
                for line in self._iter_lines_from_bytes(data):
                    sm.feed(line, source=fname)
            if i >= last_hit and not sm.has_open():
                break
        sm.flush()

        for call in sm.emitted:
            call['server'] = label
        return sm.emitted

    # ── 진입점 (인덱스 기반) ────────────────────────────────
    def search_inbound(self, phone=None, cust_id=None):
        """
        핸드폰 또는 고객ID로 ARS 인바운드 콜 검색 — 인덱스(SQLite) 조회 후
        각 콜의 원본 파일에서 [start_offset, end_offset] 구간만 읽어 복원한다.
        6GB 풀스캔 없음. 각 call 의 'cust_id' 가 VGW 연결키.
        """
        needle = (phone or cust_id or '').strip()
        if not needle:
            return {'success': False, 'message': '핸드폰 또는 고객ID를 입력하세요', 'calls': []}

        store = self.index_store
        if store is None:
            from ars_index_store import get_default_store
            store = get_default_store()

        # 대상 서버 라벨(선택된 ARS 서버로 제한)
        targets = get_enabled_servers(server_type='ARS', purpose='inbound',
                                      server_ids=self.server_ids, access_method='unc')
        labels = [get_server_label(s) for _, s in targets] if targets else None

        rows = store.search(phone=phone, cust_id=cust_id,
                            start_date=self.start_date, end_date=self.end_date,
                            servers=labels or None)
        if not rows:
            return {'success': True, 'search_key': needle, 'call_count': 0,
                    'calls': [], 'errors': self.errors or None}

        # 매칭 콜이 든 파일들의 호스트 연결
        self.conn.connect_for_paths([r['file_path'] for r in rows])

        calls = []
        try:
            for r in rows:
                lines = self._read_call_lines(
                    r['file_path'], r['start_offset'], r['end_offset'], r['channel'])
                st = (r.get('start_time') or '')
                et = (r.get('end_time') or '')
                calls.append({
                    'server': r.get('server'),
                    'channel': r.get('channel'),
                    'ucid': r.get('ucid'),
                    'start_time': st.split(' ', 1)[1] if ' ' in st else st,  # HH:MM:SS
                    'end_time': et.split(' ', 1)[1] if ' ' in et else et,
                    'end_by': r.get('end_by'),
                    'cust_id': r.get('cust_id'),
                    'phone': r.get('phone'),
                    'line_count': r.get('line_count'),
                    'source_files': [os.path.basename(r.get('file_path') or '')],
                    'lines': lines,
                })
        finally:
            self.conn.disconnect_all()

        calls.sort(key=lambda c: (c.get('start_time') or ''))
        return {
            'success': True,
            'search_key': needle,
            'call_count': len(calls),
            'calls': calls,
            'errors': self.errors if self.errors else None,
        }

    def _read_call_lines(self, path, start_offset, end_offset, channel):
        """원본 파일의 [start_offset, end_offset) 만 읽어 해당 채널 라인만 반환."""
        try:
            with open(path, 'rb') as f:
                f.seek(start_offset or 0)
                blob = f.read((end_offset or 0) - (start_offset or 0))
        except OSError as e:
            self.errors.append({'error': 'ARS 원본 읽기 오류',
                                'details': f'{path}: {e} (원본이 정리됐을 수 있음)'})
            return []
        enc = self._detect_encoding_bytes(blob) if blob else 'utf-8'
        text = blob.decode(enc, errors='replace')
        # 구간엔 다른 채널이 섞여 있으므로 이 콜의 채널만 필터 (상태머신 결과와 동일)
        return [l for l in text.splitlines(keepends=True) if _channel_of(l) == channel]

    # ── 패턴(정규식) 검색 ──────────────────────────────────
    def search_by_pattern(self, pattern):
        """
        ARS(UNC) 로그에서 정규식 패턴을 포함하는 라인 원문을 반환.

        ARS 는 셸이 없어 서버측 grep 을 못 하므로 후보 파일을 파일당 1회 통째로
        읽어(_read_file_bytes) 라인 단위로 매칭한다. {HH} 시간당 파일이 크므로
        날짜 범위를 넓게 잡으면 느려질 수 있다(파일은 순차 처리로 메모리 보호).

        Returns:
            {'success', 'pattern', 'result_count', 'results':[{line,server,type,file,timestamp}], 'errors'}
            timestamp 은 표시용 'YYYY-MM-DD HH:MM:SS' (접두부 없으면 HH:MM:SS).
        """
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return {'success': False, 'message': f'잘못된 정규식: {str(e)}', 'results': []}

        # 선택된 ARS 서버(운영구분/업무 필터는 프런트에서 server_ids 로 반영됨)
        targets = get_enabled_servers(server_type='ARS', server_ids=self.server_ids,
                                      access_method='unc')
        if not targets:
            return {'success': True, 'pattern': pattern, 'result_count': 0,
                    'results': [], 'errors': self.errors or None}

        dates = self._date_range()
        results = []

        try:
            for idx, server in targets:
                label = get_server_label(server)
                # inbound + outbound 전체 경로 (패턴 검색은 용도 구분 없음)
                paths = get_log_paths(server)
                if not paths:
                    continue

                # 호스트 연결 (연결 실패 호스트는 후보에서 제외)
                conn_results = self.conn.connect_for_paths(paths)
                dead_hosts = {h for h, (ok, err) in conn_results.items() if not ok}
                for h, (ok, err) in conn_results.items():
                    if not ok and err:
                        self.errors.append(err)

                candidates = self._candidate_files(paths, dates, dead_hosts)

                # 파일 순차 처리 (동시에 모든 파일을 메모리에 올리지 않음)
                for _, path in candidates:
                    data = self._read_file_bytes(path)
                    if not data:
                        continue
                    enc = self._detect_encoding_bytes(data)
                    fname = os.path.basename(path)
                    for line in data.decode(enc, errors='replace').splitlines():
                        if rx.search(line):
                            results.append({
                                'line': line.strip(),
                                'server': label,
                                'type': 'ARS',
                                'file': fname,
                                'timestamp': _datetime_of(line),
                            })
                    del data  # 다음 파일 전에 해제
        finally:
            self.conn.disconnect_all()

        return {'success': True, 'pattern': pattern, 'result_count': len(results),
                'results': results, 'errors': self.errors if self.errors else None}
