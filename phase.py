# -*- coding: utf-8 -*-
"""phase.py — 서브호출/노드 이름으로 업무 단계(phase) 분류 (B: 성격 기반)."""
import re

# (phase, 색, 정규식)  — 위에서부터 우선순위
PHASE_RULES = [
    ("완료",     "#38a169", r"정상\s*종료|종료\s*안내|정상\s*접수|처리\s*완료|접수\s*완료|감사합니다|이용해\s*주셔|^\d*\s*정상|hangup|stopsmartivr"),
    ("인증",     "#dd6b20", r"인증|본인확인|고객확인"),
    ("동의",     "#805ad5", r"동의"),
    ("처리",     "#6f42c1", r"mci|hostcomm|host통신"),
    ("조회·확인","#3182ce", r"조회|appdb|체크|여부|적합성|적정성|가능|초과|이하|검증|산정"),
    ("입력",     "#0d9488", r"입력|input"),
    ("안내",     "#718096", r"멘트|play|tts|안내|불가사유|상담유형"),
    ("외부연동", "#b7791f", r"callback|cti|모니터링|deeplink|deep link"),
    ("진입",     "#48bb78", r"^시작$|start|서비스코드|서비스통계|초기화"),
]
# 인증 수단
AUTH_METHOD = [
    ("비밀번호", r"비밀번호|pass(?:word|wd)?|비번"),
    ("보안카드", r"보안카드|scutcard|보안"),
    ("SMS인증", r"sms|문자"),
    ("간편인증", r"간편"),
    ("통신사인증", r"통신사"),
    ("카카오페이", r"카카오|kakao"),
    ("휴대폰인증", r"휴대폰|핸드폰|hp"),
    ("주민번호", r"주민"),
    ("전화번호확인", r"전화번호|고객확인"),
]
# 접어둘 유틸
UTIL = re.compile(r"_std_?\w*log|근무시간|_std_tts", re.I)


TYPE_PHASE = {
    "PromptNode": ("안내", "#718096"),
    "StartMOHNode": ("안내", "#718096"),
    "StopMOHNode": ("안내", "#718096"),
    "GetDigitPromptNode": ("입력", "#0d9488"),
    "IfNode": ("조회·확인", "#3182ce"),
    "SwitchNode": ("조회·확인", "#3182ce"),
    "ServiceCheckNode": ("조회·확인", "#3182ce"),
    "ScriptNode": ("처리", "#6f42c1"),
    "NetworkStreamNodeEx": ("처리", "#6f42c1"),
    "CallPageNode": ("처리", "#6f42c1"),
    "GotoPageNode": ("처리", "#6f42c1"),
    "ReturnPageNode": ("완료", "#38a169"),
    "StopNode": ("완료", "#38a169"),
    "HangupNode": ("완료", "#38a169"),
    "StopSmartIVRNode": ("완료", "#38a169"),
}


def classify(name):
    """이름 → (phase, 색, 인증수단or None, is_util)."""
    n = (name or "").lower()
    if UTIL.search(n):
        return ("로그·유틸", "#cbd5e0", None, True)
    for ph, color, pat in PHASE_RULES:
        if re.search(pat, n, re.I):
            method = None
            if ph == "인증":
                for mname, mpat in AUTH_METHOD:
                    if re.search(mpat, n, re.I):
                        method = mname
                        break
            return (ph, color, method, False)
    return ("기타", "#a0aec0", None, False)


def group_steps(steps):
    """연속 동일 phase 를 하나의 블록으로 묶는다 (실행 순서 유지)."""
    blocks = []
    for s in steps:
        name = s.get("sub_target") or s.get("label")
        ph, color, method, util = classify(name)
        if ph == "기타":                       # 이름으로 못 잡으면 노드 종류로 보조 판정
            tp = TYPE_PHASE.get(s.get("type") or "")
            if tp:
                ph, color = tp
        s = dict(s, phase=ph, phase_color=color, auth_method=method, util=util)
        if blocks and blocks[-1]["phase"] == ph:
            blocks[-1]["steps"].append(s)
        else:
            blocks.append({"phase": ph, "color": color, "steps": [s]})
    return blocks
