// webvoice_render.js 맨 위 — CSS 자동 주입 (HTML 안 건드려도 됨)
(function(){
  if (document.getElementById('wv-render-css')) return;
  const css = `
.wv-phone{width:280px;height:560px;display:flex;flex-direction:column;border:8px solid #2b2b2b;border-radius:28px;overflow:hidden;background:#fff;box-shadow:0 8px 24px rgba(0,0,0,.15);flex:0 0 auto;}
.wv-top{height:18px;background:#2b2b2b;border-radius:0 0 12px 12px;margin:0 78px;flex:0 0 auto;}
.wv-head{display:flex;align-items:center;justify-content:center;gap:6px;padding:12px 10px 8px;position:relative;flex:0 0 auto;}
.wv-logo{width:19px;height:19px;border-radius:50%;background:radial-gradient(circle at 62% 38%,#ffce9e,#F47725 68%);}
.wv-co{font-weight:800;font-size:14px;color:#333;}
.wv-spk{position:absolute;right:12px;top:10px;font-size:12px;color:#999;}
.wv-note{background:#fff3ec;text-align:center;padding:10px;font-weight:800;font-size:12px;color:#333;flex:0 0 auto;}
.wv-body{flex:1 1 auto;overflow-y:auto;padding:14px 12px;}
.wv-foot{display:flex;background:linear-gradient(90deg,#8B7B6B,#a08b78);flex:0 0 auto;}
.wv-f{flex:1;text-align:center;padding:10px 4px;font-size:9px;color:#fff;line-height:1.5;}
.wv-f.act{color:#F47725;font-weight:800;background:rgba(255,255,255,.92);border-radius:10px;margin:4px;}
.wv-cap{background:#2b2b2b;color:#ffd8b8;font-size:10px;text-align:center;padding:6px;font-weight:700;flex:0 0 auto;}
.wvm-tit{font-weight:800;font-size:15px;margin-bottom:9px;color:#222;line-height:1.4;}
.wvm-txt{color:#555;font-size:11px;line-height:1.7;margin-bottom:9px;}
.wvm-str{font-weight:700;font-size:11px;color:#333;margin:11px 0 4px;}
.wvm-inp{border:1.5px solid #d5d8dd;border-radius:8px;padding:10px;color:#aaa;font-size:11px;margin-bottom:8px;}
.wvm-info{background:#f3f4f6;border-radius:8px;padding:8px;font-size:10px;color:#666;margin-bottom:9px;}
.wvm-icon{font-size:30px;text-align:center;margin:12px 0;}
.wvm-quick{display:flex;gap:10px;justify-content:center;border-bottom:1px solid #eee;padding-bottom:10px;margin-bottom:8px;flex-wrap:wrap;}
.wvm-q{font-size:10px;font-weight:700;display:flex;flex-direction:column;gap:4px;align-items:center;}
.wvm-menu{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.wvm-m{border:1px solid #eaecef;border-radius:10px;padding:12px 6px;text-align:center;font-weight:700;font-size:11px;line-height:1.35;display:flex;flex-direction:column;gap:6px;align-items:center;min-height:64px;justify-content:center;color:#333;}
.wvm-mi-img{width:26px;height:26px;object-fit:contain;}
.wvm-q .wvm-mi-img{width:20px;height:20px;}
.wvm-list-btn{border:1px solid #eaecef;border-radius:8px;padding:11px;margin-bottom:6px;font-weight:700;font-size:12px;}
.wvm-btn{padding:11px;border-radius:8px;font-weight:700;font-size:12px;text-align:center;margin-top:8px;}
.wvm-btn.o{background:#F47725;color:#fff;}
.wvm-btn.ghost{background:#f0f1f3;color:#555;}
.wvm-btn2{display:flex;gap:8px;margin-top:11px;}
.wvm-btn2 .wvm-btn{flex:1;margin-top:0;}
`;
  const st = document.createElement('style');
  st.id = 'wv-render-css';
  st.textContent = css;
  document.head.appendChild(st);
})();

// 아이콘값(연동정의서 S06) → 아이콘 파일 경로 (별도 폴더에 이미지 배치)
const WV_ICON_BASE = '/static/wv_icons/';  // ← 아이콘 png 넣을 경로
const WV_ICON = {"01":"loan","02":"refund","03":"payment","04":"realestate",
  "05":"phishing","06":"variable","07":"docs","08":"card","09":"common",
  "10":"common","11":"accident","12":"claim","13":"video","14":"agent",
  "21":"payment","22":"accident"};
function _wvIcon(val){
  const f = WV_ICON[val];
  return f ? `<img class="wvm-mi-img" src="${WV_ICON_BASE}${f}.png" onerror="this.style.display='none'">` : '📄';
}

function renderWebVoiceScreen(code, name, payload) {
  const kv = _wvParse(payload || ('S$' + code));
  const g = k => kv.filter(p => p && p[0] === k);
  const mute = g('MUTE');
  const muteOn = mute.length && mute[0][2] === 'ON';
  let body = '';

  g('NOT').forEach(p => { if (p[2] === 'ON') body += `<div class="wvm-info">📢 ${_wvEsc(_wvClean(p[4]))}</div>`; });
  if (g('IMG').length) body += `<div class="wvm-icon">🎧</div>`;
  g('TIT').forEach(p => { if (p[3]) body += `<div class="wvm-tit">${_wvClean(p[3])}</div>`; });
  g('TXT').forEach(p => { if (p[3]) body += `<div class="wvm-txt">${_wvClean(p[3])}</div>`; });
  g('STR').forEach(p => { if (p[2]) body += `<div class="wvm-str">${_wvClean(p[2])}</div>`; });
  ['INP','INP2','INPH'].forEach(k => g(k).forEach(p => {
    let ph = ''; [p[4],p[3],p[2]].forEach(c => { if (c && !/^\d+$/.test(c) && !ph) ph = c; });
    body += `<div class="wvm-inp">${_wvEsc(_wvClean(ph) || '입력')}</div>`;
  }));
  // 퀵메뉴
  const q = g('BTNQ2');
  if (q.length) body += '<div class="wvm-quick">' + q.map(p =>
    `<div class="wvm-q">${_wvIcon(p[2])}<span>${_wvEsc(_wvClean(p[3]))}</span></div>`).join('') + '</div>';
  // 메인 메뉴 그리드 (아이콘값 p[2])
  const m = g('BTNM');
  if (m.length) body += '<div class="wvm-menu">' + m.map(p =>
    `<div class="wvm-m">${_wvIcon(p[2])}<span>${_wvClean(p[3])}</span></div>`).join('') + '</div>';
  // ★단독 BTN (오류5: 누락됐던 부분) — BTN$idx$라벨$...
  const bsingle = g('BTN');
  if (bsingle.length) body += '<div class="wvm-menu">' + bsingle.map(p =>
    `<div class="wvm-m"><span>${_wvClean(p[2])}</span></div>`).join('') + '</div>';
  // 아코디언/리스트 버튼
  g('BTNA').forEach(p => { if (p[2]) body += `<div class="wvm-list-btn">${_wvClean(p[2])}</div>`; });
  // 입력 확인/재전송
  g('INPTXT').forEach(p => { if (p[2]) body += `<div class="wvm-btn o">${_wvEsc(_wvClean(p[2]))}</div>`; });
  g('BTNZ').forEach(p => { if (p[2]) body += `<div class="wvm-btn ghost">${_wvEsc(_wvClean(p[2]))}</div>`; });
  // 2지선다/안내버튼
  ['BTN2','BTNE2','BTN0','BTN1','BTNE1'].forEach(k => {
    const b = g(k);
    if (b.length) body += '<div class="wvm-btn2">' + b.map((p,i) =>
      `<div class="wvm-btn ${i===b.length-1?'o':'ghost'}">${_wvEsc(_wvClean(p[2]))}</div>`).join('') + '</div>';
  });

  const spk = muteOn ? '🔊' : '🔈';
  return `<div class="wv-phone">
    <div class="wv-top"></div>
    <div class="wv-head"><span class="wv-logo"></span><span class="wv-co">한화생명</span><span class="wv-spk">${spk}</span></div>
    <div class="wv-note">삶의 가치를 더하는 한화생명입니다.</div>
    <div class="wv-body">${body || `<div class='wvm-tit'>${_wvEsc(name||code)}</div>`}</div>
    <div class="wv-foot"><div class="wv-f">🔊<br>음성 ARS</div><div class="wv-f act"><span>🎧<br>상담사</span></div><div class="wv-f">✕<br>통화종료</div></div>
    <div class="wv-cap">${_wvEsc(code)}${name ? ' · ' + _wvEsc(name) : ''}</div>
  </div>`;
}