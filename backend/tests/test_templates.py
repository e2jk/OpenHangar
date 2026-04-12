class TestIndexTemplate:
    def test_renders_index_template(self, client, captured_templates):
        client.get("/")
        assert any(t.name == "index.html" for t, _ in captured_templates)

    def test_includes_base_css(self, client):
        assert b"base.css" in client.get("/").data

    def test_includes_page_css(self, client):
        assert b"index.css" in client.get("/").data

    def test_has_navbar(self, client):
        assert b"navbar" in client.get("/").data

    def test_has_footer(self, client):
        assert b"footer" in client.get("/").data
