import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json


from utils.util import load_ground_truth, load_predict_output, batch_evaluation


if __name__ == '__main__':
    swe_bench_like_dataset_path = "/home/jiawei/Agentless/sklearn_swe-bench_lite_num=2"
    local_repo_path = "/home/jiawei/CommitInsight/repos/scikit-learn"
    ground_truth_map = load_ground_truth(swe_bench_like_dataset_path, local_repo_path)


    edit_loc_path = "/home/jiawei/Agentless/results/sklearn-swe-bench-lite-num-2/edit_location_individual/loc_merged_0-0_outputs.jsonl"
    predict_output = load_predict_output(edit_loc_path = edit_loc_path)


    results, metrics_summary = batch_evaluation(predictions=predict_output, ground_truths=ground_truth_map)

    print("===================results================")
    print(json.dumps(results, indent = 4))
    print("===================summary================")
    print(json.dumps(metrics_summary, indent=4))