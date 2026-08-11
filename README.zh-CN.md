# Dynamic trace collection

[English](README.md) · [简体中文](README.zh-CN.md)

<p align="center">
  <img src="assets/dynamic-trace-collection-architecture.png" alt="Dynamic trace collection 架构" width="100%" />
</p>

> 面向 [IssueExec](https://github.com/code-philia/IssueExec) artifact 的批量动态测试执行轨迹收集工具。

[![IssueExec repository](https://img.shields.io/badge/IssueExec-code--philia%2FIssueExec-181717?logo=github)](https://github.com/code-philia/IssueExec)
[![Paper](https://img.shields.io/badge/paper-arXiv%3A2607.17286-b31b1b?logo=arxiv)](https://arxiv.org/abs/2607.17286)

本仓库实现 IssueExec 的 **Dynamic trace collection** 阶段：在 SWE-bench Docker 环境中执行测试，记录每个通过测试实际调用到的 Python 函数，并为每个实例生成动态执行路径数据库。IssueExec 的测试驱动问题定位流程会读取这些轨迹。

本仓库专注于动态轨迹收集和轨迹后处理，不包含 IssueExec 的问题定位模型、提示词构造或评测代码。

## 在 IssueExec 中的作用

```text
SWE-bench 实例 + Docker 镜像
          │
          ▼
  Dynamic trace collection（本仓库）
          │  tests-info.json + traces.json
          ▼
  IssueExec：issue → 相关测试 → 动态路径 → 候选代码位置
```

对于每个实例，工具会在隔离环境中执行测试，并通过 profiler hook 记录调用关系。只保留项目根目录下的 Python 文件，去除重复调用边，并记录调用者与被调用者之间的关系。失败测试不会写入轨迹数据库，但会单独输出诊断信息；跳过的测试会以明确的状态保留。

## 仓库结构

```text
Dynamic_Coverage_Map/
├── run_dockers.py             # 在 SWE-bench 镜像中并行批量运行
├── pull_dockers.py            # 镜像发现与并行下载
├── generate_swebench_list.py  # 根据 Hugging Face 数据集生成镜像列表
├── prepare_repos.py           # 可选：准备本地仓库检出
├── utils/                     # 注入每个容器的轨迹收集脚本
│   ├── trace.py               # pytest 测试收集与逐测试轨迹记录
│   └── hooks.py               # 基于 sys.setprofile 的调用 hook
├── parse_coverage_map.py      # traces → 测试到函数的覆盖映射
├── parse_call_graph.py        # traces → 调用图（节点与边）
├── parse_call_tree.py         # traces → 调用树、紧凑图或图结构文本
├── repair_results.py          # 修复或规范化已有结果目录
└── swebench_lite_images.txt   # 示例镜像标签列表
```

## 环境要求

- Docker Engine，以及运行容器的权限；
- Python 3.8+（IssueExec 实验使用名为 `agentless` 的 conda 环境）；
- 与 SWE-bench 兼容、包含 `testbed` 环境的 Docker 镜像；
- 宿主机 Python 依赖：`docker`、`datasets`、`requests`、`tqdm` 和 `pytest`；
- 容器内依赖：`pytest-json-report` 和 `pytest-cov`，`run_dockers.py` 会在容器中安装它们。

完整镜像集合规模较大，请提前准备足够的 Docker 磁盘空间、内存和 CPU 资源。

## 安装

```bash
git clone git@github.com:AWGiaGia/swe-tools.git
cd swe-tools
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 复现批量收集流程

### 生成镜像列表（可选）

```bash
python Dynamic_Coverage_Map/generate_swebench_list.py \
  --dataset /data/swe-bench-lite \
  --output Dynamic_Coverage_Map/swebench_lite_images.txt
```

### 拉取镜像（可选）

建议先执行 dry run，再下载少量镜像验证环境：

```bash
python Dynamic_Coverage_Map/pull_dockers.py \
  --prefix sweb.eval.x86_64.scikit-learn \
  --image-file Dynamic_Coverage_Map/swebench_lite_images.txt \
  --use-ghcr --workers 4 --dry-run

python Dynamic_Coverage_Map/pull_dockers.py \
  --prefix sweb.eval.x86_64.scikit-learn \
  --image-file Dynamic_Coverage_Map/swebench_lite_images.txt \
  --use-ghcr --workers 4 --max 2
```

下载器默认跳过本地已有镜像，并将 GHCR 镜像重新标记为 SWE-bench 命名格式。必要时可使用 `--proxy`、`--no-skip-existing` 或 `--no-retag`。

### 运行 Dynamic trace collection

建议先进行有界 smoke test：

```bash
python Dynamic_Coverage_Map/run_dockers.py \
  --script-dir "$PWD/Dynamic_Coverage_Map/utils" \
  --result-dir "$PWD/results" \
  --log-dir "$PWD/logs" \
  --image-prefix ghcr.io/epoch-research/swe-bench.eval.x86_64 \
  --parallel 4 --max 2 --enable-timeout
```

完整运行时删除 `--max 2`。`--parallel` 控制并行容器数量；`--docker-timeout SECONDS` 设置单个容器的超时时间；单独使用 `--enable-timeout` 时默认超时时间为 7 小时。

运行器会将只读的 `utils/` 挂载到容器内的 `/host_scripts`，将每个实例独立的结果目录挂载到 `/workspace/result`，随后在镜像的 `testbed` 环境中执行 `trace.py`。

### 直接跟踪本地项目

调试或进行 smoke test 时，也可以直接对已检出的项目运行：

```bash
python Dynamic_Coverage_Map/utils/trace.py \
  --project-root /path/to/project \
  --output-dir ./results/local-smoke \
  --max-tests 5 --max-workers 2 --random True --random-seed 42
```

## 输出格式

```text
results/<instance-id>/result/
├── tests-info.json
├── traces.json
├── progress.txt
├── trace_runtime.log
├── failed_tests.txt       # 存在失败测试时生成
├── skipped_tests.txt      # 存在跳过测试时生成
└── error_logs/            # 逐测试诊断日志
logs/batch_run_YYYYMMDD_HHMMSS/
├── batch_run.log
└── <instance-id>.log
```

`tests-info.json` 是 pytest 生成的测试发现报告。`traces.json` 是 IssueExec 使用的核心 Dynamic Test Execution Path Database 输入：

```json
{
  "test-id": "sklearn/tests/test_example.py::test_basic",
  "test-func-id": "sklearn/tests/test_example.py:12:test_basic",
  "call-relations": [
    {
      "caller": {"filepath": "sklearn/model.py", "lineno": 42, "func_name": "fit", "class_name": "Model"},
      "callee": {"filepath": "sklearn/utils.py", "lineno": 18, "func_name": "validate_data", "class_name": ""}
    }
  ]
}
```

路径均相对于被跟踪项目根目录；同一个测试内的重复调用边会被去除。跳过的测试包含空的 `call-relations` 列表以及 `"status": "skipped"`；失败测试会列在 `failed_tests.txt` 中。

## 后处理

```bash
python Dynamic_Coverage_Map/parse_coverage_map.py \
  --source_folder ./results --save_folder ./results_coverage \
  --substring scikit-learn

python Dynamic_Coverage_Map/parse_call_graph.py \
  --source_folder ./results --save_folder ./results_call_graph \
  --substring scikit-learn

python Dynamic_Coverage_Map/parse_call_tree.py \
  --source_folder ./results --save_folder ./results_call_tree \
  --substring scikit-learn --format tree
```

三个转换脚本分别生成 `test_id → covered_functions` 覆盖映射、显式或紧凑调用图，以及供人或 LLM 阅读的调用树表示，供 IssueExec 后续分析使用。

## 设计与可复现性说明

1. `trace.py` 使用 pytest 的 JSON report 插件发现测试，并移除参数化测试的后缀。
2. 每个选中的测试由独立进程执行；`hooks.py` 注册 `sys.setprofile`，捕获 `call`/`return` 事件。
3. 仅保留项目根目录下的 Python 函数，并记录相对于项目根目录的路径、行号、函数名和类名。
4. 通过的测试贡献动态轨迹；跳过的测试被显式记录；失败测试产生诊断信息但不会污染 `traces.json`。
5. Docker 运行器为每个实例提供隔离容器，并保留逐实例日志，支持中断后继续批量运行。

工具针对 Django 和 Astropy 仓库包含兼容逻辑，例如在旧版 Astropy pytest 插件与当前 pytest 不兼容时自动禁用相关插件。

## 常见问题

- **找不到镜像：** 检查 `docker image ls`，并确保 `--image-prefix` 与本地镜像前缀一致。
- **轨迹为空：** 检查 `tests-info.json`、`failed_tests.txt` 和实例日志；先使用 `--max 1 --max-tests 5` 重试。
- **测试收集或插件错误：** 检查 `astropy_plugins_disabled.marker` 和运行时日志。
- **超时：** 降低 `--parallel`，或设置更大的 `--docker-timeout`。

## 引用

```bibtex
@article{liu2026issueexec,
  title={IssueExec: A Test-Driven Approach for Localizing Software Engineering Issues},
  author={Liu, Jiawei and Lin, Yun and Liu, Chenyan and Qian, Yu and Liu, Yiming and Chang, Jiaxin and Zhang, Weinan and Huang, Linpeng},
  journal={arXiv preprint arXiv:2607.17286},
  year={2026}
}
```

## 相关资源

- [IssueExec 代码仓库](https://github.com/code-philia/IssueExec)
- [IssueExec arXiv 论文](https://arxiv.org/abs/2607.17286)
- [本仓库](https://github.com/AWGiaGia/swe-tools)
