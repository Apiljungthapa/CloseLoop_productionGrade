"""XML child chunker: chunks health records by <chapter> sections."""
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from loguru import logger

from .base import BaseChunker

# XML records are intentionally parsed with html.parser (lowercased tags)
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


class XMLChunker(BaseChunker):
    """
    Chunks XML health records by <chapter heading="..."> sections, with
    notices as a final unnamed section. Data grids are rendered with the
    same #table_start / #table_end markers as HTML tables.
    """

    @staticmethod
    def extract_data_grid(element) -> str:
        """Render a <dataGrid> with #table_start / #table_end markers,
        each gridRow's gridCells comma-separated."""
        try:
            rows = []
            caption = element.get('caption', '')
            if caption:
                rows.append(caption+"\n")
            for row in element.find_all('gridrow'):
                if row.get("kind") == "header":
                    gc = row.find_all("gridcell")
                    header = [f"| {cell.get_text(strip=True)} |" if gc.index(cell) == (len(gc)-1) else f"| {cell.get_text(strip=True)} " for cell in gc]
                    border= "|---"*len(header)+"|"
                    rows.append("".join(header)+"\n")
                    rows.append(border+"\n")
                else:
                    gc = row.find_all("gridcell")
                    cell_elements = [f"| {cell.get_text(strip=True)} |" if gc.index(cell) == (len(gc)-1) else f"| {cell.get_text(strip=True)} " for cell in gc]
                    rows.append("".join(cell_elements)+"\n")
            
            final_table_data = "\n"+"".join(rows)+"\n"
            return final_table_data
        except AttributeError as e:
            logger.error(f"Error in extract_data_grid: {str(e)} | Type: {type(e).__name__}")
            return ""

    def extract_sections(self, soup: BeautifulSoup):
        main_title = soup.find("title")
        main_title_text = main_title.get_text(strip=True) if main_title else ""

        sections = []

        for chapter in soup.find_all("chapter"):
            heading = chapter.get("heading", "")

            content = ""
            for child in chapter.children:
                if getattr(child, 'name', None) is None:
                    continue
                if child.name == 'datagrid':
                    content += str(self.extract_data_grid(child)) + "\n"
                elif child.name == 'itemlist':
                    items = [f"- {item.get_text(strip=True)}" for item in child.find_all('item')]
                    content += "\n".join(items) + "\n"
                else:
                    content += child.get_text(strip=True) + " "

            section_content = heading + "\n" + content.strip() if content.strip() else heading
            sections.append((heading, section_content))

        # Notices behave like a final section
        notice_block = soup.find("noticeblock")
        if notice_block:
            notices = [
                f"[{notice.get('level', 'info')}] {notice.get_text(strip=True)}"
                for notice in notice_block.find_all("notice")
            ]
            sections.append(("", "Notices\n" + "\n".join(notices)))

        return main_title_text, sections
