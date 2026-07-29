# 기존 프로젝트 통합 가이드

## run.py 는 필요 없습니다

`run.py` 는 **독립 테스트용 실행기**입니다.
기존 프로젝트는 그대로 **`app.py` 로 실행**하면 됩니다.
통합 시 `run.py` 는 복사하지 않아도 되고, 복사해도 사용되지 않습니다.

---

## 1. 파일 복사

기존 프로젝트 구조에 맞춰 아래처럼 넣습니다. (기존 파일과 이름이 겹치지 않습니다)

```
프로젝트루트/
├─ app.py                      ← 2줄만 수정 (아래 2번)
│
├─ scenario_parser.py          ← 신규 복사
├─ scenario_flow.py            ← 신규
├─ scenario_menu.py            ← 신규
├─ scenario_bizflow.py         ← 신규
├─ scenario_detail.py          ← 신규
├─ scenario_doc.py             ← 신규
├─ scenario_store.py           ← 신규
├─ scenario_summary.py         ← 신규
├─ scenario_version.py         ← 신규 (배포 전후 비교)
├─ scenario_diag.py            ← 신규 (진단, 선택)
├─ scenario_fetcher.py         ← 신규 (SSH 수집, 선택)
├─ mci_extractor.py            ← 신규
├─ phase.py                    ← 신규
├─ biz_lang.py                 ← 신규
│
├─ routes/
│   └─ topology.py             ← 기존 routes/ 안에 추가
├─ templates/
│   └─ topology.html           ← 기존 templates/ 안에 추가
├─ static/
│   └─ vendor/                 ← 기존 static/ 안에 vendor 폴더 추가
│       ├─ cytoscape.min.js
│       ├─ dagre.min.js
│       └─ cytoscape-dagre.js
└─ scenario_cache/
    └─ 운영/                    ← 여기에 실제 dxml 복사
```

> `snapshots/` 폴더는 배포 비교 스냅샷 저장 시 자동 생성됩니다.

---

## 2. app.py 수정 (2줄)

기존 blueprint 등록부(대략 55~63행)에 **같은 위치**로 끼워넣습니다.

```python
from routes.search import search_bp
from routes.servers import servers_bp
from routes.system import system_bp
from routes.analysis import analysis_bp
from routes.topology import topology_bp      # <-- 추가

app.register_blueprint(search_bp)
app.register_blueprint(servers_bp)
app.register_blueprint(system_bp)
app.register_blueprint(analysis_bp)
app.register_blueprint(topology_bp)          # <-- 추가
```

> `app = Flask(__name__, template_folder='templates', static_folder='static')` 는
> 이미 기존 app.py 에 있으므로 수정 불필요합니다.

---

## 3. 좌측 메뉴에 링크 추가 (선택)

기존 `index.html` 의 메뉴 영역에 항목 하나 추가:

```html
<div class="menu-item" onclick="location.href='/topology'">
    ARS FLOW 뷰어
</div>
```

---

## 4. 실행 확인

```
python app.py
```
→ `http://<서버>:<포트>/topology`

---

## 추가되는 라우트 (기존과 충돌 없음)

| 경로 | 용도 |
|---|---|
| `GET /topology` | 뷰어 화면 |
| `GET /api/topology/envs` | 환경 목록 |
| `GET /api/topology/menuroots` | 메뉴 진입점 |
| `GET /api/topology/menutree` | 메뉴 트리 |
| `GET /api/topology/treedoc` | 요약 구성도 (`&dev=1` 이면 상세) |
| `GET /api/topology/detail` | 블록 상세 |
| `GET /api/topology/graph` | 블록 그래프(지도) |
| `GET /api/topology/locate` | **블록ID → 업무 위치 (로그 연계)** |
| `GET/POST /api/topology/snapshot` | 스냅샷 목록 / 저장 |
| `GET /api/topology/diff` | **배포 전후 비교** |

---

## 의존성

추가 설치 없음 — **Flask 만** 사용합니다.
파싱은 표준 라이브러리(`xml.etree`, `re`, `hashlib`)만 쓰고,
Cytoscape 는 `static/vendor/` 에 번들되어 있어 인터넷이 필요 없습니다.

---

## 로그 모니터링 연계 (다음 단계)

기존 로그 뷰어에서 에러 블록ID를 얻었을 때, 같은 프로세스이므로 HTTP 없이 직접 호출 가능:

```python
import scenario_store as store

# 로그에 시나리오명이 함께 있으면 page 로 넘겨야 정확
res = store.locate_blocks('운영', ['00002813'], page='4111_신규대출일반.xml')
# -> menu_path, step_no, step_title, substep 반환
```
