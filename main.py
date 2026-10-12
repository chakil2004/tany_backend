import json
import logging
import os
import random

from dotenv import load_dotenv
from supabase import Client, create_client


logger = logging.getLogger(__name__)

SOURCE_TABLE = "to_be_treated"   # table to read UUIDs from
TARGET_TABLE = "georeport"   # table with uuid + infos columns
errors_table = "errorlogs"
main_table = "main_parcels"


def create_supabase_client() -> Client:
    """Load the existing environment configuration and create the client."""
    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    return create_client(supabase_url, supabase_key)


def get_uuids(supabase: Client) -> list[str]:
    """Fetch all UUIDs from the source table."""
    response = supabase.table(SOURCE_TABLE).select("uuid").execute()
    return [row["uuid"] for row in response.data]


def write_row_georeport(supabase: Client, uuid: str, infos: dict) -> None:
    uuid_text = str(uuid)
    existing = (
        supabase.table(TARGET_TABLE)
        .select("uuid_parcel")
        .eq("uuid_parcel", uuid_text)
        .execute()
    )

    if len(existing.data) > 1:
        logger.error(
            "Found %s GeoReport rows for uuid_parcel=%s; refusing to overwrite an ambiguous report",
            len(existing.data),
            uuid_text,
        )
        raise ValueError(f"Multiple GeoReport rows found for parcel UUID {uuid_text}")

    geoinfos = json.dumps(infos)
    if existing.data:
        (
            supabase.table(TARGET_TABLE)
            .update({"geoinfos": geoinfos})
            .eq("uuid_parcel", uuid_text)
            .execute()
        )
        logger.info("Updated GeoReport row for uuid=%s", uuid_text)
        return

    supabase.table(TARGET_TABLE).insert({
        "uuid_parcel": uuid_text,
        "geoinfos": geoinfos,
    }).execute()
    logger.info("Inserted GeoReport row for uuid=%s", uuid_text)


def write_row_log(supabase: Client, uuid: str, infos: dict) -> None:
    uuid_text = str(uuid)
    payload = {
        "uuid_parcel": uuid_text,
        "errorlogs": json.dumps(infos),   # store as JSON string
    }
    supabase.table(errors_table).insert(payload).execute()
    logger.info("Written error log row for uuid=%s", uuid_text)


def get_coordinates(supabase: Client, uuid: str) -> dict:
    """Fetch longitude and latitude for a given UUID."""
    response = (
        supabase.table(main_table)
        .select("longitude, latitude")
        .eq("uuid", uuid)
        .single()   # expects exactly one row
        .execute()
    )
    return response.data  # {"longitude": ..., "latitude": ...}


def process_parcel(supabase: Client, uuid: str, utils_module) -> None:
    """Read, calculate, and write one parcel's GeoReport."""
    # Read the parcel coordinates.
    coords = get_coordinates(supabase, uuid)
    latitude = coords["latitude"]
    longitude = coords["longitude"]

    # Calculate GeoReport data and per-service error details.
    report = utils_module.get_all_infos(
        longitude=longitude,
        latitude=latitude,
        uuid_parcel=uuid,
    ).all_data()
    report_uuid, geoinfos, errorlogs = report

    # Persist the report and its existing error log as separate inserts.
    write_row_georeport(supabase, report_uuid, geoinfos)
    write_row_log(supabase, report_uuid, errorlogs)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    try:
        supabase = create_supabase_client()
        import utils as ut

        all_list = get_uuids(supabase)
    except Exception:
        logger.exception("Could not initialize the GeoReport batch")
        raise

    n = random.randint(1, 3)  # Preserve the existing batch size and selection.
    treated = all_list[:n]
    logger.info("Selected %s of %s returned parcel UUIDs", len(treated), len(all_list))

    for uuid in treated:
        try:
            process_parcel(supabase, uuid, ut)
        except Exception:
            logger.exception("Failed to process parcel uuid=%s; continuing", uuid)


if __name__ == "__main__":
    main()
