#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH 원격 로그 수집 모듈
- OpenSSH 네이티브 사용
- 명령 병합: 서버당 1회 SSH로 다중 날짜/경로 처리
- 서버측 grep으로 전송량 최소화
"""

import subprocess
import logging
from pathlib import Path
from enum import Enum

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

    def _execute_ssh(self, ssh_cmd, timeout=30):
        """SSH 명령 실행 및 결과 반환"""
        ssh_target = ssh_cmd[-2] if len(ssh_cmd) > 2 else 'unknown'

        try:
            logger.debug(f"SSH 명령: {' '.join(ssh_cmd[:5])}...")
            result = subprocess.run(
                ssh_cmd,
                capture_output=True, text=True,
                timeout=timeout,
                encoding='utf-8', errors='ignore'
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip()
                logger.error(f"SSH 오류 (코드 {result.returncode}): {error_msg}")
                return None, self._classify_error(ssh_target, error_msg)

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

        logger.info(f"서버측 grep: {ssh_target} (패턴: {grep_pattern[:50]}...)")
        lines, error = self._execute_ssh(ssh_cmd, timeout=60)

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
        lines, error = self._execute_ssh(ssh_cmd, timeout=60)

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
        patterns = []

        for log_path in log_paths:
            # 플레이스홀더 유효성 검증
            if '{YYYY-MM-DD}' not in log_path and '{YYYYMMDD}' not in log_path:
                logger.warning(
                    f"플레이스홀더 없음 — 경로 스킵: {log_path} "
                    f"(예: /path/to/api.{{YYYY-MM-DD}}.log 형태로 등록하세요)"
                )
                continue

            if dates:
                # 각 날짜별로 플레이스홀더 치환
                for date in dates:
                    expanded = self._expand_placeholders(log_path, date)
                    patterns.append(expanded)
            else:
                # 날짜 미지정 시 플레이스홀더를 와일드카드로 치환
                expanded = log_path
                expanded = expanded.replace('{YYYY-MM-DD}', '*')
                expanded = expanded.replace('{YYYYMMDD}', '*')
                patterns.append(expanded)

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
        result = path_pattern

        # {YYYY-MM-DD} → 그대로 치환
        result = result.replace('{YYYY-MM-DD}', date_str)

        # {YYYYMMDD} → 하이픈 제거해서 치환
        compact_date = date_str.replace('-', '')
        result = result.replace('{YYYYMMDD}', compact_date)

        return result

    def _build_grep_command(self, grep_pattern, file_patterns, use_extended=False):
        """
        grep 명령 생성.
        use_extended=True이면 grep -E (확장 정규식, | 지원)
        """
        if not file_patterns:
            return None
        file_list = ' '.join(file_patterns)
        flag = '-Eh' if use_extended else '-h'
        return f"grep {flag} '{grep_pattern}' {file_list} 2>/dev/null"

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
