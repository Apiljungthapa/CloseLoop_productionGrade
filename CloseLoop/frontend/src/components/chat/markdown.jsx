import React from 'react';

// Minimal, safe markdown for assistant replies. Everything is built as React elements (never
// innerHTML), links are limited to http(s), so model output can't inject markup or script.

const INLINE_SOURCE =
  '(`[^`\\n]+`|\\*\\*[^*\\n]+\\*\\*|\\*[^*\\s][^*\\n]*?\\*|\\[[^\\]\\n]+\\]\\(https?:\\/\\/[^)\\s]+\\))';

function inline(text, keyPrefix) {
  const out = [];
  let last = 0;
  let i = 0;
  let m;
  // A fresh regex per call: bold/italic recurse into inline(), and a shared /g regex would have its
  // lastIndex reset by the inner call, so the outer loop would match the same span forever.
  const re = new RegExp(INLINE_SOURCE, 'g');
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    const key = `${keyPrefix}-${i++}`;
    if (t[0] === '`') {
      out.push(<code key={key}>{t.slice(1, -1)}</code>);
    } else if (t.startsWith('**')) {
      out.push(<strong key={key}>{inline(t.slice(2, -2), key)}</strong>);
    } else if (t[0] === '*') {
      out.push(<em key={key}>{inline(t.slice(1, -1), key)}</em>);
    } else {
      const [, label, url] = /^\[([^\]]+)\]\((.+)\)$/.exec(t);
      out.push(
        <a key={key} href={url} target="_blank" rel="noopener noreferrer">
          {label}
        </a>
      );
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const splitRow = (line) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());
const isTableSep = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

function parseBlocks(text) {
  const lines = text.replace(/\r/g, '').split('\n');
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (/^\s*```/.test(line)) {
      const code = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i += 1;
      blocks.push({ type: 'code', text: code.join('\n') });
      continue;
    }
    let m;
    if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {
      blocks.push({ type: 'heading', text: m[2] });
      i += 1;
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = splitRow(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(splitRow(lines[i++]));
      blocks.push({ type: 'table', head, rows });
      continue;
    }
    if (/^\s*[-*•]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*•]\s+/, ''));
      blocks.push({ type: 'ul', items });
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const start = parseInt(/^\s*(\d+)/.exec(line)[1], 10);
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ''));
      blocks.push({ type: 'ol', start, items });
      continue;
    }
    const para = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*```/.test(lines[i]) &&
      !/^(#{1,4})\s+/.test(lines[i]) &&
      !/^\s*[-*•]\s+/.test(lines[i]) &&
      !/^\s*\d+[.)]\s+/.test(lines[i])
    ) {
      para.push(lines[i++]);
    }
    // Never leave i where it was (e.g. "# x" + U+2028 fails the heading test above but trips the
    // stop test here): an empty paragraph would loop forever.
    if (!para.length) para.push(lines[i++]);
    blocks.push({ type: 'p', lines: para });
  }
  return blocks;
}

export default function Markdown({ text }) {
  const blocks = parseBlocks(text || '');
  return blocks.map((b, bi) => {
    const k = `b${bi}`;
    switch (b.type) {
      case 'heading':
        return (
          <div key={k} className="clchat-h">
            {inline(b.text, k)}
          </div>
        );
      case 'code':
        return (
          <pre key={k}>
            <code>{b.text}</code>
          </pre>
        );
      case 'ul':
        return (
          <ul key={k}>
            {b.items.map((it, ii) => (
              <li key={ii}>{inline(it, `${k}-${ii}`)}</li>
            ))}
          </ul>
        );
      case 'ol':
        return (
          <ol key={k} start={b.start}>
            {b.items.map((it, ii) => (
              <li key={ii}>{inline(it, `${k}-${ii}`)}</li>
            ))}
          </ol>
        );
      case 'table':
        return (
          <div key={k} className="clchat-table-wrap">
            <table>
              <thead>
                <tr>
                  {b.head.map((c, ci) => (
                    <th key={ci}>{inline(c, `${k}-h${ci}`)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((r, ri) => (
                  <tr key={ri}>
                    {r.map((c, ci) => (
                      <td key={ci}>{inline(c, `${k}-${ri}-${ci}`)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      default:
        return (
          <p key={k}>
            {b.lines.map((ln, li) => (
              <React.Fragment key={li}>
                {li > 0 && <br />}
                {inline(ln, `${k}-${li}`)}
              </React.Fragment>
            ))}
          </p>
        );
    }
  });
}
