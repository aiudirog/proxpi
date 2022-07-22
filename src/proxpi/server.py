"""Cached package index server."""

import os
import gzip
import zlib
import logging
import typing as t
import urllib.parse

import flask
import jinja2

from . import _cache

fmt = "%(asctime)s [%(levelname)8s] %(name)s: %(message)s"
try:
    import coloredlogs
except ImportError:  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format=fmt)
else:  # pragma: no cover
    coloredlogs.install(
        level=logging.INFO,
        fmt=fmt,
        field_styles={
            "asctime": {"faint": True, "color": "white"},
            "levelname": {"bold": True, "color": "blue"},
            "name": {"bold": True, "color": "yellow"},
        },
    )
logger = logging.getLogger(__name__)

try:
    import importlib.metadata
except ImportError:
    importlib = None
else:
    try:
        logger.info(f"proxpi version: {importlib.metadata.version('proxpi')}")
    except importlib.metadata.PackageNotFoundError:
        pass

try:
    import gunicorn.glogging
except ImportError:
    gunicorn = None
else:

    class _GunicornLogger(gunicorn.glogging.Logger):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.error_log.propagate = True
            self.access_log.propagate = True

        def _set_handler(self, *_, **__):
            pass


app = flask.Flask("proxpi")
app.jinja_loader = jinja2.PackageLoader("proxpi")
cache = _cache.Cache.from_config()
if app.debug or app.testing:
    logging.root.setLevel(logging.DEBUG)
    for handler in logging.root.handlers:
        if handler.level > logging.DEBUG:
            handler.level = logging.DEBUG
logger.info("Cache: %r", cache)

BINARY_FILE_MIME_TYPE = (
    os.environ.get("PROXPI_BINARY_FILE_MIME_TYPE", "")
).lower() not in ("", "0", "no", "off", "false")
_file_mime_type = "application/octet-stream" if BINARY_FILE_MIME_TYPE else None


def _compress(response: t.Union[str, flask.Response]) -> flask.Response:
    response = flask.make_response(response)
    gzip_quality = flask.request.accept_encodings.quality("gzip")
    zlib_quality = flask.request.accept_encodings.quality("deflate")
    identity_quality = flask.request.accept_encodings.quality("identity")
    if gzip_quality and gzip_quality >= max(identity_quality, zlib_quality):
        response.data = gzip.compress(response.data)
        response.content_encoding = "gzip"
    elif zlib_quality and zlib_quality >= identity_quality:
        response.data = zlib.compress(response.data)
        response.content_encoding = "deflate"
    elif "identity" in flask.request.accept_encodings and not identity_quality:
        flask.abort(406)
    response.vary.add("Accept-Encoding")
    return response


@app.route("/")
def index():
    """Home page."""
    max_age = app.get_send_file_max_age("index.html")
    return flask.send_from_directory(
        os.path.join(app.root_path, app.template_folder), "index.html", max_age=max_age
    )


@app.route("/index/")
def list_packages():
    """List all projects in index(es)."""
    package_names = cache.list_projects()
    text = flask.render_template("packages.html", package_names=package_names)
    return _compress(text)


@app.route("/index/<package_name>/")
def list_files(package_name: str):
    """List all files for a project."""
    try:
        files = cache.list_files(package_name)
    except _cache.NotFound:
        flask.abort(404)
        raise
    text = flask.render_template("files.html", package_name=package_name, files=files)
    return _compress(text)


@app.route("/index/<package_name>/<file_name>")
def get_file(package_name: str, file_name: str):
    """Download a file."""
    try:
        path = cache.get_file(package_name, file_name)
    except _cache.NotFound:
        flask.abort(404)
        raise
    scheme = urllib.parse.urlparse(path).scheme
    if scheme and scheme != "file":
        return flask.redirect(path)
    return flask.send_file(path, mimetype=_file_mime_type)


@app.route("/cache/list", methods=["DELETE"])
def invalidate_list():
    """Invalidate project list cache."""
    cache.invalidate_list()
    return {"status": "success", "data": None}


@app.route("/cache/<package_name>", methods=["DELETE"])
def invalidate_package(package_name):
    """Invalidate project file list cache."""
    cache.invalidate_project(package_name)
    return {"status": "success", "data": None}
