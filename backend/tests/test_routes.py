class TestIndex:
    def test_ok(self, client):
        assert client.get("/").status_code == 200

    def test_contains_brand(self, client):
        assert b"OpenHangar" in client.get("/").data

    def test_contains_cta(self, client):
        assert b"Get Started" in client.get("/").data

    def test_unknown_route_returns_404(self, client):
        assert client.get("/nonexistent").status_code == 404


class TestHealth:
    def test_ok(self, client):
        assert client.get("/health").status_code == 200

    def test_json_response(self, client):
        assert client.get("/health").get_json() == {"status": "ok"}
