import os
import re

import mysql.connector

from services import get_profile_details, remove_emojis


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
    linkedin_cookie = os.environ.get("LINKEDIN_COOKIE", "").strip()
    if not linkedin_cookie:
        print("LINKEDIN_COOKIE env var not set, exiting.", flush=True)
        return
    token_match = re.search(r"ajax:\d*", linkedin_cookie)
    if not token_match:
        print("Invalid LINKEDIN_COOKIE — no ajax token found, exiting.", flush=True)
        return
    token = token_match.group()

    print("scrape_prospects_details starting...", flush=True)
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, prospects_profile_url, member_id FROM podserver_prospectsprofile WHERE profile_photo_url IS NULL ORDER BY date_added DESC LIMIT 500"
    )
    prospects = cursor.fetchall()
    print(f"Prospects to process: {len(prospects)}", flush=True)

    for prospect in prospects:
        prospect_id = prospect[0]
        prospects_profile_url = prospect[1]

        match = re.search(r"((?<=in/)|(?<=company/)).*", prospects_profile_url)
        if not match:
            continue
        profile_name = match.group(0).split("/")[0]
        is_company = "company" in prospects_profile_url

        result = get_profile_details(profile_name, linkedin_cookie, token, is_company)
        if isinstance(result, dict):
            try:
                first_name = result["first_name"]
                if first_name is None and "miniProfile" in profile_name:
                    m = re.search(r"^[^?]+", profile_name)
                    if m:
                        cursor.execute(
                            'UPDATE podserver_prospectsprofile SET first_name=%s, last_name="-", headline="" WHERE prospects_profile_url=%s',
                            (m.group(0), prospects_profile_url),
                        )
                        conn.commit()
                    continue
                elif first_name is None:
                    cursor.execute(
                        'UPDATE podserver_prospectsprofile SET first_name=%s, last_name="-", headline="" WHERE prospects_profile_url=%s',
                        (profile_name, prospects_profile_url),
                    )
                    conn.commit()
                    continue

                first_name = remove_emojis(first_name).replace('"', "").replace("'", "")
                last_name_raw = result.get("last_name") or ""
                last_name = remove_emojis(last_name_raw).replace('"', "").replace("'", "")
                headline_raw = result.get("headline") or ""
                headline = remove_emojis(headline_raw).replace('"', "")
                profile_photo_url = result.get("profile_photo_url")

                cursor.execute(
                    "UPDATE podserver_prospectsprofile SET first_name=%s, last_name=%s, headline=%s, profile_photo_url=%s WHERE prospects_profile_url=%s",
                    (first_name, last_name, headline, profile_photo_url, prospects_profile_url),
                )
                conn.commit()
                print(f"Updated prospect_id={prospect_id}", flush=True)
            except Exception as e:
                print(f"Error updating prospect_id={prospect_id}: {e}", flush=True)
                continue
        elif result is None and "miniProfile" in profile_name:
            profile_url = prospects_profile_url.split("?")[0] + "/"
            m = re.search(r"^[^?]+", profile_name)
            if m:
                cursor.execute(
                    'UPDATE podserver_prospectsprofile SET first_name=%s, last_name="-", headline="", prospects_profile_url=%s WHERE prospects_profile_url=%s',
                    (m.group(0), profile_url, prospects_profile_url),
                )
                conn.commit()

    cursor.close()
    conn.close()
    print("scrape_prospects_details finished.", flush=True)


if __name__ == "__main__":
    main()
