# Dynamic Coverage Map（中文版说明）

本仓库聚焦于 **SWE-bench-lite** 数据集的测试用例分析，自动运行每个任务对应的 Docker 镜像、收集所有 `pytest` 测试函数的调用轨迹，并将结果保存到 `results/`。配套的解析脚本可以进一步生成函数覆盖表、调用图与调用树，方便后续的 LLM 评估或可视化工作。

## 目录速览

- `run_dockers.py`：主入口，批量遍历镜像并在容器中执行 `utils/trace.py`。
- `utils/trace.py`、`utils/hooks.py`：收集测试列表、运行测试并记录函数调用关系。
- `prepare_repos.py`：从 SWE-bench 数据准备本地仓库目录，同时可批量启动带挂载的容器。
- `pull_dockers.py`、`generate_swebench_list.py`：生成镜像列表并从 Docker Hub/ghcr 批量拉取。
- `parse_coverage_map.py`、`parse_call_graph.py`、`parse_call_tree.py`：对 `traces.json` 做结构化整理。
- `results/`、`logs/`、`automation_logs/`：分别保存测试输出与运行日志。

## 环境依赖（暂不确定正确性）

1. Python 3.8+，并安装脚本中用到的三方库：
   ```bash
   pip install docker datasets tqdm pytest-json-report pytest-cov
   ```
2. 已安装并登录 Docker，且本机具备运行 SWE-bench 镜像所需的 CPU/内存。
3. 可访问本地或远端的 SWE-bench-lite 数据集（HuggingFace `datasets` 可离线读取本地路径）。

## 数据与镜像准备流程

1. **生成镜像清单**
   ```bash
   python generate_swebench_list.py \
     --dataset /path/to/swe-bench-lite \
     --output swebench_lite_images.txt
   ```
   输出中记录了 `sweb.eval.x86_64.<instance_id>` 形式的全部镜像标签。

2. **准备本地代码仓库（可选）**
   ```bash
   python prepare_repos.py
   ```
   该脚本会：
   - 从数据集中读取每个 `instance_id`；
   - 按 `scikit-learn__scikit-learn-xxxx` 的结构在 `./sklearn-swe-bench/` 下创建目录；
   - 克隆仓库、切换到 `base_commit`，并预建 `result/` 目录，用于之后挂载输出。

3. **拉取 Docker 镜像**
   - 若已有镜像清单，可使用 `pull_dockers.py` 过滤、补齐缺失的镜像。例如：
     ```bash
     python pull_dockers.py \
       --prefix sweb.eval.x86_64.scikit-learn \
       --max-workers 4
     ```
   - 也可以直接在 `prepare_repos.py` 中调用 `prepare_dockers()`，为每个实例拉取镜像并启动挂载容器，便于手动调试。

## 批量运行与覆盖收集

`run_dockers.py` 是数据生成主流程。脚本会依次：

1. 读取本地可用镜像（默认 `ghcr.io/epoch-research/swe-bench.eval.x86_64`，可用 `--image-prefix` 调整）。
2. 针对每个镜像创建容器，挂载：
   - `--result-dir/<instance>/result` → `/workspace/result`
   - `--script-dir`（包含 `trace.py` 和 `hooks.py`）→ `/host_scripts`
3. 在容器的 `testbed` conda 环境中安装 `pytest-json-report`、`pytest-cov` 等依赖。
4. 运行：
   ```bash
   conda run -n testbed bash -c \
     'cd /host_scripts && python trace.py \
        --project-root /testbed \
        --max-workers 16 \
        --output-dir /workspace/result'
   ```
5. 停止并删除容器，把输出和日志保存在宿主机。

示例命令：
```bash
python run_dockers.py \
  --script-dir /home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/utils \
  --result-dir /home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/results \
  --image-prefix "ghcr.io/epoch-research/swe-bench.eval.x86_64"
```

### `trace.py` / `hooks.py` 如何工作

- `trace.py` 先用 `pytest --collect-only` 收集所有测试，支持 `--max-workers`、`--max-tests`、`--random` 等参数，并默认忽略 `doc/、examples/、build/` 等噪声目录。
- 随后会并行调用 `trace_test()`，执行单个测试时通过 `hooks.py`（注入 `sys.setprofile`）记录函数调用链，最终写入 `<temp>/trace.json`。
- 每条测试的结果格式：
  ```json
  {
    "test-id": "...::test_xxx",
    "test-func-id": "path/to/file.py:123:test_xxx",
    "call-relations": [
      {"caller": {...}, "callee": {...}},
      ...
    ]
  }
  ```
- 所有测试的列表与细节分别输出到 `<result_dir>/tests-info.json` 与 `<result_dir>/traces.json`。

## 结果结构与二次解析

- `results/<instance_id>/result/tests-info.json`：`pytest --collect-only` 的原始 JSON。
- `results/<instance_id>/result/traces.json`：每个测试的调用关系，是后续分析的基础。
- `logs/`：`run_dockers.py` 自动生成的时间戳日志，包含容器执行细节。

解析脚本提供了多种下游格式：

| 脚本 | 功能 | 输出示例 |
| --- | --- | --- |
| `parse_coverage_map.py` | 聚合所有调用点，得到 `test_id -> covered_functions` 映射 | `dynamic_scikit-learn_repaired/` |
| `parse_call_graph.py` | 将调用关系转换为 `nodes + edges` 或紧凑 `caller -> callee` 列表 | `dynamic_scikit-learn_call_graph/` |
| `parse_call_tree.py` | 生成更适合 LLM 阅读的树形文本，也支持 `compact` / `graph` 模式 | `dynamic_scikit-learn_call_tree/` |

运行示例：
```bash
# 生成覆盖映射
python parse_coverage_map.py \
  --source_folder ./results \
  --save_folder ./dynamic_scikit-learn_repaired \
  --substring scikit-learn

# 生成调用树
python parse_call_tree.py \
  --source_folder ./results \
  --save_folder ./dynamic-scikit-learn_call_tree \
  --substring scikit-learn \
  --format tree
```

## 日志与排障建议

- 若 `run_dockers.py` 报错，优先查看 `logs/batch_run_*.log`，其中保留了容器 `STDOUT/STDERR`。
- `results/<instance>/result` 为空通常表示容器执行失败或 `pytest` 未通过；可以用 `docker run -it <image> /bin/bash` 手动验证。
- `trace.py` 在子进程内捕获异常，会输出具体的测试名及失败原因，可根据 `tests-info.json` 重新过滤或减小 `--max-workers`。
- 拉取镜像缓慢时，可在 `pull_dockers.py` 中配置 `--proxy http://host:port` 或切换到本地镜像源。

## 进一步的扩展

- 在 `prepare_repos.py` 中增补 `REPO_DOCKER_MAPPING`，即可支持更多 SWE-bench 项目。
- 若需自定义忽略目录或 hook 逻辑，可直接修改 `utils/trace.py` 与 `utils/hooks.py`，再通过 `run_dockers.py --script-dir` 指向新的脚本目录。
- 结果可与 `swe_bench_data.json` 等原始信息结合，用于生成覆盖驱动的数据切片或可视化报告。

祝使用顺利！
