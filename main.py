import json
import os
from supabase import create_client, Client
from dotenv import load_dotenv
import utils as ut
import random

load_dotenv()
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

SOURCE_TABLE = "to_be_treated"   # table to read UUIDs from
TARGET_TABLE = "georeport"   # table with uuid + infos columns
errors_table = "errorlogs"
main_table = "main_parcels"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_uuids() -> list[str]:
    """Fetch all UUIDs from the source table."""
    response = supabase.table(SOURCE_TABLE).select("uuid").execute()
    return [row["uuid"] for row in response.data]

def write_row_georeport(uuid: str, infos: dict) -> None:
    """Insert or update a row in the target table."""
    payload = {
        "uuid_parcel": uuid,
        "geoinfos": json.dumps(infos),   # store as JSON string
    }
    # upsert: inserts if uuid doesn't exist, updates if it does
    #supabase.table(TARGET_TABLE).upsert(payload, on_conflict="uuid_parcel").execute()
    supabase.table(TARGET_TABLE).insert(payload).execute()
    print(f"[✓] Written row for uuid={uuid}")

def write_row_log(uuid: str, infos: dict) -> None:
    """Insert or update a row in the target table."""
    payload = {
        "uuid_parcel": uuid,
        "errorlogs": json.dumps(infos),   # store as JSON string
    }
    # upsert: inserts if uuid doesn't exist, updates if it does
    supabase.table(errors_table).insert(payload).execute()
    print(f"[✓] Written row for uuid={uuid}")

def get_coordinates(uuid: str) -> dict:
    """Fetch longitude and latitude for a given UUID."""
    response = (
        supabase.table(main_table)
        .select("longitude, latitude")
        .eq("uuid", uuid)
        .single()   # expects exactly one row
        .execute()
    )
    return response.data  # {"longitude": ..., "latitude": ...}

n = random.randint(1, 3) #maleable
all_list = get_uuids()
treated = all_list[:n]

for el in treated: 
    coords = get_coordinates(el)
    lat = coords["latitude"]
    lon = coords["longitude"]
    res_ = ut.get_all_infos(longitude=lon, latitude=lat,uuid_parcel=el)
    res = res_.all_data()
    write_row_georeport(res[0], res[1])
    write_row_log(res[0], res[2])

