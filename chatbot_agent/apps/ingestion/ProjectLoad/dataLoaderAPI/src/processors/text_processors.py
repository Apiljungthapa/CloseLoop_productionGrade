import re
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from html_to_markdown import ConversionOptions, convert
from .base import TextProcessor

# XML records are intentionally parsed with html.parser (lowercased tags)
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


# All Unicode space/whitespace characters to normalize to standard space (0x20)
UNICODE_SPACES_PATTERN = re.compile(
    "[          "
    "   ​  　﻿]"
)


class HtmlToTextProcessor(TextProcessor):

    supported_formats = ["html", "htm"]

    @staticmethod
    def normalize_unicode_spaces(text: str) -> str:
        """Replace all Unicode space characters with standard ASCII space (0x20)."""
        return UNICODE_SPACES_PATTERN.sub(" ", text)

    def process(self, text: str, **kwargs) -> tuple[str, dict]:
        reader = BeautifulSoup(text, "html.parser")

        # Drop non-content elements before conversion
        for tag in reader.find_all(["style", "script"]):
            tag.decompose()

        # Remove images, but preserve <br> so the converter can handle line breaks
        config = ConversionOptions(strip_tags=["img"])

        # Preserve the HTML structure for Markdown conversion
        processed_text = str(reader)

        # Normalize line endings while preserving newlines
        processed_text = re.sub(r"\r\n|\r", "\n", processed_text)

        # Remove Unicode replacement characters caused by invalid encoding
        processed_text = processed_text.replace("�", " ")

        # Convert HTML to Markdown
        processed_text = convert(processed_text, config)

        # Remove any remaining HTML tags
        processed_text = re.sub(r"<[^>]+>", "", processed_text)

        # Normalize trailing whitespace on each line
        processed_text = "\n".join(
            line.rstrip() for line in processed_text.splitlines()
        )

        # Prevent excessive blank lines
        processed_text = re.sub(r"\n{3,}", "\n\n", processed_text)

        # Normalize remaining Unicode spaces
        processed_text = self.normalize_unicode_spaces(processed_text)

        # Remove leading/trailing whitespace
        processed_text = processed_text.strip()

        return processed_text, {}

class XmlToTextProcessor(TextProcessor):
    """Extracts clean text from Health Docs XML records (<healthRecord>)."""

    supported_formats = ["xml"]

    @staticmethod
    def normalize_unicode_spaces(text: str) -> str:
        """Replace all Unicode space characters with standard ASCII space (0x20)."""
        return UNICODE_SPACES_PATTERN.sub(" ", text)

    def process(self, text: str, **kwargs) -> tuple[str, dict]:
        # html.parser lowercases XML tags (recordheader, gridrow, ...)
        soup = BeautifulSoup(text, "html.parser")

        parts = []

        title = soup.find("title")
        if title:
            parts.append(f"# {title.get_text(strip=True)}")

        for chapter in soup.find_all("chapter"):
            heading = chapter.get("heading", "")
            if heading:
                parts.append(f"## {heading}")
            for child in chapter.children:
                if getattr(child, "name", None) is None:
                    continue
                if child.name == "passage":
                    parts.append(child.get_text(strip=True))
                elif child.name == "itemlist":
                    for item in child.find_all("item"):
                        parts.append(f"- {item.get_text(strip=True)}")
                elif child.name == "datagrid":
                    caption = child.get("caption", "")
                    if caption:
                        parts.append(caption)
                    for row in child.find_all("gridrow"):
                        if row.get("kind") == "header":
                            gc = row.find_all("gridcell")
                            header = [f"| {cell.get_text(strip=True)} |" if gc.index(cell) == (len(gc)-1) else f"| {cell.get_text(strip=True)} " for cell in gc]
                            border= "|---"*len(header)+"|"
                            parts.append("".join(header))
                            parts.append(border)
                        else:
                            gc = row.find_all("gridcell")
                            cell_elements = [f"| {cell.get_text(strip=True)} |" if gc.index(cell) == (len(gc)-1) else f"| {cell.get_text(strip=True)} " for cell in gc]
                            parts.append("".join(cell_elements))
                else:
                    parts.append(child.get_text(strip=True))

        notice_block = soup.find("noticeblock")
        if notice_block:
            parts.append("## Notices")
            for notice in notice_block.find_all("notice"):
                parts.append(f"[{notice.get('level', 'info')}] {notice.get_text(strip=True)}")

        processed_text = "\n".join(parts)
        processed_text = "".join(
            [char if ord(char) < 128 or char in ("–", "—") else " " for char in processed_text]
        )
        processed_text = self.normalize_unicode_spaces(processed_text)

        return processed_text, {}
