from conftest import TEST_EMAIL


def test_messages_write_uses_authenticated_email_as_sender(
    client, fake_supabase, bearer_headers
):
    response = client.post(
        "/api/messages_w",
        json={
            "sender_id": "attacker@example.test",
            "receiver_id": "recipient@example.test",
            "content": "Hello",
            "uuid_post": "parcel-uuid",
            "objet": "Conversation subject",
        },
        headers=bearer_headers(),
    )

    assert response.status_code == 201
    insert_call = next(
        call
        for call in fake_supabase.calls
        if call["table"] == "messages" and call["operation"] == "insert"
    )
    assert insert_call["payload"]["sender_id"] == TEST_EMAIL
    assert insert_call["payload"]["sender_id"] != "attacker@example.test"


def test_load_message_allows_a_conversation_participant(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response(
        "messages",
        "select",
        [
            {
                "sender_id": TEST_EMAIL,
                "receiver_id": "recipient@example.test",
                "objet": "Conversation subject",
            },
            {
                "sender_id": "recipient@example.test",
                "receiver_id": TEST_EMAIL,
                "objet": "Conversation subject",
            },
        ],
    )

    response = client.post(
        "/api/load_message",
        json={"data": "Conversation subject"},
        headers=bearer_headers(),
    )

    assert response.status_code == 200
    assert len(response.get_json()["data"]) == 2


def test_load_message_for_non_participant_is_forbidden(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response(
        "messages",
        "select",
        [
            {
                "sender_id": "sender@example.test",
                "receiver_id": "recipient@example.test",
                "objet": "Conversation subject",
            }
        ],
    )

    response = client.post(
        "/api/load_message",
        json={"data": "Conversation subject"},
        headers=bearer_headers(),
    )

    assert response.status_code == 403


def test_load_message_for_missing_conversation_is_not_found(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response("messages", "select", [])

    response = client.post(
        "/api/load_message",
        json={"data": "Missing conversation"},
        headers=bearer_headers(),
    )

    assert response.status_code == 404
