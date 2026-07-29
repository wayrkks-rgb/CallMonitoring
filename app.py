#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Log Analysis Web Server — OpenSSH Native Version
모듈 분리 버전 (v3)
"""

from flask import Flask, send_from_directory
import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime
from threading import Lock



# ── 로깅 설정 ──────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            LOG_DIR / 'app.log',
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── Flask 앱 초기화 ────────────────────────────────────────
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'log-analyzer-openssh-2024')

# ── 전역 상태 ──────────────────────────────────────────────
TOTAL_SEARCHES = 0
APP_START_TIME = datetime.now()
_search_lock = Lock()

HTTP_PORT = int(os.environ.get('HTTP_PORT', 5000))


def increment_search_count():
    """검색 카운터 증가 (스레드 안전)"""
    global TOTAL_SEARCHES
    with _search_lock:
        TOTAL_SEARCHES += 1


# ── Blueprint 등록 ─────────────────────────────────────────
from routes.search import search_bp
from routes.servers import servers_bp
from routes.system import system_bp
from routes.analysis import analysis_bp
from routes.monitor import monitor_bp
from routes.topology import topology_bp
from routes.precheck import precheck_bp
from routes.topology_screen  import topology_screen_bp


app.register_blueprint(search_bp)
app.register_blueprint(servers_bp)
app.register_blueprint(system_bp)
app.register_blueprint(analysis_bp)
app.register_blueprint(monitor_bp)
app.register_blueprint(topology_bp)
app.register_blueprint(precheck_bp)
app.register_blueprint(topology_screen_bp)

# ── ARS 인덱서 (백그라운드 준실시간 색인) ──────────────────
from flask import jsonify
from ars_index_store import get_default_store
from ars_indexer import ArsIndexer

# 시작 시 과거 색인 범위(최신순, 1회). 30일 전체(≈60GB) 백필 — 연속 버스트로 처리.
ARS_INDEX_BACKFILL_DAYS = 30
ars_index_store_inst = get_default_store()
ars_indexer_instance = ArsIndexer(ars_index_store_inst,
                                  poll_interval=5, max_interval=10,
                                  retention_days=30,
                                  backfill_days=ARS_INDEX_BACKFILL_DAYS)


@app.route('/ars-index-status')
def ars_index_status():
    st = ars_index_store_inst.stats()
    ix = ars_indexer_instance
    live = ix._t_live
    bf = ix._t_bf
    st['live_running'] = bool(live and live.is_alive())
    st['backfill_running'] = bool(bf and bf.is_alive())
    st['backfill_remaining'] = len(ix._backfill)
    st['backfill_done'] = ix._backfill_done
    st['last_error'] = ix.last_error
    return jsonify(st)


# ── VGW 채널 모니터링 수집기 (설정 enabled=true 일 때만 기동) ──
from vgw_monitor import VgwCollector, VgwCallTracker
from vgw_stats_store import VgwStatsStore
from config_manager import get_vgw_monitor_config, get_server_by_id

vgw_stats_store = VgwStatsStore()

# 콜 이벤트 → 통계 저장소 적재
vgw_call_tracker = VgwCallTracker(
    on_call=lambda e: vgw_stats_store.record_call(
        e['vgw'], e['direction'], e['channel'],
        e['start_ts'], e['end_ts'], e['duration_sec'], e['outcome'])
)

vgw_collector = VgwCollector(
    config_provider=get_vgw_monitor_config,
    server_resolver=get_server_by_id,
    on_snapshot=vgw_call_tracker.process,   # 스냅샷마다 상태전이 → 콜 이벤트 집계
)


# ── 메인 페이지 ────────────────────────────────────────────
@app.route('/')
def index():
    templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    return send_from_directory(templates_dir, 'index.html')


# ── 서버 실행 ──────────────────────────────────────────────
if __name__ == '__main__':
    from ssh_fetcher import check_openssh_installed

    logger.info("=" * 60)
    logger.info("Log Analysis Web Server — v3 (모듈 분리)")
    logger.info("=" * 60)

    if not check_openssh_installed():
        logger.error("OpenSSH 미설치!")
        logger.error("설치: Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0")

    logger.info(f"작업 디렉토리: {Path(__file__).parent}")
    logger.info(f"포트: HTTP {HTTP_PORT}")
    logger.info(f"접속 주소: http://localhost:{HTTP_PORT}")

    # ARS 인덱서 백그라운드 시작 (debug=False 라 리로더 중복 기동 없음)
    import atexit
    ars_indexer_instance.start()
    atexit.register(ars_indexer_instance.stop)
    logger.info("ARS 인덱서 백그라운드 기동")

    # VGW 채널 모니터 수집기 (설정 enabled=true 일 때만 실제 연결)
    try:
        vgw_stats_store.cleanup()
        vgw_collector.start()
        atexit.register(vgw_collector.stop)
    except Exception as e:
        logger.warning(f"VGW 모니터 기동 경고: {e}")
    import scenario_boot
    scenario_boot.install()
    scenario_boot.warm_async()

    try:
        # threaded=True: 백그라운드 모니터 폴링(/vgw-channels)과 로그/패턴 검색이
        #   서로를 막지 않도록 요청을 동시 처리 (단일 스레드면 폴링이 검색을 굶김)
        app.run(host='0.0.0.0', port=HTTP_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("\n서버 종료")
        ars_indexer_instance.stop()
    except Exception as e:
        logger.exception(f"서버 오류: {e}")
