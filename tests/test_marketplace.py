from conftest import TEST_EMAIL


LISTING_COLUMNS = (
    "uuid,owner,owner_uuid,parcel_name,description,price,currency,price_m2,"
    "location_name,sell_category,status_category"
)
SENSITIVE_COLUMNS = {"geojson", "longitude", "latitude", "area_ha", "created_at"}


def test_getprodd_requires_a_token(client, fake_supabase):
    response = client.post("/api/getprodd", json={"data": "parcel-uuid"})

    assert response.status_code == 401
    assert fake_supabase.calls == []


def test_getprodd_returns_published_listing_without_sensitive_fields(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response(
        "main_parcels",
        "select",
        [
            {
                "uuid": "parcel-uuid",
                "owner": "Parcel owner",
                "owner_uuid": TEST_EMAIL,
                "parcel_name": "Published parcel",
                "description": "Public listing",
                "price": 1000,
                "currency": "USD",
                "price_m2": 10,
                "location_name": "Test location",
                "sell_category": "to_sell",
                "status_category": "available",
                "geojson": {"private": True},
                "longitude": 10,
                "latitude": 20,
                "area_ha": 1,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
    )

    response = client.post(
        "/api/getprodd",
        json={"data": "parcel-uuid"},
        headers=bearer_headers(),
    )

    assert response.status_code == 200
    product = response.get_json()["data"][0]
    assert product["uuid"] == "parcel-uuid"
    assert product["sell_category"] == "to_sell"
    assert SENSITIVE_COLUMNS.isdisjoint(product)

    parcel_query = next(
        call for call in fake_supabase.calls if call["table"] == "main_parcels"
    )
    assert parcel_query["columns"] == LISTING_COLUMNS
    assert ("sell_category", "to_sell") in parcel_query["filters"]


def test_getprodd_returns_not_found_for_unpublished_listing(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response("main_parcels", "select", [])

    response = client.post(
        "/api/getprodd",
        json={"data": "unpublished-parcel"},
        headers=bearer_headers(),
    )

    assert response.status_code == 404
    parcel_query = next(
        call for call in fake_supabase.calls if call["table"] == "main_parcels"
    )
    assert parcel_query["columns"] == LISTING_COLUMNS
    assert ("sell_category", "to_sell") in parcel_query["filters"]
