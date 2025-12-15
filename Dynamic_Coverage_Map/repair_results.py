import os
import ast
from datasets import load_dataset
import git
import json
from tqdm import tqdm
from multiprocessing import Pool
import tempfile
import shutil
import warnings

# 忽略 SyntaxWarning
warnings.filterwarnings('ignore', category=SyntaxWarning)


def get_class_at_line(file_path, line_number):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_start = node.lineno
                class_end = max(
                    getattr(child, 'end_lineno', 0) 
                    for child in ast.walk(node)
                )
                if class_start <= line_number <= class_end:
                    return node.name
    except Exception as e:
        # 静默处理错误，不打印
        pass
    return None


def process_single_folder(args):
    """处理单个文件夹的函数，动态克隆仓库到临时目录"""
    raw_result_folder, raw_results_root_folder, target_results_root_folder, instance_info_map = args
    
    temp_dir = None
    try:
        raw_reult_path = os.path.join(raw_results_root_folder, raw_result_folder, "result", "traces.json")

        # 从文件夹名称解析 instance_name
        instance_name = raw_result_folder.split("_")[-1]
        instance_name = instance_name.rsplit("-", 1)[0] + "__" + instance_name

        instance_info = instance_info_map.get(instance_name)
        if instance_info is None:
            return f"Error: No instance info found for {instance_name}"

        base_commit = instance_info['base_commit']
        repo = instance_info['repo']

        # 构建 GitHub URL 并克隆到临时目录
        repo_url = f"https://github.com/{repo}.git"
        temp_dir = tempfile.mkdtemp(prefix=f"repo_{raw_result_folder}_")

        # 克隆仓库并切换到指定 commit
        cloned_repo = git.Repo.clone_from(repo_url, temp_dir)
        cloned_repo.git.checkout(base_commit)
        
        # 读取 traces.json
        with open(raw_reult_path, "r") as f:
            raw_results = json.load(f)
        
        # 处理每个 item
        for item in raw_results:
            for relation in item.get("call-relations", []):
                caller = relation["caller"]
                callee = relation["callee"]

                # 使用临时目录中的文件
                caller_class = get_class_at_line(
                    os.path.join(temp_dir, caller["filepath"]), 
                    caller["lineno"]
                )
                callee_class = get_class_at_line(
                    os.path.join(temp_dir, callee["filepath"]), 
                    callee["lineno"]
                )
                
                caller["class_name"] = caller_class if caller_class else ""
                callee["class_name"] = callee_class if callee_class else ""

        # 保存修复后的结果
        os.makedirs(os.path.join(target_results_root_folder, raw_result_folder, "result"), exist_ok=True)
        save_path = os.path.join(target_results_root_folder, raw_result_folder, "result", "traces.json")
        with open(save_path, "w") as f:
            json.dump(raw_results, f, indent=4)
        
        return f"✓ {raw_result_folder}"
    
    except Exception as e:
        return f"✗ {raw_result_folder}: {str(e)}"
    
    finally:
        # 清理临时目录
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def process_raw_results(raw_results_root_folder, target_results_root_folder, instance_info_map, num_processes=4):
    folders = [f for f in os.listdir(raw_results_root_folder) 
               if os.path.isdir(os.path.join(raw_results_root_folder, f))]
    
    print(f"Found {len(folders)} folders to process")
    print(f"Using {num_processes} processes")
    
    # 准备参数
    args_list = [
        (folder, raw_results_root_folder, target_results_root_folder, instance_info_map)
        for folder in folders
    ]
    
    # 使用进程池处理
    with Pool(processes=num_processes) as pool:
        # 使用 imap_unordered 以获得更好的进度显示
        results = list(tqdm(
            pool.imap_unordered(process_single_folder, args_list),
            total=len(args_list),
            desc="Processing folders",
            unit="folder",
            # 显示更详细的信息
            bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]'
        ))
    
    # 统计结果
    success_count = sum(1 for r in results if r.startswith("✓"))
    error_count = sum(1 for r in results if r.startswith("✗"))
    
    print(f"\n{'='*60}")
    print(f"Processing complete!")
    print(f"Success: {success_count}/{len(folders)}")
    print(f"Errors: {error_count}/{len(folders)}")
    print(f"{'='*60}\n")
    
    # 只打印错误信息
    if error_count > 0:
        print("Errors encountered:")
        for result in results:
            if result.startswith("✗"):
                print(f"  {result}")


if __name__ == '__main__':
    # 构建 instance 信息映射，包含 base_commit 和 repo
    instance_info_map = dict()
    swe_bench_data = load_dataset("/home/jiawei/RepoCodeLoc/swe-bench-lite")
    for item in swe_bench_data['test']:
        instance = item['instance_id']
        instance_info_map[instance] = {
            'base_commit': item['base_commit'],
            'repo': item['repo']  # 格式如 "scikit-learn/scikit-learn"
        }

    raw_results_root_folder = "/home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/results"
    target_results_root_folder = "/home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/results_repaired"
    
    # 设置进程数
    num_processes = min(os.cpu_count(), 23)
    process_raw_results(raw_results_root_folder, target_results_root_folder, instance_info_map, num_processes=num_processes)