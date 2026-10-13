def test_allowed_frontend_origin_receives_cors_headers(client):
    response = client.options(
        "/api/market",
        headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5500"
    allowed_headers = {
        header.strip().lower()
        for header in response.headers["Access-Control-Allow-Headers"].split(",")
    }
    assert {"authorization", "content-type"}.issubset(allowed_headers)
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_unknown_origin_does_not_receive_cors_permission(client):
    response = client.options(
        "/api/market",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization",
        },
    )

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers


def test_api_preflight_uses_automatic_options_and_declares_methods(client):
    response = client.options(
        "/api/market",
        headers={
            "Origin": "http://127.0.0.1:5501",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    allowed_methods = {
        method.strip()
        for method in response.headers["Access-Control-Allow-Methods"].split(",")
    }
    assert {"GET", "POST", "OPTIONS"}.issubset(allowed_methods)
