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
    "0-999": {"floodlight_id": "460685756", "value": 1},
    "1000-2999": {"floodlight_id": "461093029", "value": 150},
    "3000-9999": {"floodlight_id": "461099151", "value": 1500},
    "10000-19999": {"floodlight_id": "460927983", "value": 3000},
    "20000-49999": {"floodlight_id": "461095948", "value": 6000},
    "50000+": {"floodlight_id": "460685762", "value": 10000},
}


ALLOWED_QL_FLOODLIGHT_IDS = {
    "460685756",
    "461093029",
    "461099151",
    "460927983",
    "461095948",
    "460685762",
}


def get_ql_bucket_config(props):
    raw_bucket = props.get("actual_amount_bucket") or props.get("budget_bucket")


    bucket_normalization = {
        "below_1000": "0-999",
        "below-1000": "0-999",
        "below 1000": "0-999",
        "0-999": "0-999",
        "0 - 999": "0-999",
        "1000-2999": "1000-2999",
        "1000 - 2999": "1000-2999",
        "3000-9999": "3000-9999",
        "3000 - 9999": "3000-9999",
        "10000-19999": "10000-19999",
        "10000 - 19999": "10000-19999",
        "20000-29999": "20000-49999",
        "20000-49999": "20000-49999",
        "20000 - 49999": "20000-49999",
        "30000-49999": "20000-49999",
        "50000+": "50000+",
        "50000 +": "50000+",
        "over-50000": "50000+",
        "over_50000": "50000+",
        "over 50000": "50000+",
    }


    def bucket_from_numeric_amount(amount):
        if amount >= 50000:
            return "50000+"
        elif amount >= 20000:
            return "20000-49999"
        elif amount >= 10000:
            return "10000-19999"
        elif amount >= 3000:
            return "3000-9999"
        elif amount >= 1000:
            return "1000-2999"
        else:
            return "0-999"


    if raw_bucket in (None, ""):
        fallback_amount = props.get("hs_closed_amount") or props.get("amount")
        if fallback_amount not in (None, ""):
            try:
                bucket = bucket_from_numeric_amount(float(fallback_amount))
                print(
                    f"No explicit QL bucket found. "
                    f"Falling back from revenue amount raw={repr(fallback_amount)} -> bucket={bucket}"
                )
                return bucket, QL_BUCKET_CONFIG[bucket]
            except Exception:
                pass


        print("No QL bucket found and no numeric fallback available.")
        return None, None


    raw_bucket = str(raw_bucket).strip()
    normalized_key = raw_bucket.lower().replace("$", "").replace(",", "")
    bucket = bucket_normalization.get(raw_bucket) or bucket_normalization.get(normalized_key)


    if not bucket:
        try:
            numeric_value = float(normalized_key)
            bucket = bucket_from_numeric_amount(numeric_value)
            print(
                f"Unrecognized QL bucket raw={repr(raw_bucket)}. "
                f"Parsed numeric fallback -> bucket={bucket}"
            )
            return bucket, QL_BUCKET_CONFIG[bucket]
        except Exception:
            fallback_amount = props.get("hs_closed_amount") or props.get("amount")
            if fallback_amount not in (None, ""):
                try:
                    bucket = bucket_from_numeric_amount(float(fallback_amount))
                    print(
                        f"Unrecognized QL bucket raw={repr(raw_bucket)}. "
                        f"Falling back from revenue amount raw={repr(fallback_amount)} -> bucket={bucket}"
                    )
                    return bucket, QL_BUCKET_CONFIG[bucket]
                except Exception:
                    pass


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


def get_contact_for_deal(deal_id):
    url = f"{BASE_URL}/crm/v4/objects/deals/{deal_id}/associations/contacts"

    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()

    results = response.json().get("results", [])

    if not results:
        print(f"CONTACT DEBUG | deal_id={deal_id} | associated_contacts=0 | gclid_found=False")
        return None

    print(f"CONTACT DEBUG | deal_id={deal_id} | associated_contacts={len(results)}")

    for association in results:
        contact_id = association["toObjectId"]

        contact_url = f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}?properties=hs_google_click_id,email"
        contact_res = requests.get(contact_url, headers=HEADERS)
        contact_res.raise_for_status()

        contact = contact_res.json()
        props = contact.get("properties", {})
        gclid = props.get("hs_google_click_id")
        email = props.get("email")

        print(
            f"CONTACT DEBUG | deal_id={deal_id} | "
            f"contact_id={contact_id} | email={email} | has_gclid={bool(gclid)}"
        )

        if gclid:
            print(
                f"CONTACT DEBUG | deal_id={deal_id} | "
                f"selected_contact_id={contact_id} | selected_email={email}"
            )
            return contact

    print(f"CONTACT DEBUG | deal_id={deal_id} | gclid_found=False")
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
        audit_ql_response_floodlight(response, conversion_id, floodlight_id)
        return "uploaded"


    except Exception as e:
        error_str = str(e)
        error_str_lower = error_str.lower()


        if "conversion id is already specified" in error_str_lower:
            print(f"\nAlready uploaded (safe to skip): {conversion_id}")
            return "already_uploaded"


        if "click id" in error_str_lower and "is not found" in error_str_lower:
            print(f"\nSkipping QL (click ID not found in SA360): {conversion_id}")
            return "click_not_found"


        if "conversion timestamp is before click timestamp" in error_str_lower:
            print(f"\nSkipping QL (conversion happened before recorded click): {conversion_id}")
            return "before_click"


        print(f"\nQL upload failed for {conversion_id}: {e}")
        return "failed"

def update_qualified_lead_value(service, click_id, conversion_time, conversion_id, revenue_dollars):
    body = {
        "conversion": [
            {
                "clickId": click_id,
                "conversionId": conversion_id,
                "conversionTimestamp": conversion_time,
                "type": "TRANSACTION",
                "revenueMicros": str(int(round(float(revenue_dollars) * 1_000_000))),
                "currencyCode": "USD"
            }
        ]
    }


    try:
        response = service.conversion().update(body=body).execute()


        print("\nUPDATED QL SA360 RESPONSE:")
        print(json.dumps(response, indent=2))
        return True


    except Exception as e:
        print(f"\nQL update failed for {conversion_id}: {e}")
        return False
def get_ql_value_from_revenue(revenue):
    revenue = float(revenue or 0)


    if revenue >= 50000:
        return 10000
    elif revenue >= 20000:
        return 6000
    elif revenue >= 10000:
        return 3000
    elif revenue >= 3000:
        return 1500
    elif revenue >= 1000:
        return 150
    else:
        return 1

def normalize_segmentation_id(segmentation_id):
    if segmentation_id is None:
        return None


    segmentation_id = str(segmentation_id)


    for allowed_id in ALLOWED_QL_FLOODLIGHT_IDS:
        if segmentation_id.endswith(allowed_id):
            return allowed_id


    if len(segmentation_id) >= 9:
        return segmentation_id[-9:]


    return segmentation_id

def audit_ql_response_floodlight(response, expected_conversion_id, expected_floodlight_id):
    conversions = response.get("conversion", [])


    if not conversions:
        print(
            f"QL AUDIT WARNING | conversion_id={expected_conversion_id} "
            f"| expected_floodlight_id={expected_floodlight_id} "
            f"| no conversion objects returned"
        )
        return None


    conv = conversions[0]
    segmentation_id_raw = conv.get("segmentationId")
    segmentation_name = conv.get("segmentationName")
    segmentation_id_short = normalize_segmentation_id(segmentation_id_raw)


    if segmentation_id_short != str(expected_floodlight_id):
        print(
            f"QL AUDIT WARNING | conversion_id={expected_conversion_id} "
            f"| expected_floodlight_id={expected_floodlight_id} "
            f"| returned_segmentation_id={segmentation_id_raw} "
            f"| returned_segmentation_id_short={segmentation_id_short} "
            f"| returned_segmentation_name={segmentation_name}"
        )
    else:
        print(
            f"QL AUDIT OK | conversion_id={expected_conversion_id} "
            f"| expected_floodlight_id={expected_floodlight_id} "
            f"| returned_segmentation_id_short={segmentation_id_short} "
            f"| returned_segmentation_name={segmentation_name}"
        )


    return segmentation_id_short

def get_first_deals(lookback_days=1):
    url = f"{BASE_URL}/crm/v3/objects/deals/search"

    local_tz = ZoneInfo("America/Chicago")
    end_date = datetime.now(local_tz).date() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days - 1)

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
            "amount",
            "dealstage",
            "order_sequence",
            "actual_amount_bucket",
            "budget_bucket"
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

def get_closed_won_deals(lookback_days=1):
    url = f"{BASE_URL}/crm/v3/objects/deals/search"


    local_tz = ZoneInfo("America/Chicago")
    end_date = datetime.now(local_tz).date() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days - 1)


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
                        "propertyName": "dealstage",
                        "operator": "EQ",
                        "value": "closedwon"
                    },
                    {
                        "propertyName": "paid_ad_bid_strategy",
                        "operator": "HAS_PROPERTY"
                    },
                    {
                        "propertyName": "closedate",
                        "operator": "GTE",
                        "value": str(start)
                    },
                    {
                        "propertyName": "closedate",
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
            "amount",
            "dealstage",
            "order_sequence",
            "actual_amount_bucket",
            "budget_bucket"
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


        print(f"CLOSED WON SEARCH PAGE {page} STATUS:", response.status_code)
        response.raise_for_status()


        data = response.json()


        if total is None:
            total = data.get("total")


        results = data.get("results", [])
        all_results.extend(results)


        print(f"Fetched {len(results)} closed won deals on page {page}; running total: {len(all_results)} of {total}")


        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break


        page += 1


    print(f"CLOSED WON SEARCH COMPLETE: {len(all_results)} deals fetched out of {total}")
    return all_results

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
        return "uploaded"


    except Exception as e:
        error_str = str(e)
        error_str_lower = error_str.lower()


        if "conversion id is already specified" in error_str_lower:
            print(f"\nAlready uploaded Closed Won (safe to skip): {conversion_id}")
            return "already_uploaded"


        if "click id" in error_str_lower and "is not found" in error_str_lower:
            print(f"\nSkipping Closed Won (click ID not found in SA360): {conversion_id}")
            return "click_not_found"


        if "conversion timestamp is before click timestamp" in error_str_lower:
            print(f"\nSkipping Closed Won (conversion happened before recorded click): {conversion_id}")
            return "before_click"


        print(f"\nClosed Won upload failed for {conversion_id}: {e}")
        return "failed"
    

def run(service):
    lookback_days = int(os.getenv("LOOKBACK_DAYS", "1"))
    print(f"RUNNING WITH LOOKBACK_DAYS={lookback_days}")


    deals = get_first_deals(lookback_days=lookback_days)
    closed_won_deals = get_closed_won_deals(lookback_days=lookback_days)
    upload_log = load_upload_log()


    ql_uploaded_count = 0
    ql_already_uploaded_count = 0
    ql_skipped_no_contact_count = 0
    ql_skipped_no_gclid_count = 0
    ql_skipped_no_bucket_count = 0
    ql_skipped_click_not_found_count = 0
    ql_skipped_before_click_count = 0
    ql_failed_count = 0


    cw_uploaded_count = 0
    cw_already_uploaded_count = 0
    cw_skipped_no_contact_count = 0
    cw_skipped_no_gclid_count = 0
    cw_skipped_no_closedate_count = 0
    cw_skipped_click_not_found_count = 0
    cw_skipped_before_click_count = 0
    cw_failed_count = 0

    print("\n=== QUALIFIED LEADS (First Deal Created) ===\n")


    for deal in deals:
        deal_id = deal["id"]


        if deal_id in upload_log["qualified_leads"]:
            print(f"Skipping QL (already uploaded): {deal_id}")
            ql_already_uploaded_count += 1
            continue


        props = deal["properties"]


        contact = get_contact_for_deal(deal_id)
        if not contact:
            print(f"Skipping QL (no associated contact): {deal_id}")
            ql_skipped_no_contact_count += 1
            continue


        gclid = contact["properties"].get("hs_google_click_id")
        if not gclid:
            print(f"Skipping QL (no GCLID): {deal_id} | Contact: {contact['properties'].get('email')}")
            ql_skipped_no_gclid_count += 1
            continue


        ql_bucket, ql_bucket_config = get_ql_bucket_config(props)
        if not ql_bucket_config:
            print(f"Skipping QL (no valid bucket): {deal_id}")
            ql_skipped_no_bucket_count += 1
            continue


        conversion_time_str = props.get("createdate")
        conversion_time = int(
            datetime.fromisoformat(
                conversion_time_str.replace("Z", "+00:00")
            ).timestamp() * 1000
        )


        sa360_row = {
            "deal_id": deal_id,
            "clickId": gclid,
            "raw_bucket": props.get("actual_amount_bucket") or props.get("budget_bucket"),
            "fallback_amount": props.get("hs_closed_amount") or props.get("amount"),
            "conversionName": f"Qualified Lead - {ql_bucket}",
            "conversionTime": conversion_time,
            "conversionValue": ql_bucket_config["value"],
            "floodlightId": ql_bucket_config["floodlight_id"],
            "conversionId": f"hubspot-deal-{deal_id}-ql"
        }


        print("QL SA360 PAYLOAD:")
        print(json.dumps(sa360_row, indent=2))


        result = upload_qualified_lead(
            service=service,
            click_id=gclid,
            conversion_time=conversion_time,
            conversion_id=f"hubspot-deal-{deal_id}-ql",
            floodlight_id=ql_bucket_config["floodlight_id"],
            ql_value=ql_bucket_config["value"]
        )


        if result == "uploaded":
            ql_uploaded_count += 1
            upload_log["qualified_leads"].append(deal_id)
            save_upload_log(upload_log)
        elif result == "already_uploaded":
            ql_already_uploaded_count += 1
        elif result == "click_not_found":
            ql_skipped_click_not_found_count += 1
        elif result == "before_click":
            ql_skipped_before_click_count += 1
        else:
            ql_failed_count += 1

    print("\n=== CLOSED WON DEALS ===\n")


    for deal in closed_won_deals:
        deal_id = deal["id"]


        if deal_id in upload_log["closed_won"]:
            print(f"Skipping Closed Won (already uploaded): {deal_id}")
            cw_already_uploaded_count += 1
            continue


        props = deal["properties"]


        contact = get_contact_for_deal(deal_id)
        if not contact:
            print(f"Skipping Closed Won (no associated contact): {deal_id}")
            cw_skipped_no_contact_count += 1
            continue


        gclid = contact["properties"].get("hs_google_click_id")
        if not gclid:
            print(f"Skipping Closed Won (no GCLID): {deal_id} | Contact: {contact['properties'].get('email')}")
            cw_skipped_no_gclid_count += 1
            continue


        conversion_time_str = props.get("closedate")
        if not conversion_time_str:
            print(f"Skipping Closed Won (no closedate): {deal_id}")
            cw_skipped_no_closedate_count += 1
            continue


        conversion_time = int(
            datetime.fromisoformat(
                conversion_time_str.replace("Z", "+00:00")
            ).timestamp() * 1000
        )


        revenue = float(props.get("hs_closed_amount") or props.get("amount") or 0)


        print("CLOSED WON SA360 PAYLOAD:")
        print({
            "deal_id": deal_id,
            "clickId": gclid,
            "conversionTime": conversion_time,
            "revenue": revenue
        })


        result = upload_closed_won(
            service=service,
            click_id=gclid,
            conversion_time=conversion_time,
            conversion_id=f"hubspot-deal-{deal_id}-cw",
            revenue=revenue
        )


        if result == "uploaded":
            cw_uploaded_count += 1
            upload_log["closed_won"].append(deal_id)
            save_upload_log(upload_log)
        elif result == "already_uploaded":
            cw_already_uploaded_count += 1
        elif result == "click_not_found":
            cw_skipped_click_not_found_count += 1
        elif result == "before_click":
            cw_skipped_before_click_count += 1
        else:
            cw_failed_count += 1

    print("\n=== DAILY RUN SUMMARY ===")


    print("\nQUALIFIED LEADS")
    print(f"Uploaded successfully: {ql_uploaded_count}")
    print(f"Already uploaded: {ql_already_uploaded_count}")
    print(f"Skipped - no associated contact: {ql_skipped_no_contact_count}")
    print(f"Skipped - no GCLID: {ql_skipped_no_gclid_count}")
    print(f"Skipped - no valid bucket: {ql_skipped_no_bucket_count}")
    print(f"Skipped - click ID not found in SA360: {ql_skipped_click_not_found_count}")
    print(f"Skipped - conversion before click: {ql_skipped_before_click_count}")
    print(f"Failed: {ql_failed_count}")


    print("\nCLOSED WON")
    print(f"Uploaded successfully: {cw_uploaded_count}")
    print(f"Already uploaded: {cw_already_uploaded_count}")
    print(f"Skipped - no associated contact: {cw_skipped_no_contact_count}")
    print(f"Skipped - no GCLID: {cw_skipped_no_gclid_count}")
    print(f"Skipped - no closedate: {cw_skipped_no_closedate_count}")
    print(f"Skipped - click ID not found in SA360: {cw_skipped_click_not_found_count}")
    print(f"Skipped - conversion before click: {cw_skipped_before_click_count}")
    print(f"Failed: {cw_failed_count}")

def backfill_qualified_lead_values(service):
    lookback_days = int(os.getenv("LOOKBACK_DAYS", "60"))
    print(f"RUN_MODE=ql_backfill")
    print(f"BACKFILLING QL VALUES WITH LOOKBACK_DAYS={lookback_days}")


    deals = get_first_deals(lookback_days=lookback_days)


    updated_count = 0
    already_ok_count = 0
    skipped_no_contact_count = 0
    skipped_no_gclid_count = 0
    skipped_no_bucket_count = 0
    skipped_click_not_found_count = 0
    skipped_before_click_count = 0
    failed_count = 0


    for deal in deals:
        deal_id = deal["id"]
        props = deal["properties"]


        contact = get_contact_for_deal(deal_id)
        if not contact:
            print(f"Skipping QL backfill (no associated contact): {deal_id}")
            skipped_no_contact_count += 1
            continue


        gclid = contact["properties"].get("hs_google_click_id")
        if not gclid:
            print(f"Skipping QL backfill (no GCLID): {deal_id} | Contact: {contact['properties'].get('email')}")
            skipped_no_gclid_count += 1
            continue


        ql_bucket, ql_bucket_config = get_ql_bucket_config(props)
        if not ql_bucket_config:
            print(f"Skipping QL backfill (no valid bucket): {deal_id}")
            skipped_no_bucket_count += 1
            continue


        conversion_time_str = props.get("createdate")
        if not conversion_time_str:
            print(f"Skipping QL backfill (no createdate): {deal_id}")
            failed_count += 1
            continue


        conversion_time = int(
            datetime.fromisoformat(
                conversion_time_str.replace("Z", "+00:00")
            ).timestamp() * 1000
        )


        revenue_dollars = ql_bucket_config["value"]
        conversion_id = f"hubspot-deal-{deal_id}-ql"


        print("QL BACKFILL PAYLOAD:")
        print({
            "deal_id": deal_id,
            "clickId": gclid,
            "raw_bucket": props.get("actual_amount_bucket") or props.get("budget_bucket"),
            "fallback_amount": props.get("hs_closed_amount") or props.get("amount"),
            "conversionTime": conversion_time,
            "conversionValue": revenue_dollars,
            "conversionId": conversion_id
        })


        body = {
            "conversion": [
                {
                    "clickId": gclid,
                    "conversionId": conversion_id,
                    "conversionTimestamp": conversion_time,
                    "type": "TRANSACTION",
                    "revenueMicros": str(int(round(float(revenue_dollars) * 1_000_000))),
                    "currencyCode": "USD"
                }
            ]
        }


        try:
            response = service.conversion().update(body=body).execute()


            print("\nUPDATED QL SA360 RESPONSE:")
            print(json.dumps(response, indent=2))
            audit_ql_response_floodlight(
                response,
                conversion_id,
                ql_bucket_config["floodlight_id"]
            )
            updated_count += 1


        except Exception as e:
            error_str = str(e)
            error_str_lower = error_str.lower()


            if "click id" in error_str_lower and "is not found" in error_str_lower:
                print(f"\nSkipping QL backfill (click ID not found in SA360): {conversion_id}")
                skipped_click_not_found_count += 1
                continue


            if "conversion timestamp is before click timestamp" in error_str_lower:
                print(f"\nSkipping QL backfill (conversion happened before recorded click): {conversion_id}")
                skipped_before_click_count += 1
                continue


            if "advertiser conversion id" in error_str_lower and "is not found" in error_str_lower:
                print(f"\nSkipping QL backfill (conversion not previously uploaded): {conversion_id}")
                already_ok_count += 1
                continue


            print(f"\nQL backfill update failed for {conversion_id}: {e}")
            failed_count += 1


    print("\n=== QL BACKFILL SUMMARY ===")
    print(f"Updated successfully: {updated_count}")
    print(f"Skipped - conversion not previously uploaded: {already_ok_count}")
    print(f"Skipped - no associated contact: {skipped_no_contact_count}")
    print(f"Skipped - no GCLID: {skipped_no_gclid_count}")
    print(f"Skipped - no valid bucket: {skipped_no_bucket_count}")
    print(f"Skipped - click ID not found in SA360: {skipped_click_not_found_count}")
    print(f"Skipped - conversion before click: {skipped_before_click_count}")
    print(f"Failed: {failed_count}")

def catchup_insert_missing_qualified_leads(service):
    lookback_days = int(os.getenv("LOOKBACK_DAYS", "60"))
    print("RUN_MODE=ql_catchup_insert")
    print(f"CATCHING UP MISSING QL INSERTS WITH LOOKBACK_DAYS={lookback_days}")


    deals = get_first_deals(lookback_days=lookback_days)
    upload_log = load_upload_log()


    uploaded_count = 0
    already_uploaded_count = 0
    skipped_no_contact_count = 0
    skipped_no_gclid_count = 0
    skipped_no_bucket_count = 0
    skipped_click_not_found_count = 0
    skipped_before_click_count = 0
    failed_count = 0


    for deal in deals:
        deal_id = deal["id"]
        props = deal["properties"]


        if deal_id in upload_log["qualified_leads"]:
            print(f"Skipping QL catchup (already in upload log): {deal_id}")
            already_uploaded_count += 1
            continue


        contact = get_contact_for_deal(deal_id)
        if not contact:
            print(f"Skipping QL catchup (no associated contact): {deal_id}")
            skipped_no_contact_count += 1
            continue


        gclid = contact["properties"].get("hs_google_click_id")
        if not gclid:
            print(f"Skipping QL catchup (no GCLID): {deal_id} | Contact: {contact['properties'].get('email')}")
            skipped_no_gclid_count += 1
            continue


        ql_bucket, ql_bucket_config = get_ql_bucket_config(props)
        if not ql_bucket_config:
            print(f"Skipping QL catchup (no valid bucket): {deal_id}")
            skipped_no_bucket_count += 1
            continue


        conversion_time_str = props.get("createdate")
        if not conversion_time_str:
            print(f"Skipping QL catchup (no createdate): {deal_id}")
            failed_count += 1
            continue


        conversion_time = int(
            datetime.fromisoformat(
                conversion_time_str.replace("Z", "+00:00")
            ).timestamp() * 1000
        )


        sa360_row = {
            "deal_id": deal_id,
            "clickId": gclid,
            "raw_bucket": props.get("actual_amount_bucket") or props.get("budget_bucket"),
            "fallback_amount": props.get("hs_closed_amount") or props.get("amount"),
            "conversionName": f"Qualified Lead - {ql_bucket}",
            "conversionTime": conversion_time,
            "conversionValue": ql_bucket_config["value"],
            "floodlightId": ql_bucket_config["floodlight_id"],
            "conversionId": f"hubspot-deal-{deal_id}-ql"
        }


        print("QL CATCHUP SA360 PAYLOAD:")
        print(json.dumps(sa360_row, indent=2))


        result = upload_qualified_lead(
            service=service,
            click_id=gclid,
            conversion_time=conversion_time,
            conversion_id=f"hubspot-deal-{deal_id}-ql",
            floodlight_id=ql_bucket_config["floodlight_id"],
            ql_value=ql_bucket_config["value"]
        )


        if result == "uploaded":
            uploaded_count += 1
            upload_log["qualified_leads"].append(deal_id)
            save_upload_log(upload_log)
        elif result == "already_uploaded":
            already_uploaded_count += 1
        elif result == "click_not_found":
            skipped_click_not_found_count += 1
        elif result == "before_click":
            skipped_before_click_count += 1
        else:
            failed_count += 1


    print("\n=== QL CATCHUP INSERT SUMMARY ===")
    print(f"Uploaded successfully: {uploaded_count}")
    print(f"Skipped - already uploaded: {already_uploaded_count}")
    print(f"Skipped - no associated contact: {skipped_no_contact_count}")
    print(f"Skipped - no GCLID: {skipped_no_gclid_count}")
    print(f"Skipped - no valid bucket: {skipped_no_bucket_count}")
    print(f"Skipped - click ID not found in SA360: {skipped_click_not_found_count}")
    print(f"Skipped - conversion before click: {skipped_before_click_count}")
    print(f"Failed: {failed_count}")

if __name__ == "__main__":
    service = get_sa360_service()


    run_mode = os.getenv("RUN_MODE", "daily").strip().lower()


    if run_mode == "daily":
        print("RUN_MODE=daily")
        run(service)
    elif run_mode == "ql_backfill":
        backfill_qualified_lead_values(service)
    elif run_mode == "ql_catchup_insert":
        catchup_insert_missing_qualified_leads(service)
    else:
        raise ValueError(
            f"Unsupported RUN_MODE={run_mode}. "
            f"Use RUN_MODE=daily, RUN_MODE=ql_backfill, or RUN_MODE=ql_catchup_insert."
        )