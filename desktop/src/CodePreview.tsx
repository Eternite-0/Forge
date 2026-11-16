import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

const KEYWORDS = /^(?:async|await|break|case|catch|class|const|continue|def|delete|elif|else|enum|except|export|extends|false|finally|for|from|fn|function|if|impl|import|in|interface|let|match|mod|new|None|null|pass|private|pub|raise|return|self|static|struct|super|switch|throw|trait|true|try|type|use|var|while|with|yield)$/;
function Tokens({ line }: { line: string }) {
  if (line.length > 20000) return <>{line}</>;
  return <>{line.split(/(\/\/.*$|#.*$|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b[A-Za-z_$][\w$]*\b|\b\d+(?:\.\d+)?\b)/g).map((part, index) => <span key={index} className={/^(\/\/|#)/.test(part) ? "syntax-comment" : /^["'`]/.test(part) ? "syntax-string" : KEYWORDS.test(part) ? "syntax-keyword" : /^\d/.test(part) ? "syntax-number" : undefined}>{part}</span>)}</>;
}
type DiffRow = { raw: string; content: string; kind: "context" | "added" | "removed" | "meta"; oldLine?: number; newLine?: number; change?: { start: number; end: number } };
function diffRows(lines: string[]): DiffRow[] {
  let oldLine = 0, newLine = 0, inHunk = false;
  const result: DiffRow[] = [];
  for (const line of lines) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) { oldLine = Number(hunk[1]); newLine = Number(hunk[2]); inHunk = true; result.push({ raw: line, content: line, kind: "meta" }); continue; }
    // The surrounding file header already names the file. Do not treat removed source text as a header.
    if (!inHunk && /^(?:diff --git |index |--- |\+\+\+ |(?:new|deleted) file mode |(?:old|new) mode |rename (?:from|to) |similarity index )/.test(line)) continue;
    if (!inHunk || line.startsWith("\\") || !/^[ +\-]/.test(line)) { result.push({ raw: line, content: line, kind: "meta" }); continue; }
    if (line.startsWith("+")) result.push({ raw: line, content: line.slice(1), kind: "added", newLine: newLine++ });
    else if (line.startsWith("-")) result.push({ raw: line, content: line.slice(1), kind: "removed", oldLine: oldLine++ });
    else result.push({ raw: line, content: line.slice(1), kind: "context", oldLine: oldLine++, newLine: newLine++ });
  }
  // Only adjacent deletion/addition blocks within a hunk are replacement candidates.
  for (let index = 0; index < result.length;) {
    if (result[index].kind !== "removed") { index += 1; continue; }
    const removed = index;
    while (index < result.length && result[index].kind === "removed") index += 1;
    const added = index;
    while (index < result.length && result[index].kind === "added") index += 1;
    const pairs = Math.min(added - removed, index - added);
    for (let offset = 0; offset < pairs; offset += 1) {
      const before = result[removed + offset], after = result[added + offset];
      const oldCharacters = Array.from(before.content), newCharacters = Array.from(after.content);
      let prefix = 0, suffix = 0;
      while (prefix < oldCharacters.length && prefix < newCharacters.length && oldCharacters[prefix] === newCharacters[prefix]) prefix += 1;
      while (suffix < oldCharacters.length - prefix && suffix < newCharacters.length - prefix && oldCharacters[oldCharacters.length - suffix - 1] === newCharacters[newCharacters.length - suffix - 1]) suffix += 1;
      const start = oldCharacters.slice(0, prefix).join("").length;
      before.change = { start, end: before.content.length - oldCharacters.slice(oldCharacters.length - suffix).join("").length };
      after.change = { start, end: after.content.length - newCharacters.slice(newCharacters.length - suffix).join("").length };
    }
  }
  return result;
}
function DiffTokens({ row }: { row: DiffRow }) {
  const change = row.change;
  if (!change || change.start === change.end) return <Tokens line={row.content || " "} />;
  return <><Tokens line={row.content.slice(0, change.start)} /><span className="diff-word-change"><Tokens line={row.content.slice(change.start, change.end)} /></span><Tokens line={row.content.slice(change.end)} /></>;
}
type ReadingPosition = { top: number; left: number };
export default function CodePreview({ text, path, diff = false, compact = false, wrapSmallDiff = false, initialScroll, onReadingPosition }: { text: string; path: string; diff?: boolean; compact?: boolean; wrapSmallDiff?: boolean; initialScroll?: ReadingPosition; onReadingPosition?: (position: ReadingPosition) => void }) {
  const rawLines = useMemo(() => text.split(/\r?\n/), [text]);
  const rows = useMemo(() => diff ? diffRows(rawLines) : [], [rawLines, diff]);
  const lines = useMemo(() => diff ? rows.map((row) => row.raw) : rawLines, [rawLines, rows, diff]);
  const lineHeight = 21.6;
  const [wrapPreference, setWrap] = useState<boolean | null>(null), [query, setQuery] = useState(""), [match, setMatch] = useState(0), [top, setTop] = useState(() => initialScroll?.top || 0);
  const [findOpen, setFindOpen] = useState(false);
  const findInput = useRef<HTMLInputElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const positions = useRef(new Map<string, ReadingPosition>());
  const initialPosition = useRef(initialScroll), restoring = useRef<ReadingPosition | null>(null);
  initialPosition.current = initialScroll;
  const searchNavigated = useRef(false);
  const matches = useMemo(() => query ? lines.flatMap((line, i) => line.toLocaleLowerCase().includes(query.toLocaleLowerCase()) ? [i] : []) : [], [lines, query]);
  const allowWrap = lines.length <= 5000;
  const wrap = allowWrap && (wrapPreference ?? (wrapSmallDiff && diff && lines.length <= 300));
  const openFind = () => { setFindOpen(true); requestAnimationFrame(() => findInput.current?.focus()); };
  useEffect(() => {
    const element = viewport.current?.closest(".code-preview");
    if (!element || compact) return;
    element.addEventListener("forge-find", openFind);
    return () => element.removeEventListener("forge-find", openFind);
  }, [compact]);
  const start = wrap ? 0 : Math.min(Math.max(0, lines.length - 1), Math.max(0, Math.floor(top / lineHeight) - 12)), end = wrap ? lines.length : Math.min(lines.length, start + 160);
  useLayoutEffect(() => {
    const position = positions.current.get(path) || initialPosition.current || { top: 0, left: 0 };
    restoring.current = position;
    setTop(position.top); setQuery(""); setMatch(0); searchNavigated.current = false;
  }, [path]);
  useLayoutEffect(() => {
    // Restore after the virtual spacer has adopted the saved row, including after a group remount.
    const position = restoring.current, element = viewport.current;
    if (!position || !element || top !== position.top) return;
    element.scrollTop = position.top; element.scrollLeft = position.left;
    restoring.current = null;
  });
  const updateQuery = (value: string) => { setQuery(value); setMatch(0); searchNavigated.current = false; };
  const go = (direction: number) => { if (!matches.length) return; const next = searchNavigated.current ? match + direction : direction < 0 ? -1 : 0; const index = (next + matches.length) % matches.length; searchNavigated.current = true; setMatch(index); if (viewport.current) { if (wrap) viewport.current.querySelector(`[data-line="${matches[index] + 1}"]`)?.scrollIntoView({ block: "center" }); else viewport.current.scrollTop = Math.max(0, (matches[index] - 8) * lineHeight); } };
  return <div className={`code-preview${compact ? " code-preview-compact" : ""}${diff ? " code-preview-diff" : ""}`} onKeyDown={(event) => { if (!compact && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") { event.preventDefault(); openFind(); } }}>
    {!compact && findOpen && <div className="source-controls"><label><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor"><circle cx="7" cy="7" r="4.5" /><path d="m10.5 10.5 3 3" /></svg><input ref={findInput} aria-label="在文件中查找" placeholder="在文件中查找" value={query} onChange={(event) => updateQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); go(event.shiftKey ? -1 : 1); } if (event.key === "Escape") { event.preventDefault(); updateQuery(""); setFindOpen(false); viewport.current?.focus(); } }} /></label>{query && <><span>{matches.length ? match + 1 : 0}/{matches.length}</span><button type="button" aria-label="上一个匹配" disabled={!matches.length} onClick={() => go(-1)}>↑</button><button type="button" aria-label="下一个匹配" disabled={!matches.length} onClick={() => go(1)}>↓</button></>}<button type="button" className={wrap ? "active" : ""} disabled={!allowWrap} title={allowWrap ? undefined : "超过 5,000 行的文件使用虚拟滚动，请横向阅读。"} aria-pressed={wrap} onClick={() => setWrap(!wrap)}>自动换行</button><span>{lines.length.toLocaleString()} 行</span></div>}
    <div ref={viewport} className={`source-lines${wrap ? " is-wrapped" : ""}`} tabIndex={0} aria-label={`${path} ${diff ? "更改" : "源码"}`} onScroll={(event) => { const element = event.currentTarget, position = { top: element.scrollTop, left: element.scrollLeft }; setTop(position.top); if (element.clientHeight > 0) { positions.current.set(path, position); onReadingPosition?.(position); } }}>
      <div style={{ paddingTop: start * lineHeight, paddingBottom: (lines.length - end) * lineHeight }}>
        {lines.slice(start, end).map((line, offset) => {
          const number = start + offset, row = rows[number];
          const change = diff && (row.kind === "added" || row.kind === "removed") ? row.kind : undefined;
          return <div className={`source-line${query && line.toLocaleLowerCase().includes(query.toLocaleLowerCase()) ? " search-match" : ""}${diff ? ` diff-${row.kind}` : ""}`} data-line={number + 1} data-old-line={diff ? row.oldLine : undefined} data-new-line={diff ? row.newLine : undefined} key={number}>
            <span className="source-line-number" aria-hidden="true">{change && <span className="diff-kind-symbol">{change === "added" ? "+" : "−"}</span>}{diff ? row.newLine ?? row.oldLine ?? "" : number + 1}</span>
            {change && <span className="sr-only diff-kind-label">{change === "added" ? "新增：" : "删除："}</span>}
            <code>{diff ? <DiffTokens row={row} /> : <Tokens line={line || " "} />}</code>
          </div>;
        })}
      </div>
    </div>
  </div>;
}
