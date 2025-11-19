# Introduction

在swe-bench like任务和数据集上，进行一系列数据处理操作的工具。

# Landing

- Dynamic_Coverage_Map
- GetGTTests
- show_data
- Statistic_Coverage_Map (TODO)
- SWEStastics


## Dynamic_Coverage_Map

**重要！**

动态获取tests的执行覆盖路径，是test-driven localization的基石

## GetGTTests

得到swe-bench每一个instance的ground truth locations和ground truth tests

- ground truth locations
    - issue对应的patches修改了哪些locations，哪些就是ground truth locations

- ground truth tests
    - repo中已有的，覆盖了ground truth locations的tests，即为ground truth tests
    - 依赖`Dynamic_Coverage_Map`阶段提供的test_coverage_information

## show_data

展示ground truth locations和predicted locations

## Statistic_Coverage_Map (TODO)

动态获取tests的执行覆盖路径。

- 预期将使用静态分析工具开发（ast，或者CodeQL等）
- 目前尚未完成


## SWEStastics

对swe-bench proj进行某些统计，为论文后续的调研提供基础