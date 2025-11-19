#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Python项目统计工具
统计项目中的.py文件数、代码行数、类数量和函数数量
"""

import os
import ast
from pathlib import Path


class PythonProjectStats:
    def __init__(self, project_path):
        self.project_path = Path(project_path)
        self.py_files = []
        self.total_lines = 0
        self.total_classes = 0
        self.total_functions = 0
        
    def find_py_files(self):
        """递归查找所有.py文件"""
        for root, dirs, files in os.walk(self.project_path):
            # 跳过常见的虚拟环境和缓存目录
            dirs[:] = [d for d in dirs if d not in ['venv', '.venv', 'env', '__pycache__', '.git', 'node_modules']]
            
            for file in files:
                if file.endswith('.py'):
                    self.py_files.append(os.path.join(root, file))
    
    def count_lines(self, file_path):
        """统计文件的代码行数（包括空行和注释）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return len(f.readlines())
        except Exception as e:
            print(f"警告: 无法读取文件 {file_path}: {e}")
            return 0
    
    def count_classes_and_functions(self, file_path):
        """使用AST解析文件，统计类和函数数量"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            tree = ast.parse(content, filename=file_path)
            
            classes = 0
            functions = 0
            
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    classes += 1
                elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                    functions += 1
            
            return classes, functions
        
        except SyntaxError as e:
            print(f"警告: 语法错误在文件 {file_path}: {e}")
            return 0, 0
        except Exception as e:
            print(f"警告: 无法解析文件 {file_path}: {e}")
            return 0, 0
    
    def analyze(self):
        """执行完整分析"""
        print(f"正在分析项目: {self.project_path}")
        print("-" * 60)
        
        # 查找所有.py文件
        self.find_py_files()
        
        if not self.py_files:
            print("错误: 未找到任何.py文件")
            return
        
        # 分析每个文件
        for py_file in self.py_files:
            lines = self.count_lines(py_file)
            classes, functions = self.count_classes_and_functions(py_file)
            
            self.total_lines += lines
            self.total_classes += classes
            self.total_functions += functions
        
        # 打印结果
        self.print_results()
    
    def print_results(self):
        """打印统计结果"""
        print(f"\n{'='*60}")
        print(f"项目统计结果")
        print(f"{'='*60}")
        print(f"1) .py文件数量:     {len(self.py_files)}")
        print(f"2) 总代码行数:       {self.total_lines:,}")
        print(f"3) 类(Class)数量:    {self.total_classes}")
        print(f"4) 函数数量:         {self.total_functions}")
        print(f"   (包括类方法和独立函数)")
        print(f"{'='*60}")
        
        # 计算平均值
        if len(self.py_files) > 0:
            avg_lines = self.total_lines / len(self.py_files)
            print(f"\n平均每个文件: {avg_lines:.1f} 行")
        
        # 可选：显示前10个最大的文件
        if len(self.py_files) > 0:
            print(f"\n文件列表示例 (前10个):")
            for i, file in enumerate(self.py_files[:10], 1):
                rel_path = os.path.relpath(file, self.project_path)
                lines = self.count_lines(file)
                print(f"  {i}. {rel_path} ({lines} 行)")
            
            if len(self.py_files) > 10:
                print(f"  ... 还有 {len(self.py_files) - 10} 个文件")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='统计Python项目的代码信息')
    parser.add_argument('--project_path', 
                        nargs='?',
                        default='.',
                        help='项目路径 (默认为当前目录)')
    
    args = parser.parse_args()
    
    # 检查路径是否存在
    if not os.path.exists(args.project_path):
        print(f"错误: 路径不存在: {args.project_path}")
        return
    
    # 执行分析
    stats = PythonProjectStats(args.project_path)
    stats.analyze()


if __name__ == '__main__':
    main()