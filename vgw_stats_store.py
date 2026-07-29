#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VGW 콜 통계 저장소 (SQLite, WAL)

채널 상태 전이로 감지한 '완료된 콜 이벤트'를 적재하고 집계한다.

이벤트(outcome):
  - answered : 통화 연결(B) 후 종료. duration_sec = 마지막 calltime(초)
  - noanswer : 아웃바운드 발신(R) 이 B 로 이어지지 못하고 종료(통화무응답)

집계(방향/VGW별 + 합계):
  - 응답콜수, 무응답수(아웃바운드), 시도수(=응답+무응답), 응답률
  - 분당/시간당/일 평균 (선택 구간 wall-clock 기준)
  - 건당 평균 통화시간
"""

import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / 'vgw_stats.db'

KST = timezone(timedelta(hours=9))


def _kst_date(ts):
    """epoch(초) → KST 날짜 문자열 YYYY-MM-DD"""
    return datetime.fromtimestamp(ts, KST).strftime('%Y-%m-%d')


class VgwStatsStore:
    def __init__(self, db_path=DB_PATH, retention_days=30):
        self.db_path = str(db_path)
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn

    def _init_db(self):
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kdate TEXT NOT NULL,          -- KST 날짜 (종료시각 기준)
                    vgw TEXT NOT NULL,
                    direction TEXT NOT NULL,      -- inbound | outbound
                    channel INTEGER,
                    start_ts REAL,
                    end_ts REAL NOT NULL,
                    duration_sec INTEGER DEFAULT 0,
                    outcome TEXT NOT NULL          -- answered | noanswer
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_calls_date ON calls(kdate)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_calls_grp ON calls(kdate, direction, vgw)")

    def record_call(self, vgw, direction, channel, start_ts, end_ts,
                    duration_sec, outcome):
        try:
            with self._lock, self._conn() as c:
                c.execute(
                    "INSERT INTO calls(kdate,vgw,direction,channel,start_ts,end_ts,duration_sec,outcome)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (_kst_date(end_ts), vgw, direction, channel,
                     start_ts, end_ts, int(duration_sec or 0), outcome)
                )
        except Exception as e:
            logger.debug(f"VGW 콜 적재 실패(무시): {e}")

    def cleanup(self):
        """보존기간 초과 데이터 삭제."""
        cutoff = (datetime.now(KST) - timedelta(days=self.retention_days)).strftime('%Y-%m-%d')
        try:
            with self._lock, self._conn() as c:
                c.execute("DELETE FROM calls WHERE kdate < ?", (cutoff,))
        except Exception as e:
            logger.debug(f"VGW 통계 정리 실패(무시): {e}")

    # ── 집계 ───────────────────────────────────────────────
    def aggregate(self, start_date=None, end_date=None):
        """
        선택 구간(KST 날짜, 기본=오늘)의 방향/VGW별 통계 + 합계 반환.
        평균(분/시/일)은 구간 wall-clock 길이 기준.
        """
        today = datetime.now(KST).strftime('%Y-%m-%d')
        start_date = start_date or today
        end_date = end_date or today

        # 구간 wall-clock 초 (오늘이 끝이면 현재시각까지)
        win_start = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=KST)
        win_end_day = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=KST) + timedelta(days=1)
        now = datetime.now(KST)
        win_end = min(now, win_end_day)
        window_sec = max(1.0, (win_end - win_start).total_seconds())

        rows = []
        total_events = 0
        try:
            with self._lock, self._conn() as c:
                total_events = c.execute(
                    "SELECT COUNT(*) FROM calls WHERE kdate >= ? AND kdate <= ?",
                    (start_date, end_date)
                ).fetchone()[0]
                cur = c.execute(
                    """SELECT direction, vgw,
                              SUM(CASE WHEN outcome='answered' THEN 1 ELSE 0 END) AS answered,
                              SUM(CASE WHEN outcome='noanswer' THEN 1 ELSE 0 END) AS noanswer,
                              SUM(CASE WHEN outcome='answered' THEN duration_sec ELSE 0 END) AS dur_sum
                       FROM calls
                       WHERE kdate >= ? AND kdate <= ?
                       GROUP BY direction, vgw
                       ORDER BY direction, vgw""",
                    (start_date, end_date)
                )
                rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"VGW 통계 집계 오류: {e}")

        def build(direction, vgw, answered, noanswer, dur_sum):
            attempts = answered + noanswer
            return {
                'direction': direction,
                'vgw': vgw,
                'answered': answered,
                'noanswer': noanswer,
                'attempts': attempts,
                'answer_rate': round(answered / attempts * 100, 1) if attempts else None,
                'avg_duration_sec': round(dur_sum / answered, 1) if answered else 0,
                'per_min': round(answered / (window_sec / 60), 2),
                'per_hour': round(answered / (window_sec / 3600), 1),
                'per_day': round(answered / (window_sec / 86400), 1),
            }

        detail = [build(*r) for r in rows]

        # 합계 (전체 / 방향별)
        def totalize(items, direction):
            a = sum(x['answered'] for x in items)
            n = sum(x['noanswer'] for x in items)
            d = sum(x['avg_duration_sec'] * x['answered'] for x in items)  # 역산 합
            return build(direction, '합계', a, n, d)

        inbound = [x for x in detail if x['direction'] == 'inbound']
        outbound = [x for x in detail if x['direction'] == 'outbound']
        totals = {
            'inbound': totalize(inbound, 'inbound') if inbound else None,
            'outbound': totalize(outbound, 'outbound') if outbound else None,
        }

        return {
            'start_date': start_date,
            'end_date': end_date,
            'window_seconds': int(window_sec),
            'total_events': total_events,
            'detail': detail,
            'totals': totals,
        }
