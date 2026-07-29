/* =====================================================================
 * webvoice_render.js — 보이는ARS 화면 렌더러 + 팝업 모달
 * ---------------------------------------------------------------------
 *  · 연동정의서 프로토콜(S$HLIxxx;...) → 폰 목업 HTML
 *  · openScreenModal(code, name, payload) : 단일 화면 팝업
 *  · openScreenFlowModal(screens, title)  : 화면 흐름(여러 장) 팝업
 *  · WV_SCREEN_MAP : 화면코드→이름 (screen_map.json 내장)
 *
 *  ※ 이 파일이 렌더러의 단일 소스입니다. index.html 에 같은 함수를
 *    중복 정의하지 마세요 (예전에 인라인 사본이 있어 아이콘 연동이
 *    이 파일에만 반영되고 화면에는 안 나오는 문제가 있었습니다).
 * ===================================================================== */

// ── 이미지 자산 ───────────────────────────────────────────
const WV_ICON_BASE = '/static/wv_icons/';

/* icon_main.png — 업무 메뉴 아이콘 스프라이트 (14열 × 2행 / 0행:진회색, 1행:주황)
 * 아이콘값(연동정의서 S06) → 스프라이트 열 인덱스(0-base).
 * 값-열 대응은 스프라이트 배열 순서를 그대로 따랐습니다.
 * 실제 정의서와 어긋나는 항목이 있으면 이 표의 숫자만 고치면 됩니다. */
const WV_MAIN_COLS = 14;
const WV_ICON_COL = {
  '01': 0,   // 대출
  '02': 1,   // 환급금
  '03': 2,   // 납입/이체
  '04': 3,   // 부동산
  '05': 4,   // 보이스피싱
  '06': 5,   // 변액
  '07': 6,   // 서류
  '08': 7,   // 보안/증명
  '09': 8,   // 공통(전체메뉴)
  '10': 8,   // 공통(전체메뉴)
  '11': 10,  // 사고/재해
  '12': 11,  // 보험금 청구
  '13': 12,  // 영상통화
  '14': 13,  // 상담사
  '21': 2,   // 납입/이체 (별칭)
  '22': 10,  // 사고/재해 (별칭)
};

/* 간편인증 사업자 — 버튼 라벨로 판별 (HLIC07/07A/07B/07C).
 * 일반 업무 메뉴를 가로채지 않도록 사업자명에만 매칭한다.
 * ('상담사', '더보기' 같은 일반 라벨은 icon_main 스프라이트가 담당) */
const WV_PROVIDER = [
  [/카카오|카톡/, 'ico_talk'],
  [/토스|\btoss\b/i, 'ico_toss'],
  [/네이버|\bnaver\b/i, 'ico_naver'],
  [/페이코|\bpayco\b/i, 'ico_payco'],
  [/\bpass\b/i, 'ico_pass'],
];

(function () {
  if (document.getElementById('wv-render-css')) return;
  const css = `
/* ── 화면 팝업 모달 ── */
.wv-modal{display:none;position:fixed;inset:0;z-index:9999;}
.wv-modal.open{display:block;}
.wv-modal-bg{position:absolute;inset:0;background:rgba(0,0,0,.5);}
.wv-modal-box{position:relative;max-width:92vw;max-height:90vh;margin:4vh auto;background:#fff;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.3);display:flex;flex-direction:column;width:max-content;}
.wv-modal-head{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:14px 18px;border-bottom:1px solid #eee;font-weight:800;color:#E8590C;font-size:15px;}
.wv-modal-x{cursor:pointer;color:#999;font-size:18px;padding:2px 6px;border-radius:6px;}
.wv-modal-x:hover{background:#f4f4f4;color:#333;}
.wv-modal-body{padding:24px 18px 18px;overflow:auto;}
.wv-single{display:flex;justify-content:center;}
/* 화면들이 같은 높이로 서도록 상단 정렬 (stretch 금지 — 폰 높이 고정) */
.wv-flow-scroll{display:flex;gap:8px;align-items:flex-start;overflow-x:auto;padding:6px 0 8px;}
.wv-flow-item{position:relative;flex:0 0 auto;}
.wv-flow-arrow{flex:0 0 auto;color:#F47725;font-size:26px;font-weight:700;align-self:center;}
.wv-flow-step{position:absolute;top:-8px;left:50%;transform:translateX(-50%);z-index:2;background:#7048e8;color:#fff;font-size:11px;font-weight:700;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(112,72,232,.4);}
.wv-flow-ts{text-align:center;font-size:10px;color:#adb5bd;font-family:'Courier New',monospace;margin-top:4px;}

/* ── 폰 목업 (크기 고정: 모든 화면이 동일 규격) ── */
.wv-phone{width:280px;height:580px;flex:0 0 auto;display:flex;flex-direction:column;
  background:#fff;border:8px solid #2b2b2b;border-radius:28px;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,.15);}
.wv-top{height:18px;background:#2b2b2b;border-radius:0 0 12px 12px;margin:0 78px;flex:0 0 auto;}
.wv-head{display:flex;align-items:center;justify-content:center;padding:11px 10px 8px;position:relative;flex:0 0 auto;}
.wv-logo{height:17px;width:auto;display:block;}
.wv-spk{position:absolute;right:11px;top:9px;display:flex;flex-direction:column;align-items:center;gap:1px;font-size:7px;color:#999;font-weight:700;}
.wv-note{background:#fff3ec;text-align:center;padding:9px;font-weight:800;font-size:12px;color:#333;flex:0 0 auto;}
/* 내용이 길면 폰 안에서 스크롤 — 폰 자체가 늘어나지 않게 */
.wv-body{flex:1 1 auto;min-height:0;overflow-y:auto;padding:14px 12px;}
.wv-body::-webkit-scrollbar{width:4px;}
.wv-body::-webkit-scrollbar-thumb{background:#dcdfe4;border-radius:2px;}
.wv-foot{display:flex;background:linear-gradient(90deg,#8B7B6B,#a08b78);flex:0 0 auto;}
.wv-f{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;
  text-align:center;padding:8px 4px;font-size:9px;color:#fff;line-height:1.3;position:relative;}
.wv-f.act{color:#F47725;font-weight:800;}
.wv-f.act::before{content:'';position:absolute;inset:4px 6px;background:rgba(255,255,255,.92);border-radius:16px;}
.wv-f.act > *{position:relative;}
.wv-cap{background:#2b2b2b;color:#ffd8b8;font-size:10px;text-align:center;padding:6px;font-weight:700;flex:0 0 auto;}

/* ── 화면 본문 요소 ── */
.wvm-tit{font-weight:800;font-size:15px;margin-bottom:9px;color:#222;line-height:1.4;}
.wvm-txt{color:#555;font-size:11px;line-height:1.7;margin-bottom:9px;}
.wvm-str{font-weight:700;font-size:11px;color:#333;margin:11px 0 4px;}
.wvm-inp{border:1.5px solid #d5d8dd;border-radius:8px;padding:10px;color:#aaa;font-size:11px;margin-bottom:8px;}
.wvm-info{display:flex;align-items:flex-start;gap:6px;background:#f3f4f6;border-radius:8px;padding:8px;font-size:10px;color:#666;margin-bottom:9px;line-height:1.5;}
.wvm-msg{text-align:center;margin:10px 0 12px;}
.wvm-msg img{width:56px;height:56px;object-fit:contain;}
.wvm-quick{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;border-bottom:1px solid #eee;padding-bottom:11px;margin-bottom:9px;}
.wvm-q{font-size:10px;font-weight:700;color:#444;text-align:center;display:flex;flex-direction:column;gap:4px;align-items:center;}
.wvm-menu{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.wvm-m{border:1px solid #eaecef;border-radius:10px;padding:12px 6px;text-align:center;font-weight:700;font-size:11px;
  line-height:1.35;color:#333;display:flex;flex-direction:column;gap:6px;align-items:center;justify-content:center;min-height:64px;}
.wvm-list-btn{border:1px solid #eaecef;border-radius:8px;padding:11px;margin-bottom:6px;font-weight:700;font-size:12px;color:#333;}
.wvm-btn{padding:11px;border-radius:8px;font-weight:700;font-size:12px;text-align:center;margin-top:8px;}
.wvm-btn.o{background:#F47725;color:#fff;}
.wvm-btn.ghost{background:#f0f1f3;color:#555;}
.wvm-btn2{display:flex;gap:8px;margin-top:11px;}
.wvm-btn2 .wvm-btn{flex:1;margin-top:0;}

/* ── 아이콘 (스프라이트 / 개별 png) ── */
.wv-ico{display:inline-block;background-repeat:no-repeat;flex:0 0 auto;}
.wv-ico-main{width:28px;height:28px;background-image:url(${WV_ICON_BASE}icon_main.png);background-size:${WV_MAIN_COLS * 100}% 200%;}
.wv-ico-quick{width:24px;height:24px;background-image:url(${WV_ICON_BASE}icon_quick.png);background-size:200% 200%;}
.wv-ico-start{width:26px;height:26px;background-image:url(${WV_ICON_BASE}icon_start.png);background-size:200% 200%;}
.wv-ico-support{width:18px;height:18px;background-image:url(${WV_ICON_BASE}icon_support.png);background-size:100% 200%;}
.wv-ico-notice{width:14px;height:14px;background-image:url(${WV_ICON_BASE}icon_notice.png);background-size:contain;}
.wv-ico-img{width:26px;height:26px;object-fit:contain;}
.wvm-q .wv-ico-main{width:22px;height:22px;}
`;
  const st = document.createElement('style');
  st.id = 'wv-render-css';
  st.textContent = css;
  document.head.appendChild(st);
})();

// ── 프로토콜 파서 ─────────────────────────────────────────
function _wvParse(payload) {
  return (payload || '').split(';').filter(s => s).map(s => s.split('$'));
}
function _wvEsc(s) {
  return (s == null ? '' : String(s)).replace(/[&<>"]/g,
    m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]));
}
// 프로토콜 구분자(|)를 공백으로 편 뒤 이스케이프 — 항상 이 함수로 출력
function _wvText(s) { return _wvEsc((s || '').replace(/\|/g, ' ')); }

// 스프라이트 셀 → background-position (col: 0-base, row: 0=진회색 1=주황)
function _wvCell(col, cols, row) {
  const x = cols > 1 ? (col / (cols - 1)) * 100 : 0;
  return `background-position:${x.toFixed(4)}% ${row ? 100 : 0}%`;
}

/** 메뉴 아이콘 — 사업자 로고(라벨 우선) → 업무 아이콘 스프라이트 → 대체 문자 */
function _wvIcon(val, label, row) {
  const txt = (label || '').replace(/\|/g, ' ');
  for (const [re, file] of WV_PROVIDER) {
    if (re.test(txt)) {
      return `<img class="wv-ico-img" src="${WV_ICON_BASE}${file}.png" alt=""
                onerror="this.style.display='none'">`;
    }
  }
  const col = WV_ICON_COL[String(val || '').padStart(2, '0')];
  if (col == null) return '';
  return `<span class="wv-ico wv-ico-main" style="${_wvCell(col, WV_MAIN_COLS, row ? 1 : 0)}"></span>`;
}

// 안내 이미지 — IMG 파라미터에서 2자리 이미지 id 를 찾아 msg_NN.png 로 (IMG$1$91 / IMG$91 모두 허용)
function _wvMsgImg(parts) {
  const id = (parts || []).slice(1).map(v => String(v || '').match(/^\d{2}$/))
                          .find(Boolean);
  if (!id) return `<span class="wv-ico wv-ico-support" style="${_wvCell(0, 1, 1)};width:40px;height:40px"></span>`;
  return `<img src="${WV_ICON_BASE}msg_${id[0]}.png" alt=""
            onerror="this.replaceWith(document.createTextNode(''))">`;
}

// ── 프로토콜 → 폰 목업 HTML ───────────────────────────────
function renderWebVoiceScreen(code, name, payload) {
  const kv = _wvParse(payload || ('S$' + code));
  const g = k => kv.filter(p => p && p[0] === k);
  const mute = g('MUTE');
  const muteOn = mute.length && mute[0][2] === 'ON';
  let body = '';

  g('NOT').forEach(p => {
    if (p[2] === 'ON') {
      body += `<div class="wvm-info"><span class="wv-ico wv-ico-notice"></span>` +
              `<span>${_wvText(p[4])}</span></div>`;
    }
  });
  g('IMG').forEach(p => { body += `<div class="wvm-msg">${_wvMsgImg(p)}</div>`; });
  g('TIT').forEach(p => { if (p[3]) body += `<div class="wvm-tit">${_wvText(p[3])}</div>`; });
  g('TXT').forEach(p => { if (p[3]) body += `<div class="wvm-txt">${_wvText(p[3])}</div>`; });
  g('STR').forEach(p => { if (p[2]) body += `<div class="wvm-str">${_wvText(p[2])}</div>`; });
  ['INP', 'INP2', 'INPH'].forEach(k => g(k).forEach(p => {
    let ph = '';
    [p[4], p[3], p[2]].forEach(c => { if (c && !/^\d+$/.test(c) && !ph) ph = c; });
    body += `<div class="wvm-inp">${_wvText(ph) || '입력'}</div>`;
  }));
  // 퀵메뉴
  const q = g('BTNQ2');
  if (q.length) {
    body += '<div class="wvm-quick">' + q.map(p =>
      `<div class="wvm-q">${_wvIcon(p[2], p[3], 1)}<span>${_wvText(p[3])}</span></div>`).join('') + '</div>';
  }
  // 메인 메뉴 그리드 (아이콘값 p[2], 라벨 p[3])
  const m = g('BTNM');
  if (m.length) {
    body += '<div class="wvm-menu">' + m.map(p =>
      `<div class="wvm-m">${_wvIcon(p[2], p[3], 0)}<span>${_wvText(p[3])}</span></div>`).join('') + '</div>';
  }
  // 단독 BTN — BTN$idx$라벨$...
  const bsingle = g('BTN');
  if (bsingle.length) {
    body += '<div class="wvm-menu">' + bsingle.map(p =>
      `<div class="wvm-m">${_wvIcon(null, p[2], 0)}<span>${_wvText(p[2])}</span></div>`).join('') + '</div>';
  }
  // 아코디언/리스트 버튼
  g('BTNA').forEach(p => { if (p[2]) body += `<div class="wvm-list-btn">${_wvText(p[2])}</div>`; });
  // 입력 확인/재전송
  g('INPTXT').forEach(p => { if (p[2]) body += `<div class="wvm-btn o">${_wvText(p[2])}</div>`; });
  g('BTNZ').forEach(p => { if (p[2]) body += `<div class="wvm-btn ghost">${_wvText(p[2])}</div>`; });
  // 2지선다/안내버튼
  ['BTN2', 'BTNE2', 'BTN0', 'BTN1', 'BTNE1'].forEach(k => {
    const b = g(k);
    if (b.length) {
      body += '<div class="wvm-btn2">' + b.map((p, i) =>
        `<div class="wvm-btn ${i === b.length - 1 ? 'o' : 'ghost'}">${_wvText(p[2])}</div>`).join('') + '</div>';
    }
  });

  // 스피커 표시 — icon_start.png 2열(스피커), 켜짐이면 주황
  const spk = `<span class="wv-ico wv-ico-start" style="${_wvCell(1, 2, muteOn ? 1 : 0)};width:16px;height:16px"></span>` +
              `<span>${muteOn ? '음성ON' : '음성OFF'}</span>`;

  return `<div class="wv-phone">
    <div class="wv-top"></div>
    <div class="wv-head">
      <img class="wv-logo" src="${WV_ICON_BASE}logo.png" alt="한화생명"
           onerror="this.replaceWith(document.createTextNode('한화생명'))">
      <span class="wv-spk">${spk}</span>
    </div>
    <div class="wv-note">삶의 가치를 더하는 한화생명입니다.</div>
    <div class="wv-body">${body || `<div class="wvm-tit">${_wvEsc(name || code)}</div>`}</div>
    <div class="wv-foot">
      <div class="wv-f"><span class="wv-ico wv-ico-start" style="${_wvCell(1, 2, 0)};width:16px;height:16px;filter:brightness(0) invert(1)"></span>음성 ARS</div>
      <div class="wv-f act"><span class="wv-ico wv-ico-support" style="${_wvCell(0, 1, 1)}"></span><span>상담사</span></div>
      <div class="wv-f"><span style="font-size:14px;line-height:1">✕</span>통화종료</div>
    </div>
    <div class="wv-cap">${_wvEsc(code)}${name ? ' · ' + _wvEsc(name) : ''}</div>
  </div>`;
}

// ── 모달 인프라 ───────────────────────────────────────────
function _ensureScreenModal() {
  let m = document.getElementById('wvScreenModal');
  if (m) return m;
  m = document.createElement('div');
  m.id = 'wvScreenModal';
  m.className = 'wv-modal';
  m.innerHTML = `<div class="wv-modal-bg" onclick="closeScreenModal()"></div>
    <div class="wv-modal-box">
      <div class="wv-modal-head"><span id="wvModalTitle">화면 미리보기</span>
        <span class="wv-modal-x" onclick="closeScreenModal()">✕</span></div>
      <div class="wv-modal-body" id="wvModalBody"></div>
    </div>`;
  document.body.appendChild(m);
  return m;
}
function closeScreenModal() {
  const m = document.getElementById('wvScreenModal');
  if (m) m.classList.remove('open');
}

// 단일 화면 팝업 (payload 있으면 실데이터, 없으면 코드만)
function openScreenModal(code, name, payload) {
  const m = _ensureScreenModal();
  document.getElementById('wvModalTitle').textContent =
    '📱 ' + code + (name ? ' · ' + name : '');
  document.getElementById('wvModalBody').innerHTML =
    `<div class="wv-single">${renderWebVoiceScreen(code, name || (WV_SCREEN_MAP[code] || {}).name, payload)}</div>`;
  m.classList.add('open');
}

// 화면 흐름 팝업 (여러 장 나란히) — screens: [{code,name,payload,ts}]
function openScreenFlowModal(screens, title) {
  const m = _ensureScreenModal();
  const list = screens || [];
  document.getElementById('wvModalTitle').textContent =
    (title || '📱 화면 진행 흐름') + (list.length ? ` (${list.length}장)` : '');
  const cards = list.map((s, i) => {
    const nm = s.name || (WV_SCREEN_MAP[s.code] || {}).name || '';
    const arrow = i > 0 ? '<div class="wv-flow-arrow">›</div>' : '';
    const ts = s.ts ? `<div class="wv-flow-ts">${_wvEsc(s.ts)}</div>` : '';
    return arrow + `<div class="wv-flow-item">
      <div class="wv-flow-step">${i + 1}</div>
      ${renderWebVoiceScreen(s.code, nm, s.payload)}
      ${ts}
    </div>`;
  }).join('');
  document.getElementById('wvModalBody').innerHTML =
    `<div class="wv-flow-scroll">${cards || '<div style="padding:40px;color:#aaa;">표시할 화면이 없습니다.</div>'}</div>`;
  m.classList.add('open');
}

// ESC 닫기
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeScreenModal(); });

// ── 화면코드 → 이름 (screen_map.json 내장) ────────────────
const WV_SCREEN_MAP = {"HLIA00": {"name": "시작하기 화면", "category": "메인/시작"}, "HLIA01": {"name": "메인 화면", "category": "메인/시작"}, "HLIA10": {"name": "2단 메뉴 화면", "category": "메인/시작"}, "HLIB00": {"name": "메뉴 리스트", "category": "메뉴리스트/고객확인"}, "HLIB01": {"name": "상담사연결 전용 메뉴 리스트", "category": "메뉴리스트/고객확인"}, "HLIB02": {"name": "아코디언 메뉴 리스트 화면", "category": "메뉴리스트/고객확인"}, "HLIB10": {"name": "고객 확인", "category": "메뉴리스트/고객확인"}, "HLIC00": {"name": "1개 항목 입력 (일반)", "category": "입력"}, "HLIC01": {"name": "1개 항목 입력 (SMS인증, 데이터변경불가)", "category": "입력"}, "HLIC02V": {"name": "1개 항목 입력 (보안)", "category": "입력"}, "HLIC03": {"name": "인증번호 입력", "category": "입력"}, "HLIC04V": {"name": "주민등록번호 입력", "category": "입력"}, "HLIC05V": {"name": "보안카드 입력", "category": "입력"}, "HLIC06": {"name": "2개 항목 입력 (일반,일반)", "category": "입력"}, "HLIC07": {"name": "카카오페이 인증", "category": "입력"}, "HLIC07A": {"name": "간편인증", "category": "입력"}, "HLIC07B": {"name": "간편인증 3자통화", "category": "입력"}, "HLIC07C": {"name": "간편인증 3자통화 2", "category": "입력"}, "HLIC08": {"name": "1개 항목 선택", "category": "입력"}, "HLIC09": {"name": "2개 항목 선택", "category": "입력"}, "HLIC10": {"name": "금융기관 선택 및 계좌번호 입력", "category": "입력"}, "HLIC11": {"name": "금융기관/이체일 선택 및 계좌번호 입력", "category": "입력"}, "HLIC12": {"name": "상환방법/이체일 선택", "category": "입력"}, "HLIC13": {"name": "신분증 인증", "category": "입력"}, "HLIC20": {"name": "이메일 입력", "category": "입력"}, "HLID00": {"name": "단일 테이블, 유의사항", "category": "테이블/조회"}, "HLID01": {"name": "다중 테이블, 유의사항", "category": "테이블/조회"}, "HLID10": {"name": "조회 내역 안내 A (혼합형, 메뉴리스트)", "category": "테이블/조회"}, "HLID20": {"name": "조회 내역 안내 B (혼합형, 버튼)", "category": "테이블/조회"}, "HLID21": {"name": "조회 내역 안내 C (혼합형 신규)", "category": "테이블/조회"}, "HLID23": {"name": "신청내역 점검", "category": "테이블/조회"}, "HLID30": {"name": "장문 내용 안내", "category": "테이블/조회"}, "HLID31": {"name": "약정서 안내", "category": "테이블/조회"}, "HLID32": {"name": "동의 여부 확인 A", "category": "테이블/조회"}, "HLID33": {"name": "동의 여부 확인 B", "category": "테이블/조회"}, "HLID34": {"name": "대출상품설명서", "category": "테이블/조회"}, "HLID40": {"name": "동의항목 녹취 (버튼N)", "category": "테이블/조회"}, "HLID41": {"name": "동의항목 녹취 (버튼Y)", "category": "테이블/조회"}, "HLID50": {"name": "입력형 테이블 A (가로형)", "category": "테이블/조회"}, "HLID51": {"name": "입력형 테이블 B (세로형 3열)", "category": "테이블/조회"}, "HLID52": {"name": "입력형 테이블 C (신청금액 토탈)", "category": "테이블/조회"}, "HLID60": {"name": "조회 결과 테이블 (나의 계약정보)", "category": "테이블/조회"}, "HLID61": {"name": "PDF 뷰 화면", "category": "테이블/조회"}, "HLIE00": {"name": "안내 화면 A (기본형)", "category": "안내"}, "HLIE01": {"name": "안내 화면 B (상담사 연결 대기정보)", "category": "안내"}, "HLIE02": {"name": "안내 화면 C (안내 및 버튼)", "category": "안내"}, "HLIE03": {"name": "안내 화면 E (안내 및 테이블)", "category": "안내"}, "HLIE04": {"name": "안내 화면 F (안내 및 유의사항)", "category": "안내"}, "HLIE05": {"name": "안내 화면 G (안내, 테이블, 버튼)", "category": "안내"}, "HLIE06": {"name": "안내 화면 H (URL 연결)", "category": "안내"}, "HLIF00": {"name": "신계약모니터링 초기화면", "category": "신계약모니터링"}, "HLIF10": {"name": "개인(신용)정보 수집 및 모니터링 동의", "category": "신계약모니터링"}, "HLIF20V": {"name": "휴대폰 본인인증 - 정보 입력", "category": "신계약모니터링"}, "HLIF21": {"name": "휴대폰 본인인증 - 인증번호 입력", "category": "신계약모니터링"}, "HLIF30": {"name": "대상계약 확인 - 대상계약 부재", "category": "신계약모니터링"}, "HLIF31": {"name": "대상계약 확인 - 대상계약 존재", "category": "신계약모니터링"}, "HLIF40": {"name": "모니터링 진행", "category": "신계약모니터링"}, "HLIF41": {"name": "모니터링 진행 (정답 표기)", "category": "신계약모니터링"}, "HLIF42": {"name": "적합성 원칙 확인", "category": "신계약모니터링"}, "HLIF50": {"name": "모니터링 완료", "category": "신계약모니터링"}};
