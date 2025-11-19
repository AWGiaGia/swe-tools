import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json


from utils.util import load_ground_truth


if __name__ == '__main__':
    swe_bench_like_dataset_path = "/home/jiawei/Agentless/sklearn_swe-bench_lite"
    local_repo_path = "/home/jiawei/CommitInsight/repos/scikit-learn"

    ground_truth_map = load_ground_truth(swe_bench_like_dataset_path, local_repo_path)

    print(json.dumps(ground_truth_map, indent = 4))