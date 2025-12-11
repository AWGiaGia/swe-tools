#!/usr/bin/env python3
"""
收集测试函数的历史编辑信息（优化版本）
功能包括:
1. 测试与被测代码的共同修改记录
2. commit元信息(message, type)
3. 修改的时间线与频率
4. 修改的原子性分组

优化内容:
- 方案一: 批量处理Git操作，使用git log -p一次性获取diff
- 方案二: 使用git show避免checkout切换
- 方案三: 并行处理多个instance
- 方案五: 缓存AST解析结果
- 方案六: 预筛选减少不必要分析
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
from functools import lru_cache
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

from datasets import load_dataset
from nltk.stem import PorterStemmer


class PythonEntityExtractor:
    """从Python代码修改中提取实体信息"""
    
    # 方案五: 类级别的缓存，用于存储解析结果
    _parse_cache: Dict[Tuple[str, str], Dict[int, str]] = {}
    
    @staticmethod
    def parse_source(source: str) -> Dict[int, str]:
        """
        解析Python源代码字符串，提取所有实体(函数、方法、类)
        返回: {line_number: entity_name}的映射
        """
        entities = {}
        
        try:
            # 尝试使用Python 3语法解析
            try:
                tree = ast.parse(source)
            except SyntaxError:
                # 如果失败，尝试使用Python 2兼容模式
                # 将print语句转换为print函数
                source_py3 = re.sub(r'\bprint\s+([^(])', r'print(\1)', source)
                try:
                    tree = ast.parse(source_py3)
                except SyntaxError:
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
                    
        except Exception:
            pass
            
        return entities
    
    @staticmethod
    def parse_file(file_path: str) -> Dict[int, str]:
        """
        解析Python文件，提取所有实体(函数、方法、类)
        返回: {line_number: entity_name}的映射
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            return PythonEntityExtractor.parse_source(source)
        except Exception as e:
            logging.warning(f"Failed to parse {file_path}: {e}")
            return {}
    
    @staticmethod
    def find_entity_at_line(entities: Dict[int, str], line_num: int) -> Optional[str]:
        """找到给定行号对应的实体"""
        # 找到小于等于line_num的最大行号
        valid_lines = [l for l in entities.keys() if l <= line_num]
        if not valid_lines:
            return None
        closest_line = max(valid_lines)
        return entities[closest_line]

    @staticmethod
    def extract_function_source(source: str, function_name: str) -> Optional[str]:
        """
        从源代码中提取特定函数的源代码
        """
        try:
            tree = ast.parse(source)
            lines = source.split('\n')
            
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == function_name:
                    start_line = node.lineno - 1
                    end_line = getattr(node, 'end_lineno', None)
                    
                    if end_line:
                        return '\n'.join(lines[start_line:end_line])
                    else:
                        # Python 3.7以下没有end_lineno，尝试估算
                        # 找到下一个同缩进级别的定义
                        indent = len(lines[start_line]) - len(lines[start_line].lstrip())
                        for i in range(start_line + 1, len(lines)):
                            line = lines[i]
                            if line.strip() and not line.startswith(' ' * (indent + 1)) and not line.startswith('\t'):
                                if line.strip().startswith('def ') or line.strip().startswith('class '):
                                    return '\n'.join(lines[start_line:i])
                        return '\n'.join(lines[start_line:])
            return None
        except:
            return None
    
    @staticmethod
    def normalize_function_ast(func_source: str) -> Optional[str]:
        """
        将函数源代码规范化为AST字符串表示，用于语义比较
        """
        try:
            tree = ast.parse(func_source)
            # 使用ast.dump获取规范化表示，忽略行号等位置信息
            return ast.dump(tree, annotate_fields=False)
        except SyntaxError:
            return None
    
    @staticmethod
    def is_semantic_change(old_source: str, new_source: str, function_name: str) -> bool:
        """
        判断函数的修改是否是语义上的修改
        返回True表示是语义修改，False表示只是格式/注释修改
        """
        old_func = PythonEntityExtractor.extract_function_source(old_source, function_name)
        new_func = PythonEntityExtractor.extract_function_source(new_source, function_name)
        
        if old_func is None and new_func is None:
            # 无法在两个版本中找到函数
            # 可能原因：1) AST解析失败（如Python 2语法）2) 函数名匹配问题
            # 保守处理：认为是语义修改，避免错误过滤重要commit
            logging.debug(f"    Function '{function_name}' not found in both old and new source, assuming semantic change (conservative)")
            return True
        if old_func is None:
            logging.debug(f"    Function '{function_name}' not found in old source (newly added)")
            return True  # 函数新建，是语义修改
        if new_func is None:
            logging.debug(f"    Function '{function_name}' not found in new source (deleted)")
            return True  # 函数删除，是语义修改
        
        old_ast = PythonEntityExtractor.normalize_function_ast(old_func)
        new_ast = PythonEntityExtractor.normalize_function_ast(new_func)
        
        if old_ast is None or new_ast is None:
            logging.debug(f"    AST parsing failed for '{function_name}', falling back to text comparison")
            # AST解析失败，回退到简单比较（去除空白和注释）
            old_clean = re.sub(r'#.*$', '', old_func, flags=re.MULTILINE)
            old_clean = re.sub(r'\s+', ' ', old_clean).strip()
            new_clean = re.sub(r'#.*$', '', new_func, flags=re.MULTILINE)
            new_clean = re.sub(r'\s+', ' ', new_clean).strip()
            is_different = old_clean != new_clean
            if not is_different:
                logging.debug(f"    Text comparison: no semantic change (whitespace/comment only)")
            return is_different
        
        is_different = old_ast != new_ast
        if not is_different:
            logging.debug(f"    AST comparison: no semantic change (format/comment only)")
        else:
            logging.debug(f"    AST comparison: semantic change detected")
        
        return is_different


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
    
    # 应该跳过的 commit message 模式（纯格式/风格修改，不涉及语义变化）
    SKIP_PATTERNS = [
        r'\btypo(s|fix(es)?|graphical\s+error)?\b',  # typo, typos, typofix, typofixes
        r'\bpep\s?8\b',                              # pep8, PEP8, pep 8
        r'\bflake\s?8\b',                            # flake8
        r'\bwhitespace(s)?\b',                       # whitespace, whitespaces
        r'\bindent(s|ed|ing|ation)?\b',              # indent, indentation...
        r'\bcosm[ei]tic(s|al)?\b',                   # cosmetic, cosmit...
        r'\bnit(pick|s)?\b',                         # nit, nitpick
        r'\blint(s|ed|ing|er)?\b',                   # lint, linting, linter
        r'\bspelling\b',                             # spelling
    ]
    
    @staticmethod
    def extract_commit_type(message: str) -> str:
        """从commit message中提取类型"""
        message_lower = message.lower()
        
        for commit_type, pattern in CommitAnalyzer.COMMIT_TYPE_PATTERNS.items():
            if re.search(pattern, message_lower):
                return commit_type
        
        return 'other'
    
    @staticmethod
    def should_skip_commit(message: str) -> bool:
        """
        判断是否应该跳过该 commit（基于 commit message）
        返回 True 表示应该跳过（纯格式/风格修改或信息量很低）
        """
        message_lower = message.lower()
        
        # 1) 纯格式/风格类修改
        if any(re.search(pattern, message_lower) for pattern in CommitAnalyzer.SKIP_PATTERNS):
            return True
        
        # 2) 只包含通用词汇的信息量过低的commit message
        if CommitAnalyzer.is_low_information_message(message):
            return True
        
        return False


    @staticmethod
    def is_low_information_message(message: str) -> bool:
        """
        判断commit message是否信息量很低（只包含通用词汇等）
        返回True表示信息量过低,可以视为噪声commit
        """
        # 初始化词干提取器
        stemmer = PorterStemmer()

        msg = message.strip().lower()
        if not msg:
            return True

        # 去掉标点后按空白分词
        msg_clean = re.sub(r'[^a-z0-9\s]', ' ', msg)
        tokens = [t for t in msg_clean.split() if t]

        if not tokens:
            return True

        # 提取每个词的词干
        stems = [stemmer.stem(token) for token in tokens]

        # 通用的、信息量低的词干
        generic_single_words = {
            'init', 'initi', 'updat', 'chang', 'refactor', 'cleanup', 'clean', 'fix', 
            'fixes', 'fixing', 'wip', 'temp', 'test', 'typofix', 'typofix', 'minor', 'small'
        }

        generic_words = generic_single_words | {
            'code', 'stuff', 'refactor', 'fixing', 'typofix', 'update', 'test'
        }

        # 情况1: 如果只有一个词并且是通用词干，则视为低信息
        if len(stems) == 1 and stems[0] in generic_single_words:
            return True

        # 情况2: 如果词数小于等于3且所有词都属于通用词干集合，则视为低信息
        if len(stems) <= 3 and all(t in generic_words for t in stems):
            return True

        return False




class GitRepoManager:
    """管理Git仓库操作"""
    
    def __init__(self, temp_dir: str, instance_id: str = None):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        # 用于区分不同instance的仓库路径
        self.instance_id = instance_id
        # 方案五: 缓存文件内容解析结果
        self._file_content_cache: Dict[Tuple[str, str], str] = {}
        self._entity_cache: Dict[Tuple[str, str], Dict[int, str]] = {}
    
    def clone_repo(self, repo_url: str, commit_hash: str) -> Optional[Path]:
        """克隆仓库到指定commit"""
        repo_name = repo_url.split('/')[-1].replace('.git', '')
        # 并行处理时，为每个instance使用独立的目录
        if self.instance_id:
            repo_name = f"{repo_name}_{self.instance_id}"
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
                        since_commit: Optional[str] = None,
                        filter_non_semantic: bool = True) -> List[Dict]:
        """
        获取文件的commit历史
        
        Args:
            repo_path: 仓库路径
            file_path: 文件路径
            since_commit: 起始commit（可选）
            filter_non_semantic: 是否过滤纯格式/风格修改的commit（默认True）
        """
        try:
            cmd = ['git', 'log', '--follow', '--format=%H|%at|%s', '--', file_path]
            
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            
            commits = []
            skipped_count = 0
            
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split('|', 2)
                if len(parts) == 3:
                    commit_hash, timestamp, message = parts
                    
                    # 根据 commit message 过滤纯格式/风格修改
                    if filter_non_semantic and CommitAnalyzer.should_skip_commit(message):
                        skipped_count += 1
                        logging.debug(f"Skipping commit {commit_hash[:8]} by message filter: {message[:60]}")
                        continue
                    
                    commits.append({
                        'commit_hash': commit_hash,
                        'timestamp': datetime.fromtimestamp(int(timestamp)).isoformat() + 'Z',
                        'commit_message': message,
                        'commit_type': CommitAnalyzer.extract_commit_type(message)
                    })
            
            if skipped_count > 0:
                logging.debug(f"Filtered {skipped_count} non-semantic commits for {file_path}")
            
            if not commits:
                logging.warning(f"No commit history found for {file_path}")
            
            return commits
            
        except subprocess.CalledProcessError as e:
            logging.warning(f"Git log failed for {file_path}: {e.stderr}")
            return []
        except Exception as e:
            logging.warning(f"Failed to get commit history for {file_path}: {e}")
            return []


    # 方案一: 批量获取commit的diff信息
    def get_batch_commit_diffs(self, repo_path: Path, commit_hashes: List[str]) -> Dict[str, Dict[str, List[Tuple[int, int]]]]:
        """
        批量获取多个commit的diff信息
        返回: {commit_hash: {file_path: [(start_line, end_line), ...]}}
        """
        if not commit_hashes:
            return {}
        
        result = {}
        
        # 使用git show批量获取diff，每次处理一批以避免命令行过长
        batch_size = 50
        for i in range(0, len(commit_hashes), batch_size):
            batch = commit_hashes[i:i + batch_size]
            
            for commit_hash in batch:
                try:
                    diff_result = subprocess.run(
                        ['git', 'diff', f'{commit_hash}^', commit_hash, '--unified=0'],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    
                    if diff_result.returncode != 0:
                        continue
                    
                    result[commit_hash] = self._parse_diff_output(diff_result.stdout)
                    
                except Exception as e:
                    logging.debug(f"Failed to get diff for commit {commit_hash}: {e}")
                    result[commit_hash] = {}
        
        return result
    
    def _parse_diff_output(self, diff_output: str) -> Dict[str, List[Tuple[int, int]]]:
        """解析git diff输出"""
        modified_files = {}
        current_file = None
        
        for line in diff_output.split('\n'):
            # 解析文件路径
            if line.startswith('+++'):
                file_match = re.match(r'\+\+\+ b/(.+)', line)
                if file_match:
                    current_file = file_match.group(1)
                    if current_file not in modified_files:
                        modified_files[current_file] = []
            
            # 解析修改的行号
            elif line.startswith('@@') and current_file:
                match = re.search(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
                if match:
                    start = int(match.group(1))
                    count = int(match.group(2)) if match.group(2) else 1
                    modified_files[current_file].append((start, start + count - 1))
        
        return modified_files
    
    def get_commit_diff(self, repo_path: Path, commit_hash: str) -> Dict[str, List[Tuple[int, int]]]:
        """
        获取commit的diff信息（保留原接口，内部使用批量方法）
        返回: {file_path: [(start_line, end_line), ...]}
        """
        result = self.get_batch_commit_diffs(repo_path, [commit_hash])
        return result.get(commit_hash, {})
    
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
    
    # 方案一: 批量获取commit中的文件列表
    def get_batch_files_in_commits(self, repo_path: Path, commit_hashes: List[str]) -> Dict[str, List[str]]:
        """
        批量获取多个commit中修改的文件
        返回: {commit_hash: [file_path, ...]}
        """
        result = {}
        
        for commit_hash in commit_hashes:
            try:
                cmd_result = subprocess.run(
                    ['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', commit_hash],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if cmd_result.returncode == 0:
                    files = [f for f in cmd_result.stdout.strip().split('\n') if f]
                    result[commit_hash] = files
                else:
                    result[commit_hash] = []
                    
            except Exception:
                result[commit_hash] = []
        
        return result
    
    # 方案二: 使用git show获取文件内容，避免checkout
    def get_file_content_at_commit(self, repo_path: Path, commit_hash: str, file_path: str) -> Optional[str]:
        """
        获取指定commit时的文件内容（不需要checkout）
        """
        cache_key = (commit_hash, file_path)
        
        # 方案五: 检查缓存
        if cache_key in self._file_content_cache:
            return self._file_content_cache[cache_key]
        
        try:
            result = subprocess.run(
                ['git', 'show', f'{commit_hash}:{file_path}'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                content = result.stdout
                # 缓存结果（限制缓存大小）
                if len(self._file_content_cache) < 1000:
                    self._file_content_cache[cache_key] = content
                return content
            return None
            
        except Exception as e:
            logging.debug(f"Failed to get file content for {file_path} at {commit_hash}: {e}")
            return None
    
    # 方案二: 获取commit父提交时的文件内容
    def get_file_content_before_commit(self, repo_path: Path, commit_hash: str, file_path: str) -> Optional[str]:
        """
        获取commit之前（父提交）的文件内容
        """
        return self.get_file_content_at_commit(repo_path, f'{commit_hash}^', file_path)
    
    # 方案五: 获取并缓存实体映射
    def get_entities_at_commit(self, repo_path: Path, commit_hash: str, file_path: str) -> Dict[int, str]:
        """
        获取指定commit时文件的实体映射（带缓存）
        """
        cache_key = (commit_hash, file_path)
        
        if cache_key in self._entity_cache:
            return self._entity_cache[cache_key]
        
        content = self.get_file_content_at_commit(repo_path, commit_hash, file_path)
        if content is None:
            return {}
        
        entities = PythonEntityExtractor.parse_source(content)
        
        # 缓存结果
        if len(self._entity_cache) < 2000:
            self._entity_cache[cache_key] = entities
        
        return entities
    

    def find_function_init_commit(self, repo_path: Path, file_path: str, function_name: str) -> Optional[Dict]:
            """
            查找函数首次出现的commit（使用git log -S，并验证是否为真正的init commit）
            """
            try:
                search_pattern = f"def {function_name}("
                
                logging.debug(f"Searching init commit for function '{function_name}' in {file_path}")
                logging.debug(f"Using search pattern: '{search_pattern}'")
                
                # 不使用 --follow，获取所有改变该函数定义出现次数的 commit
                cmd = ['git', 'log', '-S', search_pattern, '--format=%H|%at|%s', '--reverse', '--', file_path]
                logging.debug(f"Running command: {' '.join(cmd)}")
                
                result = subprocess.run(
                    cmd,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                
                logging.debug(f"git log -S return code: {result.returncode}")
                logging.debug(f"git log -S stdout: {result.stdout[:500] if result.stdout else '(empty)'}")
                if result.stderr:
                    logging.debug(f"git log -S stderr: {result.stderr[:500]}")
                
                if result.returncode != 0:
                    logging.warning(f"git log -S failed for {function_name}, return code: {result.returncode}")
                    return None
                
                # 解析结果
                lines = result.stdout.strip().split('\n')
                valid_lines = [l for l in lines if l]
                logging.debug(f"git log -S returned {len(valid_lines)} candidate commits")
                
                # 明显是文件/目录移动的 commit message 模式（硬证据）
                move_patterns = [
                    r'\bmoves?\s+(project|directory|folder|files?)\b',  # "Move/Moves project/directory/folder/file"
                    r'\bmoves?\s+\S+\s+(to|from)\s+\S+',                # "Move xxx to/from yyy"
                    r'\bmoves?\s+\S+\s+(out\s+of|into)\s+\S+',          # "Moves xxx out of yyy" / "Move xxx into yyy"
                    r'\brename\s+(project|directory|folder)\b',         # "Rename project/directory/folder"
                    r'\brelocate\s+(project|directory|folder)\b',       # "Relocate project/directory/folder"
                    r'\bmigrate\s+(project|directory|folder)\b',        # "Migrate project/directory/folder"
                ]
                
                # # 明显不是创建新函数的 commit message 模式（这些 commit 不太可能是 init commit）
                # skip_patterns = [
                #     r'\btypos?\b',                  # 纯拼写修复
                #     r'\bpep\s?8\b',                 # 纯风格规范
                #     r'\bflake\s?8\b',               # 纯风格规范
                #     r'\bwhitespace\b',              # 纯空白调整
                #     r'\bindent(ation|ing|ed|s)?\b', # 纯缩进调整
                #     r'\bcosmetic\b',                # 纯外观调整
                #     r'\bnit(pick)?\b',              # 纯小修小补
                #     r'\blint(ing|ed|s)?\b',         # 通常是自动修复
                # ]
                
                # 验证每个候选 commit，找到真正首次引入函数的 commit
                for line in valid_lines:
                    parts = line.split('|', 2)
                    if len(parts) != 3:
                        continue
                        
                    commit_hash, timestamp, message = parts
                    message_lower = message.lower()
                    
                    # 检查是否是明显的文件移动 commit（基于 commit message）
                    is_move_commit = any(re.search(pattern, message_lower) for pattern in move_patterns)
                    if is_move_commit:
                        # commit message 表明是文件移动，但可能同时新增了函数，需要检查 diff
                        diff_result = subprocess.run(
                            ['git', 'show', '--format=', commit_hash, '--', file_path],
                            cwd=repo_path,
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                        
                        if diff_result.returncode == 0:
                            has_added_func_def = any(
                                ln.startswith('+') and f"def {function_name}(" in ln and not ln.startswith('+++')
                                for ln in diff_result.stdout.split('\n')
                            )
                            
                            if has_added_func_def:
                                logging.debug(f"Commit {commit_hash[:8]} is file move but has '+def {function_name}(' in diff, treating as init")
                            else:
                                logging.debug(f"Skipping commit {commit_hash[:8]}: commit message indicates file move and no '+def {function_name}(' in diff - '{message[:60]}'")
                                continue
                        else:
                            logging.debug(f"Skipping commit {commit_hash[:8]}: commit message indicates file move - '{message[:60]}'")
                            continue
                    
                    # 检查是否是明显不会创建新函数的 commit（typo、refactor 等）
                    # is_skip_commit = any(re.search(pattern, message_lower) for pattern in skip_patterns)
                    # if is_skip_commit:
                    #     logging.debug(f"Skipping commit {commit_hash[:8]}: commit message indicates non-init type (typo/refactor/style) - '{message[:60]}'")
                    #     continue

                    # 检查是否是明显不会创建新函数的 commit（typo、refactor 等）
                    is_skip_commit = CommitAnalyzer.should_skip_commit(message)
                    if is_skip_commit:
                        logging.debug(f"Skipping commit {commit_hash[:8]}: commit message indicates non-semantic type - '{message[:60]}'")
                        continue
                    
                    # 获取 commit 前后的文件内容
                    old_content = self.get_file_content_at_commit(repo_path, f'{commit_hash}^', file_path)
                    new_content = self.get_file_content_at_commit(repo_path, commit_hash, file_path)
                    
                    # 如果 old_content 为 None，需要检查是文件新建还是文件移动
                    if old_content is None:
                        # 检查文件在这个 commit 中的状态（A=Added, R=Renamed, M=Modified 等）
                        status_result = subprocess.run(
                            ['git', 'show', '--name-status', '--format=', commit_hash],
                            cwd=repo_path,
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                        
                        if status_result.returncode == 0:
                            # 检测是否是 Rename
                            is_rename = any(file_path in status_line and status_line.startswith('R') 
                                        for status_line in status_result.stdout.strip().split('\n'))
                            
                            if is_rename:
                                # 文件是 Rename 过来的，需要进一步检查 diff 中是否真的新增了该函数定义
                                diff_result = subprocess.run(
                                    ['git', 'show', '--format=', commit_hash, '--', file_path],
                                    cwd=repo_path,
                                    capture_output=True,
                                    text=True,
                                    timeout=60
                                )
                                
                                if diff_result.returncode == 0:
                                    # 检查是否有 "+def function_name(" 行
                                    has_added_func_def = any(
                                        ln.startswith('+') and f"def {function_name}(" in ln and not ln.startswith('+++')
                                        for ln in diff_result.stdout.split('\n')
                                    )
                                    
                                    if has_added_func_def:
                                        logging.debug(f"Commit {commit_hash[:8]} is Rename but has '+def {function_name}(' in diff, treating as init")
                                    else:
                                        logging.debug(f"Skipping commit {commit_hash[:8]}: file was renamed and no '+def {function_name}(' in diff")
                                        continue
                                else:
                                    logging.debug(f"Skipping commit {commit_hash[:8]}: file was renamed/moved")
                                    continue
                    
                    old_has_func = old_content is not None and search_pattern in old_content
                    new_has_func = new_content is not None and search_pattern in new_content
                    
                    logging.debug(f"Validating commit {commit_hash[:8]}: old_has_func={old_has_func}, new_has_func={new_has_func}")
                    
                    if not old_has_func and new_has_func:
                        # 额外验证1：用 git grep 检查函数是否在 commit 之前就已存在于仓库中（任意位置）
                        grep_result = subprocess.run(
                            ['git', 'grep', '-l', search_pattern, f'{commit_hash}^', '--', '*.py'],
                            cwd=repo_path,
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                        
                        if grep_result.returncode == 0 and grep_result.stdout.strip():
                            existing_files = grep_result.stdout.strip().split('\n')
                            logging.debug(f"Skipping commit {commit_hash[:8]}: function '{function_name}' already exists in repo before this commit")
                            logging.debug(f"  Existing locations: {existing_files[:5]}")
                            continue
                        
                        # 额外验证2：大规模 commit（修改文件数 > 阈值）不太可能是单个函数的 init commit
                        files_in_commit = self.get_files_in_commit(repo_path, commit_hash)
                        if len(files_in_commit) > 50:
                            logging.debug(f"Skipping commit {commit_hash[:8]}: too many files changed ({len(files_in_commit)}), likely a large refactor/migration")
                            continue
                        
                        # 额外验证3（最终防线）：直接检查 diff 中是否真的有 "+def function_name(" 行
                        diff_result = subprocess.run(
                            ['git', 'show', '--format=', commit_hash, '--', file_path],
                            cwd=repo_path,
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                        
                        if diff_result.returncode == 0:
                            has_added_func_def = any(
                                ln.startswith('+') and f"def {function_name}(" in ln and not ln.startswith('+++')
                                for ln in diff_result.stdout.split('\n')
                            )
                            
                            if not has_added_func_def:
                                logging.debug(f"Skipping commit {commit_hash[:8]}: no '+def {function_name}(' line found in diff (function was moved, not created)")
                                continue
                        else:
                            logging.debug(f"Skipping commit {commit_hash[:8]}: failed to get diff for final verification")
                            continue
                        
                        # 真正的 init commit：通过所有验证
                        init_commit = {
                            'commit_hash': commit_hash,
                            'timestamp': datetime.fromtimestamp(int(timestamp)).isoformat() + 'Z',
                            'commit_message': message,
                            'commit_type': CommitAnalyzer.extract_commit_type(message)
                        }
                        logging.info(f"Found verified init commit for '{function_name}': {commit_hash[:8]} - {message[:60]}")
                        return init_commit

                    else:
                        # 可能是文件移动或其他情况，跳过继续检查下一个
                        logging.debug(f"Skipping commit {commit_hash[:8]}: not a true init (old_has={old_has_func}, new_has={new_has_func})")
                
                logging.warning(f"No verified init commit found for function '{function_name}' in {file_path}")
                return None
                
            except subprocess.TimeoutExpired:
                logging.error(f"Timeout while searching init commit for {function_name}")
                return None
            except Exception as e:
                logging.warning(f"Failed to find init commit for {function_name}: {e}")
                return None

    def is_semantic_modification(self, repo_path: Path, commit_hash: str, 
                                    file_path: str, function_name: str) -> bool:
        """
        检查commit对指定函数是否是语义上的修改（而非仅格式修改）
        """
        try:
            logging.debug(f"Checking semantic modification for '{function_name}' in commit {commit_hash[:8]}")
            
            old_content = self.get_file_content_at_commit(repo_path, f'{commit_hash}^', file_path)
            new_content = self.get_file_content_at_commit(repo_path, commit_hash, file_path)
            
            if old_content is None and new_content is None:
                logging.debug(f"  Both old and new content are None, not a semantic change")
                return False
            if old_content is None:
                logging.debug(f"  Old content is None (file created), is semantic change")
                return True
            if new_content is None:
                logging.debug(f"  New content is None (file deleted), is semantic change")
                return True
            
            is_semantic = PythonEntityExtractor.is_semantic_change(old_content, new_content, function_name)
            
            if is_semantic:
                logging.debug(f"  Commit {commit_hash[:8]} IS a semantic modification to '{function_name}'")
            else:
                logging.debug(f"  Commit {commit_hash[:8]} is NOT a semantic modification to '{function_name}' (format/comment only)")
            
            return is_semantic
            
        except Exception as e:
            logging.debug(f"Failed to check semantic modification for {function_name} in {commit_hash[:8]}: {e}")
            return True  # 出错时保守处理，认为是语义修改
    
    def clear_cache(self):
        """清理缓存"""
        self._file_content_cache.clear()
        self._entity_cache.clear()


class HistoricalInfoCollector:
    """收集测试函数的历史信息"""
    
    def __init__(self, output_dir: str, log_dir: str, instance_id: str = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 传入instance_id用于区分并行任务的仓库路径
        self.git_manager = GitRepoManager(tempfile.gettempdir() + '/swe_bench_repos', instance_id)
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
    
    # 方案二: 重写实体提取方法，不使用checkout
    def extract_entities_from_diff_optimized(self, repo_path: Path, commit_hash: str, 
                                            file_path: str, modified_lines: List[Tuple[int, int]]) -> Set[str]:
        """
        从diff中提取被修改的实体（优化版本，不使用checkout）
        """
        entities = set()
        
        if not file_path.endswith('.py'):
            return entities
        
        try:
            # 方案二: 使用git show获取commit前的文件内容
            entity_map = self.git_manager.get_entities_at_commit(
                repo_path, f'{commit_hash}^', file_path
            )
            
            if not entity_map:
                return entities
            
            # 找到修改的实体
            for start, end in modified_lines:
                for line_num in range(start, end + 1):
                    entity = self.entity_extractor.find_entity_at_line(entity_map, line_num)
                    if entity:
                        entities.add(f"{file_path}::{entity}")
            
        except Exception as e:
            logging.debug(f"Failed to extract entities from {file_path}: {e}")
        
        return entities

    # 保留原方法作为备用，但标记为deprecated
    def extract_entities_from_diff(self, repo_path: Path, commit_hash: str, 
                                file_path: str, modified_lines: List[Tuple[int, int]]) -> Set[str]:
        """从diff中提取被修改的实体（使用优化版本）"""
        return self.extract_entities_from_diff_optimized(repo_path, commit_hash, file_path, modified_lines)

    def collect_for_test(self, logger: logging.Logger, repo_path: Path, 
                        test_function: str, covered_entities: List[str],
                        base_commit: str) -> Dict:
        """收集单个测试函数的历史信息"""
        logger.info(f"Collecting history for test: {test_function}")
        
        result = {
            'test_function': test_function,
            'covered_entities': covered_entities,
            'init_commit': None,  # 新增：测试函数的初始commit
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

        # 方案二: 使用git show检查文件是否存在，而不是检查工作目录
        test_file_content = self.git_manager.get_file_content_at_commit(repo_path, base_commit, test_file)
        if test_file_content is None:
            logger.warning(f"Test file does not exist at base_commit: {test_file}")
            return result
        
        logger.debug(f"Test file exists: {test_file}")

        # 获取测试文件的commit历史
        test_commits = self.git_manager.get_commit_history(repo_path, test_file)
        logger.info(f"Found {len(test_commits)} commits for test file")
        
        # 解析测试函数名（用于查找init commit和语义过滤）
        test_func_name = test_function.split('::')[-1] if '::' in test_function else None
        
        # 查找测试函数的init commit
        if test_func_name:
            init_commit = self.git_manager.find_function_init_commit(repo_path, test_file, test_func_name)
            result['init_commit'] = init_commit
            if init_commit:
                logger.info(f"Found init commit for {test_func_name}: {init_commit['commit_hash'][:8]}")
            else:
                logger.warning(f"Could not find init commit for {test_func_name}")
        
        # 过滤非语义修改的commit
        if test_func_name:
            logger.info(f"Starting semantic filtering for {len(test_commits)} commits...")
            semantic_commits = []
            filtered_commits = []
            
            for commit_info in test_commits:
                commit_hash = commit_info['commit_hash']
                commit_msg = commit_info['commit_message']
                
                is_semantic = self.git_manager.is_semantic_modification(repo_path, commit_hash, test_file, test_func_name)
                
                if is_semantic:
                    semantic_commits.append(commit_info)
                    logger.debug(f"KEPT semantic commit: {commit_hash[:8]} - {commit_msg[:60]}")
                else:
                    filtered_commits.append(commit_info)
                    logger.info(f"FILTERED non-semantic commit: {commit_hash[:8]} - {commit_msg[:60]}")
            
            # 汇总日志
            logger.info(f"Semantic filtering complete:")
            logger.info(f"  - Total commits analyzed: {len(test_commits)}")
            logger.info(f"  - Semantic commits (kept): {len(semantic_commits)}")
            logger.info(f"  - Non-semantic commits (filtered): {len(filtered_commits)}")
            
            if filtered_commits:
                logger.info(f"  - Filtered commits list:")
                for fc in filtered_commits:
                    logger.info(f"      {fc['commit_hash'][:8]} | {fc['timestamp'][:10]} | {fc['commit_message'][:50]}")
            
            test_commits = semantic_commits
        
        result['test_modification_history'] = test_commits
        result['statistics']['total_test_modifications'] = len(test_commits)
        
        if not test_commits:
            return result
        
        # 创建covered entities的集合用于快速查找
        covered_set = set(covered_entities)
        
        # 方案六: 预先提取covered entities涉及的文件
        covered_files = set()
        for entity in covered_entities:
            if '::' in entity:
                covered_files.add(entity.split('::')[0])
        
        # 方案一: 批量获取所有commit的信息
        commit_hashes = [c['commit_hash'] for c in test_commits]
        
        logger.debug(f"Batch fetching diff info for {len(commit_hashes)} commits...")
        all_diffs = self.git_manager.get_batch_commit_diffs(repo_path, commit_hashes)
        
        logger.debug(f"Batch fetching file lists for {len(commit_hashes)} commits...")
        all_files = self.git_manager.get_batch_files_in_commits(repo_path, commit_hashes)
        
        first_test_commit_time = test_commits[-1]['timestamp'] if test_commits else None
        co_modified_entities = set()
        
        for commit_info in test_commits:
            commit_hash = commit_info['commit_hash']
            logger.debug(f"Analyzing commit: {commit_hash}")
            
            # 使用预先获取的数据
            modified_files_in_commit = all_files.get(commit_hash, [])
            file_diffs = all_diffs.get(commit_hash, {})
            
            # 方案六: 快速检查是否有可能涉及covered entities
            modified_py_files = [f for f in modified_files_in_commit if f.endswith('.py')]
            
            # 检查是否有covered files被修改
            has_covered_file = any(f in covered_files for f in modified_py_files)
            has_test_file = test_file in modified_files_in_commit
            
            if not has_test_file and not has_covered_file:
                # 方案六: 跳过不相关的commit
                continue
            
            # 提取该commit中修改的所有实体
            modified_entities_in_commit = set()
            
            for file_path, line_ranges in file_diffs.items():
                if not file_path.endswith('.py'):
                    continue
                
                # 方案六: 只处理相关文件
                if file_path != test_file and file_path not in covered_files:
                    continue
                
                entities = self.extract_entities_from_diff_optimized(
                    repo_path, commit_hash, file_path, line_ranges
                )
                modified_entities_in_commit.update(entities)
            
            # 只保留在covered_entities中的实体
            covered_modified = modified_entities_in_commit & covered_set
            
            if test_function in modified_entities_in_commit or has_test_file:
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
                return None
            
            logger.info(f"Repository cloned to: {repo_path}")
            
            # 处理每个测试函数
            results = {}
            
            for test_function, test_data in coverage_graph.items():
                covered_entities = test_data['nodes']
                
                test_result = self.collect_for_test(
                    logger, repo_path, test_function, covered_entities, base_commit
                )
                
                results[test_function] = test_result
            
            # 清理缓存
            self.git_manager.clear_cache()
            
            # 保存结果
            output_file = self.output_dir / f'{instance_id}.json'
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Results saved to: {output_file}")
            logger.info(f"Successfully processed {len(results)} test functions")
            
            return instance_id
            
        except Exception as e:
            logger.error(f"Error processing instance {instance_id}: {e}", exc_info=True)
            return None
        
        finally:
            # 清理克隆的仓库目录
            logger.info("Cleaning up...")
            try:
                if repo_path and repo_path.exists():
                    shutil.rmtree(repo_path)
                    logger.info(f"Removed cloned repo: {repo_path}")
            except NameError:
                # repo_path未定义（克隆失败的情况）
                pass
            except Exception as e:
                logger.warning(f"Failed to remove repo: {e}")


# 方案三: 用于并行处理的独立函数
def process_instance_parallel(args: Tuple[Dict, Dict, str, str]) -> Optional[str]:
    """
    并行处理单个实例的包装函数
    args: (instance, coverage_graph, output_dir, log_dir)
    """
    instance, coverage_graph, output_dir, log_dir = args
    
    # 每个进程创建独立的collector，并传入instance_id以区分仓库路径
    instance_id = instance['instance_id']
    collector = HistoricalInfoCollector(output_dir, log_dir, instance_id)
    
    return collector.process_instance(instance, coverage_graph)


def main(swe_bench_path: str, coverage_graph_path: str, output_dir: str = 'historical_information',
         num_workers: int = None):
    """主函数"""
    print("="*80)
    print("Historical Information Collection Tool (Optimized)")
    print("="*80)
    
    # 方案三: 设置并行worker数量
    if num_workers is None:
        num_workers = max(1, multiprocessing.cpu_count() - 1)
    
    print(f"Using {num_workers} parallel workers")
    
    # 创建输出目录
    log_dir = 'logs'
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    
    # 加载SWE-bench数据
    print(f"\nLoading SWE-bench data from: {swe_bench_path}")
    swe_bench_data = load_dataset(swe_bench_path)
    
    # 获取测试集
    test_data = swe_bench_data['test']
    print(f"Loaded {len(test_data)} instances")
    
    # 加载coverage graphs
    coverage_graph_dir = Path(coverage_graph_path)
    
    # 准备任务列表
    tasks = []
    for instance in test_data:
        instance_id = instance['instance_id']
        coverage_file = coverage_graph_dir / f'{instance_id}.json'
        
        if not coverage_file.exists():
            print(f"Skipping {instance_id}: coverage graph not found")
            continue
        
        # 加载coverage graph
        with open(coverage_file, 'r') as f:
            coverage_graph = json.load(f)
        
        # 将instance转换为可序列化的字典
        instance_dict = dict(instance)
        tasks.append((instance_dict, coverage_graph, output_dir, log_dir))
    
    print(f"\nPrepared {len(tasks)} tasks for processing")
    
    # 方案三: 并行处理
    processed = 0
    failed = 0
    
    if num_workers > 1:
        print(f"\nStarting parallel processing with {num_workers} workers...")
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = {executor.submit(process_instance_parallel, task): task[0]['instance_id'] 
                      for task in tasks}
            
            for future in as_completed(futures):
                instance_id = futures[future]
                try:
                    result = future.result()
                    if result:
                        processed += 1
                        print(f"[{processed + failed}/{len(tasks)}] Completed: {instance_id}")
                    else:
                        failed += 1
                        print(f"[{processed + failed}/{len(tasks)}] Failed: {instance_id}")
                except Exception as e:
                    failed += 1
                    print(f"[{processed + failed}/{len(tasks)}] Error processing {instance_id}: {e}")
    else:
        # 单进程模式（方便调试）
        print("\nStarting sequential processing...")
        collector = HistoricalInfoCollector(output_dir, log_dir)
        
        for task in tasks:
            instance, coverage_graph, _, _ = task
            instance_id = instance['instance_id']
            print(f"\n[{processed + failed + 1}/{len(tasks)}] Processing: {instance_id}")
            
            result = collector.process_instance(instance, coverage_graph)
            if result:
                processed += 1
            else:
                failed += 1
    
    print(f"\n{'='*80}")
    print(f"Processing complete!")
    print(f"Processed: {processed} instances")
    print(f"Failed: {failed} instances")
    print(f"Results saved to: {output_dir}/")
    print(f"Logs saved to: {log_dir}/")
    print(f"{'='*80}")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python collect_historical_info_optimized.py <swe_bench_path> <coverage_graph_path> [output_dir] [num_workers]")
        print("  num_workers: number of parallel workers (default: CPU count - 1)")
        sys.exit(1)
    
    swe_bench_path = sys.argv[1]
    coverage_graph_path = sys.argv[2]
    output_dir = sys.argv[3] if len(sys.argv) > 3 else 'historical_information'
    num_workers = int(sys.argv[4]) if len(sys.argv) > 4 else None
    
    main(swe_bench_path, coverage_graph_path, output_dir, num_workers)