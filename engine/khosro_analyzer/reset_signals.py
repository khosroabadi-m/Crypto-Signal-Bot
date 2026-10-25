"""Start a clean signal ledger for today without deleting the signal schema."""
from signal_store import initialize_daily_file

path = initialize_daily_file()
print(f"Initialized empty signal ledger: {path}")
