import { reportStudioAPI } from '../../../apiEntities.js';

// Download one file version of a report. The server decides the file name (ASCII, descriptive) and writes the
// download to the audit log; this only turns the response into a browser download.

function fileNameFrom(disposition, fallback) {
  const m = /filename="?([^";]+)"?/i.exec(disposition || '');
  return m ? m[1] : fallback;
}

// A failed Blob request carries its JSON error as a Blob: read the message out of it.
async function messageFrom(error) {
  const data = error?.response?.data;
  try {
    const text = data instanceof Blob ? await data.text() : null;
    const detail = text ? JSON.parse(text).detail : data?.detail;
    if (typeof detail === 'string') return detail;
  } catch {
    /* not JSON */
  }
  return error?.response?.status === 403
    ? 'You do not have access to that report.'
    : 'Could not build the file. Please try again.';
}

export const FORMAT_LABEL = { pdf: 'PDF', xlsx: 'Excel', csv: 'CSV' };

export async function downloadReport(spec, format, { table } = {}) {
  let res;
  try {
    res = await reportStudioAPI.generate(spec, format, table);
  } catch (e) {
    throw new Error(await messageFrom(e));
  }
  const name = fileNameFrom(res.headers?.['content-disposition'], `report.${format}`);
  const url = URL.createObjectURL(res.data);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  return name;
}
