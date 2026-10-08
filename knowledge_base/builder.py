# -*- coding: utf-8 -*-
"""
knowledge_base.builder —— 课程资料 → 章节 → 知识点/公式 → 入库

入库来源：
1) 课程文档/PPT/PDF（用**已登录会话**以 GET 方式下载到本地 data/downloads）；
2) 小节页面正文（视频讲义、网页资料）；
3) 用户手动放入 data/downloads 的本地资料文件（`kb build --local` 扫描入库）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from browser.js import COLLECT_TEXT_BLOCKS_JS
from knowledge_base.extractors import (
    ParsedDoc, chunk_blocks, guess_knowledge_points, is_noise, parse_file, parse_web_blocks,
)
from utils.logger import get_logger
from utils.text import one_line
from utils.time_util import fmt_duration

log = get_logger("knowledge_base.builder")

SAFE_NAME_RE = re.compile(r"[^\w\u4e00-\u9fff\.\-]+")


@dataclass
class KnowledgeBase:
    store: Any
    cfg: Any
    browser: Any = None
    engine: Any = None          # 可选：AI 知识点提炼

    # ------------------------------------------------------------ 文件下载
    def download(self, url: str, course_id: int | None = None, filename: str = "") -> Path | None:
        """下载课程资料（只读 GET，保存到 data/downloads/<course>/）。"""
        if self.browser is None:
            log.warning("未提供浏览器会话，无法带着登录态下载")
            return None
        try:
            session = self.browser.http_session()
            resp = session.get(url, timeout=60, stream=True)
        except Exception as exc:
            log.warning("下载失败：%s", exc)
            return None
        if resp.status_code >= 400:
            log.warning("下载返回 HTTP %s：%s", resp.status_code, url[:120])
            return None
        name = filename or resp.headers.get("Content-Disposition", "").split("filename=")[-1]
        if not name or len(name) < 3:
            tail = url.split("?")[0].rsplit("/", 1)[-1]
            name = tail or f"file_{hashlib.md5(url.encode()).hexdigest()[:10]}"
        from urllib.parse import unquote

        name = SAFE_NAME_RE.sub("_", unquote(one_line(name)))[:120] or "file.bin"
        sub = f"course_{course_id}" if course_id else "common"
        target = Path(self.cfg.path("KB_DOWNLOAD_DIR")) / sub / name
        target.parent.mkdir(parents=True, exist_ok=True)
        limit_mb = float(self.cfg.get("KB_MAX_DOC_MB") or 60)
        written = 0
        try:
            with target.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > limit_mb * 1024 * 1024:
                        log.warning("文件超过大小上限 %.0fMB，跳过：%s", limit_mb, name)
                        target.unlink(missing_ok=True)
                        return None
                    fh.write(chunk)
        except Exception as exc:
            log.warning("写入文件失败：%s", exc)
            return None
        log.info("已下载资料：%s（%s 字节）", target, written)
        return target

    # ------------------------------------------------------------ 入库
    def ingest_file(self, path: str | Path, course_id: int | None = None, chapter_key: str = "",
                    title: str = "", url: str = "") -> dict[str, Any]:
        p = Path(path)
        stat = {"path": str(p), "chunks": 0, "points": 0, "status": "failed", "error": ""}
        parsed = parse_file(p)
        if parsed.error or not parsed.blocks:
            stat["error"] = parsed.error or "无正文内容"
            self.store.kb_add_doc({
                "course_id": course_id, "chapter_key": chapter_key, "title": title or parsed.title,
                "doc_type": parsed.doc_type, "path": str(p), "url": url, "size": p.stat().st_size if p.exists() else 0,
                "chars": 0, "status": "skipped" if not parsed.blocks else "failed", "error": stat["error"],
            })
            return stat
        stat["status"] = "done"
        stat["chars"] = parsed.chars
        doc_id = self.store.kb_add_doc({
            "course_id": course_id, "chapter_key": chapter_key, "title": title or parsed.title,
            "doc_type": parsed.doc_type, "path": str(p), "url": url,
            "size": p.stat().st_size if p.exists() else 0, "chars": parsed.chars, "status": "done", "error": "",
        })
        chunks = chunk_blocks(parsed, int(self.cfg.get("KB_CHUNK_SIZE") or 900))
        for ch in chunks:
            ch["chapter_key"] = chapter_key
        self.store.kb_add_chunks(doc_id, chunks, course_id=course_id, chapter_key=chapter_key)
        stat["chunks"] = len(chunks)
        stat["doc_id"] = doc_id
        stat["points"] = self._harvest_points(doc_id, chunks, course_id, chapter_key, parsed)
        log.info("资料入库：%s → %s 块 / %s 知识点", parsed.title, len(chunks), stat["points"])
        return stat

    def _harvest_points(self, doc_id: int, chunks: Sequence[dict[str, Any]], course_id: int | None,
                        chapter_key: str, parsed: ParsedDoc) -> int:
        """规则 + （可选）AI 提炼知识点，作为 kind=formula/definition 的块单独入库便于检索。"""
        points = guess_knowledge_points(chunks, limit=25)
        if self.engine and getattr(self.engine, "ai", None) and self.engine.ai.enabled:
            try:
                points = self._ai_points(parsed) or points
            except Exception as exc:
                log.debug("AI 知识点提炼失败，退回规则法：%s", exc)
        extra = []
        for i, pt in enumerate(points):
            text = f"{pt.get('name', '')}｜{pt.get('summary', '')}"
            extra.append({
                "seq": 900000 + i, "text": text, "loc": f"知识点提炼（{pt.get('type', 'concept')}）",
                "kind": "formula" if pt.get("type") == "formula" else "definition",
                "terms": pt.get("keywords", "") or "", "chapter_key": chapter_key,
            })
        if extra:
            self.store.kb_add_chunks(doc_id, extra, course_id=course_id, chapter_key=chapter_key)
        return len(extra)

    def _ai_points(self, parsed: ParsedDoc) -> list[dict[str, Any]]:
        from ai.prompts import SYSTEM_KB

        sample = "\n".join(b.text for b in parsed.blocks[:8])[:3500]
        if len(one_line(sample)) < 60:
            return []
        data = self.engine.ai.ask_json(
            SYSTEM_KB, f"资料标题：{parsed.title}\n内容：\n{sample}", purpose="kb_points", max_tokens=1200
        )
        items = data.get("points") if isinstance(data, dict) else data
        out = []
        for it in items or []:
            if not isinstance(it, dict) or not one_line(str(it.get("name", ""))):
                continue
            out.append({
                "name": one_line(str(it.get("name"))), "type": str(it.get("type") or "concept"),
                "summary": one_line(str(it.get("summary") or "")),
                "keywords": " ".join(str(x) for x in (it.get("keywords") or [])) or one_line(str(it.get("name"))),
            })
        return out[:25]

    # ------------------------------------------------------------ 页面正文
    def ingest_page(self, page: Any, course_id: int | None = None, chapter_key: str = "",
                    title: str = "") -> dict[str, Any]:
        """
        抓取“当前小节正文”入库。

        关键：学习通点章节里的小节时是**内层 iframe 跳转**，顶层 URL 仍是章节页，
        所以必须逐 frame 评估正文量、挑真正的内容 frame，而不是看顶层 URL。
        """
        NAV_KEYS = ("/mycourse/stu", "studentcourse", "/mooc2-ans/course",
                    "/exam/exam-list", "/work/list", "/coursedata/stu-datalist")
        best_score, best_frame, best_blocks, best_url = -1, None, [], ""
        for frame in _frames(page):
            try:
                got = frame.evaluate(COLLECT_TEXT_BLOCKS_JS, 2000) or []
            except Exception:
                got = []
            chars = sum(len(str(b.get("text", "") or "")) for b in got)
            url = str(getattr(frame, "url", "") or "")
            nav = any(k in url for k in NAV_KEYS)
            score = chars // 20 if nav else chars
            if score > best_score:
                best_score, best_frame, best_blocks, best_url = score, frame, got, url
        if best_frame is None or not best_blocks:
            return {"status": "skipped", "chunks": 0, "points": 0, "error": "页面无可读正文"}
        if any(k in best_url for k in NAV_KEYS):
            return {"status": "skipped", "chunks": 0, "points": 0, "error": "目录/门户页，跳过"}
        kept = [b for b in best_blocks if not is_noise(b.get("text", ""))]
        if len(kept) < max(2, int(len(best_blocks) * 0.4)):
            return {"status": "skipped", "chunks": 0, "points": 0,
                    "error": "有效正文过少（多为导航噪声）"}
        parsed = parse_web_blocks(kept, title=title or _page_title(page))
        if parsed.chars < 60:
            return {"status": "skipped", "chunks": 0, "points": 0, "error": "页面正文过短"}
        doc_id = self.store.kb_add_doc({
            "course_id": course_id, "chapter_key": chapter_key, "title": parsed.title,
            "doc_type": "html",
            "path": "page://" + hashlib.md5((best_url + parsed.title).encode("utf-8", "ignore")).hexdigest()[:12],
            "url": best_url, "size": 0, "chars": parsed.chars, "status": "done", "error": "",
        })
        chunks = chunk_blocks(parsed, int(self.cfg.get("KB_CHUNK_SIZE") or 900))
        for ch in chunks:
            ch["chapter_key"] = chapter_key
        self.store.kb_add_chunks(doc_id, chunks, course_id=course_id, chapter_key=chapter_key)
        log.info("正文入库：%s（%s 字 / %s 块）frame=%s", parsed.title[:30], parsed.chars,
                 len(chunks), best_url[:60])
        return {"status": "done", "chunks": len(chunks), "points": 0, "error": "", "doc_id": doc_id}

    # ------------------------------------------------------------ 批量构建
    def build_from_items(self, course: dict[str, Any], items: Sequence[dict[str, Any]],
                         downloader: Any = None, max_items: int = 50) -> dict[str, int]:
        """
        遍历章节文档/PPT 任务：优先用页面正文入库，其次尝试下载附件文件入库。
        downloader(item) -> Path|None：由调用方注入（通常封装 self.download）。
        """
        stat = {"scanned": 0, "ingested": 0, "skipped": 0, "failed": 0, "chunks": 0}
        for item in items:
            if item.get("kind") not in ("document", "ppt", "video", "other"):
                continue
            stat["scanned"] += 1
            if stat["scanned"] > max_items:
                log.info("资料入库达到单次上限 %s，停止", max_items)
                break
            path = None
            if downloader and item.get("kind") in ("document", "ppt"):
                try:
                    path = downloader(item)
                except Exception as exc:
                    log.debug("下载资料失败：%s", exc)
            if path:
                res = self.ingest_file(path, course_id=course.get("id"), chapter_key=item.get("chapter_key", ""),
                                       title=item.get("title", ""), url=item.get("url", ""))
            else:
                res = {"status": "skipped", "chunks": 0, "error": "无可下载文件"}
            if res.get("status") == "done":
                stat["ingested"] += 1
                stat["chunks"] += int(res.get("chunks") or 0)
            elif res.get("status") == "failed":
                stat["failed"] += 1
            else:
                stat["skipped"] += 1
        return stat

    def ingest_local_dir(self, directory: str | Path, course_id: int | None = None,
                         pattern: str = "*") -> dict[str, int]:
        """扫描本地资料目录入库（用户自己整理的笔记/PDF 也能进知识库）。"""
        d = Path(directory)
        stat = {"found": 0, "done": 0, "skip": 0, "fail": 0, "chunks": 0}
        if not d.exists():
            log.warning("目录不存在：%s", d)
            return stat
        for p in sorted(d.rglob(pattern)):
            if not p.is_file() or p.suffix.lower() not in (
                ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".txt", ".md", ".html", ".htm"
            ):
                continue
            stat["found"] += 1
            res = self.ingest_file(p, course_id=course_id, title=p.stem)
            if res.get("status") == "done":
                stat["done"] += 1
                stat["chunks"] += int(res.get("chunks") or 0)
            elif res.get("status") == "failed":
                stat["fail"] += 1
            else:
                stat["skip"] += 1
        return stat

    def rebuild_index(self) -> int:
        """重新按规则补齐缺失的关键词列（轻量维护操作）。"""
        rows = self.store.kb_all_chunks()
        n = 0
        for row in rows:
            if not (row.get("terms") or "").strip():
                from knowledge_base.extractors import top_terms

                self.store.exec("UPDATE kb_chunks SET terms=? WHERE id=?",
                                (" ".join(top_terms(row.get("text", ""), 14)), row["id"]))
                n += 1
        return n


def _frames(page: Any) -> list[Any]:
    try:
        return list(page.frames)
    except Exception:
        return []


def _page_title(page: Any) -> str:
    try:
        return one_line(page.title())
    except Exception:
        return ""


def _page_url(page: Any) -> str:
    try:
        return str(page.url)
    except Exception:
        return ""