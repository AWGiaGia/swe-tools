# results中存在一些问题，需要对其进行修复。包括类名缺失等等
import os
import ast
from datasets import load_dataset
import git
import json
from tqdm import tqdm


CATEGORY_BY_REPO_NAME = {
    "scikit-learn": "/home/jiawei/CommitInsight/repos/scikit-learn"
    }


def get_class_at_line(file_path, line_number):
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    tree = ast.parse(source)
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # 检查行号范围
            class_start = node.lineno
            # 找到类的结束行
            class_end = max(
                getattr(child, 'end_lineno', 0) 
                for child in ast.walk(node)
            )
            if class_start <= line_number <= class_end:
                return node.name
    return None


def process_raw_results(raw_results_root_folder, target_results_root_folder, base_commit_map):
    for raw_result_folder in tqdm(os.listdir(raw_results_root_folder)):
        print(f"Processing folder: {raw_result_folder}")
        raw_reult_path = os.path.join(raw_results_root_folder, raw_result_folder, "result", "traces.json")

        # print(f"{raw_result_folder=}")
        for repo_name in CATEGORY_BY_REPO_NAME:
            if repo_name in raw_result_folder:
                repo_path = CATEGORY_BY_REPO_NAME[repo_name]
                break
        
        instance_name = raw_result_folder.split("_")[-1] # scikit-learn-25747
        instance_name = instance_name.rsplit("-", 1)[0] + "__" + instance_name # scikit-learn__scikit-learn-25747

        # base_commit = swe_bench_data['test'][instance_name]["base_commit"]
        base_commit = base_commit_map[instance_name]

        # 将repo切换到对应的commit
        repo = git.Repo(repo_path)
        repo.git.checkout(base_commit, force=True)

        with open(raw_reult_path, "r") as f:
            raw_results = json.load(f)
        for item in tqdm(raw_results):
            for relation in item.get("call-relations", []):
                caller = relation["caller"]
                callee = relation["callee"]

                # 输入caller和callee的文件名和行好，获取类名
                caller_class = get_class_at_line(os.path.join(repo_path, caller["filepath"]), caller["lineno"])
                callee_class = get_class_at_line(os.path.join(repo_path, callee["filepath"]), callee["lineno"])
                if caller_class:
                    caller["class_name"] = caller_class
                else:
                    caller["class_name"] = ""
                if callee_class:
                    callee["class_name"] = callee_class
                else:
                    callee["class_name"] = ""

                print(json.dumps(relation, indent=4))
                raise ValueError("Debugging stop here")

        # 保存修复后的结果
        os.makedirs(os.path.join(target_results_root_folder, raw_result_folder, "result"), exist_ok=True)
        save_path = os.path.join(target_results_root_folder, raw_result_folder, "result", "traces.json")
        with open(save_path, "w") as f:
            json.dump(raw_results, f, indent=4)


if __name__ == '__main__':
    base_commit_map = dict()
    swe_bench_data = load_dataset("/home/jiawei/RepoCodeLoc/swe-bench-lite")
    for item in swe_bench_data['test']:
        instance = item['instance_id']  # scikit-learn__scikit-learn-25747
        base_commit = item['base_commit']
        base_commit_map[instance] = base_commit


    raw_results_root_folder = "/home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/results copy"
    target_results_root_folder = "/home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/results_scikit-learn_repaired"
    process_raw_results(raw_results_root_folder, target_results_root_folder, base_commit_map)

