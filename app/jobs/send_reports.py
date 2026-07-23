from __future__ import annotations

from app.db.database import get_session, init_db
from app.reports.markdown_reports import write_daily_report, write_weekly_report


def run() -> tuple[str, str]:
    init_db()
    with get_session() as session:
        daily = write_daily_report(session)
        weekly = write_weekly_report(session)
    return str(daily), str(weekly)


if __name__ == "__main__":
    daily, weekly = run()
    print(f"Wrote reports: {daily}, {weekly}")
