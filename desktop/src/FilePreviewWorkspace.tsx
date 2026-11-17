import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import CodePreview from "./CodePreview";
import FileMarkdown from "./FileMarkdown";
import ArtifactHistoryPreview from "./ArtifactHistoryPreview";
import type { Connection } from "./api";
import type { ArtifactDiff } from "./artifactHistory";
import { errorMessage } from "./logic";
import { importedFileSource, relativeFilePath, type ChangeSet, type FileDiff, type FileSource, type WorkspaceDocument, type WorkspaceFile } from "./workspaceFiles";

export interface PreviewRequest { id: number; mode: "files" | "changes" | "conversation" | "history"; path?: string; paths?: string[]; receiptId?: string }
type DocumentState = { loading: boolean; error: string; document?: WorkspaceDocument };
type ViewMode = "preview" | "source" | "diff";
const emptyChanges: ChangeSet = { files: [], repository: false };
function FileIcon({ folder = false }: { folder?: boolean }) { return <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">{folder ? <path d="M1.5 4h5l1.5 1.5h6.5v8h-13V4Zm0 0V2.5h5L8 4" /> : <path d="M4 1.5h5l3.5 3.5v9.5H4v-13Zm5 0V5h3.5" />}</svg>; }

// File glyphs port the native DMvp sprite paths; they stay inline and never fetch assets.
const treeFileGlyphs = {
  default: <><path fill="currentColor" d="M8 1v3a3 3 0 0 0 3 3h3v5.5a2.5 2.5 0 0 1-2.5 2.5h-7A2.5 2.5 0 0 1 2 12.5v-9A2.5 2.5 0 0 1 4.5 1z" className="bg" opacity=".4"/> <path fill="currentColor" d="M9.5 1a.5.5 0 0 1 .354.146l4 4A.5.5 0 0 1 14 5.5V6h-3a2 2 0 0 1-2-2V1z" className="fg"/></>,
  text: <><path fill="currentColor" fillRule="evenodd" d="M8 4a3 3 0 0 0 3 3h3v5.5a2.5 2.5 0 0 1-2.5 2.5h-7A2.5 2.5 0 0 1 2 12.5v-9A2.5 2.5 0 0 1 4.5 1H8z" className="bg" clipRule="evenodd" opacity=".4"/> <path fill="currentColor" d="M8.5 11a.5.5 0 0 1 0 1h-3a.5.5 0 0 1 0-1zm2-2a.5.5 0 0 1 0 1h-5a.5.5 0 0 1 0-1zm-1-8a.5.5 0 0 1 .354.146l4 4A.5.5 0 0 1 14 5.5V6h-3a2 2 0 0 1-2-2V1z"/></>,
  json: <><path fill="currentColor" d="M13.25 11.5V9.75a.5.5 0 0 1 .36-.48l.55-.15a1.16 1.16 0 0 0 0-2.24l-.55-.15a.5.5 0 0 1-.36-.48V4.5a2.5 2.5 0 0 0-2.5-2.5h-.25a.5.5 0 0 0 0 1h.25a1.5 1.5 0 0 1 1.5 1.5v1.75a1.5 1.5 0 0 0 1.09 1.44l.54.15a.16.16 0 0 1 0 .32l-.54.15a1.5 1.5 0 0 0-1.09 1.44v1.75a1.5 1.5 0 0 1-1.5 1.5h-.25a.5.5 0 0 0 0 1h.25a2.5 2.5 0 0 0 2.5-2.5m-10.5 0V9.75a.5.5 0 0 0-.36-.48l-.55-.15a1.16 1.16 0 0 1 0-2.24l.55-.15a.5.5 0 0 0 .36-.48V4.5A2.5 2.5 0 0 1 5.25 2h.25a.5.5 0 0 1 0 1h-.25a1.5 1.5 0 0 0-1.5 1.5v1.75a1.5 1.5 0 0 1-1.09 1.44l-.54.15a.16.16 0 0 0 0 .32l.54.15a1.5 1.5 0 0 1 1.09 1.45v1.74a1.5 1.5 0 0 0 1.5 1.5h.25a.5.5 0 0 1 0 1h-.25a2.5 2.5 0 0 1-2.5-2.5"/></>,
  markdown: <><path fill="currentColor" d="M1 12V4h2l2 2.5L7 4h2v8H7V7.5l-2 2-2-2V12zm9-3 3 3.5L16 9h-2V4h-2v5z"/></>,
  python: <><path fill="currentColor" d="M8.33 8.4H10c1.16 0 1.9-.73 1.9-1.86V5.08q0-.24.25-.24h.74c.75 0 1.33.32 1.66.97q.4.73.41 1.46c.09.9.09 1.78-.24 2.67-.25.73-.75 1.3-1.58 1.46h-4.8c-.08 0-.25 0-.25.08v.4s.17.09.25.09h2.82q.34-.02.33.32v1.06c0 .56-.25.97-.75 1.13-.41.16-.83.33-1.24.4a7 7 0 0 1-2.98-.07 3 3 0 0 1-1.16-.49c-.33-.32-.58-.65-.5-1.14v-2.91c0-1.13.67-1.78 1.82-1.78q.89-.1 1.66-.08m2.32 4.86a.65.65 0 0 0-.66-.65c-.34 0-.67.33-.67.65s.33.57.67.65a.65.65 0 0 0 .66-.65" className="bg" opacity=".8"/> <path fill="currentColor" d="M7.67 7.6H6c-1.16 0-1.9.73-1.9 1.86v1.46q0 .24-.25.24h-.74c-.75 0-1.33-.32-1.66-.97a3 3 0 0 1-.41-1.46 6 6 0 0 1 .24-2.67c.25-.73.75-1.3 1.58-1.46h4.8c.08 0 .25 0 .25-.08v-.4s-.17-.09-.25-.09H4.85c-.24 0-.33-.08-.33-.32V2.65c0-.56.25-.97.75-1.13.41-.16.83-.33 1.24-.4a7 7 0 0 1 2.98.07c.41.09.83.25 1.16.49.33.32.58.65.5 1.13v2.92c0 1.14-.67 1.78-1.82 1.78-.58.08-1.16.08-1.66.08M5.35 2.73c0 .33.25.65.66.65.33 0 .66-.32.66-.65 0-.32-.33-.56-.66-.64a.65.65 0 0 0-.66.64" className="fg"/></>,
  git: <><path fill="currentColor" d="M14.74 7.38 8.62 1.26a.9.9 0 0 0-1.27 0L6.08 2.53l1.61 1.61a1.07 1.07 0 0 1 1.36 1.37l1.55 1.55a1.07 1.07 0 0 1 1.1 1.77 1.07 1.07 0 0 1-1.74-1.16L8.5 6.22v3.8a1.07 1.07 0 1 1-.89-.02V6.15a1.07 1.07 0 0 1-.58-1.4l-1.58-1.6-4.2 4.2a.9.9 0 0 0 0 1.27l6.12 6.12a.9.9 0 0 0 1.27 0l6.09-6.09a.9.9 0 0 0 0-1.27"/></>,
  image: <><path fill="currentColor" d="M12.5 2A2.5 2.5 0 0 1 15 4.5v4.67l-4.05-3.54-4.08 4.08-3-2L1 10.6V4.5A2.5 2.5 0 0 1 3.5 2z" opacity=".3"/> <path fill="currentColor" d="M15 10.5v1a2.5 2.5 0 0 1-2.5 2.5h-9a2.5 2.5 0 0 1-2.46-2.04L4 9l3 2 4-4zm-7-5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0"/></>,
  typescript: <><path fill="currentColor" d="M8 1C2.24 1 1 2.24 1 8s1.24 7 7 7 7-1.24 7-7-1.24-7-7-7" className="bg" opacity=".2"/> <path fill="currentColor" d="M8.1 9.64h.95c.04.62.28.76 1.28.76s1.2-.14 1.2-.85c0-.66-.2-.85-1.2-1.07-1.79-.38-2.18-.7-2.18-1.86C8.15 5.3 8.54 5 10.31 5c1.67 0 2.04.26 2.07 1.42h-.95c-.02-.43-.23-.53-1.1-.53-1 0-1.22.14-1.22.74 0 .52.22.7 1.24.92 1.76.38 2.15.73 2.15 2 0 1.44-.4 1.75-2.24 1.75-1.8 0-2.18-.3-2.15-1.66m-3 1.57V5.99H3.5v-.9h4.21v.9H6.1v5.22z"/></>,
  javascript: <><path fill="currentColor" d="M8 1C2.24 1 1 2.24 1 8s1.24 7 7 7 7-1.24 7-7-1.24-7-7-7" className="bg" opacity=".2"/> <path fill="currentColor" d="M8.1 9.64h.95c.04.62.28.76 1.28.76s1.2-.14 1.2-.85c0-.66-.2-.85-1.2-1.07-1.79-.38-2.18-.7-2.18-1.86C8.15 5.3 8.54 5 10.31 5c1.67 0 2.04.26 2.07 1.42h-.95c-.02-.43-.23-.53-1.1-.53-1 0-1.22.14-1.22.74 0 .52.22.7 1.24.92 1.76.38 2.15.73 2.15 2 0 1.44-.4 1.75-2.24 1.75-1.8 0-2.18-.3-2.15-1.66M3.5 9.5h.98c0 .76.15.92.85.92.77 0 .94-.18.94-1.02V5.1h1v4.34c0 1.54-.35 1.87-1.92 1.87-1.55 0-1.89-.32-1.86-1.8"/></>,
  react: <><path fill="currentColor" d="M8 6.65c.73 0 1.31.6 1.31 1.35S8.73 9.35 8 9.35 6.69 8.75 6.69 8 7.27 6.65 8 6.65"/> <path fill="currentColor" fillRule="evenodd" d="M8 2.55c1.3-.99 2.59-1.34 3.5-.8.92.55 1.27 1.87 1.08 3.53C14.06 5.94 15 6.9 15 8s-.94 2.06-2.42 2.72c.19 1.65-.16 2.98-1.08 3.52-.91.55-2.2.2-3.5-.8-1.3 1-2.58 1.35-3.5.8-.91-.54-1.27-1.87-1.08-3.52C1.94 10.06 1 9.1 1 8s.94-2.06 2.42-2.72c-.19-1.66.17-2.98 1.08-3.52s2.2-.2 3.5.8M4.26 11.2c-.08 1.34.28 2.03.68 2.26s1.15.22 2.25-.52l.11-.09a12 12 0 0 1-1.24-1.39 11 11 0 0 1-1.8-.41zm7.47-.15q-.83.27-1.79.41-.6.8-1.24 1.4l.11.08c1.1.74 1.86.76 2.25.52.4-.23.76-.92.68-2.26zm-3.04.54a14 14 0 0 1-1.38 0q.34.38.69.7.35-.32.7-.7M8 5.29q-.76 0-1.47.1A13 13 0 0 0 5.07 8a14 14 0 0 0 1.46 2.62 13 13 0 0 0 2.94 0A13 13 0 0 0 10.93 8a14 14 0 0 0-1.46-2.62A13 13 0 0 0 8 5.3M4.64 9.18q-.15.5-.25.96.44.16.94.27a15 15 0 0 1-.7-1.23m6.73 0a15 15 0 0 1-.7 1.23q.5-.11.95-.27a10 10 0 0 0-.25-.96M3.44 6.26C2.27 6.86 1.87 7.53 1.87 8s.4 1.14 1.57 1.74l.13.07q.18-.88.55-1.81a12 12 0 0 1-.55-1.8q-.07.02-.13.06m8.99-.07A12 12 0 0 1 11.88 8q.36.94.55 1.8l.13-.06c1.17-.6 1.56-1.27 1.56-1.74s-.39-1.14-1.56-1.74zm-7.1-.6q-.5.11-.94.27.1.46.25.96a15 15 0 0 1 .69-1.23m5.34 0a15 15 0 0 1 .7 1.23q.14-.5.24-.96-.44-.15-.94-.27M7.18 3.06c-1.09-.74-1.85-.76-2.24-.52s-.76.92-.69 2.26l.01.15a11 11 0 0 1 1.8-.41q.6-.8 1.24-1.4zm3.88-.52c-.4-.24-1.15-.22-2.25.52l-.12.08q.65.6 1.25 1.4.96.15 1.8.41v-.14c.08-1.35-.28-2.04-.68-2.27M8 3.7a10 10 0 0 0-.7.7 14 14 0 0 1 1.4 0 10 10 0 0 0-.7-.7" clipRule="evenodd"/></>,
  css: <><path fill="currentColor" d="M8 15c-5.76 0-7-1.24-7-7V2a1 1 0 0 1 1-1h6c5.77 0 7 1.24 7 7s-1.24 7-7 7" className="vector" opacity=".2"/> <path fill="currentColor" d="M10.1 9.19h.73c.03.49.22.6 1 .6.76 0 .93-.12.93-.68 0-.52-.17-.67-.94-.85-1.38-.3-1.68-.56-1.68-1.47 0-1.05.3-1.29 1.67-1.29 1.29 0 1.57.2 1.6 1.13h-.74c-.01-.34-.17-.42-.85-.42-.77 0-.94.1-.94.58 0 .42.17.55.96.73 1.36.3 1.66.58 1.66 1.59 0 1.14-.31 1.39-1.73 1.39-1.39 0-1.69-.24-1.67-1.31m-3.9 0h.74c.03.49.21.6.99.6.76 0 .93-.12.93-.68 0-.52-.17-.67-.93-.85-1.39-.3-1.69-.56-1.69-1.47 0-1.05.3-1.29 1.67-1.29 1.3 0 1.58.2 1.6 1.13h-.73c-.02-.34-.18-.42-.85-.42-.78 0-.95.1-.95.58 0 .42.17.55.96.73 1.37.3 1.67.58 1.67 1.59 0 1.14-.32 1.39-1.74 1.39-1.38 0-1.68-.24-1.66-1.31m-1.22 0h.75c-.09 1.07-.37 1.31-1.56 1.31-1.37 0-1.68-.45-1.68-2.5 0-1.96.36-2.5 1.68-2.5 1.16 0 1.44.25 1.52 1.35h-.76c-.08-.52-.22-.64-.76-.64-.74 0-.9.33-.9 1.78 0 1.47.16 1.8.9 1.8.58 0 .74-.11.8-.6"/></>,
  html: <><path fill="currentColor" d="M8 1C2.24 1 1 2.24 1 8s1.24 7 7 7 7-1.24 7-7-1.24-7-7-7" className="bg" opacity=".2"/> <path fill="currentColor" d="M10.48 3.76a.5.5 0 0 1 .4.58L10.6 5.8h1.14a.5.5 0 0 1 0 1h-1.32L10 9.2h1.08a.5.5 0 0 1 0 1H9.8l-.3 1.64a.5.5 0 1 1-.98-.18l.27-1.46H6.4l-.3 1.64a.5.5 0 1 1-.98-.18l.27-1.46H4.25a.5.5 0 0 1 0-1h1.32L6 6.8H4.93a.5.5 0 0 1 0-1H6.2l.3-1.64a.5.5 0 1 1 .98.18L7.2 5.8h2.4l.3-1.64a.5.5 0 0 1 .58-.4M6.58 9.2h2.4l.44-2.4h-2.4z" className="fg"/></>,
  yml: <><path fill="currentColor" d="M7.5 2A1.5 1.5 0 0 1 9 3.5v3A1.5 1.5 0 0 1 7.5 8h-2v2A1.5 1.5 0 0 0 7 11.5v-1A1.5 1.5 0 0 1 8.5 9h5a1.5 1.5 0 0 1 1.5 1.5v3a1.5 1.5 0 0 1-1.5 1.5h-5A1.5 1.5 0 0 1 7 13.5v-1A2.5 2.5 0 0 1 4.5 10V8h-2A1.5 1.5 0 0 1 1 6.5v-3A1.5 1.5 0 0 1 2.5 2zm1 8a.5.5 0 0 0-.5.5v3a.5.5 0 0 0 .5.5h5a.5.5 0 0 0 .5-.5v-3a.5.5 0 0 0-.5-.5zm-6-7a.5.5 0 0 0-.5.5v3a.5.5 0 0 0 .5.5h5a.5.5 0 0 0 .5-.5v-3a.5.5 0 0 0-.5-.5z"/></>,
  table: <><path fill="currentColor" d="M8 4a3 3 0 0 0 3 3h3v5.5a2.5 2.5 0 0 1-2.5 2.5h-7A2.5 2.5 0 0 1 2 12.5v-9A2.5 2.5 0 0 1 4.5 1H8z" className="bg" opacity=".4"/> <path fill="currentColor" d="M11.5 8a.5.5 0 0 1 .5.5v4a.5.5 0 0 1-.5.5h-7a.5.5 0 0 1-.5-.5v-4a.5.5 0 0 1 .5-.5zM5 12h2.5v-1H5zm3.5 0H11v-1H8.5zM5 10h2.5V9H5zm3.5 0H11V9H8.5zm1-9a.5.5 0 0 1 .354.146l4 4A.5.5 0 0 1 14 5.5V6h-3a2 2 0 0 1-2-2V1z" className="fg"/></>,
  rust: <><path fill="currentColor" fillRule="evenodd" d="M8 .8a.2.2 0 0 1 .18.1l.38.6.16.02.5-.53.01-.01a.2.2 0 0 1 .33.08l.25.68.16.05.59-.43h.02a.2.2 0 0 1 .3.14l.12.71.15.08.65-.3a.2.2 0 0 1 .2.02.2.2 0 0 1 .1.18l-.03.72.12.1.71-.16a.2.2 0 0 1 .25.25l-.17.7q.06.06.1.13l.73-.03A.2.2 0 0 1 14 4a.2.2 0 0 1 .02.2l-.3.66.08.14.71.12a.2.2 0 0 1 .14.32l-.43.59.05.16.68.25a.2.2 0 0 1 .07.35l-.53.49.01.16.62.38a.2.2 0 0 1 0 .36l-.62.38-.01.16.53.5a.2.2 0 0 1-.07.34l-.68.25-.05.16.43.59a.2.2 0 0 1-.14.32l-.72.12-.07.15.3.65a.2.2 0 0 1-.02.2.2.2 0 0 1-.18.1l-.72-.03-.1.13.16.7a.2.2 0 0 1-.25.25l-.7-.17-.13.1.03.73a.2.2 0 0 1-.1.18.2.2 0 0 1-.2.02l-.66-.3-.14.08-.12.71a.2.2 0 0 1-.32.14l-.59-.43-.16.05-.25.68a.2.2 0 0 1-.34.07l-.5-.53-.16.01-.38.62a.2.2 0 0 1-.36 0l-.38-.62-.16-.01-.5.53a.2.2 0 0 1-.34-.07l-.25-.68-.16-.05-.59.43a.2.2 0 0 1-.32-.14L5 13.78l-.15-.07-.65.3a.2.2 0 0 1-.2-.02.2.2 0 0 1-.1-.18l.03-.72-.13-.1-.7.16a.2.2 0 0 1-.25-.25l.17-.7-.1-.13-.73.03a.2.2 0 0 1-.2-.3l.3-.66-.08-.14-.71-.12a.2.2 0 0 1-.14-.32l.43-.59-.05-.16-.68-.25A.2.2 0 0 1 1 9.22l.53-.5-.02-.16-.6-.38A.2.2 0 0 1 .8 8a.2.2 0 0 1 .1-.18l.6-.38.02-.16-.53-.5a.2.2 0 0 1 .07-.34l.68-.25.05-.16-.43-.59a.2.2 0 0 1 .14-.32L2.2 5l.08-.15L2 4.2a.2.2 0 0 1 .2-.3l.72.03.1-.13-.16-.7a.2.2 0 0 1 .25-.25l.7.16.13-.1-.03-.72A.2.2 0 0 1 4 2a.2.2 0 0 1 .2-.02l.65.3L5 2.2l.12-.71v-.03a.2.2 0 0 1 .32-.1l.59.41.16-.04.25-.68.01-.02A.2.2 0 0 1 6.8.99l.49.53.16-.02.38-.61.02-.02A.2.2 0 0 1 8 .79M6.8 9.45h1.26l.06.01q.03.01.03.05v1.52q0 .07-.09.06h-4.5A5.4 5.4 0 0 0 8 13.42a5.4 5.4 0 0 0 4.45-2.33h-2.42c-.36 0-.68-.5-.77-.75-.08-.22-.2-.91-.25-1.12-.15-.61-.59-.71-.78-.73H6.8zM8 2.58a5.4 5.4 0 0 0-4.07 1.85h5.74l.17.02c.23.03.6.12.96.35.34.23.83.68.83 1.4 0 .66-.55 1.16-1.08 1.5.42.33.7.53.86 1.44.04.17.34.32.62.29.29-.03.62-.16.62-.75v-.24q0-.1.07-.1h.68A5.43 5.43 0 0 0 8 2.59M2.96 6.03a5.4 5.4 0 0 0-.19 3.37h1.66V6.03zM6.8 7.06h1.66c.35 0 .77-.12.77-.47 0-.42-.55-.53-.65-.53H6.8z" clipRule="evenodd"/></>,
  zip: <><path fill="currentColor" d="M4.585 2a2 2 0 0 1 1.028.285l1.788 1.072a1 1 0 0 0 .514.143H12A2 2 0 0 1 13.935 5H0V4a2 2 0 0 1 2-2z" className="bg" opacity=".5"/> <path fill="currentColor" fillRule="evenodd" d="M14 12a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2v-1.25h1v-1H0V6h14zM9.9 8.25c-.883 0-1.9.5-1.9.5H7v1h1v1s1.017.5 1.9.5c.884 0 1.6-.672 1.6-1.5s-.716-1.5-1.6-1.5M2 9.75v1h1v-1zm2 0v1h1v-1zm2 0v1h1v-1zm-5-1v1h1v-1zm2 0v1h1v-1zm2 0v1h1v-1z" className="fg" clipRule="evenodd"/></>,
  bash: <><path fill="currentColor" d="M8 1C2.24 1 1 2.24 1 8s1.24 7 7 7 7-1.24 7-7-1.24-7-7-7" className="bg" opacity=".2"/> <path fill="currentColor" d="M11.5 11a.5.5 0 0 1 0 1h-3a.5.5 0 0 1 0-1zM7 6.75C7 6.42 6.64 6 6 6s-1 .42-1 .75q-.01.25.22.41.26.21.89.35.74.14 1.28.53c.37.29.61.7.61 1.21 0 .87-.68 1.5-1.5 1.7v.55a.5.5 0 0 1-1 0v-.56c-.82-.18-1.5-.82-1.5-1.69a.5.5 0 0 1 1 0c0 .33.36.75 1 .75s1-.42 1-.75q.01-.25-.22-.41a2 2 0 0 0-.89-.35q-.74-.14-1.28-.53A1.5 1.5 0 0 1 4 6.75c0-.87.68-1.5 1.5-1.7V4.5a.5.5 0 0 1 1 0v.56c.82.18 1.5.82 1.5 1.69a.5.5 0 0 1-1 0" className="fg-stroke"/></>,
  c: <><path fill="currentColor" fillRule="evenodd" d="M8 1q.084 0 .166.021.098.023.186.075c1.055.624 4.22 2.486 5.277 3.11.085.05.15.112.209.192h-.002l.028.037a.5.5 0 0 1 .103.21q.031.102.033.21v6.29a.71.71 0 0 1-.347.616l-5.307 3.144a.68.68 0 0 1-.693 0l-5.307-3.144A.72.72 0 0 1 2 11.145V4.832a.71.71 0 0 1 .346-.612l5.288-3.126A.7.7 0 0 1 7.992 1zm2.901 4.349a3.75 3.75 0 1 0 0 5.302l-1.06-1.06a2.25 2.25 0 1 1 0-3.182z" clipRule="evenodd"/></>,
  cpp: <><path fill="currentColor" fillRule="evenodd" d="M8 1q.084 0 .166.021.098.023.186.075c1.055.624 4.22 2.486 5.277 3.11.085.05.15.112.209.192h-.002l.028.037a.5.5 0 0 1 .103.21q.031.102.033.21v6.29a.71.71 0 0 1-.347.616l-5.307 3.144a.68.68 0 0 1-.693 0l-5.307-3.144A.72.72 0 0 1 2 11.145V4.832a.71.71 0 0 1 .346-.612l5.288-3.126A.7.7 0 0 1 7.992 1zm2.901 4.349a3.75 3.75 0 1 0 0 5.302l-1.06-1.06a2.25 2.25 0 1 1 0-3.182z" clipRule="evenodd"/></>,
  go: <><path fill="currentColor" fillRule="evenodd" d="M4.41 4.57A3.2 3.2 0 0 1 6.87 5q.74.49 1.08 1.29.08.12-.1.16l-1.55.4c-.14.03-.15.04-.27-.1a1 1 0 0 0-.44-.34 1.6 1.6 0 0 0-1.68.14q-.95.61-.94 1.73c0 .73.52 1.33 1.25 1.43q.95.1 1.58-.6l.25-.34h-1.8c-.19 0-.24-.12-.17-.27.12-.28.34-.76.47-1a.3.3 0 0 1 .24-.14h2.98a4 4 0 0 1 .64-1.19 4 4 0 0 1 2.6-1.52 3.5 3.5 0 0 1 2.64.46q1.13.73 1.31 2.04a3.5 3.5 0 0 1-1.06 3.09q-.93.92-2.23 1.17l-.74.08a3.5 3.5 0 0 1-2.27-.8 3 3 0 0 1-.93-1.42 4 4 0 0 1-.39.61 4 4 0 0 1-2.64 1.56 3.3 3.3 0 0 1-2.5-.6 3 3 0 0 1-1.18-2.03 3.5 3.5 0 0 1 .8-2.67 4 4 0 0 1 2.6-1.58M13.1 7.5a1.53 1.53 0 0 0-1.9-1.21q-1.3.3-1.62 1.59a1.5 1.5 0 0 0 .85 1.72q.77.33 1.52-.05a2 2 0 0 0 1.18-1.74q0-.17-.03-.3" clipRule="evenodd"/></>,
};
type TreeGlyphType = keyof typeof treeFileGlyphs;
const treeExtensionGlyphs: Record<string, TreeGlyphType> = {
  txt: "text", log: "text", rst: "text", ini: "text", cfg: "text", conf: "text", env: "text",
  json: "json", json5: "json", jsonc: "json", jsonl: "json", md: "markdown", markdown: "markdown", mdx: "markdown",
  py: "python", pyi: "python", pyw: "python", pyx: "python", ts: "typescript", mts: "typescript", cts: "typescript",
  js: "javascript", mjs: "javascript", cjs: "javascript", tsx: "react", jsx: "react",
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", avif: "image", ico: "image", bmp: "image",
  css: "css", scss: "css", sass: "css", less: "css", html: "html", htm: "html", yaml: "yml", yml: "yml",
  csv: "table", tsv: "table", xls: "table", xlsx: "table", ods: "table", rs: "rust", go: "go",
  zip: "zip", gz: "zip", tar: "zip", tgz: "zip", bz2: "zip", rar: "zip", "7z": "zip", xz: "zip",
  sh: "bash", bash: "bash", zsh: "bash", fish: "bash", c: "c", h: "c", cc: "cpp", cpp: "cpp", cxx: "cpp", hpp: "cpp",
};
function TreeFileIcon({ path }: { path: string }) {
  const name = (path.split("/").pop() || path).toLowerCase();
  const type: TreeGlyphType = /^\.git(?:attributes|ignore|keep|modules)$/.test(name) ? "git" :
    /^(?:license|authors|contributors|changelog|\.env(?:\..*)?)$/.test(name) ? "text" :
      treeExtensionGlyphs[name.split(".").pop() || ""] || "default";
  return <svg className="file-type-glyph" data-file-type={type} viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">{treeFileGlyphs[type]}</svg>;
}
// BaseFileTree halves the library's indentation: gap5 + (icon16 - border1)/4.
const treeLevelIndent = 8.75;
function TreeGuides({ depth }: { depth: number }) {
  return <i className="file-tree-guides" aria-hidden="true">{Array.from({ length: depth + 1 }, (_, level) => <i key={level} style={{ left: 10.75 + level * treeLevelIndent }} />)}</i>;
}
function treeRowInset(depth: number) { return 3 + (depth + 1) * treeLevelIndent; }
const treeChangeMarks = {
  added: "M10 6.41797C10.367 6.41815 10.6649 6.71599 10.665 7.08301V9.33496H12.916C13.2832 9.33496 13.5809 9.63287 13.5811 10C13.5809 10.3671 13.2832 10.665 12.916 10.665H10.665V12.916C10.665 13.2832 10.3671 13.5809 10 13.5811C9.63284 13.5809 9.33496 13.2832 9.33496 12.916V10.665H7.08301C6.71596 10.6649 6.41815 10.367 6.41797 10C6.41813 9.63295 6.71595 9.33509 7.08301 9.33496H9.33496V7.08301C9.33513 6.71596 9.63295 6.4181 10 6.41797Z",
  deleted: "M12.916 9.33496C13.2833 9.33496 13.5811 9.63273 13.5811 10C13.5811 10.3673 13.2833 10.665 12.916 10.665H7.08301C6.71585 10.6649 6.41797 10.3672 6.41797 10C6.41797 9.63281 6.71585 9.33509 7.08301 9.33496H12.916Z",
  modified: "M10 8.33301C10.9205 8.33301 11.667 9.07953 11.667 10C11.667 10.9205 10.9205 11.667 10 11.667C9.07961 11.6669 8.334 10.9204 8.33399 10C8.33399 9.07958 9.0796 8.3331 10 8.33301Z",
};
const treeChangeOutline = "M12.666 2.66797C13.3549 2.66797 13.9121 2.66735 14.3623 2.7041C14.8202 2.74151 15.2268 2.82084 15.6035 3.0127C16.1989 3.31608 16.6829 3.80115 16.9863 4.39649C17.1781 4.77304 17.2575 5.17902 17.2949 5.63672C17.3317 6.08689 17.3311 6.64406 17.3311 7.33301V12.666C17.3311 13.3549 17.3317 13.9121 17.2949 14.3623C17.2575 14.8203 17.1782 15.2268 16.9863 15.6035C16.6829 16.1988 16.1988 16.6829 15.6035 16.9863C15.2268 17.1783 14.8203 17.2575 14.3623 17.2949C13.9121 17.3317 13.3549 17.3311 12.666 17.3311H7.33301C6.64403 17.3311 6.08691 17.3317 5.63672 17.2949C5.17899 17.2575 4.77306 17.1781 4.39649 16.9863C3.80114 16.6829 3.31609 16.1989 3.0127 15.6035C2.82085 15.2269 2.74151 14.8202 2.7041 14.3623C2.66736 13.9122 2.66797 13.3548 2.66797 12.666V7.33301C2.66797 6.64406 2.66732 6.08689 2.7041 5.63672C2.74152 5.17904 2.82093 4.77302 3.0127 4.39649C3.31613 3.80104 3.80103 3.31611 4.39649 3.0127C4.77304 2.82092 5.17902 2.74152 5.63672 2.7041C6.08691 2.66732 6.64403 2.66797 7.33301 2.66797H12.666ZM7.33301 3.99805C6.6221 3.99805 6.12859 3.99894 5.74512 4.03027C5.36955 4.06098 5.15798 4.11775 5 4.19824C4.65484 4.37414 4.37416 4.65485 4.19824 5C4.11775 5.15797 4.06098 5.36959 4.03027 5.74512C3.99894 6.12858 3.99805 6.62214 3.99805 7.33301V12.666C3.99805 13.3767 3.99898 13.8705 4.03027 14.2539C4.06094 14.6292 4.11788 14.8411 4.19824 14.999C4.37412 15.3442 4.65487 15.6258 5 15.8018C5.15795 15.8822 5.36972 15.939 5.74512 15.9697C6.12859 16.0011 6.6221 16.001 7.33301 16.001H12.666C13.3767 16.001 13.8705 16.001 14.2539 15.9697C14.6292 15.9391 14.8411 15.8821 14.999 15.8018C15.3443 15.6258 15.6258 15.3443 15.8018 14.999C15.8821 14.8411 15.9391 14.6292 15.9697 14.2539C16.001 13.8705 16.001 13.3767 16.001 12.666V7.33301C16.001 6.62213 16.0011 6.12858 15.9697 5.74512C15.939 5.36976 15.8822 5.15795 15.8018 5C15.6258 4.65488 15.3442 4.37411 14.999 4.19824C14.8411 4.11787 14.6292 4.06094 14.2539 4.03027C13.8705 3.99898 13.3767 3.99805 12.666 3.99805H7.33301Z";
function TreeChangeStatus({ status, decorative = false }: { status: string; decorative?: boolean }) {
  const kind = /[A?]/.test(status) ? "added" : status.includes("D") ? "deleted" : /[MR]/.test(status) ? "modified" : null;
  return <span className="file-change-status" data-change={kind || undefined} aria-hidden={decorative || undefined}><span className="sr-only">{status}</span>{kind && <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d={treeChangeMarks[kind]} /><path d={treeChangeOutline} /></svg>}</span>;
}


function BinaryPreview({ document }: { document: WorkspaceDocument }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!document.bytes) { setUrl(""); return; }
    const owned = URL.createObjectURL(new Blob([new Uint8Array(document.bytes)], { type: document.mime || "application/octet-stream" }));
    setUrl(owned);
    return () => URL.revokeObjectURL(owned);
  }, [document]);
  if (!url) return <p className="preview-notice">此格式暂不支持预览。</p>;
  const filename = document.path.split("/").pop();
  return <div className="binary-preview"><div className="binary-toolbar"><span>{document.kind === "pdf" ? "PDF 文档" : "图片"} · {(document.size / 1024).toFixed(1)} KB</span><a href={url} download={filename}>下载{document.kind === "pdf" ? " PDF" : "图片"}</a></div>
    {document.kind === "pdf" ? navigator.pdfViewerEnabled === false ? <p className="preview-notice">当前查看器不支持内嵌 PDF。可下载后阅读。</p> : <object className="pdf-preview" data={`${url}#view=FitH&toolbar=1&navpanes=0`} type="application/pdf" aria-label={`${filename} PDF 预览`}><p className="preview-notice">无法显示 PDF，<a href={url} download={filename}>下载文件</a>后阅读。</p></object> : <div className="image-preview"><img src={url} alt={filename || "工作空间图片"} /></div>}
  </div>;
}

function DocumentPane({ state, path, mode, source, native, visible, refresh, onOpen }: { state: DocumentState; path: string; mode: ViewMode; source: FileSource | null; native: boolean; visible: boolean; refresh: number; onOpen: (path: string) => void }) {
  const [diff, setDiff] = useState<FileDiff | null>(null), [diffError, setDiffError] = useState("");
  const [diffLoading, setDiffLoading] = useState(false);
  useEffect(() => {
    if (mode !== "diff" || !source || !visible) return;
    let active = true; setDiffLoading(true); setDiffError("");
    void source.diff(path).then((value) => { if (active) setDiff(value); }).catch((problem) => { if (active) setDiffError(errorMessage(problem)); }).finally(() => { if (active) setDiffLoading(false); });
    return () => { active = false; };
  }, [mode, path, source, visible, refresh]);
  const document = state.document;
  return <div className="document-pane" hidden={!visible}>
    <div className="document-main" hidden={mode !== "diff"}>
      {diff?.scope === "staged" && <p className="preview-notice">暂存区更改 · 工作树与 HEAD 相同，以下显示暂存区相对 HEAD 的差异。</p>}
      {diffLoading && <p className="preview-notice" role="status">正在读取更改…</p>}{diffError && <p className="preview-notice error" role="alert">{diffError}</p>}
      {diff && <>{diff.truncated && <p className="preview-notice">更改内容过长，仅显示部分。</p>}{diff.binary ? <p className="preview-notice">二进制文件已更改，无法显示文本差异。</p> : diff.text ? <div className="document-source" hidden={!!diffError}><CodePreview text={diff.text} path={`${path}:diff`} diff wrapSmallDiff /></div> : !diffLoading && <p className="preview-notice">没有可显示的文本差异。</p>}</>}
    </div>
    <div className="document-main" hidden={mode === "diff"}>
      {state.loading && <p className="preview-notice" role="status">正在读取文件…</p>}{state.error && <p className="preview-notice error" role="alert">{state.error}</p>}{document && <>
      {document.kind === "text" ? <><div className="document-rendered" hidden={mode === "source" || !/\.md(?:own)?$/i.test(path)}><FileMarkdown content={document.text || ""} path={path} native={native} onOpen={onOpen} /></div><div className="document-source" hidden={mode !== "source" && /\.md(?:own)?$/i.test(path)}><CodePreview text={document.text || ""} path={path} /></div></> : document.kind === "unsupported" ? <p className="preview-notice">此文件格式不支持预览（{document.size.toLocaleString()} 字节）。</p> : <BinaryPreview document={document} />}
    </>}</div>
  </div>;
}

function Directory({ path, source, active, onOpen, query, refresh, depth = 0, statuses }: { path: string; source: FileSource; active: string; onOpen: (path: string) => void; query: string; refresh: number; depth?: number; statuses: Map<string, string> }) {
  const [entries, setEntries] = useState<WorkspaceFile[]>([]), [error, setError] = useState(""), [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(new Set<string>());
  useLayoutEffect(() => { setEntries([]); setError(""); setExpanded(new Set()); }, [path, source]);
  useEffect(() => { let current = true; setLoading(true); setError(""); void source.list(path).then((value) => { if (current) setEntries(value); }).catch((problem) => { if (current) { setError(errorMessage(problem)); setEntries([]); } }).finally(() => { if (current) setLoading(false); }); return () => { current = false; }; }, [path, source, refresh]);
  useEffect(() => { const branch = entries.find((entry) => entry.directory && active.startsWith(`${entry.path}/`)); if (branch) setExpanded((old) => old.has(branch.path) ? old : new Set([...old, branch.path])); }, [active, entries]);
  return <div className="file-directory">{loading && <p className="tree-notice" role="status">读取目录…</p>}{error && <p className="tree-notice error" role="alert">{error}</p>}{!loading && !error && !entries.length && <p className="tree-notice">空目录</p>}
    {entries.filter((entry) => !query || entry.directory || entry.path.toLocaleLowerCase().includes(query)).map((entry) => <div key={entry.path}>
      <button type="button" className={`file-tree-row${active === entry.path ? " selected" : ""}`} data-status={entry.directory ? undefined : statuses.get(entry.path)?.trim()} style={{ paddingInlineStart: treeRowInset(depth) }} aria-expanded={entry.directory ? expanded.has(entry.path) : undefined} aria-current={active === entry.path ? "true" : undefined} title={entry.path} onClick={() => entry.directory ? setExpanded((old) => { const next = new Set(old); if (next.has(entry.path)) next.delete(entry.path); else next.add(entry.path); return next; }) : onOpen(entry.path)}>
        <TreeGuides depth={depth} />{entry.directory ? <span className="file-tree-chevron" data-expanded={expanded.has(entry.path)}>›</span> : <TreeFileIcon path={entry.path} />}<span>{entry.name}</span>{entry.directory ? [...statuses.keys()].some((path) => path.startsWith(`${entry.path}/`)) && <i className="changed-directory-dot" aria-hidden="true" /> : statuses.get(entry.path) && <TreeChangeStatus status={statuses.get(entry.path)!} decorative />}
      </button>{entry.directory && <div hidden={!expanded.has(entry.path)}>{expanded.has(entry.path) && <Directory path={entry.path} source={source} active={active} onOpen={onOpen} query={query} refresh={refresh} depth={depth + 1} statuses={statuses} />}</div>}
    </div>)}
  </div>;
}

function ChangeDirectory({ files, active, onOpen, root = "", depth = 0 }: { files: ChangeSet["files"]; active: string; onOpen: (path: string) => void; root?: string; depth?: number }) {
  const [closed, setClosed] = useState(new Set<string>());
  const groups = new Map<string, ChangeSet["files"]>();
  const leaves: ChangeSet["files"] = [];
  for (const file of files) {
    const relative = file.path.slice(root.length), slash = relative.indexOf("/");
    if (slash < 0) { leaves.push(file); continue; }
    const directory = relative.slice(0, slash), group = groups.get(directory) || [];
    group.push(file); groups.set(directory, group);
  }
  return <>{[...groups.entries()].map(([name, children]) => <div className="changed-directory" key={name}>
    <button type="button" className="file-tree-row changed-directory-toggle" style={{ paddingInlineStart: treeRowInset(depth) }} aria-expanded={!closed.has(name)} title={`${root}${name}`} onClick={() => setClosed((old) => { const next = new Set(old); if (next.has(name)) next.delete(name); else next.add(name); return next; })}><TreeGuides depth={depth} /><span className="file-tree-chevron" data-expanded={!closed.has(name)}>›</span><span>{name}</span><i className="changed-directory-dot" aria-hidden="true" /></button>
    <div hidden={closed.has(name)}><ChangeDirectory files={children} root={`${root}${name}/`} depth={depth + 1} active={active} onOpen={onOpen} /></div>
  </div>)}{leaves.map((file) => <button type="button" key={file.path} className={`file-tree-row changed-file${active === file.path ? " selected" : ""}`} data-status={file.status.trim()} style={{ paddingInlineStart: treeRowInset(depth) }} title={file.path} onClick={() => onOpen(file.path)}><TreeGuides depth={depth} /><TreeFileIcon path={file.path} /><span>{file.path.slice(root.length)}</span>{file.added !== undefined && <small className="file-added sr-only">+{file.added}</small>}{file.removed !== undefined && <small className="file-removed sr-only">−{file.removed}</small>}<TreeChangeStatus status={file.status} /></button>)}</>;
}

export default function FilePreviewWorkspace({ native, source: nativeSource, identity, workspaceName, open, present, visible, request, connection, onRequest, onHistoryLoaded, onClose, onChanges, expanded = false, onToggleExpanded }: {
  native: boolean; source: FileSource | null; identity: string; workspaceName: string; open: boolean; present: boolean; visible: boolean; request: PreviewRequest | null; connection: Connection | null; onRequest: (messageId: string) => boolean; onHistoryLoaded: (record: ArtifactDiff) => void; onClose: () => void; onChanges: (count: number | undefined) => void;
  expanded?: boolean; onToggleExpanded?: () => void;
}) {
  const [imported, setImported] = useState<FileSource | null>(null), [importName, setImportName] = useState("");
  const source = nativeSource || imported;
  const [tabs, setTabs] = useState<string[]>([]), [active, setActive] = useState("");
  const [historyTabs, setHistoryTabs] = useState<Record<string, { receiptId: string; path: string }>>({});
  const [documents, setDocuments] = useState<Record<string, DocumentState>>({});
  const [modes, setModes] = useState<Record<string, ViewMode>>({}), [tree, setTree] = useState(true), [query, setQuery] = useState("");
  // Native U5mJ/gbrq: one shared 250px width, bounded to 200px / 60% of the pane.
  const [treeWidth, setTreeWidth] = useState(250);
  const [treePaneWidth, setTreePaneWidth] = useState(0);
  const content = useRef<HTMLDivElement>(null);
  const treeDrag = useRef<{ x: number; width: number } | null>(null);
  const maximumTreeWidth = Math.max(200, treePaneWidth * .6);
  const renderedTreeWidth = Math.min(treeWidth, maximumTreeWidth);
  const resizeTree = (width: number) => {
    if (width < 100) { setTree(false); return; }
    setTreeWidth(Math.max(200, Math.min(width, maximumTreeWidth)));
  };
  const [scope, setScope] = useState<PreviewRequest["mode"]>("files"), [paths, setPaths] = useState<string[]>([]);
  const [allFilesPreference, setAllFilesPreference] = useState<boolean | null>(null), [changedFilesOpen, setChangedFilesOpen] = useState(true);
  const [changes, setChanges] = useState<ChangeSet>(emptyChanges), [changesError, setChangesError] = useState(""), [refresh, setRefresh] = useState(0);
  const [feedback, setFeedback] = useState("");
  const filesInput = useRef<HTMLInputElement>(null), directoryInput = useRef<HTMLInputElement>(null), panel = useRef<HTMLElement>(null);
  const sourceRef = useRef(source), documentRef = useRef(documents), readSerial = useRef(new Map<string, number>()), lastRequest = useRef(0), feedbackTimer = useRef<number>();
  const nextReadId = useRef(0);
  const focusAfterTabClose = useRef(false);
  const latestIdentity = useRef(identity);
  useLayoutEffect(() => {
    const element = content.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setTreePaneWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  sourceRef.current = source; documentRef.current = documents;
  useLayoutEffect(() => { if (latestIdentity.current !== identity) { setImported(null); setImportName(""); latestIdentity.current = identity; } }, [identity]);
  useEffect(() => { directoryInput.current?.setAttribute("webkitdirectory", ""); return () => window.clearTimeout(feedbackTimer.current); }, []);
  const report = (message: string) => { setFeedback(message); window.clearTimeout(feedbackTimer.current); feedbackTimer.current = window.setTimeout(() => setFeedback(""), 2200); };
  useLayoutEffect(() => { setTabs([]); setHistoryTabs({}); setActive(""); setDocuments({}); setModes({}); readSerial.current.clear(); setChanges(emptyChanges); onChanges(undefined); }, [identity, source, onChanges]);
  const read = useCallback((path: string, force = false) => {
    if (!source || (!force && documentRef.current[path])) return;
    const serial = ++nextReadId.current; readSerial.current.set(path, serial);
    setDocuments((old) => ({ ...old, [path]: { loading: true, error: "", document: old[path]?.document } }));
    void source.read(path).then((document) => { if (sourceRef.current === source && readSerial.current.get(path) === serial) setDocuments((old) => ({ ...old, [path]: { loading: false, error: "", document } })); }).catch((problem) => { if (sourceRef.current === source && readSerial.current.get(path) === serial) setDocuments((old) => ({ ...old, [path]: { loading: false, error: errorMessage(problem) } })); });
  }, [source]);
  const openFile = useCallback((value: string) => { try { const path = relativeFilePath(value); setTabs((old) => old.includes(path) ? old : [...old, path]); setActive(path); read(path); } catch (problem) { report(errorMessage(problem)); } }, [read]);
  useEffect(() => {
    if (!request || request.id === lastRequest.current) return;
    lastRequest.current = request.id;
    if (request.mode === "history" && request.receiptId && request.path) {
      try {
        const path = relativeFilePath(request.path), key = `history:${request.receiptId}`;
        setHistoryTabs((old) => ({ ...old, [key]: { receiptId: request.receiptId!, path } }));
        setTabs((old) => old.includes(key) ? old : [...old, key]); setActive(key);
      } catch (problem) { report(errorMessage(problem)); }
    } else {
      setScope(request.mode); setPaths(request.paths || []); setTree(true);
      if (request.path) openFile(request.path);
    }
  }, [request, openFile]);
  useEffect(() => { if (source) tabs.filter((path) => !path.startsWith("history:")).forEach((path) => read(path)); }, [source, tabs, read]);
  useEffect(() => {
    if (!source || !open) return;
    let current = true, busy = false;
    const load = async () => { if (busy) return; busy = true; try { const result = await source.changes(); if (current) { setChanges(result); setChangesError(""); onChanges(result.repository ? result.files.length : undefined); } } catch (problem) { if (current) { setChanges(emptyChanges); setChangesError(errorMessage(problem)); onChanges(undefined); } } finally { busy = false; } };
    void load(); const timer = window.setInterval(() => void load(), 6000);
    return () => { current = false; window.clearInterval(timer); };
  }, [source, open, refresh, onChanges]);
  const chooseFiles = (input: FileList | null) => { if (!input?.length) return; const files = [...input]; setImported(importedFileSource(files)); setImportName(files[0].webkitRelativePath?.split("/")[0] || "已选择的文件"); setTabs([]); setDocuments({}); setActive(""); setScope("files"); setAllFilesPreference(true); };
  const closeTab = (path: string) => {
    const index = tabs.indexOf(path);
    if (index < 0) return;
    const next = tabs.filter((tab) => tab !== path);
    focusAfterTabClose.current = true;
    setTabs(next);
    if (active === path) setActive(next[Math.min(index, next.length - 1)] || "");
    readSerial.current.delete(path);
    setDocuments((old) => { const copy = { ...old }; delete copy[path]; return copy; });
  };
  useLayoutEffect(() => {
    if (!focusAfterTabClose.current) return;
    focusAfterTabClose.current = false;
    if (!open || !present || !visible) return;
    // The removed tab may have held focus. Move to the selected tab after
    // React commits, or return to the real file entry when none remain.
    const target = panel.current?.querySelector<HTMLButtonElement>(
      active ? '.preview-tabs [role="tab"][aria-selected="true"]' : '.preview-new-tab',
    );
    target?.focus();
  }, [active, tabs, open, present, visible]);
  const mode = modes[active] || "preview", document = documents[active]?.document;
  const activeHistory = historyTabs[active], activePath = activeHistory?.path || active;
  const tabPath = (key: string) => historyTabs[key]?.path || key;
  const tabName = (key: string) => `${tabPath(key).split("/").pop()}${historyTabs[key] ? " · 记录的更改" : ""}`;
  const changed = changes.files.find((file) => file.path === active);
  const displayedChanges = scope === "conversation" ? paths.map((path) => changes.files.find((file) => file.path === path) || { path, status: "记录" }) : changes.files;
  // Prefer the changes hierarchy when it contains real entries, without replacing
  // the user's explicit choice about whether the full workspace tree is open.
  const allFilesOpen = allFilesPreference ?? (displayedChanges.length === 0);
  const statuses = useMemo(() => new Map(changes.files.map((file) => [file.path, file.status])), [changes.files]);
  const filter = query.toLocaleLowerCase().trim();
  const openChanged = (path: string) => { openFile(path); if (changes.files.some((change) => change.path === path)) setModes((old) => ({ ...old, [path]: "diff" })); };
  const browseFile = () => {
    if (!native) { filesInput.current?.click(); return; }
    setTree(true); setScope("files"); setAllFilesPreference(true);
    requestAnimationFrame(() => panel.current?.querySelector<HTMLInputElement>('[aria-label="筛选已加载文件"]')?.focus());
  };
  const findInFile = () => panel.current?.querySelector('.file-tab-panel:not([hidden]) .document-main:not([hidden]) .document-source:not([hidden]) .code-preview')?.dispatchEvent(new Event("forge-find"));
  const canFind = document?.kind === "text" && (!/\.md(?:own)?$/i.test(active) || mode !== "preview");
  return <aside ref={panel} className={`file-preview-workspace${tree ? " with-file-tree" : ""}${expanded ? " is-expanded" : ""}`} hidden={!present || !visible} aria-label="工作空间文件预览" onKeyDown={(event) => { if (event.key === "Escape" && !event.defaultPrevented && !(event.target instanceof HTMLInputElement)) { event.preventDefault(); onClose(); } }}>
    <div className="preview-tabs-bar"><button type="button" className="preview-open-file-leading" aria-label="在工作空间打开文件" onClick={browseFile}><FileIcon />打开文件</button><div className="preview-tabs" role="tablist" aria-label="打开的文件">{tabs.map((path) => <div className={`preview-tab${active === path ? " active" : ""}`} key={path}><button type="button" role="tab" id={`file-tab-${tabs.indexOf(path)}`} aria-controls={`file-pane-${tabs.indexOf(path)}`} aria-selected={active === path} tabIndex={active === path ? 0 : -1} title={tabPath(path)} onClick={() => setActive(path)} onKeyDown={(event) => { const index = tabs.indexOf(path), next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1; if (next >= 0) { event.preventDefault(); setActive(tabs[next]); requestAnimationFrame(() => panel.current?.querySelector<HTMLButtonElement>(`#file-tab-${next}`)?.focus()); } if (event.key === "Delete" && !event.altKey && !event.ctrlKey && !event.metaKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); closeTab(path); } }}>{tabPath(path).endsWith(".py") ? <TreeFileIcon path={tabPath(path)} /> : <FileIcon />}<span>{tabName(path)}</span></button><button type="button" className="preview-tab-close" aria-label={`关闭 ${historyTabs[path] ? tabName(path) : path}`} onClick={() => closeTab(path)}>×</button></div>)}<button type="button" className="preview-new-tab" aria-label="打开文件" title="打开文件" onClick={browseFile}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d="M8 2.5v11M2.5 8h11" /></svg></button></div>{onToggleExpanded && <button type="button" className="icon-button" aria-label="展开文件预览" aria-pressed={expanded} title={expanded ? "还原文件预览" : "展开文件预览"} onClick={onToggleExpanded}><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d={expanded ? "M7 2v5H2m11 11v-5h5" : "M3 8V3h5m9 9v5h-5"} /></svg></button>}<button type="button" className="icon-button preview-close" aria-label="关闭文件预览" title="关闭预览 · Escape" onClick={onClose}>×</button></div>
    <div className="preview-toolbar"><span className="preview-breadcrumb" title={activePath || workspaceName}><span className="preview-breadcrumb-root">{source === imported ? importName : workspaceName}</span>{(activePath || "文件").split("/").map((part, index, parts) => <span className="preview-breadcrumb-part" key={`${part}:${index}`}><span aria-hidden="true">›</span>{index === parts.length - 1 ? <strong>{part}</strong> : <span>{part}</span>}</span>)}</span>{activeHistory ? <button type="button" className="text-button" onClick={() => openFile(activeHistory.path)}>查看当前文件</button> : active && <div className="preview-view-actions">{canFind && <button type="button" className="preview-icon-action" aria-label="查找文件内容" title="在文件中查找 · Ctrl+F" onClick={findInFile}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" aria-hidden="true"><circle cx="6.7" cy="6.7" r="3.7" /><path d="m9.5 9.5 3 3" /></svg></button>}{changed && <button type="button" aria-pressed={mode === "diff"} onClick={() => setModes((old) => ({ ...old, [active]: mode === "diff" ? "preview" : "diff" }))}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><rect x="2.5" y="2" width="11" height="12" rx="2" /><path d="M8 4.5v4M6 6.5h4M6 11h4" /></svg><span className="preview-action-label">显示更改</span></button>}{document?.kind === "text" && /\.md(?:own)?$/i.test(active) && <button type="button" className="preview-source-toggle" aria-label={mode === "source" ? "预览" : "查看源代码"} aria-pressed={mode === "source"} onClick={() => setModes((old) => ({ ...old, [active]: mode === "source" ? "preview" : "source" }))}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><path d="M3 1.5h6l3 3v3M9 1.5V5h3M3 1.5v13h4" /><circle cx="10.5" cy="10.5" r="2.5" /><path d="m12.5 12.5 2 2" /></svg><span className="preview-action-label">{mode === "source" ? "查看预览" : "查看源代码"}</span></button>}{document?.text !== undefined && <button type="button" className="preview-icon-action" aria-label="复制文件内容" title="复制内容" onClick={() => void navigator.clipboard.writeText(document.text || "").then(() => report("已复制文件内容"), () => report("无法复制，请手动选择。"))}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><rect x="5" y="5" width="9" height="9" rx="2" /><path d="M11 5V3a1 1 0 0 0-1-1H3a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h2" /></svg></button>}<button type="button" className="preview-icon-action" aria-label="重新读取文件" title="重新读取" onClick={() => { read(active, true); setRefresh((value) => value + 1); }}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><path d="M13 7a5 5 0 1 0-.7 3.6M13 2.5V7H8.5" /></svg></button></div>}<button type="button" className="preview-tree-toggle" aria-label="切换文件树" aria-pressed={tree} title="文件树" onClick={() => setTree((value) => !value)}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><path d="M2 5h5l1.5 1.5H13v6H2V5Zm3-3h5l1.5 1.5H15v6h-2M5 5V2" /></svg></button></div>
    {feedback && <span className="preview-feedback" role="status">{feedback}</span>}
    <div ref={content} className="preview-content"><div className="preview-document">{tabs.map((path, index) => <div className="file-tab-panel" key={path} id={`file-pane-${index}`} role="tabpanel" aria-labelledby={`file-tab-${index}`} hidden={active !== path}>{historyTabs[path] ? <ArtifactHistoryPreview connection={connection} receiptId={historyTabs[path].receiptId} visible={active === path} onClose={() => closeTab(path)} onRequest={onRequest} onLoaded={onHistoryLoaded} /> : <DocumentPane state={documents[path] || { loading: !!source, error: source ? "" : "请先选择此文件所在的目录。" }} path={path} mode={modes[path] || "preview"} source={source} native={native} refresh={refresh} visible={active === path} onOpen={openFile} />}</div>)}{!active && <div className="preview-empty"><FileIcon folder /><h3>工作空间文件</h3><p>{source ? "从文件树选择文件，或点击对话中的文件引用。" : "在浏览器中选择本地目录或文件进行只读预览。"}</p>{!native && <div className="button-row"><button type="button" className="button" onClick={() => directoryInput.current?.click()}>选择目录</button><button type="button" className="button" onClick={() => filesInput.current?.click()}>选择文件</button></div>}</div>}</div>
      <div className="preview-file-tree" hidden={!tree} style={{ width: renderedTreeWidth, maxWidth: "60%" }}>
        <div className="file-tree-divider" role="separator" aria-label="调整文件树宽度" aria-orientation="vertical" aria-valuemin={200} aria-valuemax={Math.round(maximumTreeWidth)} aria-valuenow={Math.round(renderedTreeWidth)} tabIndex={0} onPointerDown={(event) => { treeDrag.current = { x: event.clientX, width: renderedTreeWidth }; event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={(event) => { if (treeDrag.current) resizeTree(treeDrag.current.width + treeDrag.current.x - event.clientX); }} onPointerUp={(event) => { treeDrag.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }} onPointerCancel={() => { treeDrag.current = null; }} onKeyDown={(event) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); resizeTree(event.key === "Home" ? 200 : event.key === "End" ? maximumTreeWidth : renderedTreeWidth + (event.key === "ArrowLeft" ? 10 : -10)); } }} />
        <div className="file-tree-search"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><circle cx="7" cy="7" r="4.8" /><path d="m10.5 10.5 3.5 3.5" /></svg><input aria-label="筛选已加载文件" placeholder="筛选文件…" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
        <div className="file-tree-scopes"><button type="button" className="file-tree-all-changes" onClick={() => { setScope("changes"); setChangedFilesOpen(true); }}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><path d="M5 3h9M5 8h9M5 13h9" /><circle cx="1.5" cy="3" r=".5" /><circle cx="1.5" cy="8" r=".5" /><circle cx="1.5" cy="13" r=".5" /></svg>所有更改</button><button type="button" aria-label="刷新文件树" title="刷新文件树与更改" onClick={() => setRefresh((value) => value + 1)}>↻</button></div>
        <div className="file-tree-scroll">
          <section className="file-tree-section">
            <button type="button" className="file-tree-section-heading" aria-label={`已更改${changes.repository ? ` ${changes.files.length}` : ""}`} aria-pressed={scope === "changes" || scope === "conversation"} aria-expanded={changedFilesOpen || !!filter} onClick={() => { setScope("changes"); setChangedFilesOpen((value) => scope !== "changes" || !value); }}><span className="file-tree-chevron" data-expanded={changedFilesOpen || !!filter}>›</span><span>{scope === "conversation" ? "对话记录的文件" : "已更改的文件"}</span>{displayedChanges.length > 0 && <i className="changed-directory-dot" aria-hidden="true" />}</button>
            <div hidden={!changedFilesOpen && !filter}>
              {changesError && <p className="tree-notice error" role="alert">{changesError}</p>}
              {scope === "conversation" && <p className="file-tree-caption">已加载对话中记录的文件；差异来自当前 Git。</p>}
              {displayedChanges.length ? <ChangeDirectory files={displayedChanges.filter((file) => file.path.toLocaleLowerCase().includes(filter))} active={active} onOpen={openChanged} /> : <p className="tree-notice">{changes.repository ? "当前没有文件更改。" : "当前文件来源没有 Git 变更记录。"}</p>}
              {changes.truncated && <p className="tree-notice">变更较多，仅显示已读取部分。</p>}
            </div>
          </section>
          <section className="file-tree-section">
            <button type="button" className="file-tree-section-heading" aria-pressed={scope === "files"} aria-expanded={allFilesOpen || !!filter} onClick={() => { setScope("files"); setAllFilesPreference(!allFilesOpen); }}><span className="file-tree-chevron" aria-hidden="true" data-expanded={allFilesOpen || !!filter}>›</span><span>所有文件</span></button>
            <div hidden={!allFilesOpen && !filter}>{source ? <Directory path="" source={source} active={active} onOpen={openFile} query={filter} refresh={refresh} statuses={statuses} /> : <p className="tree-notice">尚未选择文件。</p>}</div>
          </section>
        </div>{!native && <button type="button" className="file-import-button" onClick={() => directoryInput.current?.click()}>选择本地目录…</button>}
      </div>
    </div><input ref={filesInput} type="file" multiple hidden aria-label="选择预览文件" onChange={(event) => { chooseFiles(event.target.files); event.currentTarget.value = ""; }} /><input ref={directoryInput} type="file" multiple hidden aria-label="选择预览目录" onChange={(event) => { chooseFiles(event.target.files); event.currentTarget.value = ""; }} />
  </aside>;
}

export function PreviewDivider({ width, minimum, maximum, onResize, onReset }: { width: number; minimum: number; maximum: number; onResize: (width: number) => void; onReset: () => void }) {
  const drag = useRef<{ x: number; width: number } | null>(null);
  const clamp = (value: number) => Math.min(maximum, Math.max(minimum, value));
  const move = (event: ReactPointerEvent<HTMLDivElement>) => { if (drag.current) onResize(drag.current.width + drag.current.x - event.clientX); };
  return <div className="preview-divider" role="separator" aria-label="调整文件预览宽度" aria-orientation="vertical" aria-valuemin={minimum} aria-valuemax={Math.round(maximum)} aria-valuenow={Math.round(width)} tabIndex={0} onDoubleClick={onReset} onPointerDown={(event) => { drag.current = { x: event.clientX, width }; event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={move} onPointerUp={(event) => { drag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} onPointerCancel={() => { drag.current = null; }} onKeyDown={(event) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); onResize(event.key === "Home" ? minimum : event.key === "End" ? maximum : clamp(width + (event.key === "ArrowLeft" ? 10 : -10))); } }} />;
}
