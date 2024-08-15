from unittest.mock import patch

from pickley import latest_pypi_version

SAMPLE_RESPONSE = """
<!DOCTYPE html>
<html>
  <head>
    <meta name="pypi:repository-version" content="1.1">
    <title>Links for cowsay</title>
  </head>
  <body>
    <h1>Links for cowsay</h1>
<a href="https://.../cowsay-1.0.tar.gz#sha256=..." >...</a><br />
<a href="https://.../cowsay-3.0-py2.py3-none-any.whl#sha256=..." data-dist-info-...">...</a><br />
</body>
</html>
<!--SERIAL 123-->%
"""


def test_no_good_version():
    with patch("pickley.http_get", return_value="oops"):
        assert latest_pypi_version("some-package", "some-index") is None


def test_parse_versions():
    with patch("pickley.http_get", return_value=SAMPLE_RESPONSE):
        assert latest_pypi_version("some-package", "some-index") == "3.0"
