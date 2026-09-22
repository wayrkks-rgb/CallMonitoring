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
# 자기 폴더의 templates 를 먼저 본다. 예전에는 상위 폴더를 먼저 봐서,
# 상위에 templates 폴더가 있으면 덮어쓴 파일이 아니라 그쪽이 서빙됐다.
TEMPLATES_DIR = os.path.join(_HERE, "templates")
if not os.path.isdir(TEMPLATES_DIR):
    TEMPLATES_DIR = os.path.join(os.path.dirname(_HERE), "templates")


@deploy_bp.route("/deploy-diff")
def deploy_page():
    # max_age=0: send_from_directory 는 기본적으로 캐시 수명을 붙인다.
    # 그대로 두면 브라우저가 예전 화면을 계속 써서 '화면 수정이 반영 안 됨'이 된다.
    resp = send_from_directory(TEMPLATES_DIR, "deploy_diff.html", max_age=0)
    # 화면 파일을 바꿔도 브라우저가 예전 것을 계속 쓰면 '적용 안 됨'이 된다.
    # 매번 새로 받게 한다(Ctrl+F5 없이도 반영되도록).
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


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


@deploy_bp.route("/api/deploy/selftest")
def api_selftest():
    """DIFF 가 '어디까지' 동작하는지 한 화면으로 확인.

    '화면에 안 보인다'는 원인이 셋이다 — 분석기가 안 만들었거나, 캐시된
    예전 리포트가 나오거나, 브라우저가 예전 화면을 쓰고 있거나.
    이 응답은 앞의 둘을 확실히 가려준다(브라우저는 화면 우측 상단 버전으로).
    """
    import glob
    import json as _json
    from flask import Response
    import scenario_deploy_diff as D

    L = []
    try:
        L.append(f"분석기 REPORT_VERSION : v{D.REPORT_VERSION}")
        L.append(f"스냅샷 CACHE_VERSION  : v{D.CACHE_VERSION}")
        L.append(f"화면 파일             : {os.path.join(TEMPLATES_DIR, 'deploy_diff.html')}")
        try:
            html = open(os.path.join(TEMPLATES_DIR, "deploy_diff.html"),
                        encoding="utf-8", errors="replace").read()
            L.append(f"  블록 전문 기능 포함 : {'예' if '블록 전문 보기' in html else '★ 아니오(옛날 파일)'}")
            L.append(f"  연관 시나리오 포함  : {'예' if 'relHtml' in html else '★ 아니오(옛날 파일)'}")
            import re as _re
            m = _re.search(r"PAGE_VERSION\s*=\s*'([^']+)'", html)
            L.append(f"  화면 버전           : {m.group(1) if m else '(표시 없음 — 옛날 파일)'}")
        except OSError as e:
            L.append(f"  ★ 화면 파일을 읽지 못함: {e}")

        reps = sorted(glob.glob(os.path.join(DEP.REPORT_DIR, "*.json")),
                      key=os.path.getmtime, reverse=True)
        L.append("")
        L.append(f"리포트 캐시 : {len(reps)}건  ({DEP.REPORT_DIR})")
        for p in reps[:5]:
            L.append(f"  {os.path.basename(p)}")
        if not reps:
            L.append("  (없음 — 아직 분석한 적이 없습니다)")

        cur = [p for p in reps if os.path.basename(p).startswith(f"v{D.REPORT_VERSION}__")]
        L.append("")
        if not cur:
            L.append("▶ 현재 버전 리포트가 없습니다.")
            L.append("  DIFF 화면에서 [강제 재분석] 을 한 번 누르세요.")
        else:
            rep = _json.load(open(cur[0], encoding="utf-8"))
            files = rep.get("files") or []
            L.append(f"▶ 최신 리포트 : {os.path.basename(cur[0])}")
            L.append(f"  report_version = {rep.get('report_version')}")
            L.append(f"  변경 파일 {len(files)}건")
            has = {"vars": 0, "full": 0, "context": 0, "related": 0}
            for f in files:
                if f.get("related"):
                    has["related"] += 1
                for b in (f.get("changed_blocks") or []) + (f.get("added_blocks") or []):
                    for k in ("vars", "full", "context"):
                        if b.get(k):
                            has[k] += 1
            L.append(f"  변수 변경이 담긴 블록 : {has['vars']}")
            L.append(f"  블록 전문이 담긴 블록 : {has['full']}")
            L.append(f"  흐름 위치가 담긴 블록 : {has['context']}")
            L.append(f"  연관 시나리오 있는 파일: {has['related']}")
            L.append("")
            if has["full"] or has["context"]:
                L.append("  → 분석기는 정상입니다. 화면에 안 보이면 브라우저가")
                L.append("     예전 화면을 쓰고 있는 것입니다 (Ctrl+F5).")
            elif files:
                L.append("  ★ 리포트에 상세 정보가 없습니다 — 분석기 쪽 문제입니다.")
            else:
                L.append("  변경된 파일이 없어 표시할 상세도 없습니다(정상).")
    except Exception as e:
        logger.exception("DIFF 자가진단 오류")
        L.append(f"★ 오류: {e}")
    return Response("\n".join(L), mimetype="text/plain; charset=utf-8")


@deploy_bp.route("/api/deploy/xmlprobe")
def api_xmlprobe():
    """수집된 시나리오 파일의 실제 XML 구조를 들여다본다.

    '파일은 읽히는데 블록이 0개'일 때, 문서 구조가 예상과 다른 것이 원인이다.
    추측하지 말고 실제 태그를 보고 맞춘다.
    """
    import glob
    import xml.etree.ElementTree as ET
    from flask import Response
    import scenario_deploy_diff as D

    L = []
    try:
        for slot in ("new", "old"):
            d = DEP._local_dir(slot)
            files = []
            for ext in D.SCN_EXT:
                files += glob.glob(os.path.join(d, "*" + ext))
            L.append(f"[{DEP.SLOT_NAME[slot]}] {d}")
            L.append(f"  파일 {len(files)}개"
                     + (f" (확장자별: " + ", ".join(
                         f"{e} {len([f for f in files if f.lower().endswith(e)])}개"
                         for e in D.SCN_EXT) + ")" if files else ""))
            # 하위 폴더도 있는지 알려준다(수집 경로가 맞는지 확인용)
            subs = [x for x in glob.glob(os.path.join(d, "*")) if os.path.isdir(x)]
            if subs:
                L.append(f"  하위 폴더: {', '.join(os.path.basename(x) for x in subs)}")
            if not files:
                L.append("  ★ 시나리오 파일이 없습니다 (수집 경로/확장자 확인)")
                L.append("")
                continue

            for path in sorted(files)[:2]:      # 앞 2개만 자세히
                L.append(f"\n  ── {os.path.basename(path)} "
                         f"({os.path.getsize(path):,} 바이트)")
                try:
                    raw = open(path, "rb").read(400)
                    L.append(f"     앞부분: {raw[:200].decode('utf-8', 'replace')!r}")
                except OSError as e:
                    L.append(f"     ★ 읽기 실패: {e}")
                    continue
                try:
                    root = ET.parse(path).getroot()
                except Exception as e:
                    L.append(f"     ★ XML 파싱 실패: {type(e).__name__}: {e}")
                    continue
                tag = root.tag
                nsuri = tag.split("}")[0][1:] if tag.startswith("{") else ""
                L.append(f"     루트 태그 : {tag}")
                if nsuri:
                    L.append(f"     네임스페이스: {nsuri}  ← 태그 이름에 붙습니다")
                kids = {}
                for c in root:
                    k = c.tag.split("}")[-1]
                    kids[k] = kids.get(k, 0) + 1
                L.append(f"     자식 태그 : {kids}")

                D._strip_ns(root)
                nodes = D._find_nodes(root)
                L.append(f"     찾은 Node : {len(nodes)}개")
                if nodes:
                    n0 = nodes[0]
                    L.append(f"     첫 Node 속성 : {dict(n0.attrib)}")
                    L.append(f"     첫 Node 자식 : {[c.tag for c in n0]}")
                    cp = n0.find("CustomProperties")
                    if cp is None:
                        L.append("     ★ CustomProperties 가 없습니다")
                    else:
                        L.append(f"     CustomProperties 안: {[c.tag for c in cp][:25]}")
                        L.append(f"     읽어낸 식별자(Sequence): {D._seq_of(n0, cp)!r}")
                snap = D.snapshot_file(path)
                L.append(f"     → 최종 블록 수: {len(snap['blocks']) if snap else '파싱실패'}")
            L.append("")
    except Exception as e:
        logger.exception("XML 구조 확인 오류")
        L.append(f"★ 오류: {e}")
    return Response("\n".join(L), mimetype="text/plain; charset=utf-8")


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


@deploy_bp.route("/api/deploy/config", methods=["GET"])
def api_config_get():
    """시나리오 경로/접속 설정 조회 (웹에서 편집 가능하도록)."""
    try:
        cfg = DEP.load_cfg()
        return jsonify({"ok": True, "config": cfg,
                        "remote": {"new": DEP._remote_dir(cfg, "new"),
                                   "old": DEP._remote_dir(cfg, "old")}})
    except Exception as e:
        logger.exception("배포 설정 조회 오류")
        return jsonify({"ok": False, "error": str(e)}), 500


@deploy_bp.route("/api/deploy/config", methods=["POST"])
def api_config_post():
    """시나리오 경로/접속 설정 저장."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        ok, cfg, err = DEP.save_cfg(data)
        if not ok:
            return jsonify({"ok": False, "error": err, "config": cfg}), 400
        return jsonify({"ok": True, "config": cfg,
                        "remote": {"new": DEP._remote_dir(cfg, "new"),
                                   "old": DEP._remote_dir(cfg, "old")}})
    except Exception as e:
        logger.exception("배포 설정 저장 오류")
        return jsonify({"ok": False, "error": str(e)}), 500


@deploy_bp.route("/api/deploy/status")
def api_status():
    try:
        return jsonify(DEP.status())
    except Exception as e:
        logger.exception("배포 상태 오류")
        return jsonify({"error": str(e)}), 500
