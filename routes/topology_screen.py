# -*- coding: utf-8 -*-
import os, json
from flask import Blueprint, request, jsonify
import scenario_store
import block_index as BI

topology_screen_bp = Blueprint("topology_screen", __name__)
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCREEN_MAP = None

def _screen_map():
    global _SCREEN_MAP
    if _SCREEN_MAP is None:
        try:
            _SCREEN_MAP = json.load(open(os.path.join(_BASE, "screen_map.json"), encoding="utf-8"))
        except Exception:
            _SCREEN_MAP = {"screens": {}}
    return _SCREEN_MAP

def _name_of(code):
    return (_screen_map().get("screens", {}).get(code, {}) or {}).get("name") or code

def _folder(env):
    base = getattr(scenario_store, "CACHE_ROOT", None)
    return os.path.join(base, env) if base and env else None

@topology_screen_bp.route("/api/topology/screen")
def api_topology_screen():
    env = (request.args.get("env") or "").strip()
    seq = (request.args.get("seq") or "").strip()
    page = (request.args.get("page") or "").strip() or None
    if not env or not seq:
        return jsonify({"error": "env, seq 필요"}), 400
    folder = _folder(env)
    if not (folder and os.path.isdir(folder)):
        return jsonify({"found": False, "error": "시나리오 폴더 없음"}), 404
    sc = BI.screen_for_block(seq, page=page, folder=folder)
    if not sc:
        return jsonify({"found": False, "seq": seq})
    return jsonify({"found": True, "seq": seq, "code": sc["code"],
                    "payload": sc["payload"], "name": _name_of(sc["code"])})

@topology_screen_bp.route("/api/topology/screens")
def api_topology_screens():
    env = (request.args.get("env") or "").strip()
    page = (request.args.get("page") or "").strip() or None
    if not env:
        return jsonify({"error": "env 필요"}), 400
    folder = _folder(env)
    if not (folder and os.path.isdir(folder)):
        return jsonify({"error": "시나리오 폴더 없음"}), 404
    if not page:
        return jsonify({"error": "page 필요(성능)"}), 400
    pm = BI.page_screen_map(folder, page=page)
    key = os.path.splitext(os.path.basename(page))[0].lower()
    screens = pm.get(key, [])
    for s in screens:
        s["name"] = _name_of(s["screen_code"])
    return jsonify({"page": page, "screens": screens})