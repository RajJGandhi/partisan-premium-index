from app.db.database import init_db
from app.jobs.scan_markets import run as scan_markets
from app.jobs.update_snapshots import run as update_snapshots
from app.jobs.score_markets import run as score_markets
from app.jobs.update_paper_trades import run as update_paper_trades

if __name__ == "__main__":
    init_db()
    print("Scanning Polymarket...")
    markets = scan_markets()
    print(f"Stored/updated {markets} relevant markets.")
    print("Updating order-book snapshots...")
    snapshots = update_snapshots()
    print(f"Stored {snapshots} snapshots.")
    print("Scoring markets...")
    signals = score_markets()
    print(f"Generated {signals} PPI signals.")
    print("Updating paper trades...")
    created, updated = update_paper_trades()
    print(f"Created {created} paper trades; updated {updated} open paper trades.")
