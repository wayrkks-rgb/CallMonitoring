# -*- coding: utf-8 -*-
"""
block_index.py — 전체 블록 인덱스 (최상위 공용 유틸) [성능 최적화판]
개선: 파일 단위 lazy 파싱 + 메모리/디스크 캐시 → 콜당 1~3개 파일만 파싱(수 ms).
API: locate / screen_for_block / page_screen_map / get_file_index / warm / get_index
"""
import os
import re
import glob
import pickle
import hashlib
import xml.etree.ElementTree as ET

try:
    from phase import classify as _phase_classify
except Exception:
    _phase_classify = None

_DISK_CACHE_DIR = os.environ.get(
    "BLOCK_INDEX_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".block_index_cache"))


def _stem(name):
    if not name:
        return ""
    return os.path.splitext(os.path.basename(name))[0].lower()


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


_STEP_TITLE_TYPES = {"StartNode", "CallPageNode", "GotoPageNode", "ReturnPageNode"}
_MILESTONE_TYPES = _STEP_TITLE_TYPES | {
    "StopNode", "HangupNode", "StopSmartIVRNode",
    "GetDigitPromptNode", "IfNode", "SwitchNode"}
_EXC_EDGE = {"error", "timeout", "default", "-1", "-2", "err", "fail",
             "false", "retry", "재입력", "no", "n"}
_SCREEN_RE = re.compile(r'S\$(HLI[A-Z0-9]+)')


def _node_seq(n):
    cp = n.find("CustomProperties")
    if cp is None:
        return None
    return (cp.findtext("Sequence") or "").strip() or None


def _extract_screen(node_el):
    try:
        blob = ET.tostring(node_el, encoding="unicode")
    except Exception:
        return None
    m = _SCREEN_RE.search(blob)
    if not m:
        return None
    code = m.group(1)
    pm = re.search(r'(S\$' + re.escape(code) + r';[^"<]*)', blob)
    return {"code": code, "payload": pm.group(1) if pm else ("S$" + code)}


def _parse_graph(root):
    node_els = root.findall("./Nodes/Node") or root.findall(".//Node")
    nodes, id2node = [], {}
    for order, n in enumerate(node_els):
        nid = n.get("Id")
        nd = {"id": nid, "order": order, "seq": _node_seq(n),
              "type": n.get("NodeType") or n.get("Type") or "",
              "label": _clean(n.findtext("Text") or ""),
              "screen": _extract_screen(n)}
        nodes.append(nd)
        if nid is not None:
            id2node[nid] = nd
    preds = {}
    for lk in (root.findall("./Links/Link") or root.findall(".//Link")):
        o, d = lk.find("Origin"), lk.find("Destination")
        if o is None or d is None:
            continue
        preds.setdefault(d.get("Id"), []).append(
            (o.get("Id"), _clean(lk.findtext("Text") or "").lower()))
    return nodes, id2node, preds


def _resolve_step_title(node, id2node, preds):
    if node["type"] in _STEP_TITLE_TYPES and node["label"]:
        return node["label"]
    seen, stack = set(), [(node["id"], 0)]
    while stack:
        nid, depth = stack.pop(0)
        if nid in seen or depth > 40:
            continue
        seen.add(nid)
        for src, lbl in preds.get(nid, []):
            if lbl in _EXC_EDGE:
                continue
            sn = id2node.get(src)
            if not sn:
                continue
            if sn["type"] in _STEP_TITLE_TYPES and sn["label"]:
                return sn["label"]
            stack.append((src, depth + 1))
    return None


def _index_one_file(path):
    page = os.path.basename(path)
    out = {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, FileNotFoundError):
        return out
    nodes, id2node, preds = _parse_graph(root)
    for nd in nodes:
        seq = nd["seq"]
        if not seq:
            continue
        ntype, label = nd["type"], nd["label"]
        phase = None
        if _phase_classify:
            try:
                phase = _phase_classify(label or ntype)[0]
            except Exception:
                phase = None
        step_title = _resolve_step_title(nd, id2node, preds) or _stem(page)
        out.setdefault(seq, []).append({
            "seq": seq, "page": page, "menu_path": [_stem(page)],
            "step_no": None, "phase": phase, "step_title": step_title,
            "substep_no": nd["order"], "substep": label,
            "node_type": ntype, "milestone": ntype in _MILESTONE_TYPES,
            "screen": nd.get("screen")})
    return out


_file_cache = {}


def _disk_path(abspath):
    h = hashlib.md5(abspath.encode("utf-8")).hexdigest()
    return os.path.join(_DISK_CACHE_DIR, h + ".pkl")


def _resolve_file(folder, page):
    if not folder or not page:
        return None
    stem = _stem(page)
    for ext in (".xml", ".dxml"):
        p = os.path.join(folder, stem + ext)
        if os.path.isfile(p):
            return p
    for f in glob.glob(os.path.join(folder, "*.xml")) + \
             glob.glob(os.path.join(folder, "*.dxml")):
        if _stem(f) == stem:
            return f
    return None


def get_file_index(folder, page):
    path = _resolve_file(folder, page)
    if not path:
        return {}
    abspath = os.path.abspath(path)
    mtime = int(os.path.getmtime(abspath))
    c = _file_cache.get(abspath)
    if c and c[0] == mtime:
        return c[1]
    dp = _disk_path(abspath)
    try:
        if os.path.isfile(dp) and int(os.path.getmtime(dp)) >= mtime:
            idx = pickle.load(open(dp, "rb"))
            _file_cache[abspath] = (mtime, idx)
            return idx
    except Exception:
        pass
    idx = _index_one_file(abspath)
    _file_cache[abspath] = (mtime, idx)
    try:
        os.makedirs(_DISK_CACHE_DIR, exist_ok=True)
        pickle.dump(idx, open(dp, "wb"))
    except Exception:
        pass
    return idx


def locate(seq, page=None, folder=None, index=None):
    if index is None and folder and page:
        index = get_file_index(folder, page)
    hits = list((index or {}).get(seq, []))
    if page and hits:
        ps = _stem(page)
        sh = [h for h in hits if _stem(h["page"]) == ps]
        if sh:
            hits = sh
    return {"seq": seq, "found": bool(hits), "matches": hits}


def screen_for_block(seq, page=None, folder=None, index=None):
    for m in (locate(seq, page=page, folder=folder, index=index).get("matches") or []):
        if m.get("screen"):
            return m["screen"]
    return None


def _collect_screens(idx, out):
    for seq, recs in idx.items():
        for r in recs:
            if not r.get("screen"):
                continue
            out.setdefault(_stem(r["page"]), []).append({
                "seq": seq, "order": r["substep_no"],
                "screen_code": r["screen"]["code"], "payload": r["screen"]["payload"],
                "label": r["substep"], "step_title": r["step_title"]})


def page_screen_map(folder, page=None):
    result = {}
    if page:
        _collect_screens(get_file_index(folder, page), result)
    else:
        for f in glob.glob(os.path.join(folder, "*.xml")) + \
                 glob.glob(os.path.join(folder, "*.dxml")):
            _collect_screens(_index_one_file(f), result)
    for k in result:
        result[k].sort(key=lambda x: x["order"])
    return result


def warm(folder, pages=None):
    files = []
    if pages:
        for p in pages:
            rp = _resolve_file(folder, p)
            if rp:
                files.append(rp)
    else:
        files = glob.glob(os.path.join(folder, "*.xml")) + \
                glob.glob(os.path.join(folder, "*.dxml"))
    for f in files:
        try:
            get_file_index(folder, os.path.basename(f))
        except Exception:
            pass
    return len(files)


_full_cache = {}


def get_index(folder):
    files = sorted(glob.glob(os.path.join(folder, "*.xml")) +
                   glob.glob(os.path.join(folder, "*.dxml")))
    sig = tuple((os.path.basename(f), int(os.path.getmtime(f))) for f in files)
    c = _full_cache.get(folder)
    if c and c[0] == sig:
        return c[1]
    merged = {}
    for f in files:
        for k, v in _index_one_file(f).items():
            merged.setdefault(k, []).extend(v)
    _full_cache[folder] = (sig, merged)
    return merged