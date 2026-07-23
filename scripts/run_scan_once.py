from app.jobs.scan_markets import run

if __name__ == "__main__":
    count = run()
    print(f"Stored/updated {count} relevant markets.")
