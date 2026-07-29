# -*- coding: utf-8 -*-
"""
routes/precheck.py — 로그 조회 "사전 노티" [성능 최적화판]
★ scenario_store.locate_blocks(1~2분) 호출 제거. 로그의 시나리오명(page)으로
  block_index 단일 파일 파싱만 사용 → 수 ms(+캐시).
API: POST /api/precheck  { env, ars_lines:[원문...], call:{...,end_by} }
"""
import os
import re
import json

from flask import Blueprint, request, jsonify

import scenario_store
import block_index as BI

precheck_bp = Blueprint("precheck", __name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCREEN_MAP = None


def _screen_map():
    global _SCREEN_MAP
    if _SCREEN_MAP is None:
        try:
            _SCREEN_MAP = json.load(open(os.path.join(_BASE, "screen_map.json"),
                                         encoding="utf-8"))
        except Exception:
            _SCREEN_MAP = {"screens": {}}
    return _SCREEN_MAP


def _screen_name(code):
    return (_screen_map().get("screens", {}).get(code, {}) or {}).get("name") or code


_PREFIX = re.compile(r'^\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+(\w+)\s+\S+@\d+\s+')
_TIME = re.compile(r'(\d{2}:\d{2}:\d{2})')
_BLOCK = re.compile(
    r'\[(?P<scn>[가-힣A-Za-z0-9_]+\.d?xml)\]'
    r'\[(?P<name>[^\]]*)\]\[(?P<block>\d{8})\]\[(?P<node>[^\]]*)\]\s+'
    r'End Event\[(?P<result>[^\]]*)\]')
_DTMF = re.compile(r'\[GETDIGIT\]\s+Inputdigit\s*=\s*(?P<d>[\dA-D\*#]+)')
_SCREEN_FULL = re.compile(r'(?:szSendMenuData|SendData)\((?P<payload>S\$(?P<scr>HLI[A-Z0-9]+);.*?)\)')
_SCREEN = re.compile(r'\[WV_SENDMENU\].*?S\$(?P<scr>HLI[A-Z0-9]+)')
_SCN_ANY = re.compile(r'\[(?P<scn>[가-힣A-Za-z0-9_]+\.d?xml)\]')
_TERM = re.compile(r'TERM REASON ==>\s*(?P<r>TM_\w+)')

SENTINEL = "99999999"
_RENDER_SCN = {"W_WebVoicePlay.dxml", "W_WebVoice_Main.dxml"}
_SUB_SCN = ("_std_", "CALL_APPDB", "MCI_", "InputChkTime", "CTIInterface",
            "RESTfulAPI", "sendnreceive", "QuerySet", "HostComm")
TERM_DESC = {"TM_USRSTOP": "고객 중단(끊음)", "TM_EOD": "정상(멘트 종료)",
             "TM_DIGIT": "정상(입력 수신)", "TM_TIMEOUT": "타임아웃"}


def _is_sub(scn):
    return any(p in scn for p in _SUB_SCN)


def _stem(name):
    return os.path.splitext(os.path.basename(name or ""))[0].lower()


def _parse_ars_lines(ars_lines):
    flow, screens, dtmf = [], [], []
    term_reason, has_error, is_webvoice = None, False, False
    cur_scn = [None]
    for raw in (ars_lines or []):
        s = (raw or "").rstrip("\r\n")
        tm = _TIME.search(s)
        ts = tm.group(1) if tm else ""
        msn = _SCN_ANY.search(s)
        if msn:
            sc = msn.group("scn")
            if sc not in _RENDER_SCN and not _is_sub(sc):
                cur_scn[0] = sc
        pm = _PREFIX.search(s)
        if pm and pm.group(1) == "ERROR" and "DNISGROUP.ini" not in s:
            has_error = True
        mb = _BLOCK.search(s)
        if mb:
            rec = {"ts": ts, "scn": mb.group("scn"), "block": mb.group("block"),
                   "name": mb.group("name").strip(), "node": mb.group("node"),
                   "result": mb.group("result") or ""}
            flow.append(rec)
            if rec["result"] == "hangup":
                term_reason = term_reason or "TM_USRSTOP"
        mfull = _SCREEN_FULL.search(s)
        msc = mfull or _SCREEN.search(s)
        if msc:
            is_webvoice = True
            code = msc.group("scr")
            payload = mfull.group("payload") if mfull else ("S$" + code)
            _dup = re.search(r'^(S\$' + re.escape(code) + r';.*?);?:S\$' + re.escape(code) + r';',payload)
            if _dup:
                payload = _dup.group(1)
            if not screens or screens[-1]["code"] != code:
                screens.append({"ts": ts, "code": code, "payload": payload,
                                "scn": cur_scn[0]})
            elif len(payload) > len(screens[-1]["payload"]):
                screens[-1]["payload"] = payload
        md = _DTMF.search(s)
        if md and md.group("d").strip():
            dtmf.append({"ts": ts, "digit": md.group("d").strip()})
        mt = _TERM.search(s)
        if mt:
            term_reason = mt.group("r")
        if "WebVoice" in s or "WV_" in s or "webVoiceYN" in s:
            is_webvoice = True
    return {"flow": flow, "screens": screens, "dtmf": dtmf,
            "term_reason": term_reason, "has_error": has_error,
            "is_webvoice": is_webvoice}


def _steps(flow):
    out = []
    for f in flow:
        seq, scn = f["block"], f["scn"]
        if not seq or seq == SENTINEL or scn in _RENDER_SCN:
            continue
        item = (scn, seq)
        if not out or out[-1] != item:
            out.append(item)
    return out

# 시스템/공통/teardown 시나리오 (요약에서 제외)
_SKIP_SCN = re.compile(r'^(_hangup|_std_|ivrmain|ivrservice|CTIInterface|MCI_|'
                       r'CALL_APPDB|W_WebVoicePlay|W_WebVoice_Main|W_WebVoice_Start|'
                       r'InputDTMF|근무시간체크|휴일)')
# 업무 시나리오 → 서비스명 (필요시 추가)
_BIZ_NAME = {"W_고객조회":"고객조회","W_SelectType":"채널선택",
             "고객인증_SMS인증":"SMS인증","고객조회_주민번호":"주민번호조회",
             "고객조회_핸드폰":"핸드폰조회"}

def _biz_name(scn):
    stem = re.sub(r'\.(dxml|xml)$', '', scn or '')
    return _BIZ_NAME.get(stem, stem.replace('W_', ''))

def _service_flow(flow, screens):
    """의미있는 서비스 흐름 요약: 시나리오 전환 + 화면(메뉴) 이름."""
    seq = []
    for f in flow:
        scn = f.get("scn", "")
        if _SKIP_SCN.search(scn):
            continue
        nm = _biz_name(scn)
        if nm and (not seq or seq[-1] != nm):
            seq.append(nm)
    # 화면코드로 보강 (메뉴 선택 흐름)
    for s in screens:
        nm = _screen_name(s["code"])
        if nm and (not seq or seq[-1] != nm):
            seq.append(nm)
    return seq

def _locate(seq, page, folder):
    if not (folder and page):
        return None, None
    try:
        res = BI.locate(seq, page=page, folder=folder)
        if res.get("found"):
            return res["matches"][0], "block_index"
    except Exception:
        pass
    return None, None


def _fmt_loc(loc):
    if not loc:
        return None
    parts = []
    for k in ("menu", "root", "step_title", "phase", "substep", "title"):
        v = loc.get(k)
        if v and str(v) not in parts:
            parts.append(str(v))
    if not parts and loc.get("page"):
        parts.append(_stem(loc["page"]))
    return " › ".join(parts[:4]) if parts else None


def _build_precheck(ars_lines, env, folder=None, call_meta=None):
    call_meta = call_meta or {}
    parsed = _parse_ars_lines(ars_lines)
    flow = parsed["flow"]
    steps = _steps(flow)
    if folder is None:
        base = getattr(scenario_store, "CACHE_ROOT", None)
        folder = os.path.join(base, env) if base else None

    first_loc = last_loc = None
    last_info = None
    if steps:
        f_scn, f_seq = steps[0]
        first_loc, _ = _locate(f_seq, f_scn, folder)
        l_scn, l_seq = steps[-1]
        last_loc, src = _locate(l_seq, l_scn, folder)
        last_info = {"scenario": l_scn, "block": l_seq,
                     "block_name": next((f["name"] for f in reversed(flow)
                                         if f["block"] == l_seq), ""), "source": src}

    term = parsed["term_reason"]
    end_by = call_meta.get("end_by") or ""
    end_type = "정상 종료"
    if term == "TM_USRSTOP":
        end_type = "고객 중단"
    elif parsed["has_error"]:
        end_type = "오류 발생"

    screen_flow = [{"code": s["code"], "name": _screen_name(s["code"]), "ts": s["ts"],
                    "payload": s.get("payload", "S$" + s["code"]), "scn": s.get("scn")}
                   for s in parsed["screens"]]
    last_screen = screen_flow[-1] if screen_flow else None

    where = _fmt_loc(last_loc) or (last_info and
             f"{_stem(last_info['scenario'])} / 블록 {last_info['block']}"
             f"({last_info['block_name']})") or "위치 미상"
    reason = TERM_DESC.get(term or "", end_by or "")
    scr_txt = f" · 화면 '{last_screen['name']}'" if last_screen else ""
    svc_flow = _service_flow(flow, parsed["screens"])
    flow_summary = " > ".join(svc_flow[:6]) if svc_flow else where
    interpretation = f"[{flow_summary}] 흐름 ㆍ {reason or end_type}(으)로 종료"

    return {
        "ok": True, "is_webvoice": parsed["is_webvoice"], "end_type": end_type,
        "term_reason": term, "term_desc": TERM_DESC.get(term or "", ""),
        "flow_range": {"from": _fmt_loc(first_loc), "to": _fmt_loc(last_loc),
                       "steps": len(steps)},
        "end_point": {
            "scenario": last_info["scenario"] if last_info else None,
            "block": last_info["block"] if last_info else None,
            "block_name": last_info["block_name"] if last_info else None,
            "business": _fmt_loc(last_loc),
            "reason": reason or end_type,
            "source": last_info["source"] if last_info else None},
        "screen_flow": screen_flow, "last_screen": last_screen,
        "dtmf": parsed["dtmf"], "interpretation": interpretation,
        "topology_link": (f"/topology?env={env}&highlight={last_info['block']}"
                          if last_info else "/topology"),
        "service_flow": svc_flow,
        "flow_summary": flow_summary,
    }


@precheck_bp.route("/api/precheck", methods=["POST"])
def api_precheck():
    data = request.get_json(force=True, silent=True) or {}
    env = (data.get("env") or "").strip()
    ars_lines = data.get("ars_lines") or []
    call_meta = data.get("call") or {}
    if not env:
        try:
            envs = scenario_store.list_envs()
            env = envs[0] if envs else ""
        except Exception:
            env = ""
    if not ars_lines:
        return jsonify({"ok": False, "error": "ars_lines 필요"}), 400
    folder = None
    base = getattr(scenario_store, "CACHE_ROOT", None)
    if base and env:
        folder = os.path.join(base, env)
    try:
        return jsonify(_build_precheck(ars_lines, env, folder=folder,
                                       call_meta=call_meta))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500