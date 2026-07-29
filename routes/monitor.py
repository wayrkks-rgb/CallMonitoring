#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VGW 채널 모니터링 라우트

- GET  /vgw-channels          : 방향별 최신 스냅샷 + 스트림 상태
- GET  /vgw-monitor-config    : 모니터 설정 + 서버 선택 목록
- POST /vgw-monitor-config    : 설정 저장 후 수집기 재기동
- POST /vgw-monitor/restart   : 수집기 재기동
"""

from flask import Blueprint, request, jsonify
import logging

from config_manager import (
    load_config, get_server_label,
    get_vgw_monitor_config, save_vgw_monitor_config,
)
from vgw_monitor import STATUS_LABELS

logger = logging.getLogger(__name__)

monitor_bp = Blueprint('monitor', __name__)


def _collector():
    """app 에서 생성한 수집기 싱글턴 참조 (지연 임포트로 순환참조 회피)."""
    from app import vgw_collector
    return vgw_collector


def _stats_store():
    from app import vgw_stats_store
    return vgw_stats_store


@monitor_bp.route('/vgw-stats', methods=['GET'])
def vgw_stats():
    """방향/VGW별 콜 통계 (기본 구간=오늘 KST)."""
    try:
        start = (request.args.get('start') or '').strip() or None
        end = (request.args.get('end') or '').strip() or None
        result = _stats_store().aggregate(start_date=start, end_date=end)
        result['success'] = True
        return jsonify(result)
    except Exception as e:
        logger.exception(f"VGW 통계 조회 오류: {e}")
        return jsonify({'success': False, 'message': str(e)})


@monitor_bp.route('/vgw-channels', methods=['GET'])
def vgw_channels():
    """방향별 최신 채널 스냅샷 반환."""
    try:
        snap = _collector().snapshot_all()
        snap['status_labels'] = STATUS_LABELS
        snap['success'] = True
        return jsonify(snap)
    except Exception as e:
        logger.exception(f"VGW 채널 조회 오류: {e}")
        return jsonify({'success': False, 'message': str(e),
                        'running': False, 'endpoints': [], 'streams': []})


@monitor_bp.route('/vgw-monitor-config', methods=['GET'])
def vgw_monitor_config_get():
    """모니터 설정 + SSH 대상으로 지정할 수 있는 서버 목록."""
    try:
        vm = get_vgw_monitor_config()
        config = load_config() or {}
        servers = []
        for idx, s in enumerate(config.get('remote_servers', [])):
            servers.append({
                'id': idx,
                'label': get_server_label(s),
                'type': s.get('type', 'AICC'),
                'env': s.get('env', ''),
            })
        return jsonify({'success': True, 'config': vm, 'servers': servers,
                        'running': _collector().is_running()})
    except Exception as e:
        logger.exception(f"VGW 모니터 설정 조회 오류: {e}")
        return jsonify({'success': False, 'message': str(e)})


@monitor_bp.route('/vgw-monitor-config', methods=['POST'])
def vgw_monitor_config_post():
    """설정 저장 후 수집기 재기동."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        ok, saved = save_vgw_monitor_config(data)
        if not ok:
            return jsonify({'success': False, 'message': '설정 저장 실패'})
        try:
            _collector().restart()
        except Exception as e:
            logger.warning(f"수집기 재기동 경고: {e}")
        return jsonify({'success': True, 'config': saved,
                        'running': _collector().is_running()})
    except Exception as e:
        logger.exception(f"VGW 모니터 설정 저장 오류: {e}")
        return jsonify({'success': False, 'message': str(e)})


@monitor_bp.route('/vgw-monitor/restart', methods=['POST'])
def vgw_monitor_restart():
    try:
        _collector().restart()
        return jsonify({'success': True, 'running': _collector().is_running()})
    except Exception as e:
        logger.exception(f"VGW 모니터 재기동 오류: {e}")
        return jsonify({'success': False, 'message': str(e)})
