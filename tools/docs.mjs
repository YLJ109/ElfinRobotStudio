// Read-only documentation search and integrity checks. No backend imports or network.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
function local(relative) {
  if (typeof relative !== 'string' || !relative || path.isAbsolute(relative)) throw Error('Expected a repository-relative path');
  const target = path.resolve(root, relative);
  const rel = path.relative(root, target);
  if (rel === '..' || rel.startsWith('..' + path.sep) || path.isAbsolute(rel)) throw Error('Path outside repository');
  return target;
}
const read = relative => fs.readFileSync(local(relative), 'utf8');
const json = relative => JSON.parse(read(relative));
const index = json('docs/SEARCH_INDEX.json');
const manifest = json('docs/archive/MANIFEST.json');
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const args = process.argv.slice(2), command = args.shift() || 'help';
const normalize = value => String(value).toLocaleLowerCase();
function contains(value, term) {
  if (/^[a-z][a-z0-9_-]*$/i.test(term)) {
    return new RegExp('(^|[^a-z0-9_])'+term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'(?=$|[^a-z0-9_])','i').test(value);
  }
  return value.includes(term);
}
function find() {
  const historical = args.includes('--history');
  const terms = args.filter(x => x !== '--history').flatMap(x => normalize(x).split(/\s+/)).filter(Boolean);
  if (!terms.length) throw Error('Supply search words, e.g. find "真机 停止"');
  const entries = [...index.documents];
  if (historical) for (const x of manifest.entries) {
    if (/\.(md|py|json|csv)$/i.test(x.archived)) entries.push({
      path:x.archived, title:'历史：' + x.original, keywords:[x.original], role:'history'
    });
  }
  const hits = [];
  for (const entry of entries) {
    if (!fs.existsSync(local(entry.path))) continue;
    const metadata = normalize([entry.title,...entry.keywords,entry.path].join(' '));
    const lines = read(entry.path).split(/\r?\n/);
    let score = 0, matched = 0, first = -1;
    for (const term of terms) {
      const positions = [];
      for (let n=0;n<lines.length;n++) if (contains(normalize(lines[n]),term)) positions.push(n);
      const metaHit = contains(metadata,term);
      if (metaHit || positions.length) matched++;
      score += (metaHit ? 12 : 0) + Math.min(positions.length, 4);
      if (positions.length && (first < 0 || positions[0] < first)) first = positions[0];
    }
    if (!matched) continue;
    score += matched === terms.length ? 15 : 0;
    if (entry.role === 'active') score += 2;
    let best = -1;
    for (let n=0;n<lines.length;n++) {
      const weight = terms.filter(term=>contains(normalize(lines[n]),term)).length;
      if (weight > best && weight > 0) { best=weight;first=n; }
    }
    hits.push({entry,score,matched,line:first < 0 ? 1 : first+1,
      excerpt:(first < 0 ? entry.title : lines[first]).replace(/[\u0000-\u001f]/g,' ').slice(0,180)});
  }
  hits.sort((a,b)=>b.score-a.score || a.entry.path.localeCompare(b.entry.path));
  if (!hits.length) { console.log('没有命中；可拆分关键词，历史搜索加 --history。'); return; }
  const selected = hits.some(hit=>hit.matched===terms.length) ? hits.filter(hit=>hit.matched===terms.length) : hits;
  for (const hit of selected.slice(0,8)) console.log(
    '['+hit.entry.role+'] '+hit.entry.path+':'+hit.line+'\n  '+hit.entry.title+'\n  '+hit.excerpt);
}
function locate() {
  const original = args.join(' ').replace(/\\/g,'/');
  local(original);
  let found = false;
  if (fs.existsSync(local(original))) { console.log('当前：'+original); found = true; }
  for (const x of manifest.entries) if (x.original === original) {
    console.log('归档：'+x.archived+'\nSHA256 '+x.sha256+'\n恢复到新目录，不覆盖当前文件。');
    found = true;
  }
  if (!found) { process.exitCode = 1; console.log('未找到当前文件或归档映射：'+original); }
}
function check() {
  const errors = [], seen = new Set();
  for (const entry of index.documents) {
    if (seen.has(entry.path)) errors.push('Duplicate index: '+entry.path);
    seen.add(entry.path);
    const file = local(entry.path);
    if (!fs.existsSync(file)) { errors.push('Missing document: '+entry.path); continue; }
    if (!Array.isArray(entry.keywords) || !entry.keywords.length) errors.push('No keywords: '+entry.path);
    const markdown = fs.readFileSync(file,'utf8').replace(/^\s*\x60{3}[\s\S]*?^\s*\x60{3}/gm,'');
    for (const match of markdown.matchAll(/!?\[[^\]]*\]\(([^)\n]+)\)/g)) {
      let target = match[1].trim().replace(/^<|>$/g,'');
      if (/^(https?:|mailto:|data:)/i.test(target) || target.startsWith('#')) continue;
      target = target.split('#')[0];
      try {
        target = decodeURIComponent(target);
        const resolved = path.resolve(path.dirname(file), target);
        const relative = path.relative(root,resolved);
        local(relative);
        if (!fs.existsSync(resolved)) errors.push(entry.path+' -> missing link '+target);
      } catch (error) { errors.push(entry.path+' -> invalid local link '+target+': '+error.message); }
    }
  }
  const originals = new Set();
  for (const record of manifest.entries) {
    if (originals.has(record.original)) errors.push('Duplicate archive original: '+record.original);
    originals.add(record.original);
    try {
      const file = local(record.archived);
      if (fs.statSync(file).size !== record.bytes || hash(file) !== record.sha256) errors.push('Archive mismatch: '+record.archived);
      if (record.action === 'archive_move' && fs.existsSync(local(record.original))) errors.push('Old active history remains: '+record.original);
    } catch(error) { errors.push('Missing/unreadable archive: '+record.archived+': '+error.message); }
  }
  const section = json('docs/project.json').comprehensive_optimization_20261029;
  const renewal = section.enterprise_renewal_20261030, ready = new Set();
  if (!renewal || renewal.status !== 'planned_not_implemented') errors.push('Renewal status must remain planned');
  for (const job of renewal?.packages || []) {
    if (ready.has(job.id)) errors.push('Duplicate renewal task: '+job.id);
    for (const id of job.depends_on) if (!ready.has(id)) errors.push('Invalid task dependency: '+job.id+' -> '+id);
    ready.add(job.id);
  }
  if (manifest.state !== 'archived_verified') errors.push('Archive migration not finalized');
  if (errors.length) { console.error(errors.join('\n')); process.exitCode=1; return; }
  console.log(JSON.stringify({result:'DOCS_OK',active_documents:index.documents.length,
    archive_records:manifest.entries.length,renewal_tasks:ready.size,
    scope:'local links/index/archive hashes/plan dependencies only; no external URL, runtime or hardware certification'},null,2));
}
try {
  if (command === 'find') find();
  else if (command === 'locate') locate();
  else if (command === 'check') check();
  else { console.log('node tools/docs.mjs find "关键词" [--history]\nnode tools/docs.mjs locate docs/旧文件.md\nnode tools/docs.mjs check'); }
} catch(error) { console.error(error.message); process.exitCode=1; }
