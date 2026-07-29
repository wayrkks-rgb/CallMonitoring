#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ARS 로그 접근 — SSH(Windows OpenSSH) 대체 경로  [UNC 대비책 / 장기 전환용]

기존 ars_fetcher.py(UNC/SMB) 와 '같은 인터페이스'를 목표로 한 SSH 버전.
서버 설정의 access_method == 'ssh' 인 ARS 서버에 대해 이 페처를 쓴다.

■ 접근 모델 (UNC 와 다른 점)
  - UNC 모드: log_paths 가 '\\host\share\...' (경로에서 host 추출)
  - SSH 모드: log_paths 가 그 서버의 '로컬 경로'(예: D:\ARSLOG\...{YYYY-MM-DD}.log)
              접속 대상(SSH target)은 서버설정의 hostname/ip/user/ssh_port/ssh_key_path
              → 서버 1대 = SSH 타깃 1개 + 로컬 경로들 (host 추출 개념 없음)

■ 왜 PowerShell -EncodedCommand 인가
  ssh → cmd.exe → powershell 로 넘어가며 따옴표가 3중으로 꼬인다.
  스크립트를 UTF-16LE base64 로 인코딩해 `powershell -EncodedCommand XXXX` 로
  넘기면 따옴표가 아예 없어 안전하다. 모든 원격 실행은 이 방식을 쓴다.

■ 제공 원시연산 (인덱서/페처 공용) : ArsSshIO
  - file_size(server, path)                 : (Get-Item).Length         [tail EOF]
  - read_range(server, path, offset, length): FileStream seek+read→b64  [tail/재구성]
  - grep(server, paths, pattern, regex)     : Select-String             [패턴검색]
  - exists(server, path)                    : Test-Path

■ 통합 지점 (직접 구현/테스트용 — 이 파일 하단 INTEGRATION NOTES 참고)
  1) search.py 에서 access_method 로 UNC/SSH 페처 분기
  2) ars_indexer.py 의 파일 읽기(_tail/_backfill)도 ArsSshIO 로 분기
  3) 서버설정 access_method 토글 (config_manager/servers/프런트) — 별도 제공
"""

import base64
import logging
import subprocess
from pathlib import Path

from ars_fetcher import ArsLogFetcher, _channel_of
from config_manager import get_enabled_servers, get_log_paths, get_server_label

logger = logging.getLogger(__name__)


# ── SSH/PowerShell 원시연산 ────────────────────────────────
class ArsSshIO:
    """
    ARS 서버(Windows OpenSSH)에 대한 파일 IO 원시연산.

    runner 를 주입하면 테스트에서 ssh 실행을 가짜로 대체할 수 있다
    (runner(cmd_list) -> (returncode, stdout_bytes, stderr_text)).
    """

    def __init__(self, connect_timeout=10, runner=None):
        self.connect_timeout = connect_timeout
        self._runner = runner or self._default_runner

    # SSH 타깃 문자열 (hostname 우선, 없으면 user@ip)
    @staticmethod
    def ssh_target(server):
        hostname = server.get('hostname')
        if hostname and not server.get('ip'):
            return hostname
        ip = server.get('ip')
        user = server.get('user', 'loguser')
        return f"{user}@{ip}" if ip else (hostname or None)

    def _ssh_base(self, server):
        cmd = ['ssh',
               '-o', 'ControlMaster=no',
               '-o', 'ControlPath=none',
               '-o', f'ConnectTimeout={self.connect_timeout}',
               '-o', 'ConnectionAttempts=1',
               '-o', 'StrictHostKeyChecking=accept-new',
               '-o', 'BatchMode=yes']
        key_path = server.get('ssh_key_path')
        if key_path and Path(key_path).exists():
            cmd += ['-i', str(key_path)]
        port = server.get('ssh_port', 22)
        if port and int(port) != 22:
            cmd += ['-p', str(port)]
        return cmd

    @staticmethod
    def _encode_ps(script):
        """PowerShell 스크립트 → -EncodedCommand 용 UTF-16LE base64."""
        return base64.b64encode(script.encode('utf-16-le')).decode('ascii')

    def _default_runner(self, cmd):
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               stdin=subprocess.DEVNULL, timeout=120)
            return p.returncode, p.stdout, (p.stderr or b'').decode('utf-8', 'ignore')
        except FileNotFoundError:
            return 127, b'', 'OpenSSH 미설치'
        except subprocess.TimeoutExpired:
            return 124, b'', 'SSH 타임아웃'

    def _run_ps(self, server, script):
        """서버에서 PowerShell 스크립트 실행 → (rc, stdout_bytes, stderr)."""
        target = self.ssh_target(server)
        if not target:
            return 1, b'', 'SSH 대상 없음(hostname/ip 확인)'
        remote = f'powershell -NoProfile -NonInteractive -EncodedCommand {self._encode_ps(script)}'
        cmd = self._ssh_base(server) + [target, remote]
        return self._runner(cmd)

    # ── 원시연산 ───────────────────────────────────────────
    def file_size(self, server, path):
        """파일 크기(byte). 없거나 오류면 None."""
        script = f"$ErrorActionPreference='Stop'; (Get-Item -LiteralPath '{path}').Length"
        rc, out, err = self._run_ps(server, script)
        if rc != 0:
            return None
        try:
            return int(out.decode('ascii', 'ignore').strip())
        except (ValueError, TypeError):
            return None

    def read_range(self, server, path, offset, length):
        """[offset, offset+length) 바이트를 읽어 bytes 반환. 없거나 오류면 None.

        - FileShare=ReadWrite : 로그가 계속 쓰이는 중에도 읽기 가능
        - 결과는 base64 로 회수 후 디코드 (바이너리 안전)
        """
        if length is None or length <= 0:
            return b''
        script = (
            "$ErrorActionPreference='Stop';"
            f"$f=[IO.File]::Open('{path}',[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite);"
            "try{"
            f"[void]$f.Seek([long]{int(offset or 0)},[IO.SeekOrigin]::Begin);"
            f"$b=New-Object byte[] {int(length)};"
            "$n=$f.Read($b,0,$b.Length);"
            "[Convert]::ToBase64String($b,0,$n)"
            "}finally{$f.Close()}"
        )
        rc, out, err = self._run_ps(server, script)
        if rc != 0:
            logger.debug(f"read_range 실패 {path}: {err[:150]}")
            return None
        try:
            return base64.b64decode(out.decode('ascii', 'ignore').strip() or '')
        except Exception:
            return None

    def read_all(self, server, path):
        """파일 전체 bytes (없으면 None). 대용량엔 grep 을 우선 사용할 것."""
        size = self.file_size(server, path)
        if size is None:
            return None
        if size == 0:
            return b''
        return self.read_range(server, path, 0, size)

    def exists(self, server, path):
        script = f"if(Test-Path -LiteralPath '{path}'){{'1'}}else{{'0'}}"
        rc, out, err = self._run_ps(server, script)
        return rc == 0 and out.decode('ascii', 'ignore').strip() == '1'

    def grep(self, server, paths, pattern, regex=True, encoding='utf8'):
        """서버측 Select-String 으로 매칭 라인만 회수.

        Args:
            paths: 로컬 경로 리스트 (존재하지 않는 경로는 무시됨)
            regex: True=.NET 정규식, False=리터럴(SimpleMatch)
            encoding: 'utf8' | 'default'(CP949 등 시스템 기본) | 'unicode' ...
        Returns:
            (lines: list[str], error: str|None)
        """
        if not paths:
            return [], None
        # 경로 배열 리터럴 구성 (작은따옴표 이스케이프)
        arr = ','.join("'" + p.replace("'", "''") + "'" for p in paths)
        pat = pattern.replace("'", "''")
        simple = '' if regex else '-SimpleMatch '
        # 존재하는 경로만 필터 → Select-String (여러 파일 한 번에)
        # 주의: 이 문자열은 f-string 이 아니므로 중괄호를 이스케이프하지 말 것
        #       ('{{ }}' 로 쓰면 PowerShell 에 그대로 전달돼 스크립트블록 오류 → 출력 0줄)
        script = (
            "$ErrorActionPreference='SilentlyContinue';"
            f"$ps=@({arr}) | Where-Object {{ Test-Path -LiteralPath $_ }};"
            "if($ps){"
            f"Select-String -LiteralPath $ps -Encoding {encoding} {simple}-Pattern '{pat}'"
            " | ForEach-Object { $_.Line }"
            "}"
        )
        rc, out, err = self._run_ps(server, script)
        if rc != 0 and err:
            return [], err[:200]
        text = out.decode('utf-8', 'ignore')
        lines = [l.rstrip('\r\n') for l in text.splitlines() if l.strip()]
        return lines, None


# ── SSH 기반 ARS 페처 (UNC 페처와 동일 인터페이스) ──────────
class ArsSshLogFetcher(ArsLogFetcher):
    """
    ArsLogFetcher(UNC) 의 SSH 버전.
    IO 원시연산만 SSH 로 바꾸고, 날짜/후보파일/상태머신 등 IO-무관 로직은 부모 재사용.
    """

    def __init__(self, start_date=None, end_date=None, server_ids=None,
                 index_store=None, io=None):
        # 부모의 UNC 연결관리자는 쓰지 않으므로 no-op 로 주입
        super().__init__(start_date, end_date, server_ids,
                         conn_manager=_NoopConn(), index_store=index_store)
        self.io = io or ArsSshIO()

    def _ars_ssh_servers(self):
        """선택된 ARS 서버 중 access_method=='ssh' 인 것만 (idx, server)."""
        return get_enabled_servers(server_type='ARS', server_ids=self.server_ids,
                                   access_method='ssh')

    def _label_to_server(self):
        return {get_server_label(s): s for _, s in self._ars_ssh_servers()}

    # ── 패턴 검색 (서버측 Select-String) ───────────────────
    def search_by_pattern(self, pattern):
        try:
            import re
            re.compile(pattern)
        except re.error as e:
            return {'success': False, 'message': f'잘못된 정규식: {str(e)}', 'results': []}

        servers = self._ars_ssh_servers()
        if not servers:
            return {'success': True, 'pattern': pattern, 'result_count': 0,
                    'results': [], 'errors': self.errors or None}

        dates = self._date_range()
        results = []
        for _, server in servers:
            label = get_server_label(server)
            paths = get_log_paths(server)          # SSH 모드: 로컬 경로들
            if not paths:
                continue
            # 날짜/시(HH) 전개 → 로컬 경로 후보
            candidates = [p for _, p in self._candidate_files(paths, dates)]
            lines, err = self.io.grep(server, candidates, pattern, regex=True)
            if err:
                self.errors.append({'server': label, 'error': 'SSH 검색 오류', 'details': err})
            for line in lines:
                results.append({'line': line.strip(), 'server': label,
                                'type': 'ARS', 'file': '', 'timestamp': _time_of_safe(line)})
        return {'success': True, 'pattern': pattern, 'result_count': len(results),
                'results': results, 'errors': self.errors or None}

    # ── 인바운드 검색 (인덱스 + 오프셋 SSH 읽기) ────────────
    def search_inbound(self, phone=None, cust_id=None):
        needle = (phone or cust_id or '').strip()
        if not needle:
            return {'success': False, 'message': '핸드폰 또는 고객ID를 입력하세요', 'calls': []}

        store = self.index_store
        if store is None:
            from ars_index_store import get_default_store
            store = get_default_store()

        servers = self._ars_ssh_servers()
        label_map = {get_server_label(s): s for _, s in servers}
        labels = list(label_map.keys()) or None

        rows = store.search(phone=phone, cust_id=cust_id,
                            start_date=self.start_date, end_date=self.end_date,
                            servers=labels)
        if not rows:
            return {'success': True, 'search_key': needle, 'call_count': 0,
                    'calls': [], 'errors': self.errors or None}

        calls = []
        for r in rows:
            server = label_map.get(r.get('server'))
            if not server:
                continue
            lines = self._read_call_lines_ssh(
                server, r['file_path'], r['start_offset'], r['end_offset'], r['channel'])
            st = (r.get('start_time') or '')
            et = (r.get('end_time') or '')
            calls.append({
                'server': r.get('server'), 'channel': r.get('channel'),
                'ucid': r.get('ucid'),
                'start_time': st.split(' ', 1)[1] if ' ' in st else st,
                'end_time': et.split(' ', 1)[1] if ' ' in et else et,
                'end_by': r.get('end_by'), 'cust_id': r.get('cust_id'),
                'phone': r.get('phone'), 'line_count': r.get('line_count'),
                'source_files': [r.get('file_path') or ''],
                'lines': lines,
            })
        calls.sort(key=lambda c: (c.get('start_time') or ''))
        return {'success': True, 'search_key': needle, 'call_count': len(calls),
                'calls': calls, 'errors': self.errors or None}

    def _read_call_lines_ssh(self, server, path, start_offset, end_offset, channel):
        """SSH 오프셋 읽기로 콜 구간만 회수 → 해당 채널 라인만."""
        length = (end_offset or 0) - (start_offset or 0)
        blob = self.io.read_range(server, path, start_offset or 0, length)
        if not blob:
            if blob is None:
                self.errors.append({'error': 'ARS(SSH) 원본 읽기 오류',
                                    'details': f'{path} (원본 정리 가능성)'})
            return []
        enc = self._detect_encoding_bytes(blob)
        text = blob.decode(enc, errors='replace')
        return [l for l in text.splitlines(keepends=True) if _channel_of(l) == channel]


# ── 부모의 UNC 연결관리자 자리채움 (SSH 모드는 연결관리 불필요) ──
class _NoopConn:
    def connect_for_paths(self, paths):
        return {}
    def disconnect_all(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass


def _time_of_safe(line):
    """라인에서 표시용 'YYYY-MM-DD HH:MM:SS' 추출 (날짜 접두부 없으면 HH:MM:SS, 실패 시 '')."""
    import re
    s = line or ''
    m = re.search(r'\b(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\b', s)
    if m:
        return f'{m.group(1)} {m.group(2)}'
    m = re.search(r'\b(\d{2}:\d{2}:\d{2})\b', s)
    return m.group(1) if m else ''


# =====================================================================
# INTEGRATION NOTES (직접 구현/테스트용)
# ---------------------------------------------------------------------
# [1] 서버설정 access_method 토글  (config_manager.py / servers.py / 프런트)
#     - server['access_method'] in ('unc','ssh'), 기본 'unc'
#     - SSH 모드에선 log_paths 를 '로컬 경로'로 입력 (예: D:\ARSLOG\..{YYYY-MM-DD}.log)
#     - SSH 접속값은 기존 AICC 처럼 hostname/ip/user/ssh_port/ssh_key_path 사용
#
# [2] search.py 분기 (인바운드/패턴)
#       ars_unc = ArsLogFetcher(start, end, server_ids)          # access_method=='unc'
#       ars_ssh = ArsSshLogFetcher(start, end, server_ids)       # access_method=='ssh'
#       # 각 페처는 자기 방식 서버만 처리하므로 결과를 merge 하면 됨
#       # (ArsSshLogFetcher 는 내부에서 ssh 서버만 필터, 기존 ArsLogFetcher 는
#       #  unc 서버만 처리하도록 get_enabled_servers 에 access_method 필터 추가 권장)
#
# [3] ars_indexer.py (tail/backfill) SSH 지원
#     - SSH 모드 서버는 open() 대신 ArsSshIO 사용:
#         EOF   = io.file_size(server, path)
#         chunk = io.read_range(server, path, pending_offset, EOF-pending_offset)
#       그 bytes 를 기존 _ChannelStateMachine 에 그대로 흘리면 offset 추적 동일.
#     - 인덱스에 저장하는 file_path 를 'SSH 모드=로컬경로'로 저장 (라벨은 그대로).
#       → 재구성 시 라벨→서버설정→SSH타깃 으로 해석 (본 파일 search_inbound 참고)
#
# [4] 인코딩: 로그가 CP949 면 grep encoding='default', read_range 는 바이트라 무관.
# [5] 성능: tail 은 폴링마다 ssh 프로세스가 뜬다. 잦으면 VGW 모니터처럼
#     '엔드포인트당 상시 원격 리더' 로 바꿔 프로세스 기동비용 제거 가능.
# =====================================================================
