#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 원격 로그 수집 모듈
- OpenSSH 네이티브 사용
- 명령 병합: 서버당 1회 SSH로 다중 날짜/경로 처리
- 서버측 grep으로 전송량 최소화
"""

import shlex
import subprocess
import logging
from pathlib import Path
from enum import Enum

from config_manager import DATE_PLACEHOLDERS

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """에러 유형 정의"""
    FILE_NOT_FOUND = "파일 없음"
    PERMISSION_DENIED = "권한 없음"
    NETWORK_ERROR = "네트워크 오류"
    SSH_AUTH_ERROR = "SSH 인증 오류"
    SSH_NOT_FOUND = "OpenSSH 미설치"
    TIMEOUT_ERROR = "타임아웃"
    JSON_PARSE_ERROR = "JSON 파싱 오류"
    MEMORY_ERROR = "메모리 오류"
    UNKNOWN_ERROR = "알 수 없는 오류"


def check_openssh_installed():
    """OpenSSH 클라이언트 설치 여부 확인"""
    try:
        result = subprocess.run(
            ['ssh', '-V'],
            capture_output=True, text=True, timeout=5
        )
        logger.info(f"OpenSSH 확인: {result.stderr.strip()}")
        return True
    except FileNotFoundError:
        logger.error("OpenSSH 미설치")
        return False
    except Exception as e:
        logger.error(f"OpenSSH 확인 오류: {e}")
        return False


class OpenSSHLogFetcher:
    """OpenSSH를 이용한 원격 로그 수집"""

    def __init__(self):
        self.openssh_available = check_openssh_installed()
        if not self.openssh_available:
            logger.warning("OpenSSH 미설치 — SSH 기능 비활성화")

    def _build_ssh_target(self, server_config):
        """SSH 접속 대상 문자열 생성"""
        hostname = server_config.get('hostname')
        if hostname and not server_config.get('ip'):
            return hostname

        ip = server_config.get('ip')
        user = server_config.get('user', 'loguser')
        if ip:
            return f"{user}@{ip}"

        return None

    def _build_ssh_cmd(self, server_config, ssh_target):
        """SSH 명령어 기본 부분 생성"""
        ssh_cmd = ['ssh']
        ssh_cmd.extend([
            '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=3',
            '-o', 'StrictHostKeyChecking=accept-new',
        ])

        ssh_key = server_config.get('ssh_key_path')
        if ssh_key and Path(ssh_key).exists():
            ssh_cmd.extend(['-i', str(ssh_key)])

        ssh_port = server_config.get('ssh_port', 22)
        if ssh_port != 22:
            ssh_cmd.extend(['-p', str(ssh_port)])

        ssh_cmd.append(ssh_target)
        return ssh_cmd

    def _execute_ssh(self, ssh_cmd, timeout=30, ok_codes=(0,)):
        """
        SSH 명령 실행 및 결과 반환.

        Args:
            ok_codes: 정상으로 볼 원격 종료코드들.
                grep/cat 은 '매칭 없음 / 파일 없음' 에도 1 을 반환하므로
                검색 계열은 (0, 1) 을 넘겨 정상 무결과와 실제 장애를 구분한다.
                (구분하지 않으면 로그가 없는 서버마다 가짜 오류가 쌓여
                 진짜 SSH 장애를 가린다.)
        """
        ssh_target = ssh_cmd[-2] if len(ssh_cmd) > 2 else 'unknown'

        try:
            logger.debug(f"SSH 명령: {' '.join(ssh_cmd[:5])}...")
            result = subprocess.run(
                ssh_cmd,
                capture_output=True, text=True,
                timeout=timeout,
                encoding='utf-8', errors='ignore'
            )

            if result.returncode not in ok_codes:
                error_msg = result.stderr.strip()
                logger.error(f"SSH 오류 (코드 {result.returncode}): {error_msg}")
                return None, self._classify_error(ssh_target, error_msg)

            # 허용 코드지만 stderr 가 있으면(예: 잘못된 정규식) 로그로 남긴다.
            # '없는 파일' 메시지는 grep -s 가 억제하므로 여기 걸리지 않는다.
            if result.returncode != 0 and result.stderr.strip():
                logger.warning("원격 명령 경고 (코드 %s) %s: %s", result.returncode,
                               ssh_target, result.stderr.strip()[:300])

            lines = result.stdout.splitlines()
            return lines, None

        except subprocess.TimeoutExpired:
            logger.error(f"SSH 타임아웃: {ssh_target}")
            return None, {
                'server': ssh_target,
                'error': ErrorType.TIMEOUT_ERROR.value,
                'details': f'{timeout}초 타임아웃'
            }
        except FileNotFoundError:
            return None, {
                'error': ErrorType.SSH_NOT_FOUND.value,
                'details': 'OpenSSH 미설치'
            }
        except Exception as e:
            logger.exception(f"SSH 명령 실행 오류: {e}")
            return None, {
                'server': ssh_target,
                'error': ErrorType.UNKNOWN_ERROR.value,
                'details': str(e)
            }

    def _classify_error(self, ssh_target, error_msg):
        """SSH 에러 메시지 분류"""
        if 'Permission denied' in error_msg or 'publickey' in error_msg:
            return {
                'server': ssh_target,
                'error': ErrorType.SSH_AUTH_ERROR.value,
                'details': 'SSH 키 인증 실패. ssh-copy-id 실행 필요.'
            }
        elif 'Connection refused' in error_msg:
            return {
                'server': ssh_target,
                'error': ErrorType.NETWORK_ERROR.value,
                'details': 'SSH 연결 거부됨'
            }
        elif 'Connection timed out' in error_msg or 'Timeout' in error_msg:
            return {
                'server': ssh_target,
                'error': ErrorType.TIMEOUT_ERROR.value,
                'details': '연결 타임아웃'
            }
        else:
            return {
                'server': ssh_target,
                'error': ErrorType.UNKNOWN_ERROR.value,
                'details': error_msg
            }

    def grep_remote(self, server_config, log_paths, dates, grep_pattern, use_extended=False):
        """
        서버측 grep 실행 — 서버당 1회 SSH로 다중 경로/날짜 처리.

        Args:
            server_config: 서버 설정 dict
            log_paths: 로그 경로 리스트 ["/path/to/log", ...]
            dates: 날짜 리스트 ["2025-01-10", ...] 또는 빈 리스트(최신)
            grep_pattern: grep에 전달할 검색 패턴 (정규식)
            use_extended: True이면 grep -E (확장 정규식, | 지원)

        Returns:
            (lines, errors): 매칭된 라인 리스트, 에러 리스트
        """
        if not self.openssh_available:
            return [], [{'error': ErrorType.SSH_NOT_FOUND.value}]

        ssh_target = self._build_ssh_target(server_config)
        if not ssh_target:
            return [], [{'error': 'SSH 타겟 설정 오류'}]

        # 대상 파일 목록 생성
        file_patterns = self._build_file_patterns(log_paths, dates)
        if not file_patterns:
            return [], [{
                'server': ssh_target,
                'error': '검색할 파일 패턴 없음',
                'details': '로그 경로에 {YYYY-MM-DD} 또는 {YYYYMMDD} 플레이스홀더가 포함되어야 합니다'
            }]

        # grep 명령 조합
        remote_cmd = self._build_grep_command(grep_pattern, file_patterns, use_extended=use_extended)

        ssh_cmd = self._build_ssh_cmd(server_config, ssh_target)
        ssh_cmd.append(remote_cmd)

        logger.info(f"서버측 grep: {ssh_target} (패턴: {grep_pattern[:50]}..., "
                    f"파일 {len(file_patterns)}개)")
        # grep 종료코드: 0=매칭, 1=매칭 없음, 2=없는 파일이 섞였거나 오류.
        # 후보 파일은 존재 확인 없이 (경로 × 날짜) 조합으로 만들기 때문에 '없는
        # 파일'이 섞이는 것이 정상이고, 그때도 존재하는 파일의 매칭 결과는
        # stdout 으로 정상 출력된다. 2를 오류로 처리하면 경로/날짜가 늘어날수록
        # 없는 파일이 낄 확률이 100%에 수렴해 결과가 통째로 버려진다.
        lines, error = self._execute_ssh(ssh_cmd, timeout=60, ok_codes=(0, 1, 2))

        if error:
            return [], [error]

        if not lines:
            logger.info(f"매칭 없음: {ssh_target}")
            return [], []

        logger.info(f"grep 결과: {ssh_target} — {len(lines)}줄")
        return lines, []

    def fetch_full_log(self, server_config, log_paths, dates):
        """
        전체 로그 가져오기 (패턴 검색 등에서 사용).
        서버당 1회 SSH로 다중 경로/날짜 처리.
        """
        if not self.openssh_available:
            return [], [{'error': ErrorType.SSH_NOT_FOUND.value}]

        ssh_target = self._build_ssh_target(server_config)
        if not ssh_target:
            return [], [{'error': 'SSH 타겟 설정 오류'}]

        file_patterns = self._build_file_patterns(log_paths, dates)
        if not file_patterns:
            return [], [{
                'server': ssh_target,
                'error': '검색할 파일 패턴 없음',
                'details': '로그 경로에 {YYYY-MM-DD} 또는 {YYYYMMDD} 플레이스홀더가 포함되어야 합니다'
            }]

        # cat 명령으로 전체 로그 가져오기
        file_list = ' '.join(file_patterns)
        remote_cmd = f'cat {file_list} 2>/dev/null'

        ssh_cmd = self._build_ssh_cmd(server_config, ssh_target)
        ssh_cmd.append(remote_cmd)

        logger.info(f"로그 수집: {ssh_target}")
        # cat: 대상 파일이 하나도 없으면 1 (해당 날짜 로그 미존재 = 정상)
        lines, error = self._execute_ssh(ssh_cmd, timeout=60, ok_codes=(0, 1))

        if error:
            return [], [error]

        if not lines:
            return [], []

        logger.info(f"로그 수집 완료: {ssh_target} — {len(lines)}줄")
        return lines, []

    def _build_file_patterns(self, log_paths, dates):
        """
        로그 파일 경로 패턴 생성.

        log_paths의 각 항목은 반드시 플레이스홀더를 포함해야 함:
          - {YYYY-MM-DD}  → 2025-01-15
          - {YYYYMMDD}    → 20250115

        예시:
          /hli_app/log/.../api.{YYYY-MM-DD}.log
          /var/log/app/{YYYYMMDD}/server.log
        """
        patterns, seen = [], set()

        for log_path in log_paths:
            # 날짜 플레이스홀더 유효성 검증.
            # config_manager.DATE_PLACEHOLDERS 가 허용하는 형식을 모두 인정해야 한다.
            # 예전엔 {YYYY-MM-DD}/{YYYYMMDD} 만 봐서, {YYYY}-{MMDD} 같은 형식으로
            # 등록된 경로가 조용히 스킵됐다 — 경로를 여러 개 등록했을 때 그중
            # 일부만 검색되는 원인이었다.
            if not any(tok in log_path for tok in DATE_PLACEHOLDERS):
                logger.warning(
                    f"날짜 플레이스홀더 없음 — 경로 스킵: {log_path} "
                    f"(사용 가능: {', '.join(DATE_PLACEHOLDERS)})"
                )
                continue

            expanded_list = []
            if dates:
                expanded_list = [self._expand_placeholders(log_path, d) for d in dates]
            else:
                # 날짜 미지정 → 날짜 자리를 와일드카드로
                w = log_path
                for tok in DATE_PLACEHOLDERS:
                    w = w.replace(tok, '*')
                expanded_list = [w]

            for e in expanded_list:
                # 시(HH)는 날짜로 특정되지 않으므로 항상 와일드카드
                e = e.replace('{HH}', '*')
                if e not in seen:
                    seen.add(e)
                    patterns.append(e)

        return patterns

    def _expand_placeholders(self, path_pattern, date_str):
        """
        플레이스홀더를 실제 날짜로 치환.

        Args:
            path_pattern: "/log/api.{YYYY-MM-DD}.log"
            date_str: "2025-01-15" (YYYY-MM-DD 형식)

        Returns:
            치환된 경로
        """
        y, m, d = date_str.split('-')
        result = path_pattern
        result = result.replace('{YYYY-MM-DD}', date_str)
        result = result.replace('{YYYYMMDD}', y + m + d)
        result = result.replace('{YYYY}', y).replace('{MM}', m).replace('{DD}', d)
        result = result.replace('{MMDD}', m + d)
        return result

    def _build_grep_command(self, grep_pattern, file_patterns, use_extended=False):
        """
        grep 명령 생성.
        use_extended=True이면 grep -E (확장 정규식, | 지원)

        패턴은 사용자 입력(검색어/custId)과 로그에서 뽑은 값(sessionKey)에서 오므로
        반드시 shlex.quote 로 감싼다. 직접 따옴표로 감싸면 패턴에 작은따옴표가
        들어올 때 셸 인용이 깨져 검색이 실패하고, 원격 명령이 주입될 수 있다.
        (경로는 {YYYY-MM-DD} 미지정 시 '*' 글롭을 셸이 전개해야 하므로 인용하지 않음)
        """
        if not file_patterns:
            return None
        file_list = ' '.join(file_patterns)
        # -s: 없는/못 읽는 파일 메시지 억제 (2>/dev/null 대신 — 잘못된 정규식 같은
        #     진짜 오류는 stderr 로 남아 로그에 찍히게 한다)
        flag = '-Ehs' if use_extended else '-hs'
        return f"grep {flag} -- {shlex.quote(grep_pattern)} {file_list}"

    def test_connection(self, server_config):
        """SSH 연결 테스트"""
        if not self.openssh_available:
            return False, ErrorType.SSH_NOT_FOUND.value

        ssh_target = self._build_ssh_target(server_config)
        if not ssh_target:
            return False, 'SSH 타겟 설정 오류'

        ssh_cmd = self._build_ssh_cmd(server_config, ssh_target)
        ssh_cmd.append('echo OK')

        lines, error = self._execute_ssh(ssh_cmd, timeout=10)

        if error:
            return False, error.get('details', error.get('error', '연결 실패'))

        if lines and lines[0].strip() == 'OK':
            return True, '연결 성공'

        return False, '알 수 없는 응답'
