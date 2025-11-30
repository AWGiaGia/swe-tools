#!/usr/bin/env python3
"""
收集测试函数的历史编辑信息
功能包括:
1. 测试与被测代码的共同修改记录
2. commit元信息(message, type)
3. 修改的时间线与频率
4. 修改的原子性分组
"""

import os
import sys
import json
import re
import ast
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from datetime import datetime
from collections import defaultdict
import subprocess

from datasets import load_dataset


class PythonEntityExtractor:
    """从Python代码修改中提取实体信息"""
    
    @staticmethod
    def parse_file(file_path: str) -> Dict[str, Set[str]]:
        """
        解析Python文件，提取所有实体(函数、方法、类)
        返回: {line_number: entity_name}的映射
        """
        entities = {}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            
            # # 尝试使用Python 3语法解析
            # try:
            #     tree = ast.parse(source)
            # except SyntaxError:
            #     # 如果失败，尝试使用Python 2兼容模式
            #     # 将print语句转换为print函数
            #     import re
            #     # 简单的print语句转换（处理常见情况）
            #     source_py3 = re.sub(r'\bprint\s+([^(])', r'print(\1)', source)
            #     try:
            #         tree = ast.parse(source_py3)
            #     except SyntaxError:
            #         # 仍然失败，跳过该文件
            #         logging.debug(f"Skipping file with incompatible syntax: {file_path}")
            #         return entities
            
            # 尝试使用Python 3语法解析
            try:
                tree = ast.parse(source)
            except SyntaxError as e:
                # 如果失败，尝试使用Python 2兼容模式
                logging.debug(f"Python 3 parse failed for {file_path}, trying Python 2 compatibility mode")
                # 将print语句转换为print函数
                import re
                # 简单的print语句转换（处理常见情况）
                source_py3 = re.sub(r'\bprint\s+([^(])', r'print(\1)', source)
                try:
                    tree = ast.parse(source_py3)
                    logging.debug(f"Successfully parsed {file_path} with Python 2 compatibility mode")
                except SyntaxError:
                    # 仍然失败，跳过该文件
                    logging.debug(f"Skipping file with incompatible syntax: {file_path}")
                    return entities

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # 类定义
                    entity_name = node.name
                    entities[node.lineno] = entity_name
                    
                    # 类中的方法
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef):
                            method_name = f"{entity_name}.{item.name}"
                            entities[item.lineno] = method_name
                            
                elif isinstance(node, ast.FunctionDef) and node.col_offset == 0:
                    # 顶层函数
                    entities[node.lineno] = node.name
                    
        except Exception as e:
            logging.warning(f"Failed to parse {file_path}: {e}")
            
        return entities
    
    @staticmethod
    def find_entity_at_line(entities: Dict[int, str], line_num: int) -> Optional[str]:
        """找到给定行号对应的实体"""
        # 找到小于等于line_num的最大行号
        valid_lines = [l for l in entities.keys() if l <= line_num]
        if not valid_lines:
            return None
        closest_line = max(valid_lines)
        return entities[closest_line]


class CommitAnalyzer:
    """分析Git commit信息"""
    
    COMMIT_TYPE_PATTERNS = {
        'fix': r'\b(fix|fixed|fixes|bugfix|bug)\b',
        'feat': r'\b(feat|feature|add|added|new)\b',
        'refactor': r'\b(refactor|refactoring|restructure)\b',
        'test': r'\b(test|tests|testing)\b',
        'docs': r'\b(doc|docs|documentation)\b',
        'style': r'\b(style|format|formatting)\b',
        'perf': r'\b(perf|performance|optimize)\b',
        'chore': r'\b(chore|build|ci|release)\b',
    }
    
    @staticmethod
    def extract_commit_type(message: str) -> str:
        """从commit message中提取类型"""
        message_lower = message.lower()
        
        for commit_type, pattern in CommitAnalyzer.COMMIT_TYPE_PATTERNS.items():
            if re.search(pattern, message_lower):
                return commit_type
        
        return 'other'


class GitRepoManager:
    """管理Git仓库操作"""
    
    def __init__(self, temp_dir: str):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
    
    def clone_repo(self, repo_url: str, commit_hash: str) -> Optional[Path]:
        """克隆仓库到指定commit"""
        repo_name = repo_url.split('/')[-1].replace('.git', '')
        repo_path = self.temp_dir / repo_name
        
        if repo_path.exists():
            shutil.rmtree(repo_path)
        
        try:
            # 克隆仓库
            subprocess.run(
                ['git', 'clone', f'https://github.com/{repo_url}.git', str(repo_path)],
                check=True,
                capture_output=True,
                timeout=300
            )
            
            # 切换到指定commit
            subprocess.run(
                ['git', 'checkout', commit_hash],
                cwd=repo_path,
                check=True,
                capture_output=True
            )
            
            return repo_path
            
        except Exception as e:
            logging.error(f"Failed to clone repo {repo_url}: {e}")
            return None
    
    def get_commit_history(self, repo_path: Path, file_path: str, 
                        since_commit: Optional[str] = None) -> List[Dict]:
        """获取文件的commit历史"""
        try:
            # 基础命令:获取文件的完整历史(--follow追踪重命名)
            cmd = ['git', 'log', '--follow', '--format=%H|%at|%s', '--', file_path]
            
            # 注意:不使用since_commit参数,因为我们已经checkout到base_commit
            # 直接获取从初始到当前HEAD(即base_commit)的所有历史
            
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            
            commits = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split('|', 2)
                if len(parts) == 3:
                    commit_hash, timestamp, message = parts
                    commits.append({
                        'commit_hash': commit_hash,
                        'timestamp': datetime.fromtimestamp(int(timestamp)).isoformat() + 'Z',
                        'commit_message': message,
                        'commit_type': CommitAnalyzer.extract_commit_type(message)
                    })
            
            if not commits:
                logging.warning(f"No commit history found for {file_path} - file may not exist at base_commit")
            
            return commits
            
        except subprocess.CalledProcessError as e:
            logging.warning(f"Git log failed for {file_path}: {e.stderr}")
            return []
        except Exception as e:
            logging.warning(f"Failed to get commit history for {file_path}: {e}")
            return []
    
    def get_commit_diff(self, repo_path: Path, commit_hash: str) -> Dict[str, List[Tuple[int, int]]]:
        """
        获取commit的diff信息
        返回: {file_path: [(start_line, end_line), ...]}
        """
        try:
            result = subprocess.run(
                ['git', 'diff', f'{commit_hash}^', commit_hash, '--unified=0'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            
            modified_files = {}
            current_file = None
            
            for line in result.stdout.split('\n'):
                # 解析文件路径
                if line.startswith('+++'):
                    file_match = re.match(r'\+\+\+ b/(.+)', line)
                    if file_match:
                        current_file = file_match.group(1)
                        if current_file not in modified_files:
                            modified_files[current_file] = []
                
                # 解析修改的行号
                elif line.startswith('@@') and current_file:
                    # 格式: @@ -old_start,old_count +new_start,new_count @@
                    match = re.search(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
                    if match:
                        start = int(match.group(1))
                        count = int(match.group(2)) if match.group(2) else 1
                        modified_files[current_file].append((start, start + count - 1))
            
            return modified_files
            
        except Exception as e:
            logging.warning(f"Failed to get diff for commit {commit_hash}: {e}")
            return {}
    
    def get_files_in_commit(self, repo_path: Path, commit_hash: str) -> List[str]:
        """获取commit中修改的所有文件"""
        try:
            result = subprocess.run(
                ['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', commit_hash],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            
            return [f for f in result.stdout.strip().split('\n') if f]
            
        except Exception as e:
            logging.warning(f"Failed to get files in commit {commit_hash}: {e}")
            return []


class HistoricalInfoCollector:
    """收集测试函数的历史信息"""
    
    def __init__(self, output_dir: str, log_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.git_manager = GitRepoManager(tempfile.gettempdir() + '/swe_bench_repos')
        self.entity_extractor = PythonEntityExtractor()
    
    def setup_logger(self, instance_id: str) -> logging.Logger:
        """为每个instance设置独立的logger"""
        logger = logging.getLogger(instance_id)
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        
        # 文件handler
        fh = logging.FileHandler(self.log_dir / f'{instance_id}.log', mode='w')
        fh.setLevel(logging.DEBUG)
        
        # 控制台handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        
        # 格式
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        
        logger.addHandler(fh)
        logger.addHandler(ch)
        
        return logger
    
    def extract_entities_from_diff(self, repo_path: Path, commit_hash: str, 
                                file_path: str, modified_lines: List[Tuple[int, int]]) -> Set[str]:
        """从diff中提取被修改的实体"""
        entities = set()
        
        # 记录当前commit，确保最后能恢复
        current_commit = None
        
        try:
            # 先记录当前所在的commit
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            current_commit = result.stdout.strip()
            
            # 检出到commit前的状态
            subprocess.run(
                ['git', 'checkout', f'{commit_hash}^', '--quiet'],
                cwd=repo_path,
                capture_output=True,
                check=True
            )
            
            full_path = repo_path / file_path
            if not full_path.exists() or not file_path.endswith('.py'):
                return entities
            
            # 解析文件
            entity_map = self.entity_extractor.parse_file(str(full_path))
            
            # 找到修改的实体
            for start, end in modified_lines:
                for line_num in range(start, end + 1):
                    entity = self.entity_extractor.find_entity_at_line(entity_map, line_num)
                    if entity:
                        entities.add(f"{file_path}::{entity}")
            
        except Exception as e:
            logging.warning(f"Failed to extract entities from {file_path}: {e}")
        
        finally:
            # 无论成功失败，都要恢复到原来的commit
            if current_commit:
                try:
                    subprocess.run(
                        ['git', 'checkout', current_commit, '--quiet'],
                        cwd=repo_path,
                        capture_output=True,
                        check=True
                    )
                except Exception as e:
                    logging.error(f"Failed to restore commit {current_commit}: {e}")
        
        return entities


    def collect_for_test(self, logger: logging.Logger, repo_path: Path, 
                        test_function: str, covered_entities: List[str],
                        base_commit: str) -> Dict:
        """收集单个测试函数的历史信息"""
        logger.info(f"Collecting history for test: {test_function}")
        
        # 确保当前在base_commit
        try:
            subprocess.run(
                ['git', 'checkout', base_commit, '--quiet'],
                cwd=repo_path,
                capture_output=True,
                check=True
            )
            logger.debug(f"Ensured HEAD is at base_commit: {base_commit}")
        except Exception as e:
            logger.error(f"Failed to checkout base_commit: {e}")
            return result


        result = {
            'test_function': test_function,
            'covered_entities': covered_entities,
            'co_modifications': [],
            'test_modification_history': [],
            'co_occurrence_timeline': {},
            'modification_groups': [],
            'statistics': {
                'total_test_modifications': 0,
                'total_co_modifications': 0,
                'co_modified_entities_count': 0,
                'avg_modification_group_size': 0.0,
                'core_entities_count': 0,
                'extended_entities_count': 0
            }
        }
        
        # 解析测试函数路径
        test_file = test_function.split('::')[0]

        # 先检查文件是否存在
        test_file_full_path = repo_path / test_file
        if not test_file_full_path.exists():
            logger.warning(f"Test file does not exist at base_commit: {test_file}")
            
            # 诊断信息
            try:
                result_head = subprocess.run(
                    ['git', 'rev-parse', 'HEAD'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    check=True
                )
                current_commit = result_head.stdout.strip()
                logger.warning(f"Current HEAD: {current_commit}, Expected: {base_commit}")
                
                # 检查文件是否被git追踪
                result_ls = subprocess.run(
                    ['git', 'ls-files', test_file],
                    cwd=repo_path,
                    capture_output=True,
                    text=True
                )
                if result_ls.stdout.strip():
                    logger.warning(f"File IS tracked by git: {test_file}")
                else:
                    logger.warning(f"File is NOT tracked by git at current commit")
                
                # 查找文件第一次被添加的commit
                result_log = subprocess.run(
                    ['git', 'log', '--diff-filter=A', '--format=%H|%ai', '--', test_file],
                    cwd=repo_path,
                    capture_output=True,
                    text=True
                )
                if result_log.stdout.strip():
                    first_commit_line = result_log.stdout.strip().split('\n')[0]
                    logger.warning(f"File first added in commit: {first_commit_line}")
                else:
                    logger.warning(f"No commit found that added this file")
                    
            except Exception as e:
                logger.error(f"Failed to diagnose: {e}")
            
            # 文件不存在，返回空结果
            return result
        
        logger.debug(f"Test file exists: {test_file}")

        # 获取测试文件的commit历史（只调用一次）
        test_commits = self.git_manager.get_commit_history(repo_path, test_file)
        logger.info(f"Found {len(test_commits)} commits for test file")
        
        result['test_modification_history'] = test_commits
        result['statistics']['total_test_modifications'] = len(test_commits)
        
        # 创建covered entities的集合用于快速查找
        covered_set = set(covered_entities)
        
        # 分析每个commit
        first_test_commit_time = None
        if test_commits:
            first_test_commit_time = test_commits[-1]['timestamp']  # 最早的commit
        
        co_modified_entities = set()
        
        for commit_info in test_commits:
            commit_hash = commit_info['commit_hash']
            logger.debug(f"Analyzing commit: {commit_hash}")
            
            # 获取该commit修改的所有文件
            modified_files_in_commit = self.git_manager.get_files_in_commit(repo_path, commit_hash)
            
            # 获取diff信息
            file_diffs = self.git_manager.get_commit_diff(repo_path, commit_hash)
            
            # 提取该commit中修改的所有实体
            modified_entities_in_commit = set()
            
            for file_path, line_ranges in file_diffs.items():
                if not file_path.endswith('.py'):
                    continue
                
                entities = self.extract_entities_from_diff(
                    repo_path, commit_hash, file_path, line_ranges
                )
                modified_entities_in_commit.update(entities)
            
            # 只保留在covered_entities中的实体
            covered_modified = modified_entities_in_commit & covered_set
            
            if test_function in modified_entities_in_commit or test_file in modified_files_in_commit:
                # 这是一个包含测试函数的commit
                if covered_modified:
                    # 测试与覆盖的实体共同修改
                    co_mod_record = {
                        'commit_hash': commit_hash,
                        'timestamp': commit_info['timestamp'],
                        'modified_entities': sorted(list(covered_modified | {test_function})),
                        'commit_message': commit_info['commit_message'],
                        'commit_type': commit_info['commit_type']
                    }
                    result['co_modifications'].append(co_mod_record)
                    
                    # 记录修改组
                    result['modification_groups'].append({
                        'commit_hash': commit_hash,
                        'timestamp': commit_info['timestamp'],
                        'commit_message': commit_info['commit_message'],
                        'commit_type': commit_info['commit_type'],
                        'entities_modified_together': sorted(list(covered_modified | {test_function})),
                        'group_size': len(covered_modified) + 1
                    })
                    
                    # 更新共现时间线
                    for entity in covered_modified:
                        if entity not in result['co_occurrence_timeline']:
                            result['co_occurrence_timeline'][entity] = {
                                'first_co_modification': commit_info['timestamp'],
                                'is_initial_coverage': (commit_info['timestamp'] == first_test_commit_time),
                                'co_modification_count': 0
                            }
                        result['co_occurrence_timeline'][entity]['co_modification_count'] += 1
                    
                    co_modified_entities.update(covered_modified)
        
        # 计算统计信息
        result['statistics']['total_co_modifications'] = len(result['co_modifications'])
        result['statistics']['co_modified_entities_count'] = len(co_modified_entities)
        
        if result['modification_groups']:
            avg_size = sum(g['group_size'] for g in result['modification_groups']) / len(result['modification_groups'])
            result['statistics']['avg_modification_group_size'] = round(avg_size, 2)
        
        result['statistics']['core_entities_count'] = sum(
            1 for info in result['co_occurrence_timeline'].values() 
            if info['is_initial_coverage']
        )
        result['statistics']['extended_entities_count'] = sum(
            1 for info in result['co_occurrence_timeline'].values() 
            if not info['is_initial_coverage']
        )
        
        logger.info(f"Completed collection for {test_function}")
        logger.info(f"Statistics: {result['statistics']}")
        
        return result


    def process_instance(self, instance: Dict, coverage_graph: Dict):
        """处理单个SWE-bench实例"""
        instance_id = instance['instance_id']
        logger = self.setup_logger(instance_id)
        
        logger.info(f"{'='*80}")
        logger.info(f"Processing instance: {instance_id}")
        logger.info(f"{'='*80}")
        
        try:
            # 克隆仓库
            repo = instance['repo']
            base_commit = instance['base_commit']
            
            logger.info(f"Cloning repository: {repo}")
            logger.info(f"Base commit: {base_commit}")
            
            repo_path = self.git_manager.clone_repo(repo, base_commit)
            if not repo_path:
                logger.error("Failed to clone repository")
                return
            
            logger.info(f"Repository cloned to: {repo_path}")
            
            # 处理每个测试函数
            results = {}
            
            for test_function, test_data in coverage_graph.items():
                covered_entities = test_data['nodes']
                
                test_result = self.collect_for_test(
                    logger, repo_path, test_function, covered_entities, base_commit
                )
                
                results[test_function] = test_result
            
            # 保存结果
            output_file = self.output_dir / f'{instance_id}.json'
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Results saved to: {output_file}")
            logger.info(f"Successfully processed {len(results)} test functions")
            
        except Exception as e:
            logger.error(f"Error processing instance {instance_id}: {e}", exc_info=True)
        
        finally:
            # 清理
            logger.info("Cleaning up...")


def main(swe_bench_path: str, coverage_graph_path: str, output_dir: str = 'historical_information'):
    """主函数"""
    print("="*80)
    print("Historical Information Collection Tool")
    print("="*80)
    
    # 创建输出目录
    log_dir = 'logs'
    collector = HistoricalInfoCollector(output_dir, log_dir)
    
    # 加载SWE-bench数据
    print(f"\nLoading SWE-bench data from: {swe_bench_path}")
    swe_bench_data = load_dataset(swe_bench_path)
    
    # 获取测试集
    test_data = swe_bench_data['test']
    print(f"Loaded {len(test_data)} instances")
    
    # 加载coverage graphs
    coverage_graph_dir = Path(coverage_graph_path)
    
    # 处理每个实例
    processed = 0
    for instance in test_data:
        instance_id = instance['instance_id']
        coverage_file = coverage_graph_dir / f'{instance_id}.json'
        
        if not coverage_file.exists():
            print(f"\nSkipping {instance_id}: coverage graph not found")
            continue
        
        # 加载coverage graph
        with open(coverage_file, 'r') as f:
            coverage_graph = json.load(f)
        
        print(f"\n[{processed + 1}/{len(test_data)}] Processing: {instance_id}")
        collector.process_instance(instance, coverage_graph)
        processed += 1
    
    print(f"\n{'='*80}")
    print(f"Processing complete!")
    print(f"Processed: {processed} instances")
    print(f"Results saved to: {output_dir}/")
    print(f"Logs saved to: {log_dir}/")
    print(f"{'='*80}")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python collect_historical_info.py <swe_bench_path> <coverage_graph_path> [output_dir]")
        sys.exit(1)
    
    swe_bench_path = sys.argv[1]
    coverage_graph_path = sys.argv[2]
    output_dir = sys.argv[3] if len(sys.argv) > 3 else 'historical_information'
    
    main(swe_bench_path, coverage_graph_path, output_dir)
