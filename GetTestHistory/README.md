# 测试函数历史编辑信息收集工具

## 功能描述

该工具用于收集SWE-bench数据集中测试函数的历史编辑信息,包括:

1. **共同修改记录**: 测试函数与其覆盖的代码实体在同一commit中的修改记录
2. **Commit元信息**: Commit message和类型标签(fix/feat/refactor等)
3. **修改时间线**: 每个实体的修改历史时间戳和首次共同出现时间
4. **修改原子性分组**: 同一commit中被修改的实体集合

## 依赖安装

```bash
pip install datasets
```

系统需要安装Git命令行工具。

## 使用方法

### 基本用法

```bash
python collect_historical_info.py <swe_bench_path> <coverage_graph_path> [output_dir]
```

### 参数说明

- `swe_bench_path`: SWE-bench数据集路径(本地或Hugging Face路径)
- `coverage_graph_path`: 测试覆盖关系图文件夹路径
- `output_dir`: 输出目录(可选,默认为'historical_information')

### 示例

```bash
# 使用本地数据集
python collect_historical_info.py \
    /path/to/swe-bench-lite \
    /path/to/coverage_graphs \
    historical_information

# 使用Hugging Face数据集
python collect_historical_info.py \
    "princeton-nlp/SWE-bench_Lite" \
    ./coverage_graphs \
    output
```

例如：
```
python collect_historical_info.py \
    /home/jiawei/RepoCodeLoc/swe-bench-lite \
    /home/jiawei/RepoCodeLoc/tools/GetTestHistory/dynamic_scikit-learn_call_graph \
    historical_information
```


## 输出结构

### 目录结构

```
historical_information/
├── scikit-learn__scikit-learn-10297.json
├── scikit-learn__scikit-learn-25638.json
└── ...

logs/
├── scikit-learn__scikit-learn-10297.log
├── scikit-learn__scikit-learn-25638.log
└── ...
```

### 输出JSON格式

每个实例的JSON文件包含以下结构:

```json
{
  "test_function_name": {
    "test_function": "sklearn/tests/test_isotonic.py::test_isotonic_regression_ties_min",
    "covered_entities": [...],
    
    "co_modifications": [
      {
        "commit_hash": "abc123",
        "timestamp": "2023-01-15T10:30:00Z",
        "modified_entities": [...],
        "commit_message": "fix tie-breaking in isotonic regression",
        "commit_type": "fix"
      }
    ],
    
    "test_modification_history": [...],
    
    "co_occurrence_timeline": {
      "entity_name": {
        "first_co_modification": "2022-06-01T09:00:00Z",
        "is_initial_coverage": true,
        "co_modification_count": 3
      }
    },
    
    "modification_groups": [...],
    
    "statistics": {
      "total_test_modifications": 5,
      "total_co_modifications": 8,
      "co_modified_entities_count": 10,
      "avg_modification_group_size": 3.2,
      "core_entities_count": 5,
      "extended_entities_count": 5
    }
  }
}
```

## 实现细节

### 核心组件

1. **PythonEntityExtractor**: 使用Python AST解析代码,提取类、方法、函数实体
2. **CommitAnalyzer**: 分析commit message,提取commit类型
3. **GitRepoManager**: 管理Git仓库操作,包括克隆、历史查询、diff分析
4. **HistoricalInfoCollector**: 主收集器,协调各组件完成数据收集

### 工作流程

1. 加载SWE-bench实例和测试覆盖图
2. 克隆对应的Git仓库到base_commit
3. 获取测试文件的commit历史
4. 对每个commit:
   - 分析修改的文件和代码行
   - 使用AST提取被修改的实体
   - 识别测试与覆盖实体的共同修改
   - 记录时间线和分组信息
5. 计算统计指标并保存结果

### 实体识别

使用Python AST识别代码实体:
- 顶层函数: `file.py::function_name`
- 类: `file.py::ClassName`
- 方法: `file.py::ClassName.method_name`

### Commit类型提取

通过正则表达式从commit message中提取类型:
- `fix`: 修复bug
- `feat`: 新功能
- `refactor`: 重构
- `test`: 测试相关
- `docs`: 文档
- `style`: 代码风格
- `perf`: 性能优化
- `chore`: 构建/CI等

## 日志说明

每个实例都有独立的日志文件,记录:
- 仓库克隆状态
- Commit分析进度
- 实体提取结果
- 统计信息
- 错误和警告

日志级别:
- INFO: 关键进度信息(控制台和文件)
- DEBUG: 详细调试信息(仅文件)
- WARNING: 警告信息
- ERROR: 错误信息

## 注意事项

1. **网络要求**: 需要网络连接以从GitHub克隆仓库
2. **磁盘空间**: 每个仓库会临时克隆到系统临时目录,需要足够磁盘空间
3. **处理时间**: 每个实例的处理时间取决于仓库大小和commit历史长度
4. **错误处理**: 如果某个实例处理失败,会记录错误并继续处理下一个

## 性能优化建议

1. 使用SSD存储临时仓库
2. 确保稳定的网络连接
3. 可以修改代码实现并行处理多个实例
4. 已处理的实例会跳过(检查输出文件是否存在)

## 故障排查

### 常见问题

1. **Git命令失败**
   - 确保系统已安装Git
   - 检查网络连接和GitHub访问

2. **AST解析失败**
   - 某些非标准Python代码可能无法解析
   - 查看日志中的WARNING信息

3. **内存不足**
   - 大型仓库可能消耗大量内存
   - 考虑增加系统内存或分批处理

### 调试方法

查看日志文件获取详细信息:
```bash
tail -f logs/<instance_id>.log
```
