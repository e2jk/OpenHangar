import pytest
from flask import template_rendered
from init import create_app


@pytest.fixture()
def app():
    app = create_app()
    app.config["TESTING"] = True
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def captured_templates(app):
    """Collects (template, context) pairs rendered during a request."""
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    yield recorded
    template_rendered.disconnect(record, app)
