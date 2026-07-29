#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
콜 분석 모듈 (VGW/AICC) — v4
- "UCID 없음" 에러 콜 일괄 분석
- 세션키별 시작/종료시간, 전화번호, UCID, END값, CALL09 추출
- log_paths 는 config_manager 헬퍼로 purpose 별 조회
"""

import re
import logging
from datetime import datetime, timedelta

from ssh_fetcher import OpenSSHLogFetcher
from config_manager import get_enabled_servers, get_log_paths, get_server_label

logger = logging.getLogger(__name__)


class CallAnalyzer:
    """에러 콜 분석 엔진 (AICC/VGW 대상)."""

    def __init__(self, start_date=None, end_date=None, server_ids=None, purpose='outbound'):
        self.start_date = start_date
        self.end_date = end_date
        self.server_ids = server_ids
        self.purpose = purpose
        self.fetcher = OpenSSHLogFetcher()
        self.errors = []

    def _get_date_range(self):
        """날짜 범위 리스트 반환"""
        if not self.start_date and not self.end_date:
            return []
        try:
            fmt = '%Y-%m-%d'
            start = datetime.strptime(self.start_date, fmt) if self.start_date else None
            end = datetime.strptime(self.end_date, fmt) if self.end_date else None
            if start and not end:
                end = start
            if end and not start:
                start = end
            if start > end:
                start, end = end, start
            dates, current = [], start
            while current <= end:
                dates.append(current.strftime(fmt))
                current += timedelta(days=1)
            return dates
        except ValueError as e:
            self.errors.append({'error': f'날짜 형식 오류: {str(e)}'})
            return []

    def _get_target_servers(self):
        """검색 대상 AICC 서버 목록 (purpose 경로 보유분만)"""
        targets = get_enabled_servers(
            server_type='AICC', purpose=self.purpose, server_ids=self.server_ids
        )
        if not targets:
            self.errors.append({'error': '검색 대상 서버 없음'})
        return targets

    def _extract_timestamp(self, line):
        """로그 라인에서 타임스탬프 추출 (HH:mm:ss 형태로 반환)"""
        patterns = [
            r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})',
            r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})',
        ]
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                return match.group(1)
        return None

    def _extract_timestamp_hhmmss(self, line):
        """로그 라인에서 HHmmss 형태 시간만 추출"""
        ts = self._extract_timestamp(line)
        if ts:
            match = re.search(r'(\d{2}:\d{2}:\d{2})', ts)
            if match:
                return match.group(1)
        return None

    def _extract_session_keys(self, lines):
        """라인 목록에서 세션키(pom:XXXX:XXXXXXX) 추출 (순서 유지, 중복 제거)"""
        keys = []
        patterns = [
            r'(?:call)?[Ss]ession[Kk]ey[\\\"]*\s*[:=]\s*[\\\"]*\s*(pom:[0-9:]+)',
        ]
        for line in lines:
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    key = match.group(1)
                    if key not in keys:
                        keys.append(key)
                    break
        return keys

    def _extract_cust_tlno(self, lines):
        """전화번호 추출: custTlno 또는 custHpno (하이픈 제거)"""
        pattern = r'(?:custTlno|custHpno)[\\\"]*\s*[:=]\s*[\\\"]*([0-9\-]+)'
        for line in lines:
            match = re.search(pattern, line)
            if match:
                return match.group(1).replace('-', '')
        return None

    def _extract_ucid(self, lines):
        """UCID 추출"""
        pattern = r'ucid[\\\"]*\s*[:=]\s*[\\\"]*([0-9a-zA-Z]+)'
        for line in lines:
            match = re.search(pattern, line)
            if match:
                return match.group(1)
        return None

    def _extract_recend_info(self, lines):
        """recend.do LINE에서 종료시간 + requestData 추출."""
        for line in lines:
            if '"녹취연동-요청-recend.do"' in line or '녹취연동-요청-recend.do' in line:
                end_time = self._extract_timestamp_hhmmss(line)
                request_data = None
                rd_match = re.search(r'"requestData"\s*:\s*(\{[^}]+\})', line)
                if rd_match:
                    request_data = rd_match.group(1)
                return end_time, request_data
        return None, None

    def _build_call09(self, ucid, phone):
        """CALL09 값 생성"""
        ucid_val = ucid or ''
        phone_val = phone or ''
        return f'{ucid_val}| |{phone_val}| |O||||||'

    def _patch_call09_in_request_data(self, request_data, call09_value):
        """requestData 안의 CALL09 치환"""
        if not request_data or not call09_value:
            return request_data
        patched = re.sub(
            r'("CALL09"\s*:\s*)"[^"]*"',
            f'\\1"{call09_value}"',
            request_data
        )
        return patched

    def analyze_error_calls(self, error_pattern=None):
        """에러 콜 일괄 분석."""
        if not error_pattern:
            error_pattern = '"description" : "UCID 없음"'

        try:
            targets = self._get_target_servers()
            if not targets:
                return {
                    'success': False,
                    'message': '검색 대상 서버가 없습니다.',
                    'errors': self.errors
                }

            dates = self._get_date_range()

            # ── 1단계: 에러 패턴 검색
            logger.info(f"1단계: 에러 패턴 검색 — {error_pattern}")
            error_lines = []
            error_line_by_session = {}

            for idx, server in targets:
                server_id = get_server_label(server)
                log_paths = get_log_paths(server, self.purpose)

                lines, errors = self.fetcher.grep_remote(
                    server, log_paths, dates, error_pattern
                )
                if errors:
                    self.errors.extend(errors)

                for line in lines:
                    error_lines.append({'line': line.strip(), 'server': server_id})

            if not error_lines:
                return {
                    'success': False,
                    'message': f'검색 패턴에 해당하는 로그를 찾을 수 없습니다: {error_pattern}',
                    'errors': self.errors
                }

            raw_lines = [item['line'] for item in error_lines]
            if raw_lines:
                logger.info(f"[디버그] 에러 라인 샘플: {raw_lines[0][:200]}")

            session_keys = self._extract_session_keys(raw_lines)
            logger.info(f"[디버그] 추출된 세션키 수: {len(session_keys)}")

            if not session_keys:
                return {
                    'success': False,
                    'message': f'에러 로그에서 sessionKey를 찾을 수 없습니다 (에러 라인: {len(raw_lines)}줄)',
                    'errors': self.errors
                }

            for item in error_lines:
                keys = self._extract_session_keys([item['line']])
                for k in keys:
                    if k not in error_line_by_session:
                        error_line_by_session[k] = item['line']

            logger.info(f"1단계 완료: {len(error_lines)}줄, 세션키 {len(session_keys)}개")

            # ── 2단계: 세션키 일괄 grep (서버당 1회, 50개씩 분할)
            logger.info(f"2단계: {len(session_keys)}개 세션키 일괄 grep")
            all_session_lines = []

            for idx, server in targets:
                server_id = get_server_label(server)
                log_paths = get_log_paths(server, self.purpose)

                chunk_size = 50
                for chunk_start in range(0, len(session_keys), chunk_size):
                    chunk = session_keys[chunk_start:chunk_start + chunk_size]
                    grep_pattern = '|'.join(chunk)

                    lines, errors = self.fetcher.grep_remote(
                        server, log_paths, dates, grep_pattern, use_extended=True
                    )
                    if errors:
                        self.errors.extend(errors)
                    all_session_lines.extend(lines)

            logger.info(f"2단계 완료: 총 {len(all_session_lines)}줄 수집")

            # ── 3단계: 세션키별 분류 + 파싱
            logger.info("3단계: 세션키별 분류 및 파싱")

            session_line_map = {k: [] for k in session_keys}
            for line in all_session_lines:
                for key in session_keys:
                    if key in line:
                        session_line_map[key].append(line)
                        break

            results = []
            for i, session_key in enumerate(session_keys):
                session_lines = session_line_map.get(session_key, [])

                start_time = None
                if session_key in error_line_by_session:
                    start_time = self._extract_timestamp_hhmmss(
                        error_line_by_session[session_key]
                    )

                phone = self._extract_cust_tlno(session_lines)
                ucid = self._extract_ucid(session_lines)
                end_time, request_data = self._extract_recend_info(session_lines)
                call09 = self._build_call09(ucid, phone)
                patched_request_data = self._patch_call09_in_request_data(request_data, call09)

                results.append({
                    'no': i + 1,
                    'session_key': session_key,
                    'start_time': start_time or '(없음)',
                    'end_time': end_time or 'recend.do 없음',
                    'phone': phone or '(없음)',
                    'ucid': ucid or '(없음)',
                    'call09': call09,
                    'request_data': patched_request_data or '(없음)',
                    'request_data_raw': request_data or '(없음)',
                })

            logger.info(f"분석 완료: {len(results)}건")

            return {
                'success': True,
                'error_pattern': error_pattern,
                'total_errors': len(error_lines),
                'total_sessions': len(session_keys),
                'results': results,
                'errors': self.errors if self.errors else None
            }

        except Exception as e:
            logger.exception(f"콜 분석 오류: {e}")
            return {
                'success': False,
                'message': f'분석 오류: {str(e)}',
                'errors': self.errors
            }
