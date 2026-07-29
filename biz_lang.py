# -*- coding: utf-8 -*-
"""
biz_lang.py — 현업 제공용 표기 변환.

- 기술 용어(서비스ID, APPDB, DBSVCLOG 등)를 업무 용어로 바꾸거나 숨김
- 단계(phase)·항목별 업무 설명문 생성
"""
import re

# 서비스ID 패턴 (SVfncst914vw, icsHpPSI007c, svpAtrsPolnPrcsPSI002c …)
SRVC_ID = re.compile(r'\b(?:SV|ics|svp|WV)[A-Za-z0-9_]{6,}\b')

# 기술 용어 → 업무 용어
TERM = [
    (re.compile(r'\(?APPDB\)?\s*DB_?SVCLOG', re.I), "처리 이력 기록"),
    (re.compile(r'\(?APPDB\)?\s*DB_?LOANRESIN', re.I), "대출 신청내역 저장"),
    (re.compile(r'CALL_?APPDB(_QuerySet)?', re.I), "고객정보 조회"),
    (re.compile(r'\(?APPDB\)?\s*콜?DB', re.I), "고객정보 조회"),
    (re.compile(r'\bDBSVCLOG\b', re.I), "처리 이력 기록"),
    (re.compile(r'_std_serviceLog|서비스통계\s*서비스코드', re.I), "서비스 이용 기록"),
    (re.compile(r'_std_callLog', re.I), "통화 기록"),
    (re.compile(r'\bInputDTMF\b', re.I), "번호 입력"),
    (re.compile(r'\bInputMaxMin\b', re.I), "금액 입력"),
    (re.compile(r'\bMCI_?HostComm\b', re.I), "호스트 연동"),
    (re.compile(r'\bTTS_?', re.I), "음성 안내 "),
    (re.compile(r'\bOutput_?Data\b', re.I), "조회 결과 정리"),
    (re.compile(r'\bParam_?Set\b', re.I), "안내 값 준비"),
    (re.compile(r'\bSet_?(\w+)', re.I), r"\1 설정"),
    (re.compile(r'\b근무시간체크\b'), "운영시간 확인"),
    (re.compile(r'\b상담유형코드\b'), "상담 유형 기록"),
]

# 단계별 업무 설명
PHASE_DESC = {
    "진입": "고객이 해당 메뉴를 선택해 서비스가 시작되는 단계입니다.",
    "입력": "업무 처리에 필요한 정보를 고객으로부터 입력받는 단계입니다.",
    "조회·확인": "입력한 정보와 고객 자격이 업무 조건에 맞는지 확인하는 단계입니다.",
    "인증": "본인 여부를 확인하는 단계입니다. 확인에 실패하면 재시도하거나 상담원으로 연결됩니다.",
    "동의": "처리 전 고객의 동의를 받는 단계입니다.",
    "안내": "확인된 내용을 고객에게 음성으로 안내하는 단계입니다.",
    "처리": "확인된 내용으로 실제 업무를 처리하는 단계입니다. 시스템 연동을 통해 조회·등록이 이루어집니다.",
    "완료": "처리 결과를 안내하고 통화를 마무리하는 단계입니다.",
    "외부연동": "다른 시스템·채널과 연계하는 단계입니다.",
    "기타": "업무 진행에 필요한 보조 처리 단계입니다.",
}


def clean_label(text, hide_ids=True):
    """현업 표기: 서비스ID 제거 + 기술 용어 치환."""
    s = (text or "").strip()
    if hide_ids:
        s = SRVC_ID.sub("", s)
    for pat, rep in TERM:
        s = pat.sub(rep, s)
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ·-")
    return s or (text or "").strip()


def _josa(word, a="을", b="를"):
    if not word:
        return a
    ch = word[-1]
    if "가" <= ch <= "힣":
        return b if (ord(ch) - 0xAC00) % 28 == 0 else a
    return a


def describe_item(label, phase, cond_plain=None, mci=None, auth=None):
    """항목의 업무 설명 한 줄."""
    nm = clean_label(label).rstrip(".·- ")
    if mci:
        kind = mci.get("kind") or "전문"
        act = "조회합니다" if "조회" in kind else "등록·처리합니다"
        return f"호스트 시스템에 {nm} 정보를 요청해 {act}."
    if cond_plain:
        return cond_plain
    if auth:
        return f"{auth} 방식으로 본인 여부를 확인합니다."
    if phase == "진입":
        return "서비스를 시작합니다."
    if phase == "입력":
        return f"고객에게 {nm}{_josa(nm)} 안내하고 입력받습니다."
    if phase == "안내":
        return f"{nm} 내용을 고객에게 안내합니다."
    if phase == "동의":
        return f"{nm}에 대한 고객 동의를 확인합니다."
    if phase == "완료":
        if re.search(r"감사|종료", nm):
            return "처리 결과를 안내한 뒤 통화를 종료합니다."
        return f"{nm} 처리 후 통화를 종료합니다."
    if phase == "조회·확인":
        return f"{nm} 조건을 확인합니다."
    if phase == "처리":
        return f"{nm}{_josa(nm)} 처리합니다."
    return nm


def phase_desc(phase):
    return PHASE_DESC.get(phase, "")
