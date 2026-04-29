import os
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from googleapiclient.discovery import build
from google.oauth2 import service_account


HUBSPOT_API_KEY = os.getenv("HUBSPOT_ACCESS_TOKEN")

BASE_URL = "https://api.hubapi.com"

HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json"
}

SCOPES = ["https://www.googleapis.com/auth/doubleclicksearch"]

ADVERTISER_ID = "1354960311"
ENGINE_ACCOUNT_ID = "9620336649"
QUALIFIED_LEAD_FLOODLIGHT_ID = "14378257"
CLOSED_WON_FLOODLIGHT_ID = "14543866"
UPLOAD_LOG_FILE = "uploaded_conversions.json"
QL_BUCKET_CONFIG = {
    "0-999": {
        "floodlight_id": "460685756",
        "value": 0,
    },
    "1000-2999": {
        "floodlight_id": "461093029",
        "value": 500,
    },
    "3000-9999": {
        "floodlight_id": "461099151",
        "value": 1500,
    },
    "10000-19999": {
        "floodlight_id": "460927983",
        "value": 3000,
    },
    "20000-49999": {
        "floodlight_id": "461095948",
        "value": 6000,
    },
    "50000+": {
        "floodlight_id": "460685762",
        "value": 10000,
    },
}


def get_ql_bucket_config(props):
    raw_bucket = props.get("actual_amount_bucket") or props.get("budget_bucket")

    bucket_normalization = {
        "below_1000": "0-999",
        "below-1000": "0-999",
        "0-999": "0-999",
        "1000-2999": "1000-2999",
        "3000-9999": "3000-9999",
        "10000-19999": "10000-19999",
        "20000-29999": "20000-49999",
        "20000-49999": "20000-49999",
        "30000-49999": "20000-49999",
        "50000+": "50000+",
        "over-50000": "50000+",
        "over_50000": "50000+",
    }

    if not raw_bucket:
        print("No QL bucket found.")
        return None, None

    raw_bucket = str(raw_bucket).strip()
    bucket = bucket_normalization.get(raw_bucket)

    if not bucket:
        print(f"Unrecognized QL bucket raw={repr(raw_bucket)}.")
        return None, None

    return bucket, QL_BUCKET_CONFIG[bucket]

def load_upload_log():
    if not os.path.exists(UPLOAD_LOG_FILE):
        return {
            "qualified_leads": [],
            "closed_won": []
        }

    with open(UPLOAD_LOG_FILE, "r") as f:
        return json.load(f)


def save_upload_log(upload_log):
    with open(UPLOAD_LOG_FILE, "w") as f:
        json.dump(upload_log, f, indent=2)


def get_sa360_service():
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

    creds = service_account.Credentials.from_service_account_file(
        service_account_file,
        scopes=SCOPES
    )

    service = build("doubleclicksearch", "v2", credentials=creds)
    return service


def get_first_deals():
    url = f"{BASE_URL}/crm/v3/objects/deals/search"

    local_tz = ZoneInfo("America/Chicago")
    end_date = datetime.now(local_tz).date() - timedelta(days=1)
    start_date = end_date - timedelta(days=1)

    start = int(
        datetime.combine(
            start_date,
            datetime.min.time(),
            tzinfo=local_tz
        ).timestamp() * 1000
    )

    end = int(
        datetime.combine(
            end_date,
            datetime.max.time(),
            tzinfo=local_tz
        ).timestamp() * 1000
    )

    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "order_sequence",
                        "operator": "LT",
                        "value": "2"
                    },
                    {
                        "propertyName": "paid_ad_bid_strategy",
                        "operator": "HAS_PROPERTY"
                    },
                    {
                        "propertyName": "createdate",
                        "operator": "GTE",
                        "value": str(start)
                    },
                    {
                        "propertyName": "createdate",
                        "operator": "LTE",
                        "value": str(end)
                    }
                ]
            },
            {
                "filters": [
                    {
                        "propertyName": "order_sequence",
                        "operator": "NOT_HAS_PROPERTY"
                    },
                    {
                        "propertyName": "paid_ad_bid_strategy",
                        "operator": "HAS_PROPERTY"
                    },
                    {
                        "propertyName": "createdate",
                        "operator": "GTE",
                        "value": str(start)
                    },
                    {
                        "propertyName": "createdate",
                        "operator": "LTE",
                        "value": str(end)
                    }
                ]
            }
        ],
        "properties": [
            "createdate",
            "closedate",
            "hs_closed_amount",
            "dealstage",
            "actual_amount_bucket",
            "budget_bucket",
            "paid_ad_bid_strategy"
        ],
        "limit": 100
    }

    all_results = []
    after = None
    page = 1
    total = None

    while True:
        if after:
            payload["after"] = after
        elif "after" in payload:
            del payload["after"]

        response = requests.post(url, headers=HEADERS, json=payload)

        print(f"DEAL SEARCH PAGE {page} STATUS:", response.status_code)
        response.raise_for_status()

        data = response.json()

        if total is None:
            total = data.get("total")

        results = data.get("results", [])
        all_results.extend(results)

        print(f"Fetched {len(results)} deals on page {page}; running total: {len(all_results)} of {total}")

        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

        page += 1

    print(f"DEAL SEARCH COMPLETE: {len(all_results)} deals fetched out of {total}")
    return all_results


def get_contact_for_deal(deal_id):
    import time

    url = f"{BASE_URL}/crm/v4/objects/deals/{deal_id}/associations/contacts"

    for attempt in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            results = response.json().get("results", [])

            if not results:
                return None

            contact_id = results[0]["toObjectId"]

            contact_url = f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}?properties=hs_google_click_id,email"
            contact_res = requests.get(contact_url, headers=HEADERS, timeout=30)
            contact_res.raise_for_status()

            return contact_res.json()

        except requests.exceptions.RequestException as e:
            print(f"HubSpot contact lookup failed for deal {deal_id} on attempt {attempt + 1}: {e}")

            if attempt < 2:
                time.sleep(3)
            else:
                print(f"Skipping deal {deal_id} after repeated HubSpot lookup failures.")
                return None

def upload_qualified_lead(service, click_id, conversion_time, conversion_id, floodlight_id, ql_value):
    body = {
        "conversion": [
            {
                "clickId": click_id,
                "conversionId": conversion_id,
                "conversionTimestamp": conversion_time,
                "segmentationType": "FLOODLIGHT",
                "segmentationId": floodlight_id,
                "type": "TRANSACTION",
                "revenueMicros": str(int(round(float(ql_value) * 1_000_000))),
                "currencyCode": "USD"
            }
        ]
    }

    try:
        response = service.conversion().insert(body=body).execute()

        print("\nSA360 RESPONSE:")
        print(json.dumps(response, indent=2))
        return True

    except Exception as e:
        error_str = str(e)

        if "conversion ID is already specified" in error_str:
            print(f"\nAlready uploaded (safe to skip): {conversion_id}")
            return True

        print(f"\nQL upload failed for {conversion_id}: {e}")
        return False


def upload_closed_won(service, click_id, conversion_time, conversion_id, revenue):
    body = {
        "conversion": [
            {
                "clickId": click_id,
                "conversionId": conversion_id,
                "conversionTimestamp": conversion_time,
                "segmentationType": "FLOODLIGHT",
                "segmentationId": CLOSED_WON_FLOODLIGHT_ID,
                "type": "TRANSACTION",
                "revenueMicros": str(int(round(float(revenue) * 1_000_000))),
                "currencyCode": "USD"
            }
        ]
    }

    try:
        response = service.conversion().insert(body=body).execute()

        print("\nCLOSED WON SA360 RESPONSE:")
        print(json.dumps(response, indent=2))
        return True

    except Exception as e:
        error_str = str(e)

        if "conversion ID is already specified" in error_str:
            print(f"\nAlready uploaded (safe to skip): {conversion_id}")
            return True

        print(f"\nQL upload failed for {conversion_id}: {e}")
        return False


def run(service):
    deals = get_first_deals()
    upload_log = load_upload_log()

    print("\n=== QUALIFIED LEADS (First Deal Created) ===\n")

    for deal in deals:
        deal_id = deal["id"]

        if deal_id in upload_log["qualified_leads"]:
            print(f"Skipping QL (already uploaded): {deal_id}")
            continue

        props = deal["properties"]

        contact = get_contact_for_deal(deal_id)
        if not contact:
            print(f"Skipping QL (no associated contact): {deal_id}")
            continue

        gclid = contact["properties"].get("hs_google_click_id")
        if not gclid:
            print(f"Skipping QL (no GCLID): {deal_id} | Contact: {contact['properties'].get('email')}")
            continue

        conversion_time_str = props.get("createdate")
        conversion_time = int(
            datetime.fromisoformat(
                conversion_time_str.replace("Z", "+00:00")
            ).timestamp() * 1000
        )

        ql_bucket, ql_bucket_config = get_ql_bucket_config(props)

        if not ql_bucket_config:
            print(f"Skipping QL (no bucket): {deal_id}")
            continue

        sa360_row = {
            "clickId": gclid,
            "conversionName": f"Qualified Lead - {ql_bucket}",
            "conversionTime": conversion_time,
            "conversionValue": ql_bucket_config["value"],
            "floodlightId": ql_bucket_config["floodlight_id"],
            "conversionId": f"hubspot-deal-{deal_id}-ql"
        }

        print("QL SA360 PAYLOAD:")
        print(json.dumps(sa360_row, indent=2))

        success = upload_qualified_lead(
            service=service,
            click_id=gclid,
            conversion_time=conversion_time,
            conversion_id=f"hubspot-deal-{deal_id}-ql",
            floodlight_id=ql_bucket_config["floodlight_id"],
            ql_value=ql_bucket_config["value"]
        )

        if success:
            upload_log["qualified_leads"].append(deal_id)
            save_upload_log(upload_log)

    print("\n=== CLOSED WON DEALS ===\n")

    for deal in deals:
        deal_id = deal["id"]

        if deal_id in upload_log["closed_won"]:
            print(f"Skipping Closed Won (already uploaded): {deal_id}")
            continue

        props = deal["properties"]

        if props.get("dealstage") != "closedwon":
            print(f"Skipping Closed Won (not closedwon): {deal_id} | Stage: {props.get('dealstage')}")
            continue

        contact = get_contact_for_deal(deal_id)
        if not contact:
            print(f"Skipping Closed Won (no associated contact): {deal_id}")
            continue

        gclid = contact["properties"].get("hs_google_click_id")
        if not gclid:
            print(f"Skipping Closed Won (no GCLID): {deal_id} | Contact: {contact['properties'].get('email')}")
            continue

        conversion_time_str = props.get("closedate")
        conversion_time = int(
            datetime.fromisoformat(
                conversion_time_str.replace("Z", "+00:00")
            ).timestamp() * 1000
        )

        revenue = float(props.get("hs_closed_amount") or 0)

        print("CLOSED WON SA360 PAYLOAD:")
        print({
            "clickId": gclid,
            "conversionTime": conversion_time,
            "revenue": revenue
        })

        success = upload_closed_won(
            service=service,
            click_id=gclid,
            conversion_time=conversion_time,
            conversion_id=f"hubspot-deal-{deal_id}-cw",
            revenue=revenue
        )

        if success:
            upload_log["closed_won"].append(deal_id)
            save_upload_log(upload_log)


if __name__ == "__main__":
    if not HUBSPOT_API_KEY:
        raise ValueError("HUBSPOT_ACCESS_TOKEN environment variable is not set.")

    service = get_sa360_service()
    print("SA360 AUTH SUCCESSFUL")
    run(service)