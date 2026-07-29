# -*- coding: utf-8 -*-
"""
scenario_menu.py — 고객 관점 메뉴 내비게이션 트리 추출 (store 통합용).

구조(3층):
  1) 진입 라우팅  : ivrmain — CallPage/Goto 로 채널(음성/WebVoice)·서비스(인증/콜백/방카…) 라우팅
  2) 채널 대메뉴  : ivrservice(음성) / W_WebVoice_Main(보이는) — GetDigit 번호 → 대메뉴
  3) 업무 메뉴    : 1_보험계약대출 → 하위 → 리프(처리 시나리오)

규칙:
  - 진입 페이지(ivrmain): CallPage/GotoPage 목적지를 자식으로. 이벤트/시스템/유틸 제외.
  - 그 외 페이지: GetDigit/Switch 의 번호(1~9)→TargetPage 만 메뉴 분기. 없으면 리프.
  - If(_InputValue) 등 입력검증은 처리로직 → 트리에서 제외(우측 상세에서 표시).
  - 메뉴 변형(메뉴_초기메뉴/단축1/단축2…)은 번호 union 으로 자동 병합.
  - WebVoice(W_ 접두)는 버리지 않고 태깅 → 프런트에서 토글.
"""
import os
import re

import xml.etree.ElementTree as ET

MENU_DIGIT = re.compile(r"^[1-9]$")
NAV_KEYS = {"0", "#", "*"}
ENTRY_CANDIDATES = ["ivrmain"]

# 이벤트/시스템/테스트 — 트리에서 제외
EXCLUDE = {
    "_error", "_hangup", "_timeout", "_no-next", "_no-return-page",
    "99999_테스트", "test", "w_webvoice_error", "w_webvoice_start",
}


def _stem(name):
    return os.path.splitext(os.path.basename(name))[0].lower()


def _is_wv_name(fname):
    b = os.path.basename(fname).upper()
    return b.startswith("W") or b.startswith("_W")


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def _excluded(fname):
    s = _stem(fname)
    return s in EXCLUDE or s.startswith("_std") or s == "<현재페이지>"


class MenuExtractor:
    def __init__(self, folder):
        self.folder = folder
        self.stemmap = {}
        for f in (os.listdir(folder) if os.path.isdir(folder) else []):
            if f.lower().endswith((".xml", ".dxml")):
                self.stemmap.setdefault(_stem(f), f)
        self._cache = {}
        self.diag = {"routing": 0, "menu_pages": 0, "leaf_pages": 0}

    def _resolve(self, name):
        real = self.stemmap.get(_stem(name))
        return os.path.join(self.folder, real) if real else None

    def _channel(self, fname):
        """음성전용(voice) / WebVoice전용(webvoice) / 공통(shared)."""
        if _is_wv_name(fname):
            return "webvoice"
        data = self._load(fname)
        if data:
            for n in data["nodes"].values():
                if n["tp"] and _is_wv_name(n["tp"]):
                    return "shared"
        return "voice"

    def _load(self, fname):
        key = _stem(fname)
        if key in self._cache:
            return self._cache[key]
        path = self._resolve(fname)
        if not path or not os.path.isfile(path):
            self._cache[key] = None
            return None
        try:
            r = ET.parse(path).getroot()
        except ET.ParseError:
            self._cache[key] = None
            return None
        nodes = {}
        for n in r.findall(".//Node"):
            cp = n.find("CustomProperties")
            nodes[n.get("Id")] = {
                "type": n.get("NodeType"),
                "text": _clean(n.findtext("Text")),
                "tp": (cp.findtext("TargetPage") if cp is not None else "") or "",
            }
        links = []
        for lk in r.findall(".//Link"):
            o = lk.find("Origin"); d = lk.find("Destination")
            links.append(((lk.findtext("Text") or "").strip(),
                          o.get("Id") if o is not None else "",
                          d.get("Id") if d is not None else ""))
        self._cache[key] = {"nodes": nodes, "links": links}
        return self._cache[key]

    def _digit_menu(self, fname):
        """번호(1~9)→TargetPage 메뉴. (여러 변형 union 병합)"""
        data = self._load(fname)
        if not data:
            return None
        nodes, links = data["nodes"], data["links"]
        options, navkeys = {}, {}
        for lbl, oid, did in links:
            on = nodes.get(oid, {}); dn = nodes.get(did, {})
            if on.get("type") not in ("SwitchNode", "GetDigitPromptNode"):
                continue
            lbl = lbl.strip(); tp = dn.get("tp", "")
            if MENU_DIGIT.match(lbl):
                if tp and not _excluded(tp) and lbl not in options:
                    options[lbl] = {"digit": lbl, "label": dn.get("text", ""), "target": tp}
            elif lbl in NAV_KEYS:
                navkeys.setdefault(lbl, _clean(dn.get("text", "") or tp)[:32])
        return {"options": options, "navkeys": navkeys}

    def _routing(self, fname):
        """진입/라우팅 페이지: CallPage/Goto 목적지를 자식으로 (이벤트/시스템 제외)."""
        data = self._load(fname)
        if not data:
            return []
        seen, out = set(), []
        for n in data["nodes"].values():
            if n["type"] not in ("CallPageNode", "GotoPageNode"):
                continue
            tp = n["tp"]
            if not tp or _excluded(tp):
                continue
            s = _stem(tp)
            if s in seen:
                continue
            seen.add(s)
            out.append({"label": n["text"], "target": tp})
        return out

    def build(self, entry, is_entry=False, _seen=None, depth=0, max_depth=12):
        _seen = _seen if _seen is not None else set()
        st = _stem(entry)
        real = self._resolve(entry)
        node = {"page": os.path.basename(real) if real else entry,
                "digit": None, "label": "", "children": [], "navkeys": {},
                "leaf": False, "routing": False,
                "channel": self._channel(entry) if real else "voice"}
        if real is None:
            node["missing"] = True; node["leaf"] = True
            return node
        if st in _seen or depth > max_depth:
            node["revisit"] = True; node["leaf"] = True
            return node
        _seen = _seen | {st}

        if is_entry:
            # 진입 라우팅 페이지
            routes = self._routing(entry)
            if routes:
                node["routing"] = True
                self.diag["routing"] += 1
                for r in routes:
                    child = self.build(r["target"], False, _seen, depth + 1, max_depth)
                    child["label"] = r["label"]
                    node["children"].append(child)
                return node

        mo = self._digit_menu(entry)
        if not mo or not mo["options"]:
            node["leaf"] = True
            self.diag["leaf_pages"] += 1
            return node
        node["navkeys"] = mo["navkeys"]
        self.diag["menu_pages"] += 1
        for dg in sorted(mo["options"]):
            opt = mo["options"][dg]
            child = self.build(opt["target"], False, _seen, depth + 1, max_depth)
            child["digit"] = dg
            child["label"] = opt["label"]
            node["children"].append(child)
        return node


def find_menu_roots(folder):
    """진입점: ivrmain 있으면 그것. 없으면 번호분기하며 안 불리는 메뉴페이지."""
    ex = MenuExtractor(folder)
    for cand in ENTRY_CANDIDATES:
        if cand in ex.stemmap:
            return [ex.stemmap[cand]]
    referenced, menu_pages = set(), []
    for stem, fname in ex.stemmap.items():
        mo = ex._digit_menu(fname)
        if mo and mo["options"]:
            menu_pages.append(fname)
            for opt in mo["options"].values():
                referenced.add(_stem(opt["target"]))
    roots = [f for f in menu_pages if _stem(f) not in referenced]
    return sorted(roots) or sorted(menu_pages)


def build_menu_tree(folder, entry):
    ex = MenuExtractor(folder)
    is_entry = _stem(entry) in ENTRY_CANDIDATES
    tree = ex.build(entry, is_entry=is_entry)
    return {"entry": os.path.basename(entry), "tree": tree, "diag": ex.diag}


if __name__ == "__main__":
    import sys, json
    folder = sys.argv[1] if len(sys.argv) > 1 else "scenario_cache/운영"
    entry = sys.argv[2] if len(sys.argv) > 2 else "ivrmain.xml"
    print(json.dumps(build_menu_tree(folder, entry), ensure_ascii=False, indent=2))
