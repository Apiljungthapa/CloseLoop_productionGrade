import React from 'react';

// Dependency-free SVG charts for Report Studio. The server sends a chart as
//   { id, title, kind, labels, series: [{ name, values, tone, point_tones }], unit, subtitle }
// and these draw it: donut | bar | hbar | stacked_bar | stacked_hbar | line. A `null` value is a gap (the
// running week has no completions "yet"), never a zero.

export const TONE = {
  good: '#1e8e5a', warn: '#d98a00', bad: '#c62f3e', info: '#2f6fde', neutral: '#52606d', muted: '#9aa7b5',
};
const CYCLE = ['#2f6fde', '#9aa7b5', '#1e8e5a', '#d98a00', '#c62f3e', '#7b5cd6', '#17a2b8'];

export function seriesColor(series, index, point) {
  if (point != null && series.point_tones) return TONE[series.point_tones[point]] || CYCLE[point % CYCLE.length];
  if (series.tone) return TONE[series.tone] || CYCLE[index % CYCLE.length];
  return CYCLE[index % CYCLE.length];
}

const fmt = (v, unit) => {
  if (v === null || v === undefined) return '–';
  if (unit === 'percent') return `${Math.round(v * 10) / 10}%`;
  return Number(v).toLocaleString();
};
const trunc = (s, n) => (String(s).length > n ? `${String(s).slice(0, n - 1)}…` : String(s));

function niceMax(value) {
  if (!(value > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(value));
  const n = value / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function axisMax(chart, stacked) {
  if (chart.unit === 'percent') return 100;
  const per = chart.labels.map((_, i) => chart.series.map((s) => s.values[i] || 0));
  const top = stacked ? Math.max(...per.map((v) => v.reduce((a, b) => a + b, 0)), 0) : Math.max(...per.flat(), 0);
  return niceMax(top);
}

const ticks = (max) => [0, 1, 2, 3, 4].map((i) => (max * i) / 4);

function Legend({ chart }) {
  if (chart.series.length < 2) return null;
  return (
    <ul className="rs-legend" aria-label="Legend">
      {chart.series.map((s, i) => (
        <li key={s.name}>
          <span className="rs-swatch" style={{ background: seriesColor(s, i) }} />
          {s.name}
        </li>
      ))}
    </ul>
  );
}

function Donut({ chart }) {
  const s = chart.series[0];
  const values = s.values.map((v) => v || 0);
  const total = values.reduce((a, b) => a + b, 0);
  const R = 70;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="rs-donut-wrap">
      <svg viewBox="0 0 200 200" className="rs-donut" role="img" aria-label={`${chart.title}: ${chart.labels.map((l, i) => `${l} ${values[i]}`).join(', ')}`}>
        <circle cx="100" cy="100" r={R} fill="none" stroke="#eef1f5" strokeWidth="26" />
        {total > 0 &&
          values.map((v, i) => {
            const len = (v / total) * C;
            const node = (
              <circle
                key={chart.labels[i]}
                cx="100" cy="100" r={R} fill="none" stroke={seriesColor(s, 0, i)} strokeWidth="26"
                strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-offset} transform="rotate(-90 100 100)"
              >
                <title>{`${chart.labels[i]}: ${v.toLocaleString()} (${Math.round((v / total) * 100)}%)`}</title>
              </circle>
            );
            offset += len;
            return node;
          })}
        <text x="100" y="98" textAnchor="middle" className="rs-donut-total">{total.toLocaleString()}</text>
        <text x="100" y="118" textAnchor="middle" className="rs-donut-sub">tasks</text>
      </svg>
      <ul className="rs-donut-legend">
        {chart.labels.map((l, i) => (
          <li key={l}>
            <span className="rs-swatch" style={{ background: seriesColor(s, 0, i) }} />
            <span className="rs-donut-label">{l}</span>
            <b>{values[i].toLocaleString()}</b>
            <small>{total ? `${Math.round((values[i] / total) * 100)}%` : ''}</small>
          </li>
        ))}
      </ul>
    </div>
  );
}

function VBars({ chart, stacked }) {
  const W = 560;
  const n = chart.labels.length;
  const rotate = n > 5;
  const m = { l: 46, r: 12, t: 14, b: rotate ? 70 : 38 };
  const H = 270;
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const max = axisMax(chart, stacked);
  const y = (v) => m.t + ph - (v / max) * ph;
  const gw = pw / Math.max(n, 1);
  const k = chart.series.length;
  const bw = stacked ? Math.min(gw * 0.62, 56) : Math.min((gw * 0.78) / k, 38);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="rs-svg" role="img" aria-label={chart.title}>
      {ticks(max).map((t) => (
        <g key={t}>
          <line x1={m.l} x2={W - m.r} y1={y(t)} y2={y(t)} className="rs-grid" />
          <text x={m.l - 6} y={y(t) + 3} textAnchor="end" className="rs-axis">{fmt(t, chart.unit)}</text>
        </g>
      ))}
      {chart.labels.map((label, i) => {
        const cx = m.l + gw * (i + 0.5);
        let acc = 0;
        return (
          <g key={label}>
            {chart.series.map((s, j) => {
              const v = s.values[i];
              if (v == null) return null;
              const x = stacked ? cx - bw / 2 : cx - (bw * k) / 2 + j * bw;
              const base = stacked ? acc : 0;
              if (stacked) acc += v;
              const top = y(base + v);
              const h = Math.max(y(base) - top, v > 0 ? 1 : 0);
              return (
                <g key={s.name}>
                  <rect x={x} y={top} width={bw - (stacked ? 0 : 2)} height={h} rx="2" fill={seriesColor(s, j)}>
                    <title>{`${label} · ${s.name}: ${fmt(v, chart.unit)}`}</title>
                  </rect>
                  {k === 1 && v > 0 && (
                    <text x={x + bw / 2} y={top - 4} textAnchor="middle" className="rs-value">{fmt(v, chart.unit)}</text>
                  )}
                </g>
              );
            })}
            <text
              x={cx} y={H - m.b + 14} textAnchor={rotate ? 'end' : 'middle'} className="rs-axis"
              transform={rotate ? `rotate(-32 ${cx} ${H - m.b + 14})` : undefined}
            >
              {trunc(label, rotate ? 16 : 14)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function HBars({ chart, stacked }) {
  const n = chart.labels.length;
  const rowH = 28;
  const m = { l: 128, r: 46, t: 8, b: 24 };
  const W = 560;
  const H = Math.max(120, n * rowH + m.t + m.b);
  const pw = W - m.l - m.r;
  const max = axisMax(chart, stacked);
  const x = (v) => m.l + (v / max) * pw;
  const k = chart.series.length;
  const bh = stacked ? 16 : Math.min(18, (rowH - 8) / k);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="rs-svg" role="img" aria-label={chart.title}>
      {ticks(max).map((t) => (
        <g key={t}>
          <line x1={x(t)} x2={x(t)} y1={m.t} y2={H - m.b} className="rs-grid" />
          <text x={x(t)} y={H - m.b + 14} textAnchor="middle" className="rs-axis">{fmt(t, chart.unit)}</text>
        </g>
      ))}
      {chart.labels.map((label, i) => {
        const cy = m.t + rowH * (i + 0.5);
        let acc = 0;
        return (
          <g key={label}>
            <text x={m.l - 8} y={cy + 3} textAnchor="end" className="rs-axis">{trunc(label, 20)}</text>
            {chart.series.map((s, j) => {
              const v = s.values[i];
              if (v == null) return null;
              const y0 = stacked ? cy - bh / 2 : cy - (bh * k) / 2 + j * bh;
              const x0 = stacked ? x(acc) : m.l;
              const w = Math.max((stacked ? x(acc + v) - x(acc) : x(v) - m.l), v > 0 ? 1 : 0);
              if (stacked) acc += v;
              return (
                <g key={s.name}>
                  <rect x={x0} y={y0} width={w} height={bh - 1} rx="2" fill={seriesColor(s, j)}>
                    <title>{`${label} · ${s.name}: ${fmt(v, chart.unit)}`}</title>
                  </rect>
                  {k === 1 && (
                    <text x={x0 + w + 5} y={y0 + bh / 2 + 3} className="rs-value">{fmt(v, chart.unit)}</text>
                  )}
                </g>
              );
            })}
            {stacked && acc > 0 && <text x={x(acc) + 5} y={cy + 3} className="rs-value">{fmt(acc, chart.unit)}</text>}
          </g>
        );
      })}
    </svg>
  );
}

function Lines({ chart }) {
  const W = 560;
  const H = 270;
  const n = chart.labels.length;
  const rotate = n > 6;
  const m = { l: 46, r: 16, t: 14, b: rotate ? 62 : 38 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const max = axisMax(chart, false);
  const x = (i) => (n === 1 ? m.l + pw / 2 : m.l + (pw * i) / (n - 1));
  const y = (v) => m.t + ph - (v / max) * ph;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="rs-svg" role="img" aria-label={chart.title}>
      {ticks(max).map((t) => (
        <g key={t}>
          <line x1={m.l} x2={W - m.r} y1={y(t)} y2={y(t)} className="rs-grid" />
          <text x={m.l - 6} y={y(t) + 3} textAnchor="end" className="rs-axis">{fmt(t, chart.unit)}</text>
        </g>
      ))}
      {chart.labels.map((label, i) => (
        <text
          key={label} x={x(i)} y={H - m.b + 14} textAnchor={rotate ? 'end' : 'middle'} className="rs-axis"
          transform={rotate ? `rotate(-32 ${x(i)} ${H - m.b + 14})` : undefined}
        >
          {trunc(label, 12)}
        </text>
      ))}
      {chart.series.map((s, j) => {
        const color = seriesColor(s, j);
        const runs = [];
        let run = [];
        s.values.forEach((v, i) => {
          if (v == null) {
            if (run.length) runs.push(run);
            run = [];
          } else run.push([x(i), y(v), v, i]);
        });
        if (run.length) runs.push(run);
        return (
          <g key={s.name}>
            {runs.map((r, ri) => (
              <polyline key={ri} points={r.map(([px, py]) => `${px},${py}`).join(' ')} fill="none" stroke={color} strokeWidth="2.4" strokeLinejoin="round" />
            ))}
            {runs.flat().map(([px, py, v, i]) => (
              <circle key={`${j}-${i}`} cx={px} cy={py} r="3.4" fill="#fff" stroke={color} strokeWidth="2">
                <title>{`${chart.labels[i]} · ${s.name}: ${fmt(v, chart.unit)}`}</title>
              </circle>
            ))}
          </g>
        );
      })}
    </svg>
  );
}

export function StudioChart({ chart }) {
  const body =
    chart.kind === 'donut' ? <Donut chart={chart} />
      : chart.kind === 'line' ? <Lines chart={chart} />
        : chart.kind === 'hbar' ? <HBars chart={chart} stacked={false} />
          : chart.kind === 'stacked_hbar' ? <HBars chart={chart} stacked />
            : chart.kind === 'stacked_bar' ? <VBars chart={chart} stacked />
              : <VBars chart={chart} stacked={false} />;
  return (
    <figure className="rs-chart" data-chart={chart.id}>
      <figcaption>
        <b>{chart.title}</b>
        {chart.subtitle && <small>{chart.subtitle}</small>}
      </figcaption>
      {chart.labels.length === 0 ? <div className="rs-empty">Nothing to chart yet.</div> : body}
      {chart.kind !== 'donut' && <Legend chart={chart} />}
    </figure>
  );
}
