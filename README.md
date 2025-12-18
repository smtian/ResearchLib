# paper-tracker
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/) [![Version](https://img.shields.io/badge/version-0.1.0-ff8c00)](setup.cfg) [![License](https://img.shields.io/badge/license-MIT-2ea44f)](setup.cfg) [![Status](https://img.shields.io/badge/status-active-brightgreen)](README.md)


paper-tracker 是一个面向计算生物学 / 生物信息学方向的论文追踪与筛选工具。它通过配置期刊清单与元数据来源，从 Crossref 等源拉取最新论文，再结合关键词规则与期刊分层进行打分，最终生成每日可读报告。

核心目标：
- 按期刊抓取最新论文元数据（Crossref）
- 从多个开放来源补全摘要 / PDF / 关联信息（Crossref、PubMed、arXiv、bioRxiv）
- 基于关键词与期刊层级进行评分
- 生成每日报告，便于快速浏览与筛选

---

## 快速开始

### 1) 安装依赖

```bash
pip install -r requirements.txt
```

### 2) 本地可编辑安装

```bash
pip install -e .
```

### 3) 查看 CLI 帮助

```bash
paper-tracker --help
```

---

## 配置说明

项目配置集中在 `config/` 目录：

### 1) `config/venues.yml`

定义期刊列表与抓取参数（使用 Crossref 抓取）：

```yaml
venues:
  - id: nature
    name: "Nature"
    issn:
      print: "0028-0836"
      online: "1476-4687"
    fetcher: crossref
    fetcher_params:
      days_back: 1
```

常用字段：
- `id` / `name` / `publisher` / `issn` / `website`
- `tier`：期刊层级，用于打分
- `topics` / `notes`：辅助信息
- `fetcher` / `fetcher_params`：抓取器与参数（当前实现为 Crossref）

### 2) `config/sources.yml`

定义元数据补全来源与优先级：

```yaml
sources:
  - id: pubmed
    type: pubmed
    enabled: true
    provides: ["metadata", "abstract"]
    search_by: ["doi", "title"]
    priority:
      abstract: 90
      pdf: 10
```

支持的 `type`：
- `crossref`
- `pubmed`
- `arxiv`
- `biorxiv`

`priority` 会与每个来源的 `confidence` 相乘，用于挑选最佳 `abstract` / `pdf_url`。

### 3) `config/keywords.yml`

定义打分关键词：

- `include_keywords_1` / `include_keywords_2`：需要同时命中的两组关键词
- `exclude_keywords`：可用于过滤不相关主题
- `min_score`：默认最低分阈值（报告筛选可用）

---

## CLI 工作流

推荐的完整流程如下：

### 1) 抓取期刊最新论文

```bash
paper-tracker fetch-venues --days-back 1
```

### 2) 补全摘要 / PDF / 关联信息

```bash
paper-tracker enrich-metadata --since-days 1
```

### 3) 论文打分

```bash
paper-tracker score-papers --since-days 1 --version v1
```

### 4) 生成每日报告

```bash
paper-tracker generate-daily-report --date 2025-12-16 --min-score 11
```

### 5) 清理历史数据（占位实现）

```bash
paper-tracker cleanup --keep-days 90
```

---

## 数据与输出

- `data/index/papers.jsonl`
  - 主索引文件，记录论文的基础元数据与打分摘要
- `data/cold/abstracts/YYYY-MM.jsonl.gz`
  - 全文摘要冷存储（按月分片）
- `reports/daily/YYYY-MM-DD.md`
  - 每日报告输出

---

## 打分规则（当前实现）

打分管线位于 `src/paper_tracker/scoring/`：

- `KeywordTitleScorer`：标题必须同时命中两组关键词
- `KeywordAbstractScorer`：摘要必须同时命中两组关键词
- `VenueScorer`：根据 `venues.yml` 的 `tier` 映射成分值

综合分数为各子打分之和，版本号在 `scoring/composite.py` 中维护。

---

## 开发与测试

```bash
pytest
```

---

## 目录结构

```text
.
├─ src/
│  └─ paper_tracker/
│     ├─ cli.py                # CLI 入口
│     ├─ config.py             # 配置加载与校验
│     ├─ models.py             # Paper / ScoringResult 数据模型
│     ├─ storage.py            # JSONL 索引读写与去重
│     ├─ utils.py              # 元数据补全与摘要冷存储逻辑
│     ├─ venues/               # 期刊抓取器（Crossref）
│     ├─ sources/              # 元数据来源（Crossref/PubMed/arXiv/bioRxiv）
│     └─ scoring/              # 打分逻辑
├─ config/                     # 期刊/来源/关键词配置
├─ data/                       # 索引与冷存储数据
├─ reports/                    # 每日报告输出
└─ tests/                      # 单元测试
```

---

## 注意事项

- 需要网络访问以调用 Crossref / PubMed / arXiv / bioRxiv API。
- `src/paper_tracker/venues/crossref.py` 与 `src/paper_tracker/sources/pubmed.py` 中的 `User-Agent`/`email` 仍为占位值，建议替换为自己的邮箱以符合服务要求。
