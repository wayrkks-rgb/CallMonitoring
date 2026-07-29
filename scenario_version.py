# -*- coding: utf-8 -*-
"""
scenario_version.py — 시나리오 버전 스냅샷 저장/비교 (배포 전후 영향도 확인용).

- 스냅샷: 파일 해시 + 블록(Sequence) 단위 내용 해시 + 연결(next) 해시
- 비교: 추가/삭제/변경된 시나리오·블록, 그리고 그 블록이 속한 업무 단계
"""
import os
import json
import hashlib
import glob
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(BASE_DIR, "snapshots")


def _h(*p):
    m = hashlib.md5()
    for x in p:
        m.update((x or "").encode("utf-8", "ignore"))
    return m.hexdigest()[:12]


def build_snapshot(folder):
    """폴더 전체 → 시나리오/블록 단위 스냅샷."""
    snap = {"files": {}}
    for f in sorted(glob.glob(os.path.join(folder, "*.xml")) +
                    glob.glob(os.path.join(folder, "*.dxml"))):
        name = os.path.basename(f)
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        blocks = {}
        for n in root.findall(".//Node"):
            cp = n.find("CustomProperties")
            seq = (cp.findtext("Sequence") if cp is not None else "") or ""
            if not seq:
                continue
            body = ((cp.findtext("Script") or "") + (cp.findtext("PreScript") or "")
                    if cp is not None else "")
            cond = (cp.findtext("Condition") if cp is not None else "") or ""
            tp = (cp.findtext("TargetPage") if cp is not None else "") or ""
            blocks[seq] = {
                "label": (n.findtext("Text") or "").strip(),
                "type": n.get("NodeType"),
                "hash": _h(cond, body, tp),      # 함수처리/조건/이동 변경 감지
                "target": tp,
            }
        with open(f, "rb") as fh:
            fhash = hashlib.md5(fh.read()).hexdigest()[:12]
        snap["files"][name] = {"hash": fhash, "blocks": blocks}
    return snap


def save_snapshot(folder, tag):
    os.makedirs(SNAP_DIR, exist_ok=True)
    snap = build_snapshot(folder)
    path = os.path.join(SNAP_DIR, f"{tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    return {"tag": tag, "files": len(snap["files"]), "path": path}


def list_snapshots():
    if not os.path.isdir(SNAP_DIR):
        return []
    return sorted(os.path.splitext(x)[0] for x in os.listdir(SNAP_DIR) if x.endswith(".json"))


def load_snapshot(tag):
    path = os.path.join(SNAP_DIR, f"{tag}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def diff(old, new):
    """스냅샷 비교 → 시나리오/블록 단위 변경 목록."""
    o, n = old.get("files", {}), new.get("files", {})
    res = {"added_files": sorted(set(n) - set(o)),
           "removed_files": sorted(set(o) - set(n)),
           "changed": []}
    for name in sorted(set(o) & set(n)):
        if o[name]["hash"] == n[name]["hash"]:
            continue
        ob, nb = o[name]["blocks"], n[name]["blocks"]
        added = sorted(set(nb) - set(ob))
        removed = sorted(set(ob) - set(nb))
        modified = [s for s in sorted(set(ob) & set(nb)) if ob[s]["hash"] != nb[s]["hash"]]
        res["changed"].append({
            "file": name,
            "added_blocks": [{"seq": s, "label": nb[s]["label"], "type": nb[s]["type"]} for s in added],
            "removed_blocks": [{"seq": s, "label": ob[s]["label"]} for s in removed],
            "modified_blocks": [{"seq": s, "label": nb[s]["label"],
                                 "target_changed": ob[s]["target"] != nb[s]["target"]}
                                for s in modified],
        })
    return res


if __name__ == "__main__":
    import sys
    folder = sys.argv[1] if len(sys.argv) > 1 else "scenario_cache/운영"
    tag = sys.argv[2] if len(sys.argv) > 2 else "base"
    print(save_snapshot(folder, tag))
