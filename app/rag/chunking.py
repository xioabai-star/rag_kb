"""模块 9：文本切分 —— 把整篇文档切成检索/生成的最小单位（chunk）。

策略（按文件类型）：
- Markdown / MinerU 产物：MarkdownHeaderTextSplitter 按 #/##/### 标题切，保章节结构
  → 超长块用 RecursiveCharacterTextSplitter(500/50) 兜底二次切
- 表格（MinerU 输出 <table> HTML）：按行（</tr>）整切为独立块，行内不动刀
  —— 字符切分会把单元格劈成两半（实证：差旅「，超支自理」成了块首，见 probe_chunk_map.py）
- 其他纯文本：RecursiveCharacterTextSplitter(chunk_size=500, overlap=50,
  separators 含中文标点 —— 中文没有空格，必须显式指定中文分隔符)
统一收尾：
- 标题回填（把 metadata 里的标题拼回 content 顶部，向量/LLM 都能看到章节上下文）
- 赋唯一 id：{doc_id}_chunk_{i}，并把 id/doc_id/filename/header 写入 metadata
  （metadata 是引用溯源的信息源，也是按文档删除的过滤键）
"""

import re

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# 中文分隔符（从大到小逐级尝试，"递归"缩小刀口，尽量在语义完整处断开）
_CHINESE_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]

_HEADER_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=[
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
    ],
    strip_headers=True,  # 标题剥离进 metadata，稍后由 _prepend_headers 拼回 content
)

_CHAR_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=_CHINESE_SEPARATORS,
)

# 表格区域识别（MinerU 表格输出为 <table>…</table> 的 HTML）
_TABLE_RE = re.compile(r"<table\b.*?</table>", re.DOTALL)


def _split_part(part: Document) -> list[Document]:
    """标题段内的正文切分：表格按行整切，其余走原 500/50 字符分块。

    病因（scripts/probe_chunk_map.py 实证）：字符切分器在表格单元格里的中文
    逗号处动刀（差旅「凭发票实报实销，超支自理」被劈成两半），答案碎片化、
    精排全盲 → RAGAS 上海 CP 0.5。表格的正确刀口只有行边界 </tr>。
    """
    if not _TABLE_RE.search(part.page_content):
        return _CHAR_SPLITTER.split_documents([part])

    chunks: list[Document] = []
    pos = 0
    for m in _TABLE_RE.finditer(part.page_content):
        if m.start() > pos:  # 表格前的文本 → 原逻辑
            seg = part.page_content[pos:m.start()]
            if seg.strip():
                chunks.extend(_CHAR_SPLITTER.split_documents(
                    [Document(seg, metadata=part.metadata)]))
        chunks.extend(_table_row_chunks(m.group(0), part.metadata))
        pos = m.end()
    tail = part.page_content[pos:]
    if tail.strip():  # 表格后的文本 → 原逻辑
        chunks.extend(_CHAR_SPLITTER.split_documents(
            [Document(tail, metadata=part.metadata)]))
    return chunks


def _table_row_chunks(table_html: str, metadata: dict) -> list[Document]:
    """表格按行切块；第一行视为表头，切数据行时把列名逐列拼回（"列名: 值"）。

    病因：数值行脱离表头后失去列含义（"500|400|300" 不知道属于哪个城市），
    检索和评委都读不懂 → RAGAS 上海住宿 CP 0.583。表头注入后行块自带语义。
    """
    rows = re.findall(r"<tr\b.*?</tr>", table_html, re.DOTALL)
    if not rows:
        return []

    # 第 1 步：第一行 <tr> 单独拎出来当「列名表」
    header = _row_cells(rows[0])

    chunks: list[Document] = []
    for row in rows[1:]:                      # 第 2 步：数据行从第二行开始
        if len(row) > CHUNK_SIZE * 2:         # 防御保留：畸形超大行退回字符分块
            chunks.extend(_CHAR_SPLITTER.split_documents(
                [Document(row, metadata=metadata)]))
            continue
        cells = _row_cells(row)
        if not cells:
            continue
        if header and len(cells) == len(header):
            # 第 3 步：列名和值逐列 zip 拼回；空列名（表首列通常没列名）只留值本身
            text = " | ".join(
                f"{h}: {v}" if h else v
                for h, v in zip(header, cells)
            )
        else:
            text = " | ".join(cells)          # 防御：列数对不上 → 退回裸行文本
        if text:
            chunks.append(Document(text, metadata=dict(metadata)))
    return chunks


def _row_cells(row_html: str) -> list[str]:
    """把一行 HTML 拆成单元格文本列表（空单元格保留空串，位置不能错位）。"""
    cells = re.findall(r"<t[dh]\b.*?</t[dh]>", row_html, re.DOTALL)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip(" |") for c in cells]


def split_documents(
    raw_docs: list[Document], file_type: str, doc_id: str, filename: str
) -> list[Document]:
    """按文件类型切分，返回已回填标题、已赋 id、已打 metadata 的 chunk 列表。"""
    chunks: list[Document] = []

    for doc in raw_docs:
        if file_type in (".md", ".pdf", ".docx"):
            # MinerU 与 md 的 content 均为 markdown：先按标题切，再兜底超长块
            parts = _HEADER_SPLITTER.split_text(doc.page_content)
            for part in parts:
                chunks.extend(_split_part(part))
        else:
            chunks.extend(_CHAR_SPLITTER.split_documents([doc]))

    chunks = [_prepend_headers(doc) for doc in chunks]
    _assign_ids_and_metadata(chunks, doc_id, filename)
    return chunks


def _prepend_headers(doc: Document) -> Document:
    """标题回填：把 metadata 里的各级标题按层级拼回 content 顶部。

    切分时 MarkdownHeaderTextSplitter 把标题放进了 metadata（Header 1/2/3 字段），
    这里取出来拼回 content，使每个 chunk 自带章节上下文（服务检索与引用溯源）。
    """
    headers = []
    for level in ("Header 1", "Header 2", "Header 3"):
        if level in doc.metadata:
            mark = "#" * int(level.split()[-1])
            headers.append(f"{mark} {doc.metadata[level]}")
    if headers:
        doc.page_content = "\n".join(headers + [doc.page_content])
    return doc


def _assign_ids_and_metadata(
    chunks: list[Document], doc_id: str, filename: str
) -> None:
    """赋唯一 id 并写入检索/溯源/删除所需的全部 metadata。"""
    for i, chunk in enumerate(chunks):
        chunk_id = f"{doc_id}_chunk_{i}"
        chunk.id = chunk_id
        chunk.metadata.update(
            {
                "id": chunk_id,       # Chroma 检索后从 metadata 取 id（稠密路径）
                "doc_id": doc_id,     # 按文档删除/过滤的键
                "filename": filename, # 引用溯源显示
                "chunk_index": i,
                "header": chunk.metadata.get("Header 1", "") + " " +
                          chunk.metadata.get("Header 2", ""),  # 引用溯源显示章节
            }
        )
