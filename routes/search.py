#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""검색 라우트 — 인바운드(ARS→VGW 병합) / 아웃바운드 / 패턴 검색

인바운드 흐름:
  입력(고객ID 또는 핸드폰) → ArsLogFetcher.search_inbound()
    → 각 ARS 콜에서 cust_id 확정
    → 그 cust_id 로 LogSearcher(purpose='inbound').search_by_custid_flow()
    → ARS 구간 + VGW 세션 플로우를 하나의 콜로 병합하여 반환
"""

from flask import Blueprint, request, jsonify
import logging
import re
from datetime import datetime, timedelta

from log_searcher import LogSearcher
from ars_fetcher import ArsLogFetcher
from ars_ssh_fetcher import ArsSshLogFetcher
from config_manager import validate_date_format

logger = logging.getLogger(__name__)

search_bp = Blueprint('search', __name__)

# VGW 세션을 ARS 콜에 붙일 때의 뒤쪽 시간 마진 (앞 마진 없음)
VGW_TAIL_MARGIN = timedelta(minutes=2)


def _parse_call_window(call):
    """
    ARS 콜의 [시작, 종료+5분] 윈도우를 datetime 으로 반환.
    콜 날짜는 UCID 앞 8자리(YYYYMMDD)에서 추출, 실패 시 (None, None).
    """
    ucid = (call.get('ucid') or '')
    st = call.get('start_time')
    et = call.get('end_time')
    if not st:
        return None, None
    if len(ucid) < 8 or not ucid[:8].isdigit():
        return None, None
    try:
        base = datetime.strptime(ucid[:8], '%Y%m%d').date()
        start_dt = datetime.combine(base, datetime.strptime(st, '%H:%M:%S').time())
        end_src = et if (et and _looks_like_time(et)) else st
        end_dt = datetime.combine(base, datetime.strptime(end_src, '%H:%M:%S').time())
        if end_dt < start_dt:          # 자정 넘어간 콜
            end_dt += timedelta(days=1)
        return start_dt, end_dt + VGW_TAIL_MARGIN
    except (ValueError, TypeError):
        return None, None


def _looks_like_time(s):
    return bool(s) and len(s) == 8 and s[2] == ':' and s[5] == ':'


def _vgw_in_window(first_ts, start_dt, end_dt):
    """VGW 세션 first_timestamp 가 콜 윈도우 안인지. 판별 불가하면 True(유지)."""
    if start_dt is None or not first_ts:
        return True
    m = _vgw_ts(first_ts)
    if m is None:
        return True
    return start_dt <= m <= end_dt


def _vgw_ts(first_ts):
    """VGW 세션 first_timestamp 문자열 → datetime (파싱 실패 시 None)."""
    if not first_ts:
        return None
    s = first_ts.strip()
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s[:26 if '.' in s else 19], fmt)
        except (ValueError, TypeError):
            continue
    return None


def _pick_best_vgw(flows, start_dt):
    """
    윈도우 내 VGW 세션이 여러 개일 때 ARS 콜에 붙일 1개만 선택 (1:1 보장).

    선택 규칙:
      1) ARS 콜 시작시각 이후(>=)에 시작한 세션 중 가장 이른 것
      2) 이후 세션이 없으면 시작시각과 절대 시간차가 가장 작은 세션
      3) 시간 판별이 전혀 안 되면 첫 세션
    반환: [flow] (0~1개)
    """
    if not flows:
        return []
    if start_dt is None:
        return [flows[0]]

    after, others = [], []
    for fr in flows:
        ts = _vgw_ts(fr.get('first_timestamp'))
        if ts is None:
            others.append((None, fr))
        elif ts >= start_dt:
            after.append((ts, fr))
        else:
            others.append((ts, fr))

    if after:
        after.sort(key=lambda x: x[0])   # 시작 이후 가장 이른 세션
        return [after[0][1]]

    scored = [(abs((ts - start_dt).total_seconds()) if ts else float('inf'), fr)
              for ts, fr in others]
    scored.sort(key=lambda x: x[0])
    return [scored[0][1]]


def _parse_common(data):
    """공통 파라미터 파싱/검증. (params, error_response) 반환."""
    start_date = (data.get('start_date') or '').strip()
    end_date = (data.get('end_date') or '').strip()
    server_ids = data.get('server_ids')

    if start_date and not validate_date_format(start_date):
        return None, {'success': False, 'message': '시작일 형식 오류 (YYYY-MM-DD)'}
    if end_date and not validate_date_format(end_date):
        return None, {'success': False, 'message': '종료일 형식 오류 (YYYY-MM-DD)'}

    if server_ids is not None:
        if not isinstance(server_ids, list) or len(server_ids) == 0:
            return None, {'success': False, 'message': '검색할 서버를 선택하세요'}
        server_ids = [int(sid) for sid in server_ids]

    return {
        'start_date': start_date or None,
        'end_date': end_date or None,
        'server_ids': server_ids,
    }, None


# ── 인바운드 통합 검색 ─────────────────────────────────────
@search_bp.route('/search-inbound', methods=['POST'])
def search_inbound():
    """
    인바운드 통합 검색: 고객ID 또는 핸드폰번호 → ARS 콜 + VGW 플로우 병합.

    응답:
    {
      "success": true,
      "search_key": "...",
      "call_count": N,
      "calls": [
        {
          "server": "ARS01", "channel": "0013",
          "ucid": "...", "start_time": "14:28:46", "end_time": "14:29:39",
          "end_by": "WaitCall Success!",
          "cust_id": "9062859379", "phone": "01012345678",
          "ars_line_count": 1996,
          "ars_lines": [...],            # ARS 구간 원문
          "vgw": {                        # 해당 cust_id 의 VGW 플로우 (없으면 null)
             "session_count": 1,
             "flow_results": [ { "session_key":..., "flow_lines":[...] } ]
          }
        }
      ]
    }
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        params, err = _parse_common(data)
        if err:
            return jsonify(err)

        cust_id = (data.get('custId') or '').strip()
        phone = (data.get('phone') or '').strip()
        if not cust_id and not phone:
            return jsonify({'success': False, 'message': '고객ID 또는 핸드폰번호를 입력하세요'})

        from app import increment_search_count
        increment_search_count()

        # ── 1) ARS 인바운드 검색 (UNC + SSH 병합)
        ars = ArsLogFetcher(
            start_date=params['start_date'],
            end_date=params['end_date'],
            server_ids=params['server_ids'],
        )
        ars_result = ars.search_inbound(phone=phone or None, cust_id=cust_id or None)

        calls = list(ars_result.get('calls', []))
        errors = list(ars_result.get('errors') or [])

        # SSH 방식 ARS 서버(access_method='ssh')도 함께 검색해 병합
        ars_ssh = ArsSshLogFetcher(
            start_date=params['start_date'],
            end_date=params['end_date'],
            server_ids=params['server_ids'],
        )
        ars_ssh_result = ars_ssh.search_inbound(phone=phone or None, cust_id=cust_id or None)
        calls.extend(ars_ssh_result.get('calls', []))
        errors.extend(ars_ssh_result.get('errors') or [])

        # UNC + SSH 이중 검색으로 같은 콜이 2번 들어옴 -> UCID로 중복 제거
        _seen = {}
        for c in calls:
            key = c.get('ucid') or (c.get('channel'), c.get('start_time'), c.get('server'))
            prev = _seen.get(key)
            if prev is None or (c.get('line_count') or 0) > (prev.get('line_count') or 0):
                _seen[key] = c
        calls = sorted(_seen.values(), key=lambda c: (c.get('start_time') or ''))

        # ── 2) 각 ARS 콜의 cust_id 로 VGW 플로우 조회 후 병합
        #    (동일 cust_id 는 1회만 조회하여 캐시, 콜별로 시간윈도우 필터)
        vgw_full_cache = {}
        merged_calls = []
        for call in calls:
            cid = call.get('cust_id')
            vgw_block = None
            if cid:
                if cid not in vgw_full_cache:
                    vsearch = LogSearcher(
                        start_date=params['start_date'],
                        end_date=params['end_date'],
                        server_ids=params['server_ids'],
                        purpose='inbound',
                    )
                    vres = vsearch.search_by_custid_flow(cid)
                    if vres.get('errors'):
                        errors.extend(vres['errors'])
                    vgw_full_cache[cid] = vres

                full = vgw_full_cache[cid]
                start_dt, end_dt = _parse_call_window(call)
                all_flows = full.get('flow_results', []) if full.get('success') else []
                in_window = [
                    fr for fr in all_flows
                    if _vgw_in_window(fr.get('first_timestamp'), start_dt, end_dt)
                ]
                # ARS 1건당 VGW 로그는 최대 1개 (윈도우 내 다수면 콜 시작에 가장 가까운 세션)
                picked = _pick_best_vgw(in_window, start_dt)
                vgw_block = {
                    'session_count': len(picked),
                    'flow_results': picked,
                    'sessions_in_window': len(in_window),      # 참고: 윈도우 내 후보 수(드롭 감지)
                    'total_sessions_for_cust': len(all_flows),  # 참고: 이 고객 전체 세션 수
                }
                if not full.get('success') and full.get('message'):
                    vgw_block['message'] = full.get('message')

            merged_calls.append({
                'server': call.get('server'),
                'channel': call.get('channel'),
                'ucid': call.get('ucid'),
                'start_time': call.get('start_time'),
                'end_time': call.get('end_time'),
                'end_by': call.get('end_by'),
                'cust_id': cid,
                'phone': call.get('phone'),
                'ars_line_count': call.get('line_count'),
                'ars_source_files': call.get('source_files'),
                'ars_lines': call.get('lines'),
                'vgw': vgw_block,
            })

        return jsonify({
            'success': True,
            'direction': 'inbound',
            'search_key': ars_result.get('search_key', phone or cust_id),
            'call_count': len(merged_calls),
            'calls': merged_calls,
            'errors': errors or None,
        })

    except Exception as e:
        logger.exception(f"인바운드 검색 오류: {e}")
        return jsonify({'success': False, 'message': f'서버 오류: {str(e)}'})


# ── 아웃바운드 검색 (기존 로직) ────────────────────────────
@search_bp.route('/search', methods=['POST'])
def search():
    """아웃바운드 custId 플로우 검색 (기존 동작 유지)"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        params, err = _parse_common(data)
        if err:
            return jsonify(err)

        cust_id = (data.get('custId') or '').strip()
        if not cust_id:
            return jsonify({'success': False, 'message': 'custId를 입력하세요'})

        from app import increment_search_count
        increment_search_count()

        searcher = LogSearcher(
            start_date=params['start_date'],
            end_date=params['end_date'],
            server_ids=params['server_ids'],
            purpose='outbound',
        )
        result = searcher.search_by_custid_flow(cust_id)
        result.setdefault('direction', 'outbound')
        return jsonify(result)

    except Exception as e:
        logger.exception(f"검색 오류: {e}")
        return jsonify({'success': False, 'message': f'서버 오류: {str(e)}'})


# ── 패턴 검색 (ARS + AICC 통합, 매칭 라인 원문 반환) ────────
@search_bp.route('/pattern-search', methods=['POST'])
def pattern_search():
    """
    패턴(정규식) 기반 로그 검색 — 선택된 ARS/AICC 서버를 모두 대상으로,
    패턴을 포함하는 라인 원문을 반환한다.

    - AICC: 서버측 grep(-E) 으로 inbound+outbound 경로 전체 검색
    - ARS : UNC 파일을 읽어 라인 매칭
    - 두 결과를 병합. 한쪽에 대상 서버가 없어도 실패로 처리하지 않음.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        params, err = _parse_common(data)
        if err:
            return jsonify(err)

        pattern = (data.get('pattern') or '').strip()
        if not pattern:
            return jsonify({'success': False, 'message': '패턴을 입력하세요'})

        # 정규식 유효성은 한 번만 검사 (엔진별 중복 에러 방지)
        try:
            re.compile(pattern)
        except re.error as e:
            return jsonify({'success': False, 'message': f'잘못된 정규식: {str(e)}'})

        results = []
        errors = []

        # 1) AICC — 서버측 grep (purpose=None → inbound+outbound 전체)
        aicc = LogSearcher(
            start_date=params['start_date'],
            end_date=params['end_date'],
            server_ids=params['server_ids'],
            purpose=None,
        )
        aicc_res = aicc.search_by_pattern(pattern)
        results.extend(aicc_res.get('results') or [])
        errors.extend(aicc_res.get('errors') or [])

        # 2) ARS(UNC) — UNC 파일 매칭
        ars = ArsLogFetcher(
            start_date=params['start_date'],
            end_date=params['end_date'],
            server_ids=params['server_ids'],
        )
        ars_res = ars.search_by_pattern(pattern)
        results.extend(ars_res.get('results') or [])
        errors.extend(ars_res.get('errors') or [])

        # 3) ARS(SSH) — 서버측 Select-String
        ars_ssh = ArsSshLogFetcher(
            start_date=params['start_date'],
            end_date=params['end_date'],
            server_ids=params['server_ids'],
        )
        ars_ssh_res = ars_ssh.search_by_pattern(pattern)
        results.extend(ars_ssh_res.get('results') or [])
        errors.extend(ars_ssh_res.get('errors') or [])

        return jsonify({
            'success': True,
            'pattern': pattern,
            'result_count': len(results),
            'results': results,
            'errors': errors or None,
        })

    except Exception as e:
        logger.exception(f"패턴 검색 오류: {e}")
        return jsonify({'success': False, 'message': str(e)})
