import os
import re
from datetime import datetime, timedelta

import mysql.connector

from services import (
    get_linkedin_profile_id,
    get_recent_posts,
    admin_email,
    get_profile_details,
    remove_emojis,
)
from openai_api import azureAI, deepseekAI
from hubspot import HubSpot


def get_db_connection():
    """Create a new MySQL connection using environment variables."""
    return mysql.connector.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        port=int(os.environ.get("DB_PORT", "3306")),
        ssl_disabled=False,
    )


def main():
    # HubSpot OAuth app credentials from env
    hubspot_client_id = os.environ.get("HUBSPOT_CLIENT_ID", "")
    hubspot_client_secret = os.environ.get("HUBSPOT_CLIENT_SECRET", "")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            linkedin_cookie,
            hubspot_account_id,
            hubspot_refresh_token,
            max_number_of_regenerated_AI_responses
        FROM podserver_customuser
        WHERE `group` IN ('Member', 'Growth Plan', 'Trial', 'Premium', 'Starter')
          AND linkedin_cookie != ''
          AND linkedin_cookie IS NOT NULL
        ORDER BY last_login DESC
        """
    )

    users = cursor.fetchall()
    now = datetime.now()

    for user in users:
        user_id = user[0]
        cookie = user[1]
        hubspot_account_id = user[2]
        hubspot_refresh_token = user[3]
        max_number_of_regenerated_AI_responses = user[4]

        print("user id:", user_id)

        try:
            if not cookie:
                print("Missing LinkedIn cookie")
                continue

            token_match = re.search(r"ajax:\d*", cookie)
            if not token_match:
                print("Invalid LinkedIn cookie")
                continue
            token = token_match.group()

            li_at_match = re.search(r"li_at=(.*?);", cookie)
            if not li_at_match:
                print("Missing li_at cookie")
                continue
            li_at = "li_at=" + li_at_match.group(1)

            cursor.execute(
                f"""
                SELECT
                    id,
                    prospects_profile_url,
                    email,
                    monitoring
                FROM podserver_prospectsprofile
                WHERE member_id = {user_id}
                  AND (last_lookup_date < '{(now - timedelta(hours=24)).date()}' OR last_lookup_date IS NULL)
                ORDER BY last_lookup_date DESC
                """
            )
            prospects = cursor.fetchall()

            for prospect in prospects:
                prospect_id = prospect[0]
                prospects_profile_url = prospect[1]
                prospects_email = prospect[2]
                monitoring = prospect[3]

                match = re.search(r"((?<=in/)|(?<=company/)).*", prospects_profile_url)
                if not match:
                    continue

                profile_name = match.group(0).split("/")[0]

                if "company" in prospects_profile_url:
                    is_company = True
                    url = (
                        "https://www.linkedin.com/company/"
                        + profile_name
                        + "/posts/?feedView=all"
                    )
                else:
                    is_company = False
                    url = (
                        "https://www.linkedin.com/in/"
                        + profile_name
                        + "/recent-activity/all/"
                    )

                if not monitoring:
                    continue

                li_profile_id = get_linkedin_profile_id(
                    profile_name, cookie, token, is_company
                )
                if li_profile_id in (401, 403, 503):
                    continue
                elif li_profile_id is not None:
                    response = get_recent_posts(
                        li_profile_id, is_company, cookie, token
                    )
                else:
                    continue

                print(response)

                if response == "invalid cookie":
                    break
                elif response is not None and len(response) > 0:
                    print("success")
                    most_recent_post_date = response[0]["date_submitted"]
                    is_active = most_recent_post_date > now - timedelta(days=30)

                    for data in response:
                        post_content = data["text"]
                        post_url = data["post_url"]
                        post_date = data["date_submitted"]

                        cursor.execute(
                            """
                            SELECT id
                            FROM podserver_prospectssubmissions
                            WHERE member_id = %s
                              AND prospects_link = %s
                            LIMIT 1
                            """,
                            (user_id, post_url),
                        )
                        submission = cursor.fetchone()

                        try:
                            if not submission and post_date > now - timedelta(days=8):
                                ai_response = ""

                                resp1 = deepseekAI(post_content)
                                try:
                                    print("DeepSeek AI:", resp1)
                                    summary = resp1.choices[0].message.content
                                    num_tokens_used = resp1.usage.total_tokens
                                except Exception:
                                    summary = post_content[:300]
                                    num_tokens_used = 0

                                print(summary)
                                post_content_clean = (
                                    post_content.encode("ascii", "ignore").decode(
                                        "UTF-8"
                                    )
                                )

                                prospects_profile_data = {
                                    "member_id": user_id,
                                    "prospects_link": post_url,
                                    "date_submitted": post_date,
                                    "post_content": post_content_clean,
                                    "prospects_profile_url": prospects_profile_url,
                                    "max_num_regenerations": max_number_of_regenerated_AI_responses,
                                    "num_tokens_used": num_tokens_used,
                                    "post_summary": summary,
                                    "date_scraped": now,
                                }

                                columns = ", ".join(prospects_profile_data.keys())
                                placeholders = ", ".join(
                                    ["%s"] * len(prospects_profile_data.keys())
                                )
                                values = tuple(prospects_profile_data.values())

                                insert_statement = f"""
                                    INSERT INTO podserver_prospectssubmissions ({columns})
                                    VALUES ({placeholders})
                                """
                                cursor.execute(insert_statement, values)
                                conn.commit()

                                if (
                                    hubspot_account_id
                                    and hubspot_refresh_token
                                    and prospects_email
                                    and hubspot_client_id
                                    and hubspot_client_secret
                                ):
                                    api_client = HubSpot()
                                    tokens_response = (
                                        api_client.auth.oauth.tokens_api.create_token(
                                            grant_type="refresh_token",
                                            refresh_token=hubspot_refresh_token,
                                            client_id=hubspot_client_id,
                                            client_secret=hubspot_client_secret,
                                        )
                                    )
                                    api_client.access_token = tokens_response.access_token
                                    api_client.crm.timeline.events_api.create(
                                        timeline_event={
                                            "eventTemplateId": 1210728,
                                            "email": prospects_email,
                                            "tokens": {
                                                "linkedin_post_url": post_url,
                                                "suggested_comment": ai_response,
                                            },
                                        }
                                    )

                            elif submission:
                                print("submission already exists")

                        except Exception as e:
                            print("Error", e)

                    result = get_profile_details(
                        profile_name, cookie, token, is_company
                    )
                    if isinstance(result, dict):
                        first_name = remove_emojis(result["first_name"])
                        last_name = (
                            remove_emojis(result["last_name"])
                            if result["last_name"]
                            else result["last_name"]
                        )
                        headline = (
                            remove_emojis(result["headline"])
                            if result["headline"]
                            else result["headline"]
                        )
                        profile_photo_url = result["profile_photo_url"]
                        update_statement = f'''
                            UPDATE podserver_prospectsprofile
                            SET first_name="{first_name}",
                                last_name="{last_name}",
                                headline="{headline}",
                                profile_photo_url="{profile_photo_url}",
                                last_lookup_date='{now}',
                                is_active={int(is_active)}
                            WHERE member_id={user_id}
                              AND prospects_profile_url="{prospects_profile_url}"
                        '''
                    else:
                        update_statement = f"""
                            UPDATE podserver_prospectsprofile
                            SET last_lookup_date='{now}',
                                is_active={int(is_active)}
                            WHERE member_id={user_id}
                              AND prospects_profile_url='{prospects_profile_url}'
                        """

                    try:
                        cursor.execute(update_statement)
                        conn.commit()
                    except Exception as e:
                        print("Error updating prospect profile", e)
                        continue

        except Exception as e:
            print("Top-level user loop error:", e)

            # Attempt to reconnect to the database if the connection is lost.
            try:
                conn.close()
            except Exception:
                pass

            try:
                conn = get_db_connection()
                cursor = conn.cursor()
            except Exception as conn_err:
                print("Failed to reconnect to database:", conn_err)
                break

    cursor.close()
    conn.close()

    # Notify admin that the scraping job finished.
    try:
        admin_email()
    except Exception as e:
        print("Failed to send admin email:", e)


if __name__ == "__main__":
    main()

