#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""콜 분석 라우트 — UCID 없음 에러 콜 일괄 분석"""

from flask import Blueprint, request, jsonify
import logging

from call_analyzer import CallAnalyzer
from config_manager import validate_date_format

logger = logging.getLogger(__name__)

analysis_bp = Blueprint('analysis', __name__)


@analysis_bp.route('/analyze-calls', methods=['POST'])
def analyze_calls():
    """에러 콜 일괄 분석"""
    try:
        data = request.get_json()
        error_pattern = (data.get('error_pattern') or '').strip()
        start_date = (data.get('start_date') or '').strip()
        end_date = (data.get('end_date') or '').strip()
        server_ids = data.get('server_ids')

        if not error_pattern:
            error_pattern = '"description" : "UCID 없음"'

        # 날짜 검증
        if start_date and not validate_date_format(start_date):
            return jsonify({'success': False, 'message': '시작일 형식 오류 (YYYY-MM-DD)'})
        if end_date and not validate_date_format(end_date):
            return jsonify({'success': False, 'message': '종료일 형식 오류 (YYYY-MM-DD)'})

        # 서버 검증
        if server_ids is not None:
            if not isinstance(server_ids, list) or len(server_ids) == 0:
                return jsonify({'success': False, 'message': '검색할 서버를 선택하세요'})
            server_ids = [int(sid) for sid in server_ids]

        analyzer = CallAnalyzer(
            start_date=start_date if start_date else None,
            end_date=end_date if end_date else None,
            server_ids=server_ids
        )
        result = analyzer.analyze_error_calls(error_pattern)
        return jsonify(result)

    except Exception as e:
        logger.exception(f"콜 분석 오류: {e}")
        return jsonify({'success': False, 'message': f'서버 오류: {str(e)}'})
