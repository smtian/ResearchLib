
# paper-tracker

paper-tracker 是一个面向计算生物学 / 机器学习领域的论文日常抓取与筛选工具。  
通过配置期刊列表、开放获取源（如 arXiv、bioRxiv 等）以及关键词规则，自动完成：

- 追踪目标期刊的新发表论文
- 解析开放获取 PDF 地址
- 基于标题 / 摘要进行关键词打分与筛选
- 输出每日候选论文清单，便于人工快速浏览与阅读

仓库采用标准的 Python 包结构（`src/` 布局），支持本地开发、单元测试以及 GitHub Actions 定时任务集成。

---

## 功能概览

- **期刊追踪**
  - 使用 `venues.yml` 配置期刊元数据（名称、ISSN、Crossref 查询参数等）。
  - 基于 Crossref 等数据源拉取最新论文元信息。

- **开放获取解析**
  - 使用 `sources.yml` 配置开放获取来源（如 arXiv、bioRxiv 等）。
  - 根据 DOI / 标题查询开放获取 PDF 链接（Open Access Resolution）。

- **关键词打分与筛选**
  - 使用 `keywords.yml` 配置包含词/排除词等规则。
  - 对标题与摘要进行打分，输出含分数与标签的结果。

- **存储与去重**
  - 所有论文以结构化 JSONL 等形式存储，仅保留必要字段，用于去重与增量更新。

- **CLI 工作流**
  - 通过子命令分阶段执行：`fetch-venues`、`resolve-sources`、`enrich-and-score`、`generate-daily-report`、`cleanup`。

---

## 环境准备

### 1. 前置要求

- Python 版本：建议 `3.10+`
- 操作系统：Linux / macOS / Windows 均可
- 已安装 `git` 和 `pip`


### 2. 安装依赖

```bash
pip install -r requirements.txt
```

---

## 安装与本地开发

以下命令以类 Unix Shell 为例；在 Windows PowerShell 下请相应调整激活虚拟环境的命令。

### 1. 克隆仓库

```bash
git clone https://github.com/Sheldon04/paper-tracker.git
cd paper-tracker
```

### 2. 创建虚拟环境

使用 `venv`（也可以根据个人习惯改用 conda）：

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
# .venv\Scripts\Activate.ps1
```

> 说明：你可以使用 `python-dotenv` 在程序启动时自动加载 `.env`，也可以在 GitHub Actions 中通过 `env:` 直接配置相同的变量。

### 3. 安装项目

在仓库根目录执行：

```bash
pip install -e .
```

安装完成后，应当能看到 CLI 帮助信息：

```bash
paper-tracker --help
```

若出现 `ModuleNotFoundError: No module named 'paper_tracker'`，请确认：

* 项目采用 `src` 布局：`src/paper_tracker/__init__.py` 存在；
* 已在 `pyproject.toml` 或 `setup.cfg` 中正确声明了包和入口点；
* 当前终端已激活虚拟环境，并在仓库根目录下执行命令。

---

## 配置文件说明

所有配置文件通常放在 `config/` 目录下（可根据实际项目结构调整）：

```text
config/
  keywords.yml
  sources.yml
  venues.yml
```

### 1. `keywords.yml`

用于定义论文打分规则，包括“包含关键词”、“生物相关关键词”、“排除关键词”等：

```yaml
include_keywords_1:
  - "deep learning"
  - "machine learning"
  - "neural network"

include_keywords_2:
  - "single-cell"
  - "genomics"
  - "transcriptomics"

exclude_keywords:
  - "review"
  - "editorial"
```

打分阶段会在 `title + abstract` 中搜索这些关键词，用于筛选高相关度论文。

### 2. `sources.yml`

TODO

用于在“开放获取解析（resolve-sources）”阶段，根据 DOI 或标题查询 PDF 链接。

### 3. `venues.yml`

定义你希望追踪的期刊列表及其元信息：

```yaml
venues:
  - id: nature
    name: "Nature"
    issn: "0028-0836"
    type: crossref
    query:
      publisher: "Springer Nature"
      subject: ["Biological Sciences"]

  - id: cell
    name: "Cell"
    issn: "0092-8674"
    type: crossref
```

CLI 在 `fetch-venues` 阶段会根据这些配置查询对应期刊的最新论文。

---

## CLI 使用说明

项目通过一个统一的命令行入口（示例：`paper-tracker`）对外提供功能。
执行以下命令查看完整帮助：

```bash
paper-tracker --help
```

常见子命令包括（名称以你当前实现为准）：

### 1. 抓取期刊最新论文：`fetch-venues`

```bash
paper-tracker fetch-venues \
  --venues-config config/venues.yml \
  --output data/raw/venues.jsonl
```

* 读取 `venues.yml`；
* 调用 Crossref 等接口获取新论文元信息；
* 将结果保存为 JSONL 文件（便于增量更新和后续处理）。

### TODO

---

## 项目结构示例

根据当前设计，大致结构可能如下（以实际仓库为准）：

```text
.
├── src/
│   └── paper_tracker/
│       ├── __init__.py
│       ├── cli.py          # 命令行入口
│       ├── config.py       # 配置模型与加载函数
│       ├── models.py       # Paper、ScoringResult 等数据模型
│       ├── storage.py      # JSONL 等存储读写
│       ├── venues/         # 不同期刊抓取逻辑（含 crossref 等）
│       └── sources/        # 开放获取源解析逻辑
├── config/
│   ├── keywords.yml
│   ├── sources.yml
│   └── venues.yml
├── data/                   # 运行时生成的数据（可在 .gitignore 中控制）
├── reports/                # 每日报告输出目录
├── tests/
│   ├── test_config.py
│   ├── test_models.py
│   └── test_storage.py
├── .env
├── pyproject.toml / setup.cfg
└── README.md
```
---

## GitHub Actions

若你使用 GitHub Actions 做每日定时抓取，典型工作流可以包含：

* 每日定时触发（如 `cron: "0 6 * * *"`）
* 设置 Python 版本、安装依赖
* 设置环境变量（对应 `.env` 内容）
* 顺序执行：

  * `paper-tracker fetch-venues ...`
  * `paper-tracker resolve-sources ...`
  * `paper-tracker enrich-and-score ...`
  * `paper-tracker generate-daily-report ...`
* 将 `reports/` 中的新日报提交到仓库或发布为静态页面

> 具体 workflow 配置可根据你实际需求在 `.github/workflows/` 中补充。

