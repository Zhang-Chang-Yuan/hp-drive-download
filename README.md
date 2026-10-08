# HP 驱动下载工具（通用交互版）

一个**纯 Python 标准库**实现的 HP 驱动批量下载脚本：既可以在终端里一步步交互引导，
也可以完全用命令行参数驱动（适合自动化 / 脚本化）。

数据全部来自 HP 官方接口（`support.hp.com`），不依赖任何第三方库、不需要 API Key、
不需要浏览器，也不会有任何遥测。

```
$ python3 hp_driver_download.py --model 492J0PA --type laptop --dry-run --yes
...
可下载文件：46 个，已知大小合计 6.7 GB
目录结构：
  downloads/
    public/           17 个文件   1.2 GB
    windows-10/        9 个文件   1.1 GB
    windows-11/       20 个文件   4.4 GB
```

---

## 目录

- [特性](#特性)
- [安装要求](#安装要求)
- [快速开始](#快速开始)
- [交互流程](#交互流程)
- [命令行参数](#命令行参数)
- [目录结构规则](#目录结构规则)
- [支持的产品类型](#支持的产品类型)
- [API 流程简述](#api-流程简述)
- [退出码](#退出码)
- [常见问题](#常见问题)
- [已知限制与设计取舍](#已知限制与设计取舍)

---

## 特性

| 特性 | 说明 |
|------|------|
| 通用 | 支持 HP 全部产品类型：笔记本 / 台式机 / 打印机 / Poly / 其他；覆盖 Windows XP～11（含 ARM64）、Linux、macOS 等平台（Poly 的可检索范围见[已知限制](#已知限制与设计取舍)） |
| 交互引导 | 类型菜单 → 型号输入 → 搜索反馈 → 产品确认 → 平台/系统选择 → 清单确认，每一步都有中文提示 |
| 自动化 | `--model` / `--url` + `--type` / `--os` / `--yes` 即可无人值守运行；未加 `--yes` 时非交互模式也会先确认，避免误触发大体积下载 |
| 清单预览 | `--dry-run` 只输出清单（文件数、总大小、目录结构、逐文件明细、跳过原因），不下载任何文件 |
| 智能去重 | 以 `softwareItemId` 为主键，并额外按 URL 归并，同一驱动只下一次 |
| 稳定的目录结构 | 跨平台共享的包放 `public/`，单平台独有的包放对应平台目录；`public/` 的判定不随用户选择范围漂移 |
| 自动跳过非直链 | 应用商店链接、Windows Update ID（`ish_...`）、`mediaType=Reference` 等条目单独列出并说明原因 |
| 幂等 / 可重跑 | 已存在且大小匹配的文件直接跳过；大小不符则重新下载 |
| 稳健下载 | 浏览器 UA + Referer + Cookie 会话、自动重试退避、`.part` 临时文件、下载后大小校验 |
| 安全文件名 | 防路径穿越（`../../etc/passwd` → `passwd`）、替换非法字符、超长截断、目录内重名自动加 itemId 后缀 |
| 纯标准库 | 只用到 `argparse / html / http.cookiejar / json / os / re / sys / time / urllib / pathlib` |
| 可核对 | `--manifest` 输出 JSON 清单（含 itemId、URL、大小、平台归属、跳过原因），便于事后审计 |

---

## 安装要求

- **Python 3.8+**（开发与测试环境为 3.13.5；脚本只用标准库，无新语法依赖）
- **无需 `pip install`**，无任何第三方依赖
- 需要能访问 `support.hp.com` 与 `ftp.hp.com`（公司网络/代理环境下请设置 `https_proxy`）

```bash
git clone <本仓库地址>
cd hp-drive-download
python3 hp_driver_download.py --help
```

---

## 快速开始

```bash
# 1) 交互模式：全程引导（推荐第一次使用）
python3 hp_driver_download.py

# 2) 先看清单，不下载（强烈建议先跑一次；--yes 跳过清单确认）
python3 hp_driver_download.py --model 492J0PA --type laptop --dry-run --yes

# 3) 确认无误后正式下载（--yes 跳过确认，适合自动化）
python3 hp_driver_download.py --model 492J0PA --type laptop --yes

# 4) 只要某个系统
python3 hp_driver_download.py --model 492J0PA --os "Windows 11"

# 5) 打印机（覆盖 Windows 7/8/10/11）
python3 hp_driver_download.py --model W1A53A --type printer --dry-run --yes

# 6) 兼容旧用法：直接给 HP 驱动页面 URL
python3 hp_driver_download.py --url "https://support.hp.com/cn-zh/drivers/omen-16.1-inch-gaming-laptop-pc-16-b0000/model/2100371527?sku=492J0PA"

# 7) 额外产出 JSON 清单，便于核对
python3 hp_driver_download.py --model 492J0PA --dry-run --yes --manifest /tmp/manifest.json
```

> **未加 `--yes` 时**：非交互模式（`--model` / `--url`）打印清单后同样会询问
> `是否开始下载？ [y/N]`，默认「否」；stdin 为 EOF（自动化管道）时按「否」安全退出
> （退出码 0），并提示加 `--yes`。因此下面第 2 条命令不会在无人值守时误触发下载。

已实测可用的型号：

| 型号 | 类型 | Model OID | 可下载文件 | 目录归属（实测） |
|------|------|-----------|-----------|------------------|
| 492J0PA | 笔记本 | 2100371527 | 46 | `public/` 17 + `windows-10/` 9 + `windows-11/` 20 |
| W1A53A | 打印机 | 19202536 | 20 | `public/` 15 + `windows-11/` 3 + `windows-7/` 1 + `windows-8/` 1 |
| 4ZB79A | 打印机 | 24494345 | 4 | `public/` 3 + `linux/` 1 |
| G5J38A | 打印机 | 7682228 | 10 | `public/` 8 + `windows-7/` 1 + `windows-8/` 1 |

> 「目录归属」按**去重后实际落盘的目录**统计，与「扫描到的平台」不是一回事：
> 例如 W1A53A 会扫描 Windows 10，但该平台下的包都与其他平台重叠，故全部落入
> `public/`；4ZB79A 的 4 个包中有 3 个跨平台共享，只有 1 个是 Linux 独有。

---

## 交互流程

不带 `--model` / `--url` 时进入交互模式，共 6 步：

| 步骤 | 内容 | 说明 |
|------|------|------|
| 1 | **选择产品类型** | 列表来自 HP 接口（打印机 / 笔记本电脑 / 台式机 / Poly / 其他），按接口 `order` 排序；接口不可用时回退到内置列表 |
| 2 | **输入产品型号** | 如 `492J0PA`、`W1A53A`；输入 `q` 退出 |
| 3 | **搜索并反馈** | 找到 → 显示型号 / 产品名 / Model OID / 系列 OID / 编号 OID / 产品页；未找到 → 明确提示 `✗ 未找到该型号` 并给出查询建议，然后询问是否重新输入 |
| 4 | **确认产品** | `是否使用该产品继续？ [Y/n]`；答 `n` 可重新输入型号 |
| 5 | **选择平台与系统** | 先多选平台（`0` = 全部平台），再逐平台多选系统版本（`0` = 该平台全部系统）；只有一个系统版本的平台会自动选中并提示。若带了 `--os`，脚本会**预选**匹配到的平台与系统，直接回车即采用（详见下文） |
| 6 | **清单确认并下载** | 打印输出目录、已选系统、文件数、总大小、目录结构、逐文件明细、跳过条目，然后询问 `是否开始下载？ [y/N]` |

任意提示处输入 `q`（或管道 EOF）都会安全退出，不写任何文件。

**`--yes` 的作用**：跳过第 4 步的产品确认和第 6 步的下载确认（非交互模式下同样跳过下载确认），
但**不会**跳过第 1/2/5 步的类型、型号、系统等必要输入。

**`--dry-run` 的作用**：打印清单后不下载任何文件；仍会请求一次确认（默认「否」），
加 `--yes` 可跳过。

**非交互模式的下载确认**：用 `--model` / `--url` 时，只要**没有**加 `--yes`，
打印清单后同样会询问 `是否开始下载？ [y/N]`，默认「否」：

- 直接运行（终端）：等待你输入，回车即取消，不会下载；
- stdin 为 EOF（如 `... < /dev/null`、CI 管道）：按「否」安全退出，退出码 `0`，
  并提示「自动化场景请加 `--yes`」；
- 加 `--yes`：跳过确认直接下载，保持无人值守能力。

**交互模式下的 `--os`（预选）**：`--os` 在交互模式不会被静默忽略——若关键字匹配到系统，
脚本会打印提示并把这些平台/系统设为默认项（直接回车即采用，仍可手动改选）；
若没有任何匹配，则打印提示后忽略该参数，回到普通选择流程。

---

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--model MODEL` | 产品型号，如 `492J0PA` / `W1A53A`。给出后进入非交互模式 |
| `--url URL` | HP 驱动页面 URL，直接解析其中的 Model OID（兼容旧用法）；与 `--model` 同时给出时优先使用 `--url` |
| `--type {desktop,headset,laptop,other,poly,printer}` | 产品类型。**仅影响 `driverDetails` 的 `template` 参数**——HP 接口对 template 不敏感，因此下载清单不会因它而变化；缺省时自动从产品 URL 推断，推断不出则用默认 template |
| `--os OS` | 系统版本关键字，默认全部；支持子串匹配、逗号分隔多个（如 `--os "Windows 11,Windows 10"`），`--os 全部` 表示全选。交互模式下会据此预选匹配的平台与系统（回车即采用，可改选） |
| `--dry-run` | 只输出清单，不下载文件；仍会请求确认（默认否），加 `--yes` 跳过确认 |
| `--output-dir DIR` | 下载目录，默认 `downloads` |
| `--yes` | 跳过确认提示（产品确认 + 下载确认），用于自动化；不跳过必要输入。**未指定时交互与非交互模式都会询问 `是否开始下载？`（默认否）** |
| `--manifest FILE` | 把本次清单写入指定 JSON 文件（父目录会自动创建） |
| `--verbose` | 输出更详细的调试信息（含重试退避、template / systemId 等） |
| `-h, --help` | 帮助，含示例、目录组织与退出码说明 |

---

## 目录结构规则

```
downloads/
  public/        # 出现在 2 个及以上平台的包（跨平台共享）
  windows-10/    # 仅 Windows 10 出现的包
  windows-11/    # 仅 Windows 11 出现的包
  windows-7/     # 仅 Windows 7 出现的包
  linux/         # 仅 Linux 出现的包
  <其他平台>/     # 目录名由平台名规范化而来
```

规则细节：

1. **去重键**是 `softwareItemId`；HP 偶尔给同一个文件分配多个 itemId，脚本会额外按归一化 URL 合并为一条，避免重复下载。
2. **`public/` 的判定基于「产品的全部平台」**，而不是用户这次选中的范围。若用户只选了部分系统，脚本会额外扫描其余系统**仅用于比对重叠**（这些系统的驱动不会进入清单），因此目录结构不会随选择范围漂移。
3. **平台目录只用大版本**：`Windows 11 版本 22H2（64 位）` → `windows-11`，不会出现 `windows-11-22h2` 这种小版本目录。
4. **同目录内重名**（不同 URL / 不同 itemId）会自动加 itemId 后缀，例如 `setup.exe` → `setup_ob-2.exe`，不会互相覆盖。
   **跨目录同名是正常的**：如 `HPEasyStart_17_6_14.exe` 会同时出现在 `public/`、`windows-7/`、`windows-8/`，
   它们的 URL 与 itemId 各不相同（HP 语义上是不同构建的包），脚本不会跨目录去重，也不会互相覆盖。
5. 下载先写 `<文件名>.part`，成功后再改名，避免半成品被误认为完整文件。

---

## 支持的产品类型

类型 key 与显示名（直接来自 HP 接口，按接口 `order` 排序）：

| 顺序 | type key | 显示名 | template |
|------|----------|--------|----------|
| 1 | `printer` | 打印机 | `SWD-PrinterLanding` |
| 2 | `laptop` | 笔记本电脑 | `SWD-LaptopLanding` |
| 3 | `desktop` | 台式机 | `SWD-DesktopLanding` |
| 4 | `headset` | Poly | `SWD-PolyLanding` |
| 5 | `other` | 其他 | `SWD-OtherLanding` |

> **注意**：HP 接口中 Poly 的 type key 是 **`headset`**（`linkText` 为 "Poly"，链接为 `/drivers/poly`），
> 不是 `poly`。脚本同时接受 `headset` 与 `poly` 两个别名，命令行传哪个都可以。

### 各类型的实际可用范围

| 类型 | 能否选择 / 检索 | 能否列出并下载驱动 |
|------|----------------|-------------------|
| 笔记本 / 台式机 / 打印机 / 其他 | ✅ | ✅（取决于该产品是否有 OS 维度驱动数据） |
| Poly（`headset` / `poly`） | ✅ 可被选择、可搜索到产品 | ❌ HP 的 `osVersionData` 接口对 Poly 产品返回空的 `osversions`，脚本拿不到 OS 维度数据 |

**Poly 的实际表现**（实测 `--model 76U47AA --type headset`）：

```text
[3/6] 正在获取操作系统版本列表 ...
✗ 该产品没有可用的操作系统版本（可能已停止支持）。
  说明：HP 的 osVersionData 接口对 Poly 产品不返回 OS 维度驱动数据，因此脚本无法列出可下载文件。
  Poly 相关软件请从 HP Poly 驱动/软件页获取：https://support.hp.com/cn-zh/drivers/poly
```

脚本会**明确提示原因**并以退出码 `2` 结束（属于「没有可用驱动」，不是崩溃）。
需要 Poly 的固件 / 软件（Poly Lens、Poly Studio、耳机固件等）时，
请前往 HP Poly 软件与驱动页手动获取（[`/drivers/poly`](https://support.hp.com/cn-zh/drivers/poly)）。

---

## API 流程简述

全部为 `https://support.hp.com` 下的公开接口，需带浏览器 UA / Referer，并先建立 Cookie 会话
（缺少 UA 会直接返回 403）。

### 步骤 1：初始化会话

```
GET /wcc-services/s/init?cc=cn&lc=zh
Headers: wPlatform: Windows, wPlatformVersion: 10.0, wBitness: 64
```

建立会话，获取 `wcc_s_flag` / `wcc_s_ref` cookie。

### 步骤 2：获取产品类型列表

```
GET /wcc-services/cms-v2/{cc}-{lc}/wcc_swd_landing_page
例: /wcc-services/cms-v2/cn-zh/wcc_swd_landing_page
```

```json
{"code":200,"data":[
  {"type":"printer","linkText":"打印机","link":"/{cc}-{lc}/drivers/printers","order":"1"},
  {"type":"laptop", "linkText":"笔记本电脑","link":"/{cc}-{lc}/drivers/laptops","order":"2"},
  {"type":"desktop","linkText":"台式机","link":"/{cc}-{lc}/drivers/desktops","order":"3"},
  {"type":"headset","linkText":"Poly","link":"/{cc}-{lc}/drivers/poly","order":"4"},
  {"type":"other",  "linkText":"其他","link":"/{cc}-{lc}/drivers/products","order":"5"}
]}
```

### 步骤 3：按型号搜索产品

```
GET /wcc-services/searchresult/{cc}-{lc}?q={型号}&context=PfinderContact&navigation=false
例: /wcc-services/searchresult/cn-zh?q=492J0PA&context=PfinderContact&navigation=false
```

```json
{"code":200,"data":{
  "kaaSResponse":{"code":200,"data":{"searchResults":{"categories":[
    {"subCategoryList":[{"productList":[{
      "productName":"492J0PA",
      "SEOFriendlyName":"omen-16.1-inch-gaming-laptop-pc-16-b0000",
      "productNameOID":"2100371527",
      "productNumberOID":"2100802005",
      "productSeriesOID":"2100371510",
      "targetUrl":"/contact/product/omen-.../2100371510/model/2100371527?sku=492J0PA"
    }]}]}
  ]}}},
  "verifyResponse":{"code":204,"message":"Device Not Found"}
}}
```

要点：

- 关键路径是 `data.kaaSResponse.data.searchResults.categories[].subCategoryList[].productList[]`；
- `verifyResponse.code = 204`（Device Not Found）是正常的——因为我们搜的是**型号**而不是序列号；
- 未找到时 `searchResults` 为 `null`；
- Model OID 取 `productNameOID`。

### 步骤 4：获取操作系统版本列表

```
GET /wcc-services/swd-v2/osVersionData?cc={cc}&lc={lc}&productOid={productNameOID}
```

```json
{"statusCode":200,"data":{"osversions":[
  {"name":"Windows 10","osVersionList":[
    {"id":"792898937266030878164166465223921","name":"Windows 10（64 位）","osBitVersion":"64"}
  ]},
  {"name":"Windows 11","osVersionList":[
    {"id":"1117042031711110499111149613201312551119131","name":"Windows 11"},
    {"id":"110156114612151291184491210713131221415189135131014","name":"Windows 11 版本 22H2（64 位）"}
  ]}
]}}
```

- **平台** = `osversions[].name`；**系统** = `osversions[].osVersionList[].name`
- 系统 ID 取 `osVersionList[].id`（接口的 `osTMSId` 通常为 `null`，脚本会自动回退到 `id`）

### 步骤 5：获取驱动列表

```
POST /wcc-services/swd-v2/driverDetails?authState=anonymous&template={template}
Content-Type: application/json

{
  "productLineCode": "",
  "lc": "zh", "cc": "cn",
  "osTMSId": "{系统ID}",
  "osName": "Windows",
  "productSeriesOid": 0,
  "platformId": "{系统ID}",
  "productNameOid": {productNameOID}
}
```

```json
{"statusCode":200,"data":{"softwareTypes":[
  {"accordionName":"驱动程序-显卡","softwareDriversList":[
    {"latestVersionDriver":{
      "title":"NVIDIA 显卡驱动程序",
      "version":"30.0.14.7219 Rev.A",
      "fileUrl":"https://ftp.hp.com/pub/softpaq/sp135501-136000/sp135677.exe",
      "fileSize":"929.5 MB",
      "softwareItemId":"ob-282587-1",
      "releaseDate":"2021-10-05T00:00:00.000+00:00",
      "detailInformation":{"fileName":"sp135677.exe"}
    }}
  ]}
]}}
```

要点：

- 文件名在 `latestVersionDriver.detailInformation.fileName`（不在顶层）；
- 部分条目的 `fileUrl` 是 Windows Update ID（如 `ish_4511134-4630612-16`）而不是 HTTP 链接，需跳过；
- 部分条目的 `fileSize` 为 `null`（此时脚本仍会下载，只是不做大小校验）；
- `isAppStore = true` 或 `mediaType = "Reference"` 的条目同样跳过；
- **`fileSize` 是向上取整（ceil）到 1 位小数的 1024 进制值**（如标注 `0.8 MB` 实际可能是 743 KB）。脚本的容差计算按「整步进」估算，即 `10^-小数位 × 单位`（外加 5% 兜底），这样这类文件在重复运行时能正确跳过而不会被误判为不完整。

### 步骤 6：下载

直接下载 `fileUrl`（通常为 `ftp.hp.com` 直链），按 `softwareItemId` 去重后落到对应目录。

---

## 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 成功；**用户主动取消**也返回 0——包括交互流程中的类型菜单 / 型号 / 产品确认 / 平台 / 系统 / 下载确认处取消，以及非交互模式在下载确认处选「否」或 stdin 为 EOF 时按默认「否」安全退出 |
| `1` | 出错：网络 / 接口失败、`--os` 无匹配（非交互模式）、下载失败、输出目录不可用 |
| `2` | 未找到型号、无法从输入解析出产品信息，或没有可用驱动（**Poly 产品无 OS 维度数据也归入此类**） |
| `130` | 被 `Ctrl-C`（SIGINT）中断 |

> **注意**：`argparse` 对参数错误固定使用退出码 `2`，与「未找到型号」同码。
> 自动化脚本如需区分，请结合 stderr 内容判断（参数错误会打印 `usage:` 与 `error:`）。

---

## 常见问题

**Q：为什么下载清单里有些驱动被「跳过」了？**
A：这些条目不是可直接下载的文件，清单里会逐条给出原因：应用商店链接（`isAppStore`）、
Windows Update ID（如 `ish_4511134-4630612-16`，非 HTTP 直链）、`mediaType=Reference` 的参考条目。
它们需要通过 Windows Update / Microsoft Store / 参考页面自行获取。

**Q：`--type` 好像对结果没有任何影响？**
A：正常。HP 的 `driverDetails` 接口对 `template` 参数不敏感，所以同一型号无论传哪个类型，
清单都逐条相同。`--type` 仍然会按类型传正确的 `template`（语义正确），未指定时会尝试从产品 URL 自动推断。

**Q：为什么 `public/` 里的文件比我想的多（或少）？**
A：`public/` 表示「在该产品的 2 个及以上平台出现」，判定基于产品的**全部平台**而非本次选择范围。
只选 Windows 11 时脚本也会额外扫描 Windows 10 用于比对，但不会下载未选系统的驱动，
所以 `public/` 的数量与你选多少系统无关，目录结构稳定。

**Q：某个文件每次运行都重新下载，还提示「文件可能不完整」？**
A：这是 HP 接口 `fileSize` 向上取整导致的（如标注 `0.8 MB`，实际 743 KB）。
脚本已按 ceil 语义放宽容差（`10^-小数位 × 单位`，另有 5% 兜底），
实测 77/77 个文件都能正确跳过；若仍遇到，请用 `--verbose` 反馈具体文件名与大小。

**Q：`--os` 怎么写？**
A：关键字匹配「平台 + 系统名」的子串，不区分大小写，支持逗号分隔多个。
`--os "Windows 11"` 会匹配 Windows 11 下的所有系统版本；`--os 全部` 或省略表示全选。
非交互模式下关键字没匹配到任何系统时会列出全部可用系统并以退出码 1 结束；
交互模式下会打印提示并忽略该参数，回到普通选择流程。
交互模式下 `--os` 还会**预选**匹配到的平台与系统（直接回车即采用，仍可手动改选），不会被静默忽略。

**Q：我只想先看看清单，会不会一不小心开始下载？**
A：不会。只要不加 `--yes`，无论交互还是非交互模式（`--model` / `--url` / `--dry-run`），
脚本都会在打印清单后询问 `是否开始下载？ [y/N]`，默认「否」；
stdin 为 EOF（如 CI 管道、`< /dev/null`）时同样按「否」退出，退出码 0，并提示加 `--yes`。
只有显式加了 `--yes` 才会跳过确认直接下载。

**Q：为什么选 Poly 产品最后会退出码 2？**
A：Poly 产品可以被选择和检索，但 HP 的 `osVersionData` 接口对这类产品返回空的 `osversions`，
脚本拿不到 OS 维度驱动数据，因此会明确提示并以退出码 2 结束。
Poly 的软件 / 固件请从 [HP Poly 驱动页](https://support.hp.com/cn-zh/drivers/poly) 获取。

**Q：能只下载历史版本吗？**
A：当前不支持。接口中每个条目都带 `previousVersionOfDriversList`，脚本**只下载最新版本**
（`latestVersionDriver`），这是有意的取舍（详见下节）。

**Q：能断点续传吗？**
A：不能。失败时会清理 `.part` 并整体重下。已完整下载且大小匹配的文件会跳过，因此重跑代价可控。

**Q：下载报 403 / 网络错误怎么办？**
A：脚本已内置浏览器 UA、Referer 与 Cookie 会话。若仍失败，多为网络或代理问题：
检查 `https_proxy` / `http_proxy`，或加 `--verbose` 观察重试退避日志。

**Q：`--manifest` 会写进 `downloads/` 吗？**
A：不会，写到 `--manifest` 指定的路径（父目录自动创建）；`--dry-run` 下也只会写清单、不创建下载目录。

---

## 已知限制与设计取舍

- **只下载最新版驱动**：接口的 `previousVersionOfDriversList`（历史版本）被有意忽略，对绝大多数用户而言最新版即为所需。
- **Poly 产品拿不到 OS 维度驱动数据**：Poly（type key `headset`，别名 `poly`）可以被选择、检索并显示产品信息，
  但 HP 的 `osVersionData` 接口对其返回空的 `osversions`，脚本无法列出可下载文件，
  会明确提示原因并以退出码 `2` 结束。Poly 软件 / 固件请从 [HP Poly 驱动页](https://support.hp.com/cn-zh/drivers/poly) 获取。
  因此本工具的「通用」指的是**产品类型可枚举、可按型号检索**，实际可下载范围受 HP 各接口数据完整度限制。
- **跨目录会出现同名文件**：例如 `HPEasyStart_17_6_14.exe` 同时出现在 `public/`、`windows-7/`、`windows-8/`。
  它们的 `fileUrl` 与 `softwareItemId` 都不同——按 HP 语义这是**同一名字的不同构建 / 不同平台包**，不是重复下载。
  脚本的「同名消歧」只作用于**同一个目录内**（加 itemId 后缀，如 `setup_ob-2.exe`），跨目录同名属预期行为。
- **地区 / 语言固定为 `cn-zh`**：`HPClient` 默认 `cc="cn", lc="zh"`，命令行暂未暴露 `--cc` / `--lc`（接口本身支持）。
- **文件名未处理 Windows 保留名**：如 `CON.txt`。HP 驱动文件名均为 `spNNNNNN.exe` 形式，实际不会触发。
- **不做断点续传**：见上文 FAQ。
- **`--os` 是子串匹配**：不是精确匹配，过短的关键字可能匹配到多个平台。

---

## 仓库内容

```
hp_driver_download.py   # 主程序（单文件，纯标准库）
README.md               # 本文档（含 API 说明）
.gitignore              # 忽略下载产物与缓存
```

下载产物、`manifest.json`、`__pycache__/`、`*.part` 等均已在 `.gitignore` 中忽略，仓库只保留代码与文档。
