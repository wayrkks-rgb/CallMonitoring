# -*- coding: utf-8 -*-
"""
mci_extractor.py — MCI 전문(HostComm) input↔output 짝짓기.

- INPUT : 호출자의 PreScript 에서 rcveSrvcId + H_Input_NN (값 + 한글주석)
- OUTPUT: MCI_HostComm 의 '결과값 출력' ScriptNode 안 switch(rcveSrvcId) →
          서비스ID별 Output_NN = obj["payload"]["필드"]
서비스ID 접미로 성격 추정: vw=조회, in/c=등록/처리, r=조회.
"""
import os
import re
import glob
import xml.etree.ElementTree as ET

SRVC_ASSIGN = re.compile(r'rcveSrvcId\s*=\s*["\'](\w+)["\']')
HINPUT = re.compile(r'(app\.H_Input_\d+)\s*=\s*([^;/\n]+?)\s*;?\s*(?://\s*(.+))?$')
OUTPUT_MAP = re.compile(r'(Output_\d+)\s*=\s*obj\["payload"\]\["(\w+)"\]')
CASE = re.compile(r'''case\s+['"]([^'"]+)['"]\s*:''')


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def _kind(srvc):
    s = srvc.lower()
    if s.endswith("vw") or s.endswith("r"):
        return "조회"
    if s.endswith("in") or s.endswith("c"):
        return "등록/처리"
    return "전문"


def extract_inputs(folder):
    """모든 파일에서 rcveSrvcId 정의 노드 → {srvcId: {caller, inputs:[(var,val,comment)]}}."""
    result = {}
    for f in glob.glob(os.path.join(folder, "*.xml")) + glob.glob(os.path.join(folder, "*.dxml")):
        try:
            r = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        for n in r.findall(".//Node"):
            cp = n.find("CustomProperties")
            if cp is None:
                continue
            body = (cp.findtext("Script") or "") + "\n" + (cp.findtext("PreScript") or "")
            m = SRVC_ASSIGN.search(body)
            if not m:
                continue
            srvc = m.group(1)
            inputs = []
            for line in body.split("\n"):
                hm = HINPUT.search(line.strip())
                if hm:
                    inputs.append((hm.group(1).replace("app.", ""),
                                   hm.group(2).strip().strip('"'),
                                   (hm.group(3) or "").strip()))
            result.setdefault(srvc, {"callers": [], "inputs": []})
            result[srvc]["callers"].append(os.path.basename(f) + " / " + _clean(n.findtext("Text")))
            if inputs and not result[srvc]["inputs"]:
                result[srvc]["inputs"] = inputs
    return result


def extract_outputs(mci_path):
    """MCI_HostComm 의 switch(rcveSrvcId) → {srvcId: [(Output_NN, payload필드)]}."""
    r = ET.parse(mci_path).getroot()
    body = ""
    for n in r.findall(".//Node"):
        if _clean(n.findtext("Text")) == "결과값 출력":
            cp = n.find("CustomProperties")
            body = (cp.findtext("Script") or "") if cp is not None else ""
            break
    if not body:
        return {}
    # switch 블록만
    si = body.find("switch")
    seg = body[si:] if si >= 0 else body
    outputs, cur = {}, []
    for line in seg.split("\n"):
        s = line.strip()
        cm = CASE.findall(s)
        if cm:
            cur = cm  # 한 줄에 여러 case 가능
            for c in cur:
                outputs.setdefault(c, [])
            continue
        if s.startswith("break") or s.startswith("}"):
            cur = []
            continue
        om = OUTPUT_MAP.search(s)
        if om and cur:
            for c in cur:
                outputs[c].append((om.group(1), om.group(2)))
    return outputs


def build_mci_catalog(folder, mci_file="MCI_HostComm.xml"):
    inputs = extract_inputs(folder)
    mci_path = os.path.join(folder, mci_file)
    outputs = extract_outputs(mci_path) if os.path.isfile(mci_path) else {}
    catalog = {}
    for srvc in sorted(set(inputs) | set(outputs)):
        catalog[srvc] = {
            "kind": _kind(srvc),
            "inputs": inputs.get(srvc, {}).get("inputs", []),
            "callers": inputs.get(srvc, {}).get("callers", []),
            "outputs": outputs.get(srvc, []),
        }
    return catalog


if __name__ == "__main__":
    import sys
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    cat = build_mci_catalog(folder)
    print(f"MCI 전문 {len(cat)}종\n")
    for srvc, d in cat.items():
        print(f"■ {srvc}  [{d['kind']}]")
        if d["inputs"]:
            print("  INPUT:")
            for v, val, c in d["inputs"][:8]:
                print(f"    {v} = {val:<16} // {c}")
        if d["outputs"]:
            print("  OUTPUT:")
            for o, fld in d["outputs"][:8]:
                print(f"    {o} = payload.{fld}")
        if not d["inputs"]:
            print("  (input 정의 파일 없음 — 이 폴더에 호출자 미포함)")
        print()
