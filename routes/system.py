#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""시스템 관련 라우트 — 시스템 통계, 설정 조회"""

from flask import Blueprint, jsonify
import psutil
import logging
from datetime import datetime

from config_manager import load_config

logger = logging.getLogger(__name__)

system_bp = Blueprint('system', __name__)


@system_bp.route('/system-stats', methods=['GET'])
def get_system_stats():
    """시스템 통계 (CPU, 메모리, 디스크)"""
    try:
        from app import APP_START_TIME, TOTAL_SEARCHES

        cpu_percent = psutil.cpu_percent(interval=0)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        uptime = datetime.now() - APP_START_TIME
        uptime_str = str(uptime).split('.')[0]

        return jsonify({
            'success': True,
            'cpu_percent': round(cpu_percent, 1),
            'memory_percent': round(memory.percent, 1),
            'disk_percent': round(disk.percent, 1),
            'total_searches': TOTAL_SEARCHES,
            'uptime': uptime_str,
        })
    except Exception as e:
        logger.exception(f"시스템 통계 오류: {e}")
        return jsonify({'success': False, 'error': str(e)})


@system_bp.route('/config', methods=['GET'])
def get_config():
    """설정 조회"""
    try:
        config = load_config()
        if config:
            return jsonify({'success': True, 'config': config})
        return jsonify({'success': False, 'error': '설정 파일 없음'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
