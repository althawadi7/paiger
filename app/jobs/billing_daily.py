"""Hourly billing emails. Safe to run again: email_log keeps each one unique."""
from .. import billing_mail


def main() -> None:
    rows = billing_mail.run_daily()
    if not rows:
        print("nothing to send")
        return
    for row in rows:
        print(f"{row.get('kind')} {row.get('status')} {row.get('ref_key')}")


if __name__ == "__main__":
    main()
