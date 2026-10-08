#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HP 驱动下载脚本（通用交互版）
=============================

支持 HP 全部产品类型（笔记本 / 台式机 / 打印机 / Poly / 其他），
既可以通过终端交互一步步引导用户操作，也可以完全用命令行参数驱动（自动化）。

注意（Poly）：Poly 产品可以被选择与检索，但 HP 的 osVersionData 接口对这类产品
返回空的 osversions 列表，因此脚本拿不到 OS 维度驱动数据，会明确提示并以退出码 2 结束。
Poly 相关软件请从 HP Poly 软件下载页获取。

关于确认与 --yes：
    未指定 --yes 时，无论交互模式还是非交互模式（--model / --url），
    打印清单后都会询问「是否开始下载？」（默认「否」）。
    非交互模式下若 stdin 已结束（EOF，如自动化管道），按默认「否」安全退出（退出码 0）
    并提示加 --yes；指定 --yes 则跳过确认直接下载，保持自动化能力。
    --dry-run 也会请求确认（同样默认「否」），但无论确认与否都只输出清单、不下载文件。

交互流程（不带 --url / --model 时）：
    1. 选择产品类型（类型列表来自 HP 接口）
    2. 输入产品型号（如 492J0PA、W1A53A）
    3. 调用接口搜索；无论是否找到都会明确告知用户
    4. 找到后显示产品名称 / 型号 / OID，请用户确认
    5. 选择操作系统平台与系统版本（支持「全部」）
    6. 显示下载清单（文件数、总大小、目录结构），确认后下载
    7. 下载到本地（已存在的文件会跳过）

用法示例：
    # 交互模式
    python3 hp_driver_download.py

    # 非交互：按型号下载（默认所有系统）
    python3 hp_driver_download.py --model 492J0PA --type laptop

    # 非交互：只输出清单，不下载
    python3 hp_driver_download.py --model W1A53A --type printer --dry-run

    # 非交互：兼容旧用法，直接从 HP 驱动页面 URL 解析 Model OID
    python3 hp_driver_download.py --url "https://support.hp.com/cn-zh/drivers/omen-16.1-inch-gaming-laptop-pc-16-b0000/model/2100371527?sku=492J0PA"

    # 只下载指定系统
    python3 hp_driver_download.py --model 492J0PA --os "Windows 11 版本 22H2"

目录组织规则（按去重后的包归属决定）：
    downloads/
      public/        Windows 10 和 Windows 11（或任意 2 个及以上平台）共同出现的包
      windows-10/    仅 Windows 10 出现的包
      windows-11/    仅 Windows 11 出现的包
      <其他平台>/    其他平台独有包（目录名由平台名规范化，如 macos / linux）

    去重键为 softwareItemId；同一包在 2 个及以上平台出现 → public/。
    同一 URL 被 HP 分配了多个 itemId 时按 URL 合并为一条，避免重复下载。
    注意：public/ 的判定基于「产品全部平台」的重叠情况——若用户只选了部分
    系统，脚本会额外扫描其余系统用于比对（这些系统的驱动不会下载），
    这样目录结构不会随选择范围而变化。

自动跳过的条目（会在清单中单独列出，说明原因）：
    - 应用商店链接（isAppStore / apps.microsoft.com 等，指向网页而非文件）
    - Windows Update ID（如 ish_4511134-4630612-16，非 HTTP 直链）
    - mediaType 为 Reference 的参考条目

关于驱动版本（有意的取舍）：
    接口中每个驱动条目都同时带 latestVersionDriver 与 previousVersionOfDriversList，
    本脚本**只下载最新版本**（latestVersionDriver），忽略历史版本。
    对绝大多数用户而言最新版即为所需；如需历史版本请自行扩展。

关于 --type：
    该参数只影响 driverDetails 的 template 参数（HP 接口对 template 不敏感，
    因此下载清单不会因 --type 不同而变化）。未指定时会尝试从产品 URL 自动推断，
    推断不出则使用默认 template；清单中会显示「产品类型：自动」。

退出码：
    0   = 成功（含用户主动取消：交互流程的类型菜单 / 型号 / 产品确认 / 平台 /
          系统 / 最终下载确认处取消，以及非交互模式在下载确认处选择「否」或
          stdin 为 EOF 时按默认「否」安全退出）
    1   = 出错（网络 / 接口 / 下载失败 / 输出目录不可用）
    2   = 未找到型号、无法从输入解析产品信息，或没有可用驱动
          （Poly 产品无 OS 维度驱动数据也归入此类）
    130 = 被 Ctrl-C（SIGINT）中断
    注意：argparse 的参数错误固定使用退出码 2（argparse 自身行为），
          与「未找到型号」同码，自动化时请结合 stderr 内容区分。

依赖：仅 Python 3 标准库（urllib / json / argparse / pathlib 等）。
"""

import argparse
import html
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ============================================================
# 常量配置
# ============================================================

BASE_URL = "https://support.hp.com"
API_BASE = f"{BASE_URL}/wcc-services"

# 浏览器 UA：HP 接口要求带浏览器特征的头，否则返回 403
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 产品类型 → driverDetails 接口的 template 参数
# （实测 template 不敏感，但按类型传正确值更符合 HP 前端行为）
# 注意：HP 接口中 Poly 的类型 key 是 "headset"（链接为 /drivers/poly），
#       "poly" 作为别名保留，便于命令行使用。
TEMPLATE_MAP = {
    "laptop": "SWD-LaptopLanding",
    "desktop": "SWD-DesktopLanding",
    "printer": "SWD-PrinterLanding",
    "headset": "SWD-PolyLanding",
    "poly": "SWD-PolyLanding",
    "other": "SWD-OtherLanding",
}
DEFAULT_TEMPLATE = "SWD-LaptopLanding"

# 产品类型的中文兜底名称（接口取不到类型列表时使用，顺序与 HP 页面一致）
FALLBACK_TYPES = [
    {"type": "printer", "linkText": "打印机"},
    {"type": "laptop", "linkText": "笔记本电脑"},
    {"type": "desktop", "linkText": "台式机"},
    {"type": "headset", "linkText": "Poly"},
    {"type": "other", "linkText": "其他"},
]

# 产品类型 key → 中文显示名
TYPE_DISPLAY = {item["type"]: item["linkText"] for item in FALLBACK_TYPES}
TYPE_DISPLAY["poly"] = "Poly"

# URL 中推断产品类型的关键字（--url 模式下无法从接口得知类型）
URL_TYPE_KEYWORDS = [
    ("printer", ("printer", "printers", "scanner")),
    ("laptop", ("laptop", "laptops", "notebook")),
    ("desktop", ("desktop", "desktops", "tower")),
    ("headset", ("poly", "polycom")),
    ("other", ("products", "other")),
]

PUBLIC_DIR = "public"          # 跨平台共享包目录名
DEFAULT_OUTPUT_DIR = "downloads"

CHUNK_SIZE = 64 * 1024         # 下载分块大小 64KB
SIZE_TOLERANCE = 0.05          # 文件大小容差 ±5%（接口 fileSize 是取整值）
HTTP_TIMEOUT = 60              # 单次 HTTP 请求超时（秒）
MAX_RETRIES = 3                # 请求/下载失败重试次数
RETRY_BACKOFF = 1.5            # 重试退避基数（秒）

SEPARATOR = "=" * 70

# 进程退出码（统一语义，供 main() 与 --help 使用）
EXIT_SUCCESS = 0        # 成功，或用户在交互流程中主动取消
EXIT_ERROR = 1          # 网络 / 接口 / 下载失败 / 输出目录不可用
EXIT_NOT_FOUND = 2      # 未找到型号、无法解析产品信息、没有可用驱动
EXIT_CANCELLED = 0      # 用户主动取消：视为正常结束，与 EXIT_SUCCESS 同码
EXIT_INTERRUPTED = 130  # 被 Ctrl-C（SIGINT）中断

# 产品解析结果状态（用于区分「用户取消」与「未找到型号」）
RESOLVE_OK = "ok"
RESOLVE_CANCELLED = "cancelled"
RESOLVE_NOT_FOUND = "not_found"
RESOLVE_ERROR = "error"


class HPError(Exception):
    """脚本内部统一异常：网络错误、接口错误、参数错误等。"""


# ============================================================
# 通用工具函数
# ============================================================

def clean_text(value):
    """清理接口返回的文本：去掉 BOM、零宽字符、HTML 实体与首尾空白。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    text = value.replace("\ufeff", "").replace("\u200b", "")
    return html.unescape(text).strip()


_SIZE_RE = re.compile(r"([\d.,]+)\s*([KMGTP]?B)\b", re.IGNORECASE)
# HP 接口的 fileSize 使用 1024 进制（实测 "21.1 MB" 对应 22060656 字节 = 21.04 MiB）
_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3,
               "TB": 1024 ** 4, "PB": 1024 ** 5}
_SIZE_EMPTY = {"", "-", "--", "n/a", "na", "null", "none", "unknown", "未知"}


def parse_size_to_bytes(text):
    """把 "929.5 MB" 这类大小字符串解析为字节数；无法解析时返回 None。"""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    raw = clean_text(text)
    if raw.lower() in _SIZE_EMPTY:
        return None
    match = _SIZE_RE.search(raw)
    if match:
        number = match.group(1).replace(",", "")
        try:
            value = float(number)
        except ValueError:
            return None
        return int(value * _SIZE_UNITS[match.group(2).upper()])
    # 没有单位时按纯字节数处理
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def format_size(num_bytes):
    """把字节数格式化为易读字符串（1024 进制）。"""
    if not num_bytes:
        return "0 B"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def platform_dir_name(platform_name):
    """平台名 → 目录名：只用大版本，如 "Windows 10" → "windows-10"。

    会去掉括号内容（中英文括号）和「版本 xx」这类小版本描述，
    因此 "Windows 11 版本 22H2（64 位）" 也会得到 "windows-11"。
    """
    name = clean_text(platform_name)
    name = re.sub(r"[（(][^）)]*[）)]", " ", name)      # 去掉括号内容
    name = re.sub(r"版本\s*[\w.]+", " ", name)          # 去掉「版本 22H2」
    name = re.sub(r"\bversion\s*[\w.]+", " ", name, flags=re.IGNORECASE)
    name = name.lower()
    name = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", name)
    return name.strip("-") or "unknown"


def sanitize_filename(name, max_length=180):
    """把接口返回的文件名清洗为安全的本地文件名。"""
    name = clean_text(name)
    name = name.replace("\\", "/").split("/")[-1]        # 去掉路径部分
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)      # Windows 非法字符
    name = name.strip(" .")                              # 结尾的点/空格在 Windows 非法
    if not name:
        name = "driver.bin"
    if len(name) > max_length:
        stem, ext = os.path.splitext(name)
        name = stem[: max_length - len(ext)] + ext
    return name


def is_http_url(url):
    """判断是否为可下载的 HTTP(S) 直链（HP 部分 fileUrl 是 Windows Update ID）。"""
    return bool(url) and isinstance(url, str) and url.strip().lower().startswith(
        ("http://", "https://"))


# 应用商店链接的主机名（这些 fileUrl 指向网页而非驱动文件，需跳过）
APP_STORE_HOSTS = (
    "apps.microsoft.com",
    "www.microsoft.com/store",
    "play.google.com",
    "itunes.apple.com",
    "apps.apple.com",
)


def is_app_store_url(url):
    """判断 URL 是否为应用商店页面（而非可直接下载的文件）。"""
    if not is_http_url(url):
        return False
    lowered = url.strip().lower()
    return any(host in lowered for host in APP_STORE_HOSTS)


def normalize_url_key(url):
    """生成用于「同一文件」判断的 URL 归一化键（忽略协议与主机大小写）。"""
    raw = clean_text(url)
    if not raw:
        return ""
    parts = urllib.parse.urlsplit(raw)
    if not parts.scheme:
        return raw.lower()
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.netloc.lower()}{parts.path}{query}"


def size_tolerance(expected_bytes, size_text=None):
    """计算允许的文件大小误差（字节）。

    取以下两者的较大值：
      1. 期望大小的 ±5%（SIZE_TOLERANCE）；
      2. 由接口显示精度推算的取整误差 —— HP 的 fileSize 是「向上取整（ceil）
         到 1 位小数」的 1024 进制值（如 "0.8 MB" 实际可能是 743 KB；
         实测 77/77 个文件满足 ceil 语义），因此真实误差上限是**一整个步进**
         （10^-小数位 × 单位），而不是半步进。小文件的取整误差可能远超 5%，
         若按半步进估算，会导致这类文件每次运行都被判定为「大小不符」而重复下载。
    """
    tolerance = int(expected_bytes * SIZE_TOLERANCE)
    if size_text:
        match = _SIZE_RE.search(clean_text(size_text))
        if match:
            number = match.group(1).replace(",", "")
            decimals = len(number.split(".")[1]) if "." in number else 0
            unit = _SIZE_UNITS.get(match.group(2).upper(), 1)
            # ceil 语义：实际值与标注值最多相差一个完整步进
            rounding = (10 ** -decimals) * unit
            tolerance = max(tolerance, int(round(rounding)) + 1024)
    return max(tolerance, 1024)


def platform_tag(platforms):
    """把平台集合压缩成短标签，如 win10+win11，用于清单展示。"""
    if not platforms:
        return "-"
    names = sorted(platform_dir_name(p) for p in platforms)
    return "+".join(n.replace("windows-", "win") for n in names)


_MODEL_OID_PATTERNS = (
    r"/model/(\d+)",
    r"[?&]productOid=(\d+)",
    r"[?&]modelOid=(\d+)",
    r"[?&]productNameOid=(\d+)",
)


def extract_model_oid(text):
    """从 HP 驱动页面 URL（或纯数字）中解析 Model OID（productNameOID）。"""
    if not text:
        return None
    raw = clean_text(text)
    if raw.isdigit():
        return raw
    for pattern in _MODEL_OID_PATTERNS:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def infer_type_from_url(url):
    """从驱动页面 URL 推断产品类型；推断不出时返回 None。"""
    lowered = clean_text(url).lower()
    for type_key, keywords in URL_TYPE_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return type_key
    return None


def type_display_name(type_key):
    """产品类型 key → 中文显示名；未指定（None / 空）时返回「自动」。"""
    if not type_key:
        return "自动"
    return TYPE_DISPLAY.get(type_key, type_key)


# ============================================================
# HP 接口客户端
# ============================================================

class HPClient:
    """support.hp.com 接口客户端（标准库实现，带 Cookie 会话）。

    所有请求统一附加浏览器 UA / Referer / wPlatform 等头；
    首次使用前需调用 init() 建立会话（获取 wcc_s_flag / wcc_s_ref cookie）。
    """

    def __init__(self, cc="cn", lc="zh", timeout=HTTP_TIMEOUT, verbose=False):
        """初始化客户端：配置地区/语言、超时与详细日志，并建立 Cookie 会话容器。

        参数：
            cc      ：国家/地区代码（默认 "cn"）
            lc      ：语言代码（默认 "zh"）
            timeout ：单次 HTTP 请求超时秒数（默认 HTTP_TIMEOUT）
            verbose ：为 True 时打印重试退避等调试信息

        注意：这里只准备 CookieJar / opener，真正建立会话需调用 init()。
        """
        self.cc = cc
        self.lc = lc
        self.timeout = timeout
        self.verbose = verbose
        self._cookie_jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookie_jar)
        )
        self._initialized = False

    # ---------- 底层请求 ----------

    def _build_headers(self, extra=None):
        """构造请求头（HP 接口缺少 UA/Referer 会 403）。"""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{BASE_URL}/{self.cc}-{self.lc}/",
            "wPlatform": "Windows",
            "wPlatformVersion": "10.0",
            "wBitness": "64",
        }
        if extra:
            headers.update(extra)
        return headers

    def request(self, url, data=None, method=None, extra_headers=None,
                retries=MAX_RETRIES):
        """发送 HTTP 请求并返回响应体 bytes，失败自动重试。"""
        if url.startswith("/"):
            url = BASE_URL + url
        body = None
        headers = self._build_headers(extra_headers)
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        last_error = None
        for attempt in range(1, retries + 1):
            request = urllib.request.Request(url, data=body, headers=headers,
                                             method=method)
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                last_error = HPError(f"HTTP {exc.code} {exc.reason}（{url}）")
                # 4xx 属于请求本身的问题，重试没有意义
                if 400 <= exc.code < 500 and exc.code != 429:
                    break
            except urllib.error.URLError as exc:
                last_error = HPError(f"网络错误：{exc.reason}（{url}）")
            except (TimeoutError, OSError) as exc:
                last_error = HPError(f"网络异常：{exc}（{url}）")
            if attempt < retries:
                wait = RETRY_BACKOFF * attempt
                if self.verbose:
                    print(f"    [重试 {attempt}/{retries - 1}] {last_error}，{wait:.1f}s 后重试")
                time.sleep(wait)
        raise last_error or HPError(f"请求失败：{url}")

    def get_json(self, url, extra_headers=None):
        """GET 并解析 JSON。"""
        raw = self.request(url, extra_headers=extra_headers)
        return self._parse_json(raw, url)

    def post_json(self, url, payload):
        """POST JSON 并解析 JSON。"""
        raw = self.request(url, data=payload)
        return self._parse_json(raw, url)

    @staticmethod
    def _parse_json(raw, url):
        """解析响应体为 JSON，失败时抛出带上下文的异常。"""
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError) as exc:
            snippet = raw[:200].decode("utf-8", errors="replace")
            raise HPError(f"接口返回的不是合法 JSON（{url}）：{exc}；内容片段：{snippet}")

    # ---------- 业务接口 ----------

    def init(self):
        """步骤 1：初始化会话（建立 cookie）。"""
        self.get_json(f"{API_BASE}/s/init?cc={self.cc}&lc={self.lc}")
        self._initialized = True
        return True

    def ensure_init(self):
        """确保会话已初始化（每个业务接口调用前兜底）。"""
        if not self._initialized:
            self.init()

    def product_types(self):
        """步骤 2：获取产品类型列表，返回 [{type, linkText, order}]（按 order 排序）。"""
        self.ensure_init()
        data = self.get_json(
            f"{API_BASE}/cms-v2/{self.cc}-{self.lc}/wcc_swd_landing_page")
        items = data.get("data") or []
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            type_key = clean_text(item.get("type"))
            if not type_key:
                continue
            result.append({
                "type": type_key,
                "linkText": clean_text(item.get("linkText")) or type_key,
                "order": clean_text(item.get("order")),
            })
        try:
            result.sort(key=lambda x: int(x["order"] or 999))
        except (TypeError, ValueError):
            pass
        return result

    def search_products(self, query):
        """步骤 3：按型号搜索产品，返回去重后的产品列表。

        关键路径：data.kaaSResponse.data.searchResults.categories[]
                  .subCategoryList[].productList[]
        未找到时 searchResults 为 null，此时返回空列表。
        """
        self.ensure_init()
        url = (f"{API_BASE}/searchresult/{self.cc}-{self.lc}"
               f"?q={urllib.parse.quote(clean_text(query))}"
               f"&context=PfinderContact&navigation=false")
        data = self.get_json(url)
        kaaS = ((data.get("data") or {}).get("kaaSResponse") or {})
        search_results = ((kaaS.get("data") or {}).get("searchResults") or {})
        categories = search_results.get("categories") or []

        products, seen = [], set()
        for category in categories:
            for sub_category in (category.get("subCategoryList") or []):
                for product in (sub_category.get("productList") or []):
                    oid = clean_text(product.get("productNameOID"))
                    if not oid or oid in seen:
                        continue
                    seen.add(oid)
                    products.append({
                        "productName": clean_text(product.get("productName")),
                        "SEOFriendlyName": clean_text(product.get("SEOFriendlyName")),
                        "productNameOID": oid,
                        "productSeriesOID": clean_text(product.get("productSeriesOID")),
                        "productNumberOID": clean_text(product.get("productNumberOID")),
                        "targetUrl": clean_text(product.get("targetUrl")),
                        "historicalWebSupportFlag": bool(
                            product.get("historicalWebSupportFlag")),
                    })
        return products

    def os_versions(self, model_oid):
        """步骤 4：获取操作系统版本列表。

        返回 [{"platform": 平台名, "systems": [{"id", "name", "bit"}]}]
        注意：接口的 osTMSId 通常为 null，真正的系统 ID 在 id 字段。
        """
        self.ensure_init()
        url = (f"{API_BASE}/swd-v2/osVersionData"
               f"?cc={self.cc}&lc={self.lc}&productOid={urllib.parse.quote(str(model_oid))}")
        data = self.get_json(url)
        platforms = []
        for platform in ((data.get("data") or {}).get("osversions") or []):
            platform_name = clean_text(platform.get("name"))
            systems = []
            for system in (platform.get("osVersionList") or []):
                system_id = clean_text(system.get("osTMSId")) or clean_text(system.get("id"))
                if not system_id:
                    continue
                systems.append({
                    "id": system_id,
                    "name": clean_text(system.get("name")) or platform_name,
                    "bit": clean_text(system.get("osBitVersion")),
                })
            if systems:
                platforms.append({"platform": platform_name, "systems": systems})
        return platforms

    def driver_details(self, model_oid, system_id, template):
        """步骤 5：获取某个系统下的驱动列表。

        返回规范化后的驱动条目列表（含 item_id / title / url / size_bytes 等）。
        """
        self.ensure_init()
        payload = {
            "productLineCode": "",
            "lc": self.lc,
            "cc": self.cc,
            "osTMSId": str(system_id),
            "osName": "Windows",
            "productSeriesOid": 0,
            "platformId": str(system_id),
            "productNameOid": int(model_oid) if str(model_oid).isdigit() else model_oid,
        }
        url = (f"{API_BASE}/swd-v2/driverDetails"
               f"?authState=anonymous&template={template}")
        data = self.post_json(url, payload)

        drivers = []
        for software_type in ((data.get("data") or {}).get("softwareTypes") or []):
            category = clean_text(software_type.get("accordionName")
                                  or software_type.get("tmsName"))
            for wrapper in (software_type.get("softwareDriversList") or []):
                latest = wrapper.get("latestVersionDriver") or {}
                detail = latest.get("detailInformation") or {}
                file_url = clean_text(latest.get("fileUrl"))
                item_id = clean_text(latest.get("softwareItemId"))
                size_text = clean_text(latest.get("fileSize"))
                release = clean_text(latest.get("releaseDateString")
                                     or latest.get("releaseDate"))
                drivers.append({
                    "item_id": item_id,
                    "item_ids": [item_id] if item_id else [],
                    "title": clean_text(latest.get("title")),
                    "version": clean_text(latest.get("version")),
                    "url": file_url,
                    "size_text": size_text,
                    "size_bytes": parse_size_to_bytes(size_text),
                    "release_date": release[:10],
                    "file_name": driver_filename(item_id, file_url,
                                                 detail.get("fileName")),
                    "category": category,
                    # isAppStore / mediaType 用于识别「非直接下载」条目
                    "is_app_store": bool(latest.get("isAppStore")),
                    "media_type": clean_text(latest.get("mediaType")),
                })
        return drivers


def driver_filename(item_id, file_url, api_file_name=None):
    """确定保存用的文件名：优先 detailInformation.fileName，其次 URL 末段，最后用 itemId。"""
    name = clean_text(api_file_name)
    if not name and is_http_url(file_url):
        path = urllib.parse.urlsplit(file_url).path
        name = urllib.parse.unquote(os.path.basename(path))
    if not name:
        name = f"{clean_text(item_id) or 'driver'}.bin"
    return sanitize_filename(name)


# ============================================================
# 终端交互辅助函数（全部对 EOF 安全：管道输入结束不会抛栈）
# ============================================================

def read_input(prompt):
    """读取一行输入；遇到 EOF（管道结束）返回 None。"""
    try:
        return input(prompt)
    except EOFError:
        return None


def ask_text(prompt, default=None, allow_quit=True):
    """询问文本输入；返回字符串，EOF 或用户放弃时返回 None。"""
    while True:
        raw = read_input(prompt)
        if raw is None:
            print("\n（输入结束）")
            return None
        raw = raw.strip()
        if not raw:
            if default is not None:
                return default
            print("  输入不能为空，请重新输入。")
            continue
        if allow_quit and raw.lower() in ("q", "quit", "exit", "退出"):
            return None
        return raw


def ask_choice(title, labels):
    """单选菜单。返回选中项索引（从 0 开始）；EOF / 放弃返回 None。"""
    print(f"\n{title}")
    for index, label in enumerate(labels, 1):
        print(f"  {index:>2}. {label}")
    while True:
        raw = read_input("请输入编号: ")
        if raw is None:
            print("（输入结束，已取消）")
            return None
        raw = raw.strip()
        if raw.lower() in ("q", "quit", "exit", "退出"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(labels):
            return int(raw) - 1
        print("  无效的编号，请重新输入。")


def ask_multi_choice(title, labels, all_label="全部", default_indices=None):
    """多选菜单：支持 "1,3" 形式，0 表示全部。返回索引列表；EOF 默认全部。

    default_indices 不为 None 时，直接回车 / EOF 采用该预选项
    （用于交互模式下按 --os 预选平台与系统）；为 None 时沿用「回车 = 全部」。
    """
    if default_indices is None:
        default_indices = list(range(len(labels)))
    default_indices = sorted({i for i in default_indices if 0 <= i < len(labels)})
    if default_indices == list(range(len(labels))):
        default_label = all_label
    elif default_indices:
        default_label = "、".join(str(i + 1) for i in default_indices)
    else:
        default_label = "无"
    print(f"\n{title}")
    for index, label in enumerate(labels, 1):
        print(f"  {index:>2}. {label}")
    print(f"   0. {all_label}")
    while True:
        raw = read_input(f"请输入编号（可多选，如 1,3；0 = {all_label}；"
                         f"回车 = {default_label}；q = 退出）: ")
        if raw is None:
            print(f"（输入结束，默认选择 {default_label}）")
            return list(default_indices)
        raw = raw.strip()
        if raw.lower() in ("q", "quit", "exit", "退出"):
            return None
        if raw == "":
            return list(default_indices)
        if raw == "0":
            return list(range(len(labels)))
        picked, invalid = [], False
        for part in re.split(r"[,，、\s]+", raw):
            if not part:
                continue
            if part.isdigit() and 1 <= int(part) <= len(labels):
                index = int(part) - 1
                if index not in picked:
                    picked.append(index)
            else:
                invalid = True
        if picked and not invalid:
            return sorted(picked)
        print("  无效输入，请重新输入。")


def ask_confirm_ex(prompt, default=False):
    """询问是/否；返回 (是否确认, 是否遇到 EOF)。

    EOF（如自动化管道里 stdin 已结束）按 default 处理——调用方据此实现
    「安全失败」并给出可操作的提示。default 默认为 False（不确认）。
    """
    suffix = "[Y/n]" if default else "[y/N]"
    raw = read_input(f"{prompt} {suffix}: ")
    if raw is None:
        print("（输入结束，按默认处理）")
        return default, True
    raw = raw.strip().lower()
    if not raw:
        return default, False
    return raw in ("y", "yes", "是", "1", "true", "ok"), False


def ask_confirm(prompt, default=False):
    """询问是/否；EOF 时按 default 处理（默认值用于「安全失败」）。"""
    confirmed, _ = ask_confirm_ex(prompt, default)
    return confirmed


# ============================================================
# 产品与系统选择
# ============================================================

def print_header():
    """打印程序头部信息。"""
    print(SEPARATOR)
    print(" HP 驱动下载工具（通用交互版）")
    print(f" 数据来源：{BASE_URL}   地区/语言：cn-zh")
    print(" 支持产品：笔记本 / 台式机 / 打印机 / Poly / 其他")
    print(SEPARATOR)


def print_product(product):
    """显示产品信息（型号 / 名称 / OID 等）。"""
    print("\n找到产品：")
    print(f"   型号        : {product.get('productName') or '(未知)'}")
    print(f"   产品名称    : {product.get('SEOFriendlyName') or '(未知)'}")
    print(f"   Model OID   : {product.get('productNameOID') or '(未知)'}")
    if product.get("productSeriesOID"):
        print(f"   产品系列 OID: {product['productSeriesOID']}")
    if product.get("productNumberOID"):
        print(f"   产品编号 OID: {product['productNumberOID']}")
    if product.get("targetUrl"):
        print(f"   产品页面    : {BASE_URL}{product['targetUrl']}")
    if product.get("historicalWebSupportFlag"):
        print("   提示        : 该产品已被标记为历史产品，驱动可能不再更新。")


def choose_product_type(client):
    """交互步骤 1：让用户选择产品类型，返回类型 key（放弃返回 None）。"""
    print("\n正在获取产品类型列表 ...")
    try:
        types = client.product_types()
    except HPError as exc:
        print(f"  获取失败（{exc}），改用内置默认类型列表。")
        types = []
    if not types:
        types = FALLBACK_TYPES
    labels = [f"{item['linkText']}（{item['type']}）" for item in types]
    index = ask_choice("请选择产品类型：", labels)
    if index is None:
        return None
    return types[index]["type"]


def interactive_resolve_product(client, args, assume_yes=False):
    """交互模式：选择类型 → 输入型号 → 搜索 → 确认。

    返回 (product, type_key, status)，status 取 RESOLVE_* 常量：
      - RESOLVE_OK        ：成功解析出产品
      - RESOLVE_CANCELLED ：用户在任意提示处主动取消（q / EOF），退出码 0
      - RESOLVE_NOT_FOUND ：型号不存在且用户放弃重试，退出码 2
      - RESOLVE_ERROR     ：搜索过程中发生网络/接口错误且用户放弃重试，退出码 1

    assume_yes（--yes）为 True 时，所有**确认提示**都按「是」自动通过，
    但类型 / 型号 / 系统等**必要输入**不会被跳过。
    """
    def confirm(prompt, default=True):
        """确认提示；--yes 时自动按「是」处理。"""
        if assume_yes:
            print(f"{prompt} [{'Y/n' if default else 'y/N'}] → 已由 --yes 自动确认")
            return True
        return ask_confirm(prompt, default)

    type_key = args.type
    if type_key:
        print(f"\n已通过 --type 指定产品类型：{type_key}")
    else:
        type_key = choose_product_type(client)
        if type_key is None:
            print("已取消。")
            return None, None, RESOLVE_CANCELLED

    while True:
        model = ask_text("\n请输入产品型号（如 492J0PA / W1A53A；输入 q 退出）: ")
        if model is None:
            print("已取消。")
            return None, None, RESOLVE_CANCELLED
        print(f"\n正在搜索型号 {model} ...")
        try:
            results = client.search_products(model)
        except HPError as exc:
            print(f"  搜索失败：{exc}")
            if not confirm("是否重新输入型号？", default=True):
                return None, None, RESOLVE_ERROR
            continue

        # 无论有没有结果都要明确告知用户
        if not results:
            print(f"✗ 未找到该型号：{model}")
            print("  请确认型号是否正确（可在 https://support.hp.com/cn-zh/drivers 上查询）。")
            if not confirm("是否重新输入型号？", default=True):
                print("未找到可用型号，已退出。")
                return None, None, RESOLVE_NOT_FOUND
            continue

        if len(results) > 1:
            labels = [
                f"{p['productName']}  |  {p['SEOFriendlyName']}  |  OID {p['productNameOID']}"
                for p in results
            ]
            index = ask_choice(f"找到 {len(results)} 个匹配产品，请选择：", labels)
            if index is None:
                print("已取消。")
                return None, None, RESOLVE_CANCELLED
            product = results[index]
        else:
            product = results[0]

        print_product(product)
        if confirm("是否使用该产品继续？", default=True):
            return product, type_key, RESOLVE_OK
        if not confirm("是否重新输入型号？", default=True):
            print("已取消。")
            return None, None, RESOLVE_CANCELLED


def noninteractive_resolve_product(client, args):
    """非交互模式：从 --url 或 --model 解析产品，返回 (product, type_key, status)。"""
    type_key = args.type

    if args.url:
        model_oid = extract_model_oid(args.url)
        if not model_oid:
            print(f"✗ 无法从 URL 中解析 Model OID：{args.url}")
            print("  支持的 URL 形式：.../model/2100371527?sku=492J0PA 或 ...?productOid=2100371527")
            return None, type_key, RESOLVE_NOT_FOUND
        if not type_key:
            type_key = infer_type_from_url(args.url)
        print(f"从 URL 解析到 Model OID：{model_oid}"
              f"（产品类型：{type_display_name(type_key)}）")
        seo = urllib.parse.urlsplit(args.url).path.strip("/").split("/")
        product = {
            "productName": f"OID {model_oid}",
            "SEOFriendlyName": seo[2] if len(seo) > 2 else "",
            "productNameOID": model_oid,
            "productSeriesOID": "",
            "productNumberOID": "",
            "targetUrl": "",
            "historicalWebSupportFlag": False,
        }
        print_product(product)
        return product, type_key, RESOLVE_OK

    # --model 模式
    print(f"\n正在搜索型号 {args.model} ...")
    results = client.search_products(args.model)
    if not results:
        print(f"✗ 未找到该型号：{args.model}")
        print("  请确认型号是否正确（可在 https://support.hp.com/cn-zh/drivers 上查询）。")
        return None, type_key, RESOLVE_NOT_FOUND
    if len(results) > 1:
        print(f"找到 {len(results)} 个匹配产品，非交互模式默认使用第一个"
              f"（如需选择请去掉 --model 使用交互模式）：")
        for index, item in enumerate(results, 1):
            print(f"   {index}. {item['productName']} | {item['SEOFriendlyName']}"
                  f" | OID {item['productNameOID']}")
    product = results[0]
    if not type_key:
        # 未指定 --type 时从产品页面 URL 推断类型，使 template 传参更准确
        # （HP 接口对 template 不敏感，下载清单结果不受影响）
        type_key = infer_type_from_url(product.get("targetUrl") or "")
    print_product(product)
    return product, type_key, RESOLVE_OK


def resolve_exit_code(status):
    """产品解析状态 → 进程退出码。"""
    if status == RESOLVE_CANCELLED:
        return EXIT_CANCELLED
    if status == RESOLVE_ERROR:
        return EXIT_ERROR
    return EXIT_NOT_FOUND


def all_systems(platforms):
    """把平台结构摊平成系统列表。"""
    systems = []
    for platform in platforms:
        for system in platform["systems"]:
            systems.append({
                "platform": platform["platform"],
                "name": system["name"],
                "id": system["id"],
            })
    return systems


def filter_systems_by_os(platforms, os_filter):
    """按 --os 关键字过滤系统（不区分大小写、支持子串、逗号分隔多个）。"""
    systems = all_systems(platforms)
    if not os_filter:
        return systems
    keys = [k.strip().lower() for k in re.split(r"[,，]", os_filter) if k.strip()]
    if not keys or any(k in ("all", "全部", "*") for k in keys):
        return systems
    matched = []
    for system in systems:
        haystack = f"{system['platform']} {system['name']}".lower()
        if any(key in haystack for key in keys):
            matched.append(system)
    return matched


def interactive_select_systems(platforms, os_filter=None):
    """交互步骤 5：两级选择（平台 → 系统），均支持「全部」。

    os_filter（即 --os）不为空时据此**预选**：匹配到的平台与系统成为默认项，
    直接回车即采用，用户仍可手动改选（例如输入 1,3 覆盖预选）。
    若关键字没有任何匹配，则提示后忽略该参数，退回普通选择流程。
    """
    default_platforms = None
    default_systems = {}
    if os_filter:
        matched = filter_systems_by_os(platforms, os_filter)
        if matched:
            matched_pairs = {(s["platform"], s["id"]) for s in matched}
            default_platforms = [
                index for index, platform in enumerate(platforms)
                if any((platform["platform"], system["id"]) in matched_pairs
                       for system in platform["systems"])
            ]
            for index, platform in enumerate(platforms):
                picks = [i for i, system in enumerate(platform["systems"])
                         if (platform["platform"], system["id"]) in matched_pairs]
                if picks:
                    default_systems[index] = picks
            print(f"\n提示：检测到 --os 关键字「{os_filter}」，"
                  f"已预选 {len(matched)} 个匹配系统（回车即采用，可手动改选）。")
        else:
            print(f"\n提示：--os 关键字「{os_filter}」在交互模式下没有匹配到任何系统，"
                  "已忽略该参数，请手动选择。")

    labels = [f"{p['platform']}（{len(p['systems'])} 个系统版本）" for p in platforms]
    picked_platforms = ask_multi_choice("请选择操作系统平台：", labels,
                                        all_label="全部平台",
                                        default_indices=default_platforms)
    if picked_platforms is None:
        print("已取消。")
        return None

    selected = []
    for index in picked_platforms:
        platform = platforms[index]
        systems = platform["systems"]
        if len(systems) == 1:
            system = systems[0]
            print(f"  {platform['platform']} 只有一个系统版本：{system['name']}，已自动选择。")
            selected.append({"platform": platform["platform"],
                             "name": system["name"], "id": system["id"]})
            continue
        system_labels = [s["name"] for s in systems]
        picked_systems = ask_multi_choice(
            f"  {platform['platform']} 下选择系统版本：", system_labels,
            all_label="该平台全部系统",
            default_indices=default_systems.get(index))
        if picked_systems is None:
            print("已取消。")
            return None
        for sys_index in picked_systems:
            system = systems[sys_index]
            selected.append({"platform": platform["platform"],
                             "name": system["name"], "id": system["id"]})
    return selected


# ============================================================
# 驱动收集与清单构建
# ============================================================

def collect_drivers(client, model_oid, systems, template, verbose=False,
                    selected_ids=None):
    """按给定系统逐个拉取驱动，返回按首次出现顺序排列的去重条目列表。

    去重规则：
      1. 主键为 softwareItemId（缺失时退化为 fileUrl）；
      2. 额外按归一化 fileUrl 合并——HP 偶尔给同一个文件分配不同 itemId
         （如应用商店链接），此时只保留一条并记录全部 itemId 别名。

    同时记录每个包出现过的平台与系统（用于判定 public/ 归属）；
    selected_ids 为「用户真正要下载」的系统 ID 集合，
    用于区分「仅用于判定重叠而扫描」的系统，并标记 selected 字段。
    """
    if selected_ids is None:
        selected_ids = {s["id"] for s in systems}
    entries = {}       # itemId -> 条目
    url_index = {}     # 归一化 URL -> 条目
    order = []
    total = len(systems)
    for index, system in enumerate(systems, 1):
        label = f"{system['platform']} / {system['name']}"
        print(f"  [{index}/{total}] 正在获取驱动：{label} ...", end="", flush=True)
        try:
            drivers = client.driver_details(model_oid, system["id"], template)
        except HPError as exc:
            print(f" 失败（{exc}）")
            continue
        new_count = 0
        for driver in drivers:
            key = driver["item_id"] or driver["url"] or driver["file_name"]
            url_key = normalize_url_key(driver["url"])
            entry = entries.get(key)
            if entry is None and url_key:
                entry = url_index.get(url_key)
            if entry is None:
                entry = dict(driver)
                entry["platforms"] = set()
                entry["systems"] = []
                entry["selected_systems"] = []
                entry["selected"] = False
                entries[key] = entry
                if url_key:
                    url_index[url_key] = entry
                order.append(key)
                new_count += 1
            elif driver["item_id"] and driver["item_id"] not in entry["item_ids"]:
                # 同一文件被分配了不同 itemId：登记别名，指向同一条目
                entry["item_ids"].append(driver["item_id"])
                entries[driver["item_id"]] = entry
            entry["platforms"].add(system["platform"])
            entry["systems"].append(label)
            if system["id"] in selected_ids:
                entry["selected"] = True
                entry["selected_systems"].append(label)
        print(f" {len(drivers)} 个驱动（新增 {new_count}）")
        if verbose:
            print(f"        template={template} systemId={system['id']}")
    return [entries[key] for key in order]


def build_plan(entries):
    """把条目拆成「可下载」与「跳过（非直接下载）」两类，并决定每个包的目标目录。

    目录规则：出现在 2 个及以上平台 → public/；只出现在 1 个平台 → 该平台目录。
    跳过规则：应用商店链接、Windows Update ID（非 HTTP）、mediaType=Reference。
    """
    downloadable, skipped = [], []
    for entry in entries:
        item = dict(entry)
        if item["is_app_store"] or is_app_store_url(item["url"]):
            item["reason"] = "应用商店链接（需在应用商店中获取，非直接下载）"
            skipped.append(item)
            continue
        if not is_http_url(item["url"]):
            item["reason"] = "非 HTTP 直链（Windows Update ID 或空链接）"
            skipped.append(item)
            continue
        if (item["media_type"] or "").lower() == "reference":
            item["reason"] = "参考链接（mediaType=Reference，非直接下载）"
            skipped.append(item)
            continue
        if len(item["platforms"]) >= 2:
            item["dir"] = PUBLIC_DIR
        elif item["platforms"]:
            item["dir"] = platform_dir_name(next(iter(item["platforms"])))
        else:
            item["dir"] = PUBLIC_DIR
        downloadable.append(item)

    # 同一目录内文件名冲突时，用 itemId 后缀区分，避免互相覆盖
    used = {}
    for item in downloadable:
        name = item["file_name"]
        key = (item["dir"], name.lower())
        if key in used and used[key] != item["item_id"]:
            stem, ext = os.path.splitext(name)
            suffix = sanitize_filename(item["item_id"] or "dup", max_length=40)
            name = f"{stem}_{suffix}{ext}"
            key = (item["dir"], name.lower())
        used[key] = item["item_id"]
        item["file_name"] = name
    return downloadable, skipped


def print_plan(downloadable, skipped, systems, output_dir):
    """打印下载清单：文件数、总大小、目录结构。"""
    print("\n" + SEPARATOR)
    print("下载清单")
    print(SEPARATOR)
    print(f"输出目录：{output_dir}")
    print(f"已选系统（{len(systems)} 个）：")
    for system in systems:
        print(f"   - {system['platform']} / {system['name']}")

    groups = {}
    for item in downloadable:
        groups.setdefault(item["dir"], []).append(item)

    known_size = sum(item["size_bytes"] or 0 for item in downloadable)
    unknown_count = sum(1 for item in downloadable if not item["size_bytes"])
    print(f"\n可下载文件：{len(downloadable)} 个"
          f"，已知大小合计 {format_size(known_size)}"
          + (f"（另有 {unknown_count} 个大小未知）" if unknown_count else ""))
    print("目录归属：出现在 2 个及以上平台的包 → public/；仅 1 个平台的包 → 该平台目录。")

    if groups:
        print("\n目录结构：")
        print(f"  {Path(output_dir).name}/")
        for directory in sorted(groups, key=lambda d: (d != PUBLIC_DIR, d)):
            items = groups[directory]
            size = sum(item["size_bytes"] or 0 for item in items)
            print(f"    {directory + '/':<16} {len(items):>3} 个文件   {format_size(size)}")

    for directory in sorted(groups, key=lambda d: (d != PUBLIC_DIR, d)):
        items = groups[directory]
        size = sum(item["size_bytes"] or 0 for item in items)
        print(f"\n[{directory}/]  {len(items)} 个文件，{format_size(size)}")
        for item in items:
            size_text = format_size(item["size_bytes"]) if item["size_bytes"] else "大小未知"
            title = item["title"][:40]
            print(f"   {item['file_name']:<44} {size_text:>10}  "
                  f"{platform_tag(item['platforms']):<16} {title}")

    if skipped:
        print(f"\n跳过 {len(skipped)} 个非直接下载条目：")
        for item in skipped:
            print(f"   - {item['title'][:46]}  [{item['item_id']}]")
            print(f"       {item['reason']}；链接：{item['url'] or '(空链接)'}")


def write_manifest(path, product, type_key, template, systems,
                   downloadable, skipped, output_dir, stats=None):
    """把本次清单写入 JSON 文件（可选，便于后续核对）。"""
    payload = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": BASE_URL,
        "product": product,
        "productType": type_key,
        "template": template,
        "outputDir": str(output_dir),
        "systems": systems,
        "files": [
            {
                "dir": item["dir"],
                "fileName": item["file_name"],
                "itemId": item["item_id"],
                "itemIds": item["item_ids"],
                "title": item["title"],
                "version": item["version"],
                "releaseDate": item["release_date"],
                "size": item["size_text"],
                "sizeBytes": item["size_bytes"],
                "platforms": sorted(item["platforms"]),
                "systems": item["systems"],
                "selectedSystems": item["selected_systems"],
                "url": item["url"],
            }
            for item in downloadable
        ],
        "skipped": [
            {"itemId": item["item_id"], "itemIds": item["item_ids"],
             "title": item["title"], "url": item["url"], "reason": item["reason"]}
            for item in skipped
        ],
        "downloadStats": stats,
    }
    manifest_path = Path(path)
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except OSError as exc:
        raise HPError(f"写入清单失败（{manifest_path}）：{exc}")
    print(f"\n清单已写入：{manifest_path.resolve()}")


def try_write_manifest(path, *args, **kwargs):
    """写清单的容错包装：失败只告警，不影响清单展示与下载结果。"""
    if not path:
        return
    try:
        write_manifest(path, *args, **kwargs)
    except HPError as exc:
        print(f"⚠ {exc}（清单未写入，不影响下载结果）")


# ============================================================
# 下载
# ============================================================

def print_progress(done, total, started):
    """在单行内刷新下载进度。"""
    elapsed = max(time.time() - started, 1e-6)
    speed = done / elapsed
    if total:
        percent = done * 100.0 / total
        filled = int(24 * min(done / total, 1.0))
        bar = "#" * filled + "-" * (24 - filled)
        sys.stdout.write(f"\r    [{bar}] {percent:5.1f}%  "
                         f"{format_size(done)}/{format_size(total)}  "
                         f"{format_size(int(speed))}/s   ")
    else:
        sys.stdout.write(f"\r    已下载 {format_size(done)}  "
                         f"{format_size(int(speed))}/s   ")
    sys.stdout.flush()


def download_one(url, dest_path, expected_size=None, size_text=None,
                 max_retries=MAX_RETRIES):
    """下载单个文件，返回 (状态, 字节数)。

    状态：'skipped'（已存在且大小匹配）/ 'downloaded'（新下载）。
    已存在文件的判断使用 size_tolerance() 计算的大小容差
    （接口 fileSize 是保留 1 位小数的向上取整值，小文件需要额外放宽）。
    下载先写 .part 临时文件，成功后再改名，避免半成品被当成完整文件。
    输出目录无法创建（路径上有普通文件、权限不足等）时抛出 HPError 而不是
    Python 原生 OSError，避免 traceback 并让上层按「单文件失败」继续处理。
    """
    dest_path = Path(dest_path)
    if dest_path.exists():
        actual = dest_path.stat().st_size
        if expected_size:
            tolerance = size_tolerance(expected_size, size_text)
            if abs(actual - expected_size) <= tolerance:
                return "skipped", actual
            print(f"    已存在但大小不符（{format_size(actual)} ≠ 期望 "
                  f"{format_size(expected_size)}），重新下载")
        elif actual > 0:
            return "skipped", actual

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # 目录不可创建属于用户环境问题，重试无意义，立即上报
        raise HPError(f"无法创建输出目录 {dest_path.parent}：{exc}")

    part_path = dest_path.with_name(dest_path.name + ".part")
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            request = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Referer": f"{BASE_URL}/",
                "Accept": "*/*",
            })
            done = 0
            started = time.time()
            last_update = 0.0
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                header_length = response.headers.get("Content-Length")
                total = int(header_length) if header_length and header_length.isdigit() else 0
                if not total:
                    total = expected_size or 0
                with open(part_path, "wb") as handle:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last_update >= 0.2:
                            last_update = now
                            print_progress(done, total, started)
            print_progress(done, total or done, started)
            sys.stdout.write("\n")
            sys.stdout.flush()

            # 下载完整性校验（仅提示，不删除文件，方便用户自行判断）
            if expected_size:
                tolerance = size_tolerance(expected_size, size_text)
                if abs(done - expected_size) > tolerance:
                    print(f"    警告：实际大小 {format_size(done)} 与接口标注 "
                          f"{format_size(expected_size)} 相差较大，请留意文件是否完整。")
            os.replace(part_path, dest_path)
            return "downloaded", done
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                TimeoutError) as exc:
            last_error = exc
            sys.stdout.write("\n")
            if part_path.exists():
                try:
                    part_path.unlink()
                except OSError:
                    pass
            if attempt < max_retries:
                wait = RETRY_BACKOFF * attempt
                print(f"    下载出错：{exc}，{wait:.1f}s 后重试（{attempt}/{max_retries - 1}）")
                time.sleep(wait)
    raise HPError(f"下载失败（已重试 {max_retries} 次）：{last_error}")


def download_all(items, output_dir):
    """批量下载，返回统计信息。"""
    stats = {"downloaded": 0, "skipped": 0, "failed": 0,
             "bytes": 0, "failures": []}
    total = len(items)
    for index, item in enumerate(items, 1):
        dest_path = Path(output_dir) / item["dir"] / item["file_name"]
        size_text = format_size(item["size_bytes"]) if item["size_bytes"] else "大小未知"
        print(f"\n[{index}/{total}] {item['dir']}/{item['file_name']}  ({size_text})")
        print(f"        {item['title'][:60]}")
        print(f"        {item['url']}")
        try:
            status, size = download_one(item["url"], dest_path, item["size_bytes"],
                                        item["size_text"])
        except (HPError, OSError) as exc:
            # 兜底捕获 OSError：任何文件系统异常都只记为单个文件失败，不中断整体下载
            print(f"    ✗ {exc}")
            stats["failed"] += 1
            stats["failures"].append({"file": str(dest_path), "error": str(exc)})
            continue
        if status == "skipped":
            print(f"    → 已存在且大小匹配（{format_size(size)}），跳过")
            stats["skipped"] += 1
        else:
            print(f"    ✓ 完成（{format_size(size)}）")
            stats["downloaded"] += 1
            stats["bytes"] += size
    return stats


# ============================================================
# 命令行入口
# ============================================================

def parse_args(argv=None):
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        prog="hp_driver_download.py",
        description="HP 驱动下载工具（通用交互版）：支持 HP 全部产品类型，"
                    "交互引导或纯命令行自动化。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  交互模式              python3 hp_driver_download.py\n"
            "  按型号下载            python3 hp_driver_download.py --model 492J0PA --type laptop\n"
            "  只输出清单            python3 hp_driver_download.py --model W1A53A --type printer --dry-run --yes\n"
            "  按 URL 下载           python3 hp_driver_download.py --url "
            "\"https://support.hp.com/cn-zh/drivers/omen-16.1-inch-gaming-laptop-pc-16-b0000/model/2100371527?sku=492J0PA\"\n"
            "  指定系统              python3 hp_driver_download.py --model 492J0PA --os \"Windows 11\"\n"
            "\n"
            "目录组织: downloads/public（跨平台共享）、downloads/windows-10、downloads/windows-11、"
            "downloads/<其他平台>\n"
            "退出码: 0=成功或用户主动取消, 1=出错, 2=未找到型号/无可用驱动"
            "（argparse 参数错误同为 2）, 130=被 Ctrl-C 中断\n"
        ),
    )
    parser.add_argument("--url", help="HP 驱动页面 URL，直接从中解析 Model OID（兼容旧用法）")
    parser.add_argument("--model", help="产品型号，如 492J0PA / W1A53A")
    parser.add_argument("--type", choices=sorted(TEMPLATE_MAP.keys()),
                        help="产品类型（laptop/desktop/printer/headset（别名 poly）/other）；"
                             "仅影响 driverDetails 的 template 参数——HP 接口对 template "
                             "不敏感，下载清单不会因此变化；缺省时自动推断/使用默认值")
    parser.add_argument("--os", help="指定系统版本关键字（默认全部），支持子串匹配与逗号分隔；"
                                     "交互模式下会据此预选匹配的平台与系统（回车即采用，可改选）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只输出下载清单，不下载文件；仍会请求确认（默认否），"
                             "加 --yes 可跳过确认")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"下载目录（默认 {DEFAULT_OUTPUT_DIR}）")
    parser.add_argument("--yes", action="store_true",
                        help="跳过确认提示（产品确认 + 下载确认），用于自动化；"
                             "不跳过产品类型/型号/系统等必要输入。"
                             "未指定时交互与非交互模式都会询问「是否开始下载」（默认否）")
    parser.add_argument("--manifest", help="把下载清单写入指定 JSON 文件（可选）")
    parser.add_argument("--verbose", action="store_true", help="输出更详细的调试信息")
    return parser.parse_args(argv)


def main(argv=None):
    """主流程：确定产品 → 选择系统 → 收集驱动 → 打印清单 → 确认 → 下载。"""
    args = parse_args(argv)
    print_header()

    if args.url and args.model:
        print("提示：同时提供了 --url 与 --model，将优先使用 --url。")

    # 有 --url 或 --model 时进入非交互模式
    interactive = not (args.url or args.model)
    if interactive:
        print("当前为交互模式（如需自动化，请使用 --model 或 --url 参数）。")
    else:
        print("当前为非交互模式。")

    client = HPClient(verbose=args.verbose)

    # ---------- 步骤 1: 初始化会话 ----------
    print("\n[1/6] 正在初始化 HP 会话 ...")
    try:
        client.init()
        print("  会话初始化完成（已获取 wcc_s_flag / wcc_s_ref cookie）。")
    except HPError as exc:
        print(f"✗ 初始化会话失败：{exc}")
        return EXIT_ERROR

    # ---------- 步骤 2: 确定产品 ----------
    print("\n[2/6] 确定目标产品 ...")
    try:
        if interactive:
            product, type_key, status = interactive_resolve_product(
                client, args, assume_yes=args.yes)
        else:
            product, type_key, status = noninteractive_resolve_product(client, args)
    except HPError as exc:
        print(f"✗ 搜索产品失败：{exc}")
        return EXIT_ERROR
    if status != RESOLVE_OK or product is None:
        # 区分「用户取消」（0）与「未找到型号 / 解析失败」（2）
        return resolve_exit_code(status)

    model_oid = str(product["productNameOID"])
    template = TEMPLATE_MAP.get(type_key, DEFAULT_TEMPLATE)
    if type_key:
        print(f"\n  产品类型：{type_display_name(type_key)}（template={template}）")
    else:
        print(f"\n  产品类型：自动（未指定，使用默认 template={template}）")
    print(f"  Model OID：{model_oid}")

    # ---------- 步骤 3: 获取系统版本列表 ----------
    print("\n[3/6] 正在获取操作系统版本列表 ...")
    try:
        platforms = client.os_versions(model_oid)
    except HPError as exc:
        print(f"✗ 获取系统版本列表失败：{exc}")
        return EXIT_ERROR
    if not platforms:
        print("✗ 该产品没有可用的操作系统版本（可能已停止支持）。")
        if type_key in ("headset", "poly"):
            print("  说明：HP 的 osVersionData 接口对 Poly 产品不返回 OS 维度驱动数据，"
                  "因此脚本无法列出可下载文件。")
            print(f"  Poly 相关软件请从 HP Poly 驱动/软件页获取："
                  f"{BASE_URL}/{client.cc}-{client.lc}/drivers/poly")
        return EXIT_NOT_FOUND
    print(f"  共 {len(platforms)} 个平台："
          + "、".join(f"{p['platform']}({len(p['systems'])})" for p in platforms))

    # ---------- 步骤 4: 选择系统 ----------
    print("\n[4/6] 选择要下载的系统版本 ...")
    if interactive:
        systems = interactive_select_systems(platforms, os_filter=args.os)
        if systems is None:
            return EXIT_CANCELLED
    else:
        systems = filter_systems_by_os(platforms, args.os)
        if not systems and args.os:
            print(f"✗ --os 关键字没有匹配到任何系统：{args.os}")
            print("  可用系统：")
            for system in all_systems(platforms):
                print(f"   - {system['platform']} / {system['name']}")
            return EXIT_ERROR
    if not systems:
        print("✗ 没有选中任何系统版本，已退出。")
        return EXIT_ERROR
    print(f"  已选 {len(systems)} 个系统："
          + "、".join(f"{s['platform']}/{s['name']}" for s in systems))

    # ---------- 步骤 5: 收集驱动并生成清单 ----------
    print("\n[5/6] 正在获取驱动列表 ...")
    # 目录归属（public/）按「产品全部平台」的重叠情况判定：
    # 若用户只选了部分系统，额外扫描其余系统仅用于判定重叠，
    # 未选系统的驱动不会进入下载清单。
    selected_ids = {s["id"] for s in systems}
    scan_systems = list(systems)
    extra_systems = [s for s in all_systems(platforms) if s["id"] not in selected_ids]
    if extra_systems:
        print(f"  说明：为判定 public/ 归属，额外扫描 {len(extra_systems)} 个未选系统"
              f"（仅用于比对，其驱动不会下载）。")
        scan_systems.extend(extra_systems)
    entries = collect_drivers(client, model_oid, scan_systems, template,
                              verbose=args.verbose, selected_ids=selected_ids)
    entries = [entry for entry in entries if entry["selected"]]
    if not entries:
        print("✗ 所选系统下没有找到任何驱动。")
        return EXIT_NOT_FOUND
    downloadable, skipped = build_plan(entries)
    output_dir = Path(args.output_dir).resolve()
    print_plan(downloadable, skipped, systems, output_dir)

    if not downloadable:
        print("\n没有可下载的 HTTP 直链文件，已退出。")
        try_write_manifest(args.manifest, product, type_key, template, systems,
                           downloadable, skipped, output_dir)
        return EXIT_NOT_FOUND

    # ---------- 步骤 6: 确认并下载 ----------
    # 未指定 --yes 时一律请求确认：交互模式、非交互模式（--model / --url），
    # 以及 --dry-run 都会询问；默认「否」为安全选项，
    # stdin 为 EOF（自动化管道）时同样按「否」处理并安全退出（退出码 0）。
    # --dry-run 下无论确认与否都只输出清单，绝不下载。
    if not args.yes:
        if not interactive:
            print("\n提示：非交互模式在未指定 --yes 时同样需要确认；"
                  "如需无人值守直接下载，请加 --yes 参数。")
        confirmed, hit_eof = ask_confirm_ex("\n是否开始下载？", default=False)
        if not confirmed:
            print("已取消下载，未写入任何文件。" if not args.dry_run
                  else "已取消（--dry-run 模式本来也不会下载文件）。")
            if not interactive:
                if hit_eof:
                    print("  提示：检测到标准输入已结束（EOF），已按默认「否」安全退出；"
                          "自动化场景请加 --yes 跳过确认。")
                else:
                    print("  提示：如需跳过确认直接下载，请加 --yes 参数。")
            if not args.dry_run:
                return EXIT_CANCELLED if interactive else EXIT_SUCCESS

    # --dry-run：只输出清单，不下载任何文件
    if args.dry_run:
        print("\n（--dry-run 模式：仅输出清单，未下载任何文件）")
        try_write_manifest(args.manifest, product, type_key, template, systems,
                           downloadable, skipped, output_dir)
        return EXIT_SUCCESS

    print("\n[6/6] 开始下载 ...")
    stats = download_all(downloadable, output_dir)

    print("\n" + SEPARATOR)
    print("下载完成")
    print(f"  新下载  : {stats['downloaded']} 个文件（{format_size(stats['bytes'])}）")
    print(f"  已跳过  : {stats['skipped']} 个文件（已存在且大小匹配）")
    print(f"  失败    : {stats['failed']} 个文件")
    if stats["failures"]:
        print("  失败明细：")
        for failure in stats["failures"]:
            print(f"    - {failure['file']}：{failure['error']}")
    print(f"  输出目录: {output_dir}")
    print(SEPARATOR)

    try_write_manifest(args.manifest, product, type_key, template, systems,
                       downloadable, skipped, output_dir, stats)
    return EXIT_SUCCESS if stats["failed"] == 0 else EXIT_ERROR


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已被用户中断。")
        sys.exit(EXIT_INTERRUPTED)
