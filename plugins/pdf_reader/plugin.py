"""
PDF 阅读插件

提供 PDF 文档的文本提取和元信息查询能力。
依赖 pypdf（纯 Python，pip install pypdf）。

设计原则：
  - 插件只负责"把 PDF 变成纯文本"，不负责理解或总结
  - 总结由 LLM 在拿到文本后自行完成（这是 LLM 的强项）
  - 插件 = 数据提取器，LLM = 理解器
"""

from pathlib import Path
from uki.plugin_manager import UkiPlugin

# 默认截断上限：避免一个 PDF 撑爆上下文
DEFAULT_MAX_CHARS = 8000


class PDFReaderPlugin(UkiPlugin):
    """PDF 文档阅读和元信息查询。"""

    def on_load(self, agent=None):
        try:
            from pypdf import PdfReader
            self._reader = PdfReader
            print(f"  [PDF Reader] 插件 v{self.version} 就绪")
        except ImportError:
            self._reader = None
            print(f"  [PDF Reader] 插件 v{self.version} 已加载，但缺少 pypdf 库")
            print(f"    请运行: pip install pypdf")

    def get_tool_definitions(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_pdf",
                    "description": (
                        "读取 PDF 文件的文本内容（不是 read_file！PDF 文件必须用此工具）。"
                        "返回从 PDF 中提取的纯文本，可用于阅读、分析、总结。"
                        "如果 PDF 是扫描件（图片型），可能返回空或乱码。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "PDF 文件的路径（相对或绝对）",
                            },
                            "max_chars": {
                                "type": "integer",
                                "description": f"最大返回字符数，默认 {DEFAULT_MAX_CHARS}。设置更大的值可获取更多内容",
                            },
                            "start_page": {
                                "type": "integer",
                                "description": "起始页码（1-based），默认从第 1 页开始",
                            },
                            "end_page": {
                                "type": "integer",
                                "description": "结束页码（1-based，含）。例如 end_page=20 表示读到第 20 页为止",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pdf_info",
                    "description": (
                        "获取 PDF 文件的元信息：总页数、标题、作者、是否加密等。"
                        "用于在读取之前先了解文档概况。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "PDF 文件的路径",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
        ]

    def execute_tool(self, name: str, arguments: dict) -> str | None:
        if name == "read_pdf":
            return self._read_pdf(
                arguments["path"],
                arguments.get("max_chars", DEFAULT_MAX_CHARS),
                arguments.get("start_page", 1),
                arguments.get("end_page", 0),
            )
        if name == "pdf_info":
            return self._pdf_info(arguments["path"])
        return None

    def get_commands(self) -> list:
        return [
            ("pdf", "查看 PDF 文件的基本信息", self._cmd_pdf),
        ]

    # ================================================================
    # 工具实现
    # ================================================================

    def _get_reader(self):
        if self._reader is None:
            try:
                from pypdf import PdfReader
                self._reader = PdfReader
            except ImportError:
                pass
        return self._reader

    def _read_pdf(self, path: str, max_chars: int, start_page: int, end_page: int = 0) -> str:
        abs_path = Path(path).resolve()
        if not abs_path.exists():
            return f"文件不存在: {path}"
        if abs_path.suffix.lower() != ".pdf":
            return f"不是 PDF 文件: {path}"

        PdfReader = self._get_reader()
        if PdfReader is None:
            return "错误：缺少 pypdf 库。请运行 pip install pypdf 后重试。"

        try:
            reader = PdfReader(str(abs_path))
        except Exception as e:
            return f"无法打开 PDF: {e}"

        if reader.is_encrypted:
            return "此 PDF 文件已加密，无法读取。请先解密后再试。"

        total_pages = len(reader.pages)

        if start_page < 1:
            start_page = 1
        if end_page <= 0 or end_page > total_pages:
            end_page = total_pages
        if start_page > total_pages:
            return f"起始页码 {start_page} 超出范围（共 {total_pages} 页）"

        parts = []
        total_chars = 0
        truncated = False

        for i in range(start_page - 1, end_page):
            try:
                page = reader.pages[i]
                text = page.extract_text() or ""
            except Exception as e:
                text = f"[第 {i+1} 页提取失败: {e}]"

            if not text.strip():
                parts.append(f"\n--- 第 {i+1} 页（无文本，可能是扫描件）---\n")
                continue

            parts.append(f"\n--- 第 {i+1} 页 ---\n{text}")
            total_chars += len(text)
            if total_chars >= max_chars:
                truncated = True
                break

        content = "".join(parts).strip()
        if end_page < total_pages:
            header = f"PDF: {abs_path.name}（共 {total_pages} 页，第 {start_page}-{end_page} 页）\n"
        else:
            header = f"PDF: {abs_path.name}（共 {total_pages} 页，从第 {start_page} 页开始）\n"

        if truncated:
            last_page = i + 1
            header += f"（已截断至前 {last_page - start_page + 1} 页，约 {total_chars} 字符。"
            header += f"如需更多内容，请用 start_page 参数指定起始页）\n"

        if not content:
            header += "\n未提取到任何文本。该 PDF 可能是纯图片扫描件，需要 OCR 处理。"

        return header + "\n" + content

    def _pdf_info(self, path: str) -> str:
        abs_path = Path(path).resolve()
        if not abs_path.exists():
            return f"文件不存在: {path}"
        if abs_path.suffix.lower() != ".pdf":
            return f"不是 PDF 文件: {path}"

        PdfReader = self._get_reader()
        if PdfReader is None:
            return "错误：缺少 pypdf 库。请运行 pip install pypdf 后重试。"

        try:
            reader = PdfReader(str(abs_path))
        except Exception as e:
            return f"无法打开 PDF: {e}"

        info = []
        info.append(f"文件: {abs_path.name}")
        info.append(f"大小: {abs_path.stat().st_size / 1024:.1f} KB")
        info.append(f"页数: {len(reader.pages)}")
        info.append(f"加密: {'是' if reader.is_encrypted else '否'}")

        meta = reader.metadata
        if meta:
            if meta.title:
                info.append(f"标题: {meta.title}")
            if meta.author:
                info.append(f"作者: {meta.author}")
            if meta.subject:
                info.append(f"主题: {meta.subject}")
            if meta.creator:
                info.append(f"创建工具: {meta.creator}")

        return "\n".join(info)

    def _cmd_pdf(self, args: str) -> str:
        path = args.strip()
        if not path:
            return "用法: /pdf <文件路径>\n示例: /pdf report.pdf"
        return self._pdf_info(path)
