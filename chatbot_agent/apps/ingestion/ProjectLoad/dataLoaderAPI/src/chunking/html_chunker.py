"""HTML child chunker: chunks documents by their configured section headers."""
import json
import os

from bs4 import BeautifulSoup, NavigableString
from loguru import logger

from .base import BaseChunker

# Section-header config file per HTML document type
HTML_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "html_config"
)
CONFIG_FILES = {
    "html": "health_docs.json",
    "htm": "health_docs.json",
}


class HTMLChunker(BaseChunker):
    """
    Chunks HTML documents by their section headers (tags/classes defined in
    the html_config JSON for the document type). Tables are rendered with
    #table_start / #table_end markers.
    """

    def __init__(self, document_type: str) -> None:
        config_file = CONFIG_FILES.get((document_type or "").lower())
        if not config_file:
            raise ValueError(
                f"Invalid document type '{document_type}'. Valid options are: {', '.join(CONFIG_FILES)}"
            )
        file_path = os.path.join(HTML_CONFIG_DIR, config_file)
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                self.html_config = json.load(file)
        except FileNotFoundError:
            raise FileNotFoundError(f"Error: Configuration file '{file_path}' not found.")
        except json.JSONDecodeError:
            raise ValueError(f"Error: Failed to parse JSON file '{file_path}'.")

    @staticmethod
    def extract_all_text(element) -> str:
        """Concatenate all text content from an element, stripping whitespace."""
        try:
            return ''.join(element.stripped_strings)
        except AttributeError as e:
            logger.error(f"Error in extract_all_text: {str(e)} | Type: {type(e).__name__}")
            return ""

    @staticmethod
    def extract_table(element) -> str:
        """Render an HTML table with #table_start / #table_end markers,
        each row's cells comma-separated."""
        try:
            rows = []
            for row in element.find_all('tr'):
                cells = [cell.get_text().strip() + " " for cell in row.find_all(['td', 'th'])]
                rows.append(", ".join(cells))
            return '\n#table_start\n' + "\n".join(rows) + '\n#table_end\n'
        except AttributeError as e:
            logger.error(f"Error in extract_table: {str(e)} | Type: {type(e).__name__}")
            return ""

    def extract_list(self, element, indent: int = 0) -> str:
        """Render a <ul>/<ol> as one line per item ('-' bullets or '1.' numbering),
        indenting nested lists two spaces per level."""
        try:
            ordered = element.name == 'ol'
            try:
                start = int(element.get('start', 1))
            except (TypeError, ValueError):
                start = 1
            # Only this list's own items - nested lists are handled recursively
            items = [li for li in element.find_all('li') if li.find_parent(['ul', 'ol']) is element]
            lines = []
            for number, li in enumerate(items, start=start):
                # The item's own text, excluding text that belongs to a nested list
                own_text = ' '.join(
                    ns.strip() for ns in li.strings
                    if ns.strip() and ns.find_parent(['ul', 'ol']) is element
                )
                marker = f"{number}." if ordered else "-"
                lines.append(("  " * indent + f"{marker} {own_text}").rstrip())
                for nested in li.find_all(['ul', 'ol']):
                    if nested.find_parent('li') is li:
                        lines.append(self.extract_list(nested, indent + 1))
            return "\n".join(lines)
        except AttributeError as e:
            logger.error(f"Error in extract_list: {str(e)} | Type: {type(e).__name__}")
            return ""

    def _element_text(self, element) -> str:
        """Plain text for one element; tables and lists keep their structure."""
        if isinstance(element, NavigableString):
            text = element.strip()
            return text + " " if text else ""
        if element.name == 'table':
            return str(self.extract_table(element)) + "\n"
        if element.name in ('ul', 'ol'):
            return "\n" + self.extract_list(element) + "\n"
        # Wrappers holding a table or list: walk children so structure survives
        if hasattr(element, 'find') and element.find(['table', 'ul', 'ol']):
            return ''.join(self._element_text(child) for child in element.children)
        return self.extract_all_text(element) + " "

    def extract_sections(self, soup: BeautifulSoup):
        main_title = soup.find(class_=self.html_config["main_title"]["classes"]) or \
            soup.find(self.html_config["main_title"]["tags"])
        main_title_text = main_title.get_text(strip=True) if main_title else ""

        # Find section-header elements by configured classes, falling back to tags
        section_elements = []
        for cls in self.html_config["section_header"]["classes"]:
            if soup.find(class_=cls):
                section_elements = soup.find_all(class_=cls)
                break
        if not section_elements:
            for tag in self.html_config["section_header"]["tags"]:
                if soup.find(tag):
                    section_elements = soup.find_all(tag)
                    break
        if not section_elements:
            return main_title_text, []

        sections = []

        # Content before the first section header becomes an unnamed section
        initial_content = ""
        for sibling in section_elements[0].previous_siblings:
            if not isinstance(sibling, NavigableString):
                # NavigableString is text between tags (often just \n) - skipped
                if self.extract_all_text(sibling) != main_title_text:
                    initial_content = self._element_text(sibling) + initial_content
        if initial_content.strip():
            sections.append(("", initial_content.strip()))

        for i, section in enumerate(section_elements):
            # The section element may be a container (e.g. <section>) wrapping
            # its heading AND content - use the inner heading as the name
            heading_el = section.find(["h1", "h2", "h3", "h4", "h5", "h6"])
            section_text = (heading_el or section).get_text(strip=True)

            content = ""
            if heading_el is not None:
                # Container-style section: content is inside the element
                for child in section.children:
                    if child is heading_el:
                        continue
                    content += self._element_text(child)
            else:
                # Flat header: content is between this heading and the next one
                next_section = section_elements[i + 1] if i + 1 < len(section_elements) else None
                current_element = section.next_sibling
                while current_element and (next_section is None or current_element != next_section):
                    content += self._element_text(current_element)
                    current_element = current_element.next_sibling

            section_content = section_text + "\n" + content.strip() if content.strip() else section_text
            sections.append((section_text, section_content))

        return main_title_text, sections
