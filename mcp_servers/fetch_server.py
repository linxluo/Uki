"""
MCP Fetch 服务器

通过 HTTP 抓取网页内容并转为纯文本。
零外部依赖，纯 Python 标准库。
"""

import sys
import json
import re
from urllib.request import urlopen, Request
from urllib.error import URLError
from html.parser import HTMLParser


class TextExtractor(HTMLParser):
    """从 HTML 中提取纯文本"""
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = False
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.skip:
            text = data.strip()
            if text:
                self.text.append(text + " ")

    def get_text(self):
        return "".join(self.text)


def fetch_url(url: str, max_length: int = 8000) -> str:
    """抓取 URL 并返回纯文本"""
    req = Request(url, headers={
        "User-Agent": "Uki-Fetch/1.0",
        "Accept": "text/html,text/plain",
    })
    with urlopen(req, timeout=15) as resp:
        content_type = resp.headers.get("Content-Type", "")

        # 只读前 1MB，防止超大文件
        body = resp.read(1_000_000)
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()

        try:
            html = body.decode(charset, errors="ignore")
        except Exception:
            html = body.decode("utf-8", errors="ignore")

        # 提取文本
        extractor = TextExtractor()
        extractor.feed(html)
        text = extractor.get_text()
        # 压缩多余空白
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)

        if len(text) > max_length:
            text = text[:max_length] + f"\n\n...（内容过长，已截断至 {max_length} 字符）"

        return f"URL: {url}\n\n{text.strip()}"


def handle_request(request: dict) -> dict:
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "uki-fetch", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "tools": [{
                    "name": "fetch_url",
                    "description": "抓取指定 URL 的网页内容，返回纯文本。适合读取文章、文档、API 响应等。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "要抓取的完整 URL（包含 https://）",
                            },
                            "max_length": {
                                "type": "number",
                                "description": "最大返回字符数，默认 8000",
                            },
                        },
                        "required": ["url"],
                    },
                }],
            },
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        if tool_name == "fetch_url":
            try:
                url = tool_args.get("url", "")
                max_len = int(tool_args.get("max_length", 8000))
                text = fetch_url(url, max_len)
            except URLError as e:
                text = f"抓取失败: {e}"
            except Exception as e:
                text = f"抓取出错: {e}"
        else:
            text = f"未知工具: {tool_name}"

        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    return {
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"未知方法: {method}"},
    }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            pass


if __name__ == "__main__":
    main()
