import json
import os
import re
from datetime import datetime, timedelta
from operator import itemgetter

import requests
from bs4 import BeautifulSoup

now = datetime.now()


def get_date(date_string):
    number = int(re.search(r"\d+", date_string).group())
    match = re.search(r"^(.*?)\s*•", date_string)
    if match:
        date_string = match.group(1)
    if any(x in date_string for x in ("month", "months", "m")):
        if number == 0:
            number = 1
        date = now - timedelta(weeks=4 * number)
    elif any(x in date_string for x in ("week", "weeks", "w")):
        date = now - timedelta(weeks=number)
    elif any(x in date_string for x in ("day", "days", "d")):
        date = now - timedelta(days=number)
    elif any(x in date_string for x in ("hour", "hours", "h")):
        date = now - timedelta(hours=number)
    else:
        date = now - timedelta(days=365)
    return date


def get_linkedin_profile_id(profile_name, cookie, token, is_company=False):
    headers = {
        "accept": "application/vnd.linkedin.normalized+json+2.1",
        "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Cookie": cookie,
        "Csrf-token": token,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "x-li-lang": "en_US",
        "x-li-page-instance": "urn:li:page:d_flagship3_profile_view_base;x4hHqteNSz+bf42OVgbxWQ==",
        "x-li-track": '{"clientVersion":"","mpVersion":"","osName":"web","timezoneOffset":-4,"mpName":"voyager-web","displayDensity":2,"displayWidth":2880,"displayHeight":1800}',
        "x-restli-protocol-version": "2.0.0",
        "Referer": f"https://www.linkedin.com/in/{profile_name}/recent-activity/all/",
    }
    try:
        if is_company:
            url = (
                "https://www.linkedin.com/voyager/api/graphql"
                "?includeWebMetadata=true"
                f"&variables=(universalName:{profile_name})"
                "&queryId=voyagerOrganizationDashCompanies.66b63095f5bc90a4972aaa61dd2ea70b"
            )
        else:
            url = (
                "https://www.linkedin.com/voyager/api/graphql"
                "?includeWebMetadata=true"
                f"&variables=(memberIdentity:{profile_name})"
                "&queryId=voyagerIdentityDashProfiles.7ca063cf163e5eea69e01132b41784f9"
            )
        response = requests.get(url, headers=headers)
        response = json.loads(response.text)

        try:
            if is_company:
                node = response["data"]["data"]["organizationDashCompaniesByUniversalName"]
                if node and "*elements" in node:
                    element = node["*elements"][0]
                    li_profile_id = re.search(r"(?<=fsd_company:).*", element).group(0)
                else:
                    return 400
            else:
                node = response["data"]["data"]["identityDashProfilesByMemberIdentity"]
                if node and "*elements" in node:
                    element = node["*elements"][0]
                    li_profile_id = re.search(r"(?<=fsd_profile:).*", element).group(0)
                else:
                    return 400
            return li_profile_id
        except Exception as e:
            print(e)
            print("Error retrieving LI profile ID of:", profile_name)
            if "status" in response["data"] and response["data"]["status"] == 401:
                return 401
            elif "status" in response["data"] and response["data"]["status"] == 403:
                return 403
            elif "errors" in response["data"]:
                print("error in LI profile data")
            elif hasattr(response, "status_code") and response.status_code == 403:
                return 403
            elif hasattr(response, "status_code") and response.status_code == 401:
                return 401
            elif hasattr(response, "status_code") and response.status_code == 503:
                return 503
            return None

    except Exception as e:
        print(e)
        return None


def get_recent_posts(li_profile_id, is_company, cookie, token):
    try:
        headers = {
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
            "Cookie": cookie,
            "Csrf-token": token,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-li-lang": "en_US",
            "x-li-page-instance": "urn:li:page:d_flagship3_profile_view_base_recent_activity_details_shares;dONl3vafS8+mxiKkiI8PHQ==",
            "x-li-track": '{"clientVersion":"","mpVersion":"","osName":"web","timezoneOffset":-4,"mpName":"voyager-web","displayDensity":2,"displayWidth":2880,"displayHeight":1800}',
            "x-restli-protocol-version": "2.0.0",
        }
        if is_company:
            url = (
                "https://www.linkedin.com/voyager/api/graphql"
                "?includeWebMetadata=true"
                f"&variables=(count:10,start:0,moduleKey:ORGANIZATION_MEMBER_FEED_DESKTOP,organizationalPageUrn:urn%3Ali%3Afsd_organizationalPage%3A{li_profile_id})"
                "&queryId=voyagerFeedDashOrganizationalPageUpdates.ec233104c90f05569937d88705b4efc6"
            )
            response = requests.get(url, headers=headers)
        else:
            url = (
                "https://www.linkedin.com/voyager/api/graphql"
                "?includeWebMetadata=true"
                f"&variables=(count:20,start:0,profileUrn:urn%3Ali%3Afsd_profile%3A{li_profile_id})"
                "&queryId=voyagerFeedDashProfileUpdates.140fe34f4cf20ae185d73b7a142f6882"
            )
            response = requests.get(url, headers=headers)
        response = json.loads(response.text)

        if is_company:
            recent_posts_ids = response["data"]["data"][
                "feedDashOrganizationalPageUpdatesByOrganizationalPageRelevanceFeed"
            ]["*elements"]
        else:
            recent_posts_ids = response["data"]["data"][
                "feedDashProfileUpdatesByMemberShareFeed"
            ]["*elements"]

        posts_details = response["included"]
        posts_ids_list = []
        for x in recent_posts_ids:
            post_id = re.search(r"(urn:li:activity:)\d*", x)
            if post_id:
                posts_ids_list.append(post_id.group())

        temp_array = []
        for y in posts_details:
            if "*socialDetail" in y and "commentary" in y and "actor" in y:
                try:
                    post_id = re.search(
                        r"(urn:li:activity:|urn:li:ugcPost:)\d*", y["*socialDetail"]
                    )
                    if not post_id:
                        continue
                    post_id = post_id.group()

                    if (
                        "subDescription" in y["actor"]
                        and y["actor"]["subDescription"]
                    ):
                        date_str = y["actor"]["subDescription"]["accessibilityText"].replace(
                            "Edited", ""
                        )
                    elif (
                        "additionalContents" in y
                        and y["additionalContents"][0]["creationStatusComponent"]["text"][
                            "text"
                        ]
                    ):
                        date_str = y["additionalContents"][0]["creationStatusComponent"][
                            "text"
                        ]["text"]
                    else:
                        date_str = y["additionalContents"][0]["text"]["text"].replace(
                            "Edited", ""
                        )

                    date = get_date(date_str)
                    duration = now - date
                    duration_in_days = duration.days

                    if (
                        y["commentary"] is not None
                        and y["commentary"]["text"] is not None
                    ):
                        text = (
                            y["commentary"]["text"]["text"]
                            .replace("\n", " ")
                            .replace("\\\\n", "")
                            .replace("\\n", "")
                        )

                        is_repost = False
                        is_reshare = False

                        profile_full_name = (
                            y["actor"]["name"]["attributesV2"][0]["detailData"][
                                "*companyName"
                            ]
                            if is_company
                            else y["actor"]["name"]["attributesV2"][0]["detailData"][
                                "*profileFullName"
                            ]
                        )
                        if str(li_profile_id) not in str(profile_full_name):
                            is_repost = True
                            text = "This is a repost. " + text
                            print("this is a repost")
                        if "*resharedUpdate" in y:
                            is_reshare = True
                            text = "This is a reshare. " + text
                            print("this is a reshare")

                        temp_array.append(
                            {
                                "post_url": f"https://www.linkedin.com/feed/update/{post_id}",
                                "text": text,
                                "date_submitted": date,
                                "is_repost": is_repost,
                                "is_reshare": is_reshare,
                            }
                        )
                except Exception as e:
                    print("Error matching string in fetched post", e)
                    continue

        if len(temp_array) < 1:
            return None
        else:
            sorted_array = sorted(
                temp_array, key=itemgetter("date_submitted"), reverse=True
            )
            return sorted_array[:3]
    except Exception as e:
        print(e)
        print("Error fetching data from LinkedIn")
        return None


def get_profile_details(profile_name, cookie, token, is_company):
    try:
        headers = {
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
            "Cookie": cookie,
            "Csrf-token": token,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-li-lang": "en_US",
            "x-li-page-instance": "urn:li:page:d_flagship3_profile_view_base_recent_activity_details_shares;dONl3vafS8+mxiKkiI8PHQ==",
            "x-li-track": '{"clientVersion":"","mpVersion":"","osName":"web","timezoneOffset":-4,"mpName":"voyager-web","displayDensity":2,"displayWidth":2880,"displayHeight":1800}',
            "x-restli-protocol-version": "2.0.0",
        }
        if is_company:
            url = (
                "https://www.linkedin.com/voyager/api/graphql"
                f"?variables=(universalName:{profile_name})"
                "&queryId=voyagerOrganizationDashCompanies.54122aa9bd2308dc9bf3bc525e2efb2e"
            )
        else:
            url = (
                "https://www.linkedin.com/voyager/api/graphql"
                f"?variables=(vanityName:{profile_name})"
                "&queryId=voyagerIdentityDashProfiles.2531a1a7d1d5530ad1834e0012bf7d50"
            )

        response = requests.get(url, headers=headers)
        response = json.loads(response.text)

        first_name = None
        last_name = None
        headline = None
        profile_photo_url = None
        data = response["included"]

        if not data:
            if "data" in response and "errors" in response["data"]:
                return response["data"]["errors"][0]["message"]

        if is_company:
            for item in data:
                if "universalName" in item and item["universalName"] == profile_name:
                    if "logoResolutionResult" in item:
                        root_url = item["logoResolutionResult"]["vectorImage"][
                            "rootUrl"
                        ]
                        file_identifying_url = item["logoResolutionResult"][
                            "vectorImage"
                        ]["artifacts"][0]["fileIdentifyingUrlPathSegment"]
                        profile_photo_url = root_url + file_identifying_url
                    if "tagline" in item:
                        headline = item["tagline"]
                    if "name" in item:
                        first_name = item["name"]
        else:
            for item in data:
                if "headline" in item:
                    if (
                        "publicIdentifier" in item
                        and item["publicIdentifier"] != profile_name
                    ):
                        pass
                    else:
                        headline = item["headline"]
                if "multiLocaleFirstName" in item:
                    first_name = item["multiLocaleFirstName"][0]["value"]
                if "multiLocaleLastName" in item:
                    last_name = item["multiLocaleLastName"][0]["value"]
                if "profilePicture" in item:
                    root = item["profilePicture"]["displayImageReferenceResolutionResult"]
                    if root is not None:
                        root_url = root["vectorImage"]["rootUrl"]
                        file_identifying_url = root["vectorImage"]["artifacts"][0][
                            "fileIdentifyingUrlPathSegment"
                        ]
                        profile_photo_url = root_url + file_identifying_url

        return {
            "first_name": first_name,
            "last_name": last_name,
            "headline": headline,
            "profile_photo_url": profile_photo_url,
        }

    except Exception as e:
        print(e)
        return None


def scrap_it(url, li_at, proxy_country):
    try:
        body = {
            "url": url,
            "js_rendering": True,
            "screenshot": True,
            "block_resources": False,
            "extract_emails": False,
            "block_ads": False,
            "headers": {"cookie": li_at},
            "proxy_type": "residential",
            "proxy_country": proxy_country,
        }

        # NOTE: API key moved to environment variable
        api_key = os.environ.get("SCRAPE_IT_API_KEY", "")

        response = requests.post(
            "https://api.scrape-it.cloud/scrape",
            json=body,
            headers={"x-api-key": api_key},
        )
        print(response)
        status = response.json()["status"]
        if status != "ok":
            print("invalid cookie")
            return "invalid cookie"
        html_str = response.json()["scrapingResult"]["content"]

        soup = BeautifulSoup(html_str, "html.parser")
        post_divs = soup.select(".feed-shared-update-v2")
        data = []
        for post_div in post_divs:
            data_urn = post_div["data-urn"]
            actor = post_div.find("div", {"class": "update-components-actor__container"})
            post_date = actor.find(
                "span", {"class": "update-components-actor__sub-description"}
            ).text
            date = get_date(post_date)
            duration = now - date
            duration_in_days = duration.days
            if duration_in_days > 30:
                continue
            post_url = "https://www.linkedin.com/feed/update/" + data_urn
            post_contents = post_div.findAll(
                "div", {"class": "update-components-text"}
            )
            post = ""
            for post_content in post_contents:
                text = post_content.text
                post += " " + text
            data.append(
                {
                    "post_url": post_url,
                    "text": post,
                    "date_submitted": date,
                }
            )
        sorted_array = sorted(data, key=itemgetter("date_submitted"), reverse=True)
        return sorted_array[:3]
    except Exception as e:
        print(e)
        return None


def remove_emojis(text):
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"
        "\U0001FA70-\U0001FAFF"
        "\U0001F1F2-\U0001F1F4"
        "\U0001F1E6-\U0001F1FF"
        "]+",
        flags=re.UNICODE,
    )
    return emoji_pattern.sub(r"", text)


def admin_email():
    values = {
        "From": "hello@engage-ai.co",
        "To": os.environ.get("ADMIN_EMAIL", "hello@engage-ai.co"),
        "TemplateAlias": "scraping-success",
        "TemplateModel": {},
    }

    server_token = os.environ.get("POSTMARK_SERVER_TOKEN", "")

    headers = {
        "X-Postmark-Server-Token": server_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    contact_request = requests.post(
        "https://api.postmarkapp.com/email/withTemplate",
        data=json.dumps(values),
        headers=headers,
    )
    return {"status": contact_request.status_code}

