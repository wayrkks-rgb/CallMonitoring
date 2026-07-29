# -*- coding: utf-8 -*-
"""
scenario_store.py — 환경별 시나리오 캐시 폴더를 읽어 화면 데이터를 관리.

개선점(v2):
- 페이지 파싱 캐시(mtime 기반) → 반복 요청/병합 시 재파싱 안 함
- 서브 병합을 '깊이 제한 + 공유 유틸 자동 접기 + 노드 상한'으로 제한 → 대량 메뉴 지연 해결
- 접힌 서브 노드에 노드 수(sub_count) 부여 → 프런트에서 배지로 표시
- 여러 페이지 병합 시 페이지 단위 그룹(compound) 정보 제공
"""
import os
import glob
import threading

from scenario_parser import parse_page
from scenario_summary import build_summary
from scenario_flow import build_core_flow
from scenario_menu import MenuExtractor, find_menu_roots
from scenario_menu import build_menu_tree, find_menu_roots

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_ROOT = os.path.join(BASE_DIR, "scenario_cache")

UTILITY_MIN_PARENTS = 3   # 이 수 이상 부모가 참조하면 '공유 유틸' → 자동으로 펼치지 않음

_lock = threading.Lock()
_cache = {}          # env -> env cache
_page_cache = {}     # path -> (mtime, graph)


def _env_dir(env):
    return os.path.join(CACHE_ROOT, env)


def list_envs():
    if not os.path.isdir(CACHE_ROOT):
        return []
    return sorted(d for d in os.listdir(CACHE_ROOT)
                  if os.path.isdir(os.path.join(CACHE_ROOT, d)))


def _scan_files(env):
    d = _env_dir(env)
    return sorted(glob.glob(os.path.join(d, "*.dxml")) +
                  glob.glob(os.path.join(d, "*.xml")))


def _signature(files):
    return tuple((os.path.basename(f), int(os.path.getmtime(f)), os.path.getsize(f))
                 for f in files)


def _stem(name):
    return os.path.splitext(os.path.basename(name))[0].lower()


def _parse_cached(path):
    """페이지 파싱 결과 캐시 (mtime 이 같으면 재사용)."""
    m = os.path.getmtime(path)
    c = _page_cache.get(path)
    if c and c[0] == m:
        return c[1]
    g = parse_page(path)
    _page_cache[path] = (m, g)
    return g


def _build_env(env):
    files = _scan_files(env)
    sig = _signature(files)
    stem_map, node_count, referenced = {}, {}, {}
    for f in files:
        stem_map.setdefault(_stem(f), os.path.basename(f))
        try:
            g = _parse_cached(f)
        except Exception:
            node_count[_stem(f)] = 0
            continue
        node_count[_stem(f)] = len(g["nodes"])
        for n in g["nodes"]:
            tp = n.get("target_page")
            if tp:
                referenced.setdefault(_stem(tp), set()).add(_stem(f))
    ref_count = {k: len(v) for k, v in referenced.items()}
    roots = sorted(stem_map[s] for s in (set(stem_map) - set(referenced)))
    return {"sig": sig, "files": files, "stem_map": stem_map,
            "node_count": node_count, "ref_count": ref_count,
            "roots": roots, "summary": {}, "coreflow": {}, "menutree": {}}


def _get_env(env):
    with _lock:
        files = _scan_files(env)
        sig = _signature(files)
        c = _cache.get(env)
        if c is None or c["sig"] != sig:
            c = _build_env(env)
            _cache[env] = c
        return c


def _dir_of(c):
    return os.path.dirname(c["files"][0]) if c["files"] else CACHE_ROOT


def _resolve(c, name):
    real = c["stem_map"].get(_stem(name))
    return os.path.join(_dir_of(c), real) if real else None


def _is_utility(c, stem):
    return c["ref_count"].get(stem, 0) >= UTILITY_MIN_PARENTS


# ── 공개 API ───────────────────────────────────────────────
def get_scenarios(env):
    c = _get_env(env)
    roots = set(c["roots"])
    items = []
    for f in c["files"]:
        name = os.path.basename(f)
        stem = _stem(f)
        items.append({
            "name": name,
            "is_root": name in roots,
            "nodes": c["node_count"].get(stem, 0),
            "shared": _is_utility(c, stem),   # 여러 곳에서 쓰는 공용 시나리오
        })
    return {"env": env, "count": len(items), "roots": c["roots"], "scenarios": items}


def get_graph(env, entry, depth=0, cap_pages=14, cap_nodes=320):
    """
    depth=0 : 진입 페이지만 (서브는 접힌 노드로 표시)
    depth=1 : 진입 + 바로 아래 서브까지 병합 (공유 유틸 제외, 상한 적용)
    """
    c = _get_env(env)
    root_path = _resolve(c, entry)
    if not root_path or not os.path.isfile(root_path):
        return {"error": f"시나리오 없음: {entry}"}

    # 포함할 페이지 선정 (BFS, depth 제한 + 공유유틸 제외 + 상한)
    included, inc_set = [], set()
    queue = [(os.path.basename(root_path), 0)]
    total_nodes, capped = 0, False
    while queue:
        fname, lvl = queue.pop(0)
        stem = _stem(fname)
        if stem in inc_set:
            continue
        nc = c["node_count"].get(stem, 0)
        if included and (len(included) >= cap_pages or total_nodes + nc > cap_nodes):
            capped = True
            continue
        inc_set.add(stem)
        included.append(fname)
        total_nodes += nc
        if lvl >= depth:
            continue
        fpath = _resolve(c, fname)
        for n in _parse_cached(fpath)["nodes"]:
            tp = n.get("target_page")
            if not tp:
                continue
            rp = _resolve(c, tp)
            if not rp or _stem(tp) in inc_set or _is_utility(c, _stem(tp)):
                continue
            queue.append((os.path.basename(rp), lvl + 1))

    multi = len(included) > 1
    nodes, edges = [], []
    for fname in included:
        stem = _stem(fname)
        pfx = stem + ":"
        g = _parse_cached(_resolve(c, fname))
        if multi:
            nodes.append({"id": "grp:" + stem, "group": True, "label": fname})
        for n in g["nodes"]:
            m = dict(n)
            m["id"] = pfx + n["id"]
            m["page"] = fname
            if multi:
                m["parent"] = "grp:" + stem
            tp = n.get("target_page")
            if tp:
                sub = _stem(tp)
                if sub not in inc_set:      # 접힌 서브
                    m["sub_target"] = tp
                    m["sub_count"] = c["node_count"].get(sub)
                    m["sub_exists"] = _resolve(c, tp) is not None
                    m["sub_shared"] = _is_utility(c, sub)
            nodes.append(m)
        for e in g["edges"]:
            edges.append({"from": pfx + e["from"], "to": pfx + e["to"], "label": e["label"]})
        # 펼쳐진 서브로 이어지는 교차 엣지
        for n in g["nodes"]:
            tp = n.get("target_page")
            if tp and _stem(tp) in inc_set and _stem(tp) != stem:
                sg = _parse_cached(_resolve(c, tp))
                st = next((x for x in sg["nodes"] if x["type"] == "StartNode"), None)
                if st:
                    edges.append({"from": pfx + n["id"], "to": _stem(tp) + ":" + st["id"],
                                  "label": "호출", "sub": True})

    return {"env": env, "entry": entry, "depth": depth, "multi": multi,
            "pages": included, "capped": capped, "nodes": nodes, "edges": edges}


def get_menus(env):
    """대메뉴(진입점) 목록 — 번호분기를 하는데 아무도 이 파일로 이동하지 않는 페이지."""
    c = _get_env(env)
    roots = find_menu_roots(_dir_of(c))
    return {"env": env, "menus": [os.path.basename(f) for f in roots]}


def get_menu_tree(env, entry):
    c = _get_env(env)
    cache = c.setdefault("menutree", {})
    if entry in cache:
        return cache[entry]
    if not _resolve(c, entry):
        return {"error": f"시나리오 없음: {entry}"}
    t = build_menu_tree(_dir_of(c), entry)
    cache[entry] = t
    return t


def get_menu_roots(env):
    """대메뉴(진입점) 목록 — 번호분기를 하면서 아무도 그리로 이동 안 하는 페이지."""
    c = _get_env(env)
    if c.get("menu_roots") is None:
        c["menu_roots"] = find_menu_roots(_dir_of(c))
    return {"env": env, "roots": c["menu_roots"]}


def get_menu_tree(env, entry):
    """진입 파일 기준 메뉴 내비게이션 트리 (리프까지 재귀)."""
    c = _get_env(env)
    ex = c.get("menu_ex")
    if ex is None:
        ex = MenuExtractor(_dir_of(c))
        c["menu_ex"] = ex
    from scenario_menu import _stem as _mstem, ENTRY_CANDIDATES
    is_entry = _mstem(entry) in ENTRY_CANDIDATES
    return {"env": env, "entry": entry, "tree": ex.build(entry, is_entry=is_entry)}


def get_menu_summary(env, entry, max_leaves=40):
    """대메뉴 요약 — 하위 메뉴 트리 + 각 리프의 업무 흐름(phase) 요약."""
    c = _get_env(env)
    cache = c.setdefault("menusum", {})
    if entry in cache:
        return cache[entry]
    tree = get_menu_tree(env, entry)["tree"]

    count = {"n": 0}
    _fc = {}

    def flow_of(page):
        """시나리오의 phase 시퀀스 (경로 재사용 위해 캐시)."""
        if page in _fc:
            return _fc[page]
        if count["n"] >= max_leaves * 3:
            return None
        count["n"] += 1
        try:
            bf = get_bizflow(env, page, mode="summary")
        except Exception:
            return None
        if bf.get("error"):
            return None
        # 현업 제공용: STEP 전체(항목·조건·MCI·분기)를 그대로 담는다
        from biz_lang import clean_label, describe_item, phase_desc
        out = []
        for m in bf.get("summary_steps", []):
            items = []
            for s in m["steps"][:14]:
                mci = ({"kind": s["mci"]["kind"],
                        "nin": len(s["mci"]["inputs"]), "nout": len(s["mci"]["outputs"])}
                       if s.get("mci") else None)
                lbl = clean_label(s.get("label"))
                items.append({
                    "label": lbl,
                    "desc": describe_item(s.get("label"), m["phase"],
                                          s.get("cond_plain"), s.get("mci"),
                                          s.get("auth_method")),
                    "auth_method": s.get("auth_method"),
                    "mci": mci,
                    "_mci_raw": s.get("mci"),
                    "seq": s.get("seq"), "blocks": [s.get("seq")] if s.get("seq") else [],
                    "cond": s.get("cond"), "script_hash": s.get("seq"),
                    "exc": [[l, clean_label(t)] for l, t in (s.get("exc") or [])][:4],
                })
            out.append({
                "step_no": m.get("step_no"), "phase": m["phase"], "color": m["color"],
                "desc": phase_desc(m["phase"]),
                "routes": [{"from": clean_label(r.get("from")), "to": clean_label(r.get("to")),
                            "label": r.get("label"), "cond": r.get("cond")}
                           for r in (m.get("routes") or [])],
                "steps": items,
            })
        _fc[page] = out
        return out

    def is_menu_child(ch):
        """하위 메뉴 판정: 자기 자식이 있으면 메뉴. 없으면(액션) 처리성만 제외."""
        if ch.get("missing"):
            return False
        if ch.get("children"):
            return True          # 자체 번호분기를 가진 진짜 메뉴 (예: 1_1 약대조회)
        from phase import classify
        nm = (ch.get("page") or "") + " " + (ch.get("label") or "")
        ph, _c, _m, util = classify(nm)
        if util:
            return False
        # 액션 리프인데 순수 처리 단계면 메뉴가 아님 (예: 전화번호입력)
        return ph not in ("인증", "입력", "안내")

    def walk(node, depth=0):
        out = {
            "page": node.get("page"), "label": node.get("label") or node.get("page"),
            "digit": node.get("digit"), "leaf": node.get("leaf"),
            "missing": node.get("missing"), "channel": node.get("channel"),
            "children": [],
        }
        # 모든 노드가 '자기 몫'의 처리만 갖는다 (상위 처리 반복 없음)
        if not node.get("missing"):
            out["flow"] = flow_of(node.get("page"))
        kids = [k for k in (node.get("children") or []) if is_menu_child(k)]
        if kids and depth < 5:
            for ch in kids:
                out["children"].append(walk(ch, depth + 1))
        return out

    root = walk(tree)
    res = {"env": env, "entry": os.path.basename(_resolve(c, entry) or entry),
           "tree": root, "leaves_computed": count["n"]}
    cache[entry] = res
    return res


def get_tree_doc(env, entry, dev=False):
    """트리 구성도. dev=True 면 각 세부단계에 실제 블록 소스를 붙인다(개발자 모드)."""
    import datetime
    from scenario_doc import build_node_doc, file_version
    c = _get_env(env)
    cache = c.setdefault("treedoc_dev" if dev else "treedoc", {})
    if entry in cache:
        return cache[entry]

    ms = get_menu_summary(env, entry)
    folder = _dir_of(c)
    versions = []

    _srcidx = {}

    def src_of(page):
        """페이지의 블록 소스 인덱스 (seq → 원본)."""
        if page in _srcidx:
            return _srcidx[page]
        try:
            d = get_detail(env, page)
            idx = {b["seq"]: b for b in d.get("blocks", []) if b.get("seq")}
        except Exception:
            idx = {}
        _srcidx[page] = idx
        return idx

    def attach_src(page, groups):
        idx = src_of(page)
        for g in groups:
            for sb in g.get("substeps", []):
                srcs = []
                for sq in sb.get("blocks") or []:
                    b = idx.get(sq)
                    if not b:
                        continue
                    srcs.append({
                        "seq": sq, "label": b.get("label"), "type": b.get("type"),
                        "cond": b.get("cond"), "script": b.get("script"),
                        "input_cfg": b.get("input_cfg") or [],
                        "sets": b.get("sets") or [],
                        "uses": b.get("uses") or [],
                        "next": b.get("next") or [], "prev": b.get("prev") or [],
                        "sub_target": b.get("sub_target"),
                    })
                if srcs:
                    sb["src"] = srcs

    def conv(n):
        out = {
            "page": n.get("page"), "label": n.get("label"), "digit": n.get("digit"),
            "missing": n.get("missing"), "channel": n.get("channel"),
            "kind": "menu" if (n.get("children") or []) else "action",
            "children": [conv(ch) for ch in (n.get("children") or [])],
        }
        if n.get("flow"):
            out["groups"] = build_node_doc(n["flow"])
            if dev:
                attach_src(n.get("page"), out["groups"])
            if dev and n.get("page"):
                _attach_source(out, n["page"], env)
        v = file_version(folder, n.get("page") or "")
        if v:
            versions.append(v)
            out["version"] = v
        return out

    def _attach_source(node_out, page, env_):
        """세부단계의 blocks(Sequence) → 실제 소스 첨부."""
        try:
            det = get_detail(env_, page)
        except Exception:
            return
        if det.get("error"):
            return
        bymap = {b["seq"]: b for b in det.get("blocks", []) if b.get("seq")}
        for g in node_out.get("groups", []):
            for sb in g.get("substeps", []):
                srcs = []
                for sq in sb.get("blocks") or []:
                    b = bymap.get(sq)
                    if not b:
                        continue
                    srcs.append({
                        "seq": sq, "label": b.get("label"), "type": b.get("type"),
                        "cond": b.get("cond"), "script": b.get("script"),
                        "sets": b.get("sets") or [], "uses": b.get("uses") or [],
                        "next": b.get("next") or [], "prev": b.get("prev") or [],
                        "sub_target": b.get("sub_target") or "",
                        "input_cfg": b.get("input_cfg") or [],
                    })
                if srcs:
                    sb["source"] = srcs

    tree = conv(ms["tree"])
    # 목차 (메뉴 경로 목록)
    toc = []

    def walk_toc(n, path):
        p2 = path + [n.get("label") or n.get("page")]
        if n.get("kind") == "action" or n.get("groups"):
            toc.append({"path": p2, "page": n.get("page")})
        for ch in n.get("children") or []:
            walk_toc(ch, p2)
    walk_toc(tree, [])

    res = {
        "env": env, "entry": ms["entry"], "tree": tree, "toc": toc,
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "versions": versions,
        "snapshot_hash": __import__("hashlib").md5(
            "".join(sorted(v["hash"] for v in versions)).encode()).hexdigest()[:12],
    }
    cache[entry] = res
    return res



def build_locator(env, entry):
    """블록ID → 업무 위치 인덱스 (로그 뷰어 연계용)."""
    c = _get_env(env)
    key = "loc:" + entry
    if c.get(key):
        return c[key]
    doc = get_tree_doc(env, entry)
    idx = {}

    def walk(n, path):
        p2 = path + [n.get("label") or n.get("page")]
        for gi, g in enumerate(n.get("groups") or [], 1):
            for si, sb in enumerate(g.get("substeps") or [], 1):
                for sq in sb.get("blocks") or []:
                    if not sq:
                        continue
                    idx.setdefault(sq, []).append({
                        "seq": sq, "page": n.get("page"),
                        "menu_path": p2[1:] if len(p2) > 1 else p2,
                        "step_no": gi, "phase": g.get("phase"),
                        "step_title": g.get("title"),
                        "substep_no": si, "substep": sb.get("text"),
                    })
        for ch in n.get("children") or []:
            walk(ch, p2)
    walk(doc["tree"], [])
    c[key] = idx
    return idx


def locate_blocks(env, seqs, entry=None, page=None):
    """
    블록ID → 업무 위치. 같은 Sequence 가 여러 시나리오에 있을 수 있으므로 목록 반환.
    page(시나리오 파일명)를 주면 그 파일 것만 필터 → 로그의 시나리오명과 함께 쓰면 정확.
    """
    entries = [entry] if entry else get_menu_roots(env)["roots"]
    merged = {}
    for e in entries:
        try:
            for k, v in build_locator(env, e).items():
                merged.setdefault(k, [])
                have = {(x["page"], x["step_no"], x["substep_no"]) for x in merged[k]}
                for item in v:
                    kk = (item["page"], item["step_no"], item["substep_no"])
                    if kk not in have:
                        merged[k].append(item)
                        have.add(kk)
        except Exception:
            continue
    out = []
    for sq in seqs:
        hits = merged.get(sq) or []
        if page:
            hits = [h for h in hits if (h.get("page") or "").lower() == page.lower()]
        out.append({"seq": sq, "found": bool(hits), "matches": hits})
    return {"env": env, "results": out}


def snapshot_save(env, tag):
    from scenario_version import save_snapshot
    c = _get_env(env)
    return save_snapshot(_dir_of(c), tag)


def snapshot_list():
    from scenario_version import list_snapshots
    return list_snapshots()


def snapshot_diff(old_tag, new_tag=None, env=None):
    """스냅샷 비교. new_tag 없으면 현재 폴더와 비교."""
    from scenario_version import load_snapshot, build_snapshot, diff
    old = load_snapshot(old_tag)
    if not old:
        return {"error": f"스냅샷 없음: {old_tag}"}
    if new_tag:
        new = load_snapshot(new_tag)
        if not new:
            return {"error": f"스냅샷 없음: {new_tag}"}
    else:
        c = _get_env(env)
        new = build_snapshot(_dir_of(c))
    d = diff(old, new)
    # 변경 블록을 업무 위치로 매핑
    if env:
        for ch in d["changed"]:
            seqs = [m["seq"] for m in ch["modified_blocks"]] + \
                   [a["seq"] for a in ch["added_blocks"]]
            if not seqs:
                continue
            # 변경된 '그 파일' 기준으로만 위치를 찾는다 (Sequence 는 파일별 중복 가능)
            res = locate_blocks(env, seqs, page=ch["file"])["results"]
            loc = {r["seq"]: (r["matches"][0] if r["matches"] else None) for r in res}
            for m in ch["modified_blocks"] + ch["added_blocks"]:
                m["location"] = loc.get(m["seq"])
    d["old"] = old_tag
    d["new"] = new_tag or "현재"
    return d


def get_detail(env, entry):
    """상세 뷰: 블록 카드 + 변수 추적 + 연결정보."""
    c = _get_env(env)
    from scenario_detail import build_detail, build_glossary
    d = _dir_of(c)
    if c.get("glossary") is None:
        c["glossary"] = build_glossary(d)
    real = _resolve(c, entry)
    if not real:
        return {"error": f"시나리오 없음: {entry}"}
    return build_detail(d, os.path.basename(real), c["glossary"])


def get_bizflow(env, entry, mode="summary"):
    """업무 관통 flow (재귀 인라인 + MCI 카드 + phase 묶음)."""
    c = _get_env(env)
    from scenario_bizflow import build_bizflow
    from phase import group_steps
    from scenario_detail import build_glossary, _plain_cond
    d = _dir_of(c)
    if c.get("glossary") is None:
        c["glossary"] = build_glossary(d)
    gl = c["glossary"]
    bf = build_bizflow(d, entry, mode=mode)
    if bf.get("missing"):
        return {"error": f"시나리오 없음: {entry}"}

    def group(node):
        steps = node.get("steps", [])
        vis = [s for s in steps if s.get("milestone") or s.get("mci") or s.get("expanded")]
        blocks = group_steps(vis)
        for b in blocks:
            for s in b["steps"]:
                if s.get("cond"):
                    s["cond_plain"] = _plain_cond(s["cond"], gl)
                if s.get("expanded"):
                    s["expanded_blocks"] = group(s["expanded"])
        return blocks

    blocks = group(bf)
    # STEP 번호 부여 (로그·유틸 제외)
    n = 0
    for b in blocks:
        if b["phase"] == "로그·유틸":
            b["step_no"] = None
            continue
        n += 1
        b["step_no"] = n

    # 구성도용: 같은 phase 를 병합 (등장 순서 유지) → 6~9단계
    merged, idx = [], {}
    for b in blocks:
        if b["phase"] == "로그·유틸":
            continue
        if b["phase"] in idx:
            merged[idx[b["phase"]]]["steps"].extend(b["steps"])
        else:
            idx[b["phase"]] = len(merged)
            merged.append({"phase": b["phase"], "color": b["color"], "steps": list(b["steps"])})
    for i, m in enumerate(merged, 1):
        m["step_no"] = i
        # 이 단계의 실패/예외 경로 요약
        routes = []
        for s in m["steps"]:
            for lbl, tgt in (s.get("exc") or []):
                routes.append({"from": s["label"], "label": lbl, "to": tgt,
                               "cond": s.get("cond_plain")})
            if s.get("branch"):
                import re as _re
                for lbl, tgt in (s.get("next") or []):
                    if lbl == "false" and _re.search(
                            r"재입력|잘못|오류|상담원|불가|초기메뉴|종료", tgt or ""):
                        routes.append({"from": s["label"], "label": lbl, "to": tgt,
                                       "cond": s.get("cond_plain")})
        # 중복 제거
        seen_r, uniq = set(), []
        for r in routes:
            k = (r["from"], r["to"])
            if k in seen_r:
                continue
            seen_r.add(k)
            uniq.append(r)
        m["routes"] = uniq[:6]

    return {"env": env, "entry": os.path.basename(_resolve(c, entry) or entry),
            "mode": mode, "steps_total": n, "summary_steps": merged, "blocks": blocks}


def get_coreflow(env, entry):
    c = _get_env(env)
    cache = c.setdefault("coreflow", {})
    if entry in cache:
        return cache[entry]
    path = _resolve(c, entry)
    if not path or not os.path.isfile(path):
        return {"error": f"시나리오 없음: {entry}"}
    cf = build_core_flow(path)
    cache[entry] = cf
    return cf


def get_doc(env, entry):
    c = _get_env(env)
    if entry in c["summary"]:
        return c["summary"][entry]
    path = _resolve(c, entry)
    if not path or not os.path.isfile(path):
        return {"error": f"시나리오 없음: {entry}"}
    doc = build_summary(path)
    c["summary"][entry] = doc
    return doc


if __name__ == "__main__":
    for env in list_envs():
        d = get_scenarios(env)
        print(env, "→", d["count"], "개, 루트", len(d["roots"]))
