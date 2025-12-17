# Tools Overview

该目录收集了围绕 SWE-bench 任务的数据处理与分析工具，旨在支撑 test-driven localization、ground-truth 统计和可视化等流程。下面按照子目录介绍各组件的目标和当前状态，便于快速上手。

## Repository Layout

- `Dynamic_Coverage_Map`：动态获取测试覆盖路径，test-driven localization 的核心输入。
- `GetGTTests`：汇总每个 SWE-bench 实例的 ground-truth 代码位置与测试。
- `show_data`：可视化 ground-truth locations 与模型预测结果。
- `Statistic_Coverage_Map` (进行中)：计划通过静态分析手段构建覆盖关系。
- `SWEStastics`：对 SWE-bench 项目进行统计分析，为调研提供数据。
- `Preliminary`、`GetGTTests` 等目录下包含辅助脚本及实验记录。

## Tool Details

### Dynamic_Coverage_Map

> **核心能力**

通过动态执行测试来收集每条测试覆盖的代码路径，生成 `test_coverage_information`。这些覆盖数据在后续步骤中用来定位 ground-truth 位置并匹配测试，是整个 pipeline 的基础。

### GetGTTests

为 SWE-bench 的每个实例输出两类信息：

- `ground truth locations`：由 issue 所关联的补丁直接修改的源码位置。
- `ground truth tests`：在仓库中已经存在、并覆盖上述位置的测试用例，依赖 `Dynamic_Coverage_Map` 的覆盖结果进行判定。

### show_data

对 ground-truth locations 和预测位置进行可视化展示，便于对比模型表现、人工审查或制作报告图表。

### Statistic_Coverage_Map (TODO)

目标是以静态分析方式（AST、CodeQL 等）构建覆盖映射，减少对动态执行的依赖。目前处于设计阶段，尚未完成。

### SWEStastics

面向 SWE-bench 全局的统计组件，生成任务数量、项目规模、测试分布等指标，以支持论文调研和数据分析。

## Usage Notes

- 各工具通常依赖 Python 环境，所需三方包列在根目录 `requirements.txt` 中。
- 运行动态分析相关工具前，请确保目标仓库可被完整检出并能在本地执行测试。
- 目录间存在数据依赖（例如 `GetGTTests` 依赖 `Dynamic_Coverage_Map` 输出），建议按照“覆盖生成 → GT 构建 → 可视化/统计”的顺序执行。
