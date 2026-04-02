import os
import re
import time
from datetime import datetime, timedelta

import concurrent.futures
import threading
from queue import Queue

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


def _existing_submission_links(cursor, user_id, post_urls, chunk_size=100):
    """Return set of prospects_link values already stored for this member."""
    existing = set()
    if not post_urls:
        return existing
    for i in range(0, len(post_urls), chunk_size):
        chunk = post_urls[i : i + chunk_size]
        placeholders = ",".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT prospects_link
            FROM podserver_prospectssubmissions
            WHERE member_id = %s
              AND prospects_link IN ({placeholders})
            """,
            (user_id, *chunk),
        )
        for row in cursor.fetchall():
            existing.add(row[0])
    return existing


def get_db_connection():
    """Create a new MySQL connection using environment variables."""
    print("Connecting to database...", flush=True)
    conn = mysql.connector.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        port=int(os.environ.get("DB_PORT", "3306")),
        ssl_disabled=False,
        connection_timeout=30,
    )
    print("Database connected.", flush=True)
    return conn


def main():
    run_number_raw = os.environ.get("SCRAPE_RUN_NUMBER", "").strip()
    run_number = run_number_raw if run_number_raw else str(int(time.time()))

    print(f"Scraper job starting (run={run_number})...", flush=True)

    # App Platform scheduled jobs are killed around 30 minutes; stop earlier so we
    # exit cleanly, commit work, and send admin email. Next cron run continues.
    max_runtime_s = float(os.environ.get("SCRAPE_MAX_RUNTIME_SECONDS", "1500"))
    start_mono = time.monotonic()

    def over_budget():
        return (time.monotonic() - start_mono) >= max_runtime_s

    print(
        f"Runtime budget: {max_runtime_s}s (set SCRAPE_MAX_RUNTIME_SECONDS to tune).",
        flush=True,
    )

    # Cap how many eligible users to load per run (ORDER BY last_login DESC).
    max_users_per_run = max(1, int(os.environ.get("SCRAPE_MAX_USERS_PER_RUN", "5")))

    # Parallelism knobs (prospects processed in parallel, DB writes serialized).
    max_workers = max(1, int(os.environ.get("SCRAPE_MAX_WORKERS", "4")))
    write_queue_size = max(1, int(os.environ.get("SCRAPE_WRITE_QUEUE_SIZE", "50")))

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
        LIMIT %s
        """,
        (max_users_per_run,),
    )

    users = cursor.fetchall()
    print('-------------------------------- STARTING SCRAPE --------------------------------')
    print(
        f"Batch: limit={max_users_per_run} eligible_users_fetched={len(users)}",
        flush=True,
    )
    now = datetime.now()

    for user in users:
        if over_budget():
            print(
                "Stopping: SCRAPE_MAX_RUNTIME_SECONDS budget exhausted (between users).",
                flush=True,
            )
            break

        user_id = user[0]
        cookie = user[1]
        hubspot_account_id = user[2]
        hubspot_refresh_token = user[3]
        max_number_of_regenerated_AI_responses = user[4]

        print(f"[run={run_number}] user_id={user_id}", flush=True)

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

            cutoff_date = (now - timedelta(hours=24)).date()
            cursor.execute(
                """
                SELECT
                    id,
                    prospects_profile_url,
                    email,
                    monitoring
                FROM podserver_prospectsprofile
                WHERE member_id = %s
                  AND (last_lookup_date < %s OR last_lookup_date IS NULL)
                ORDER BY last_lookup_date DESC
                """,
                (user_id, cutoff_date),
            )
            prospects = cursor.fetchall()

            hubspot_api_client = None
            if (
                hubspot_account_id
                and hubspot_refresh_token
                and hubspot_client_id
                and hubspot_client_secret
            ):
                try:
                    # HubSpot SDK requires an auth field at initialization time.
                    hubspot_api_client = HubSpot(access_token="bootstrap")
                    tokens_response = (
                        hubspot_api_client.auth.oauth.tokens_api.create_token(
                            grant_type="refresh_token",
                            refresh_token=hubspot_refresh_token,
                            client_id=hubspot_client_id,
                            client_secret=hubspot_client_secret,
                        )
                    )
                    hubspot_api_client.access_token = tokens_response.access_token
                except Exception as hubspot_auth_err:
                    print("HubSpot token refresh failed:", hubspot_auth_err, flush=True)
                    hubspot_api_client = None

            # Prospect processing in parallel with a single DB writer.
            write_queue: Queue = Queue(maxsize=write_queue_size)
            invalid_cookie_event = threading.Event()

            worker_local = threading.local()
            read_conns = []
            read_conns_lock = threading.Lock()

            def get_worker_cursor():
                cur = getattr(worker_local, "cursor", None)
                if cur is not None:
                    return cur

                read_conn = get_db_connection()
                read_cursor = read_conn.cursor()
                worker_local.conn = read_conn
                worker_local.cursor = read_cursor
                with read_conns_lock:
                    read_conns.append(read_conn)
                return read_cursor

            def writer_loop():
                write_conn = get_db_connection()
                write_cursor = write_conn.cursor()
                try:
                    while True:
                        item = write_queue.get()
                        if item is None:
                            break
                        if over_budget():
                            # Keep draining the queue to avoid deadlocks when
                            # the main thread is still trying to enqueue results.
                            continue

                        inserts = item.get("inserts", [])
                        update_statement = item.get("update_statement")
                        update_values = item.get("update_values")

                        for ins in inserts:
                            try:
                                write_cursor.execute(
                                    ins["insert_statement"], ins["values"]
                                )
                                write_conn.commit()

                                # TODO: Re-enable HubSpot timeline writes after threaded
                                # pipeline is validated in production.
                                time.sleep(0.25)  # simulate HubSpot API latency
                                # hubspot_event = ins.get("hubspot_event")
                                # if hubspot_api_client and hubspot_event:
                                #     hubspot_api_client.crm.timeline.events_api.create(
                                #         timeline_event=hubspot_event
                                #     )
                            except Exception as e:
                                print(
                                    f"Error inserting submission run={run_number} user_id={item.get('user_id')} prospect_id={item.get('prospect_id')}: {e}",
                                    flush=True,
                                )

                        if update_statement:
                            try:
                                write_cursor.execute(
                                    update_statement, update_values
                                )
                                write_conn.commit()
                            except Exception as e:
                                print(
                                    f"Error updating prospect profile run={run_number} user_id={item.get('user_id')} prospect_id={item.get('prospect_id')}: {e}",
                                    flush=True,
                                )
                finally:
                    try:
                        write_cursor.close()
                    except Exception:
                        pass
                    try:
                        write_conn.close()
                    except Exception:
                        pass

            writer_thread = threading.Thread(target=writer_loop, daemon=True)
            writer_thread.start()

            def prospect_worker(prospect_row):
                if invalid_cookie_event.is_set() or over_budget():
                    return None

                prospect_id_local = prospect_row[0]
                prospects_profile_url_local = prospect_row[1]
                prospects_email_local = prospect_row[2]
                monitoring_local = prospect_row[3]

                print(
                    f"[run={run_number}] prospect_id={prospect_id_local} prospects_profile_url={prospects_profile_url_local}",
                    flush=True,
                )

                try:
                    match_local = re.search(
                        r"((?<=in/)|(?<=company/)).*",
                        prospects_profile_url_local,
                    )
                    if not match_local:
                        return None

                    profile_name_local = match_local.group(0).split("/")[0]

                    if "company" in prospects_profile_url_local:
                        is_company_local = True
                    else:
                        is_company_local = False

                    if not monitoring_local:
                        return None

                    li_profile_id = get_linkedin_profile_id(
                        profile_name_local, cookie, token, is_company_local
                    )
                    if li_profile_id in (401, 403, 503):
                        return None
                    if li_profile_id is None:
                        return None

                    response = get_recent_posts(
                        li_profile_id, is_company_local, cookie, token
                    )

                    if isinstance(response, list):
                        print(
                            f"[run={run_number}] prospect_id={prospect_id_local} fetched_posts={len(response)}",
                            flush=True,
                        )
                    else:
                        print(response)

                    if response == "invalid cookie":
                        invalid_cookie_event.set()
                        return {"type": "invalid_cookie"}

                    if response is None or len(response) == 0:
                        return None

                    print(
                        f"success run={run_number} user_id={user_id} prospect_id={prospect_id_local}",
                        flush=True,
                    )
                    most_recent_post_date = response[0]["date_submitted"]
                    is_active_local = most_recent_post_date > now - timedelta(days=30)

                    # Read-only existence check in worker to avoid wasted DeepSeek.
                    post_urls_batch = [d["post_url"] for d in response]
                    read_cursor = get_worker_cursor()
                    existing_submission_links = _existing_submission_links(
                        read_cursor, user_id, post_urls_batch
                    )

                    inserts = []
                    for data in response:
                        post_content = data["text"]
                        post_url = data["post_url"]
                        post_date = data["date_submitted"]
                        submission_exists = post_url in existing_submission_links

                        if submission_exists:
                            continue
                        if post_date <= now - timedelta(days=8):
                            continue

                        ai_response = ""
                        resp1 = deepseekAI(post_content)
                        try:
                            print(
                                f"DeepSeek AI run={run_number} user_id={user_id} prospect_id={prospect_id_local}:",
                                resp1,
                                flush=True,
                            )
                            summary = resp1.choices[0].message.content
                            num_tokens_used = resp1.usage.total_tokens
                        except Exception:
                            summary = post_content[:300]
                            num_tokens_used = 0

                        print(
                            f"DeepSeek summary run={run_number} user_id={user_id} prospect_id={prospect_id_local}:",
                            summary,
                            flush=True,
                        )
                        post_content_clean = (
                            post_content.encode("ascii", "ignore").decode("UTF-8")
                        )

                        prospects_profile_data = {
                            "member_id": user_id,
                            "prospects_link": post_url,
                            "date_submitted": post_date,
                            "post_content": post_content_clean,
                            "prospects_profile_url": prospects_profile_url_local,
                            "max_num_regenerations": max_number_of_regenerated_AI_responses,
                            "num_tokens_used": num_tokens_used,
                            "post_summary": summary,
                            "date_scraped": now,
                            "is_complete": False,
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

                        hubspot_event = None
                        if hubspot_api_client and prospects_email_local:
                            hubspot_event = {
                                "eventTemplateId": 1210728,
                                "email": prospects_email_local,
                                "tokens": {
                                    "linkedin_post_url": post_url,
                                    "suggested_comment": ai_response,
                                },
                            }

                        inserts.append(
                            {
                                "insert_statement": insert_statement,
                                "values": values,
                                "hubspot_event": hubspot_event,
                            }
                        )
                        existing_submission_links.add(post_url)

                    result = get_profile_details(
                        profile_name_local, cookie, token, is_company_local
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
                        update_statement = """
                            UPDATE podserver_prospectsprofile
                            SET first_name=%s,
                                last_name=%s,
                                headline=%s,
                                profile_photo_url=%s,
                                last_lookup_date=%s,
                                is_active=%s
                            WHERE id=%s
                        """
                        update_values = (
                            first_name,
                            last_name,
                            headline,
                            profile_photo_url,
                            now,
                            int(is_active_local),
                            prospect_id_local,
                        )
                    else:
                        update_statement = """
                            UPDATE podserver_prospectsprofile
                            SET last_lookup_date=%s,
                                is_active=%s
                            WHERE id=%s
                        """
                        update_values = (
                            now,
                            int(is_active_local),
                            prospect_id_local,
                        )

                    return {
                        "user_id": user_id,
                        "prospect_id": prospect_id_local,
                        "inserts": inserts,
                        "update_statement": update_statement,
                        "update_values": update_values,
                    }
                except Exception as e:
                    print(
                        f"Error in prospect_worker run={run_number} user_id={user_id} prospect_id={prospect_id_local}: {e}",
                        flush=True,
                    )
                    return None

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:
                futures = []
                for prospect_row in prospects:
                    if over_budget():
                        print(
                            "Stopping: SCRAPE_MAX_RUNTIME_SECONDS budget exhausted (between prospects).",
                            flush=True,
                        )
                        break
                    if invalid_cookie_event.is_set():
                        break
                    futures.append(executor.submit(prospect_worker, prospect_row))

                try:
                    for fut in concurrent.futures.as_completed(futures):
                        result = fut.result()
                        if result is None:
                            continue
                        if result.get("type") == "invalid_cookie":
                            invalid_cookie_event.set()
                            break
                        if invalid_cookie_event.is_set() or over_budget():
                            continue
                        write_queue.put(result)
                finally:
                    # Ensure we don't block forever on workers that may still be running.
                    for fut in futures:
                        if not fut.done():
                            fut.cancel()

            write_queue.put(None)
            writer_thread.join()

            # Cleanup any per-thread DB connections.
            for c in read_conns:
                try:
                    c.close()
                except Exception:
                    pass

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

