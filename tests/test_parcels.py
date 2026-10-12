import pytest

from conftest import TEST_EMAIL


def test_parcels_write_uses_authenticated_email_as_owner(
    client, fake_supabase, bearer_headers
):
    response = client.post(
        "/api/parcels_w",
        json={
            "owner": "Parcel owner",
            "owner_uuid": "attacker@example.test",
            "price_m2": 10,
            "price": 1000,
            "currency": "USD",
            "location_name": "Test location",
            "description": "Test parcel",
            "sell_category": "to_sell",
            "status_category": "available",
            "longitude": 10,
            "latitude": 20,
            "parcel_name": "Test parcel",
            "geojson": {"type": "FeatureCollection", "features": []},
            "area_ha": 1,
        },
        headers=bearer_headers(),
    )

    assert response.status_code == 201
    insert_call = next(
        call
        for call in fake_supabase.calls
        if call["table"] == "main_parcels" and call["operation"] == "insert"
    )
    assert insert_call["payload"]["owner_uuid"] == TEST_EMAIL


def test_parcels_write_hides_database_exception_details(
    client, fake_supabase, bearer_headers, monkeypatch
):
    original_table = fake_supabase.table

    class FailingInsert:
        def insert(self, payload):
            return self

        def execute(self):
            raise RuntimeError("private database detail")

    def table_with_failing_parcel_insert(table_name):
        if table_name == "main_parcels":
            return FailingInsert()
        return original_table(table_name)

    monkeypatch.setattr(fake_supabase, "table", table_with_failing_parcel_insert)

    response = client.post(
        "/api/parcels_w",
        json={
            "owner": "Parcel owner",
            "owner_uuid": "attacker@example.test",
            "price_m2": 10,
            "price": 1000,
            "currency": "USD",
            "location_name": "Test location",
            "description": "Test parcel",
            "sell_category": "to_sell",
            "status_category": "available",
            "longitude": 10,
            "latitude": 20,
            "parcel_name": "Test parcel",
            "geojson": {"type": "FeatureCollection", "features": []},
            "area_ha": 1,
        },
        headers=bearer_headers(),
    )

    assert response.status_code == 500
    assert response.get_json()["message"] == "Failed to create parcel"
    assert "private database detail" not in response.get_data(as_text=True)


def test_update_t_accepts_an_authorized_field(client, fake_supabase, bearer_headers):
    fake_supabase.queue_response("main_parcels", "update", [{"id": 7}])

    response = client.post(
        "/api/update_t",
        json={"id": 7, "element": {"parcel_name": "Updated name"}},
        headers=bearer_headers(),
    )

    assert response.status_code == 200
    update_call = next(
        call
        for call in fake_supabase.calls
        if call["table"] == "main_parcels" and call["operation"] == "update"
    )
    assert update_call["payload"] == {"parcel_name": "Updated name"}
    assert ("owner_uuid", TEST_EMAIL) in update_call["filters"]


@pytest.mark.parametrize("forbidden_field", ["owner_uuid", "owner", "geojson"])
def test_update_t_rejects_protected_fields(
    forbidden_field, client, fake_supabase, bearer_headers
):
    response = client.post(
        "/api/update_t",
        json={"id": 7, "element": {forbidden_field: "tampered"}},
        headers=bearer_headers(),
    )

    assert response.status_code == 400
    assert not any(
        call["table"] == "main_parcels" and call["operation"] == "update"
        for call in fake_supabase.calls
    )


def test_update_t_returns_not_found_for_missing_or_unowned_parcel(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response("main_parcels", "update", [])

    response = client.post(
        "/api/update_t",
        json={"id": 999, "element": {"parcel_name": "Updated name"}},
        headers=bearer_headers(),
    )

    assert response.status_code == 404
