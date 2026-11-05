import { api } from '../net';
import type { AgentState, FeedEntry } from '../types';

interface UsageRow {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  fallbacks: number;
  json_mode: string;
  priced: boolean;
  quirks: string[];
  cost: number;
}

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;
const esc = (s: string) =>
  s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

export class Panel {
  private selected: string | null = null;
  private agents: AgentState[] = [];
  private refreshTimer?: number;

  constructor(private onSelect: (id: string | null) => void) {
    this.wireTabs();
    this.wireGodBox();
    this.wireControls();
  }

  // ------------------------------------------------------------------ tabs
  private wireTabs() {
    document.querySelectorAll<HTMLButtonElement>('#tabs button').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('#tabs button').forEach((b) => b.classList.remove('active'));
        document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
        btn.classList.add('active');
        $(`#tab-${btn.dataset.tab}`).classList.add('active');
        if (btn.dataset.tab === 'relations') void this.renderRelations();
        if (btn.dataset.tab === 'usage') void this.renderUsage();
      });
    });
  }

  private wireControls() {
    let paused = false;
    let seconds = 2;
    $('#pause').addEventListener('click', async () => {
      const res = await api.control(paused ? 'resume' : 'pause');
      paused = res.paused;
      $('#pause').textContent = paused ? 'Resume' : 'Pause';
    });
    $('#faster').addEventListener('click', async () => {
      seconds = Math.max(0.25, seconds / 1.5);
      await api.control('speed', seconds);
    });
    $('#slower').addEventListener('click', async () => {
      seconds = Math.min(10, seconds * 1.5);
      await api.control('speed', seconds);
    });
    $('#reset').addEventListener('click', async () => {
      if (confirm('Wipe all memories, relationships and history?')) {
        await api.control('reset');
        location.reload();
      }
    });
  }

  // --------------------------------------------------------------- god box
  private wireGodBox() {
    const send = async () => {
      const text = $<HTMLTextAreaElement>('#godtext').value.trim();
      if (!text) return;
      const target = $<HTMLSelectElement>('#godtarget').value;
      $<HTMLTextAreaElement>('#godtext').value = '';
      if (target) {
        const { reply } = await api.ask(target, text);
        this.push({
          tick: 0,
          label: 'you',
          kind: 'dialogue',
          text: `${this.nameOf(target)} answers the sky: ${reply}`,
        });
      } else {
        await api.event(text);
      }
    };
    $('#godsend').addEventListener('click', () => void send());
    $<HTMLTextAreaElement>('#godtext').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void send();
      }
    });
  }

  private nameOf(id: string) {
    return this.agents.find((a) => a.id === id)?.name ?? id;
  }

  // -------------------------------------------------------------- chronicle
  seedFeed(entries: FeedEntry[]) {
    $('#feed').innerHTML = '';
    entries.forEach((e) => this.push(e));
  }

  push(entry: FeedEntry) {
    const feed = $('#feed');
    const row = document.createElement('div');
    row.className = `entry ${entry.kind}`;
    row.innerHTML = `<span class="when">${esc(entry.label)}</span><span class="what">${esc(
      entry.text,
    )}</span>`;
    feed.appendChild(row);
    while (feed.childElementCount > 300) feed.firstElementChild?.remove();
    feed.scrollTop = feed.scrollHeight;
  }

  // --------------------------------------------------------------- agents
  setAgents(agents: AgentState[]) {
    const first = this.agents.length === 0;
    this.agents = agents;
    if (first) {
      const select = $<HTMLSelectElement>('#godtarget');
      for (const a of agents) {
        const opt = document.createElement('option');
        opt.value = a.id;
        opt.textContent = `Speak to ${a.name}`;
        select.appendChild(opt);
      }
    }
  }

  select(id: string | null) {
    this.selected = id;
    this.onSelect(id);
    window.clearInterval(this.refreshTimer);
    if (!id) {
      $('#inspector').innerHTML = '<p class="muted">Click a villager to look inside their head.</p>';
      return;
    }
    document.querySelector<HTMLButtonElement>('#tabs button[data-tab="inspector"]')?.click();
    void this.renderInspector();
    this.refreshTimer = window.setInterval(() => void this.renderInspector(), 4000);
  }

  private bar(label: string, value: number) {
    const hue = Math.round((value / 100) * 110);
    return `<div class="need"><span>${label}</span>
      <div class="track"><i style="width:${value}%;background:hsl(${hue} 70% 45%)"></i></div>
      <b>${Math.round(value)}</b></div>`;
  }

  private async renderInspector() {
    if (!this.selected) return;
    const a = await api.agent(this.selected);
    const needs = a.needs as Record<string, number>;
    const plan = (a.plan as { time: string; activity: string; location: string }[]) ?? [];

    $('#inspector').innerHTML = `
      <header class="who">
        <span class="swatch" style="background:${esc(a.color)}"></span>
        <div>
          <h2>${esc(a.name)}</h2>
          <p class="muted">${esc(a.job ?? '')} · <code>${esc(a.deployment)}</code></p>
        </div>
      </header>
      <p class="persona">${esc(a.persona)}</p>
      <div class="statline">
        <span>🪙 ${a.coins}</span><span>📍 ${esc(a.location || a.area)}</span>
        <span>${esc(a.action)}${a.target ? ` → ${esc(a.target)}` : ''}</span>
      </div>
      ${a.thought ? `<blockquote>${esc(a.thought)}</blockquote>` : ''}
      <div class="needs">
        ${this.bar('hunger', needs.hunger)}${this.bar('energy', needs.energy)}
        ${this.bar('social', needs.social)}${this.bar('fun', needs.fun)}
      </div>
      <h3>Today's plan</h3>
      <ol class="plan">${
        plan.length
          ? plan.map((s) => `<li><b>${esc(s.time)}</b> ${esc(s.activity)} <i>${esc(s.location)}</i></li>`).join('')
          : '<li class="muted">Not planned yet.</li>'
      }</ol>
      <h3>Impressions</h3>
      <ul class="rels">${
        (a.relationships as any[]).length
          ? (a.relationships as any[])
              .map(
                (r) =>
                  `<li><b>${esc(r.name)}</b> <span class="aff ${r.affinity >= 0 ? 'pos' : 'neg'}">${
                    r.affinity >= 0 ? '+' : ''
                  }${r.affinity}</span><br><span class="muted">${esc(r.summary || '—')}</span></li>`,
              )
              .join('')
          : '<li class="muted">Hasn\'t formed any yet.</li>'
      }</ul>
      <h3>Memory stream</h3>
      <ul class="mems">${(a.memories as any[])
        .map(
          (m) =>
            `<li class="m-${esc(m.kind)}"><span class="when">${esc(m.when)}</span> ${esc(
              m.content,
            )} <span class="imp">${m.importance}</span></li>`,
        )
        .join('')}</ul>`;
  }

  private async renderRelations() {
    const data = await api.relationships();
    const nodes = data.nodes as { id: string; name: string; color: string; model: string }[];
    const edges = data.edges as any[];
    const byPair = new Map<string, any>();
    for (const e of edges) byPair.set(`${e.from}->${e.to}`, e);

    $('#relations').innerHTML = `
      <table class="matrix">
        <thead><tr><th></th>${nodes.map((n) => `<th title="${esc(n.name)}">${esc(n.name.split(' ')[0])}</th>`).join('')}</tr></thead>
        <tbody>${nodes
          .map(
            (row) =>
              `<tr><th style="color:${esc(row.color)}">${esc(row.name.split(' ')[0])}</th>${nodes
                .map((col) => {
                  if (row.id === col.id) return '<td class="self">—</td>';
                  const e = byPair.get(`${row.id}->${col.id}`);
                  if (!e) return '<td class="none"></td>';
                  const hue = e.affinity >= 0 ? 120 : 0;
                  const alpha = Math.min(1, Math.abs(e.affinity) / 60 + 0.15);
                  return `<td style="background:hsl(${hue} 60% 40% / ${alpha})" title="${esc(
                    row.name,
                  )} → ${esc(col.name)} after ${e.encounters} talk(s)">${e.affinity > 0 ? '+' : ''}${
                    e.affinity
                  }</td>`;
                })
                .join('')}</tr>`,
          )
          .join('')}</tbody>
      </table>
      <p class="muted">Rows are how that villager feels about the column. Asymmetry is the point.</p>`;
  }

  private async renderUsage() {
    const data = await api.usage();
    const deployments = data.deployments as Record<string, UsageRow>;
    const rows = Object.entries(deployments);
    const fallbacks = rows.reduce((n, [, v]) => n + (v.fallbacks ?? 0), 0);
    const money = (n: number) => `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`;

    $('#usage').innerHTML = `
      <p class="${data.live ? 'pill ok' : 'pill warn'}">${
        data.live ? 'Live Foundry deployments' : 'Mock mode — no cloud calls'
      }</p>
      ${
        fallbacks
          ? `<p class="alert">⚠ ${fallbacks} call(s) fell back to scripted mock responses.
             Those villagers were not really thinking. Check the server log.</p>`
          : ''
      }
      <div class="spend">
        <span>Total spend</span><b>${money((data.total_cost as number) ?? 0)}</b>
        <span>${data.total_calls ?? 0} calls</span>
      </div>
      <h3>Who runs on what</h3>
      <ul class="models">${Object.entries(data.agents as Record<string, string>)
        .map(([name, model]) => `<li><b>${esc(name)}</b> <code>${esc(model)}</code></li>`)
        .join('')}</ul>
      <h3>Call volume &amp; spend</h3>
      ${
        rows.length
          ? `<table class="usage"><thead><tr>
               <th>Deployment</th><th>Calls</th><th>In</th><th>Out</th><th>Cost</th>
             </tr></thead>
             <tbody>${rows
               .map(([k, v]) => {
                 const flags = [
                   ...(v.quirks ?? []).map(
                     (q) =>
                       `<span class="flag" title="Deployment rejected a parameter; adapted automatically">${esc(q)}</span>`,
                   ),
                   v.priced ? '' : '<span title="No entry in prices.json — using default rate" class="flag est">est</span>',
                 ].join('');
                 return `<tr><td><code>${esc(k)}</code>${flags}</td><td>${v.calls}</td>
                   <td>${v.prompt_tokens.toLocaleString()}</td>
                   <td>${v.completion_tokens.toLocaleString()}</td>
                   <td>${money(v.cost)}</td></tr>`;
               })
               .join('')}</tbody></table>
             <p class="muted">Rates come from <code>backend/prices.json</code>. Entries marked
             <i>est</i> use the default rate — add your deployment to that file for real numbers.</p>`
          : '<p class="muted">No calls recorded yet.</p>'
      }`;
  }
}
