# 检查swe-bench-lite数据集里面，有多少issue_statement里面是提到了最终的edit_loc的，有多少是没有提到edit_loc的。利用deepseek_api进行筛选
import json

from datasets import load_dataset

from util import load_ground_truth
from client import DeepSeekClient
from prompt import system_prompt, user_prompt, extract_location_analysis
from tqdm import tqdm



# 读取和加载swe_bench数据，智能一次处理一个repo的
def load_swe_bench(target_repo, swe_bench_path, local_repo_path):

    def only_target_repo(x):
        global target_repo
        return target_repo in x['repo']

    swebench = load_dataset(swe_bench_path, split='test')
    swebench = swebench.filter(only_target_repo)
    ground_truth_map = load_ground_truth(swe_bench_path, local_repo_path, target_repo)

    instance_table = {}
    for data in swebench:
        instance_id = data['instance_id']
        issue_statement = data['problem_statement']
        if instance_id not in ground_truth_map:
            print(f"Warning: {instance_id} not in ground_truth_map")
            continue
        edit_loc = ground_truth_map[instance_id]
        instance_table[instance_id] = {
            'problem_statement': issue_statement,
            'edit_loc': edit_loc
        }

    return instance_table


def label_with_ds(instance_table, save_path, model_api = "sk-9a39714a31be4b27952b0510951847df"):
    client = DeepSeekClient(api_key=model_api)
    for k, v in tqdm(instance_table.items()):
        usr_p = user_prompt.format(problem_statement=v['problem_statement'], edit_loc=v['edit_loc'])
        raw_output = client(prompt = usr_p, system_prompt = system_prompt)

        result = extract_location_analysis(raw_output)

        have_related_loc = False
        if len(result) > 0 and result[0][0] != "None":
            have_related_loc = True

        result_dict = {
            'instance_id': k,
            'problem_statement': v['problem_statement'],
            'edit_loc': v['edit_loc'],
            'ds_output': raw_output,
            'location_analysis': result,
            "have_related_loc": have_related_loc
        }

        with open(save_path, 'a') as f:
            f.write(json.dumps(result_dict) + '\n')

        # print(result)
        # raise ValueError("Stop here")



if __name__ == '__main__':
    target_repo = "sympy"
    swe_bench_path = "swe-bench_lite"
    loca_repo_path = "/home/jiawei/CommitInsight/repos/sympy"

    instance_table = load_swe_bench(target_repo, swe_bench_path, loca_repo_path)

    label_with_ds(instance_table, "./sympy.jsonl")