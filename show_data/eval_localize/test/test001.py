import json
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.util import GetPatch


from datasets import load_dataset
sklearn_swe_bench_lite_num_2 = load_dataset("/home/jiawei/Agentless/sklearn_swe-bench_lite_num=2")
patch = sklearn_swe_bench_lite_num_2['test'][0]['patch']


local_repo_path = '/home/jiawei/CommitInsight/repos/scikit-learn'
base_commit_url = 'b90661d6a46aa3619d3eec94d5281f5888add501'


if __name__ == '__main__':
    get_patch = GetPatch(local_repo_path=local_repo_path)
    patch_info = get_patch(patch_content=patch, commit_sha=base_commit_url)
    print(json.dumps(patch_info, indent = 4))