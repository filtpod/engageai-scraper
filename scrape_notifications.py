import os
import re
from datetime import datetime, timedelta

import mysql.connector

from services import get_notifications, get_profile_viewers


def get_db_connection():
    return mysql.connector.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        port=int(os.environ.get("DB_PORT", "3306")),
        ssl_disabled=False,
        connection_timeout=30,
    )


def main():
    print("scrape_notifications starting...", flush=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now()
    last_day = now - timedelta(days=1)

    cursor.execute(
        "SELECT DISTINCT user_id FROM podserver_linkedinnotification WHERE date > %s",
        (last_day,),
    )
    excluded_ids = tuple(row[0] for row in cursor.fetchall())

    if excluded_ids:
        placeholders = ", ".join(["%s"] * len(excluded_ids))
        cursor.execute(
            f"""
            SELECT id, linkedin_cookie FROM podserver_customuser
            WHERE linkedin_cookie IS NOT NULL
              AND `group` IN ('Premium', 'Growth Plan', 'Starter')
              AND id NOT IN ({placeholders})
            ORDER BY last_login DESC LIMIT 200
            """,
            excluded_ids,
        )
    else:
        cursor.execute(
            """
            SELECT id, linkedin_cookie FROM podserver_customuser
            WHERE linkedin_cookie IS NOT NULL
              AND `group` IN ('Premium', 'Growth Plan', 'Starter')
            ORDER BY last_login DESC LIMIT 200
            """
        )
    users = cursor.fetchall()
    print(f"Users to process: {len(users)}", flush=True)

    for user in users:
        user_id = user[0]
        cookie = user[1]
        token_match = re.search(r"ajax:\d*", cookie)
        if not token_match:
            print(f"user_id={user_id} invalid cookie, skipping", flush=True)
            continue
        token = token_match.group()

        result = get_notifications(cookie, token)
        if result["status"] == 200:
            for d in result["data"]:
                try:
                    name_matches = re.findall(r'"?([^"]*)"?', d["view_profile_text"])
                    name = [m for m in name_matches if m.strip()][0]
                except (IndexError, TypeError):
                    name = ""
                row = {
                    "user_id": user_id,
                    "post_link": d["post_link"],
                    "date": datetime.fromtimestamp(int(d["timestamp"] / 1000)),
                    "prospects_profile_url": d["prospects_profile_url"],
                    "prospects_profile_id": d["prospects_profile_url_id"],
                    "notification_text": d["view_profile_text"],
                    "prospects_name": name,
                    "notification_type": d["reaction_type"],
                }
                cursor.execute(
                    "SELECT id FROM podserver_linkedinnotification WHERE user_id = %s AND notification_type = %s AND post_link = %s LIMIT 1",
                    (user_id, d["reaction_type"], d["post_link"]),
                )
                if cursor.fetchone():
                    continue
                columns = ", ".join(row.keys())
                placeholders = ", ".join(["%s"] * len(row))
                cursor.execute(
                    f"INSERT INTO podserver_linkedinnotification ({columns}) VALUES ({placeholders})",
                    tuple(row.values()),
                )
                conn.commit()

        for start in range(0, 101, 10):
            result = get_profile_viewers(cookie, token, start)
            if result["status"] != 200:
                break
            if not result["data"]:
                break
            for d in result["data"]:
                d["user_id"] = user_id
                cursor.execute(
                    "SELECT id FROM podserver_linkedinnotification WHERE user_id = %s AND notification_type = %s AND (prospects_profile_url = %s OR prospects_profile_id = %s) LIMIT 1",
                    (user_id, d["notification_type"], d.get("prospects_profile_url"), d["prospects_profile_id"]),
                )
                if cursor.fetchone():
                    continue
                columns = ", ".join(d.keys())
                placeholders = ", ".join(["%s"] * len(d))
                cursor.execute(
                    f"INSERT INTO podserver_linkedinnotification ({columns}) VALUES ({placeholders})",
                    tuple(d.values()),
                )
                conn.commit()

        print(f"user_id={user_id} done", flush=True)

    cursor.close()
    conn.close()
    print("scrape_notifications finished.", flush=True)


if __name__ == "__main__":
    main()
