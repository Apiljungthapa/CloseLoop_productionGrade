import copy
import random
import asyncio
import aiohttp
from typing import Dict, Any, List, Tuple, Optional
from loguru import logger
import time
from datetime import datetime

from .loader_config import SourceLoaderConfig
from .data_parser import DataParser
from .schema import DocumentSchema


class SourceJsonConfig:
    @staticmethod
    def load_source_from_config(source_name: str) -> SourceLoaderConfig:
        return SourceLoaderConfig(source_name)

    @staticmethod
    def download_payload_from_config(source_name: str, document_path: str) -> Dict:
        config_loader = SourceJsonConfig.load_source_from_config(source_name)
        return config_loader.get_payload("download", additional_fields={"storagePaths": [document_path]})

    @staticmethod
    def get_metadata_model(source_name: str) -> Dict:
        config_loader = SourceJsonConfig.load_source_from_config(source_name)
        return config_loader.load_metadata_schema_from_config()


class DataLoader:
    def __init__(self, base_url, search_url=None, download_url=None) -> None:
        self.base_url = base_url
        self.search_url = search_url
        self.download_url = download_url

    async def _fetch_page_async(
        self,
        session: aiohttp.ClientSession,
        payload: Dict[str, Any],
        page: int,
        header: Dict[str, str],
        metadata_schema_class: Any,
        max_retries: int = 20,
        backoff_factor: int = 2
    ) -> Tuple[int, List[DocumentSchema], int]:
        """
        Fetch a single page of documents asynchronously.

        Args:
            session: aiohttp client session
            payload: Search payload for the request
            page: Page number being fetched
            header: Request headers
            metadata_schema_class: Schema class for parsing metadata
            max_retries: Maximum retry attempts
            backoff_factor: Exponential backoff factor

        Returns:
            Tuple of (page_number, list of documents, total_count)
        """
        url = f"{self.base_url}{self.search_url}"

        for attempt in range(1, max_retries + 1):
            try:
                async with session.post(
                    url,
                    json=payload,
                    headers=header,
                ) as response:
                    if response.status == 200:
                        response_json = await response.json()

                        total_count = response_json.get("totalMatches", 0)
                        page_documents = DataParser.parse_search_response(
                            response_json, metadata_schema_class
                        )
                        logger.debug(f"[Async] Page {page}: fetched {len(page_documents)} documents")
                        return page, page_documents, total_count
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"[Async] API Error: Page {page} | Status: {response.status} | {error_text}"
                        )
                        raise aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=f"Unexpected status code: {response.status}"
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    f"[Async] Attempt {attempt}/{max_retries} failed for page {page}: {e}"
                )
                if attempt < max_retries:
                    sleep_time = backoff_factor ** attempt + random.uniform(0, 0.25)
                    await asyncio.sleep(sleep_time)
                else:
                    logger.error(f"[Async] Max retries reached for page {page}")
                    return page, [], 0

        return page, [], 0

    async def search_document_async(
        self,
        source_name: str,
        extra_params: Dict[str, Any] = None,
        additional_fields: Dict[str, Any] = None,
        max_concurrent: int = 10,
        payload_key: str = "search",
    ) -> Tuple[int, List[DocumentSchema]]:
        """
        Search documents asynchronously using aiohttp for parallel page fetching.

        This method fetches all pages concurrently, significantly improving
        performance for large document sets. The Health Docs provider paginates
        with limit/offset, so page N maps to offset (N-1) * limit.

        Args:
            source_name: Name of the source configuration
            extra_params: Extra parameters for the search query (category, fileFormat)
            additional_fields: Additional fields including limit/offset
            max_concurrent: Maximum number of concurrent requests (default: 10)
            payload_key: Payload template to use ('search' or 'revisions_search')

        Returns:
            Tuple of (total_documents, list of all documents)
        """
        logger.info(f"[Async] Searching '{payload_key}' documents - {extra_params}")

        # Load source config once and reuse for payload and metadata
        source_config = SourceJsonConfig.load_source_from_config(source_name)
        search_payload = source_config.get_payload(payload_key, extra_params, additional_fields)
        metadata_schema_class = source_config.load_metadata_schema_from_config()

        header = {}

        # Create semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_with_semaphore(session, payload, page):
            async with semaphore:
                return await self._fetch_page_async(
                    session, payload, page, header, metadata_schema_class
                )

        all_documents = []
        start_time = time.time()

        page_size = int(additional_fields.get("limit", 100)) if additional_fields else 100
        search_payload["limit"] = page_size
        search_payload["offset"] = 0

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
            # First request to get total document count (reusing _fetch_page_async)
            _, first_page_docs, total_documents = await self._fetch_page_async(
                session, search_payload, 1, header, metadata_schema_class
            )

            if total_documents == 0:
                logger.info("[Async] No documents found")
                return 0, []

            # Add first page docs to results
            all_documents.extend(first_page_docs)

            total_pages = (total_documents // page_size) + (1 if total_documents % page_size != 0 else 0)
            
            """
            how to calculate total pages from total_documents and page_size

            Page        Offset  Documents
            Page 1      0           1–100
            Page 2      100         101–200
            Page 3      200         201–250

            total_documents : the total number of documents found in the search
            page_size : the number of documents available per page

            now from this two value lets see how we can find how many pages are there
            total_documents = 250
            page_size = 100

            first expression:
            (total_documents // page_size) = 250 // 100 = 2
            second expression:
            250 % 100 = 50 Since there are 50 documents remaining, it adds one more page
            so total_pages = 2 + 1 = 3
            """


            # Prepare payloads for remaining pages (skip page 1, already fetched)
            if total_pages > 1:
                page_payloads = []
                for page in range(2, total_pages + 1):
                    # Copy base payload and update offset only
                    page_payload = copy.deepcopy(search_payload)
                    page_payload["offset"] = (page - 1) * page_size
                    page_payloads.append((page, page_payload))

                # Fetch remaining pages concurrently
                tasks = [
                    fetch_with_semaphore(session, payload, page)
                    for page, payload in page_payloads
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"[Async] Task failed with exception: {result}")
                        continue

                    _ , page_docs, _ = result
                    if page_docs:
                        # Filter duplicates
                        unique_documents = [
                            doc for doc in page_docs
                            if not any(
                                existing_doc.id == doc.id
                                for existing_doc in all_documents
                            )
                        ]
                        all_documents.extend(unique_documents)

        elapsed_time = time.time() - start_time
        logger.info(
            f"[Async] Fetched {len(all_documents)}/{total_documents} documents "
            f"({total_pages} pages) in {elapsed_time:.2f}s"
        )

        return total_documents, all_documents

    @staticmethod
    def _download_error(doc: DocumentSchema, error: str) -> Dict:
        """Build a standard error record for a failed download."""
        return {
            "doc_id": doc.id,
            "doc_name": doc.metadata.title if doc.metadata else doc.id,
            "error": error,
            "doc_metadata": {
                "id": doc.id,
                "file_path": doc.file_path,
                "file_format": doc.metadata.fileFormat if doc.metadata else "",
                "category": doc.metadata.category if doc.metadata else "",
                "metadata": doc.metadata.model_dump() if doc.metadata else {},
            },
        }

    async def _download_doc_async(
        self,
        session: aiohttp.ClientSession,
        source: str,
        doc: DocumentSchema,
        header: Dict[str, str],
        max_retries: int = 5,
        backoff_factor: int = 3
    ) -> Tuple[DocumentSchema, Optional[Dict]]:
        """
        Download a single document asynchronously.

        The Health Docs provider fetch endpoint returns JSON with a 'documents'
        list; each entry has 'found' plus the raw document body in 'content'.

        Args:
            session: aiohttp client session
            source: Source name for config lookup
            doc: Document to download
            header: Request headers
            max_retries: Maximum retry attempts
            backoff_factor: Exponential backoff factor

        Returns:
            Tuple of (document with text, error_dict or None)
        """
        url = f"{self.base_url}{self.download_url}"
        payload = SourceJsonConfig.download_payload_from_config(source, doc.file_path)

        for attempt in range(1, max_retries + 1):
            try:
                async with session.post(
                    url,
                    json=payload,
                    headers=header,
                ) as response:
                    if response.status == 200:
                        response_json = await response.json()
                        entries = response_json.get("documents", [])
                        entry = entries[0] if entries else {}

                        if not entry.get("found"):
                            reason = entry.get("reason", "No document registered at this storagePath")
                            logger.warning(
                                f"[Async] Download Not Found: Doc {doc.id} | {reason}"
                            )
                            return doc, self._download_error(doc, f"Download failed: {reason}")

                        content = entry.get("content", "")
                        doc.text, doc.processed_text = None, None

                        doc.text = content
                        doc.processed_text = content

                        return doc, None
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"[Async] Download Error: Doc {doc.id} | Status: {response.status} | {error_text}"
                        )
                        raise aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=f"Download failed with status: {response.status}"
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    f"[Async] Download attempt {attempt}/{max_retries} failed for doc {doc.id}: {e}"
                )
                if attempt < max_retries:
                    sleep_time = backoff_factor ** attempt + random.uniform(0, 0.25)
                    await asyncio.sleep(sleep_time)
                else:
                    logger.error(f"[Async] Max retries reached for downloading doc {doc.id}")
                    return doc, self._download_error(
                        doc, f"Download Failed after {max_retries} retries: {str(e)}"
                    )

        return doc, {"doc_id": doc.id, "error": "Unknown error"}

    async def download_documents(
        self,
        source_name: str,
        docs: Dict[str, List[DocumentSchema]],
        max_concurrent: int = 10
    ) -> Tuple[List[DocumentSchema], List[str], List[Dict]]:
        """
        Download documents asynchronously using aiohttp for parallel fetching.

        Args:
            source_name: Source name for config lookup
            docs: Dictionary with a 'documents' list
            max_concurrent: Maximum number of concurrent downloads (default: 10)

        Returns:
            Tuple of (downloaded_docs, download_failed_docs, download_failed_docs_list)
        """
        all_docs = docs.get('documents', [])
        logger.info(f"[Async] Downloading {len(all_docs)} documents")

        if not all_docs:
            return [], [], []

        header = {}

        # Create semaphore to limit concurrent downloads
        semaphore = asyncio.Semaphore(max_concurrent)

        async def download_with_semaphore(session, doc):
            async with semaphore:
                return await self._download_doc_async(session, source_name, doc, header)

        downloaded_results = []
        download_failed_docs = []
        download_failed_docs_list = []

        start_time = time.time()

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
            # Download all documents concurrently
            tasks = [download_with_semaphore(session, doc) for doc in all_docs]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for i, result in enumerate(results):
                doc = all_docs[i]

                if isinstance(result, Exception):
                    logger.exception(f"[Async] Download task failed with exception: {result}")
                    download_failed_docs_list.append(
                        self._download_error(doc, f"Download Failed: {str(result)}")
                    )
                    download_failed_docs.append(f"Document name: {doc.metadata.title if doc.metadata else doc.id}")
                    continue

                downloaded_doc, error = result

                if error:
                    download_failed_docs_list.append(error)
                    download_failed_docs.append(f"Document name: {doc.metadata.title if doc.metadata else doc.id}")
                else:
                    downloaded_results.append(downloaded_doc)

        # Sort results by id
        downloaded_results.sort(key=lambda d: d.id)

        elapsed_time = time.time() - start_time
        logger.info(
            f"[Async] Downloaded {len(downloaded_results)}/{len(all_docs)} documents "
            f"in {elapsed_time:.2f}s ({len(download_failed_docs)} failed)"
        )

        return downloaded_results, download_failed_docs, download_failed_docs_list

    async def search_all_revisions_async(
        self,
        source_name: str,
        file_format: str = None,
        category: str = None,
        max_concurrent: int = 10,
    ) -> List[DocumentSchema]:
        """
        Search all revisions (every stored version) of documents for the given
        fileFormat/category. 
        """
        logger.info(
            f"[Async] Started searching all revisions - fileFormat={file_format}, category={category}"
        )

        extra_params: Dict[str, Any] = {}
        if file_format:
            extra_params['fileFormat'] = file_format
        if category:
            extra_params['category'] = category

        _total, revisions = await self.search_document_async(
            source_name=source_name,
            extra_params=extra_params,
            additional_fields={"limit": "100"},
            max_concurrent=max_concurrent,
            payload_key="revisions_search",
        )
        return revisions

    @staticmethod
    def build_versions_map(revisions: List[DocumentSchema]) -> Tuple[Dict[str, List[Dict[str, Any]]], int]:
        """
        Build a map of recordId -> list of revision entries (sorted by revision),
        used to attach version_history metadata to each document.
        """
        versions_map: Dict[str, Dict[str, Dict[str, Any]]] = {}
        duplicate_count = 0

        def _to_iso(v: Any) -> Optional[str]:
            if v is None:
                return None
            if isinstance(v, datetime):
                try:
                    return v.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    return str(v)
            return str(v)

        for rev in revisions or []:
            try:
                meta = getattr(rev, 'metadata', None)
                record_id = getattr(meta, 'recordId', None) if meta is not None else None
                if not record_id:
                    continue
                data_id = getattr(meta, 'dataId', None) if meta is not None else None
                fallback = getattr(rev, 'id', None) or getattr(rev, 'file_path', None) or ''
                key = data_id if data_id else fallback
                if not key:
                    continue
                entry = {
                    "dataId": key,
                    "revision": getattr(meta, 'revision', None),
                    "file_path": getattr(rev, 'file_path', None),
                    "isLatest": getattr(meta, 'isLatest', None),
                    "publishedOn": _to_iso(getattr(meta, 'publishedOn', None)),
                    "lastRevisedOn": _to_iso(getattr(meta, 'lastRevisedOn', None)),
                    "validUntil": _to_iso(getattr(meta, 'validUntil', None)),
                }
                record_bucket = versions_map.setdefault(record_id, {})
                if key in record_bucket:
                    duplicate_count += 1
                    logger.debug(
                        f"Duplicate key '{key}' under recordId '{record_id}' - overwriting with newer revision"
                    )
                record_bucket[key] = entry
            except Exception as e:
                logger.warning(
                    f"Failed to process revision {getattr(rev, 'id', 'unknown')} in build_versions_map: {e}"
                )

        sorted_map: Dict[str, List[Dict[str, Any]]] = {} # sorting the versions by revision number 
        for _rid, _bucket in versions_map.items():
            sorted_map[_rid] = sorted(
                _bucket.values(),
                key=lambda e: (e.get("revision") or 0)
            )

        return sorted_map, duplicate_count
