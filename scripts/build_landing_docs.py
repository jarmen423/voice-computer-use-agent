#!/usr/bin/env python3
"""Build landing-page/docs/ from docs/*.md using the custom site styling.

This converts the Markdown source docs into the flat-HTML docs style used by the
landing page (landing-page/docs/*.html). It preserves the landing-page look by
wrapping content in the same nav/sidebar/footer template.

Run from repo root:
    python scripts/build_landing_docs.py
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import markdown

DOCS_DIR = Path("docs")
OUTPUT_DIR = Path("landing-page/docs")

# Order and labels for the docs sidebar.
DOCS_PAGES = [
    ("index.md", "Getting Started"),
    ("installation.md", "Installation"),
    ("configuration.md", "Configuration"),
    ("usage.md", "Usage"),
    ("plugins.md", "Plugins"),
    ("safety.md", "Safety"),
    ("troubleshooting.md", "Troubleshooting"),
    ("development.md", "Development"),
    ("architecture.md", "Architecture"),
]

ADMONITION_CLASSES = {
    "note": "tip",
    "tip": "tip",
    "warning": "warning",
    "danger": "warning",
    "info": "tip",
}


def parse_frontmatter(text: str) -> tuple[str, str]:
    """Strip a leading YAML frontmatter block and return (body, frontmatter)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n"), parts[1]
    return text, ""


def convert_keys(text: str) -> str:
    """Convert ++key++ syntax to <kbd>key</kbd>."""
    return re.sub(r"\+\+([^+]+)\+\+", r"<kbd>\1</kbd>", text)


def convert_admonitions(text: str) -> str:
    """Convert pymdownx admonitions to custom docs-callout HTML."""
    pattern = re.compile(
        r"^!!!\s+(?P<type>\w+)\s+\"(?P<title>[^\"]+)\"\n"
        r"(?P<body>(?:^[ \t]{4}.*\n?)*)",
        re.MULTILINE,
    )

    def replace(match: re.Match[str]) -> str:
        kind = match.group("type").lower()
        title = match.group("title")
        body = match.group("body")
        # Remove the 4-space indent from each line.
        lines = [line[4:] if line.startswith("    ") else line for line in body.splitlines(keepends=True)]
        body_unindented = "".join(lines).strip("\n")
        body_html = _basic_markdown(body_unindented)
        callout_class = ADMONITION_CLASSES.get(kind, "tip")
        return f'<div class="docs-callout {callout_class}">\n<div class="docs-callout-title">{title}</div>\n{body_html}\n</div>\n'

    return pattern.sub(replace, text)


def convert_tabs(text: str) -> str:
    """Convert pymdownx tabbed blocks to sequential <h3> sections."""
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r'^(={3,})\s+"([^"]+)"\s*$', line)
        if not match:
            result.append(line)
            i += 1
            continue

        title = match.group(2)
        result.append(f"\n### {title}\n")
        i += 1

        # Collect indented content until next non-indented/non-empty line or next tab.
        tab_body: list[str] = []
        while i < len(lines):
            current = lines[i]
            if current.strip() == "":
                tab_body.append(current)
                i += 1
                continue
            if re.match(r'^(={3,})\s+"', current):
                break
            if not current.startswith("    "):
                break
            tab_body.append(current[4:])
            i += 1

        # Trim leading/trailing blank lines from the tab body.
        while tab_body and tab_body[0].strip() == "":
            tab_body.pop(0)
        while tab_body and tab_body[-1].strip() == "":
            tab_body.pop()

        result.extend(tab_body)
        result.append("\n")

    return "".join(result)


def protect_mermaid(text: str) -> tuple[str, dict[str, str]]:
    """Replace mermaid code blocks with HTML-comment placeholders."""
    placeholders: dict[str, str] = {}
    counter = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal counter
        content = match.group("content")
        key = f"MERMAID_PLACEHOLDER_{counter}"
        counter += 1
        placeholders[key] = f'<div class="mermaid">\n{content}</div>\n'
        return f"\n<!-- {key} -->\n"

    pattern = re.compile(
        r"^```mermaid\n(?P<content>.*?)^```\s*$",
        re.MULTILINE | re.DOTALL,
    )
    return pattern.sub(replace, text), placeholders


def _strip_markdown(text: str) -> str:
    """Remove simple Markdown emphasis from a string for meta descriptions."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def restore_mermaid(html: str, placeholders: dict[str, str]) -> str:
    for key, value in placeholders.items():
        html = html.replace(f"<!-- {key} -->", value)
    return html


def convert_md_links(html: str) -> str:
    """Convert internal .md links to .html."""
    # Match href="...md" or href='...md' optionally with anchor.
    html = re.sub(r'href="([^"]+)\.md(#[^"]+)?"', r'href="\1.html\2"', html)
    html = re.sub(r"href='([^']+)\.md(#[^']+)?'", r"href='\1.html\2'", html)
    return html


def _basic_markdown(text: str) -> str:
    """Run Markdown on text that has already had admonitions/tabs processed."""
    text, mermaid_placeholders = protect_mermaid(text)
    text = convert_keys(text)

    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "toc",
        ],
    )
    html = md.convert(text)
    html = restore_mermaid(html, mermaid_placeholders)
    html = convert_md_links(html)
    return html


def markdown_to_html(text: str) -> str:
    """Convert preprocessed Markdown to HTML."""
    text = convert_admonitions(text)
    text = convert_tabs(text)
    return _basic_markdown(text)


def page_template(
    *,
    title: str,
    description: str,
    content: str,
    active_page: str,
    breadcrumb: list[tuple[str | None, str]],
    include_mermaid: bool,
) -> str:
    sidebar_links = "\n".join(
        f'<a href="{src.replace(".md", ".html")}"{" class=\"active\"" if src == active_page else ""}>{label}</a>'
        for src, label in DOCS_PAGES
    )

    breadcrumb_html = "\n".join(
        f'<a href="{href}">{label}</a>' if href else f"<span>{label}</span>"
        for href, label in breadcrumb
    )
    # Insert separators between breadcrumb items.
    parts = []
    for href, label in breadcrumb:
        parts.append(f'<a href="{href}">{label}</a>' if href else f"<span>{label}</span>")
    breadcrumb_html = '\n<span class="docs-breadcrumb-sep">/</span>\n'.join(parts)

    mermaid_script = ""
    if include_mermaid:
        mermaid_script = (
            '\n  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>'
            '\n  <script>mermaid.initialize({ startOnLoad: true, theme: \'dark\', themeVariables: { primaryColor: \'#00f0ff\', primaryTextColor: \'#e8e8f0\', primaryBorderColor: \'#00f0ff\', lineColor: \'#6a6a80\', secondaryColor: \'#1a1a24\', tertiaryColor: \'#111118\', fontFamily: \'JetBrains Mono\' }});</script>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — VoiceUse Docs</title>
  <meta name="description" content="{description}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700;800&family=Space+Grotesk:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="../styles.css">
</head>
<body>
  <nav class="nav" id="nav">
    <div class="nav-inner">
      <a href="../index.html" class="logo">
        <span class="logo-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/></svg>
        </span>
        <span class="logo-text">VoiceUse</span>
      </a>
      <button class="nav-toggle" id="navToggle" aria-label="Toggle navigation" aria-expanded="false">
        <span></span><span></span><span></span>
      </button>
      <ul class="nav-links" id="navLinks">
        <li><a href="../index.html#features">Features</a></li>
        <li><a href="../index.html#how-it-works">How It Works</a></li>
        <li><a href="../index.html#downloads">Download</a></li>
        <li><a href="../index.html#plugins">Plugins</a></li>
        <li><a href="../index.html#safety">Safety</a></li>
        <li><a href="index.html" class="active">Docs</a></li>
        <li><a href="https://github.com/jarmen423/voice-computer-use-agent" target="_blank" rel="noopener" class="nav-cta">GitHub</a></li>
      </ul>
    </div>
  </nav>

  <div class="docs-layout">
    <aside class="docs-sidebar">
      <div class="docs-sidebar-title">Documentation</div>
      <nav class="docs-nav">
{sidebar_links}
      </nav>
    </aside>

    <main class="docs-content">
      <div class="docs-breadcrumb">
{breadcrumb_html}
      </div>

{content}
    </main>
  </div>

  <footer class="footer">
    <div class="container">
      <div class="footer-inner">
        <div class="footer-brand">
          <span class="logo-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/></svg>
          </span>
          <span>VoiceUse</span>
        </div>
        <div class="footer-links">
          <a href="https://github.com/jarmen423/voice-computer-use-agent" target="_blank" rel="noopener">GitHub</a>
          <a href="https://github.com/jarmen423/voice-computer-use-agent/issues" target="_blank" rel="noopener">Issues</a>
          <a href="https://github.com/jarmen423/voice-computer-use-agent/releases" target="_blank" rel="noopener">Releases</a>
          <a href="https://github.com/jarmen423/voice-computer-use-agent/blob/main/LICENSE" target="_blank" rel="noopener">MIT License</a>
        </div>
        <p class="footer-copy">Open source under MIT License. Built by VoiceUse contributors.</p>
      </div>
    </div>
  </footer>

  <script src="../script.js"></script>{mermaid_script}
</body>
</html>
"""


def build(source_dir: Path, output_dir: Path, clean: bool = True) -> None:
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for src_file, label in DOCS_PAGES:
        src_path = source_dir / src_file
        if not src_path.exists():
            print(f"Warning: {src_path} not found, skipping.", file=sys.stderr)
            continue

        text = src_path.read_text(encoding="utf-8")
        body, _frontmatter = parse_frontmatter(text)

        # Derive a plain-text description from the first paragraph.
        first_para_match = re.search(r"^\s*([A-Z][^\n]{40,200})\.", body, re.MULTILINE)
        description = (
            _strip_markdown(first_para_match.group(1)) + "."
            if first_para_match
            else f"VoiceUse {label.lower()} documentation."
        )

        content_html = markdown_to_html(body)
        active_page = src_file

        if src_file == "index.md":
            breadcrumb = [("../index.html", "VoiceUse"), (None, "Docs")]
        else:
            breadcrumb = [
                ("../index.html", "VoiceUse"),
                ("index.html", "Docs"),
                (None, label),
            ]

        include_mermaid = "```mermaid" in body

        html = page_template(
            title=label,
            description=description,
            content=content_html,
            active_page=active_page,
            breadcrumb=breadcrumb,
            include_mermaid=include_mermaid,
        )

        out_file = output_dir / src_file.replace(".md", ".html")
        out_file.write_text(html, encoding="utf-8")
        print(f"Wrote {out_file}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build landing-page docs from Markdown source.")
    parser.add_argument("--source", type=Path, default=DOCS_DIR, help="Source docs directory")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR, help="Output docs directory")
    parser.add_argument("--no-clean", action="store_true", help="Don't wipe output directory first")
    args = parser.parse_args()

    build(args.source, args.output, clean=not args.no_clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
