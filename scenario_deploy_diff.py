# -*- coding: utf-8 -*-
"""
scenario_deploy_diff.py — 배포 전/후 시나리오 폴더 비교 엔진

기존 scenario_version.py 는 블록 해시만 저장해 '변경됨' 까지만 알 수 있다.
이 모듈은 스냅샷에 원문을 담아 '무엇이 어떻게 바뀌었는지' 를 산출한다.

산출 3종:
  1) 함수처리 내용   : Script / PreScript 라인 단위 diff
  2) 업무 flow 변화  : 블록 추가·삭제, 이동대상(TargetPage)·분기조건·반환코드·연결(Link) 변화
  3) 보이는ARS 화면  : WV_Param 파싱 → 화면템플릿(S$)·타이틀·안내문·버튼 변화

전부 표준 라이브러리(xml.etree, difflib, re)만 사용 → 폐쇄망 무의존.
"""
import os
import re
import glob
import json
import difflib
import hashlib
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SCN_EXT = (".dxml", ".xml")
MAX_DIFF_LINES = 400          # 블록당 스크립트 diff 라인 상한
MAX_BLOCK_CHANGES = 4000      # 리포트 전체 블록 변경 상한(안전판)


# ══════════════════════════════════════════════════════════════
# 1. WV_Param (보이는ARS 화면) 파서
# ══════════════════════════════════════════════════════════════
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_WV_LINE = re.compile(r"app\.(WV_\w+)\s*(\+=|=)\s*(.*)$")

# WV_Param 외에 화면 컨텍스트로 함께 수집할 변수
WV_META_KEYS = ("WV_ment", "WV_mentFormat", "WV_InputTimeout",
                "WV_RetryCount", "WV_TimeoutMent", "WV_2DepthCode")


def _js_value(rhs):
    """
    JS 우변을 문자열로 평가. 문자열 리터럴은 그대로,
    변수/식은 {expr} 플레이스홀더로 남긴다. 문장 끝(;)·주석(//)에서 종료.
      '"MUTE$0$ON$" + app.WV_Mute_State + ";";'  →  'MUTE$0$ON${app.WV_Mute_State};'
    """
    out, expr = [], []
    i, n = 0, len(rhs)

    def flush_expr():
        e = "".join(expr).strip()
        expr.clear()
        if e and e not in ("+",):
            out.append("{" + e.strip("+ \t") + "}")

    while i < n:
        c = rhs[i]
        if c in ('"', "'"):
            q = c
            i += 1
            buf = []
            while i < n:
                if rhs[i] == "\\" and i + 1 < n:
                    buf.append(rhs[i + 1])
                    i += 2
                    continue
                if rhs[i] == q:
                    i += 1
                    break
                buf.append(rhs[i])
                i += 1
            flush_expr()
            out.append("".join(buf))
            continue
        if c == "/" and i + 1 < n and rhs[i + 1] == "/":
            break                      # 주석 시작
        if c == ";":
            break                      # 문장 종료
        if c == "+":
            flush_expr()
            i += 1
            continue
        expr.append(c)
        i += 1
    flush_expr()
    return "".join(out)


def _split_cmds(s):
    """WV_Param 누적 문자열 → 명령 리스트. 각 명령은 $ 로 필드 분리."""
    return [c.strip() for c in s.split(";") if c.strip()]


def parse_wv(script):
    """
    블록 스크립트에서 보이는ARS 화면 정의를 추출.
    Returns: {"screens":[screen,...], "meta":{WV_ment:..., ...}}
      screen = {code, title, texts[], buttons[], flags{}, raw}
    """
    if not script or "WV_" not in script:
        return {"screens": [], "meta": {}}
    script = _BLOCK_COMMENT.sub("", script)

    screens, meta = [], {}
    cur = None            # 현재 누적중인 WV_Param 문자열
    for line in script.splitlines():
        ls = line.strip()
        if not ls or ls.startswith("//"):
            continue
        m = _WV_LINE.search(ls)
        if not m:
            continue
        var, op, rhs = m.group(1), m.group(2), m.group(3)
        val = _js_value(rhs)
        if var == "WV_Param":
            if op == "=":
                if cur:
                    screens.append(cur)
                cur = val
            else:
                cur = (cur or "") + val
        elif var in WV_META_KEYS:
            meta[var] = val.rstrip(";")
    if cur:
        screens.append(cur)

    out = []
    for raw in screens:
        s = {"code": "", "title": "", "texts": [], "buttons": [],
             "flags": {}, "raw": raw}
        for cmd in _split_cmds(raw):
            f = cmd.split("$")
            head = f[0].upper()
            if head == "S" and len(f) >= 2:
                s["code"] = f[1]
            elif head == "TIT" and len(f) >= 4:
                s["title"] = "$".join(f[3:])
            elif head == "TXT" and len(f) >= 4:
                s["texts"].append("$".join(f[3:]))
            elif head == "BTNA" and len(f) >= 4:
                s["buttons"].append({
                    "idx": f[1], "label": f[2], "ret": f[3],
                    "flag": f[4] if len(f) > 4 else "",
                })
            else:
                if len(f) >= 2:
                    s["flags"][head] = "$".join(f[1:])
        if s["code"] or s["buttons"] or s["title"]:
            out.append(s)
    return {"screens": out, "meta": meta}


# ── screen_map.json (템플릿 코드 → 화면 이름) ─────────────────
_SCREEN_MAP = None


def screen_name(code):
    global _SCREEN_MAP
    if _SCREEN_MAP is None:
        _SCREEN_MAP = {}
        for p in (os.path.join(BASE_DIR, "screen_map.json"),
                  os.path.join(os.path.dirname(BASE_DIR), "screen_map.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    _SCREEN_MAP = json.load(f).get("screens", {}) or {}
                break
            except Exception:
                continue
    return (_SCREEN_MAP.get(code, {}) or {}).get("name") or ""


def screen_label(code):
    nm = screen_name(code)
    return f"{code}({nm})" if nm else (code or "-")


# ══════════════════════════════════════════════════════════════
# 2. 스냅샷 (원문 포함)
# ══════════════════════════════════════════════════════════════
def _t(el, tag):
    if el is None:
        return ""
    v = el.findtext(tag)
    return (v or "").strip()


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def _h(*parts):
    m = hashlib.md5()
    for p in parts:
        m.update((p or "").encode("utf-8", "ignore"))
    return m.hexdigest()[:12]


def snapshot_file(path):
    """단일 시나리오 파일 → 블록(Sequence) 단위 상세 스냅샷 + 연결(Link)."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None

    blocks, id2seq = {}, {}
    for n in root.findall("./Nodes/Node"):
        cp = n.find("CustomProperties")
        seq = _t(cp, "Sequence")
        nid = n.get("Id")
        if not seq:
            continue
        id2seq[nid] = seq
        script = _t(cp, "Script")
        pre = _t(cp, "PreScript")
        wv = parse_wv(pre + "\n" + script)
        blocks[seq] = {
            "seq": seq,
            "label": _clean(n.findtext("Text")),
            "type": n.get("NodeType") or "",
            "cond": _t(cp, "Condition"),
            "target": _t(cp, "TargetPage"),
            "target_node": _t(cp, "TargetNodeId"),
            "result_case": _t(cp, "ResultCase"),
            "ret": _t(cp, "r"),
            "comment": _clean(_t(cp, "Comment")),
            "script": script,
            "prescript": pre,
            "screens": wv["screens"],
            "wv_meta": wv["meta"],
        }

    links = []
    for lk in root.findall("./Links/Link"):
        o, d = lk.find("Origin"), lk.find("Destination")
        if o is None or d is None:
            continue
        fs, ts = id2seq.get(o.get("Id")), id2seq.get(d.get("Id"))
        if fs and ts:
            links.append([_clean(lk.findtext("Text")), fs, ts])

    with open(path, "rb") as f:
        fhash = hashlib.md5(f.read()).hexdigest()[:12]

    return {"file": os.path.basename(path), "hash": fhash,
            "blocks": blocks, "links": links}


def snapshot_folder(folder):
    """폴더 전체 → {파일명(lower): 스냅샷}"""
    snap = {}
    files = []
    for ext in SCN_EXT:
        files += glob.glob(os.path.join(folder, "*" + ext))
    for f in sorted(set(files)):
        s = snapshot_file(f)
        if s:
            snap[os.path.basename(f).lower()] = s
    return snap


def snapshot_folder_cached(folder, cache_path):
    """
    증분 스냅샷. 파일 (크기,mtime) 이 같으면 캐시 재사용 → 바뀐 파일만 재파싱.
    Returns: (snapshot, {"parsed":n, "reused":n})
    """
    import pickle
    cache = {}
    if cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path, "rb") as f:
                cache = pickle.load(f)
        except Exception:
            cache = {}

    files = []
    for ext in SCN_EXT:
        files += glob.glob(os.path.join(folder, "*" + ext))
    snap, newcache = {}, {}
    parsed = reused = 0
    for f in sorted(set(files)):
        key = os.path.basename(f).lower()
        st = os.stat(f)
        stamp = (st.st_size, int(st.st_mtime))
        hit = cache.get(key)
        if hit and hit.get("stamp") == stamp:
            snap[key] = hit["snap"]
            newcache[key] = hit
            reused += 1
            continue
        s = snapshot_file(f)
        if s:
            snap[key] = s
            newcache[key] = {"stamp": stamp, "snap": s}
            parsed += 1
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "wb") as f:
                pickle.dump(newcache, f, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            pass
    return snap, {"parsed": parsed, "reused": reused}


def folder_signature(folder):
    sig = []
    for ext in SCN_EXT:
        for f in glob.glob(os.path.join(folder, "*" + ext)):
            st = os.stat(f)
            sig.append(f"{os.path.basename(f)}|{st.st_size}|{int(st.st_mtime)}")
    return hashlib.md5("\n".join(sorted(sig)).encode()).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════
# 3. 변경 검출
# ══════════════════════════════════════════════════════════════
def _script_diff(old, new, title):
    """스크립트 라인 diff → {title, added, removed, lines[]}"""
    if (old or "") == (new or ""):
        return None
    ol = (old or "").splitlines()
    nl = (new or "").splitlines()
    lines, add, rem = [], 0, 0
    for d in difflib.unified_diff(ol, nl, lineterm="", n=2):
        if d.startswith("---") or d.startswith("+++"):
            continue
        if d.startswith("+"):
            add += 1
        elif d.startswith("-"):
            rem += 1
        lines.append(d)
        if len(lines) >= MAX_DIFF_LINES:
            lines.append("... (이하 생략)")
            break
    return {"title": title, "added": add, "removed": rem, "lines": lines}


def _screen_key(s):
    return (s.get("code", ""), s.get("title", ""),
            tuple(s.get("texts", [])),
            tuple((b["idx"], b["label"], b["ret"]) for b in s.get("buttons", [])),
            tuple(sorted(s.get("flags", {}).items())))


def _screen_changes(olds, news):
    """화면 목록 비교 → 사람이 읽는 변경 문구 + 전후 스냅"""
    if not olds and not news:
        return None
    if [_screen_key(x) for x in olds] == [_screen_key(x) for x in news]:
        return None

    msgs = []
    n = max(len(olds), len(news))
    for i in range(n):
        o = olds[i] if i < len(olds) else None
        w = news[i] if i < len(news) else None
        if o and not w:
            msgs.append(f"화면 삭제: {screen_label(o['code'])}")
            continue
        if w and not o:
            msgs.append(f"화면 추가: {screen_label(w['code'])}"
                        + (f" · 제목 '{w['title']}'" if w.get("title") else ""))
            continue
        if o["code"] != w["code"]:
            msgs.append(f"화면 템플릿 변경: {screen_label(o['code'])} → {screen_label(w['code'])}")
        if o.get("title") != w.get("title"):
            msgs.append(f"제목 변경: '{o.get('title')}' → '{w.get('title')}'")
        # 안내문
        ot, wt = o.get("texts", []), w.get("texts", [])
        for t in wt:
            if t not in ot:
                msgs.append(f"안내문 추가: '{t}'")
        for t in ot:
            if t not in wt:
                msgs.append(f"안내문 삭제: '{t}'")
        # 버튼 (반환코드 기준 매칭)
        ob = {b["ret"]: b for b in o.get("buttons", [])}
        wb = {b["ret"]: b for b in w.get("buttons", [])}
        for r in wb:
            if r not in ob:
                msgs.append(f"버튼 추가: '{wb[r]['label']}' (반환 {r})")
        for r in ob:
            if r not in wb:
                msgs.append(f"버튼 삭제: '{ob[r]['label']}' (반환 {r})")
        for r in set(ob) & set(wb):
            if ob[r]["label"] != wb[r]["label"]:
                msgs.append(f"버튼 문구 변경({r}): '{ob[r]['label']}' → '{wb[r]['label']}'")
            if ob[r]["idx"] != wb[r]["idx"]:
                msgs.append(f"버튼 위치 변경({r}): {ob[r]['idx']} → {wb[r]['idx']}")
        # 제어 플래그
        of, wf = o.get("flags", {}), w.get("flags", {})
        for k in sorted(set(of) | set(wf)):
            if of.get(k) != wf.get(k):
                msgs.append(f"화면설정 {k}: '{of.get(k,'-')}' → '{wf.get(k,'-')}'")
    if not msgs:
        msgs.append("화면 정의 변경")

    def enrich(lst):
        out = []
        for x in lst:
            d = dict(x)
            d["name"] = screen_name(d.get("code", ""))
            out.append(d)
        return out

    return {"msgs": msgs, "before": enrich(olds), "after": enrich(news)}


def _flow_changes(o, w):
    """블록 단위 업무 흐름 변화."""
    out = []
    if o["target"] != w["target"]:
        out.append({"what": "이동 대상", "from": o["target"] or "-", "to": w["target"] or "-",
                    "major": True})
    if o["target_node"] != w["target_node"]:
        out.append({"what": "이동 지점", "from": o["target_node"] or "-",
                    "to": w["target_node"] or "-"})
    if o["cond"] != w["cond"]:
        out.append({"what": "분기 조건", "from": o["cond"] or "-", "to": w["cond"] or "-",
                    "major": True})
    if o["ret"] != w["ret"]:
        out.append({"what": "반환 코드", "from": o["ret"] or "-", "to": w["ret"] or "-"})
    if o["result_case"] != w["result_case"]:
        out.append({"what": "복귀 분기", "from": o["result_case"] or "-",
                    "to": w["result_case"] or "-"})
    if o["type"] != w["type"]:
        out.append({"what": "블록 종류", "from": o["type"], "to": w["type"], "major": True})
    if o["label"] != w["label"]:
        out.append({"what": "블록 이름", "from": o["label"] or "-", "to": w["label"] or "-"})
    return out


def _link_changes(o_links, w_links, o_blocks, w_blocks):
    """연결(Link) 추가·삭제 → 흐름 구조 변화."""
    def name(blocks, seq):
        b = blocks.get(seq)
        return f"{b['label'] or b['type']}[{seq}]" if b else f"[{seq}]"

    os_ = {tuple(x) for x in o_links}
    ws_ = {tuple(x) for x in w_links}
    out = []
    for lbl, fs, ts in sorted(ws_ - os_):
        out.append({"kind": "added", "label": lbl,
                    "from": name(w_blocks, fs), "to": name(w_blocks, ts),
                    "from_seq": fs, "to_seq": ts})
    for lbl, fs, ts in sorted(os_ - ws_):
        out.append({"kind": "removed", "label": lbl,
                    "from": name(o_blocks, fs), "to": name(o_blocks, ts),
                    "from_seq": fs, "to_seq": ts})
    return out


def _block_brief(b):
    """추가/삭제 블록 요약 카드."""
    d = {"seq": b["seq"], "label": b["label"], "type": b["type"]}
    if b["target"]:
        d["target"] = b["target"]
    if b["cond"]:
        d["cond"] = b["cond"]
    if b["screens"]:
        d["screens"] = [{"code": s["code"], "name": screen_name(s["code"]),
                         "title": s["title"],
                         "buttons": [f"{x['label']}({x['ret']})" for x in s["buttons"]]}
                        for s in b["screens"]]
    if b["script"] or b["prescript"]:
        d["has_script"] = True
    return d


def diff_snapshots(old, new, locate=None):
    """
    두 스냅샷 비교 → 리포트.
    locate: fn(page, [seq,...]) -> {seq: location} (선택, 업무 위치 매핑)
    """
    o_files, w_files = set(old), set(new)
    report = {
        "summary": {
            "files_total": len(w_files),
            "files_added": 0, "files_removed": 0, "files_changed": 0, "files_same": 0,
            "blocks_added": 0, "blocks_removed": 0, "blocks_modified": 0,
            "flow_changes": 0, "script_changes": 0, "screen_changes": 0,
            "link_changes": 0, "truncated": False,
        },
        "added_files": [], "removed_files": [], "files": [],
    }
    S = report["summary"]
    total_changes = 0

    for key in sorted(w_files - o_files):
        f = new[key]
        S["files_added"] += 1
        report["added_files"].append({"file": f["file"], "blocks": len(f["blocks"])})
    for key in sorted(o_files - w_files):
        f = old[key]
        S["files_removed"] += 1
        report["removed_files"].append({"file": f["file"], "blocks": len(f["blocks"])})

    for key in sorted(o_files & w_files):
        of, wf = old[key], new[key]
        if of["hash"] == wf["hash"]:
            S["files_same"] += 1
            continue

        ob, wb = of["blocks"], wf["blocks"]
        added = sorted(set(wb) - set(ob))
        removed = sorted(set(ob) - set(wb))
        common = sorted(set(ob) & set(wb))

        entry = {"file": wf["file"], "status": "changed",
                 "added_blocks": [], "removed_blocks": [], "changed_blocks": [],
                 "link_changes": []}

        for s in added:
            entry["added_blocks"].append(_block_brief(wb[s]))
        for s in removed:
            entry["removed_blocks"].append(_block_brief(ob[s]))

        for s in common:
            o, w = ob[s], wb[s]
            flow = _flow_changes(o, w)
            sc_pre = _script_diff(o["prescript"], w["prescript"], "PreScript")
            sc_scr = _script_diff(o["script"], w["script"], "Script")
            scr = _screen_changes(o["screens"], w["screens"])
            meta_chg = []
            for k in sorted(set(o["wv_meta"]) | set(w["wv_meta"])):
                if o["wv_meta"].get(k) != w["wv_meta"].get(k):
                    meta_chg.append({"what": k, "from": o["wv_meta"].get(k, "-"),
                                     "to": w["wv_meta"].get(k, "-")})
            if not (flow or sc_pre or sc_scr or scr or meta_chg):
                continue
            ch = {"seq": s, "label": w["label"], "type": w["type"]}
            if flow:
                ch["flow"] = flow
                S["flow_changes"] += len(flow)
            scripts = [x for x in (sc_pre, sc_scr) if x]
            if scripts:
                ch["scripts"] = scripts
                S["script_changes"] += 1
            if scr:
                ch["screen"] = scr
                S["screen_changes"] += 1
            if meta_chg:
                ch["wv_meta"] = meta_chg
            entry["changed_blocks"].append(ch)
            total_changes += 1

        entry["link_changes"] = _link_changes(of["links"], wf["links"], ob, wb)

        if not (entry["added_blocks"] or entry["removed_blocks"]
                or entry["changed_blocks"] or entry["link_changes"]):
            S["files_same"] += 1
            continue

        S["files_changed"] += 1
        S["blocks_added"] += len(entry["added_blocks"])
        S["blocks_removed"] += len(entry["removed_blocks"])
        S["blocks_modified"] += len(entry["changed_blocks"])
        S["link_changes"] += len(entry["link_changes"])

        # 업무 위치 매핑 (선택)
        if locate:
            seqs = ([b["seq"] for b in entry["added_blocks"]]
                    + [b["seq"] for b in entry["changed_blocks"]])
            try:
                loc = locate(wf["file"], seqs)
                for b in entry["added_blocks"] + entry["changed_blocks"]:
                    if loc.get(b["seq"]):
                        b["location"] = loc[b["seq"]]
            except Exception:
                pass

        entry["counts"] = {
            "added": len(entry["added_blocks"]),
            "removed": len(entry["removed_blocks"]),
            "changed": len(entry["changed_blocks"]),
            "links": len(entry["link_changes"]),
        }
        report["files"].append(entry)

        if total_changes > MAX_BLOCK_CHANGES:
            S["truncated"] = True
            break

    # 영향받은 화면(보이는ARS) 목록 — 배포 리뷰용 상단 요약
    codes = {}
    for f in report["files"]:
        for b in f["changed_blocks"]:
            for s in (b.get("screen", {}) or {}).get("after", []) or []:
                if s.get("code"):
                    codes.setdefault(s["code"], set()).add(f["file"])
        for b in f["added_blocks"]:
            for s in b.get("screens", []) or []:
                if s.get("code"):
                    codes.setdefault(s["code"], set()).add(f["file"])
    report["screens_touched"] = [
        {"code": c, "name": screen_name(c), "files": sorted(v)}
        for c, v in sorted(codes.items())
    ]
    return report


def diff_folders(old_folder, new_folder, locate=None):
    return diff_snapshots(snapshot_folder(old_folder),
                          snapshot_folder(new_folder), locate=locate)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("사용: python scenario_deploy_diff.py <과거폴더> <운영폴더>")
        sys.exit(1)
    r = diff_folders(sys.argv[1], sys.argv[2])
    print(json.dumps(r["summary"], ensure_ascii=False, indent=2))
    for f in r["files"]:
        print(f"\n■ {f['file']}  {f['counts']}")
        for b in f["changed_blocks"][:5]:
            print(f"   [{b['seq']}] {b['label']}")
            for x in b.get("flow", []):
                print(f"      흐름) {x['what']}: {x['from']} → {x['to']}")
            for x in (b.get("screen", {}) or {}).get("msgs", []):
                print(f"      화면) {x}")
            for sc in b.get("scripts", []):
                print(f"      함수) {sc['title']} +{sc['added']}/-{sc['removed']}")
