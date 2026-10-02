#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""시스템 관련 라우트 — 시스템 통계, 설정 조회"""

from flask import Blueprint, jsonify
import os
import logging
from datetime import datetime

# psutil 은 시스템 통계(CPU/메모리/디스크)에만 쓴다. 없다고 해서 이 모듈
# 전체가 import 실패하면 /version, /config 까지 통째로 사라져(404) 원인을
# 찾을 방법이 없어진다. 없으면 통계만 포기한다.
try:
    import psutil
except ImportError:                     # pragma: no cover - 환경 의존
    psutil = None

from config_manager import load_config

logger = logging.getLogger(__name__)

system_bp = Blueprint('system', __name__)


@system_bp.route('/system-stats', methods=['GET'])
def get_system_stats():
    """시스템 통계 (CPU, 메모리, 디스크)"""
    try:
        from app import APP_START_TIME, TOTAL_SEARCHES

        if psutil is None:
            uptime = datetime.now() - APP_START_TIME
            return jsonify({
                'success': True,
                'cpu_percent': None, 'memory_percent': None, 'disk_percent': None,
                'total_searches': TOTAL_SEARCHES,
                'uptime': str(uptime).split('.')[0],
                'note': 'psutil 미설치 — 시스템 통계만 표시되지 않습니다',
            })

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


@system_bp.route('/version', methods=['GET'])
def get_version():
    """지금 '실행 중인' 코드가 최신인지 점검.

    디스크의 파일이 아니라 이미 import 된 모듈의 소스를 본다. 폐쇄망에서
    일부 폴더만 덮어써져 예전 코드가 도는 경우를 확실히 잡아낸다.
    브라우저에서 /version 으로 바로 열어볼 수 있다.
    """
    try:
        import build_info
        info = build_info.collect()

        lines = [f"빌드 : {info['build']}", ""]
        for it in info['items']:
            mark = '최신 ' if it['ok'] else ('★옛날' if it['ok'] is False else '  ?  ')
            lines.append(f"[{mark}] {it['module']:<20} {it.get('mtime', '')}")
            if it['ok'] is False:
                lines.append(f"          빠짐: {it['why']}")
            if it.get('detail'):
                lines.append(f"          {it['detail']}")
        # 시나리오 DIFF 는 분석기 코드가 최신이어도 '캐시된 리포트'가 나올 수
        # 있다. 버전과 캐시 파일 상태를 같이 보여줘 그 경우를 구분한다.
        try:
            import scenario_deploy_diff as _D
            import scenario_deploy as _DP
            import glob as _g
            reps = _g.glob(os.path.join(_DP.REPORT_DIR, "*.json"))
            cur = [p for p in reps
                   if os.path.basename(p).startswith(f"v{_D.REPORT_VERSION}__")]
            lines.append("")
            lines.append(f"시나리오 DIFF : 분석기 v{_D.REPORT_VERSION} / "
                         f"스냅샷캐시 v{_D.CACHE_VERSION}")
            lines.append(f"                리포트 캐시 {len(reps)}건 "
                         f"(현재 버전 {len(cur)}건)")
            if reps and not cur:
                lines.append("                ※ 전부 예전 버전 — '강제 재분석'을 "
                             "한 번 누르면 새로 만듭니다")
        except Exception as e:
            lines.append(f"시나리오 DIFF : 확인 불가 ({e})")

        lines.append("")
        if info['up_to_date']:
            lines.append("▶ 실행 중인 코드는 모두 최신입니다.")
        else:
            lines.append("▶ 아래 파일이 예전 것입니다. 덮어쓰고 재기동하세요:")
            for m in info['stale_modules']:
                lines.append(f"    {m.replace('.', '/')}.py")
            lines.append("")
            lines.append("  ※ zip 을 풀 때 routes/ templates/ static/ 폴더까지")
            lines.append("     통째로 덮어쓰는지 확인하세요.")

        from flask import Response
        return Response("\n".join(lines), mimetype='text/plain; charset=utf-8')
    except Exception as e:
        logger.exception(f"버전 점검 오류: {e}")
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
