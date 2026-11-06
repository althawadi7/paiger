"""One price per plan: admin card, landing, and the owner subscription card."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.getcwd(), ".env"))

from app import billing, database as db
from app import main as app_main


def prices():
    admin = {row["code"]: row["price_label"] for row in billing.plans_admin()}
    landing = {row["code"]: row["amount"] for row in app_main._landing_plans()}
    rest = db.get_restaurant_by_id(1)
    owner = {row["code"]: row["price_label"] for row in app_main._subscription_view(rest)["plans"]}
    features = {row["code"]: row["features"] for row in app_main._landing_plans()}
    return admin, landing, owner, features


def main():
    admin, landing, owner, features = prices()
    failed = []
    for code, amount in admin.items():
        if code not in landing:
            continue
        same = amount == landing[code] == owner.get(code)
        print(("PASS" if same else "FAIL"), code, amount, landing.get(code), owner.get(code))
        if not same:
            failed.append(code)
    points = next(row for row in billing.plans_admin() if row["code"] == "points")
    other = next((row for row in billing.plans_admin() if row["type"] == "points" and row["code"] != "points"), None)
    if other and not other["features"]["en"]:
        leaked = features.get(other["code"]) == points["features"]["en"] and bool(points["features"]["en"])
        print(("FAIL" if leaked else "PASS"), "empty features stay empty", other["code"], features.get(other["code"]))
        if leaked:
            failed.append(other["code"])
    if failed:
        raise SystemExit(1)
    print("ok", len(admin))


if __name__ == "__main__":
    main()
