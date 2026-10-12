def test_getcoords_with_valid_token_returns_gone_without_reading_payload_or_parcels(
    client, fake_supabase, bearer_headers
):
    response = client.post(
        "/api/getcoords",
        data="not-json",
        headers=bearer_headers(),
    )

    assert response.status_code == 410
    assert response.get_json() == {
        "success": False,
        "message": "This endpoint is deprecated",
    }
    assert response.headers["Deprecation"] == "true"
    assert "main_parcels" not in fake_supabase.table_names


def test_getcoords_without_token_keeps_authentication_response(client, fake_supabase):
    response = client.post("/api/getcoords", json={"data": "parcel-uuid"})

    assert response.status_code == 401
    assert "main_parcels" not in fake_supabase.table_names


def test_getcoords_options_returns_gone_without_authentication(client, fake_supabase):
    response = client.options("/api/getcoords")

    assert response.status_code == 410
    assert response.get_json() == {
        "success": False,
        "message": "This endpoint is deprecated",
    }
    assert response.headers["Deprecation"] == "true"
    assert fake_supabase.table_names == []
