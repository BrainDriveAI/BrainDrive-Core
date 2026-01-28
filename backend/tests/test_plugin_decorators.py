import pytest

from app.plugins.decorators import (
    PathValidationError,
    validate_endpoint_path,
    plugin_endpoint,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/projects", "/projects"),
        ("/projects/", "/projects"),
        ("/", "/"),
    ],
)
def test_validate_endpoint_path_normalizes(path, expected):
    assert validate_endpoint_path(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "",
        "projects",
        "/api/test",
        "/admin/test",
        "/_internal",
        "/API/test",
        "/projects/../secrets",
        "/projects//secrets",
    ],
)
def test_validate_endpoint_path_rejects_invalid(path):
    with pytest.raises(PathValidationError):
        validate_endpoint_path(path)


def test_plugin_endpoint_rejects_invalid_methods():
    with pytest.raises(ValueError):
        plugin_endpoint("/ok", methods=["GET", "INVALID"])


def test_plugin_endpoint_sets_metadata():
    @plugin_endpoint("/projects", methods=["get"], admin_only=True)
    async def list_projects(request):
        """List projects."""
        return {"ok": True}

    assert getattr(list_projects, "_plugin_endpoint", False) is True
    metadata = list_projects._plugin_endpoint_metadata
    assert metadata.path == "/projects"
    assert metadata.methods == ["GET"]
    assert metadata.admin_only is True
    assert metadata.summary == "List projects."
