from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str, str]] = []
        self.images: list[dict[str, str]] = []
        self.headings: list[str] = []
        self.ids: set[str] = set()
        self.i18n_keys: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        for attribute in ("data-i18n", "data-i18n-html", "data-i18n-aria", "data-i18n-alt", "data-i18n-src"):
            if values.get(attribute):
                self.i18n_keys.add(values[attribute])
        if values.get("id"):
            self.ids.add(values["id"])
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.headings.append(tag)
        if tag == "img":
            self.images.append(values)
        for attribute in ("href", "src"):
            value = values.get(attribute)
            if value:
                self.references.append((tag, attribute, value))


def parse_page(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def validate_reference(site_dir: Path, page: Path, value: str, errors: list[str]) -> None:
    parsed = urlparse(value)
    if parsed.scheme in {"https", "mailto"} or value.startswith("//"):
        return
    if parsed.scheme or value.startswith("data:"):
        errors.append(f"{page}: unsupported URL scheme in {value!r}")
        return
    if value.startswith("#"):
        return
    if value.startswith("/TokenMeter/"):
        target = site_dir / value.removeprefix("/TokenMeter/")
    elif value.startswith("/"):
        errors.append(f"{page}: root-relative URL is not /TokenMeter/-safe: {value!r}")
        return
    else:
        target = page.parent / parsed.path
    if parsed.path.endswith("/"):
        target = target / "index.html"
    if parsed.path and not target.resolve().is_file():
        errors.append(f"{page}: missing local target for {value!r}")


def validate_html(site_dir: Path, page: Path, errors: list[str]) -> None:
    parser = parse_page(page)
    for _, _, value in parser.references:
        validate_reference(site_dir, page, value, errors)
    for image in parser.images:
        if "alt" not in image:
            errors.append(f"{page}: image is missing alt text: {image.get('src', '<unknown>')}")
        if not image.get("width") or not image.get("height"):
            errors.append(f"{page}: image is missing width/height: {image.get('src', '<unknown>')}")
    if page.name == "index.html":
        if parser.headings.count("h1") != 1:
            errors.append(f"{page}: expected exactly one h1")
        # The landing page is intentionally limited to the redesigned narrative sections.
        for required_id in {"main-content", "product", "advantages", "privacy", "personalization", "download"}:
            if required_id not in parser.ids:
                errors.append(f"{page}: missing required id #{required_id}")


def validate_css(site_dir: Path, errors: list[str]) -> None:
    css_path = site_dir / "styles.css"
    css = css_path.read_text(encoding="utf-8")
    for value in re.findall(r"url\((?:['\"])?([^)'\"]+)", css):
        validate_reference(site_dir, css_path, value, errors)
    if "prefers-reduced-motion" not in css:
        errors.append(f"{css_path}: missing prefers-reduced-motion handling")


def validate_locales(site_dir: Path, index: PageParser, errors: list[str]) -> None:
    required_keys = index.i18n_keys | {"meta_title", "meta_description"}
    for locale in ("zh-CN", "zh-TW", "en", "ja", "ko"):
        path = site_dir / "locales" / f"{locale}.json"
        if not path.is_file():
            errors.append(f"missing locale file: {path}")
            continue
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: invalid locale JSON: {exc}")
            continue
        missing = sorted(key for key in required_keys if not isinstance(values.get(key), str) or not values[key])
        if missing:
            errors.append(f"{path}: missing translations: {', '.join(missing)}")
        # Screenshot paths live in locale JSON but resolve against the page, not the locale directory.
        for key, value in values.items():
            if key.endswith("_src") and isinstance(value, str):
                validate_reference(site_dir, site_dir / "index.html", value, errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the static GitHub Pages artifact.")
    parser.add_argument("--site", type=Path, default=Path("site"))
    args = parser.parse_args()
    site_dir = args.site.resolve()
    errors: list[str] = []

    for required in ("index.html", "404.html", "favicon.ico", "styles.css", "script.js", ".nojekyll", "sitemap.xml"):
        if not (site_dir / required).is_file():
            errors.append(f"missing required site file: {required}")

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    index = parse_page(site_dir / "index.html")
    for page in (site_dir / "index.html", site_dir / "404.html"):
        validate_html(site_dir, page, errors)
    validate_css(site_dir, errors)
    validate_locales(site_dir, index, errors)

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    print(f"GitHub Pages validation passed: {site_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
