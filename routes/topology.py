#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ARS FLOW 뷰어 라우트 — 시나리오 토폴로지 지도 + 설명서"""

import os
import logging

from flask import Blueprint, request, jsonify, send_from_directory

import scenario_store as store

logger = logging.getLogger(__name__)

topology_bp = Blueprint('topology', __name__)

TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates'
)


@topology_bp.route('/topology')
def topology_page():
    """ARS FLOW 뷰어 화면"""
    return send_from_directory(TEMPLATES_DIR, 'topology.html')


@topology_bp.route('/api/topology/envs')
def api_envs():
    """환경(운영/개발·QA 등) 목록"""
    return jsonify({'envs': store.list_envs()})


@topology_bp.route('/api/topology/scenarios')
def api_scenarios():
    """환경별 시나리오 목록 + 루트(진입점)"""
    env = (request.args.get('env') or '').strip()
    if not env:
        return jsonify({'error': 'env 파라미터 필요'}), 400
    try:
        return jsonify(store.get_scenarios(env))
    except Exception as e:
        logger.exception('시나리오 목록 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/graph')
def api_graph():
    """지도용 그래프 (depth=1 이면 바로 아래 서브 시나리오까지 병합)"""
    env = (request.args.get('env') or '').strip()
    entry = (request.args.get('entry') or '').strip()
    try:
        depth = int(request.args.get('depth') or 0)
    except ValueError:
        depth = 0
    depth = max(0, min(depth, 2))
    if not env or not entry:
        return jsonify({'error': 'env, entry 파라미터 필요'}), 400
    try:
        return jsonify(store.get_graph(env, entry, depth=depth))
    except Exception as e:
        logger.exception('그래프 생성 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/menuroots')
def api_menuroots():
    """대메뉴(진입점) 목록"""
    env = (request.args.get('env') or '').strip()
    if not env:
        return jsonify({'error': 'env 파라미터 필요'}), 400
    try:
        return jsonify(store.get_menu_roots(env))
    except Exception as e:
        logger.exception('메뉴루트 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/menutree')
def api_menutree():
    """진입 파일 기준 메뉴 내비게이션 트리"""
    env = (request.args.get('env') or '').strip()
    entry = (request.args.get('entry') or '').strip()
    if not env or not entry:
        return jsonify({'error': 'env, entry 파라미터 필요'}), 400
    try:
        return jsonify(store.get_menu_tree(env, entry))
    except Exception as e:
        logger.exception('메뉴트리 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/locate')
def api_locate():
    """블록ID → 업무 위치 (로그 뷰어 연계). seq=00002813,00002815"""
    env = (request.args.get('env') or '').strip()
    seqs = [x.strip() for x in (request.args.get('seq') or '').split(',') if x.strip()]
    entry = (request.args.get('entry') or '').strip() or None
    page = (request.args.get('page') or '').strip() or None
    if not env or not seqs:
        return jsonify({'error': 'env, seq 파라미터 필요'}), 400
    try:
        return jsonify(store.locate_blocks(env, seqs, entry, page))
    except Exception as e:
        logger.exception('블록 위치 조회 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/snapshot', methods=['GET', 'POST'])
def api_snapshot():
    """스냅샷 목록(GET) / 저장(POST tag=배포전)"""
    try:
        if request.method == 'POST':
            env = (request.args.get('env') or '').strip()
            tag = (request.args.get('tag') or '').strip()
            if not env or not tag:
                return jsonify({'error': 'env, tag 필요'}), 400
            return jsonify(store.snapshot_save(env, tag))
        return jsonify({'snapshots': store.snapshot_list()})
    except Exception as e:
        logger.exception('스냅샷 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/diff')
def api_diff():
    """배포 전후 비교 (old=태그, new=태그 생략 시 현재)"""
    env = (request.args.get('env') or '').strip()
    old = (request.args.get('old') or '').strip()
    new = (request.args.get('new') or '').strip() or None
    if not old:
        return jsonify({'error': 'old 파라미터 필요'}), 400
    try:
        return jsonify(store.snapshot_diff(old, new, env))
    except Exception as e:
        logger.exception('비교 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/treedoc')
def api_treedoc():
    """현업 제공용 트리 구성도 문서"""
    env = (request.args.get('env') or '').strip()
    entry = (request.args.get('entry') or '').strip()
    if not env or not entry:
        return jsonify({'error': 'env, entry 파라미터 필요'}), 400
    try:
        dev = request.args.get('dev') in ('1', 'true', 'yes')
        return jsonify(store.get_tree_doc(env, entry, dev=dev))
    except Exception as e:
        logger.exception('트리 문서 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/menusummary')
def api_menusummary():
    """대메뉴 요약: 하위 메뉴 트리 + 각 액션의 업무 흐름"""
    env = (request.args.get('env') or '').strip()
    entry = (request.args.get('entry') or '').strip()
    if not env or not entry:
        return jsonify({'error': 'env, entry 파라미터 필요'}), 400
    try:
        return jsonify(store.get_menu_summary(env, entry))
    except Exception as e:
        logger.exception('대메뉴 요약 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/detail')
def api_detail():
    """상세: 블록 카드 + 변수 추적 + 연결정보"""
    env = (request.args.get('env') or '').strip()
    entry = (request.args.get('entry') or '').strip()
    if not env or not entry:
        return jsonify({'error': 'env, entry 파라미터 필요'}), 400
    try:
        return jsonify(store.get_detail(env, entry))
    except Exception as e:
        logger.exception('상세 생성 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/bizflow')
def api_bizflow():
    """업무 관통 flow (요약: summary / 상세: detail)"""
    env = (request.args.get('env') or '').strip()
    entry = (request.args.get('entry') or '').strip()
    mode = (request.args.get('mode') or 'summary').strip()
    if mode not in ('summary', 'detail'):
        mode = 'summary'
    if not env or not entry:
        return jsonify({'error': 'env, entry 파라미터 필요'}), 400
    try:
        return jsonify(store.get_bizflow(env, entry, mode=mode))
    except Exception as e:
        logger.exception('업무흐름 생성 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/coreflow')
def api_coreflow():
    """핵심 흐름 (예외 제거한 업무 줄기)"""
    env = (request.args.get('env') or '').strip()
    entry = (request.args.get('entry') or '').strip()
    if not env or not entry:
        return jsonify({'error': 'env, entry 파라미터 필요'}), 400
    try:
        return jsonify(store.get_coreflow(env, entry))
    except Exception as e:
        logger.exception('핵심흐름 생성 오류')
        return jsonify({'error': str(e)}), 500


@topology_bp.route('/api/topology/doc')
def api_doc():
    """설명서 데이터 (단계별 흐름/분기조건/사용 서비스/블록 설정값)"""
    env = (request.args.get('env') or '').strip()
    entry = (request.args.get('entry') or '').strip()
    if not env or not entry:
        return jsonify({'error': 'env, entry 파라미터 필요'}), 400
    try:
        return jsonify(store.get_doc(env, entry))
    except Exception as e:
        logger.exception('설명서 생성 오류')
        return jsonify({'error': str(e)}), 500
