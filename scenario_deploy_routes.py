# -*- coding: utf-8 -*-
"""
scenario_deploy_routes.py — 배포 변경내용 확인 API + 리포트 페이지

app.py 등록:
    from scenario_deploy_routes import deploy_bp
    app.register_blueprint(deploy_bp)

페이지:  GET  /deploy-diff
API   :  GET  /api/deploy/check      ?force=1   ← "변경내용 확인" 버튼
         GET  /api/deploy/peek       ← 가벼운 변경 감지(전송 없음)
         GET  /api/deploy/status
"""
import os
import logging

from flask import Blueprint, request, jsonify, send_from_directory

import scenario_deploy as DEP

logger = logging.getLogger(__name__)
deploy_bp = Blueprint("scenario_deploy", __name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(os.path.dirname(_HERE), "templates")
if not os.path.isdir(TEMPLATES_DIR):
    TEMPLATES_DIR = os.path.join(_HERE, "templates")


@deploy_bp.route("/deploy-diff")
def deploy_page():
    return send_from_directory(TEMPLATES_DIR, "deploy_diff.html")


@deploy_bp.route("/api/deploy/check")
def api_check():
    """과거 → 운영 변경내용 전체 리포트."""
    force = request.args.get("force") in ("1", "true", "yes")
    try:
        rep = DEP.check(force=force)
        if rep.get("error"):
            return jsonify(rep), 400
        return jsonify(rep)
    except Exception as e:
        logger.exception("배포 변경내용 확인 오류")
        return jsonify({"error": str(e)}), 500


@deploy_bp.route("/api/deploy/peek")
def api_peek():
    """매니페스트만 조회해 변경 여부만 알려준다 (파일 전송 없음, 약 1~2초)."""
    try:
        r = DEP.peek()
        return (jsonify(r), 400) if r.get("error") else jsonify(r)
    except Exception as e:
        logger.exception("배포 변경 감지 오류")
        return jsonify({"error": str(e)}), 500


@deploy_bp.route("/api/deploy/fresh")
def api_fresh():
    """뷰어 최신화 — 원격이 바뀌었으면 로컬 갱신 후 결과 반환."""
    import scenario_freshness as FR
    env = (request.args.get("env") or "").strip() or None
    force = request.args.get("force") in ("1", "true", "yes")
    try:
        thr = int(request.args.get("throttle") or FR.DEFAULT_THROTTLE)
    except ValueError:
        thr = FR.DEFAULT_THROTTLE
    try:
        return jsonify(FR.ensure_fresh(env, throttle=thr, force=force))
    except Exception as e:
        logger.exception("최신화 오류")
        return jsonify({"error": str(e)}), 500


@deploy_bp.route("/api/deploy/status")
def api_status():
    try:
        return jsonify(DEP.status())
    except Exception as e:
        logger.exception("배포 상태 오류")
        return jsonify({"error": str(e)}), 500
