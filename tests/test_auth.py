from types import SimpleNamespace

from conftest import TEST_EMAIL, TEST_USER_ID


def test_register_ignores_client_pro_account(client, fake_supabase):
    response = client.post(
        "/api/register",
        json={
            "email": "new-user@example.test",
            "password": "safe-test-password",
            "name": "New User",
            "username": "newuser",
            "mobile": "+10000000000",
            "account": "pro",
        },
    )

    assert response.status_code == 201
    profile_insert = next(
        call["payload"]
        for call in fake_supabase.calls
        if call["table"] == "profiles" and call["operation"] == "insert"
    )
    assert profile_insert["account"] == "free"


def test_get_infos_free_is_forbidden_before_parcel_or_report_queries(
    client, fake_supabase, bearer_headers
):
    headers = bearer_headers({"account": "free"})

    response = client.post(
        "/api/get_infos",
        json={"data": "parcel-uuid"},
        headers=headers,
    )

    assert response.status_code == 403
    assert not {"main_parcels", "georeport"}.intersection(fake_supabase.table_names)


def test_get_infos_pro_published_parcel_with_report_is_allowed(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response("main_parcels", "select", [{"uuid": "parcel-uuid"}])
    fake_supabase.queue_response(
        "georeport",
        "select",
        [{"uuid_parcel": "parcel-uuid", "geoinfos": {"elevation": 12}}],
    )

    response = client.post(
        "/api/get_infos",
        json={"data": "parcel-uuid"},
        headers=bearer_headers({"account": "pro"}),
    )

    assert response.status_code == 200
    assert response.get_json()["data"][0]["uuid_parcel"] == "parcel-uuid"
    parcel_query = next(
        call for call in fake_supabase.calls if call["table"] == "main_parcels"
    )
    assert ("sell_category", "to_sell") in parcel_query["filters"]


def test_get_infos_pro_unpublished_parcel_is_not_found(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response("main_parcels", "select", [])

    response = client.post(
        "/api/get_infos",
        json={"data": "unpublished-parcel"},
        headers=bearer_headers({"account": "pro"}),
    )

    assert response.status_code == 404
    assert "georeport" not in fake_supabase.table_names


def test_current_user_email_fallback_uses_supabase_auth(
    client, fake_supabase, bearer_headers, monkeypatch
):
    headers = bearer_headers({"email": ""})
    verified_tokens = []

    def get_verified_user(token):
        verified_tokens.append(token)
        return SimpleNamespace(
            user=SimpleNamespace(
                id=TEST_USER_ID,
                email=TEST_EMAIL,
                user_metadata={},
            )
        )

    monkeypatch.setattr(fake_supabase.auth, "get_user", get_verified_user)

    response = client.get("/api/current_user", headers=headers)

    assert response.status_code == 200
    assert response.get_json()["data"]["email"] == TEST_EMAIL
    # token_required validates once; the fallback retrieves the verified email.
    assert len(verified_tokens) == 2


def test_account_returns_account_with_explicit_projection(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response(
        "profiles",
        "select",
        [{"uuid": TEST_USER_ID, "email": TEST_EMAIL, "account": "pro"}],
    )
    fake_supabase.queue_response(
        "profiles",
        "select",
        [{"account": "pro", "email": TEST_EMAIL, "address": "Private address"}],
    )

    response = client.get("/api/account", headers=bearer_headers())

    assert response.status_code == 200
    body = response.get_json()
    assert body["data"][0]["account"] == "pro"
    assert body["data"] == [{"account": "pro"}]

    account_query = next(
        call
        for call in fake_supabase.calls
        if call["table"] == "profiles" and ("email", TEST_EMAIL) in call["filters"]
    )
    assert account_query["columns"] == "account"
