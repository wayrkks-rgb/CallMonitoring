# -*- coding: utf-8 -*-
"""
diag_call_volume.py — 시간당 콜 수와 '장애 1건당 영향 통화' 계산

장애가 나서 원인을 찾는 동안에도 콜은 계속 들어온다. 원인 특정이 빨라지면
그동안 장애를 겪는 통화가 줄어든다. 이 스크립트는 인덱스 DB(최근 30일)의
실제 통화 기록으로 시간당 콜 수를 구하고, 원인 특정 시간 전후로 영향 통화가
얼마나 달라지는지 계산한다.

사용법:
    python diag_call_volume.py
    python diag_call_volume.py --before 30 --after 5
    python diag_call_volume.py --db D:/CallMonitoring/ars_index.db --share 0.2

  --before / --after : 원인 특정 시간(분). 기본 30 / 5
  --share            : 장애가 전체 콜 중 몇 %에 영향을 주는지 (0~1, 기본 1).
                       특정 업무에서만 나는 장애라면 그 업무 비중을 넣는다.
"""
import os
import sys
import sqlite3
import argparse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description="시간당 콜 수 / 장애 1건당 영향 통화")
    ap.add_argument("--db", default=os.path.join(HERE, "ars_index.db"))
    ap.add_argument("--before", type=float, default=30, help="개선 전 원인 특정 시간(분)")
    ap.add_argument("--after", type=float, default=5, help="개선 후 원인 특정 시간(분)")
    ap.add_argument("--share", type=float, default=1.0,
                    help="장애 영향 비중 0~1 (기본 1 = 전체 콜)")
    a = ap.parse_args()

    if not os.path.isfile(a.db):
        print(f"인덱스 DB 가 없습니다: {a.db}")
        return 2
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT substr(start_time,1,13) AS h, COUNT(*) FROM calls "
        "WHERE start_time IS NOT NULL AND length(start_time) >= 13 "
        "GROUP BY h").fetchall()
    con.close()
    if not rows:
        print("통화 기록이 없습니다 (인덱서가 아직 채우지 못했을 수 있음).")
        return 1

    # 시각대(0~23시)별로 모아 평균을 낸다. 콜이 0건인 시간은 DB 에 없으므로
    # 날짜 수로 나눠야 실제 평균이 된다.
    days = sorted({h[:10] for h, _ in rows})
    by_hour = defaultdict(int)
    for h, n in rows:
        try:
            by_hour[int(h[11:13])] += n
        except ValueError:
            continue
    nd = len(days)
    avg_by_hour = {hh: by_hour.get(hh, 0) / nd for hh in range(24)}
    biz = [avg_by_hour[hh] for hh in range(9, 18)]
    biz_avg = sum(biz) / len(biz)
    peak_hh = max(avg_by_hour, key=avg_by_hour.get)
    peak = avg_by_hour[peak_hh]
    busiest = max(rows, key=lambda r: r[1])
    total = sum(n for _, n in rows)

    print(f"대상 기간 : {days[0]} ~ {days[-1]} ({nd}일) · 통화 {total:,}건")
    print("\n시각대별 평균 콜 수 (1시간)")
    for hh in range(24):
        v = avg_by_hour[hh]
        if v <= 0:
            continue
        bar = "█" * int(40 * v / peak) if peak else ""
        print(f"  {hh:02d}시  {v:8.0f}  {bar}")

    print(f"\n업무시간(09~18시) 평균 : 시간당 {biz_avg:,.0f}콜")
    print(f"피크 시각대 ({peak_hh:02d}시)  : 시간당 {peak:,.0f}콜")
    print(f"가장 많았던 1시간      : {busiest[0]}시 {busiest[1]:,}콜")

    def hit(per_hour, minutes):
        return per_hour * a.share * minutes / 60

    print(f"\n장애 1건당 영향 통화 (원인 특정 {a.before:g}분 → {a.after:g}분, "
          f"영향 비중 {a.share:.0%})")
    for name, v in (("업무시간 평균", biz_avg), ("피크 시각대", peak)):
        b, f = hit(v, a.before), hit(v, a.after)
        print(f"  {name:10s}  {b:7,.0f}건 → {f:6,.0f}건   ({b - f:,.0f}건 감소)")

    print("\n※ 장애가 특정 업무에서만 난다면 --share 에 그 업무 비중을 넣어야 "
          "과장되지 않습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
