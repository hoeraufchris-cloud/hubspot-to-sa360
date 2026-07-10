import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build


# =============================================================================
# CONFIGURATION
# =============================================================================

HUBSPOT_API_KEY = os.getenv("HUBSPOT_ACCESS_TOKEN")

BASE_URL = "https://api.hubapi.com"

HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type": "application/json",
}

SCOPES = [
    "https://www.googleapis.com/auth/doubleclicksearch"
]

ADVERTISER_ID = "1354960311"
ENGINE_ACCOUNT_ID = "9620336649"

QUALIFIED_LEAD_FLOODLIGHT_ID = "14378257"
CLOSED_WON_FLOODLIGHT_ID = "14543866"

# New offline MQL Floodlight.
# Environment variables can override these defaults later if needed.
SWAG_MQL_FLOODLIGHT_ID = os.getenv(
    "SWAG_MQL_FLOODLIGHT_ID",
    "468767456",
).strip()

SWAG_MQL_VALUE = float(
    os.getenv("SWAG_MQL_VALUE", "224")
)

UPLOAD_LOG_FILE = "uploaded_conversions.json"


# =============================================================================
# QL BUCKET VALUES
# =============================================================================

QL_BUCKET_CONFIG = {
    "0-999": {
        "floodlight_id": "460685756",
        "value": 452,
    },
    "1000-2999": {
        "floodlight_id": "461093029",
        "value": 877,
    },
    "3000-9999": {
        "floodlight_id": "461099151",
        "value": 1732,
    },
    "10000-19999": {
        "floodlight_id": "460927983",
        "value": 2973,
    },
    "20000-49999": {
        "floodlight_id": "461095948",
        "value": 4534,
    },
    "50000+": {
        "floodlight_id": "460685762",
        "value": 6038,
    },
}

ALLOWED_QL_FLOODLIGHT_IDS = {
    config["floodlight_id"]
    for config in QL_BUCKET_CONFIG.values()
}


# =============================================================================
# HUBSPOT MQL PROPERTY CONFIGURATION
# =============================================================================

MQL_LIFECYCLE_PROPERTY = "lifecyclestage"
MQL_LIFECYCLE_VALUE = "marketingqualifiedlead"

MQL_DATE_PROPERTY = (
    "hs_v2_date_entered_marketingqualifiedlead"
)

MQL_ORIGINAL_SOURCE_PROPERTY = "hs_analytics_source"
MQL_PAID_SEARCH_VALUE = "PAID_SEARCH"

MQL_ADS_INFLUENCED_PROPERTY = "ads_influenced"

MQL_ADS_INFLUENCED_VALUES = [
    "30 days",
    "60 days",
    "90 days",
]

MQL_GCLID_PROPERTY = "hs_google_click_id"


# =============================================================================
# GENERAL HELPERS
# =============================================================================

def validate_environment():
    if not HUBSPOT_API_KEY:
        raise ValueError(
            "HUBSPOT_ACCESS_TOKEN is missing."
        )

    if not SWAG_MQL_FLOODLIGHT_ID:
        raise ValueError(
            "SWAG_MQL_FLOODLIGHT_ID is missing."
        )

    if SWAG_MQL_VALUE <= 0:
        raise ValueError(
            "SWAG_MQL_VALUE must be greater than zero."
        )


def get_sa360_service():
    service_account_file = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        "service_account.json",
    )

    credentials = (
        service_account.Credentials
        .from_service_account_file(
            service_account_file,
            scopes=SCOPES,
        )
    )

    return build(
        "doubleclicksearch",
        "v2",
        credentials=credentials,
    )


def get_lookback_window_ms(lookback_days):
    if lookback_days < 1:
        raise ValueError(
            "LOOKBACK_DAYS must be at least 1."
        )

    local_timezone = ZoneInfo(
        "America/Chicago"
    )

    end_date = (
        datetime.now(local_timezone).date()
        - timedelta(days=1)
    )

    start_date = (
        end_date
        - timedelta(days=lookback_days - 1)
    )

    start_timestamp = int(
        datetime.combine(
            start_date,
            datetime.min.time(),
            tzinfo=local_timezone,
        ).timestamp()
        * 1000
    )

    end_timestamp = int(
        datetime.combine(
            end_date,
            datetime.max.time(),
            tzinfo=local_timezone,
        ).timestamp()
        * 1000
    )

    return start_timestamp, end_timestamp


def parse_hubspot_datetime_to_ms(raw_value):
    if raw_value in (None, ""):
        return None

    raw_string = str(raw_value).strip()

    if raw_string.isdigit():
        numeric_value = int(raw_string)

        if numeric_value > 10_000_000_000:
            return numeric_value

        return numeric_value * 1000

    parsed_datetime = datetime.fromisoformat(
        raw_string.replace(
            "Z",
            "+00:00",
        )
    )

    return int(
        parsed_datetime.timestamp()
        * 1000
    )


def load_upload_log():
    default_log = {
        "qualified_leads": [],
        "closed_won": [],
        "swag_mql": [],
    }

    if not os.path.exists(UPLOAD_LOG_FILE):
        return default_log

    with open(
        UPLOAD_LOG_FILE,
        "r",
        encoding="utf-8",
    ) as file:
        upload_log = json.load(file)

    # Preserve compatibility with the existing log file.
    for key in default_log:
        upload_log.setdefault(key, [])

    return upload_log


def save_upload_log(upload_log):
    temporary_file = (
        f"{UPLOAD_LOG_FILE}.tmp"
    )

    with open(
        temporary_file,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            upload_log,
            file,
            indent=2,
            sort_keys=True,
        )

    os.replace(
        temporary_file,
        UPLOAD_LOG_FILE,
    )


def search_hubspot_objects(
    object_type,
    filter_groups,
    properties,
    search_label,
):
    url = (
        f"{BASE_URL}/crm/v3/objects/"
        f"{object_type}/search"
    )

    payload = {
        "filterGroups": filter_groups,
        "properties": properties,
        "limit": 100,
    }

    all_results = []
    after = None
    page = 1
    total = None

    while True:
        if after:
            payload["after"] = after
        else:
            payload.pop("after", None)

        response = requests.post(
            url,
            headers=HEADERS,
            json=payload,
            timeout=60,
        )

        print(
            f"{search_label} PAGE {page} "
            f"STATUS: {response.status_code}"
        )

        response.raise_for_status()

        response_data = response.json()

        if total is None:
            total = response_data.get(
                "total",
                0,
            )

        results = response_data.get(
            "results",
            [],
        )

        all_results.extend(results)

        print(
            f"Fetched {len(results)} records "
            f"on page {page}; running total: "
            f"{len(all_results)} of {total}"
        )

        after = (
            response_data
            .get("paging", {})
            .get("next", {})
            .get("after")
        )

        if not after:
            break

        page += 1

    print(
        f"{search_label} COMPLETE: "
        f"{len(all_results)} records fetched"
    )

    return all_results


# =============================================================================
# CONTACT LOOKUP FOR DEALS
# =============================================================================

def get_contact_for_deal(deal_id):
    association_url = (
        f"{BASE_URL}/crm/v4/objects/"
        f"deals/{deal_id}/associations/contacts"
    )

    response = requests.get(
        association_url,
        headers=HEADERS,
        timeout=60,
    )

    response.raise_for_status()

    associations = response.json().get(
        "results",
        [],
    )

    if not associations:
        print(
            f"CONTACT DEBUG | deal_id={deal_id} "
            f"| associated_contacts=0 "
            f"| gclid_found=False"
        )

        return None

    print(
        f"CONTACT DEBUG | deal_id={deal_id} "
        f"| associated_contacts="
        f"{len(associations)}"
    )

    for association in associations:
        contact_id = association[
            "toObjectId"
        ]

        contact_url = (
            f"{BASE_URL}/crm/v3/objects/"
            f"contacts/{contact_id}"
            f"?properties=hs_google_click_id,email"
        )

        contact_response = requests.get(
            contact_url,
            headers=HEADERS,
            timeout=60,
        )

        contact_response.raise_for_status()

        contact = contact_response.json()
        properties = contact.get(
            "properties",
            {},
        )

        gclid = properties.get(
            "hs_google_click_id"
        )

        email = properties.get("email")

        print(
            f"CONTACT DEBUG | deal_id={deal_id} "
            f"| contact_id={contact_id} "
            f"| email={email} "
            f"| has_gclid={bool(gclid)}"
        )

        if gclid:
            print(
                f"CONTACT DEBUG | deal_id={deal_id} "
                f"| selected_contact_id={contact_id}"
            )

            return contact

    print(
        f"CONTACT DEBUG | deal_id={deal_id} "
        f"| gclid_found=False"
    )

    return None


# =============================================================================
# QL BUCKET LOGIC
# =============================================================================

def get_ql_bucket_config(properties):
    raw_bucket = (
        properties.get(
            "actual_amount_bucket"
        )
        or properties.get(
            "budget_bucket"
        )
    )

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

        if amount >= 20000:
            return "20000-49999"

        if amount >= 10000:
            return "10000-19999"

        if amount >= 3000:
            return "3000-9999"

        if amount >= 1000:
            return "1000-2999"

        return "0-999"

    if raw_bucket not in (None, ""):
        raw_bucket_string = str(
            raw_bucket
        ).strip()

        normalized_key = (
            raw_bucket_string
            .lower()
            .replace("$", "")
            .replace(",", "")
        )

        bucket = (
            bucket_normalization.get(
                raw_bucket_string
            )
            or bucket_normalization.get(
                normalized_key
            )
        )

        if bucket:
            return (
                bucket,
                QL_BUCKET_CONFIG[bucket],
            )

        try:
            numeric_value = float(
                normalized_key
            )

            bucket = bucket_from_numeric_amount(
                numeric_value
            )

            print(
                "Unrecognized QL bucket "
                f"raw={repr(raw_bucket_string)}. "
                f"Parsed numeric fallback "
                f"-> bucket={bucket}"
            )

            return (
                bucket,
                QL_BUCKET_CONFIG[bucket],
            )

        except (TypeError, ValueError):
            pass

    fallback_amount = (
        properties.get(
            "hs_closed_amount"
        )
        or properties.get("amount")
    )

    if fallback_amount not in (None, ""):
        try:
            numeric_amount = float(
                fallback_amount
            )

            bucket = bucket_from_numeric_amount(
                numeric_amount
            )

            print(
                "Using numeric deal amount "
                f"raw={repr(fallback_amount)} "
                f"-> bucket={bucket}"
            )

            return (
                bucket,
                QL_BUCKET_CONFIG[bucket],
            )

        except (TypeError, ValueError):
            pass

    print(
        "No valid QL bucket or numeric "
        "fallback was available."
    )

    return None, None


def normalize_segmentation_id(
    segmentation_id,
):
    if segmentation_id is None:
        return None

    segmentation_id = str(
        segmentation_id
    )

    for allowed_id in (
        ALLOWED_QL_FLOODLIGHT_IDS
    ):
        if segmentation_id.endswith(
            allowed_id
        ):
            return allowed_id

    if len(segmentation_id) >= 9:
        return segmentation_id[-9:]

    return segmentation_id


def audit_ql_response_floodlight(
    response,
    expected_conversion_id,
    expected_floodlight_id,
):
    conversions = response.get(
        "conversion",
        [],
    )

    if not conversions:
        print(
            "QL AUDIT WARNING "
            f"| conversion_id="
            f"{expected_conversion_id} "
            f"| expected_floodlight_id="
            f"{expected_floodlight_id} "
            "| no conversion objects returned"
        )

        return None

    conversion = conversions[0]

    segmentation_id_raw = conversion.get(
        "segmentationId"
    )

    segmentation_name = conversion.get(
        "segmentationName"
    )

    segmentation_id_short = (
        normalize_segmentation_id(
            segmentation_id_raw
        )
    )

    if (
        segmentation_id_short
        != str(expected_floodlight_id)
    ):
        print(
            "QL AUDIT WARNING "
            f"| conversion_id="
            f"{expected_conversion_id} "
            f"| expected_floodlight_id="
            f"{expected_floodlight_id} "
            f"| returned_segmentation_id="
            f"{segmentation_id_raw} "
            f"| returned_segmentation_id_short="
            f"{segmentation_id_short} "
            f"| returned_segmentation_name="
            f"{segmentation_name}"
        )
    else:
        print(
            "QL AUDIT OK "
            f"| conversion_id="
            f"{expected_conversion_id} "
            f"| expected_floodlight_id="
            f"{expected_floodlight_id} "
            f"| returned_segmentation_name="
            f"{segmentation_name}"
        )

    return segmentation_id_short


# =============================================================================
# HUBSPOT DEAL SEARCHES
# =============================================================================

def get_first_deals(lookback_days=1):
    start_timestamp, end_timestamp = (
        get_lookback_window_ms(
            lookback_days
        )
    )

    filter_groups = [
        {
            "filters": [
                {
                    "propertyName":
                        "order_sequence",
                    "operator": "LT",
                    "value": "2",
                },
                {
                    "propertyName":
                        "paid_ad_bid_strategy",
                    "operator":
                        "HAS_PROPERTY",
                },
                {
                    "propertyName":
                        "createdate",
                    "operator": "GTE",
                    "value":
                        str(start_timestamp),
                },
                {
                    "propertyName":
                        "createdate",
                    "operator": "LTE",
                    "value":
                        str(end_timestamp),
                },
            ]
        },
        {
            "filters": [
                {
                    "propertyName":
                        "order_sequence",
                    "operator":
                        "NOT_HAS_PROPERTY",
                },
                {
                    "propertyName":
                        "paid_ad_bid_strategy",
                    "operator":
                        "HAS_PROPERTY",
                },
                {
                    "propertyName":
                        "createdate",
                    "operator": "GTE",
                    "value":
                        str(start_timestamp),
                },
                {
                    "propertyName":
                        "createdate",
                    "operator": "LTE",
                    "value":
                        str(end_timestamp),
                },
            ]
        },
    ]

    properties = [
        "createdate",
        "closedate",
        "hs_closed_amount",
        "amount",
        "dealstage",
        "order_sequence",
        "actual_amount_bucket",
        "budget_bucket",
    ]

    return search_hubspot_objects(
        object_type="deals",
        filter_groups=filter_groups,
        properties=properties,
        search_label="DEAL SEARCH",
    )


def get_closed_won_deals(
    lookback_days=1,
):
    start_timestamp, end_timestamp = (
        get_lookback_window_ms(
            lookback_days
        )
    )

    filter_groups = [
        {
            "filters": [
                {
                    "propertyName":
                        "dealstage",
                    "operator": "EQ",
                    "value": "closedwon",
                },
                {
                    "propertyName":
                        "paid_ad_bid_strategy",
                    "operator":
                        "HAS_PROPERTY",
                },
                {
                    "propertyName":
                        "closedate",
                    "operator": "GTE",
                    "value":
                        str(start_timestamp),
                },
                {
                    "propertyName":
                        "closedate",
                    "operator": "LTE",
                    "value":
                        str(end_timestamp),
                },
            ]
        }
    ]

    properties = [
        "createdate",
        "closedate",
        "hs_closed_amount",
        "amount",
        "dealstage",
        "order_sequence",
        "actual_amount_bucket",
        "budget_bucket",
    ]

    return search_hubspot_objects(
        object_type="deals",
        filter_groups=filter_groups,
        properties=properties,
        search_label="CLOSED WON SEARCH",
    )


# =============================================================================
# HUBSPOT MQL SEARCH
# =============================================================================

def get_mql_contacts(lookback_days=1):
    start_timestamp, end_timestamp = (
        get_lookback_window_ms(
            lookback_days
        )
    )

    common_filters = [
        {
            "propertyName":
                MQL_LIFECYCLE_PROPERTY,
            "operator": "EQ",
            "value":
                MQL_LIFECYCLE_VALUE,
        },
        {
            "propertyName":
                MQL_DATE_PROPERTY,
            "operator": "GTE",
            "value":
                str(start_timestamp),
        },
        {
            "propertyName":
                MQL_DATE_PROPERTY,
            "operator": "LTE",
            "value":
                str(end_timestamp),
        },
        {
            "propertyName":
                MQL_GCLID_PROPERTY,
            "operator":
                "HAS_PROPERTY",
        },
    ]

    filter_groups = [
        {
            "filters":
                common_filters
                + [
                    {
                        "propertyName":
                            MQL_ORIGINAL_SOURCE_PROPERTY,
                        "operator": "EQ",
                        "value":
                            MQL_PAID_SEARCH_VALUE,
                    }
                ]
        },
        {
            "filters":
                common_filters
                + [
                    {
                        "propertyName":
                            MQL_ADS_INFLUENCED_PROPERTY,
                        "operator": "IN",
                        "values":
                            MQL_ADS_INFLUENCED_VALUES,
                    }
                ]
        },
    ]

    properties = [
        "email",
        MQL_GCLID_PROPERTY,
        MQL_LIFECYCLE_PROPERTY,
        MQL_DATE_PROPERTY,
        MQL_ORIGINAL_SOURCE_PROPERTY,
        MQL_ADS_INFLUENCED_PROPERTY,
    ]

    results = search_hubspot_objects(
        object_type="contacts",
        filter_groups=filter_groups,
        properties=properties,
        search_label="MQL CONTACT SEARCH",
    )

    # A contact may qualify through both OR branches.
    contacts_by_id = {
        contact["id"]: contact
        for contact in results
    }

    unique_contacts = list(
        contacts_by_id.values()
    )

    print(
        "MQL CONTACT DEDUPLICATION COMPLETE: "
        f"{len(unique_contacts)} "
        "unique contacts"
    )

    return unique_contacts


# =============================================================================
# SA360 UPLOAD FUNCTIONS
# =============================================================================

def upload_qualified_lead(
    service,
    click_id,
    conversion_time,
    conversion_id,
    floodlight_id,
    ql_value,
):
    body = {
        "conversion": [
            {
                "clickId": click_id,
                "conversionId":
                    conversion_id,
                "conversionTimestamp":
                    conversion_time,
                "segmentationType":
                    "FLOODLIGHT",
                "segmentationId":
                    floodlight_id,
                "type":
                    "TRANSACTION",
                "revenueMicros": str(
                    int(
                        round(
                            float(ql_value)
                            * 1_000_000
                        )
                    )
                ),
                "currencyCode": "USD",
            }
        ]
    }

    try:
        response = (
            service
            .conversion()
            .insert(body=body)
            .execute()
        )

        print("\nQL SA360 RESPONSE:")
        print(
            json.dumps(
                response,
                indent=2,
            )
        )

        audit_ql_response_floodlight(
            response,
            conversion_id,
            floodlight_id,
        )

        return "uploaded"

    except Exception as error:
        error_string = str(
            error
        ).lower()

        if (
            "conversion id is already specified"
            in error_string
        ):
            print(
                "\nAlready uploaded QL "
                f"(safe to skip): "
                f"{conversion_id}"
            )

            return "already_uploaded"

        if (
            "click id" in error_string
            and "is not found"
            in error_string
        ):
            print(
                "\nSkipping QL "
                "(click ID not found in SA360): "
                f"{conversion_id}"
            )

            return "click_not_found"

        if (
            "conversion timestamp is before "
            "click timestamp"
            in error_string
        ):
            print(
                "\nSkipping QL "
                "(conversion occurred before "
                "the recorded click): "
                f"{conversion_id}"
            )

            return "before_click"

        print(
            f"\nQL upload failed for "
            f"{conversion_id}: {error}"
        )

        return "failed"


def upload_closed_won(
    service,
    click_id,
    conversion_time,
    conversion_id,
    revenue,
):
    body = {
        "conversion": [
            {
                "clickId": click_id,
                "conversionId":
                    conversion_id,
                "conversionTimestamp":
                    conversion_time,
                "segmentationType":
                    "FLOODLIGHT",
                "segmentationId":
                    CLOSED_WON_FLOODLIGHT_ID,
                "type":
                    "TRANSACTION",
                "revenueMicros": str(
                    int(
                        round(
                            float(revenue)
                            * 1_000_000
                        )
                    )
                ),
                "currencyCode": "USD",
            }
        ]
    }

    try:
        response = (
            service
            .conversion()
            .insert(body=body)
            .execute()
        )

        print(
            "\nCLOSED WON SA360 RESPONSE:"
        )

        print(
            json.dumps(
                response,
                indent=2,
            )
        )

        return "uploaded"

    except Exception as error:
        error_string = str(
            error
        ).lower()

        if (
            "conversion id is already specified"
            in error_string
        ):
            print(
                "\nAlready uploaded Closed Won "
                f"(safe to skip): "
                f"{conversion_id}"
            )

            return "already_uploaded"

        if (
            "click id" in error_string
            and "is not found"
            in error_string
        ):
            print(
                "\nSkipping Closed Won "
                "(click ID not found in SA360): "
                f"{conversion_id}"
            )

            return "click_not_found"

        if (
            "conversion timestamp is before "
            "click timestamp"
            in error_string
        ):
            print(
                "\nSkipping Closed Won "
                "(conversion occurred before "
                "the recorded click): "
                f"{conversion_id}"
            )

            return "before_click"

        print(
            "\nClosed Won upload failed for "
            f"{conversion_id}: {error}"
        )

        return "failed"


def upload_swag_mql(
    service,
    click_id,
    conversion_time,
    conversion_id,
):
    body = {
        "conversion": [
            {
                "clickId": click_id,
                "conversionId":
                    conversion_id,
                "conversionTimestamp":
                    conversion_time,
                "segmentationType":
                    "FLOODLIGHT",
                "segmentationId":
                    SWAG_MQL_FLOODLIGHT_ID,
                "type":
                    "TRANSACTION",
                "revenueMicros": str(
                    int(
                        round(
                            SWAG_MQL_VALUE
                            * 1_000_000
                        )
                    )
                ),
                "currencyCode": "USD",
            }
        ]
    }

    try:
        response = (
            service
            .conversion()
            .insert(body=body)
            .execute()
        )

        print("\nSWAG MQL SA360 RESPONSE:")

        print(
            json.dumps(
                response,
                indent=2,
            )
        )

        returned_conversion = (
            response.get(
                "conversion",
                [{}],
            )[0]
        )

        print(
            "SWAG MQL AUDIT "
            f"| conversion_id={conversion_id} "
            f"| expected_floodlight_id="
            f"{SWAG_MQL_FLOODLIGHT_ID} "
            f"| returned_segmentation_id="
            f"{returned_conversion.get('segmentationId')} "
            f"| returned_segmentation_name="
            f"{returned_conversion.get('segmentationName')}"
        )

        return "uploaded"

    except Exception as error:
        error_string = str(
            error
        ).lower()

        if (
            "conversion id is already specified"
            in error_string
        ):
            print(
                "\nAlready uploaded Swag MQL "
                f"(safe to skip): "
                f"{conversion_id}"
            )

            return "already_uploaded"

        if (
            "click id" in error_string
            and "is not found"
            in error_string
        ):
            print(
                "\nSkipping Swag MQL "
                "(click ID not found in SA360): "
                f"{conversion_id}"
            )

            return "click_not_found"

        if (
            "conversion timestamp is before "
            "click timestamp"
            in error_string
        ):
            print(
                "\nSkipping Swag MQL "
                "(conversion occurred before "
                "the recorded click): "
                f"{conversion_id}"
            )

            return "before_click"

        print(
            "\nSwag MQL upload failed for "
            f"{conversion_id}: {error}"
        )

        return "failed"


# =============================================================================
# PROCESS QUALIFIED LEADS
# =============================================================================

def process_qualified_leads(
    service,
    deals,
    upload_log,
):
    counts = {
        "uploaded": 0,
        "already_uploaded": 0,
        "no_contact": 0,
        "no_gclid": 0,
        "no_bucket": 0,
        "no_createdate": 0,
        "click_not_found": 0,
        "before_click": 0,
        "failed": 0,
    }

    print(
        "\n=== QUALIFIED LEADS "
        "(First Deal Created) ===\n"
    )

    for deal in deals:
        deal_id = deal["id"]

        if (
            deal_id
            in upload_log["qualified_leads"]
        ):
            print(
                "Skipping QL "
                f"(already uploaded): {deal_id}"
            )

            counts["already_uploaded"] += 1
            continue

        properties = deal.get(
            "properties",
            {},
        )

        contact = get_contact_for_deal(
            deal_id
        )

        if not contact:
            print(
                "Skipping QL "
                f"(no associated contact): "
                f"{deal_id}"
            )

            counts["no_contact"] += 1
            continue

        contact_properties = contact.get(
            "properties",
            {},
        )

        gclid = contact_properties.get(
            "hs_google_click_id"
        )

        if not gclid:
            print(
                f"Skipping QL (no GCLID): "
                f"{deal_id} | Contact: "
                f"{contact_properties.get('email')}"
            )

            counts["no_gclid"] += 1
            continue

        ql_bucket, ql_config = (
            get_ql_bucket_config(
                properties
            )
        )

        if not ql_config:
            print(
                "Skipping QL "
                f"(no valid bucket): {deal_id}"
            )

            counts["no_bucket"] += 1
            continue

        conversion_time = (
            parse_hubspot_datetime_to_ms(
                properties.get(
                    "createdate"
                )
            )
        )

        if conversion_time is None:
            print(
                "Skipping QL "
                f"(no createdate): {deal_id}"
            )

            counts["no_createdate"] += 1
            continue

        conversion_id = (
            f"hubspot-deal-{deal_id}-ql"
        )

        payload_preview = {
            "deal_id": deal_id,
            "clickId": gclid,
            "raw_bucket": (
                properties.get(
                    "actual_amount_bucket"
                )
                or properties.get(
                    "budget_bucket"
                )
            ),
            "fallback_amount": (
                properties.get(
                    "hs_closed_amount"
                )
                or properties.get(
                    "amount"
                )
            ),
            "conversionName":
                f"Qualified Lead - {ql_bucket}",
            "conversionTime":
                conversion_time,
            "conversionValue":
                ql_config["value"],
            "floodlightId":
                ql_config["floodlight_id"],
            "conversionId":
                conversion_id,
        }

        print("QL SA360 PAYLOAD:")

        print(
            json.dumps(
                payload_preview,
                indent=2,
            )
        )

        result = upload_qualified_lead(
            service=service,
            click_id=gclid,
            conversion_time=
                conversion_time,
            conversion_id=
                conversion_id,
            floodlight_id=
                ql_config["floodlight_id"],
            ql_value=
                ql_config["value"],
        )

        if result == "uploaded":
            counts["uploaded"] += 1

            upload_log[
                "qualified_leads"
            ].append(deal_id)

            save_upload_log(upload_log)

        elif result == "already_uploaded":
            counts["already_uploaded"] += 1

            if (
                deal_id
                not in upload_log[
                    "qualified_leads"
                ]
            ):
                upload_log[
                    "qualified_leads"
                ].append(deal_id)

                save_upload_log(
                    upload_log
                )

        elif result == "click_not_found":
            counts["click_not_found"] += 1

        elif result == "before_click":
            counts["before_click"] += 1

        else:
            counts["failed"] += 1

    return counts


# =============================================================================
# PROCESS CLOSED WON
# =============================================================================

def process_closed_won(
    service,
    deals,
    upload_log,
):
    counts = {
        "uploaded": 0,
        "already_uploaded": 0,
        "no_contact": 0,
        "no_gclid": 0,
        "no_closedate": 0,
        "click_not_found": 0,
        "before_click": 0,
        "failed": 0,
    }

    print("\n=== CLOSED WON DEALS ===\n")

    for deal in deals:
        deal_id = deal["id"]

        if (
            deal_id
            in upload_log["closed_won"]
        ):
            print(
                "Skipping Closed Won "
                f"(already uploaded): {deal_id}"
            )

            counts["already_uploaded"] += 1
            continue

        properties = deal.get(
            "properties",
            {},
        )

        contact = get_contact_for_deal(
            deal_id
        )

        if not contact:
            print(
                "Skipping Closed Won "
                f"(no associated contact): "
                f"{deal_id}"
            )

            counts["no_contact"] += 1
            continue

        contact_properties = contact.get(
            "properties",
            {},
        )

        gclid = contact_properties.get(
            "hs_google_click_id"
        )

        if not gclid:
            print(
                "Skipping Closed Won "
                f"(no GCLID): {deal_id} "
                f"| Contact: "
                f"{contact_properties.get('email')}"
            )

            counts["no_gclid"] += 1
            continue

        conversion_time = (
            parse_hubspot_datetime_to_ms(
                properties.get(
                    "closedate"
                )
            )
        )

        if conversion_time is None:
            print(
                "Skipping Closed Won "
                f"(no closedate): {deal_id}"
            )

            counts["no_closedate"] += 1
            continue

        revenue = float(
            properties.get(
                "hs_closed_amount"
            )
            or properties.get("amount")
            or 0
        )

        conversion_id = (
            f"hubspot-deal-{deal_id}-cw"
        )

        payload_preview = {
            "deal_id": deal_id,
            "clickId": gclid,
            "conversionTime":
                conversion_time,
            "revenue": revenue,
            "floodlightId":
                CLOSED_WON_FLOODLIGHT_ID,
            "conversionId":
                conversion_id,
        }

        print(
            "CLOSED WON SA360 PAYLOAD:"
        )

        print(
            json.dumps(
                payload_preview,
                indent=2,
            )
        )

        result = upload_closed_won(
            service=service,
            click_id=gclid,
            conversion_time=
                conversion_time,
            conversion_id=
                conversion_id,
            revenue=revenue,
        )

        if result == "uploaded":
            counts["uploaded"] += 1

            upload_log[
                "closed_won"
            ].append(deal_id)

            save_upload_log(upload_log)

        elif result == "already_uploaded":
            counts["already_uploaded"] += 1

            if (
                deal_id
                not in upload_log[
                    "closed_won"
                ]
            ):
                upload_log[
                    "closed_won"
                ].append(deal_id)

                save_upload_log(
                    upload_log
                )

        elif result == "click_not_found":
            counts["click_not_found"] += 1

        elif result == "before_click":
            counts["before_click"] += 1

        else:
            counts["failed"] += 1

    return counts


# =============================================================================
# PROCESS SWAG MQL
# =============================================================================

def process_swag_mqls(
    service,
    contacts,
    upload_log,
):
    counts = {
        "uploaded": 0,
        "already_uploaded": 0,
        "no_gclid": 0,
        "no_mql_date": 0,
        "click_not_found": 0,
        "before_click": 0,
        "failed": 0,
    }

    print("\n=== SWAG MQL ===\n")

    for contact in contacts:
        contact_id = contact["id"]

        if (
            contact_id
            in upload_log["swag_mql"]
        ):
            print(
                "Skipping Swag MQL "
                f"(already uploaded): "
                f"{contact_id}"
            )

            counts["already_uploaded"] += 1
            continue

        properties = contact.get(
            "properties",
            {},
        )

        gclid = properties.get(
            MQL_GCLID_PROPERTY
        )

        if not gclid:
            print(
                "Skipping Swag MQL "
                f"(no GCLID): {contact_id} "
                f"| Contact: "
                f"{properties.get('email')}"
            )

            counts["no_gclid"] += 1
            continue

        conversion_time = (
            parse_hubspot_datetime_to_ms(
                properties.get(
                    MQL_DATE_PROPERTY
                )
            )
        )

        if conversion_time is None:
            print(
                "Skipping Swag MQL "
                f"(no MQL entry date): "
                f"{contact_id}"
            )

            counts["no_mql_date"] += 1
            continue

        conversion_id = (
            f"hubspot-contact-"
            f"{contact_id}-mql"
        )

        payload_preview = {
            "contact_id": contact_id,
            "clickId": gclid,
            "lifecycleStage":
                properties.get(
                    MQL_LIFECYCLE_PROPERTY
                ),
            "dateEnteredMql":
                properties.get(
                    MQL_DATE_PROPERTY
                ),
            "originalSource":
                properties.get(
                    MQL_ORIGINAL_SOURCE_PROPERTY
                ),
            "adsInfluenced":
                properties.get(
                    MQL_ADS_INFLUENCED_PROPERTY
                ),
            "conversionName":
                "Swag - MQL",
            "conversionTime":
                conversion_time,
            "conversionValue":
                SWAG_MQL_VALUE,
            "floodlightId":
                SWAG_MQL_FLOODLIGHT_ID,
            "conversionId":
                conversion_id,
        }

        print("SWAG MQL SA360 PAYLOAD:")

        print(
            json.dumps(
                payload_preview,
                indent=2,
            )
        )

        result = upload_swag_mql(
            service=service,
            click_id=gclid,
            conversion_time=
                conversion_time,
            conversion_id=
                conversion_id,
        )

        if result == "uploaded":
            counts["uploaded"] += 1

            upload_log[
                "swag_mql"
            ].append(contact_id)

            save_upload_log(upload_log)

        elif result == "already_uploaded":
            counts["already_uploaded"] += 1

            if (
                contact_id
                not in upload_log[
                    "swag_mql"
                ]
            ):
                upload_log[
                    "swag_mql"
                ].append(contact_id)

                save_upload_log(
                    upload_log
                )

        elif result == "click_not_found":
            counts["click_not_found"] += 1

        elif result == "before_click":
            counts["before_click"] += 1

        else:
            counts["failed"] += 1

    return counts


# =============================================================================
# SUMMARY FUNCTIONS
# =============================================================================

def print_ql_summary(counts):
    print("\nQUALIFIED LEADS")
    print(
        f"Uploaded successfully: "
        f"{counts['uploaded']}"
    )
    print(
        f"Already uploaded: "
        f"{counts['already_uploaded']}"
    )
    print(
        "Skipped - no associated contact: "
        f"{counts['no_contact']}"
    )
    print(
        f"Skipped - no GCLID: "
        f"{counts['no_gclid']}"
    )
    print(
        f"Skipped - no valid bucket: "
        f"{counts['no_bucket']}"
    )
    print(
        f"Skipped - no createdate: "
        f"{counts['no_createdate']}"
    )
    print(
        "Skipped - click ID not found "
        f"in SA360: "
        f"{counts['click_not_found']}"
    )
    print(
        "Skipped - conversion before click: "
        f"{counts['before_click']}"
    )
    print(
        f"Failed: {counts['failed']}"
    )


def print_closed_won_summary(counts):
    print("\nCLOSED WON")
    print(
        f"Uploaded successfully: "
        f"{counts['uploaded']}"
    )
    print(
        f"Already uploaded: "
        f"{counts['already_uploaded']}"
    )
    print(
        "Skipped - no associated contact: "
        f"{counts['no_contact']}"
    )
    print(
        f"Skipped - no GCLID: "
        f"{counts['no_gclid']}"
    )
    print(
        f"Skipped - no closedate: "
        f"{counts['no_closedate']}"
    )
    print(
        "Skipped - click ID not found "
        f"in SA360: "
        f"{counts['click_not_found']}"
    )
    print(
        "Skipped - conversion before click: "
        f"{counts['before_click']}"
    )
    print(
        f"Failed: {counts['failed']}"
    )


def print_mql_summary(counts):
    print("\nSWAG MQL")
    print(
        f"Uploaded successfully: "
        f"{counts['uploaded']}"
    )
    print(
        f"Already uploaded: "
        f"{counts['already_uploaded']}"
    )
    print(
        f"Skipped - no GCLID: "
        f"{counts['no_gclid']}"
    )
    print(
        "Skipped - no MQL entry date: "
        f"{counts['no_mql_date']}"
    )
    print(
        "Skipped - click ID not found "
        f"in SA360: "
        f"{counts['click_not_found']}"
    )
    print(
        "Skipped - conversion before click: "
        f"{counts['before_click']}"
    )
    print(
        f"Failed: {counts['failed']}"
    )


# =============================================================================
# DAILY RUN
# =============================================================================

def run(service):
    lookback_days = int(
        os.getenv(
            "LOOKBACK_DAYS",
            "1",
        )
    )

    print(
        f"RUNNING WITH LOOKBACK_DAYS="
        f"{lookback_days}"
    )

    upload_log = load_upload_log()

    first_deals = get_first_deals(
        lookback_days=lookback_days
    )

    closed_won_deals = (
        get_closed_won_deals(
            lookback_days=lookback_days
        )
    )

    mql_contacts = get_mql_contacts(
        lookback_days=lookback_days
    )

    ql_counts = process_qualified_leads(
        service=service,
        deals=first_deals,
        upload_log=upload_log,
    )

    closed_won_counts = (
        process_closed_won(
            service=service,
            deals=closed_won_deals,
            upload_log=upload_log,
        )
    )

    mql_counts = process_swag_mqls(
        service=service,
        contacts=mql_contacts,
        upload_log=upload_log,
    )

    print("\n=== DAILY RUN SUMMARY ===")

    print_ql_summary(ql_counts)
    print_closed_won_summary(
        closed_won_counts
    )
    print_mql_summary(mql_counts)


# =============================================================================
# QL BACKFILL — UPDATE PREVIOUSLY UPLOADED VALUES
# =============================================================================

def backfill_qualified_lead_values(
    service,
):
    lookback_days = int(
        os.getenv(
            "LOOKBACK_DAYS",
            "60",
        )
    )

    print("RUN_MODE=ql_backfill")

    print(
        "BACKFILLING QL VALUES WITH "
        f"LOOKBACK_DAYS={lookback_days}"
    )

    deals = get_first_deals(
        lookback_days=lookback_days
    )

    updated_count = 0
    not_found_count = 0
    no_contact_count = 0
    no_gclid_count = 0
    no_bucket_count = 0
    click_not_found_count = 0
    before_click_count = 0
    failed_count = 0

    for deal in deals:
        deal_id = deal["id"]

        properties = deal.get(
            "properties",
            {},
        )

        contact = get_contact_for_deal(
            deal_id
        )

        if not contact:
            no_contact_count += 1
            continue

        contact_properties = contact.get(
            "properties",
            {},
        )

        gclid = contact_properties.get(
            "hs_google_click_id"
        )

        if not gclid:
            no_gclid_count += 1
            continue

        ql_bucket, ql_config = (
            get_ql_bucket_config(
                properties
            )
        )

        if not ql_config:
            no_bucket_count += 1
            continue

        conversion_time = (
            parse_hubspot_datetime_to_ms(
                properties.get(
                    "createdate"
                )
            )
        )

        if conversion_time is None:
            failed_count += 1
            continue

        conversion_id = (
            f"hubspot-deal-{deal_id}-ql"
        )

        body = {
            "conversion": [
                {
                    "clickId": gclid,
                    "conversionId":
                        conversion_id,
                    "conversionTimestamp":
                        conversion_time,
                    "type":
                        "TRANSACTION",
                    "revenueMicros": str(
                        int(
                            round(
                                float(
                                    ql_config[
                                        "value"
                                    ]
                                )
                                * 1_000_000
                            )
                        )
                    ),
                    "currencyCode": "USD",
                }
            ]
        }

        print("QL BACKFILL PAYLOAD:")

        print(
            json.dumps(
                {
                    "deal_id":
                        deal_id,
                    "bucket":
                        ql_bucket,
                    "conversionId":
                        conversion_id,
                    "conversionValue":
                        ql_config["value"],
                },
                indent=2,
            )
        )

        try:
            response = (
                service
                .conversion()
                .update(body=body)
                .execute()
            )

            print(
                "\nUPDATED QL SA360 RESPONSE:"
            )

            print(
                json.dumps(
                    response,
                    indent=2,
                )
            )

            audit_ql_response_floodlight(
                response,
                conversion_id,
                ql_config[
                    "floodlight_id"
                ],
            )

            updated_count += 1

        except Exception as error:
            error_string = str(
                error
            ).lower()

            if (
                "click id" in error_string
                and "is not found"
                in error_string
            ):
                click_not_found_count += 1
                continue

            if (
                "conversion timestamp is before "
                "click timestamp"
                in error_string
            ):
                before_click_count += 1
                continue

            if (
                "advertiser conversion id"
                in error_string
                and "is not found"
                in error_string
            ):
                not_found_count += 1
                continue

            print(
                "\nQL backfill update failed "
                f"for {conversion_id}: {error}"
            )

            failed_count += 1

    print("\n=== QL BACKFILL SUMMARY ===")
    print(
        f"Updated successfully: "
        f"{updated_count}"
    )
    print(
        "Skipped - conversion was not "
        f"previously uploaded: "
        f"{not_found_count}"
    )
    print(
        "Skipped - no associated contact: "
        f"{no_contact_count}"
    )
    print(
        f"Skipped - no GCLID: "
        f"{no_gclid_count}"
    )
    print(
        f"Skipped - no valid bucket: "
        f"{no_bucket_count}"
    )
    print(
        "Skipped - click ID not found "
        f"in SA360: "
        f"{click_not_found_count}"
    )
    print(
        "Skipped - conversion before click: "
        f"{before_click_count}"
    )
    print(f"Failed: {failed_count}")


# =============================================================================
# CATCH-UP MODES
# =============================================================================

def catchup_insert_missing_qualified_leads(
    service,
):
    lookback_days = int(
        os.getenv(
            "LOOKBACK_DAYS",
            "60",
        )
    )

    print(
        "RUN_MODE=ql_catchup_insert"
    )

    print(
        "CATCHING UP MISSING QL INSERTS "
        f"WITH LOOKBACK_DAYS={lookback_days}"
    )

    upload_log = load_upload_log()

    deals = get_first_deals(
        lookback_days=lookback_days
    )

    counts = process_qualified_leads(
        service=service,
        deals=deals,
        upload_log=upload_log,
    )

    print(
        "\n=== QL CATCHUP "
        "INSERT SUMMARY ==="
    )

    print_ql_summary(counts)


def catchup_insert_missing_swag_mqls(
    service,
):
    lookback_days = int(
        os.getenv(
            "LOOKBACK_DAYS",
            "60",
        )
    )

    print(
        "RUN_MODE=mql_catchup_insert"
    )

    print(
        "CATCHING UP MISSING SWAG MQL "
        f"INSERTS WITH LOOKBACK_DAYS="
        f"{lookback_days}"
    )

    upload_log = load_upload_log()

    contacts = get_mql_contacts(
        lookback_days=lookback_days
    )

    counts = process_swag_mqls(
        service=service,
        contacts=contacts,
        upload_log=upload_log,
    )

    print(
        "\n=== SWAG MQL CATCHUP "
        "INSERT SUMMARY ==="
    )

    print_mql_summary(counts)


# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    validate_environment()

    service = get_sa360_service()

    run_mode = os.getenv(
        "RUN_MODE",
        "daily",
    ).strip().lower()

    if run_mode == "daily":
        print("RUN_MODE=daily")
        run(service)

    elif run_mode == "ql_backfill":
        backfill_qualified_lead_values(
            service
        )

    elif run_mode == "ql_catchup_insert":
        catchup_insert_missing_qualified_leads(
            service
        )

    elif run_mode == "mql_catchup_insert":
        catchup_insert_missing_swag_mqls(
            service
        )

    else:
        raise ValueError(
            f"Unsupported RUN_MODE="
            f"{run_mode}. Use daily, "
            "ql_backfill, "
            "ql_catchup_insert, or "
            "mql_catchup_insert."
        )