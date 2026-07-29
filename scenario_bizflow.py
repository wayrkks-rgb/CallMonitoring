# -*- coding: utf-8 -*-
"""scenario_bizflow.py — 업무 관통 flow 엔진 (재귀 인라인 + MCI 카드)."""
import os, re
import xml.etree.ElementTree as ET
from scenario_parser import parse_page
from scenario_flow import build_core_flow
import mci_extractor

MCI_STEM="mci_hostcomm"
UTIL_HINT=re.compile(r"(^_std|appdb|근무시간|inputdtmf|inputchktime|멘트플레이|_tts|로그)",re.I)
SRVC=re.compile(r'rcveSrvcId\s*=\s*["\'](\w+)["\']')
HINPUT=re.compile(r'(app\.H_Input_\d+)\s*=\s*([^;/\n]+?)\s*;?\s*(?://\s*(.+))?$')

def _stem(n): return os.path.splitext(os.path.basename(n))[0].lower()

class BizFlow:
    def __init__(self,folder):
        self.folder=folder
        self.stemmap={}
        for f in (os.listdir(folder) if os.path.isdir(folder) else []):
            if f.lower().endswith(('.xml','.dxml')): self.stemmap.setdefault(_stem(f),f)
        self.mci=mci_extractor.build_mci_catalog(folder)
        self._sc={}
    def _resolve(self,name):
        r=self.stemmap.get(_stem(name)); return os.path.join(self.folder,r) if r else None
    def _srvc_inputs(self,path):
        if path in self._sc: return self._sc[path]
        out={}
        try: r=ET.parse(path).getroot()
        except Exception: self._sc[path]=out; return out
        for n in r.findall('.//Node'):
            cp=n.find('CustomProperties')
            if cp is None: continue
            body=(cp.findtext('PreScript') or '')+'\n'+(cp.findtext('Script') or '')
            m=SRVC.search(body)
            if not m: continue
            inputs=[]
            for line in body.split('\n'):
                hm=HINPUT.search(line.strip())
                if hm: inputs.append((hm.group(1).replace('app.',''),hm.group(2).strip().strip('"'),(hm.group(3) or '').strip()))
            out[cp.findtext('Sequence') or '']=(m.group(1),inputs)
        self._sc[path]=out; return out
    def _classify(self,t):
        s=_stem(t)
        if s==MCI_STEM: return 'mci'
        if UTIL_HINT.search(s): return 'util'
        return 'sub'
    def _card(self,srvc,inputs):
        cat=self.mci.get(srvc,{})
        return {'srvc':srvc,'kind':cat.get('kind','전문'),'inputs':inputs or cat.get('inputs',[]),'outputs':cat.get('outputs',[])}
    def build(self,entry,mode='summary',depth=0,max_depth=3,seen=None):
        seen=seen if seen is not None else set()
        path=self._resolve(entry)
        node={'page':os.path.basename(path) if path else entry,'steps':[]}
        if not path: node['missing']=True; return node
        if _stem(entry) in seen or depth>max_depth: node['revisit']=True; return node
        seen=seen|{_stem(entry)}
        cf=build_core_flow(path); sm=self._srvc_inputs(path)
        for s in cf['steps']:
            step={'label':s['label'],'kind':s['kind'],'type':s.get('type',''),'seq':s['seq'],'milestone':s['milestone'],
                  'cond':s.get('cond',''),'branch':s.get('branch'),'next':s.get('next',[]),'exc':s.get('exc',[])}
            tp=s.get('sub')
            if tp:
                cls=self._classify(tp)
                step['sub_target']=os.path.basename(self._resolve(tp) or tp); step['sub_type']=cls
                if cls=='mci':
                    srvc,inputs=sm.get(s['seq'],(None,[]))
                    if not srvc and sm: srvc,inputs=next(iter(sm.values()))
                    if srvc: step['mci']=self._card(srvc,inputs)
                elif cls=='sub' and mode=='detail':
                    step['expanded']=self.build(tp,mode,depth+1,max_depth,seen)
            node['steps'].append(step)
        return node

def build_bizflow(folder,entry,mode='summary'): return BizFlow(folder).build(entry,mode=mode)

