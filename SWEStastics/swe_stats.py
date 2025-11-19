#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SWE-Bench-Lite 项目统计工具 - 支持本地文件版本
可以从本地 JSON 文件或 HuggingFace 数据集读取
"""

import os
import ast
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime


class PythonProjectStats:
    def __init__(self, project_path, project_name=None):
        self.project_path = Path(project_path)
        self.project_name = project_name or self.project_path.name
        self.py_files = []
        self.total_lines = 0
        self.total_code_lines = 0
        self.total_classes = 0
        self.total_functions = 0
        
    def find_py_files(self):
        """递归查找所有.py文件"""
        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [d for d in dirs if d not in [
                'venv', '.venv', 'env', '__pycache__', '.git', 
                'node_modules', '.tox', '.pytest_cache', 'dist', 
                'build', 'eggs', '.eggs', '.mypy_cache'
            ]]
            
            for file in files:
                if file.endswith('.py'):
                    self.py_files.append(os.path.join(root, file))
    
    def count_lines(self, file_path):
        """统计文件的代码行数"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                total = len(lines)
                code_lines = sum(1 for line in lines if line.strip() and not line.strip().startswith('#'))
                return total, code_lines
        except Exception:
            return 0, 0
    
    def count_classes_and_functions(self, file_path):
        """使用AST解析文件，统计类和函数数量"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            tree = ast.parse(content, filename=file_path)
            classes = sum(1 for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
            functions = sum(1 for node in ast.walk(tree) 
                          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
            return classes, functions
        except (SyntaxError, Exception):
            return 0, 0
    
    def analyze(self):
        """执行完整分析"""
        self.find_py_files()
        if not self.py_files:
            return False
        
        for py_file in self.py_files:
            lines, code_lines = self.count_lines(py_file)
            classes, functions = self.count_classes_and_functions(py_file)
            self.total_lines += lines
            self.total_code_lines += code_lines
            self.total_classes += classes
            self.total_functions += functions
        
        return True
    
    def get_stats_dict(self):
        """返回统计结果的字典"""
        return {
            'py_files_count': len(self.py_files),
            'total_lines': self.total_lines,
            'total_code_lines': self.total_code_lines,
            'total_classes': self.total_classes,
            'total_functions': self.total_functions,
            'avg_lines_per_file': round(self.total_lines / len(self.py_files), 2) if self.py_files else 0,
            'avg_code_lines_per_file': round(self.total_code_lines / len(self.py_files), 2) if self.py_files else 0,
        }


class SWEBenchStatsCollector:
    def __init__(self, output_file='swe_bench_stats.json', cache_dir=None):
        self.output_file = output_file
        self.cache_dir = cache_dir or tempfile.mkdtemp(prefix='swe_bench_repos_')
        self.results = []
        
    def load_dataset_from_file(self, json_file):
        """从本地 JSON 文件加载数据"""
        print(f"正在从本地文件加载数据: {json_file}")
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 支持不同的 JSON 格式
            if isinstance(data, list):
                dataset = data
            elif isinstance(data, dict) and 'instances' in data:
                dataset = data['instances']
            elif isinstance(data, dict) and 'data' in data:
                dataset = data['data']
            else:
                dataset = [data]
            
            print(f"✓ 加载成功，共 {len(dataset)} 个实例")
            return dataset
        except Exception as e:
            print(f"✗ 加载文件失败: {e}")
            return None
    
    def load_dataset_from_huggingface(self):
        """从 HuggingFace 加载数据集"""
        print("正在从 HuggingFace 加载 SWE-Bench-Lite 数据集...")
        try:
            from datasets import load_dataset
            dataset = load_dataset('/home/jiawei/RepoCodeLoc/swe-bench-lite', split='test')
            print(f"✓ 加载成功，共 {len(dataset)} 个实例")
            return list(dataset)
        except Exception as e:
            print(f"✗ 加载数据集失败: {e}")
            return None
    
    def clone_repo_at_commit(self, repo_url, commit_hash, target_dir):
        """克隆仓库到指定commit"""
        try:
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            
            # 克隆仓库
            result = subprocess.run(
                ['git', 'clone', '--quiet', '--depth', '1', '--no-single-branch', 
                 repo_url, target_dir],
                check=True,
                capture_output=True,
                timeout=300,
                text=True
            )
            
            # 切换到指定commit
            subprocess.run(
                ['git', '-C', target_dir, 'fetch', '--quiet', '--depth', '1', 
                 'origin', commit_hash],
                check=True,
                capture_output=True,
                timeout=60,
                text=True
            )
            
            subprocess.run(
                ['git', '-C', target_dir, 'checkout', '--quiet', commit_hash],
                check=True,
                capture_output=True,
                timeout=60,
                text=True
            )
            
            return True
        except subprocess.TimeoutExpired:
            print(f"    ✗ 超时")
            return False
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            print(f"    ✗ Git错误: {error_msg[:100]}")
            return False
        except Exception as e:
            print(f"    ✗ 错误: {str(e)[:100]}")
            return False
    
    def process_instance(self, instance, save_path="results.jsonl"):
        """处理单个instance"""
        instance_id = instance.get('instance_id', 'unknown')
        repo = instance.get('repo', '')
        base_commit = instance.get('base_commit', '')
        
        if not repo or not base_commit:
            print(f"    ✗ 缺少 repo 或 base_commit")
            return None
        
        # 构建GitHub URL
        if not repo.startswith('http'):
            repo_url = f"https://github.com/{repo}.git"
        else:
            repo_url = repo
        
        # 创建临时目录
        temp_dir = os.path.join(self.cache_dir, instance_id.replace('/', '_'))
        
        print(f"  克隆: {repo} @ {base_commit[:8]}")
        
        # 克隆仓库
        if not self.clone_repo_at_commit(repo_url, base_commit, temp_dir):
            return None
        
        print(f"  分析代码...")
        
        # 统计代码
        stats = PythonProjectStats(temp_dir, instance_id)
        success = stats.analyze()
        
        if not success:
            print(f"    ✗ 未找到Python文件")
            return None
        
        result = {
            'instance_id': instance_id,
            'repo': repo,
            'base_commit': base_commit,
            **stats.get_stats_dict()
        }
        
        print(f"    ✓ 文件:{result['py_files_count']}, "
              f"行:{result['total_lines']:,}, "
              f"类:{result['total_classes']}, "
              f"函数:{result['total_functions']}")
        
        with open(save_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')


        # 清理临时目录
        try:
            shutil.rmtree(temp_dir)
        except:
            pass
        
        return result
    
    def analyze_all_instances(self, dataset):
        """分析所有实例"""
        if dataset is None:
            return
        
        print("\n" + "=" * 70)
        print(f"开始分析 {len(dataset)} 个实例")
        print("=" * 70)
        
        failed_instances = []
        
        for i, instance in enumerate(dataset, 1):
            instance_id = instance.get('instance_id', f'unknown_{i}')
            print(f"\n[{i}/{len(dataset)}] {instance_id}")
            
            result = self.process_instance(instance)
            
            if result:
                self.results.append(result)
                # 每10个保存一次
                if i % 10 == 0:
                    self.save_results(self.output_file)
                    print(f"  [已保存进度: {len(self.results)} 个实例]")
            else:
                failed_instances.append(instance_id)
        
        print("\n" + "=" * 70)
        print(f"完成! 成功: {len(self.results)}/{len(dataset)}")
        
        if failed_instances:
            print(f"\n失败的实例 ({len(failed_instances)}):")
            for fid in failed_instances[:10]:
                print(f"  - {fid}")
            if len(failed_instances) > 10:
                print(f"  ... 还有 {len(failed_instances) - 10} 个")
    
    def save_results(self, output_file=None):
        """保存结果到JSON文件"""
        output_file = output_file or self.output_file
        
        summary = {
            'timestamp': datetime.now().isoformat(),
            'total_instances': len(self.results),
            'total_py_files': sum(r['py_files_count'] for r in self.results),
            'total_lines': sum(r['total_lines'] for r in self.results),
            'total_code_lines': sum(r['total_code_lines'] for r in self.results),
            'total_classes': sum(r['total_classes'] for r in self.results),
            'total_functions': sum(r['total_functions'] for r in self.results),
        }
        
        output_data = {
            'summary': summary,
            'instances': self.results
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    def cleanup(self):
        """清理临时目录"""
        try:
            if os.path.exists(self.cache_dir):
                shutil.rmtree(self.cache_dir)
                print(f"✓ 已清理临时目录")
        except Exception as e:
            print(f"警告: 清理失败: {e}")
    
    def print_summary(self):
        """打印汇总统计"""
        if not self.results:
            return
        
        print("\n" + "=" * 70)
        print("汇总统计")
        print("=" * 70)
        print(f"总实例数:        {len(self.results)}")
        print(f"总.py文件数:     {sum(r['py_files_count'] for r in self.results):,}")
        print(f"总代码行数:      {sum(r['total_lines'] for r in self.results):,}")
        print(f"总有效代码行:    {sum(r['total_code_lines'] for r in self.results):,}")
        print(f"总类数量:        {sum(r['total_classes'] for r in self.results):,}")
        print(f"总函数数量:      {sum(r['total_functions'] for r in self.results):,}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='统计SWE-Bench-Lite实例的Python代码信息')
    parser.add_argument('-f', '--file', help='从本地JSON文件读取数据')
    parser.add_argument('-o', '--output', default='swe_bench_stats.json', 
                       help='输出文件 (默认: swe_bench_stats.json)')
    parser.add_argument('--cache-dir', help='临时缓存目录')
    parser.add_argument('--keep-cache', action='store_true', help='保留缓存')
    
    args = parser.parse_args()
    
    # 检查git
    try:
        subprocess.run(['git', '--version'], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("错误: 未找到git")
        return
    
    collector = SWEBenchStatsCollector(args.output, args.cache_dir)
    
    try:
        # 加载数据
        if args.file:
            dataset = collector.load_dataset_from_file(args.file)
        else:
            dataset = collector.load_dataset_from_huggingface()
        
        if dataset:
            collector.analyze_all_instances(dataset)
            collector.print_summary()
            collector.save_results()
            print(f"\n结果已保存到: {os.path.abspath(args.output)}")
    finally:
        if not args.keep_cache:
            collector.cleanup()
    
    print(f"\n✓ 完成!")


if __name__ == '__main__':
    main()