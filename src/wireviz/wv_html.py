# -*- coding: utf-8 -*-

import re
from pathlib import Path
from typing import Callable, Dict, List, Union

from jinja2 import FileSystemLoader

from wireviz import APP_NAME, APP_URL, __version__, wv_colors
from wireviz.DataClasses import Metadata, Options
from wireviz.svgembed import data_URI_base64
from wireviz.wv_gv_html import html_line_breaks
from wireviz.wv_helper import (
    file_read_text,
    file_write_text,
    flatten2d,
    smart_file_resolve,
)


# embed SVG diagram (only if used)
def svgdata(filename: str | Path) -> str:
    return re.sub(  # TODO?: Verify xml encoding="utf-8" in SVG?
        "^<[?]xml [^?>]*[?]>[^<]*<!DOCTYPE [^>]*>",
        "<!-- XML and DOCTYPE declarations from SVG file removed -->",
        file_read_text(f"{filename}.tmp.svg"),
        1,
    )

def svgaspectratio(svg):
    pattern = r'viewBox\s*=\s*\"\s*?(.*?)\s*?\"'
    results = re.search(pattern, svg)
    if results:
        (x,y,w,h) = (float(segment) for segment in results[1].split(' '))
        return w / h
    return 1


def generate_html_output(
        filename: Union[str, Path],
        bom_list: List[List[str]],
        metadata: Metadata,
        options: Options,
        source: Union[str, Path] = None,
):
    # load HTML template
    templatename = Path(metadata.get("template", {}).get("name"))
    template_search_paths = [Path(filename).parent, Path(__file__).parent / "templates"]

    if source is not None:
        template_search_paths.insert(0, Path(source).parent)

    if templatename:
        # if relative path to template was provided, check directory of YAML file first, fall back to built-in template directory
        try:
            templatefile = smart_file_resolve(
                f"{templatename}.html", template_search_paths
            )
        except Exception:
            templatefile = smart_file_resolve(
                templatename, template_search_paths
            )
    else:
        # fall back to built-in simple template if no template was provided
        templatefile = Path(__file__).parent / "templates/simple.html"

    match (metadata.get("template", {}).get("type"), templatefile.suffix):
        case ('jinja', _) | (None, '.jinja'):
            generate_html_jinja(templatefile, filename, bom_list, metadata, options)
        case ('legacy', _) | (None, _):
            generate_html_legacy(templatefile, filename, bom_list, metadata, options)
        case (ty, _):
            raise RuntimeError(f'Unknown template type "{ty}"')


def generate_html_jinja(
        templatefile: Path,
        filename: Union[str, Path],
        bom_list: List[List[str]],
        metadata: Metadata,
        options: Options
):
    import jinja2
    jenv = jinja2.Environment(
        loader=jinja2.FileSystemLoader(templatefile.parent),
        autoescape=jinja2.select_autoescape(), trim_blocks=True, lstrip_blocks=True
    )
    template_text = file_read_text(templatefile)
    template = jenv.from_string(template_text)
    svg = svgdata(filename)
    result = template.render(
        generator = f"{APP_NAME} {__version__} - {APP_URL}",
        fontname = options.fontname,
        bgcolor = wv_colors.translate_color(options.bgcolor, "hex"),
        filename = filename,
        bom=bom_list,
        options = options,
        svg = svg,
        svg_aspect_ratio = svgaspectratio,
        data_URI_base64=data_URI_base64,
        **metadata
    )
    file_write_text(f"{filename}.html", result)


def generate_html_legacy(
        templatefile: Path,
        filename: Union[str, Path],
        bom_list: List[List[str]],
        metadata: Metadata,
        options: Options,
):
    html = file_read_text(templatefile)  # TODO?: Warn if unexpected meta charset?

    # generate BOM table
    bom = flatten2d(bom_list)

    # generate BOM header (may be at the top or bottom of the table)
    bom_header_html = "  <tr>\n"
    for item in bom[0]:
        th_class = f"bom_col_{item.lower()}"
        bom_header_html = f'{bom_header_html}    <th class="{th_class}">{item}</th>\n'
    bom_header_html = f"{bom_header_html}  </tr>\n"

    # generate BOM contents
    bom_contents = []
    for row in bom[1:]:
        row_html = "  <tr>\n"
        for i, item in enumerate(row):
            td_class = f"bom_col_{bom[0][i].lower()}"
            row_html = f'{row_html}    <td class="{td_class}">{item}</td>\n'
        row_html = f"{row_html}  </tr>\n"
        bom_contents.append(row_html)

    bom_html = (
            '<table class="bom">\n' + bom_header_html + "".join(bom_contents) + "</table>\n"
    )
    bom_html_reversed = (
            '<table class="bom">\n'
            + "".join(list(reversed(bom_contents)))
            + bom_header_html
            + "</table>\n"
    )

    # prepare simple replacements
    replacements = {
        "<!-- %generator% -->": f"{APP_NAME} {__version__} - {APP_URL}",
        "<!-- %fontname% -->": options.fontname,
        "<!-- %bgcolor% -->": wv_colors.translate_color(options.bgcolor, "hex"),
        "<!-- %filename% -->": str(filename),
        "<!-- %filename_stem% -->": Path(filename).stem,
        "<!-- %bom% -->": bom_html,
        "<!-- %bom_reversed% -->": bom_html_reversed,
        "<!-- %sheet_current% -->": "1",  # TODO: handle multi-page documents
        "<!-- %sheet_total% -->": "1",  # TODO: handle multi-page documents
        "<!-- %template_sheetsize% -->": metadata.get("template", {}).get(
            "sheetsize", ""
        ),
    }

    def replacement_if_used(key: str, func: Callable[[], str]) -> None:
        """Append replacement only if used in html."""
        if key in html:
            replacements[key] = func()

    replacement_if_used("<!-- %diagram% -->", lambda: svgdata(filename))
    replacement_if_used(
        "<!-- %diagram_png_b64% -->", lambda: data_URI_base64(f"{filename}.png")
    )

    # prepare metadata replacements
    if metadata:
        for item, contents in metadata.items():
            if isinstance(contents, (str, int, float)):
                replacements[f"<!-- %{item}% -->"] = html_line_breaks(str(contents))
            elif isinstance(contents, Dict):  # useful for authors, revisions
                for index, (category, entry) in enumerate(contents.items()):
                    if isinstance(entry, Dict):
                        replacements[f"<!-- %{item}_{index + 1}% -->"] = str(category)
                        for entry_key, entry_value in entry.items():
                            replacements[
                                f"<!-- %{item}_{index + 1}_{entry_key}% -->"
                            ] = html_line_breaks(str(entry_value))
                    elif isinstance(entry, (str, int, float)):
                        pass  # TODO?: replacements[f"<!-- %{item}_{category}% -->"] = html_line_breaks(str(entry))

    # perform replacements
    # regex replacement adapted from:
    # https://gist.github.com/bgusach/a967e0587d6e01e889fd1d776c5f3729

    # longer replacements first, just in case
    replacements_sorted = sorted(replacements, key=len, reverse=True)
    replacements_escaped = map(re.escape, replacements_sorted)
    pattern = re.compile("|".join(replacements_escaped))
    html = pattern.sub(lambda match: replacements[match.group(0)], html)

    file_write_text(f"{filename}.html", html)
