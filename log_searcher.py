#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
로그 검색 모듈 (VGW/AICC) — v4
- custId 기반 플로우 추적 (서버측 grep)
- 패턴(정규식) 검색
- 서버 지정 검색 지원
- log_paths 는 config_manager 헬퍼로 purpose(inbound/outbound)별 조회
"""

import re
import logging
from datetime import datetime, timedelta

from ssh_fetcher import OpenSSHLogFetcher
from config_manager import get_enabled_servers, get_log_paths, get_server_label

logger = logging.getLogger(__name__)


class LogSearcher:
    """
    로그 검색 엔진 (AICC/VGW 대상).
    서버측 grep → 세션 키 추출 → 세션 키 grep 2단계로 동작.

    purpose:
        'outbound' (기본) — 아웃바운드 로그 경로 사용
        'inbound'         — 인바운드 VGW 경로 사용 (ARS→VGW 연동에서 호출)
    """

    def __init__(self, start_date=None, end_date=None, server_ids=None, purpose='outbound'):
        self.start_date = start_date
        self.end_date = end_date
        self.server_ids = server_ids
        self.purpose = purpose
        self.fetcher = OpenSSHLogFetcher()
        self.errors = []

    def _get_date_range(self):
        """start_date ~ end_date 날짜 리스트. 미입력 시 빈 리스트(최신 파일 자동)."""
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
            logger.info(f"날짜 범위: {dates[0]} ~ {dates[-1]} ({len(dates)}일)")
            return dates
        except ValueError as e:
            logger.error(f"날짜 형식 오류: {e}")
            self.errors.append({'error': f'날짜 형식 오류: {str(e)}'})
            return []

    def _get_target_servers(self):
        """검색 대상 AICC 서버 목록 (purpose 경로 보유분만)"""
        targets = get_enabled_servers(
            server_type='AICC', purpose=self.purpose, server_ids=self.server_ids
        )
        if not targets:
            self.errors.append({'error': '검색 대상 서버 없음 (서버를 선택하세요)'})
        return targets

    def _extract_timestamp_from_line(self, line):
        """로그 라인에서 타임스탬프 추출"""
        try:
            patterns = [
                r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})',
                r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})',
            ]
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    return match.group(1)
            return None
        except Exception:
            return None

    def _extract_session_keys_from_lines(self, lines):
        """라인 목록에서 callSessionKey 추출 (중복 제거)"""
        session_keys = []
        patterns = [
            r'"callSessionKey"\s*:\s*"([^"]+)"',
            r'callSessionKey=([a-zA-Z0-9\-_:]+)'
        ]
        for line in lines:
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    key = match.group(1)
                    if key not in session_keys:
                        session_keys.append(key)
                    break
        logger.info(f"세션 키 추출: {len(session_keys)}개")
        return session_keys

    def _sort_by_timestamp(self, flow_lines):
        """타임스탬프 기준 시간순 정렬 (None 은 직전 값 기준)"""
        try:
            last_ts = '0000-00-00 00:00:00'
            keyed = []
            for idx, item in enumerate(flow_lines):
                ts = item.get('timestamp')
                if ts:
                    last_ts = ts
                sort_key = f"{last_ts}_{idx:08d}"
                keyed.append((sort_key, item))
            keyed.sort(key=lambda x: x[0])
            return [x[1] for x in keyed]
        except Exception as e:
            logger.exception(f"정렬 오류: {e}")
            return flow_lines

    def search_by_custid_flow(self, cust_id):
        """
        custId 기반 플로우 검색 (서버측 grep 2단계).
        1단계: custId 포함 라인 grep → 2단계: 세션 키로 재 grep.
        """
        try:
            logger.info(f"검색 시작: custId={cust_id}, purpose={self.purpose}, 서버={self.server_ids}")

            targets = self._get_target_servers()
            if not targets:
                return {
                    'success': False,
                    'message': '검색 대상 서버가 없습니다. 서버를 선택해주세요.',
                    'errors': self.errors,
                    'flow_results': []
                }

            dates = self._get_date_range()

            # ── 1단계: 서버측 custId grep
            custid_pattern = f'"custId":"{cust_id}"'
            all_custid_lines = []

            for idx, server in targets:
                server_id = get_server_label(server)
                log_paths = get_log_paths(server, self.purpose)

                lines, errors = self.fetcher.grep_remote(
                    server, log_paths, dates, custid_pattern
                )
                if errors:
                    self.errors.extend(errors)

                for line in lines:
                    all_custid_lines.append({
                        'line': line.strip(),
                        'server': server_id,
                        'timestamp': self._extract_timestamp_from_line(line)
                    })

            if not all_custid_lines:
                return {
                    'success': False,
                    'message': f'custId {cust_id}에 해당하는 로그를 찾을 수 없습니다',
                    'errors': self.errors,
                    'flow_results': []
                }

            logger.info(f"1단계 완료: custId 매칭 {len(all_custid_lines)}줄")

            # ── 세션 키 추출
            raw_lines = [item['line'] for item in all_custid_lines]
            session_keys = self._extract_session_keys_from_lines(raw_lines)

            if not session_keys:
                return {
                    'success': False,
                    'message': f'custId {cust_id}에서 callSessionKey를 찾을 수 없습니다',
                    'errors': self.errors,
                    'flow_results': []
                }

            # ── 2단계: 서버측 세션 키 grep
            all_flow_results = []

            for session_key in session_keys:
                flow_lines = []

                for idx, server in targets:
                    server_id = get_server_label(server)
                    log_paths = get_log_paths(server, self.purpose)

                    lines, errors = self.fetcher.grep_remote(
                        server, log_paths, dates, session_key
                    )
                    if errors:
                        self.errors.extend(errors)

                    for line_num, line in enumerate(lines, 1):
                        flow_lines.append({
                            'line': line.strip(),
                            'file_path': f"{server_id}:remote_grep",
                            'line_number': line_num,
                            'timestamp': self._extract_timestamp_from_line(line)
                        })

                if flow_lines:
                    sorted_flow = self._sort_by_timestamp(flow_lines)

                    first_timestamp = next(
                        (item['timestamp'] for item in sorted_flow if item.get('timestamp')),
                        None
                    )
                    seen = set()
                    hostnames = []
                    for item in sorted_flow:
                        fp = item.get('file_path', '')
                        host = fp.split(':')[0] if ':' in fp else fp
                        if host and host not in seen:
                            seen.add(host)
                            hostnames.append(host)

                    all_flow_results.append({
                        'session_key': session_key,
                        'first_timestamp': first_timestamp,
                        'hostnames': hostnames,
                        'line_count': len(sorted_flow),
                        'flow_lines': sorted_flow
                    })

            # 세션을 '서버 순서'가 아니라 '첫 타임스탬프' 기준 전역 시간순으로 정렬
            #   (기존: VGW1 세션들 → VGW2 세션들 로 서버별 묶임)
            all_flow_results.sort(
                key=lambda s: (s.get('first_timestamp') is None,
                               s.get('first_timestamp') or '')
            )

            logger.info(f"검색 완료: {len(all_flow_results)}개 세션")

            return {
                'success': True,
                'cust_id': cust_id,
                'session_count': len(all_flow_results),
                'flow_results': all_flow_results,
                'errors': self.errors if self.errors else None
            }

        except Exception as e:
            logger.exception(f"custId 검색 오류: {e}")
            return {
                'success': False,
                'message': f'검색 오류: {str(e)}',
                'errors': self.errors,
                'flow_results': []
            }

    def search_by_pattern(self, pattern):
        """
        패턴(정규식) 기반 로그 검색 — AICC 대상, 서버측 grep 으로 매칭 라인만 회수.

        - purpose 미지정(None) 이면 inbound + outbound 경로 전부를 대상으로 검색
        - grep -E(ERE) 사용 → '|' 등 확장 정규식 지원, 매칭 라인 원문 그대로 반환
        - 대상 AICC 서버가 없으면(예: ARS 만 선택) 에러가 아니라 빈 결과 반환
          (라우트에서 ARS 결과와 병합)
        """
        try:
            re.compile(pattern)
        except re.error as e:
            return {'success': False, 'message': f'잘못된 정규식: {str(e)}', 'results': []}

        # ARS 만 선택된 경우 등: AICC 타깃이 없어도 에러로 처리하지 않음
        targets = get_enabled_servers(
            server_type='AICC', purpose=self.purpose, server_ids=self.server_ids
        )

        dates = self._get_date_range()
        results = []

        for idx, server in targets:
            server_id = get_server_label(server)
            # purpose=None → inbound + outbound 전체 경로 (중복 경로 제거:
            #   dev 서버처럼 inbound/outbound 경로가 동일하면 grep 중복 매칭 방지)
            log_paths = list(dict.fromkeys(get_log_paths(server, self.purpose)))
            if not log_paths:
                continue

            lines, errors = self.fetcher.grep_remote(
                server, log_paths, dates, pattern, use_extended=True
            )
            if errors:
                self.errors.extend(errors)

            for line in lines:
                results.append({
                    'line': line.strip(),
                    'server': server_id,
                    'type': 'AICC',
                    'file_path': f"{server_id}:pattern_search",
                    'timestamp': self._extract_timestamp_from_line(line)
                })

        return {
            'success': True,
            'pattern': pattern,
            'result_count': len(results),
            'results': results,
            'errors': self.errors if self.errors else None
        }
