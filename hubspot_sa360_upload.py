import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

BASE_URL = "https://api.hubapi.com"
HUBSPOT_API_KEY = os.getenv("HUBSPOT_ACCESS_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

SCOPES = ["https://www.googleapis.com/auth/doubleclicksearch"]
UPLOAD_LOG_FILE = "uploaded_conversions.json"

SWAG_MQL_FLOODLIGHT_ID = os.getenv("SWAG_MQL_FLOODLIGHT_ID", "469108996").strip()
SWAG_MQL_VALUE = float(os.getenv("SWAG_MQL_VALUE", "224"))
CLOSED_WON_FLOODLIGHT_ID = os.getenv("CLOSED_WON_FLOODLIGHT_ID", "14543866").strip()

# SQL replaces the old deal-created QL definition. Existing value-bucket
# Floodlights are retained, but the conversion is timestamped at Date Entered SQL.
SQL_BUCKET_CONFIG = {
    "0-999": {"floodlight_id": "460685756", "value": 452},
    "1000-2999": {"floodlight_id": "461093029", "value": 877},
    "3000-9999": {"floodlight_id": "461099151", "value": 1732},
    "10000-19999": {"floodlight_id": "460927983", "value": 2973},
    "20000-49999": {"floodlight_id": "461095948", "value": 4534},
    "50000+": {"floodlight_id": "460685762", "value": 6038},
}

PAID_SEARCH_PROPERTY = "hs_analytics_source"
PAID_SEARCH_VALUE = "PAID_SEARCH"
ADS_INFLUENCED_PROPERTY = "ads_influenced"
ADS_INFLUENCED_VALUES = ["30 days", "60 days", "90 days"]
FIRST_PAGE_PROPERTY = "hs_analytics_first_url"
GCLID_PROPERTY = "hs_google_click_id"
MQL_DATE_PROPERTY = "hs_v2_date_entered_marketingqualifiedlead"
SQL_DATE_PROPERTY = "hs_v2_date_entered_salesqualifiedlead"

ECOMMERCE_PIPELINE_ID = "default"
CLOSED_WON_ECOMMERCE_STAGE_ID = "closedwon"

DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "y"}
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "1"))


def validate_environment():
    if not HUBSPOT_API_KEY:
        raise ValueError("HUBSPOT_ACCESS_TOKEN is missing")
    if LOOKBACK_DAYS < 1:
        raise ValueError("LOOKBACK_DAYS must be at least 1")


def get_sa360_service():
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    credentials = service_account.Credentials.from_service_account_file(
        service_account_file,
        scopes=SCOPES,
    )
    return build("doubleclicksearch", "v2", credentials=credentials)


def reporting_window_ms():
    tz = ZoneInfo("America/Chicago")
    end_date = datetime.now(tz).date() - timedelta(days=1)
    start_date = end_date - timedelta(days=LOOKBACK_DAYS - 1)
    start_ms = int(datetime.combine(start_date, datetime.min.time(), tzinfo=tz).timestamp() * 1000)
    end_ms = int(datetime.combine(end_date, datetime.max.time(), tzinfo=tz).timestamp() * 1000)
    return start_date, end_date, start_ms, end_ms


def parse_ms(raw_value):
    if raw_value in (None, ""):
        return None
    text = str(raw_value).strip()
    if text.isdigit():
        value = int(text)
        return value if value > 10_000_000_000 else value * 1000
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def load_upload_log():
    default = {"swag_mql": [], "sql": [], "closed_won": []}
    if not os.path.exists(UPLOAD_LOG_FILE):
        return default

    with open(UPLOAD_LOG_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    for key in default:
        data.setdefault(key, [])

    return data


def save_upload_log(data):
    temporary_file = f"{UPLOAD_LOG_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, sort_keys=True)
    os.replace(temporary_file, UPLOAD_LOG_FILE)


def hubspot_search(object_type, filter_groups, properties, label):
    url = f"{BASE_URL}/crm/v3/objects/{object_type}/search"
    payload = {
        "filterGroups": filter_groups,
        "properties": properties,
        "limit": 100,
    }

    results = []
    after = None
    page = 1

    while True:
        if after:
            payload["after"] = after
        else:
            payload.pop("after", None)

        response = requests.post(url, headers=HEADERS, json=payload, timeout=60)
        print(f"{label} | page={page} | status={response.status_code}")
        response.raise_for_status()

        data = response.json()
        results.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")

        if not after:
            break

        page += 1

    print(f"{label} COMPLETE: {len(results)} records")
    return results


def paid_search_contact_filter_groups(date_property, start_ms, end_ms):
    common_filters = [
        {"propertyName": date_property, "operator": "GTE", "value": str(start_ms)},
        {"propertyName": date_property, "operator": "LTE", "value": str(end_ms)},
        {"propertyName": GCLID_PROPERTY, "operator": "HAS_PROPERTY"},
    ]

    return [
        {
            "filters": common_filters + [
                {
                    "propertyName": PAID_SEARCH_PROPERTY,
                    "operator": "EQ",
                    "value": PAID_SEARCH_VALUE,
                }
            ]
        },
        {
            "filters": common_filters + [
                {
                    "propertyName": ADS_INFLUENCED_PROPERTY,
                    "operator": "IN",
                    "values": ADS_INFLUENCED_VALUES,
                }
            ]
        },
    ]


def is_printfection(properties):
    return "printfection" in str(properties.get(FIRST_PAGE_PROPERTY) or "").lower()


def get_contact_events(date_property, label):
    _, _, start_ms, end_ms = reporting_window_ms()

    properties = [
        "email",
        GCLID_PROPERTY,
        FIRST_PAGE_PROPERTY,
        PAID_SEARCH_PROPERTY,
        ADS_INFLUENCED_PROPERTY,
        date_property,
    ]

    rows = hubspot_search(
        "contacts",
        paid_search_contact_filter_groups(date_property, start_ms, end_ms),
        properties,
        label,
    )

    unique_contacts = {row["id"]: row for row in rows}
    eligible_contacts = []
    excluded_printfection = 0

    for contact in unique_contacts.values():
        if is_printfection(contact.get("properties", {})):
            excluded_printfection += 1
            continue
        eligible_contacts.append(contact)

    print(
        f"{label} ELIGIBLE: {len(eligible_contacts)} "
        f"| excluded Printfection: {excluded_printfection}"
    )

    return eligible_contacts


def get_deal_property_options(property_name):
    url = f"{BASE_URL}/crm/v3/properties/deals/{property_name}"
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return response.json().get("options", [])


def resolve_option_value(property_name, label):
    for option in get_deal_property_options(property_name):
        if str(option.get("label", "")).strip().lower() == label.strip().lower():
            return option.get("value")
    raise ValueError(f"Could not resolve {property_name} option label: {label}")


def get_closed_won_deals():
    _, _, start_ms, end_ms = reporting_window_ms()
    full_order_value = resolve_option_value("dealtype", "Full Order")

    filters = [
        {"propertyName": "closedate", "operator": "GTE", "value": str(start_ms)},
        {"propertyName": "closedate", "operator": "LTE", "value": str(end_ms)},
        {"propertyName": "dealtype", "operator": "EQ", "value": full_order_value},
        {
            "propertyName": "dealstage",
            "operator": "EQ",
            "value": CLOSED_WON_ECOMMERCE_STAGE_ID,
        },
        {
            "propertyName": "pipeline",
            "operator": "EQ",
            "value": ECOMMERCE_PIPELINE_ID,
        },
    ]

    return hubspot_search(
        "deals",
        [{"filters": filters}],
        ["closedate", "hs_closed_amount", "amount", "dealstage", "pipeline", "dealtype"],
        "CLOSED WON SEARCH",
    )


def get_associated_contacts_for_deal(deal_id):
    association_url = f"{BASE_URL}/crm/v4/objects/deals/{deal_id}/associations/contacts"
    response = requests.get(association_url, headers=HEADERS, timeout=60)
    response.raise_for_status()

    contacts = []

    for association in response.json().get("results", []):
        contact_id = association["toObjectId"]
        properties = ",".join(
            [
                "email",
                GCLID_PROPERTY,
                FIRST_PAGE_PROPERTY,
                PAID_SEARCH_PROPERTY,
                ADS_INFLUENCED_PROPERTY,
            ]
        )
        contact_url = (
            f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}"
            f"?properties={properties}"
        )
        contact_response = requests.get(contact_url, headers=HEADERS, timeout=60)
        contact_response.raise_for_status()
        contacts.append(contact_response.json())

    return contacts


def contact_is_paid_search_eligible(contact):
    properties = contact.get("properties", {})
    source_ok = properties.get(PAID_SEARCH_PROPERTY) == PAID_SEARCH_VALUE
    influenced_ok = properties.get(ADS_INFLUENCED_PROPERTY) in ADS_INFLUENCED_VALUES

    return (
        (source_ok or influenced_ok)
        and not is_printfection(properties)
        and bool(properties.get(GCLID_PROPERTY))
    )


def select_eligible_contact_for_deal(deal_id):
    for contact in get_associated_contacts_for_deal(deal_id):
        if contact_is_paid_search_eligible(contact):
            return contact
    return None


def get_associated_deals_for_contact(contact_id):
    association_url = f"{BASE_URL}/crm/v4/objects/contacts/{contact_id}/associations/deals"
    response = requests.get(association_url, headers=HEADERS, timeout=60)
    response.raise_for_status()

    deals = []

    for association in response.json().get("results", []):
        deal_id = association["toObjectId"]
        properties = "createdate,actual_amount_bucket,budget_bucket,hs_closed_amount,amount"
        deal_url = f"{BASE_URL}/crm/v3/objects/deals/{deal_id}?properties={properties}"
        deal_response = requests.get(deal_url, headers=HEADERS, timeout=60)
        deal_response.raise_for_status()
        deals.append(deal_response.json())

    deals.sort(key=lambda deal: str(deal.get("properties", {}).get("createdate") or ""))
    return deals


def sql_bucket_from_deal(properties):
    raw_bucket = properties.get("actual_amount_bucket") or properties.get("budget_bucket")
    normalized = str(raw_bucket or "").strip().lower().replace("$", "").replace(",", "")

    aliases = {
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

    bucket = aliases.get(normalized)
    if bucket:
        return bucket

    amount_raw = properties.get("hs_closed_amount") or properties.get("amount")

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return None

    if amount >= 50000:
        return "50000+"
    if amount >= 20000:
        return "20000-49999"
    if amount >= 10000:
        return "10000-19999"
    if amount >= 3000:
        return "3000-9999"
    if amount >= 1000:
        return "1000-2999"
    return "0-999"


def send_conversion(
    service,
    *,
    click_id,
    conversion_id,
    conversion_time,
    floodlight_id,
    revenue,
):
    preview = {
        "clickId": click_id,
        "conversionId": conversion_id,
        "conversionTimestamp": conversion_time,
        "segmentationId": floodlight_id,
        "revenue": revenue,
    }

    print(json.dumps(preview, indent=2))

    if DRY_RUN:
        return "dry_run"

    body = {
        "conversion": [
            {
                "clickId": click_id,
                "conversionId": conversion_id,
                "conversionTimestamp": conversion_time,
                "segmentationType": "FLOODLIGHT",
                "segmentationId": str(floodlight_id),
                "type": "TRANSACTION",
                "revenueMicros": str(int(round(float(revenue) * 1_000_000))),
                "currencyCode": "USD",
            }
        ]
    }

    try:
        response = service.conversion().insert(body=body).execute()
        print(json.dumps(response, indent=2))
        return "uploaded"
    except Exception as error:
        error_text = str(error).lower()

        if "conversion id is already specified" in error_text:
            return "already_uploaded"
        if "click id" in error_text and "is not found" in error_text:
            return "click_not_found"
        if "conversion timestamp is before click timestamp" in error_text:
            return "before_click"

        print(f"UPLOAD FAILED: {conversion_id}: {error}")
        return "failed"


def process_mqls(service, contacts, upload_log):
    counts = {
        "uploaded": 0,
        "dry_run": 0,
        "already_uploaded": 0,
        "skipped": 0,
        "failed": 0,
    }

    for contact in contacts:
        contact_id = contact["id"]

        if contact_id in upload_log["swag_mql"]:
            counts["already_uploaded"] += 1
            continue

        properties = contact.get("properties", {})
        conversion_time = parse_ms(properties.get(MQL_DATE_PROPERTY))
        gclid = properties.get(GCLID_PROPERTY)

        if conversion_time is None or not gclid:
            counts["skipped"] += 1
            continue

        conversion_id = f"hubspot-contact-{contact_id}-mql"
        result = send_conversion(
            service,
            click_id=gclid,
            conversion_id=conversion_id,
            conversion_time=conversion_time,
            floodlight_id=SWAG_MQL_FLOODLIGHT_ID,
            revenue=SWAG_MQL_VALUE,
        )

        if result in {"uploaded", "already_uploaded"}:
            counts[result] += 1
            if not DRY_RUN:
                upload_log["swag_mql"].append(contact_id)
                save_upload_log(upload_log)
        elif result == "dry_run":
            counts["dry_run"] += 1
        elif result == "failed":
            counts["failed"] += 1
        else:
            counts["skipped"] += 1

    return counts


def process_sql(service, contacts, upload_log):
    counts = {
        "uploaded": 0,
        "dry_run": 0,
        "already_uploaded": 0,
        "no_deal": 0,
        "no_bucket": 0,
        "skipped": 0,
        "failed": 0,
    }

    for contact in contacts:
        contact_id = contact["id"]

        if contact_id in upload_log["sql"]:
            counts["already_uploaded"] += 1
            continue

        properties = contact.get("properties", {})
        gclid = properties.get(GCLID_PROPERTY)
        conversion_time = parse_ms(properties.get(SQL_DATE_PROPERTY))

        if not gclid or conversion_time is None:
            counts["skipped"] += 1
            continue

        deals = get_associated_deals_for_contact(contact_id)
        if not deals:
            counts["no_deal"] += 1
            continue

        selected_deal = deals[0]
        bucket = sql_bucket_from_deal(selected_deal.get("properties", {}))

        if not bucket:
            counts["no_bucket"] += 1
            continue

        config = SQL_BUCKET_CONFIG[bucket]
        conversion_id = f"hubspot-contact-{contact_id}-sql"

        print(
            f"SQL | contact_id={contact_id} "
            f"| deal_id={selected_deal['id']} | bucket={bucket}"
        )

        result = send_conversion(
            service,
            click_id=gclid,
            conversion_id=conversion_id,
            conversion_time=conversion_time,
            floodlight_id=config["floodlight_id"],
            revenue=config["value"],
        )

        if result in {"uploaded", "already_uploaded"}:
            counts[result] += 1
            if not DRY_RUN:
                upload_log["sql"].append(contact_id)
                save_upload_log(upload_log)
        elif result == "dry_run":
            counts["dry_run"] += 1
        elif result == "failed":
            counts["failed"] += 1
        else:
            counts["skipped"] += 1

    return counts


def process_closed_won(service, deals, upload_log):
    counts = {
        "uploaded": 0,
        "dry_run": 0,
        "already_uploaded": 0,
        "no_eligible_contact": 0,
        "skipped": 0,
        "failed": 0,
    }

    for deal in deals:
        deal_id = deal["id"]

        if deal_id in upload_log["closed_won"]:
            counts["already_uploaded"] += 1
            continue

        contact = select_eligible_contact_for_deal(deal_id)
        if not contact:
            counts["no_eligible_contact"] += 1
            continue

        deal_properties = deal.get("properties", {})
        contact_properties = contact.get("properties", {})
        conversion_time = parse_ms(deal_properties.get("closedate"))
        gclid = contact_properties.get(GCLID_PROPERTY)
        revenue = float(
            deal_properties.get("hs_closed_amount")
            or deal_properties.get("amount")
            or 0
        )

        if conversion_time is None or not gclid:
            counts["skipped"] += 1
            continue

        conversion_id = f"hubspot-deal-{deal_id}-cw"
        result = send_conversion(
            service,
            click_id=gclid,
            conversion_id=conversion_id,
            conversion_time=conversion_time,
            floodlight_id=CLOSED_WON_FLOODLIGHT_ID,
            revenue=revenue,
        )

        if result in {"uploaded", "already_uploaded"}:
            counts[result] += 1
            if not DRY_RUN:
                upload_log["closed_won"].append(deal_id)
                save_upload_log(upload_log)
        elif result == "dry_run":
            counts["dry_run"] += 1
        elif result == "failed":
            counts["failed"] += 1
        else:
            counts["skipped"] += 1

    return counts


def run():
    validate_environment()
    start_date, end_date, _, _ = reporting_window_ms()

    print("=" * 72)
    print(
        f"HubSpot -> SA360 | {start_date} through {end_date} "
        f"| LOOKBACK_DAYS={LOOKBACK_DAYS} | DRY_RUN={DRY_RUN}"
    )
    print("=" * 72)

    service = get_sa360_service()
    upload_log = load_upload_log()

    mql_contacts = get_contact_events(MQL_DATE_PROPERTY, "MQL SEARCH")
    sql_contacts = get_contact_events(SQL_DATE_PROPERTY, "SQL SEARCH")
    closed_won_deals = get_closed_won_deals()

    summaries = {
        "mql": process_mqls(service, mql_contacts, upload_log),
        "sql": process_sql(service, sql_contacts, upload_log),
        "closed_won": process_closed_won(service, closed_won_deals, upload_log),
    }

    print("\n=== RUN SUMMARY ===")
    for name, counts in summaries.items():
        print(f"{name}: {counts}")


if __name__ == "__main__":
    run()
