import json
import os


from utils.util import load_ground_truth, load_predict_output, batch_evaluation


if __name__ == '__main__':
    exp_name = "blind-based-sklearn_swe_bench_recall_0824_k2"


    swe_bench_like_dataset_path = "/home/jiawei/Agentless/sklearn_swe-bench_lite"
    local_repo_path = "/home/jiawei/CommitInsight/repos/scikit-learn"
    ground_truth_map = load_ground_truth(swe_bench_like_dataset_path, local_repo_path)

    edit_loc_path = "/home/jiawei/RepoCodeLoc/ours/TestBlindLoc/sklearn_0824/blind_spot_analysis/loc_outputs.jsonl"
    predict_output = load_predict_output(edit_loc_path = edit_loc_path)


    results, metrics_summary = batch_evaluation(predictions=predict_output, ground_truths=ground_truth_map)

    # print("===================results================")
    # print(json.dumps(results, indent = 4))
    # print("===================summary================")
    # print(json.dumps(metrics_summary, indent=4))

    if not os.path.exists("./results"):
        os.mkdir("./results")

    results_path = os.path.join("./results", exp_name)
    if not os.path.exists(results_path):
        os.makedirs(results_path)
    results_file = os.path.join(results_path, "results.json")
    summary_file = os.path.join(results_path, "summary.json")

    with open(results_file, "w") as f:
        json.dump(results, f, indent=4)

    with open(summary_file, "w") as f:
        json.dump(metrics_summary, f, indent=4)

    print(f"Results and summary saved to {results_path}")